/**
 * A minimal fetch-on-mount hook.
 *
 * The four states it exposes are the four UI-LAYOUT.md §4 requires every data region
 * to implement — loading, data, empty, not-verified — with the fourth deferred to
 * the component, because only the component knows which of its figures were missing.
 * `error` is kept distinct from empty for the same reason a failed fetch is kept
 * distinct from a zero: they are different facts about the world.
 */

import { useCallback, useEffect, useState } from 'react';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);

  // The fetcher is a fresh closure on every render, so the effect keys on the
  // caller's deps instead. That is the same contract useEffect itself has.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fetcher, deps);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setError(null);

    run(controller.signal)
      .then((result) => {
        if (!active) return;
        setData(result);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
        setLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [run, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, loading, error, reload };
}
