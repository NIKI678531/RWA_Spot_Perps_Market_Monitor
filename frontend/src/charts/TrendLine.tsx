/**
 * One metric over time (DATAVIZ.md §2.3).
 *
 * Two things distinguish this from a default line chart, and both are about not
 * inventing observations:
 *  - a null point breaks the line rather than joining across it, because connecting
 *    over a failed collection draws a trend through data that was never gathered;
 *  - a point the pipeline carried forward from an earlier snapshot is drawn hollow,
 *    so a flat stretch produced by a stalled source cannot be read as a calm market.
 */

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';

import type { MetricScope, TimeseriesPoint } from '@/api/types';
import { SESSION_LABEL } from '@/api/types';
import { useI18n } from '@/i18n';
import { axisTitle } from './guards';
import {
  baseOption,
  categoricalPalette,
  disabledColor,
  usdAxisFormatter,
} from './theme';
import { formatTimestamp } from '@/utils/format';

export interface TrendLineProps {
  points: TimeseriesPoint[];
  scope: MetricScope;
  name: string;
  height?: number;
}

export function TrendLine({ points, scope, name, height = 320 }: TrendLineProps) {
  const { t, locale } = useI18n();

  const option = useMemo<EChartsOption>(() => {
    const palette = categoricalPalette();
    const carried = disabledColor();

    return {
      ...baseOption(),
      tooltip: {
        ...(baseOption().tooltip as object),
        trigger: 'axis',
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const first = rows[0] as { dataIndex: number } | undefined;
          const point = first ? points[first.dataIndex] : undefined;
          if (!point) return '';
          const value =
            point.value === null
              ? t('common.notVerified', '未验证')
              : usdAxisFormatter(Number(point.value));
          return [
            `<strong>${formatTimestamp(point.snapshot_ts)}</strong>`,
            axisTitle(scope, locale),
            `${name}: ${value}`,
            t(`session.${point.market_session}`, SESSION_LABEL[point.market_session]),
            point.is_carried_forward
              ? t('chart.carriedForward', '（沿用上一快照，本次未采集到新值）')
              : '',
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      },
      grid: { left: 8, right: 16, top: 24, bottom: 40, containLabel: true },
      xAxis: {
        ...(baseOption().xAxis as object),
        type: 'category',
        boundaryGap: false,
        data: points.map((point) => formatTimestamp(point.snapshot_ts)),
        axisLabel: { fontSize: 12, hideOverlap: true },
      },
      yAxis: {
        ...(baseOption().yAxis as object),
        type: 'value',
        name: axisTitle(scope, locale),
        axisLabel: { formatter: usdAxisFormatter, fontSize: 12 },
        // A money axis may be truncated as long as it is labelled; a bar chart's
        // may not, because the bar's length is the value.
        scale: true,
      },
      series: [
        {
          name,
          type: 'line',
          smooth: false,
          connectNulls: false,
          symbolSize: 6,
          lineStyle: { width: 2, color: palette[0] },
          itemStyle: {
            color: (params: { dataIndex: number }) =>
              points[params.dataIndex]?.is_carried_forward
                ? carried
                : (palette[0] ?? ''),
          },
          data: points.map((point) =>
            point.value === null ? null : Number(point.value),
          ),
        },
      ],
    } as EChartsOption;
  }, [points, scope, name, t, locale]);

  return (
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      notMerge
      opts={{ renderer: 'canvas' }}
    />
  );
}
