"""Research tasks — the record that a finding was actually looked into.

Thin on purpose. The value is not in workflow features; it is in being able to answer
"what came of that alert in March" with a row rather than a recollection.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Limit, SessionDep, WriterDep
from app.db.base import utcnow
from app.models.enums import ResearchTaskStatus
from app.models.workflow import ResearchTask
from app.schemas.common import Meta
from app.schemas.workflow import (
    ResearchTaskList,
    ResearchTaskRow,
    TaskCreateRequest,
    TaskStatusRequest,
)
from app.services.workflow import research_task as service

router = APIRouter(tags=["tasks"])

_NOTE = "有到期日的任务在前，未排期的在后——未排期不等于不紧急，但它没有时钟。"


@router.get("/tasks", response_model=ResearchTaskList)
def tasks(
    session: SessionDep,
    status: ResearchTaskStatus | None = Query(default=None),
    owner_id: str | None = Query(
        default=None, description="Filter by owner. Pass `unassigned` for the pool."
    ),
    alert_id: int | None = Query(default=None),
    include_closed: bool = Query(default=False),
    limit: Limit = 100,
) -> ResearchTaskList:
    stmt = select(ResearchTask)
    if status is not None:
        stmt = stmt.where(ResearchTask.status == status)
    elif not include_closed:
        stmt = stmt.where(ResearchTask.status.in_(service.OPEN_STATUSES))
    if owner_id == "unassigned":
        stmt = stmt.where(ResearchTask.owner_id.is_(None))
    elif owner_id:
        stmt = stmt.where(ResearchTask.owner_id == owner_id)
    if alert_id is not None:
        stmt = stmt.where(ResearchTask.alert_id == alert_id)

    rows = list(
        session.execute(
            stmt.order_by(
                ResearchTask.due_at.is_(None),
                ResearchTask.due_at,
                ResearchTask.created_at,
            ).limit(limit)
        )
        .scalars()
        .all()
    )
    return ResearchTaskList(
        meta=Meta(as_of=utcnow(), note=_NOTE, row_count=len(rows)),
        rows=[task_row(row) for row in rows],
    )


@router.post("/tasks", response_model=ResearchTaskRow, status_code=201)
def create_task(
    payload: TaskCreateRequest, session: SessionDep, actor: WriterDep
) -> ResearchTaskRow:
    """Create a standalone task, not tied to an alert.

    Tasks born from a finding go through ``POST /alerts/{id}/tasks`` instead, so the
    alert's own timeline records that the work was spawned.
    """
    try:
        task = service.create(
            session,
            actor,
            title=payload.title,
            detail=payload.detail,
            owner_id=payload.owner_id,
            due_at=payload.due_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return task_row(task)


@router.post("/tasks/{task_id}/status", response_model=ResearchTaskRow)
def update_status(
    task_id: int,
    payload: TaskStatusRequest,
    session: SessionDep,
    actor: WriterDep,
) -> ResearchTaskRow:
    task = _load(session, task_id)
    try:
        service.update_status(
            session, task, actor, status=payload.status, outcome=payload.outcome
        )
    except ValueError as exc:
        # Completing without an outcome. Refused for the same reason closing an alert
        # without a retrospective is: the next reader learns nothing and repeats it.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return task_row(task)


def task_row(task: ResearchTask) -> ResearchTaskRow:
    """Shared with the alert work item, which lists the same tasks."""
    return ResearchTaskRow(
        id=task.id,
        alert_id=task.alert_id,
        title=task.title,
        detail=task.detail,
        status=task.status,
        owner_id=task.owner_id,
        entity_type=task.entity_type,
        entity_id=task.entity_id,
        due_at=task.due_at,
        closed_at=task.closed_at,
        outcome=task.outcome,
        created_at=task.created_at,
    )


def _load(session: Session, task_id: int) -> ResearchTask:
    task = session.get(ResearchTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"unknown task {task_id}")
    return task


__all__ = ["router", "task_row"]
