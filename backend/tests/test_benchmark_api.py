"""Tests for the benchmark endpoint and the two coverage blocks on data-quality.

These three figures answer questions the rest of the API cannot: whether a token's
price is *right*, how much of the published market we index at all, and how much of
what we index can be checked against a real share. Each has the same failure mode —
looking complete when it is empty — so most of what is asserted here is that an
absent reference stays visibly absent instead of arriving as a zero, a 1.0 or a row
of nulls.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.sessions import MarketSession
from app.db.base import Base
from app.db.session import get_session
from app.main import API_PREFIX, create_app
from app.models.dimensions import DimAsset, DimIssuer, DimPerpContract, DimUnderlying
from app.models.enums import AssetClass, RwaTier
from app.models.facts import FactAssetSnapshot, FactUnderlyingReference

NOW = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
#: Friday's close, read the following Tuesday. The normal state, not a fault.
FRIDAY_CLOSE = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
        yield session


@pytest.fixture()
def client(session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _url(path: str) -> str:
    return f"{API_PREFIX}{path}"


def _seed(session: Session) -> None:
    """Three underlyings: one quoted, one tracked but unquoted, one crypto-native."""
    session.add_all(
        [
            DimUnderlying(
                underlying_id="AAPL", name="Apple", asset_class=AssetClass.EQUITY
            ),
            DimUnderlying(
                underlying_id="SPY",
                name="SPDR S&P 500 ETF",
                asset_class=AssetClass.ETF,
            ),
            DimUnderlying(
                underlying_id="TSLA", name="Tesla", asset_class=AssetClass.EQUITY
            ),
            DimIssuer(issuer_id="xstocks", name="xStocks", official_product_count=640),
            # Publishes nothing. Must not be counted as a denominator of zero.
            DimIssuer(issuer_id="ondo", name="Ondo"),
            DimAsset(
                asset_id="aaplx",
                symbol="AAPLx",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="AAPL",
                issuer_id="xstocks",
            ),
            DimAsset(
                asset_id="spyx",
                symbol="SPYx",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="SPY",
                issuer_id="xstocks",
            ),
            DimAsset(
                asset_id="tslax",
                symbol="TSLAx",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="TSLA",
                issuer_id="xstocks",
            ),
            # Benchmark-only. Never in a ranking, never in a coverage numerator.
            DimAsset(asset_id="btc", symbol="BTC", rwa_tier=RwaTier.NON_RWA),
            DimPerpContract(
                contract_id="HL:rwa:AAPL",
                exchange="Hyperliquid",
                perp_dex="rwa",
                symbol="AAPL",
                underlying_id="AAPL",
            ),
        ]
    )
    session.flush()

    session.add_all(
        [
            FactAssetSnapshot(
                asset_id="aaplx",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                price_usd=Decimal("236.15"),
                change_24h=Decimal("0.012"),
            ),
            FactAssetSnapshot(
                asset_id="spyx",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                price_usd=Decimal("640.00"),
            ),
            # Tracked, priced on-chain, but no TradFi quote exists for it below.
            FactAssetSnapshot(
                asset_id="tslax",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                price_usd=Decimal("410.00"),
            ),
            FactUnderlyingReference(
                underlying_id="AAPL",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                price=Decimal("230.00"),
                price_ts=FRIDAY_CLOSE,
                prev_close=Decimal("228.00"),
                change_24h=Decimal("0.0088"),
                feed="iex",
            ),
            FactUnderlyingReference(
                underlying_id="SPY",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                price=Decimal("639.00"),
                price_ts=NOW - timedelta(minutes=30),
                feed="iex",
            ),
        ]
    )
    session.commit()


# --- benchmark ---------------------------------------------------------------


def test_the_token_is_reported_beside_the_share_it_wraps(client: TestClient) -> None:
    body = client.get(_url("/benchmark")).json()
    row = next(r for r in body["rows"] if r["asset_id"] == "aaplx")

    assert Decimal(row["token_price"]) == Decimal("236.15")
    assert Decimal(row["reference_price"]) == Decimal("230.00")
    assert row["feed"] == "iex"
    # 236.15 / 230 - 1. Rounded here only because the wire carries a float.
    assert row["basis"] == pytest.approx(0.026739, abs=1e-6)


def test_the_age_of_the_reference_is_reported_with_it(client: TestClient) -> None:
    """A basis quoted without its staleness reads every weekend as a dislocation."""
    body = client.get(_url("/benchmark")).json()
    rows = {r["asset_id"]: r for r in body["rows"]}

    # Friday 20:00 to Tuesday 14:00 is 90 hours.
    assert rows["aaplx"]["reference_age_minutes"] == 90 * 60
    assert rows["spyx"]["reference_age_minutes"] == 30
    assert rows["aaplx"]["reference_price_ts"].startswith("2026-08-14T20:00")


def test_an_underlying_with_no_quote_produces_no_row(client: TestClient) -> None:
    """A row of nulls would claim we compared TSLA against something and drew."""
    body = client.get(_url("/benchmark")).json()

    assert {r["asset_id"] for r in body["rows"]} == {"aaplx", "spyx"}


def test_the_widest_gap_sorts_first(client: TestClient) -> None:
    """A benchmark table sorted by name buries the one row worth reading."""
    body = client.get(_url("/benchmark")).json()

    assert [r["asset_id"] for r in body["rows"]] == ["aaplx", "spyx"]


def test_the_endpoint_declares_no_metric_scope(client: TestClient) -> None:
    """A price is none of the five families, and nothing here is ever summed."""
    body = client.get(_url("/benchmark")).json()

    assert body["meta"]["scopes"] == []
    assert "1:1" in body["meta"]["note"]


def test_a_configured_source_that_ran_needs_no_excuse(client: TestClient) -> None:
    assert client.get(_url("/benchmark")).json()["unavailable_reason"] is None


def test_no_reference_source_is_explained_rather_than_shown_empty(
    session: Session, client: TestClient
) -> None:
    """An empty table reads as "no token trades near its share price". Say why."""
    session.query(FactUnderlyingReference).delete()
    session.commit()

    body = client.get(_url("/benchmark")).json()
    assert body["rows"] == []
    assert "ALPACA_API_KEY_ID" in body["unavailable_reason"]


# --- data-quality coverage ---------------------------------------------------


def test_catalogue_coverage_divides_indexed_by_published(client: TestClient) -> None:
    """Three in-scope wrappers against the 640 one issuer says it offers."""
    catalogue = client.get(_url("/data-quality")).json()["catalogue"]

    assert catalogue["indexed_assets"] == 3  # BTC is out of scope
    assert catalogue["official_products"] == 640
    assert catalogue["ratio"] == pytest.approx(3 / 640)


def test_an_issuer_that_publishes_nothing_is_not_a_denominator_of_zero(
    client: TestClient,
) -> None:
    """Counting Ondo as 0 would inflate the ratio exactly where coverage is worst."""
    catalogue = client.get(_url("/data-quality")).json()["catalogue"]

    assert catalogue["issuers_with_count"] == 1
    assert catalogue["issuer_count"] == 2


def test_an_unknown_denominator_leaves_the_ratio_null_not_one(
    session: Session, client: TestClient
) -> None:
    for issuer in session.query(DimIssuer):
        issuer.official_product_count = None
    session.commit()

    catalogue = client.get(_url("/data-quality")).json()["catalogue"]
    assert catalogue["official_products"] is None
    assert catalogue["ratio"] is None


def test_reference_coverage_counts_tracked_against_priced(client: TestClient) -> None:
    reference = client.get(_url("/data-quality")).json()["reference"]

    assert reference["tracked_underlyings"] == 3
    assert reference["priced_underlyings"] == 2
    assert reference["feed"] == "iex"


def test_reference_freshness_is_the_oldest_row_not_the_average(
    client: TestClient,
) -> None:
    """A mean would hide the 90-hour-old quote behind the 30-minute-old one."""
    reference = client.get(_url("/data-quality")).json()["reference"]

    assert reference["max_age_minutes"] == 90 * 60
    assert reference["unavailable_reason"] is None


def test_missing_reference_coverage_says_which_credentials_are_absent(
    session: Session, client: TestClient
) -> None:
    session.query(FactUnderlyingReference).delete()
    session.commit()

    reference = client.get(_url("/data-quality")).json()["reference"]
    assert reference["priced_underlyings"] == 0
    assert reference["max_age_minutes"] is None
    assert "ALPACA_API_KEY_ID" in reference["unavailable_reason"]
