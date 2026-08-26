"""Research tasks — the record that a finding was actually looked into.

Thin by design. The value is not in the workflow features; it is in being able to
answer "what came of that alert in March" with a row instead of a recollection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.enums import EntityType, ResearchTaskStatus
from app.models.workflow import ResearchTask
from app.services.workflow import audit
from app.services.workflow.alert_lifecycle import Actor

#: Statuses that still count as outstanding work.
OPEN_STATUSES = (
    ResearchTaskStatus.OPEN,
    ResearchTaskStatus.IN_PROGRESS,
    ResearchTaskStatus.BLOCKED,
)


def create(
    session: Session,
    actor: Actor,
    *,
    title: str,
    detail: str | None = None,
    owner_id: str | None = None,
    entity_type: EntityType | None = None,
    entity_id: str | None = None,
    due_at: datetime | None = None,
    alert_id: int | None = None,
) -> ResearchTask:
    if not title.strip():
        raise ValueError("a task needs a title")
    task = ResearchTask(
        alert_id=alert_id,
        title=title,
        detail=detail,
        owner_id=owner_id,
        entity_type=entity_type,
        entity_id=entity_id,
        due_at=due_at,
        created_by=actor.user_id,
    )
    session.add(task)
    session.flush()
    audit.record(
        session,
        entity_kind="research_task",
        entity_id=task.id,
        action="create",
        actor_id=actor.user_id,
        actor_role=actor.role,
        after={"title": title, "owner_id": owner_id, "status": task.status.value},
    )
    return task


def update_status(
    session: Session,
    task: ResearchTask,
    actor: Actor,
    *,
    status: ResearchTaskStatus,
    outcome: str | None = None,
) -> ResearchTask:
    """Move a task along.

    Closing without an outcome is refused for the same reason closing an alert
    without a retrospective is: a task that ended with no statement of what was found
    tells the next reader nothing, and the work gets repeated.
    """
    if status is ResearchTaskStatus.DONE and not (outcome or "").strip():
        raise ValueError("completing a task requires an outcome")

    before = {"status": task.status.value, "outcome": task.outcome}
    task.status = status
    if outcome is not None:
        task.outcome = outcome
    if status in (ResearchTaskStatus.DONE, ResearchTaskStatus.CANCELLED):
        task.closed_at = utcnow()

    audit.record(
        session,
        entity_kind="research_task",
        entity_id=task.id,
        action="update_status",
        actor_id=actor.user_id,
        actor_role=actor.role,
        before=before,
        after={"status": task.status.value, "outcome": task.outcome},
    )
    return task


def open_tasks(
    session: Session, *, owner_id: str | None = None, limit: int = 100
) -> Sequence[ResearchTask]:
    stmt = select(ResearchTask).where(ResearchTask.status.in_(OPEN_STATUSES))
    if owner_id:
        stmt = stmt.where(ResearchTask.owner_id == owner_id)
    # Due first, and undated tasks after them: an undated task is not urgent, it is
    # unscheduled, and sorting it to the top would push real deadlines down.
    stmt = stmt.order_by(
        ResearchTask.due_at.is_(None), ResearchTask.due_at, ResearchTask.created_at
    ).limit(limit)
    return session.execute(stmt).scalars().all()


__all__ = ["OPEN_STATUSES", "create", "open_tasks", "update_status"]
