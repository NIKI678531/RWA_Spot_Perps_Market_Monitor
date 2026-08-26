"""Request and response models for the business layer.

The alert schemas here differ from those in ``market.py`` in one way that matters:
they carry the work-queue fields — state, owner, SLA, confirmations, counter-evidence
— because the R1 surfaces are a queue, not a feed. A feed row needs a headline; a
queue row needs to tell you whether it is yours, whether it is late, and what argues
against acting on it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import (
    AlertActionType,
    AlertSeverity,
    AlertState,
    CandidateDecision,
    CandidateStage,
    CoverageState,
    DataGapKind,
    DataGapStatus,
    DetectorFamily,
    EntityType,
    ResearchTaskStatus,
    VerificationStatus,
)
from app.schemas.common import Confidence, CounterEvidenceItem, Meta, MetricValue


class QueueAlertRow(BaseModel):
    """One row in the anomaly work queue."""

    id: int
    detector: str
    family: DetectorFamily
    #: The clause that states what kind of comparison produced this. Server-side so
    #: the two detector families cannot end up described in each other's language.
    family_claim: str
    severity: AlertSeverity
    score: float | None = None
    state: AlertState
    entity_type: EntityType
    entity_id: str
    entity_name: str | None = None
    metric_scope: MetricScope
    market_session: MarketSession
    headline_zh: str
    headline_en: str | None = None

    owner_id: str | None = None
    owner_name: str | None = None
    confirmation_count: int = 1
    confirmations_required: int = 1
    evidence_completeness: Decimal | None = None

    first_seen_ts: datetime
    last_seen_ts: datetime
    published_at: datetime | None = None
    sla_due_ts: datetime | None = None
    #: Precomputed rather than left to the browser: "late" must mean the same thing
    #: in the queue, the digest and the retrospective, and three clocks do not agree.
    is_overdue: bool = False
    deferred_until: datetime | None = None
    occurrence_count: int = 1


class QueueSummary(BaseModel):
    """The five counters above the queue (UI-LAYOUT §2.4)."""

    unclaimed: int = 0
    in_progress: int = 0
    awaiting_review: int = 0
    closed: int = 0
    false_positive: int = 0
    #: Open alerts past their SLA, across every bucket above.
    overdue: int = 0


class AlertQueue(BaseModel):
    meta: Meta
    summary: QueueSummary
    rows: list[QueueAlertRow]


class TimelineEntry(BaseModel):
    """One entry in the append-only action stream."""

    id: int
    action: AlertActionType
    from_state: AlertState | None = None
    to_state: AlertState | None = None
    actor_id: str | None = None
    actor_kind: str = "user"
    from_owner_id: str | None = None
    to_owner_id: str | None = None
    note: str | None = None
    reason_code: str | None = None
    created_at: datetime


class QueueEvidenceRow(BaseModel):
    """One firing's inputs, with what argues against them attached."""

    rule_name: str
    snapshot_ts: datetime
    observed_value: Decimal | None = None
    baseline_median: Decimal | None = None
    baseline_mad: Decimal | None = None
    robust_z: float | None = None
    sample_size: int | None = None
    market_session: MarketSession
    peer_count: int | None = None
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    version: int = 1
    revision_reason: str | None = None
    extra: dict[str, object] = Field(default_factory=dict)
    counter_evidence: list[CounterEvidenceItem] = Field(default_factory=list)


class AlertWorkItem(BaseModel):
    """Everything the evidence-and-action drawer needs, in one response."""

    alert: QueueAlertRow
    confidence: Confidence
    #: Newest first.
    evidence: list[QueueEvidenceRow]
    counter_evidence: list[CounterEvidenceItem] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    tasks: list["ResearchTaskRow"] = Field(default_factory=list)
    #: What the caller is allowed to do next, resolved on the server. The front end
    #: renders buttons from this rather than re-deriving the state machine, so the
    #: two cannot disagree about whether an alert can be closed.
    available_actions: list[str] = Field(default_factory=list)
    close_reasons: list[str] = Field(default_factory=list)


# --- write payloads ----------------------------------------------------------


class ClaimRequest(BaseModel):
    note: str | None = None


class ReassignRequest(BaseModel):
    to_user_id: str
    note: str | None = None


class NoteRequest(BaseModel):
    note: str


class ActionRequest(BaseModel):
    note: str


class DeferRequest(BaseModel):
    until: datetime
    note: str | None = None


class CloseRequest(BaseModel):
    """Closing an alert. One of ``note`` or ``reason_code`` is mandatory.

    Enforced in the service layer rather than here, so the same rule applies to the
    scheduler's own closures and to anything that calls the lifecycle directly.
    """

    to_state: AlertState
    note: str | None = None
    reason_code: str | None = None


class TaskCreateRequest(BaseModel):
    title: str
    detail: str | None = None
    owner_id: str | None = None
    due_at: datetime | None = None


class TaskStatusRequest(BaseModel):
    """Moving a task along.

    ``outcome`` is mandatory when completing, and the service layer enforces it — a
    task that ended with no statement of what was found tells the next reader nothing.
    """

    status: ResearchTaskStatus
    outcome: str | None = None


class ResearchTaskRow(BaseModel):
    id: int
    alert_id: int | None = None
    title: str
    detail: str | None = None
    status: ResearchTaskStatus
    owner_id: str | None = None
    entity_type: EntityType | None = None
    entity_id: str | None = None
    due_at: datetime | None = None
    closed_at: datetime | None = None
    outcome: str | None = None
    created_at: datetime


class ResearchTaskList(BaseModel):
    meta: Meta
    rows: list[ResearchTaskRow]


# --- issuance candidates -----------------------------------------------------


class ReadinessCell(BaseModel):
    """One heatmap cell. Independently normalised, never summed with its siblings."""

    key: str
    label: str
    value: Decimal | None = None
    #: Why this cell reads the way it does, so a shade is never the only encoding.
    basis: str | None = None


class CandidateRow(BaseModel):
    id: int
    underlying_id: str
    underlying_name: str
    stage: CandidateStage
    owner_id: str | None = None
    #: The four columns, in display order. There is deliberately no total field: a
    #: row sum would turn a diagnostic heatmap into an approval score.
    readiness: list[ReadinessCell]
    #: The lowest-scoring dimension — the thing to look at first. Not a summary of
    #: the row, and not comparable across rows.
    weakest_dimension: str | None = None
    rationale: str | None = None
    counter_rationale: str | None = None
    last_reviewed_at: datetime | None = None
    #: Present so the UI can render the gate control without guessing.
    allowed_decisions: list[CandidateDecision] = Field(default_factory=list)


class CandidateList(BaseModel):
    meta: Meta
    rows: list[CandidateRow]
    note: str = (
        "热力图各列独立归一化，仅用于排序与定位短板；任何一行都不产生总分，"
        "阶段推进只能由记录在案的人工决策触发。"
    )


class GateDecisionRequest(BaseModel):
    decision: CandidateDecision
    reason: str


class GateLogRow(BaseModel):
    id: int
    from_stage: CandidateStage | None = None
    to_stage: CandidateStage
    decision: CandidateDecision
    reason: str
    decided_by: str
    created_at: datetime


class CandidateDetail(BaseModel):
    candidate: CandidateRow
    history: list[GateLogRow]


# --- coverage and data gaps --------------------------------------------------


class CoverageRow(BaseModel):
    underlying_id: str
    underlying_name: str
    state: CoverageState
    product_id: str | None = None
    demand: MetricValue | None = None
    note: str | None = None
    assessed_at: datetime | None = None


class CoverageList(BaseModel):
    meta: Meta
    rows: list[CoverageRow]


class DataGapRow(BaseModel):
    id: int
    kind: DataGapKind
    status: DataGapStatus
    title: str
    detail: str | None = None
    blocks: str | None = None
    impact_score: Decimal | None = None
    entity_type: EntityType | None = None
    entity_id: str | None = None
    source_id: str | None = None
    owner_id: str | None = None
    is_blocking: bool = False
    first_seen_ts: datetime
    last_seen_ts: datetime
    occurrence_count: int = 1


class DataGapZone(BaseModel):
    """One of the five quality zones, with its own queue underneath it."""

    kind: DataGapKind
    label: str
    open_count: int = 0
    blocking_count: int = 0
    rows: list[DataGapRow] = Field(default_factory=list)


class DataGapQueue(BaseModel):
    meta: Meta
    zones: list[DataGapZone]
    #: True when at least one blocking gap is open. The status bar reads this to say
    #: whether a conclusion is publishable at all.
    publication_blocked: bool = False


class GapAssignRequest(BaseModel):
    owner_id: str


class GapCloseRequest(BaseModel):
    status: DataGapStatus
    note: str


AlertWorkItem.model_rebuild()

__all__ = [
    "ActionRequest",
    "AlertQueue",
    "AlertWorkItem",
    "CandidateDetail",
    "CandidateList",
    "CandidateRow",
    "ClaimRequest",
    "CloseRequest",
    "CoverageList",
    "CoverageRow",
    "DataGapQueue",
    "DataGapRow",
    "DataGapZone",
    "DeferRequest",
    "GapAssignRequest",
    "GapCloseRequest",
    "GateDecisionRequest",
    "GateLogRow",
    "NoteRequest",
    "QueueAlertRow",
    "QueueEvidenceRow",
    "QueueSummary",
    "ReadinessCell",
    "ReassignRequest",
    "ResearchTaskList",
    "ResearchTaskRow",
    "TaskCreateRequest",
    "TaskStatusRequest",
    "TimelineEntry",
]
