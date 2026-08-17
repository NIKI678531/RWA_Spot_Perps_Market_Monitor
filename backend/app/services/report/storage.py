"""Where a generated report goes.

Production K8s provides no PersistentVolumeClaim, so the backend has no durable
filesystem: a report written to disk survives until the next rollout and no longer.
Artifacts therefore go to the database or to object storage, and a ``ReportArtifact``
row is written either way so the API can serve a download without knowing which.

Regeneration for the same date and format replaces the existing artifact rather than
accumulating duplicates. ``report_artifact`` is an operational table, not a fact
table — the append-only rule applies to observations, and a re-rendered report is
not a new observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.operations import ReportArtifact

XLSX = "xlsx"
DOCX = "docx"

_MEDIA_TYPES = {
    XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def media_type(report_format: str) -> str:
    return _MEDIA_TYPES.get(report_format, "application/octet-stream")


def filename_for(report_format: str, report_date: datetime) -> str:
    stamp = report_date.strftime("%Y-%m-%d")
    stem = "RWA_Spot_Perps_Market_Monitor"
    if report_format == DOCX:
        stem = "RWA_Spot_Perps_Market_Analysis"
    return f"{stem}_{stamp}.{report_format}"


class ObjectUploader(Protocol):
    """Uploads bytes and returns the key they can be read back by."""

    def __call__(self, key: str, content: bytes, content_type: str) -> str: ...


@dataclass
class ReportStore:
    """Persists artifacts to the database, or to object storage when wired up.

    ``uploader`` is injected rather than constructed here so the object-storage path
    can be exercised in tests without credentials, and so a missing TOS SDK cannot
    break an install that never uses it.
    """

    backend: str = ""
    uploader: ObjectUploader | None = None
    key_prefix: str = "reports"

    def __post_init__(self) -> None:
        self.backend = self.backend or settings.report_storage_backend

    def save(
        self,
        session: Session,
        *,
        content: bytes,
        report_format: str,
        report_date: datetime,
        snapshot_ts: datetime | None = None,
    ) -> ReportArtifact:
        name = filename_for(report_format, report_date)
        artifact = self._existing(session, report_date, report_format)
        if artifact is None:
            artifact = ReportArtifact(
                report_date=report_date, report_format=report_format
            )
            session.add(artifact)

        artifact.filename = name
        artifact.size_bytes = len(content)
        artifact.snapshot_ts = snapshot_ts

        if self.backend == "database":
            artifact.content = content
            artifact.storage_key = None
        else:
            if self.uploader is None:
                raise RuntimeError(
                    f"report_storage_backend={self.backend!r} needs an uploader; "
                    "none was configured. Writing to the container filesystem is "
                    "not an option — there is no PVC in production."
                )
            key = f"{self.key_prefix}/{report_date:%Y/%m}/{name}"
            artifact.storage_key = self.uploader(
                key, content, media_type(report_format)
            )
            # Not kept in both places: two copies of one artifact eventually differ.
            artifact.content = None

        session.flush()
        return artifact

    def _existing(
        self, session: Session, report_date: datetime, report_format: str
    ) -> ReportArtifact | None:
        stmt = select(ReportArtifact).where(
            ReportArtifact.report_date == report_date,
            ReportArtifact.report_format == report_format,
        )
        return session.execute(stmt).scalars().first()


def load_content(
    artifact: ReportArtifact, fetcher: Callable[[str], bytes] | None = None
) -> bytes:
    """Read an artifact back, from wherever it was put."""
    if artifact.content is not None:
        return artifact.content
    if artifact.storage_key and fetcher is not None:
        return fetcher(artifact.storage_key)
    raise FileNotFoundError(
        f"artifact {artifact.id} lives at {artifact.storage_key!r} and no object "
        "storage fetcher was provided"
    )
