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
