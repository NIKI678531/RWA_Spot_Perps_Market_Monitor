/**
 * Reading design tokens out of CSS.
 *
 * `tokens.css` is the single source of truth for every colour, radius and duration,
 * but antd and ECharts both want JavaScript values. Rather than keep a second copy of
 * the palette in TS — which is how a design system drifts — both read the same custom
 * properties at runtime, which also makes them follow dark mode for free.
 *
 * The fallbacks are only for a non-browser context (tests, SSR). In the browser the
 * stylesheet is imported before the first render, so the real value is always there.
 */

export function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.body).getPropertyValue(name).trim();
  return value || fallback;
}

function pixels(name: string, fallback: number): number {
  const raw = cssVar(name, '');
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export interface AntdSeedTokens {
  colorPrimary: string;
  colorSuccess: string;
  colorWarning: string;
  colorError: string;
  colorInfo: string;
  colorText: string;
  colorTextSecondary: string;
  colorBgContainer: string;
  colorBorder: string;
  borderRadius: number;
  borderRadiusLG: number;
  fontFamily: string;
  fontSize: number;
}

/**
 * The subset antd needs. Read after the `dark-mode` class has been applied, so the
 * caller must recompute this when the theme flips.
 */
export function readAntdTokens(): AntdSeedTokens {
  return {
    colorPrimary: cssVar('--color-primary', '#2361AD'),
    colorSuccess: cssVar('--color-success', '#52C41A'),
    colorWarning: cssVar('--color-warning', '#FAAD14'),
    colorError: cssVar('--color-error', '#F5222D'),
    colorInfo: cssVar('--color-info', '#1890FF'),
    colorText: cssVar('--color-text', '#1E293B'),
    colorTextSecondary: cssVar('--color-text-secondary', '#64748B'),
    // Transparent on purpose: antd surfaces sit on the glass card, and a solid
    // container colour would punch an opaque hole through it.
    colorBgContainer: 'transparent',
    colorBorder: cssVar('--color-border', 'rgba(100,116,139,0.35)'),
    borderRadius: pixels('--rounded-md', 12),
    borderRadiusLG: pixels('--rounded-lg', 16),
    fontFamily: cssVar(
      '--font-sans',
      'Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
    ),
    fontSize: pixels('--text-body-md-size', 14),
  };
}
