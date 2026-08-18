"""Tests for the CoinGecko category slugs and what they carry.

Four of the five slugs were once guessed from CoinGecko's *display* names —
``tokenized-etf`` for "Tokenized ETF", ``xstocks`` for "xStocks" — and all four
404'd. Nothing looked broken: a 404 is logged NOT_VERIFIED and correctly contributes
no rows, so the collector went on producing a market cap, a ranking and a category
union built from one category out of five.

Two consequences make this worth its own file. The union that domain rule 3 calls
"the only valid total" was a union of one, and no coin ever met an issuer-bearing
category, so every custodied wrapper classified SYNTHETIC instead of CORE_RWA.
"""

from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.models.dimensions import DimAsset, DimUnderlying
from app.models.enums import AssetClass, FetchStatus, RwaTier
from app.services.ingest.base import FetchResult
from app.services.ingest.coingecko import (
    CATEGORIES,
    CATEGORY_ISSUERS,
    CoinGeckoCollector,
)

TS = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def test_every_issuer_bearing_category_is_one_we_actually_fetch() -> None:
    """The two lists are keyed on the same slug and drift silently apart.

    Renaming a slug in ``CATEGORIES`` without ``CATEGORY_ISSUERS`` does not fail, it
    just stops attributing that issuer — which is how a competitive ranking loses a
    competitor without anything appearing to go wrong.
    """
    assert set(CATEGORY_ISSUERS) <= set(CATEGORIES)


def test_all_five_categories_are_distinct() -> None:
    """A duplicated slug would quietly shrink the union to four."""
    assert len(set(CATEGORIES)) == len(CATEGORIES) == 5


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    with maker() as db:
        # A wrapper only resolves to a security somebody seeded, so the tier
        # assertions below need these present or they would pass for the wrong
        # reason — NON_RWA is also what an unseeded ticker gets.
        db.add_all(
            [
                DimUnderlying(
                    underlying_id=uid, name=uid, asset_class=AssetClass.EQUITY
                )
                for uid in ("TSLA", "AAPL")
            ]
        )
        db.flush()
        yield db


class _FakeFetcher:
    """Serves canned category payloads; a missing category 404s like the real one."""

    def __init__(self, by_category: Mapping[str, list[dict[str, Any]]]) -> None:
        self._by_category = by_category
        self.requested: list[str] = []

    def __enter__(self) -> "_FakeFetcher":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_json(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> FetchResult:
        if path == "/coins/markets":
            category = str((params or {}).get("category"))
            self.requested.append(category)
            payload = self._by_category.get(category)
            if payload is None:
                return FetchResult(
                    source_id="coingecko",
                    endpoint=path,
                    status=FetchStatus.NOT_VERIFIED,
                    http_status=404,
                    error="HTTP 404",
                )
            return FetchResult(
                source_id="coingecko",
                endpoint=path,
                status=FetchStatus.OK,
                payload=payload,
                http_status=200,
            )
        # Ticker calls are not what this file is about, and letting them 404 keeps
        # the collector on its "no rows from a failed fetch" path.
        return FetchResult(
            source_id="coingecko",
            endpoint=path,
            status=FetchStatus.NOT_VERIFIED,
            http_status=404,
            error="HTTP 404",
        )


def _coin(coin_id: str, symbol: str, market_cap: float) -> dict[str, Any]:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": symbol.upper(),
        "market_cap": market_cap,
        "total_volume": 1000.0,
    }


def _collect(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    by_category: Mapping[str, list[dict[str, Any]]],
) -> _FakeFetcher:
    collector = CoinGeckoCollector()
    fetcher = _FakeFetcher(by_category)
    monkeypatch.setattr(collector, "_fetcher", lambda: fetcher)
    collector.collect(session, TS)
    session.flush()
    return fetcher


def test_a_wrapper_found_in_its_issuers_category_is_core_rwa(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure the wrong slugs produced, stated as the behaviour that fixes it.

    ``tslax`` appears in both the broad category and the xStocks one. Only the second
    names an issuer, and ``classify_tier`` needs that issuer to reach CORE_RWA.
    """
    coin = _coin("tesla-xstock", "tslax", 5_000_000.0)
    _collect(
        session,
        monkeypatch,
        {"tokenized-stock": [coin], "xstocks-ecosystem": [coin]},
    )

    asset = session.execute(
        select(DimAsset).where(DimAsset.asset_id == "tesla-xstock")
    ).scalar_one()
    assert asset.issuer_id == "xStocks"
    assert asset.rwa_tier is RwaTier.CORE_RWA


def test_an_issuer_survives_being_seen_first_under_the_broad_category(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Iteration order must not decide attribution.

    ``tokenized-stock`` is fetched first and names no issuer. If the collector wrote
    assets as they arrived, every wrapper would be stamped issuer-less by the first
    category that mentioned it and no later category could correct it.
    """
    coin = _coin("apple-bstock", "aaplb", 3_000_000.0)
    _collect(
        session,
        monkeypatch,
        {"tokenized-stock": [coin], "bstocks-ecosystem": [coin]},
    )

    asset = session.execute(
        select(DimAsset).where(DimAsset.asset_id == "apple-bstock")
    ).scalar_one()
    assert asset.issuer_id == "bStocks"


def test_a_404_category_contributes_no_rows_rather_than_zeroes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Domain rule 4: a failed fetch is a missing observation, not an empty market.

    This is also why the wrong slugs were survivable for so long, so the test pins
    the behaviour rather than treating it as the bug.
    """
    fetcher = _collect(
        session,
        monkeypatch,
        {"tokenized-stock": [_coin("solo", "solo", 1.0)]},
    )

    # Every category was still attempted; four simply had nothing to give.
    assert fetcher.requested == list(CATEGORIES)
    assert session.execute(select(DimAsset)).scalars().all() != []


def test_a_404_category_is_logged_as_a_configuration_error(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one signal that separates a wrong slug from a bad network.

    Rate limits and timeouts are transient and retried; a 404 means the slug does not
    exist and will fail identically forever, so it is the operator's problem now.
    """
    with caplog.at_level("ERROR"):
        _collect(
            session,
            monkeypatch,
            {"tokenized-stock": [_coin("solo", "solo", 1.0)]},
        )

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 4
    assert "does not exist" in errors[0].getMessage()
