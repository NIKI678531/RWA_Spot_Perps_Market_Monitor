/**
 * Which edition, and therefore which numbers, the whole app is reading.
 *
 * Picking a frozen edition sets `as_of` on the URL, which every read endpoint honours
 * by reading each fact table at its own latest snapshot at or before that instant.
 * That is what makes "the 17:00 figure" reproducible from any page rather than only
 * from the report — and it is why the control lives in the chrome: an edition that
 * applied to one page would be a filter, not a citation.
 *
 * Live is the default and carries no parameter at all, so the plain URL is always the
 * current view. The `as_of` value is the edition's data cut-off, never its generation
 * time; the two are routinely hours apart and conflating them is how a report gets
 * quoted as being newer than the data behind it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CalendarClock, ChevronDown, Snowflake } from 'lucide-react';

import { api } from '@/api/client';
import type { EditionRow } from '@/api/types';
import { useApi } from '@/hooks/useApi';
import { useI18n } from '@/i18n';
import { useLabels } from '@/i18n/labels';
import { formatTimestamp } from '@/utils/format';

/**
 * The trailing windows a page may offer. Chosen, never free-form.
 *
 * These live beside each page's own filters rather than in the chrome, because a
 * window only means something where it changes a query — a global control that did
 * nothing on four of nine pages would teach people to distrust the other five.
 */
export const WINDOWS: ReadonlyArray<{ id: string; label: string }> = [
  { id: '24h', label: '24 小时' },
  { id: '7d', label: '7 天' },
  { id: '30d', label: '30 天' },
];

export function WindowChips({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { t } = useI18n();
  return (
    <div className="chip-row" role="group" aria-label={t('edition.window', '时间窗')}>
      {WINDOWS.map((entry) => (
        <button
          key={entry.id}
          type="button"
          className={entry.id === value ? 'chip chip--active' : 'chip'}
          aria-pressed={entry.id === value}
          onClick={() => onChange(entry.id)}
        >
          {t(`edition.windowOption.${entry.id}`, entry.label)}
        </button>
      ))}
    </div>
  );
}

export function EditionPicker() {
  const { t } = useI18n();
  const label = useLabels();
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const editions = useApi((signal) => api.editions({ limit: 12 }, signal), []);
  const asOf = params.get('as_of');
  const editionKey = params.get('edition');

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, []);

  const select = useCallback(
    (edition: EditionRow | null) => {
      const next = new URLSearchParams(params);
      if (!edition || edition.kind === 'live') {
        next.delete('as_of');
        next.delete('edition');
      } else {
        next.set('as_of', edition.as_of);
        next.set('edition', edition.edition_key);
      }
      setParams(next, { replace: true });
      setOpen(false);
    },
    [params, setParams],
  );

  const current = editionKey ?? (asOf ? formatTimestamp(asOf) : t('edition.live', 'Live'));
  const frozen = Boolean(asOf);

  return (
    <div className="edition-picker" ref={boxRef}>
      <button
        type="button"
        className={frozen ? 'chip chip--active' : 'chip'}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={t('edition.hint', '切换到某个冻结版，全站按该版本的数据截止时间读取')}
      >
        {frozen ? <Snowflake size={14} aria-hidden /> : <CalendarClock size={14} aria-hidden />}
        {current}
        <ChevronDown size={13} aria-hidden />
      </button>

      {open ? (
        <div className="edition-picker__panel" role="listbox">
          <button
            type="button"
            role="option"
            aria-selected={!frozen}
            className={!frozen ? 'edition-option edition-option--active' : 'edition-option'}
            onClick={() => select(null)}
          >
            <span className="edition-option__name">{t('edition.live', 'Live')}</span>
            <span className="edition-option__detail">
              {t('edition.liveHint', '滚动读取最新快照，不可引用')}
            </span>
          </button>

          {editions.loading ? (
            <p className="card__hint">{t('common.loading', '加载中…')}</p>
          ) : null}

          {(editions.data?.rows ?? [])
            .filter((row) => row.kind !== 'live')
            .map((row) => (
              <button
                key={`${row.edition_key}-${row.revision}`}
                type="button"
                role="option"
                aria-selected={editionKey === row.edition_key}
                className={
                  editionKey === row.edition_key
                    ? 'edition-option edition-option--active'
                    : 'edition-option'
                }
                onClick={() => select(row)}
                disabled={row.status === 'failed'}
              >
                <span className="edition-option__name">
                  {row.label}
                  {row.revision > 1 ? ` v${row.revision}` : ''}
                </span>
                <span className="edition-option__detail">
                  {label.editionStatus(row.status)} · {t('edition.asOf', '数据截止')}{' '}
                  {formatTimestamp(row.as_of)}
                </span>
              </button>
            ))}

          {!editions.loading && !(editions.data?.rows ?? []).length ? (
            <p className="card__hint">
              {t('edition.none', '还没有冻结版。09:00 与 17:00 各生成一次。')}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
