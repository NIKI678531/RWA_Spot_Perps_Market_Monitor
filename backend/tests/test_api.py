"""Tests for the HTTP layer.

The endpoints are thin over ``report.dataset``, so these do not re-test the maths.
What they check is the part the API can get wrong on its own: that a missing
observation reaches the client as ``null`` rather than ``0``, that overlapping
category rows arrive flagged non-additive, that the timeseries endpoint refuses a
series it cannot attach a metric scope to, and that a chart can never be handed two
scopes on one axis.
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
from app.models.alerts import Alert, AlertEvidence
from app.models.dimensions import (
    DimAsset,
    DimIssuer,
    DimPerpContract,
    DimTheme,
    DimUnderlying,
    DimVenue,
)
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AssetClass,
    DetectorFamily,
    EntityType,
    RwaTier,
    VenueType,
)
from app.models.facts import (
    FactAssetSnapshot,
    FactCategorySnapshot,
    FactPairSnapshot,
    FactPerpContractSnapshot,
)
from app.core.metrics import MetricScope

NOW = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session() -> Iterator[Session]:
    # TestClient serves requests on a worker thread, so the connection has to be
    # shareable across threads and has to be the *same* connection — a fresh one
    # would open an empty in-memory database.
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
    """A client bound to the seeded in-memory database.

    ``get_dataset`` is left alone deliberately: overriding it would test the routes
    against a hand-built dataset rather than against the same load path the daily
    workbook uses, which is the whole reason the routes go through it.
    """
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _url(path: str) -> str:
    return f"{API_PREFIX}{path}"


def _seed(session: Session) -> None:
    """One underlying, two wrappers, two venues, one perp, one alert."""
    session.add_all(
        [
            DimTheme(theme_id="broad_index", name_zh="宽基指数", name_en="Broad Index"),
            DimUnderlying(
                underlying_id="SPY",
                name="SPDR S&P 500 ETF",
                asset_class=AssetClass.ETF,
                theme_id="broad_index",
            ),
            DimIssuer(issuer_id="xstocks", name="xStocks", official_product_count=640),
            DimIssuer(issuer_id="bstocks", name="bStocks", official_product_count=60),
            DimVenue(venue_id="binance", name="Binance", venue_type=VenueType.CEX),
            DimVenue(
                venue_id="native_bsc",
                name="Native (BSC)",
                venue_type=VenueType.DEX,
                chain="bsc",
            ),
            DimAsset(
                asset_id="spyx",
                symbol="SPYx",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="SPY",
                issuer_id="xstocks",
            ),
            DimAsset(
                asset_id="spyb",
                symbol="SPYB",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="SPY",
                issuer_id="bstocks",
            ),
            # Benchmark-only. Must never appear in a ranking or a total.
            DimAsset(asset_id="btc", symbol="BTC", rwa_tier=RwaTier.NON_RWA),
            DimPerpContract(
                contract_id="HL:rwa:SPY",
                exchange="Hyperliquid",
                perp_dex="rwa",
                symbol="SPY",
                source_underlying_type="EQUITY",
                underlying_id="SPY",
            ),
            DimPerpContract(
                contract_id="BN:SPYUSDT",
                exchange="Binance",
                symbol="SPYUSDT",
                source_underlying_type="EQUITY",
                underlying_id="SPY",
            ),
        ]
    )
    session.flush()

    session.add_all(
        [
            FactAssetSnapshot(
                asset_id="spyx",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                market_cap=Decimal("5000000"),
                vol_24h=Decimal("362000"),
            ),
            FactAssetSnapshot(
                asset_id="spyb",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                market_cap=None,  # not verified — not zero
                vol_24h=Decimal("120000"),
            ),
            FactAssetSnapshot(
                asset_id="btc",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                market_cap=Decimal("2000000000000"),
                vol_24h=Decimal("40000000000"),
            ),
            FactPairSnapshot(
                asset_id="spyx",
                venue_id="binance",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("300000"),
                adjusted_vol_24h=Decimal("300000"),
            ),
            # The Native (BSC) case: ~$29.3mn raw against ~$216 adjusted, because
            # everything but the small pair carries a quality flag.
            FactPairSnapshot(
                asset_id="spyb",
                venue_id="native_bsc",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("29299784"),
                adjusted_vol_24h=None,
                is_quality_anomaly=True,
            ),
            FactPairSnapshot(
                asset_id="spyx",
                venue_id="native_bsc",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("216"),
                adjusted_vol_24h=Decimal("216"),
            ),
            FactPerpContractSnapshot(
                contract_id="HL:rwa:SPY",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                vol_24h=Decimal("700000"),
                oi_units=Decimal("100"),
                oi_usd=Decimal("77316"),
                mark_price=Decimal("773.16"),
            ),
            FactPerpContractSnapshot(
                contract_id="BN:SPYUSDT",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                vol_24h=Decimal("2500000"),
                oi_usd=Decimal("450000"),
            ),
            FactCategorySnapshot(
                category_id="tokenized-stock",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                asset_count=150,
                market_cap=Decimal("400000000"),
                is_additive=False,
            ),
            FactCategorySnapshot(
                category_id="xstocks",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                asset_count=113,
                market_cap=Decimal("300000000"),
                is_additive=False,
            ),
            FactCategorySnapshot(
                category_id="rwa_union",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                asset_count=250,
                market_cap=Decimal("500000000"),
                is_additive=True,
            ),
        ]
    )

    alert = Alert(
        dedup_key="X1:spyb:CLOSED_WEEKEND",
        detector="X1",
        family=DetectorFamily.CROSS_SECTIONAL,
        entity_type=EntityType.UNDERLYING,
        entity_id="SPY",
        metric_scope=MetricScope.SPOT_VOLUME,
        market_session=MarketSession.CLOSED_WEEKEND,
        severity=AlertSeverity.HIGH,
        score=Decimal("0.82"),
        status=AlertStatus.CONFIRMED,
        headline_zh="SPY 换手率显著高于同类",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        occurrence_count=2,
    )
    session.add(alert)
    session.add(
        AlertEvidence(
            alert=alert,
            snapshot_ts=NOW,
            rule_name="cross_sectional_turnover",
            observed_value=Decimal("362000"),
            baseline_median=Decimal("40000"),
            baseline_mad=Decimal("8000"),
            robust_z=Decimal("27.1"),
            sample_size=19,
            market_session=MarketSession.CLOSED_WEEKEND,
            peer_count=12,
            extra_json='{"turnover": 0.0724}',
        )
    )
    session.commit()


# --- the contract every endpoint shares ------------------------------------


def test_health_reports_the_newest_snapshot(client: TestClient) -> None:
    body = client.get(_url("/health")).json()

    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["as_of"].startswith("2026-08-17T14:00")


def test_docs_are_served_under_the_api_prefix(client: TestClient) -> None:
    assert client.get(f"{API_PREFIX}/openapi.json").status_code == 200


# --- headline ---------------------------------------------------------------


def test_executive_kpis_cover_all_five_scopes_separately(client: TestClient) -> None:
    """Rule 1: the five scopes are five numbers, never one."""
    body = client.get(_url("/kpi/executive")).json()
    keys = [m["key"] for m in body["metrics"]]

    assert keys == [
        "spot_market_cap",
        "spot_volume",
        "dex_liquidity",
        "perp_volume",
        "perp_oi",
    ]
    scopes = {m["current"]["scope"] for m in body["metrics"]}
    assert len(scopes) == len(keys)


def test_a_kpi_with_no_history_reports_no_previous_period(client: TestClient) -> None:
    """A first-day deployment must not render a 0% change against nothing."""
    body = client.get(_url("/kpi/executive")).json()

    assert body["previous_as_of"] is None
    assert all(m["change_pct"] is None for m in body["metrics"])


def test_out_of_scope_assets_stay_out_of_the_headline(client: TestClient) -> None:
    """BTC is 2 trillion of market cap and must not touch the RWA total."""
    body = client.get(_url("/kpi/executive")).json()
    market_cap = next(m for m in body["metrics"] if m["key"] == "spot_market_cap")

    assert Decimal(market_cap["current"]["value"]) == Decimal("5000000")
    assert market_cap["entity_count"] == 2


# --- scale ------------------------------------------------------------------


def test_only_the_union_row_is_additive(client: TestClient) -> None:
    """Rule 2: the five source categories overlap; only the union may be totalled."""
    body = client.get(_url("/scale/categories")).json()
    additive = [r["category_id"] for r in body["rows"] if r["is_additive"]]

    assert additive == ["rwa_union"]
    assert body["overlap_note"]
    assert len(body["rows"]) == 3


# --- spot -------------------------------------------------------------------


def test_raw_and_adjusted_volume_are_both_reported(client: TestClient) -> None:
    """Rule 4: never show one without the other."""
    body = client.get(_url("/spot/venues")).json()
    native = next(r for r in body["rows"] if r["venue_id"] == "native_bsc")

    assert Decimal(native["raw_vol_24h"]["value"]) == Decimal("29300000")
    assert Decimal(native["adjusted_vol_24h"]["value"]) == Decimal("216")
    assert native["materially_divergent"] is True


def test_a_venue_filter_narrows_the_pair_list(client: TestClient) -> None:
    body = client.get(_url("/spot/pairs"), params={"venue_id": "binance"}).json()

    assert [r["asset_id"] for r in body["rows"]] == ["spyx"]


def test_flagged_only_returns_the_quality_flagged_pairs(client: TestClient) -> None:
    body = client.get(_url("/spot/pairs"), params={"flagged_only": True}).json()

    assert [r["venue_id"] for r in body["rows"]] == ["native_bsc"]


# --- perpetuals -------------------------------------------------------------


def test_perp_volume_and_open_interest_stay_on_separate_scopes(
    client: TestClient,
) -> None:
    """Rule 1 again: a flow and a stock cannot share an axis."""
    body = client.get(_url("/perps/contracts")).json()
    row = next(r for r in body["rows"] if r["contract_id"] == "HL:rwa:SPY")

    assert row["vol_24h"]["scope"] == MetricScope.PERP_VOLUME.value
    assert row["open_interest_usd"]["scope"] == MetricScope.PERP_OI.value
    assert row["vol_24h"]["dimension"] != row["open_interest_usd"]["dimension"]
    assert set(body["meta"]["scopes"]) == {
        MetricScope.PERP_VOLUME.value,
        MetricScope.PERP_OI.value,
    }


def test_the_exchange_label_is_preserved_verbatim(client: TestClient) -> None:
    """Rule 8: Binance calls some ETFs EQUITY. We store it, we do not fix it."""
    body = client.get(_url("/perps/contracts")).json()
    row = next(r for r in body["rows"] if r["contract_id"] == "BN:SPYUSDT")

    assert row["source_underlying_type"] == "EQUITY"


def test_hip3_perp_dexs_are_identified(client: TestClient) -> None:
    body = client.get(_url("/perps/dexs")).json()
    row = next(r for r in body["rows"] if r["perp_dex"] == "rwa")

    assert row["is_hip3"] is True


# --- demand -----------------------------------------------------------------


def test_the_underlying_view_joins_wrappers_venues_and_perps(
    client: TestClient,
) -> None:
    """The question the whole schema exists for: is anyone buying the S&P 500?"""
    body = client.get(_url("/underlying/SPY")).json()

    assert {w["asset_id"] for w in body["tokenized_wrappers"]} == {"spyx", "spyb"}
    assert {v["venue_id"] for v in body["venue_breakdown"]} == {"binance", "native_bsc"}
    assert len(body["perp_exposure"]) == 2
    assert body["scope_note"]


def test_an_unknown_underlying_is_a_404(client: TestClient) -> None:
    assert client.get(_url("/underlying/NOSUCH")).status_code == 404


def test_alerts_carry_the_evidence_that_justifies_them(client: TestClient) -> None:
    """Rule 7: an alert you cannot defend to management is noise."""
    listed = client.get(_url("/alerts")).json()
    alert_id = listed["rows"][0]["id"]
    detail = client.get(_url(f"/alerts/{alert_id}")).json()
    evidence = detail["evidence"][0]

    assert evidence["rule_name"] == "cross_sectional_turnover"
    assert Decimal(evidence["observed_value"]) == Decimal("362000")
    assert Decimal(evidence["baseline_median"]) == Decimal("40000")
    assert evidence["sample_size"] == 19
    assert evidence["market_session"] == MarketSession.CLOSED_WEEKEND.value
    assert evidence["extra"] == {"turnover": 0.0724}


def test_alerts_can_be_filtered_by_severity(client: TestClient) -> None:
    assert client.get(_url("/alerts"), params={"severity": "low"}).json()["rows"] == []
    assert client.get(_url("/alerts"), params={"severity": "high"}).json()["rows"]


# --- timeseries -------------------------------------------------------------


def test_a_series_carries_the_scope_it_belongs_to(client: TestClient) -> None:
    # ``until`` is pinned rather than left to default to "now": the window is real
    # wall-clock time, so a suite run before 14:00 UTC would otherwise end before the
    # seeded snapshot and return an empty chart.
    body = client.get(
        _url("/timeseries"),
        params={
            "entity_type": "asset",
            "entity_id": "spyx",
            "metric": "vol_24h",
            "until": NOW.isoformat(),
        },
    ).json()

    assert body["scope"] == MetricScope.SPOT_VOLUME.value
    assert len(body["points"]) == 1
    assert Decimal(body["points"][0]["value"]) == Decimal("362000")
    assert body["points"][0]["market_session"] == MarketSession.CLOSED_WEEKEND.value


def test_an_unknown_series_is_refused_rather_than_guessed(client: TestClient) -> None:
    """A series with no scope could be plotted against anything. 400, not 200."""
    response = client.get(
        _url("/timeseries"),
        params={"entity_type": "asset", "entity_id": "spyx", "metric": "nonsense"},
    )

    assert response.status_code == 400
    assert "vol_24h" in response.json()["detail"]


# --- operations -------------------------------------------------------------


def test_data_quality_names_the_divergent_venues(client: TestClient) -> None:
    body = client.get(_url("/data-quality")).json()

    assert "Native (BSC)" in body["divergent_venues"]
    assert body["flagged_pairs"] == 1


def test_a_report_that_was_never_generated_is_a_404(client: TestClient) -> None:
    assert client.get(_url("/reports/2026-08-17/excel")).status_code == 404


def test_generate_produces_both_formats_and_they_are_downloadable(
    client: TestClient,
) -> None:
    generated = client.post(_url("/reports/generate")).json()

    assert {r["report_format"] for r in generated["rows"]} == {"xlsx", "docx"}
    # No PVC in production: the bytes live in the database, not on a volume.
    assert all(r["storage"] == "database" for r in generated["rows"])

    day = generated["rows"][0]["report_date"][:10]
    excel = client.get(_url(f"/reports/{day}/excel"))
    assert excel.status_code == 200
    assert excel.content[:2] == b"PK"


def test_a_report_stamped_mid_day_is_still_found_by_its_date(
    client: TestClient,
) -> None:
    """The URL names a calendar day; the artifact carries an instant."""
    as_of = (NOW + timedelta(hours=8)).isoformat()
    client.post(_url("/reports/generate"), json={"as_of": as_of})

    assert client.get(_url("/reports/2026-08-17/word")).status_code == 200
