/**
 * T2 — 交易场所 (UI-LAYOUT.md §2.2). P1 market-structure depth.
 *
 * Raw and quality-adjusted turnover are shown side by side everywhere on this page,
 * never behind a toggle. One venue in the reference data reports ~$29.3mn raw against
 * ~$216 adjusted because 17 of its 19 pairs are flagged; a reader shown either figure
 * alone draws the wrong conclusion, and a reader shown both immediately asks the right
 * question. The ranking itself is on the adjusted figure.
 *
 * R1 moves the venue selection and the type filter into the URL (rule 23) — the
 * canonical form is `/venues?venue=`, which is what the backend's `href_for` emits, so
 * a link out of an alert or a report lands on the venue it named rather than on the
 * unfiltered list. Every pair row drills into Underlying 360, or into the honest
 * "not mapped yet" page when the wrapper has no underlying.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { AlertTriangle } from 'lucide-react';

import { api } from '@/api/client';
import type { ConcentrationSummary, PairRow, VenueRow, VenueType } from '@/api/types';
import { BarRanking } from '@/charts/BarRanking';
import { ChartFrame } from '@/charts/ChartFrame';
import { AmountValue } from '@/components/AmountValue';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { amountNumber, formatCount, formatPercent } from '@/utils/format';

const DEFAULTS = { type: 'all', venue: '', flagged: '0' };

const TYPE_FILTERS: ReadonlyArray<{ id: VenueType | 'all'; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'cex', label: 'CEX' },
  { id: 'dex', label: 'DEX' },
  { id: 'aggregator', label: '聚合器' },
];

const SEGMENT_LABEL: Record<string, string> = {
  all: '全市场',
  cex: 'CEX',
  dex: 'DEX',
  aggregator: '聚合器',
  perp_dex: '永续 DEX',
};

function ConcentrationCards({ rows }: { rows: ConcentrationSummary[] }) {
  const { t } = useI18n();
  const label = useLabels();
  if (rows.length === 0) return null;

  return (
    <section className="card stack-md">
      <h2 className="section-title">{t('venues.concentration', '集中度')}</h2>
      <p className="card__hint">
        {t(
          'venues.concentrationNote',
          '排名说不清市场结构：领先者占 30% 与占 85% 的两个市场，看起来都只是一份有序列表。HHI 高于 0.25 视为高度集中。',
        )}
      </p>
      <div className="source-grid">
        {rows.map((row) => (
          <div className="card stack-sm" key={row.segment}>
            <div className="row-between">
              <span className="card__title">
                {label.from('venues.segment', row.segment, SEGMENT_LABEL)}
              </span>
              {row.is_concentrated ? (
                <span className="tag-divergent">
                  {t('venues.concentrated', '高度集中')}
                </span>
              ) : null}
            </div>
            <div className="kpi-card__value numeric">{row.hhi.toFixed(3)}</div>
            <div className="card__hint">
              HHI · {formatCount(row.venue_count)} {t('common.venues', '个场所')}
            </div>
            <div className="alert-item__evidence">
              <div className="evidence-cell">
                <span>Top 1</span>
                <span className="numeric">{formatPercent(row.top1_share)}</span>
              </div>
              <div className="evidence-cell">
                <span>Top 3</span>
                <span className="numeric">{formatPercent(row.top3_share)}</span>
              </div>
              <div className="evidence-cell">
                <span>Top 5</span>
                <span className="numeric">{formatPercent(row.top5_share)}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function Venues() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { state: filters, setState, reset } = useUrlState(DEFAULTS);

  const ranking = useApi(
    (signal) =>
      api.venues(
        { venue_type: filters.type === 'all' ? undefined : filters.type },
        signal,
      ),
    [filters.type],
  );

  const pairs = useApi(
    (signal) =>
      api.pairs(
        {
          venue_id: filters.venue || undefined,
          flagged_only: filters.flagged === '1' ? true : undefined,
          limit: 200,
        },
        signal,
      ),
    [filters.venue, filters.flagged],
  );

  const rows = useMemo<VenueRow[]>(() => ranking.data?.rows ?? [], [ranking.data]);
  const pairRows = useMemo<PairRow[]>(() => pairs.data?.rows ?? [], [pairs.data]);
  const top = rows.slice(0, 10);

  const selectedName =
    rows.find((row) => row.venue_id === filters.venue)?.name ?? filters.venue;

  const columns: ColumnsType<VenueRow> = [
    {
      title: t('common.rank', '排名'),
      dataIndex: 'rank',
      key: 'rank',
      width: 72,
      align: 'right',
      className: 'numeric',
    },
    {
      title: t('common.venue', '交易场所'),
      dataIndex: 'name',
      key: 'name',
      render: (value: string, row) => (
        <span>
          {value}{' '}
          {row.materially_divergent ? (
            <Tooltip title={t('venues.divergent', '原始与质量调整口径相差十倍以上')}>
              <span className="tag-divergent">
                <AlertTriangle size={12} aria-hidden />
                {t('venues.divergentShort', '口径背离')}
              </span>
            </Tooltip>
          ) : null}
        </span>
      ),
    },
    {
      title: t('common.type', '类型'),
      dataIndex: 'venue_type',
      key: 'venue_type',
      width: 96,
      render: (value: string | null) => value?.toUpperCase() ?? '—',
    },
    {
      title: t('common.chain', '链'),
      dataIndex: 'chain',
      key: 'chain',
      width: 120,
      render: (value: string | null) => value ?? '—',
    },
    {
      title: t('common.raw', '原始成交额'),
      key: 'raw',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.raw_vol_24h} />,
    },
    {
      title: t('common.adjusted', '质量调整成交额'),
      key: 'adjusted',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.adjusted_vol_24h} />,
    },
    {
      title: t('common.share', '份额'),
      dataIndex: 'share_of_adjusted',
      key: 'share',
      width: 96,
      align: 'right',
      className: 'numeric',
      render: (value: number | null) => formatPercent(value),
    },
    {
      title: t('common.pairs', '交易对'),
      key: 'pairs',
      width: 120,
      align: 'right',
      className: 'numeric',
      render: (_value, row) =>
        row.flagged_pairs > 0
          ? `${formatCount(row.pair_count)} (${formatCount(row.flagged_pairs)} ${t(
              'common.flaggedSuffix',
              '标记',
            )})`
          : formatCount(row.pair_count),
    },
  ];

  const pairColumns: ColumnsType<PairRow> = [
    {
      title: t('common.pair', '交易对'),
      key: 'symbol',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>{row.symbol}</span>
          {row.underlying_id ? null : (
            <span className="card__hint">
              {t('venues.unmapped', '尚未映射到底层')}
            </span>
          )}
        </span>
      ),
    },
    { title: t('common.venue', '交易场所'), dataIndex: 'venue', key: 'venue' },
    {
      title: t('common.raw', '原始成交额'),
      key: 'raw',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.raw_vol_24h} showScope={false} />,
    },
    {
      title: t('common.adjusted', '质量调整成交额'),
      key: 'adjusted',
      align: 'right',
      render: (_value, row) => (
        <AmountValue amount={row.adjusted_vol_24h} showScope={false} />
      ),
    },
    {
      title: t('common.flagged', '质量标记'),
      key: 'flags',
      width: 140,
      render: (_value, row) =>
        row.is_quality_anomaly || row.is_quality_stale ? (
          <span className="tag-divergent">
            {row.is_quality_anomaly ? t('quality.anomaly', '异常') : ''}
            {row.is_quality_anomaly && row.is_quality_stale ? ' · ' : ''}
            {row.is_quality_stale ? t('quality.stale', '停更') : ''}
          </span>
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('venues.title', '交易场所')}</h1>
          <p className="card__hint">
            {t('venues.subtitle', '代币化 RWA 真正成交的地方，按质量调整口径排名。')}
          </p>
        </div>
      </div>

      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('common.type', '类型')}</span>
          <div className="chip-row" role="group" aria-label={t('common.type', '类型')}>
            {TYPE_FILTERS.map((filter) => (
              <button
                key={filter.id}
                type="button"
                className={filter.id === filters.type ? 'chip chip--active' : 'chip'}
                onClick={() => setState({ type: filter.id })}
                aria-pressed={filter.id === filters.type}
              >
                {t(`venues.type.${filter.id}`, filter.label)}
              </button>
            ))}
          </div>
        </div>

        <div className="chip-row">
          {filters.venue ? (
            <button
              type="button"
              className="chip chip--active"
              onClick={() => setState({ venue: '' })}
            >
              {t('common.venue', '交易场所')}：{selectedName} ×
            </button>
          ) : null}
          <button
            type="button"
            className={filters.flagged === '1' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.flagged === '1'}
            onClick={() => setState({ flagged: filters.flagged === '1' ? '0' : '1' })}
          >
            {t('venues.onlyFlagged', '只看被质量标记的对')}
          </button>
          <button type="button" className="chip" onClick={reset}>
            {t('common.reset', '重置筛选')}
          </button>
        </div>
      </section>

      <ChartFrame
        title={t('venues.chart', '质量调整成交额前十')}
        ariaLabel="横向条形图：交易场所按现货成交额（24h, USD）排名前十，口径为质量调整后"
        loading={ranking.loading}
        error={ranking.error}
        empty={top.length === 0}
        footnote={ranking.data?.meta.note}
        tableColumns={[
          { key: 'venue', title: t('common.venue', '交易场所') },
          { key: 'raw', title: t('common.raw', '原始'), numeric: true },
          { key: 'adjusted', title: t('common.adjusted', '质量调整'), numeric: true },
        ]}
        tableRows={top.map((row) => ({
          venue: row.name,
          raw: <AmountValue amount={row.raw_vol_24h} showScope={false} />,
          adjusted: <AmountValue amount={row.adjusted_vol_24h} showScope={false} />,
        }))}
      >
        {/* Two series, one scope — legal on one axis, and the gap between the pair of
            bars is the point of the chart. */}
        <BarRanking
          categories={top.map((row) => row.name)}
          scope="spot_volume"
          series={[
            {
              name: t('common.adjusted', '质量调整'),
              scope: 'spot_volume',
              values: top.map((row) => amountNumber(row.adjusted_vol_24h)),
            },
            {
              name: t('common.raw', '原始'),
              scope: 'spot_volume',
              values: top.map((row) => amountNumber(row.raw_vol_24h)),
            },
          ]}
        />
      </ChartFrame>

      <ConcentrationCards rows={ranking.data?.concentration ?? []} />

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('venues.table', '场所明细')}</h2>
          <span className="card__hint">
            {t('venues.tableHint', '点击一行，下方交易对表切换到该场所。')}
          </span>
        </div>

        {ranking.loading ? (
          <TableSkeleton />
        ) : ranking.error ? (
          <ErrorState error={ranking.error} onRetry={ranking.reload} />
        ) : rows.length === 0 ? (
          <EmptyState title={t('common.empty', '暂无观测数据')} />
        ) : (
          <Table<VenueRow>
            rowKey="venue_id"
            size="middle"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={columns}
            dataSource={rows}
            onRow={(row) => ({
              onClick: () =>
                setState({ venue: filters.venue === row.venue_id ? '' : row.venue_id }),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.venue_id === filters.venue ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">
            {filters.venue
              ? `${t('venues.pairs', '交易对明细')} · ${selectedName}`
              : t('venues.pairsAll', '交易对明细 · 全市场')}
          </h2>
          <span className="card__hint">
            {t('venues.pairsHint', '点击一行进入底层 360。')}
          </span>
        </div>
        {pairs.loading ? (
          <TableSkeleton />
        ) : pairs.error ? (
          <ErrorState error={pairs.error} onRetry={pairs.reload} />
        ) : pairRows.length === 0 ? (
          <EmptyState title={t('common.empty', '暂无观测数据')} />
        ) : (
          <Table<PairRow>
            rowKey={(row) => `${row.asset_id}@${row.venue_id}`}
            size="small"
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
      </section>
    </div>
  );
}
