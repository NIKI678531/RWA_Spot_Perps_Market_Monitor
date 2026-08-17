"""Spot venues and the pair-level detail behind them."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query

from app.api.deps import DatasetDep, Limit
from app.core.metrics import MetricScope
from app.models.enums import RwaTier, VenueType
from app.schemas.common import Amount, Meta
from app.schemas.market import (
    ConcentrationSummary,
    PairList,
    PairRow,
    VenueRanking,
    VenueRow,
)
from app.services.analytics import concentration
from app.services.normalize.quality import Pair, screen
from app.services.report.dataset import (
    ReportDataset,
    amount_of,
    group_sum,
    sort_by_amount,
)
from app.services.report.dataset import PairRow as PairFact

router = APIRouter(tags=["spot"])

_SPOT = MetricScope.SPOT_VOLUME

_VENUE_NOTE = (
    "Ranked on quality-adjusted turnover, with raw shown alongside. Ranking on raw "
    "would put a venue whose quotes are almost entirely flagged near the top: one "
    "venue in the reference data reports ~$29.3mn raw against ~$216 adjusted. "
    "NON_RWA assets are excluded from every figure here."
)


@router.get("/spot/venues", response_model=VenueRanking)
def venues(
    data: DatasetDep,
    venue_type: VenueType | None = Query(default=None),
    limit: Limit = 200,
) -> VenueRanking:
    pairs = [
        p
        for p in data.scoped_pairs
        if venue_type is None or (p.venue and p.venue.venue_type is venue_type)
    ]
    raw = group_sum(
        pairs, lambda p: p.snapshot.venue_id, lambda p: p.snapshot.raw_vol_24h, _SPOT
    )
    adjusted = group_sum(
        pairs,
        lambda p: p.snapshot.venue_id,
        lambda p: p.snapshot.adjusted_vol_24h,
        _SPOT,
    )
    registry = {v.venue_id: v for v in data.venues}

    pair_counts: dict[str, int] = {}
    flagged: dict[str, int] = {}
    underlyings: dict[str, set[str]] = {}
    divergent: dict[str, bool] = {}
    by_venue: dict[str, list[Pair]] = {}
    for pair in pairs:
        vid = pair.snapshot.venue_id
        pair_counts[vid] = pair_counts.get(vid, 0) + 1
        if pair.snapshot.is_quality_anomaly or pair.snapshot.is_quality_stale:
            flagged[vid] = flagged.get(vid, 0) + 1
        if pair.asset.underlying_id:
            underlyings.setdefault(vid, set()).add(pair.asset.underlying_id)
        by_venue.setdefault(vid, []).append(
            Pair(
                pair_id=f"{pair.snapshot.asset_id}@{vid}",
                volume_usd=pair.snapshot.raw_vol_24h,
                is_quality_anomaly=pair.snapshot.is_quality_anomaly,
                is_quality_stale=pair.snapshot.is_quality_stale,
            )
        )
    for vid, screened in by_venue.items():
        divergent[vid] = screen(screened).is_materially_divergent

    total = sum(
        (v.amount for v in adjusted.values() if v.amount is not None), start=Decimal(0)
    )
    keys = sort_by_amount(list(adjusted), adjusted)[:limit]

    rows = []
    for rank, key in enumerate(keys, start=1):
        venue = registry.get(key)
        value = amount_of(adjusted, key)
        rows.append(
            VenueRow(
                rank=rank,
                venue_id=key,
                name=venue.name if venue else key,
                venue_type=venue.venue_type if venue else None,
                chain=venue.chain if venue else None,
                raw_vol_24h=Amount.raw(amount_of(raw, key), _SPOT),
                adjusted_vol_24h=Amount.of(adjusted[key]),
                share_of_adjusted=(
                    float(value / total) if value is not None and total else None
                ),
                pair_count=pair_counts.get(key, 0),
                underlying_count=len(underlyings.get(key, set())),
                flagged_pairs=flagged.get(key, 0),
                materially_divergent=divergent.get(key, False),
            )
        )

    return VenueRanking(
        meta=Meta(
            as_of=data.as_of,
            scopes=[_SPOT],
            note=_VENUE_NOTE,
            row_count=len(rows),
        ),
        rows=rows,
        concentration=_concentration(data),
    )


def _concentration(data: ReportDataset) -> list[ConcentrationSummary]:
    """HHI and Top-N by segment.

    A ranking cannot tell a market whose leader holds 30% from one whose leader holds
    85%. Both read as an ordered list; only one is worth entering.
    """
    segments: dict[str, list[PairFact]] = {"all": list(data.scoped_pairs)}
    for pair in data.scoped_pairs:
        if pair.venue:
            segments.setdefault(pair.venue.venue_type.value, []).append(pair)

    summaries = []
    for segment, members in sorted(segments.items()):
        adjusted = group_sum(
            members,
            lambda p: p.snapshot.venue_id,
            lambda p: p.snapshot.adjusted_vol_24h,
            _SPOT,
        )
        if not adjusted:
            continue
        keys = list(adjusted)
        result = concentration.compute([adjusted[k] for k in keys], keys)
        summaries.append(
            ConcentrationSummary(
                segment=segment,
                venue_count=len(keys),
                hhi=float(result.hhi),
                top1_share=_share(result, 1),
                top3_share=_share(result, 3),
                top5_share=_share(result, 5),
                is_concentrated=result.is_concentrated,
            )
        )
    return summaries


def _share(result: concentration.Concentration, n: int) -> float | None:
    value = result.top_n_share(n).value
    return float(value) if value is not None else None


@router.get("/spot/pairs", response_model=PairList)
def pairs(
    data: DatasetDep,
    venue_id: str | None = Query(default=None),
    issuer_id: str | None = Query(default=None),
    underlying_id: str | None = Query(default=None),
    rwa_tier: RwaTier | None = Query(default=None),
    flagged_only: bool = Query(
        default=False,
        description="Only pairs the data provider marked anomalous or stale.",
    ),
    limit: Limit = 200,
) -> PairList:
    selected = [
        p
        for p in data.scoped_pairs
        if (venue_id is None or p.snapshot.venue_id == venue_id)
        and (issuer_id is None or p.asset.issuer_id == issuer_id)
        and (underlying_id is None or p.asset.underlying_id == underlying_id)
        and (rwa_tier is None or p.asset.rwa_tier is rwa_tier)
        and (
            not flagged_only
            or p.snapshot.is_quality_anomaly
            or p.snapshot.is_quality_stale
        )
    ]
    # Unobserved turnover sorts last rather than to zero's position: it is unknown,
    # not idle.
    selected.sort(
        key=lambda p: (
            p.snapshot.raw_vol_24h is None,
            -(p.snapshot.raw_vol_24h or Decimal(0)),
        )
    )

    rows = [
        PairRow(
            asset_id=p.snapshot.asset_id,
            symbol=p.asset.symbol,
            rwa_tier=p.asset.rwa_tier,
            underlying_id=p.asset.underlying_id,
            issuer_id=p.asset.issuer_id,
            venue_id=p.snapshot.venue_id,
            venue=p.venue_name,
            venue_type=p.venue.venue_type if p.venue else None,
            raw_vol_24h=Amount.raw(p.snapshot.raw_vol_24h, _SPOT),
            adjusted_vol_24h=Amount.raw(p.snapshot.adjusted_vol_24h, _SPOT),
            price_usd=p.snapshot.price_usd,
            spread_pct=p.snapshot.spread_pct,
            trust_score=p.snapshot.trust_score,
            is_quality_anomaly=p.snapshot.is_quality_anomaly,
            is_quality_stale=p.snapshot.is_quality_stale,
        )
        for p in selected[:limit]
    ]
    return PairList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[_SPOT],
            note=(
                "is_quality_anomaly and is_quality_stale are the data provider's view "
                "of the quote, not this system's demand alerts. A flagged pair keeps "
                "its raw volume and drops out of the adjusted figure."
            ),
            row_count=len(rows),
        ),
        rows=rows,
    )
