"""The two things every page needs: where the numbers came from, and how to find one.

``/status-bar`` backs the strip that sits on every screen (GEN-001). Its job is to make
"when is this from, and can I trust it" answerable without navigating away — every
field in it has been the subject of a real argument about a figure.

``/search`` is the single entry point into every entity (GEN-002). Wrappers, pairs,
pools and contracts all resolve to their underlying, because the underlying is what
people reason about; the specific instrument travels as filter context.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import AsOf, DatasetDep, Limit, SessionDep
from app.api.naming import KIND_LABELS, Named, href_for
from app.core.metrics import MetricScope
from app.db.base import utcnow
from app.models.dimensions import (
    DimAsset,
    DimIssuer,
    DimPerpContract,
    DimTheme,
    DimUnderlying,
    DimVenue,
)
from app.models.enums import (
    EditionKind,
    EntityType,
    FetchStatus,
    RwaTier,
    VerificationStatus,
)
from app.schemas.common import Meta, StatusBar
from app.schemas.decision import HomeAsOf, SearchHit, SearchResults
from app.services.editions import freeze
from app.services.report.dataset import ReportDataset, age_minutes

router = APIRouter(tags=["context"])

#: Past this, the bar turns amber. An hourly pipeline that has not written for ninety
#: minutes has missed a run, whatever the reason.
_STALE_AFTER_MINUTES = 90

#: How many characters a query needs before it is worth running. One character
#: matches most of the catalogue and teaches the user that search is useless.
_MIN_QUERY = 2


@router.get("/status-bar", response_model=HomeAsOf)
def status_bar(data: DatasetDep, session: SessionDep, as_of: AsOf = None) -> HomeAsOf:
    return HomeAsOf(status_bar=build_status_bar(data, session), server_time=utcnow())


@router.get("/search", response_model=SearchResults)
def search(
    session: SessionDep,
    q: str = Query(min_length=1, description="Symbol, name, ticker or venue."),
    limit: Limit = 20,
) -> SearchResults:
    query = q.strip()
    if len(query) < _MIN_QUERY:
        return SearchResults(
            meta=Meta(
                as_of=utcnow(),
                note="至少输入两个字符。",
                row_count=0,
            ),
            query=query,
            hits=[],
        )

    hits = _search_all(session, query, limit)
    return SearchResults(
        meta=Meta(
            as_of=utcnow(),
            note=(
                "包装代币、交易对、池与合约均落到其底层的 Underlying 360，"
                "并保留具体标的作为筛选上下文。"
            ),
            row_count=len(hits),
        ),
        query=query,
        hits=hits,
        counts=dict(Counter(h.kind_label for h in hits)),
    )


def build_status_bar(
    data: ReportDataset,
    session: Session,
    *,
    scopes: list[MetricScope] | None = None,
) -> StatusBar:
    """Assemble the bar. Shared by every page that renders one.

    ``as_of`` is the data cut-off and ``generated_at`` is when we rendered — two
    columns rather than one, because they are routinely hours apart and conflating
    them is how a report gets quoted as being newer than the data behind it.
    """
    now = utcnow()
    # Read-only: the live edition row is created by the scheduler, not by whoever
    # happens to load a page first. A GET that writes turns every dashboard refresh
    # into a transaction that can lose a race with the freeze job.
    trading_date = freeze.trading_date_for(data.as_of)
    key = freeze.edition_key(EditionKind.LIVE, trading_date)
    edition = freeze.resolve(session, key)

    # One entry per source per attempt; a source counts as answering if any of its
    # attempts in this snapshot came back OK or PARTIAL.
    outcomes: dict[str, set[FetchStatus]] = {}
    for entry in data.fetch_log:
        outcomes.setdefault(entry.source_id, set()).add(entry.status)
    ok = sum(
        1
        for statuses in outcomes.values()
        if statuses & {FetchStatus.OK, FetchStatus.PARTIAL}
    )
    total = len(outcomes)

    age = age_minutes(data.as_of, now)
    return StatusBar(
        scopes=scopes if scopes is not None else list(MetricScope),
        edition_key=edition.edition_key if edition else key,
        edition_kind=EditionKind.LIVE.value if edition is None else edition.kind.value,
        edition_revision=edition.revision if edition else 1,
        as_of=data.as_of,
        generated_at=now,
        data_age_minutes=age,
        sources_ok=ok,
        sources_total=total,
        verification_status=_verification(ok, total, age),
        next_refresh_at=(
            data.as_of + timedelta(hours=1) if data.as_of is not None else None
        ),
    )


def _verification(ok: int, total: int, age: int | None) -> VerificationStatus:
    """One word for the state of the whole page.

    Deliberately conservative. ``STALE`` blocks new management-level conclusions, and
    a page whose data stopped updating an hour and a half ago should not be able to
    produce one just because every source that *did* answer answered cleanly.
    """
    if total == 0:
        return VerificationStatus.NOT_VERIFIED
    if age is not None and age > _STALE_AFTER_MINUTES:
        return VerificationStatus.STALE
    if ok == 0:
        return VerificationStatus.NOT_VERIFIED
    if ok < total:
        return VerificationStatus.PARTIAL
    return VerificationStatus.VERIFIED


def _search_all(session: Session, query: str, limit: int) -> list[SearchHit]:
    """Search each catalogue, then interleave by relevance.

    Scored rather than concatenated: typing ``SPY`` should surface the underlying
    before eleven wrappers of it, and a per-table limit would bury the underlying
    behind whichever table happened to be queried first.
    """
    pattern = f"%{query}%"
    lowered = query.lower()
    found: list[SearchHit] = []

    def add(named: Named, *, haystack: str) -> None:
        found.append(
            SearchHit(
                entity_type=named.entity_type,
                entity_id=named.entity_id,
                name=named.name,
                kind_label=named.kind_label,
                detail=named.detail,
                href=named.href,
                score=_score(haystack, lowered, named.entity_type),
            )
        )

    underlyings = (
        session.execute(
            select(DimUnderlying)
            .where(
                or_(
                    DimUnderlying.underlying_id.ilike(pattern),
                    DimUnderlying.name.ilike(pattern),
                    DimUnderlying.isin.ilike(pattern),
                )
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for row in underlyings:
        add(
            Named(
                EntityType.UNDERLYING,
                row.underlying_id,
                row.name,
                row.asset_class.value,
            ),
            haystack=f"{row.underlying_id} {row.name}",
        )

    # NON_RWA wrappers are excluded: they exist only as benchmark reference and must
    # never be reachable as if they were part of the tracked market.
    assets = (
        session.execute(
            select(DimAsset)
            .where(DimAsset.rwa_tier != RwaTier.NON_RWA)
            .where(
                or_(
                    DimAsset.symbol.ilike(pattern),
                    DimAsset.name.ilike(pattern),
                    DimAsset.asset_id.ilike(pattern),
                )
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for asset in assets:
        add(
            Named(
                EntityType.ASSET,
                asset.asset_id,
                asset.name or asset.symbol,
                asset.chain,
            ),
            haystack=f"{asset.symbol} {asset.name or ''}",
        )

    issuers = (
        session.execute(
            select(DimIssuer).where(DimIssuer.name.ilike(pattern)).limit(limit)
        )
        .scalars()
        .all()
    )
    for issuer in issuers:
        add(
            Named(EntityType.ISSUER, issuer.issuer_id, issuer.name),
            haystack=issuer.name,
        )

    venues = (
        session.execute(
            select(DimVenue)
            .where(or_(DimVenue.name.ilike(pattern), DimVenue.aliases.ilike(pattern)))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for venue in venues:
        add(
            Named(EntityType.VENUE, venue.venue_id, venue.name, venue.venue_type.value),
            haystack=venue.name,
        )

    contracts = (
        session.execute(
            select(DimPerpContract)
            .where(DimPerpContract.symbol.ilike(pattern))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for contract in contracts:
        where = contract.exchange
        if contract.perp_dex:
            where = f"{contract.exchange} · {contract.perp_dex}"
        add(
            Named(
                EntityType.PERP_CONTRACT, contract.contract_id, contract.symbol, where
            ),
            haystack=contract.symbol,
        )

    themes = (
        session.execute(
            select(DimTheme)
            .where(
                or_(DimTheme.name_zh.ilike(pattern), DimTheme.name_en.ilike(pattern))
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for theme in themes:
        add(
            Named(EntityType.THEME, theme.theme_id, theme.name_zh, theme.name_en),
            haystack=f"{theme.name_zh} {theme.name_en}",
        )

    found.sort(key=lambda h: (-h.score, h.name))
    return found[:limit]


#: Ranking between kinds when the text match is equally good. The underlying wins:
#: it is the entity every other kind drills into.
_TYPE_WEIGHT: dict[EntityType, float] = {
    EntityType.UNDERLYING: 0.30,
    EntityType.THEME: 0.20,
    EntityType.ASSET: 0.15,
    EntityType.ISSUER: 0.10,
    EntityType.VENUE: 0.10,
    EntityType.PERP_CONTRACT: 0.05,
}


def _score(haystack: str, needle: str, entity_type: EntityType) -> float:
    text = haystack.lower()
    if text == needle:
        base = 1.0
    elif text.startswith(needle):
        base = 0.8
    elif f" {needle}" in text:
        base = 0.6
    else:
        base = 0.4
    return round(base + _TYPE_WEIGHT.get(entity_type, 0.0), 4)


__all__ = ["KIND_LABELS", "build_status_bar", "href_for", "router"]
