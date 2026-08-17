"""Rolling baselines for anomaly detection.

Two decisions here carry most of the signal quality:

1. **Session stratification.** RWA tokens trade 24/7; their underlyings do not.
   Weekend turnover is structurally lower than weekday turnover, and after-hours
   turnover is structurally lower than regular-hours turnover. A single blended
   baseline fires on every Monday open and every US close, so baselines key on
   ``(entity, metric, market_session)``.

2. **Median + MAD instead of mean + stdev.** RWA volume is extremely right-skewed —
   in the 2026-08-09 snapshot the top 10 Binance TradFi contracts carried 78.2% of
   volume, and one contract (SPCX) carried 28.2% alone. A mean absorbs the spike it
   is supposed to detect; the median does not.

See ``docs/adr/0002-baseline-stratification-and-robust-statistics.md``.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from app.core.sessions import MarketSession

#: Scaling constant making MAD a consistent estimator of stdev for normal data,
#: so the resulting score is interpretable on the familiar z-score scale.
_MAD_TO_SIGMA = 0.6745

#: Below this many *same-session* observations the baseline is not trustworthy.
#: Detectors record but do not alert, so the first two weeks after deployment do not
#: spam the feed. Cross-sectional detectors (X1-X7) exist to cover this window.
MIN_SAMPLES_FOR_ALERT = 14


@dataclass(frozen=True, slots=True)
class Baseline:
    """Robust location and scale for one (entity, metric, market_session) series."""

    median: float
    mad: float
    sample_size: int
    market_session: MarketSession

    @property
    def is_alertable(self) -> bool:
        """Whether this baseline has enough history to justify firing an alert."""
        return self.sample_size >= MIN_SAMPLES_FOR_ALERT

    def robust_z(self, value: float) -> float:
        """Score ``value`` against this baseline on a z-like scale.

        A zero MAD means the series was flat across the whole window — common for
        dormant assets sitting at ~0 volume, which is exactly the population the
        cold-start detector cares about. Returning an infinite score for any departure
        from a flat baseline is the honest answer: the deviation is unbounded in MAD
        units. Callers gate on absolute magnitude, so this does not by itself fire an
        alert.

        The infinity keeps the *sign* of the move. An unsigned ``inf`` would make a
        collapse to zero score identically to a spike, and every detector here is
        directional — "nobody traded this, now everyone does" is the finding, not
        "something changed".
        """
        if self.mad == 0:
            if value == self.median:
                return 0.0
            return float("inf") if value > self.median else float("-inf")
        return _MAD_TO_SIGMA * (value - self.median) / self.mad


def compute_baseline(
    observations: Sequence[float], market_session: MarketSession
) -> Baseline | None:
    """Build a baseline from observations taken in the same market session.

    ``observations`` must already be filtered to one session; mixing them is the
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
        market_session=market_session,
    )
