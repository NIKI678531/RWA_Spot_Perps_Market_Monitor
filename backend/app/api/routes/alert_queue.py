"""The anomaly work queue and the actions that move an alert through it.

Everything here treats an alert as a business object rather than a notification: it
has one owner, a state, an SLA and a resolution somebody signed. The route layer is
deliberately thin — it resolves the alert, checks the caller may write, and hands off
to :mod:`app.services.workflow.alert_lifecycle`, which appends the action row and the
audit row inside the same transaction. A route that mutated ``alert.state`` itself
would produce a state the action stream cannot explain.

``DETECTED`` never appears in any response. A detector emitting a finding does not
make it visible; the publication gate does, and it records that decision by setting
``published_at``.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Limit, SessionDep, WriterDep
from app.api.naming import Resolver
from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.base import utcnow
from app.models.alerts import Alert, AlertEvidence
from app.models.enums import (
    ALERT_TRANSITIONS,
    CLOSED_ALERT_STATES,
    OPEN_ALERT_STATES,
    AlertSeverity,
    AlertState,
    DetectorFamily,
    VerificationStatus,
)
from app.models.workflow import AlertAction, ResearchTask
from app.schemas.common import Confidence, CounterEvidenceItem, Meta
from app.schemas.workflow import (
    ActionRequest,
    AlertQueue,
    AlertWorkItem,
    ClaimRequest,
    CloseRequest,
    DeferRequest,
    NoteRequest,
    QueueAlertRow,
    QueueEvidenceRow,
    QueueSummary,
    ReassignRequest,
    ResearchTaskRow,
    TaskCreateRequest,
    TimelineEntry,
)
from app.services.anomaly import publication
from app.services.workflow import alert_lifecycle
from app.services.workflow.alert_lifecycle import (
    STANDARD_CLOSE_REASONS,
    TransitionError,
)

router = APIRouter(tags=["alert-queue"])

_NOTE = (
    "队列只显示已过发布门的告警。X* 为同组现状异常、单次确认即可发布；"
    "T* 为相对历史基线升温，需退出冷启动并连续两次同时段确认。"
)

#: Everything the queue serves has cleared the publication gate. Expressed as a
#: predicate rather than a filter on ``state`` so a state added later is covered.
_PUBLISHED = Alert.published_at.isnot(None)


@router.get("/alerts/queue", response_model=AlertQueue)
def queue(
    session: SessionDep,
    severity: AlertSeverity | None = Query(default=None),
    state: AlertState | None = Query(default=None),
    family: DetectorFamily | None = Query(default=None),
    metric_scope: MetricScope | None = Query(default=None),
    market_session: MarketSession | None = Query(default=None),
    owner_id: str | None = Query(
        default=None, description="Filter to one owner. `unassigned` for the pool."
    ),
    detector: str | None = Query(default=None),
    only_overdue: bool = Query(default=False),
    include_closed: bool = Query(default=False),
    include_deferred: bool = Query(
        default=False,
        description=(
            "Deferred alerts are hidden until their snooze expires. They are never "
            "dropped — set this to see them early."
        ),
    ),
    limit: Limit = 100,
) -> AlertQueue:
    now = utcnow()
    stmt = select(Alert).where(_PUBLISHED)

    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    if state is not None:
        stmt = stmt.where(Alert.state == state)
    elif not include_closed:
        stmt = stmt.where(Alert.state.notin_(tuple(CLOSED_ALERT_STATES)))
    if family is not None:
        stmt = stmt.where(Alert.family == family)
    if metric_scope is not None:
        stmt = stmt.where(Alert.metric_scope == metric_scope)
    if market_session is not None:
        stmt = stmt.where(Alert.market_session == market_session)
    if owner_id == "unassigned":
        stmt = stmt.where(Alert.owner_id.is_(None))
    elif owner_id:
        stmt = stmt.where(Alert.owner_id == owner_id)
    if detector is not None:
        stmt = stmt.where(Alert.detector == detector)
    if only_overdue:
        stmt = stmt.where(Alert.sla_due_ts.isnot(None), Alert.sla_due_ts < now)
    if not include_deferred:
        stmt = stmt.where(
            (Alert.deferred_until.is_(None)) | (Alert.deferred_until <= now)
        )

    # Overdue first, then severity by way of the score, then recency. The ordering is
    # the queue's opinion about what to pick up next, and it is stated in one place.
    stmt = stmt.order_by(
        Alert.sla_due_ts.is_(None),
        Alert.sla_due_ts.asc(),
        Alert.score.desc(),
        Alert.last_seen_ts.desc(),
    ).limit(limit)

    resolver = Resolver(session)
    rows = [
        queue_row(a, resolver, now=now) for a in session.execute(stmt).scalars().all()
    ]

    return AlertQueue(
        meta=Meta(
            as_of=max((r.last_seen_ts for r in rows), default=now),
            scopes=sorted({r.metric_scope for r in rows}),
            note=_NOTE,
            row_count=len(rows),
        ),
        summary=_summary(session, now=now),
        rows=rows,
    )


@router.get("/alerts/{alert_id}/workitem", response_model=AlertWorkItem)
def workitem(alert_id: int, session: SessionDep) -> AlertWorkItem:
    alert = _load(session, alert_id)
    resolver = Resolver(session)
    now = utcnow()

    evidence_rows = list(
        session.execute(
            select(AlertEvidence)
            .where(AlertEvidence.alert_id == alert_id)
            .order_by(AlertEvidence.snapshot_ts.desc())
        )
        .scalars()
        .all()
    )
    timeline = list(
        session.execute(
            select(AlertAction)
            .where(AlertAction.alert_id == alert_id)
            .order_by(AlertAction.created_at.asc(), AlertAction.id.asc())
        )
        .scalars()
        .all()
    )
    tasks = list(
        session.execute(
            select(ResearchTask)
            .where(ResearchTask.alert_id == alert_id)
            .order_by(ResearchTask.created_at.desc())
        )
        .scalars()
        .all()
    )

    evidence = [_evidence_row(e) for e in evidence_rows]
    return AlertWorkItem(
        alert=queue_row(alert, resolver, now=now),
        confidence=confidence_of(alert, evidence_rows[0] if evidence_rows else None),
        evidence=evidence,
        # The newest firing's counter-evidence is the one that applies now. Older
        # rows keep theirs so a reader can see whether a weakness was resolved or
        # has simply been there the whole time.
        counter_evidence=evidence[0].counter_evidence if evidence else [],
        timeline=[_timeline_entry(a) for a in timeline],
        tasks=[_task_row(t) for t in tasks],
        available_actions=available_actions(alert),
        close_reasons=list(STANDARD_CLOSE_REASONS),
    )


# --- actions -----------------------------------------------------------------
#
# Each one commits, because each one is a decision. The service functions write the
# AlertAction and the AuditLog into the same transaction, so a commit here either
# lands all three rows or none of them.


@router.post("/alerts/{alert_id}/claim", response_model=AlertWorkItem)
def claim(
    alert_id: int, payload: ClaimRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(lambda: alert_lifecycle.claim(session, alert, actor))
    if payload.note:
        alert_lifecycle.add_note(session, alert, actor, payload.note)
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/release", response_model=AlertWorkItem)
def release(
    alert_id: int, payload: ClaimRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(lambda: alert_lifecycle.release(session, alert, actor, payload.note))
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/reassign", response_model=AlertWorkItem)
def reassign(
    alert_id: int, payload: ReassignRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(
        lambda: alert_lifecycle.reassign(
            session, alert, actor, to_user_id=payload.to_user_id, note=payload.note
        )
    )
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/note", response_model=AlertWorkItem)
def note(
    alert_id: int, payload: NoteRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(lambda: alert_lifecycle.add_note(session, alert, actor, payload.note))
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/review", response_model=AlertWorkItem)
def review(alert_id: int, session: SessionDep, actor: WriterDep) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(lambda: alert_lifecycle.start_review(session, alert, actor))
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/action", response_model=AlertWorkItem)
def action(
    alert_id: int, payload: ActionRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(
        lambda: alert_lifecycle.record_action(session, alert, actor, note=payload.note)
    )
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/defer", response_model=AlertWorkItem)
def defer(
    alert_id: int, payload: DeferRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(
        lambda: alert_lifecycle.defer(
            session, alert, actor, until=payload.until, note=payload.note
        )
    )
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/tasks", response_model=AlertWorkItem)
def create_task(
    alert_id: int, payload: TaskCreateRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(
        lambda: alert_lifecycle.create_task(
            session,
            alert,
            actor,
            title=payload.title,
            detail=payload.detail,
            owner_id=payload.owner_id,
            due_at=payload.due_at,
        )
    )
    session.commit()
    return workitem(alert_id, session)


@router.post("/alerts/{alert_id}/close", response_model=AlertWorkItem)
def close(
    alert_id: int, payload: CloseRequest, session: SessionDep, actor: WriterDep
) -> AlertWorkItem:
    alert = _load(session, alert_id)
    _guard(
        lambda: alert_lifecycle.close(
            session,
            alert,
            actor,
            to_state=payload.to_state,
            note=payload.note,
            reason_code=payload.reason_code,
        )
    )
    session.commit()
    return workitem(alert_id, session)


# --- shared helpers ----------------------------------------------------------


def queue_row(alert: Alert, resolver: Resolver, *, now: datetime) -> QueueAlertRow:
    """Shared with the home page and Underlying 360, which list the same alerts."""
    return QueueAlertRow(
        id=alert.id,
        detector=alert.detector,
        family=alert.family,
        family_claim=publication.family_prefix(alert.family),
        severity=alert.severity,
        score=float(alert.score) if alert.score is not None else None,
        state=alert.state,
        entity_type=alert.entity_type,
        entity_id=alert.entity_id,
        entity_name=resolver.name(alert.entity_type, alert.entity_id),
        metric_scope=alert.metric_scope,
        market_session=alert.market_session,
        headline_zh=alert.headline_zh,
        headline_en=alert.headline_en,
        owner_id=alert.owner_id,
        owner_name=resolver.user_name(alert.owner_id),
        confirmation_count=alert.confirmation_count,
        confirmations_required=publication.REQUIRED_CONFIRMATIONS[alert.family],
        evidence_completeness=alert.evidence_completeness,
        first_seen_ts=alert.first_seen_ts,
        last_seen_ts=alert.last_seen_ts,
        published_at=alert.published_at,
        sla_due_ts=alert.sla_due_ts,
        is_overdue=alert_lifecycle.is_overdue(alert, now=now),
        deferred_until=alert.deferred_until,
        occurrence_count=alert.occurrence_count,
    )


def confidence_of(alert: Alert, evidence: AlertEvidence | None) -> Confidence:
    """How much weight the finding will bear, stated rather than scored.

    Three inputs, all of which a reader can check: whether it met its confirmation
    requirement, how much of the evidence is present, and — for a time-series
    finding — how long a baseline it was judged against.
    """
    required = publication.REQUIRED_CONFIRMATIONS[alert.family]
    complete = (alert.evidence_completeness or 0) >= 1
    sample = evidence.sample_size if evidence else None

    if alert.family is DetectorFamily.TIME_SERIES:
        cold = (sample or 0) < publication.MIN_BASELINE_SAMPLES
        basis = (
            f"同时段样本 {sample or 0} 条，基线尚未成熟"
            if cold
            else f"同时段样本 {sample} 条，连续 {alert.confirmation_count} 次确认"
        )
        level = (
            "low"
            if cold or not complete
            else ("high" if alert.confirmation_count >= required else "medium")
        )
    else:
        peers = evidence.peer_count if evidence else None
        basis = f"同组对照 {peers} 个实体的当期分布" if peers else "同组横截面对照"
        level = "high" if complete and peers and peers >= 5 else "medium"
        if not complete:
            level = "low"

    return Confidence(
        level=level,  # type: ignore[arg-type]
        confirmations=alert.confirmation_count,
        confirmations_required=required,
        sample_size=sample,
        basis=basis,
    )


def available_actions(alert: Alert) -> list[str]:
    """What may legally happen next, derived from the same table the service uses.

    Returned to the client so the drawer's buttons and the server's state machine
    cannot disagree. Deriving it in the browser is how a UI ends up offering a
    button that always fails.
    """
    if alert.state in CLOSED_ALERT_STATES:
        return []

    reachable = ALERT_TRANSITIONS.get(alert.state, frozenset())
    actions: list[str] = ["note"]
    if AlertState.CLAIMED in reachable and alert.owner_id is None:
        actions.append("claim")
    if alert.owner_id is not None:
        actions.extend(["release", "reassign"])
    elif AlertState.CLAIMED in reachable:
        actions.append("reassign")
    if AlertState.IN_REVIEW in reachable:
        actions.append("review")
    if AlertState.ACTIONED in reachable:
        actions.append("action")
    actions.extend(["defer", "create_task"])
    if AlertState.RESOLVED in reachable:
        actions.append("resolve")
    if AlertState.FALSE_POSITIVE in reachable:
        actions.append("false_positive")
    if AlertState.DISMISSED in reachable:
        actions.append("dismiss")
    return actions


def counter_evidence_of(row: AlertEvidence) -> list[CounterEvidenceItem]:
    """Read the stored counter-evidence, tolerating a malformed blob.

    A payload that will not parse must not take the alert down with it: the columns
    beside it still justify the finding. It surfaces as a single item so the gap is
    visible rather than silently rendering as "nothing argues against this".
    """
    if not row.counter_evidence_json:
        return []
    try:
        parsed = json.loads(row.counter_evidence_json)
    except ValueError:
        return [
            CounterEvidenceItem(
                code="unparsed",
                label="反证记录无法解析",
                detail="请以原始 evidence 字段复核后再引用该结论。",
            )
        ]
    if not isinstance(parsed, list):
        return []
    return [
        CounterEvidenceItem(
            code=str(item.get("code", "unknown")),
            label=str(item.get("label_zh") or item.get("label") or ""),
            detail=item.get("detail"),
        )
        for item in parsed
        if isinstance(item, dict)
    ]


def _evidence_row(row: AlertEvidence) -> QueueEvidenceRow:
    from app.api.routes.alerts import _extra

    return QueueEvidenceRow(
        rule_name=row.rule_name,
        snapshot_ts=row.snapshot_ts,
        observed_value=row.observed_value,
        baseline_median=row.baseline_median,
        baseline_mad=row.baseline_mad,
        robust_z=float(row.robust_z) if row.robust_z is not None else None,
        sample_size=row.sample_size,
        market_session=row.market_session,
        peer_count=row.peer_count,
        verification_status=row.verification_status or VerificationStatus.VERIFIED,
        version=row.version,
        revision_reason=row.revision_reason,
        extra=_extra(row.extra_json),
        counter_evidence=counter_evidence_of(row),
    )


def _timeline_entry(row: AlertAction) -> TimelineEntry:
    return TimelineEntry(
        id=row.id,
        action=row.action,
        from_state=row.from_state,
        to_state=row.to_state,
        actor_id=row.actor_id,
        actor_kind=row.actor_kind,
        from_owner_id=row.from_owner_id,
        to_owner_id=row.to_owner_id,
        note=row.note,
        reason_code=row.reason_code,
        created_at=row.created_at,
    )


def _task_row(row: ResearchTask) -> ResearchTaskRow:
    return ResearchTaskRow(
        id=row.id,
        alert_id=row.alert_id,
        title=row.title,
        detail=row.detail,
        status=row.status,
        owner_id=row.owner_id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        due_at=row.due_at,
        closed_at=row.closed_at,
        outcome=row.outcome,
        created_at=row.created_at,
    )


def _summary(session: Session, *, now: datetime) -> QueueSummary:
    counts: dict[AlertState, int] = {
        state: int(count)
        for state, count in session.execute(
            select(Alert.state, func.count()).where(_PUBLISHED).group_by(Alert.state)
        ).all()
    }
    overdue = session.execute(
        select(func.count())
        .select_from(Alert)
        .where(_PUBLISHED)
        .where(Alert.state.in_(tuple(OPEN_ALERT_STATES)))
        .where(Alert.sla_due_ts.isnot(None), Alert.sla_due_ts < now)
    ).scalar_one()

    return QueueSummary(
        unclaimed=counts.get(AlertState.PUBLISHED, 0)
        + counts.get(AlertState.TENTATIVE, 0),
        in_progress=counts.get(AlertState.CLAIMED, 0),
        awaiting_review=counts.get(AlertState.IN_REVIEW, 0),
        closed=counts.get(AlertState.RESOLVED, 0)
        + counts.get(AlertState.ACTIONED, 0)
        + counts.get(AlertState.DISMISSED, 0),
        false_positive=counts.get(AlertState.FALSE_POSITIVE, 0),
        overdue=int(overdue or 0),
    )


def _load(session: Session, alert_id: int) -> Alert:
    """Fetch an alert that has actually been published.

    A ``DETECTED`` row returns 404 rather than 403. It exists, but it is not a
    finding yet — and saying "you may not see this" would leak the fact that the
    detector fired on an entity we have not published anything about.
    """
    alert = session.get(Alert, alert_id)
    if alert is None or alert.published_at is None:
        raise HTTPException(status_code=404, detail=f"unknown alert {alert_id}")
    return alert


def _guard(operation) -> None:  # type: ignore[no-untyped-def]
    """Turn a refused transition into a 409 rather than a 500.

    The state machine rejecting a move is a normal outcome — two people opening the
    same alert and both pressing 认领 is a Tuesday, not a server fault.
    """
    try:
        operation()
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = [
    "available_actions",
    "confidence_of",
    "counter_evidence_of",
    "queue_row",
    "router",
]
