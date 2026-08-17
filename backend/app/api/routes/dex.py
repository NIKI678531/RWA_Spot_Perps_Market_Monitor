"""DEX pools — the only place the data says which *direction* customers traded."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query

from app.api.deps import DatasetDep, Limit
from app.core.metrics import MetricScope
from app.schemas.common import Amount, Meta
from app.schemas.market import PoolList, PoolRow

router = APIRouter(tags=["dex"])


@router.get("/dex/pools", response_model=PoolList)
def pools(
    data: DatasetDep,
    network: str | None = Query(default=None),
    dex: str | None = Query(default=None),
    asset_id: str | None = Query(default=None),
    limit: Limit = 200,
) -> PoolList:
    # ``scoped_pools``, not ``pools``: GeckoTerminal search returns every pool whose
    # name matched the query, so the unmapped remainder is unidentified liquidity
    # rather than tokenized-asset liquidity, and ranking it here would put it in front
    # of a reader as if it were the latter.
    selected = [
        row
        for row in data.scoped_pools
        if (network is None or row.pool.network == network)
        and (dex is None or row.pool.dex == dex)
        and (asset_id is None or row.pool.base_asset_id == asset_id)
    ]
    selected.sort(
        key=lambda r: (
            r.snapshot.reserve_usd is None,
            -(r.snapshot.reserve_usd or Decimal(0)),
        )
    )

    rows = [
        PoolRow(
            pool_id=row.pool.pool_id,
            network=row.pool.network,
            dex=row.pool.dex,
            base_symbol=row.base_asset.symbol if row.base_asset else None,
            quote_token=row.pool.quote_token,
            is_canonical_quote=row.pool.is_canonical_quote,
            reserve_usd=Amount.raw(row.snapshot.reserve_usd, MetricScope.DEX_LIQUIDITY),
            vol_24h=Amount.raw(row.snapshot.vol_24h, MetricScope.SPOT_VOLUME),
            buys_24h=row.snapshot.buys_24h,
            sells_24h=row.snapshot.sells_24h,
            buy_ratio=_buy_ratio(row.snapshot.buys_24h, row.snapshot.sells_24h),
        )
        for row in selected[:limit]
    ]
    return PoolList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[MetricScope.DEX_LIQUIDITY, MetricScope.SPOT_VOLUME],
            note=(
                "reserve_usd is a stock and vol_24h is a flow; they share a currency "
                "and nothing else, so they need separate axes. buy_ratio is the only "
                "direction-bearing figure in the system — turnover says somebody "
                "traded, this says whether they were buying."
            ),
            row_count=len(rows),
        ),
        rows=rows,
    )


def _buy_ratio(buys: int | None, sells: int | None) -> float | None:
    """Buys over total trades, or ``None`` when the split was not observed."""
    if buys is None or sells is None:
        return None
    total = buys + sells
    if total == 0:
        return None
    return buys / total
