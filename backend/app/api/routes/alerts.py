"""The alert stream and the evidence behind each alert.

An alert without its inputs is an assertion. Every row served here can be expanded
into the raw value, the baseline it was judged against, the sample size and the
market session — because an alert nobody can justify to management gets ignored, and
then so does the next one.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import Limit, SessionDep
from app.models.alerts import Alert, AlertEvidence
from app.models.enums import AlertSeverity, AlertStatus, DetectorFamily, EntityType
from app.schemas.common import Meta
from app.schemas.market import AlertDetail, AlertList, AlertRow, EvidenceRow

router = APIRouter(tags=["alerts"])

_NOTE = (
    "Every alert clears a $50,000 absolute floor: $500 to $5,000 is +900% and "
    "commercially meaningless. TENTATIVE fired on one snapshot; CONFIRMED survived a "
    "second, because a single-snapshot spike is often a data artefact."
)


@router.get("/alerts", response_model=AlertList)
def alerts(
    session: SessionDep,
    severity: AlertSeverity | None = Query(default=None),
    family: DetectorFamily | None = Query(default=None),
    detector: str | None = Query(default=None),
    entity_type: EntityType | None = Query(default=None),
    status: AlertStatus | None = Query(default=None),
    since: datetime | None = Query(
        default=None, description="Only alerts last seen at or after this instant."
    ),
    include_resolved: bool = Query(default=False),
    limit: Limit = 100,
) -> AlertList:
    stmt = select(Alert)
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    if family is not None:
        stmt = stmt.where(Alert.family == family)
    if detector is not None:
        stmt = stmt.where(Alert.detector == detector)
    if entity_type is not None:
        stmt = stmt.where(Alert.entity_type == entity_type)
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    if since is not None:
        stmt = stmt.where(Alert.last_seen_ts >= since)
    if not include_resolved:
        stmt = stmt.where(Alert.resolved_ts.is_(None))

    # Both MySQL and SQLite sort NULLs last under DESC: an unscored alert belongs
    # below the scored ones, not above them.
    stmt = stmt.order_by(Alert.score.desc(), Alert.last_seen_ts.desc()).limit(limit)
    rows = [alert_row(a) for a in session.execute(stmt).scalars().all()]

    return AlertList(
        meta=Meta(
            as_of=max((r.last_seen_ts for r in rows), default=_epoch()),
            scopes=sorted({r.metric_scope for r in rows}),
            note=_NOTE,
            row_count=len(rows),
        ),
        rows=rows,
    )


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
def alert_detail(alert_id: int, session: SessionDep) -> AlertDetail:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"unknown alert {alert_id}")

    stmt = (
        select(AlertEvidence)
        .where(AlertEvidence.alert_id == alert_id)
        .order_by(AlertEvidence.snapshot_ts.desc())
    )
    evidence = [_evidence(e) for e in session.execute(stmt).scalars().all()]
    return AlertDetail(alert=alert_row(alert), evidence=evidence)


def alert_row(alert: Alert) -> AlertRow:
    """Shared with the underlying-360 view, which lists the same alerts."""
    return AlertRow(
        id=alert.id,
        detector=alert.detector,
        family=alert.family,
        severity=alert.severity,
        score=float(alert.score) if alert.score is not None else None,
        status=alert.status,
        entity_type=alert.entity_type,
        entity_id=alert.entity_id,
        metric_scope=alert.metric_scope,
        market_session=alert.market_session,
        headline_zh=alert.headline_zh,
        headline_en=alert.headline_en,
        first_seen_ts=alert.first_seen_ts,
        last_seen_ts=alert.last_seen_ts,
        occurrence_count=alert.occurrence_count,
    )


def _evidence(row: AlertEvidence) -> EvidenceRow:
    return EvidenceRow(
        rule_name=row.rule_name,
        snapshot_ts=row.snapshot_ts,
        observed_value=row.observed_value,
        baseline_median=row.baseline_median,
        baseline_mad=row.baseline_mad,
        robust_z=float(row.robust_z) if row.robust_z is not None else None,
        sample_size=row.sample_size,
        market_session=row.market_session,
        peer_count=row.peer_count,
        extra=_extra(row.extra_json),
    )


def _extra(payload: str | None) -> dict[str, object]:
    """Detector-specific inputs, or an empty mapping if they cannot be read.

    A malformed blob must not take down the alert it belongs to: the columns beside
    it already carry enough to justify the decision.
    """
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except ValueError:
        return {"_unparsed": payload}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _epoch() -> datetime:
    from datetime import timezone

    return datetime(1970, 1, 1, tzinfo=timezone.utc)
