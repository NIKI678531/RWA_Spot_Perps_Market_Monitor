/**
 * T2 — cross-venue perpetual exposure to real-world underlyings.
 *
 * Volume and open interest appear together because the comparison is the point: a
 * contract with heavy turnover and thin OI is being traded, one with thin turnover and
 * heavy OI is being held. They are a flow and a stock, so they never share an axis —
 * bars on the left, a dashed line on the right.
 *
 * The exchange's own classification is shown verbatim next to ours. Binance files some
 * ETFs and leveraged ETPs under `EQUITY`; overwriting that label would make our
 * numbers impossible to reconcile against theirs.
 */

import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type { PerpContractRow, PerpDexRow, PerpVenueRow } from '@/api/types';
import { ChartFrame } from '@/charts/ChartFrame';
import { DualScopeChart } from '@/charts/DualScopeChart';
import { AmountValue } from '@/components/AmountValue';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { amountNumber, formatCount } from '@/utils/format';

function fundingText(rate: string | null): string {
  if (rate === null) return '—';
  const value = Number(rate);
  if (!Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(4)}%`;
}

/**
 * One venue's totals, with its equity subset folded in rather than listed beside it.
 *
 * `/perps/venues` returns a `stock` row *inside* the venue's own total, the way the
 * overlapping CoinGecko categories work: listing both as siblings would let a reader
 * add a venue to itself. The subset therefore becomes a column on its parent and
 * never a row of its own.
 */
interface VenueGroup {
  key: string;
  total: PerpVenueRow;
  stock: PerpVenueRow | null;
}

const SEGMENT_SUBSET = 'stock';

function groupVenues(rows: PerpVenueRow[]): VenueGroup[] {
  const key = (row: PerpVenueRow) => `${row.exchange}::${row.perp_dex}`;
  const groups: VenueGroup[] = [];
  const byKey = new Map<string, VenueGroup>();

  for (const row of rows) {
    if (row.segment === SEGMENT_SUBSET) continue;
    const group: VenueGroup = { key: key(row), total: row, stock: null };
    groups.push(group);
    byKey.set(group.key, group);
  }
  // Second pass: a subset row can only be attached once its parent exists, and the
  // API's ordering is by volume, not by segment.
  for (const row of rows) {
    if (row.segment !== SEGMENT_SUBSET) continue;
    const parent = byKey.get(key(row));
    if (parent) parent.stock = row;
  }
  return groups;
}

export function Perps() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';
  const needle = query.trim().toLowerCase();

  const contracts = useApi((signal) => api.perpContracts({ limit: 200 }, signal), []);
  const dexs = useApi((signal) => api.perpDexs(signal), []);
  const perpVenues = useApi((signal) => api.perpVenues(signal), []);

  const rows = useMemo<PerpContractRow[]>(() => {
    const all = contracts.data?.rows ?? [];
    if (!needle) return all;
    return all.filter(
      (row) =>
        row.symbol.toLowerCase().includes(needle) ||
        row.exchange.toLowerCase().includes(needle),
    );
  }, [contracts.data, needle]);

  const top = rows.slice(0, 10);
  const dexRows: PerpDexRow[] = dexs.data?.rows ?? [];
  const venueGroups = useMemo(
    () => groupVenues(perpVenues.data?.rows ?? []),
    [perpVenues.data],
  );

  const columns: ColumnsType<PerpContractRow> = [
    {
      title: t('common.rank', '排名'),
      dataIndex: 'rank',
      key: 'rank',
      width: 72,
      align: 'right',
      className: 'numeric',
    },
    { title: t('perps.contract', '合约'), dataIndex: 'symbol', key: 'symbol' },
    { title: t('perps.exchange', '交易所'), dataIndex: 'exchange', key: 'exchange' },
    {
      title: (
        <Tooltip
          title={t(
            'perps.sourceLabelHint',
            '交易所自己的分类，原样保留。Binance 会把部分 ETF、杠杆 ETP 归为 EQUITY；改写它会让我们的数字无法与交易所对账。',
          )}
        >
          <span>{t('perps.sourceLabel', '交易所分类')}</span>
        </Tooltip>
      ),
      dataIndex: 'source_underlying_type',
      key: 'source_underlying_type',
      width: 140,
      render: (value: string | null) => value ?? '—',
    },
    {
      title: t('perps.analysisGroup', '分析口径'),
      dataIndex: 'analysis_group',
      key: 'analysis_group',
      width: 140,
      render: (value: string | null) => value ?? '—',
    },
    {
      title: t('perps.volume', '成交额 24h（流量）'),
      key: 'vol',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.vol_24h} />,
    },
    {
      title: t('perps.oi', '未平仓名义（存量）'),
      key: 'oi',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.open_interest_usd} />,
    },
    {
      title: t('perps.funding', '资金费率'),
      dataIndex: 'funding_rate',
      key: 'funding_rate',
      width: 120,
      align: 'right',
      className: 'numeric',
      render: (value: string | null) => fundingText(value),
    },
  ];

  const venueColumns: ColumnsType<VenueGroup> = [
    {
      title: t('perps.exchange', '交易所'),
      key: 'exchange',
      render: (_value, row) => (
        <span>
          {row.total.exchange}
          {row.total.is_hip3 ? (
            <>
              {' '}
              <span className="tag-union">{row.total.perp_dex}</span>
            </>
          ) : null}
        </span>
      ),
    },
    {
      title: t('perps.symbols', '在册合约'),
      key: 'symbols',
      width: 130,
      align: 'right',
      className: 'numeric',
      // The count of RWA contracts the venue lists, not of everything it lists: the
      // collectors read whole exchanges and drop the crypto-native tail before this.
      render: (_value, row) => formatCount(row.total.symbol_count),
    },
    {
      title: t('perps.volume', '成交额 24h（流量）'),
      key: 'vol',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.total.vol_24h} />,
    },
    {
      title: t('perps.oi', '未平仓名义（存量）'),
      key: 'oi',
      align: 'right',
      render: (_value, row) => {
        const covered = row.total.oi_symbol_count;
        const listed = row.total.symbol_count;
        const isFloor = covered !== null && listed !== null && covered < listed;
        const cell = <AmountValue amount={row.total.open_interest_usd} />;
        if (!isFloor) return cell;
        // A partial open-interest sum is a floor on the venue's book, not the book.
        // Some venues charge one request per symbol for it, so the collector stops
        // before the tail; saying so beats publishing a total that reads complete.
        return (
          <Tooltip
            title={t(
              'perps.oiFloorHint',
              '未平仓只覆盖 {covered} / {listed} 个合约，因此这是下限而非全量。部分交易所的未平仓需要逐合约请求，采集在尾部停止。',
            )
              .replace('{covered}', String(covered))
              .replace('{listed}', String(listed))}
          >
            <span>
              {cell}
              <span className="muted"> ≥</span>
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: (
        <Tooltip
          title={t(
            'perps.stockSubsetHint',
            '这一列是左侧成交额的一部分，不是另一笔。股票类合约包含在该场所的总额里，两者不可相加。',
          )}
        >
          <span>{t('perps.stockSubset', '其中股票类')}</span>
        </Tooltip>
      ),
      key: 'stock',
      align: 'right',
      render: (_value, row) =>
        row.stock ? (
          <AmountValue amount={row.stock.vol_24h} showScope={false} />
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];

  const dexColumns: ColumnsType<PerpDexRow> = [
    {
      title: t('perps.dex', '永续 DEX'),
      dataIndex: 'perp_dex',
      key: 'perp_dex',
      render: (value: string, row) => (
        <span>
          {value}{' '}
          {row.is_hip3 ? (
            <Tooltip
              title={t(
                'perps.hip3Hint',
                'HIP-3 是 Hyperliquid 上由第三方部署的市场：场所是 Hyperliquid，做市与上架责任在部署方。',
              )}
            >
              <span className="tag-union">HIP-3</span>
            </Tooltip>
          ) : null}
        </span>
      ),
    },
    {
      title: t('perps.contracts', '合约数'),
      dataIndex: 'contract_count',
      key: 'contract_count',
      align: 'right',
      className: 'numeric',
      // In-scope over observed. The two amounts cover the first number only, and a
      // deployment listing nothing but crypto-native contracts reads as "0 / 47" —
      // observed and out of scope — rather than as an empty venue.
      render: (value: number, row) => (
        <Tooltip
          title={t(
            'perps.contractsHint',
            '左为映射到真实世界标的的合约数，成交额与未平仓只统计这些；右为该部署上观测到的全部合约数。',
          )}
        >
          <span>
            {formatCount(value)}
            <span className="muted"> / {formatCount(row.observed_contract_count)}</span>
          </span>
        </Tooltip>
      ),
    },
    {
      title: t('perps.volume', '成交额 24h（流量）'),
      key: 'vol',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.vol_24h} />,
    },
    {
      title: t('perps.oi', '未平仓名义（存量）'),
      key: 'oi',
      align: 'right',
      render: (_value, row) => <AmountValue amount={row.open_interest_usd} />,
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('perps.title', '永续合约')}</h1>
          <p className="card__hint">
            {t(
              'perps.subtitle',
              '跨场所对真实世界标的的永续敞口：成交是流量，持仓是存量',
            )}
          </p>
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
      </div>

      <ChartFrame
        title={t('perps.chart', '成交额与未平仓 · 前十合约')}
        ariaLabel="双轴组合图：左轴柱状为永续成交额（24h, USD，流量），右轴虚线为永续未平仓名义（USD，存量），按前十合约排列"
        loading={contracts.loading}
        error={contracts.error}
        empty={top.length === 0}
        footnote={t(
          'perps.chartNote',
          '两条序列口径不同，分列左右轴：成交额是 24 小时的流量，未平仓是某一时点的存量，交叉点没有含义。',
        )}
        tableColumns={[
          { key: 'symbol', title: t('perps.contract', '合约') },
          { key: 'vol', title: t('perps.volume', '成交额 24h'), numeric: true },
          { key: 'oi', title: t('perps.oi', '未平仓名义'), numeric: true },
        ]}
        tableRows={top.map((row) => ({
          symbol: `${row.symbol} · ${row.exchange}`,
          vol: <AmountValue amount={row.vol_24h} showScope={false} />,
          oi: <AmountValue amount={row.open_interest_usd} showScope={false} />,
        }))}
      >
        <DualScopeChart
          categories={top.map((row) => row.symbol)}
          left={{
            name: t('perps.volume', '成交额 24h（流量）'),
            scope: 'perp_volume',
            values: top.map((row) => amountNumber(row.vol_24h)),
          }}
          right={{
            name: t('perps.oi', '未平仓名义（存量）'),
            scope: 'perp_oi',
            values: top.map((row) => amountNumber(row.open_interest_usd)),
          }}
        />
      </ChartFrame>

      <section className="card stack-md">
        <h2 className="section-title">{t('perps.venues', '跨场所永续排名')}</h2>
        <p className="card__hint">
          {t(
            'perps.venuesNote',
            '每个场所只统计能映射到真实世界标的的合约，加密原生合约不计入。「其中股票类」是同一场所成交额的子集，不是另一笔成交。',
          )}
        </p>
        {perpVenues.loading ? (
          <TableSkeleton rows={4} />
        ) : perpVenues.error ? (
          <ErrorState error={perpVenues.error} onRetry={perpVenues.reload} />
        ) : venueGroups.length === 0 ? (
          <EmptyState
            title={t('common.empty', '暂无观测数据')}
            hint={t('perps.venuesEmptyHint', '尚未采集到任何场所的永续汇总。')}
          />
        ) : (
          <Table<VenueGroup>
            rowKey="key"
            size="middle"
            pagination={false}
            columns={venueColumns}
            dataSource={venueGroups}
          />
        )}
      </section>

      <section className="card stack-md">
        <h2 className="section-title">{t('perps.dexs', '永续 DEX')}</h2>
        {dexs.loading ? (
          <TableSkeleton rows={3} />
        ) : dexs.error ? (
          <ErrorState error={dexs.error} onRetry={dexs.reload} />
        ) : dexRows.length === 0 ? (
          <EmptyState title={t('common.empty', '暂无观测数据')} />
        ) : (
          <Table<PerpDexRow>
            rowKey="perp_dex"
            size="middle"
            pagination={false}
            columns={dexColumns}
            dataSource={dexRows}
          />
        )}
      </section>

      <section className="card stack-md">
        <h2 className="section-title">{t('perps.table', '合约明细')}</h2>
        {contracts.loading ? (
          <TableSkeleton />
        ) : contracts.error ? (
          <ErrorState error={contracts.error} onRetry={contracts.reload} />
        ) : rows.length === 0 ? (
          <EmptyState title={t('common.empty', '暂无观测数据')} />
        ) : (
          <Table<PerpContractRow>
            rowKey="contract_id"
            size="small"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={columns}
            dataSource={rows}
          />
        )}
      </section>
    </div>
  );
}
