"""Issuer competition, and where each issuer's products actually trade."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException

from app.api.deps import DatasetDep
from app.core.metrics import MetricScope
from app.schemas.common import Amount, Meta
from app.schemas.market import IssuerList, IssuerRow, IssuerVenueCell, IssuerVenues
from app.services.report.dataset import (
    ReportDataset,
    group_sum,
    scoped,
    sort_by_amount,
)

router = APIRouter(tags=["issuers"])

_SPOT = MetricScope.SPOT_VOLUME

_COVERAGE_NOTE = (
    "index_coverage divides the assets we index by the issuer's own published "
    "product count, not the other way round. xStocks publishes about 640 products "
    "against roughly 113 indexed by the aggregator; treating the indexed count as "
    "the market size understates that issuer about 5.7x."
)


@router.get("/issuers", response_model=IssuerList)
def issuers(data: DatasetDep) -> IssuerList:
    market_cap = group_sum(
        data.scoped_assets,
        lambda a: a.asset.issuer_id,
        lambda a: a.snapshot.market_cap if a.snapshot else None,
        MetricScope.SPOT_MARKET_CAP,
    )
    adjusted = group_sum(
        data.scoped_pairs,
        lambda p: p.asset.issuer_id,
        lambda p: p.snapshot.adjusted_vol_24h,
        _SPOT,
    )
    registry = {i.issuer_id: i for i in data.issuers}
    counts = _indexed_counts(data)

    keys = sort_by_amount(
        list(set(registry) | set(market_cap) | set(adjusted)), adjusted
    )
    rows = [
        IssuerRow(
            rank=rank,
            issuer_id=key,
            name=registry[key].name if key in registry else key,
            indexed_asset_count=counts.get(key, 0),
            official_product_count=(
                registry[key].official_product_count if key in registry else None
            ),
            index_coverage=_coverage_ratio(
                counts.get(key, 0),
                registry[key].official_product_count if key in registry else None,
            ),
            market_cap=Amount.of(
                market_cap.get(key, scoped(None, MetricScope.SPOT_MARKET_CAP))
            ),
            adjusted_vol_24h=Amount.of(adjusted.get(key, scoped(None, _SPOT))),
            legal_structure_note=(
                registry[key].legal_structure_note if key in registry else None
            ),
        )
        for rank, key in enumerate(keys, start=1)
    ]
    return IssuerList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[MetricScope.SPOT_MARKET_CAP, _SPOT],
            note=_COVERAGE_NOTE,
            row_count=len(rows),
        ),
        rows=rows,
    )


@router.get("/issuers/{issuer_id}/venues", response_model=IssuerVenues)
def issuer_venues(issuer_id: str, data: DatasetDep) -> IssuerVenues:
    registry = {i.issuer_id: i for i in data.issuers}
    if issuer_id not in registry:
        raise HTTPException(status_code=404, detail=f"unknown issuer {issuer_id!r}")

    pairs = [p for p in data.scoped_pairs if p.asset.issuer_id == issuer_id]
    adjusted = group_sum(
        pairs,
        lambda p: p.snapshot.venue_id,
        lambda p: p.snapshot.adjusted_vol_24h,
        _SPOT,
    )
    counts: dict[str, int] = {}
    for pair in pairs:
        vid = pair.snapshot.venue_id
        counts[vid] = counts.get(vid, 0) + 1

    venues = {v.venue_id: v for v in data.venues}
    rows = [
        IssuerVenueCell(
            venue_id=key,
            venue=venues[key].name if key in venues else key,
            venue_type=venues[key].venue_type if key in venues else None,
            adjusted_vol_24h=Amount.of(adjusted[key]),
            pair_count=counts.get(key, 0),
        )
        for key in sort_by_amount(list(adjusted), adjusted)
    ]
    return IssuerVenues(
        meta=Meta(
            as_of=data.as_of,
            scopes=[_SPOT],
            note=(
                "One issuer's distribution across venues. A product listed everywhere "
                "and traded nowhere and a product concentrated on one venue are "
                "different competitive positions that a single volume figure hides."
            ),
            row_count=len(rows),
        ),
        issuer_id=issuer_id,
        name=registry[issuer_id].name,
        rows=rows,
    )


def _indexed_counts(data: ReportDataset) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in data.scoped_assets:
        if asset.asset.issuer_id:
            counts[asset.asset.issuer_id] = counts.get(asset.asset.issuer_id, 0) + 1
    return counts


def _coverage_ratio(indexed: int, official: int | None) -> float | None:
    """Indexed over published, or ``None`` when the issuer publishes no count.

    Null rather than 1.0: an unknown denominator makes the ratio unknown, and a
    coverage of 1.0 would read as "we see everything they sell".
    """
    if not official:
        return None
    return float(Decimal(indexed) / Decimal(official))
