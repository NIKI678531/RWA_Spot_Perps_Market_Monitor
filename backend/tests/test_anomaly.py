"""Tests for scoring gates, the cross-sectional detectors and the alert lifecycle."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.base import Base
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AssetClass,
    DetectorFamily,
    EntityType,
    RwaTier,
)
from app.services.anomaly import scoring
from app.services.anomaly.detectors import (
    x1_cross_sectional_turnover as x1,
    x2_buy_sell_imbalance as x2,
    x3_vol_liq_ratio_extreme as x3,
    x4_new_pair_listing as x4,
)
from app.services.anomaly.engine import AnomalyEngine
from app.services.anomaly.signals import Evidence, Signal

NOW = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)


def _signal(
    *,
    entity_id: str = "SPYB",
    notional: str | None = "1000000",
    robust_z: float | None = 5.0,
    tier: RwaTier = RwaTier.CORE_RWA,
    peer_count: int | None = None,
) -> Signal:
    return Signal(
        detector="X1",
        family=DetectorFamily.CROSS_SECTIONAL,
        entity_type=EntityType.ASSET,
        entity_id=entity_id,
        metric_scope=MetricScope.SPOT_VOLUME,
        market_session=MarketSession.RTH,
        headline_zh=f"{entity_id} 换手率离群",
        notional_usd=Decimal(notional) if notional is not None else None,
        rwa_tier=tier,
        evidence=Evidence(
            rule_name="CrossSectionalTurnover",
            observed_value=Decimal(notional) if notional is not None else None,
            robust_z=robust_z,
            peer_count=peer_count,
        ),
    )


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


class TestGates:
    def test_the_absolute_floor_rejects_commercially_meaningless_moves(self) -> None:
        """$500 to $5,000 is +900% and worth nobody's attention."""
        result = scoring.check_gates(_signal(notional="5000"))
        assert not result.passed
        assert "floor" in (result.reason or "")

    def test_out_of_scope_assets_never_alert(self) -> None:
        result = scoring.check_gates(_signal(tier=RwaTier.NON_RWA))
        assert not result.passed

    def test_a_signal_without_a_notional_cannot_bypass_the_floor(self) -> None:
        result = scoring.check_gates(_signal(notional=None))
        assert not result.passed

    def test_a_thin_peer_group_is_not_a_finding(self) -> None:
        assert not scoring.check_gates(_signal(peer_count=3)).passed
        assert scoring.check_gates(_signal(peer_count=5)).passed


class TestScoring:
    def test_a_just_triggering_signal_is_not_labelled_critical(self) -> None:
        score = scoring.severity_score(
            robust_z=3.5, notional_usd=Decimal("60000"), consecutive_snapshots=1
        )
        assert scoring.to_severity(score) in (
            AlertSeverity.LOW,
            AlertSeverity.MEDIUM,
        )

    def test_a_large_persistent_extreme_move_is_critical(self) -> None:
        score = scoring.severity_score(
            robust_z=25.0,
            notional_usd=Decimal("2000000000"),
            consecutive_snapshots=5,
        )
        assert scoring.to_severity(score) is AlertSeverity.CRITICAL

    def test_a_flat_baseline_saturates_rather_than_producing_nan(self) -> None:
        """Dormant assets have zero MAD; the score must stay a number."""
        score = scoring.severity_score(
            robust_z=float("inf"), notional_usd=Decimal("100000")
        )
        assert 0.0 <= score <= 1.0

    def test_magnitude_is_logarithmic_not_linear(self) -> None:
        small = scoring.severity_score(robust_z=None, notional_usd=Decimal("100000"))
        mid = scoring.severity_score(robust_z=None, notional_usd=Decimal("10000000"))
        large = scoring.severity_score(
            robust_z=None, notional_usd=Decimal("1000000000")
        )
        # Each 100x step adds a comparable amount, rather than the first being lost.
        assert mid - small == pytest.approx(large - mid, abs=0.01)


class TestX1CrossSectionalTurnover:
    def _peers(self, n: int = 8) -> list[x1.AssetObservation]:
        """A realistic spread of turnover: roughly 0.8% to 1.5%."""
        return [
            x1.AssetObservation(
                asset_id=f"PEER{i}",
                asset_class=AssetClass.EQUITY,
                rwa_tier=RwaTier.CORE_RWA,
                vol_24h=Decimal(80_000 + i * 10_000),
                market_cap=Decimal("10000000"),
            )
            for i in range(n)
        ]

    def test_an_intensely_traded_asset_stands_out_with_no_history(self) -> None:
        """The cold-start answer to 'who is being bought' needs no baseline."""
        peers = self._peers()
        peers[0] = x1.AssetObservation(
            asset_id="HOT",
            asset_class=AssetClass.EQUITY,
            rwa_tier=RwaTier.CORE_RWA,
            vol_24h=Decimal("4000000"),
            market_cap=Decimal("10000000"),  # 40% turnover against a ~1.2% median
        )

        signals = x1.detect(peers, MarketSession.RTH)

        assert [s.entity_id for s in signals] == ["HOT"]
        assert signals[0].metric_scope is MetricScope.SPOT_VOLUME
        assert signals[0].evidence.peer_count == 8

    def test_a_peer_group_with_no_spread_ranks_nobody(self) -> None:
        """Every member equidistant from an identical median is not a finding."""
        flat = [
            x1.AssetObservation(
                asset_id=f"FLAT{i}",
                asset_class=AssetClass.EQUITY,
                rwa_tier=RwaTier.CORE_RWA,
                vol_24h=Decimal("100000"),
                market_cap=Decimal("10000000"),
            )
            for i in range(7)
        ] + [
            x1.AssetObservation(
                asset_id="SLIGHTLY_OFF",
                asset_class=AssetClass.EQUITY,
                rwa_tier=RwaTier.CORE_RWA,
                vol_24h=Decimal("150000"),
                market_cap=Decimal("10000000"),
            )
        ]
        assert x1.detect(flat, MarketSession.RTH) == []

    def test_a_thin_peer_group_yields_nothing(self) -> None:
        assert x1.detect(self._peers(3), MarketSession.RTH) == []

    def test_a_microcap_denominator_does_not_manufacture_an_outlier(self) -> None:
        peers = self._peers()
        peers[0] = x1.AssetObservation(
            asset_id="DUST",
            asset_class=AssetClass.EQUITY,
            rwa_tier=RwaTier.CORE_RWA,
            vol_24h=Decimal("100000"),
            market_cap=Decimal("1000"),  # below the $250k market-cap floor
        )
        assert [s.entity_id for s in x1.detect(peers, MarketSession.RTH)] == []

    def test_peer_groups_do_not_mix_asset_classes(self) -> None:
        """Pre-IPO turnover is structurally unlike ETF turnover."""
        mixed = self._peers(4) + [
            x1.AssetObservation(
                asset_id=f"PREIPO{i}",
                asset_class=AssetClass.PRE_IPO,
                rwa_tier=RwaTier.CORE_RWA,
                vol_24h=Decimal("5000000"),
                market_cap=Decimal("10000000"),
            )
            for i in range(4)
        ]
        # Neither group reaches five members, so nothing fires — which is the point:
        # pooling them would have made every Pre-IPO an outlier.
        assert x1.detect(mixed, MarketSession.RTH) == []


class TestX2BuySellImbalance:
    def test_a_one_sided_book_is_the_only_direct_evidence_of_buying(self) -> None:
        pools = [
            x2.PoolObservation(
                pool_id="pool-hot",
                network="solana",
                vol_24h=Decimal("500000"),
                buys_24h=800,
                sells_24h=200,
            )
        ]
        signals = x2.detect(pools, MarketSession.RTH)

        assert len(signals) == 1
        assert signals[0].evidence.extra["direction"] == "buy"
        assert "净买入" in signals[0].headline_zh

    def test_nine_trades_are_not_a_market(self) -> None:
        pools = [
            x2.PoolObservation(
                pool_id="pool-thin",
                network="solana",
                vol_24h=Decimal("500000"),
                buys_24h=7,
                sells_24h=2,
            )
        ]
        assert x2.detect(pools, MarketSession.RTH) == []

    def test_a_balanced_book_says_nothing(self) -> None:
        pools = [
            x2.PoolObservation(
                pool_id="pool-even",
                network="solana",
                vol_24h=Decimal("500000"),
                buys_24h=520,
                sells_24h=480,
            )
        ]
        assert x2.detect(pools, MarketSession.RTH) == []


class TestX3VolLiqRatio:
    def test_a_thin_pool_turning_over_many_times_is_flagged(self) -> None:
        pools = [
            x3.PoolLiquidityObservation(
                pool_id="pool-thin",
                network="bsc",
                vol_24h=Decimal("6000000"),
                reserve_usd=Decimal("200000"),  # 30x
            )
        ]
        signals = x3.detect(pools, MarketSession.RTH)
        assert len(signals) == 1
        assert signals[0].evidence.extra["vol_liq"] == pytest.approx(30.0)

    def test_a_deep_pool_is_left_alone(self) -> None:
        pools = [
            x3.PoolLiquidityObservation(
                pool_id="pool-deep",
                network="ethereum",
                vol_24h=Decimal("1000000"),
                reserve_usd=Decimal("5000000"),
            )
        ]
        assert x3.detect(pools, MarketSession.RTH) == []


class TestX4NewPairListing:
    def test_a_first_sighting_with_real_volume_is_a_distribution_signal(self) -> None:
        pairs = [x4.PairObservation("SPYB", "binance", Decimal("900000"))]
        signals = x4.detect(pairs, known_pairs=set(), market_session=MarketSession.RTH)

        assert [s.entity_id for s in signals] == ["SPYB@binance"]
        assert signals[0].evidence.robust_z is None

    def test_an_announcement_nobody_traded_is_not_a_signal(self) -> None:
        pairs = [x4.PairObservation("SPYB", "binance", Decimal("1000"))]
        assert x4.detect(pairs, set(), MarketSession.RTH) == []

    def test_a_pair_seen_before_is_not_new(self) -> None:
        pairs = [x4.PairObservation("SPYB", "binance", Decimal("900000"))]
        assert x4.detect(pairs, {("SPYB", "binance")}, MarketSession.RTH) == []


class TestAlertLifecycle:
    def test_a_first_firing_is_tentative_and_carries_evidence(
        self, session: Session
    ) -> None:
        result = AnomalyEngine().process(session, NOW, [_signal()])
        session.flush()

        assert len(result.created) == 1
        alert = result.created[0]
        assert alert.status is AlertStatus.TENTATIVE
        assert alert.occurrence_count == 1
        assert len(alert.evidence) == 1
        assert alert.evidence[0].rule_name == "CrossSectionalTurnover"

    def test_a_second_snapshot_confirms_rather_than_duplicates(
        self, session: Session
    ) -> None:
        engine = AnomalyEngine()
        engine.process(session, NOW, [_signal()])
        session.flush()

        result = engine.process(session, NOW + timedelta(hours=1), [_signal()])
        session.flush()

        assert result.created == []
        assert len(result.updated) == 1
        alert = result.updated[0]
        assert alert.status is AlertStatus.CONFIRMED
        assert alert.occurrence_count == 2
        assert len(alert.evidence) == 2

    def test_the_same_condition_after_the_cooldown_is_a_new_alert(
        self, session: Session
    ) -> None:
        engine = AnomalyEngine()
        engine.process(session, NOW, [_signal()])
        session.flush()

        result = engine.process(session, NOW + timedelta(hours=30), [_signal()])
        session.flush()

        assert len(result.created) == 1

    def test_suppressions_are_reported_not_silently_dropped(
        self, session: Session
    ) -> None:
        result = AnomalyEngine().process(session, NOW, [_signal(notional="1000")])

        assert result.created == []
        assert len(result.suppressed) == 1
        assert "floor" in result.suppressed[0][1]

    def test_an_infinite_z_is_recorded_in_evidence_rather_than_lost(
        self, session: Session
    ) -> None:
        result = AnomalyEngine().process(session, NOW, [_signal(robust_z=float("inf"))])
        session.flush()

        evidence = result.created[0].evidence[0]
        assert evidence.robust_z is None
        assert "inf" in (evidence.extra_json or "")

    def test_a_broken_detector_does_not_cost_the_snapshot(
        self, session: Session
    ) -> None:
        engine = AnomalyEngine()
        engine.register(
            "BROKEN",
            DetectorFamily.CROSS_SECTIONAL,
            lambda: (_ for _ in ()).throw(RuntimeError("upstream shape changed")),
        )
        engine.register("OK", DetectorFamily.CROSS_SECTIONAL, lambda: [_signal()])

        result = engine.run(session, NOW)
        session.flush()

        assert len(result.created) == 1
