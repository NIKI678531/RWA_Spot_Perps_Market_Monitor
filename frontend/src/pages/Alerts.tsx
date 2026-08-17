/**
 * The anomaly radar — the page this whole system exists for.
 *
 * The default view is open alerts only, ranked by score. Every row expands into its
 * evidence; nothing here is asserted without the baseline it was judged against.
 */

import { useState } from 'react';

import { api } from '@/api/client';
import type { AlertSeverity, AlertStatus } from '@/api/types';
import { AlertFeed } from '@/components/AlertFeed';
import { useApi } from '@/hooks/useApi';
import { useI18n, type Translate } from '@/i18n';

const SEVERITIES: ReadonlyArray<{ id: AlertSeverity | 'all'; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'critical', label: '严重' },
  { id: 'high', label: '高' },
  { id: 'medium', label: '中' },
  { id: 'low', label: '低' },
];

const STATUSES: ReadonlyArray<{ id: AlertStatus | 'all'; label: string }> = [
  { id: 'all', label: '全部状态' },
  { id: 'confirmed', label: '已确认' },
  { id: 'tentative', label: '待确认' },
];

/** The chips share their keys with the labels the feed itself renders. */
const chipLabel = (t: Translate, prefix: string, id: string, fallback: string) =>
  t(`${prefix}.${id}`, fallback);

export function Alerts() {
  const { t } = useI18n();
  const [severity, setSeverity] = useState<AlertSeverity | 'all'>('all');
  const [status, setStatus] = useState<AlertStatus | 'all'>('all');

  const alerts = useApi(
    (signal) =>
      api.alerts(
        {
          severity: severity === 'all' ? undefined : severity,
          status: status === 'all' ? undefined : status,
          limit: 100,
        },
        signal,
      ),
    [severity, status],
  );

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('alerts.title', '异常雷达')}</h1>
          <p className="card__hint">
            {t(
              'alerts.subtitle',
              '需求异常：以前没人买的产品，突然有人买了。基线按日类型分层，中位数与 MAD 计算，低于 5 万美元的变化不报警。',
            )}
          </p>
        </div>
      </div>

      <div className="chip-row" role="group" aria-label={t('alerts.severity', '级别')}>
        {SEVERITIES.map((item) => (
          <button
            key={item.id}
            type="button"
            className={item.id === severity ? 'chip chip--active' : 'chip'}
            onClick={() => setSeverity(item.id)}
            aria-pressed={item.id === severity}
          >
            {chipLabel(t, 'severity', item.id, item.label)}
          </button>
        ))}
        <span style={{ width: 'var(--spacing-md)' }} />
        {STATUSES.map((item) => (
          <button
            key={item.id}
            type="button"
            className={item.id === status ? 'chip chip--active' : 'chip'}
            onClick={() => setStatus(item.id)}
            aria-pressed={item.id === status}
          >
            {chipLabel(t, 'status', item.id, item.label)}
          </button>
        ))}
      </div>

      <AlertFeed
        title={t('alerts.feed', '告警列表')}
        rows={alerts.data?.rows ?? []}
        loading={alerts.loading}
        error={alerts.error}
        onRetry={alerts.reload}
      />

      {alerts.data?.meta.note ? (
        <p className="card__hint">{alerts.data.meta.note}</p>
      ) : null}
    </div>
  );
}
