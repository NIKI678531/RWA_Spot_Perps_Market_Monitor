"""Data gaps: the Data Quality page as a work queue, not a status board.

A red cell on a status board is information. A red cell nobody owns is information
that stays red. So every quality defect this system can detect becomes a row here
with a dedup key, an owner slot and a resolution — the same shape as an alert,
because it needs the same treatment.

Gaps are deduplicated across scans. A source that has been down for three days is one
gap seen seventy times, not seventy gaps; the distinction is the difference between a
queue somebody works and a queue somebody mutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.enums import DataGapKind, DataGapStatus, EntityType
from app.models.workflow import DataGap
from app.services.workflow import audit
from app.services.workflow.alert_lifecycle import Actor

#: Statuses that still represent outstanding work.
OPEN_STATUSES = (DataGapStatus.OPEN, DataGapStatus.ASSIGNED)


@dataclass(frozen=True, slots=True)
class GapCandidate:
    """A detected defect, before it is reconciled against what is already open."""

    kind: DataGapKind
    dedup_key: str
    title: str
    detail: str | None = None
    blocks: str | None = None
    impact_score: Decimal | None = None
    entity_type: EntityType | None = None
    entity_id: str | None = None
    source_id: str | None = None
    is_blocking: bool = False


def reconcile(
    session: Session,
    candidates: Sequence[GapCandidate],
    *,
    now: datetime | None = None,
) -> tuple[list[DataGap], list[DataGap]]:
    """Fold a scan's findings into the open queue.

    Returns ``(opened, recurring)``. A gap that has stopped appearing is *not*
    auto-closed here: it may have stopped appearing because the scan itself broke,
    and silently clearing the queue in that case is the worst available outcome.
    Closing is a human action with a reason attached.
    """
    stamp = now or utcnow()
    open_rows = {
        row.dedup_key: row
        for row in session.execute(
            select(DataGap).where(DataGap.status.in_(OPEN_STATUSES))
        )
        .scalars()
        .all()
    }

    opened: list[DataGap] = []
    recurring: list[DataGap] = []
    for candidate in candidates:
        row = open_rows.get(candidate.dedup_key)
        if row is None:
            row = DataGap(
                kind=candidate.kind,
                status=DataGapStatus.OPEN,
                dedup_key=candidate.dedup_key,
                entity_type=candidate.entity_type,
                entity_id=candidate.entity_id,
                source_id=candidate.source_id,
                title=candidate.title,
                detail=candidate.detail,
                blocks=candidate.blocks,
                impact_score=candidate.impact_score,
                first_seen_ts=stamp,
                last_seen_ts=stamp,
                is_blocking=candidate.is_blocking,
            )
            session.add(row)
            opened.append(row)
        else:
            row.last_seen_ts = stamp
            row.occurrence_count += 1
            # The description is refreshed but first_seen_ts is not: how long this has
            # been broken is the number that gets it prioritised.
            row.detail = candidate.detail
            row.impact_score = candidate.impact_score
            row.is_blocking = candidate.is_blocking
            recurring.append(row)

    return opened, recurring


def assign(session: Session, gap: DataGap, actor: Actor, *, owner_id: str) -> DataGap:
    before = {"owner_id": gap.owner_id, "status": gap.status.value}
    gap.owner_id = owner_id
    gap.status = DataGapStatus.ASSIGNED
    audit.record(
        session,
        entity_kind="data_gap",
        entity_id=gap.id,
        action="assign",
        actor_id=actor.user_id,
        actor_role=actor.role,
        before=before,
        after={"owner_id": owner_id, "status": gap.status.value},
    )
    return gap


def close(
    session: Session,
    gap: DataGap,
    actor: Actor,
    *,
    status: DataGapStatus,
    note: str,
) -> DataGap:
    """Close a gap as fixed, or accept it with a reason.

    ``ACCEPTED`` is a real outcome — some sources simply cannot be collected without
    credentials we do not have. Recording that explicitly keeps the queue honest;
    leaving such a gap permanently open trains people to ignore the whole page.
    """
    if status not in (DataGapStatus.FIXED, DataGapStatus.ACCEPTED):
        raise ValueError("a gap closes as FIXED or ACCEPTED")
    if not note.strip():
        raise ValueError("closing a gap requires a note saying what was done or why")

    before = {"status": gap.status.value}
    gap.status = status
    gap.resolution_note = note
    gap.resolved_ts = utcnow()
    audit.record(
        session,
        entity_kind="data_gap",
        entity_id=gap.id,
        action=f"close_{status.value}",
        actor_id=actor.user_id,
        actor_role=actor.role,
        before=before,
        after={"status": status.value, "note": note},
    )
    return gap


def open_gaps(
    session: Session,
    *,
    kind: DataGapKind | None = None,
    owner_id: str | None = None,
    limit: int = 200,
) -> Sequence[DataGap]:
    stmt = select(DataGap).where(DataGap.status.in_(OPEN_STATUSES))
    if kind is not None:
        stmt = stmt.where(DataGap.kind == kind)
    if owner_id:
        stmt = stmt.where(DataGap.owner_id == owner_id)
    # Blocking first, then by impact, then by age. A gap that blocks publication
    # outranks a larger one that merely degrades a figure.
    stmt = stmt.order_by(
        DataGap.is_blocking.desc(),
        DataGap.impact_score.is_(None),
        DataGap.impact_score.desc(),
        DataGap.first_seen_ts,
    ).limit(limit)
    return session.execute(stmt).scalars().all()


__all__ = [
    "OPEN_STATUSES",
    "GapCandidate",
    "assign",
    "close",
    "open_gaps",
    "reconcile",
]
