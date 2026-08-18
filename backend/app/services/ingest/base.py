"""Collector infrastructure.

This layer does exactly two things: fetch, and record what happened. It performs no
unit conversion, no deduplication and no scope judgement — those belong to
``services/normalize``. Keeping the boundary strict means a bad transform can always
be re-run against stored payloads instead of re-hitting a rate-limited source.

The contract that matters: a failed fetch produces ``NOT_VERIFIED``, never a zero.
Coercing a missing observation to 0 silently understates every aggregate it flows
into, and the resulting chart looks complete.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

import httpx
from sqlalchemy.orm import Session

from app.models.enums import FetchStatus
from app.models.operations import FetchLog

logger = logging.getLogger(__name__)

#: Transport-level failures worth retrying. A 4xx is not here on purpose: retrying a
#: 403 against a challenge-protected source just burns the rate limit.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Ceiling on any single retry wait. A server is free to answer ``Retry-After: 3600``;
#: honouring that literally would park a scheduler thread for an hour and turn one
#: throttled source into a missing snapshot for every source after it in the pass.
MAX_RETRY_DELAY_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The outcome of one HTTP call, successful or not."""

    source_id: str
    endpoint: str
    status: FetchStatus
    payload: Any | None = None
    http_status: int | None = None
    duration_ms: int = 0
    record_count: int | None = None
    error: str | None = None
    attempt: int = 1

    @property
    def ok(self) -> bool:
        return self.status in (FetchStatus.OK, FetchStatus.PARTIAL)

    def to_log(self, snapshot_ts: datetime) -> FetchLog:
        return FetchLog(
            source_id=self.source_id,
            snapshot_ts=snapshot_ts,
            endpoint=self.endpoint,
            status=self.status,
            http_status=self.http_status,
            record_count=self.record_count,
            duration_ms=self.duration_ms,
            attempt=self.attempt,
            error_message=self.error,
        )


class TokenBucket:
    """A simple rate limiter.

    CoinGecko's free tier allows roughly 30 requests per minute and answers with 429
    beyond it. Blocking locally is cheaper than being throttled remotely, because a
    429 costs the request *and* the backoff.
    """

    def __init__(self, rate_per_minute: int) -> None:
        self._interval = 60.0 / rate_per_minute if rate_per_minute > 0 else 0.0
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._interval <= 0:
            return
        now = time.monotonic()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
        self._next_allowed = max(now, self._next_allowed) + self._interval


@dataclass
class HttpFetcher:
    """A JSON fetcher that turns every failure mode into a ``FetchResult``.

    It never raises for a source problem. A collector that has to wrap each call in
    try/except eventually forgets one, and the missed exception aborts the whole
    snapshot instead of degrading a single source.
    """

    source_id: str
    base_url: str
    rate_limit_per_minute: int = 30
    timeout_seconds: float = 30.0
    max_attempts: int = 3
    #: Consecutive 429s after which this fetcher stops retrying — see ``_request``.
    throttle_trip_after: int = 2
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._bucket = TokenBucket(self.rate_limit_per_minute)
        self._consecutive_throttles = 0
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            headers=dict(self.headers),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> FetchResult:
        return self._request("GET", path, params=params)

    def get_text(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> FetchResult:
        """Fetch a page as text, for sources that publish HTML rather than JSON.

        Same retries, same rate limiting, same ``FetchResult``. A scraped page and an
        API call must be indistinguishable in ``fetch_log`` — the data-quality page
        reports on sources, and how a source happens to be read is not the operator's
        problem until it breaks, at which point the error message says so.
        """
        return self._request("GET", path, params=params, as_text=True)

    def post_json(self, path: str, json_body: Mapping[str, Any]) -> FetchResult:
        return self._request("POST", path, json_body=json_body)

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        as_text: bool = False,
    ) -> FetchResult:
        last_error: str | None = None
        last_status: int | None = None

        # Once a source has answered 429 twice running, retrying is futile: the limit
        # is a *window*, and every request from inside it is another the window has to
        # clear. Retrying anyway is what turned one throttled CoinGecko pass into 11
        # minutes of sleeping — 45s per request across 45 requests, for nothing. From
        # here each call costs one round trip and returns RATE_LIMITED, so the pass
        # ends and writes NOT_VERIFIED rows instead of never ending. Any non-429
        # answer resets it, so a source that recovers mid-pass is retried again.
        attempts = (
            1
            if self._consecutive_throttles >= self.throttle_trip_after
            else self.max_attempts
        )

        for attempt in range(1, attempts + 1):
            self._bucket.acquire()
            started = time.monotonic()
            try:
                response = self._client.request(
                    method, path, params=params, json=json_body
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                last_status = response.status_code

                if response.status_code == 429:
                    self._consecutive_throttles += 1
                else:
                    self._consecutive_throttles = 0

                if response.status_code in RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}"
                    if attempt == attempts:
                        # No sleep after the final attempt: the loop is over, so the
                        # wait buys nothing and delays the collectors behind this one.
                        break
                    # Otherwise the sleep is deliberate and blocking. The scheduler
                    # runs collectors sequentially, and racing a throttled source only
                    # deepens the throttle.
                    time.sleep(retry_delay(response, attempt))
                    continue

                if response.status_code >= 400:
                    return FetchResult(
                        source_id=self.source_id,
                        endpoint=path,
                        status=FetchStatus.NOT_VERIFIED,
                        http_status=response.status_code,
                        duration_ms=elapsed_ms,
                        error=f"HTTP {response.status_code}: {response.text[:500]}",
                        attempt=attempt,
                    )

                payload = response.text if as_text else response.json()
                return FetchResult(
                    source_id=self.source_id,
                    endpoint=path,
                    status=FetchStatus.OK,
                    payload=payload,
                    http_status=response.status_code,
                    duration_ms=elapsed_ms,
                    record_count=None if as_text else _count_records(payload),
                    attempt=attempt,
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt == attempts:
                    break
                time.sleep(2.0 ** (attempt - 1))

        status = (
            FetchStatus.RATE_LIMITED if last_status == 429 else FetchStatus.NOT_VERIFIED
        )
        logger.warning(
            "fetch failed source=%s endpoint=%s status=%s error=%s",
            self.source_id,
            path,
            status,
            last_error,
        )
        return FetchResult(
            source_id=self.source_id,
            endpoint=path,
            status=status,
            http_status=last_status,
            error=last_error,
            # What was actually tried, not the configured maximum. Once the throttle
            # breaker has tripped that is one, and a log claiming three would hide
            # the reason a source came back empty so quickly.
            attempt=attempts,
        )


def retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying ``response``.

    ``Retry-After`` wins whenever the server sends it. That header is the origin
    stating when it will answer again, and guessing shorter only spends another
    request to be told the same thing.

    Otherwise a 429 backs off an order of magnitude harder than a 5xx, because they
    are different failures. A 502 is usually one bad request and a second is enough.
    A 429 means a rate-limit *window* has to expire, and 1s/2s/4s cannot outlast a
    per-minute window — which is why three attempts against CoinGecko's keyless tier
    all came back 429 and the pass stored nothing.
    """
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(max(float(header), 0.0), MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            # The HTTP-date form of the header. None of these sources send it, and
            # parsing it to be wrong about clock skew is worse than falling through
            # to the computed backoff below.
            pass
    base = 15.0 if response.status_code == 429 else 1.0
    return min(base * 2.0 ** (attempt - 1), MAX_RETRY_DELAY_SECONDS)


def _count_records(payload: Any) -> int | None:
    """Best-effort row count for the fetch log. Null when the shape is unknown."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "tickers", "coins", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            # Bybit wraps its rows one level deeper, as result.list. Counting the
            # envelope as a single record understates the fetch log, and the log is
            # what the data-quality page uses to say how much a source returned.
            if isinstance(value, dict) and isinstance(value.get("list"), list):
                return len(value["list"])
        return 1
    return None


def record_fetches(
    session: Session, snapshot_ts: datetime, results: Sequence[FetchResult]
) -> None:
    """Persist fetch outcomes. Called for successes and failures alike."""
    session.add_all([r.to_log(snapshot_ts) for r in results])


class Collector(ABC):
    """One source, one collector."""

    #: Must match a ``source_registry.source_id`` row.
    source_id: str

    @abstractmethod
    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        """Fetch, write facts, and return every fetch outcome for logging."""
