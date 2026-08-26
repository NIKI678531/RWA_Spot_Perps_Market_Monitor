"""The audit record, and the rule that it is not optional.

Every state-changing operation in the business layer calls :func:`record` inside the
same transaction as the change it describes. Not afterwards, not in a background
task, not best-effort: if the audit write fails, the change must fail with it.

The reasoning is unglamorous. An audit trail that can be missing rows is an audit
trail you cannot reason from — you can never tell "nobody did this" from "the log
write failed". At that point it is decoration.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.models.workflow import AuditLog


def record(
    session: Session,
    *,
    entity_kind: str,
    entity_id: str | int,
    action: str,
    actor_id: str | None,
    actor_role: str | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """Append one audit row to the caller's unit of work.

    Does not commit. The caller owns the transaction, which is the whole point: the
    audit row and the change it describes land together or not at all.
    """
    entry = AuditLog(
        entity_kind=entity_kind,
        entity_id=str(entity_id),
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        diff_json=_diff(before, after),
        request_id=request_id,
    )
    session.add(entry)
    return entry


def _diff(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> str | None:
    """Serialise a before/after pair, narrowed to what actually changed.

    Storing whole rows makes the log unreadable at exactly the moment it is needed —
    a reviewer scanning six months of changes wants the two fields that moved, not
    forty that did not.
    """
    if before is None and after is None:
        return None
    keys = set(before or {}) | set(after or {})
    changed = {
        key: {"before": (before or {}).get(key), "after": (after or {}).get(key)}
        for key in sorted(keys)
        if (before or {}).get(key) != (after or {}).get(key)
    }
    if not changed:
        return None
    return json.dumps(changed, ensure_ascii=False, default=str)


__all__ = ["record"]
