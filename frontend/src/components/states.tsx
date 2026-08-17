/**
 * The four states every data region must implement (UI-LAYOUT.md §4).
 *
 * Empty and not-verified are kept apart on purpose: empty means "observed, and there
 * was nothing", not-verified means "the observation never happened". Collapsing them
 * into one grey box loses the only distinction that tells a reader whether to trust
 * the rest of the page.
 */

import type { ReactNode } from 'react';
import { CloudOff, Inbox, RefreshCw } from 'lucide-react';

import { useI18n } from '@/i18n';

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="stack-sm" aria-busy="true">
      {Array.from({ length: rows }, (_, index) => (
        <div
          className="skeleton"
          key={index}
          style={{ height: 36, animationDelay: `${Math.min(index * 60, 600)}ms` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Inbox size={28} aria-hidden />
      <p className="card__hint">{title}</p>
      {hint ? <p className="card__hint">{hint}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const { t } = useI18n();

  return (
    <div className="empty-state" role="alert">
      <CloudOff size={28} aria-hidden />
      <p className="card__hint">
        {t('common.loadFailed', '这一区块加载失败。')} {error.message}
      </p>
      {onRetry ? (
        <button type="button" className="chip" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden />
          {t('common.retry', '重试')}
        </button>
      ) : null}
    </div>
  );
}
