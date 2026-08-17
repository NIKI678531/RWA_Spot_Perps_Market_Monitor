"""Turns the warehouse into the inputs the cross-sectional detectors expect.

Detectors take plain observation records rather than ORM rows so they stay testable
without a database, and so a change to a fact table cannot quietly alter what a rule
means. This module is the one place that translation happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dimensions import DimAsset, DimPool, DimUnderlying
from app.models.enums import RwaTier
from app.models.facts import FactAssetSnapshot, FactPairSnapshot, FactPoolSnapshot
from app.services.anomaly.detectors.x1_cross_sectional_turnover import AssetObservation
from app.services.anomaly.detectors.x2_buy_sell_imbalance import PoolObservation
from app.services.anomaly.detectors.x3_vol_liq_ratio_extreme import (
    PoolLiquidityObservation,
)
from app.services.anomaly.detectors.x4_new_pair_listing import PairObservation


@dataclass(frozen=True, slots=True)
class PoolViews:
    """One pool seen through the two lenses that judge it.

    X2 asks which direction the trades went; X3 asks whether turnover fits the depth.
    Both read the same row, so the row is read once.
    """

    imbalance: PoolObservation
    liquidity: PoolLiquidityObservation


def assets(session: Session, snapshot_ts: datetime) -> list[AssetObservation]:
    """Assets at this snapshot, with the asset class their peer group keys on.

    An asset with no mapped underlying has no asset class and therefore no peer
    group. It is dropped rather than defaulted: putting an unmapped token into the
    EQUITY cohort would compare it against instruments it may have nothing to do
    with.
    """
    stmt = (
        select(FactAssetSnapshot, DimAsset, DimUnderlying)
        .join(DimAsset, FactAssetSnapshot.asset_id == DimAsset.asset_id)
        .join(DimUnderlying, DimAsset.underlying_id == DimUnderlying.underlying_id)
        .where(FactAssetSnapshot.snapshot_ts == snapshot_ts)
    )
    return [
        AssetObservation(
            asset_id=snapshot.asset_id,
            asset_class=underlying.asset_class,
            rwa_tier=asset.rwa_tier,
            vol_24h=snapshot.vol_24h,
            market_cap=snapshot.market_cap,
        )
        for snapshot, asset, underlying in session.execute(stmt).all()
    ]


def pools(session: Session, snapshot_ts: datetime) -> list[PoolViews]:
    stmt = (
        select(FactPoolSnapshot, DimPool, DimAsset)
        .join(DimPool, FactPoolSnapshot.pool_id == DimPool.pool_id)
        .outerjoin(DimAsset, DimPool.base_asset_id == DimAsset.asset_id)
        .where(FactPoolSnapshot.snapshot_ts == snapshot_ts)
    )
    views: list[PoolViews] = []
    for snapshot, pool, asset in session.execute(stmt).all():
        tier = asset.rwa_tier if asset else RwaTier.NON_RWA
        views.append(
            PoolViews(
                imbalance=PoolObservation(
                    pool_id=snapshot.pool_id,
                    network=pool.network,
                    vol_24h=snapshot.vol_24h,
                    buys_24h=snapshot.buys_24h,
                    sells_24h=snapshot.sells_24h,
                    rwa_tier=tier,
                ),
                liquidity=PoolLiquidityObservation(
                    pool_id=snapshot.pool_id,
                    network=pool.network,
                    vol_24h=snapshot.vol_24h,
                    reserve_usd=snapshot.reserve_usd,
                    rwa_tier=tier,
                ),
            )
        )
    return views


def pairs(session: Session, snapshot_ts: datetime) -> list[PairObservation]:
    stmt = (
        select(FactPairSnapshot, DimAsset)
        .join(DimAsset, FactPairSnapshot.asset_id == DimAsset.asset_id)
        .where(FactPairSnapshot.snapshot_ts == snapshot_ts)
    )
    return [
        PairObservation(
            asset_id=snapshot.asset_id,
            venue_id=snapshot.venue_id,
            adjusted_vol_24h=snapshot.adjusted_vol_24h,
            rwa_tier=asset.rwa_tier,
        )
        for snapshot, asset in session.execute(stmt).all()
    ]


def known_pairs(session: Session, snapshot_ts: datetime) -> set[tuple[str, str]]:
    """Every (asset, venue) seen *before* this snapshot.

    Strictly before: including the current snapshot would make every pair known and
    X4 would never fire. On the very first collection this set is empty, which would
    report the entire market as newly listed; X4 treats an empty set as "no history"
    and stays silent rather than trusting it.
    """
    stmt = (
        select(FactPairSnapshot.asset_id, FactPairSnapshot.venue_id)
        .where(FactPairSnapshot.snapshot_ts < snapshot_ts)
        .distinct()
    )
    return {(asset_id, venue_id) for asset_id, venue_id in session.execute(stmt).all()}
