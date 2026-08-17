"""The anomaly engine: registration, gating, and the alert lifecycle.

Detectors decide *what* is unusual. This module decides what reaches a human — the
$50k floor, the tier gate, TENTATIVE-then-CONFIRMED persistence, and the 24h cooldown
that stops one continuing condition from filing an alert every fifteen minutes.

Every surviving signal writes an ``alert_evidence`` row. That is not optional
bookkeeping: an alert that cannot be justified to management on inspection is noise,
and a feed of noise gets muted once and never unmuted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.sessions import classify_session
from app.models.alerts import Alert, AlertEvidence
from app.models.enums import AlertStatus, DetectorFamily
from app.services.anomaly import observations
from app.services.anomaly.detectors import x1_cross_sectional_turnover as x1
from app.services.anomaly.detectors import x2_buy_sell_imbalance as x2
from app.services.anomaly.detectors import x3_vol_liq_ratio_extreme as x3
from app.services.anomaly.detectors import x4_new_pair_listing as x4
from app.services.anomaly.scoring import check_gates, score_signal, to_severity
from app.services.anomaly.signals import Signal

logger = logging.getLogger(__name__)

#: Two firings of the same condition inside this window are one alert. Snapshots run
#: as often as every fifteen minutes; without this, a three-day trend produces almost
#: three hundred rows describing one event.
COOLDOWN = timedelta(hours=24)

DetectorFn = Callable[[], Sequence[Signal]]


@dataclass(frozen=True, slots=True)
class RegisteredDetector:
    """A detector as the engine sees it."""

    name: str
    family: DetectorFamily
    run: DetectorFn


@dataclass(frozen=True, slots=True)
class EngineResult:
    """What one detection pass produced, including what it suppressed.

    Suppressions are returned rather than dropped so the data-quality page can show
    that the gates are working. A gate nobody can see is a gate nobody trusts.
    """

    created: list[Alert]
    updated: list[Alert]
    suppressed: list[tuple[Signal, str]]

    @property
    def alert_count(self) -> int:
        return len(self.created) + len(self.updated)


class AnomalyEngine:
    """Runs registered detectors and reconciles their output against open alerts."""

    def __init__(self) -> None:
        self._detectors: dict[str, RegisteredDetector] = {}

    def register(self, name: str, family: DetectorFamily, run: DetectorFn) -> None:
        if name in self._detectors:
            raise ValueError(f"detector {name} is already registered")
        self._detectors[name] = RegisteredDetector(name=name, family=family, run=run)

    @property
    def registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._detectors))

    def run(self, session: Session, snapshot_ts: datetime) -> EngineResult:
        """Execute every registered detector and persist the surviving signals.

        A detector that raises is logged and skipped rather than aborting the pass.
        One broken detector must not cost the snapshot its other sixteen.
        """
        signals: list[Signal] = []
        for detector in self._detectors.values():
            try:
                signals.extend(detector.run())
            except Exception:  # noqa: BLE001 - isolation is the point
                logger.exception("detector %s failed; skipping", detector.name)

        return self.process(session, snapshot_ts, signals)

    def process(
        self, session: Session, snapshot_ts: datetime, signals: Sequence[Signal]
    ) -> EngineResult:
        """Apply the gates and the lifecycle to an explicit list of signals."""
        created: list[Alert] = []
        updated: list[Alert] = []
        suppressed: list[tuple[Signal, str]] = []

        for signal in signals:
            gate = check_gates(signal)
            if not gate.passed:
                suppressed.append((signal, gate.reason or "suppressed"))
                continue

            existing = _find_open_alert(session, signal.dedup_key, snapshot_ts)
            if existing is None:
                alert = _create_alert(signal, snapshot_ts)
                session.add(alert)
                created.append(alert)
            else:
                alert = _extend_alert(existing, signal, snapshot_ts)
                updated.append(alert)

            session.add(_build_evidence(alert, signal, snapshot_ts))

        return EngineResult(created=created, updated=updated, suppressed=suppressed)


def _find_open_alert(
    session: Session, dedup_key: str, snapshot_ts: datetime
) -> Alert | None:
    """The same condition seen inside the cooldown window, if any."""
    stmt = (
        select(Alert)
        .where(Alert.dedup_key == dedup_key)
        .where(Alert.status != AlertStatus.RESOLVED)
        .where(Alert.last_seen_ts >= snapshot_ts - COOLDOWN)
        .order_by(Alert.last_seen_ts.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _create_alert(signal: Signal, snapshot_ts: datetime) -> Alert:
    score = score_signal(signal, consecutive_snapshots=1)
    return Alert(
        dedup_key=signal.dedup_key,
        detector=signal.detector,
        family=signal.family,
        entity_type=signal.entity_type,
        entity_id=signal.entity_id,
        metric_scope=signal.metric_scope,
        market_session=signal.market_session,
        severity=to_severity(score),
        score=Decimal(str(round(score, 4))),
        # A single snapshot is not yet a finding. Confirmation costs one more
        # snapshot and removes most one-off data artefacts.
        status=AlertStatus.TENTATIVE,
        headline_zh=signal.headline_zh,
        first_seen_ts=snapshot_ts,
        last_seen_ts=snapshot_ts,
        occurrence_count=1,
    )


def _extend_alert(alert: Alert, signal: Signal, snapshot_ts: datetime) -> Alert:
    """Fold a repeat firing into the existing alert rather than filing a new one."""
    alert.occurrence_count += 1
    alert.last_seen_ts = snapshot_ts
    alert.headline_zh = signal.headline_zh

    if alert.status is AlertStatus.TENTATIVE:
        alert.status = AlertStatus.CONFIRMED

    score = score_signal(signal, consecutive_snapshots=alert.occurrence_count)
    alert.score = Decimal(str(round(score, 4)))
    alert.severity = to_severity(score)
    return alert


def _build_evidence(
    alert: Alert, signal: Signal, snapshot_ts: datetime
) -> AlertEvidence:
    evidence = signal.evidence
    robust_z = evidence.robust_z
    return AlertEvidence(
        alert=alert,
        snapshot_ts=snapshot_ts,
        rule_name=evidence.rule_name,
        observed_value=evidence.observed_value,
        baseline_median=evidence.baseline_median,
        baseline_mad=evidence.baseline_mad,
        # An infinite z is meaningful (a flat baseline) but not storable. The reason
        # survives in extra_json, so the evidence stays readable.
        robust_z=(
            Decimal(str(round(robust_z, 6)))
            if robust_z is not None and robust_z not in (float("inf"), float("-inf"))
            else None
        ),
        sample_size=evidence.sample_size,
        market_session=signal.market_session,
        peer_count=evidence.peer_count,
        extra_json=json.dumps(
            {
                **dict(evidence.extra),
                **(
                    {"robust_z": "inf"}
                    if robust_z is not None and robust_z == float("inf")
                    else {}
                ),
            },
            ensure_ascii=False,
            default=str,
        ),
    )


def build_default_engine(session: Session, snapshot_ts: datetime) -> AnomalyEngine:
    """The P1 engine: the cross-sectional family only.

    The time-series family is deliberately absent until the warehouse holds fourteen
    same-session snapshots. Registering them earlier would fire them against baselines
    built from three observations, and the first impression of the alert feed would be
    that it is wrong.

    Observations are read once here and closed over, so all four detectors judge the
    same snapshot. Reading per detector would let a collector land between them and
    produce alerts that contradict each other.
    """
    engine = AnomalyEngine()
    market_session = classify_session(snapshot_ts)

    assets = observations.assets(session, snapshot_ts)
    pools = observations.pools(session, snapshot_ts)
    pairs = observations.pairs(session, snapshot_ts)
    known_pairs = observations.known_pairs(session, snapshot_ts)

    engine.register(
        x1.DETECTOR,
        DetectorFamily.CROSS_SECTIONAL,
        lambda: x1.detect(assets, market_session),
    )
    engine.register(
        x2.DETECTOR,
        DetectorFamily.CROSS_SECTIONAL,
        lambda: x2.detect([p.imbalance for p in pools], market_session),
    )
    engine.register(
        x3.DETECTOR,
        DetectorFamily.CROSS_SECTIONAL,
        lambda: x3.detect([p.liquidity for p in pools], market_session),
    )
    engine.register(
        x4.DETECTOR,
        DetectorFamily.CROSS_SECTIONAL,
        lambda: x4.detect(pairs, known_pairs, market_session),
    )
    return engine
