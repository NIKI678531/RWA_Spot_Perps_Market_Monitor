"""X4 · A trading pair appearing for the first time.

A competitor listing a product on a new venue is a distribution decision, and it is
visible the moment it happens — no history of the pair itself is required, only the
knowledge that we have not seen it before.

The volume floor matters more here than elsewhere. Venues list pairs constantly; a
listing that attracts nothing is an announcement, not demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import AbstractSet, Sequence

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import DetectorFamily, EntityType, RwaTier
from app.services.anomaly.signals import Evidence, Signal

DETECTOR = "X4"
RULE_NAME = "NewPairListing"

MIN_FIRST_VOLUME_USD = Decimal(50_000)


@dataclass(frozen=True, slots=True)
class PairObservation:
    """One (asset, venue) pair as of the current snapshot."""

    asset_id: str
    venue_id: str
    adjusted_vol_24h: Decimal | None
    rwa_tier: RwaTier = RwaTier.CORE_RWA

    @property
    def pair_key(self) -> tuple[str, str]:
        return (self.asset_id, self.venue_id)


def detect(
    observations: Sequence[PairObservation],
    known_pairs: AbstractSet[tuple[str, str]],
    market_session: MarketSession,
) -> list[Signal]:
    """Flag pairs absent from ``known_pairs``, the set seen in prior snapshots.

    Uses adjusted rather than raw volume: a new listing whose entire volume carries a
    quality flag is a venue populating a book, not a market arriving.
    """
    signals: list[Signal] = []

    for pair in observations:
        if pair.rwa_tier is RwaTier.NON_RWA:
            continue
        if pair.pair_key in known_pairs:
            continue
        if (
            pair.adjusted_vol_24h is None
            or pair.adjusted_vol_24h < MIN_FIRST_VOLUME_USD
        ):
            continue

        signals.append(
            Signal(
                detector=DETECTOR,
                family=DetectorFamily.CROSS_SECTIONAL,
                entity_type=EntityType.PAIR,
                entity_id=f"{pair.asset_id}@{pair.venue_id}",
                metric_scope=MetricScope.SPOT_VOLUME,
                market_session=market_session,
                headline_zh=(
                    f"{pair.venue_id} 新上架 {pair.asset_id}，"
                    f"首次观测质量调整后成交 ${float(pair.adjusted_vol_24h):,.0f}"
                ),
                notional_usd=pair.adjusted_vol_24h,
                rwa_tier=pair.rwa_tier,
                evidence=Evidence(
                    rule_name=RULE_NAME,
                    observed_value=pair.adjusted_vol_24h,
                    # No deviation term: there is no prior state to deviate from, so
                    # severity rests on magnitude and persistence alone.
                    robust_z=None,
                    extra={
                        "asset_id": pair.asset_id,
                        "venue_id": pair.venue_id,
                        "known_pair_count": len(known_pairs),
                    },
                ),
            )
        )

    return signals
