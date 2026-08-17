"""T2 · Cold-start awakening detector.

Fires when a product that essentially nobody traded starts trading meaningfully —
"原先没有人买的产品现在突然多了很多人买". This is the flagship detector: it surfaces
genuinely new demand rather than fluctuation in demand that already existed.

Deliberately *not* implemented as a percentage change. Going from $200 to $4,000 is
+1900% and commercially irrelevant; the dormancy floor plus the absolute awakening
threshold together encode "was asleep" and "is now material" as separate conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.core.metrics import MetricScope
from app.services.analytics.baseline import Baseline

#: A product counts as dormant while every observation in the lookback sits at or
#: below this. Not zero — dust trades and rounding keep truly idle pairs slightly
#: above nil, and a strict zero test would miss most real awakenings.
DORMANCY_CEILING_USD = 1_000.0

#: Minimum current turnover for an awakening to be worth management attention.
AWAKENING_FLOOR_USD = 100_000.0

#: Share of the lookback window that must sit under the dormancy ceiling. Below 1.0
#: so a single unrepresentative print does not disqualify an otherwise idle series.
DORMANCY_RATIO = 0.9


@dataclass(frozen=True, slots=True)
class AwakeningSignal:
    """Evidence for one detected awakening.

    Every field here lands in ``alert_evidence``. An alert that cannot be justified
    to management on inspection is noise, so the detector emits the inputs to its
    own decision rather than just a verdict.
    """

    entity_id: str
    metric_scope: MetricScope
    current_value: float
    baseline: Baseline
    dormant_observations: int
    total_observations: int
    multiple_of_baseline: float

    @property
    def headline_zh(self) -> str:
        return (
            f"{self.entity_id} 24h 成交从近 {self.total_observations} 个快照的"
            f"中位数 ${self.baseline.median:,.0f} 跃升至 ${self.current_value:,.0f}"
        )


def detect(
    entity_id: str,
    current_value: float,
    history: Sequence[float],
    baseline: Baseline,
    metric_scope: MetricScope = MetricScope.SPOT_VOLUME,
) -> AwakeningSignal | None:
    """Return a signal if ``entity_id`` just woke up, else ``None``.

    ``history`` and ``baseline`` must both be restricted to the same market session
    as the current observation — comparing a regular-hours print against a baseline
    that includes weekends is the single largest source of false positives here.
    """
    if not baseline.is_alertable:
        # Insufficient history. Record upstream, but do not alert on a baseline we
        # cannot stand behind.
        return None

    if current_value < AWAKENING_FLOOR_USD:
        return None

    if not history:
        return None

    dormant = sum(1 for v in history if v <= DORMANCY_CEILING_USD)
    if dormant / len(history) < DORMANCY_RATIO:
        # It was already trading. A jump here is a volume spike, which is T1's job —
        # keeping the two separate stops "grew 3x" and "came from nothing" landing in
        # the feed as the same kind of event.
        return None

    return AwakeningSignal(
        entity_id=entity_id,
        metric_scope=metric_scope,
        current_value=current_value,
        baseline=baseline,
        dormant_observations=dormant,
        total_observations=len(history),
        multiple_of_baseline=(
            current_value / baseline.median if baseline.median > 0 else float("inf")
        ),
    )
