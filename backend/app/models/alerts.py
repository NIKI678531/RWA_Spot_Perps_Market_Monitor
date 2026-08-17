"""Alert tables.

An alert without its inputs is an assertion. ``alert_evidence`` carries the raw
value, the baseline, the sample size, the session and the rule name, so anyone
reading the alert can reconstruct the decision instead of taking it on faith.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
    AlertStatus,
    DetectorFamily,
    EntityType,
)


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
    status: Mapped[AlertStatus] = enum_column(AlertStatus, nullable=False, index=True)

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
    created_at: Mapped[datetime] = created_at_column()

    alert: Mapped[Alert] = relationship(back_populates="evidence")
