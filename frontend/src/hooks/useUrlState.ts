/**
 * View state lives in the URL (rule 23).
 *
 * Scope, edition, as-of, window, filters, sort and raw/adjusted are all query
 * parameters rather than component state, for one reason: a screenshot pasted into a
 * deck has to be reproducible six weeks later, and so does the export taken beside
 * it. State held in `useState` is state nobody else can get back to.
 *
 * Values equal to their default are dropped from the URL. A link then carries only
 * what was deliberately changed, which is what makes it readable and what stops two
 * links to the same view from looking different.
 */

import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

export type UrlDefaults = Record<string, string>;

export interface UrlState<T extends UrlDefaults> {
  /** Defaults filled in, so a caller never handles `undefined`. */
  state: T;
  /** Merge a patch. `''` resets that key to its default and drops it from the URL. */
  setState: (patch: Partial<Record<keyof T, string>>) => void;
  /** Back to the defaults, in one history entry. */
  reset: () => void;
  /** The parameters actually present — what an export has to inherit verbatim. */
  active: Record<string, string>;
}

export function useUrlState<T extends UrlDefaults>(defaults: T): UrlState<T> {
  const [params, setParams] = useSearchParams();

  // `params.toString()` rather than `params` in the dependency list: URLSearchParams
  // is a fresh object on every render even when nothing changed, and keying on the
  // object identity would rebuild the state on every keystroke elsewhere on the page.
  const search = params.toString();

  const state = useMemo(() => {
    const current = new URLSearchParams(search);
    const out = { ...defaults } as T;
    for (const key of Object.keys(defaults)) {
      const value = current.get(key);
      if (value !== null && value !== '') (out as UrlDefaults)[key] = value;
    }
    return out;
  }, [search, defaults]);

  const active = useMemo(() => {
    const current = new URLSearchParams(search);
    const out: Record<string, string> = {};
    for (const [key, value] of current.entries()) {
      if (value !== '' && value !== defaults[key]) out[key] = value;
    }
    return out;
  }, [search, defaults]);

  const setState = useCallback(
    (patch: Partial<Record<keyof T, string>>) => {
      const next = new URLSearchParams(search);
      for (const [key, value] of Object.entries(patch)) {
        if (value === undefined) continue;
        if (value === '' || value === defaults[key]) next.delete(key);
        else next.set(key, value);
      }
      // `replace`: changing a filter is a refinement of where you are, not a new
      // place. Otherwise Back walks a reader through every chip they toggled.
      setParams(next, { replace: true });
    },
    [search, defaults, setParams],
  );

  const reset = useCallback(() => setParams(new URLSearchParams()), [setParams]);

  return { state, setState, reset, active };
}

/**
 * The data cut-off every read on the page is taken at, or `undefined` for Live.
 *
 * Separate from `useUrlState` because it is not a page filter: it is set in the chrome
 * by the edition picker and consumed by every page, so a page that took it through its
 * own defaults map would be able to accidentally drop it and read Live while the
 * status bar above said 17:00.
 */
export function useAsOf(): string | undefined {
  const [params] = useSearchParams();
  return params.get('as_of') ?? undefined;
}

/**
 * The view state an export has to inherit: the page's own filters plus the two things
 * that make a figure re-readable later — the timezone it was displayed in and the
 * data cut-off it was taken at.
 */
export function exportViewState(
  active: Record<string, string>,
  asOf: string | null | undefined,
): Record<string, string> {
  return {
    ...active,
    ...(asOf ? { as_of: asOf } : {}),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Hong_Kong',
  };
}
