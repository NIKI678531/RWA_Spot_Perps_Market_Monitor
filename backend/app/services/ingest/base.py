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
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._bucket = TokenBucket(self.rate_limit_per_minute)
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

    def post_json(self, path: str, json_body: Mapping[str, Any]) -> FetchResult:
        return self._request("POST", path, json_body=json_body)

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> FetchResult:
        last_error: str | None = None
        last_status: int | None = None

        for attempt in range(1, self.max_attempts + 1):
            self._bucket.acquire()
            started = time.monotonic()
            try:
                response = self._client.request(
                    method, path, params=params, json=json_body
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                last_status = response.status_code

                if response.status_code in RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}"
                    # Exponential backoff. The sleep is deliberate and blocking: the
                    # scheduler runs collectors sequentially, and racing a throttled
                    # source only deepens the throttle.
                    time.sleep(2 ** (attempt - 1))
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

                payload = response.json()
                return FetchResult(
                    source_id=self.source_id,
                    endpoint=path,
                    status=FetchStatus.OK,
                    payload=payload,
                    http_status=response.status_code,
                    duration_ms=elapsed_ms,
                    record_count=_count_records(payload),
                    attempt=attempt,
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(2 ** (attempt - 1))

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
            attempt=self.max_attempts,
        )


def _count_records(payload: Any) -> int | None:
    """Best-effort row count for the fetch log. Null when the shape is unknown."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "tickers", "coins", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
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
