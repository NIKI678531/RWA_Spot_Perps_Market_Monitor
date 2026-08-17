"""Global gates and severity scoring.

Every detector output passes through here before it can become an alert. Centralised
because the gates are the reason the feed is readable: a detector author who forgets
the $50k floor produces a feed nobody opens, and by then the credibility is gone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import AlertSeverity, RwaTier
from app.services.anomaly.signals import Signal

#: No alert below this notional. $500 growing to $5,000 is +900% and commercially
#: meaningless; without a floor the feed fills with dust.
ABSOLUTE_FLOOR_USD = Decimal(50_000)

#: A cross-sectional detector needs a peer group this large before its median and
#: MAD describe anything. Below it, the "outlier" is just the small sample.
MIN_PEER_GROUP = 5

#: Consecutive firings at which persistence counts as fully established.
PERSISTENCE_SATURATION = 3

#: Upper anchor for the magnitude term: $10bn, roughly the largest single figure the
#: tokenized RWA market produces today.
_MAGNITUDE_CEILING_USD = Decimal(10_000_000_000)

#: Robust-z at which the deviation term saturates. The trigger threshold across
#: detectors is 3.5, which lands at 0.35 here — deliberately mid-scale, so a
#: just-triggering signal does not arrive labelled CRITICAL.
_Z_CEILING = 10.0

SEVERITY_BANDS: tuple[tuple[float, AlertSeverity], ...] = (
    (0.85, AlertSeverity.CRITICAL),
    (0.60, AlertSeverity.HIGH),
    (0.35, AlertSeverity.MEDIUM),
)


@dataclass(frozen=True, slots=True)
class GateResult:
    """Whether a signal may become an alert, and why not when it may not."""

    passed: bool
    reason: str | None = None


def check_gates(signal: Signal) -> GateResult:
    """Apply the gates every alert must clear, regardless of detector.

    The peer-count gate reads ``signal.evidence.peer_count`` rather than taking it as
    an argument: a caller who forgets to thread it through gets no gate at all, and a
    silently disabled gate is worse than an absent one.
    """
    peer_count = signal.evidence.peer_count
    if signal.rwa_tier is RwaTier.NON_RWA:
        return GateResult(False, "out of scope: NON_RWA is benchmark reference only")

    if signal.notional_usd is None:
        # Not a rejection of the finding, a rejection of alerting on it: without a
        # notional the floor cannot be applied, and an unbounded alert is noise.
        return GateResult(False, "no notional established; floor cannot be applied")

    if signal.notional_usd < ABSOLUTE_FLOOR_USD:
        return GateResult(
            False, f"below the ${ABSOLUTE_FLOOR_USD:,.0f} absolute-magnitude floor"
        )

    if peer_count is not None and peer_count < MIN_PEER_GROUP:
        return GateResult(
            False, f"peer group of {peer_count} is below the minimum {MIN_PEER_GROUP}"
        )

    return GateResult(True)


def _norm_deviation(robust_z: float | None) -> float:
    """Map a robust-z onto 0-1.

    ``inf`` arises whenever the baseline window was flat, which is the normal state
    of a dormant asset — exactly the population the flagship detector targets. It
    saturates rather than propagating, so the score stays a number.
    """
    if robust_z is None:
        return 0.0
    if math.isinf(robust_z):
        return 1.0
    return min(abs(robust_z) / _Z_CEILING, 1.0)


def _norm_magnitude(notional_usd: Decimal | None) -> float:
    """Map a USD notional onto 0-1 on a log scale.

    Log, not linear: the difference between $50k and $500k matters commercially far
    more than the difference between $5bn and $5.45bn, and a linear term would
    compress every mid-size finding into indistinguishable near-zero scores.
    """
    if notional_usd is None or notional_usd <= 0:
        return 0.0
    floor = math.log10(float(ABSOLUTE_FLOOR_USD))
    ceiling = math.log10(float(_MAGNITUDE_CEILING_USD))
    value = math.log10(float(notional_usd))
    return min(max((value - floor) / (ceiling - floor), 0.0), 1.0)


def _norm_persistence(consecutive_snapshots: int) -> float:
    if consecutive_snapshots <= 0:
        return 0.0
    return min(consecutive_snapshots / PERSISTENCE_SATURATION, 1.0)


def severity_score(
    *,
    robust_z: float | None,
    notional_usd: Decimal | None,
    consecutive_snapshots: int = 1,
) -> float:
    """Combine deviation, magnitude and persistence into a 0-1 score.

    The weights say what the business cares about: how unusual it is (0.5) matters
    most, how big it is (0.3) next, and whether it held (0.2) least — a large move
    that reverses immediately is still worth a look.
    """
    return (
        0.5 * _norm_deviation(robust_z)
        + 0.3 * _norm_magnitude(notional_usd)
        + 0.2 * _norm_persistence(consecutive_snapshots)
    )


def to_severity(score: float) -> AlertSeverity:
    """Bucket a score for display. The continuous score is stored alongside."""
    for threshold, severity in SEVERITY_BANDS:
        if score >= threshold:
            return severity
    return AlertSeverity.LOW


def score_signal(signal: Signal, *, consecutive_snapshots: int = 1) -> float:
    return severity_score(
        robust_z=signal.evidence.robust_z,
        notional_usd=signal.notional_usd,
        consecutive_snapshots=consecutive_snapshots,
    )
