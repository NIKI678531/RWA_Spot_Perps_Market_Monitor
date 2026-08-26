"""Edition, revision and retrospective payloads.

An edition is a citation. These shapes exist so that "the 17:00 number" is a thing
someone can link to, quote, and still resolve six weeks later — including after it has
been superseded, which is exactly when they need it most.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import (
    AlertSeverity,
    AlertState,
    DetectorFamily,
    EditionKind,
    EditionStatus,
)
from app.schemas.common import Meta


class EditionRow(BaseModel):
    """One edition in the version list (T5)."""

    id: int
    edition_key: str
    kind: EditionKind
    status: EditionStatus
    revision: int
    label: str
    #: Data cut-off and render time. Routinely hours apart, and conflating them is
    #: how a report gets quoted as being newer than the data behind it.
    as_of: datetime
    generated_at: datetime | None = None
    trading_date: str
    theme_map_version: int | None = None
    #: Set on a superseded row, so an old link can offer the way forward without
    #: silently redirecting to different numbers.
    superseded_by_id: int | None = None
    superseded_by_revision: int | None = None
    failure_reason: str | None = None
    revision_reason: str | None = None
    artifact_count: int = 0


class EditionList(BaseModel):
    meta: Meta
    rows: list[EditionRow]


class FieldDiffRow(BaseModel):
    """One figure that moved between two revisions."""

    path: str
    before: Any = None
    after: Any = None


class EditionDetail(BaseModel):
    edition: EditionRow
    #: The frozen snapshot, exactly as it was written. Redacted for a share viewer
    #: before it leaves the server, never in the browser.
    payload: dict[str, Any] | None = None
    #: Every revision of this edition key, newest first, superseded ones included.
    revisions: list["EditionRevisionRow"] = Field(default_factory=list)
    artifacts: list["ArtifactRow"] = Field(default_factory=list)


class EditionRevisionRow(BaseModel):
    id: int
    revision: int
    reason: str
    created_by: str | None = None
    created_at: datetime
    supersedes_revision: int | None = None
    diff: list[FieldDiffRow] = Field(default_factory=list)


class ArtifactRow(BaseModel):
    """A generated file belonging to an edition.

    ``href`` points at a download route rather than at storage: the bytes live in
    object storage or in the database, never on a container filesystem, and the route
    is what hides which of the two it happens to be today.
    """

    id: int
    artifact_kind: str
    filename: str
    content_type: str | None = None
    byte_size: int | None = None
    created_at: datetime
    href: str
    #: The exact view state the export was taken under — scope, filters, window,
    #: timezone, cut-off. What makes a screenshot reproducible later.
    view_state: dict[str, Any] = Field(default_factory=dict)


class ReviseRequest(BaseModel):
    """Correcting a frozen edition.

    ``reason`` is required and is checked for content in the service layer. A
    revision whose reason is "updated" tells a reader nothing about whether their
    deck needs changing, which is the only question a revision exists to answer.
    """

    reason: str
    payload: dict[str, Any] | None = None
    as_of: datetime | None = None


class ShareLinkRequest(BaseModel):
    expires_at: datetime | None = None
    note: str | None = None


class ShareLink(BaseModel):
    token: str
    href: str
    edition_key: str
    revision: int
    expires_at: datetime | None = None
    #: Stated back to the creator so they know what the recipient will and will not
    #: see. Redaction the sharer cannot see is redaction they will not trust.
    redacted_fields: list[str] = Field(default_factory=list)


class RetrospectiveRow(BaseModel):
    """One closed alert, seen from the review side."""

    alert_id: int
    detector: str
    family: DetectorFamily
    severity: AlertSeverity
    final_state: AlertState
    entity_id: str
    entity_name: str | None = None
    headline_zh: str
    owner_id: str | None = None
    published_at: datetime | None = None
    resolved_ts: datetime | None = None
    #: Wall-clock hours from publication to close. The number a threshold review
    #: argues about.
    time_to_close_hours: float | None = None
    reason_code: str | None = None
    outcome_note: str | None = None
    #: Whether the close beat the SLA that was in force when it was published.
    within_sla: bool | None = None


class DetectorReviewRow(BaseModel):
    """One detector's record over the review window (threshold review)."""

    detector: str
    family: DetectorFamily
    published: int = 0
    actioned: int = 0
    false_positive: int = 0
    dismissed: int = 0
    open_count: int = 0
    #: False positives over closed findings. Null when nothing has closed yet —
    #: a rate over zero cases reads as 0% and would argue for loosening a threshold
    #: on no evidence at all.
    false_positive_rate: float | None = None
    median_time_to_close_hours: float | None = None
    #: The most common counter-evidence code on this detector's findings. Usually
    #: the fastest route to what a threshold is getting wrong.
    top_counter_evidence: str | None = None


class Retrospective(BaseModel):
    meta: Meta
    window_days: int
    rows: list[RetrospectiveRow]
    detectors: list[DetectorReviewRow] = Field(default_factory=list)


EditionDetail.model_rebuild()

__all__ = [
    "ArtifactRow",
    "DetectorReviewRow",
    "EditionDetail",
    "EditionList",
    "EditionRevisionRow",
    "EditionRow",
    "FieldDiffRow",
    "Retrospective",
    "RetrospectiveRow",
    "ReviseRequest",
    "ShareLink",
    "ShareLinkRequest",
]
