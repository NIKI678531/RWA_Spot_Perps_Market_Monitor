"""The alert lifecycle: an alert is a business object, not a notification.

A notification is delivered and forgotten. An alert has one owner, a state, and a
resolution somebody signed. This module is where those transitions happen, and it
refuses the ones that are not in the state machine rather than accepting them and
letting the queue drift into states nobody designed.

Three invariants hold on every call:

* every transition appends an :class:`AlertAction` **and** an audit row, in the
  caller's transaction;
* the ``alert.state`` column is only ever written here, so it cannot disagree with
  the action stream that defines it;
* closing requires either a retrospective or a standardised declined reason — an
  alert closed with an empty note is an alert nobody can review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.base import as_utc, utcnow
from app.models.alerts import Alert
from app.models.enums import (
    ALERT_TRANSITIONS,
    CLOSED_ALERT_STATES,
    AlertActionType,
    AlertState,
)
from app.models.workflow import AlertAction, ResearchTask
from app.services.workflow import audit

#: Close reasons the queue offers when an alert is dismissed or marked false. Free
#: text is stored alongside; this list exists so false-positive rate is a number the
#: threshold review can act on rather than a folder of prose.
STANDARD_CLOSE_REASONS: tuple[str, ...] = (
    "duplicate",  # same condition already tracked under another alert
    "data_artefact",  # the move was in our pipeline, not in the market
    "below_materiality",  # real, but too small to act on
    "known_event",  # explained by a listing, index rebalance, corporate action
    "expected_seasonality",
    "no_action_needed",  # real and understood; no response warranted
)


class TransitionError(ValueError):
    """A transition the state machine does not model.

    Raised rather than coerced. A queue that silently accepts an unmodelled jump ends
    up with alerts in states no screen renders, and those alerts are simply lost.
    """


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting, carried explicitly rather than read from a global."""

    user_id: str | None
    role: str | None = None
    #: ``system`` for the scheduler's own actions (publication, auto-confirmation).
    kind: str = "user"
    #: Set only for a share-link caller. Carried on the actor so the route that
    #: serves a shared edition can check the token names *that* edition rather than
    #: treating any token as a pass to every one of them.
    share_token: str | None = None


def _transition(
    session: Session,
    alert: Alert,
    *,
    to_state: AlertState,
    action: AlertActionType,
    actor: Actor,
    note: str | None = None,
    reason_code: str | None = None,
    from_owner: str | None = None,
    to_owner: str | None = None,
    defer_until: datetime | None = None,
) -> AlertAction:
    """Apply one legal transition, with its action row and its audit row."""
    from_state = alert.state
    allowed = ALERT_TRANSITIONS.get(from_state, frozenset())
    if to_state is not from_state and to_state not in allowed:
        raise TransitionError(
            f"alert {alert.id}: {from_state.value} -> {to_state.value} is not a "
            f"modelled transition (allowed: "
            f"{', '.join(sorted(s.value for s in allowed)) or 'none — terminal'})"
        )

    entry = AlertAction(
        alert=alert,
        action=action,
        from_state=from_state if to_state is not from_state else None,
        to_state=to_state if to_state is not from_state else None,
        actor_id=actor.user_id,
        actor_kind=actor.kind,
        from_owner_id=from_owner,
        to_owner_id=to_owner,
        note=note,
        reason_code=reason_code,
        defer_until=defer_until,
    )
    session.add(entry)

    before = {"state": from_state.value, "owner_id": alert.owner_id}
    alert.state = to_state
    if to_state in CLOSED_ALERT_STATES:
        alert.resolved_ts = utcnow()

    audit.record(
        session,
        entity_kind="alert",
        entity_id=alert.id if alert.id is not None else alert.dedup_key,
        action=action.value,
        actor_id=actor.user_id,
        actor_role=actor.role,
        before=before,
        after={"state": alert.state.value, "owner_id": alert.owner_id},
    )
    return entry


def claim(session: Session, alert: Alert, actor: Actor) -> AlertAction:
    """Take ownership. One owner at a time, and never silently reassigned."""
    if alert.owner_id and alert.owner_id != actor.user_id:
        raise TransitionError(
            f"alert {alert.id} is already owned by {alert.owner_id}; "
            "use reassign() so the handover is recorded"
        )
    previous, alert.owner_id = alert.owner_id, actor.user_id
    return _transition(
        session,
        alert,
        to_state=AlertState.CLAIMED,
        action=AlertActionType.CLAIM,
        actor=actor,
        from_owner=previous,
        to_owner=actor.user_id,
    )


def release(
    session: Session, alert: Alert, actor: Actor, note: str | None = None
) -> AlertAction:
    """Return an alert to the unclaimed queue."""
    previous, alert.owner_id = alert.owner_id, None
    return _transition(
        session,
        alert,
        to_state=AlertState.PUBLISHED,
        action=AlertActionType.RELEASE,
        actor=actor,
        note=note,
        from_owner=previous,
        to_owner=None,
    )


def reassign(
    session: Session, alert: Alert, actor: Actor, *, to_user_id: str, note: str | None
) -> AlertAction:
    """Hand an alert to someone else.

    Appends rather than overwrites: "who had this when it went wrong" has to survive
    the next handover, and a mutable owner column cannot answer it.
    """
    previous, alert.owner_id = alert.owner_id, to_user_id
    return _transition(
        session,
        alert,
        to_state=(
            alert.state
            if alert.state is not AlertState.PUBLISHED
            else AlertState.CLAIMED
        ),
        action=AlertActionType.REASSIGN,
        actor=actor,
        note=note,
        from_owner=previous,
        to_owner=to_user_id,
    )


def add_note(session: Session, alert: Alert, actor: Actor, note: str) -> AlertAction:
    """A comment. Changes no state, and is still part of the permanent record."""
    if not note.strip():
        raise ValueError("a note must say something")
    return _transition(
        session,
        alert,
        to_state=alert.state,
        action=AlertActionType.NOTE,
        actor=actor,
        note=note,
    )


def start_review(session: Session, alert: Alert, actor: Actor) -> AlertAction:
    return _transition(
        session,
        alert,
        to_state=AlertState.IN_REVIEW,
        action=AlertActionType.START_REVIEW,
        actor=actor,
    )


def record_action(
    session: Session, alert: Alert, actor: Actor, *, note: str
) -> AlertAction:
    """Record that a business decision was taken in response to this alert."""
    if not note.strip():
        raise ValueError("an action must state what was decided")
    return _transition(
        session,
        alert,
        to_state=AlertState.ACTIONED,
        action=AlertActionType.ACTION,
        actor=actor,
        note=note,
    )


def defer(
    session: Session,
    alert: Alert,
    actor: Actor,
    *,
    until: datetime,
    note: str | None = None,
) -> AlertAction:
    """Snooze an alert without closing it.

    The alert leaves the default queue view and comes back at ``until``. It is never
    dropped: a deferred alert that quietly disappeared is the failure mode this whole
    layer exists to prevent.
    """
    if until <= utcnow():
        raise ValueError("a deferral must point at a future time")
    alert.deferred_until = until
    return _transition(
        session,
        alert,
        to_state=alert.state,
        action=AlertActionType.DEFER,
        actor=actor,
        note=note,
        defer_until=until,
    )


def create_task(
    session: Session,
    alert: Alert,
    actor: Actor,
    *,
    title: str,
    detail: str | None = None,
    owner_id: str | None = None,
    due_at: datetime | None = None,
) -> ResearchTask:
    """Spawn research work from a finding, and record that it happened.

    The entity is copied onto the task rather than joined through the alert: a task
    list grouped by underlying should not go dark when the alert behind it is closed.
    """
    task = ResearchTask(
        alert_id=alert.id,
        title=title,
        detail=detail,
        owner_id=owner_id or alert.owner_id,
        entity_type=alert.entity_type,
        entity_id=alert.entity_id,
        due_at=due_at,
        created_by=actor.user_id,
    )
    session.add(task)
    _transition(
        session,
        alert,
        to_state=alert.state,
        action=AlertActionType.CREATE_TASK,
        actor=actor,
        note=title,
    )
    return task


def close(
    session: Session,
    alert: Alert,
    actor: Actor,
    *,
    to_state: AlertState,
    note: str | None,
    reason_code: str | None,
) -> AlertAction:
    """Close an alert — with a retrospective, or a standard declined reason.

    The requirement is not bureaucratic. The false-positive rate and the threshold
    review both read this field; an alert closed with nothing attached is invisible
    to both, and the detector that produced it never gets retuned.
    """
    if to_state not in CLOSED_ALERT_STATES:
        raise TransitionError(f"{to_state.value} is not a closing state")

    has_retrospective = bool(note and note.strip())
    if not has_retrospective and reason_code not in STANDARD_CLOSE_REASONS:
        raise ValueError(
            "closing an alert requires a retrospective note or one of the standard "
            f"reasons: {', '.join(STANDARD_CLOSE_REASONS)}"
        )

    action = {
        AlertState.RESOLVED: AlertActionType.RESOLVE,
        AlertState.FALSE_POSITIVE: AlertActionType.MARK_FALSE_POSITIVE,
        AlertState.DISMISSED: AlertActionType.DISMISS,
    }[to_state]

    return _transition(
        session,
        alert,
        to_state=to_state,
        action=action,
        actor=actor,
        note=note,
        reason_code=reason_code,
    )


def is_overdue(alert: Alert, *, now: datetime | None = None) -> bool:
    """Whether the response clock has run out on an open alert."""
    if alert.sla_due_ts is None or alert.state in CLOSED_ALERT_STATES:
        return False
    return as_utc(now or utcnow()) > as_utc(alert.sla_due_ts)


def sla_remaining(alert: Alert, *, now: datetime | None = None) -> timedelta | None:
    if alert.sla_due_ts is None:
        return None
    return as_utc(alert.sla_due_ts) - as_utc(now or utcnow())


__all__ = [
    "STANDARD_CLOSE_REASONS",
    "Actor",
    "TransitionError",
    "add_note",
    "claim",
    "close",
    "create_task",
    "defer",
    "is_overdue",
    "reassign",
    "record_action",
    "release",
    "sla_remaining",
    "start_review",
]
