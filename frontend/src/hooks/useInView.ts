/**
 * Latches true the first time an element is scrolled into view.
 *
 * Charts animate as they mount, and on these pages most charts mount below the fold —
 * so the entrance plays out against an empty screen and is finished before the reader
 * arrives. Gating the mount on visibility moves the motion to the moment it can
 * actually be read, which is the difference between motion as causality
 * (DESIGN.md principle 5) and motion as decoration.
 *
 * It latches on purpose. Replaying the entrance every time a chart scrolls past would
 * make the animation mean "you moved" rather than "this data arrived".
 */

import { useEffect, useRef, useState, type RefObject } from 'react';

/** Fires once the element is 15% inside the viewport, not merely touching its edge. */
const ROOT_MARGIN = '0px 0px -15% 0px';

export function useInView<T extends HTMLElement>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    if (seen) return;
    const node = ref.current;
    if (!node) return;

    // Without the observer there is no way to know when the chart is read, so it
    // renders immediately: a chart that never appears is worse than one that
    // animated too early.
    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin: ROOT_MARGIN },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [seen]);

  return [ref, seen];
}
