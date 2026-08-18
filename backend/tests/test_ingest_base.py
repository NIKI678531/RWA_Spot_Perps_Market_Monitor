"""Tests for the retry policy every collector inherits.

A 429 and a 502 are different failures and cannot share a backoff. The 502 is one bad
request; the 429 means a rate-limit window has to expire, and a delay shorter than the
window just spends the remaining attempts confirming it.

The counterweight is that a *throttled* source must not be retried indefinitely
either: waiting out the window once per request is how a pass stops finishing at all.
"""

import httpx
import pytest

from app.models.enums import FetchStatus
from app.services.ingest.base import (
    MAX_RETRY_DELAY_SECONDS,
    HttpFetcher,
    retry_delay,
)


def _response(status: int, **headers: str) -> httpx.Response:
    return httpx.Response(status, headers=headers)


def test_retry_after_wins_over_the_computed_backoff() -> None:
    """The header is the origin saying when it will answer. Guessing shorter is waste."""
    assert retry_delay(_response(429, **{"Retry-After": "7"}), attempt=1) == 7.0


def test_retry_after_is_capped() -> None:
    """A scheduler thread parked for an hour costs every source after it in the pass."""
    delay = retry_delay(_response(503, **{"Retry-After": "3600"}), attempt=1)
    assert delay == MAX_RETRY_DELAY_SECONDS


def test_an_http_date_retry_after_falls_through_rather_than_raising() -> None:
    """None of these sources send the date form; being wrong about clock skew is worse."""
    delay = retry_delay(
        _response(429, **{"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), attempt=1
    )
    assert delay == 15.0


def test_a_throttle_backs_off_far_harder_than_a_server_error() -> None:
    """1s/2s/4s cannot outlast a per-minute window, which is how three tries all 429'd."""
    throttled = [retry_delay(_response(429), attempt=n) for n in (1, 2, 3)]
    server_error = [retry_delay(_response(502), attempt=n) for n in (1, 2, 3)]

    assert throttled == [15.0, 30.0, 60.0]
    assert server_error == [1.0, 2.0, 4.0]
    # The whole point: by the third attempt the throttled source has waited out a
    # minute-long window and the server error has not wasted one.
    assert sum(throttled[:2]) >= 45.0


@pytest.fixture()
def sleepless(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record what the fetcher would have slept instead of sleeping it."""
    slept: list[float] = []
    monkeypatch.setattr(
        "app.services.ingest.base.time.sleep", lambda s: slept.append(s)
    )
    return slept


def _always(status: int) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status))


def test_the_last_attempt_does_not_sleep_before_giving_up(
    sleepless: list[float],
) -> None:
    """The loop is over, so the wait buys nothing and delays the next collector."""
    # rate_limit_per_minute=0 disables the token bucket, whose own sleeps would
    # otherwise be indistinguishable from the retry backoff being measured.
    fetcher = HttpFetcher(
        source_id="test", base_url="https://example.test", rate_limit_per_minute=0
    )
    fetcher._client = httpx.Client(
        base_url="https://example.test", transport=_always(502)
    )

    result = fetcher.get_json("/thing")

    assert result.status is FetchStatus.NOT_VERIFIED
    # Three attempts, two gaps between them. A third sleep would be pure delay.
    assert sleepless == [1.0, 2.0]


def test_a_persistently_throttled_source_stops_being_retried(
    sleepless: list[float],
) -> None:
    """The failure this prevents is a pass that never ends.

    A 429 window is not cleared by requests made from inside it, so retrying every
    call costs the full backoff and buys nothing. One live CoinGecko pass spent 11
    minutes asleep this way and stored nothing. After the breaker trips each call is
    one round trip, and the pass finishes and writes NOT_VERIFIED rows.
    """
    # rate_limit_per_minute=0 disables the token bucket, whose own sleeps would
    # otherwise be indistinguishable from the retry backoff being measured.
    fetcher = HttpFetcher(
        source_id="test", base_url="https://example.test", rate_limit_per_minute=0
    )
    fetcher._client = httpx.Client(
        base_url="https://example.test", transport=_always(429)
    )

    first = fetcher.get_json("/one")
    sleeps_after_first = len(sleepless)
    later = [fetcher.get_json(f"/{n}") for n in range(2, 6)]

    assert first.status is FetchStatus.RATE_LIMITED
    assert all(r.status is FetchStatus.RATE_LIMITED for r in later)
    assert sleeps_after_first > 0, "the first request should still wait out the window"
    assert len(sleepless) == sleeps_after_first, "later requests must not sleep at all"
    # And the log says how few attempts were made, so a fast empty result is
    # explainable rather than mysterious.
    assert [r.attempt for r in later] == [1, 1, 1, 1]


def test_a_source_that_recovers_is_retried_again(sleepless: list[float]) -> None:
    """A tripped breaker is not a permanent verdict on the source."""
    answers = iter([429, 429, 429, 200, 502, 502, 200])
    # rate_limit_per_minute=0 disables the token bucket, whose own sleeps would
    # otherwise be indistinguishable from the retry backoff being measured.
    fetcher = HttpFetcher(
        source_id="test", base_url="https://example.test", rate_limit_per_minute=0
    )
    fetcher._client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(next(answers), json={})
        ),
    )

    assert fetcher.get_json("/throttled").status is FetchStatus.RATE_LIMITED
    assert fetcher.get_json("/recovered").status is FetchStatus.OK

    # The 200 reset the counter, so this one gets its full retry budget back and
    # succeeds on the third attempt rather than failing fast.
    assert fetcher.get_json("/flaky").status is FetchStatus.OK
