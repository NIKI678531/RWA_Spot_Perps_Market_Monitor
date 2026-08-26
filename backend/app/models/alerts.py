"""Alert tables.

An alert without its inputs is an assertion. ``alert_evidence`` carries the raw
value, the baseline, the sample size, the session and the rule name, so anyone
reading the alert can reconstruct the decision instead of taking it on faith.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.base import (
    Base,
    created_at_column,
    enum_column,
    id_column,
    money_column,
)
from app.models.enums import (
    AlertSeverity,
    AlertState,
    AlertStatus,
    DetectorFamily,
    EntityType,
    VerificationStatus,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle exists only for the type checker
    from app.models.workflow import AlertAction


class Alert(Base):
    """One detected demand anomaly."""

    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    #: Stable across snapshots for the same (detector, entity, metric) so a
    #: continuing condition updates one alert rather than emitting a new one hourly.
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    detector: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Cross-sectional and time-series detectors answer different questions and must
    #: stay separately reviewable; see ADR 0005.
    family: Mapped[DetectorFamily] = enum_column(
        DetectorFamily, nullable=False, index=True
    )
    entity_type: Mapped[EntityType] = enum_column(EntityType, nullable=False)
    entity_id: Mapped[str] = id_column(nullable=False, index=True)
    metric_scope: Mapped[MetricScope] = enum_column(MetricScope, nullable=False)
    market_session: Mapped[MarketSession] = enum_column(MarketSession, nullable=False)

    severity: Mapped[AlertSeverity] = enum_column(
        AlertSeverity, nullable=False, index=True
    )
    #: Continuous 0-1 score behind the severity bucket. Kept so the thresholds can be
    #: retuned without re-running detection over history.
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    #: TENTATIVE on first fire, CONFIRMED once it survives a second snapshot. A
    #: single-snapshot spike is frequently a data artefact.
    #:
    #: This is the *detector's* confidence and nothing else. What the organisation
    #: did about the finding lives in ``state``; the two move independently.
    status: Mapped[AlertStatus] = enum_column(AlertStatus, nullable=False, index=True)

    #: Business state. A materialised view of the ``alert_action`` stream, kept as a
    #: column so the work queue can filter without replaying history per row.
    state: Mapped[AlertState] = enum_column(
        AlertState, nullable=False, index=True, default=AlertState.DETECTED
    )
    #: How many consecutive same-session snapshots re-fired this finding. The
    #: publication gate requires two for a T* alert; an X* alert may publish at one
    #: because it is a peer comparison and has no history to be consistent with.
    confirmation_count: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )
    #: Exactly one owner, or nobody. A queue where everyone is responsible is a queue
    #: where nobody is.
    owner_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    #: When the gate let it through. Null while ``state`` is DETECTED, and the reason
    #: a DETECTED row can be excluded from every response with one predicate.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: When a response is late by policy. Drives the queue's SLA colouring; null for
    #: severities that carry no clock.
    sla_due_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: Set while an alert is deferred; it stays out of the default queue view until
    #: this time passes, and is never silently dropped.
    deferred_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Fraction of the required evidence fields that are actually populated. The
    #: management surfaces require 1.0 — an alert nobody can justify is noise, and
    #: the target in REQUIREMENTS-R1 is 100%, not "mostly".
    evidence_completeness: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )

    #: One sentence a manager can read without opening the evidence.
    headline_zh: Mapped[str] = mapped_column(Text, nullable=False)
    headline_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    resolved_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    evidence: Mapped[list[AlertEvidence]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )
    #: The append-only action stream. ``state`` above is this list, materialised.
    actions: Mapped[list["AlertAction"]] = relationship(
        back_populates="alert",
        cascade="all, delete-orphan",
        order_by="AlertAction.created_at",
    )


class AlertEvidence(Base):
    """The inputs to one alert decision, one row per firing snapshot."""

    __tablename__ = "alert_evidence"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alert.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    #: Which rule fired, named exactly as in docs/DETECTORS.md (e.g. ``T2``).
    rule_name: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value: Mapped[Decimal | None] = money_column()
    baseline_median: Mapped[Decimal | None] = money_column()
    baseline_mad: Mapped[Decimal | None] = money_column()
    robust_z: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Restated here rather than only on the alert: a long-running alert can span
    #: sessions, and each firing must be readable on its own.
    market_session: Mapped[MarketSession] = enum_column(MarketSession, nullable=False)
    peer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Detector-specific inputs as JSON text, for fields not worth a column.
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: What argues *against* this finding, as a JSON list of
    #: ``{"code", "label_zh", "detail"}``. A one-sided alert page trains people to
    #: stop reading alerts, so this ships with the evidence rather than on request.
    counter_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: How trustworthy the observed value itself was at firing time. An alert built
    #: on a STALE reading is a different claim from one built on a VERIFIED reading.
    verification_status: Mapped[VerificationStatus] = enum_column(
        VerificationStatus,
        nullable=False,
        default=VerificationStatus.VERIFIED,
    )
    #: Evidence is append-only and versioned. A data repair adds version n+1; it
    #: never edits version n, because someone already quoted version n.
    version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )
    #: Why a new version exists. Null on the original observation.
    revision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    alert: Mapped[Alert] = relationship(back_populates="evidence")
