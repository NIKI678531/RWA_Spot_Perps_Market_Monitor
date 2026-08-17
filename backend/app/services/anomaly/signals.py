"""The shared vocabulary between detectors and the engine.

Detectors are pure functions over plain data. They emit ``Signal`` objects and know
nothing about the database, the alert lifecycle or deduplication — which keeps each
one testable against a handful of numbers instead of a fixture-loaded schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.models.enums import DetectorFamily, EntityType, RwaTier


@dataclass(frozen=True, slots=True)
class Evidence:
    """The inputs behind one detection, verbatim.

    Every field here reaches ``alert_evidence``. A detector that emits only a verdict
    produces alerts nobody can check, and an alert nobody can check gets ignored the
    third time it fires.
    """

    rule_name: str
    observed_value: Decimal | None
    baseline_median: Decimal | None = None
    baseline_mad: Decimal | None = None
    robust_z: float | None = None
    sample_size: int | None = None
    peer_count: int | None = None
    #: Detector-specific inputs that do not deserve a column, e.g. buy/sell counts.
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Signal:
    """One detection, before the global gates and the alert lifecycle apply."""

    detector: str
    family: DetectorFamily
    entity_type: EntityType
    entity_id: str
    #: Must be a single scope. A signal spanning scopes is not comparable to itself.
    metric_scope: MetricScope
    market_session: MarketSession
    headline_zh: str
    #: The USD figure the $50k floor is applied to. ``None`` means the detector could
    #: not establish a notional, which fails the floor rather than bypassing it.
    notional_usd: Decimal | None
    evidence: Evidence
    rwa_tier: RwaTier = RwaTier.CORE_RWA

    @property
    def dedup_key(self) -> str:
        """Stable across snapshots, so a continuing condition is one alert.

        Excludes the session on purpose: a condition that persists from regular hours
        into after-hours is the same condition, and splitting it would double-count a
        single event in the feed.
        """
        return ":".join(
            (
                self.detector,
                self.entity_type.value,
                self.entity_id,
                self.metric_scope.value,
            )
        )
