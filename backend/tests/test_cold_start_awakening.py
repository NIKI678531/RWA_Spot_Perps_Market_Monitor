"""Tests for the flagship anomaly detector and its day-type-stratified baseline."""

from datetime import datetime

from app.core.metrics import MetricScope
from app.services.analytics.baseline import (
    DayType,
    classify_day,
    compute_baseline,
)
from app.services.anomaly.detectors import cold_start_awakening as det


def _dormant_history(n: int = 20, level: float = 300.0) -> list[float]:
    return [level] * n


def test_detects_a_genuine_awakening() -> None:
    """A dormant product suddenly trading materially is the core signal."""
    history = _dormant_history()
    baseline = compute_baseline(history, DayType.WEEKDAY)
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
    baseline = compute_baseline(history, DayType.WEEKDAY)
    assert baseline is not None

    assert det.detect("DUSTB", 4_000.0, history, baseline) is None


def test_ignores_products_that_were_already_trading() -> None:
    """An already-liquid product jumping is a volume spike, not an awakening."""
    history = [5_000_000.0] * 20
    baseline = compute_baseline(history, DayType.WEEKDAY)
    assert baseline is not None

    assert det.detect("SPYB", 15_000_000.0, history, baseline) is None


def test_tolerates_one_unrepresentative_print_in_the_dormant_window() -> None:
    history = _dormant_history(19) + [80_000.0]
    baseline = compute_baseline(history, DayType.WEEKDAY)
    assert baseline is not None

    signal = det.detect("QQQB", 500_000.0, history, baseline)
    assert signal is not None
    assert signal.dormant_observations == 19


def test_does_not_alert_before_baseline_has_enough_history() -> None:
    """Cold-start period records but stays silent, so week one is not a spam feed."""
    history = _dormant_history(5)
    baseline = compute_baseline(history, DayType.WEEKDAY)
    assert baseline is not None
    assert not baseline.is_alertable

    assert det.detect("NEWB", 900_000.0, history, baseline) is None


def test_flat_series_yields_infinite_score_rather_than_dividing_by_zero() -> None:
    baseline = compute_baseline([300.0] * 20, DayType.WEEKDAY)
    assert baseline is not None
    assert baseline.mad == 0
    assert baseline.robust_z(362_076.0) == float("inf")
    assert baseline.robust_z(300.0) == 0.0


def test_median_resists_the_spike_a_mean_would_absorb() -> None:
    """Right-skewed volume is why the baseline is median + MAD, not mean + stdev."""
    history = [300.0] * 19 + [50_000_000.0]
    baseline = compute_baseline(history, DayType.WEEKDAY)
    assert baseline is not None
    assert baseline.median == 300.0  # a mean here would be ~2.5mn


def test_day_classification_separates_weekend_from_weekday() -> None:
    # The source snapshot was taken on Sunday 2026-08-09.
    assert classify_day(datetime(2026, 8, 9, 13, 0)) is DayType.WEEKEND
    assert classify_day(datetime(2026, 8, 7, 13, 0)) is DayType.WEEKDAY


def test_us_holiday_is_its_own_bucket() -> None:
    holidays = frozenset({datetime(2026, 7, 3).date()})
    assert classify_day(datetime(2026, 7, 3, 13, 0), holidays) is DayType.US_HOLIDAY


def test_empty_history_has_no_baseline() -> None:
    assert compute_baseline([], DayType.WEEKDAY) is None
