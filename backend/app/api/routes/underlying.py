"""Deprecated singular alias for the Underlying 360 endpoint.

The canonical route is ``GET /underlyings/{underlying_id}`` in
:mod:`app.api.routes.underlyings`, alongside the catalogue listing it is drilled
from. This module keeps the pre-R1 path answering so a bookmark, a saved export URL
or a pasted link from an earlier edition still resolves — the whole point of putting
view state in the URL (rule 23) is defeated if the URLs stop working.

It delegates rather than duplicating: one builder, so the two paths cannot drift into
returning different figures for the same security.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DatasetDep, SessionDep
from app.api.routes.underlyings import underlying_detail
from app.schemas.market import Underlying360

router = APIRouter(tags=["underlying"])


@router.get(
    "/underlying/{underlying_id}",
    response_model=Underlying360,
    deprecated=True,
    summary="Deprecated — use GET /underlyings/{underlying_id}",
)
def underlying(
    underlying_id: str, data: DatasetDep, session: SessionDep
) -> Underlying360:
    return underlying_detail(underlying_id, data, session)


__all__ = ["router"]
