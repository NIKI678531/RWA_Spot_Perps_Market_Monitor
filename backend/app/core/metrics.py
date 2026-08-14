"""Metric scope isolation.

The single most important invariant in this system: market cap, spot volume, DEX
liquidity, perpetual volume and open interest are five *different kinds of number*.
Summing across them produces a figure that looks authoritative and means nothing.

The source workbook enforces this with a written note ("任何汇总页都只能并列，不得相加").
Notes do not survive a refactor. This module makes the rule a type error instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Sequence


class MetricScope(StrEnum):
    """The five non-additive metric families."""

    SPOT_MARKET_CAP = "spot_market_cap"  # stock: tokenized market capitalisation
    SPOT_VOLUME = "spot_volume"  # flow: 24h spot turnover
    DEX_LIQUIDITY = "dex_liquidity"  # stock: pool reserves / TVL
    PERP_VOLUME = "perp_volume"  # flow: 24h perpetual turnover
    PERP_OI = "perp_oi"  # stock: open interest notional


#: Scopes that measure a *stock* (a level at a point in time) rather than a *flow*
#: (an amount over a window). Mixing the two on one axis is a charting error even
#: when the units happen to both be USD.
STOCK_SCOPES = frozenset(
    {MetricScope.SPOT_MARKET_CAP, MetricScope.DEX_LIQUIDITY, MetricScope.PERP_OI}
)
FLOW_SCOPES = frozenset({MetricScope.SPOT_VOLUME, MetricScope.PERP_VOLUME})


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


def safe_sum(values: Sequence[ScopedValue]) -> ScopedValue:
    """Sum values that share a metric scope.

    Raises ``MetricScopeViolation`` if the inputs span more than one scope. This is
    the only sanctioned aggregation entry point; do not sum ``.amount`` directly.

    Unverified inputs are skipped rather than treated as zero, and the result is
    marked unverified so the caller can render it as partial rather than complete.
    """
    if not values:
        raise MetricScopeViolation("cannot infer a metric scope from zero values")

    scopes = {v.scope for v in values}
    if len(scopes) > 1:
        raise MetricScopeViolation(
            "refusing to add across metric scopes: "
            + ", ".join(sorted(s.value for s in scopes))
        )

    scope = scopes.pop()
    observed = [v for v in values if v.verified and v.amount is not None]
    if not observed:
        return ScopedValue(amount=None, scope=scope, verified=False)

    return ScopedValue(
        amount=sum((v.amount for v in observed), start=Decimal(0)),
        scope=scope,
        # Partial coverage is still partial. Do not present it as a complete total.
        verified=len(observed) == len(values),
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
