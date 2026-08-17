"""Market scale by CoinGecko category."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DatasetDep
from app.core.metrics import MetricScope
from app.schemas.common import Amount, Meta
from app.schemas.market import CategoryRow, CategoryScale

router = APIRouter(tags=["scale"])

_OVERLAP_NOTE = (
    "Tokenized Stock / Tokenized ETF / Ondo / xStocks / bStocks overlap by "
    "construction — one coin can sit in three of them at once. Only the row with "
    "is_additive=true (the deduplicated union) is a valid total; adding the others "
    "roughly 2.7x-counts the market. Charts must not stack or pie these rows."
)


@router.get("/scale/categories", response_model=CategoryScale)
def categories(data: DatasetDep) -> CategoryScale:
    rows = [
        CategoryRow(
            category_id=snapshot.category_id,
            asset_count=snapshot.asset_count,
            market_cap=Amount.raw(snapshot.market_cap, MetricScope.SPOT_MARKET_CAP),
            vol_24h=Amount.raw(snapshot.vol_24h, MetricScope.SPOT_VOLUME),
            is_additive=snapshot.is_additive,
        )
        # Additive first: the one row a reader may legitimately quote as the market
        # size should not have to be hunted for among the five that overlap.
        for snapshot in sorted(
            data.categories, key=lambda c: (not c.is_additive, c.category_id)
        )
    ]
    return CategoryScale(
        meta=Meta(
            as_of=data.as_of,
            scopes=[MetricScope.SPOT_MARKET_CAP, MetricScope.SPOT_VOLUME],
            note=_OVERLAP_NOTE,
            row_count=len(rows),
        ),
        rows=rows,
        overlap_note=_OVERLAP_NOTE,
    )
