"""Quality screening: raw and adjusted turnover, side by side.

Reference case from the source data: Native (BSC) reports about $29.3mn of 24h
turnover, of which 17 of 19 pairs carry a data-hygiene flag. Excluding them leaves
about $216. Publishing $29.3mn alone overstates the venue by five orders of
magnitude; publishing $216 alone hides that the venue claims otherwise. Both numbers
are reported, always, and the gap between them is itself the finding.

"Quality flag" here means CoinGecko's assessment of the *quote* — anomalous or stale
pricing. It is not a demand anomaly. The two must not be conflated; see CONTEXT.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Protocol

from app.core.metrics import MetricScope, ScopedValue


class QualityFlagged(Protocol):
    """Whatever a caller passes must at least declare these three things."""

    volume_usd: Decimal | None
    is_quality_anomaly: bool
    is_quality_stale: bool


@dataclass(frozen=True, slots=True)
class Pair:
    """A minimal spot pair for screening."""

    pair_id: str
    volume_usd: Decimal | None
    is_quality_anomaly: bool = False
    is_quality_stale: bool = False

    @property
    def is_flagged(self) -> bool:
        return self.is_quality_anomaly or self.is_quality_stale


@dataclass(frozen=True, slots=True)
class QualityScreen:
    """The paired result. Neither figure is meaningful without the other."""

    raw: ScopedValue
    adjusted: ScopedValue
    total_pairs: int
    flagged_pairs: int
    #: Pairs whose volume was never observed. Distinct from pairs observed at zero.
    unverified_pairs: int

    @property
    def flagged_share(self) -> float:
        """Fraction of pairs excluded from the adjusted figure."""
        if self.total_pairs == 0:
            return 0.0
        return self.flagged_pairs / self.total_pairs

    @property
    def is_materially_divergent(self) -> bool:
        """Whether raw and adjusted disagree enough to warrant a UI warning.

        Set at an order of magnitude rather than a percentage: a venue whose adjusted
        turnover is 90% of raw is normal, one where it is 0.001% is telling a
        different story about itself than its data supports.
        """
        raw_amount = self.raw.amount
        adjusted_amount = self.adjusted.amount
        if raw_amount is None or raw_amount == 0:
            return False
        if adjusted_amount is None:
            # Every observed pair was flagged, so there is no adjusted figure at all.
            # That is the widest divergence there is, not the absence of one.
            return True
        return adjusted_amount < raw_amount / 10


def screen(
    pairs: Iterable[Pair], scope: MetricScope = MetricScope.SPOT_VOLUME
) -> QualityScreen:
    """Split turnover into raw and quality-adjusted totals.

    A pair with no observed volume is counted as unverified and left out of both
    sums, which marks the totals partial. It is never counted as zero — that would
    make a broken feed look like an idle venue.
    """
    pair_list = list(pairs)
    observed = [p for p in pair_list if p.volume_usd is not None]
    clean = [p for p in observed if not p.is_flagged]

    def total(subset: list[Pair]) -> ScopedValue:
        if not subset:
            return ScopedValue(amount=None, scope=scope, verified=False)
        amount = sum(
            (p.volume_usd for p in subset if p.volume_usd is not None),
            start=Decimal(0),
        )
        # Verified only when nothing in the input was missing. Partial coverage stays
        # visibly partial rather than being presented as a complete total.
        return ScopedValue(
            amount=amount, scope=scope, verified=len(observed) == len(pair_list)
        )

    return QualityScreen(
        raw=total(observed),
        adjusted=total(clean),
        total_pairs=len(pair_list),
        flagged_pairs=sum(1 for p in observed if p.is_flagged),
        unverified_pairs=len(pair_list) - len(observed),
    )
