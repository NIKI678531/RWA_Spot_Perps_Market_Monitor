/**
 * Number and date formatting.
 *
 * The abbreviation suffixes stay K / M / B in every language. Chinese uses 万 and 亿,
 * which do not line up with thousands and billions; a reader who mentally converts a
 * localised 亿 back to a B gets the wrong number by two orders of magnitude, so the
 * unit is deliberately not localised (UI-LAYOUT.md §6).
 */

import type { Amount } from '@/api/types';

const UNITS: ReadonlyArray<[number, string]> = [
  [1e12, 'T'],
  [1e9, 'B'],
  [1e6, 'M'],
  [1e3, 'K'],
];

/** `$34.5M`, one decimal place, identical in every locale. */
export function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const amount = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(amount)) return '—';

  const sign = amount < 0 ? '-' : '';
  const magnitude = Math.abs(amount);
  for (const [size, suffix] of UNITS) {
    if (magnitude >= size) {
      return `${sign}$${(magnitude / size).toFixed(1)}${suffix}`;
    }
  }
  return `${sign}$${magnitude.toFixed(magnitude < 1 ? 4 : 2)}`;
}

/** Bare number with thousands separators, for counts rather than money. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.toLocaleString('en-US');
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

/** Signed change, for period-over-period deltas. Null stays a dash, never `0.0%`. */
export function formatChange(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(1)}%`;
}

export function amountNumber(amount: Amount | null | undefined): number | null {
  if (!amount || amount.value === null) return null;
  const parsed = Number(amount.value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

/** Whole minutes since `iso`, or null if it is unparseable. */
export function minutesSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 60000);
}

export function formatAge(iso: string | null | undefined): string {
  const minutes = minutesSince(iso);
  if (minutes === null) return '—';
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

/**
 * A span already measured in minutes, spelled out.
 *
 * Separate from `formatAge`, which measures against the browser clock. A reference
 * price is aged against the *snapshot*, not against now, so that reopening the tab
 * tomorrow does not silently make every quote look a day staler than the figures it
 * is being compared with.
 */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) {
    return '—';
  }
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${Math.round(minutes)} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时`;
  return `${Math.floor(hours / 24)} 天`;
}

/** A signed ratio as a percentage. `+2.67%`. Null stays a dash, never `0.00%`. */
export function formatBasis(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(2)}%`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
