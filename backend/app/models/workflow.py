"""Business-object tables: the "act" and "review" halves of the loop.

Everything above this layer observes the market. Everything here records what people
did about it — who took an alert, what they concluded, what got built as a result.

Two properties hold across the whole module and are the reason it is separate from
``facts.py``:

* **Append-only history.** ``alert_action`` and ``audit_log`` are never updated. An
  alert's ``state`` column is a materialised view of its action stream, so the stream
  is the truth and the column is the index.
* **No pipeline writes.** These tables read analytics output; they never write a
  ``fact_*`` row. A business decision must not be able to change a measurement.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.metrics import MetricScope
from app.models.alerts import Alert
from app.db.base import (
    Base,
    created_at_column,
    enum_column,
    id_column,
    money_column,
)
from app.models.enums import (
    AlertActionType,
    AlertState,
    CandidateDecision,
    CandidateStage,
    CoverageState,
    DataGapKind,
    DataGapStatus,
    EntityType,
    ResearchTaskStatus,
)


def _pk() -> Mapped[int]:
    """Autoincrement surrogate key that works on both SQLite and MySQL."""
    return mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )


class AlertAction(Base):
    """One entry in an alert's append-only action stream.

    Rows are never updated or deleted. Reassigning an alert appends a REASSIGN row
    naming both the previous and the new owner rather than overwriting a field, so
    "who had this when it went wrong" survives the next reassignment.
    """

    __tablename__ = "alert_action"

    id: Mapped[int] = _pk()
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alert.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[AlertActionType] = enum_column(
        AlertActionType, nullable=False, index=True
    )
    #: The transition this action caused, if any. NOTE and CONFIRM change nothing and
    #: leave both null — a comment is part of the record without being a state change.
    from_state: Mapped[AlertState | None] = enum_column(AlertState, nullable=True)
    to_state: Mapped[AlertState | None] = enum_column(AlertState, nullable=True)

    #: Who acted. Null only for actions the scheduler takes on its own behalf
    #: (publication, automatic confirmation), which carry ``actor_kind='system'``.
    actor_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    actor_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default="user"
    )
    #: Owner before and after, for CLAIM / RELEASE / REASSIGN.
    from_owner_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    to_owner_id: Mapped[str | None] = mapped_column(String(96), nullable=True)

    #: Free-text rationale. Required by the service layer for every closing action —
    #: an alert closed without a reason cannot be reviewed, which defeats the review
    #: step of the loop.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Standardised close reason (``duplicate``, ``data_artefact``,
    #: ``below_materiality``, ``known_event``, ``no_action_needed``). Kept beside the
    #: free text so false-positive rates can be counted, not read.
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: For DEFER: when the alert returns to the queue.
    defer_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column(index=True)

    #: Present so the engine can append an action to an alert that has not been
    #: flushed yet — at that point the alert has no id to reference.
    alert: Mapped[Alert] = relationship(back_populates="actions")


class AuditLog(Base):
    """Every state-changing request, recorded in the same transaction as the change.

    Not a log file: a table, written inside the unit of work it describes. If the
    audit write can fail independently of the change, the change is not auditable —
    so a failure here rolls the change back with it.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = _pk()
    #: Which table and row changed, as ``entity_kind`` + natural or surrogate id.
    entity_kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Serialised ``{"before": …, "after": …}``. Kept as JSON text rather than
    #: columns: the shape differs per entity, and a generic audit reader only ever
    #: renders it as a diff.
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column(index=True)


class ResearchTask(Base):
    """Work spawned from a finding.

    An alert that produces no task and no decision was, in the end, noise. This table
    is how "we looked into it" becomes checkable.
    """

    __tablename__ = "research_task"

    id: Mapped[int] = _pk()
    #: Null for a task raised independently of any alert.
    alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ResearchTaskStatus] = enum_column(
        ResearchTaskStatus,
        nullable=False,
        default=ResearchTaskStatus.OPEN,
        index=True,
    )
    owner_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    #: What the task is about, so a task list can be grouped by underlying or venue
    #: without joining back through the alert.
    entity_type: Mapped[EntityType | None] = enum_column(EntityType, nullable=True)
    entity_id: Mapped[str | None] = id_column(nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class IssuanceCandidate(Base):
    """An underlying under evaluation for a product of our own.

    ``stage`` moves only through :class:`CandidateGateLog` — a recorded human
    decision. Nothing in this table is computed into an approval, and the readiness
    figures below exist to inform that decision, never to make it. Summing them into
    a total would turn a heatmap into an auto-approver.
    """

    __tablename__ = "issuance_candidate"
    __table_args__ = (
        UniqueConstraint("underlying_id", name="uq_issuance_candidate_underlying"),
    )

    id: Mapped[int] = _pk()
    underlying_id: Mapped[str] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=False, index=True
    )
    stage: Mapped[CandidateStage] = enum_column(
        CandidateStage, nullable=False, default=CandidateStage.WATCH, index=True
    )
    owner_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)

    #: Independently normalised 0-1 readiness states, one per heatmap column. They
    #: are shown side by side and never added: "liquidity 0.9, custody 0.1" is a
    #: blocked candidate, and an average of 0.5 hides exactly the blocker.
    demand_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    liquidity_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    competition_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    feasibility_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )

    #: Why this underlying is on the list at all, in one sentence.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What argues against it. Ships with the rationale, never on request.
    counter_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The alert that put it here, when there was one.
    source_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert.id", ondelete="SET NULL"), nullable=True
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()


class CandidateGateLog(Base):
    """One recorded human decision at an issuance gate.

    Append-only, like ``alert_action`` and for the same reason: the question a review
    asks six months later is not "what stage is it in" but "who moved it, when, on
    what evidence".
    """

    __tablename__ = "candidate_gate_log"

    id: Mapped[int] = _pk()
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("issuance_candidate.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_stage: Mapped[CandidateStage | None] = enum_column(
        CandidateStage, nullable=True
    )
    to_stage: Mapped[CandidateStage] = enum_column(CandidateStage, nullable=False)
    decision: Mapped[CandidateDecision] = enum_column(CandidateDecision, nullable=False)
    #: Never null. A gate crossed without a stated reason is not a decision.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(96), nullable=False)
    #: The evidence the decision was taken on, frozen as JSON at decision time so a
    #: later data revision cannot retroactively justify or undermine it.
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column(index=True)


class ProductCoverage(Base):
    """Whether our shelf answers the demand observed against one underlying."""

    __tablename__ = "product_coverage"
    __table_args__ = (
        UniqueConstraint("underlying_id", name="uq_product_coverage_underlying"),
    )

    id: Mapped[int] = _pk()
    underlying_id: Mapped[str] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=False, index=True
    )
    state: Mapped[CoverageState] = enum_column(
        CoverageState, nullable=False, default=CoverageState.GAP, index=True
    )
    #: Our product answering this underlying, when one exists.
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_own_product.product_id"), nullable=True, index=True
    )
    #: Market demand at the time coverage was last assessed, carried so a gap can be
    #: sorted by size without recomputing the whole market. One scope, stated.
    demand_value: Mapped[Decimal | None] = money_column()
    demand_scope: Mapped[MetricScope | None] = enum_column(MetricScope, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assessed_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class DataGap(Base):
    """A defect in the data that blocks or weakens a conclusion.

    The Data Quality page is a work queue, not a status board: every red cell on it
    is a row here that someone can be assigned. A quality problem nobody owns is a
    quality problem that stays.
    """

    __tablename__ = "data_gap"

    id: Mapped[int] = _pk()
    #: Which of the five quality zones this belongs to.
    kind: Mapped[DataGapKind] = enum_column(DataGapKind, nullable=False, index=True)
    status: Mapped[DataGapStatus] = enum_column(
        DataGapStatus, nullable=False, default=DataGapStatus.OPEN, index=True
    )
    #: Stable across scans so a recurring gap accumulates history instead of
    #: producing a new row every hour.
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[EntityType | None] = enum_column(EntityType, nullable=True)
    entity_id: Mapped[str | None] = id_column(nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What is downstream of this gap — which page or figure is degraded by it.
    blocks: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Severity as a 0-1 score so the queue can sort; deliberately not an enum
    #: mirroring AlertSeverity, because a data gap is not an alert and must not be
    #: counted alongside one.
    impact_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    owner_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    first_seen_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    resolved_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Required when status is ACCEPTED: why we are living with it.
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )
    is_blocking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


__all__ = [
    "AlertAction",
    "AuditLog",
    "CandidateGateLog",
    "DataGap",
    "IssuanceCandidate",
    "ProductCoverage",
    "ResearchTask",
]
