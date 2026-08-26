"""Revising a frozen edition without rewriting it.

The rule is one sentence: never ``UPDATE`` an edition row, and never let a fix
silently change a number someone already quoted.

So a correction writes a *new* edition row at revision n+1, marks the old one
``SUPERSEDED`` — still readable, still fetchable by its revision number — and records
why, together with a field-level diff. The diff is the part that matters. "Corrected
data" tells a reader nothing about whether their deck needs changing; "SPYx spot
volume moved from $1.2mn to $3.4mn because Bybit's 08:00 fetch landed late" tells them
exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.editions import Edition, EditionRevision
from app.models.enums import EditionKind, EditionStatus
from app.services.workflow import audit


class RevisionError(ValueError):
    """A revision that would lose the record it is supposed to preserve."""


@dataclass(frozen=True, slots=True)
class FieldDiff:
    """One figure that moved between two revisions."""

    path: str
    before: Any
    after: Any

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "before": self.before, "after": self.after}


def diff_payloads(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> list[FieldDiff]:
    """Flatten two edition payloads and report every leaf that changed.

    Flattened rather than compared structurally: readers think in terms of "the SPYx
    volume figure", not "the third element of the rankings array", and a diff that
    reports a whole array as changed is a diff nobody reads.
    """
    flat_before = _flatten(before or {})
    flat_after = _flatten(after or {})
    keys = sorted(set(flat_before) | set(flat_after))
    return [
        FieldDiff(path=key, before=flat_before.get(key), after=flat_after.get(key))
        for key in keys
        if flat_before.get(key) != flat_after.get(key)
    ]


def revise(
    session: Session,
    edition: Edition,
    *,
    reason: str,
    payload: Mapping[str, Any] | None,
    as_of: datetime | None = None,
    created_by: str | None = None,
    actor_role: str | None = None,
) -> Edition:
    """Issue revision n+1 of a frozen edition.

    The previous row is marked superseded and left otherwise untouched. Nothing in
    this function writes over a figure.
    """
    if edition.kind is EditionKind.LIVE:
        raise RevisionError("the live edition is not frozen and cannot be revised")
    if edition.status is EditionStatus.SUPERSEDED:
        raise RevisionError(
            f"{edition.edition_key} r{edition.revision} is already superseded; "
            "revise the current revision"
        )
    if not reason.strip():
        raise RevisionError(
            "a revision must state why the numbers changed — "
            '"updated" is not a reason someone can act on'
        )

    previous_payload = (
        json.loads(edition.payload_json) if edition.payload_json else None
    )
    diffs = diff_payloads(previous_payload, payload)

    replacement = Edition(
        edition_key=edition.edition_key,
        kind=edition.kind,
        status=EditionStatus.FROZEN,
        revision=edition.revision + 1,
        as_of=as_of if as_of is not None else edition.as_of,
        generated_at=utcnow(),
        trading_date=edition.trading_date,
        theme_map_version=edition.theme_map_version,
        payload_json=(
            json.dumps(dict(payload), ensure_ascii=False, default=str)
            if payload is not None
            else None
        ),
    )
    session.add(replacement)
    session.flush()

    # The superseded row keeps its numbers. Only its status and its forward pointer
    # change, so a stale link still resolves to what it always said.
    edition.status = EditionStatus.SUPERSEDED
    edition.superseded_by_id = replacement.id

    entry = EditionRevision(
        edition_id=replacement.id,
        revision=replacement.revision,
        reason=reason,
        diff_json=json.dumps(
            [diff.as_dict() for diff in diffs], ensure_ascii=False, default=str
        ),
        supersedes_edition_id=edition.id,
        created_by=created_by,
    )
    session.add(entry)

    audit.record(
        session,
        entity_kind="edition",
        entity_id=edition.edition_key,
        action="revise",
        actor_id=created_by,
        actor_role=actor_role,
        before={"revision": edition.revision, "status": EditionStatus.FROZEN.value},
        after={
            "revision": replacement.revision,
            "reason": reason,
            "changed_fields": len(diffs),
        },
    )
    return replacement


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Depth-first flatten to ``a.b[0].c`` paths."""
    flat: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            flat.update(_flatten(item, f"{prefix}[{index}]"))
    else:
        flat[prefix or "value"] = value
    return flat


__all__ = ["FieldDiff", "RevisionError", "diff_payloads", "revise"]
