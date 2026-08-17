"""Liveness, plus the one operational fact worth checking at a glance."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select, text

from app.api.deps import SessionDep
from app.core.config import settings
from app.models.facts import FactAssetSnapshot, FactPairSnapshot
from app.schemas.common import Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health(session: SessionDep) -> Health:
    """Report reachability and the age of the newest observation.

    ``degraded`` means the process is up but the database is not answering. It is
    reported rather than raised so a probe can distinguish a dead pod from a dead
    dependency.
    """
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - only on a broken database
        return Health(
            status="degraded", environment=settings.environment, database="unreachable"
        )

    as_of = session.execute(
        select(FactAssetSnapshot.snapshot_ts)
        .order_by(FactAssetSnapshot.snapshot_ts.desc())
        .limit(1)
    ).scalar()
    if as_of is None:
        as_of = session.execute(
            select(FactPairSnapshot.snapshot_ts)
            .order_by(FactPairSnapshot.snapshot_ts.desc())
            .limit(1)
        ).scalar()

    return Health(
        status="ok",
        environment=settings.environment,
        database="ok",
        as_of=as_of,
    )
