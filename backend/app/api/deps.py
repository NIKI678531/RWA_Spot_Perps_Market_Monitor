"""Shared request dependencies.

Every read endpoint serves its figures from one :func:`app.services.report.dataset.load`
call. The dashboard and the daily workbook therefore answer from the same code path:
if the two ever disagree about a venue's turnover, it is a bug in one loader rather
than a difference of opinion between two.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.enums import UserRole
from app.services.editions import redaction
from app.services.workflow.alert_lifecycle import Actor
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


def get_actor(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_role: Annotated[str | None, Header()] = None,
    share_token: Annotated[str | None, Query()] = None,
) -> Actor:
    """Who is making this request.

    Authentication belongs to the surrounding platform; this system only needs a
    stable identity to attribute a decision to, so it reads one from the headers the
    gateway sets. The seam is deliberate and narrow — everything downstream takes an
    :class:`Actor` rather than reaching for a global, which is what makes the
    lifecycle functions testable without a request at all.

    A ``share_token`` collapses the caller to ``SHARE_VIEWER`` regardless of headers.
    A share link is an anonymous link; letting a header upgrade it would make the
    redaction advisory.
    """
    if share_token:
        return Actor(
            user_id=None,
            role=UserRole.SHARE_VIEWER.value,
            kind="share",
            share_token=share_token,
        )

    role = UserRole.VIEWER.value
    if x_user_role:
        try:
            role = UserRole(x_user_role).value
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown role {x_user_role}")
        if role == UserRole.SHARE_VIEWER.value:
            # SHARE_VIEWER is reached by holding a share token, not by claiming it.
            raise HTTPException(status_code=400, detail="unknown role share_viewer")
    return Actor(user_id=x_user_id, role=role)


ActorDep = Annotated[Actor, Depends(get_actor)]


def require_write(actor: ActorDep) -> Actor:
    """Guard for every state-changing endpoint.

    Two separate checks. A viewer may not write at all; and a writer without an
    identity may not write either, because an unattributable action in an audit trail
    is the same as no audit trail.
    """
    if redaction.is_share_viewer(actor.role):
        # Same shape as "no such route" — a share viewer must not learn that a
        # write surface exists behind the link they were given.
        raise HTTPException(status_code=404, detail="not found")
    if actor.role not in (
        UserRole.ANALYST.value,
        UserRole.OWNER.value,
        UserRole.ADMIN.value,
    ):
        raise HTTPException(
            status_code=403, detail="this action requires an analyst role or above"
        )
    if not actor.user_id:
        raise HTTPException(
            status_code=403,
            detail="this action must be attributable; send X-User-Id",
        )
    return actor


WriterDep = Annotated[Actor, Depends(require_write)]
