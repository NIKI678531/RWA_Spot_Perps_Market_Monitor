"""Group observations into venue, issuer, underlying and theme totals.

Two rules are enforced here rather than left to callers, because both produce
numbers that look right when broken:

1. Every rollup runs within one ``MetricScope``. Grouping is not the same as
   summing, and the sum still goes through ``safe_sum``.
2. ``NON_RWA`` rows are excluded from RWA totals by default. They remain available
   as benchmark reference, but a tokenized-RWA market size that includes crypto-
   native tokens is not a market size.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.metrics import (
    MetricScope,
    MetricScopeViolation,
    RatioScope,
    RatioValue,
    ScopedValue,
    safe_sum,
)
from app.models.enums import IN_SCOPE_TIERS, RwaTier
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Contribution:
    """One observation, tagged with the group it belongs to and its tier."""

    group_id: str
    value: Decimal | None
    scope: MetricScope
    rwa_tier: RwaTier = RwaTier.CORE_RWA
    verified: bool = True

    def as_scoped(self) -> ScopedValue:
        return ScopedValue(
            amount=self.value,
            scope=self.scope,
            verified=self.verified and self.value is not None,
        )


@dataclass(frozen=True, slots=True)
class RollupRow:
    """One group's total within one scope."""

    group_id: str
    total: ScopedValue
    contributor_count: int
    unverified_count: int


@dataclass(frozen=True, slots=True)
class Rollup:
    """A complete grouping, ordered largest first."""

    scope: MetricScope
    rows: tuple[RollupRow, ...]
    grand_total: ScopedValue
    #: Contributions dropped by the tier gate. Reported rather than hidden: a large
    #: number here means the scope decision is doing real work and should be visible.
    excluded_out_of_scope: int

    def share_of(self, group_id: str) -> RatioValue:
        """One group's share of the grand total, as a ratio that cannot be summed."""
        grand = self.grand_total.amount
        row = next((r for r in self.rows if r.group_id == group_id), None)
        if row is None or grand is None or grand == 0 or row.total.amount is None:
            return RatioValue(
                value=None,
                scope=RatioScope.VENUE_SHARE,
                weight_basis=self.scope,
                verified=False,
            )
        return RatioValue(
            value=row.total.amount / grand,
            scope=RatioScope.VENUE_SHARE,
            weight_basis=self.scope,
            verified=row.total.verified and self.grand_total.verified,
        )


def rollup(
    contributions: Sequence[Contribution], *, include_out_of_scope: bool = False
) -> Rollup:
    """Group and total contributions that share one metric scope.

    Raises ``MetricScopeViolation`` on a mixed-scope input rather than silently
    picking one, because the resulting chart would be readable and wrong.
    """
    if not contributions:
        raise MetricScopeViolation("cannot infer a metric scope from zero values")

    scopes = {c.scope for c in contributions}
    if len(scopes) > 1:
        raise MetricScopeViolation(
            "refusing to roll up across metric scopes: "
            + ", ".join(sorted(s.value for s in scopes))
        )
    scope = scopes.pop()

    if include_out_of_scope:
        kept = list(contributions)
    else:
        kept = [c for c in contributions if c.rwa_tier in IN_SCOPE_TIERS]

    excluded = len(contributions) - len(kept)
    if not kept:
        return Rollup(
            scope=scope,
            rows=(),
            grand_total=ScopedValue(amount=None, scope=scope, verified=False),
            excluded_out_of_scope=excluded,
        )

    grouped: dict[str, list[Contribution]] = {}
    for contribution in kept:
        grouped.setdefault(contribution.group_id, []).append(contribution)

    rows = [
        RollupRow(
            group_id=group_id,
            total=safe_sum([c.as_scoped() for c in members]),
            contributor_count=len(members),
            unverified_count=sum(
                1 for c in members if not c.verified or c.value is None
            ),
        )
        for group_id, members in grouped.items()
    ]
    # Unverified groups sort last: a group whose total is unknown has no defensible
    # position in a ranking, and placing it at zero would imply one.
    rows.sort(key=lambda r: (r.total.amount is None, -(r.total.amount or Decimal(0))))

    return Rollup(
        scope=scope,
        rows=tuple(rows),
        grand_total=safe_sum([c.as_scoped() for c in kept]),
        excluded_out_of_scope=excluded,
    )
