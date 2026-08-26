/**
 * The current identity, as React state.
 *
 * `api/identity.ts` owns the value; this subscribes to it so that changing who you
 * are re-renders the action bars rather than leaving disabled buttons behind. Kept
 * out of a context provider deliberately: the API client reads the same store
 * directly on every request, and two sources for one fact is how a request ends up
 * attributed to the person who was signed in a moment ago.
 */

import { useEffect, useState } from 'react';

import { onIdentityChange, readIdentity, type Identity } from '@/api/identity';

export function useIdentity(): Identity {
  const [identity, setIdentity] = useState<Identity>(readIdentity);

  useEffect(() => {
    // Also picks up a change made in another tab, where the store is the same
    // localStorage key but the in-process listeners are not.
    const onStorage = () => setIdentity(readIdentity());
    window.addEventListener('storage', onStorage);
    const unsubscribe = onIdentityChange(setIdentity);
    return () => {
      window.removeEventListener('storage', onStorage);
      unsubscribe();
    };
  }, []);

  return identity;
}
