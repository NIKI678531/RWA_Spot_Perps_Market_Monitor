"""Operational tables.

These sit outside the star schema. They exist so that any number in a report can be
traced back to the fetch attempt that produced it, the mapping decision that placed
it, and the baseline it was judged against. Without them an alert is plausible but
not defensible, and a figure nobody can defend gets ignored.
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
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.base import (
    Base,
    created_at_column,
    enum_column,
    id_column,
    snapshot_pk_column,
)
from app.models.enums import (
    AuthMode,
    EntityType,
    FetchStatus,
    MappingStatus,
    SourceStatus,
)


class SourceRegistry(Base):
    """Every data source we have evaluated, including the ones we do not collect.

    Rejected sources stay here with ``REFERENCE_ONLY`` status so the evaluation is
    not silently repeated in six months by someone who finds the endpoint and assumes
    nobody looked at it.
    """

    __tablename__ = "source_registry"

    source_id: Mapped[str] = id_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auth_mode: Mapped[AuthMode] = enum_column(AuthMode, nullable=False)
    status: Mapped[SourceStatus] = enum_column(SourceStatus, nullable=False, index=True)
    #: How often the scheduler should collect. Null for sources that are never run.
    cadence_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Which metric scopes this source can populate, newline-separated. Documentation
    #: rather than a constraint — it answers "if this source dies, what goes dark?".
    provides_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class FetchLog(Base):
    """One collection attempt.

    A row with ``NOT_VERIFIED`` is the whole point of this table: it records that we
    tried and failed to observe, which is categorically different from observing a
    zero. Downstream, missing rows render as a grey placeholder rather than a bar of
    height nil.
    """

    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_registry.source_id"), nullable=False, index=True
    )
    snapshot_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[FetchStatus] = enum_column(FetchStatus, nullable=False, index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Rows successfully parsed. Null when the attempt never got far enough to know;
    #: zero here means "the source genuinely returned nothing", which is a real
    #: observation and distinct from null.
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


#: The two columns of ``uq_underlying_map_source``. MySQL 8.4 defaults utf8mb4 columns
#: to a case-insensitive collation, under which ``AAPLx`` and ``AAPLX`` are one key —
#: but the suffix rules in ``normalize.underlying_map`` are case-sensitive precisely
#: because those are an xStocks wrapper and an unrecognised ticker respectively. Pinned
#: to a binary collation there; SQLite already compares by bytes. See revision
#: 7d5a1c2e9b04.
_CASE_SENSITIVE_KEY = String(96).with_variant(
    mysql.VARCHAR(96, collation="utf8mb4_bin"), "mysql"
)


class UnderlyingMap(Base):
    """A source symbol resolved (or not) to an underlying.

    Unmatched symbols land in ``PENDING_REVIEW`` instead of being guessed. The source
    data is full of traps: GOLD, GOLDJM and GLDMINE are three different underlyings,
    and SKHX and SKHY trade about 7x apart despite differing by one character.
    """

    __tablename__ = "underlying_map"
    __table_args__ = (
        UniqueConstraint("source_id", "source_symbol", name="uq_underlying_map_source"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    source_id: Mapped[str] = mapped_column(
        _CASE_SENSITIVE_KEY, nullable=False, index=True
    )
    #: The symbol exactly as the source spells it, e.g. ``AAPLX`` or ``SPYB``.
    source_symbol: Mapped[str] = mapped_column(
        _CASE_SENSITIVE_KEY, nullable=False, index=True
    )
    #: The symbol after suffix stripping, e.g. ``AAPL``. Kept separately so a bad
    #: stripping rule can be audited without re-fetching.
    normalized_symbol: Mapped[str | None] = mapped_column(String(96), nullable=True)
    underlying_id: Mapped[str | None] = mapped_column(
        ForeignKey("dim_underlying.underlying_id"), nullable=True, index=True
    )
    status: Mapped[MappingStatus] = enum_column(
        MappingStatus, nullable=False, index=True
    )
    #: Which rule produced this mapping, e.g. ``strip_suffix_X``. An automatic mapping
    #: nobody can explain is worse than no mapping.
    rule: Mapped[str | None] = mapped_column(String(96), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()


class BaselineSnapshot(Base):
    """A persisted rolling baseline. Grain: entity x metric x session x snapshot_ts.

    Stored rather than recomputed on read so that an alert fired last Tuesday can be
    re-examined against the baseline that actually produced it, not against whatever
    the window says today.
    """

    __tablename__ = "baseline_snapshot"

    entity_type: Mapped[EntityType] = enum_column(EntityType, primary_key=True)
    entity_id: Mapped[str] = id_column(primary_key=True)
    metric_scope: Mapped[MetricScope] = enum_column(MetricScope, primary_key=True)
    #: Baselines are stratified on this. A blended baseline fires on every Monday
    #: open and every US close; see ADR 0002.
    market_session: Mapped[MarketSession] = enum_column(MarketSession, primary_key=True)
    snapshot_ts: Mapped[datetime] = snapshot_pk_column()

    median: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    mad: Mapped[Decimal | None] = mapped_column(Numeric(30, 8), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    #: False while the series is still short. Detectors record but stay silent, so
    #: the first two weeks after deployment are not a spam feed.
    is_alertable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ReportArtifact(Base):
    """A generated xlsx or docx.

    Production K8s provides no PersistentVolumeClaim, so a report written to the
    container filesystem is lost on the next rollout. Small artifacts live here as
    bytes; large ones live in object storage and this row holds the key. Either way
    nothing depends on local disk.
    """

    __tablename__ = "report_artifact"
    __table_args__ = (
        UniqueConstraint(
            "report_date", "report_format", name="uq_report_artifact_date_format"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    #: The business date the report covers, not the moment it was rendered.
    report_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    report_format: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Populated when ``report_storage_backend = "database"``.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    #: Populated when the artifact went to object storage instead.
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Snapshot the figures were taken from, so a report can be reconciled against
    #: the warehouse state that produced it.
    snapshot_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
