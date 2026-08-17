"""Shared request dependencies.

Every read endpoint serves its figures from one :func:`app.services.report.dataset.load`
call. The dashboard and the daily workbook therefore answer from the same code path:
if the two ever disagree about a venue's turnover, it is a bug in one loader rather
than a difference of opinion between two.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.report.dataset import ReportDataset, load

SessionDep = Annotated[Session, Depends(get_session)]

AsOf = Annotated[
    datetime | None,
    Query(
        description=(
            "Read the warehouse as it stood at this instant. Omit for the newest "
            "snapshot. Every fact table is still read at its own latest timestamp "
            "at or before this value, because collectors run on different cadences."
        )
    ),
]


def get_dataset(session: SessionDep, as_of: AsOf = None) -> ReportDataset:
    return load(session, as_of)


DatasetDep = Annotated[ReportDataset, Depends(get_dataset)]

Limit = Annotated[int, Query(ge=1, le=1000, description="Maximum rows to return.")]
