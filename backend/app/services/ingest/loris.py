"""Loris Tools cross-venue perpetual aggregation. Key-gated, never scheduled.

The workbook's ``09_Perps_Venues`` and ``10_Perps_Summary`` came from the
``loris.tools/rwa`` public page. That page renders client-side, so its HTML carries
venue names and no figures at all. The API behind it is ``api.loris.tools``; the
endpoints below were recovered from the site's own JavaScript bundles and every one
of them answers ``401 Missing API key``.

This collector exists so that supplying ``LORIS_API_KEY`` is the only step needed to
turn the source on. Until then ``source_registry`` keeps it ``PLANNED`` and the
scheduler never calls it, with the five exchange collectors in ``cex_perps``
covering the same ground without a key. See ADR 0006.

**The response shape below is inferred, not observed.** Nobody has seen a 200 from
this API, so the field names are guesses taken from the endpoint names and the
front-end code. The parser therefore treats an unrecognised shape as a parse failure
rather than as an empty market, and the status must not be moved to ``ACTIVE`` until
a real response has been checked against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.enums import FetchStatus
from app.models.facts import FactPerpVenueSnapshot
from app.services.ingest.base import Collector, FetchResult, HttpFetcher

SOURCE_ID = "loris"

#: Recovered from the site's Next.js chunks. Listed in full so the next person does
#: not have to re-read the bundles to find out what was probed.
EXCHANGES_ENDPOINT = "/rwa/exchanges"
TIMESERIES_ENDPOINT = "/rwa/aggregates-timeseries"
SYMBOLS_ENDPOINT = "/markets/symbols"

#: Field names the parser will accept for each figure, in preference order. A list
#: rather than one name because the shape is unverified; see the module docstring.
_NAME_KEYS = ("exchange", "name", "venue")
_VOLUME_KEYS = ("volume_24h", "volume24h", "volume", "vol_24h")
_OI_KEYS = ("open_interest", "openInterest", "oi", "open_interest_usd")
_COUNT_KEYS = ("symbol_count", "symbols", "markets", "market_count")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _first(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


@dataclass
class LorisCollector(Collector):
    """Reads the cross-venue perp table. Degrades to ``NOT_VERIFIED`` without a key."""

    source_id: str = SOURCE_ID

    def _fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.loris_api_base_url,
            rate_limit_per_minute=30,
            headers={"X-API-Key": settings.loris_api_key},
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        if not settings.loris_api_key:
            # A missing credential is a missing observation, not an empty market. It
            # is logged so the data-quality page can say why this source is blank
            # instead of leaving a silent hole.
            return [
                FetchResult(
                    source_id=self.source_id,
                    endpoint=EXCHANGES_ENDPOINT,
                    status=FetchStatus.NOT_VERIFIED,
                    error="LORIS_API_KEY is not configured; source not collected.",
                )
            ]

        market_session = classify_session(snapshot_ts)
        with self._fetcher() as fetcher:
            result = fetcher.get_json(EXCHANGES_ENDPOINT)

        if not result.ok:
            return [result]

        rows = _exchange_rows(result.payload)
        if not rows:
            # HTTP 200 that parses to nothing means the shape changed, or was never
            # what we guessed. Reporting it as a successful fetch of zero venues
            # would publish the perp market as having vanished.
            return [
                FetchResult(
                    source_id=self.source_id,
                    endpoint=EXCHANGES_ENDPOINT,
                    status=FetchStatus.NOT_VERIFIED,
                    http_status=result.http_status,
                    duration_ms=result.duration_ms,
                    error="200 OK but no exchange rows parsed; response shape "
                    "differs from the inferred one. Check before trusting.",
                )
            ]

        for row in rows:
            name = _first(row, _NAME_KEYS)
            if not name:
                continue
            count = _first(row, _COUNT_KEYS)
            session.add(
                FactPerpVenueSnapshot(
                    exchange=str(name),
                    perp_dex="",
                    segment="all",
                    snapshot_ts=snapshot_ts,
                    market_session=market_session,
                    vol_24h=_decimal(_first(row, _VOLUME_KEYS)),
                    open_interest_usd=_decimal(_first(row, _OI_KEYS)),
                    symbol_count=int(count) if isinstance(count, int) else None,
                    # The aggregator does not say how many of its symbols the open
                    # interest covers, and null is how this column says "unstated".
                    oi_symbol_count=None,
                )
            )
        return [result]


def _exchange_rows(payload: Any) -> list[Mapping[str, Any]]:
    """Pull the venue list out, tolerating a bare list or a wrapped one."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("data", "exchanges", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, Mapping)]
    return []
