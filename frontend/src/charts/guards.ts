/**
 * Chart-layer assertions (DATAVIZ.md §6).
 *
 * These throw rather than degrade. A chart that silently drops a series or quietly
 * picks a different type still gets read as a statement about the market, and a
 * wrong statement drawn confidently is worse than a missing panel. The backend
 * enforces the same rules in `app/core/metrics.py`; both sides do it because chart
 * data can also be derived locally.
 *
 * `assertSingleScope`, `assertSameAxis` and `assertSeriesLimit` run on every chart the
 * app draws. `assertAdditive` and `convergeToTop8` have no caller yet: no pie or
 * stacked chart exists here, and the overlapping-category rule is currently enforced by
 * that absence rather than by a check. They are the contract the first such chart has
 * to pass — not evidence that anything is being checked today.
 */

import type { MetricScope } from '@/api/types';
import { scopeLabel } from '@/api/types';

export class ChartScopeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ChartScopeError';
  }
}

/** Categorical palette cap. Nine is the limit of reliable visual discrimination. */
export const MAX_SERIES = 9;

/** One plotted series: a name, its values, and the scope those values belong to. */
export interface SeriesSpec {
  name: string;
  scope: MetricScope;
  values: ReadonlyArray<number | null>;
}

/** Every series sharing one Y axis must share one scope. */
export function assertSingleScope(series: ReadonlyArray<SeriesSpec>): void {
  const scopes = new Set(series.map((s) => s.scope));
  if (scopes.size > 1) {
    throw new ChartScopeError(
      `one axis cannot hold ${[...scopes].join(' and ')}; use dual axes or split ` +
        'the chart (CLAUDE.md chart rules)',
    );
  }
}

/**
 * A dual-axis chart carries exactly two scopes — one per axis. Both other counts are
 * refused.
 *
 * Three is unreadable long before it is wrong. One is the case worth spelling out: the
 * two axes are scaled independently, so plotting a scope against itself draws a bar at
 * half height beside a line at full height and invites the reader to see a difference
 * in the market where there is only a difference in the axis. Values of one kind
 * belong on one axis.
 *
 * Two scopes of the same dimension — spot volume against perp volume, both flows — do
 * pass. They are still not addable, which is exactly why they get an axis each.
 */
export function assertSameAxis(scopes: ReadonlyArray<MetricScope>): void {
  const distinct = [...new Set(scopes)];
  if (distinct.length > 2) {
    throw new ChartScopeError(
      `${distinct.length} scopes on one chart; split it rather than adding a third axis`,
    );
  }
  if (distinct.length < 2) {
    throw new ChartScopeError(
      `a dual-axis chart needs two scopes, got ${distinct[0] ?? 'none'}; two ` +
        'independently scaled axes make one scope look like two',
    );
  }
}

export type ChartType = 'bar' | 'line' | 'pie' | 'stacked-bar' | 'scatter' | 'heatmap';

/**
 * Pie and stacked shapes assert that the parts sum to the whole. For the overlapping
 * CoinGecko categories that assertion is false by construction, so the shape is
 * refused for any dataset carrying `is_additive: false`.
 */
export function assertAdditive(
  rows: ReadonlyArray<{ is_additive: boolean }>,
  chartType: ChartType,
): void {
  const impliesSummation = chartType === 'pie' || chartType === 'stacked-bar';
  if (!impliesSummation) return;
  if (rows.some((row) => !row.is_additive)) {
    throw new ChartScopeError(
      `a ${chartType} states that the parts sum to the whole, which is false for ` +
        'overlapping categories; use grouped bars plus a separate union bar',
    );
  }
}

export function assertSeriesLimit(series: ReadonlyArray<unknown>): void {
  if (series.length > MAX_SERIES) {
    throw new ChartScopeError(
      `${series.length} series exceeds the ${MAX_SERIES}-colour cap; converge to ` +
        'Top 8 plus "other" before plotting',
    );
  }
}

/** Top 8 by value plus an "other" bucket, which is how the cap is respected. */
export function convergeToTop8<T>(
  rows: ReadonlyArray<T>,
  valueOf: (row: T) => number | null,
  otherLabel = '其他',
): { rows: T[]; other: { label: string; value: number } | null } {
  if (rows.length <= MAX_SERIES) return { rows: [...rows], other: null };
  const sorted = [...rows].sort((a, b) => (valueOf(b) ?? 0) - (valueOf(a) ?? 0));
  const head = sorted.slice(0, MAX_SERIES - 1);
  const tail = sorted.slice(MAX_SERIES - 1);
  const total = tail.reduce((sum, row) => sum + (valueOf(row) ?? 0), 0);
  return { rows: head, other: { label: otherLabel, value: total } };
}

/**
 * The axis title carries the full scope phrase — "成交额" alone names no scope.
 *
 * Only two label sets exist; ko and zh-TW fall back to zh rather than to a scope
 * phrase we have not had reviewed, since a mistranslated scope is exactly the kind of
 * error this whole layer exists to prevent.
 */
export function axisTitle(scope: MetricScope, locale = 'zh'): string {
  return scopeLabel(scope, locale);
}
