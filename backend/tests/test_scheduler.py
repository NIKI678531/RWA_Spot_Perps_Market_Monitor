"""Tests for collection-pass isolation.

One broken source must cost its own rows and nothing else, and the breakage has to be
visible in ``fetch_log``: a source that failed and a source that was never scheduled
look identical from the data-quality page otherwise, and they need opposite responses.
"""

from datetime import datetime
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.models.enums import FetchStatus
from app.models.operations import FetchLog
from app.services import scheduler
from app.services.ingest import registry
from app.services.ingest.base import Collector, FetchResult


class _Recording(Collector):
    """A collector that succeeds and says so."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.ran = False

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        self.ran = True
        return [
            FetchResult(
                source_id=self.source_id,
                endpoint="/ok",
                status=FetchStatus.OK,
                record_count=1,
            )
        ]


class _Exploding(Collector):
    """A collector that raises before returning anything, as a timeout would."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        raise RuntimeError("upstream timed out")


class _Throttled(Collector):
    """A collector whose source answered 429. It returns, having observed nothing."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.ran = False

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        self.ran = True
        return [
            FetchResult(
                source_id=self.source_id,
                endpoint="/throttled",
                status=FetchStatus.RATE_LIMITED,
                http_status=429,
            )
        ]


@pytest.fixture()
def factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """An in-memory warehouse with the source registry seeded.

    ``fetch_log.source_id`` is a foreign key into ``source_registry``, so a collector
    cannot record its own failure until bootstrap has run.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as session:
        registry.seed(session)
        session.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", maker)
    yield maker


def test_a_crashing_collector_does_not_cost_the_pass_the_others(
    factory: sessionmaker[Session],
) -> None:
    healthy = _Recording("binance")
    result = scheduler.run_pass(
        [_Exploding("coingecko"), healthy], detect=False, label="test"
    )

    assert healthy.ran, "the collector after the crash still ran"
    assert result.fetches == 1
    assert result.failures == 1


def test_a_crashing_collector_leaves_a_not_verified_row(
    factory: sessionmaker[Session],
) -> None:
    scheduler.run_pass([_Exploding("coingecko")], detect=False, label="test")

    with factory() as session:
        rows = list(session.execute(select(FetchLog)).scalars())

    assert len(rows) == 1
    assert rows[0].source_id == "coingecko"
    assert rows[0].status is FetchStatus.NOT_VERIFIED
    # The reason survives into the table the data-quality page reads, not just the
    # application log nobody queries.
    assert "upstream timed out" in (rows[0].error_message or "")


def test_a_source_already_observed_at_this_instant_is_not_collected_again(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collision that cost a live pass 232 Hyperliquid contracts.

    ``now_utc`` truncates to the minute and ``HyperliquidCollector`` is registered on
    both the 15-minute interval and the hourly cron, so the two passes share an
    instant several times a day. Re-collecting would re-insert the same
    ``(contract_id, snapshot_ts)`` rows, and because they go in as one executemany the
    duplicate costs the collector *every* row it gathered, not just the repeat.
    """
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 8, 18, 2, 59))

    first = _Recording("hyperliquid")
    scheduler.run_pass([first], detect=False, label="headline")

    second = _Recording("hyperliquid")
    result = scheduler.run_pass([second], detect=False, label="hourly")

    assert first.ran
    assert not second.ran, "the same source was collected twice at one instant"
    # Skipping is not a failure. It reports nothing because it did nothing.
    assert result.fetches == 0
    assert result.failures == 0

    with factory() as session:
        rows = list(session.execute(select(FetchLog)).scalars())
    assert len(rows) == 1, "the skipped pass invented a second observation"


def test_a_source_that_observed_nothing_is_still_retried_at_the_same_instant(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 is not an observation, so the next pass must still try.

    Skipping on any prior ``fetch_log`` row rather than a successful one would turn a
    single throttled minute into a permanent hole: the later pass has its own rate
    budget and would often have succeeded.
    """
    monkeypatch.setattr(scheduler, "now_utc", lambda: datetime(2026, 8, 18, 2, 59))

    scheduler.run_pass([_Throttled("coingecko")], detect=False, label="hourly")

    retry = _Recording("coingecko")
    result = scheduler.run_pass([retry], detect=False, label="long_tail")

    assert retry.ran, "a throttled source was treated as already observed"
    assert result.fetches == 1


def test_every_job_runs_on_one_worker() -> None:
    """Two different passes must not write at the same time.

    ``max_instances=1`` is per job, so it stops a pass colliding with itself and
    nothing else. The schedule guarantees different jobs meet: the hourly pass fires
    at :05 and holds its transaction for over ten minutes because its sources are
    rate-limited, while headline fires every fifteen minutes and long_tail at :20. A
    live run with the default pool logged 72 ``database is locked`` errors and lost a
    collector from every source in the pass.
    """
    scheduler_ = scheduler.build_scheduler()

    executor = scheduler_._lookup_executor("default")

    assert executor._pool._max_workers == 1


def test_a_headline_pass_delayed_by_another_job_still_runs() -> None:
    """Serialising jobs must not silently convert collisions into missing snapshots.

    With one worker the headline pass waits behind the hourly one, which runs well
    past the old 300s grace. Under that setting APScheduler would have declared it a
    misfire and dropped it every hour — a guaranteed hole rather than a late row.
    """
    jobs = {job.id: job for job in scheduler.build_scheduler().get_jobs()}

    headline = jobs["headline_snapshot"]

    assert headline.misfire_grace_time >= headline.trigger.interval.total_seconds()
