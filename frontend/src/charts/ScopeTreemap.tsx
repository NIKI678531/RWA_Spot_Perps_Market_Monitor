/**
 * A treemap of exactly one MetricScope (DATAVIZ.md §2, CLAUDE.md chart rules).
 *
 * The rule that shapes this component is "one treemap per scope". The home page draws
 * a volume treemap beside an OI treemap, and they are never combined, never totalled
 * and never given a shared legend — two rectangles of the same size in the two panels
 * mean different things, and a shared legend would invite the reader to compare them
 * as if they did not.
 *
 * So the scope is a required prop, it is printed in the panel's own subtitle, and the
 * area label spells it out again in the tooltip. There is no `series` array: a treemap
 * with two series is the mistake this file exists to make impossible.
 *
 * Unobserved entities are dropped rather than drawn at zero. A treemap encodes value
 * as area, and there is no area that reads as "we did not look" — the count of what
 * was dropped is returned to the caller to state in the footnote instead.
 */

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';

import type { MetricScope } from '@/api/types';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { MAX_SERIES } from './guards';
import { baseOption, categoricalPalette, disabledColor, usdAxisFormatter } from './theme';

export interface TreemapNode {
  id: string;
  name: string;
  /** Null means not observed. Such a node is excluded, never drawn as a zero area. */
  value: number | null;
  detail?: string | null;
}

export interface ScopeTreemapProps {
  scope: MetricScope;
  nodes: TreemapNode[];
  height?: number;
  /** Top 8 by default, expandable to Top 20 by the caller. */
  topN?: number;
  onSelect?: (id: string) => void;
}

/**
 * Top N plus an "other" bucket. `other` only ever aggregates nodes of this one scope,
 * which is the only condition under which such a bucket is additive at all.
 */
export function treemapData(
  nodes: TreemapNode[],
  topN: number,
  otherLabel: string,
): { shown: TreemapNode[]; other: TreemapNode | null; dropped: number } {
  const observed = nodes.filter(
    (node): node is TreemapNode & { value: number } =>
      node.value !== null && Number.isFinite(node.value) && node.value > 0,
  );
  const dropped = nodes.length - observed.length;
  const sorted = [...observed].sort((a, b) => b.value - a.value);
  if (sorted.length <= topN) return { shown: sorted, other: null, dropped };

  const head = sorted.slice(0, topN);
  const tail = sorted.slice(topN);
  return {
    shown: head,
    other: {
      id: '__other__',
      name: otherLabel,
      value: tail.reduce((sum, node) => sum + node.value, 0),
      detail: `${tail.length} 项`,
    },
    dropped,
  };
}

export function ScopeTreemap({
  scope,
  nodes,
  height = 320,
  topN = MAX_SERIES - 1,
  onSelect,
}: ScopeTreemapProps) {
  const { t } = useI18n();
  const labels = useLabels();
  const label = labels.scope(scope);

  const { shown, other } = useMemo(
    () => treemapData(nodes, topN, t('chart.other', '其他')),
    [nodes, topN, t],
  );

  const option = useMemo<EChartsOption>(() => {
    const palette = categoricalPalette();
    const data = [
      ...shown.map((node, index) => ({
        name: node.name,
        value: node.value ?? 0,
        id: node.id,
        detail: node.detail ?? '',
        itemStyle: { color: palette[index % palette.length] },
      })),
      ...(other
        ? [
            {
              name: other.name,
              value: other.value ?? 0,
              id: other.id,
              detail: other.detail ?? '',
              itemStyle: { color: disabledColor() },
            },
          ]
        : []),
    ];

    return {
      ...baseOption(),
      legend: { show: false },
      tooltip: {
        ...(baseOption().tooltip as object),
        formatter: (params: unknown) => {
          const item = params as {
            name: string;
            value: number;
            data?: { detail?: string };
          };
          return [
            `<strong>${item.name}</strong>`,
            label,
            `<span style="font-family:'Roboto Mono';font-feature-settings:'tnum'">${usdAxisFormatter(
              item.value,
            )}</span>`,
            item.data?.detail ?? '',
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      },
      series: [
        {
          type: 'treemap' as const,
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          width: '100%',
          height: '100%',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          itemStyle: { borderColor: 'transparent', borderWidth: 2, gapWidth: 2 },
          label: {
            show: true,
            fontSize: 12,
            formatter: (params: { name: string; value: number }) =>
              `${params.name}\n${usdAxisFormatter(params.value)}`,
          },
          data,
        },
      ],
    } as EChartsOption;
  }, [shown, other, label]);

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
                if (id && id !== '__other__') onSelect(id);
              },
            }
          : undefined
      }
    />
  );
}
