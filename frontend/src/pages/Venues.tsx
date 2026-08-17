/**
 * T2 — where tokenized RWAs actually trade.
 *
 * Raw and quality-adjusted turnover are shown side by side everywhere on this page,
 * never behind a toggle. One venue in the reference data reports ~$29.3mn raw against
 * ~$216 adjusted because 17 of its 19 pairs are flagged; a reader shown either figure
 * alone draws the wrong conclusion, and a reader shown both immediately asks the right
 * question. The ranking itself is on the adjusted figure.
 */

import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
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
import { useI18n } from '@/i18n';
import { amountNumber, formatCount, formatPercent } from '@/utils/format';

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
                {t(
                  `venues.segment.${row.segment}`,
                  SEGMENT_LABEL[row.segment] ?? row.segment,
                )}
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
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';
  const [venueType, setVenueType] = useState<VenueType | 'all'>('all');
  const [selected, setSelected] = useState<string | null>(null);

  const ranking = useApi(
    (signal) =>
      api.venues({ venue_type: venueType === 'all' ? undefined : venueType }, signal),
    [venueType],
  );
  const pairs = useApi(
    (signal) => api.pairs({ venue_id: selected ?? undefined, limit: 100 }, signal),
    [selected],
  );

  const needle = query.trim().toLowerCase();

  const rows = useMemo<VenueRow[]>(() => {
    const all = ranking.data?.rows ?? [];
    if (!needle) return all;
    return all.filter((row) => row.name.toLowerCase().includes(needle));
  }, [ranking.data, needle]);

  const pairRows = useMemo<PairRow[]>(() => {
    const all = pairs.data?.rows ?? [];
    if (!needle) return all;
    return all.filter(
      (row) =>
        row.symbol.toLowerCase().includes(needle) ||
        row.venue.toLowerCase().includes(needle),
    );
  }, [pairs.data, needle]);

  const top = rows.slice(0, 10);

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
    { title: t('common.pair', '交易对'), dataIndex: 'symbol', key: 'symbol' },
    { title: t('common.venue', '交易场所'), dataIndex: 'venue', key: 'venue' },
    {
      title: t('common.raw', '原始成交额'),
      key: 'raw',
      align: 'right',
      render: (_value, row) => (
        <AmountValue amount={row.raw_vol_24h} showScope={false} />
      ),
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
            {t('venues.subtitle', '代币化 RWA 真正成交的地方，按质量调整口径排名')}
          </p>
        </div>
        <div className="chip-row" role="group" aria-label={t('common.type', '类型')}>
          {TYPE_FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              className={filter.id === venueType ? 'chip chip--active' : 'chip'}
              onClick={() => setVenueType(filter.id)}
              aria-pressed={filter.id === venueType}
            >
              {t(`venues.type.${filter.id}`, filter.label)}
            </button>
          ))}
        </div>
      </div>

      {needle ? (
        <div className="chip-row">
          <span className="chip chip--active">
            {t('common.filter', '筛选')}: {query}
          </span>
          <button type="button" className="chip" onClick={() => setParams({})}>
            {t('common.clear', '清除')}
          </button>
        </div>
      ) : null}

      <ChartFrame
        title={t('venues.chart', '质量调整成交额前十')}
        ariaLabel="横向条形图：交易场所按现货成交额（24h, USD）排名前十，口径为质量调整后"
        loading={ranking.loading}
        error={ranking.error}
        empty={top.length === 0}
        footnote={ranking.data?.meta.note}
        tableColumns={[
          { key: 'venue', title: t('common.venue', '交易场所') },
          { key: 'adjusted', title: t('common.adjusted', '质量调整'), numeric: true },
        ]}
        tableRows={top.map((row) => ({
          venue: row.name,
          adjusted: <AmountValue amount={row.adjusted_vol_24h} showScope={false} />,
        }))}
      >
        <BarRanking
          categories={top.map((row) => row.name)}
          scope="spot_volume"
          series={[
            {
              name: t('common.adjusted', '质量调整'),
              scope: 'spot_volume',
              values: top.map((row) => amountNumber(row.adjusted_vol_24h)),
            },
          ]}
        />
      </ChartFrame>

      <ConcentrationCards rows={ranking.data?.concentration ?? []} />

      <section className="card stack-md">
        <div className="row-between">
          <h2 className="section-title">{t('venues.table', '场所明细')}</h2>
          {selected ? (
            <button type="button" className="chip" onClick={() => setSelected(null)}>
              {t('venues.clearVenue', '取消场所筛选')}
            </button>
          ) : null}
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
              onClick: () => setSelected(row.venue_id),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.venue_id === selected ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      <section className="card stack-md">
        <h2 className="section-title">
          {selected
            ? `${t('venues.pairs', '交易对明细')} · ${selected}`
            : t('venues.pairsAll', '交易对明细 · 全市场前 100')}
        </h2>
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
          />
        )}
      </section>
    </div>
  );
}
