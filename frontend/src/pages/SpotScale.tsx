/**
 * T2 — spot market scale by CoinGecko category.
 *
 * The five source categories overlap by construction: one coin can sit in three of
 * them at once. So the page draws grouped bars and one clearly-labelled union bar
 * rather than a pie or a stack, both of which would assert that the parts sum to the
 * whole. Every row is tagged with whether it may be quoted as a total, and the overlap
 * note travels with the chart rather than living in a caption somewhere else.
 *
 * Nothing enforces the shape at runtime — `charts/guards.ts` has the check a pie or
 * stacked chart would have to pass, and no such chart exists to call it. Swapping the
 * chart below for one is a change that needs the guard wired in first.
 */

import { useMemo } from 'react';
import { Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type { CategoryRow } from '@/api/types';
import { ChartFrame } from '@/charts/ChartFrame';
import { DualScopeChart } from '@/charts/DualScopeChart';
import { TrendLine } from '@/charts/TrendLine';
import { AmountValue } from '@/components/AmountValue';
import { ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { amountNumber, formatCount } from '@/utils/format';

/** The deduplicated union produced by `services/normalize/dedup.py`. */
const UNION_CATEGORY_ID = 'rwa_union';

const CATEGORY_LABEL: Record<string, string> = {
  [UNION_CATEGORY_ID]: '去重并集',
  'tokenized-stock': 'Tokenized Stock',
  'tokenized-etf': 'Tokenized ETF',
  'ondo-finance-ecosystem': 'Ondo 生态',
  xstocks: 'xStocks',
  bstocks: 'bStocks',
};

function label(categoryId: string): string {
  return CATEGORY_LABEL[categoryId] ?? categoryId;
}

export function SpotScale() {
  const { t } = useI18n();
  const scale = useApi((signal) => api.categories(signal), []);
  const trend = useApi(
    (signal) =>
      api.timeseries(
        {
          entity_type: 'category',
          entity_id: UNION_CATEGORY_ID,
          metric: 'market_cap',
          days: 30,
        },
        signal,
      ),
    [],
  );

  const rows = useMemo<CategoryRow[]>(() => scale.data?.rows ?? [], [scale.data]);

  const columns: ColumnsType<CategoryRow> = [
    {
      title: t('scale.category', '分类'),
      dataIndex: 'category_id',
      key: 'category_id',
      render: (_value, row) => (
        <span>
          {label(row.category_id)}{' '}
          {row.is_additive ? (
            <span className="tag-union">
              {t('scale.additive', '并集行 · 可作总计')}
            </span>
          ) : (
            <span className="tag-overlap">{t('scale.overlap', '与其他分类重叠')}</span>
          )}
        </span>
      ),
    },
    {
      title: t('scale.assets', '资产数'),
      dataIndex: 'asset_count',
      key: 'asset_count',
      align: 'right',
      className: 'numeric',
      render: (value: number | null) => formatCount(value),
    },
    {
      title: t('scale.marketCap', '市值（存量）'),
      dataIndex: 'market_cap',
      key: 'market_cap',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.market_cap} />,
    },
    {
      title: t('scale.volume', '成交额 24h（流量）'),
      dataIndex: 'vol_24h',
      key: 'vol_24h',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.vol_24h} />,
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('scale.title', '现货规模')}</h1>
          <p className="card__hint">
            {t('scale.subtitle', '分类市值与成交额，含去重并集口径')}
          </p>
        </div>
        <Tag color="default">
          {t('scale.additiveOnly', '只有并集行可以作为总计引用')}
        </Tag>
      </div>

      <ChartFrame
        title={t('scale.chart', '分类市值与成交额')}
        ariaLabel="双轴组合图：左轴为分类现货市值（USD，存量），右轴为分类现货成交额（24h，USD，流量），按分类分组"
        loading={scale.loading}
        error={scale.error}
        empty={rows.length === 0}
        footnote={scale.data?.overlap_note}
        tableColumns={[
          { key: 'category', title: t('scale.category', '分类') },
          { key: 'cap', title: t('scale.marketCap', '市值'), numeric: true },
          { key: 'vol', title: t('scale.volume', '成交额 24h'), numeric: true },
        ]}
        tableRows={rows.map((row) => ({
          category: label(row.category_id),
          cap: <AmountValue amount={row.market_cap} showScope={false} />,
          vol: <AmountValue amount={row.vol_24h} showScope={false} />,
        }))}
      >
        <DualScopeChart
          categories={rows.map((row) => label(row.category_id))}
          left={{
            name: t('scale.marketCap', '市值（存量）'),
            scope: 'spot_market_cap',
            values: rows.map((row) => amountNumber(row.market_cap)),
          }}
          right={{
            name: t('scale.volume', '成交额 24h（流量）'),
            scope: 'spot_volume',
            values: rows.map((row) => amountNumber(row.vol_24h)),
          }}
        />
      </ChartFrame>

      <ChartFrame
        title={t('scale.trend', '去重并集市值 · 近 30 天')}
        ariaLabel="折线图：去重并集分类的现货市值（USD，存量），近 30 天"
        height={280}
        loading={trend.loading}
        error={trend.error}
        empty={(trend.data?.points.length ?? 0) === 0}
        emptyHint={t('scale.trendEmpty', '仓库里还没有足够的历史快照。')}
        footnote={t(
          'scale.trendNote',
          '空心点表示该快照沿用了上一次的观测值；断线表示该次采集未成功，不代表市值归零。',
        )}
        tableColumns={[
          { key: 'ts', title: t('common.time', '时间') },
          { key: 'value', title: t('scale.marketCap', '市值'), numeric: true },
        ]}
        tableRows={(trend.data?.points ?? []).map((point) => ({
          ts: point.snapshot_ts,
          value: point.value ?? t('common.notVerified', '未验证'),
        }))}
      >
        <TrendLine
          points={trend.data?.points ?? []}
          scope="spot_market_cap"
          name={t('scale.marketCap', '市值（存量）')}
          height={280}
        />
      </ChartFrame>

      <section className="card stack-md">
        <h2 className="section-title">{t('scale.table', '分类明细')}</h2>
        {scale.loading ? (
          <TableSkeleton />
        ) : scale.error ? (
          <ErrorState error={scale.error} onRetry={scale.reload} />
        ) : (
          <Table<CategoryRow>
            rowKey="category_id"
            size="middle"
            pagination={false}
            columns={columns}
            dataSource={rows}
          />
        )}
      </section>
    </div>
  );
}
