/**
 * A four-quadrant scatter (DATAVIZ.md §2 发行候选四象限, UI-LAYOUT.md §7).
 *
 * The quadrant sorts; it does not approve (rule 17). Everything unusual about this
 * component follows from that one sentence:
 *
 *  - **The split is fixed at the midpoint, not at the median of what is on screen.**
 *    A median split would move the lines every time someone changed a filter, so a
 *    candidate could cross into "high demand" because two others were filtered out.
 *    The axes are normalised 0–1 states — the backend rejects anything outside that
 *    range — so the midpoint is a defined position, and a point stays where it is.
 *  - **No quadrant is tinted, and no point is bigger than another.** A green top-right
 *    corner, or an area-encoded third dimension, is an approval score drawn as
 *    geometry. The four corners are named in words instead; naming a region is a
 *    description, shading it is a verdict.
 *  - **A candidate missing either coordinate is excluded, not plotted at zero.** There
 *    is no position on a plane that reads as "we have not assessed this", and (0, 0)
 *    reads as the worst one. The count of what was dropped goes back to the caller for
 *    the footnote, the same contract `ScopeTreemap` uses.
 *
 * There is no scope assertion here because there is no scope: both axes are unitless
 * normalised states, not measured quantities. That is also why neither axis may ever
 * be summed, averaged across, or combined with the other into a total.
 */

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';

import { useI18n } from '@/i18n';
import { assertSeriesLimit } from './guards';
import { baseOption, categoricalPalette, guideColor, semanticColor } from './theme';

/** Where the axes are cut. Both axes are normalised 0–1, so this is the midpoint. */
export const QUADRANT_SPLIT = 0.5;

/**
 * Shape carries the group as well as colour, so the chart survives being printed in
 * greyscale or read by someone who cannot separate two of the palette hues.
 */
const SYMBOLS = [
  'circle',
  'triangle',
  'diamond',
  'roundRect',
  'pin',
  'arrow',
  'rect',
  'circle',
  'triangle',
] as const;

export interface QuadrantPoint {
  id: string;
  name: string;
  /** Horizontal state, 0–1. Null means not assessed; the point is then excluded. */
  x: number | null;
  /** Vertical state, 0–1. Null means not assessed; the point is then excluded. */
  y: number | null;
  /** Grouping key — one colour and one symbol per distinct value. */
  group: string;
  groupLabel: string;
  /** One extra line in the tooltip, e.g. the weakest dimension. */
  detail?: string | null;
}

/** Corner names, so the reader is told what a region means rather than inferring it. */
export interface QuadrantCorners {
  topRight: string;
  topLeft: string;
  bottomRight: string;
  bottomLeft: string;
}

export interface QuadrantProps {
  points: QuadrantPoint[];
  /** Full axis phrase, not a bare noun — "可行性" alone names no measurement. */
  xName: string;
  yName: string;
  corners: QuadrantCorners;
  height?: number;
  onSelect?: (id: string) => void;
}

type PlottedPoint = QuadrantPoint & { x: number; y: number };

/**
 * Split the input into what can be placed and what cannot.
 *
 * Exported so the page can state the dropped count in the footnote: a candidate that
 * silently vanishes from a chart is indistinguishable from one that was never a
 * candidate, and the difference is exactly the work item — go assess it.
 */
export function quadrantData(points: ReadonlyArray<QuadrantPoint>): {
  shown: PlottedPoint[];
  dropped: number;
} {
  const shown = points.filter(
    (point): point is PlottedPoint =>
      point.x !== null &&
      point.y !== null &&
      Number.isFinite(point.x) &&
      Number.isFinite(point.y),
  );
  return { shown, dropped: points.length - shown.length };
}

/** Which corner a point sits in, named. The boundary belongs to the upper/right half. */
export function cornerOf(
  point: { x: number; y: number },
  corners: QuadrantCorners,
): string {
  const right = point.x >= QUADRANT_SPLIT;
  const high = point.y >= QUADRANT_SPLIT;
  if (high) return right ? corners.topRight : corners.topLeft;
  return right ? corners.bottomRight : corners.bottomLeft;
}

export function Quadrant({
  points,
  xName,
  yName,
  corners,
  height = 400,
  onSelect,
}: QuadrantProps) {
  const { t } = useI18n();

  const { shown } = useMemo(() => quadrantData(points), [points]);

  const groups = useMemo(() => {
    const seen = new Map<string, string>();
    shown.forEach((point) => {
      if (!seen.has(point.group)) seen.set(point.group, point.groupLabel);
    });
    return [...seen.entries()].map(([group, groupLabel]) => ({ group, groupLabel }));
  }, [shown]);

  const option = useMemo<EChartsOption>(() => {
    assertSeriesLimit(groups);
    const palette = categoricalPalette();
    const guide = guideColor();
    const muted = semanticColor('neutral');
    const base = baseOption();

    const series = groups.map((entry, index) => ({
      type: 'scatter' as const,
      name: entry.groupLabel,
      symbol: SYMBOLS[index % SYMBOLS.length],
      symbolSize: 14,
      itemStyle: { color: palette[index % palette.length], opacity: 0.9 },
      emphasis: { scale: 1.4 },
      label: {
        show: true,
        position: 'right' as const,
        fontSize: 12,
        color: muted,
        formatter: (params: { data?: { name?: string } }) => params.data?.name ?? '',
      },
      labelLayout: { hideOverlap: true },
      data: shown
        .filter((point) => point.group === entry.group)
        .map((point) => ({
          value: [point.x, point.y],
          id: point.id,
          name: point.name,
          groupLabel: point.groupLabel,
          corner: cornerOf(point, corners),
          detail: point.detail ?? '',
        })),
    }));

    // The corner names ride on a silent, sizeless series so they sit in data
    // coordinates and stay put when the frame resizes. They are excluded from the
    // legend below — they name regions, not a category anything belongs to.
    const cornerSeries = {
      type: 'scatter' as const,
      name: '__corners__',
      silent: true,
      symbolSize: 0,
      tooltip: { show: false },
      z: 1,
      label: {
        show: true,
        fontSize: 12,
        color: muted,
        formatter: (params: { data?: { text?: string } }) => params.data?.text ?? '',
      },
      data: [
        { value: [0.985, 0.985], text: corners.topRight, label: { position: 'left' as const } },
        { value: [0.015, 0.985], text: corners.topLeft, label: { position: 'right' as const } },
        { value: [0.985, 0.015], text: corners.bottomRight, label: { position: 'left' as const } },
        {
          value: [0.015, 0.015],
          text: corners.bottomLeft,
          label: { position: 'right' as const },
        },
      ],
      markLine: {
        silent: true,
        symbol: 'none' as const,
        label: { show: false },
        lineStyle: { color: guide, width: 1, type: 'dashed' as const },
        data: [{ xAxis: QUADRANT_SPLIT }, { yAxis: QUADRANT_SPLIT }],
      },
    };

    return {
      ...base,
      legend: {
        ...(base.legend as object),
        data: groups.map((entry) => entry.groupLabel),
      },
      grid: { left: 8, right: 24, top: 32, bottom: 40, containLabel: true },
      tooltip: {
        ...(base.tooltip as object),
        trigger: 'item' as const,
        formatter: (params: unknown) => {
          const item = params as {
            value: [number, number];
            data?: { name?: string; groupLabel?: string; corner?: string; detail?: string };
          };
          const numeric =
            "font-family:'Roboto Mono';font-feature-settings:'tnum'";
          return [
            `<strong>${item.data?.name ?? ''}</strong>`,
            item.data?.groupLabel ?? '',
            item.data?.corner ?? '',
            `${xName} <span style="${numeric}">${item.value[0].toFixed(2)}</span>`,
            `${yName} <span style="${numeric}">${item.value[1].toFixed(2)}</span>`,
            item.data?.detail ?? '',
            t('chart.quadrantTooltipNote', '两轴各自独立归一化，不可相加、不可平均。'),
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      },
      xAxis: {
        ...(base.xAxis as object),
        type: 'value' as const,
        name: xName,
        nameLocation: 'middle' as const,
        nameGap: 28,
        min: 0,
        max: 1,
        interval: 0.25,
      },
      yAxis: {
        ...(base.yAxis as object),
        type: 'value' as const,
        name: yName,
        nameLocation: 'middle' as const,
        nameGap: 36,
        min: 0,
        max: 1,
        interval: 0.25,
      },
      series: [...series, cornerSeries],
    } as EChartsOption;
  }, [groups, shown, corners, xName, yName, t]);

  return (
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      notMerge
      opts={{ renderer: 'canvas' }}
      onEvents={
        onSelect
          ? {
              click: (params: { data?: { id?: string } }) => {
                const id = params.data?.id;
                if (id) onSelect(id);
              },
            }
          : undefined
      }
    />
  );
}
