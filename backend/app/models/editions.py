"""Edition tables: a quotable, frozen cut of the market.

The problem these solve is narrow and expensive. Someone screenshots a figure at
09:15, pastes it into a deck, and is asked about it three weeks later. If the number
moved in between — because a late fetch landed, or a mapping was corrected — the deck
is now wrong and nobody can say when it became wrong.

So a frozen edition is written once and never updated. A correction produces a
numbered :class:`EditionRevision` carrying its reason and diff, and the superseded
version stays readable. ``as_of`` (the data cut-off) and ``generated_at`` (when we
rendered it) are separate columns because they answer different questions and are
routinely hours apart.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, enum_column
from app.models.enums import EditionKind, EditionStatus


class Edition(Base):
    """One published cut: Live, or the 09:00 / 17:00 HKT freeze.

    ``LIVE`` rows exist so every screen can name the edition it is showing, but they
    are never frozen and must never be quoted as a source.
    """

    __tablename__ = "edition"
    __table_args__ = (
        UniqueConstraint("edition_key", "revision", name="uq_edition_key_revision"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    #: Human-quotable identity, e.g. ``2026-08-26.morning``. Stable across revisions;
    #: the revision number distinguishes them.
    edition_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[EditionKind] = enum_column(EditionKind, nullable=False, index=True)
    status: Mapped[EditionStatus] = enum_column(
        EditionStatus, nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    #: The data cut-off: no observation after this time is included.
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: When this edition was rendered. Later than ``as_of``, sometimes much later.
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The HKT trading day this edition belongs to, as ``YYYY-MM-DD``. Stored rather
    #: than derived: the UTC date of a 09:00 HKT freeze is the previous day.
    trading_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    #: The theme definition version in force at freeze time, so a past edition can be
    #: replayed under the classification it was actually built on.
    theme_map_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Frozen headline content: the one-sentence summary, four-question answers and
    #: KPI value objects, as JSON. Rendering from live tables would defeat the freeze.
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Set when this row has been superseded by a later revision. The row itself is
    #: never modified beyond this pointer and its status.
    superseded_by_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True
    )
    #: Why the freeze failed, when status is FAILED. A silent slip is worse than a
    #: missing edition: readers assume the 09:00 number simply has not changed.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    revisions: Mapped[list[EditionRevision]] = relationship(
        back_populates="edition", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[EditionArtifact]] = relationship(
        back_populates="edition", cascade="all, delete-orphan"
    )


class EditionRevision(Base):
    """Why a frozen edition was re-issued, and exactly what moved.

    The diff is the point. "Corrected data" tells a reader nothing; "spot volume for
    SPYx moved from $1.2mn to $3.4mn because Bybit's 08:00 fetch landed late" tells
    them whether their deck needs changing.
    """

    __tablename__ = "edition_revision"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    edition_id: Mapped[int] = mapped_column(
        ForeignKey("edition.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Never null, and never a bare "update". The service layer rejects empty text.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: JSON list of ``{"path", "before", "after"}`` for every figure that changed.
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The edition row this revision replaced, kept readable.
    supersedes_edition_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    edition: Mapped[Edition] = relationship(back_populates="revisions")


class EditionArtifact(Base):
    """A rendered file belonging to an edition.

    Content lives in the database or in object storage, never on the container's
    filesystem: production runs without a PersistentVolumeClaim, so a file written to
    disk is a file that disappears at the next rollout.
    """

    __tablename__ = "edition_artifact"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    edition_id: Mapped[int] = mapped_column(
        ForeignKey("edition.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: ``xlsx`` | ``docx`` | ``digest_html``.
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Exactly one of these two is set. ``storage_key`` points at TOS; ``content``
    #: holds small artifacts inline.
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    #: The filter/sort/scope state the export was taken under, so the same view can
    #: be reproduced six weeks later.
    view_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    edition: Mapped[Edition] = relationship(back_populates="artifacts")


__all__ = ["Edition", "EditionArtifact", "EditionRevision"]
