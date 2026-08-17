"""Tests for the flagship detector and its session-stratified baseline."""

from datetime import datetime

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession, classify_session, is_underlying_open
from app.services.analytics.baseline import compute_baseline
from app.services.anomaly.detectors import t2_cold_start_awakening as det


def _dormant_history(n: int = 20, level: float = 300.0) -> list[float]:
    return [level] * n


def test_detects_a_genuine_awakening() -> None:
    """A dormant product suddenly trading materially is the core signal."""
    history = _dormant_history()
    baseline = compute_baseline(history, MarketSession.RTH)
    assert baseline is not None

    signal = det.detect(
        entity_id="SPCXB",
        current_value=362_076.0,
        history=history,
        baseline=baseline,
    )

    assert signal is not None
    assert signal.entity_id == "SPCXB"
    assert signal.metric_scope is MetricScope.SPOT_VOLUME
    assert signal.dormant_observations == 20
    assert signal.multiple_of_baseline > 1000
    assert "SPCXB" in signal.headline_zh


def test_ignores_small_absolute_moves() -> None:
    """$200 to $4,000 is +1900% and commercially meaningless."""
    history = _dormant_history(level=200.0)
    baseline = compute_baseline(history, MarketSession.RTH)
    assert baseline is not None

    assert det.detect("DUSTB", 4_000.0, history, baseline) is None


def test_ignores_products_that_were_already_trading() -> None:
    """An already-liquid product jumping is a volume spike, not an awakening."""
    history = [5_000_000.0] * 20
    baseline = compute_baseline(history, MarketSession.RTH)
    assert baseline is not None

    assert det.detect("SPYB", 15_000_000.0, history, baseline) is None


def test_tolerates_one_unrepresentative_print_in_the_dormant_window() -> None:
    history = _dormant_history(19) + [80_000.0]
    baseline = compute_baseline(history, MarketSession.RTH)
    assert baseline is not None

    signal = det.detect("QQQB", 500_000.0, history, baseline)
    assert signal is not None
    assert signal.dormant_observations == 19


def test_does_not_alert_before_baseline_has_enough_history() -> None:
    """Cold-start period records but stays silent, so week one is not a spam feed."""
    history = _dormant_history(5)
    baseline = compute_baseline(history, MarketSession.RTH)
    assert baseline is not None
    assert not baseline.is_alertable

    assert det.detect("NEWB", 900_000.0, history, baseline) is None


def test_flat_series_yields_infinite_score_rather_than_dividing_by_zero() -> None:
    baseline = compute_baseline([300.0] * 20, MarketSession.RTH)
    assert baseline is not None
    assert baseline.mad == 0
    assert baseline.robust_z(362_076.0) == float("inf")
    assert baseline.robust_z(300.0) == 0.0


def test_the_infinite_score_keeps_the_direction_of_the_move() -> None:
    """A collapse and a spike must not score identically.

    Every detector reading this number is directional — an awakening looks for a jump,
    an evaporation for a fall. An unsigned infinity would make a product that stopped
    trading indistinguishable from one that suddenly did.
    """
    baseline = compute_baseline([300.0] * 20, MarketSession.RTH)
    assert baseline is not None
    assert baseline.robust_z(0.0) == float("-inf")


def test_median_resists_the_spike_a_mean_would_absorb() -> None:
    """Right-skewed volume is why the baseline is median + MAD, not mean + stdev."""
    history = [300.0] * 19 + [50_000_000.0]
    baseline = compute_baseline(history, MarketSession.RTH)
    assert baseline is not None
    assert baseline.median == 300.0  # a mean here would be ~2.5mn


def test_empty_history_has_no_baseline() -> None:
    assert compute_baseline([], MarketSession.RTH) is None


def test_weekend_is_its_own_session() -> None:
    # The source snapshot was taken on Sunday 2026-08-09.
    assert classify_session(datetime(2026, 8, 9, 13, 0)) is MarketSession.CLOSED_WEEKEND


def test_us_holiday_on_a_weekday_is_its_own_session() -> None:
    holidays = frozenset({datetime(2026, 7, 3).date()})
    session = classify_session(datetime(2026, 7, 3, 17, 0), holidays)
    assert session is MarketSession.CLOSED_HOLIDAY


def test_regular_and_after_hours_are_separated() -> None:
    """13:30 UTC in August is 09:30 ET — the open. 21:00 UTC is 17:00 ET."""
    assert classify_session(datetime(2026, 8, 7, 13, 30)) is MarketSession.RTH
    assert classify_session(datetime(2026, 8, 7, 21, 0)) is MarketSession.AH
    assert classify_session(datetime(2026, 8, 7, 11, 0)) is MarketSession.PRE
    assert classify_session(datetime(2026, 8, 7, 2, 0)) is MarketSession.CLOSED_WEEKDAY


def test_session_boundaries_follow_dst_not_utc() -> None:
    """The same UTC clock time lands in different sessions across the DST boundary.

    14:00 UTC is 10:00 EDT in August (regular hours) but 09:00 EST in January
    (pre-market). Classifying in UTC would shift every boundary by an hour twice a
    year, silently mislabelling two weeks of history each time.
    """
    assert classify_session(datetime(2026, 8, 7, 14, 0)) is MarketSession.RTH
    assert classify_session(datetime(2026, 1, 7, 14, 0)) is MarketSession.PRE


def test_only_open_sessions_count_as_underlying_trading() -> None:
    assert is_underlying_open(MarketSession.RTH)
    assert is_underlying_open(MarketSession.PRE)
    assert not is_underlying_open(MarketSession.CLOSED_WEEKEND)
