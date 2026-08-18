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

import type { ThemeConfig } from 'antd';

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

/**
 * Per-component overrides, read at the same moment as the seed tokens.
 *
 * antd draws a tooltip on `colorBgSpotlight`, which defaults to near-opaque black in
 * both algorithms. That is the wrong surface here and it is not a small mistake:
 * `AmountValue` puts a tooltip on every amount in the app, so pointing at a figure
 * drops a black slab over the figure you were reading. DESIGN.md `components.popover`
 * defines a pop layer as `surface-strong` + `text` + `rounded.xl`, so the tokens are
 * pointed at exactly those.
 *
 * Scoped under `Tooltip` rather than set as a seed alias: a spotlight surface is a
 * legitimate thing for some other component to want, and this is a statement about
 * tooltips, not about the palette.
 */
export function readAntdComponentTokens(): ThemeConfig['components'] {
  const border = cssVar('--color-border', 'rgba(100,116,139,0.35)');
  const primaryContainer = cssVar('--color-primary-container', 'rgba(35,97,173,0.12)');

  return {
    // antd's own table chrome would double the card's surface; the glass card
    // underneath is the surface, so the table draws only rows and dividers.
    //
    // Every state surface below is antd's, and antd derives all of them from the
    // `colorFill*` ramp — which is plain black at a low alpha in the light algorithm.
    // Over a glass card that reads as a dirty grey slab across the row you are
    // pointing at. They are all pointed at the primary container instead: one tint,
    // stated as an alpha, so a highlighted row is tinted rather than covered.
    Table: {
      headerBg: 'transparent',
      borderColor: border,
      rowHoverBg: primaryContainer,
      rowSelectedBg: primaryContainer,
      rowSelectedHoverBg: primaryContainer,
      rowExpandedBg: 'transparent',
      bodySortBg: 'transparent',
      headerSortActiveBg: 'transparent',
      headerSortHoverBg: primaryContainer,
      headerFilterHoverBg: primaryContainer,
      footerBg: 'transparent',
      filterDropdownBg: cssVar('--color-surface-strong', '#FFFFFFEB'),
      expandIconBg: 'transparent',
      stickyScrollBarBg: cssVar('--color-border-strong', 'rgba(100,116,139,0.55)'),
    },
    // Same call as the `.tag-*` classes in pages.css: outline and text, no fill.
    // antd's default tag fills with `colorFillQuaternary`, which is black at a low
    // alpha and greys out whatever it sits on.
    Tag: {
      defaultBg: 'transparent',
      defaultColor: cssVar('--color-text-secondary', '#64748B'),
    },
    Tooltip: {
      colorBgSpotlight: cssVar('--color-surface-strong', '#FFFFFFEB'),
      colorTextLightSolid: cssVar('--color-text', '#1E293B'),
      borderRadius: pixels('--rounded-xl', 16),
      boxShadowSecondary: cssVar(
        '--elevation-3',
        '0 12px 32px rgba(17, 24, 39, 0.1)',
      ),
    },
  };
}
