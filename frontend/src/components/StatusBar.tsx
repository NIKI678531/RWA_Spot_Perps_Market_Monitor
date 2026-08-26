/**
 * The status strip that sits on every page (GEN-001, UI-LAYOUT.md §1).
 *
 * Its job is to make "when is this from, and can I trust it" answerable without
 * leaving the screen — including on a screenshot pasted into a deck, which is the
 * case it really exists for. So it is part of the shell rather than a home-page
 * component, and it is never conditionally hidden.
 *
 * Pages publish their own bar through `usePageStatus`, because the scope list is a
 * property of what is on screen: the perps page is showing two scopes and the home
 * page five, and a bar that always said the same thing would be furniture. Until a
 * page publishes one, the shell falls back to `/status-bar`, which answers for the
 * warehouse as a whole.
 */

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { Tooltip } from 'antd';
import { CircleAlert, Clock3, Database, Layers, ShieldCheck } from 'lucide-react';

import type { StatusBar as StatusBarPayload } from '@/api/types';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatMinutes, formatTimestamp } from '@/utils/format';

/** Past this, the age reads amber. Same threshold the shell's data stamp uses. */
export const STALE_AFTER_MINUTES = 90;

interface StatusBarContextValue {
  page: StatusBarPayload | null;
  setPage: (value: StatusBarPayload | null) => void;
  /** Set by the governance surfaces when a blocking data gap is open. */
  blocked: string | null;
  setBlocked: (value: string | null) => void;
}

const StatusBarContext = createContext<StatusBarContextValue | null>(null);

export function StatusBarProvider({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<StatusBarPayload | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const value = useMemo(
    () => ({ page, setPage, blocked, setBlocked }),
    [page, blocked],
  );
  return (
    <StatusBarContext.Provider value={value}>{children}</StatusBarContext.Provider>
  );
}

function useStatusBarContext(): StatusBarContextValue {
  const value = useContext(StatusBarContext);
  if (!value) throw new Error('useStatusBar must be used inside <StatusBarProvider>');
  return value;
}

/**
 * Publish this page's status bar. Clears on unmount, so a page that fails to load
 * never leaves its predecessor's cut-off on screen — an old `as_of` beside new
 * content is exactly the misreading the bar exists to prevent.
 */
export function usePageStatus(status: StatusBarPayload | null | undefined): void {
  const { setPage } = useStatusBarContext();
  useEffect(() => {
    setPage(status ?? null);
    return () => setPage(null);
  }, [status, setPage]);
}

/** Publish a publication-blocking condition. Same lifecycle as `usePageStatus`. */
export function usePublicationBlock(reason: string | null | undefined): void {
  const { setBlocked } = useStatusBarContext();
  useEffect(() => {
    setBlocked(reason ?? null);
    return () => setBlocked(null);
  }, [reason, setBlocked]);
}

export function StatusBarStrip({ fallback }: { fallback: StatusBarPayload | null }) {
  const { t } = useI18n();
  const label = useLabels();
  const { page, blocked } = useStatusBarContext();
  const status = page ?? fallback;

  if (!status) {
    return (
      <div className="statusbar statusbar--pending" aria-live="polite">
        <span className="skeleton" style={{ height: 14, width: 260 }} />
      </div>
    );
  }

  const age = status.data_age_minutes;
  const stale = age !== null && age >= STALE_AFTER_MINUTES;
  const sourcesShort = status.sources_total > 0 && status.sources_ok < status.sources_total;

  return (
    <div className="statusbar" aria-live="polite">
      <Tooltip
        title={t(
          'status.scopeHint',
          '当前页面展示的口径。不同口径之间不可相加，只能并列。',
        )}
      >
        <span className="statusbar__item">
          <Layers size={13} aria-hidden />
          {status.scopes.length
            ? status.scopes.map((scope) => label.scope(scope)).join(' · ')
            : t('status.noScope', '无口径数据')}
        </span>
      </Tooltip>

      <span className="statusbar__sep" aria-hidden />

      <Tooltip title={t('status.editionHint', '当前引用的版本。冻结版写入后不可修改。')}>
        <span className="statusbar__item">
          <Database size={13} aria-hidden />
          {status.edition_key}
          {status.edition_revision > 1 ? ` v${status.edition_revision}` : ''}
        </span>
      </Tooltip>

      <span className="statusbar__sep" aria-hidden />

      <Tooltip
        title={`${t('status.asOfHint', '数据截止')} ${formatTimestamp(status.as_of)}${
          status.generated_at
            ? ` · ${t('status.generatedHint', '生成于')} ${formatTimestamp(status.generated_at)}`
            : ''
        }`}
      >
        <span className={`statusbar__item${stale ? ' statusbar__item--warn' : ''}`}>
          <Clock3 size={13} aria-hidden />
          {formatTimestamp(status.as_of)}
          {age !== null ? ` · ${t('status.age', '数据年龄')} ${formatMinutes(age)}` : ''}
        </span>
      </Tooltip>

      <span className="statusbar__sep" aria-hidden />

      <span className={`statusbar__item${sourcesShort ? ' statusbar__item--warn' : ''}`}>
        <ShieldCheck size={13} aria-hidden />
        {t('status.sources', '源覆盖')} {status.sources_ok}/{status.sources_total} ·{' '}
        {label.verification(status.verification_status)}
      </span>

      {status.next_refresh_at ? (
        <>
          <span className="statusbar__sep" aria-hidden />
          <span className="statusbar__item statusbar__item--muted">
            {t('status.nextRefresh', '下次刷新')} {formatTimestamp(status.next_refresh_at)}
          </span>
        </>
      ) : null}

      {blocked ? (
        <span className="statusbar__blocked" role="status">
          <CircleAlert size={13} aria-hidden />
          {blocked}
        </span>
      ) : null}
    </div>
  );
}
