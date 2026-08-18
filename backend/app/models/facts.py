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

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
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


class FactIssuerSnapshot(Base, _SnapshotMixin):
    """Issuer product breadth, taken from the issuer's own site.

    Grain: issuer x snapshot_ts. ``dim_issuer.official_product_count`` holds only the
    latest figure; this table holds the series, which is what makes "Ondo added 12
    products this week" a question anyone can ask. Product launches are the leading
    edge of the demand this system exists to detect, and a dimension column overwritten
    in place cannot show them.
    """

    __tablename__ = "fact_issuer_snapshot"

    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("dim_issuer.issuer_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    #: What the issuer says it offers. The denominator of every coverage ratio.
    official_product_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How many venues, wallets and integrations the issuer names publicly. Not a
    #: subset or superset of anything we observe trading — it is the issuer's claim.
    listed_platform_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FactIssuerPlatformSnapshot(Base, _SnapshotMixin):
    """One platform an issuer names as carrying its products.

    Grain: issuer x platform x snapshot_ts. Append-only, so a platform appearing for
    the first time is visible as a new row rather than inferred from a count that
    went up.

    ``platform_name`` is stored exactly as the issuer spells it and is deliberately
    not classified into CEX / DEX / wallet: the source page mixes exchanges, chains,
    brokers and analytics vendors without saying which is which, and assigning a type
    we were not told is the kind of invented label domain rule 9 forbids.
    """

    __tablename__ = "fact_issuer_platform_snapshot"

    issuer_id: Mapped[str] = mapped_column(
        ForeignKey("dim_issuer.issuer_id"), primary_key=True
    )
    platform_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()


class FactUnderlyingReference(Base, _SnapshotMixin):
    """The real security's own price, from a TradFi feed. Grain: underlying x ts.

    Every other fact table in this system measures the *wrapper*. This one measures
    the thing being wrapped, which is what makes "the token traded 4% above the share"
    a statement anyone can check. Without it, a tokenized price has nothing to be
    right or wrong against.

    **This table carries no ``MetricScope`` and is never summed.** A price is not one
    of the five families, and adding prices across underlyings is meaningless in the
    same way adding market caps to volumes is. Its use is per-underlying comparison —
    ``RatioScope.BASIS`` — and display alongside, in the manner of ``dim_benchmark``.

    ``price_ts`` matters more here than anywhere else. RWA tokens trade around the
    clock and their underlyings do not, so outside RTH this row repeats a print that
    is hours or days old. That is not staleness to be hidden: the token drifting away
    from a frozen reference over a weekend is precisely the phenomenon worth watching.
    But a basis computed without checking ``price_ts`` against ``snapshot_ts`` will
    read a closed market as a mispricing.
    """

    __tablename__ = "fact_underlying_reference"

    underlying_id: Mapped[str] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), primary_key=True
    )
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    #: Last trade price on the feed named below.
    price: Mapped[Decimal | None] = money_column()
    #: When that trade happened *at the source*, not when we asked. See above.
    price_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Previous session's close on the same feed. On a single-venue feed such as IEX
    #: this is that venue's last print, not the official closing-auction price — the
    #: auction happens at the listing exchange. Close enough to compare a day's move
    #: against; not close enough to reconcile against a broker statement.
    prev_close: Mapped[Decimal | None] = money_column()
    #: Derived from the two columns above and stored for symmetry with
    #: ``fact_asset_snapshot.change_24h``, which is the number it gets compared to.
    change_24h: Mapped[Decimal | None] = ratio_column()
    #: Deliberately not a money column: this is a count of *shares* on *one* venue,
    #: not USD turnover and not a consolidated tape. It must never be placed next to
    #: ``SPOT_VOLUME`` as though the two were the same measurement.
    venue_vol_shares: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 8), nullable=True
    )
    #: Which feed produced the row (``iex``, ``sip``). Stored per row because the two
    #: are different populations: IEX is a few per cent of consolidated volume, so a
    #: series that silently switches feed changes meaning mid-chart.
    feed: Mapped[str | None] = mapped_column(String(16), nullable=True)


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
