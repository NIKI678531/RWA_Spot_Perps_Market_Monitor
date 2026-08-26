"""Issuance candidates and the gates they pass through.

The single rule this module enforces: **no composite score approves anything.**

The four readiness figures on a candidate are independently normalised states, shown
side by side in a heatmap so a reader can see *which* dimension is weak. Adding them
into a total destroys exactly that information — "demand 0.9, feasibility 0.1"
averages to the same 0.5 as "demand 0.5, feasibility 0.5", and the first is blocked
while the second is merely unremarkable. So there is no total here, no ranking by
one, and no automatic stage advance. A candidate moves only through
:func:`decide`, which requires a named human and a written reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.enums import CandidateDecision, CandidateStage
from app.models.workflow import CandidateGateLog, IssuanceCandidate
from app.services.workflow import audit
from app.services.workflow.alert_lifecycle import Actor

#: The evaluation ladder, in order. ``DECLINED`` and ``PARKED`` are exits, not rungs.
STAGE_ORDER: tuple[CandidateStage, ...] = (
    CandidateStage.WATCH,
    CandidateStage.SCREENING,
    CandidateStage.FEASIBILITY,
    CandidateStage.PROPOSAL,
    CandidateStage.APPROVED,
)

#: The heatmap columns, in display order. Each is normalised independently; the
#: absence of a fifth "total" column here is deliberate and load-bearing.
READINESS_DIMENSIONS: tuple[str, ...] = (
    "demand_score",
    "liquidity_score",
    "competition_score",
    "feasibility_score",
)


class GateError(ValueError):
    """A stage move that is not a legal step on the ladder."""


@dataclass(frozen=True, slots=True)
class Readiness:
    """One candidate's four readiness states, deliberately without a total."""

    demand: Decimal | None
    liquidity: Decimal | None
    competition: Decimal | None
    feasibility: Decimal | None

    @property
    def weakest(self) -> tuple[str, Decimal] | None:
        """The dimension a reviewer should look at first.

        Returned instead of a total because it answers the question a total is
        usually misused to answer — "is this ready?" — without implying the others
        can compensate for it.
        """
        scored = [
            (name, value)
            for name, value in (
                ("demand", self.demand),
                ("liquidity", self.liquidity),
                ("competition", self.competition),
                ("feasibility", self.feasibility),
            )
            if value is not None
        ]
        return min(scored, key=lambda item: item[1]) if scored else None

    @property
    def complete(self) -> bool:
        return all(
            value is not None
            for value in (
                self.demand,
                self.liquidity,
                self.competition,
                self.feasibility,
            )
        )


def readiness_of(candidate: IssuanceCandidate) -> Readiness:
    return Readiness(
        demand=candidate.demand_score,
        liquidity=candidate.liquidity_score,
        competition=candidate.competition_score,
        feasibility=candidate.feasibility_score,
    )


def _next_stage(current: CandidateStage) -> CandidateStage:
    if current not in STAGE_ORDER:
        raise GateError(f"{current.value} is an exit state; it has no next stage")
    index = STAGE_ORDER.index(current)
    if index + 1 >= len(STAGE_ORDER):
        raise GateError("already at the final stage")
    return STAGE_ORDER[index + 1]


def upsert(
    session: Session,
    *,
    underlying_id: str,
    rationale: str | None = None,
    counter_rationale: str | None = None,
    source_alert_id: int | None = None,
) -> IssuanceCandidate:
    """Put an underlying on the evaluation list, or return the existing entry.

    Idempotent on purpose: an underlying that keeps producing alerts should
    accumulate history against one candidate row, not spawn a new one each time.
    """
    existing = session.execute(
        select(IssuanceCandidate).where(
            IssuanceCandidate.underlying_id == underlying_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    candidate = IssuanceCandidate(
        underlying_id=underlying_id,
        stage=CandidateStage.WATCH,
        rationale=rationale,
        counter_rationale=counter_rationale,
        source_alert_id=source_alert_id,
    )
    session.add(candidate)
    return candidate


def decide(
    session: Session,
    candidate: IssuanceCandidate,
    actor: Actor,
    *,
    decision: CandidateDecision,
    reason: str,
    evidence: Mapping[str, Any] | None = None,
) -> CandidateGateLog:
    """Record a human decision at a gate, and move the candidate accordingly.

    ``reason`` is required and not defaulted. A gate crossed without a stated reason
    is not a decision, it is a state change, and six months later nobody can tell the
    two apart.

    The evidence is frozen into the log as JSON at decision time so a later data
    revision cannot retroactively justify — or undermine — a call that was made on
    what was known then.
    """
    if not actor.user_id:
        raise GateError("a gate decision must name the person taking it")
    if not reason.strip():
        raise GateError("a gate decision must state its reason")

    from_stage = candidate.stage
    if decision is CandidateDecision.ADVANCE:
        to_stage = _next_stage(from_stage)
    elif decision is CandidateDecision.DECLINE:
        to_stage = CandidateStage.DECLINED
    elif decision is CandidateDecision.PARK:
        to_stage = CandidateStage.PARKED
    else:
        to_stage = from_stage  # HOLD: reviewed, deliberately not moved

    entry = CandidateGateLog(
        candidate_id=candidate.id,
        from_stage=from_stage,
        to_stage=to_stage,
        decision=decision,
        reason=reason,
        decided_by=actor.user_id,
        evidence_json=(
            json.dumps(dict(evidence), ensure_ascii=False, default=str)
            if evidence
            else None
        ),
    )
    session.add(entry)

    candidate.stage = to_stage
    candidate.last_reviewed_at = utcnow()

    audit.record(
        session,
        entity_kind="issuance_candidate",
        entity_id=candidate.id,
        action=f"gate_{decision.value}",
        actor_id=actor.user_id,
        actor_role=actor.role,
        before={"stage": from_stage.value},
        after={"stage": to_stage.value, "reason": reason},
    )
    return entry


def set_readiness(
    session: Session,
    candidate: IssuanceCandidate,
    actor: Actor,
    *,
    demand: Decimal | None = None,
    liquidity: Decimal | None = None,
    competition: Decimal | None = None,
    feasibility: Decimal | None = None,
) -> IssuanceCandidate:
    """Update the readiness states. Never advances a stage as a side effect."""
    before = {name: getattr(candidate, name) for name in READINESS_DIMENSIONS}
    for name, value in (
        ("demand_score", demand),
        ("liquidity_score", liquidity),
        ("competition_score", competition),
        ("feasibility_score", feasibility),
    ):
        if value is not None:
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{name} must be normalised to 0-1")
            setattr(candidate, name, value)

    audit.record(
        session,
        entity_kind="issuance_candidate",
        entity_id=candidate.id,
        action="set_readiness",
        actor_id=actor.user_id,
        actor_role=actor.role,
        before=before,
        after={name: getattr(candidate, name) for name in READINESS_DIMENSIONS},
    )
    return candidate


def history(session: Session, candidate_id: int) -> Sequence[CandidateGateLog]:
    stmt = (
        select(CandidateGateLog)
        .where(CandidateGateLog.candidate_id == candidate_id)
        .order_by(CandidateGateLog.created_at)
    )
    return session.execute(stmt).scalars().all()


__all__ = [
    "READINESS_DIMENSIONS",
    "STAGE_ORDER",
    "GateError",
    "Readiness",
    "decide",
    "history",
    "readiness_of",
    "set_readiness",
    "upsert",
]
