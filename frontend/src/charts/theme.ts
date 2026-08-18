/**
 * The single ECharts theme (DATAVIZ.md §1, §3, §4).
 *
 * Values are read from the CSS custom properties in styles/tokens.css so the chart
 * layer cannot drift from DESIGN.md and follows dark mode without a second palette.
 * No component may pass a literal colour to ECharts.
 */

import type { EChartsOption } from 'echarts';

import { cssVar } from '@/styles/tokens';

/** Categorical palette, capped at nine and ordered by discriminability. */
export function categoricalPalette(): string[] {
  return [
    cssVar('--color-primary', '#2361AD'),
    cssVar('--color-cat-marketing', '#722ED1'),
    cssVar('--color-cat-trading', '#52C41A'),
    cssVar('--color-cat-pcs', '#FAAD14'),
    cssVar('--color-cat-data', '#1890FF'),
    cssVar('--color-error', '#F5222D'),
    cssVar('--color-accent', '#60A5FA'),
    cssVar('--color-primary-hover', '#1A4E8A'),
    cssVar('--color-cat-system', '#6B7280'),
  ];
}

/** The "other" bucket and every not-verified placeholder share this grey. */
export function disabledColor(): string {
  return cssVar('--color-text-disabled', '#94A3B8');
}

export function semanticColor(sign: 'positive' | 'negative' | 'neutral'): string {
  if (sign === 'positive') return cssVar('--color-success', '#52C41A');
  if (sign === 'negative') return cssVar('--color-error', '#F5222D');
  return cssVar('--color-text-secondary', '#64748B');
}

/**
 * A 45° hatch in the disabled grey, used for bars whose value was never observed.
 * The bar is drawn at the series mean height, never at zero: a zero-height bar reads
 * as "observed and idle", which is a different and false claim.
 */
export function notVerifiedPattern(): Record<string, unknown> {
  const stripe = disabledColor();
  return {
    image: hatchCanvas(stripe),
    repeat: 'repeat',
  };
}

function hatchCanvas(color: string): HTMLCanvasElement | undefined {
  if (typeof document === 'undefined') return undefined;
  const size = 8;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return canvas;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, size);
  ctx.lineTo(size, 0);
  ctx.moveTo(-2, 2);
  ctx.lineTo(2, -2);
  ctx.moveTo(size - 2, size + 2);
  ctx.lineTo(size + 2, size - 2);
  ctx.stroke();
  return canvas;
}

/**
 * DESIGN.md requires every motion to degrade under `prefers-reduced-motion`, and
 * tokens.css does that for CSS by collapsing the `dur-*` values. ECharts animates in
 * canvas and never reads a stylesheet, so charts stayed animated no matter the setting
 * until this is checked explicitly.
 */
function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Everything shared by every chart: axis styling, tooltip surface, entrance motion.
 * Duration values come from the `dur-*` vocabulary; nothing invents its own.
 */
export function baseOption(): EChartsOption {
  const text = cssVar('--color-text', '#1E293B');
  const textSecondary = cssVar('--color-text-secondary', '#64748B');
  const border = cssVar('--color-border', 'rgba(100,116,139,0.35)');
  const surfaceStrong = cssVar('--color-surface-strong', '#FFFFFFEB');
  const primaryContainer = cssVar('--color-primary-container', 'rgba(35,97,173,0.12)');

  return {
    color: categoricalPalette(),
    textStyle: {
      fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
      color: text,
    },
    animation: !prefersReducedMotion(),
    // dur-base + ease-emphasized, with a 60ms-per-series stagger capped at 600ms.
    animationDuration: 300,
    animationEasing: 'cubicOut',
    animationDelay: (index: number) => Math.min(index * 60, 600),
    // A filter change is a transition of the same chart, not a new one, so the update
    // eases over dur-base rather than snapping (DESIGN.md principle 5).
    animationDurationUpdate: 300,
    animationEasingUpdate: 'cubicOut',
    grid: {
      left: 8,
      right: 16,
      top: 24,
      bottom: 8,
      containLabel: true,
    },
    tooltip: {
      // components.popover tokens: surface-strong, rounded.xl, padding 8.
      backgroundColor: surfaceStrong,
      borderWidth: 0,
      borderRadius: 16,
      padding: 8,
      extraCssText: 'box-shadow: 0 12px 32px rgba(17,24,39,0.10);',
      textStyle: { color: text, fontSize: 13 },
      transitionDuration: 0.2,
      // The band behind the hovered category reuses the same container colour that
      // marks hover on chips and table rows, so "pointing at something" looks the same
      // everywhere. ECharts' default is a grey that belongs to no palette.
      //
      // It is drawn on top of the bars, so the token has to stay an alpha — an opaque
      // container colour here hides the bar the reader is hovering to read.
      axisPointer: {
        type: 'shadow',
        shadowStyle: { color: primaryContainer },
        lineStyle: { color: border, width: 1, type: [3, 3] },
      },
    },
    legend: {
      // Series colours borrow the badge category tokens, which carry an unrelated
      // business meaning there. A legend is therefore mandatory, never optional.
      type: 'scroll',
      bottom: 0,
      itemWidth: 10,
      itemHeight: 10,
      icon: 'roundRect',
      textStyle: { color: textSecondary, fontSize: 12 },
    },
    xAxis: {
      axisLine: { lineStyle: { color: border } },
      axisTick: { show: false },
      axisLabel: { color: textSecondary, fontSize: 12 },
      nameTextStyle: { color: textSecondary, fontSize: 13 },
      splitLine: { show: false },
    },
    yAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: textSecondary, fontSize: 12 },
      nameTextStyle: { color: textSecondary, fontSize: 13 },
      // Horizontal grid lines only, dashed, at half the border alpha.
      splitLine: { lineStyle: { color: border, type: [3, 3], opacity: 0.5 } },
    },
  };
}

/** Axis label formatter: `$1.2K / $34.5M / $6.7B`, identical in every locale. */
export function usdAxisFormatter(value: number): string {
  const magnitude = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  if (magnitude >= 1e12) return `${sign}$${(magnitude / 1e12).toFixed(1)}T`;
  if (magnitude >= 1e9) return `${sign}$${(magnitude / 1e9).toFixed(1)}B`;
  if (magnitude >= 1e6) return `${sign}$${(magnitude / 1e6).toFixed(1)}M`;
  if (magnitude >= 1e3) return `${sign}$${(magnitude / 1e3).toFixed(1)}K`;
  return `${sign}$${magnitude.toFixed(0)}`;
}
