"""Fact tables.

Every row is one observation at one ``snapshot_ts``. These tables are append-only:
a correction is a new snapshot, never an UPDATE. Without that, "what did we believe
last Tuesday" becomes unanswerable, and every alert loses the evidence that produced
it.

Nullable money columns mean *not verified*. They never mean zero.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    enum_column,
    id_column,
    money_column,
    ratio_column,
    snapshot_pk_column,
)
from app.core.sessions import MarketSession


class _SnapshotMixin:
    """Fields every fact row carries besides its own key.

    ``snapshot_ts`` is deliberately not here: it forms part of each table's composite
    primary key, and a mixin cannot express where in that key it sits.
    """

    #: Session of the *underlying* market at observation time. Baselines stratify on
    #: this; storing it at write time means a later calendar change cannot silently
    #: reclassify history.
    market_session: Mapped[MarketSession] = enum_column(
        MarketSession, nullable=False, index=True
    )
    #: Set when a collection failed and the previous snapshot was reused. Such rows
    #: must be excluded from baselines — carrying a value forward and then measuring
    #: its variance manufactures artificial stability.
    is_carried_forward: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class FactAssetSnapshot(Base, _SnapshotMixin):
    """Asset-level market data. Grain: asset x snapshot_ts."""

    __tablename__ = "fact_asset_snapshot"

    asset_id: Mapped[str] = mapped_column(
        ForeignKey("dim_asset.asset_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    price_usd: Mapped[Decimal | None] = money_column()
    market_cap: Mapped[Decimal | None] = money_column()
    fdv: Mapped[Decimal | None] = money_column()
    vol_24h: Mapped[Decimal | None] = money_column()
    circulating_supply: Mapped[Decimal | None] = money_column()
    change_24h: Mapped[Decimal | None] = ratio_column()
    change_7d: Mapped[Decimal | None] = ratio_column()
    change_30d: Mapped[Decimal | None] = ratio_column()


class FactPairSnapshot(Base, _SnapshotMixin):
    """Spot trading pair. Grain: asset x venue x snapshot_ts."""

    __tablename__ = "fact_pair_snapshot"

    asset_id: Mapped[str] = mapped_column(
        ForeignKey("dim_asset.asset_id"), primary_key=True
    )
    venue_id: Mapped[str] = mapped_column(
        ForeignKey("dim_venue.venue_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    #: Turnover as the source reported it, quality flags included.
    raw_vol_24h: Mapped[Decimal | None] = money_column()
    #: Turnover excluding quality-flagged pairs. Reported *alongside* raw, never
    #: instead of it: Native (BSC) shows ~$29.3mn raw against ~$216 adjusted.
    adjusted_vol_24h: Mapped[Decimal | None] = money_column()
    price_usd: Mapped[Decimal | None] = money_column()
    spread_pct: Mapped[Decimal | None] = ratio_column()
    trust_score: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: CoinGecko's data-hygiene markers. These describe the *quote*, not the market;
    #: they are not the demand anomalies this system detects.
    is_quality_anomaly: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_quality_stale: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class FactVenueSnapshot(Base, _SnapshotMixin):
    """Venue rollup. Grain: venue x snapshot_ts."""

    __tablename__ = "fact_venue_snapshot"

    venue_id: Mapped[str] = mapped_column(
        ForeignKey("dim_venue.venue_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    raw_vol_24h: Mapped[Decimal | None] = money_column()
    adjusted_vol_24h: Mapped[Decimal | None] = money_column()
    share: Mapped[Decimal | None] = ratio_column()
    pair_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    underlying_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FactPoolSnapshot(Base, _SnapshotMixin):
    """DEX pool. Grain: pool x snapshot_ts."""

    __tablename__ = "fact_pool_snapshot"

    pool_id: Mapped[str] = mapped_column(
        ForeignKey("dim_pool.pool_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    reserve_usd: Mapped[Decimal | None] = money_column()
    vol_24h: Mapped[Decimal | None] = money_column()
    #: The only direction-bearing data in the system. Turnover says someone traded;
    #: the buy/sell split says whether customers were buying.
    buys_24h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sells_24h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_count_24h: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FactPerpVenueSnapshot(Base, _SnapshotMixin):
    """Perp venue rollup. Grain: exchange x perp_dex x segment x snapshot_ts."""

    __tablename__ = "fact_perp_venue_snapshot"

    exchange: Mapped[str] = mapped_column(String(64), primary_key=True)
    perp_dex: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    segment: Mapped[str] = mapped_column(String(32), primary_key=True, default="all")
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    vol_24h: Mapped[Decimal | None] = money_column()
    open_interest_usd: Mapped[Decimal | None] = money_column()
    symbol_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How many of those symbols the open-interest figure actually covers. Volume
    #: arrives in one bulk call while open interest is one request per symbol, so the
    #: two columns of a single row can have different coverage. Equal to
    #: ``symbol_count`` means complete; smaller means the total is a floor, and null
    #: means the collector did not say.
    oi_symbol_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FactPerpContractSnapshot(Base, _SnapshotMixin):
    """Perp contract. Grain: contract x snapshot_ts."""

    __tablename__ = "fact_perp_contract_snapshot"

    contract_id: Mapped[str] = mapped_column(
        ForeignKey("dim_perp_contract.contract_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    vol_24h: Mapped[Decimal | None] = money_column()
    #: Contract units as reported. Binance does not publish notional OI for these,
    #: so oi_usd below is derived as units x mark and both are kept.
    oi_units: Mapped[Decimal | None] = money_column()
    oi_usd: Mapped[Decimal | None] = money_column()
    funding_rate: Mapped[Decimal | None] = ratio_column()
    mark_price: Mapped[Decimal | None] = money_column()
    index_price: Mapped[Decimal | None] = money_column()


class FactCategorySnapshot(Base, _SnapshotMixin):
    """CoinGecko category rollup. Grain: category x snapshot_ts."""

    __tablename__ = "fact_category_snapshot"

    category_id: Mapped[str] = id_column(primary_key=True)
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    asset_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_cap: Mapped[Decimal | None] = money_column()
    vol_24h: Mapped[Decimal | None] = money_column()
    #: False for the five overlapping source categories (Tokenized Stock, Tokenized
    #: ETF, Ondo, xStocks, bStocks) and True only for the deduplicated union row.
    #: The API and every chart must respect it.
    is_additive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
