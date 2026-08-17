"""X3 · High turnover against a thin pool.

A pool whose 24h volume is twenty times its reserves can be moved by a single trade.
Volume from such a pool is real in the sense that it happened, and weak in the sense
that it says little about demand — so this detector exists as much to *discount*
other findings as to raise its own.

Note the two inputs sit in different metric scopes (SPOT_VOLUME over DEX_LIQUIDITY).
Their ratio is a RATIO and is never summed; the signal is judged on the volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import DetectorFamily, EntityType, RwaTier
from app.services.anomaly.signals import Evidence, Signal

DETECTOR = "X3"
RULE_NAME = "VolLiqRatioExtreme"

#: Volume-to-reserves above this means the pool turns over its entire depth many
#: times a day, which no organic book does.
TRIGGER_RATIO = 20.0

#: Below this, the ratio is arithmetic noise rather than a liquidity observation.
MIN_RESERVE_USD = Decimal(50_000)


@dataclass(frozen=True, slots=True)
class PoolLiquidityObservation:
    """One pool's turnover against its depth."""

    pool_id: str
    network: str
    vol_24h: Decimal | None
    reserve_usd: Decimal | None
    rwa_tier: RwaTier = RwaTier.CORE_RWA

    @property
    def vol_liq(self) -> float | None:
        if self.vol_24h is None or self.reserve_usd is None or self.reserve_usd <= 0:
            return None
        return float(self.vol_24h / self.reserve_usd)


def detect(
    observations: Sequence[PoolLiquidityObservation], market_session: MarketSession
) -> list[Signal]:
    """Flag pools whose turnover is out of proportion to their depth."""
    signals: list[Signal] = []

    for pool in observations:
        if pool.rwa_tier is RwaTier.NON_RWA:
            continue

        ratio = pool.vol_liq
        if ratio is None or ratio <= TRIGGER_RATIO:
            continue
        if pool.reserve_usd is None or pool.reserve_usd < MIN_RESERVE_USD:
            continue

        signals.append(
            Signal(
                detector=DETECTOR,
                family=DetectorFamily.CROSS_SECTIONAL,
                entity_type=EntityType.POOL,
                entity_id=pool.pool_id,
                metric_scope=MetricScope.SPOT_VOLUME,
                market_session=market_session,
                headline_zh=(
                    f"{pool.pool_id} 24h 成交为池储备的 {ratio:.1f} 倍，"
                    f"池深 ${float(pool.reserve_usd):,.0f}，成交量信息含量低"
                ),
                notional_usd=pool.vol_24h,
                rwa_tier=pool.rwa_tier,
                evidence=Evidence(
                    rule_name=RULE_NAME,
                    observed_value=pool.vol_24h,
                    # This detector has no peer distribution, so it reports its
                    # deviation on the same scale the scorer expects: the trigger
                    # ratio of 20 maps to 4, just past the 3.5 other detectors use.
                    robust_z=ratio / 5,
                    extra={
                        "vol_liq": ratio,
                        "reserve_usd": str(pool.reserve_usd),
                        "network": pool.network,
                        "interpretation": "discount this pool's volume elsewhere",
                    },
                ),
            )
        )

    return signals
