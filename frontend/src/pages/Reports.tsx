/**
 * T5 — 报告与复盘 (UI-LAYOUT.md §2.5, RPT-001…RPT-007, REV-001…REV-004).
 *
 * Three things live here because they are the same job seen at three ranges: the
 * editions someone can quote, the files those editions produced, and the review that
 * asks whether last month's alerts were worth anyone's attention.
 *
 * The rules this page exists to hold:
 *
 *  - **A frozen edition is never edited.** A correction is a new numbered revision
 *    carrying its reason and a field-level diff; the superseded version stays readable
 *    at `?rev=`. "已更新" is not a reason anyone can act on, so the reason is required.
 *  - **Share links are redacted server-side**, and the redacted field list is stated
 *    back to the sharer. Redaction someone cannot see is redaction they will not trust,
 *    and will work around by pasting a screenshot instead.
 *  - **Every export carries the view state it was taken under.** An artifact whose
 *    filters are unknown cannot be reproduced six weeks later, which is the whole
 *    reason the export exists.
 *  - Files are served from the database or object storage, never a container
 *    filesystem: production K8s has no PersistentVolumeClaim, and a report that
 *    disappears at the next rollout is worse than no report.
 */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CircleCheck,
  Download,
  FileSpreadsheet,
  FileText,
  Link2,
  Loader2,
  RefreshCw,
  Snowflake,
} from 'lucide-react';

import { api } from '@/api/client';
import { canWrite } from '@/api/identity';
import type {
  ArtifactRow,
  DetectorReviewRow,
  EditionDetail,
  EditionRevisionRow,
  EditionRow,
  ReportRow,
  RetrospectiveRow,
  ShareLink,
} from '@/api/types';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/states';
import { useApi } from '@/hooks/useApi';
import { useIdentity } from '@/hooks/useIdentity';
import { useUrlState } from '@/hooks/useUrlState';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import {
  formatBytes,
  formatCount,
  formatDate,
  formatPercent,
  formatTimestamp,
} from '@/utils/format';

const DEFAULTS = { tab: 'editions', rev: '', days: '30' };

const TABS: ReadonlyArray<{ id: string; label: string; hint: string }> = [
  { id: 'editions', label: '版次', hint: '可引用的冻结版与它们的修订' },
  { id: 'review', label: '复盘', hint: '告警最终去向与检测器阈值回顾' },
  { id: 'files', label: '文件', hint: '生成的工作簿与简报' },
];

const REVIEW_WINDOWS: ReadonlyArray<{ id: string; label: string }> = [
  { id: '7', label: '7 天' },
  { id: '30', label: '30 天' },
  { id: '90', label: '90 天' },
];

const STORAGE_LABEL: Record<string, string> = {
  database: '数据库',
  object_storage: '对象存储 (TOS)',
};

const REASON_LABEL: Record<string, string> = {
  duplicate: '与已有告警重复',
  data_artefact: '数据管线造成，非市场变化',
  below_materiality: '真实但金额不重要',
  known_event: '已知事件（上市 / 调仓 / 公司行动）',
  expected_seasonality: '预期内的季节性',
  no_action_needed: '真实且已理解，无需响应',
};

function hoursText(value: number | null): string {
  if (value === null) return '—';
  if (value < 24) return `${value.toFixed(1)} 小时`;
  return `${(value / 24).toFixed(1)} 天`;
}

export function Reports() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { editionKey } = useParams<{ editionKey: string }>();
  const { state: view, setState } = useUrlState(DEFAULTS);

  // Landing on `/editions/:key` is a request to read that edition, whatever tab the
  // URL last carried.
  const tab = editionKey ? 'editions' : view.tab;

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('reports.title', '报告与复盘')}</h1>
          <p className="card__hint">
            {t(
              'reports.subtitle',
              '冻结版一经写入不再修改，更正走修订号；复盘回答这些告警是否值得看。',
            )}
          </p>
        </div>
      </div>

      <section className="card filter-bar stack-sm">
        <div className="chip-row" role="tablist" aria-label={t('reports.tabs', '视图')}>
          {TABS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={tab === entry.id}
              className={tab === entry.id ? 'chip chip--active' : 'chip'}
              onClick={() => {
                // Leaving the edition detail means leaving its route, not just its tab.
                if (editionKey) navigate(`/reports?tab=${entry.id}`);
                else setState({ tab: entry.id, rev: '' });
              }}
            >
              {t(`reports.tab.${entry.id}`, entry.label)}
            </button>
          ))}
        </div>
        <span className="card__hint">
          {t(
            `reports.tabHint.${tab}`,
            TABS.find((entry) => entry.id === tab)?.hint ?? '',
          )}
        </span>
      </section>

      {tab === 'editions' ? (
        <EditionsTab
          editionKey={editionKey ?? null}
          revision={view.rev}
          onSelectEdition={(key) => navigate(`/editions/${encodeURIComponent(key)}`)}
          onSelectRevision={(rev) => setState({ rev })}
          onBack={() => navigate('/reports')}
        />
      ) : null}

      {tab === 'review' ? (
        <ReviewTab days={view.days} onDays={(days) => setState({ days })} />
      ) : null}

      {tab === 'files' ? <FilesTab /> : null}
    </div>
  );
}

/* --- editions ------------------------------------------------------------- */

interface EditionsTabProps {
  editionKey: string | null;
  revision: string;
  onSelectEdition: (key: string) => void;
  onSelectRevision: (rev: string) => void;
  onBack: () => void;
}

function EditionsTab({
  editionKey,
  revision,
  onSelectEdition,
  onSelectRevision,
  onBack,
}: EditionsTabProps) {
  const { t } = useI18n();
  const label = useLabels();
  const list = useApi((signal) => api.editions({ limit: 30 }, signal), []);
  const detail = useApi(
    (signal) =>
      editionKey
        ? api.edition(
            editionKey,
            { revision: revision ? Number(revision) : undefined },
            signal,
          )
        : Promise.resolve(null),
    [editionKey, revision],
  );

  const rows = useMemo<EditionRow[]>(() => list.data?.rows ?? [], [list.data]);

  const columns: ColumnsType<EditionRow> = [
    {
      title: t('editions.label', '版次'),
      key: 'label',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>
            {row.kind === 'live' ? null : <Snowflake size={13} aria-hidden />} {row.label}
          </span>
          <span className="card__hint">{row.edition_key}</span>
        </span>
      ),
    },
    {
      title: t('editions.kind', '类型'),
      dataIndex: 'kind',
      key: 'kind',
      width: 120,
      render: (value: EditionRow['kind']) => label.editionKind(value),
    },
    {
      title: t('common.status', '状态'),
      key: 'status',
      width: 170,
      render: (_value, row) => (
        <span className="chip-row">
          <span className={`state-tag state-tag--${row.status}`}>
            {label.editionStatus(row.status)}
          </span>
          {row.revision > 1 ? (
            <span className="chip chip--static">r{row.revision}</span>
          ) : null}
          {row.superseded_by_revision ? (
            <Tooltip
              title={t(
                'editions.supersededHint',
                '这一版已被更高的修订号取代，但仍然可读——有人引用过它。',
              )}
            >
              <span className="card__hint">→ r{row.superseded_by_revision}</span>
            </Tooltip>
          ) : null}
        </span>
      ),
    },
    {
      title: (
        <Tooltip
          title={t(
            'editions.asOfHint',
            '数据截止时间，不是生成时间。两者常常相差数小时，混用会让报告看起来比数据新。',
          )}
        >
          <span>{t('editions.asOf', '数据截止')}</span>
        </Tooltip>
      ),
      key: 'as_of',
      width: 180,
      render: (_value, row) => formatTimestamp(row.as_of),
    },
    {
      title: t('editions.generated', '生成时间'),
      key: 'generated',
      width: 180,
      render: (_value, row) => formatTimestamp(row.generated_at),
    },
    {
      title: t('editions.artifacts', '附件'),
      dataIndex: 'artifact_count',
      key: 'artifact_count',
      width: 80,
      align: 'right',
      className: 'numeric',
      render: (value: number) => formatCount(value),
    },
  ];

  return (
    <>
      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('editions.list', '版次')}</h2>
          <span className="card__hint">
            {list.data?.meta.note ??
              t('editions.listHint', '点击一行查看该版次的修订、差异与附件。')}
          </span>
        </div>
        {list.loading ? (
          <TableSkeleton />
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={list.reload} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t('editions.none', '还没有冻结版。')}
            hint={t('editions.noneHint', '09:00 与 17:00 各冻结一次，由调度任务写入。')}
          />
        ) : (
          <Table<EditionRow>
            rowKey={(row) => `${row.edition_key}-${row.revision}`}
            size="middle"
            pagination={{ pageSize: 15, hideOnSinglePage: true }}
            columns={columns}
            dataSource={rows}
            onRow={(row) => ({
              onClick: () => onSelectEdition(row.edition_key),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(row) =>
              row.edition_key === editionKey ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </section>

      {editionKey ? (
        detail.loading ? (
          <section className="card">
            <TableSkeleton rows={6} />
          </section>
        ) : detail.error ? (
          <section className="card">
            <ErrorState error={detail.error} onRetry={detail.reload} />
          </section>
        ) : detail.data ? (
          <EditionDetailPanel
            detail={detail.data}
            revision={revision}
            onSelectRevision={onSelectRevision}
            onChanged={() => {
              onSelectRevision('');
              detail.reload();
              list.reload();
            }}
            onBack={onBack}
          />
        ) : null
      ) : null}
    </>
  );
}

interface EditionDetailPanelProps {
  detail: EditionDetail;
  revision: string;
  onSelectRevision: (rev: string) => void;
  onChanged: () => void;
  onBack: () => void;
}

function EditionDetailPanel({
  detail,
  revision,
  onSelectRevision,
  onChanged,
  onBack,
}: EditionDetailPanelProps) {
  const { t } = useI18n();
  const label = useLabels();
  const identity = useIdentity();
  const writable = canWrite(identity);
  // Correcting a number someone already quoted is a publishing decision. The backend
  // restricts it to owners and admins; saying so here beats a 403 after typing.
  const mayRevise = writable && ['owner', 'admin'].includes(identity.role);

  const [form, setForm] = useState<'revise' | 'share' | null>(null);
  const [reason, setReason] = useState('');
  const [expires, setExpires] = useState('');
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [share, setShare] = useState<ShareLink | null>(null);

  const edition = detail.edition;

  const revise = useCallback(async () => {
    setPending(true);
    setFailure(null);
    try {
      await api.reviseEdition(edition.edition_key, reason.trim());
      setForm(null);
      setReason('');
      onChanged();
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }, [edition.edition_key, reason, onChanged]);

  const createShare = useCallback(async () => {
    setPending(true);
    setFailure(null);
    try {
      setShare(
        await api.createShareLink(edition.edition_key, {
          expires_at: expires ? new Date(expires).toISOString() : undefined,
        }),
      );
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }, [edition.edition_key, expires]);

  return (
    <section className="card stack-md">
      <div className="card__head">
        <h2 className="card__title">
          {edition.label}
          {edition.revision > 1 ? ` · r${edition.revision}` : ''}
        </h2>
        <button type="button" className="chip" onClick={onBack}>
          {t('editions.back', '返回列表')}
        </button>
      </div>

      <div className="chip-row">
        <span className={`state-tag state-tag--${edition.status}`}>
          {label.editionStatus(edition.status)}
        </span>
        <span className="chip chip--static">{label.editionKind(edition.kind)}</span>
        <span className="chip chip--static">
          {t('editions.asOf', '数据截止')} {formatTimestamp(edition.as_of)}
        </span>
        <span className="chip chip--static">
          {t('editions.tradingDate', '交易日')} {formatDate(edition.trading_date)}
        </span>
        {edition.theme_map_version ? (
          <Tooltip
            title={t(
              'editions.themeVersionHint',
              '这一版按当时生效的主题定义计算。改动主题映射不会重写历史版次。',
            )}
          >
            <span className="chip chip--static">
              {t('editions.themeVersion', '主题定义')} v{edition.theme_map_version}
            </span>
          </Tooltip>
        ) : null}
      </div>

      {edition.failure_reason ? (
        <p className="form-error">
          {t('editions.failed', '生成失败')}：{edition.failure_reason}
        </p>
      ) : null}

      {/* 修订历史 —— 差异逐字段列出，被取代的版本仍然可读。 */}
      <div className="stack-sm">
        <h3 className="section-title">{t('editions.revisions', '修订')}</h3>
        {detail.revisions.length === 0 ? (
          <p className="card__hint">
            {t('editions.noRevisions', '这一版没有被更正过，写入后即为最终值。')}
          </p>
        ) : (
          <>
            <div className="chip-row">
              <button
                type="button"
                className={revision === '' ? 'chip chip--active' : 'chip'}
                onClick={() => onSelectRevision('')}
              >
                {t('editions.current', '当前修订')}
              </button>
              {detail.revisions.map((row) => (
                <button
                  key={row.id}
                  type="button"
                  className={
                    revision === String(row.revision) ? 'chip chip--active' : 'chip'
                  }
                  onClick={() => onSelectRevision(String(row.revision))}
                >
                  r{row.revision}
                </button>
              ))}
            </div>
            <ol className="timeline">
              {detail.revisions.map((row) => (
                <RevisionRow key={row.id} row={row} />
              ))}
            </ol>
          </>
        )}
      </div>

      {/* 附件 —— 每个导出都带着它被生成时的视图状态。 */}
      <div className="stack-sm">
        <h3 className="section-title">{t('editions.artifacts', '附件')}</h3>
        {detail.artifacts.length === 0 ? (
          <p className="card__hint">{t('editions.noArtifacts', '这一版没有附件。')}</p>
        ) : (
          <ul className="plain-list">
            {detail.artifacts.map((artifact) => (
              <ArtifactItem
                key={artifact.id}
                artifact={artifact}
                editionKey={edition.edition_key}
              />
            ))}
          </ul>
        )}
      </div>

      {/* 版次数据 —— 分享视角下由服务端删除字段，这里显示的就是收到的全部内容。 */}
      <div className="stack-sm">
        <h3 className="section-title">{t('editions.payload', '版次数据')}</h3>
        {detail.payload === null ? (
          <p className="card__hint">
            {t('editions.noPayload', '这一版没有留存快照数据，只有附件。')}
          </p>
        ) : (
          <details className="data-table-toggle">
            <summary>{t('editions.viewPayload', '查看这一版冻结的数据')}</summary>
            <pre className="payload-json">
              {JSON.stringify(detail.payload, null, 2)}
            </pre>
          </details>
        )}
      </div>

      {/* 操作 */}
      <div className="stack-sm">
        <h3 className="section-title">{t('editions.actions', '操作')}</h3>
        {!writable ? (
          <p className="card__hint">
            {t(
              'editions.needIdentity',
              '当前身份为只读或未设置。修订与分享都会记名写入审计，请先在右上角设置身份。',
            )}
          </p>
        ) : (
          <div className="chip-row">
            <button
              type="button"
              className={form === 'revise' ? 'chip chip--active' : 'chip'}
              disabled={!mayRevise || edition.kind === 'live'}
              title={
                mayRevise
                  ? undefined
                  : t('editions.reviseRole', '发起修订需要业务负责人或管理员身份')
              }
              onClick={() => setForm(form === 'revise' ? null : 'revise')}
            >
              {t('editions.revise', '发起修订')}
            </button>
            <button
              type="button"
              className={form === 'share' ? 'chip chip--active' : 'chip'}
              disabled={edition.kind === 'live'}
              title={
                edition.kind === 'live'
                  ? t('editions.shareLiveHint', 'Live 版不可引用，请分享冻结版')
                  : undefined
              }
              onClick={() => setForm(form === 'share' ? null : 'share')}
            >
              <Link2 size={13} aria-hidden />
              {t('editions.share', '生成分享链接')}
            </button>
          </div>
        )}

        {form === 'revise' ? (
          <div className="action-form stack-sm">
            <label className="field">
              <span className="field__label">
                {t(
                  'editions.reviseReason',
                  '修订原因：哪个数字变了、为什么变、引用过旧值的人该怎么办',
                )}
              </span>
              <textarea
                className="field__input"
                rows={3}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <p className="card__hint">
              {t(
                'editions.reviseHint',
                '原版本不会被改写，而是标记为「已被取代」并保持可读；差异会逐字段列出。',
              )}
            </p>
            {failure ? <p className="form-error">{failure}</p> : null}
            <div className="row-between">
              <button type="button" className="chip" onClick={() => setForm(null)}>
                {t('common.cancel', '取消')}
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={pending || reason.trim().length === 0}
                onClick={() => void revise()}
              >
                {pending ? (
                  <Loader2 size={14} className="spin" aria-hidden />
                ) : (
                  <CircleCheck size={14} aria-hidden />
                )}
                {t('editions.reviseSubmit', '写入新修订')}
              </button>
            </div>
          </div>
        ) : null}

        {form === 'share' ? (
          <div className="action-form stack-sm">
            <label className="field">
              <span className="field__label">
                {t('editions.shareExpires', '过期时间（留空为不过期）')}
              </span>
              <input
                className="field__input"
                type="datetime-local"
                value={expires}
                onChange={(event) => setExpires(event.target.value)}
              />
            </label>
            {failure ? <p className="form-error">{failure}</p> : null}
            <div className="row-between">
              <button type="button" className="chip" onClick={() => setForm(null)}>
                {t('common.cancel', '取消')}
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={pending}
                onClick={() => void createShare()}
              >
                {pending ? (
                  <Loader2 size={14} className="spin" aria-hidden />
                ) : (
                  <Link2 size={14} aria-hidden />
                )}
                {t('editions.shareSubmit', '生成链接')}
              </button>
            </div>

            {share ? (
              <div className="evidence-card stack-sm">
                <code className="share-link">{share.href}</code>
                <p className="card__hint">
                  {t('editions.shareRevision', '指向修订')} r{share.revision} ·{' '}
                  {share.expires_at
                    ? `${t('editions.shareExpiresAt', '过期于')} ${formatTimestamp(share.expires_at)}`
                    : t('editions.shareNoExpiry', '不过期')}
                </p>
                <div className="counter-evidence">
                  <span className="counter-evidence__title">
                    {t('editions.redacted', '收件人看不到这些字段')}
                  </span>
                  <div className="chip-row">
                    {share.redacted_fields.map((field) => (
                      <span key={field} className="chip chip--static">
                        {field}
                      </span>
                    ))}
                  </div>
                  <p className="card__hint">
                    {t(
                      'editions.redactedHint',
                      '删除发生在服务端：这些字段根本不会出现在对方的响应里，不是在前端藏起来。',
                    )}
                  </p>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {failure && !form ? <p className="form-error">{failure}</p> : null}
      </div>
    </section>
  );
}

function RevisionRow({ row }: { row: EditionRevisionRow }) {
  const { t } = useI18n();
  return (
    <li className="timeline__row">
      <span className="timeline__time">{formatTimestamp(row.created_at)}</span>
      <span className="timeline__body">
        <strong>r{row.revision}</strong>
        <span className="card__hint">
          {row.created_by ?? t('editions.system', '系统')}
        </span>
        <span className="timeline__note">{row.reason}</span>
        {row.diff.length ? (
          <table className="diff-table">
            <thead>
              <tr>
                <th>{t('editions.diffField', '字段')}</th>
                <th>{t('editions.diffBefore', '原值')}</th>
                <th>{t('editions.diffAfter', '新值')}</th>
              </tr>
            </thead>
            <tbody>
              {row.diff.map((entry) => (
                <tr key={entry.path}>
                  <td>{entry.path}</td>
                  <td className="numeric">{String(entry.before ?? '—')}</td>
                  <td className="numeric">{String(entry.after ?? '—')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <span className="card__hint">
            {t('editions.noDiff', '没有记录字段级差异。')}
          </span>
        )}
      </span>
    </li>
  );
}

function ArtifactItem({
  artifact,
  editionKey,
}: {
  artifact: ArtifactRow;
  editionKey: string;
}) {
  const { t } = useI18n();
  const state = Object.entries(artifact.view_state);

  return (
    <li className="stack-xs">
      <span className="row-between">
        <a className="link-button" href={api.artifactUrl(editionKey, artifact.id)}>
          <Download size={13} aria-hidden />
          {artifact.filename}
        </a>
        <span className="card__hint">
          {artifact.artifact_kind} · {formatBytes(artifact.byte_size)} ·{' '}
          {formatTimestamp(artifact.created_at)}
        </span>
      </span>
      {state.length ? (
        <span className="chip-row">
          {/* The exact filters, window, scope and timezone this file was taken under —
              without them the export cannot be reproduced, only re-guessed. */}
          {state.map(([key, value]) => (
            <span key={key} className="chip chip--static">
              {key}: {String(value)}
            </span>
          ))}
        </span>
      ) : (
        <span className="card__hint">
          {t(
            'editions.noViewState',
            '这个附件没有记录视图状态，无法完全复现它的取数条件。',
          )}
        </span>
      )}
    </li>
  );
}

/* --- review --------------------------------------------------------------- */

function ReviewTab({ days, onDays }: { days: string; onDays: (days: string) => void }) {
  const { t } = useI18n();
  const label = useLabels();
  const navigate = useNavigate();
  const review = useApi(
    (signal) => api.retrospective({ days: Number(days) || 30 }, signal),
    [days],
  );

  const rows = useMemo<RetrospectiveRow[]>(() => review.data?.rows ?? [], [review.data]);
  const detectors = useMemo<DetectorReviewRow[]>(
    () => review.data?.detectors ?? [],
    [review.data],
  );

  const alertColumns: ColumnsType<RetrospectiveRow> = [
    {
      title: t('review.alert', '告警'),
      key: 'headline',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>{row.headline_zh}</span>
          <span className="card__hint">
            {row.entity_name ?? row.entity_id} · {row.detector} ·{' '}
            {label.family(row.family)}
          </span>
        </span>
      ),
    },
    {
      title: t('common.severity', '级别'),
      dataIndex: 'severity',
      key: 'severity',
      width: 90,
      render: (value: RetrospectiveRow['severity']) => (
        <span className={`sev sev--${value}`}>{label.severity(value)}</span>
      ),
    },
    {
      title: t('review.finalState', '最终状态'),
      key: 'final_state',
      width: 120,
      render: (_value, row) => (
        <span className={`state-tag state-tag--${row.final_state}`}>
          {label.alertState(row.final_state)}
        </span>
      ),
    },
    {
      title: t('review.reason', '结论'),
      key: 'reason',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>
            {row.reason_code
              ? label.from('enum.closeReason', row.reason_code, REASON_LABEL)
              : t('review.noReason', '无标准结论')}
          </span>
          {row.outcome_note ? (
            <span className="card__hint">{row.outcome_note}</span>
          ) : null}
        </span>
      ),
    },
    {
      title: t('review.owner', 'owner'),
      key: 'owner',
      width: 110,
      render: (_value, row) =>
        row.owner_id ? (
          `@${row.owner_id}`
        ) : (
          <span className="muted">{t('review.unowned', '无')}</span>
        ),
    },
    {
      title: (
        <Tooltip
          title={t(
            'review.ttcHint',
            '从发布到结案的时长。SLA 判定按发布时的级别，不是按事后回看的重要性。',
          )}
        >
          <span>{t('review.ttc', '处理时长')}</span>
        </Tooltip>
      ),
      key: 'ttc',
      width: 130,
      align: 'right',
      className: 'numeric',
      render: (_value, row) => (
        <span>
          {hoursText(row.time_to_close_hours)}
          {row.within_sla === false ? (
            <span className="state-tag state-tag--overdue">
              {t('review.breached', '超时')}
            </span>
          ) : null}
        </span>
      ),
    },
  ];

  const detectorColumns: ColumnsType<DetectorReviewRow> = [
    {
      title: t('review.detector', '检测器'),
      key: 'detector',
      render: (_value, row) => (
        <span className="stack-xs">
          <span>{row.detector}</span>
          <span className="card__hint">{label.family(row.family)}</span>
        </span>
      ),
    },
    {
      title: t('review.published', '已发布'),
      dataIndex: 'published',
      key: 'published',
      width: 90,
      align: 'right',
      className: 'numeric',
      render: (value: number) => formatCount(value),
    },
    {
      title: t('review.actioned', '已行动'),
      dataIndex: 'actioned',
      key: 'actioned',
      width: 90,
      align: 'right',
      className: 'numeric',
      render: (value: number) => formatCount(value),
    },
    {
      title: t('review.falsePositive', '误报'),
      dataIndex: 'false_positive',
      key: 'false_positive',
      width: 80,
      align: 'right',
      className: 'numeric',
      render: (value: number) => formatCount(value),
    },
    {
      title: (
        <Tooltip
          title={t(
            'review.fprHint',
            '误报率的分母是已关闭的告警。还没有任何告警关闭时显示「—」，而不是 0%——零分之零不是零。',
          )}
        >
          <span>{t('review.fpr', '误报率')}</span>
        </Tooltip>
      ),
      dataIndex: 'false_positive_rate',
      key: 'false_positive_rate',
      width: 100,
      align: 'right',
      className: 'numeric',
      render: (value: number | null) =>
        value === null ? <span className="muted">—</span> : formatPercent(value),
    },
    {
      title: t('review.medianTtc', '中位处理时长'),
      dataIndex: 'median_time_to_close_hours',
      key: 'median_ttc',
      width: 130,
      align: 'right',
      className: 'numeric',
      render: (value: number | null) => hoursText(value),
    },
    {
      title: (
        <Tooltip
          title={t(
            'review.counterHint',
            '这个检测器最常见的反对理由。反复出现同一条，说明该把它写进阈值或前置过滤，而不是每次让人再判断一遍。',
          )}
        >
          <span>{t('review.counter', '最常见反证')}</span>
        </Tooltip>
      ),
      dataIndex: 'top_counter_evidence',
      key: 'counter',
      render: (value: string | null) =>
        value ?? <span className="muted">{t('review.noCounter', '无')}</span>,
    },
  ];

  return (
    <>
      <section className="card filter-bar stack-sm">
        <div className="filter-group">
          <span className="filter-group__label">{t('review.window', '回看窗口')}</span>
          <div
            className="chip-row"
            role="group"
            aria-label={t('review.window', '回看窗口')}
          >
            {REVIEW_WINDOWS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={entry.id === days ? 'chip chip--active' : 'chip'}
                aria-pressed={entry.id === days}
                onClick={() => onDays(entry.id)}
              >
                {entry.label}
              </button>
            ))}
          </div>
        </div>
        <span className="card__hint">
          {t(
            'review.hint',
            '复盘只看已关闭的告警：还在处理中的不构成对检测器的判断。',
          )}
        </span>
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('review.detectors', '检测器阈值回顾')}</h2>
          <span className="card__hint">
            {t(
              'review.detectorsHint',
              '误报率高、或反复被同一条反证驳回的检测器，是该调阈值的地方。调整需要人来决定，这张表不会自动改任何参数。',
            )}
          </span>
        </div>
        {review.loading ? (
          <TableSkeleton rows={5} />
        ) : review.error ? (
          <ErrorState error={review.error} onRetry={review.reload} />
        ) : detectors.length === 0 ? (
          <EmptyState title={t('review.noDetectors', '这个窗口里没有检测器发过告警。')} />
        ) : (
          <Table<DetectorReviewRow>
            rowKey="detector"
            size="middle"
            pagination={false}
            columns={detectorColumns}
            dataSource={detectors}
          />
        )}
      </section>

      <section className="card stack-md">
        <div className="card__head">
          <h2 className="card__title">{t('review.alerts', '告警复盘')}</h2>
          <span className="card__hint">
            {t('review.alertsHint', '点击一行回到该告警的完整证据与时间线。')}
          </span>
        </div>
        {review.loading ? (
          <TableSkeleton />
        ) : review.error ? (
          <ErrorState error={review.error} onRetry={review.reload} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t('review.noAlerts', '这个窗口里没有已关闭的告警。')}
            hint={t('review.noAlertsHint', '未关闭的告警在异常雷达里，不在复盘里。')}
          />
        ) : (
          <Table<RetrospectiveRow>
            rowKey="alert_id"
            size="small"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={alertColumns}
            dataSource={rows}
            onRow={(row) => ({
              onClick: () => navigate(`/alerts/${row.alert_id}`),
              style: { cursor: 'pointer' },
            })}
          />
        )}
      </section>
    </>
  );
}

/* --- generated files ------------------------------------------------------ */

function FilesTab() {
  const { t } = useI18n();
  const label = useLabels();
  const reports = useApi((signal) => api.reports(signal), []);
  const [generating, setGenerating] = useState(false);
  const [failure, setFailure] = useState<Error | null>(null);

  const generate = async () => {
    setGenerating(true);
    setFailure(null);
    try {
      await api.generateReports();
      reports.reload();
    } catch (cause) {
      setFailure(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setGenerating(false);
    }
  };

  const columns: ColumnsType<ReportRow> = [
    {
      title: t('reports.date', '报告日期'),
      dataIndex: 'report_date',
      key: 'report_date',
      render: (value: string) => formatDate(value),
    },
    {
      title: t('reports.format', '格式'),
      dataIndex: 'report_format',
      key: 'report_format',
      width: 120,
      render: (value: string) => (
        <span className="chip chip--static">
          {value === 'xlsx' ? (
            <FileSpreadsheet size={14} aria-hidden />
          ) : (
            <FileText size={14} aria-hidden />
          )}
          {value.toUpperCase()}
        </span>
      ),
    },
    { title: t('reports.filename', '文件名'), dataIndex: 'filename', key: 'filename' },
    {
      title: t('reports.size', '大小'),
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      width: 110,
      align: 'right',
      className: 'numeric',
      render: (value: number | null) => formatBytes(value),
    },
    {
      title: t('reports.snapshot', '快照时间'),
      dataIndex: 'snapshot_ts',
      key: 'snapshot_ts',
      render: (value: string | null) => formatTimestamp(value),
    },
    {
      title: t('reports.storage', '存储位置'),
      dataIndex: 'storage',
      key: 'storage',
      width: 140,
      render: (value: string) =>
        label.from('reports.storage', value, STORAGE_LABEL),
    },
    {
      title: t('reports.download', '下载'),
      key: 'download',
      width: 110,
      render: (_value, row) => (
        <a
          className="chip"
          href={api.reportUrl(
            row.report_date,
            row.report_format === 'xlsx' ? 'excel' : 'word',
          )}
        >
          <Download size={14} aria-hidden />
          {t('reports.download', '下载')}
        </a>
      ),
    },
  ];

  return (
    <section className="card stack-md">
      <div className="card__head">
        <h2 className="card__title">{t('reports.table', '已生成的报告')}</h2>
        <button type="button" className="chip" onClick={generate} disabled={generating}>
          {generating ? (
            <Loader2 size={14} className="spin" aria-hidden />
          ) : (
            <RefreshCw size={14} aria-hidden />
          )}
          {generating
            ? t('reports.generating', '生成中…')
            : t('reports.generate', '立即生成')}
        </button>
      </div>
      <p className="card__hint">
        {t(
          'reports.subtitleFiles',
          '每日工作簿（xlsx）与简报（docx）由同一次读取生成，因此两者不会互相矛盾。文件存在数据库或对象存储里，不落容器磁盘。',
        )}
      </p>

      {failure ? <ErrorState error={failure} onRetry={generate} /> : null}

      {reports.loading ? (
        <TableSkeleton />
      ) : reports.error ? (
        <ErrorState error={reports.error} onRetry={reports.reload} />
      ) : (reports.data?.rows.length ?? 0) === 0 ? (
        <EmptyState
          title={t('reports.none', '还没有生成过报告。')}
          hint={t('reports.noneHint', '调度任务会在每日收盘后生成，也可以现在手动生成。')}
        />
      ) : (
        <Table<ReportRow>
          rowKey="id"
          size="middle"
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          columns={columns}
          dataSource={reports.data?.rows ?? []}
        />
      )}
    </section>
  );
}
