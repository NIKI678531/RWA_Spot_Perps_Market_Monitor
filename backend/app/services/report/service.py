"""Report generation as one call, for the scheduler and the API to share.

Both formats are built from a single ``ReportDataset`` load. Building them from two
loads would let a collector land between the two and produce a workbook and a
document that disagree with each other about the same day.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.operations import ReportArtifact
from app.services.report.dataset import load
from app.services.report.excel import build_sheets
from app.services.report.storage import DOCX, XLSX, ReportStore
from app.services.report.word import render_docx
from app.services.report.workbook import render


def generate(
    session: Session,
    *,
    as_of: datetime | None = None,
    store: ReportStore | None = None,
) -> list[ReportArtifact]:
    """Build both reports for one snapshot and persist them."""
    data = load(session, as_of)
    store = store or ReportStore()
    report_date = as_of or datetime.now(timezone.utc)

    return [
        store.save(
            session,
            content=render(build_sheets(data)),
            report_format=XLSX,
            report_date=report_date,
            snapshot_ts=data.as_of,
        ),
        store.save(
            session,
            content=render_docx(data),
            report_format=DOCX,
            report_date=report_date,
            snapshot_ts=data.as_of,
        ),
    ]
