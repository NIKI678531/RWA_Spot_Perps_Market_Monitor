/**
 * Mirrors `backend/app/schemas/`. Kept by hand rather than generated, because the
 * types the UI must not get wrong are few and the generated ones would bury them.
 *
 * The load-bearing type is `Amount`. Every USD figure crossing the wire carries the
 * metric scope it belongs to and how much of it was observed, which is what lets the
 * chart layer refuse a two-scope axis and the table layer render a missing
 * observation as a placeholder instead of a zero.
 */

/** The five non-additive metric families. Summing across them is meaningless. */
export type MetricScope =
  'spot_market_cap' | 'spot_volume' | 'dex_liquidity' | 'perp_volume' | 'perp_oi';

/** A stock is a level at an instant; a flow is a quantity over a window. */
export type MetricDimension = 'stock' | 'flow' | 'ratio';

/**
 * `not_verified` is not zero. It means the observation was never made, and it must
 * never reach a chart as a zero-height bar or a table as `$0`.
 */
export type Coverage = 'complete' | 'partial' | 'not_verified';

export type MarketSession =
  'rth' | 'pre' | 'ah' | 'closed_weekday' | 'closed_weekend' | 'closed_holiday';

export type RwaTier = 'core_rwa' | 'rwa_adjacent' | 'synthetic' | 'non_rwa';
export type VenueType = 'cex' | 'dex' | 'aggregator' | 'perp_dex';
export type AlertSeverity = 'low' | 'medium' | 'high' | 'critical';
export type AlertStatus = 'tentative' | 'confirmed' | 'expired' | 'suppressed';
export type DetectorFamily = 'cross_sectional' | 'time_series';
export type EntityType =
  | 'asset'
  | 'pair'
  | 'pool'
  | 'venue'
  | 'issuer'
  | 'underlying'
  | 'perp_contract'
  | 'perp_venue'
  | 'category'
  | 'theme';

export interface Amount {
  /** Null means not observed. It does not mean zero. */
  value: string | null;
  scope: MetricScope;
  dimension: MetricDimension;
  coverage: Coverage;
}

export interface Meta {
  as_of: string;
  scopes: MetricScope[];
  note: string;
  row_count: number;
}

export interface Health {
  status: 'ok' | 'degraded';
  environment: string;
  database: string;
  as_of: string | null;
}

export interface Kpi {
  key: string;
  label_zh: string;
  label_en: string;
  current: Amount;
  previous: Amount | null;
  /** Null when either side was not observed: a change against a gap is invented. */
  change_pct: number | null;
  entity_count: number;
}

export interface ExecutiveKpi {
  meta: Meta;
  previous_as_of: string | null;
  metrics: Kpi[];
}

export interface CategoryRow {
  category_id: string;
  asset_count: number | null;
  market_cap: Amount;
  vol_24h: Amount;
  /** False for the five overlapping source categories. Only the union row totals. */
  is_additive: boolean;
}

export interface CategoryScale {
  meta: Meta;
  rows: CategoryRow[];
  overlap_note: string;
}

export interface VenueRow {
  rank: number;
  venue_id: string;
  name: string;
  venue_type: VenueType | null;
  chain: string | null;
  raw_vol_24h: Amount;
  adjusted_vol_24h: Amount;
  share_of_adjusted: number | null;
  pair_count: number;
  underlying_count: number;
  flagged_pairs: number;
  materially_divergent: boolean;
}

export interface ConcentrationSummary {
  segment: string;
  venue_count: number;
  hhi: number;
  top1_share: number | null;
  top3_share: number | null;
  top5_share: number | null;
  is_concentrated: boolean;
}

export interface VenueRanking {
  meta: Meta;
  rows: VenueRow[];
  concentration: ConcentrationSummary[];
}

export interface PairRow {
  asset_id: string;
  symbol: string;
  rwa_tier: RwaTier;
  underlying_id: string | null;
  issuer_id: string | null;
  venue_id: string;
  venue: string;
  venue_type: VenueType | null;
  raw_vol_24h: Amount;
  adjusted_vol_24h: Amount;
  price_usd: string | null;
  spread_pct: string | null;
  trust_score: string | null;
  is_quality_anomaly: boolean;
  is_quality_stale: boolean;
}

export interface PairList {
  meta: Meta;
  rows: PairRow[];
}

export interface PerpContractRow {
  rank: number;
  contract_id: string;
  exchange: string;
  perp_dex: string;
  symbol: string;
  /** The exchange's own label, verbatim. Binance calls some ETFs EQUITY. */
  source_underlying_type: string | null;
  analysis_group: string | null;
  underlying_id: string | null;
  vol_24h: Amount;
  open_interest_usd: Amount;
  oi_units: string | null;
  funding_rate: string | null;
  mark_price: string | null;
  index_price: string | null;
}

export interface PerpContractList {
  meta: Meta;
  rows: PerpContractRow[];
}

export interface PerpDexRow {
  perp_dex: string;
  is_hip3: boolean;
  /** Contracts resolving to a real-world underlying — what the amounts are summed over. */
  contract_count: number;
  /** Every contract seen on the deployment, in scope or not. */
  observed_contract_count: number;
  vol_24h: Amount;
  open_interest_usd: Amount;
}

export interface PerpDexList {
  meta: Meta;
  rows: PerpDexRow[];
}

export interface PerpVenueRow {
  exchange: string;
  perp_dex: string;
  is_hip3: boolean;
  segment: string;
  vol_24h: Amount;
  open_interest_usd: Amount;
  symbol_count: number | null;
  /** Symbols the open-interest total covers; below `symbol_count` it is a floor. */
  oi_symbol_count: number | null;
}

export interface PerpVenueList {
  meta: Meta;
  rows: PerpVenueRow[];
}

export interface AlertRow {
  id: number;
  detector: string;
  family: DetectorFamily;
  severity: AlertSeverity;
  score: number | null;
  status: AlertStatus;
  entity_type: EntityType;
  entity_id: string;
  metric_scope: MetricScope;
  market_session: MarketSession;
  headline_zh: string;
  headline_en: string | null;
  first_seen_ts: string;
  last_seen_ts: string;
  occurrence_count: number;
}

export interface AlertList {
  meta: Meta;
  rows: AlertRow[];
}

export interface EvidenceRow {
  rule_name: string;
  snapshot_ts: string;
  observed_value: string | null;
  baseline_median: string | null;
  baseline_mad: string | null;
  robust_z: number | null;
  sample_size: number | null;
  market_session: MarketSession;
  peer_count: number | null;
  extra: Record<string, unknown>;
}

export interface AlertDetail {
  alert: AlertRow;
  evidence: EvidenceRow[];
}

export interface SourceHealth {
  source_id: string;
  status: string;
  attempts: number;
  last_attempt_ts: string;
  records: number | null;
  avg_duration_ms: number | null;
  sample_error: string | null;
}

export interface CatalogueCoverage {
  /** Assets we index and can rank. In-scope tiers only. */
  indexed_assets: number;
  /** What the issuers publish, summed over those who publish a count at all. */
  official_products: number | null;
  /** `indexed_assets / official_products`. Null when unknown — never 1.0. */
  ratio: number | null;
  issuers_with_count: number;
  issuer_count: number;
}

export interface ReferenceCoverage {
  tracked_underlyings: number;
  priced_underlyings: number;
  feed: string | null;
  /** Age of the *oldest* reference price. Large is normal outside RTH. */
  max_age_minutes: number | null;
  unavailable_reason: string | null;
}

export interface DataQuality {
  meta: Meta;
  sources: SourceHealth[];
  pair_count: number;
  flagged_pairs: number;
  unverified_pairs: number;
  pending_mappings: number;
  divergent_venues: string[];
  catalogue: CatalogueCoverage;
  reference: ReferenceCoverage;
}

export interface BenchmarkRow {
  underlying_id: string;
  underlying_name: string;
  asset_id: string;
  symbol: string;
  issuer_id: string | null;
  token_price: string | null;
  reference_price: string | null;
  /** When the trade happened at the source — not when we read it. */
  reference_price_ts: string | null;
  /** Hours or days here is the normal state outside RTH, not a fault. */
  reference_age_minutes: number | null;
  feed: string | null;
  /** `token_price / reference_price - 1`. Null when either side is missing. */
  basis: number | null;
  token_change_24h: string | null;
  reference_change_24h: string | null;
  market_session: MarketSession | null;
}

export interface BenchmarkList {
  meta: Meta;
  rows: BenchmarkRow[];
  /** Set when no reference source has run. Shown instead of an empty table. */
  unavailable_reason: string | null;
}

export interface ReportRow {
  id: number;
  report_date: string;
  report_format: string;
  filename: string;
  size_bytes: number | null;
  snapshot_ts: string | null;
  storage: string;
  created_at: string;
}

export interface ReportList {
  rows: ReportRow[];
}

export interface TimeseriesPoint {
  snapshot_ts: string;
  value: string | null;
  market_session: MarketSession;
  is_carried_forward: boolean;
}

export interface Timeseries {
  meta: Meta;
  entity_type: EntityType;
  entity_id: string;
  metric: string;
  scope: MetricScope;
  points: TimeseriesPoint[];
}

/** Full scope names. A chart axis title carries the whole phrase, never "成交额". */
export const SCOPE_LABEL: Record<MetricScope, { zh: string; en: string }> = {
  spot_market_cap: { zh: '现货市值（USD）', en: 'Spot market cap (USD)' },
  spot_volume: { zh: '现货成交额（24h, USD）', en: 'Spot volume (24h, USD)' },
  dex_liquidity: { zh: 'DEX 池内流动性（USD）', en: 'DEX pool liquidity (USD)' },
  perp_volume: { zh: '永续成交额（24h, USD）', en: 'Perp volume (24h, USD)' },
  perp_oi: { zh: '永续未平仓（USD）', en: 'Perp open interest (USD)' },
};

/**
 * The scope phrase for a locale. Only zh and en phrases exist; ko and zh-TW fall back
 * to zh rather than to an unreviewed translation, because a scope named wrongly is the
 * one mistake the whole scope system is built to prevent.
 */
export function scopeLabel(scope: MetricScope, locale = 'zh'): string {
  return SCOPE_LABEL[scope][locale === 'en' ? 'en' : 'zh'];
}

export const SCOPE_DIMENSION: Record<MetricScope, MetricDimension> = {
  spot_market_cap: 'stock',
  spot_volume: 'flow',
  dex_liquidity: 'stock',
  perp_volume: 'flow',
  perp_oi: 'stock',
};

export const SESSION_LABEL: Record<MarketSession, string> = {
  rth: '常规交易时段',
  pre: '盘前',
  ah: '盘后',
  closed_weekday: '工作日闭市',
  closed_weekend: '周末闭市',
  closed_holiday: '假日闭市',
};
