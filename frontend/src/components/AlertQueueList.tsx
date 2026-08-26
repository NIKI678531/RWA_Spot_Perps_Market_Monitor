/**
 * A compact list of queue rows, for the surfaces that show alerts without owning them
 * — the home page and Underlying 360.
 *
 * It is deliberately not the radar's table. The radar has to be worked in: filters,
 * ownership, SLA, bulk scanning. Here the reader is being told something and offered a
 * way in, so the row carries only what decides whether to open it — severity, state,
 * whose it is, whether it is late, and the family's own claim.
 *
 * `family_claim` comes from the server rather than a lookup here, because the two
 * detector families must not be described in each other's words: a cross-sectional
 * finding says "异常于同组现状", a time-series one says "相对自身历史基线升温", and a
 * client-side label map would eventually blur them.
 */

import type { QueueAlertRow } from '@/api/types';
import { EmptyState, TableSkeleton } from '@/components/states';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatAge } from '@/utils/format';

export function AlertQueueList({
  rows,
  loading = false,
  emptyHint,
  onSelect,
}: {
  rows: QueueAlertRow[];
  loading?: boolean;
  emptyHint?: string;
  onSelect: (id: number) => void;
}) {
  const { t } = useI18n();
  const label = useLabels();

  if (loading) return <TableSkeleton rows={4} />;
  if (!rows.length) {
    return (
      <EmptyState
        title={t('alerts.none', '当前没有待处理的告警。')}
        hint={emptyHint ?? t('alerts.noneHint', '这是一个真实的空，不是采集失败。')}
      />
    );
  }

  return (
    <ul className="alert-list">
      {rows.map((row) => (
        <li key={row.id}>
          <button
            type="button"
            className="alert-list__row"
            onClick={() => onSelect(row.id)}
          >
            <span className="alert-list__marks">
              <span className={`sev sev--${row.severity}`}>
                {label.severity(row.severity)}
              </span>
              <span className={`state-tag state-tag--${row.state}`}>
                {label.alertState(row.state)}
              </span>
              {row.is_overdue ? (
                <span className="state-tag state-tag--overdue">
                  {t('alerts.overdue', '超时')}
                </span>
              ) : null}
            </span>

            <span className="alert-list__headline">{row.headline_zh}</span>

            <span className="alert-list__meta">
              <span>{row.entity_name ?? row.entity_id}</span>
              <span aria-hidden>·</span>
              <span>{row.family_claim}</span>
              <span aria-hidden>·</span>
              <span>
                {row.owner_name ?? row.owner_id ?? t('alerts.unassigned', '未分派')}
              </span>
              <span aria-hidden>·</span>
              <span>{formatAge(row.last_seen_ts)}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
