/**
 * T3 — 底层 360 (UI-LAYOUT.md §2.3, U360-001…U360-008).
 *
 * Everything about one underlying, in one place: who wraps it, who issues those
 * wrappers, where they trade, what the perpetuals say, what is alerting on it, and
 * whether we have a product against it.
 *
 * Deliberately absent from the navigation rail. It is an entity page reached by
 * drilling into something, not a destination — a rail entry would force a reader to
 * pick an entity before seeing anything.
 *
 * The page is partitioned by metric scope rather than laid out as one table, because
 * a single row carrying market cap, spot turnover and open interest reads as three
 * comparable numbers when it is three different kinds of number.
 */

import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type {
  AlertRow,
  Amount,
  FirstListingRow,
  PerpExposureRow,
  VenueBreakdownRow,
  WrapperRow,
} from '@/api/types';
import { AmountValue } from '@/components/AmountValue';
import { MetricValueView } from '@/components/values';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useAsOf } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatAge, formatTimestamp } from '@/utils/format';

export function Underlying360() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const asOf = useAsOf();
  const { underlyingId } = useParams<{ underlyingId: string }>();

  const detail = useApi(
    (signal) =>
      underlyingId
        ? api.underlying(underlyingId, { as_of: asOf }, signal)
        : Promise.resolve(null),
    [underlyingId, asOf],
  );

  const data = detail.data;

  const wrapperColumns: ColumnsType<WrapperRow> = useMemo(
    () => [
      {
        title: t('u360.wrapper', '代币化包装'),
        key: 'symbol',
        render: (_v, row) => (
          <span className="stack-xs">
            <span>{row.symbol}</span>
            <span className="card__hint">{row.asset_id}</span>
          </span>
        ),
      },
      {
        title: t('u360.issuer', '发行商'),
        key: 'issuer',
        width: 140,
        render: (_v, row) => row.issuer ?? <span className="muted">—</span>,
      },
      {
        title: t('u360.chain', '链'),
        key: 'chain',
        width: 120,
        render: (_v, row) => row.chain ?? <span className="muted">—</span>,
      },
      {
        title: t('u360.tier', 'RWA 分层'),
        key: 'tier',
        width: 120,
        render: (_v, row) => (
          <span className={`state-tag state-tag--${row.rwa_tier}`}>
            {label.tier(row.rwa_tier)}
          </span>
        ),
      },
      {
        title: label.scope('spot_market_cap'),
        key: 'cap',
        width: 150,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.market_cap} showScope={false} />,
      },
      {
        title: label.scope('spot_volume'),
        key: 'vol',
        width: 150,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.vol_24h} showScope={false} />,
      },
    ],
    [t, label],
  );

  const venueColumns: ColumnsType<VenueBreakdownRow> = useMemo(
    () => [
      {
        title: t('u360.venue', '交易场所'),
        key: 'venue',
        render: (_v, row) => row.venue,
      },
      {
        title: t('u360.venueType', '类型'),
        key: 'type',
        width: 110,
        render: (_v, row) =>
          row.venue_type ? (
            <span className="chip chip--static">{row.venue_type.toUpperCase()}</span>
          ) : (
            <span className="muted">—</span>
          ),
      },
      {
        title: `${label.scope('spot_volume')}（${t('u360.adjusted', '质量调整后')}）`,
        key: 'vol',
        width: 190,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.adjusted_vol_24h} showScope={false} />,
      },
    ],
    [t, label],
  );

  const perpColumns: ColumnsType<PerpExposureRow> = useMemo(
    () => [
      {
        title: t('u360.contract', '合约'),
        key: 'contract',
        render: (_v, row) => (
          <span className="stack-xs">
            <span>{row.contract}</span>
            <span className="card__hint">
              {row.exchange}
              {row.perp_dex && row.perp_dex !== row.exchange ? ` · ${row.perp_dex}` : ''}
            </span>
          </span>
        ),
      },
      {
        title: label.scope('perp_volume'),
        key: 'vol',
        width: 160,
        align: 'right',
        render: (_v, row) => <AmountValue amount={row.vol_24h} showScope={false} />,
      },
      {
        title: label.scope('perp_oi'),
        key: 'oi',
        width: 160,
        align: 'right',
        render: (_v, row) => (
          <AmountValue amount={row.open_interest_usd} showScope={false} />
        ),
      },
    ],
    [t, label],
  );

  const listingColumns: ColumnsType<FirstListingRow> = useMemo(
    () => [
      {
        title: t('u360.wrapper', '代币化包装'),
        key: 'symbol',
        render: (_v, row) => row.symbol,
      },
      {
        title: t('u360.issuer', '发行商'),
        key: 'issuer',
        width: 150,
        render: (_v, row) => row.issuer ?? <span className="muted">—</span>,
      },
      {
        title: t('u360.firstSeen', '首次观测到'),
        key: 'first_seen',
        width: 170,
        render: (_v, row) => (
          <Tooltip
            title={t(
              'u360.firstSeenHint',
              '这是我们第一次观测到它的时间，不是官方上架日。没有免费源提供上架日。',
            )}
          >
            <span>{formatTimestamp(row.first_seen_at)}</span>
          </Tooltip>
        ),
      },
      {
        title: t('u360.measured', '是否可测'),
        key: 'measured',
        width: 130,
        render: (_v, row) =>
          row.is_measured ? (
            <span className="state-tag state-tag--live">
              {t('u360.measuredYes', '有成交数据')}
            </span>
          ) : (
            <Tooltip
              title={t(
                'u360.measuredHint',
                '已观测到该包装存在，但没有可用的成交观测。这是缺口，不是零。',
              )}
            >
              <span className="not-verified">{t('common.notVerified', '未验证')}</span>
            </Tooltip>
          ),
      },
    ],
    [t],
  );

  if (detail.loading) {
    return (
      <div className="stack-lg">
        <TableSkeleton rows={10} />
      </div>
    );
  }
  if (detail.error) {
    return <ErrorState error={detail.error} onRetry={detail.reload} />;
  }
  if (!data) {
    return (
      <EmptyState
        title={t('u360.missing', '找不到这个底层。')}
        hint={t('u360.missingHint', '它可能还没有被映射，或不在 RWA 口径内。')}
      />
    );
  }

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div className="stack-xs">
          <h1 className="page-title">{data.name}</h1>
          <span className="chip-row">
            <span className="chip chip--static">
              {label.assetClass(data.asset_class)}
            </span>
            {data.region ? <span className="chip chip--static">{data.region}</span> : null}
            {data.is_pre_ipo ? (
              <span className="chip chip--static">{t('u360.preIpo', 'Pre-IPO')}</span>
            ) : null}
            {data.theme_id ? (
              <button
                type="button"
                className="chip"
                onClick={() => navigate(`/themes?theme=${encodeURIComponent(data.theme_id as string)}`)}
              >
                {data.theme_id}
              </button>
            ) : null}
          </span>
          <p className="card__hint">{data.scope_note}</p>
          {data.benchmark_id ? (
            <p className="card__hint">
              {t('u360.benchmark', '参考基准')}：{data.benchmark_id}
              {' · '}
              {t(
                'u360.benchmarkHint',
                '基准仅用于对照显示，不进入任何排名、汇总或告警。',
              )}
            </p>
          ) : null}
        </div>
      </div>

      {/* Four figures, four scopes, partitioned. There is no total, and there cannot be. */}
      <section className="kpi-band" aria-label={t('u360.figures', '四个口径的关键数字')}>
        <ScopeCell
          label={label.scope('spot_market_cap')}
          dimension={t('common.stock', '存量')}
          amount={data.spot_market_cap}
        />
        <ScopeCell
          label={`${label.scope('spot_volume')}（${t('u360.adjusted', '质量调整后')}）`}
          dimension={t('common.flow', '流量 · 24h')}
          amount={data.spot_vol_adjusted}
        />
        <div className="kpi-band__divider" role="separator" aria-orientation="vertical" />
        <ScopeCell
          label={label.scope('perp_volume')}
          dimension={t('common.flow', '流量 · 24h')}
          amount={data.perp_vol_24h}
        />
        <ScopeCell
          label={label.scope('perp_oi')}
          dimension={t('common.stock', '存量')}
          amount={data.perp_oi_usd}
        />
      </section>

      <section className="split-2">
        <div className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('u360.wrappers', '代币化包装与发行商')}</h2>
            <span className="card__hint">
              {t(
                'u360.wrappersHint',
                '一个发行商在一个场所是一次上架；多个发行商跨多个场所才是竞争。',
              )}
            </span>
          </div>
          {data.tokenized_wrappers.length ? (
            <Table<WrapperRow>
              rowKey="asset_id"
              size="small"
              pagination={false}
              columns={wrapperColumns}
              dataSource={data.tokenized_wrappers}
            />
          ) : (
            <EmptyState title={t('u360.noWrappers', '尚未观测到代币化包装。')} />
          )}
        </div>

        <div className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('u360.venues', '成交场所分布')}</h2>
            <span className="card__hint">
              {t('u360.venuesHint', '仅现货成交口径，质量调整后。')}
            </span>
          </div>
          {data.venue_breakdown.length ? (
            <Table<VenueBreakdownRow>
              rowKey="venue_id"
              size="small"
              pagination={false}
              columns={venueColumns}
              dataSource={data.venue_breakdown}
            />
          ) : (
            <EmptyState title={t('u360.noVenues', '本次快照没有可用的场所成交观测。')} />
          )}
        </div>
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('u360.perps', '永续敞口')}</h2>
          <span className="card__hint">
            {t(
              'u360.perpsHint',
              '成交与未平仓是两个口径，并列成两列，不合计、不共用坐标轴。',
            )}
          </span>
        </div>
        {data.perp_exposure.length ? (
          <Table<PerpExposureRow>
            rowKey={(row) => `${row.exchange}:${row.contract}`}
            size="small"
            pagination={false}
            columns={perpColumns}
            dataSource={data.perp_exposure}
          />
        ) : (
          <EmptyState
            title={t('u360.noPerps', '没有观测到对应的永续合约。')}
            hint={t('u360.noPerpsHint', '这通常意味着还没有交易所上线该标的的永续。')}
          />
        )}
      </section>

      <section className="split-2">
        <div className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('u360.alerts', '在办告警')}</h2>
          </div>
          {data.active_alerts.length ? (
            <ul className="alert-list">
              {data.active_alerts.map((row) => (
                <EntityAlertRow
                  key={row.id}
                  row={row}
                  onOpen={() => navigate(`/alerts/${row.id}`)}
                />
              ))}
            </ul>
          ) : (
            <EmptyState
              title={t('u360.noAlerts', '这个底层当前没有已发布的告警。')}
              hint={t('u360.noAlertsHint', '这是一个真实的空，不是采集失败。')}
            />
          )}
        </div>

        <div className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('u360.coverage', '我们的覆盖')}</h2>
          </div>
          {data.our_coverage ? (
            <div className="stack-sm">
              <span className={`state-tag state-tag--${data.our_coverage.state}`}>
                {label.coverage(data.our_coverage.state)}
              </span>
              {data.our_coverage.product_id ? (
                <p>
                  {t('u360.product', '对应产品')}：{data.our_coverage.product_id}
                </p>
              ) : null}
              {data.our_coverage.demand ? (
                <p>
                  {t('u360.demand', '需求读数')}：
                  <MetricValueView value={data.our_coverage.demand} size="sm" />
                </p>
              ) : null}
              {data.our_coverage.note ? (
                <p className="card__hint">{data.our_coverage.note}</p>
              ) : null}
              {data.our_coverage.assessed_at ? (
                <p className="card__hint">
                  {t('u360.assessedAt', '评估时间')}{' '}
                  {formatTimestamp(data.our_coverage.assessed_at)}
                </p>
              ) : null}
            </div>
          ) : (
            <EmptyState
              title={t('u360.noCoverage', '尚未评估我们是否应该覆盖这个底层。')}
            />
          )}

          {data.candidates.length ? (
            <div className="stack-sm">
              <h3 className="section-title">{t('u360.candidates', '发行候选')}</h3>
              <ul className="plain-list">
                {data.candidates.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => navigate(`/candidates/${row.id}`)}
                    >
                      {label.stage(row.stage)} · {row.underlying_name}
                    </button>
                    {row.weakest_dimension ? (
                      <span className="card__hint">
                        {t('candidates.weakest', '短板')}：
                        {row.readiness.find((c) => c.key === row.weakest_dimension)
                          ?.label ?? row.weakest_dimension}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </section>

      <section className="split-2">
        <div className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('u360.firstListings', '首次观测到的包装')}</h2>
            <span className="card__hint">
              {t(
                'u360.firstListingsHint',
                '新产品表现看这里：首次观测时间与是否已有成交观测。',
              )}
            </span>
          </div>
          {data.first_listings.length ? (
            <Table<FirstListingRow>
              rowKey="asset_id"
              size="small"
              pagination={false}
              columns={listingColumns}
              dataSource={data.first_listings}
            />
          ) : (
            <EmptyState title={t('u360.noListings', '没有记录到新的包装。')} />
          )}
        </div>

        <div className="card stack-md">
          <div className="card__head">
            <h2 className="card__title">{t('u360.gaps', '未处理的数据缺口')}</h2>
            <span className="card__hint">
              {t(
                'u360.gapsHint',
                '缺口决定这一页的结论能不能对外用。阻断性缺口存在时，不要在管理层材料里引用本页数字。',
              )}
            </span>
          </div>
          {data.open_data_gaps.length ? (
            <ul className="plain-list">
              {data.open_data_gaps.map((gap) => (
                <li key={gap.id}>
                  <span>
                    <span className="chip chip--static">{label.gapKind(gap.kind)}</span>
                    {gap.is_blocking ? (
                      <span className="state-tag state-tag--overdue">
                        {t('quality.blocking', '阻断发布')}
                      </span>
                    ) : null}
                  </span>
                  <span>{gap.title}</span>
                  {gap.blocks ? <span className="card__hint">{gap.blocks}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title={t('u360.noGaps', '没有影响这个底层的未处理缺口。')} />
          )}
          <button
            type="button"
            className="link-button"
            onClick={() => navigate('/data-quality')}
          >
            {t('u360.openQuality', '打开数据质量')}
          </button>
        </div>
      </section>
    </div>
  );
}

function ScopeCell({
  label,
  dimension,
  amount,
}: {
  label: string;
  dimension: string;
  amount: Amount | null;
}) {
  return (
    <div className="kpi-cell">
      <span className="kpi-cell__label">{label}</span>
      <AmountValue amount={amount} showScope={false} className="metric--lg" />
      <span className="kpi-cell__dimension">{dimension}</span>
    </div>
  );
}

function EntityAlertRow({ row, onOpen }: { row: AlertRow; onOpen: () => void }) {
  const { t } = useI18n();
  const label = useLabels();
  return (
    <li>
      <button type="button" className="alert-list__row" onClick={onOpen}>
        <span className="alert-list__marks">
          <span className={`sev sev--${row.severity}`}>
            {label.severity(row.severity)}
          </span>
          <span className="chip chip--static">{row.detector}</span>
        </span>
        <span className="alert-list__headline">{row.headline_zh}</span>
        <span className="alert-list__meta">
          <span>{label.scope(row.metric_scope)}</span>
          <span aria-hidden>·</span>
          <span>{label.session(row.market_session)}</span>
          <span aria-hidden>·</span>
          <span>
            {t('u360.occurrences', '出现')} {row.occurrence_count}
          </span>
          <span aria-hidden>·</span>
          <span>{formatAge(row.last_seen_ts)}</span>
        </span>
      </button>
    </li>
  );
}
