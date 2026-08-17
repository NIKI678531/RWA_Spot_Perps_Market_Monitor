"""Response models for the market, competition, perpetual and alert endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AssetClass,
    DetectorFamily,
    EntityType,
    RwaTier,
    VenueType,
)
from app.schemas.common import Amount, Meta


class Kpi(BaseModel):
    """One headline number. Five of these, one per scope, never combined."""

    key: str
    label_zh: str
    label_en: str
    current: Amount
    previous: Amount | None = None
    #: Period-over-period change. Null when either side was not observed — a change
    #: computed against a missing baseline is a fabricated number.
    change_pct: float | None = None
    entity_count: int = 0


class ExecutiveKpi(BaseModel):
    meta: Meta
    previous_as_of: datetime | None = None
    metrics: list[Kpi]


class CategoryRow(BaseModel):
    category_id: str
    asset_count: int | None = None
    market_cap: Amount
    vol_24h: Amount
    #: False for the five overlapping source categories. Only the union row totals.
    is_additive: bool


class CategoryScale(BaseModel):
    meta: Meta
    rows: list[CategoryRow]
    overlap_note: str


class VenueRow(BaseModel):
    rank: int
    venue_id: str
    name: str
    venue_type: VenueType | None = None
    chain: str | None = None
    raw_vol_24h: Amount
    adjusted_vol_24h: Amount
    share_of_adjusted: float | None = None
    pair_count: int = 0
    underlying_count: int = 0
    flagged_pairs: int = 0
    #: Raw exceeds adjusted by more than an order of magnitude.
    materially_divergent: bool = False


class ConcentrationSummary(BaseModel):
    segment: str
    venue_count: int
    hhi: float
    top1_share: float | None = None
    top3_share: float | None = None
    top5_share: float | None = None
    is_concentrated: bool


class VenueRanking(BaseModel):
    meta: Meta
    rows: list[VenueRow]
    concentration: list[ConcentrationSummary]


class PairRow(BaseModel):
    asset_id: str
    symbol: str
    rwa_tier: RwaTier
    underlying_id: str | None = None
    issuer_id: str | None = None
    venue_id: str
    venue: str
    venue_type: VenueType | None = None
    raw_vol_24h: Amount
    adjusted_vol_24h: Amount
    price_usd: Decimal | None = None
    spread_pct: Decimal | None = None
    trust_score: str | None = None
    is_quality_anomaly: bool = False
    is_quality_stale: bool = False


class PairList(BaseModel):
    meta: Meta
    rows: list[PairRow]


class PoolRow(BaseModel):
    pool_id: str
    network: str
    dex: str
    base_symbol: str | None = None
    quote_token: str | None = None
    is_canonical_quote: bool = True
    reserve_usd: Amount
    vol_24h: Amount
    buys_24h: int | None = None
    sells_24h: int | None = None
    buy_ratio: float | None = None


class PoolList(BaseModel):
    meta: Meta
    rows: list[PoolRow]


class IssuerRow(BaseModel):
    rank: int
    issuer_id: str
    name: str
    indexed_asset_count: int
    official_product_count: int | None = None
    #: Indexed over official. Below 1 means the aggregator sees less than the issuer
    #: publishes, which is the normal case and the reason the official count is the
    #: denominator.
    index_coverage: float | None = None
    market_cap: Amount
    adjusted_vol_24h: Amount
    legal_structure_note: str | None = None


class IssuerList(BaseModel):
    meta: Meta
    rows: list[IssuerRow]


class IssuerVenueCell(BaseModel):
    venue_id: str
    venue: str
    venue_type: VenueType | None = None
    adjusted_vol_24h: Amount
    pair_count: int


class IssuerVenues(BaseModel):
    meta: Meta
    issuer_id: str
    name: str
    rows: list[IssuerVenueCell]


class PerpVenueRow(BaseModel):
    exchange: str
    perp_dex: str
    is_hip3: bool
    segment: str
    vol_24h: Amount
    open_interest_usd: Amount
    symbol_count: int | None = None


class PerpVenueList(BaseModel):
    meta: Meta
    rows: list[PerpVenueRow]


class PerpContractRow(BaseModel):
    rank: int
    contract_id: str
    exchange: str
    perp_dex: str
    symbol: str
    #: The exchange's own classification, verbatim. Binance labels some ETFs EQUITY.
    source_underlying_type: str | None = None
    analysis_group: str | None = None
    underlying_id: str | None = None
    vol_24h: Amount
    open_interest_usd: Amount
    oi_units: Decimal | None = None
    funding_rate: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None


class PerpContractList(BaseModel):
    meta: Meta
    rows: list[PerpContractRow]


class PerpDexRow(BaseModel):
    perp_dex: str
    is_hip3: bool
    contract_count: int
    vol_24h: Amount
    open_interest_usd: Amount


class PerpDexList(BaseModel):
    meta: Meta
    rows: list[PerpDexRow]


class ThemeRow(BaseModel):
    theme_id: str
    name_zh: str | None = None
    name_en: str | None = None
    underlying_count: int
    spot_vol_adjusted: Amount
    perp_vol_24h: Amount


class ThemeList(BaseModel):
    meta: Meta
    rows: list[ThemeRow]


class EvidenceRow(BaseModel):
    rule_name: str
    snapshot_ts: datetime
    observed_value: Decimal | None = None
    baseline_median: Decimal | None = None
    baseline_mad: Decimal | None = None
    robust_z: float | None = None
    sample_size: int | None = None
    market_session: MarketSession
    peer_count: int | None = None
    extra: dict[str, object] = Field(default_factory=dict)


class AlertRow(BaseModel):
    id: int
    detector: str
    family: DetectorFamily
    severity: AlertSeverity
    score: float | None = None
    status: AlertStatus
    entity_type: EntityType
    entity_id: str
    metric_scope: MetricScope
    market_session: MarketSession
    headline_zh: str
    headline_en: str | None = None
    first_seen_ts: datetime
    last_seen_ts: datetime
    occurrence_count: int


class AlertList(BaseModel):
    meta: Meta
    rows: list[AlertRow]


class AlertDetail(BaseModel):
    alert: AlertRow
    #: Newest first. Every firing, not just the latest — an alert nobody can justify
    #: to management is noise.
    evidence: list[EvidenceRow]


class WrapperRow(BaseModel):
    asset_id: str
    symbol: str
    issuer: str | None = None
    chain: str | None = None
    rwa_tier: RwaTier
    market_cap: Amount
    vol_24h: Amount


class VenueBreakdownRow(BaseModel):
    venue_id: str
    venue: str
    venue_type: VenueType | None = None
    adjusted_vol_24h: Amount


class PerpExposureRow(BaseModel):
    exchange: str
    perp_dex: str
    contract: str
    vol_24h: Amount
    open_interest_usd: Amount


class Underlying360(BaseModel):
    """Everything known about one real-world security, by scope."""

    meta: Meta
    underlying_id: str
    name: str
    asset_class: AssetClass
    region: str | None = None
    is_pre_ipo: bool = False
    theme_id: str | None = None
    benchmark_id: str | None = None
    tokenized_wrappers: list[WrapperRow]
    venue_breakdown: list[VenueBreakdownRow]
    perp_exposure: list[PerpExposureRow]
    spot_market_cap: Amount
    spot_vol_adjusted: Amount
    perp_vol_24h: Amount
    perp_oi_usd: Amount
    scope_note: str
    active_alerts: list[AlertRow]


class TimeseriesPoint(BaseModel):
    snapshot_ts: datetime
    value: Decimal | None = None
    market_session: MarketSession
    #: A value reused from the previous snapshot after a failed fetch. Excluded from
    #: baselines: measuring the variance of a carried-forward series manufactures
    #: stability that was never observed.
    is_carried_forward: bool = False


class Timeseries(BaseModel):
    meta: Meta
    entity_type: EntityType
    entity_id: str
    metric: str
    scope: MetricScope
    points: list[TimeseriesPoint]


class SourceHealth(BaseModel):
    source_id: str
    status: str
    attempts: int
    last_attempt_ts: datetime
    records: int | None = None
    avg_duration_ms: int | None = None
    sample_error: str | None = None


class DataQuality(BaseModel):
    meta: Meta
    sources: list[SourceHealth]
    pair_count: int
    flagged_pairs: int
    unverified_pairs: int
    pending_mappings: int
    #: Venues whose adjusted turnover is under a tenth of their raw turnover.
    divergent_venues: list[str]


class ReportRow(BaseModel):
    id: int
    report_date: datetime
    report_format: str
    filename: str
    size_bytes: int | None = None
    snapshot_ts: datetime | None = None
    storage: str
    created_at: datetime


class ReportList(BaseModel):
    rows: list[ReportRow]
