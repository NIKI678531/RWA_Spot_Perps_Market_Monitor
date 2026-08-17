/**
 * Two metric scopes, two axes, two shapes (DATAVIZ.md §2.2).
 *
 * A stock and a flow can appear together — perp volume against open interest is the
 * comparison the perps page exists for — but never on one axis. The two series are
 * also forced to different chart types: two same-shaped curves invite the reader to
 * treat their crossing point as an event, and between a flow and a stock it is not
 * one.
 */

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';

import type { MetricScope } from '@/api/types';
import { useI18n } from '@/i18n';
import { assertSameAxis, axisTitle } from './guards';
import { baseOption, categoricalPalette, usdAxisFormatter } from './theme';

export interface DualScopeChartProps {
  categories: string[];
  /** Drawn as bars on the left axis. */
  left: { name: string; scope: MetricScope; values: Array<number | null> };
  /** Drawn as a line on the right axis. */
  right: { name: string; scope: MetricScope; values: Array<number | null> };
  height?: number;
}

export function DualScopeChart({
  categories,
  left,
  right,
  height = 360,
}: DualScopeChartProps) {
  const { t, locale } = useI18n();

  const option = useMemo<EChartsOption>(() => {
    assertSameAxis([left.scope, right.scope]);
    const palette = categoricalPalette();
    const leftTitle = axisTitle(left.scope, locale);
    const rightTitle = axisTitle(right.scope, locale);

    return {
      ...baseOption(),
      tooltip: {
        ...(baseOption().tooltip as object),
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const head = (rows[0] as { name?: string }).name ?? '';
          const scopes = [leftTitle, rightTitle];
          const lines = rows.map((row, index) => {
            const item = row as { seriesName?: string; value?: number | null };
            const shown =
              item.value === null || item.value === undefined
                ? t('common.notVerified', '未验证')
                : usdAxisFormatter(Number(item.value));
            return `${item.seriesName}（${scopes[index] ?? ''}）: ${shown}`;
          });
          return [`<strong>${head}</strong>`, ...lines].join('<br/>');
        },
      },
      grid: { left: 8, right: 8, top: 32, bottom: 48, containLabel: true },
      xAxis: {
        ...(baseOption().xAxis as object),
        type: 'category',
        data: categories,
        axisLabel: {
          interval: 0,
          rotate: categories.length > 8 ? 30 : 0,
          fontSize: 12,
        },
      },
      yAxis: [
        {
          ...(baseOption().yAxis as object),
          type: 'value',
          min: 0,
          name: leftTitle,
          nameTextStyle: { align: 'left' },
          axisLabel: { formatter: usdAxisFormatter, fontSize: 12 },
        },
        {
          ...(baseOption().yAxis as object),
          type: 'value',
          min: 0,
          name: rightTitle,
          nameTextStyle: { align: 'right' },
          // Only one grid applies, or the two dashed sets moiré against each other.
          splitLine: { show: false },
          axisLabel: { formatter: usdAxisFormatter, fontSize: 12 },
        },
      ],
      series: [
        {
          name: left.name,
          type: 'bar',
          yAxisIndex: 0,
          barMaxWidth: 22,
          itemStyle: { borderRadius: [8, 8, 0, 0], color: palette[0] },
          data: left.values,
        },
        {
          name: right.name,
          type: 'line',
          yAxisIndex: 1,
          smooth: false,
          symbol: 'circle',
          symbolSize: 6,
          // Dashed as well as differently coloured: colour is never the only cue.
          lineStyle: { width: 2, type: 'dashed', color: palette[3] },
          itemStyle: { color: palette[3] },
          data: right.values,
        },
      ],
    } as EChartsOption;
  }, [categories, left, right, t, locale]);

  return (
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      notMerge
      opts={{ renderer: 'canvas' }}
    />
  );
}
