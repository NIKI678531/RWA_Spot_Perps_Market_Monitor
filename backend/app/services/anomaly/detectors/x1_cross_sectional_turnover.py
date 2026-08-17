"""X1 · Turnover outlier against peers.

The most important detector during cold start. It answers "who is being bought right
now" without reading a single historical snapshot, which is what makes the system
useful on day one instead of on day fifteen.

Turnover — 24h volume over market capitalisation — is the comparison that survives
the size difference between a $2bn wrapper and a $3mn one. Comparing raw volume
across peers just re-ranks them by size.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import AssetClass, DetectorFamily, EntityType, RwaTier
from app.services.analytics.baseline import compute_baseline
from app.services.anomaly.scoring import MIN_PEER_GROUP
from app.services.anomaly.signals import Evidence, Signal

DETECTOR = "X1"
RULE_NAME = "CrossSectionalTurnover"

#: Deviation from the peer median, in MAD units, at which turnover is an outlier.
TRIGGER_Z = 3.5

#: Floors. The market-cap floor is separate from the global $50k volume floor: a
#: micro-cap denominator turns ordinary trading into an enormous turnover ratio.
MIN_VOLUME_USD = Decimal(50_000)
MIN_MARKET_CAP_USD = Decimal(250_000)


@dataclass(frozen=True, slots=True)
class AssetObservation:
    """One asset as of the current snapshot."""

    asset_id: str
    asset_class: AssetClass
    rwa_tier: RwaTier
    vol_24h: Decimal | None
    market_cap: Decimal | None

    @property
    def turnover(self) -> float | None:
        if self.vol_24h is None or self.market_cap is None or self.market_cap <= 0:
            return None
        return float(self.vol_24h / self.market_cap)


def detect(
    observations: Sequence[AssetObservation], market_session: MarketSession
) -> list[Signal]:
    """Flag assets trading far more intensely than their peers.

    Peer groups are ``(asset_class, rwa_tier)``. A tokenized ETF and a Pre-IPO share
    have structurally different turnover, and pooling them makes the Pre-IPO look
    anomalous every single snapshot.
    """
    signals: list[Signal] = []

    groups: dict[tuple[AssetClass, RwaTier], list[AssetObservation]] = {}
    for observation in observations:
        if observation.rwa_tier is RwaTier.NON_RWA:
            continue
        if observation.turnover is None:
            continue
        groups.setdefault((observation.asset_class, observation.rwa_tier), []).append(
            observation
        )

    for (asset_class, tier), members in groups.items():
        if len(members) < MIN_PEER_GROUP:
            # Too few peers for a median to describe anything. Skipping is the honest
            # outcome; the alternative is calling every member of a group of three an
            # outlier of the other two.
            continue

        turnovers = [m.turnover for m in members if m.turnover is not None]
        baseline = compute_baseline(turnovers, market_session)
        if baseline is None:
            continue

        if baseline.mad <= 0:
            # More than half the group sits at one turnover, so every other member
            # is infinitely far from the median and the whole group would fire. A
            # flat *time-series* baseline is informative — it means dormant, which is
            # what T2 looks for. A flat *peer* group is not: it means the comparison
            # has no scale, and ranking outliers against it is arithmetic, not a
            # finding.
            continue

        for member in members:
            turnover = member.turnover
            if turnover is None:
                continue
            if member.vol_24h is None or member.vol_24h < MIN_VOLUME_USD:
                continue
            if member.market_cap is None or member.market_cap < MIN_MARKET_CAP_USD:
                continue

            z = baseline.robust_z(turnover)
            if z <= TRIGGER_Z:
                continue

            signals.append(
                Signal(
                    detector=DETECTOR,
                    family=DetectorFamily.CROSS_SECTIONAL,
                    entity_type=EntityType.ASSET,
                    entity_id=member.asset_id,
                    # The signal is about turnover, but the notional it is judged on
                    # is the traded volume. One scope per signal.
                    metric_scope=MetricScope.SPOT_VOLUME,
                    market_session=market_session,
                    headline_zh=(
                        f"{member.asset_id} 换手率 {turnover:.1%}，"
                        f"为同类（{asset_class.value}）中位数 "
                        f"{baseline.median:.1%} 的显著离群"
                    ),
                    notional_usd=member.vol_24h,
                    rwa_tier=tier,
                    evidence=Evidence(
                        rule_name=RULE_NAME,
                        observed_value=member.vol_24h,
                        baseline_median=Decimal(str(baseline.median)),
                        baseline_mad=Decimal(str(baseline.mad)),
                        robust_z=z,
                        sample_size=len(turnovers),
                        peer_count=len(members),
                        extra={
                            "turnover": turnover,
                            "market_cap": str(member.market_cap),
                            "peer_group": f"{asset_class.value}/{tier.value}",
                        },
                    ),
                )
            )

    return signals
