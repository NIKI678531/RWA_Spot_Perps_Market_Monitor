/**
 * Who the browser says it is.
 *
 * Authentication belongs to the surrounding platform: the gateway sets `X-User-Id`
 * and `X-User-Role`, and the backend reads them (`app/api/deps.py:get_actor`). This
 * module is the local stand-in for that gateway — it holds the identity the user
 * picked and puts it on every request, so a claim or a close lands attributed.
 *
 * It is deliberately not a login. There is no token here and nothing is verified in
 * the browser; a deployment that puts a real gateway in front keeps working because
 * the header names are the same. What it does buy is that an action taken locally is
 * still attributable, which is the property the audit trail actually depends on.
 *
 * The default is a viewer with no id, and that is why the write surfaces are disabled
 * until someone chooses an identity: an unattributable action is worse than a blocked
 * one, and the backend refuses it anyway (403, `this action must be attributable`).
 */

export type UserRole = 'viewer' | 'analyst' | 'owner' | 'admin';

export interface Identity {
  userId: string;
  role: UserRole;
  name: string;
}

export const ROLES: ReadonlyArray<{ id: UserRole; label: string; hint: string }> = [
  { id: 'viewer', label: '只读', hint: '可查看，不能认领或关闭' },
  { id: 'analyst', label: '分析师', hint: '可认领、写备注、建任务、关闭' },
  { id: 'owner', label: '业务负责人', hint: '分析师权限，外加发行门决策' },
  { id: 'admin', label: '管理员', hint: '全部权限，含修订版与分享链接' },
];

const STORAGE_KEY = 'rwa-monitor.identity';

const ANONYMOUS: Identity = { userId: '', role: 'viewer', name: '' };

/** True when this identity may take a state-changing action. Mirrors `require_write`. */
export function canWrite(identity: Identity): boolean {
  return Boolean(identity.userId) && identity.role !== 'viewer';
}

export function readIdentity(): Identity {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) return ANONYMOUS;
    const parsed = JSON.parse(stored) as Partial<Identity>;
    if (!parsed || typeof parsed.userId !== 'string') return ANONYMOUS;
    const role = ROLES.some((r) => r.id === parsed.role)
      ? (parsed.role as UserRole)
      : 'viewer';
    return { userId: parsed.userId, role, name: parsed.name ?? parsed.userId };
  } catch {
    // A corrupt entry is not worth a broken shell; anonymous is the safe reading.
    return ANONYMOUS;
  }
}

const listeners = new Set<(identity: Identity) => void>();

export function writeIdentity(identity: Identity): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(identity));
  for (const listener of listeners) listener(identity);
}

export function clearIdentity(): void {
  window.localStorage.removeItem(STORAGE_KEY);
  for (const listener of listeners) listener(ANONYMOUS);
}

/** Subscribe to identity changes; returns the unsubscribe function. */
export function onIdentityChange(listener: (identity: Identity) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * The headers the API client attaches.
 *
 * Omitted entirely when anonymous rather than sent empty: the backend treats a
 * missing header as "no identity supplied", and an empty string would be a claim to
 * be someone with a blank name.
 */
export function identityHeaders(identity: Identity = readIdentity()): HeadersInit {
  if (!identity.userId) return {};
  return { 'X-User-Id': identity.userId, 'X-User-Role': identity.role };
}

export { ANONYMOUS };
