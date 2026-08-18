"""Tests for the cross-venue perpetual collectors.

Everything here runs offline against recorded response shapes. The shapes are
trimmed copies of live responses from the five venues, kept because the whole risk
in this collector is unit conversion: each venue reports open interest in a
different unit, and a mistake produces a number that looks entirely plausible.

The other risk is scope. These venues list roughly 3,000 contracts between them and
nearly all are crypto-native, so the tests assert on what is *not* stored as much as
on what is.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator, Mapping

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.models.dimensions import DimPerpContract, DimUnderlying
from app.models.enums import AssetClass, FetchStatus
from app.models.facts import FactPerpContractSnapshot, FactPerpVenueSnapshot
from app.services.ingest import cex_perps
from app.services.ingest.base import FetchResult
from app.services.ingest.cex_perps import (
    SEGMENT_ALL,
    SEGMENT_STOCK,
    BitgetAdapter,
    BybitAdapter,
    CexPerpCollector,
    GateAdapter,
    MexcAdapter,
    OkxAdapter,
    PerpRow,
    PerpVenueAdapter,
)

TS = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


class _FakeFetcher:
    """Answers each path from a canned map instead of going to the network."""

    def __init__(self, payloads: Mapping[str, Any]) -> None:
        self._payloads = payloads
        self.requested: list[str] = []

    def get_json(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> FetchResult:
        self.requested.append(path)
        return FetchResult(
            source_id="test",
            endpoint=path,
            status=FetchStatus.OK,
            payload=self._payloads.get(path),
        )

    def __enter__(self) -> "_FakeFetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _rows(adapter: PerpVenueAdapter, payloads: Mapping[str, Any]) -> list[PerpRow]:
    rows, _ = adapter.collect_rows(_FakeFetcher(payloads))  # type: ignore[arg-type]
    return rows


def _one(rows: list[PerpRow], symbol: str) -> PerpRow:
    return next(r for r in rows if r.symbol == symbol)


# --- per-venue unit conversion -------------------------------------------


def test_okx_turnover_converts_base_currency_at_price() -> None:
    """volCcy24h is in base currency, so USD turnover needs the price."""
    rows = _rows(
        OkxAdapter(),
        {
            "/api/v5/market/tickers": {
                "data": [
                    {"instId": "AAPL-USDT-SWAP", "last": "200", "volCcy24h": "1000"}
                ]
            },
            "/api/v5/public/open-interest": {
                "data": [{"instId": "AAPL-USDT-SWAP", "oiUsd": "8041177"}]
            },
        },
    )
    row = _one(rows, "AAPL-USDT-SWAP")
    assert row.base == "AAPL"
    assert row.vol_24h == 200_000
    # OKX publishes USD open interest itself, so it is taken rather than derived.
    assert row.oi_usd == 8_041_177


def test_gate_open_interest_applies_the_contract_multiplier() -> None:
    """total_size is in contracts. AAPLX_USDT is 0.01, so it cannot be assumed 1."""
    rows = _rows(
        GateAdapter(),
        {
            "/api/v4/futures/usdt/tickers": [
                {
                    "contract": "AAPLX_USDT",
                    "volume_24h_quote": "500",
                    "total_size": "1000",
                    "mark_price": "200",
                }
            ],
            "/api/v4/futures/usdt/contracts": [
                {"name": "AAPLX_USDT", "quanto_multiplier": "0.01"}
            ],
        },
    )
    row = _one(rows, "AAPLX_USDT")
    assert row.base == "AAPLX"
    assert row.oi_usd == 2_000  # 1000 x 0.01 x 200, not 200_000
    assert row.vol_24h == 500


def test_mexc_open_interest_applies_the_contract_size() -> None:
    rows = _rows(
        MexcAdapter(),
        {
            "/api/v1/contract/ticker": {
                "data": [
                    {
                        "symbol": "AAPLSTOCK_USDT",
                        "amount24": "700",
                        "holdVol": "5323024",
                        "fairPrice": "300",
                    }
                ]
            },
            "/api/v1/contract/detail": {
                "data": [{"symbol": "AAPLSTOCK_USDT", "contractSize": "0.01"}]
            },
        },
    )
    row = _one(rows, "AAPLSTOCK_USDT")
    assert row.base == "AAPLSTOCK"
    assert row.oi_usd == Decimal("15969072")  # 5,323,024 x 0.01 x 300


def test_bybit_reads_both_figures_from_one_call() -> None:
    rows = _rows(
        BybitAdapter(),
        {
            "/v5/market/tickers": {
                "result": {
                    "list": [
                        {
                            "symbol": "AAPLUSDT",
                            "turnover24h": "1234",
                            "openInterestValue": "5678",
                            "markPrice": "200",
                            "fundingRate": "0.0001",
                        }
                    ]
                }
            }
        },
    )
    row = _one(rows, "AAPLUSDT")
    assert row.base == "AAPL"
    assert (row.vol_24h, row.oi_usd) == (1234, 5678)
    assert row.funding_rate == Decimal("0.0001")


def test_bitget_converts_base_coin_open_interest_at_price() -> None:
    rows = _rows(
        BitgetAdapter(),
        {
            "/api/v2/mix/market/tickers": {
                "data": [
                    {
                        "symbol": "AAPLUSDT",
                        "usdtVolume": "900",
                        "holdingAmount": "50",
                        "lastPr": "200",
                    }
                ]
            }
        },
    )
    row = _one(rows, "AAPLUSDT")
    assert row.oi_usd == 10_000


def test_a_missing_factor_makes_open_interest_unknown_not_zero() -> None:
    """A derived figure with an absent input is not a small number.

    Reporting zero here would say the venue has no open positions, which is a
    claim about the market rather than about our data.
    """
    rows = _rows(
        MexcAdapter(),
        {
            "/api/v1/contract/ticker": {
                "data": [
                    {"symbol": "AAPLSTOCK_USDT", "holdVol": "5323024", "fairPrice": "3"}
                ]
            },
            # The detail call came back without this contract, so no contractSize.
            "/api/v1/contract/detail": {"data": []},
        },
    )
    assert _one(rows, "AAPLSTOCK_USDT").oi_usd is None


def test_quote_suffix_is_stripped_longest_first() -> None:
    """``USDT`` before ``USD``: the short match would leave ``AAPLT``."""
    assert cex_perps._strip_quote("AAPLUSDT", ("USD", "USDT")) == "AAPL"
    # A symbol that is only its quote asset is left alone rather than emptied.
    assert cex_perps._strip_quote("USDT", ("USDT",)) == "USDT"


# --- scope, resolution and rollup ----------------------------------------


class _StubAdapter(PerpVenueAdapter):
    """Returns fixed rows so the collector's own logic can be tested alone."""

    source_id = "okx"
    exchange = "OKX"

    def __init__(self, rows: list[PerpRow]) -> None:
        self._rows = rows

    @property
    def base_url(self) -> str:
        return "https://example.invalid"

    def collect_rows(self, fetcher: Any) -> tuple[list[PerpRow], list[FetchResult]]:
        return list(self._rows), [
            FetchResult(source_id=self.source_id, endpoint="/t", status=FetchStatus.OK)
        ]


def _row(
    symbol: str, base: str, vol: int | None = 100, oi: int | None = 200
) -> PerpRow:
    return PerpRow(
        symbol=symbol,
        base=base,
        vol_24h=None if vol is None else Decimal(vol),
        oi_usd=None if oi is None else Decimal(oi),
        mark_price=Decimal(10),
        funding_rate=None,
    )


@pytest.fixture()
def session() -> Iterator[Session]:
    """An in-memory warehouse holding one equity and one commodity underlying."""
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
                    underlying_id="XAU", name="Gold", asset_class=AssetClass.COMMODITY
                ),
                DimUnderlying(
                    underlying_id="SPX", name="S&P 500", asset_class=AssetClass.INDEX
                ),
            ]
        )
        db.commit()
        yield db


def _collect(session: Session, rows: list[PerpRow]) -> list[FetchResult]:
    collector = CexPerpCollector(adapter=_StubAdapter(rows))
    results = collector.collect(session, TS)
    session.flush()
    return results


def test_crypto_native_contracts_are_never_stored(session: Session) -> None:
    """The out-of-scope majority must not reach the dimension or the review queue."""
    _collect(
        session,
        [
            _row("AAPL-USDT-SWAP", "AAPL"),
            _row("BTC-USDT-SWAP", "BTC"),
            _row("PEPE-USDT-SWAP", "PEPE"),
        ],
    )
    stored = list(session.execute(select(DimPerpContract)).scalars())
    assert [c.symbol for c in stored] == ["AAPL-USDT-SWAP"]


def test_the_contract_resolves_on_its_base_not_its_full_name(
    session: Session,
) -> None:
    """``AAPL-USDT-SWAP`` matches nothing; the ``AAPL`` behind it matches exactly."""
    _collect(session, [_row("AAPL-USDT-SWAP", "AAPL")])
    contract = session.execute(select(DimPerpContract)).scalars().one()
    assert contract.underlying_id == "AAPL"


def test_a_ticker_shared_with_a_crypto_token_does_not_resolve(
    session: Session,
) -> None:
    """SPX is both the index and SPX6900, so the bare spelling proves nothing.

    The memecoin marks near $0.31 while the index is four orders of magnitude away,
    so mapping it would report memecoin turnover as demand for the S&P 500.
    """
    _collect(session, [_row("SPXUSDT", "SPX")])
    assert session.execute(select(DimPerpContract)).scalars().all() == []


def test_a_wrapper_suffix_rescues_the_ambiguous_ticker(session: Session) -> None:
    """``DIASTOCK`` carries issuer naming, which the bare ticker lacks."""
    from app.services.normalize import underlying_map

    assert underlying_map.resolve("SPX", {"SPX"}).underlying_id is None
    assert underlying_map.resolve("SPXSTOCK", {"SPX"}).underlying_id == "SPX"


def test_the_stock_segment_is_written_apart_from_the_total(session: Session) -> None:
    """Stock is a subset of all, so the two rows must never be added together."""
    _collect(session, [_row("AAPL-USDT-SWAP", "AAPL"), _row("XAU-USDT-SWAP", "XAU")])
    by_segment = {
        r.segment: r for r in session.execute(select(FactPerpVenueSnapshot)).scalars()
    }
    assert by_segment[SEGMENT_ALL].symbol_count == 2
    assert by_segment[SEGMENT_ALL].vol_24h == 200
    # Gold is in scope but is not equity, so it belongs to the total only.
    assert by_segment[SEGMENT_STOCK].symbol_count == 1
    assert by_segment[SEGMENT_STOCK].vol_24h == 100


def test_no_stock_row_when_the_venue_lists_no_equity(session: Session) -> None:
    """An absent row says nothing; a row of zeros would claim no equity traded."""
    _collect(session, [_row("XAU-USDT-SWAP", "XAU")])
    segments = {
        r.segment for r in session.execute(select(FactPerpVenueSnapshot)).scalars()
    }
    assert segments == {SEGMENT_ALL}


def test_partial_open_interest_coverage_is_stated(session: Session) -> None:
    """The two totals on one row can cover different contracts, so say how many."""
    _collect(
        session,
        [_row("AAPL-USDT-SWAP", "AAPL"), _row("XAU-USDT-SWAP", "XAU", oi=None)],
    )
    row = next(
        r
        for r in session.execute(select(FactPerpVenueSnapshot)).scalars()
        if r.segment == SEGMENT_ALL
    )
    assert (row.symbol_count, row.oi_symbol_count) == (2, 1)
    assert row.open_interest_usd == 200


def test_a_venue_with_nothing_observed_reports_null_not_zero(
    session: Session,
) -> None:
    """Every figure failing to parse is not a venue where nobody traded."""
    _collect(session, [_row("AAPL-USDT-SWAP", "AAPL", vol=None, oi=None)])
    row = next(
        r
        for r in session.execute(select(FactPerpVenueSnapshot)).scalars()
        if r.segment == SEGMENT_ALL
    )
    assert row.vol_24h is None
    assert row.open_interest_usd is None
    assert row.oi_symbol_count == 0


def test_no_venue_row_at_all_when_nothing_resolved(session: Session) -> None:
    """A venue listing no RWA perps produces no row, rather than a row of zeros."""
    results = _collect(session, [_row("BTC-USDT-SWAP", "BTC")])
    assert session.execute(select(FactPerpVenueSnapshot)).scalars().all() == []
    assert session.execute(select(FactPerpContractSnapshot)).scalars().all() == []
    # The fetch still happened and is still logged; silence is not the same as
    # not having looked.
    assert [r.status for r in results] == [FetchStatus.OK]


def test_every_venue_has_a_registered_source() -> None:
    """A collector cannot log a fetch against a source nobody registered."""
    from app.services.ingest import registry

    registered = {s.source_id for s in registry.SOURCES}
    assert {c.source_id for c in cex_perps.build_collectors()} <= registered
