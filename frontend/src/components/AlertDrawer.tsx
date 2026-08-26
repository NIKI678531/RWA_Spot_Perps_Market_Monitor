/**
 * The evidence-and-action drawer (UI-LAYOUT.md T4, ALERT-003…ALERT-006).
 *
 * This is where an alert stops being a notification and becomes a business object.
 * Four things are non-negotiable in here:
 *
 *  1. **No evidence field may be omitted.** Raw value, baseline or peer group,
 *     `robust_z`, sample size, `market_session`, rule name, confirmations. An alert
 *     that cannot be justified to management is noise, and hiding a missing field
 *     makes it look justified.
 *  2. **The two families are described in their own words.** A cross-sectional finding
 *     shows a peer group and says 同组现状异常; a time-series finding shows a baseline
 *     and says 相对历史基线升温. Mixing the vocabulary makes a peer comparison read as
 *     a trend, which is a different and unearned claim.
 *  3. **Counter-evidence sits beside the evidence**, not behind a toggle.
 *  4. **Buttons come from `available_actions`.** The server resolved what may legally
 *     happen next from the same transition table it will validate against, so the
 *     drawer never offers a button that is going to fail — and closing always demands
 *     a retrospective or one of the standard declined reasons.
 */

import { useCallback, useMemo, useState } from 'react';
import { Drawer, Tooltip } from 'antd';
import { CircleCheck, Clock, Loader2, UserPlus, X } from 'lucide-react';

import { api } from '@/api/client';
import { canWrite } from '@/api/identity';
import type {
  AlertState,
  AlertWorkItem,
  QueueEvidenceRow,
  TimelineEntry,
} from '@/api/types';
import { ConfidenceBadge, CounterEvidenceList } from '@/components/values';
import { ErrorState, TableSkeleton } from '@/components/states';
import { useIdentity } from '@/hooks/useIdentity';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatTimestamp, formatUsd } from '@/utils/format';

/**
 * Labels for both vocabularies: the action tokens `available_actions` offers, and the
 * `AlertActionType` values the timeline reads back. They overlap but are not the same
 * list — `review` is offered, `start_review` is what gets recorded — and the timeline
 * additionally replays transitions nobody clicked (`publish`, `evidence_appended`).
 */
const ACTION_LABEL: Record<string, string> = {
  note: '写备注',
  claim: '认领',
  release: '释放',
  reassign: '转派',
  review: '提交复核',
  start_review: '提交复核',
  action: '记录行动',
  defer: '搁置',
  create_task: '建研究任务',
  resolve: '结案',
  false_positive: '标记误报',
  mark_false_positive: '标记误报',
  dismiss: '不处理',
  publish: '通过发布门',
  confirm: '再次确认',
  evidence_appended: '追加证据版本',
};

/** The standard declined reasons, spelled out. Mirrors `STANDARD_CLOSE_REASONS`. */
const REASON_LABEL: Record<string, string> = {
  duplicate: '与已有告警重复',
  data_artefact: '数据管线造成，非市场变化',
  below_materiality: '真实但金额不重要',
  known_event: '已知事件（上市 / 调仓 / 公司行动）',
  expected_seasonality: '预期内的季节性',
  no_action_needed: '真实且已理解，无需响应',
};

/** Which of the closing actions each token maps to. */
const CLOSE_STATE: Record<string, AlertState> = {
  resolve: 'resolved',
  false_positive: 'false_positive',
  dismiss: 'dismissed',
};

export interface AlertDrawerProps {
  alertId: number | null;
  item: AlertWorkItem | null;
  loading: boolean;
  error: Error | null;
  onClose: () => void;
  onChanged: (item: AlertWorkItem) => void;
  onRetry: () => void;
}

export function AlertDrawer({
  alertId,
  item,
  loading,
  error,
  onClose,
  onChanged,
  onRetry,
}: AlertDrawerProps) {
  const { t } = useI18n();
  const label = useLabels();
  const identity = useIdentity();
  const writable = canWrite(identity);

  const [pending, setPending] = useState<string | null>(null);
  const [form, setForm] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState('');
  const [until, setUntil] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<string | null>(null);

  const reset = useCallback(() => {
    setForm(null);
    setNote('');
    setAssignee('');
    setUntil('');
    setTaskTitle('');
    setReason('');
    setFailure(null);
  }, []);

  const run = useCallback(
    async (token: string, call: () => Promise<AlertWorkItem>) => {
      setPending(token);
      setFailure(null);
      try {
        onChanged(await call());
        reset();
      } catch (cause) {
        setFailure(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setPending(null);
      }
    },
    [onChanged, reset],
  );

  const submit = useCallback(
    (token: string) => {
      if (!alertId) return;
      if (token === 'claim') return run(token, () => api.claimAlert(alertId));
      if (token === 'release') return run(token, () => api.releaseAlert(alertId));
      if (token === 'review') return run(token, () => api.reviewAlert(alertId));
      if (token === 'note') {
        return run(token, () => api.noteAlert(alertId, note.trim()));
      }
      if (token === 'reassign') {
        return run(token, () =>
          api.reassignAlert(alertId, assignee.trim(), note.trim() || undefined),
        );
      }
      if (token === 'action') {
        return run(token, () => api.actionAlert(alertId, note.trim()));
      }
      if (token === 'defer') {
        return run(token, () =>
          api.deferAlert(alertId, new Date(until).toISOString(), note.trim() || undefined),
        );
      }
      if (token === 'create_task') {
        return run(token, () =>
          api.createAlertTask(alertId, {
            title: taskTitle.trim(),
            detail: note.trim() || undefined,
          }),
        );
      }
      // Read the target state out first: `token in CLOSE_STATE` proves the key is
      // present but leaves the lookup optional, and a closing call with no target
      // state is exactly the request that must never be built.
      const closing = CLOSE_STATE[token];
      if (closing) {
        return run(token, () =>
          api.closeAlert(alertId, {
            to_state: closing,
            note: note.trim() || undefined,
            reason_code: reason || undefined,
          }),
        );
      }
      return undefined;
    },
    [alertId, run, note, assignee, until, taskTitle, reason],
  );

  // Which fields the open form needs filled before it may be submitted. Kept beside
  // the submit call so a new action cannot get a button without getting a guard.
  const ready = useMemo(() => {
    if (!form) return false;
    if (form === 'note' || form === 'action') return note.trim().length > 0;
    if (form === 'reassign') return assignee.trim().length > 0;
    if (form === 'defer') return until.length > 0;
    if (form === 'create_task') return taskTitle.trim().length > 0;
    // Closing: a retrospective note or a standard reason. Never neither.
    if (form in CLOSE_STATE) return note.trim().length > 0 || reason.length > 0;
    return true;
  }, [form, note, assignee, until, taskTitle, reason]);

  const alert = item?.alert;

  return (
    <Drawer
      open={alertId !== null}
      onClose={onClose}
      width={520}
      closable={false}
      title={
        <div className="row-between">
          <span className="drawer__title">
            {alert ? alert.headline_zh : t('alerts.drawer', '告警详情')}
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
      {loading ? (
        <TableSkeleton rows={8} />
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : !item || !alert ? null : (
        <div className="stack-md">
          <div className="chip-row">
            <span className={`sev sev--${alert.severity}`}>
              {label.severity(alert.severity)}
            </span>
            <span className={`state-tag state-tag--${alert.state}`}>
              {label.alertState(alert.state)}
            </span>
            {alert.is_overdue ? (
              <span className="state-tag state-tag--overdue">
                {t('alerts.overdue', '超时')}
              </span>
            ) : null}
            <span className="chip chip--static">{alert.detector}</span>
            <span className="chip chip--static">{label.scope(alert.metric_scope)}</span>
            <span className="chip chip--static">
              {label.session(alert.market_session)}
            </span>
          </div>

          {/* 事实 → 意义。The family's own claim, written by the server. */}
          <section className="stack-sm">
            <h3 className="section-title">{t('alerts.claim', '结论')}</h3>
            <p className="drawer__claim">{alert.family_claim}</p>
            <p className="card__hint">
              {t('alerts.entity', '实体')}：{alert.entity_name ?? alert.entity_id} ·{' '}
              {t('alerts.owner', 'owner')}：
              {alert.owner_name ?? alert.owner_id ?? t('alerts.unassigned', '未分派')} ·{' '}
              {t('alerts.firstSeen', '首次')} {formatTimestamp(alert.first_seen_ts)} ·{' '}
              {t('alerts.occurrences', '出现')} {alert.occurrence_count} 次
            </p>
            <ConfidenceBadge confidence={item.confidence} />
            {alert.sla_due_ts ? (
              <p className="card__hint">
                <Clock size={13} aria-hidden /> {t('alerts.sla', 'SLA 截止')}{' '}
                {formatTimestamp(alert.sla_due_ts)}
              </p>
            ) : null}
            {alert.deferred_until ? (
              <p className="card__hint">
                {t('alerts.deferred', '已搁置至')} {formatTimestamp(alert.deferred_until)}
              </p>
            ) : null}
          </section>

          {/* 证据 — every field, in the family's own vocabulary. */}
          <section className="stack-sm">
            <h3 className="section-title">{t('alerts.evidence', '证据')}</h3>
            {item.evidence.map((row, index) => (
              <EvidenceCard
                key={`${row.rule_name}-${row.version}-${index}`}
                row={row}
                family={alert.family}
              />
            ))}
            {!item.evidence.length ? (
              <p className="card__hint">
                {t(
                  'alerts.noEvidence',
                  '这条告警没有留存证据。缺证据的告警不该被发布，请在数据质量页登记缺口。',
                )}
              </p>
            ) : null}
          </section>

          <CounterEvidenceList items={item.counter_evidence} />

          {/* 动作区 */}
          <section className="stack-sm">
            <h3 className="section-title">{t('alerts.actions', '处理')}</h3>

            {!writable ? (
              <p className="card__hint">
                {t(
                  'alerts.needIdentity',
                  '当前身份为只读或未设置。处理动作会记名写入审计，请先在右上角设置身份。',
                )}
              </p>
            ) : !item.available_actions.length ? (
              <p className="card__hint">
                {t('alerts.closed', '这条告警已关闭，只可查阅，不可再改。')}
              </p>
            ) : (
              <div className="chip-row">
                {item.available_actions.map((token) => (
                  <button
                    key={token}
                    type="button"
                    className={form === token ? 'chip chip--active' : 'chip'}
                    onClick={() => {
                      setFailure(null);
                      // A token with no inputs fires immediately; the rest open a form.
                      if (['claim', 'release', 'review'].includes(token)) {
                        void submit(token);
                      } else {
                        setForm((current) => (current === token ? null : token));
                      }
                    }}
                    disabled={pending !== null}
                  >
                    {pending === token ? (
                      <Loader2 size={13} className="spin" aria-hidden />
                    ) : null}
                    {label.from('enum.alertAction', token, ACTION_LABEL)}
                  </button>
                ))}
              </div>
            )}

            {form ? (
              <div className="action-form stack-sm">
                {form === 'reassign' ? (
                  <label className="field">
                    <span className="field__label">
                      <UserPlus size={13} aria-hidden />
                      {t('alerts.assignee', '转派给（用户 ID）')}
                    </span>
                    <input
                      className="field__input"
                      value={assignee}
                      onChange={(event) => setAssignee(event.target.value)}
                    />
                  </label>
                ) : null}

                {form === 'defer' ? (
                  <label className="field">
                    <span className="field__label">
                      {t('alerts.deferUntil', '搁置到')}
                    </span>
                    <input
                      className="field__input"
                      type="datetime-local"
                      value={until}
                      onChange={(event) => setUntil(event.target.value)}
                    />
                  </label>
                ) : null}

                {form === 'create_task' ? (
                  <label className="field">
                    <span className="field__label">{t('alerts.taskTitle', '任务标题')}</span>
                    <input
                      className="field__input"
                      value={taskTitle}
                      onChange={(event) => setTaskTitle(event.target.value)}
                    />
                  </label>
                ) : null}

                {form in CLOSE_STATE ? (
                  <div className="field">
                    <span className="field__label">
                      {t('alerts.closeReason', '标准结论（与复盘二选一，不可都留空）')}
                    </span>
                    <div className="chip-row">
                      {item.close_reasons.map((code) => (
                        <button
                          key={code}
                          type="button"
                          className={reason === code ? 'chip chip--active' : 'chip'}
                          aria-pressed={reason === code}
                          onClick={() => setReason(reason === code ? '' : code)}
                        >
                          {label.from('enum.closeReason', code, REASON_LABEL)}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}

                <label className="field">
                  <span className="field__label">
                    {form in CLOSE_STATE
                      ? t('alerts.retrospective', '复盘：结论是否真实、影响了什么决策、阈值建议')
                      : t('common.note', '备注')}
                  </span>
                  <textarea
                    className="field__input"
                    rows={form in CLOSE_STATE ? 4 : 2}
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </label>

                {failure ? <p className="form-error">{failure}</p> : null}

                <div className="row-between">
                  <button type="button" className="chip" onClick={reset}>
                    {t('common.cancel', '取消')}
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    disabled={!ready || pending !== null}
                    onClick={() => void submit(form)}
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

          {/* 研究任务 */}
          {item.tasks.length ? (
            <section className="stack-sm">
              <h3 className="section-title">{t('alerts.tasks', '研究任务')}</h3>
              <ul className="plain-list">
                {item.tasks.map((task) => (
                  <li key={task.id}>
                    <span className="state-tag">{label.taskStatus(task.status)}</span>{' '}
                    {task.title}
                    {task.owner_id ? (
                      <span className="card__hint"> @{task.owner_id}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {/* 时间线 — the append-only audit record, read back. */}
          <section className="stack-sm">
            <h3 className="section-title">{t('alerts.timeline', '时间线')}</h3>
            <ol className="timeline">
              {item.timeline.map((entry) => (
                <TimelineRow key={entry.id} entry={entry} />
              ))}
            </ol>
          </section>
        </div>
      )}
    </Drawer>
  );
}

function EvidenceCard({
  row,
  family,
}: {
  row: QueueEvidenceRow;
  family: 'cross_sectional' | 'time_series';
}) {
  const { t } = useI18n();
  const label = useLabels();
  const crossSectional = family === 'cross_sectional';

  return (
    <div className="evidence-card">
      <div className="row-between">
        <span className="evidence-card__rule">{row.rule_name}</span>
        <span className="card__hint">
          v{row.version} · {formatTimestamp(row.snapshot_ts)}
        </span>
      </div>

      <dl className="evidence-card__grid">
        <div>
          <dt>{t('evidence.observed', '原始值')}</dt>
          <dd className="numeric">{formatUsd(row.observed_value)}</dd>
        </div>

        {crossSectional ? (
          <>
            <div>
              <dt>{t('evidence.peerMedian', '同组中位数')}</dt>
              <dd className="numeric">{formatUsd(row.baseline_median)}</dd>
            </div>
            <div>
              <dt>{t('evidence.peerCount', '同组样本数')}</dt>
              <dd className="numeric">{row.peer_count ?? '—'}</dd>
            </div>
          </>
        ) : (
          <>
            <div>
              <dt>{t('evidence.baseline', '历史基线中位数')}</dt>
              <dd className="numeric">{formatUsd(row.baseline_median)}</dd>
            </div>
            <div>
              <dt>MAD</dt>
              <dd className="numeric">{formatUsd(row.baseline_mad)}</dd>
            </div>
            <div>
              <dt>{t('evidence.sample', '同时段样本量')}</dt>
              <dd className="numeric">{row.sample_size ?? '—'}</dd>
            </div>
          </>
        )}

        <div>
          <dt>
            <Tooltip title={t('evidence.zHint', '稳健 z 值：以中位数与 MAD 计算，不用均值与标准差')}>
              robust z
            </Tooltip>
          </dt>
          <dd className="numeric">
            {row.robust_z === null ? '—' : row.robust_z.toFixed(2)}
          </dd>
        </div>
        <div>
          <dt>{t('evidence.session', '市场时段')}</dt>
          <dd>{label.session(row.market_session)}</dd>
        </div>
        <div>
          <dt>{t('evidence.verification', '验证状态')}</dt>
          <dd>{label.verification(row.verification_status)}</dd>
        </div>
      </dl>

      {row.revision_reason ? (
        <p className="card__hint">
          {t('evidence.revision', '证据修订原因')}：{row.revision_reason}
        </p>
      ) : null}

      <CounterEvidenceList items={row.counter_evidence} />
    </div>
  );
}

function TimelineRow({ entry }: { entry: TimelineEntry }) {
  const { t } = useI18n();
  const label = useLabels();
  const transition =
    entry.from_state && entry.to_state && entry.from_state !== entry.to_state
      ? `${label.alertState(entry.from_state)} → ${label.alertState(entry.to_state)}`
      : null;

  return (
    <li className="timeline__row">
      <span className="timeline__time">{formatTimestamp(entry.created_at)}</span>
      <span className="timeline__body">
        <strong>{label.from('enum.alertAction', entry.action, ACTION_LABEL)}</strong>
        {transition ? <span className="timeline__transition">{transition}</span> : null}
        <span className="card__hint">
          {entry.actor_id ?? t('alerts.system', '系统')}
          {entry.to_owner_id ? ` → @${entry.to_owner_id}` : ''}
        </span>
        {entry.note ? <span className="timeline__note">{entry.note}</span> : null}
        {entry.reason_code ? (
          <span className="timeline__note">
            {label.from('enum.closeReason', entry.reason_code, REASON_LABEL)}
          </span>
        ) : null}
      </span>
    </li>
  );
}
