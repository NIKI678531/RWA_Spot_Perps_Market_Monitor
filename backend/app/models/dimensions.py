"""Dimension tables.

``dim_underlying`` is the centre of the star. Every question about customer demand
("is anyone buying the S&P 500?") resolves to an underlying, and answering it without
this table means manually adding six rows across three issuers and two metric scopes
— which is how the manual workbook got it wrong.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, enum_column, id_column
from app.models.enums import AssetClass, RwaTier, UserRole, VenueType


class DimTheme(Base):
    """A demand grouping that cuts across issuers and venues."""

    __tablename__ = "dim_theme"

    theme_id: Mapped[str] = id_column(primary_key=True)
    name_zh: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class DimBenchmark(Base):
    """A display-only grouping of underlyings with the same economic exposure.

    The SPY ETF and the S&P 500 index are different instruments in different tiers
    that answer the same question. This table joins them for side-by-side display
    and for nothing else — it is never summed over.
    """

    __tablename__ = "dim_benchmark"

    benchmark_id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class DimUnderlying(Base):
    """The real-world security, commodity or index a token represents."""

    __tablename__ = "dim_underlying"

    underlying_id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_class: Mapped[AssetClass] = enum_column(AssetClass, nullable=False)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(24), nullable=True)
    is_pre_ipo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    theme_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_theme.theme_id"), nullable=True, index=True
    )
    benchmark_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_benchmark.benchmark_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = created_at_column()

    theme: Mapped[DimTheme | None] = relationship()
    benchmark: Mapped[DimBenchmark | None] = relationship()


class DimIssuer(Base):
    """The organization that creates a tokenized wrapper."""

    __tablename__ = "dim_issuer"

    issuer_id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Product count from the issuer's own site. Larger than any aggregator's index
    #: (xStocks lists ~640 while CoinGecko indexes ~113), so this is the denominator
    #: for coverage ratios.
    official_product_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    official_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: e.g. bStocks are receipt-like instruments rather than direct share ownership.
    #: The UI must label this; it is a materially different legal claim.
    legal_structure_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class DimAsset(Base):
    """One tokenized wrapper of an underlying, on one chain, from one issuer."""

    __tablename__ = "dim_asset"

    asset_id: Mapped[str] = id_column(primary_key=True)
    coin_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contract_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rwa_tier: Mapped[RwaTier] = enum_column(RwaTier, nullable=False, index=True)
    underlying_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=True, index=True
    )
    issuer_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_issuer.issuer_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = created_at_column()

    underlying: Mapped[DimUnderlying | None] = relationship()
    issuer: Mapped[DimIssuer | None] = relationship()


class DimVenue(Base):
    """A place where an asset trades."""

    __tablename__ = "dim_venue"

    venue_id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    venue_type: Mapped[VenueType] = enum_column(VenueType, nullable=False, index=True)
    chain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Newline-separated source spellings. The same DEX arrives as
    #: "PancakeSwap V3 (BSC)", "PancakeSwap v3" and "pancakeswap-v3" from different
    #: endpoints; without this, one venue is ranked three times.
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class DimPerpContract(Base):
    """A perpetual futures contract at one exchange (optionally one HIP-3 perp DEX)."""

    __tablename__ = "dim_perp_contract"

    contract_id: Mapped[str] = id_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Hyperliquid HIP-3 deploys independent perp DEXs under one exchange. Null for
    #: conventional venues such as Binance.
    perp_dex: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: The exchange's own classification, stored verbatim. Binance labels some ETFs
    #: and leveraged ETPs as EQUITY; overwriting that loses the ability to reconcile
    #: our numbers against theirs.
    source_underlying_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    #: Our classification, stored alongside rather than instead of the above.
    analysis_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    underlying_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = created_at_column()

    underlying: Mapped[DimUnderlying | None] = relationship()


class DimPool(Base):
    """A DEX liquidity pool."""

    __tablename__ = "dim_pool"

    pool_id: Mapped[str] = id_column(primary_key=True)
    network: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dex: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pool_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    base_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_asset.asset_id"), nullable=True, index=True
    )
    quote_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Whether the quote token is a canonical USD stablecoin. Pools quoted in an
    #: exotic token produce USD figures that depend on a second, weaker price feed.
    is_canonical_quote: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()

    base_asset: Mapped[DimAsset | None] = relationship()


class ThemeMap(Base):
    """Versioned many-to-many between underlyings and demand themes.

    ``dim_underlying.theme_id`` could only ever say one thing, and most underlyings
    genuinely belong to several themes — a tokenized gold ETF is both "commodity" and
    "inflation hedge". But a stacked share chart needs exactly one exclusive
    assignment or the bars sum past 100%, so ``is_primary`` carries that and only
    that: exactly one primary per underlying per version.

    The rows are versioned because theme definitions change, and a February edition
    must replay under February's definitions. A re-classification opens a new version
    with an author and a reason; it never edits the old rows.
    """

    __tablename__ = "theme_map"
    __table_args__ = (
        UniqueConstraint(
            "underlying_id", "theme_id", "version", name="uq_theme_map_entry"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    underlying_id: Mapped[str] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=False, index=True
    )
    theme_id: Mapped[str] = mapped_column(
        ForeignKey("dim_theme.theme_id"), nullable=False, index=True
    )
    #: Only the primary assignment may be stacked, summed or shown as a share.
    #: Secondary themes are for filtering and discovery.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Monotonic definition version. Editions record which version they rendered on.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1", index=True
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Null while current. Set, never deleted, when a later version supersedes it.
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    underlying: Mapped[DimUnderlying] = relationship()
    theme: Mapped[DimTheme] = relationship()


class DimOwnProduct(Base):
    """A product on our own shelf, and what it maps to in the market.

    Without this table "coverage gap" is an opinion. With it, a theme heating up can
    be stated as a gap against a specific set of listed products, which is the
    difference between "gold is interesting" and "we list nothing against gold".
    """

    __tablename__ = "dim_own_product"

    product_id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Our internal instrument type (ETF, feeder fund, structured note, …). Free text
    #: rather than an enum: the shelf changes faster than a migration cycle.
    product_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    underlying_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=True, index=True
    )
    listing_venue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    underlying: Mapped[DimUnderlying | None] = relationship()


class DimUser(Base):
    """Someone who can own an alert or take a recorded decision.

    Deliberately thin: authentication belongs to the surrounding platform, and this
    system only needs a stable identity to attribute an action to. A display name is
    stored so a closed alert still reads sensibly after the person leaves.
    """

    __tablename__ = "dim_user"

    user_id: Mapped[str] = id_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = enum_column(
        UserRole, nullable=False, default=UserRole.VIEWER, index=True
    )
    team: Mapped[str | None] = mapped_column(String(96), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
