"""Generated xlsx and docx: list, download, regenerate.

Artifacts are served out of the database or object storage, never off local disk.
Production K8s provides no PersistentVolumeClaim, so a file on the container
filesystem survives exactly until the next rollout.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep
from app.models.operations import ReportArtifact
from app.schemas.market import ReportList, ReportRow
from app.services.report import service
from app.services.report.storage import DOCX, XLSX, load_content, media_type

router = APIRouter(tags=["reports"])


class GenerateRequest(BaseModel):
    as_of: datetime | None = Field(
        default=None,
        description=(
            "Render the warehouse as it stood at this instant. Omit for now. Both "
            "formats are built from one read, so they cannot disagree."
        ),
    )


@router.get("/reports", response_model=ReportList)
def reports(session: SessionDep) -> ReportList:
    stmt = select(ReportArtifact).order_by(
        ReportArtifact.report_date.desc(), ReportArtifact.report_format
    )
    return ReportList(rows=[_row(a) for a in session.execute(stmt).scalars().all()])


@router.get("/reports/{report_date}/excel")
def excel(report_date: date, session: SessionDep) -> Response:
    return _download(session, report_date, XLSX)


@router.get("/reports/{report_date}/word")
def word(report_date: date, session: SessionDep) -> Response:
    return _download(session, report_date, DOCX)


@router.post("/reports/generate", response_model=ReportList)
def generate(session: SessionDep, request: GenerateRequest | None = None) -> ReportList:
    artifacts = service.generate(session, as_of=(request.as_of if request else None))
    session.commit()
    return ReportList(rows=[_row(a) for a in artifacts])


def _row(artifact: ReportArtifact) -> ReportRow:
    return ReportRow(
        id=artifact.id,
        report_date=artifact.report_date,
        report_format=artifact.report_format,
        filename=artifact.filename,
        size_bytes=artifact.size_bytes,
        snapshot_ts=artifact.snapshot_ts,
        storage="object_storage" if artifact.storage_key else "database",
        created_at=artifact.created_at,
    )


def _download(session: Session, report_date: date, report_format: str) -> Response:
    artifact = _find(session, report_date, report_format)
    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail=f"no {report_format} report for {report_date.isoformat()}",
        )
    try:
        content = load_content(artifact)
    except FileNotFoundError as exc:
        # The row says where the bytes went; without a configured fetcher we cannot
        # follow. That is a server misconfiguration, not a missing report.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=media_type(report_format),
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


def _find(
    session: Session, report_date: date, report_format: str
) -> ReportArtifact | None:
    """The newest artifact for that calendar day.

    Matched on a day-wide range rather than on equality: a report generated at
    08:00 carries that instant as its ``report_date``, and a URL naming a date should
    still find it.
    """
    start = datetime.combine(report_date, time.min, tzinfo=timezone.utc)
    stmt = (
        select(ReportArtifact)
        .where(
            ReportArtifact.report_format == report_format,
            ReportArtifact.report_date >= start,
            ReportArtifact.report_date < start + timedelta(days=1),
        )
        .order_by(ReportArtifact.report_date.desc())
    )
    return session.execute(stmt).scalars().first()
