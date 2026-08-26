/**
 * Who you are acting as, in the top bar.
 *
 * Not a login — see `api/identity.ts`. It exists because every state-changing call
 * has to be attributable: the backend refuses a claim, a close or a gate decision
 * from a caller with no `X-User-Id`, and a UI that hides that refusal behind a
 * generic 403 would read as a broken button.
 *
 * So the identity is visible chrome, and the write surfaces read it to explain
 * themselves rather than to guess. A viewer sees the queue in full; what they do not
 * see is an action button that will fail.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, LogOut, UserRound } from 'lucide-react';

import {
  ROLES,
  clearIdentity,
  readIdentity,
  writeIdentity,
  type Identity,
  type UserRole,
} from '@/api/identity';
import { useIdentity } from '@/hooks/useIdentity';
import { useI18n } from '@/i18n';

export function IdentityMenu() {
  const { t } = useI18n();
  const identity = useIdentity();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Identity>(() => readIdentity());
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setDraft(readIdentity());
  }, [open]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, []);

  const save = useCallback(() => {
    const userId = draft.userId.trim();
    if (!userId) return;
    writeIdentity({ ...draft, userId, name: draft.name.trim() || userId });
    setOpen(false);
  }, [draft]);

  const label = identity.userId
    ? identity.name || identity.userId
    : t('identity.anonymous', '未设置身份');

  return (
    <div className="identity" ref={boxRef}>
      <button
        type="button"
        className={identity.userId ? 'chip chip--active' : 'chip'}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <UserRound size={15} aria-hidden />
        {label}
        <ChevronDown size={13} aria-hidden />
      </button>

      {open ? (
        <div className="identity__panel" role="dialog" aria-label={t('identity.title', '当前身份')}>
          <p className="card__hint">
            {t(
              'identity.hint',
              '认领、备注、关闭与发行门决策都会记名写入审计。没有身份的操作不可追溯，因此后端会直接拒绝。',
            )}
          </p>

          <label className="field">
            <span className="field__label">{t('identity.userId', '用户 ID')}</span>
            <input
              className="field__input"
              value={draft.userId}
              placeholder="lin.wei"
              onChange={(event) =>
                setDraft((d) => ({ ...d, userId: event.target.value }))
              }
            />
          </label>

          <label className="field">
            <span className="field__label">{t('identity.name', '显示名')}</span>
            <input
              className="field__input"
              value={draft.name}
              placeholder={t('identity.namePlaceholder', '留空则用 ID')}
              onChange={(event) => setDraft((d) => ({ ...d, name: event.target.value }))}
            />
          </label>

          <div className="field">
            <span className="field__label">{t('identity.role', '角色')}</span>
            <div className="chip-row">
              {ROLES.map((role) => (
                <button
                  key={role.id}
                  type="button"
                  className={draft.role === role.id ? 'chip chip--active' : 'chip'}
                  aria-pressed={draft.role === role.id}
                  title={role.hint}
                  onClick={() => setDraft((d) => ({ ...d, role: role.id as UserRole }))}
                >
                  {role.label}
                </button>
              ))}
            </div>
            <p className="card__hint">
              {ROLES.find((r) => r.id === draft.role)?.hint}
            </p>
          </div>

          <div className="row-between">
            <button
              type="button"
              className="chip"
              onClick={() => {
                clearIdentity();
                setOpen(false);
              }}
            >
              <LogOut size={14} aria-hidden />
              {t('identity.clear', '清除身份')}
            </button>
            <button
              type="button"
              className="button-primary"
              onClick={save}
              disabled={!draft.userId.trim()}
            >
              {t('identity.save', '保存')}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
