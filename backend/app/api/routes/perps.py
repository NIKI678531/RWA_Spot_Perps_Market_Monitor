"""Cross-venue perpetuals, including Hyperliquid's permissionless HIP-3 perp DEXs.

Aggregators publish a Top 25 and cannot see a permissionless deployment at all, so
the perp DEX list here is enumerated from the exchange directly. A competitor
launching an RWA perp DEX that no aggregator lists is exactly the event this system
exists to notice.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query

from app.api.deps import DatasetDep, Limit
from app.core.metrics import MetricScope
from app.schemas.common import Amount, Meta
from app.schemas.market import (
    PerpContractList,
    PerpContractRow,
    PerpDexList,
    PerpDexRow,
    PerpVenueList,
    PerpVenueRow,
)
from app.services.report.dataset import PerpRow, group_sum, scoped, sort_by_amount

router = APIRouter(tags=["perps"])

_VOL = MetricScope.PERP_VOLUME
_OI = MetricScope.PERP_OI

#: Hyperliquid's own order books, as opposed to a HIP-3 deployment on top of them.
_CORE = "core"

_SCOPE_NOTE = (
    "vol_24h is a flow and open_interest is a stock. They are shown side by side and "
    "never added, and a chart must give them separate axes."
)


@router.get("/perps/venues", response_model=PerpVenueList)
def venues(data: DatasetDep) -> PerpVenueList:
    rows = [
        PerpVenueRow(
            exchange=snapshot.exchange,
            perp_dex=snapshot.perp_dex or _CORE,
            is_hip3=_is_hip3(snapshot.perp_dex),
            segment=snapshot.segment,
            vol_24h=Amount.raw(snapshot.vol_24h, _VOL),
            open_interest_usd=Amount.raw(snapshot.open_interest_usd, _OI),
            symbol_count=snapshot.symbol_count,
            oi_symbol_count=snapshot.oi_symbol_count,
        )
        for snapshot in sorted(
            data.perp_venues,
            key=lambda s: (
                s.vol_24h is None,
                -(s.vol_24h or Decimal(0)),
                s.exchange,
                s.perp_dex,
            ),
        )
    ]
    return PerpVenueList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[_VOL, _OI],
            note=_SCOPE_NOTE,
            row_count=len(rows),
        ),
        rows=rows,
    )


@router.get("/perps/contracts", response_model=PerpContractList)
def contracts(
    data: DatasetDep,
    exchange: str | None = Query(default=None),
    perp_dex: str | None = Query(default=None),
    underlying_id: str | None = Query(default=None),
    limit: Limit = 200,
) -> PerpContractList:
    # ``scoped_perp_contracts``: the collectors enumerate whole exchanges, so this list
    # would otherwise be ranked by Hyperliquid's BTC book and no tokenized-equity
    # contract would appear near the top of it.
    selected = [
        row
        for row in data.scoped_perp_contracts
        if (exchange is None or row.exchange == exchange)
        and (perp_dex is None or (row.perp_dex or _CORE) == perp_dex)
        and (
            underlying_id is None
            or (row.contract and row.contract.underlying_id == underlying_id)
        )
    ]
    volumes = group_sum(
        selected, lambda r: r.snapshot.contract_id, lambda r: r.snapshot.vol_24h, _VOL
    )
    by_id = {r.snapshot.contract_id: r for r in selected}
    keys = sort_by_amount([r.snapshot.contract_id for r in selected], volumes)[:limit]

    rows = []
    for rank, key in enumerate(keys, start=1):
        row = by_id[key]
        contract = row.contract
        rows.append(
            PerpContractRow(
                rank=rank,
                contract_id=key,
                exchange=row.exchange,
                perp_dex=row.perp_dex or _CORE,
                symbol=row.symbol,
                source_underlying_type=(
                    contract.source_underlying_type if contract else None
                ),
                analysis_group=contract.analysis_group if contract else None,
                underlying_id=contract.underlying_id if contract else None,
                vol_24h=Amount.raw(row.snapshot.vol_24h, _VOL),
                open_interest_usd=Amount.raw(row.snapshot.oi_usd, _OI),
                oi_units=row.snapshot.oi_units,
                funding_rate=row.snapshot.funding_rate,
                mark_price=row.snapshot.mark_price,
                index_price=row.snapshot.index_price,
            )
        )
    return PerpContractList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[_VOL, _OI],
            note=(
                "source_underlying_type is the exchange's own label, kept verbatim — "
                "Binance classifies some ETFs and leveraged ETPs as EQUITY. "
                "analysis_group is ours and sits alongside it, never over it. "
                + _SCOPE_NOTE
            ),
            row_count=len(rows),
        ),
        rows=rows,
    )


@router.get("/perps/dexs", response_model=PerpDexList)
def perp_dexs(data: DatasetDep) -> PerpDexList:
    """Every perp DEX we saw contracts on, HIP-3 deployments included.

    Two counts, deliberately. The *rows* are enumerated from every observed contract,
    because a deployment we cannot yet classify is precisely the thing this endpoint
    exists to surface — gating the row list would hide a competitor's launch until
    someone got round to mapping its symbols. The *money* is summed over in-scope
    contracts only, because a rollup mixing tokenized equity with the BTC book is not
    a figure about the RWA market.
    """
    scoped_rows = data.scoped_perp_contracts
    volumes = group_sum(scoped_rows, _dex_key, lambda r: r.snapshot.vol_24h, _VOL)
    interest = group_sum(scoped_rows, _dex_key, lambda r: r.snapshot.oi_usd, _OI)

    observed: dict[str, int] = {}
    for row in data.perp_contracts:
        key = _dex_key(row)
        observed[key] = observed.get(key, 0) + 1
    counts: dict[str, int] = {}
    for row in scoped_rows:
        key = _dex_key(row)
        counts[key] = counts.get(key, 0) + 1

    rows = [
        PerpDexRow(
            perp_dex=key,
            is_hip3=_is_hip3(key),
            contract_count=counts.get(key, 0),
            observed_contract_count=observed[key],
            vol_24h=Amount.of(volumes.get(key, scoped(None, _VOL))),
            open_interest_usd=Amount.of(interest.get(key, scoped(None, _OI))),
        )
        for key in sort_by_amount(list(observed), volumes)
    ]
    return PerpDexList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[_VOL, _OI],
            note=(
                "HIP-3 lets anyone deploy an independent perp DEX under one exchange. "
                "Aggregators list a Top 25 and cannot see a permissionless deployment, "
                "so this list is enumerated from the exchange. contract_count counts "
                "the contracts that resolve to a real-world underlying and is what the "
                "amounts are summed over; observed_contract_count counts everything on "
                "the deployment. " + _SCOPE_NOTE
            ),
            row_count=len(rows),
        ),
        rows=rows,
    )


def _dex_key(row: PerpRow) -> str:
    return row.perp_dex or _CORE


def _is_hip3(perp_dex: str | None) -> bool:
    """A named deployment is HIP-3; the exchange's own books are not."""
    return bool(perp_dex) and perp_dex != _CORE
