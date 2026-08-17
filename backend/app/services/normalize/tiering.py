"""Assign ``rwa_tier``, the gate on every statistic in the system.

A "tokenized RWA market size" that quietly includes crypto-native tokens is not a
market size. This module decides what counts, once, in one place, so the decision is
reviewable instead of being re-litigated inside each rollup.

``NON_RWA`` is not a rejection bucket — those rows are stored and displayed as
benchmark reference. They are simply never summed into an RWA total.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import RwaTier

#: Issuers that custody or hold a receipt claim on the real security. Membership
#: here is a legal-structure judgement, not a data judgement, so it is an explicit
#: list rather than a heuristic.
CUSTODIED_ISSUERS = frozenset(
    {
        "xStocks",
        "bStocks",
        "Ondo",
        "Dinari",
        "Swarm",
        "Backed",
    }
)

#: Tokens that fund, govern or service RWA issuance without being tokenized exposure
#: themselves. Counting an issuer's governance token as tokenized stock inflates the
#: market by the size of its own equity story.
ADJACENT_SYMBOLS = frozenset(
    {
        "ONDO",
        "RWA",
        "POLYX",
        "CFG",
        "TRU",
        "MPL",
    }
)


@dataclass(frozen=True, slots=True)
class TierDecision:
    """A tier plus the reason, so the classification can be defended."""

    tier: RwaTier
    reason: str

    @property
    def in_scope(self) -> bool:
        return self.tier is not RwaTier.NON_RWA


def classify_tier(
    *,
    symbol: str,
    issuer_id: str | None,
    underlying_id: str | None,
    is_perpetual: bool = False,
) -> TierDecision:
    """Decide how close an asset sits to a real backed claim.

    Order matters. Perpetuals are checked first because a perp on SPY resolves to a
    real underlying and would otherwise be indistinguishable from a custodied token —
    but nobody holds a claim on a share, so it cannot count toward tokenized market
    capitalisation.
    """
    if is_perpetual:
        return TierDecision(
            RwaTier.SYNTHETIC,
            "perpetual exposure: tracks the underlying without any custody claim",
        )

    if symbol.upper() in ADJACENT_SYMBOLS:
        return TierDecision(
            RwaTier.RWA_ADJACENT,
            "RWA infrastructure or governance token, not tokenized exposure",
        )

    if underlying_id is None:
        return TierDecision(
            RwaTier.NON_RWA, "no resolved underlying security; benchmark only"
        )

    if issuer_id in CUSTODIED_ISSUERS:
        return TierDecision(
            RwaTier.CORE_RWA, f"custodied or receipt-backed wrapper from {issuer_id}"
        )

    # It resolves to a real security but we cannot name a custodian. Treating that as
    # CORE would let any symbol collision inflate the headline number.
    return TierDecision(
        RwaTier.SYNTHETIC,
        "resolves to a real underlying but the backing structure is unverified",
    )
