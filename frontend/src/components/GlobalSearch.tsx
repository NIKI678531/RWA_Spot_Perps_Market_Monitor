/**
 * Unified search, in the top bar (GEN-002).
 *
 * It sits in the chrome rather than in the middle of the home page, which is where
 * v2.0 put it. Search is a tool, not the product's claim: the first screen should be
 * telling the reader what changed, not asking them what they want.
 *
 * The destination comes from the server (`SearchHit.href`), never from a rule in the
 * browser. Wrappers, pairs, pools and contracts all resolve to their Underlying 360
 * carrying themselves as filter context, and that mapping lives in `api/naming.py`
 * so the queue, the digest and this box all land in the same place.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Search, X } from 'lucide-react';

import { api } from '@/api/client';
import type { SearchHit } from '@/api/types';
import { useI18n } from '@/i18n';

/** Long enough that typing a symbol does not fire five requests, short enough to feel live. */
const DEBOUNCE_MS = 220;
const LIMIT = 12;

export function GlobalSearch() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setHits([]);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      api
        .search({ q: trimmed, limit: LIMIT }, controller.signal)
        .then((result) => {
          setHits(result.hits);
          setCursor(0);
          setLoading(false);
          setOpen(true);
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setHits([]);
            setLoading(false);
          }
        });
    }, DEBOUNCE_MS);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);

  // `/` focuses the box from anywhere, the way every monitor its readers already use
  // behaves. Ignored while they are typing into something else.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);
      if (event.key === '/' && !typing) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, []);

  const go = useCallback(
    (hit: SearchHit) => {
      setOpen(false);
      setQuery('');
      navigate(hit.href);
    },
    [navigate],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Escape') {
        setOpen(false);
        inputRef.current?.blur();
        return;
      }
      if (!hits.length) return;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setCursor((c) => (c + 1) % hits.length);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setCursor((c) => (c - 1 + hits.length) % hits.length);
      } else if (event.key === 'Enter') {
        event.preventDefault();
        const hit = hits[cursor];
        if (hit) go(hit);
      }
    },
    [hits, cursor, go],
  );

  // Grouped by kind so a reader scanning for a venue is not reading past nine
  // wrappers to find it. Order follows first appearance, which is server-ranked.
  const groups = useMemo(() => {
    const out: Array<{ label: string; items: SearchHit[] }> = [];
    for (const hit of hits) {
      const group = out.find((g) => g.label === hit.kind_label);
      if (group) group.items.push(hit);
      else out.push({ label: hit.kind_label, items: [hit] });
    }
    return out;
  }, [hits]);

  return (
    <div className="topsearch" ref={boxRef}>
      <label className="topsearch__field">
        <Search size={15} aria-hidden />
        <input
          ref={inputRef}
          type="search"
          value={query}
          placeholder={t('search.placeholder', '搜索底层、包装、发行商、场所、合约…')}
          aria-label={t('search.label', '统一搜索')}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          onKeyDown={onKeyDown}
        />
        {loading ? <Loader2 size={14} className="spin" aria-hidden /> : null}
        {query && !loading ? (
          <button
            type="button"
            className="topsearch__clear"
            onClick={() => {
              setQuery('');
              inputRef.current?.focus();
            }}
            aria-label={t('search.clear', '清空')}
          >
            <X size={14} aria-hidden />
          </button>
        ) : null}
      </label>

      {open && query.trim() ? (
        <div className="topsearch__panel" role="listbox">
          {!hits.length && !loading ? (
            <p className="topsearch__empty">
              {t('search.noHits', '没有匹配的实体。')}
            </p>
          ) : null}
          {groups.map((group) => (
            <div key={group.label} className="topsearch__group">
              <p className="topsearch__group-label">{group.label}</p>
              {group.items.map((hit) => {
                const index = hits.indexOf(hit);
                return (
                  <button
                    key={`${hit.entity_type}:${hit.entity_id}`}
                    type="button"
                    role="option"
                    aria-selected={index === cursor}
                    className={
                      index === cursor
                        ? 'topsearch__hit topsearch__hit--active'
                        : 'topsearch__hit'
                    }
                    onMouseEnter={() => setCursor(index)}
                    onClick={() => go(hit)}
                  >
                    <span className="topsearch__hit-name">{hit.name}</span>
                    {hit.detail ? (
                      <span className="topsearch__hit-detail">{hit.detail}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
