/**
 * Client-side mirror of the backend's `api/naming.py::href_for`.
 *
 * Most payloads already carry a resolved `href` — prefer it, because the server has
 * done the underlying lookup and knows whether the mapping exists. This helper is for
 * the rows that carry only `(entity_type, entity_id)`, such as data gaps, where there
 * is nothing to prefer. The two must agree: a link that lands somewhere different from
 * the identical link in an alert would make the same entity look like two entities.
 */

import type { EntityType } from '@/api/types';

export function entityHref(
  entityType: EntityType | null,
  entityId: string | null,
): string | null {
  if (!entityType || !entityId) return null;
  const id = encodeURIComponent(entityId);
  switch (entityType) {
    case 'underlying':
      return `/underlying/${id}`;
    case 'asset':
      return `/underlying/by-asset/${id}`;
    case 'pair':
      return `/underlying/by-pair/${id}`;
    case 'pool':
      return `/underlying/by-pool/${id}`;
    case 'perp_contract':
      return `/underlying/by-contract/${id}`;
    case 'venue':
    case 'perp_venue':
      return `/venues?venue=${id}`;
    case 'issuer':
      return `/spot-scale?issuer=${id}`;
    case 'theme':
      return `/themes?theme=${id}`;
    default:
      return `/search?q=${id}`;
  }
}
