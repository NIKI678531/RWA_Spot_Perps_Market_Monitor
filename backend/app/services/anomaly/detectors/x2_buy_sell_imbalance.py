"""X2 · Buy/sell imbalance on DEX pools.

Turnover says someone traded. It does not say whether customers were buying. DEX
pool data is the only source in this system that separates direction, which makes
this the one direct piece of evidence for genuine incremental demand as opposed to
wash trading.

Thresholds are absolute rather than peer-relative: a 65% buy ratio means the same
thing in a thin pool and a deep one, and the trade-count floor already removes the
small samples a peer comparison would otherwise be needed to suppress.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import DetectorFamily, EntityType, RwaTier
from app.services.anomaly.signals import Evidence, Signal

DETECTOR = "X2"
RULE_NAME = "BuySellImbalance"

BUY_PRESSURE_RATIO = 0.65
SELL_PRESSURE_RATIO = 0.35

#: Below this many trades the ratio is noise: 7 buys and 2 sells is a 78% buy ratio
#: and describes nine people, not a market.
MIN_TRADE_COUNT = 500
MIN_VOLUME_USD = Decimal(50_000)


@dataclass(frozen=True, slots=True)
class PoolObservation:
    """One DEX pool as of the current snapshot."""

    pool_id: str
    network: str
    vol_24h: Decimal | None
    buys_24h: int | None
    sells_24h: int | None
    rwa_tier: RwaTier = RwaTier.CORE_RWA

    @property
    def trade_count(self) -> int | None:
        if self.buys_24h is None or self.sells_24h is None:
            return None
        return self.buys_24h + self.sells_24h

    @property
    def buy_ratio(self) -> float | None:
        total = self.trade_count
        if total is None or total == 0 or self.buys_24h is None:
            return None
        return self.buys_24h / total


def detect(
    observations: Sequence[PoolObservation], market_session: MarketSession
) -> list[Signal]:
    """Flag pools where trading is materially one-sided."""
    signals: list[Signal] = []

    for pool in observations:
        if pool.rwa_tier is RwaTier.NON_RWA:
            continue

        ratio = pool.buy_ratio
        trades = pool.trade_count
        if ratio is None or trades is None or trades < MIN_TRADE_COUNT:
            continue
        if pool.vol_24h is None or pool.vol_24h < MIN_VOLUME_USD:
            continue
        if SELL_PRESSURE_RATIO <= ratio <= BUY_PRESSURE_RATIO:
            continue

        buying = ratio > BUY_PRESSURE_RATIO
        direction = "净买入" if buying else "净卖出"

        signals.append(
            Signal(
                detector=DETECTOR,
                family=DetectorFamily.CROSS_SECTIONAL,
                entity_type=EntityType.POOL,
                entity_id=pool.pool_id,
                metric_scope=MetricScope.SPOT_VOLUME,
                market_session=market_session,
                headline_zh=(
                    f"{pool.pool_id} 在 {pool.network} 上呈{direction}，"
                    f"买入占比 {ratio:.1%}（{trades:,} 笔）"
                ),
                notional_usd=pool.vol_24h,
                rwa_tier=pool.rwa_tier,
                evidence=Evidence(
                    rule_name=RULE_NAME,
                    observed_value=pool.vol_24h,
                    # Distance from a balanced book, expressed on the same scale the
                    # scorer uses for deviations elsewhere.
                    robust_z=abs(ratio - 0.5) * 20,
                    sample_size=trades,
                    extra={
                        "buy_ratio": ratio,
                        "buys_24h": pool.buys_24h,
                        "sells_24h": pool.sells_24h,
                        "direction": "buy" if buying else "sell",
                        "network": pool.network,
                    },
                ),
            )
        )

    return signals
