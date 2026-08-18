/**
 * Horizontal bar ranking — the answer to "who is biggest" (DATAVIZ.md §2).
 *
 * A pie chart would answer the same question by asserting the parts sum to a whole,
 * which is false for most of this data set, so the ranking is always bars.
 *
 * Three rules are enforced here rather than left to callers:
 *  - every series on the axis must share one MetricScope (assertSingleScope);
 *  - the series count stays inside the categorical palette (assertSeriesLimit), since
 *    a tenth colour is one no reader can tell from the ninth;
 *  - a bar with no observed value is drawn hatched at the series mean, never at
 *    zero, because a zero-height bar claims the venue traded nothing.
 */

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';

import type { MetricScope } from '@/api/types';
import { useI18n } from '@/i18n';
import {
  assertSeriesLimit,
  assertSingleScope,
  axisTitle,
  type SeriesSpec,
} from './guards';
import {
  baseOption,
  disabledColor,
  notVerifiedPattern,
  usdAxisFormatter,
} from './theme';

export interface BarRankingProps {
  categories: string[];
  series: SeriesSpec[];
  scope: MetricScope;
  /** Bars are drawn top-down in the order given; callers sort descending. */
  height?: number;
}

export function BarRanking({
  categories,
  series,
  scope,
  height = 360,
}: BarRankingProps) {
  const { t, locale } = useI18n();

  const option = useMemo<EChartsOption>(() => {
    assertSingleScope(series);
    assertSeriesLimit(series);

    const notVerified = t('common.notVerified', '未验证');
    const title = axisTitle(scope, locale);

    const observed = series
      .flatMap((s) => s.values)
      .filter((v): v is number => v !== null && Number.isFinite(v));
    const mean = observed.length
      ? observed.reduce((sum, v) => sum + v, 0) / observed.length
      : 0;

    return {
      ...baseOption(),
      tooltip: {
        ...(baseOption().tooltip as object),
        trigger: 'axis',
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const head = (rows[0] as { name?: string }).name ?? '';
          const lines = rows.map((row) => {
            const item = row as {
              seriesName?: string;
              dataIndex: number;
              seriesIndex: number;
            };
            const raw = series[item.seriesIndex]?.values[item.dataIndex] ?? null;
            const shown = raw === null ? notVerified : usdAxisFormatter(raw);
            return `${item.seriesName}: <span style="font-family:'Roboto Mono';font-feature-settings:'tnum'">${shown}</span>`;
          });
          return [`<strong>${head}</strong>`, title, ...lines].join('<br/>');
        },
      },
      grid: { left: 8, right: 56, top: 24, bottom: 32, containLabel: true },
      // Bars start at zero. A truncated baseline exaggerates differences, which in a
      // financial chart is indistinguishable from misleading.
      xAxis: {
        ...(baseOption().xAxis as object),
        type: 'value',
        min: 0,
        name: title,
        nameLocation: 'middle',
        nameGap: 28,
        axisLabel: {
          color: disabledColor(),
          fontSize: 12,
          formatter: usdAxisFormatter,
        },
      },
      yAxis: {
        ...(baseOption().yAxis as object),
        type: 'category',
        data: [...categories].reverse(),
        splitLine: { show: false },
      },
      series: series.map((spec) => ({
        name: spec.name,
        type: 'bar' as const,
        barMaxWidth: 18,
        itemStyle: { borderRadius: [0, 8, 8, 0] },
        // Direct value labels: colour is never the only encoding (DATAVIZ.md §5).
        label: {
          show: true,
          position: 'right' as const,
          fontFamily: 'Roboto Mono',
          fontSize: 12,
          formatter: (params: { dataIndex: number }) => {
            const value = [...spec.values].reverse()[params.dataIndex];
            return value === null || value === undefined
              ? notVerified
              : usdAxisFormatter(value);
          },
        },
        data: [...spec.values].reverse().map((value) =>
          value === null
            ? {
                value: mean,
                itemStyle: { color: notVerifiedPattern(), opacity: 0.7 },
              }
            : { value },
        ),
      })),
    } as EChartsOption;
  }, [categories, series, scope, t, locale]);

  return (
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      notMerge
      opts={{ renderer: 'canvas' }}
    />
  );
}
