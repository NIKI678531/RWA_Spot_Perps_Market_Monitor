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

#: Reserve below which an implausible pool cannot distort anything worth reporting.
#: Same reasoning as the alert floor: being wrong about a $500 pool is not a finding,
#: and screening one costs more credibility than it saves.
POOL_RESERVE_FLOOR_USD = Decimal("50000000")

#: 24h turnover as a fraction of reserve, under which a large pool is not a market.
#: A pool holding $50mn and trading $500 against it is not thinly traded, it is
#: mispriced — reserve is denominated in a quote the source has valued wrongly.
#:
#: Measured, not chosen: at one snapshot exactly six pools cleared the floor above and
#: every one of them turned over less than 0.0008% of reserve, while the other 454
#: pools had a median of 0.0025% and an upper quartile above 10%. The gap between the
#: two groups is four orders of magnitude wide, so the threshold sits in empty space.
POOL_MIN_TURNOVER_RATIO = Decimal("0.00001")


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


@dataclass(frozen=True, slots=True)
class Pool:
    """A DEX pool for reserve screening.

    Pools carry no upstream quality flag the way CoinGecko pairs do, so the judgement
    has to be made here from the two numbers the source gives us.
    """

    pool_id: str
    reserve_usd: Decimal | None
    vol_24h: Decimal | None

    @property
    def is_flagged(self) -> bool:
        """Whether this pool's reserve is too implausible to aggregate.

        The observed failure: GeckoTerminal reported ``AAPLX / USDC`` on Solana at a
        reserve of $192.8bn against $0 of 24h volume. Four more pools like it summed
        to $426bn of "DEX liquidity" — more than the entire tokenized market — on the
        executive KPI. The number is the source's own, not a parsing error, so it
        cannot be fixed by reading more carefully.

        Note this is ``AAPLX``, not the xStocks ``AAPLx``, whose real pools hold about
        $140k. The case distinction that ``underlying_map`` is careful about is the
        same distinction here: one is a wrapper, the other is a token that borrowed
        the spelling.

        Absence of trading is what makes it decidable. A reserve is a claim about how
        much value is pooled; volume is a claim about how much of it moved. When the
        first is enormous and the second is zero, the second is the credible one,
        because trades are observed individually and reserves are inferred from a
        quote token's price.
        """
        if self.reserve_usd is None or self.reserve_usd < POOL_RESERVE_FLOOR_USD:
            return False
        # Unobserved volume is not zero volume, so it cannot convict the pool.
        if self.vol_24h is None:
            return False
        return self.vol_24h / self.reserve_usd < POOL_MIN_TURNOVER_RATIO


def screen_pools(pools: Iterable[Pool]) -> QualityScreen:
    """Split DEX reserves into raw and plausibility-adjusted totals.

    Deliberately the same shape as ``screen``: both figures are reported and the gap
    between them is the finding. Dropping the flagged pools outright would hide that
    the source claims $426bn, and reporting only raw would put a number on the
    dashboard that is wrong by four orders of magnitude.
    """
    pool_list = list(pools)
    observed = [p for p in pool_list if p.reserve_usd is not None]
    clean = [p for p in observed if not p.is_flagged]

    def total(subset: list[Pool]) -> ScopedValue:
        if not subset:
            return ScopedValue(
                amount=None, scope=MetricScope.DEX_LIQUIDITY, verified=False
            )
        amount = sum(
            (p.reserve_usd for p in subset if p.reserve_usd is not None),
            start=Decimal(0),
        )
        return ScopedValue(
            amount=amount,
            scope=MetricScope.DEX_LIQUIDITY,
            verified=len(observed) == len(pool_list),
        )

    return QualityScreen(
        raw=total(observed),
        adjusted=total(clean),
        total_pairs=len(pool_list),
        flagged_pairs=sum(1 for p in observed if p.is_flagged),
        unverified_pairs=len(pool_list) - len(observed),
    )


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
