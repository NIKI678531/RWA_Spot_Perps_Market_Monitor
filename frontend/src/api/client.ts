/**
 * The HTTP client. Same-origin `/api` in both environments: nginx serves the bundle
 * and proxies `/api` in Docker, and webpack-dev-server proxies the same prefix in
 * development, so no build-time host ever ends up in the bundle.
 */

import type {
  AlertDetail,
  AlertList,
  BenchmarkList,
  CategoryScale,
  DataQuality,
  ExecutiveKpi,
  Health,
  PairList,
  PerpContractList,
  PerpDexList,
  PerpVenueList,
  ReportList,
  Timeseries,
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

async function getJson<T>(
  path: string,
  params?: Params,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(withQuery(path, params), {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    // The detail body is where a 400 explains which series it refused and why.
    const detail = await response.text().catch(() => '');
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new ApiError(response.status, detail || response.statusText);
  }
  return (await response.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => getJson<Health>('/health', undefined, signal),

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
