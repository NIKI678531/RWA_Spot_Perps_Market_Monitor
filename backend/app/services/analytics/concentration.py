"""Concentration measures: HHI and Top-N share.

The competitive question this system exists to answer — "which venue and which
product is hottest" — is not answered by a ranking alone. A market where the top
venue holds 30% and one where it holds 85% look identical as an ordered list and are
completely different businesses to enter.

Reference from the source data: the top 10 Binance TradFi perpetual contracts carry
78.2% of volume, and SPCX alone carries 28.2%.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from app.core.metrics import (
    MetricScope,
    MetricScopeViolation,
    RatioScope,
    RatioValue,
    ScopedValue,
    safe_sum,
)


@dataclass(frozen=True, slots=True)
class Share:
    """One competitor's slice of a single-scope total."""

    entity_id: str
    value: Decimal
    share: Decimal


@dataclass(frozen=True, slots=True)
class Concentration:
    """Concentration of one metric across one set of competitors."""

    scope: MetricScope
    shares: tuple[Share, ...]
    #: Herfindahl-Hirschman Index on a 0-10000 scale, the convention competition
    #: authorities use. Below 1500 is unconcentrated, above 2500 is concentrated.
    hhi: Decimal
    total: ScopedValue
    #: Competitors whose value was never observed. Excluded from the denominator, so
    #: shares describe the observed market rather than a market padded with zeros.
    unverified_count: int

    def top_n_share(self, n: int) -> RatioValue:
        """Combined share of the largest ``n`` competitors."""
        top = sum((s.share for s in self.shares[:n]), start=Decimal(0))
        return RatioValue(
            value=top,
            scope=RatioScope.CONCENTRATION,
            weight_basis=self.scope,
            verified=self.unverified_count == 0,
        )

    @property
    def is_concentrated(self) -> bool:
        return self.hhi >= 2500


def compute(values: Sequence[ScopedValue], entity_ids: Sequence[str]) -> Concentration:
    """Rank competitors and measure how concentrated the metric is among them.

    All values must share one metric scope. Mixing spot volume with open interest
    here would produce an HHI describing nothing, which is worse than an error
    because it still prints a number.
    """
    if len(values) != len(entity_ids):
        raise MetricScopeViolation("each value needs exactly one entity id")
    if not values:
        raise MetricScopeViolation("cannot measure concentration of zero competitors")

    scopes = {v.scope for v in values}
    if len(scopes) > 1:
        raise MetricScopeViolation(
            "refusing to measure concentration across metric scopes: "
            + ", ".join(sorted(s.value for s in scopes))
        )
    scope = scopes.pop()

    observed = [
        (eid, v.amount)
        for eid, v in zip(entity_ids, values)
        if v.verified and v.amount is not None
    ]
    # Through the sanctioned path, so a scope mix raises rather than prints.
    total = safe_sum(values)
    denominator = sum((amount for _, amount in observed), start=Decimal(0))

    if denominator <= 0:
        # Every observed competitor is at zero. That is a real market state, not an
        # error, but no share is definable and HHI is undefined rather than 0.
        return Concentration(
            scope=scope,
            shares=tuple(Share(eid, amount, Decimal(0)) for eid, amount in observed),
            hhi=Decimal(0),
            total=total,
            unverified_count=len(values) - len(observed),
        )

    shares = tuple(
        sorted(
            (Share(eid, amount, amount / denominator) for eid, amount in observed),
            key=lambda s: s.value,
            reverse=True,
        )
    )
    hhi = sum((s.share * s.share for s in shares), start=Decimal(0)) * Decimal(10_000)

    return Concentration(
        scope=scope,
        shares=shares,
        hhi=hhi,
        total=total,
        unverified_count=len(values) - len(observed),
    )
