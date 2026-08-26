"""Issuance candidates: a heatmap that ranks, and a gate that only a person opens.

The four readiness columns are independently normalised states. They are shown side
by side so a reviewer can see *which* dimension is blocking, and there is no total
anywhere in this module — not in the schema, not in the ordering, not in the response.
A row sum would turn a diagnostic into an auto-approver, and "liquidity 0.9, custody
0.1" would average to a comfortable 0.5 that hides exactly the blocker.

Stages move through :func:`services.workflow.candidate.decide`, which requires a named
person and a stated reason and writes both to an append-only gate log.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Limit, SessionDep, WriterDep
from app.api.naming import Resolver
from app.db.base import utcnow
from app.models.enums import CandidateDecision, CandidateStage, EntityType
from app.models.workflow import CandidateGateLog, IssuanceCandidate
from app.schemas.common import Meta
from app.schemas.workflow import (
    CandidateDetail,
    CandidateList,
    CandidateRow,
    GateDecisionRequest,
    GateLogRow,
    ReadinessCell,
)
from app.services.workflow import candidate as service

router = APIRouter(tags=["candidates"])

#: Column labels, in display order, as ``(key, dimension, label, basis)``. ``key`` is
#: the model attribute so a renamed column fails loudly here rather than rendering an
#: unlabelled heatmap cell; ``dimension`` is what :class:`candidate.Readiness` calls
#: the same thing.
_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("demand_score", "demand", "需求", "该标的的市场需求强度，按同组归一化"),
    ("liquidity_score", "liquidity", "流动性", "可承接规模，按深度与滑点归一化"),
    (
        "competition_score",
        "competition",
        "竞争",
        "同类产品的拥挤程度，越高表示空间越大",
    ),
    ("feasibility_score", "feasibility", "可行性", "托管、合规与运营的落地难度"),
)

#: ``Readiness.weakest`` names a dimension; the heatmap is keyed by column. Translated
#: here so ``weakest_dimension`` always matches a ``ReadinessCell.key`` the front end
#: can actually highlight — the one thing that figure exists to do.
_KEY_BY_DIMENSION: dict[str, str] = {dim: key for key, dim, _, _ in _COLUMNS}

#: Which decisions the gate control may offer, per stage. A candidate already at the
#: last stage cannot advance, and an exit state cannot be decided on again at all —
#: reopening one is a new candidate decision, not a fifth button.
TERMINAL_STAGES = (CandidateStage.DECLINED, CandidateStage.PARKED)

_NOTE = (
    "四列独立归一化，仅用于定位短板与排序；不存在总分列，"
    "阶段推进只能由记录在案的人工决策触发。"
)


@router.get("/candidates", response_model=CandidateList)
def candidates(
    session: SessionDep,
    stage: CandidateStage | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    underlying: str | None = Query(default=None),
    include_closed: bool = Query(
        default=False,
        description="Include declined and parked candidates. Off by default.",
    ),
    limit: Limit = 100,
) -> CandidateList:
    stmt = select(IssuanceCandidate)
    if stage is not None:
        stmt = stmt.where(IssuanceCandidate.stage == stage)
    elif not include_closed:
        stmt = stmt.where(IssuanceCandidate.stage.notin_(TERMINAL_STAGES))
    if owner_id:
        stmt = stmt.where(IssuanceCandidate.owner_id == owner_id)
    if underlying:
        stmt = stmt.where(IssuanceCandidate.underlying_id == underlying)

    rows = list(session.execute(stmt.limit(limit)).scalars().all())
    resolver = Resolver(session)
    # Ordered in Python: the key is a stage position plus the weakest column, and
    # neither is a sortable expression the database could evaluate without first
    # inventing the total this module refuses to have.
    rows.sort(key=_rank, reverse=True)

    return CandidateList(
        meta=Meta(as_of=utcnow(), note=_NOTE, row_count=len(rows)),
        rows=[candidate_row(row, resolver) for row in rows],
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
def candidate_detail(candidate_id: int, session: SessionDep) -> CandidateDetail:
    row = _load(session, candidate_id)
    return CandidateDetail(
        candidate=candidate_row(row, Resolver(session)),
        history=[_gate_row(entry) for entry in service.history(session, candidate_id)],
    )


@router.post("/candidates/{candidate_id}/decide", response_model=CandidateDetail)
def decide(
    candidate_id: int,
    payload: GateDecisionRequest,
    session: SessionDep,
    actor: WriterDep,
) -> CandidateDetail:
    """Record a gate decision.

    The readiness figures are frozen into the log alongside it, so a later data
    revision cannot retroactively justify — or undermine — a call made on what was
    known at the time.
    """
    row = _load(session, candidate_id)
    readiness = service.readiness_of(row)
    try:
        service.decide(
            session,
            row,
            actor,
            decision=payload.decision,
            reason=payload.reason,
            evidence={
                "demand": readiness.demand,
                "liquidity": readiness.liquidity,
                "competition": readiness.competition,
                "feasibility": readiness.feasibility,
                "weakest": readiness.weakest[0] if readiness.weakest else None,
            },
        )
    except service.GateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return candidate_detail(candidate_id, session)


# --- helpers -----------------------------------------------------------------


def _load(session: Session, candidate_id: int) -> IssuanceCandidate:
    row = session.get(IssuanceCandidate, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown candidate {candidate_id}")
    return row


def _rank(row: IssuanceCandidate) -> tuple[int, float]:
    """Queue position: how far along, then how strong the weakest column is.

    Deliberately not a quality score. Two candidates with the same key are equally
    placed in the queue and say nothing about each other's merit.
    """
    weakest = service.readiness_of(row).weakest
    stage_index = (
        service.STAGE_ORDER.index(row.stage) if row.stage in service.STAGE_ORDER else -1
    )
    return (stage_index, float(weakest[1]) if weakest else -1.0)


def candidate_row(row: IssuanceCandidate, resolver: Resolver) -> CandidateRow:
    readiness = service.readiness_of(row)
    weakest = readiness.weakest
    return CandidateRow(
        id=row.id,
        underlying_id=row.underlying_id,
        underlying_name=resolver.name(EntityType.UNDERLYING, row.underlying_id),
        stage=row.stage,
        owner_id=row.owner_id,
        readiness=[
            ReadinessCell(
                key=key,
                label=label,
                value=getattr(row, key),
                basis=basis if getattr(row, key) is not None else "尚未评估",
            )
            for key, _dimension, label, basis in _COLUMNS
        ],
        weakest_dimension=_KEY_BY_DIMENSION.get(weakest[0]) if weakest else None,
        rationale=row.rationale,
        counter_rationale=row.counter_rationale,
        last_reviewed_at=row.last_reviewed_at,
        allowed_decisions=_allowed_decisions(row.stage),
    )


def _allowed_decisions(stage: CandidateStage) -> list[CandidateDecision]:
    """What the gate control may offer, resolved server-side.

    The browser renders from this rather than re-deriving the ladder, so a button can
    never offer a move the service will refuse.
    """
    if stage in TERMINAL_STAGES:
        return []
    allowed = [
        CandidateDecision.HOLD,
        CandidateDecision.DECLINE,
        CandidateDecision.PARK,
    ]
    if stage in service.STAGE_ORDER[:-1]:
        allowed.insert(0, CandidateDecision.ADVANCE)
    return allowed


def _gate_row(entry: CandidateGateLog) -> GateLogRow:
    return GateLogRow(
        id=entry.id,
        from_stage=entry.from_stage,
        to_stage=entry.to_stage,
        decision=entry.decision,
        reason=entry.reason,
        decided_by=entry.decided_by,
        created_at=entry.created_at,
    )


__all__ = ["TERMINAL_STAGES", "candidate_row", "router"]
