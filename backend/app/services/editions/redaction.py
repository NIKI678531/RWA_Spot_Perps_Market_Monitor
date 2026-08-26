"""Server-side redaction for shared editions.

A share link goes to someone outside the team. What they must not see is not hidden
in the front end — it never enters the response. Hiding a field in the browser means
the field is in the payload, and the payload is one devtools tab away.

Two rules, both easy to get wrong:

* **Omit, do not blank.** A key present with ``null`` tells the reader that a
  candidate list exists and they are not allowed to see it. The key is removed.
* **A denial must not reveal existence.** Asking for a redacted record returns the
  same "not found" as asking for one that was never created. Otherwise the error
  message becomes an oracle for enumerating what we are working on.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.models.enums import UserRole

#: Field names stripped from any payload served to a share viewer. Matched by key at
#: every level of nesting, so a field added to a nested object later is covered
#: without anyone having to remember to update a per-endpoint allowlist.
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        # Who is working on what — the org chart of our attention.
        "owner",
        "owner_id",
        "owner_name",
        "assignee",
        "actions",
        "action_stream",
        "timeline",
        "notes",
        "note",
        "business_note",
        # Issuance intent. The most commercially sensitive thing in the system: it
        # is our product roadmap expressed as a list of other people's tickers.
        "candidates",
        "issuance_candidates",
        "issuance_status",
        "candidate_stage",
        "stage",
        "gate_log",
        "readiness",
        "demand_score",
        "liquidity_score",
        "competition_score",
        "feasibility_score",
        "coverage",
        "coverage_gaps",
        "own_products",
        # Detector configuration. Publishing our thresholds tells a counterparty
        # exactly how large a move has to be before we notice it.
        "thresholds",
        "threshold",
        "detector_config",
        "rule_config",
        # Unpublished detail behind a published conclusion.
        "unpublished",
        "internal_note",
        "raw_source_detail",
        "source_credentials",
        "research_tasks",
    }
)


class NotVisible(LookupError):
    """The record is absent *or* redacted, and the caller cannot tell which.

    One exception type for both cases on purpose. A distinct "forbidden" response
    would confirm that the record exists, which is the fact the redaction was
    protecting.
    """


def is_share_viewer(role: UserRole | str | None) -> bool:
    if role is None:
        return False
    value = role.value if isinstance(role, UserRole) else str(role)
    return value == UserRole.SHARE_VIEWER.value


def redact(payload: Any, *, role: UserRole | str | None) -> Any:
    """Strip everything a share viewer must not receive.

    A no-op for every other role — this is not a general permission filter, it is the
    one boundary where an anonymous link is served.
    """
    if not is_share_viewer(role):
        return payload
    return _strip(payload)


def _strip(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _strip(item) for key, item in value.items() if key not in REDACTED_KEYS
        }
    if isinstance(value, list):
        return [_strip(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip(item) for item in value)
    return value


def assert_visible(exists: bool, *, allowed: bool) -> None:
    """Raise the same error for "missing" and "not allowed".

    Callers pass both facts so the decision is made in one place; a route that
    branches on them itself will eventually branch differently for the two, and the
    difference is observable.
    """
    if not exists or not allowed:
        raise NotVisible("not found")


__all__ = ["REDACTED_KEYS", "NotVisible", "assert_visible", "is_share_viewer", "redact"]
