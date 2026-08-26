"""Tests for the underlying catalogue and the Underlying 360 entity page.

Underlying 360 is the destination every other surface drills into, so most of what
matters here is that it says the same thing they do:

* one security, however many wrappers, issuers and venues carry it (U360-001);
* four scopes side by side, never totalled, and a figure nobody observed sorting
  below every figure someone did rather than to the bottom as a zero (U360-002);
* where *we* stand on it — shelf position, live candidates, open gaps, and who
  wrapped it before we did (U360-003);
* the same publication predicate the queue uses, so a badge count and the list it
  opens cannot disagree.

The pre-R1 singular path is exercised too. It is a deprecated alias, and an alias
that returns something slightly different is worse than one that 404s.
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
from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.base import Base
from app.db.session import get_session
from app.main import API_PREFIX, create_app
from app.models.alerts import Alert
from app.models.dimensions import (
    DimAsset,
    DimIssuer,
    DimOwnProduct,
    DimPerpContract,
    DimUnderlying,
    DimVenue,
)
from app.models.enums import (
    AlertSeverity,
    AlertState,
    AlertStatus,
    AssetClass,
    CandidateStage,
    CoverageState,
    DataGapKind,
    DataGapStatus,
    DetectorFamily,
    EntityType,
    RwaTier,
    VenueType,
)
from app.models.facts import (
    FactAssetSnapshot,
    FactLaunchWindowSnapshot,
    FactPairSnapshot,
    FactPerpContractSnapshot,
)
from app.models.workflow import DataGap, IssuanceCandidate, ProductCoverage

NOW = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)

#: When xStocks' SPY wrapper was measured as listed. Backed's has no launch window, so
#: it falls back to when it entered the catalogue — which is the case that decides
#: whether the page distinguishes a measured date from a first observation.
XSTOCKS_LISTED = NOW - timedelta(days=40)


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
    """Three underlyings, deliberately unalike.

    SPY is the full case: two wrappers from two issuers on two venues, a perpetual,
    a published alert, a live product, a screening candidate and an open gap. GLD has
    one wrapper and no perp. TLT has nothing at all — no wrapper, no snapshot, no
    coverage row — which is the case that decides whether "not observed" survives the
    trip to the browser or arrives as a zero.
    """
    session.add_all(
        [
            DimUnderlying(
                underlying_id="SPY",
                name="SPDR S&P 500 ETF",
                asset_class=AssetClass.ETF,
                region="US",
                theme_id=None,
            ),
            DimUnderlying(
                underlying_id="GLD",
                name="SPDR Gold Shares",
                asset_class=AssetClass.COMMODITY,
                region="US",
            ),
            DimUnderlying(
                underlying_id="TLT",
                name="iShares 20+ Year Treasury Bond ETF",
                asset_class=AssetClass.ETF,
                region="US",
            ),
            DimIssuer(issuer_id="xstocks", name="xStocks"),
            DimIssuer(issuer_id="backed", name="Backed Finance"),
            DimVenue(venue_id="binance", name="Binance", venue_type=VenueType.CEX),
            DimVenue(venue_id="jupiter", name="Jupiter", venue_type=VenueType.DEX),
            DimAsset(
                asset_id="spyx",
                symbol="SPYx",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="SPY",
                issuer_id="xstocks",
            ),
            DimAsset(
                asset_id="bspy",
                symbol="bSPY",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="SPY",
                issuer_id="backed",
            ),
            DimAsset(
                asset_id="xaut",
                symbol="XAUt",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="GLD",
                issuer_id="xstocks",
            ),
            DimPerpContract(
                contract_id="binance-SPYUSDT",
                exchange="binance",
                symbol="SPYUSDT",
                source_underlying_type="EQUITY",
                analysis_group="ETF",
                underlying_id="SPY",
            ),
            DimOwnProduct(
                product_id="csop-spy",
                name="CSOP S&P 500 Feeder",
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
                asset_id="bspy",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                market_cap=Decimal("1200000"),
                vol_24h=Decimal("48000"),
            ),
            FactAssetSnapshot(
                asset_id="xaut",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                market_cap=Decimal("900000000"),
                vol_24h=Decimal("14000000"),
            ),
            # SPY on two venues; the raw and adjusted figures diverge on one of them,
            # which is exactly the pair a reader must see both sides of (rule 5).
            FactPairSnapshot(
                asset_id="spyx",
                venue_id="binance",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("300000"),
                adjusted_vol_24h=Decimal("300000"),
            ),
            FactPairSnapshot(
                asset_id="bspy",
                venue_id="jupiter",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("62000"),
                adjusted_vol_24h=Decimal("9000"),
            ),
            FactPairSnapshot(
                asset_id="xaut",
                venue_id="binance",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("14000000"),
                adjusted_vol_24h=Decimal("14000000"),
            ),
            FactPerpContractSnapshot(
                contract_id="binance-SPYUSDT",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                vol_24h=Decimal("88000000"),
                oi_usd=Decimal("12000000"),
            ),
            # Measured launch windows for the earlier wrapper only. The later one
            # falls back to when it entered the catalogue, and must say so.
            FactLaunchWindowSnapshot(
                asset_id="spyx",
                window="1h",
                snapshot_ts=XSTOCKS_LISTED + timedelta(hours=1),
                market_session=MarketSession.RTH,
                listed_at=XSTOCKS_LISTED,
                raw_volume=Decimal("12000"),
                adjusted_volume=Decimal("11000"),
                venue_count=1,
            ),
            FactLaunchWindowSnapshot(
                asset_id="spyx",
                window="24h",
                snapshot_ts=XSTOCKS_LISTED + timedelta(hours=24),
                market_session=MarketSession.RTH,
                listed_at=XSTOCKS_LISTED,
                raw_volume=Decimal("140000"),
                adjusted_volume=Decimal("131000"),
                venue_count=3,
            ),
        ]
    )

    session.add_all(
        [
            Alert(
                dedup_key="X1:SPY:CLOSED_WEEKEND",
                detector="X1",
                family=DetectorFamily.CROSS_SECTIONAL,
                entity_type=EntityType.UNDERLYING,
                entity_id="SPY",
                metric_scope=MetricScope.SPOT_VOLUME,
                market_session=MarketSession.CLOSED_WEEKEND,
                severity=AlertSeverity.HIGH,
                score=Decimal("0.82"),
                status=AlertStatus.CONFIRMED,
                state=AlertState.PUBLISHED,
                published_at=NOW,
                headline_zh="SPY 换手率显著高于同类",
                first_seen_ts=NOW,
                last_seen_ts=NOW,
            ),
            # Never published, and scores higher. Any path that ranks before it
            # filters will surface it and fail a test here.
            Alert(
                dedup_key="T2:SPY:RTH",
                detector="T2",
                family=DetectorFamily.TIME_SERIES,
                entity_type=EntityType.UNDERLYING,
                entity_id="SPY",
                metric_scope=MetricScope.SPOT_VOLUME,
                market_session=MarketSession.RTH,
                severity=AlertSeverity.CRITICAL,
                score=Decimal("0.99"),
                status=AlertStatus.TENTATIVE,
                state=AlertState.DETECTED,
                headline_zh="冷启动期间的探测结果",
                first_seen_ts=NOW,
                last_seen_ts=NOW,
            ),
            # Published, then resolved. Open counts must exclude it.
            Alert(
                dedup_key="X3:GLD:RTH",
                detector="X3",
                family=DetectorFamily.CROSS_SECTIONAL,
                entity_type=EntityType.UNDERLYING,
                entity_id="GLD",
                metric_scope=MetricScope.SPOT_VOLUME,
                market_session=MarketSession.RTH,
                severity=AlertSeverity.MEDIUM,
                score=Decimal("0.5"),
                status=AlertStatus.CONFIRMED,
                state=AlertState.RESOLVED,
                published_at=NOW - timedelta(days=2),
                resolved_ts=NOW - timedelta(days=1),
                headline_zh="已处理完毕的黄金异常",
                first_seen_ts=NOW - timedelta(days=2),
                last_seen_ts=NOW - timedelta(days=2),
            ),
        ]
    )

    session.add_all(
        [
            ProductCoverage(
                underlying_id="SPY",
                state=CoverageState.LIVE,
                product_id="csop-spy",
                demand_value=Decimal("309000"),
                demand_scope=MetricScope.SPOT_VOLUME,
                assessed_at=NOW,
            ),
            ProductCoverage(
                underlying_id="GLD",
                state=CoverageState.GAP,
                note="需求可观，货架上没有对应产品",
                demand_value=Decimal("14000000"),
                demand_scope=MetricScope.SPOT_VOLUME,
                assessed_at=NOW,
            ),
            IssuanceCandidate(
                underlying_id="SPY",
                stage=CandidateStage.SCREENING,
                owner_id=None,
                demand_score=Decimal("0.9"),
                liquidity_score=Decimal("0.8"),
                competition_score=Decimal("0.7"),
                feasibility_score=Decimal("0.1"),
                rationale="需求显著高于同类",
                counter_rationale="托管方案尚未确认",
                last_reviewed_at=NOW,
            ),
            # Declined — a recorded decision, and still the thing to read before
            # anyone re-raises gold. ``issuance_candidate`` is unique per underlying,
            # so hiding a closed one leaves the page silent about it.
            IssuanceCandidate(
                underlying_id="GLD",
                stage=CandidateStage.DECLINED,
                demand_score=Decimal("0.2"),
                last_reviewed_at=NOW - timedelta(days=30),
            ),
            DataGap(
                kind=DataGapKind.QUALITY_DIVERGENCE,
                status=DataGapStatus.OPEN,
                dedup_key="quality:bspy:raw-adjusted",
                entity_type=EntityType.ASSET,
                entity_id="bspy",
                title="bSPY 原值与调整值背离",
                blocks="SPY 的现货成交口径",
                impact_score=Decimal("0.6"),
                is_blocking=True,
                first_seen_ts=NOW,
                last_seen_ts=NOW,
            ),
            # Closed, so it must not appear on the page.
            DataGap(
                kind=DataGapKind.ENTITY_COVERAGE,
                status=DataGapStatus.FIXED,
                dedup_key="mapping:spyx:fixed",
                entity_type=EntityType.ASSET,
                entity_id="spyx",
                title="已修复的映射缺口",
                first_seen_ts=NOW - timedelta(days=5),
                last_seen_ts=NOW - timedelta(days=4),
            ),
        ]
    )
    session.commit()


def _rows(client: TestClient, **params: object) -> list[dict]:
    response = client.get(_url("/underlyings"), params=params)
    assert response.status_code == 200, response.text
    return list(response.json()["rows"])


def _by_id(client: TestClient, **params: object) -> dict[str, dict]:
    return {row["underlying_id"]: row for row in _rows(client, **params)}


# --- the catalogue -----------------------------------------------------------


def test_one_security_however_many_wrappers_carry_it(client: TestClient) -> None:
    """U360-001: two issuers wrapping SPY is one row, not two."""
    spy = _by_id(client)["SPY"]

    assert spy["wrapper_count"] == 2
    assert spy["issuer_count"] == 2
    assert spy["venue_count"] == 2
    assert spy["has_perp"] is True


def test_the_four_scopes_travel_side_by_side_and_never_as_a_total(
    client: TestClient,
) -> None:
    """Rule 1: five kinds of number, no sum. Each carries its own scope."""
    body = client.get(_url("/underlyings")).json()
    spy = {row["underlying_id"]: row for row in body["rows"]}["SPY"]

    assert spy["spot_market_cap"]["scope"] == MetricScope.SPOT_MARKET_CAP.value
    assert spy["spot_vol_adjusted"]["scope"] == MetricScope.SPOT_VOLUME.value
    assert spy["perp_vol_24h"]["scope"] == MetricScope.PERP_VOLUME.value
    assert spy["perp_oi_usd"]["scope"] == MetricScope.PERP_OI.value
    # Four scopes declared on the envelope means "side by side", not "addable".
    assert len(body["meta"]["scopes"]) == 4
    assert "total" not in spy


def test_raw_and_adjusted_both_travel(client: TestClient) -> None:
    """Rule 5: bSPY reports $62k raw against $9k adjusted. Either alone misleads."""
    spy = _by_id(client)["SPY"]

    assert Decimal(spy["spot_vol_raw"]["value"]) == Decimal("362000")
    assert Decimal(spy["spot_vol_adjusted"]["value"]) == Decimal("309000")


def test_an_underlying_nobody_observed_is_not_a_zero(client: TestClient) -> None:
    """Rule 4: TLT has no wrapper at all. That is missing, not nil."""
    tlt = _by_id(client)["TLT"]

    assert tlt["spot_vol_adjusted"]["value"] is None
    assert tlt["spot_vol_adjusted"]["coverage"] == "not_verified"
    assert tlt["wrapper_count"] == 0
    assert tlt["coverage_state"] is None


def test_an_unobserved_figure_sorts_below_every_observed_one(
    client: TestClient,
) -> None:
    """Not-verified belongs at the end of a ranking, not at its zero.

    Ranking it as 0 would place it among the genuinely quiet names, which is a claim
    about the market rather than about our collection.
    """
    order = [row["underlying_id"] for row in _rows(client, sort="perp_volume")]

    # Only SPY has a perpetual, so the other two are unobserved in this scope.
    assert order[0] == "SPY"
    assert set(order[1:]) == {"GLD", "TLT"}


def test_each_ordering_ranks_one_scope_and_says_which(client: TestClient) -> None:
    """Rule 1 again, at the sort: there is no cross-scope ranking to ask for."""
    by_volume = client.get(_url("/underlyings"), params={"sort": "spot_volume"}).json()
    by_cap = client.get(_url("/underlyings"), params={"sort": "spot_market_cap"}).json()

    assert by_volume["sort"] == "spot_volume"
    assert MetricScope.SPOT_VOLUME.value in by_volume["meta"]["note"]
    assert MetricScope.SPOT_MARKET_CAP.value in by_cap["meta"]["note"]
    # GLD's turnover and market cap both dwarf SPY's here, so the two orderings
    # agree — what differs is which scope the response says it ranked.
    assert [row["underlying_id"] for row in by_volume["rows"]][0] == "GLD"


def test_an_unknown_sort_is_refused_rather_than_ignored(client: TestClient) -> None:
    """A silently-ignored sort returns the wrong order under a URL that claims it."""
    assert client.get(_url("/underlyings"), params={"sort": "hhi"}).status_code == 422


def test_search_matches_the_id_or_the_name(client: TestClient) -> None:
    assert [row["underlying_id"] for row in _rows(client, q="spdr")] == ["GLD", "SPY"]
    assert [row["underlying_id"] for row in _rows(client, q="tlt")] == ["TLT"]


def test_the_chips_filter_the_catalogue(client: TestClient) -> None:
    assert [row["underlying_id"] for row in _rows(client, asset_class="commodity")] == [
        "GLD"
    ]
    assert [row["underlying_id"] for row in _rows(client, has_perp=True)] == ["SPY"]
    assert [row["underlying_id"] for row in _rows(client, coverage_state="gap")] == [
        "GLD"
    ]


def test_the_badge_counts_only_what_the_gate_published(client: TestClient) -> None:
    """Rule 14, at the count: a held finding must not send anyone hunting for a row.

    SPY carries one published alert and one that never crossed the gate; GLD's only
    published alert is resolved. Both must read as the number of rows the queue would
    actually open.
    """
    rows = _by_id(client)

    assert rows["SPY"]["open_alert_count"] == 1
    assert rows["GLD"]["open_alert_count"] == 0


# --- the 360 -----------------------------------------------------------------


def test_the_page_reports_by_scope_and_refuses_to_total(client: TestClient) -> None:
    """U360-002."""
    body = client.get(_url("/underlyings/SPY")).json()

    assert [w["symbol"] for w in body["tokenized_wrappers"]] == ["SPYx", "bSPY"]
    assert [v["venue_id"] for v in body["venue_breakdown"]] == ["binance", "jupiter"]
    assert body["perp_exposure"][0]["contract"] == "SPYUSDT"
    assert Decimal(body["perp_oi_usd"]["value"]) == Decimal("12000000")
    assert "不可相加" in body["scope_note"]


def test_only_published_and_open_alerts_reach_the_page(client: TestClient) -> None:
    body = client.get(_url("/underlyings/SPY")).json()

    assert [a["detector"] for a in body["active_alerts"]] == ["X1"]


def test_the_page_states_where_we_stand_on_it(client: TestClient) -> None:
    """U360-003: shelf position, live candidates and open gaps, on one page."""
    body = client.get(_url("/underlyings/SPY")).json()

    assert body["our_coverage"]["state"] == CoverageState.LIVE.value
    assert body["our_coverage"]["product_id"] == "csop-spy"
    assert [c["stage"] for c in body["candidates"]] == [CandidateStage.SCREENING.value]
    assert body["candidates"][0]["weakest_dimension"] == "feasibility_score"


def test_a_closed_decision_stays_readable_on_the_entity_page(
    client: TestClient,
) -> None:
    """Unlike the queue, the 360 shows a declined candidate.

    ``GET /candidates`` hides closed rows because it is a work list. Here, "we looked
    at gold in July and said no" is the first thing anyone re-raising it should read,
    and there is only ever one candidate row per underlying to say it.
    """
    candidates = client.get(_url("/underlyings/GLD")).json()["candidates"]

    assert [c["stage"] for c in candidates] == [CandidateStage.DECLINED.value]
    # A terminal candidate offers no gate control; reopening is a new decision.
    assert candidates[0]["allowed_decisions"] == []


def test_a_candidate_on_this_page_still_carries_no_total(client: TestClient) -> None:
    """Rule 17: the heatmap ranks; it never approves."""
    candidate = client.get(_url("/underlyings/SPY")).json()["candidates"][0]

    assert {cell["key"] for cell in candidate["readiness"]} == {
        "demand_score",
        "liquidity_score",
        "competition_score",
        "feasibility_score",
    }
    assert "score" not in candidate
    assert "total" not in candidate


def test_an_unassessed_underlying_has_no_coverage_row_rather_than_a_gap(
    client: TestClient,
) -> None:
    """An unassessed underlying and one assessed as uncovered are different findings."""
    body = client.get(_url("/underlyings/TLT")).json()

    assert body["our_coverage"] is None
    assert body["candidates"] == []
    assert body["first_listings"] == []


def test_a_wrapper_level_gap_surfaces_on_the_underlying(client: TestClient) -> None:
    """The divergence is on bSPY, and it is the reason SPY's turnover is soft."""
    gaps = client.get(_url("/underlyings/SPY")).json()["open_data_gaps"]

    assert [g["entity_id"] for g in gaps] == ["bspy"]
    assert gaps[0]["is_blocking"] is True
    # The resolved mapping gap on spyx is closed and must not reappear.
    assert all(g["title"] != "已修复的映射缺口" for g in gaps)


def test_first_listing_says_whether_the_date_was_measured(client: TestClient) -> None:
    """A crawl time presented as a listing date is a wrong figure in a rival brief."""
    rows = client.get(_url("/underlyings/SPY")).json()["first_listings"]

    first, second = rows
    assert first["symbol"] == "SPYx"
    assert first["is_measured"] is True
    assert first["issuer"] == "xStocks"
    # The 24h window is the settled one, so its venue count is what carries.
    assert first["venue_count"] == 3
    assert second["symbol"] == "bSPY"
    assert second["is_measured"] is False
    assert second["venue_count"] is None


def test_an_unknown_underlying_is_a_404(client: TestClient) -> None:
    assert client.get(_url("/underlyings/NVDA")).status_code == 404


# --- the deprecated alias ----------------------------------------------------


def test_the_old_singular_path_still_answers_and_answers_identically(
    client: TestClient,
) -> None:
    """Rule 23: view state lives in the URL, so an old URL has to keep resolving.

    Compared field by field rather than spot-checked: an alias that drifts is worse
    than one that 404s, because nobody finds out until two people quote it.
    """
    canonical = client.get(_url("/underlyings/SPY"))
    alias = client.get(_url("/underlying/SPY"))

    assert alias.status_code == 200
    assert alias.json() == canonical.json()


def test_the_alias_is_marked_deprecated_in_the_schema(client: TestClient) -> None:
    """The label is how a client learns to move without anything breaking first."""
    schema = client.get(f"{API_PREFIX}/openapi.json").json()

    assert schema["paths"][f"{API_PREFIX}/underlying/{{underlying_id}}"]["get"][
        "deprecated"
    ]
    assert not schema["paths"][f"{API_PREFIX}/underlyings/{{underlying_id}}"][
        "get"
    ].get("deprecated")
