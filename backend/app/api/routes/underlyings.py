"""The underlying catalogue: the list you drill from, and the 360 you land on.

"Is anyone buying the S&P 500?" cannot be answered from a token symbol. SPY arrives
as SPYB, SPYx and SPY-ON from three issuers on four venues, plus a perpetual. These
two endpoints are where those rows become one answer — reported by scope, never
totalled across scopes.

``GET /underlyings`` is the T2 listing behind the filter chips, and it is the only
way into ``GET /underlyings/{id}``: Underlying 360 is an entity page reached by
drilling down, not a navigation destination, so something has to be the list you
drill *from*. The 360 response adds the U360-003 block — our shelf position, the
open candidates, the open data gaps and who wrapped the underlying before we did —
because "what is it doing" and "where do we stand on it" are read in one sitting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypeAlias

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DatasetDep, Limit, SessionDep
from app.api.naming import Resolver
from app.api.routes.alerts import alert_row
from app.api.routes.candidates import candidate_row
from app.api.routes.governance import coverage_row, gap_row
from app.core.metrics import MetricScope
from app.models.alerts import Alert
from app.models.dimensions import DimAsset
from app.models.enums import (
    CLOSED_ALERT_STATES,
    AssetClass,
    CoverageState,
    EntityType,
)
from app.models.facts import FactLaunchWindowSnapshot
from app.models.workflow import DataGap, IssuanceCandidate, ProductCoverage
from app.schemas.common import Amount, Meta
from app.schemas.market import (
    AlertRow,
    FirstListingRow,
    PerpExposureRow,
    Underlying360,
    UnderlyingList,
    UnderlyingRow,
    UnderlyingSort,
    VenueBreakdownRow,
    WrapperRow,
)
from app.schemas.workflow import CandidateRow, CoverageRow, DataGapRow
from app.services.report.dataset import (
    ReportDataset,
    UnderlyingAggregates,
    group_sum,
    scoped,
    sort_by_amount,
)
from app.services.workflow import data_gap as gap_service

router = APIRouter(tags=["underlying"])

#: ``(unobserved, -amount, name)``. The leading flag is what pushes a figure we never
#: observed below every figure we did, whatever its size.
_SortKey: TypeAlias = tuple[bool, Decimal, str]

_SPOT = MetricScope.SPOT_VOLUME

_SCOPES = [
    MetricScope.SPOT_MARKET_CAP,
    MetricScope.SPOT_VOLUME,
    MetricScope.PERP_VOLUME,
    MetricScope.PERP_OI,
]

_SCOPE_NOTE = (
    "现货成交与永续成交为不同口径，页面并列展示，不提供合计。"
    "市值为存量、成交为流量、持仓量为存量，三者不可相加。"
)

_LIST_NOTE = (
    "四类口径并列展示，排序一次只作用于其中一类，不存在跨口径的综合排名。"
    "未观测到的口径排在该排序的末尾，不按 0 处理。"
)

#: Which scope each ordering ranks by. The mapping exists so the response's declared
#: scope and its row order cannot drift apart: adding an ordering without deciding
#: what scope it belongs to fails here rather than shipping an unlabelled ranking.
_SORT_SCOPE: dict[UnderlyingSort, MetricScope | None] = {
    UnderlyingSort.SPOT_VOLUME: MetricScope.SPOT_VOLUME,
    UnderlyingSort.SPOT_MARKET_CAP: MetricScope.SPOT_MARKET_CAP,
    UnderlyingSort.PERP_VOLUME: MetricScope.PERP_VOLUME,
    UnderlyingSort.PERP_OI: MetricScope.PERP_OI,
    UnderlyingSort.NAME: None,
}


@router.get("/underlyings", response_model=UnderlyingList)
def underlyings(
    data: DatasetDep,
    session: SessionDep,
    q: str | None = Query(
        default=None,
        description="Substring match on the id or the display name. Case-insensitive.",
    ),
    asset_class: AssetClass | None = Query(default=None),
    region: str | None = Query(default=None),
    theme_id: str | None = Query(default=None),
    is_pre_ipo: bool | None = Query(default=None),
    has_perp: bool | None = Query(
        default=None, description="Only underlyings with (or without) a perpetual."
    ),
    coverage_state: CoverageState | None = Query(
        default=None, description="Our shelf position against this underlying."
    ),
    sort: UnderlyingSort = Query(default=UnderlyingSort.SPOT_VOLUME),
    limit: Limit = 200,
) -> UnderlyingList:
    counts = _counts(data)
    demand = UnderlyingAggregates(data)
    coverage = _coverage_by_underlying(session)
    open_alerts = _open_alert_counts(session)

    needle = q.strip().lower() if q else None
    rows: list[UnderlyingRow] = []
    for record in data.underlyings:
        count = counts.get(record.underlying_id, _Counts())
        state = coverage.get(record.underlying_id)

        if needle and not _matches(needle, record.underlying_id, record.name):
            continue
        if asset_class is not None and record.asset_class is not asset_class:
            continue
        if region is not None and record.region != region:
            continue
        if theme_id is not None and record.theme_id != theme_id:
            continue
        if is_pre_ipo is not None and bool(record.is_pre_ipo) is not is_pre_ipo:
            continue
        if has_perp is not None and count.has_perp is not has_perp:
            continue
        if coverage_state is not None and state is not coverage_state:
            continue

        uid = record.underlying_id
        rows.append(
            UnderlyingRow(
                underlying_id=uid,
                name=record.name,
                asset_class=record.asset_class,
                region=record.region,
                is_pre_ipo=record.is_pre_ipo,
                theme_id=record.theme_id,
                wrapper_count=count.wrappers,
                issuer_count=count.issuers,
                venue_count=count.venues,
                has_perp=count.has_perp,
                spot_market_cap=Amount.of(
                    demand.market_cap.get(
                        uid, scoped(None, MetricScope.SPOT_MARKET_CAP)
                    )
                ),
                spot_vol_raw=Amount.of(demand.spot_raw.get(uid, scoped(None, _SPOT))),
                spot_vol_adjusted=Amount.of(
                    demand.spot_adjusted.get(uid, scoped(None, _SPOT))
                ),
                perp_vol_24h=Amount.of(
                    demand.perp_volume.get(uid, scoped(None, MetricScope.PERP_VOLUME))
                ),
                perp_oi_usd=Amount.of(
                    demand.perp_oi.get(uid, scoped(None, MetricScope.PERP_OI))
                ),
                open_alert_count=open_alerts.get(uid, 0),
                coverage_state=state,
            )
        )

    rows.sort(key=_sort_key(sort))
    rows = rows[:limit]

    scope = _SORT_SCOPE[sort]
    return UnderlyingList(
        meta=Meta(
            as_of=data.as_of,
            # All four travel on every row, so the client knows to split axes even
            # when it is only ranking by one of them.
            scopes=_SCOPES,
            note=_LIST_NOTE
            + (f" 当前排序口径：{scope.value}。" if scope is not None else ""),
            row_count=len(rows),
        ),
        sort=sort,
        rows=rows,
    )


@router.get("/underlyings/{underlying_id}", response_model=Underlying360)
def underlying_detail(
    underlying_id: str, data: DatasetDep, session: SessionDep
) -> Underlying360:
    record = next(
        (u for u in data.underlyings if u.underlying_id == underlying_id), None
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"unknown underlying {underlying_id!r}"
        )

    demand = UnderlyingAggregates(data)
    wrappers = [a for a in data.scoped_assets if a.asset.underlying_id == underlying_id]
    wrapper_ids = {a.asset.asset_id for a in wrappers}
    pairs = [p for p in data.scoped_pairs if p.snapshot.asset_id in wrapper_ids]
    perps = [
        r
        for r in data.scoped_perp_contracts
        if r.contract and r.contract.underlying_id == underlying_id
    ]

    venue_totals = group_sum(
        pairs,
        lambda p: p.snapshot.venue_id,
        lambda p: p.snapshot.adjusted_vol_24h,
        _SPOT,
    )
    venues = {v.venue_id: v for v in data.venues}
    resolver = Resolver(session)

    return Underlying360(
        meta=Meta(
            as_of=data.as_of,
            scopes=_SCOPES,
            note=_SCOPE_NOTE,
            row_count=len(wrappers),
        ),
        underlying_id=underlying_id,
        name=record.name,
        asset_class=record.asset_class,
        region=record.region,
        is_pre_ipo=record.is_pre_ipo,
        theme_id=record.theme_id,
        benchmark_id=record.benchmark_id,
        tokenized_wrappers=[
            WrapperRow(
                asset_id=a.asset.asset_id,
                symbol=a.asset.symbol,
                issuer=a.issuer.name if a.issuer else None,
                chain=a.asset.chain,
                rwa_tier=a.asset.rwa_tier,
                market_cap=Amount.raw(
                    a.snapshot.market_cap if a.snapshot else None,
                    MetricScope.SPOT_MARKET_CAP,
                ),
                vol_24h=Amount.raw(a.snapshot.vol_24h if a.snapshot else None, _SPOT),
            )
            for a in sorted(wrappers, key=lambda a: a.asset.symbol)
        ],
        venue_breakdown=[
            VenueBreakdownRow(
                venue_id=key,
                venue=venues[key].name if key in venues else key,
                venue_type=venues[key].venue_type if key in venues else None,
                adjusted_vol_24h=Amount.of(venue_totals[key]),
            )
            for key in sort_by_amount(list(venue_totals), venue_totals)
        ],
        perp_exposure=[
            PerpExposureRow(
                exchange=r.exchange,
                perp_dex=r.perp_dex or "core",
                contract=r.symbol,
                vol_24h=Amount.raw(r.snapshot.vol_24h, MetricScope.PERP_VOLUME),
                open_interest_usd=Amount.raw(r.snapshot.oi_usd, MetricScope.PERP_OI),
            )
            for r in sorted(
                perps,
                key=lambda r: (
                    r.snapshot.vol_24h is None,
                    -(r.snapshot.vol_24h or Decimal(0)),
                ),
            )
        ],
        spot_market_cap=Amount.of(
            demand.market_cap.get(
                underlying_id, scoped(None, MetricScope.SPOT_MARKET_CAP)
            )
        ),
        spot_vol_adjusted=Amount.of(
            demand.spot_adjusted.get(underlying_id, scoped(None, _SPOT))
        ),
        perp_vol_24h=Amount.of(
            demand.perp_volume.get(underlying_id, scoped(None, MetricScope.PERP_VOLUME))
        ),
        perp_oi_usd=Amount.of(
            demand.perp_oi.get(underlying_id, scoped(None, MetricScope.PERP_OI))
        ),
        scope_note=_SCOPE_NOTE,
        active_alerts=_alerts(session, underlying_id),
        our_coverage=_coverage(session, underlying_id, resolver),
        candidates=_candidates(session, underlying_id, resolver),
        open_data_gaps=_gaps(session, underlying_id, wrapper_ids),
        first_listings=_first_listings(session, wrapper_ids, resolver),
    )


# --- helpers -----------------------------------------------------------------


def _matches(needle: str, *fields: str | None) -> bool:
    """Typeahead match on any of an entity's names.

    Both the id and the display name, because people search for "SPY" and for
    "标普500" and neither spelling is more correct than the other.
    """
    return any(field and needle in field.lower() for field in fields)


@dataclass(frozen=True, slots=True)
class _Counts:
    """How many distinct wrappers, issuers and venues carry one underlying."""

    wrappers: int = 0
    issuers: int = 0
    venues: int = 0
    has_perp: bool = False


def _counts(data: ReportDataset) -> dict[str, _Counts]:
    """Distinct-count the catalogue in one pass over the scoped rows.

    Counted from ``scoped_*`` rather than from the dimension tables, so the figures
    describe what is in scope and observable right now — a wrapper screened out by
    ``rwa_tier`` is not a wrapper this market has (rule 11).
    """
    underlying_of: dict[str, str] = {}
    wrappers: dict[str, set[str]] = {}
    issuers: dict[str, set[str]] = {}
    venues: dict[str, set[str]] = {}
    perp: set[str] = set()

    for row in data.scoped_assets:
        uid = row.asset.underlying_id
        if uid is None:
            continue
        underlying_of[row.asset.asset_id] = uid
        wrappers.setdefault(uid, set()).add(row.asset.asset_id)
        if row.asset.issuer_id:
            issuers.setdefault(uid, set()).add(row.asset.issuer_id)

    for pair in data.scoped_pairs:
        uid = underlying_of.get(pair.snapshot.asset_id)
        if uid is not None:
            venues.setdefault(uid, set()).add(pair.snapshot.venue_id)

    for contract in data.scoped_perp_contracts:
        if contract.contract and contract.contract.underlying_id:
            perp.add(contract.contract.underlying_id)

    keys = set(wrappers) | set(venues) | perp
    return {
        uid: _Counts(
            wrappers=len(wrappers.get(uid, ())),
            issuers=len(issuers.get(uid, ())),
            venues=len(venues.get(uid, ())),
            has_perp=uid in perp,
        )
        for uid in keys
    }


def _sort_key(sort: UnderlyingSort) -> Callable[[UnderlyingRow], _SortKey]:
    """Order the catalogue by one scope, with unobserved rows last.

    A missing figure sorts to the bottom rather than to zero: "we did not observe
    this" and "this traded nothing" are different statements, and only the second one
    belongs at the bottom of a ranking on merit (rule 4).
    """
    if sort is UnderlyingSort.NAME:
        return lambda row: (False, Decimal(0), row.name)

    field = {
        UnderlyingSort.SPOT_VOLUME: "spot_vol_adjusted",
        UnderlyingSort.SPOT_MARKET_CAP: "spot_market_cap",
        UnderlyingSort.PERP_VOLUME: "perp_vol_24h",
        UnderlyingSort.PERP_OI: "perp_oi_usd",
    }[sort]

    def key(row: UnderlyingRow) -> _SortKey:
        amount: Amount = getattr(row, field)
        return (amount.value is None, -(amount.value or Decimal(0)), row.name)

    return key


def _coverage_by_underlying(session: Session) -> dict[str, CoverageState]:
    """Shelf state per underlying, for the list's chip filter."""
    rows = session.execute(
        select(ProductCoverage.underlying_id, ProductCoverage.state)
    ).all()
    return {underlying_id: state for underlying_id, state in rows}


def _open_alert_counts(session: Session) -> dict[str, int]:
    """Open, published alerts per underlying.

    ``published_at IS NOT NULL`` for the same reason it appears on every other read
    path: a detector output that never crossed the publication gate is not an alert,
    and a badge counting one would send a reader looking for a row that does not
    exist anywhere in the product (rule 14).
    """
    stmt = (
        select(Alert.entity_id, func.count())
        .where(
            Alert.entity_type == EntityType.UNDERLYING,
            Alert.published_at.isnot(None),
            Alert.state.notin_(CLOSED_ALERT_STATES),
        )
        .group_by(Alert.entity_id)
    )
    return {entity_id: int(count) for entity_id, count in session.execute(stmt).all()}


def _alerts(session: Session, underlying_id: str) -> list[AlertRow]:
    """Open alerts naming this underlying.

    Only ``UNDERLYING``-scoped alerts: an alert on one wrapper is about that wrapper's
    listing, and surfacing it here would attribute a venue problem to the security.
    """
    stmt = (
        select(Alert)
        .where(
            Alert.entity_type == EntityType.UNDERLYING,
            Alert.entity_id == underlying_id,
            # Same predicate as the badge count above. Two ways of asking "is this
            # still open" is how a page ends up disagreeing with its own header.
            Alert.state.notin_(CLOSED_ALERT_STATES),
            # Unpublished findings are not alerts yet; see publication.evaluate().
            Alert.published_at.isnot(None),
        )
        .order_by(Alert.score.desc(), Alert.last_seen_ts.desc())
    )
    return [alert_row(a) for a in session.execute(stmt).scalars().all()]


def _coverage(
    session: Session, underlying_id: str, resolver: Resolver
) -> CoverageRow | None:
    """Our shelf position, or null when nobody has assessed one.

    Null rather than a synthesised ``GAP``: an unassessed underlying and one assessed
    as uncovered look identical on screen otherwise, and only the second is a finding.
    """
    row = session.execute(
        select(ProductCoverage).where(ProductCoverage.underlying_id == underlying_id)
    ).scalar_one_or_none()
    return coverage_row(row, resolver) if row is not None else None


def _candidates(
    session: Session, underlying_id: str, resolver: Resolver
) -> list[CandidateRow]:
    """Issuance candidates for this underlying, newest review first.

    Every stage, including the terminal ones — unlike ``GET /candidates``, which is a
    work queue and hides what is closed. This is an entity page, and "we declined this
    in June, and here is who signed it" is precisely what somebody about to re-raise
    it needs to read. There is at most one row today (``product_coverage`` and
    ``issuance_candidate`` are both unique per underlying), but the field is a list so
    that relaxing the constraint is a data change rather than an API break.
    """
    rows = (
        session.execute(
            select(IssuanceCandidate)
            .where(IssuanceCandidate.underlying_id == underlying_id)
            .order_by(
                IssuanceCandidate.last_reviewed_at.is_(None),
                IssuanceCandidate.last_reviewed_at.desc(),
            )
        )
        .scalars()
        .all()
    )
    return [candidate_row(row, resolver) for row in rows]


def _gaps(
    session: Session, underlying_id: str, wrapper_ids: set[str]
) -> list[DataGapRow]:
    """Open data gaps that weaken anything on this page.

    Wrapper-scoped gaps are included as well as underlying-scoped ones: a stale
    snapshot on SPYx is the reason the SPY turnover figure above is soft, and filing
    it under the wrapper does not make it someone else's problem.
    """
    entity_ids = [underlying_id, *sorted(wrapper_ids)]
    rows = (
        session.execute(
            select(DataGap)
            .where(
                DataGap.status.in_(gap_service.OPEN_STATUSES),
                DataGap.entity_type.in_(
                    (EntityType.UNDERLYING, EntityType.ASSET, EntityType.PAIR)
                ),
                DataGap.entity_id.in_(entity_ids),
            )
            .order_by(DataGap.is_blocking.desc(), DataGap.last_seen_ts.desc())
        )
        .scalars()
        .all()
    )
    return [gap_row(row) for row in rows]


def _first_listings(
    session: Session, wrapper_ids: set[str], resolver: Resolver
) -> list[FirstListingRow]:
    """Who wrapped this underlying first, earliest first.

    Two sources, and the row says which one it used. A launch-window snapshot carries
    a measured ``listed_at``; without one the fallback is when the wrapper entered our
    catalogue, which is a first-*observation* and is labelled as such. No free source
    publishes an announced listing date, and presenting our own crawl time as one
    would put a wrong figure into a competitive brief.
    """
    if not wrapper_ids:
        return []

    measured: dict[str, tuple[datetime, int | None]] = {}
    windows = (
        session.execute(
            select(FactLaunchWindowSnapshot)
            .where(FactLaunchWindowSnapshot.asset_id.in_(sorted(wrapper_ids)))
            .order_by(FactLaunchWindowSnapshot.snapshot_ts.desc())
        )
        .scalars()
        .all()
    )
    for window in windows:
        # Newest snapshot per asset wins, and the newest is also the widest window:
        # the 24h row is measured last, so its venue count is the settled one. Later
        # snapshots correct earlier ones rather than adding to them (rule: fact
        # tables are append-only).
        measured.setdefault(window.asset_id, (window.listed_at, window.venue_count))

    assets = (
        session.execute(select(DimAsset).where(DimAsset.asset_id.in_(wrapper_ids)))
        .scalars()
        .all()
    )

    rows = []
    for asset in assets:
        found = measured.get(asset.asset_id)
        rows.append(
            FirstListingRow(
                asset_id=asset.asset_id,
                symbol=asset.symbol,
                issuer_id=asset.issuer_id,
                issuer=(
                    resolver.name(EntityType.ISSUER, asset.issuer_id)
                    if asset.issuer_id
                    else None
                ),
                first_seen_at=found[0] if found else asset.created_at,
                is_measured=found is not None,
                venue_count=found[1] if found else None,
            )
        )
    rows.sort(key=lambda row: (row.first_seen_at, row.symbol))
    return rows


__all__ = ["router", "underlying_detail"]
