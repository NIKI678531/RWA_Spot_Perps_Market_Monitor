"""Metric scope isolation.

The single most important invariant in this system: market cap, spot volume, DEX
liquidity, perpetual volume and open interest are five *different kinds of number*.
Summing across them produces a figure that looks authoritative and means nothing.

The source workbook enforces this with a written note ("任何汇总页都只能并列，不得相加").
Notes do not survive a refactor. This module makes the rule a type error instead.

Ratios get a separate type. A ratio cannot be summed even within one scope — adding
two market shares or two turnover rates is meaningless regardless of what they
measure — so ``RatioValue`` is deliberately not accepted by :func:`safe_sum` and
must go through :func:`weighted_avg` with an explicit weighting basis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Mapping, Sequence


class MetricDimension(StrEnum):
    """What kind of quantity a metric is, independent of what it measures."""

    STOCK = "stock"  # a level at a point in time
    FLOW = "flow"  # an amount accumulated over a window
    RATIO = "ratio"  # a proportion; never additive under any circumstances


class MetricScope(StrEnum):
    """The five non-additive metric families."""

    SPOT_MARKET_CAP = "spot_market_cap"  # stock: tokenized market capitalisation
    SPOT_VOLUME = "spot_volume"  # flow: 24h spot turnover
    DEX_LIQUIDITY = "dex_liquidity"  # stock: pool reserves / TVL
    PERP_VOLUME = "perp_volume"  # flow: 24h perpetual turnover
    PERP_OI = "perp_oi"  # stock: open interest notional


class RatioScope(StrEnum):
    """Named ratios the system computes. All are ``MetricDimension.RATIO``."""

    TURNOVER = "turnover"  # spot volume / market cap
    BUY_RATIO = "buy_ratio"  # DEX buys / (buys + sells)
    VOL_LIQ = "vol_liq"  # spot volume / pool reserves
    VENUE_SHARE = "venue_share"
    ISSUER_SHARE = "issuer_share"
    THEME_SHARE = "theme_share"
    SPREAD = "spread"
    SLIPPAGE = "slippage"
    FUNDING_RATE = "funding_rate"
    BASIS = "basis"  # token price vs underlying reference price
    CONCENTRATION = "concentration"  # HHI or Top-N share


#: Which dimension each scope belongs to. Mixing a stock and a flow on one chart
#: axis is an error even though both happen to be denominated in USD.
SCOPE_DIMENSION: Mapping[MetricScope, MetricDimension] = {
    MetricScope.SPOT_MARKET_CAP: MetricDimension.STOCK,
    MetricScope.DEX_LIQUIDITY: MetricDimension.STOCK,
    MetricScope.PERP_OI: MetricDimension.STOCK,
    MetricScope.SPOT_VOLUME: MetricDimension.FLOW,
    MetricScope.PERP_VOLUME: MetricDimension.FLOW,
}

STOCK_SCOPES = frozenset(
    s for s, d in SCOPE_DIMENSION.items() if d is MetricDimension.STOCK
)
FLOW_SCOPES = frozenset(
    s for s, d in SCOPE_DIMENSION.items() if d is MetricDimension.FLOW
)


class MetricScopeViolation(ValueError):
    """Raised when an aggregation would combine incompatible metric scopes."""


@dataclass(frozen=True, slots=True)
class ScopedValue:
    """A USD amount that knows which metric family it belongs to.

    ``verified=False`` marks a missing observation. It is *not* zero — a failed or
    rate-limited fetch means we do not know the value, and coercing it to 0 silently
    understates every aggregate it flows into.
    """

    amount: Decimal | None
    scope: MetricScope
    verified: bool = True

    def __post_init__(self) -> None:
        if self.verified and self.amount is None:
            raise ValueError("a verified ScopedValue must carry an amount")

    @property
    def dimension(self) -> MetricDimension:
        return SCOPE_DIMENSION[self.scope]


@dataclass(frozen=True, slots=True)
class RatioValue:
    """A proportion, carrying the basis it must be weighted by when averaged.

    ``weight_basis`` is not decoration. "Average venue share" weighted by pair count
    and weighted by traded volume are different numbers, and only one of them answers
    the question being asked. Recording the basis forces the choice to be deliberate.
    """

    value: Decimal | None
    scope: RatioScope
    weight_basis: MetricScope
    verified: bool = True

    def __post_init__(self) -> None:
        if self.verified and self.value is None:
            raise ValueError("a verified RatioValue must carry a value")

    @property
    def dimension(self) -> MetricDimension:
        return MetricDimension.RATIO


def safe_sum(values: Sequence[ScopedValue]) -> ScopedValue:
    """Sum values that share a metric scope.

    Raises ``MetricScopeViolation`` if the inputs span more than one scope. This is
    the only sanctioned aggregation entry point; do not sum ``.amount`` directly.

    Unverified inputs are skipped rather than treated as zero, and the result is
    marked unverified so the caller can render it as partial rather than complete.
    """
    if not values:
        raise MetricScopeViolation("cannot infer a metric scope from zero values")

    if any(isinstance(v, RatioValue) for v in values):
        raise MetricScopeViolation(
            "ratios are never additive; use weighted_avg() with an explicit basis"
        )

    scopes = {v.scope for v in values}
    if len(scopes) > 1:
        raise MetricScopeViolation(
            "refusing to add across metric scopes: "
            + ", ".join(sorted(s.value for s in scopes))
        )

    scope = scopes.pop()
    observed = [v.amount for v in values if v.verified and v.amount is not None]
    if not observed:
        return ScopedValue(amount=None, scope=scope, verified=False)

    return ScopedValue(
        amount=sum(observed, start=Decimal(0)),
        scope=scope,
        # Partial coverage is still partial. Do not present it as a complete total.
        verified=len(observed) == len(values),
    )


def weighted_avg(
    values: Sequence[RatioValue], weights: Sequence[ScopedValue]
) -> RatioValue:
    """Combine ratios by weighting them, the only valid way to aggregate a ratio.

    Every weight must sit in the scope the ratios declare as their ``weight_basis``,
    so a set of volume-weighted shares cannot be accidentally combined using market
    caps. A pair is dropped if either side is unverified, and the result is marked
    unverified when anything was dropped.
    """
    if not values:
        raise MetricScopeViolation("cannot average zero ratios")
    if len(values) != len(weights):
        raise MetricScopeViolation("each ratio needs exactly one weight")

    scopes = {v.scope for v in values}
    if len(scopes) > 1:
        raise MetricScopeViolation(
            "refusing to average across ratio kinds: "
            + ", ".join(sorted(s.value for s in scopes))
        )
    scope = scopes.pop()

    bases = {v.weight_basis for v in values}
    if len(bases) > 1:
        raise MetricScopeViolation(
            "ratios disagree on weighting basis: "
            + ", ".join(sorted(b.value for b in bases))
        )
    basis = bases.pop()

    wrong_basis = {w.scope for w in weights if w.scope is not basis}
    if wrong_basis:
        raise MetricScopeViolation(
            f"weights must be in {basis.value}, got "
            + ", ".join(sorted(s.value for s in wrong_basis))
        )

    pairs = [
        (v.value, w.amount)
        for v, w in zip(values, weights)
        if v.verified and w.verified and v.value is not None and w.amount is not None
    ]
    total_weight = sum((w for _, w in pairs), start=Decimal(0))
    if not pairs or total_weight == 0:
        return RatioValue(value=None, scope=scope, weight_basis=basis, verified=False)

    numerator = sum((v * w for v, w in pairs), start=Decimal(0))
    return RatioValue(
        value=numerator / total_weight,
        scope=scope,
        weight_basis=basis,
        verified=len(pairs) == len(values),
    )


def assert_same_axis(scopes: Sequence[MetricScope]) -> None:
    """Guard for chart builders: refuse to put stocks and flows on one Y axis.

    Both are denominated in USD, which makes the mistake easy to make and hard to
    see. A $10.94bn open-interest bar next to a $4.44bn volume bar invites the
    reader to compare two quantities that are not comparable.
    """
    distinct = set(scopes)
    if len(distinct) <= 1:
        return
    if distinct & STOCK_SCOPES and distinct & FLOW_SCOPES:
        raise MetricScopeViolation(
            "stock and flow metrics cannot share a Y axis: "
            + ", ".join(sorted(s.value for s in distinct))
        )
