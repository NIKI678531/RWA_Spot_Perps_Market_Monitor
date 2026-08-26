"""The response clock, scanned rather than merely displayed (ALERT-004).

``sla_due_ts`` is stamped when an alert crosses the publication gate, and the queue
already sorts on it. That is enough for anyone who opens the queue and not enough for
anyone who does not — which is the case the clock exists for.

So this scan walks the open, published alerts whose time has run out and records the
breach on the alert itself: an :class:`AlertAction` and an audit row, written as the
system actor. Three things follow from that, and none of them work if the breach is
only ever computed on read:

* the retrospective can say "this sat for nine hours before anyone claimed it", which
  is the number that changes a threshold;
* the digest has something to carry, since it renders from stored rows;
* the escalation is attributable — ``actor_kind='system'`` — rather than appearing as
  though a person noticed.

Recorded once per breach, not once per scan. The job runs every fifteen minutes and an
alert can sit overdue for days; re-recording would bury the alert's own history under
its escalations. Re-stamping ``sla_due_ts`` (a deferral does exactly that) starts a new
clock and therefore earns a new breach row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import as_utc, utcnow
from app.models.alerts import Alert
from app.models.enums import CLOSED_ALERT_STATES, AlertActionType
from app.models.workflow import AlertAction
from app.services.workflow import audit
from app.services.workflow.alert_lifecycle import Actor

logger = logging.getLogger(__name__)

#: The scanner acts as itself, never as the last person to touch the alert.
SYSTEM = Actor(user_id=None, role="system", kind="system")

#: Prefix every breach note carries, so the scan can recognise its own work without a
#: dedicated action type or a second table.
BREACH_MARKER = "[sla]"


@dataclass(frozen=True, slots=True)
class Breach:
    """One alert that ran out of time, and by how much."""

    alert: Alert
    overdue_hours: float
    unclaimed: bool


def scan(session: Session, *, now: datetime | None = None) -> list[Breach]:
    """Record every newly breached SLA. Returns what was recorded.

    Does not commit — the caller owns the transaction, so the action rows and the
    audit rows land together or not at all.
    """
    moment = as_utc(now or utcnow())
    overdue = _overdue(session, moment)
    if not overdue:
        return []

    already = _already_recorded(session, [a.id for a in overdue])
    breaches: list[Breach] = []

    for alert in overdue:
        due = as_utc(alert.sla_due_ts) if alert.sla_due_ts else moment
        recorded_at = already.get(alert.id)
        # A breach already noted for *this* clock. A later ``sla_due_ts`` — which is
        # what a deferral writes — is a new clock and gets its own row.
        if recorded_at is not None and as_utc(recorded_at) >= due:
            continue

        overdue_hours = (moment - due).total_seconds() / 3600
        unclaimed = alert.owner_id is None
        breaches.append(
            Breach(alert=alert, overdue_hours=overdue_hours, unclaimed=unclaimed)
        )
        _record(session, alert, overdue_hours=overdue_hours, unclaimed=unclaimed)

    if breaches:
        logger.warning(
            "%d alerts breached their SLA (%d still unclaimed)",
            len(breaches),
            sum(1 for b in breaches if b.unclaimed),
        )
    return breaches


def _overdue(session: Session, now: datetime) -> list[Alert]:
    """Published, open, undeferred alerts whose clock has run out.

    ``published_at IS NOT NULL`` is the same predicate every read path uses. An alert
    that never crossed the gate has no response obligation, because nobody was ever
    shown it.
    """
    stmt = (
        select(Alert)
        .where(Alert.published_at.isnot(None))
        .where(Alert.state.notin_(CLOSED_ALERT_STATES))
        .where(Alert.sla_due_ts.isnot(None))
        .where(Alert.sla_due_ts < now)
        .order_by(Alert.sla_due_ts)
    )
    rows = list(session.execute(stmt).scalars().all())
    # A deferral is a decision somebody recorded, with a wake time. Escalating
    # through it would make deferring pointless and train people to close instead.
    return [
        a for a in rows if a.deferred_until is None or as_utc(a.deferred_until) <= now
    ]


def _already_recorded(
    session: Session, alert_ids: list[int]
) -> dict[int, datetime | None]:
    """The newest breach note per alert, so a second scan stays quiet."""
    if not alert_ids:
        return {}
    stmt = (
        select(AlertAction.alert_id, AlertAction.created_at)
        .where(AlertAction.alert_id.in_(alert_ids))
        .where(AlertAction.action == AlertActionType.NOTE)
        .where(AlertAction.note.startswith(BREACH_MARKER))
        .order_by(AlertAction.created_at.desc())
    )
    newest: dict[int, datetime | None] = {}
    for alert_id, created_at in session.execute(stmt).all():
        newest.setdefault(int(alert_id), created_at)
    return newest


def _record(
    session: Session, alert: Alert, *, overdue_hours: float, unclaimed: bool
) -> AlertAction:
    """Append the breach to the alert's own record. Changes no state.

    Deliberately a note rather than a transition. Missing a deadline is not a decision
    about the finding, and moving the alert would misrepresent what happened: nobody
    looked at it, which is exactly the fact worth keeping.
    """
    note = f"{BREACH_MARKER} 响应时限已过 {overdue_hours:.1f} 小时" + (
        "，且仍无人认领。" if unclaimed else f"，当前 owner：{alert.owner_id}。"
    )
    entry = AlertAction(
        alert=alert,
        action=AlertActionType.NOTE,
        actor_id=None,
        actor_kind=SYSTEM.kind,
        note=note,
    )
    session.add(entry)
    audit.record(
        session,
        entity_kind="alert",
        entity_id=alert.id if alert.id is not None else alert.dedup_key,
        action="sla_breach",
        actor_id=None,
        actor_role=SYSTEM.role,
        before={"sla_due_ts": str(alert.sla_due_ts), "owner_id": alert.owner_id},
        after={"overdue_hours": round(overdue_hours, 2), "unclaimed": unclaimed},
    )
    return entry


__all__ = ["BREACH_MARKER", "SYSTEM", "Breach", "scan"]
