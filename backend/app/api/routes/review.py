"""Retrospectives and threshold review — the last step of the loop.

Detect, verify, explain, act, and then *review*. Without this page the thresholds
never move, and a detector that cries wolf every Monday keeps its place in the queue
because nobody counted how often it was wrong.

Two views of the same window. ``rows`` is one closed finding per line, for reading.
``detectors`` is the aggregate a threshold argument actually needs: how many published,
how many were acted on, how many were false, and how long each took to close.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from statistics import median

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Limit, SessionDep
from app.api.naming import Resolver
from app.api.routes.alert_queue import counter_evidence_of
from app.db.base import as_utc, utcnow
from app.models.alerts import Alert, AlertEvidence
from app.models.enums import (
    CLOSED_ALERT_STATES,
    AlertActionType,
    AlertState,
    DetectorFamily,
)
from app.models.workflow import AlertAction
from app.schemas.common import Meta
from app.schemas.editions import DetectorReviewRow, Retrospective, RetrospectiveRow

router = APIRouter(tags=["review"])

#: Default review window. A month is long enough for a seasonal pattern to show and
#: short enough that a threshold changed since then has not already invalidated it.
_DEFAULT_DAYS = 30

_NOTE = (
    "误报率只在已关闭的告警上计算：分母为 0 时留空，而不是显示 0%——"
    "「没有案例」不能拿来论证放宽阈值。"
)

#: Closing actions, so the reason and note can be read off the action stream rather
#: than duplicated onto the alert row.
_CLOSING_ACTIONS = (
    AlertActionType.RESOLVE,
    AlertActionType.MARK_FALSE_POSITIVE,
    AlertActionType.DISMISS,
)


@router.get("/retrospective", response_model=Retrospective)
def retrospective(
    session: SessionDep,
    days: int = Query(default=_DEFAULT_DAYS, ge=1, le=365),
    detector: str | None = Query(default=None),
    family: DetectorFamily | None = Query(default=None),
    limit: Limit = 200,
) -> Retrospective:
    now = utcnow()
    cutoff = now - timedelta(days=days)

    # Published findings only. An unpublished detector output never occupied anyone's
    # attention, so counting it as a false positive would flatter every threshold.
    stmt = (
        select(Alert)
        .where(Alert.published_at.isnot(None))
        .where(Alert.published_at >= cutoff)
    )
    if detector:
        stmt = stmt.where(Alert.detector == detector)
    if family is not None:
        stmt = stmt.where(Alert.family == family)
    alerts = list(session.execute(stmt).scalars().all())

    closed = [a for a in alerts if a.state in CLOSED_ALERT_STATES]
    closed.sort(key=lambda a: a.resolved_ts or a.last_seen_ts, reverse=True)

    resolver = Resolver(session)
    closures = _closing_actions(session, [a.id for a in closed])
    rows = [_row(a, resolver, closures.get(a.id)) for a in closed[:limit]]

    return Retrospective(
        meta=Meta(as_of=now, note=_NOTE, row_count=len(rows)),
        window_days=days,
        rows=rows,
        detectors=_detector_rows(session, alerts),
    )


# --- helpers -----------------------------------------------------------------


def _row(
    alert: Alert, resolver: Resolver, closure: AlertAction | None
) -> RetrospectiveRow:
    hours = _hours(alert.published_at, alert.resolved_ts)
    return RetrospectiveRow(
        alert_id=alert.id,
        detector=alert.detector,
        family=alert.family,
        severity=alert.severity,
        final_state=alert.state,
        entity_id=alert.entity_id,
        entity_name=resolver.name(alert.entity_type, alert.entity_id),
        headline_zh=alert.headline_zh,
        owner_id=alert.owner_id,
        published_at=alert.published_at,
        resolved_ts=alert.resolved_ts,
        time_to_close_hours=hours,
        reason_code=closure.reason_code if closure else None,
        outcome_note=closure.note if closure else None,
        within_sla=_within_sla(alert),
    )


def _detector_rows(session: Session, alerts: list[Alert]) -> list[DetectorReviewRow]:
    """Aggregate the window by detector.

    Grouped in Python rather than in SQL because the false-positive rate has to be
    left null on a zero denominator, and ``COUNT(...) / COUNT(...)`` in the database
    would return either 0 or an error depending on the dialect.
    """
    by_detector: dict[str, list[Alert]] = {}
    for alert in alerts:
        by_detector.setdefault(alert.detector, []).append(alert)

    counter_codes = _counter_evidence_codes(session, [a.id for a in alerts])

    rows: list[DetectorReviewRow] = []
    for name, group in by_detector.items():
        actioned = sum(1 for a in group if a.state is AlertState.RESOLVED)
        false_positive = sum(1 for a in group if a.state is AlertState.FALSE_POSITIVE)
        dismissed = sum(1 for a in group if a.state is AlertState.DISMISSED)
        open_count = sum(1 for a in group if a.state not in CLOSED_ALERT_STATES)
        closed_count = actioned + false_positive + dismissed

        durations = [
            hours
            for hours in (_hours(a.published_at, a.resolved_ts) for a in group)
            if hours is not None
        ]
        codes = Counter(
            code for alert in group for code in counter_codes.get(alert.id, ())
        )
        rows.append(
            DetectorReviewRow(
                detector=name,
                family=group[0].family,
                published=len(group),
                actioned=actioned,
                false_positive=false_positive,
                dismissed=dismissed,
                open_count=open_count,
                false_positive_rate=(
                    false_positive / closed_count if closed_count else None
                ),
                median_time_to_close_hours=(
                    round(median(durations), 2) if durations else None
                ),
                top_counter_evidence=(codes.most_common(1)[0][0] if codes else None),
            )
        )

    # Worst first: the detector producing the most false positives is the one a
    # threshold review is convened for.
    rows.sort(key=lambda r: (r.false_positive_rate or 0, r.published), reverse=True)
    return rows


def _closing_actions(session: Session, alert_ids: list[int]) -> dict[int, AlertAction]:
    """The action that closed each alert, so the reason travels with the row."""
    if not alert_ids:
        return {}
    rows = (
        session.execute(
            select(AlertAction)
            .where(AlertAction.alert_id.in_(alert_ids))
            .where(AlertAction.action.in_(_CLOSING_ACTIONS))
            .order_by(AlertAction.created_at.desc())
        )
        .scalars()
        .all()
    )
    latest: dict[int, AlertAction] = {}
    for row in rows:
        latest.setdefault(row.alert_id, row)
    return latest


def _counter_evidence_codes(
    session: Session, alert_ids: list[int]
) -> dict[int, tuple[str, ...]]:
    """The counter-evidence codes on each alert's newest evidence row.

    Usually the fastest route to what a threshold is getting wrong: a detector whose
    findings are all flagged ``single_venue`` needs a cross-venue condition, not a
    higher bar.
    """
    if not alert_ids:
        return {}
    rows = (
        session.execute(
            select(AlertEvidence)
            .where(AlertEvidence.alert_id.in_(alert_ids))
            .order_by(AlertEvidence.snapshot_ts.desc())
        )
        .scalars()
        .all()
    )
    out: dict[int, tuple[str, ...]] = {}
    for row in rows:
        if row.alert_id in out:
            continue
        out[row.alert_id] = tuple(item.code for item in counter_evidence_of(row))
    return out


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    delta = as_utc(end) - as_utc(start)
    return round(delta.total_seconds() / 3600, 2)


def _within_sla(alert: Alert) -> bool | None:
    """Whether the close beat the clock that was in force at publication.

    Null rather than ``True`` when there was no SLA. A LOW finding carries no clock,
    and counting it as met would make every quiet month look like a triumph.
    """
    if alert.sla_due_ts is None or alert.resolved_ts is None:
        return None
    return as_utc(alert.resolved_ts) <= as_utc(alert.sla_due_ts)


__all__ = ["router"]
