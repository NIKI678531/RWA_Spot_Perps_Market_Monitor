"""The decision home page: four questions, and what argues against each answer.

Every card here follows the same discipline. It states an answer, shows the figure
behind it, shows what weakens it, and says how much weight it will bear — and when the
data cannot support an answer it says *that* rather than rendering an empty number.
"No data" reads as "nothing is happening", which is a different claim and frequently a
false one.

The page is management-facing, so it is stricter than the work queue: only findings the
publication gate marked management-visible appear here. A screen that shows everything
shows nothing.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AsOf, DatasetDep, SessionDep
from app.api.naming import Resolver
from app.api.routes.alert_queue import (
    confidence_of,
    counter_evidence_of,
    queue_row,
)
from app.api.routes.context import build_status_bar
from app.api.routes.kpi import _LABELS, _ORDER, _metrics
from app.core.metrics import SCOPE_DIMENSION, MetricScope, ScopedValue
from app.db.base import utcnow
from app.models.alerts import Alert, AlertEvidence
from app.models.enums import (
    CLOSED_ALERT_STATES,
    AlertSeverity,
    CoverageState,
    EntityType,
    VerificationStatus,
)
from app.models.facts import FactLaunchWindowSnapshot
from app.models.workflow import IssuanceCandidate, ProductCoverage
from app.schemas.common import (
    Confidence,
    CounterEvidenceItem,
    EvidenceItem,
    Meta,
    MetricValue,
    StatusBar,
)
from app.schemas.decision import (
    DecisionHome,
    DrillLink,
    Highlight,
    KpiCard,
    QuestionCard,
    SummaryLine,
)
from app.services.anomaly import publication
from app.services.report.dataset import (
    ReportDataset,
    UnderlyingAggregates,
    amount_of,
    load,
    sort_by_amount,
)
from app.services.workflow import candidate as candidate_service

router = APIRouter(tags=["home"])

#: The comparison basis for every change on this page. A day rather than a snapshot:
#: at an hourly cadence the previous snapshot is noise, and RWA underlyings move on
#: the daily rhythm of the markets they track.
_WINDOW = timedelta(hours=24)

#: How long a listing stays a launch. After this it is simply a product, and belongs
#: in the ordinary rankings rather than in Q3.
_LAUNCH_WINDOW_DAYS = 14

#: Below this a raw/adjusted divergence is routine screening. Above it the two figures
#: are telling different stories, and the reader has to be told so.
_RAW_ADJUSTED_GAP = Decimal("0.30")

#: Alerts considered for the management surface before the gate is re-applied. A cap
#: rather than the whole open queue: this page ranks, it does not triage.
_ALERT_POOL = 20

_NOTE = (
    "五类 KPI 物理分开，任何跨口径合计都没有意义。四问卡均附反证与信心，"
    "管理层区域只展示已通过发布门且证据完整的 high/critical 告警。"
)


@router.get("/home", response_model=DecisionHome)
def home(data: DatasetDep, session: SessionDep, as_of: AsOf = None) -> DecisionHome:
    previous = load(session, data.as_of - _WINDOW)
    current_agg = UnderlyingAggregates(data)
    previous_agg = UnderlyingAggregates(previous)
    resolver = Resolver(session)
    now = utcnow()

    alerts = _management_alerts(session)
    evidence = _latest_evidence(session, [a.id for a in alerts])
    status_bar = build_status_bar(data, session)

    questions = [
        _q1_volume(data, current_agg, previous_agg, resolver),
        _q2_heating(data, current_agg, previous_agg, resolver, alerts, evidence),
        _q3_new_products(session, data, resolver),
        _q4_issuance(session, resolver),
    ]

    return DecisionHome(
        meta=Meta(
            as_of=data.as_of,
            scopes=list(MetricScope),
            note=_NOTE,
            row_count=len(questions),
        ),
        status_bar=status_bar,
        summary=_summary(status_bar, questions, alerts),
        questions=questions,
        opportunities=_highlights(alerts, evidence, kind="opportunity"),
        risks=_highlights(alerts, evidence, kind="risk"),
        kpis=_kpis(data, previous),
        alerts=[queue_row(a, resolver, now=now) for a in alerts],
        drilldowns=_drilldowns(),
    )


# --- the four questions ------------------------------------------------------


def _q1_volume(
    data: ReportDataset,
    current: UnderlyingAggregates,
    previous: UnderlyingAggregates,
    resolver: Resolver,
) -> QuestionCard:
    """Q1 — what is trading most."""
    ranked = sort_by_amount(list(current.spot_adjusted), current.spot_adjusted)
    top = next((k for k in ranked if amount_of(current.spot_adjusted, k)), None)
    if top is None:
        return _unanswerable(
            "q1_volume",
            "当前成交最集中的底层是哪一个？",
            "本快照没有任何可验证的现货成交观测。",
            href="/spot-scale",
            cta="查看现货规模",
        )

    adjusted = current.spot_adjusted[top]
    raw = current.spot_raw.get(top)
    name = resolver.name(EntityType.UNDERLYING, top)
    venues = _venue_count(data, top)
    counter = _volume_counter_evidence(raw, adjusted, venues)

    return QuestionCard(
        key="q1_volume",
        question="当前成交最集中的底层是哪一个？",
        answer=f"{name} 的质量调整后现货成交额居首。",
        value=MetricValue.of(
            adjusted,
            observed_at=data.as_of,
            window="24h",
            source_count=venues or None,
            raw_value=raw.amount if raw else None,
            adjusted_value=adjusted.amount,
        ),
        change_pct=_change(adjusted.amount, amount_of(previous.spot_adjusted, top)),
        change_basis="对比 24 小时前同一口径",
        evidence=[
            EvidenceItem(
                label="原始成交额（未经质量筛选）",
                value=(
                    MetricValue.of(raw, observed_at=data.as_of, window="24h")
                    if raw
                    else None
                ),
                detail="原值与调整值并列，任何一侧单独引用都会误导。",
                href=f"/underlying/{top}",
            ),
            EvidenceItem(
                label="覆盖场所数",
                detail=f"{venues} 个交易场所有成交观测",
            ),
        ],
        counter_evidence=counter,
        confidence=Confidence(
            level="high" if not counter else "medium",
            basis=(
                "多场所观测，原值与调整值接近"
                if not counter
                else "存在需要一并阅读的削弱因素"
            ),
        ),
        cta_label="进入 Underlying 360",
        cta_href=f"/underlying/{top}",
    )


def _q2_heating(
    data: ReportDataset,
    current: UnderlyingAggregates,
    previous: UnderlyingAggregates,
    resolver: Resolver,
    alerts: list[Alert],
    evidence: dict[int, AlertEvidence],
) -> QuestionCard:
    """Q2 — what is heating up fastest.

    An alert answers this better than a delta does: it has been through the
    publication gate, it carries its own evidence, and it states which comparison it
    made. The delta is the fallback for a quiet day, and it is labelled as the weaker
    answer that it is.
    """
    now = utcnow()
    if alerts:
        top = alerts[0]
        row = queue_row(top, resolver, now=now)
        latest = evidence.get(top.id)
        name = resolver.name(top.entity_type, top.entity_id)
        return QuestionCard(
            key="q2_heating",
            question="哪个标的升温最快？",
            answer=f"{name}：{top.headline_zh}",
            change_basis=row.family_claim,
            evidence=[
                EvidenceItem(
                    label="检测规则",
                    detail=f"{top.detector} · {row.family_claim}",
                    href=f"/alerts/{top.id}",
                ),
                EvidenceItem(
                    label="确认次数",
                    detail=(
                        f"{row.confirmation_count} / "
                        f"{row.confirmations_required} 次同时段确认"
                    ),
                ),
            ],
            counter_evidence=counter_evidence_of(latest) if latest else [],
            confidence=confidence_of(top, latest),
            cta_label="打开异常详情",
            cta_href=f"/alerts/{top.id}",
        )

    # No published finding. Fall back to the largest observed move, stated as an
    # observation rather than as a finding — it has not been through the gate.
    best: tuple[str, float] | None = None
    for key, value in current.spot_adjusted.items():
        change = _change(value.amount, amount_of(previous.spot_adjusted, key))
        if change is None or change <= 0:
            continue
        if best is None or change > best[1]:
            best = (key, change)

    if best is None:
        return _unanswerable(
            "q2_heating",
            "哪个标的升温最快？",
            "本快照没有已发布的升温信号，也没有可比的 24 小时基线。",
            href="/alerts",
            cta="查看异常雷达",
        )

    key, change = best
    name = resolver.name(EntityType.UNDERLYING, key)
    return QuestionCard(
        key="q2_heating",
        question="哪个标的升温最快？",
        answer=f"{name} 的调整后成交额环比增幅最大。",
        value=MetricValue.of(
            current.spot_adjusted[key], observed_at=data.as_of, window="24h"
        ),
        change_pct=change,
        change_basis="对比 24 小时前同一口径",
        evidence=[
            EvidenceItem(
                label="口径",
                detail="质量调整后现货成交额，24 小时窗口",
                href=f"/underlying/{key}",
            )
        ],
        counter_evidence=[
            CounterEvidenceItem(
                code="not_an_alert",
                label="尚未通过发布门",
                detail=(
                    "这是一次原始观测：没有基线检验，没有连续确认，也没有金额下限，"
                    "不能作为管理层结论引用。"
                ),
            )
        ],
        confidence=Confidence(level="low", basis="单次快照对比，未经检测器验证"),
        cta_label="进入 Underlying 360",
        cta_href=f"/underlying/{key}",
    )


def _q3_new_products(
    session: Session, data: ReportDataset, resolver: Resolver
) -> QuestionCard:
    """Q3 — how are newly listed products performing.

    Read from the launch-window series rather than the hourly one. A listing at 14:20
    has no 24h figure until the next day, and by then the launch is over.
    """
    cutoff = data.as_of - timedelta(days=_LAUNCH_WINDOW_DAYS)
    rows = list(
        session.execute(
            select(FactLaunchWindowSnapshot)
            .where(FactLaunchWindowSnapshot.listed_at >= cutoff)
            .where(FactLaunchWindowSnapshot.snapshot_ts <= data.as_of)
            .order_by(FactLaunchWindowSnapshot.snapshot_ts.desc())
        )
        .scalars()
        .all()
    )
    if not rows:
        return _unanswerable(
            "q3_new_products",
            "新上架产品表现如何？",
            (
                f"最近 {_LAUNCH_WINDOW_DAYS} 天内没有记录到新上架的包装代币，"
                "或上市窗口快照尚未生成。"
            ),
            href="/spot-scale",
            cta="查看现货规模",
        )

    # Newest snapshot per asset; the query orders descending, so the first wins.
    latest: dict[str, FactLaunchWindowSnapshot] = {}
    for row in rows:
        latest.setdefault(row.asset_id, row)
    best = max(
        latest.values(),
        key=lambda r: (r.adjusted_volume or Decimal(0), r.venue_count or 0),
    )

    name = resolver.name(EntityType.ASSET, best.asset_id)
    href = resolver.href(EntityType.ASSET, best.asset_id)
    counter: list[CounterEvidenceItem] = []
    if (best.venue_count or 0) <= 1:
        counter.append(
            CounterEvidenceItem(
                code="single_venue",
                label="仅一个场所有成交",
                detail="一个场所是上架，五个场所才是需求。",
            )
        )
    counter.extend(_raw_adjusted_counter(best.raw_volume, best.adjusted_volume))
    if best.closed_at is None:
        counter.append(
            CounterEvidenceItem(
                code="window_open",
                label=f"{best.window} 窗口尚未结束",
                detail="数值仍在变动，结论会随窗口关闭而改变。",
            )
        )

    return QuestionCard(
        key="q3_new_products",
        question="新上架产品表现如何？",
        answer=f"{name} 在上市 {best.window} 窗口内的调整后成交额最高。",
        value=MetricValue(
            value=best.adjusted_volume,
            metric_scope=MetricScope.SPOT_VOLUME,
            metric_dimension=SCOPE_DIMENSION[MetricScope.SPOT_VOLUME],
            raw_value=best.raw_volume,
            adjusted_value=best.adjusted_volume,
            verification_status=(
                VerificationStatus.VERIFIED
                if best.adjusted_volume is not None
                else VerificationStatus.NOT_VERIFIED
            ),
            observed_at=best.snapshot_ts,
            window=best.window,
            source_count=best.venue_count,
        ),
        change_basis=f"自 {best.listed_at:%Y-%m-%d %H:%M} 上市起 {best.window}",
        evidence=[
            EvidenceItem(
                label="上市时间",
                detail=f"{best.listed_at:%Y-%m-%d %H:%M} UTC",
                href=href,
            ),
            EvidenceItem(
                label="成交笔数",
                detail=(
                    str(best.trade_count) if best.trade_count is not None else "未观测"
                ),
            ),
        ],
        counter_evidence=counter,
        confidence=Confidence(
            level="medium" if not counter else "low",
            basis="窗口自上市时刻起算，不与整点小时序列混用",
        ),
        cta_label="查看该包装",
        cta_href=href,
    )


def _q4_issuance(session: Session, resolver: Resolver) -> QuestionCard:
    """Q4 — what is ready for issuance evaluation."""
    candidates = list(
        session.execute(
            select(IssuanceCandidate)
            .where(IssuanceCandidate.stage.in_(candidate_service.STAGE_ORDER))
            .order_by(IssuanceCandidate.last_reviewed_at.desc())
        )
        .scalars()
        .all()
    )
    if candidates:
        # Furthest along the ladder, then the one whose weakest column is strongest.
        # An ordering, not a score: nothing here adds the four readiness figures, and
        # a stage only ever moves through a recorded human decision.
        top = max(candidates, key=_candidate_rank)
        readiness = candidate_service.readiness_of(top)
        weakest = readiness.weakest
        name = resolver.name(EntityType.UNDERLYING, top.underlying_id)

        counter = [
            CounterEvidenceItem(
                code="human_gate_required",
                label="阶段推进需人工决策",
                detail="就绪度四列独立归一化，不产生总分，也不构成批准。",
            )
        ]
        if top.counter_rationale:
            counter.append(
                CounterEvidenceItem(
                    code="stated_counter_rationale",
                    label="已记录的反对理由",
                    detail=top.counter_rationale,
                )
            )
        if not readiness.complete:
            counter.append(
                CounterEvidenceItem(
                    code="incomplete_readiness",
                    label="就绪度尚未评满四列",
                    detail="缺列的候选无法与评满的候选横向比较。",
                )
            )

        return QuestionCard(
            key="q4_issuance",
            question="哪些标的可以进入发行评估？",
            answer=(
                f"{name} 处于 {top.stage.value} 阶段"
                + (f"，最短板是 {weakest[0]}。" if weakest else "，尚未评估就绪度。")
            ),
            evidence=[
                EvidenceItem(
                    label="立项理由",
                    detail=top.rationale or "尚未填写",
                    href=f"/candidates/{top.id}",
                ),
                EvidenceItem(
                    label="上次门审",
                    detail=(
                        f"{top.last_reviewed_at:%Y-%m-%d}"
                        if top.last_reviewed_at
                        else "尚无门审记录"
                    ),
                ),
            ],
            counter_evidence=counter,
            confidence=Confidence(
                level="medium" if readiness.complete else "low",
                basis="四列就绪度独立解读，最短板决定下一步",
            ),
            cta_label="打开候选评估",
            cta_href=f"/candidates/{top.id}",
        )

    # No candidates yet — the largest uncovered demand is the honest answer.
    gap = (
        session.execute(
            select(ProductCoverage)
            .where(ProductCoverage.state == CoverageState.GAP)
            .where(ProductCoverage.demand_value.isnot(None))
            .order_by(ProductCoverage.demand_value.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if gap is None:
        return _unanswerable(
            "q4_issuance",
            "哪些标的可以进入发行评估？",
            "尚无评估候选，也没有已评估的覆盖缺口。",
            href="/candidates",
            cta="查看候选列表",
        )

    name = resolver.name(EntityType.UNDERLYING, gap.underlying_id)
    scope = gap.demand_scope or MetricScope.SPOT_VOLUME
    return QuestionCard(
        key="q4_issuance",
        question="哪些标的可以进入发行评估？",
        answer=f"{name} 是当前需求最大的未覆盖标的。",
        value=MetricValue(
            value=gap.demand_value,
            metric_scope=scope,
            metric_dimension=SCOPE_DIMENSION[scope],
            verification_status=VerificationStatus.VERIFIED,
            observed_at=gap.assessed_at,
            window="24h",
        ),
        evidence=[
            EvidenceItem(
                label="覆盖状态",
                detail=gap.note or "我方产品线暂无对应产品",
                href=f"/underlying/{gap.underlying_id}",
            )
        ],
        counter_evidence=[
            CounterEvidenceItem(
                code="not_yet_screened",
                label="尚未进入评估流程",
                detail="需求规模不等于可行性，就绪度四列都还没有评。",
            )
        ],
        confidence=Confidence(level="low", basis="仅有需求侧观测，无就绪度评估"),
        cta_label="建立候选",
        cta_href=f"/candidates?underlying={gap.underlying_id}",
    )


# --- summary, highlights, KPIs ----------------------------------------------


def _summary(
    status_bar: StatusBar, questions: list[QuestionCard], alerts: list[Alert]
) -> SummaryLine:
    """Fact → meaning → confidence → action (HOME-002).

    When the page's own dependencies are unverifiable it states what cannot be judged
    instead of hedging a conclusion. A hedged conclusion still reads as a conclusion.
    """
    if status_bar.verification_status in (
        VerificationStatus.NOT_VERIFIED,
        VerificationStatus.STALE,
    ):
        age = status_bar.data_age_minutes
        return SummaryLine(
            fact=(
                f"{status_bar.sources_ok}/{status_bar.sources_total} 个数据源在本快照"
                f"中有响应，数据年龄 {age if age is not None else '未知'} 分钟。"
            ),
            meaning=(
                "当前依赖不足以支撑新的管理层结论——这不是「没有变化」，"
                "而是「无法判断」。"
            ),
            confidence=Confidence(
                level="low",
                basis=f"整页可信度为 {status_bar.verification_status.value}",
            ),
            action="先处理数据质量缺口",
            action_href="/data-quality",
            indeterminate=True,
        )

    critical = [a for a in alerts if a.severity is AlertSeverity.CRITICAL]
    answered = [q for q in questions if q.unavailable_reason is None]
    lead = next((q for q in questions if q.key == "q1_volume"), None)
    fact = (
        lead.answer
        if lead and lead.unavailable_reason is None
        else "本快照已完成采集。"
    )

    if alerts:
        meaning = f"{len(alerts)} 条 high/critical 告警已通过发布门" + (
            f"，其中 {len(critical)} 条为 critical。" if critical else "。"
        )
        action, href = "进入异常雷达认领处理", "/alerts"
    else:
        meaning = "没有达到管理层阈值的告警，市场结构处于常态区间。"
        action, href = "查看主题需求与覆盖缺口", "/themes"

    return SummaryLine(
        fact=fact,
        meaning=meaning,
        confidence=Confidence(
            level="high" if len(answered) == len(questions) else "medium",
            basis=f"四问中 {len(answered)}/{len(questions)} 项有可验证答案",
        ),
        action=action,
        action_href=href,
    )


def _highlights(
    alerts: list[Alert], evidence: dict[int, AlertEvidence], *, kind: str
) -> list[Highlight]:
    """Split the published findings into what to chase and what to watch out for.

    Direction is read from the detector rather than from the sign of a number: a
    collapse in liquidity and a surge in volume are both large moves, and only the
    detector knows which of them is good news.
    """
    wanted_up = kind == "opportunity"
    out: list[Highlight] = []
    for alert in alerts:
        if _is_rising(alert) is not wanted_up:
            continue
        row = evidence.get(alert.id)
        out.append(
            Highlight(
                kind="opportunity" if wanted_up else "risk",
                title=alert.headline_zh,
                detail=publication.family_prefix(alert.family),
                entity_type=alert.entity_type,
                entity_id=alert.entity_id,
                confidence=confidence_of(alert, row),
                counter_evidence=counter_evidence_of(row) if row else [],
                href=f"/alerts/{alert.id}",
                alert_id=alert.id,
            )
        )
    return out[:5]


def _kpis(data: ReportDataset, previous: ReportDataset) -> list[KpiCard]:
    """The five headline numbers, physically apart and never totalled."""
    current = _metrics(data)
    prior = _metrics(previous)
    # A comparison window in which nothing was observed is an absence of history, not
    # a baseline of zero — the usual state on the first day after a deployment.
    has_history = any(m.value.amount is not None for m in prior.values())

    hrefs = {
        "spot_market_cap": "/spot-scale",
        "spot_volume": "/spot-scale",
        "dex_liquidity": "/venues",
        "perp_volume": "/perps",
        "perp_oi": "/perps",
    }
    cards: list[KpiCard] = []
    for key in _ORDER:
        headline = current[key]
        label_zh, _ = _LABELS[key]
        cards.append(
            KpiCard(
                scope=headline.value.scope,
                label=label_zh,
                value=MetricValue.of(
                    headline.value,
                    observed_at=data.as_of,
                    window="24h" if "volume" in key else None,
                    source_count=headline.entity_count,
                ),
                change_pct=(
                    _change(headline.value.amount, prior[key].value.amount)
                    if has_history
                    else None
                ),
                change_basis="对比 24 小时前" if has_history else None,
                href=hrefs.get(key),
            )
        )
    return cards


def _drilldowns() -> list[DrillLink]:
    return [
        DrillLink(label="现货规模", href="/spot-scale", detail="市值与成交额分布"),
        DrillLink(label="场所结构", href="/venues", detail="CEX/DEX 竞争与集中度"),
        DrillLink(label="永续敞口", href="/perps", detail="跨所成交与未平仓"),
        DrillLink(label="主题需求", href="/themes", detail="需求主题与覆盖缺口"),
        DrillLink(label="数据质量", href="/data-quality", detail="结论是否可发布"),
    ]


# --- shared pieces -----------------------------------------------------------


def _management_alerts(session: Session) -> list[Alert]:
    """Published, open, high/critical, evidence complete (HOME-005).

    The SQL narrows the pool; :func:`publication.is_management_visible` decides. Two
    places stating the same rule would eventually state it differently, and the gate
    is the one that has to win.
    """
    stmt = (
        select(Alert)
        .where(Alert.published_at.isnot(None))
        .where(Alert.state.notin_(tuple(CLOSED_ALERT_STATES)))
        .where(Alert.severity.in_(tuple(publication.MANAGEMENT_SEVERITIES)))
        .order_by(Alert.score.desc(), Alert.last_seen_ts.desc())
        .limit(_ALERT_POOL)
    )
    return [
        alert
        for alert in session.execute(stmt).scalars().all()
        if publication.is_management_visible(alert)
    ]


def _latest_evidence(
    session: Session, alert_ids: list[int]
) -> dict[int, AlertEvidence]:
    """The newest evidence row per alert, in one query rather than one per card."""
    if not alert_ids:
        return {}
    rows = (
        session.execute(
            select(AlertEvidence)
            .where(AlertEvidence.alert_id.in_(alert_ids))
            .order_by(AlertEvidence.snapshot_ts.desc())
        )
        .scalars()
        .all()
    )
    latest: dict[int, AlertEvidence] = {}
    for row in rows:
        latest.setdefault(row.alert_id, row)
    return latest


def _candidate_rank(candidate: IssuanceCandidate) -> tuple[int, Decimal]:
    readiness = candidate_service.readiness_of(candidate)
    weakest = readiness.weakest
    return (
        candidate_service.STAGE_ORDER.index(candidate.stage),
        weakest[1] if weakest else Decimal(0),
    )


def _is_rising(alert: Alert) -> bool:
    """Whether the detector is reporting an increase.

    Read from the detector's own name rather than from the figure: the naming
    convention (``*_surge`` against ``*_drop``) is what the detector declares about
    its direction, and a magnitude alone cannot say which way is good news.
    """
    name = alert.detector.lower()
    falling = ("drop", "decline", "collapse", "drain", "stale", "gap", "divergence")
    return not any(token in name for token in falling)


def _volume_counter_evidence(
    raw: ScopedValue | None, adjusted: ScopedValue, venues: int
) -> list[CounterEvidenceItem]:
    counter = _raw_adjusted_counter(raw.amount if raw else None, adjusted.amount)
    if venues <= 1:
        counter.append(
            CounterEvidenceItem(
                code="single_venue",
                label="集中在单一场所",
                detail="单场所成交无法交叉验证，也更容易被一笔交易左右。",
            )
        )
    return counter


def _raw_adjusted_counter(
    raw: Decimal | None, adjusted: Decimal | None
) -> list[CounterEvidenceItem]:
    if raw is None or adjusted is None or raw <= 0:
        return []
    gap = (raw - adjusted) / raw
    if gap < _RAW_ADJUSTED_GAP:
        return []
    return [
        CounterEvidenceItem(
            code="raw_adjusted_gap",
            label=f"质量筛选剔除了 {gap:.0%} 的成交额",
            detail=(
                "原值与调整值差距过大，说明多数成交对被标记为异常或陈旧，"
                "两个数字讲的不是同一件事。"
            ),
        )
    ]


def _venue_count(data: ReportDataset, underlying_id: str) -> int:
    assets = {
        row.asset.asset_id
        for row in data.scoped_assets
        if row.asset.underlying_id == underlying_id
    }
    return len(
        {
            row.snapshot.venue_id
            for row in data.scoped_pairs
            if row.snapshot.asset_id in assets and row.snapshot.venue_id
        }
    )


def _unanswerable(
    key: str, question: str, reason: str, *, href: str, cta: str
) -> QuestionCard:
    """A card that says what it cannot answer, and why. Not an empty state."""
    return QuestionCard(
        key=key,  # type: ignore[arg-type]
        question=question,
        answer="暂无法判断",
        confidence=Confidence(level="low", basis=reason),
        cta_label=cta,
        cta_href=href,
        unavailable_reason=reason,
    )


def _change(current: Decimal | None, previous: Decimal | None) -> float | None:
    """Period-over-period change, or ``None`` when either side is unknown.

    Never zero: a change measured against a baseline nobody observed is fabricated,
    and it renders as a reassuring flat line.
    """
    if current is None or previous is None or previous == 0:
        return None
    return float((current - previous) / previous)


__all__ = ["router"]
