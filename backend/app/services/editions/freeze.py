"""Freezing an edition: written once, never updated.

At 09:00 and 17:00 HKT the current state of the market is captured and becomes
quotable. From that moment the row is immutable. Late data does not edit it; a
correction produces a numbered revision (see :mod:`revision`) and the superseded
version stays readable, because somebody has already pasted a figure from it into a
deck and needs to be able to find out what it said.

Two failure modes are handled explicitly and neither is allowed to be silent:

* a freeze that fails writes a ``FAILED`` row with its reason rather than leaving a
  hole. A missing 09:00 edition reads as "the 09:00 number has not changed";
* a freeze that would overwrite an existing frozen edition raises. That is the bug
  this module exists to make impossible.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.editions import Edition
from app.models.enums import EditionKind, EditionStatus

logger = logging.getLogger(__name__)

#: Everything user-facing is stated in Hong Kong time. The freeze times are business
#: hours there, and a UTC-labelled 01:00 edition would be quoted as the wrong day.
HKT = timezone(timedelta(hours=8))

#: The two frozen cuts of the day, in HKT.
FREEZE_HOURS: dict[EditionKind, int] = {
    EditionKind.MORNING: 9,
    EditionKind.AFTERNOON: 17,
}


class FrozenEditionError(RuntimeError):
    """An attempt to modify an edition that has already been frozen."""


@dataclass(frozen=True, slots=True)
class EditionRef:
    """How an edition is named in a URL, an export filename and a citation."""

    key: str
    kind: EditionKind
    trading_date: str

    @property
    def label(self) -> str:
        suffix = {
            EditionKind.LIVE: "Live",
            EditionKind.MORNING: "09:00",
            EditionKind.AFTERNOON: "17:00",
        }[self.kind]
        return f"{self.trading_date} {suffix}"


def trading_date_for(moment: datetime) -> str:
    """The HKT trading day a UTC moment belongs to.

    Not derivable from the UTC date: 09:00 HKT is 01:00 UTC the same day, but 17:00
    HKT on the 26th is 09:00 UTC on the 26th while a 23:00 HKT event is the 26th in
    HKT and the 15:00 UTC of the 26th — the mapping only holds if it goes through the
    offset rather than through the calendar.
    """
    return moment.astimezone(HKT).date().isoformat()


def edition_key(kind: EditionKind, trading_date: str) -> str:
    return f"{trading_date}.{kind.value}"


def scheduled_as_of(kind: EditionKind, trading_day: date) -> datetime:
    """The data cut-off a given freeze uses, in UTC.

    The cut-off is the scheduled time, not the moment the job happened to run. A job
    that starts three minutes late must produce the same edition it would have
    produced on time, or two runs of the same freeze disagree.
    """
    if kind is EditionKind.LIVE:
        raise ValueError("the live edition has no scheduled cut-off")
    hour = FREEZE_HOURS[kind]
    local = datetime(
        trading_day.year, trading_day.month, trading_day.day, hour, tzinfo=HKT
    )
    return local.astimezone(timezone.utc)


def current_live(session: Session, *, as_of: datetime) -> Edition:
    """The rolling edition. Never frozen, never quotable, always nameable.

    It exists so the status bar on every page can state which edition the reader is
    looking at. "Live" is an answer; a blank field is not.
    """
    trading_date = trading_date_for(as_of)
    key = edition_key(EditionKind.LIVE, trading_date)
    existing = session.execute(
        select(Edition).where(Edition.edition_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        # The live edition tracks the data; that is what makes it live, and also
        # what makes it unquotable.
        existing.as_of = as_of
        existing.generated_at = utcnow()
        return existing

    edition = Edition(
        edition_key=key,
        kind=EditionKind.LIVE,
        status=EditionStatus.LIVE,
        revision=1,
        as_of=as_of,
        generated_at=utcnow(),
        trading_date=trading_date,
    )
    session.add(edition)
    return edition


def freeze(
    session: Session,
    *,
    kind: EditionKind,
    as_of: datetime,
    payload: Mapping[str, Any] | None = None,
    theme_map_version: int | None = None,
) -> Edition:
    """Write the once-only frozen edition for one cut of one trading day.

    Raises :class:`FrozenEditionError` if that edition already exists. Re-running a
    freeze is not a way to pick up late data — that is what a revision is for.
    """
    if kind is EditionKind.LIVE:
        raise ValueError("the live edition is not frozen; use current_live()")

    trading_date = trading_date_for(as_of)
    key = edition_key(kind, trading_date)
    existing = (
        session.execute(
            select(Edition)
            .where(Edition.edition_key == key)
            .where(Edition.status != EditionStatus.FAILED)
            .order_by(Edition.revision.desc())
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise FrozenEditionError(
            f"edition {key} already exists at revision {existing.revision}; "
            "late or corrected data must go through revision.revise()"
        )

    edition = Edition(
        edition_key=key,
        kind=kind,
        status=EditionStatus.FROZEN,
        revision=1,
        as_of=as_of,
        generated_at=utcnow(),
        trading_date=trading_date,
        theme_map_version=theme_map_version,
        payload_json=(
            json.dumps(dict(payload), ensure_ascii=False, default=str)
            if payload is not None
            else None
        ),
    )
    session.add(edition)
    logger.info("froze edition %s at as_of=%s", key, as_of.isoformat())
    return edition


def record_failure(
    session: Session, *, kind: EditionKind, as_of: datetime, reason: str
) -> Edition:
    """Write a FAILED edition so the gap is visible rather than assumed away.

    A freeze that simply does not happen is indistinguishable, on screen, from a
    freeze whose numbers did not move. This row makes the difference explicit and is
    what the alerting hangs off.
    """
    trading_date = trading_date_for(as_of)
    edition = Edition(
        edition_key=edition_key(kind, trading_date),
        kind=kind,
        status=EditionStatus.FAILED,
        revision=1,
        as_of=as_of,
        generated_at=utcnow(),
        trading_date=trading_date,
        failure_reason=reason,
    )
    session.add(edition)
    logger.error("edition freeze failed for %s: %s", edition.edition_key, reason)
    return edition


def latest(
    session: Session, *, kind: EditionKind | None = None, limit: int = 20
) -> list[Edition]:
    """Recent editions, newest first, superseded revisions included.

    Superseded rows are returned on purpose: the list is how someone finds the
    version they quoted three weeks ago.
    """
    stmt = select(Edition)
    if kind is not None:
        stmt = stmt.where(Edition.kind == kind)
    stmt = stmt.order_by(Edition.as_of.desc(), Edition.revision.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def resolve(session: Session, edition_key_value: str) -> Edition | None:
    """The current revision of one edition key."""
    stmt = (
        select(Edition)
        .where(Edition.edition_key == edition_key_value)
        .where(Edition.status != EditionStatus.SUPERSEDED)
        .order_by(Edition.revision.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


__all__ = [
    "FREEZE_HOURS",
    "HKT",
    "EditionRef",
    "FrozenEditionError",
    "current_live",
    "edition_key",
    "freeze",
    "latest",
    "record_failure",
    "resolve",
    "scheduled_as_of",
    "trading_date_for",
]
