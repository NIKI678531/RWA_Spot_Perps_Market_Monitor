"""Tests for the R1 decision surfaces: home, work queue, tasks, candidates, quality.

These endpoints are where the product stops describing the market and starts recording
what people did about it, so what is asserted here is mostly the rules that make that
record trustworthy rather than the arithmetic:

* a detector output is invisible until the gate publishes it, and stricter still
  before it reaches a manager's screen;
* every state change appends an action *and* an audit row, in one transaction;
* a caller with no identity cannot act, and a share viewer cannot even see that the
  action exists;
* nothing anywhere sums the four readiness columns into an approval;
* a missing figure arrives as ``not_verified``, never as a zero.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.base import Base
from app.db.session import get_session
from app.main import API_PREFIX, create_app
from app.models.alerts import Alert, AlertEvidence
from app.models.dimensions import (
    DimAsset,
    DimIssuer,
    DimOwnProduct,
    DimUnderlying,
    DimUser,
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
    UserRole,
    VenueType,
)
from app.models.facts import FactAssetSnapshot, FactPairSnapshot
from app.models.workflow import (
    AuditLog,
    DataGap,
    IssuanceCandidate,
    ProductCoverage,
)
from app.services.anomaly.publication import sla_due

NOW = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)

#: Headers for someone who may act. The gateway sets these; the system only needs a
#: stable identity to attribute a decision to.
ANALYST = {"X-User-Id": "ada", "X-User-Role": UserRole.ANALYST.value}


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
    session.add_all(
        [
            DimUser(
                user_id="ada",
                display_name="Ada",
                role=UserRole.ANALYST,
            ),
            DimUser(user_id="grace", display_name="Grace", role=UserRole.OWNER),
            DimUnderlying(
                underlying_id="SPY",
                name="SPDR S&P 500 ETF",
                asset_class=AssetClass.ETF,
            ),
            DimUnderlying(
                underlying_id="GLD",
                name="SPDR Gold Shares",
                asset_class=AssetClass.COMMODITY,
            ),
            DimUnderlying(
                underlying_id="TLT",
                name="iShares 20+ Year Treasury Bond ETF",
                asset_class=AssetClass.ETF,
            ),
            DimIssuer(issuer_id="xstocks", name="xStocks"),
            DimVenue(venue_id="binance", name="Binance", venue_type=VenueType.CEX),
            DimAsset(
                asset_id="spyx",
                symbol="SPYx",
                rwa_tier=RwaTier.CORE_RWA,
                underlying_id="SPY",
                issuer_id="xstocks",
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
            FactPairSnapshot(
                asset_id="spyx",
                venue_id="binance",
                snapshot_ts=NOW,
                market_session=MarketSession.CLOSED_WEEKEND,
                raw_vol_24h=Decimal("362000"),
                adjusted_vol_24h=Decimal("362000"),
            ),
        ]
    )

    # A published cross-sectional finding: high, complete evidence, on the clock.
    published = Alert(
        dedup_key="X1:spyx:CLOSED_WEEKEND",
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
        sla_due_ts=sla_due(AlertSeverity.HIGH, NOW),
        evidence_completeness=Decimal("1"),
        headline_zh="SPY 换手率显著高于同类",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        occurrence_count=2,
        confirmation_count=2,
    )
    session.add(published)
    session.add(
        AlertEvidence(
            alert=published,
            snapshot_ts=NOW,
            rule_name="cross_sectional_turnover",
            observed_value=Decimal("362000"),
            baseline_median=Decimal("40000"),
            baseline_mad=Decimal("8000"),
            robust_z=Decimal("27.1"),
            sample_size=19,
            market_session=MarketSession.CLOSED_WEEKEND,
            peer_count=12,
            counter_evidence_json=(
                '[{"code": "single_venue", "label_zh": "仅单一场所出现",'
                ' "detail": "只有 Binance 一处确认"}]'
            ),
        )
    )

    # Never published. Scores higher than the one above, so any read path that sorts
    # before filtering will surface it and fail a test.
    session.add(
        Alert(
            dedup_key="T2:spyx:RTH",
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
        )
    )

    session.add_all(
        [
            IssuanceCandidate(
                underlying_id="SPY",
                stage=CandidateStage.SCREENING,
                owner_id="grace",
                demand_score=Decimal("0.9"),
                liquidity_score=Decimal("0.8"),
                competition_score=Decimal("0.7"),
                feasibility_score=Decimal("0.1"),
                rationale="需求显著高于同类",
                counter_rationale="托管方案尚未确认",
            ),
            IssuanceCandidate(
                underlying_id="GLD",
                stage=CandidateStage.WATCH,
                demand_score=Decimal("0.4"),
            ),
            ProductCoverage(
                underlying_id="SPY",
                state=CoverageState.LIVE,
                product_id="csop-spy",
                demand_value=Decimal("362000"),
                demand_scope=MetricScope.SPOT_VOLUME,
                assessed_at=NOW,
            ),
            # No demand figure: never assessed, which is not the same as no demand.
            ProductCoverage(
                underlying_id="GLD",
                state=CoverageState.GAP,
                note="尚未评估",
            ),
            # Assessed against a stated scope, but the fetch failed. A missing
            # observation, which must not arrive as a zero.
            ProductCoverage(
                underlying_id="TLT",
                state=CoverageState.PLANNED,
                demand_value=None,
                demand_scope=MetricScope.SPOT_VOLUME,
                assessed_at=NOW,
            ),
            DataGap(
                kind=DataGapKind.SOURCE_HEALTH,
                status=DataGapStatus.OPEN,
                dedup_key="source:coingecko:429",
                source_id="coingecko",
                title="CoinGecko 连续限流",
                blocks="现货规模页的类别口径",
                impact_score=Decimal("0.9"),
                is_blocking=True,
                first_seen_ts=NOW,
                last_seen_ts=NOW,
            ),
            DataGap(
                kind=DataGapKind.ENTITY_COVERAGE,
                status=DataGapStatus.OPEN,
                dedup_key="mapping:unmapped-wrapper",
                entity_type=EntityType.ASSET,
                entity_id="spyx",
                title="包装代币缺少底层映射",
                impact_score=Decimal("0.3"),
                first_seen_ts=NOW,
                last_seen_ts=NOW,
            ),
        ]
    )
    session.commit()


def _published_alert_id(client: TestClient) -> int:
    rows = client.get(_url("/alerts/queue")).json()["rows"]
    assert len(rows) == 1, "only the published finding belongs in the queue"
    return int(rows[0]["id"])


# --- the publication gate ----------------------------------------------------


def test_the_queue_shows_only_what_the_gate_published(client: TestClient) -> None:
    """Rule 14: detection is not publication."""
    body = client.get(_url("/alerts/queue")).json()

    assert [row["detector"] for row in body["rows"]] == ["X1"]
    assert body["summary"]["unclaimed"] == 1
    # The counters are drawn from the same published-only pool, so an unpublished
    # detector output cannot inflate the queue's own idea of how much work there is.
    assert body["summary"]["closed"] == 0


def test_a_queue_row_states_which_comparison_produced_it(client: TestClient) -> None:
    """Rule 12: the two detector families make different claims, in different words."""
    row = client.get(_url("/alerts/queue")).json()["rows"][0]

    assert row["family"] == DetectorFamily.CROSS_SECTIONAL.value
    assert row["family_claim"] == "同组现状异常"
    assert row["confirmations_required"] == 1


def test_the_work_item_ships_counter_evidence_with_the_evidence(
    client: TestClient,
) -> None:
    """Rule 18: a one-sided alert page trains people to stop reading alert pages."""
    alert_id = _published_alert_id(client)
    body = client.get(_url(f"/alerts/{alert_id}/workitem")).json()

    assert body["evidence"][0]["sample_size"] == 19
    assert [item["code"] for item in body["counter_evidence"]] == ["single_venue"]
    assert body["confidence"]["level"] in ("low", "medium", "high")
    assert body["confidence"]["basis"]
    assert "claim" in body["available_actions"]


# --- who may act -------------------------------------------------------------


def test_an_unattributable_write_is_refused(client: TestClient) -> None:
    """An action nobody can be named for is the same as no audit trail."""
    alert_id = _published_alert_id(client)

    anonymous = client.post(_url(f"/alerts/{alert_id}/claim"), json={})
    assert anonymous.status_code == 403

    nameless = client.post(
        _url(f"/alerts/{alert_id}/claim"),
        json={},
        headers={"X-User-Role": UserRole.ANALYST.value},
    )
    assert nameless.status_code == 403


def test_a_share_viewer_is_not_told_the_action_exists(client: TestClient) -> None:
    """Rule 22: a denial must not reveal whether the record is there."""
    alert_id = _published_alert_id(client)
    response = client.post(
        _url(f"/alerts/{alert_id}/claim"), json={}, params={"share_token": "anything"}
    )

    assert response.status_code == 404


def test_share_viewer_cannot_be_claimed_by_a_header(client: TestClient) -> None:
    """The role is reached by holding a share token, not by asserting it."""
    alert_id = _published_alert_id(client)
    response = client.post(
        _url(f"/alerts/{alert_id}/claim"),
        json={},
        headers={"X-User-Id": "ada", "X-User-Role": UserRole.SHARE_VIEWER.value},
    )

    assert response.status_code == 400


# --- the lifecycle -----------------------------------------------------------


def test_claiming_appends_an_action_and_an_audit_row(
    client: TestClient, session: Session
) -> None:
    """Rule 16 and the audit convention: one transaction, three rows or none."""
    alert_id = _published_alert_id(client)
    body = client.post(
        _url(f"/alerts/{alert_id}/claim"),
        json={"note": "我来看"},
        headers=ANALYST,
    ).json()

    assert body["alert"]["state"] == AlertState.CLAIMED.value
    assert body["alert"]["owner_id"] == "ada"
    assert body["alert"]["owner_name"] == "Ada"

    actions = [entry["action"] for entry in body["timeline"]]
    assert "claim" in actions and "note" in actions

    audit = (
        session.execute(select(AuditLog).where(AuditLog.entity_kind == "alert"))
        .scalars()
        .all()
    )
    assert [row.actor_id for row in audit] == ["ada"] * len(audit)
    assert audit, "a state change with no audit row is not auditable"


def test_an_illegal_transition_is_refused(client: TestClient) -> None:
    """The state machine lives in one place, and the route defers to it."""
    alert_id = _published_alert_id(client)
    response = client.post(
        _url(f"/alerts/{alert_id}/action"), json={"note": "已决策"}, headers=ANALYST
    )

    assert response.status_code == 409


def test_closing_requires_a_reason(client: TestClient) -> None:
    """An alert closed with no statement of why cannot be reviewed."""
    alert_id = _published_alert_id(client)
    client.post(_url(f"/alerts/{alert_id}/claim"), json={}, headers=ANALYST)

    # 422, not 409: the move itself is legal, the payload is incomplete.
    unexplained = client.post(
        _url(f"/alerts/{alert_id}/close"),
        json={"to_state": AlertState.FALSE_POSITIVE.value},
        headers=ANALYST,
    )
    assert unexplained.status_code == 422

    explained = client.post(
        _url(f"/alerts/{alert_id}/close"),
        json={
            "to_state": AlertState.FALSE_POSITIVE.value,
            "reason_code": "data_artefact",
            "note": "单一场所的报价异常",
        },
        headers=ANALYST,
    )
    assert explained.status_code == 200
    assert explained.json()["alert"]["state"] == AlertState.FALSE_POSITIVE.value
    assert explained.json()["available_actions"] == []


def test_a_task_raised_from_an_alert_lands_on_its_timeline(
    client: TestClient,
) -> None:
    """ "We looked into it" has to be checkable, so it is a row, not a memory."""
    alert_id = _published_alert_id(client)
    body = client.post(
        _url(f"/alerts/{alert_id}/tasks"),
        json={"title": "核对 Binance 单场所口径", "owner_id": "ada"},
        headers=ANALYST,
    ).json()

    assert [task["title"] for task in body["tasks"]] == ["核对 Binance 单场所口径"]
    assert "create_task" in [entry["action"] for entry in body["timeline"]]

    listed = client.get(_url("/tasks")).json()
    assert [task["alert_id"] for task in listed["rows"]] == [alert_id]


def test_completing_a_task_requires_an_outcome(client: TestClient) -> None:
    created = client.post(
        _url("/tasks"),
        json={"title": "补齐 GLD 映射"},
        headers=ANALYST,
    ).json()
    task_id = created["id"]

    silent = client.post(
        _url(f"/tasks/{task_id}/status"),
        json={"status": "done"},
        headers=ANALYST,
    )
    assert silent.status_code == 422

    answered = client.post(
        _url(f"/tasks/{task_id}/status"),
        json={"status": "done", "outcome": "映射已补齐，缺口关闭"},
        headers=ANALYST,
    )
    assert answered.status_code == 200
    assert answered.json()["outcome"] == "映射已补齐，缺口关闭"


# --- issuance candidates -----------------------------------------------------


def test_no_row_carries_a_total_score(client: TestClient) -> None:
    """Rule 17: the heatmap ranks and diagnoses; it never approves."""
    rows = client.get(_url("/candidates")).json()["rows"]
    spy = next(row for row in rows if row["underlying_id"] == "SPY")

    assert [cell["key"] for cell in spy["readiness"]] == [
        "demand_score",
        "liquidity_score",
        "competition_score",
        "feasibility_score",
    ]
    assert all("total" not in key for key in spy)
    # The row's one summary figure is the *weakest* column, which is a pointer to the
    # blocker rather than an average that would hide it. It names a column key so the
    # heatmap can highlight the cell it refers to.
    assert spy["weakest_dimension"] == "feasibility_score"
    assert spy["weakest_dimension"] in {cell["key"] for cell in spy["readiness"]}
    assert spy["counter_rationale"]


def test_an_unassessed_column_reads_as_absent_not_as_zero(client: TestClient) -> None:
    rows = client.get(_url("/candidates")).json()["rows"]
    gld = next(row for row in rows if row["underlying_id"] == "GLD")
    liquidity = next(c for c in gld["readiness"] if c["key"] == "liquidity_score")

    assert liquidity["value"] is None
    assert liquidity["basis"] == "尚未评估"


def test_a_stage_moves_only_through_a_recorded_decision(client: TestClient) -> None:
    listed = client.get(_url("/candidates")).json()["rows"]
    candidate_id = next(r["id"] for r in listed if r["underlying_id"] == "SPY")

    body = client.post(
        _url(f"/candidates/{candidate_id}/decide"),
        json={"decision": "advance", "reason": "需求侧证据充分，进入可行性评估"},
        headers=ANALYST,
    ).json()

    assert body["candidate"]["stage"] == CandidateStage.FEASIBILITY.value
    entry = body["history"][-1]
    assert entry["decided_by"] == "ada"
    assert entry["reason"]
    assert entry["from_stage"] == CandidateStage.SCREENING.value


def test_a_terminal_candidate_offers_no_gate_control(client: TestClient) -> None:
    listed = client.get(_url("/candidates")).json()["rows"]
    candidate_id = next(r["id"] for r in listed if r["underlying_id"] == "GLD")

    client.post(
        _url(f"/candidates/{candidate_id}/decide"),
        json={"decision": "decline", "reason": "现阶段无产品化空间"},
        headers=ANALYST,
    )
    detail = client.get(_url(f"/candidates/{candidate_id}")).json()

    assert detail["candidate"]["stage"] == CandidateStage.DECLINED.value
    assert detail["candidate"]["allowed_decisions"] == []
    # And it leaves the default list, which is a queue of live work.
    assert "GLD" not in {
        r["underlying_id"] for r in client.get(_url("/candidates")).json()["rows"]
    }


# --- data quality ------------------------------------------------------------


def test_gaps_are_grouped_into_the_five_zones(client: TestClient) -> None:
    body = client.get(_url("/data-gaps")).json()

    assert [zone["kind"] for zone in body["zones"]] == [
        DataGapKind.SOURCE_HEALTH.value,
        DataGapKind.ENTITY_COVERAGE.value,
        DataGapKind.QUALITY_DIVERGENCE.value,
        DataGapKind.VERIFICATION.value,
        DataGapKind.BASELINE_HEALTH.value,
    ]
    source_health = body["zones"][0]
    assert source_health["open_count"] == 1
    assert source_health["blocking_count"] == 1
    assert source_health["rows"][0]["blocks"]


def test_publication_is_blocked_regardless_of_the_zone_filter(
    client: TestClient,
) -> None:
    """Whether a conclusion is publishable cannot depend on which tab is open."""
    unfiltered = client.get(_url("/data-gaps")).json()
    filtered = client.get(
        _url("/data-gaps"), params={"kind": DataGapKind.ENTITY_COVERAGE.value}
    ).json()

    assert unfiltered["publication_blocked"] is True
    assert filtered["publication_blocked"] is True


def test_closing_a_gap_needs_a_note_and_records_who(
    client: TestClient, session: Session
) -> None:
    gap_id = client.get(_url("/data-gaps")).json()["zones"][1]["rows"][0]["id"]

    client.post(
        _url(f"/data-gaps/{gap_id}/assign"),
        json={"owner_id": "ada"},
        headers=ANALYST,
    )
    closed = client.post(
        _url(f"/data-gaps/{gap_id}/close"),
        json={"status": DataGapStatus.FIXED.value, "note": "映射已补齐"},
        headers=ANALYST,
    )

    assert closed.status_code == 200
    audit = (
        session.execute(select(AuditLog).where(AuditLog.entity_kind == "data_gap"))
        .scalars()
        .all()
    )
    assert {row.actor_id for row in audit} == {"ada"}


def test_coverage_states_demand_or_states_that_it_is_unverified(
    client: TestClient,
) -> None:
    """Rule 4: a figure nobody measured is not a figure of zero."""
    rows = client.get(_url("/coverage")).json()["rows"]
    by_id = {row["underlying_id"]: row for row in rows}

    assert by_id["SPY"]["demand"]["verification_status"] == "verified"
    assert by_id["SPY"]["demand"]["metric_scope"] == MetricScope.SPOT_VOLUME.value
    # Never assessed, so there is no scope to state a figure in. The field is absent
    # rather than a zero in an invented scope — a blank cell, not a claim of no demand.
    assert by_id["GLD"]["demand"] is None
    assert by_id["GLD"]["note"]

    # Assessed, scope known, observation missing. This one *is* a value object, and
    # it says so: null with a reason, never 0.
    assert by_id["TLT"]["demand"]["value"] is None
    assert by_id["TLT"]["demand"]["verification_status"] == "not_verified"
    assert by_id["TLT"]["demand"]["metric_scope"] == MetricScope.SPOT_VOLUME.value


# --- home and retrospective --------------------------------------------------


def test_the_home_page_answers_four_questions_with_evidence(
    client: TestClient,
) -> None:
    body = client.get(_url("/home")).json()

    assert [card["key"] for card in body["questions"]] == [
        "q1_volume",
        "q2_heating",
        "q3_new_products",
        "q4_issuance",
    ]
    for card in body["questions"]:
        assert card["question"]
        assert card["confidence"]["level"] in ("low", "medium", "high")
        # Every card either answers with evidence or says why it cannot. What it may
        # not do is state a headline with nothing behind it.
        assert card["evidence"] or card["answer"]


def test_the_home_page_carries_the_status_bar_and_a_cut_off(
    client: TestClient,
) -> None:
    body = client.get(_url("/home")).json()

    assert body["status_bar"]["as_of"]
    assert body["meta"]["as_of"]
    assert body["meta"]["note"]


def test_the_retrospective_leaves_a_rate_null_rather_than_zero(
    client: TestClient,
) -> None:
    """Rule 4 again: "no cases" is not an argument for loosening a threshold."""
    body = client.get(_url("/retrospective")).json()
    detector = next(row for row in body["detectors"] if row["detector"] == "X1")

    assert detector["published"] == 1
    assert detector["false_positive_rate"] is None
    assert detector["median_time_to_close_hours"] is None
    assert body["rows"] == []


def test_a_closed_alert_reaches_the_retrospective_with_its_reason(
    client: TestClient,
) -> None:
    alert_id = _published_alert_id(client)
    client.post(_url(f"/alerts/{alert_id}/claim"), json={}, headers=ANALYST)
    client.post(
        _url(f"/alerts/{alert_id}/close"),
        json={
            "to_state": AlertState.FALSE_POSITIVE.value,
            "reason_code": "data_artefact",
            "note": "单一场所的报价异常",
        },
        headers=ANALYST,
    )

    body = client.get(_url("/retrospective")).json()
    row = body["rows"][0]

    assert row["final_state"] == AlertState.FALSE_POSITIVE.value
    assert row["reason_code"] == "data_artefact"
    assert row["outcome_note"]
    assert body["detectors"][0]["false_positive_rate"] == 1.0
    # The counter-evidence that was there all along is what a threshold review acts on.
    assert body["detectors"][0]["top_counter_evidence"] == "single_venue"


def test_the_review_window_is_a_filter_not_a_default(client: TestClient) -> None:
    inside = client.get(_url("/retrospective"), params={"days": 365})
    assert inside.json()["window_days"] == 365
    assert inside.json()["detectors"]

    # NOW is seeded well in the past relative to wall clock, so a short window
    # legitimately finds nothing — and says so rather than returning stale rows.
    outside = client.get(_url("/retrospective"), params={"days": 1})
    assert outside.json()["detectors"] == []


def test_a_deferred_alert_leaves_the_queue_until_it_is_due(
    client: TestClient,
) -> None:
    alert_id = _published_alert_id(client)
    until = datetime.now(timezone.utc) + timedelta(days=3)
    client.post(
        _url(f"/alerts/{alert_id}/defer"),
        json={"until": until.isoformat(), "note": "等下一次同时段确认"},
        headers=ANALYST,
    )

    assert client.get(_url("/alerts/queue")).json()["rows"] == []
    # Never dropped, only hidden: the snooze is a scheduling decision, not a closure.
    shown = client.get(_url("/alerts/queue"), params={"include_deferred": True}).json()
    assert [row["id"] for row in shown["rows"]] == [alert_id]
