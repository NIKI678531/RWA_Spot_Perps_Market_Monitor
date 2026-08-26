/**
 * T4 — 数据质量 (UI-LAYOUT.md §2.4, QUAL-001…QUAL-006).
 *
 * The page answers one question before it answers any other: **is a conclusion
 * publishable at all?** That is the banner at the top, and it is computed over the whole
 * open queue rather than over the filtered view — whether a number may be quoted cannot
 * depend on which zone the reader happened to click.
 *
 * Below it the gap queue is a work queue, not a report: five zones (source health,
 * entity coverage, quality divergence, verification, baseline health), each row owned,
 * assignable and closable, with `accepted` a real outcome. Some sources cannot be
 * collected without credentials nobody has; recording that keeps the queue honest,
 * while leaving such a row permanently open teaches people to ignore the page.
 *
 * The lower half is the evidence behind the queue — source health, coverage ratios and
 * the flagged pairs that every quality-adjusted total on the site is computed against.
 * A failed fetch is `NOT_VERIFIED`, never zero, so this is where a reader finds out
 * whether a small figure elsewhere means "small" or means "we could not reach it".
 */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Drawer, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertTriangle,
  CheckCircle2,
  CircleCheck,
  CircleSlash,
  Loader2,
  ShieldAlert,
  UserPlus,
  X,
} from 'lucide-react';

import { api } from '@/api/client';
import { entityHref } from '@/api/hrefs';
import { canWrite } from '@/api/identity';
import type {
  DataGapKind,
  DataGapRow,
  DataGapStatus,
  DataGapZone,
  PairRow,
  SourceHealth,
} from '@/api/types';
import { AmountValue } from '@/components/AmountValue';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useIdentity } from '@/hooks/useIdentity';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import {
  formatCount,
  formatMinutes,
  formatPercent,
  formatTimestamp,
} from '@/utils/format';

const DEFAULTS = { kind: 'all', owner: 'all', blocking: '0', gap: '' };

const KINDS: DataGapKind[] = [
  'source_health',
  'entity_coverage',
  'quality_divergence',
  'verification',
  'baseline_health',
];

/** What each zone is for, in one line — the labels alone do not say it. */
const KIND_HINT: Record<DataGapKind, string> = {
  source_health: '采集本身失败或被限流，下游所有数字都受影响',
  entity_coverage: '市场上存在但我们没有收录的标的，缺口即低估',
  quality_divergence: '原始与质量调整口径背离，引用前必须选定口径',
  verification: '本次没有取到观测值，不能当作 0 使用',
  baseline_health: '同时段样本不足，T* 检测器无法在其上发信',
};

function StatusIcon({ status }: { status: string }) {
  if (status === 'ok') return <CheckCircle2 size={16} aria-hidden />;
  if (status === 'not_verified') return <CircleSlash size={16} aria-hidden />;
  return <AlertTriangle size={16} aria-hidden />;
}

const STATUS_LABEL: Record<string, string> = {
  ok: '正常',
  partial: '部分成功',
  failed: '失败',
  not_verified: '未验证',
  rate_limited: '被限流',
};

export function DataQuality() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const identity = useIdentity();
  const writable = canWrite(identity);
  const { state: filters, setState, reset } = useUrlState(DEFAULTS);

  /*
   * `owner_id` on this endpoint is a literal match with one special value —
   * `unassigned` for the pool. There is no `me`, so the caller's own id is substituted
   * here; without an identity the "我的" chip has nothing to filter on and is hidden.
   */
  const ownerParam =
    filters.owner === 'mine'
      ? identity.userId || undefined
      : filters.owner === 'unassigned'
        ? 'unassigned'
        : undefined;

  const gaps = useApi(
    (signal) =>
      api.dataGaps(
        {
          kind: filters.kind === 'all' ? undefined : filters.kind,
          owner_id: ownerParam,
          only_blocking: filters.blocking === '1' ? true : undefined,
          limit: 200,
        },
        signal,
      ),
    [filters.kind, filters.blocking, filters.owner, identity.userId],
  );

  const quality = useApi((signal) => api.dataQuality(signal), []);
  const flagged = useApi(
    (signal) => api.pairs({ flagged_only: true, limit: 100 }, signal),
    [],
  );

  const zones = useMemo<DataGapZone[]>(() => gaps.data?.zones ?? [], [gaps.data]);
  const rows = useMemo(() => zones.flatMap((zone) => zone.rows), [zones]);
  const selected = useMemo(
    () => rows.find((row) => String(row.id) === filters.gap) ?? null,
    [rows, filters.gap],
  );

  const sources: SourceHealth[] = quality.data?.sources ?? [];

  const onGapChanged = useCallback(() => {
    setState({ gap: '' });
    gaps.reload();
  }, [gaps, setState]);

  const gapColumns: ColumnsType<DataGapRow> = [
    {
      title: t('gaps.title', '缺口'),
      key: 'title',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>{row.title}</span>
          {row.detail ? <span className="card__hint">{row.detail}</span> : null}
        </span>
      ),
    },
    {
      title: t('common.status', '状态'),
      key: 'status',
      width: 150,
      render: (_value, row) => (
        <span className="chip-row">
          <span className={`state-tag state-tag--${row.status}`}>
            {label.gapStatus(row.status)}
          </span>
          {row.is_blocking ? (
            <Tooltip
              title={t('gaps.blockingHint', '在这条缺口关闭之前，相关结论不可对外发布。')}
            >
              <span className="state-tag state-tag--blocking">
                {t('gaps.blocking', '阻断发布')}
              </span>
            </Tooltip>
          ) : null}
        </span>
      ),
    },
    {
      title: t('gaps.entity', '对象'),
      key: 'entity',
      width: 180,
      render: (_value, row) => (
        <span className="card__hint">
          {row.source_id ?? row.entity_id ?? '—'}
        </span>
      ),
    },
    {
      title: t('gaps.occurrences', '出现'),
      dataIndex: 'occurrence_count',
      key: 'occurrence_count',
      width: 90,
      align: 'right',
      className: 'numeric',
      render: (value: number) => formatCount(value),
    },
    {
      title: t('gaps.lastSeen', '最近一次'),
      key: 'last_seen',
      width: 170,
      render: (_value, row) => formatTimestamp(row.last_seen_ts),
    },
    {
      title: t('gaps.owner', 'owner'),
      key: 'owner',
      width: 130,
      render: (_value, row) =>
        row.owner_id ? (
          <span>@{row.owner_id}</span>
        ) : (
          <span className="muted">{t('gaps.unassigned', '未分派')}</span>
        ),
    },
  ];

  const flaggedColumns: ColumnsType<PairRow> = [
    { title: t('common.pair', '交易对'), dataIndex: 'symbol', key: 'symbol' },
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
      title: t('quality.flag', '标记原因'),
      key: 'flags',
      width: 160,
      render: (_value, row) => (
        <span className="tag-divergent">
          {row.is_quality_anomaly ? t('quality.anomaly', '数据源标记异常') : ''}
          {row.is_quality_anomaly && row.is_quality_stale ? ' · ' : ''}
          {row.is_quality_stale ? t('quality.stale', '报价停更') : ''}
        </span>
      ),
    },
  ];

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('quality.title', '数据质量')}</h1>
          <p className="card__hint">
            {t(
              'quality.subtitle',
              '采集失败写入 NOT_VERIFIED，不会记为 0。这一页决定了别处的数字该怎么读。',
            )}
          </p>
        </div>
      </div>

      {/* 发布门。Asked of the whole open queue, never of the filtered view. */}
      {gaps.data ? (
        gaps.data.publication_blocked ? (
          <section className="banner banner--blocked">
            <ShieldAlert size={18} aria-hidden />
            <div className="stack-xs">
              <strong>{t('gaps.blockedTitle', '当前存在阻断发布的缺口')}</strong>
              <span className="card__hint">
                {t(
                  'gaps.blockedHint',
                  '在这些缺口关闭之前，受影响的结论不应进入版次、日报或对外材料。先处理下面标为「阻断发布」的行。',
                )}
              </span>
            </div>
            <button
              type="button"
              className="chip"
              onClick={() => setState({ blocking: '1', kind: 'all' })}
            >
              {t('gaps.showBlocking', '只看阻断项')}
            </button>
          </section>
        ) : (
          <section className="banner banner--clear">
            <CheckCircle2 size={18} aria-hidden />
            <div className="stack-xs">
              <strong>{t('gaps.clearTitle', '没有阻断发布的缺口')}</strong>
              <span className="card__hint">
                {t(
                  'gaps.clearHint',
                  '仍有非阻断缺口时，结论可以发布，但引用的数字要带上验证状态。',
                )}
              </span>
            </div>
          </section>
        )
      ) : null}

      {/* 五个缺口区，兼作筛选。 */}
      <section className="queue-summary">
        {KINDS.map((kind) => {
          const zone = zones.find((item) => item.kind === kind);
          return (
            <button
              key={kind}
              type="button"
              className={
                filters.kind === kind
                  ? 'card queue-summary__cell queue-summary__cell--active'
                  : 'card queue-summary__cell'
              }
              aria-pressed={filters.kind === kind}
              onClick={() => setState({ kind: filters.kind === kind ? 'all' : kind })}
            >
              <span className="queue-summary__value numeric">
                {zone ? zone.open_count : '—'}
              </span>
              <span className="queue-summary__label">
                {label.gapKind(kind)}
              </span>
              <span className="card__hint">
                {zone && zone.blocking_count > 0
                  ? `${t('gaps.blocking', '阻断发布')} ${zone.blocking_count}`
                  : t(`gaps.kindHint.${kind}`, KIND_HINT[kind])}
              </span>
            </button>
          );
        })}
      </section>

      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('gaps.owner', 'owner')}</span>
          <div className="chip-row" role="group" aria-label={t('gaps.owner', 'owner')}>
            <button
              type="button"
              className={filters.owner === 'all' ? 'chip chip--active' : 'chip'}
              aria-pressed={filters.owner === 'all'}
              onClick={() => setState({ owner: 'all' })}
            >
              {t('common.all', '全部')}
            </button>
            {identity.userId ? (
              <button
                type="button"
                className={filters.owner === 'mine' ? 'chip chip--active' : 'chip'}
                aria-pressed={filters.owner === 'mine'}
                onClick={() => setState({ owner: 'mine' })}
              >
                {t('gaps.mine', '我的')}
              </button>
            ) : null}
            <button
              type="button"
              className={filters.owner === 'unassigned' ? 'chip chip--active' : 'chip'}
              aria-pressed={filters.owner === 'unassigned'}
              onClick={() => setState({ owner: 'unassigned' })}
            >
              {t('gaps.unassigned', '未分派')}
            </button>
          </div>
        </div>

        <div className="chip-row">
          <button
            type="button"
            className={filters.blocking === '1' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.blocking === '1'}
            onClick={() => setState({ blocking: filters.blocking === '1' ? '0' : '1' })}
          >
            {t('gaps.onlyBlocking', '只看阻断发布')}
          </button>
          <button type="button" className="chip" onClick={reset}>
            {t('common.reset', '重置筛选')}
          </button>
        </div>
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('gaps.queue', '缺口队列')}</h2>
          <span className="card__hint">
            {t('gaps.queueHint', '阻断项在前，其次按影响，再按存在时间。点击一行处理。')}
          </span>
        </div>

        {gaps.loading ? (
          <TableSkeleton />
        ) : gaps.error ? (
          <ErrorState error={gaps.error} onRetry={gaps.reload} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t('gaps.empty', '当前筛选下没有待处理的缺口。')}
            hint={t('gaps.emptyHint', '已修复与已知情接受的缺口不在这个队列里。')}
            action={
              <button type="button" className="chip" onClick={reset}>
                {t('common.reset', '重置筛选')}
              </button>
            }
          />
        ) : (
          zones
            .filter((zone) => zone.rows.length > 0)
            .map((zone) => (
              <div className="stack-sm" key={zone.kind}>
                <div className="row-between">
                  <h3 className="section-title">
                    {t(`gaps.kind.${zone.kind}`, zone.label)}{' '}
                    <span className="numeric">{zone.open_count}</span>
                  </h3>
                  <span className="card__hint">
                    {t(`gaps.kindHint.${zone.kind}`, KIND_HINT[zone.kind])}
                  </span>
                </div>
                <Table<DataGapRow>
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  columns={gapColumns}
                  dataSource={zone.rows}
                  onRow={(row) => ({
                    onClick: () => setState({ gap: String(row.id) }),
                    style: { cursor: 'pointer' },
                  })}
                  rowClassName={(row) => (row.is_blocking ? 'row--blocking' : '')}
                />
              </div>
            ))
        )}
      </section>

      <GapDrawer
        gap={selected}
        open={Boolean(filters.gap)}
        writable={writable}
        userId={identity.userId}
        onClose={() => setState({ gap: '' })}
        onChanged={onGapChanged}
        onOpenEntity={(href) => navigate(href)}
      />

      {quality.loading ? (
        <TableSkeleton rows={3} />
      ) : quality.error ? (
        <ErrorState error={quality.error} onRetry={quality.reload} />
      ) : (
        <>
          <div className="kpi-strip">
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.pairs', '在观测交易对')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.pair_count ?? null)}
              </span>
            </div>
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.flagged', '被标记交易对')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.flagged_pairs ?? null)}
              </span>
              <span className="kpi-card__scope">
                {t('quality.flaggedNote', '不计入质量调整口径，仍保留在原始口径')}
              </span>
            </div>
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.unverified', '未验证交易对')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.unverified_pairs ?? null)}
              </span>
              <span className="kpi-card__scope">
                {t('quality.unverifiedNote', '本次未取到观测值，不等于成交额为 0')}
              </span>
            </div>
            <div className="card kpi-card">
              <span className="kpi-card__label">
                {t('quality.pending', '待映射标的')}
              </span>
              <span className="kpi-card__value">
                {formatCount(quality.data?.pending_mappings ?? null)}
              </span>
              <span className="kpi-card__scope">
                {t('quality.pendingNote', '尚未确认对应真实世界标的，未计入标的口径')}
              </span>
            </div>
          </div>

          <section className="card stack-md">
            <h2 className="section-title">{t('quality.coverage', '覆盖率')}</h2>
            <p className="card__hint">
              {t(
                'quality.coverageNote',
                '上一排数字回答「采到了多少」，这一排回答「这占市场的多少」。分母来自发行商自己公布的产品数，' +
                  '不公布的发行商不按 0 计入，否则覆盖最差的时候比率反而最好看。',
              )}
            </p>
            <div className="kpi-strip">
              <div className="card kpi-card">
                <span className="kpi-card__label">
                  {t('quality.indexed', '已收录资产')}
                </span>
                <span className="kpi-card__value">
                  {formatCount(quality.data?.catalogue.indexed_assets ?? null)}
                </span>
                <span className="kpi-card__scope">
                  {t('quality.indexedNote', '仅在口径内的层级，NON_RWA 不计入')}
                </span>
              </div>
              <div className="card kpi-card">
                <Tooltip
                  title={t(
                    'quality.catalogueRatioHint',
                    '发行商公布的产品数远大于任何聚合器收录的数量：xStocks 官方列出 700 余只，' +
                      'CoinGecko 只收录约 113 只。用收录数当市场规模会严重低估。',
                  )}
                >
                  <span className="kpi-card__label">
                    {t('quality.catalogueRatio', '收录 / 官方公布')}
                  </span>
                </Tooltip>
                <span className="kpi-card__value">
                  {/* Null stays a dash. A ratio of 1.0 would claim we see everything
                      anyone issues, which is a claim, not a default. */}
                  {quality.data?.catalogue.ratio === null ||
                  quality.data?.catalogue.ratio === undefined
                    ? '—'
                    : formatPercent(quality.data.catalogue.ratio)}
                </span>
                <span className="kpi-card__scope">
                  {formatCount(quality.data?.catalogue.official_products ?? null)}{' '}
                  {t('quality.officialProducts', '官方产品数')} ·{' '}
                  {formatCount(quality.data?.catalogue.issuers_with_count ?? null)}/
                  {formatCount(quality.data?.catalogue.issuer_count ?? null)}{' '}
                  {t('quality.issuersReporting', '家发行商有公布')}
                </span>
              </div>
              <div className="card kpi-card">
                <span className="kpi-card__label">
                  {t('quality.referenced', '有参考股价的标的')}
                </span>
                <span className="kpi-card__value">
                  {formatCount(quality.data?.reference.priced_underlyings ?? null)}
                  <span className="kpi-card__scope">
                    {' / '}
                    {formatCount(quality.data?.reference.tracked_underlyings ?? null)}
                  </span>
                </span>
                <span className="kpi-card__scope">
                  {quality.data?.reference.unavailable_reason ??
                    t(
                      'quality.referencedNote',
                      '没有参考价的代币无法判断价格对不对，只能看成交',
                    )}
                </span>
              </div>
              <div className="card kpi-card">
                <Tooltip
                  title={t(
                    'quality.referenceAgeHint',
                    '取最旧的一条参考价：覆盖率只等于最陈旧的那一行，平均值会把一条三天前的报价藏在一堆当前报价里。' +
                      '标的休市时数值大是正常的，不是采集失败。',
                  )}
                >
                  <span className="kpi-card__label">
                    {t('quality.referenceAge', '参考价最大延迟')}
                  </span>
                </Tooltip>
                <span className="kpi-card__value">
                  {formatMinutes(quality.data?.reference.max_age_minutes ?? null)}
                </span>
                <span className="kpi-card__scope">
                  {quality.data?.reference.feed
                    ? `${t('quality.feed', '数据源')}: ${quality.data.reference.feed}`
                    : t('quality.noFeed', '尚未配置参考价数据源')}
                </span>
              </div>
            </div>
          </section>

          {(quality.data?.divergent_venues.length ?? 0) > 0 ? (
            <section className="card stack-sm">
              <h2 className="section-title">
                {t('quality.divergent', '原始与质量调整口径背离的场所')}
              </h2>
              <p className="card__hint">
                {t(
                  'quality.divergentNote',
                  '这些场所的调整后成交额不足原始值的十分之一，或全部报价都被标记。引用它们的原始成交额前，先看这一行。',
                )}
              </p>
              <div className="chip-row">
                {(quality.data?.divergent_venues ?? []).map((venue) => (
                  <button
                    type="button"
                    className="tag-divergent"
                    key={venue}
                    onClick={() => navigate(`/venues?venue=${encodeURIComponent(venue)}`)}
                  >
                    <AlertTriangle size={12} aria-hidden />
                    {venue}
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          <section className="card stack-md">
            <h2 className="section-title">{t('quality.sources', '数据源状态')}</h2>
            {sources.length === 0 ? (
              <EmptyState title={t('quality.noSources', '还没有采集记录。')} />
            ) : (
              <div className="source-grid">
                {sources.map((source) => (
                  <div className="card stack-sm" key={source.source_id}>
                    <div className="row-between">
                      <span className="card__title">{source.source_id}</span>
                      <Tooltip title={source.sample_error ?? undefined}>
                        <span className="chip">
                          <StatusIcon status={source.status} />
                          {label.from('quality.status', source.status, STATUS_LABEL)}
                        </span>
                      </Tooltip>
                    </div>
                    <div className="alert-item__evidence">
                      <div className="evidence-cell">
                        <span>{t('quality.attempts', '尝试次数')}</span>
                        <span className="numeric">{formatCount(source.attempts)}</span>
                      </div>
                      <div className="evidence-cell">
                        <span>{t('quality.records', '写入记录')}</span>
                        <span className="numeric">{formatCount(source.records)}</span>
                      </div>
                      <div className="evidence-cell">
                        <span>{t('quality.duration', '平均耗时')}</span>
                        <span className="numeric">
                          {source.avg_duration_ms === null
                            ? '—'
                            : `${Math.round(source.avg_duration_ms)} ms`}
                        </span>
                      </div>
                      <div className="evidence-cell">
                        <span>{t('quality.lastAttempt', '最近一次')}</span>
                        <span>{formatTimestamp(source.last_attempt_ts)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      <section className="card stack-md">
        <h2 className="section-title">{t('quality.flaggedTable', '被标记的交易对')}</h2>
        {flagged.loading ? (
          <TableSkeleton />
        ) : flagged.error ? (
          <ErrorState error={flagged.error} onRetry={flagged.reload} />
        ) : (flagged.data?.rows.length ?? 0) === 0 ? (
          <EmptyState
            title={t('quality.noFlagged', '本次快照没有被标记的交易对。')}
            hint={t('quality.noFlaggedHint', '原始口径与质量调整口径一致。')}
          />
        ) : (
          <Table<PairRow>
            rowKey={(row) => `${row.asset_id}@${row.venue_id}`}
            size="small"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={flaggedColumns}
            dataSource={flagged.data?.rows ?? []}
          />
        )}
      </section>
    </div>
  );
}

interface GapDrawerProps {
  gap: DataGapRow | null;
  open: boolean;
  writable: boolean;
  /** Empty when anonymous — the write surfaces are already disabled in that case. */
  userId: string;
  onClose: () => void;
  onChanged: () => void;
  onOpenEntity: (href: string) => void;
}

/**
 * Assign, hand over, fix, or knowingly accept.
 *
 * Closing always demands a note. `accepted` in particular is a statement someone is
 * making — "this source needs credentials we do not have" — and an accepted gap with
 * no reason is indistinguishable from one that was clicked away.
 */
function GapDrawer({
  gap,
  open,
  writable,
  userId,
  onClose,
  onChanged,
  onOpenEntity,
}: GapDrawerProps) {
  const { t } = useI18n();
  const label = useLabels();
  const [form, setForm] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState('');
  const [pending, setPending] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const reset = useCallback(() => {
    setForm(null);
    setNote('');
    setAssignee('');
    setFailure(null);
  }, []);

  const run = useCallback(
    async (token: string, call: () => Promise<DataGapRow>) => {
      setPending(token);
      setFailure(null);
      try {
        await call();
        reset();
        onChanged();
      } catch (cause) {
        setFailure(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setPending(null);
      }
    },
    [onChanged, reset],
  );

  const href = gap ? entityHref(gap.entity_type, gap.entity_id) : null;
  const closeStatus: DataGapStatus | null =
    form === 'fixed' ? 'fixed' : form === 'accepted' ? 'accepted' : null;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={480}
      closable={false}
      title={
        <div className="row-between">
          <span className="drawer__title">
            {gap ? gap.title : t('gaps.detail', '缺口详情')}
          </span>
          <button
            type="button"
            className="chip"
            onClick={onClose}
            aria-label={t('common.close', '关闭')}
          >
            <X size={15} aria-hidden />
          </button>
        </div>
      }
    >
      {!gap ? (
        <EmptyState title={t('gaps.gone', '这条缺口已不在当前队列里。')} />
      ) : (
        <div className="stack-md">
          <div className="chip-row">
            <span className="chip chip--static">{label.gapKind(gap.kind)}</span>
            <span className={`state-tag state-tag--${gap.status}`}>
              {label.gapStatus(gap.status)}
            </span>
            {gap.is_blocking ? (
              <span className="state-tag state-tag--blocking">
                {t('gaps.blocking', '阻断发布')}
              </span>
            ) : null}
          </div>

          <section className="stack-sm">
            {gap.detail ? <p className="drawer__claim">{gap.detail}</p> : null}
            {gap.blocks ? (
              <p className="card__hint">
                {t('gaps.blocks', '影响到')}：{gap.blocks}
              </p>
            ) : null}
            <p className="card__hint">
              {t('gaps.firstSeen', '首次')} {formatTimestamp(gap.first_seen_ts)} ·{' '}
              {t('gaps.lastSeen', '最近一次')} {formatTimestamp(gap.last_seen_ts)} ·{' '}
              {t('gaps.occurrences', '出现')} {gap.occurrence_count} 次
            </p>
            <p className="card__hint">
              {t('gaps.owner', 'owner')}：
              {gap.owner_id ? `@${gap.owner_id}` : t('gaps.unassigned', '未分派')}
              {gap.impact_score
                ? ` · ${t('gaps.impact', '影响度')} ${gap.impact_score}`
                : ''}
              {gap.source_id ? ` · ${t('gaps.source', '数据源')} ${gap.source_id}` : ''}
            </p>
            {href ? (
              <button
                type="button"
                className="link-button"
                onClick={() => onOpenEntity(href)}
              >
                {t('gaps.openEntity', '打开相关实体')} → {gap.entity_id}
              </button>
            ) : null}
          </section>

          <section className="stack-sm">
            <h3 className="section-title">{t('gaps.actions', '处理')}</h3>
            {!writable ? (
              <p className="card__hint">
                {t(
                  'gaps.needIdentity',
                  '当前身份为只读或未设置。处理动作会记名写入审计，请先在右上角设置身份。',
                )}
              </p>
            ) : (
              <div className="chip-row">
                {userId && gap.owner_id !== userId ? (
                  <button
                    type="button"
                    className="chip"
                    disabled={pending !== null}
                    onClick={() =>
                      void run('claim', () => api.assignGap(gap.id, userId))
                    }
                  >
                    {pending === 'claim' ? (
                      <Loader2 size={13} className="spin" aria-hidden />
                    ) : null}
                    {t('gaps.claim', '认领')}
                  </button>
                ) : null}
                <button
                  type="button"
                  className={form === 'assign' ? 'chip chip--active' : 'chip'}
                  onClick={() =>
                    setForm((current) => (current === 'assign' ? null : 'assign'))
                  }
                >
                  {t('gaps.assign', '转派')}
                </button>
                <button
                  type="button"
                  className={form === 'fixed' ? 'chip chip--active' : 'chip'}
                  onClick={() =>
                    setForm((current) => (current === 'fixed' ? null : 'fixed'))
                  }
                >
                  {t('gaps.fixed', '标记已修复')}
                </button>
                <button
                  type="button"
                  className={form === 'accepted' ? 'chip chip--active' : 'chip'}
                  onClick={() =>
                    setForm((current) => (current === 'accepted' ? null : 'accepted'))
                  }
                >
                  {t('gaps.accepted', '知情接受')}
                </button>
              </div>
            )}

            {form ? (
              <div className="action-form stack-sm">
                {form === 'assign' ? (
                  <label className="field">
                    <span className="field__label">
                      <UserPlus size={13} aria-hidden />
                      {t('gaps.assignee', '转派给（用户 ID）')}
                    </span>
                    <input
                      className="field__input"
                      value={assignee}
                      onChange={(event) => setAssignee(event.target.value)}
                    />
                  </label>
                ) : (
                  <label className="field">
                    <span className="field__label">
                      {form === 'accepted'
                        ? t(
                            'gaps.acceptNote',
                            '接受理由：为什么这条缺口不会被修复，以及它对结论的影响',
                          )
                        : t('gaps.fixNote', '修复说明：做了什么，从哪一次采集起生效')}
                    </span>
                    <textarea
                      className="field__input"
                      rows={3}
                      value={note}
                      onChange={(event) => setNote(event.target.value)}
                    />
                  </label>
                )}

                {failure ? <p className="form-error">{failure}</p> : null}

                <div className="row-between">
                  <button type="button" className="chip" onClick={reset}>
                    {t('common.cancel', '取消')}
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    disabled={
                      pending !== null ||
                      (form === 'assign'
                        ? assignee.trim().length === 0
                        : note.trim().length === 0)
                    }
                    onClick={() => {
                      if (form === 'assign') {
                        void run('assign', () =>
                          api.assignGap(gap.id, assignee.trim()),
                        );
                      } else if (closeStatus) {
                        void run(closeStatus, () =>
                          api.closeGap(gap.id, closeStatus, note.trim()),
                        );
                      }
                    }}
                  >
                    {pending ? (
                      <Loader2 size={14} className="spin" aria-hidden />
                    ) : (
                      <CircleCheck size={14} aria-hidden />
                    )}
                    {t('common.submit', '提交')}
                  </button>
                </div>
              </div>
            ) : null}

            {failure && !form ? <p className="form-error">{failure}</p> : null}
          </section>
        </div>
      )}
    </Drawer>
  );
}
