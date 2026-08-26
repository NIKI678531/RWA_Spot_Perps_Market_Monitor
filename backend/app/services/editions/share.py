"""Read-only share links for a frozen edition.

A share link is an anonymous bearer credential handed to someone outside the team, so
two things have to be true at once: it must name exactly one edition revision, and it
must not be forgeable into naming a different one. A signed, self-describing token
gives both without a table to keep in sync — the link carries its own claims and the
signature is what makes them trustworthy.

What the holder then sees is decided by :mod:`redaction`, on the server, before the
payload is serialised. The token controls *which* edition; redaction controls *what*
of it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.base import utcnow


class ShareError(RuntimeError):
    """A share link that cannot be issued or cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ShareClaims:
    """What a valid token asserts."""

    edition_key: str
    revision: int
    expires_at: datetime | None


def is_enabled() -> bool:
    return bool(settings.share_secret)


def issue(
    *, edition_key: str, revision: int, expires_at: datetime | None = None
) -> tuple[str, datetime | None]:
    """Mint a token for one edition revision.

    Pinned to a revision rather than to the edition key. A link that followed the
    latest revision would silently change the numbers under a reader who was sent it
    to look at a specific figure, which is the exact failure the freeze exists to
    prevent.
    """
    if not is_enabled():
        raise ShareError(
            "share links are disabled: set SHARE_SECRET. A link signed with a "
            "per-process key would stop working at the next restart."
        )

    expiry = expires_at
    if expiry is None and settings.share_link_ttl_hours > 0:
        expiry = utcnow() + timedelta(hours=settings.share_link_ttl_hours)

    claims = {
        "k": edition_key,
        "r": revision,
        "e": int(expiry.timestamp()) if expiry else None,
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(body)}", expiry


def verify(token: str) -> ShareClaims:
    """Read a token's claims, or refuse it.

    Every failure raises the same exception with the same message. Distinguishing
    "bad signature" from "expired" tells someone probing the endpoint which half of
    their guess was right.
    """
    if not is_enabled():
        raise ShareError("invalid share link")

    body, _, signature = token.partition(".")
    if not body or not signature:
        raise ShareError("invalid share link")
    if not hmac.compare_digest(signature, _sign(body)):
        raise ShareError("invalid share link")

    try:
        padding = "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body + padding))
        edition_key = str(claims["k"])
        revision = int(claims["r"])
        raw_expiry = claims.get("e")
    except (KeyError, TypeError, ValueError) as exc:
        raise ShareError("invalid share link") from exc

    expires_at = (
        datetime.fromtimestamp(raw_expiry, tz=timezone.utc) if raw_expiry else None
    )
    if expires_at is not None and expires_at <= utcnow():
        raise ShareError("invalid share link")
    return ShareClaims(
        edition_key=edition_key, revision=revision, expires_at=expires_at
    )


def _sign(body: str) -> str:
    digest = hmac.new(
        settings.share_secret.encode(), body.encode(), hashlib.sha256
    ).digest()
    return _b64(digest)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


__all__ = ["ShareClaims", "ShareError", "is_enabled", "issue", "verify"]
