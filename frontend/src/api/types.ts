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
export type AssetClass =
  'equity' | 'etf' | 'fund' | 'commodity' | 'fx' | 'index' | 'pre_ipo';

/**
 * The five verification states, which mean five different things (rule 21).
 *
 * `empty` is an observed zero and may be printed as one. `not_verified` is an
 * observation that never happened and must not reach the screen as a number at all.
 * `stale` was observed, but too long ago to carry a new conclusion.
 */
export type VerificationStatus =
  'verified' | 'partial' | 'not_verified' | 'stale' | 'empty';
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

/* ===========================================================================
 * R1 — the decision loop
 *
 * Everything below mirrors `app/schemas/{common,decision,workflow,editions}.py`.
 * The shapes are wider than the pre-R1 ones on purpose: a row that only carries a
 * headline can be read, but not worked. A queue row has to say whose it is, whether
 * it is late, and what argues against acting on it.
 * ========================================================================= */

/**
 * The value object every displayable R1 number travels in (rule 20).
 *
 * `Amount` says what a figure is; this says everything a reader needs before quoting
 * it. `raw_value` and `adjusted_value` are both carried because neither is
 * publishable alone — one venue in the reference data reports ~$29.3M raw against
 * ~$216 adjusted.
 */
export interface MetricValue {
  /** Null means not observed. It does not mean zero. */
  value: string | null;
  unit: string;
  metric_scope: MetricScope;
  metric_dimension: MetricDimension;
  raw_value: string | null;
  adjusted_value: string | null;
  verification_status: VerificationStatus;
  observed_at: string | null;
  window: string | null;
  source_count: number | null;
  /** Mandatory on a RATIO figure; the backend rejects one without it. */
  weight_basis: string | null;
}

/** The strip that sits on every page (GEN-001). */
export interface StatusBar {
  scopes: MetricScope[];
  edition_key: string;
  edition_kind: string;
  edition_revision: number;
  /** Data cut-off and render time. Routinely hours apart. */
  as_of: string;
  generated_at: string | null;
  data_age_minutes: number | null;
  sources_ok: number;
  sources_total: number;
  verification_status: VerificationStatus;
  next_refresh_at: string | null;
}

export interface HomeAsOf {
  status_bar: StatusBar;
  server_time: string;
}

/** How much weight a conclusion bears, and why. Deliberately not one number. */
export interface Confidence {
  level: 'low' | 'medium' | 'high';
  confirmations: number | null;
  confirmations_required: number | null;
  sample_size: number | null;
  basis: string | null;
}

export interface EvidenceItem {
  label: string;
  value: MetricValue | null;
  detail: string | null;
  href: string | null;
}

/**
 * One fact arguing against the conclusion. Never optional on a published finding —
 * a page that only ever argues one way trains its readers to stop reading it.
 */
export interface CounterEvidenceItem {
  /** Stable code (`raw_adjusted_gap`, `single_venue`, …) so weaknesses can be counted. */
  code: string;
  label: string;
  detail: string | null;
}

export type QuestionKey = 'q1_volume' | 'q2_heating' | 'q3_new_products' | 'q4_issuance';

export interface SummaryLine {
  fact: string;
  meaning: string;
  confidence: Confidence;
  action: string | null;
  action_href: string | null;
  /** A dependency was unverifiable, so the page states what it cannot judge. */
  indeterminate: boolean;
}

export interface QuestionCard {
  key: QuestionKey;
  question: string;
  answer: string;
  value: MetricValue | null;
  change_pct: number | null;
  change_basis: string | null;
  evidence: EvidenceItem[];
  counter_evidence: CounterEvidenceItem[];
  confidence: Confidence;
  cta_label: string;
  cta_href: string;
  unavailable_reason: string | null;
}

export interface KpiCard {
  scope: MetricScope;
  label: string;
  value: MetricValue;
  change_pct: number | null;
  change_basis: string | null;
  href: string | null;
}

export interface Highlight {
  kind: 'opportunity' | 'risk';
  title: string;
  detail: string | null;
  entity_type: EntityType | null;
  entity_id: string | null;
  value: MetricValue | null;
  confidence: Confidence | null;
  counter_evidence: CounterEvidenceItem[];
  href: string | null;
  alert_id: number | null;
}

export interface DrillLink {
  label: string;
  href: string;
  detail: string | null;
}

export interface DecisionHome {
  meta: Meta;
  status_bar: StatusBar;
  summary: SummaryLine;
  questions: QuestionCard[];
  opportunities: Highlight[];
  risks: Highlight[];
  kpis: KpiCard[];
  /** `high`/`critical` with complete evidence only — the gate decides, not the page. */
  alerts: QueueAlertRow[];
  drilldowns: DrillLink[];
}

export interface SearchHit {
  entity_type: EntityType;
  entity_id: string;
  name: string;
  kind_label: string;
  detail: string | null;
  href: string;
  score: number;
}

export interface SearchResults {
  meta: Meta;
  query: string;
  hits: SearchHit[];
  counts: Record<string, number>;
}

/* --- alert lifecycle ----------------------------------------------------- */

export type AlertState =
  | 'detected'
  | 'tentative'
  | 'published'
  | 'claimed'
  | 'in_review'
  | 'actioned'
  | 'resolved'
  | 'false_positive'
  | 'dismissed';

export type AlertActionType =
  | 'publish'
  | 'confirm'
  | 'claim'
  | 'release'
  | 'reassign'
  | 'note'
  | 'start_review'
  | 'action'
  | 'create_task'
  | 'defer'
  | 'mark_false_positive'
  | 'dismiss'
  | 'resolve'
  | 'evidence_appended';

export interface QueueAlertRow {
  id: number;
  detector: string;
  family: DetectorFamily;
  /** Server-side clause: the two families must not be described in each other's words. */
  family_claim: string;
  severity: AlertSeverity;
  score: number | null;
  state: AlertState;
  entity_type: EntityType;
  entity_id: string;
  entity_name: string | null;
  metric_scope: MetricScope;
  market_session: MarketSession;
  headline_zh: string;
  headline_en: string | null;
  owner_id: string | null;
  owner_name: string | null;
  confirmation_count: number;
  confirmations_required: number;
  evidence_completeness: string | null;
  first_seen_ts: string;
  last_seen_ts: string;
  published_at: string | null;
  sla_due_ts: string | null;
  /** Precomputed: "late" must mean one thing across queue, digest and retrospective. */
  is_overdue: boolean;
  deferred_until: string | null;
  occurrence_count: number;
}

export interface QueueSummary {
  unclaimed: number;
  in_progress: number;
  awaiting_review: number;
  closed: number;
  false_positive: number;
  overdue: number;
}

export interface AlertQueue {
  meta: Meta;
  summary: QueueSummary;
  rows: QueueAlertRow[];
}

export interface TimelineEntry {
  id: number;
  action: AlertActionType;
  from_state: AlertState | null;
  to_state: AlertState | null;
  actor_id: string | null;
  actor_kind: string;
  from_owner_id: string | null;
  to_owner_id: string | null;
  note: string | null;
  reason_code: string | null;
  created_at: string;
}

export interface QueueEvidenceRow {
  rule_name: string;
  snapshot_ts: string;
  observed_value: string | null;
  baseline_median: string | null;
  baseline_mad: string | null;
  robust_z: number | null;
  sample_size: number | null;
  market_session: MarketSession;
  peer_count: number | null;
  verification_status: VerificationStatus;
  version: number;
  revision_reason: string | null;
  extra: Record<string, unknown>;
  counter_evidence: CounterEvidenceItem[];
}

export interface AlertWorkItem {
  alert: QueueAlertRow;
  confidence: Confidence;
  evidence: QueueEvidenceRow[];
  counter_evidence: CounterEvidenceItem[];
  timeline: TimelineEntry[];
  tasks: ResearchTaskRow[];
  /** Resolved on the server. The drawer renders buttons from this rather than
   *  re-deriving the state machine, so the two cannot disagree. */
  available_actions: string[];
  close_reasons: string[];
}

export type ResearchTaskStatus =
  'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled';

export interface ResearchTaskRow {
  id: number;
  alert_id: number | null;
  title: string;
  detail: string | null;
  status: ResearchTaskStatus;
  owner_id: string | null;
  entity_type: EntityType | null;
  entity_id: string | null;
  due_at: string | null;
  closed_at: string | null;
  outcome: string | null;
  created_at: string;
}

export interface ResearchTaskList {
  meta: Meta;
  rows: ResearchTaskRow[];
}

/* --- issuance candidates -------------------------------------------------- */

export type CandidateStage =
  | 'watch'
  | 'screening'
  | 'feasibility'
  | 'proposal'
  | 'approved'
  | 'declined'
  | 'parked';

export type CandidateDecision = 'advance' | 'hold' | 'decline' | 'park';

/** One heatmap cell. Independently normalised, never summed with its siblings. */
export interface ReadinessCell {
  key: string;
  label: string;
  value: string | null;
  basis: string | null;
}

export interface CandidateRow {
  id: number;
  underlying_id: string;
  underlying_name: string;
  stage: CandidateStage;
  owner_id: string | null;
  /** No total field, and no place to put one — a row sum would be an approval score. */
  readiness: ReadinessCell[];
  weakest_dimension: string | null;
  rationale: string | null;
  counter_rationale: string | null;
  last_reviewed_at: string | null;
  allowed_decisions: CandidateDecision[];
}

export interface CandidateList {
  meta: Meta;
  rows: CandidateRow[];
  note: string;
}

export interface GateLogRow {
  id: number;
  from_stage: CandidateStage | null;
  to_stage: CandidateStage;
  decision: CandidateDecision;
  reason: string;
  decided_by: string;
  created_at: string;
}

export interface CandidateDetail {
  candidate: CandidateRow;
  history: GateLogRow[];
}

/* --- coverage and data gaps ---------------------------------------------- */

export type CoverageState = 'live' | 'planned' | 'gap' | 'not_applicable';

export interface CoverageRow {
  underlying_id: string;
  underlying_name: string;
  state: CoverageState;
  product_id: string | null;
  demand: MetricValue | null;
  note: string | null;
  assessed_at: string | null;
}

export interface CoverageList {
  meta: Meta;
  rows: CoverageRow[];
}

export type DataGapKind =
  | 'source_health'
  | 'entity_coverage'
  | 'quality_divergence'
  | 'verification'
  | 'baseline_health';

/** There is no `resolved`: a gap is fixed, or knowingly accepted with a reason. */
export type DataGapStatus = 'open' | 'assigned' | 'fixed' | 'accepted';

export interface DataGapRow {
  id: number;
  kind: DataGapKind;
  status: DataGapStatus;
  title: string;
  detail: string | null;
  blocks: string | null;
  impact_score: string | null;
  entity_type: EntityType | null;
  entity_id: string | null;
  source_id: string | null;
  owner_id: string | null;
  is_blocking: boolean;
  first_seen_ts: string;
  last_seen_ts: string;
  occurrence_count: number;
}

export interface DataGapZone {
  kind: DataGapKind;
  label: string;
  open_count: number;
  blocking_count: number;
  rows: DataGapRow[];
}

export interface DataGapQueue {
  meta: Meta;
  zones: DataGapZone[];
  /** True when a blocking gap is open — whether a conclusion is publishable at all. */
  publication_blocked: boolean;
}

/* --- editions, revisions, review ----------------------------------------- */

export type EditionKind = 'live' | 'morning' | 'afternoon';
export type EditionStatus = 'live' | 'frozen' | 'superseded' | 'failed';

export interface EditionRow {
  id: number;
  edition_key: string;
  kind: EditionKind;
  status: EditionStatus;
  revision: number;
  label: string;
  as_of: string;
  generated_at: string | null;
  trading_date: string;
  theme_map_version: number | null;
  superseded_by_id: number | null;
  superseded_by_revision: number | null;
  failure_reason: string | null;
  revision_reason: string | null;
  artifact_count: number;
}

export interface EditionList {
  meta: Meta;
  rows: EditionRow[];
}

export interface FieldDiffRow {
  path: string;
  before: unknown;
  after: unknown;
}

export interface EditionRevisionRow {
  id: number;
  revision: number;
  reason: string;
  created_by: string | null;
  created_at: string;
  supersedes_revision: number | null;
  diff: FieldDiffRow[];
}

export interface ArtifactRow {
  id: number;
  artifact_kind: string;
  filename: string;
  content_type: string | null;
  byte_size: number | null;
  created_at: string;
  href: string;
  /** The exact view state the export was taken under. */
  view_state: Record<string, unknown>;
}

export interface EditionDetail {
  edition: EditionRow;
  /** Redacted server-side for a share viewer, never in the browser. */
  payload: Record<string, unknown> | null;
  revisions: EditionRevisionRow[];
  artifacts: ArtifactRow[];
}

export interface ShareLink {
  token: string;
  href: string;
  edition_key: string;
  revision: number;
  expires_at: string | null;
  /** Stated back to the sharer: redaction they cannot see is redaction they won't trust. */
  redacted_fields: string[];
}

export interface RetrospectiveRow {
  alert_id: number;
  detector: string;
  family: DetectorFamily;
  severity: AlertSeverity;
  final_state: AlertState;
  entity_id: string;
  entity_name: string | null;
  headline_zh: string;
  owner_id: string | null;
  published_at: string | null;
  resolved_ts: string | null;
  time_to_close_hours: number | null;
  reason_code: string | null;
  outcome_note: string | null;
  within_sla: boolean | null;
}

export interface DetectorReviewRow {
  detector: string;
  family: DetectorFamily;
  published: number;
  actioned: number;
  false_positive: number;
  dismissed: number;
  open_count: number;
  /** Null when nothing has closed — a rate over zero cases reads as 0%. */
  false_positive_rate: number | null;
  median_time_to_close_hours: number | null;
  top_counter_evidence: string | null;
}

export interface Retrospective {
  meta: Meta;
  window_days: number;
  rows: RetrospectiveRow[];
  detectors: DetectorReviewRow[];
}

/* --- themes and underlyings ---------------------------------------------- */

export interface ThemeRow {
  theme_id: string;
  name_zh: string | null;
  name_en: string | null;
  underlying_count: number;
  spot_vol_adjusted: Amount;
  perp_vol_24h: Amount;
}

export interface ThemeList {
  meta: Meta;
  rows: ThemeRow[];
}

/** The prepared orderings. Each names exactly one scope, so a ranking cannot mix them. */
export type UnderlyingSort =
  'spot_volume' | 'spot_market_cap' | 'perp_volume' | 'perp_oi' | 'name';

export interface UnderlyingRow {
  underlying_id: string;
  name: string;
  asset_class: AssetClass;
  region: string | null;
  is_pre_ipo: boolean;
  theme_id: string | null;
  /** One issuer on one venue is a listing; four across nine venues is competition. */
  wrapper_count: number;
  issuer_count: number;
  venue_count: number;
  has_perp: boolean;
  spot_market_cap: Amount;
  spot_vol_raw: Amount;
  spot_vol_adjusted: Amount;
  perp_vol_24h: Amount;
  perp_oi_usd: Amount;
  open_alert_count: number;
  coverage_state: CoverageState | null;
}

export interface UnderlyingList {
  meta: Meta;
  sort: UnderlyingSort;
  rows: UnderlyingRow[];
}

export interface WrapperRow {
  asset_id: string;
  symbol: string;
  issuer: string | null;
  chain: string | null;
  rwa_tier: RwaTier;
  market_cap: Amount;
  vol_24h: Amount;
}

export interface VenueBreakdownRow {
  venue_id: string;
  venue: string;
  venue_type: VenueType | null;
  adjusted_vol_24h: Amount;
}

export interface PerpExposureRow {
  exchange: string;
  perp_dex: string;
  contract: string;
  vol_24h: Amount;
  open_interest_usd: Amount;
}

export interface FirstListingRow {
  asset_id: string;
  symbol: string;
  issuer_id: string | null;
  issuer: string | null;
  /** The first observation, not an announced listing date. No free source has one. */
  first_seen_at: string;
  is_measured: boolean;
  venue_count: number | null;
}

export interface Underlying360 {
  meta: Meta;
  underlying_id: string;
  name: string;
  asset_class: AssetClass;
  region: string | null;
  is_pre_ipo: boolean;
  theme_id: string | null;
  benchmark_id: string | null;
  tokenized_wrappers: WrapperRow[];
  venue_breakdown: VenueBreakdownRow[];
  perp_exposure: PerpExposureRow[];
  spot_market_cap: Amount;
  spot_vol_adjusted: Amount;
  perp_vol_24h: Amount;
  perp_oi_usd: Amount;
  scope_note: string;
  active_alerts: AlertRow[];
  our_coverage: CoverageRow | null;
  candidates: CandidateRow[];
  open_data_gaps: DataGapRow[];
  first_listings: FirstListingRow[];
}

/* --- label maps ----------------------------------------------------------
 * Chinese is the source language (UI-LAYOUT.md §6), so these are the fallbacks
 * `t()` is given rather than a second translation layer.
 * ------------------------------------------------------------------------- */

export const STATE_LABEL: Record<AlertState, string> = {
  detected: '已检出',
  tentative: '待确认',
  published: '待认领',
  claimed: '处理中',
  in_review: '待复核',
  actioned: '已行动',
  resolved: '已结案',
  false_positive: '误报',
  dismissed: '不处理',
};

export const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

export const VERIFICATION_LABEL: Record<VerificationStatus, string> = {
  verified: '已验证',
  partial: '部分验证',
  not_verified: '未验证',
  stale: '过期',
  empty: '已验证为零',
};

export const TASK_STATUS_LABEL: Record<ResearchTaskStatus, string> = {
  open: '待开始',
  in_progress: '进行中',
  blocked: '受阻',
  done: '已完成',
  cancelled: '已取消',
};

export const STAGE_LABEL: Record<CandidateStage, string> = {
  watch: '观察',
  screening: '初筛',
  feasibility: '可行性',
  proposal: '提案',
  approved: '已通过',
  declined: '已否决',
  parked: '已搁置',
};

export const DECISION_LABEL: Record<CandidateDecision, string> = {
  advance: '推进',
  hold: '维持',
  decline: '否决',
  park: '搁置',
};

export const COVERAGE_STATE_LABEL: Record<CoverageState, string> = {
  live: '已覆盖',
  planned: '计划中',
  gap: '空白',
  not_applicable: '不适用',
};

export const GAP_KIND_LABEL: Record<DataGapKind, string> = {
  source_health: '源健康',
  entity_coverage: '实体覆盖',
  quality_divergence: '质量差异',
  verification: '验证状态',
  baseline_health: '基线健康',
};

export const GAP_STATUS_LABEL: Record<DataGapStatus, string> = {
  open: '待处理',
  assigned: '已分派',
  fixed: '已修复',
  accepted: '已接受',
};

export const EDITION_KIND_LABEL: Record<EditionKind, string> = {
  live: 'Live',
  morning: '早版 09:00',
  afternoon: '午版 17:00',
};

export const EDITION_STATUS_LABEL: Record<EditionStatus, string> = {
  live: '滚动',
  frozen: '已冻结',
  superseded: '已被取代',
  failed: '生成失败',
};

export const ASSET_CLASS_LABEL: Record<AssetClass, string> = {
  equity: '股票',
  etf: 'ETF',
  fund: '基金',
  commodity: '商品',
  fx: '外汇',
  index: '指数',
  pre_ipo: 'Pre-IPO',
};

export const TIER_LABEL: Record<RwaTier, string> = {
  core_rwa: '核心 RWA',
  rwa_adjacent: 'RWA 相邻',
  synthetic: '合成敞口',
  non_rwa: '非 RWA',
};

/** Which alert states count as closed. Mirrors `CLOSED_ALERT_STATES`. */
export const CLOSED_ALERT_STATES: ReadonlySet<AlertState> = new Set<AlertState>([
  'resolved',
  'false_positive',
  'dismissed',
]);
