"""The publication gate: detection is not publication.

A detector firing is a hypothesis. This module decides whether that hypothesis is
allowed to occupy someone's attention, and it is the *only* path from a detector
output to a stored, visible ``alert``. A detector that could publish its own findings
would eventually publish a bad one at 09:00 on a Monday.

Four things happen here and nowhere else:

1. **The gate.** In-scope tier, a single legal metric scope, the absolute-magnitude
   floor, verifiable data, complete evidence — and for the time-series family, cold
   start exited plus two consecutive confirmations.
2. **Evidence completeness** is measured rather than assumed. The R1 target is 100%,
   which is only meaningful if the missing 0% is countable.
3. **Counter-evidence** is derived and stored beside the evidence, so a published
   alert always shows what argues against it.
4. **Family-correct language.** An ``X*`` finding says "unusual against its peers
   right now"; a ``T*`` finding says "warm against its own history". Swapping the two
   sentences makes the alert say something the detector never checked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.core.metrics import SCOPE_DIMENSION, MetricDimension
from app.models.alerts import Alert, AlertEvidence
from app.models.enums import (
    AlertSeverity,
    AlertState,
    DetectorFamily,
    RwaTier,
    VerificationStatus,
)
from app.services.anomaly.scoring import ABSOLUTE_FLOOR_USD
from app.services.anomaly.signals import Signal

#: A time-series detector compares an entity against its own past. Fewer than this
#: many same-session observations is not a baseline, it is a rumour.
MIN_BASELINE_SAMPLES = 14

#: Consecutive same-session firings a ``T*`` finding needs before it may be published
#: as more than TENTATIVE. Cross-sectional findings need one: they are a statement
#: about the peer group as it stands, not about a trend, so there is no second
#: observation that would make them more true.
REQUIRED_CONFIRMATIONS: dict[DetectorFamily, int] = {
    DetectorFamily.CROSS_SECTIONAL: 1,
    DetectorFamily.TIME_SERIES: 2,
}

#: Verification states an alert may be built on. STALE is excluded deliberately: a
#: stale reading is fine for showing the last known figure and useless for asserting
#: that something changed in the last hour.
PUBLISHABLE_VERIFICATION = frozenset(
    {
        VerificationStatus.VERIFIED,
        VerificationStatus.PARTIAL,
        VerificationStatus.EMPTY,
    }
)

#: Severities the management-facing surfaces (home page, e-mail digest) accept. The
#: work queue shows everything; a manager's screen shows what needs a decision.
MANAGEMENT_SEVERITIES = frozenset({AlertSeverity.HIGH, AlertSeverity.CRITICAL})

#: How long a response may take before the queue marks it overdue. LOW carries no
#: clock — putting one on it would mean every Friday afternoon ends in red rows that
#: nobody was ever expected to clear.
SLA_BY_SEVERITY: dict[AlertSeverity, timedelta | None] = {
    AlertSeverity.CRITICAL: timedelta(hours=2),
    AlertSeverity.HIGH: timedelta(hours=8),
    AlertSeverity.MEDIUM: timedelta(hours=24),
    AlertSeverity.LOW: None,
}

#: The evidence fields an alert must carry to be justifiable on inspection. Scored as
#: a fraction rather than a boolean so a near-complete alert can still be published to
#: the work queue while being kept off the management surfaces.
_REQUIRED_EVIDENCE_FIELDS = (
    "rule_name",
    "observed_value",
    "market_session",
    "sample_or_peer_count",
    "baseline_or_peer_reference",
)


@dataclass(frozen=True, slots=True)
class CounterEvidence:
    """One reason to doubt the finding."""

    code: str
    label_zh: str
    detail: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "label_zh": self.label_zh, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PublicationVerdict:
    """Whether a finding may be shown, in what state, and on what grounds."""

    publishable: bool
    state: AlertState
    #: Every gate the finding failed. All of them, not the first — a finding that is
    #: both below the floor and built on stale data needs both fixed.
    blocked_by: list[str] = field(default_factory=list)
    evidence_completeness: Decimal = Decimal("0")
    counter_evidence: list[CounterEvidence] = field(default_factory=list)
    #: True only when the finding may also appear on a management surface.
    management_visible: bool = False

    @property
    def reason(self) -> str:
        return "; ".join(self.blocked_by) if self.blocked_by else "published"


def evidence_completeness(evidence: AlertEvidence) -> Decimal:
    """What fraction of the required evidence fields this row actually carries.

    ``sample_or_peer_count`` and ``baseline_or_peer_reference`` are single slots on
    purpose: a cross-sectional finding has a peer count and a peer median, a
    time-series finding has a sample size and a baseline median, and neither is
    incomplete for lacking the other's field.
    """
    present = 0
    if evidence.rule_name:
        present += 1
    if evidence.observed_value is not None:
        present += 1
    if evidence.market_session is not None:
        present += 1
    if evidence.sample_size is not None or evidence.peer_count is not None:
        present += 1
    if evidence.baseline_median is not None or evidence.robust_z is not None:
        present += 1
    return Decimal(present) / Decimal(len(_REQUIRED_EVIDENCE_FIELDS))


def derive_counter_evidence(signal: Signal) -> list[CounterEvidence]:
    """What argues against this finding, read out of its own inputs.

    Derived here rather than written by each detector: a detector author is arguing
    for their finding, and the weaknesses that matter most are the ones they did not
    think to mention.
    """
    found: list[CounterEvidence] = []
    evidence = signal.evidence
    extra = dict(evidence.extra)

    sample_size = evidence.sample_size
    if signal.family is DetectorFamily.TIME_SERIES and (
        sample_size is None or sample_size < MIN_BASELINE_SAMPLES
    ):
        found.append(
            CounterEvidence(
                code="cold_baseline",
                label_zh="基线样本不足",
                detail=(
                    f"同时段样本 {sample_size if sample_size is not None else 0} 条，"
                    f"低于 {MIN_BASELINE_SAMPLES} 条阈值，偏离度不可解读"
                ),
            )
        )

    venue_count = _as_int(extra.get("venue_count"))
    if venue_count is not None and venue_count <= 1:
        found.append(
            CounterEvidence(
                code="single_venue",
                label_zh="仅单一场所",
                detail="只有一个场所观测到该现象，无法与其他场所交叉确认",
            )
        )
    elif venue_count is not None and not _as_bool(extra.get("cross_venue_confirmed")):
        found.append(
            CounterEvidence(
                code="no_cross_venue_confirmation",
                label_zh="缺少跨场所确认",
                detail=f"{venue_count} 个场所有报价，但只有一个出现该信号",
            )
        )

    raw = _as_decimal(extra.get("raw_value"))
    adjusted = _as_decimal(extra.get("adjusted_value"))
    if (
        raw is not None
        and adjusted is not None
        and adjusted > 0
        and raw / adjusted >= 10
    ):
        found.append(
            CounterEvidence(
                code="raw_adjusted_gap",
                label_zh="原始与质量调整口径背离",
                detail=f"原始 {raw:,.0f} 对质量调整 {adjusted:,.0f}，相差十倍以上",
            )
        )

    liquidity = _as_decimal(extra.get("liquidity_usd"))
    if liquidity is not None and liquidity < ABSOLUTE_FLOOR_USD:
        found.append(
            CounterEvidence(
                code="thin_pool",
                label_zh="池深度过薄",
                detail=f"可用流动性约 {liquidity:,.0f} 美元，成交易受单笔交易影响",
            )
        )

    peer_count = evidence.peer_count
    if signal.family is DetectorFamily.CROSS_SECTIONAL and peer_count is not None:
        if peer_count < 10:
            found.append(
                CounterEvidence(
                    code="small_peer_group",
                    label_zh="同组样本偏小",
                    detail=f"同组仅 {peer_count} 个实体，中位数与 MAD 稳定性有限",
                )
            )

    return found


def evaluate(
    signal: Signal,
    evidence: AlertEvidence,
    *,
    confirmation_count: int,
    verification: VerificationStatus = VerificationStatus.VERIFIED,
) -> PublicationVerdict:
    """Decide whether a detected finding may become a visible alert."""
    blocked: list[str] = []

    if signal.rwa_tier is RwaTier.NON_RWA:
        blocked.append("超出口径：NON_RWA 仅作对照，不进入告警")

    dimension = SCOPE_DIMENSION[signal.metric_scope]
    if dimension is MetricDimension.RATIO and signal.notional_usd is None:
        # A ratio has no notional of its own, so the floor has to be applied to the
        # flow underneath it. A detector that omits that has produced a percentage
        # with no size attached, which is how $500 -> $5,000 becomes "+900%".
        blocked.append("比率类信号未提供对应名义金额，无法应用金额下限")

    if signal.notional_usd is None:
        blocked.append("未建立名义金额，金额下限无法应用")
    elif signal.notional_usd < ABSOLUTE_FLOOR_USD:
        blocked.append(f"低于 ${ABSOLUTE_FLOOR_USD:,.0f} 绝对金额下限")

    if verification not in PUBLISHABLE_VERIFICATION:
        blocked.append(f"数据可信度为 {verification.value}，不足以支撑结论")

    completeness = evidence_completeness(evidence)
    if completeness < 1:
        blocked.append(f"证据完整度 {completeness:.0%}，未达 100%")

    required = REQUIRED_CONFIRMATIONS[signal.family]
    cold_start = False
    if signal.family is DetectorFamily.TIME_SERIES:
        sample_size = evidence.sample_size or 0
        if sample_size < MIN_BASELINE_SAMPLES:
            cold_start = True
            blocked.append(
                f"冷启动未退出：同时段样本 {sample_size} 条 < {MIN_BASELINE_SAMPLES}"
            )

    counter = derive_counter_evidence(signal)

    if blocked:
        return PublicationVerdict(
            publishable=False,
            state=AlertState.DETECTED,
            blocked_by=blocked,
            evidence_completeness=completeness,
            counter_evidence=counter,
        )

    # Past the gate. A finding that has not yet met its confirmation requirement is
    # published as TENTATIVE rather than withheld: the queue should see it, the
    # management surfaces should not, and the difference is exactly this state.
    state = (
        AlertState.PUBLISHED if confirmation_count >= required else AlertState.TENTATIVE
    )
    return PublicationVerdict(
        publishable=True,
        state=state,
        evidence_completeness=completeness,
        counter_evidence=counter,
        management_visible=(
            state is AlertState.PUBLISHED and not cold_start and completeness >= 1
        ),
    )


def is_management_visible(alert: Alert) -> bool:
    """Whether a stored alert belongs on the home page or in the e-mail digest.

    Stricter than "published" on purpose. The work queue exists to triage medium
    findings; a manager's screen exists to show what needs a decision today, and a
    screen that shows everything shows nothing.
    """
    if alert.state not in (
        AlertState.PUBLISHED,
        AlertState.CLAIMED,
        AlertState.IN_REVIEW,
        AlertState.ACTIONED,
    ):
        return False
    if alert.severity not in MANAGEMENT_SEVERITIES:
        return False
    return (alert.evidence_completeness or Decimal("0")) >= 1


def sla_due(severity: AlertSeverity, published_at: datetime) -> datetime | None:
    window = SLA_BY_SEVERITY.get(severity)
    return None if window is None else published_at + window


def family_prefix(family: DetectorFamily) -> str:
    """The clause that states what kind of comparison produced the finding.

    Kept here so the two families cannot drift into each other's language. "同组现状
    异常" is a claim about peers and carries no implication of change over time;
    "相对历史基线升温" is a claim about time and carries no implication about peers.
    Attaching the wrong one to a finding is a false statement, not a wording choice.
    """
    return (
        "同组现状异常"
        if family is DetectorFamily.CROSS_SECTIONAL
        else "相对历史基线升温"
    )


def apply(
    alert: Alert,
    evidence: AlertEvidence,
    verdict: PublicationVerdict,
    *,
    now: datetime,
) -> None:
    """Write a verdict onto the alert and its evidence row.

    Deliberately does not touch the session or write the action stream — the caller
    owns the transaction, and ``services.workflow.alert_lifecycle`` owns the audit
    record that must land in it.
    """
    alert.evidence_completeness = verdict.evidence_completeness
    evidence.counter_evidence_json = json.dumps(
        [item.as_dict() for item in verdict.counter_evidence],
        ensure_ascii=False,
    )

    if not verdict.publishable:
        alert.state = AlertState.DETECTED
        alert.published_at = None
        alert.sla_due_ts = None
        return

    # Already past this point in a previous pass: a later confirmation must not reset
    # an alert someone has already claimed back to PUBLISHED.
    if alert.state in (AlertState.DETECTED, AlertState.TENTATIVE):
        alert.state = verdict.state
    if alert.published_at is None:
        alert.published_at = now
        alert.sla_due_ts = sla_due(alert.severity, now)


def _as_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    # Routed through Decimal rather than ``int()`` directly: evidence payloads are
    # JSON, so a sample count can arrive as 14, 14.0 or "14" depending on the
    # detector, and only one of those three is an ``int``.
    number = _as_decimal(value)
    if number is None:
        return None
    try:
        return int(number)
    except (ArithmeticError, ValueError):
        return None


def _as_bool(value: object) -> bool:
    return bool(value) if value is not None else False


__all__ = [
    "MANAGEMENT_SEVERITIES",
    "MIN_BASELINE_SAMPLES",
    "REQUIRED_CONFIRMATIONS",
    "CounterEvidence",
    "PublicationVerdict",
    "apply",
    "derive_counter_evidence",
    "evaluate",
    "evidence_completeness",
    "family_prefix",
    "is_management_visible",
    "sla_due",
]
