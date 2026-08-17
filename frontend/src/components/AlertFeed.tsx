/**
 * The anomaly feed, shared by the overview and the radar page.
 *
 * Every row expands into the evidence that produced it — observed value, baseline,
 * MAD, sample size, day type, rule name — because an alert that cannot be justified
 * to management is noise, and once one alert is dismissed as noise the next one is
 * too. Severity is carried by a 3px bar and a text label, never by colour alone.
 */

import { useState } from 'react';

import { api } from '@/api/client';
import type { AlertRow, AlertSeverity, EvidenceRow } from '@/api/types';
import { scopeLabel, SESSION_LABEL } from '@/api/types';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { formatCount, formatTimestamp, formatUsd } from '@/utils/format';
import { EmptyState, ErrorState, TableSkeleton } from './states';

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

const STATUS_LABEL: Record<string, string> = {
  tentative: '待确认',
  confirmed: '已确认',
  expired: '已过期',
  suppressed: '已抑制',
};

function Evidence({ alertId }: { alertId: number }) {
  const { t } = useI18n();
  const detail = useApi((signal) => api.alert(alertId, signal), [alertId]);

  if (detail.loading) return <TableSkeleton rows={2} />;
  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.reload} />;

  const rows: EvidenceRow[] = detail.data?.evidence ?? [];
  if (rows.length === 0) {
    return (
      <p className="card__hint">
        {t('alerts.noEvidence', '这条告警没有写入证据行，按约定不应展示给管理层。')}
      </p>
    );
  }

  return (
    <>
      {rows.map((row) => (
        <div
          className="alert-item__evidence"
          key={`${row.rule_name}-${row.snapshot_ts}`}
        >
          <div className="evidence-cell">
            <span>{t('alerts.rule', '规则')}</span>
            <span>{row.rule_name}</span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.observed', '观测值')}</span>
            <span className="numeric">{formatUsd(row.observed_value)}</span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.baseline', '基线中位数')}</span>
            <span className="numeric">{formatUsd(row.baseline_median)}</span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.mad', 'MAD')}</span>
            <span className="numeric">{formatUsd(row.baseline_mad)}</span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.z', '稳健 Z')}</span>
            <span className="numeric">
              {row.robust_z === null ? '—' : row.robust_z.toFixed(2)}
            </span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.sample', '样本量')}</span>
            <span className="numeric">{formatCount(row.sample_size)}</span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.session', '日类型')}</span>
            <span>
              {t(`session.${row.market_session}`, SESSION_LABEL[row.market_session])}
            </span>
          </div>
          <div className="evidence-cell">
            <span>{t('alerts.snapshot', '快照时间')}</span>
            <span>{formatTimestamp(row.snapshot_ts)}</span>
          </div>
        </div>
      ))}
    </>
  );
}

function AlertItem({ row }: { row: AlertRow }) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(false);

  return (
    <div className={`alert-item alert-item--${row.severity}`}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        style={{
          border: 0,
          background: 'transparent',
          padding: 0,
          textAlign: 'left',
          cursor: 'pointer',
          font: 'inherit',
          color: 'inherit',
        }}
      >
        <div className="alert-item__head">
          <span>{t(`severity.${row.severity}`, SEVERITY_LABEL[row.severity])}</span>
          <span aria-hidden>·</span>
          <span>
            {t(`status.${row.status}`, STATUS_LABEL[row.status] ?? row.status)}
          </span>
          <span aria-hidden>·</span>
          <span>{row.detector}</span>
          <span aria-hidden>·</span>
          <span>{formatTimestamp(row.last_seen_ts)}</span>
        </div>
        <div className="alert-item__headline">{row.headline_zh}</div>
        <div className="alert-item__head">
          <span>{scopeLabel(row.metric_scope, locale)}</span>
          <span aria-hidden>·</span>
          <span>
            {t(`session.${row.market_session}`, SESSION_LABEL[row.market_session])}
          </span>
          <span aria-hidden>·</span>
          <span>
            {t('alerts.occurrences', '出现次数')} {formatCount(row.occurrence_count)}
          </span>
        </div>
      </button>

      {open ? <Evidence alertId={row.id} /> : null}
    </div>
  );
}

export interface AlertFeedProps {
  rows: AlertRow[];
  loading: boolean;
  error: Error | null;
  onRetry?: () => void;
  title?: string;
}

export function AlertFeed({ rows, loading, error, onRetry, title }: AlertFeedProps) {
  const { t } = useI18n();

  return (
    <section className="card stack-md">
      <h2 className="section-title">
        {title ?? t('overview.alertFeed', '异常告警流')}
      </h2>

      {loading ? (
        <TableSkeleton rows={4} />
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={t('overview.noAlerts', '当前没有未处理的告警。')}
          hint={t('overview.noAlertsHint', '检测器已运行，没有超过绝对金额门槛的项。')}
        />
      ) : (
        <div className="stack-sm">
          {rows.map((row) => (
            <AlertItem key={row.id} row={row} />
          ))}
        </div>
      )}
    </section>
  );
}
