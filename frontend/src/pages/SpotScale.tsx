/**
 * T2 — 现货规模 (UI-LAYOUT.md §2.2). P1 market-structure depth.
 *
 * The five source categories overlap by construction: one coin can sit in three of
 * them at once. So the hero draws grouped bars plus one clearly-labelled union bar
 * rather than a pie or a stack, both of which would assert the parts sum to the whole.
 * Every row is tagged with whether it may be quoted as a total, and the overlap note
 * travels with the chart rather than living in a caption somewhere else.
 *
 * R1 adds the two things a P1 depth page needs to be worked rather than admired: the
 * filter state lives in the URL (rule 23), and the detail table below shows raw and
 * quality-adjusted turnover side by side (rule 5). Neither figure is publishable
 * alone — one venue in the reference data reports ~$29.3M raw against ~$216 adjusted,
 * and a page showing only one of those numbers is wrong in a way that looks right.
 *
 * Nothing enforces the chart shape at runtime — `charts/guards.ts` has the check a pie
 * or stacked chart would have to pass, and no such chart exists to call it. Swapping
 * the hero for one is a change that needs the guard wired in first.
 */

import { useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type { CategoryRow, PairRow } from '@/api/types';
import { ChartFrame } from '@/charts/ChartFrame';
import { DualScopeChart } from '@/charts/DualScopeChart';
import { TrendLine } from '@/charts/TrendLine';
import { AmountValue } from '@/components/AmountValue';
import { BenchmarkStrip } from '@/components/BenchmarkStrip';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { amountNumber, formatCount, formatPercent } from '@/utils/format';

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

const DEFAULTS = { issuer: '', venue: '', flagged: '0', trend: '30' };

export function SpotScale() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const categoryLabel = useCallback(
    (categoryId: string) => label.from('scale.category', categoryId, CATEGORY_LABEL),
    [label],
  );
  const { state: filters, setState, reset } = useUrlState(DEFAULTS);

  const scale = useApi((signal) => api.categories(signal), []);
  const trend = useApi(
    (signal) =>
      api.timeseries(
        {
          entity_type: 'category',
          entity_id: UNION_CATEGORY_ID,
          metric: 'market_cap',
          days: Number(filters.trend) || 30,
        },
        signal,
      ),
    [filters.trend],
  );

  const pairs = useApi(
    (signal) =>
      api.pairs(
        {
          venue_id: filters.venue || undefined,
          flagged_only: filters.flagged === '1' ? true : undefined,
          limit: 300,
        },
        signal,
      ),
    [filters.venue, filters.flagged],
  );

  /*
   * The wrapper beside the share it wraps. It belongs on this page rather than on an
   * entity page because the question it answers is about the market as a whole — is
   * what is trading priced like the thing it claims to be — and because a basis is only
   * interpretable next to the scale figures it sits under here.
   */
  const benchmark = useApi((signal) => api.benchmark({ limit: 12 }, signal), []);

  const rows = useMemo<CategoryRow[]>(() => scale.data?.rows ?? [], [scale.data]);

  // Issuer is filtered here rather than at the API: `/spot/pairs` takes a venue and an
  // underlying, and adding a third server-side filter for one page's chip would put a
  // second definition of "this issuer's pairs" into the system.
  const pairRows = useMemo(() => {
    const all = pairs.data?.rows ?? [];
    return filters.issuer
      ? all.filter((row) => row.issuer_id === filters.issuer)
      : all;
  }, [pairs.data, filters.issuer]);

  const venues = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of pairs.data?.rows ?? []) seen.set(row.venue_id, row.venue);
    return [...seen.entries()].slice(0, 12);
  }, [pairs.data]);

  const categoryColumns: ColumnsType<CategoryRow> = [
    {
      title: t('scale.category', '分类'),
      dataIndex: 'category_id',
      key: 'category_id',
      render: (_value, row) => (
        <span>
          {categoryLabel(row.category_id)}{' '}
          {row.is_additive ? (
            <span className="tag-union">{t('scale.additive', '并集行 · 可作总计')}</span>
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

  const pairColumns: ColumnsType<PairRow> = [
    {
      title: t('scale.pair', '交易对'),
      key: 'pair',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>{row.symbol}</span>
          <span className="card__hint">
            {row.venue}
            {row.issuer_id ? ` · ${row.issuer_id}` : ''}
          </span>
        </span>
      ),
    },
    {
      title: t('scale.tier', 'RWA 分层'),
      key: 'tier',
      width: 120,
      render: (_value, row) => (
        <span className={`state-tag state-tag--${row.rwa_tier}`}>
          {label.tier(row.rwa_tier)}
        </span>
      ),
    },
    {
      title: t('scale.raw', '原始成交额'),
      key: 'raw',
      width: 150,
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.raw_vol_24h} showScope={false} />,
    },
    {
      title: t('scale.adjusted', '质量调整后'),
      key: 'adjusted',
      width: 150,
      align: 'right',
      render: (_value, row) => (
        <AmountValue amount={row.adjusted_vol_24h} showScope={false} />
      ),
    },
    {
      title: t('scale.quality', '质量标记'),
      key: 'quality',
      width: 170,
      render: (_value, row) => {
        if (!row.is_quality_anomaly && !row.is_quality_stale) {
          return <span className="muted">{t('scale.clean', '未标记')}</span>;
        }
        return (
          <span className="chip-row">
            {row.is_quality_anomaly ? (
              <Tooltip
                title={t(
                  'scale.anomalyHint',
                  '来源自身的成交异常标记。该对计入原始口径，不计入调整后口径。',
                )}
              >
                <span className="state-tag state-tag--overdue">
                  {t('scale.anomaly', '成交异常')}
                </span>
              </Tooltip>
            ) : null}
            {row.is_quality_stale ? (
              <Tooltip
                title={t('scale.staleHint', '报价长时间未更新，同样只计入原始口径。')}
              >
                <span className="state-tag state-tag--stale">
                  {t('scale.stale', '报价过期')}
                </span>
              </Tooltip>
            ) : null}
          </span>
        );
      },
    },
    {
      title: t('scale.spread', '价差'),
      key: 'spread',
      width: 100,
      align: 'right',
      className: 'numeric',
      render: (_value, row) =>
        row.spread_pct === null ? (
          <span className="muted">—</span>
        ) : (
          formatPercent(Number(row.spread_pct) / 100, 2)
        ),
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('scale.title', '现货规模')}</h1>
          <p className="card__hint">
            {t(
              'scale.subtitle',
              '分类市值与成交额，含去重并集口径。只有并集行可以作为总计引用。',
            )}
          </p>
        </div>
      </div>

      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('scale.venue', '交易场所')}</span>
          <div className="chip-row" role="group">
            <button
              type="button"
              className={filters.venue ? 'chip' : 'chip chip--active'}
              aria-pressed={!filters.venue}
              onClick={() => setState({ venue: '' })}
            >
              {t('common.all', '全部')}
            </button>
            {venues.map(([id, name]) => (
              <button
                key={id}
                type="button"
                className={filters.venue === id ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.venue === id}
                onClick={() => setState({ venue: filters.venue === id ? '' : id })}
              >
                {name}
              </button>
            ))}
          </div>
        </div>

        <div className="filter-group">
          <span className="filter-group__label">{t('scale.trendWindow', '趋势窗口')}</span>
          <div className="chip-row" role="group">
            {['7', '30', '90'].map((days) => (
              <button
                key={days}
                type="button"
                className={filters.trend === days ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.trend === days}
                onClick={() => setState({ trend: days })}
              >
                {days} {t('common.days', '天')}
              </button>
            ))}
          </div>
        </div>

        <div className="chip-row">
          <button
            type="button"
            className={filters.flagged === '1' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.flagged === '1'}
            onClick={() => setState({ flagged: filters.flagged === '1' ? '0' : '1' })}
          >
            {t('scale.onlyFlagged', '只看被质量标记的对')}
          </button>
          {filters.issuer ? (
            <button
              type="button"
              className="chip chip--active"
              onClick={() => setState({ issuer: '' })}
            >
              {t('scale.issuer', '发行商')}：{filters.issuer} ×
            </button>
          ) : null}
          <button type="button" className="chip" onClick={reset}>
            {t('common.reset', '重置筛选')}
          </button>
        </div>
      </section>

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
          category: categoryLabel(row.category_id),
          cap: <AmountValue amount={row.market_cap} showScope={false} />,
          vol: <AmountValue amount={row.vol_24h} showScope={false} />,
        }))}
      >
        <DualScopeChart
          categories={rows.map((row) => categoryLabel(row.category_id))}
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
        title={`${t('scale.trend', '去重并集市值 · 近')} ${filters.trend} ${t('common.days', '天')}`}
        ariaLabel="折线图：去重并集分类的现货市值（USD，存量）随时间变化"
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

      <BenchmarkStrip
        rows={benchmark.data?.rows ?? []}
        unavailableReason={benchmark.data?.unavailable_reason ?? null}
        loading={benchmark.loading}
        error={benchmark.error}
        onRetry={benchmark.reload}
      />

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('scale.table', '分类明细')}</h2>
        </div>
        {scale.loading ? (
          <TableSkeleton />
        ) : scale.error ? (
          <ErrorState error={scale.error} onRetry={scale.reload} />
        ) : (
          <Table<CategoryRow>
            rowKey="category_id"
            size="middle"
            pagination={false}
            columns={categoryColumns}
            dataSource={rows}
          />
        )}
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('scale.pairs', '交易对明细')}</h2>
          <span className="card__hint">
            {t(
              'scale.pairsHint',
              '原始与质量调整后并列。两者差距本身就是结论：差距越大，这个场所的成交越不能直接引用。',
            )}
          </span>
        </div>
        {pairs.loading ? (
          <TableSkeleton rows={8} />
        ) : pairs.error ? (
          <ErrorState error={pairs.error} onRetry={pairs.reload} />
        ) : !pairRows.length ? (
          <EmptyState
            title={t('scale.noPairs', '当前筛选下没有交易对。')}
            action={
              <button type="button" className="chip" onClick={reset}>
                {t('common.reset', '重置筛选')}
              </button>
            }
          />
        ) : (
          <Table<PairRow>
            rowKey={(row) => `${row.venue_id}:${row.asset_id}`}
            size="middle"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={pairColumns}
            dataSource={pairRows}
            onRow={(row) => ({
              onClick: () =>
                navigate(
                  row.underlying_id
                    ? `/underlying/${row.underlying_id}`
                    : `/underlying/by-asset/${row.asset_id}`,
                ),
              style: { cursor: 'pointer' },
            })}
          />
        )}
        <p className="card__hint">
          {label.scope('spot_volume')} ·{' '}
          {pairs.data ? `${pairRows.length} / ${pairs.data.meta.row_count}` : '—'}
        </p>
      </section>
    </div>
  );
}
