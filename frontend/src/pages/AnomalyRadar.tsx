/**
 * T4 — 异常雷达 (UI-LAYOUT.md §2.4, ALERT-001…ALERT-007).
 *
 * v2.0's reverse-chronological feed is gone. An alert is a business object with an
 * owner, a state and an audit trail, so this is a work queue: the default view is
 * **open and actionable**, not "everything, newest first". That default decides who
 * gets seen — a feed sorted by time buries a critical finding from this morning under
 * a dozen low ones from this afternoon.
 *
 * Every filter is written to the URL (rule 23), so a queue someone is looking at can
 * be sent to the person who should be looking at it.
 *
 * The queue only ever contains alerts that cleared the publication gate; nothing here
 * re-derives visibility, and nothing here can show a raw detector output.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { api } from '@/api/client';
import type {
  AlertSeverity,
  AlertWorkItem,
  MetricScope,
  QueueAlertRow,
} from '@/api/types';
import { AlertDrawer } from '@/components/AlertDrawer';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useIdentity } from '@/hooks/useIdentity';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatAge, formatTimestamp } from '@/utils/format';

const DEFAULTS = {
  severity: 'all',
  state: 'open',
  family: 'all',
  scope: 'all',
  session: 'all',
  owner: 'all',
  overdue: '0',
};

const SEVERITIES: AlertSeverity[] = ['critical', 'high', 'medium', 'low'];

/**
 * The state filter is a view, not a raw enum passthrough: `open` is the working
 * default (everything not closed), and `closed` is the retrospective view. Offering
 * nine raw states as chips would make the useful one just as hard to find as the rest.
 */
const STATE_FILTERS: ReadonlyArray<{ id: string; label: string }> = [
  { id: 'open', label: '待办' },
  { id: 'published', label: '待认领' },
  { id: 'claimed', label: '处理中' },
  { id: 'in_review', label: '待复核' },
  { id: 'actioned', label: '已行动' },
  { id: 'closed', label: '已关闭' },
];

const FAMILY_FILTERS: ReadonlyArray<{ id: string; label: string; hint: string }> = [
  { id: 'all', label: '全部族', hint: '横截面与时序两族一起看' },
  {
    id: 'cross_sectional',
    label: 'X* 横截面',
    hint: '与同组对比的现状异常，单次确认即可发布，不需要历史',
  },
  {
    id: 'time_series',
    label: 'T* 时序',
    hint: '相对自身历史基线升温，需 ≥14 个同时段样本并连续两次确认',
  },
];

const SCOPES: MetricScope[] = [
  'spot_market_cap',
  'spot_volume',
  'dex_liquidity',
  'perp_volume',
  'perp_oi',
];

export function AnomalyRadar() {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const identity = useIdentity();
  const { alertId } = useParams<{ alertId?: string }>();
  const [search] = useSearchParams();
  const { state: filters, setState, reset } = useUrlState(DEFAULTS);

  const selectedId = alertId ? Number(alertId) : null;

  const queue = useApi(
    (signal) =>
      api.alertQueue(
        {
          severity: filters.severity === 'all' ? undefined : filters.severity,
          state:
            filters.state === 'open' || filters.state === 'closed'
              ? undefined
              : filters.state,
          include_closed: filters.state === 'closed' ? true : undefined,
          family: filters.family === 'all' ? undefined : filters.family,
          metric_scope: filters.scope === 'all' ? undefined : filters.scope,
          market_session: filters.session === 'all' ? undefined : filters.session,
          // `me` is resolved from the request identity server-side, so the browser
          // never has to guess who the caller is.
          owner_id: filters.owner === 'mine' ? 'me' : undefined,
          only_overdue: filters.overdue === '1' ? true : undefined,
          limit: 200,
        },
        signal,
      ),
    [
      filters.severity,
      filters.state,
      filters.family,
      filters.scope,
      filters.session,
      filters.owner,
      filters.overdue,
      // Re-read when the acting identity changes: `owner_id=me` means someone else now.
      identity.userId,
    ],
  );

  const workItem = useApi(
    (signal) =>
      selectedId === null
        ? Promise.resolve(null)
        : api.workItem(selectedId, signal),
    [selectedId],
  );

  // An action returns the updated work item. Holding it here rather than refetching
  // keeps the drawer from flickering back to its pre-action state for a frame.
  const [override, setOverride] = useState<AlertWorkItem | null>(null);
  useEffect(() => setOverride(null), [selectedId]);

  // No `usePageStatus` here on purpose. The queue endpoint returns row `Meta`, not a
  // status bar, and synthesising one would mean inventing `sources_ok` and a
  // verification status for a strip whose whole job is to say how trustworthy the
  // page is. The shell's own `/status-bar` read is the honest source.

  const closeDrawer = useCallback(() => {
    const qs = search.toString();
    navigate(qs ? `/alerts?${qs}` : '/alerts');
  }, [navigate, search]);

  const onChanged = useCallback(
    (item: AlertWorkItem) => {
      setOverride(item);
      queue.reload();
    },
    [queue],
  );

  const summary = queue.data?.summary;
  const rows = queue.data?.rows ?? [];

  const columns: ColumnsType<QueueAlertRow> = useMemo(
    () => [
      {
        title: t('common.severity', '严重度'),
        key: 'severity',
        width: 96,
        render: (_value, row) => (
          <span className={`sev sev--${row.severity}`}>
            {label.severity(row.severity)}
          </span>
        ),
      },
      {
        title: t('common.state', '状态'),
        key: 'state',
        width: 130,
        render: (_value, row) => (
          <span className="chip-row">
            <span className={`state-tag state-tag--${row.state}`}>
              {label.alertState(row.state)}
            </span>
            {row.is_overdue ? (
              <Tooltip
                title={
                  row.sla_due_ts
                    ? `${t('alerts.sla', 'SLA 截止')} ${formatTimestamp(row.sla_due_ts)}`
                    : undefined
                }
              >
                <span className="state-tag state-tag--overdue">
                  {t('alerts.overdue', '超时')}
                </span>
              </Tooltip>
            ) : null}
          </span>
        ),
      },
      {
        title: t('alerts.headline', '结论'),
        key: 'headline',
        render: (_value, row) => (
          <span className="stack-xs">
            <span>{row.headline_zh}</span>
            <span className="card__hint">
              {row.entity_name ?? row.entity_id} · {row.family_claim}
            </span>
          </span>
        ),
      },
      {
        title: t('alerts.detector', '检测器'),
        key: 'detector',
        width: 110,
        render: (_value, row) => (
          <Tooltip title={row.family_claim}>
            <span className="chip chip--static">{row.detector}</span>
          </Tooltip>
        ),
      },
      {
        title: t('common.scope', '口径'),
        key: 'scope',
        width: 130,
        render: (_value, row) => label.scope(row.metric_scope),
      },
      {
        title: t('common.session', '时段'),
        key: 'session',
        width: 120,
        render: (_value, row) => label.session(row.market_session),
      },
      {
        title: t('alerts.confirmations', '确认'),
        key: 'confirmations',
        width: 84,
        align: 'right',
        className: 'numeric',
        render: (_value, row) =>
          `${row.confirmation_count}/${row.confirmations_required}`,
      },
      {
        title: t('alerts.owner', 'owner'),
        key: 'owner',
        width: 120,
        render: (_value, row) =>
          row.owner_name ?? row.owner_id ?? (
            <span className="muted">{t('alerts.unassigned', '未分派')}</span>
          ),
      },
      {
        title: t('alerts.lastSeen', '最近一次'),
        key: 'last_seen',
        width: 120,
        render: (_value, row) => (
          <Tooltip title={formatTimestamp(row.last_seen_ts)}>
            <span>{formatAge(row.last_seen_ts)}</span>
          </Tooltip>
        ),
      },
    ],
    [t, label],
  );

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('radar.title', '异常雷达')}</h1>
          <p className="card__hint">
            {queue.data?.meta.note ??
              t(
                'radar.subtitle',
                '队列只显示已通过发布门的告警。认领后处理，关闭前复盘。',
              )}
          </p>
        </div>
      </div>

      {/* 队列摘要 — coloured by severity and lateness, never by a score. */}
      <section className="queue-summary">
        <SummaryCell
          label={t('radar.unclaimed', '待认领')}
          value={summary?.unclaimed}
          tone="attention"
          onClick={() => setState({ state: 'published' })}
        />
        <SummaryCell
          label={t('radar.inProgress', '处理中')}
          value={summary?.in_progress}
          onClick={() => setState({ state: 'claimed' })}
        />
        <SummaryCell
          label={t('radar.awaitingReview', '待复核')}
          value={summary?.awaiting_review}
          onClick={() => setState({ state: 'in_review' })}
        />
        <SummaryCell
          label={t('radar.overdue', '超时')}
          value={summary?.overdue}
          tone="danger"
          onClick={() => setState({ overdue: '1' })}
        />
        <SummaryCell
          label={t('radar.closed', '已关闭')}
          value={summary?.closed}
          onClick={() => setState({ state: 'closed' })}
        />
        <SummaryCell
          label={t('radar.falsePositive', '误报')}
          value={summary?.false_positive}
          onClick={() => setState({ state: 'closed' })}
        />
      </section>

      {/* 筛选条 */}
      <section className="card stack-sm filter-bar">
        <FilterGroup
          label={t('common.severity', '严重度')}
          options={[
            { id: 'all', label: t('common.all', '全部') },
            ...SEVERITIES.map((id) => ({ id, label: label.severity(id) })),
          ]}
          value={filters.severity}
          onChange={(severity) => setState({ severity })}
        />
        <FilterGroup
          label={t('common.state', '状态')}
          options={STATE_FILTERS.map((option) => ({
            ...option,
            label: t(`radar.stateFilter.${option.id}`, option.label),
          }))}
          value={filters.state}
          onChange={(state) => setState({ state })}
        />
        <FilterGroup
          label={t('radar.family', '检测族')}
          options={FAMILY_FILTERS.map((option) => ({
            id: option.id,
            label: t(`radar.familyFilter.${option.id}`, option.label),
            hint: t(`radar.familyHint.${option.id}`, option.hint),
          }))}
          value={filters.family}
          onChange={(family) => setState({ family })}
        />
        <FilterGroup
          label={t('common.scope', '口径')}
          options={[
            { id: 'all', label: t('common.all', '全部') },
            ...SCOPES.map((id) => ({ id, label: label.scope(id) })),
          ]}
          value={filters.scope}
          onChange={(scope) => setState({ scope })}
        />
        <FilterGroup
          label={t('alerts.owner', 'owner')}
          options={[
            { id: 'all', label: t('common.all', '全部') },
            { id: 'mine', label: t('radar.mine', '我的') },
          ]}
          value={filters.owner}
          onChange={(owner) => setState({ owner })}
        />
        <div className="chip-row">
          <button
            type="button"
            className={filters.overdue === '1' ? 'chip chip--active' : 'chip'}
            aria-pressed={filters.overdue === '1'}
            onClick={() => setState({ overdue: filters.overdue === '1' ? '0' : '1' })}
          >
            {t('radar.onlyOverdue', '只看超时')}
          </button>
          <button type="button" className="chip" onClick={reset}>
            {t('common.reset', '重置筛选')}
          </button>
        </div>
      </section>

      <section className="card stack-md">
        {queue.loading ? (
          <TableSkeleton rows={8} />
        ) : queue.error ? (
          <ErrorState error={queue.error} onRetry={queue.reload} />
        ) : !rows.length ? (
          <EmptyState
            title={t('radar.empty', '当前筛选下没有告警。')}
            hint={t(
              'radar.emptyHint',
              '这是一个真实的空：采集成功、检测运行过、没有达到发布条件的发现。',
            )}
            action={
              <button type="button" className="chip" onClick={reset}>
                {t('common.reset', '重置筛选')}
              </button>
            }
          />
        ) : (
          <Table<QueueAlertRow>
            rowKey="id"
            size="middle"
            pagination={{ pageSize: 25, hideOnSinglePage: true }}
            columns={columns}
            dataSource={rows}
            onRow={(row) => ({
              onClick: () => {
                const qs = search.toString();
                navigate(qs ? `/alerts/${row.id}?${qs}` : `/alerts/${row.id}`);
              },
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.id === selectedId ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      <AlertDrawer
        alertId={selectedId}
        item={override ?? workItem.data}
        loading={workItem.loading && !override}
        error={workItem.error}
        onClose={closeDrawer}
        onChanged={onChanged}
        onRetry={workItem.reload}
      />
    </div>
  );
}

function SummaryCell({
  label,
  value,
  tone,
  onClick,
}: {
  label: string;
  value: number | undefined;
  tone?: 'attention' | 'danger';
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`queue-summary__cell${tone ? ` queue-summary__cell--${tone}` : ''}`}
      onClick={onClick}
    >
      <span className="queue-summary__value numeric">{value ?? '—'}</span>
      <span className="queue-summary__label">{label}</span>
    </button>
  );
}

function FilterGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ReadonlyArray<{ id: string; label: string; hint?: string }>;
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="filter-group">
      <span className="filter-group__label">{label}</span>
      <div className="chip-row" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            className={option.id === value ? 'chip chip--active' : 'chip'}
            aria-pressed={option.id === value}
            title={option.hint}
            onClick={() => onChange(option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
