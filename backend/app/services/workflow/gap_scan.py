"""Turn the five Data Quality zones into rows somebody owns (DQ-001, DQ-002).

The Data Quality page can already show that a source is down, that a mapping is
unconfirmed, that a venue's adjusted turnover is a fraction of its raw. What it could
not do is remember. A red cell is information; a red cell nobody owns stays red.

So this scan reads exactly the same signals the page renders and emits a
:class:`~app.services.workflow.data_gap.GapCandidate` for each, which
:func:`~app.services.workflow.data_gap.reconcile` folds into the open queue by dedup
key. A source down for three days is one gap seen seventy times, not seventy gaps.

Nothing here closes a gap. A defect that stopped being detected may have stopped
because the scan itself broke, and clearing the queue in that case is the worst
available outcome — closing is a human action with a reason attached.

One kind per zone, matching the page:

``SOURCE_HEALTH``        a source failed or was throttled at the latest snapshot
``ENTITY_COVERAGE``      an in-scope entity has no observation at all
``QUALITY_DIVERGENCE``   adjusted turnover falls an order of magnitude below raw
``VERIFICATION``         a symbol nobody has confirmed maps to the underlying claimed
``BASELINE_HEALTH``      a time-series detector cannot fire because history is thin
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.enums import (
    DataGapKind,
    EntityType,
    FetchStatus,
    MappingStatus,
)
from app.models.operations import BaselineSnapshot, FetchLog, UnderlyingMap
from app.models.workflow import DataGap
from app.services.normalize.quality import Pair, screen
from app.services.report.dataset import ReportDataset
from app.services.workflow.data_gap import GapCandidate, reconcile

logger = logging.getLogger(__name__)

#: Fetch outcomes that mean the observation is missing rather than zero. ``PARTIAL``
#: is deliberately absent: a partial read is a degraded number, which the value object
#: already carries, not an absent one.
FAILED_FETCH = (
    FetchStatus.NOT_VERIFIED,
    FetchStatus.RATE_LIMITED,
    FetchStatus.ERROR,
)

#: Below this, a baseline exists but is too thin for a ``T*`` detector to fire on.
#: Same number as the cold-start gate, and deliberately so — a gap that does not match
#: the rule it describes sends someone to fix the wrong thing.
MIN_BASELINE_SAMPLES = 14


def scan(
    session: Session, data: ReportDataset, *, now: datetime | None = None
) -> tuple[list[DataGap], list[DataGap]]:
    """Run every zone and reconcile the findings. Returns ``(opened, recurring)``."""
    candidates: list[GapCandidate] = [
        *_source_health(data),
        *_entity_coverage(data),
        *_quality_divergence(data),
        *_verification(session),
        *_baseline_health(session),
    ]
    opened, recurring = reconcile(session, candidates, now=now)
    logger.info(
        "gap scan: %d candidates, %d newly opened, %d recurring",
        len(candidates),
        len(opened),
        len(recurring),
    )
    return opened, recurring


def _source_health(data: ReportDataset) -> list[GapCandidate]:
    """Sources whose most recent attempt did not produce an observation.

    Keyed on the source alone, not on the timestamp: a source that has been failing
    since Tuesday is one piece of work, and re-keying it per snapshot would produce a
    queue that grows every fifteen minutes and that nobody can finish.
    """
    latest: dict[str, FetchLog] = {}
    for entry in data.fetch_log:
        current = latest.get(entry.source_id)
        if current is None or entry.snapshot_ts > current.snapshot_ts:
            latest[entry.source_id] = entry

    out = []
    for source_id, entry in sorted(latest.items()):
        if entry.status not in FAILED_FETCH:
            continue
        out.append(
            GapCandidate(
                kind=DataGapKind.SOURCE_HEALTH,
                dedup_key=f"source_health:{source_id}",
                title=f"数据源 {source_id} 最近一次抓取未取得观测",
                detail=(
                    f"状态 {entry.status.value}，最后尝试 "
                    f"{entry.snapshot_ts:%Y-%m-%d %H:%M} UTC。"
                    + (f" 错误：{entry.error_message}" if entry.error_message else "")
                ),
                blocks="该源覆盖的所有指标显示为「未验证」，不得按 0 读取。",
                source_id=source_id,
                # A dead source does not merely degrade a figure; it removes it. Any
                # conclusion that rests on it is unpublishable until this is fixed.
                is_blocking=True,
            )
        )
    return out


def _entity_coverage(data: ReportDataset) -> list[GapCandidate]:
    """In-scope wrappers with no turnover observation anywhere.

    Not the same as a quiet product. A wrapper that trades nowhere we can see is a
    wrapper we cannot rank, cannot alert on, and must not report as zero.
    """
    observed = {p.snapshot.asset_id for p in data.scoped_pairs}
    out = []
    for scoped in data.scoped_assets:
        asset_id = scoped.asset.asset_id
        if asset_id in observed:
            continue
        out.append(
            GapCandidate(
                kind=DataGapKind.ENTITY_COVERAGE,
                dedup_key=f"entity_coverage:asset:{asset_id}",
                title=f"{scoped.asset.symbol} 没有任何场所的成交观测",
                detail=(
                    "该包装在 dim_asset 中在范围内，但当前快照下没有任何 pair 行。"
                    "它既不能进入排名，也不能作为 0 呈现。"
                ),
                blocks="该包装在规模、场所与主题统计中缺席，占比分母因此偏小。",
                entity_type=EntityType.ASSET,
                entity_id=asset_id,
                is_blocking=False,
            )
        )
    return out


def _quality_divergence(data: ReportDataset) -> list[GapCandidate]:
    """Venues where adjusted turnover collapses against raw.

    Reference case: about $29.3mn raw against about $216 adjusted, 17 of 19 pairs
    flagged. Neither figure alone is reportable, so the venue is named and both
    numbers are carried — and somebody has to decide which one the venue page leads
    with.
    """
    by_venue: dict[str, list[Pair]] = {}
    for pair in data.scoped_pairs:
        by_venue.setdefault(pair.snapshot.venue_id, []).append(
            Pair(
                pair_id=f"{pair.snapshot.asset_id}@{pair.snapshot.venue_id}",
                volume_usd=pair.snapshot.raw_vol_24h,
                is_quality_anomaly=pair.snapshot.is_quality_anomaly,
                is_quality_stale=pair.snapshot.is_quality_stale,
            )
        )

    names = {v.venue_id: v.name for v in data.venues}
    out = []
    for venue_id, pairs in sorted(by_venue.items()):
        result = screen(pairs)
        if not result.is_materially_divergent:
            continue
        out.append(
            GapCandidate(
                kind=DataGapKind.QUALITY_DIVERGENCE,
                dedup_key=f"quality_divergence:venue:{venue_id}",
                title=f"{names.get(venue_id, venue_id)} 的调整后成交额远低于原始值",
                detail=(
                    f"{result.flagged_pairs}/{result.total_pairs} 个交易对被标记为 "
                    "anomaly 或 stale。原始与调整后必须并列展示。"
                ),
                blocks="该场所的成交额不能单独引用，排名须以调整后为准并说明原因。",
                entity_type=EntityType.VENUE,
                entity_id=venue_id,
                impact_score=_divergence_impact(
                    result.flagged_pairs, result.total_pairs
                ),
                is_blocking=True,
            )
        )
    return out


def _divergence_impact(flagged: int, total: int) -> Decimal | None:
    """How much of the venue is affected, as a 0–1 ratio for queue ordering.

    A share, not an amount — the queue sorts on it and never sums it.
    """
    if total <= 0:
        return None
    return Decimal(flagged) / Decimal(total)


def _verification(session: Session) -> list[GapCandidate]:
    """Symbols mapped to an underlying that nobody has confirmed.

    GOLD, GOLDJM and GLDMINE are three different underlyings. An unconfirmed guess is
    worse than a gap, because it lands inside a total that reads as authoritative.
    """
    rows = list(
        session.execute(
            select(UnderlyingMap)
            .where(UnderlyingMap.status == MappingStatus.PENDING_REVIEW)
            .order_by(UnderlyingMap.source_symbol)
        )
        .scalars()
        .all()
    )
    return [
        GapCandidate(
            kind=DataGapKind.VERIFICATION,
            dedup_key=f"verification:mapping:{row.source_id}:{row.source_symbol}",
            title=f"{row.source_symbol} 的底层映射待人工确认",
            detail=(
                f"来源 {row.source_id} 的 {row.source_symbol} 被推断为 "
                f"{row.underlying_id}，尚无人确认。"
            ),
            blocks="该符号的成交额计入了一个未经确认的底层，相关排名不可发布。",
            entity_type=EntityType.UNDERLYING,
            entity_id=row.underlying_id,
            source_id=row.source_id,
            is_blocking=True,
        )
        for row in rows
    ]


def _baseline_health(session: Session) -> list[GapCandidate]:
    """Entities whose baseline is too thin for a time-series detector to fire.

    One row per entity type rather than per entity. "Forty-three contracts are still
    in cold start" is a piece of work; forty-three separate rows saying the same thing
    is a queue nobody opens twice.
    """
    newest = session.execute(
        select(func.max(BaselineSnapshot.snapshot_ts))
    ).scalar_one_or_none()
    if newest is None:
        return [
            GapCandidate(
                kind=DataGapKind.BASELINE_HEALTH,
                dedup_key="baseline_health:none",
                title="尚未计算过任何基线",
                detail=(
                    "没有 baseline_snapshot 行，时间序列检测器（T*）全部静默。"
                    "横截面检测器（X*）不受影响。"
                ),
                blocks="所有 T* 结论不可用；「异常放量」类判断暂时无法做出。",
                is_blocking=False,
            )
        ]

    rows = session.execute(
        select(
            BaselineSnapshot.entity_type,
            func.count(),
            func.sum(
                case((BaselineSnapshot.sample_size < MIN_BASELINE_SAMPLES, 1), else_=0)
            ),
        )
        .where(BaselineSnapshot.snapshot_ts == newest)
        .group_by(BaselineSnapshot.entity_type)
    ).all()

    out = []
    for entity_type, total_raw, thin_raw in rows:
        thin, total = int(thin_raw or 0), int(total_raw)
        if not thin:
            continue
        out.append(
            GapCandidate(
                kind=DataGapKind.BASELINE_HEALTH,
                dedup_key=f"baseline_health:{entity_type.value}",
                title=f"{thin}/{total} 个 {entity_type.value} 基线样本不足",
                detail=(
                    f"少于 {MIN_BASELINE_SAMPLES} 个同 session 观测，仍处于冷启动。"
                    "T* 检测器在这些实体上不会触发，这是设计如此，不是故障。"
                ),
                blocks="这些实体只能得到 X*（同组对比）结论，没有「与自身历史相比」的判断。",
                entity_type=entity_type,
                impact_score=Decimal(thin) / Decimal(total),
                is_blocking=False,
            )
        )
    return out


__all__ = ["FAILED_FETCH", "MIN_BASELINE_SAMPLES", "scan"]
