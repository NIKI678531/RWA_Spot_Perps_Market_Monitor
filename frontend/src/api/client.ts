/**
 * The HTTP client. Same-origin `/api` in both environments: nginx serves the bundle
 * and proxies `/api` in Docker, and webpack-dev-server proxies the same prefix in
 * development, so no build-time host ever ends up in the bundle.
 *
 * Two things every write goes through. It carries the identity headers, because the
 * backend refuses an unattributable state change (`X-User-Id`, see `identity.ts`);
 * and it returns the server's own view of the object afterwards rather than patching
 * a local copy, because the state machine and the available actions are resolved
 * server-side and a browser that re-derives them will eventually disagree.
 */

import { identityHeaders } from './identity';
import type {
  AlertDetail,
  AlertList,
  AlertQueue,
  AlertState,
  AlertWorkItem,
  BenchmarkList,
  CandidateDecision,
  CandidateDetail,
  CandidateList,
  CategoryScale,
  CoverageList,
  DataGapQueue,
  DataGapRow,
  DataGapStatus,
  DataQuality,
  DecisionHome,
  EditionDetail,
  EditionList,
  ExecutiveKpi,
  Health,
  HomeAsOf,
  PairList,
  PerpContractList,
  PerpDexList,
  PerpVenueList,
  ReportList,
  ResearchTaskList,
  ResearchTaskRow,
  ResearchTaskStatus,
  Retrospective,
  SearchResults,
  ShareLink,
  ThemeList,
  Timeseries,
  Underlying360,
  UnderlyingList,
  UnderlyingSort,
  VenueRanking,
} from './types';

export const API_BASE = '/api';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;

function withQuery(path: string, params?: Params): string {
  if (!params) return `${API_BASE}${path}`;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${API_BASE}${path}?${query}` : `${API_BASE}${path}`;
}

/**
 * FastAPI reports errors as `{"detail": …}`. Pulling the message out matters for the
 * write surfaces in particular: "this alert is not yours to close" is the whole
 * content of the response, and showing the reader raw JSON hides it.
 */
async function failure(response: Response): Promise<ApiError> {
  const body = await response.text().catch(() => '');
  if (!body) return new ApiError(response.status, response.statusText);
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === 'string') {
      return new ApiError(response.status, parsed.detail);
    }
    if (Array.isArray(parsed.detail)) {
      // Pydantic validation errors: one line per rejected field.
      const lines = parsed.detail
        .map((item) => {
          const entry = item as { loc?: unknown[]; msg?: string };
          const field = Array.isArray(entry.loc) ? entry.loc.slice(-1)[0] : '';
          return [field, entry.msg].filter(Boolean).join(': ');
        })
        .filter(Boolean);
      if (lines.length) return new ApiError(response.status, lines.join('；'));
    }
  } catch {
    /* not JSON; fall through to the raw body */
  }
  return new ApiError(response.status, body);
}

async function getJson<T>(
  path: string,
  params?: Params,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(withQuery(path, params), {
    headers: { Accept: 'application/json', ...identityHeaders() },
    signal,
  });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body?: unknown, params?: Params): Promise<T> {
  const response = await fetch(withQuery(path, params), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...identityHeaders(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

/*
 * Filters accepted by the alert work queue. Mirrors `routes/alert_queue.py`.
 *
 * A type alias rather than an interface, here and below, because only an alias picks
 * up an implicit index signature — without it these cannot be passed to `getJson`,
 * whose `Params` is a `Record`. Declaring the fields is the point: an untyped bag
 * would let a filter the backend does not accept through to the query string.
 */
export type QueueParams = {
  severity?: string;
  state?: string;
  family?: string;
  metric_scope?: string;
  market_session?: string;
  /** `me` resolves to the caller server-side, so the browser never guesses. */
  owner_id?: string;
  detector?: string;
  only_overdue?: boolean;
  include_closed?: boolean;
  include_deferred?: boolean;
  limit?: number;
};

export type UnderlyingParams = {
  q?: string;
  asset_class?: string;
  region?: string;
  theme_id?: string;
  is_pre_ipo?: boolean;
  has_perp?: boolean;
  coverage_state?: string;
  sort?: UnderlyingSort;
  limit?: number;
};

export const api = {
  health: (signal?: AbortSignal) => getJson<Health>('/health', undefined, signal),

  // --- R1: decision surface -------------------------------------------------

  home: (params?: { as_of?: string }, signal?: AbortSignal) =>
    getJson<DecisionHome>('/home', params, signal),

  statusBar: (params?: { as_of?: string }, signal?: AbortSignal) =>
    getJson<HomeAsOf>('/status-bar', params, signal),

  search: (params: { q: string; limit?: number }, signal?: AbortSignal) =>
    getJson<SearchResults>('/search', params, signal),

  // --- R1: the alert work queue --------------------------------------------

  alertQueue: (params?: QueueParams, signal?: AbortSignal) =>
    getJson<AlertQueue>('/alerts/queue', params, signal),

  workItem: (id: number, signal?: AbortSignal) =>
    getJson<AlertWorkItem>(`/alerts/${id}/workitem`, undefined, signal),

  claimAlert: (id: number, note?: string) =>
    postJson<AlertWorkItem>(`/alerts/${id}/claim`, { note: note ?? null }),

  releaseAlert: (id: number, note?: string) =>
    postJson<AlertWorkItem>(`/alerts/${id}/release`, { note: note ?? null }),

  reassignAlert: (id: number, toUserId: string, note?: string) =>
    postJson<AlertWorkItem>(`/alerts/${id}/reassign`, {
      to_user_id: toUserId,
      note: note ?? null,
    }),

  noteAlert: (id: number, note: string) =>
    postJson<AlertWorkItem>(`/alerts/${id}/note`, { note }),

  reviewAlert: (id: number) => postJson<AlertWorkItem>(`/alerts/${id}/review`),

  actionAlert: (id: number, note: string) =>
    postJson<AlertWorkItem>(`/alerts/${id}/action`, { note }),

  deferAlert: (id: number, until: string, note?: string) =>
    postJson<AlertWorkItem>(`/alerts/${id}/defer`, { until, note: note ?? null }),

  createAlertTask: (
    id: number,
    body: { title: string; detail?: string; owner_id?: string; due_at?: string },
  ) => postJson<AlertWorkItem>(`/alerts/${id}/tasks`, body),

  /** Closing needs a retrospective note or a standard declined reason — never neither. */
  closeAlert: (
    id: number,
    body: { to_state: AlertState; note?: string; reason_code?: string },
  ) => postJson<AlertWorkItem>(`/alerts/${id}/close`, body),

  // --- R1: research tasks ---------------------------------------------------

  tasks: (
    params?: {
      status?: ResearchTaskStatus;
      owner_id?: string;
      alert_id?: number;
      include_closed?: boolean;
      limit?: number;
    },
    signal?: AbortSignal,
  ) => getJson<ResearchTaskList>('/tasks', params, signal),

  createTask: (body: {
    title: string;
    detail?: string;
    owner_id?: string;
    due_at?: string;
  }) => postJson<ResearchTaskRow>('/tasks', body),

  updateTaskStatus: (id: number, status: ResearchTaskStatus, outcome?: string) =>
    postJson<ResearchTaskRow>(`/tasks/${id}/status`, { status, outcome }),

  // --- R1: research surface -------------------------------------------------

  underlyings: (params?: UnderlyingParams, signal?: AbortSignal) =>
    getJson<UnderlyingList>('/underlyings', params, signal),

  underlying: (id: string, params?: { as_of?: string }, signal?: AbortSignal) =>
    getJson<Underlying360>(`/underlyings/${encodeURIComponent(id)}`, params, signal),

  themes: (params?: { as_of?: string }, signal?: AbortSignal) =>
    getJson<ThemeList>('/themes', params, signal),

  candidates: (
    params?: {
      stage?: string;
      owner_id?: string;
      underlying?: string;
      include_closed?: boolean;
      limit?: number;
    },
    signal?: AbortSignal,
  ) => getJson<CandidateList>('/candidates', params, signal),

  candidate: (id: number, signal?: AbortSignal) =>
    getJson<CandidateDetail>(`/candidates/${id}`, undefined, signal),

  /** A gate decision. No score moves a candidate; only this call does (rule 17). */
  decideCandidate: (id: number, decision: CandidateDecision, reason: string) =>
    postJson<CandidateDetail>(`/candidates/${id}/decide`, { decision, reason }),

  // --- R1: governance -------------------------------------------------------

  dataGaps: (
    params?: {
      kind?: string;
      owner_id?: string;
      only_blocking?: boolean;
      limit?: number;
    },
    signal?: AbortSignal,
  ) => getJson<DataGapQueue>('/data-gaps', params, signal),

  assignGap: (id: number, ownerId: string) =>
    postJson<DataGapRow>(`/data-gaps/${id}/assign`, { owner_id: ownerId }),

  closeGap: (id: number, status: DataGapStatus, note: string) =>
    postJson<DataGapRow>(`/data-gaps/${id}/close`, { status, note }),

  coverage: (params?: { state?: string; limit?: number }, signal?: AbortSignal) =>
    getJson<CoverageList>('/coverage', params, signal),

  // --- R1: editions and review ---------------------------------------------

  editions: (params?: { kind?: string; limit?: number }, signal?: AbortSignal) =>
    getJson<EditionList>('/editions', params, signal),

  edition: (
    key: string,
    params?: { revision?: number; share_token?: string },
    signal?: AbortSignal,
  ) => getJson<EditionDetail>(`/editions/${encodeURIComponent(key)}`, params, signal),

  /** A frozen edition is never edited; a correction is a new numbered revision. */
  reviseEdition: (key: string, reason: string) =>
    postJson<EditionDetail>(`/editions/${encodeURIComponent(key)}/revise`, { reason }),

  createShareLink: (key: string, body?: { expires_at?: string; note?: string }) =>
    postJson<ShareLink>(`/editions/${encodeURIComponent(key)}/share`, body ?? {}),

  artifactUrl: (key: string, artifactId: number) =>
    `${API_BASE}/editions/${encodeURIComponent(key)}/artifacts/${artifactId}`,

  retrospective: (
    params?: { days?: number; detector?: string; family?: string },
    signal?: AbortSignal,
  ) => getJson<Retrospective>('/retrospective', params, signal),

  // --- market structure (P1 depth) -----------------------------------------

  executiveKpi: (signal?: AbortSignal) =>
    getJson<ExecutiveKpi>('/kpi/executive', undefined, signal),

  categories: (signal?: AbortSignal) =>
    getJson<CategoryScale>('/scale/categories', undefined, signal),

  venues: (params?: { venue_type?: string; limit?: number }, signal?: AbortSignal) =>
    getJson<VenueRanking>('/spot/venues', params, signal),

  pairs: (
    params?: {
      venue_id?: string;
      underlying_id?: string;
      flagged_only?: boolean;
      limit?: number;
    },
    signal?: AbortSignal,
  ) => getJson<PairList>('/spot/pairs', params, signal),

  perpContracts: (
    params?: { exchange?: string; perp_dex?: string; limit?: number },
    signal?: AbortSignal,
  ) => getJson<PerpContractList>('/perps/contracts', params, signal),

  perpDexs: (signal?: AbortSignal) =>
    getJson<PerpDexList>('/perps/dexs', undefined, signal),

  perpVenues: (signal?: AbortSignal) =>
    getJson<PerpVenueList>('/perps/venues', undefined, signal),

  benchmark: (params?: { limit?: number }, signal?: AbortSignal) =>
    getJson<BenchmarkList>('/benchmark', params, signal),

  alerts: (
    params?: { severity?: string; status?: string; family?: string; limit?: number },
    signal?: AbortSignal,
  ) => getJson<AlertList>('/alerts', params, signal),

  alert: (id: number, signal?: AbortSignal) =>
    getJson<AlertDetail>(`/alerts/${id}`, undefined, signal),

  dataQuality: (signal?: AbortSignal) =>
    getJson<DataQuality>('/data-quality', undefined, signal),

  timeseries: (
    params: {
      entity_type: string;
      entity_id: string;
      metric: string;
      days?: number;
      until?: string;
    },
    signal?: AbortSignal,
  ) => getJson<Timeseries>('/timeseries', params, signal),

  reports: (signal?: AbortSignal) => getJson<ReportList>('/reports', undefined, signal),

  generateReports: () => postJson<ReportList>('/reports/generate'),

  reportUrl: (reportDate: string, format: 'excel' | 'word') =>
    `${API_BASE}/reports/${reportDate}/${format}`,
};
