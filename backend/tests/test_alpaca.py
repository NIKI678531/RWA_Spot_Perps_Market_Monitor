"""Tests for the TradFi reference-price collector.

Two things about this source make it easy to get quietly wrong, so most of what is
asserted here is about them:

* it needs a credential nobody has yet, and a missing credential must read as a
  missing observation rather than as a market with no prices in it;
* it quotes a market that is *closed* most of the hours this system runs, so a row
  has to carry the source's own timestamp or a weekend looks like a mispricing.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.config import settings
from app.db.base import Base
from app.models.dimensions import DimUnderlying
from app.models.enums import AssetClass, FetchStatus
from app.models.facts import FactUnderlyingReference
from app.services.ingest import alpaca
from app.services.ingest.alpaca import AlpacaCollector, _change, _parse_ts

TS = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as db:
        db.add_all(
            [
                DimUnderlying(
                    underlying_id="AAPL", name="Apple", asset_class=AssetClass.EQUITY
                ),
                DimUnderlying(
                    underlying_id="SPY", name="SPDR S&P 500", asset_class=AssetClass.ETF
                ),
                # Not securities Alpaca quotes. Each is excluded by a different rule.
                DimUnderlying(
                    underlying_id="SPX",
                    name="S&P 500 Index",
                    asset_class=AssetClass.INDEX,
                ),
                DimUnderlying(
                    underlying_id="XAU", name="Gold", asset_class=AssetClass.COMMODITY
                ),
                DimUnderlying(
                    underlying_id="OPENAI",
                    name="OpenAI",
                    asset_class=AssetClass.EQUITY,
                    is_pre_ipo=True,
                ),
            ]
        )
        for row in db.execute(select(DimUnderlying)).scalars():
            if row.underlying_id != "XAU":
                row.region = "US"
        db.flush()
        yield db


@pytest.fixture()
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "alpaca_api_key_id", "key")
    monkeypatch.setattr(settings, "alpaca_api_secret_key", "secret")


def _snapshot(
    price: float | None = 232.5,
    prev: float | None = 230.0,
    stamp: str = "2026-08-15T19:59:59.123456789Z",
    volume: float | None = 1_250_000,
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    if price is not None:
        row["latestTrade"] = {"p": price, "t": stamp}
    if volume is not None:
        row["dailyBar"] = {"c": price, "v": volume}
    if prev is not None:
        row["prevDailyBar"] = {"c": prev}
    return row


def _serve(
    monkeypatch: pytest.MonkeyPatch, payload: Any, ok: bool = True
) -> list[dict[str, Any]]:
    """Answer every batch with one canned payload. Returns the params seen."""
    seen: list[dict[str, Any]] = []

    class _Fetcher:
        def __enter__(self) -> "_Fetcher":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get_json(self, path: str, params: Any = None) -> alpaca.FetchResult:
            seen.append(dict(params or {}))
            return alpaca.FetchResult(
                source_id=alpaca.SOURCE_ID,
                endpoint=path,
                status=FetchStatus.OK if ok else FetchStatus.NOT_VERIFIED,
                payload=payload if ok else None,
                http_status=200 if ok else 500,
            )

    monkeypatch.setattr(AlpacaCollector, "_fetcher", lambda self: _Fetcher())
    return seen


def _rows(session: Session) -> dict[str, FactUnderlyingReference]:
    return {
        r.underlying_id: r
        for r in session.execute(select(FactUnderlyingReference)).scalars()
    }


def test_a_missing_key_is_not_verified_rather_than_an_empty_market(
    session: Session,
) -> None:
    results = AlpacaCollector().collect(session, TS)
    assert [r.status for r in results] == [FetchStatus.NOT_VERIFIED]
    assert "ALPACA_API_KEY_ID" in (results[0].error or "")
    assert _rows(session) == {}


def test_only_quotable_underlyings_are_requested(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    """An index, a spot metal and a pre-IPO name have no listing to quote.

    Asking for them would turn "outside this source's universe" into a fetch failure,
    and those two need opposite responses on the data-quality page.
    """
    seen = _serve(monkeypatch, {"AAPL": _snapshot(), "SPY": _snapshot()})
    AlpacaCollector().collect(session, TS)
    assert [p["symbols"] for p in seen] == ["AAPL,SPY"]


def test_a_quote_is_written_with_the_sources_own_timestamp(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    """The market was shut for three days when this snapshot was taken."""
    _serve(monkeypatch, {"AAPL": _snapshot(), "SPY": _snapshot()})
    AlpacaCollector().collect(session, TS)
    session.flush()

    row = _rows(session)["AAPL"]
    assert row.price == Decimal("232.5")
    assert row.prev_close == Decimal("230")
    assert row.venue_vol_shares == Decimal("1250000")
    assert row.feed == "iex"
    # Friday's close, read on a Tuesday. Storing snapshot_ts here instead would erase
    # the only evidence that the reference had not moved.
    #
    # Compared naive because neither backend keeps the offset: SQLite and MySQL both
    # store DATETIME as naive UTC. What is under test is the instant, not the tz
    # round-trip, and _parse_ts is what makes it UTC in the first place.
    assert row.price_ts is not None
    assert row.price_ts.replace(tzinfo=None) == datetime(
        2026, 8, 15, 19, 59, 59, 123456
    )


def test_the_change_is_computed_against_the_previous_close(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    _serve(monkeypatch, {"AAPL": _snapshot(price=110.0, prev=100.0), "SPY": {}})
    AlpacaCollector().collect(session, TS)
    session.flush()
    assert _rows(session)["AAPL"].change_24h == Decimal("0.1")


def test_a_zero_previous_close_does_not_take_down_the_batch(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    """A feed glitch printing 0.00 must cost one column, not every symbol after it."""
    _serve(
        monkeypatch,
        {"AAPL": _snapshot(price=110.0, prev=0.0), "SPY": _snapshot(price=5.0)},
    )
    AlpacaCollector().collect(session, TS)
    session.flush()

    rows = _rows(session)
    assert rows["AAPL"].change_24h is None
    assert rows["AAPL"].price == Decimal("110")
    assert rows["SPY"].price == Decimal("5")


def test_a_symbol_the_feed_does_not_carry_writes_no_row(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    """Absent from the response means unquoted, which is not a price of zero."""
    _serve(monkeypatch, {"AAPL": _snapshot()})
    results = AlpacaCollector().collect(session, TS)
    session.flush()

    assert set(_rows(session)) == {"AAPL"}
    # Short coverage is reported as partial rather than as a clean fetch, because the
    # row count alone cannot distinguish "one symbol" from "one of two symbols".
    assert [r.status for r in results] == [FetchStatus.PARTIAL]
    assert "1 of 2" in (results[0].error or "")


def test_an_entry_with_no_prices_at_all_writes_no_row(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    """A row of nulls would claim we looked at a market and found none."""
    _serve(monkeypatch, {"AAPL": {"latestQuote": {}}, "SPY": _snapshot()})
    AlpacaCollector().collect(session, TS)
    session.flush()
    assert set(_rows(session)) == {"SPY"}


def test_a_failed_fetch_writes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    _serve(monkeypatch, None, ok=False)
    results = AlpacaCollector().collect(session, TS)
    session.flush()
    assert _rows(session) == {}
    assert [r.status for r in results] == [FetchStatus.NOT_VERIFIED]


def test_the_wrapped_response_shape_is_accepted(
    session: Session, monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    """Alpaca has shipped both a bare symbol map and a ``snapshots`` envelope."""
    _serve(monkeypatch, {"snapshots": {"AAPL": _snapshot(), "SPY": _snapshot()}})
    AlpacaCollector().collect(session, TS)
    session.flush()
    assert set(_rows(session)) == {"AAPL", "SPY"}


def test_symbols_are_requested_in_a_stable_order(session: Session) -> None:
    """An unordered query rotates which symbols fall off a truncated batch."""
    assert alpaca._tracked_symbols(session) == ["AAPL", "SPY"]


def test_an_empty_underlying_table_is_not_blamed_on_the_source(
    monkeypatch: pytest.MonkeyPatch, keyed: None
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as empty:
        results = AlpacaCollector().collect(empty, TS)
    assert [r.status for r in results] == [FetchStatus.OK]
    assert results[0].record_count == 0


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-08-15T19:59:59.123456789Z", datetime(2026, 8, 15, 19, 59, 59, 123456)),
        ("2026-08-15T19:59:59Z", datetime(2026, 8, 15, 19, 59, 59)),
        ("2026-08-15T19:59:59.5Z", datetime(2026, 8, 15, 19, 59, 59, 500000)),
    ],
)
def test_nanosecond_timestamps_are_parsed(text: str, expected: datetime) -> None:
    """``datetime`` holds microseconds; the feed stamps to the nanosecond."""
    assert _parse_ts(text) == expected.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("text", ["", "not a time", None, 17])
def test_an_unparseable_timestamp_is_null_not_now(text: object) -> None:
    """Substituting the current time would hide exactly the staleness we store it for."""
    assert _parse_ts(text) is None


def test_change_needs_both_sides() -> None:
    assert _change(None, Decimal("100")) is None
    assert _change(Decimal("100"), None) is None


def test_the_collector_is_not_scheduled_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daily wall of known failures is how a data-quality page gets ignored."""
    monkeypatch.setattr(settings, "alpaca_api_key_id", "")
    assert alpaca.build_collectors() == []


def test_the_collector_is_scheduled_once_a_key_exists(keyed: None) -> None:
    assert [c.source_id for c in alpaca.build_collectors()] == [alpaca.SOURCE_ID]


def test_the_source_is_registered() -> None:
    from app.services.ingest import registry

    spec = next(s for s in registry.SOURCES if s.source_id == alpaca.SOURCE_ID)
    # A share price is not one of the five metric families and must never be summed
    # into one; declaring a scope here is how that would start.
    assert spec.scopes == ()
