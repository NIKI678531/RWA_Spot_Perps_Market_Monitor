"""Editions, revisions, artifacts and share links.

An edition is a citation. Everything in this module protects one property: a figure
someone quoted at 09:15 can still be found, unchanged, weeks later — and if it turned
out to be wrong, the correction is a numbered revision with a reason and a field-level
diff sitting next to the original rather than on top of it.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ActorDep, Limit, SessionDep, WriterDep
from app.db.base import utcnow
from app.models.editions import Edition, EditionArtifact, EditionRevision
from app.models.enums import EditionKind, UserRole
from app.services.workflow.alert_lifecycle import Actor
from app.schemas.common import Meta
from app.schemas.editions import (
    ArtifactRow,
    EditionDetail,
    EditionList,
    EditionRevisionRow,
    EditionRow,
    FieldDiffRow,
    ReviseRequest,
    ShareLink,
    ShareLinkRequest,
)
from app.services.editions import freeze, redaction, revision as revision_service, share

router = APIRouter(tags=["editions"])

_NOTE = (
    "冻结版写入后不可修改。更正生成新的修订号，被取代的版本保持可读，"
    "差异逐字段列出——「已更新」不是任何人能据以行动的理由。"
)


@router.get("/editions", response_model=EditionList)
def editions(
    session: SessionDep,
    kind: EditionKind | None = Query(default=None),
    limit: Limit = 30,
) -> EditionList:
    rows = freeze.latest(session, kind=kind, limit=limit)
    reasons = _reasons_by_edition(session, [r.id for r in rows])
    counts = _artifact_counts(session, [r.id for r in rows])
    by_id = {r.id: r for r in rows}

    return EditionList(
        meta=Meta(
            as_of=max((r.as_of for r in rows), default=utcnow()),
            note=_NOTE,
            row_count=len(rows),
        ),
        rows=[
            _edition_row(
                r,
                revision_reason=reasons.get(r.id),
                artifact_count=counts.get(r.id, 0),
                superseded_by=by_id.get(r.superseded_by_id or -1),
            )
            for r in rows
        ],
    )


@router.get("/editions/{edition_key}", response_model=EditionDetail)
def edition_detail(
    edition_key: str,
    session: SessionDep,
    actor: ActorDep,
    revision: int | None = Query(
        default=None,
        description=(
            "A specific revision. Omit for the current one. Superseded revisions "
            "stay fetchable — that is the point of them."
        ),
    ),
) -> EditionDetail:
    edition = _resolve(session, edition_key, revision)
    if redaction.is_share_viewer(actor.role):
        _assert_share_token_covers(actor, edition)

    revisions = list(
        session.execute(
            select(EditionRevision)
            .join(Edition, EditionRevision.edition_id == Edition.id)
            .where(Edition.edition_key == edition_key)
            .order_by(EditionRevision.revision.desc())
        )
        .scalars()
        .all()
    )
    artifacts = list(
        session.execute(
            select(EditionArtifact)
            .where(EditionArtifact.edition_id == edition.id)
            .order_by(EditionArtifact.created_at.desc())
        )
        .scalars()
        .all()
    )

    payload = json.loads(edition.payload_json) if edition.payload_json else None
    # Redaction happens here, before serialisation. A share viewer's response never
    # contains the fields at all; filtering them in the browser would ship them.
    payload = redaction.redact(payload, role=actor.role)

    superseded_by = (
        session.get(Edition, edition.superseded_by_id)
        if edition.superseded_by_id
        else None
    )
    return EditionDetail(
        edition=_edition_row(
            edition,
            revision_reason=next(
                (r.reason for r in revisions if r.revision == edition.revision), None
            ),
            artifact_count=len(artifacts),
            superseded_by=superseded_by,
        ),
        payload=payload,
        revisions=[_revision_row(r) for r in revisions],
        artifacts=[_artifact_row(a) for a in artifacts],
    )


@router.post("/editions/{edition_key}/revise", response_model=EditionDetail)
def revise(
    edition_key: str,
    payload: ReviseRequest,
    session: SessionDep,
    actor: WriterDep,
) -> EditionDetail:
    """Issue the next revision of a frozen edition.

    Restricted to owners and admins. Correcting a number somebody has already quoted
    is a publishing decision, not a data-entry one.
    """
    if actor.role not in (UserRole.OWNER.value, UserRole.ADMIN.value):
        raise HTTPException(
            status_code=403, detail="revising a frozen edition requires an owner role"
        )

    edition = _resolve(session, edition_key, None)
    try:
        replacement = revision_service.revise(
            session,
            edition,
            reason=payload.reason,
            payload=payload.payload,
            as_of=payload.as_of,
            created_by=actor.user_id,
            actor_role=actor.role,
        )
    except revision_service.RevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return edition_detail(edition_key, session, actor, replacement.revision)


@router.post("/editions/{edition_key}/share", response_model=ShareLink)
def create_share_link(
    edition_key: str,
    payload: ShareLinkRequest,
    session: SessionDep,
    actor: WriterDep,
) -> ShareLink:
    """Mint a read-only link to one revision.

    The response states which fields the recipient will not receive. Redaction the
    sharer cannot see is redaction they will not trust — and will work around.
    """
    edition = _resolve(session, edition_key, None)
    if edition.kind is EditionKind.LIVE:
        raise HTTPException(
            status_code=409,
            detail="the live edition is not quotable; share a frozen edition",
        )
    try:
        token, expires_at = share.issue(
            edition_key=edition.edition_key,
            revision=edition.revision,
            expires_at=payload.expires_at,
        )
    except share.ShareError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ShareLink(
        token=token,
        href=f"/editions/{edition.edition_key}?share_token={token}",
        edition_key=edition.edition_key,
        revision=edition.revision,
        expires_at=expires_at,
        redacted_fields=sorted(redaction.REDACTED_KEYS),
    )


@router.get("/editions/{edition_key}/artifacts/{artifact_id}")
def download_artifact(
    edition_key: str, artifact_id: int, session: SessionDep, actor: ActorDep
) -> Response:
    """Serve a generated file from the database or object storage.

    Never from a container filesystem: production runs without a PersistentVolumeClaim,
    so a file written to disk is a file that disappears at the next rollout.
    """
    artifact = session.get(EditionArtifact, artifact_id)
    edition = session.get(Edition, artifact.edition_id) if artifact else None
    exists = artifact is not None and edition is not None
    allowed = exists and edition.edition_key == edition_key  # type: ignore[union-attr]
    if allowed and redaction.is_share_viewer(actor.role):
        # A share viewer may read the artifact only if their token names this exact
        # revision. Reuse the same failure shape so the check leaks nothing.
        allowed = _share_token_covers(actor, edition)  # type: ignore[arg-type]
    try:
        redaction.assert_visible(exists, allowed=allowed)
    except redaction.NotVisible as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    assert artifact is not None  # narrowed by assert_visible

    if artifact.storage_key:
        # Stored in TOS. Redirect rather than proxy: the backend has no business
        # streaming megabytes it does not need to inspect.
        return RedirectResponse(artifact.storage_key, status_code=307)
    if artifact.content is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "the artifact row exists but carries no bytes; the render failed "
                "and was recorded rather than silently retried"
            ),
        )
    return Response(
        content=artifact.content,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


# --- helpers -----------------------------------------------------------------


def _resolve(session: Session, edition_key: str, revision: int | None) -> Edition:
    if revision is None:
        edition = freeze.resolve(session, edition_key)
    else:
        edition = (
            session.execute(
                select(Edition)
                .where(Edition.edition_key == edition_key)
                .where(Edition.revision == revision)
            )
            .scalars()
            .first()
        )
    if edition is None:
        raise HTTPException(status_code=404, detail=f"unknown edition {edition_key}")
    return edition


def _share_token_covers(actor: Actor, edition: Edition) -> bool:
    if not actor.share_token:
        return False
    try:
        claims = share.verify(actor.share_token)
    except share.ShareError:
        return False
    return (
        claims.edition_key == edition.edition_key
        and claims.revision == edition.revision
    )


def _assert_share_token_covers(actor: Actor, edition: Edition) -> None:
    if not _share_token_covers(actor, edition):
        # 404 rather than 403: a link that names another edition must not be able to
        # confirm that this one exists.
        raise HTTPException(status_code=404, detail="not found")


def _edition_row(
    edition: Edition,
    *,
    revision_reason: str | None = None,
    artifact_count: int = 0,
    superseded_by: Edition | None = None,
) -> EditionRow:
    ref = freeze.EditionRef(
        key=edition.edition_key, kind=edition.kind, trading_date=edition.trading_date
    )
    label = ref.label
    if edition.revision > 1:
        label = f"{label} r{edition.revision}"
    return EditionRow(
        id=edition.id,
        edition_key=edition.edition_key,
        kind=edition.kind,
        status=edition.status,
        revision=edition.revision,
        label=label,
        as_of=edition.as_of,
        generated_at=edition.generated_at,
        trading_date=edition.trading_date,
        theme_map_version=edition.theme_map_version,
        superseded_by_id=edition.superseded_by_id,
        superseded_by_revision=superseded_by.revision if superseded_by else None,
        failure_reason=edition.failure_reason,
        revision_reason=revision_reason,
        artifact_count=artifact_count,
    )


def _revision_row(row: EditionRevision) -> EditionRevisionRow:
    diffs: list[FieldDiffRow] = []
    if row.diff_json:
        try:
            parsed = json.loads(row.diff_json)
        except ValueError:
            parsed = []
        diffs = [
            FieldDiffRow(
                path=str(item.get("path", "")),
                before=item.get("before"),
                after=item.get("after"),
            )
            for item in parsed
            if isinstance(item, dict)
        ]
    return EditionRevisionRow(
        id=row.id,
        revision=row.revision,
        reason=row.reason,
        created_by=row.created_by,
        created_at=row.created_at,
        supersedes_revision=None,
        diff=diffs,
    )


def _artifact_row(row: EditionArtifact) -> ArtifactRow:
    view_state: dict[str, Any] = {}
    if row.view_state_json:
        try:
            parsed = json.loads(row.view_state_json)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            view_state = parsed
    return ArtifactRow(
        id=row.id,
        artifact_kind=row.artifact_kind,
        filename=row.filename,
        content_type=row.content_type,
        byte_size=row.byte_size,
        created_at=row.created_at,
        href=f"/editions/{row.edition.edition_key}/artifacts/{row.id}",
        view_state=view_state,
    )


def _reasons_by_edition(session: Session, edition_ids: list[int]) -> dict[int, str]:
    if not edition_ids:
        return {}
    rows = (
        session.execute(
            select(EditionRevision).where(EditionRevision.edition_id.in_(edition_ids))
        )
        .scalars()
        .all()
    )
    return {r.edition_id: r.reason for r in rows}


def _artifact_counts(session: Session, edition_ids: list[int]) -> dict[int, int]:
    if not edition_ids:
        return {}
    rows = session.execute(
        select(EditionArtifact.edition_id, func.count())
        .where(EditionArtifact.edition_id.in_(edition_ids))
        .group_by(EditionArtifact.edition_id)
    ).all()
    return {edition_id: int(count) for edition_id, count in rows}


__all__ = ["router"]
