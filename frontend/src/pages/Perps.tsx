/**
 * T2 — cross-venue perpetual exposure to real-world underlyings (UI-LAYOUT.md §2.2).
 *
 * Volume and open interest appear together because the comparison is the point: a
 * contract with heavy turnover and thin OI is being traded, one with thin turnover and
 * heavy OI is being held. They are a flow and a stock, so they never share an axis —
 * bars on the left, a dashed line on the right — and they never get a shared legend
 * implying a sum.
 *
 * The exchange's own classification is shown verbatim next to ours. Binance files some
 * ETFs and leveraged ETPs under `EQUITY`; overwriting that label would make our
 * numbers impossible to reconcile against theirs.
 *
 * R1 puts the filters in the URL (rule 23) — exchange and perp-dex go to the server,
 * the analysis-group chip and the text needle filter the loaded rows. Every contract
 * row drills into Underlying 360, or into the "not mapped yet" page when the contract
 * has no underlying, so the reader never dead-ends on a symbol.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type { PerpContractRow, PerpDexRow, PerpVenueRow } from '@/api/types';
import { ChartFrame } from '@/charts/ChartFrame';
import { DualScopeChart } from '@/charts/DualScopeChart';
import { AmountValue } from '@/components/AmountValue';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { amountNumber, formatCount } from '@/utils/format';

const DEFAULTS = { exchange: '', dex: '', group: 'all', q: '' };

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
  const navigate = useNavigate();
  const { state: filters, setState, reset, active } = useUrlState(DEFAULTS);
  const needle = filters.q.trim().toLowerCase();

  const contracts = useApi(
    (signal) =>
      api.perpContracts(
        {
          exchange: filters.exchange || undefined,
          perp_dex: filters.dex || undefined,
          limit: 200,
        },
        signal,
      ),
    [filters.exchange, filters.dex],
  );
  const dexs = useApi((signal) => api.perpDexs(signal), []);
  const perpVenues = useApi((signal) => api.perpVenues(signal), []);

  const all = useMemo<PerpContractRow[]>(
    () => contracts.data?.rows ?? [],
    [contracts.data],
  );

  /*
   * The analysis group is ours, not the exchange's, and the endpoint has no parameter
   * for it — adding one would mean a second definition of the grouping living in the
   * query layer. The chip list is therefore built from what came back and applied here,
   * which also means it can never offer a group with nothing behind it.
   */
  const groups = useMemo(() => {
    const seen = new Set<string>();
    for (const row of all) if (row.analysis_group) seen.add(row.analysis_group);
    return [...seen].sort();
  }, [all]);

  const rows = useMemo<PerpContractRow[]>(
    () =>
      all.filter((row) => {
        if (filters.group !== 'all' && row.analysis_group !== filters.group) return false;
        if (!needle) return true;
        return (
          row.symbol.toLowerCase().includes(needle) ||
          row.exchange.toLowerCase().includes(needle)
        );
      }),
    [all, filters.group, needle],
  );

  const top = rows.slice(0, 10);
  const dexRows: PerpDexRow[] = dexs.data?.rows ?? [];
  const venueGroups = useMemo(
    () => groupVenues(perpVenues.data?.rows ?? []),
    [perpVenues.data],
  );

  const exchanges = useMemo(() => {
    const seen = new Set<string>();
    for (const group of venueGroups) seen.add(group.total.exchange);
    return [...seen].sort();
  }, [venueGroups]);

  const openContract = (row: PerpContractRow) =>
    navigate(
      row.underlying_id
        ? `/underlying/${row.underlying_id}`
        : `/underlying/by-contract/${row.contract_id}`,
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
    {
      title: t('perps.contract', '合约'),
      key: 'symbol',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>{row.symbol}</span>
          {row.underlying_id ? null : (
            <span className="card__hint">{t('perps.unmapped', '尚未映射到底层')}</span>
          )}
        </span>
      ),
    },
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
      </div>

      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('perps.exchange', '交易所')}</span>
          <div
            className="chip-row"
            role="group"
            aria-label={t('perps.exchange', '交易所')}
          >
            <button
              type="button"
              className={filters.exchange === '' ? 'chip chip--active' : 'chip'}
              aria-pressed={filters.exchange === ''}
              onClick={() => setState({ exchange: '' })}
            >
              {t('common.all', '全部')}
            </button>
            {exchanges.map((exchange) => (
              <button
                key={exchange}
                type="button"
                className={filters.exchange === exchange ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.exchange === exchange}
                onClick={() =>
                  setState({ exchange: filters.exchange === exchange ? '' : exchange })
                }
              >
                {exchange}
              </button>
            ))}
          </div>
        </div>

        {groups.length > 1 ? (
          <div className="filter-group">
            <span className="filter-group__label">
              {t('perps.analysisGroup', '分析口径')}
            </span>
            <div
              className="chip-row"
              role="group"
              aria-label={t('perps.analysisGroup', '分析口径')}
            >
              <button
                type="button"
                className={filters.group === 'all' ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.group === 'all'}
                onClick={() => setState({ group: 'all' })}
              >
                {t('common.all', '全部')}
              </button>
              {groups.map((group) => (
                <button
                  key={group}
                  type="button"
                  className={filters.group === group ? 'chip chip--active' : 'chip'}
                  aria-pressed={filters.group === group}
                  onClick={() =>
                    setState({ group: filters.group === group ? 'all' : group })
                  }
                >
                  {group}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {Object.keys(active).length ? (
          <div className="chip-row">
            {filters.dex ? (
              <button
                type="button"
                className="chip chip--active"
                onClick={() => setState({ dex: '' })}
              >
                {t('perps.dex', '永续 DEX')}：{filters.dex} ×
              </button>
            ) : null}
            {filters.q ? (
              <button
                type="button"
                className="chip chip--active"
                onClick={() => setState({ q: '' })}
              >
                {t('common.filter', '筛选')}：{filters.q} ×
              </button>
            ) : null}
            <button type="button" className="chip" onClick={reset}>
              {t('common.reset', '重置筛选')}
            </button>
          </div>
        ) : null}
      </section>

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
        <div className="card__head">
          <h2 className="card__title">{t('perps.venues', '跨场所永续排名')}</h2>
          <span className="card__hint">
            {t('perps.venuesHint', '点击一行，只看该交易所的合约。')}
          </span>
        </div>
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
            onRow={(row) => ({
              onClick: () =>
                setState({
                  exchange:
                    filters.exchange === row.total.exchange ? '' : row.total.exchange,
                }),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.total.exchange === filters.exchange ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('perps.dexs', '永续 DEX')}</h2>
          <span className="card__hint">
            {t('perps.dexsHint', '点击一行，只看该部署上的合约。')}
          </span>
        </div>
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
            onRow={(row) => ({
              onClick: () =>
                setState({ dex: filters.dex === row.perp_dex ? '' : row.perp_dex }),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.perp_dex === filters.dex ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('perps.table', '合约明细')}</h2>
          <span className="card__hint">
            {t('perps.tableHint', '点击一行进入底层 360。')}
          </span>
        </div>
        {contracts.loading ? (
          <TableSkeleton />
        ) : contracts.error ? (
          <ErrorState error={contracts.error} onRetry={contracts.reload} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t('common.empty', '暂无观测数据')}
            hint={
              all.length
                ? t('perps.filteredEmpty', '当前筛选下没有合约，试试清除筛选。')
                : undefined
            }
          />
        ) : (
          <Table<PerpContractRow>
            rowKey="contract_id"
            size="small"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={columns}
            dataSource={rows}
            onRow={(row) => ({
              onClick: () => openContract(row),
              style: { cursor: 'pointer' },
            })}
          />
        )}
      </section>
    </div>
  );
}
