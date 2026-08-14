"""Rolling baselines for anomaly detection.

Two decisions here carry most of the signal quality:

1. **Day-type stratification.** RWA tokens trade 24/7; their underlyings do not.
   Weekend turnover is structurally lower, so a single blended baseline fires on
   every Monday open. Baselines key on ``(entity, metric, day_type)``.

2. **Median + MAD instead of mean + stdev.** RWA volume is extremely right-skewed —
   in the 2026-08-09 snapshot the top 10 Binance TradFi contracts carried 78.2% of
   volume, and one contract (SPCX) carried 28.2% alone. A mean absorbs the spike it
   is supposed to detect; the median does not.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Sequence

#: Scaling constant making MAD a consistent estimator of stdev for normal data,
#: so the resulting score is interpretable on the familiar z-score scale.
_MAD_TO_SIGMA = 0.6745

#: Below this many observations the baseline is not trustworthy. Detectors record
#: but do not alert, so the first two weeks after deployment do not spam the feed.
MIN_SAMPLES_FOR_ALERT = 14


class DayType(StrEnum):
    """Trading-calendar bucket of the underlying market."""

    WEEKDAY = "weekday"
    WEEKEND = "weekend"
    US_HOLIDAY = "us_holiday"


def classify_day(when: datetime, us_holidays: frozenset[date] = frozenset()) -> DayType:
    """Bucket a snapshot timestamp by the underlying market's calendar."""
    if when.date() in us_holidays:
        return DayType.US_HOLIDAY
    if when.weekday() >= 5:  # Saturday, Sunday
        return DayType.WEEKEND
    return DayType.WEEKDAY


@dataclass(frozen=True, slots=True)
class Baseline:
    """Robust location and scale for one (entity, metric, day_type) series."""

    median: float
    mad: float
    sample_size: int
    day_type: DayType

    @property
    def is_alertable(self) -> bool:
        """Whether this baseline has enough history to justify firing an alert."""
        return self.sample_size >= MIN_SAMPLES_FOR_ALERT

    def robust_z(self, value: float) -> float:
        """Score ``value`` against this baseline on a z-like scale.

        A zero MAD means the series was flat across the whole window — common for
        dormant assets sitting at ~0 volume, which is exactly the population the
        cold-start detector cares about. Returning ``inf`` for any departure from a
        flat baseline is the honest answer: the deviation is unbounded in MAD units.
        Callers gate on absolute magnitude, so this does not by itself fire an alert.
        """
        if self.mad == 0:
            return 0.0 if value == self.median else float("inf")
        return _MAD_TO_SIGMA * (value - self.median) / self.mad


def compute_baseline(
    observations: Sequence[float], day_type: DayType
) -> Baseline | None:
    """Build a baseline from same-day-type historical observations.

    ``observations`` must already be filtered to one day type; mixing them is the
    failure mode this module exists to prevent. Returns ``None`` for an empty series.
    """
    if not observations:
        return None

    median = statistics.median(observations)
    mad = statistics.median([abs(x - median) for x in observations])
    return Baseline(
        median=median,
        mad=mad,
        sample_size=len(observations),
        day_type=day_type,
    )
