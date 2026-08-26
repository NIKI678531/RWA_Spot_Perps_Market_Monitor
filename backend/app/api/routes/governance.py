"""Data quality as a work queue, and product coverage as a decision prompt.

Two surfaces, one idea: a red cell nobody owns stays red. The quality page is not a
status board — every defect it can show is a row with a dedup key, an owner slot and a
resolution, and the page states plainly whether a conclusion is publishable at all.

Coverage is the join that turns "theme X is heating up" into "theme X is heating up and
we list nothing against four of its five largest underlyings".
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Limit, SessionDep, WriterDep
from app.api.naming import Resolver
from app.core.metrics import SCOPE_DIMENSION
from app.db.base import utcnow
from app.models.enums import (
    CoverageState,
    DataGapKind,
    EntityType,
    VerificationStatus,
)
from app.models.workflow import DataGap, ProductCoverage
from app.schemas.common import Meta, MetricValue
from app.schemas.workflow import (
    CoverageList,
    CoverageRow,
    DataGapQueue,
    DataGapRow,
    DataGapZone,
    GapAssignRequest,
    GapCloseRequest,
)
from app.services.workflow import data_gap as gap_service

router = APIRouter(tags=["governance"])

#: The five zones, in the order the page renders them. Source health first: a source
#: that did not answer explains most of what is wrong further down, and fixing the
#: others first is wasted work.
_ZONES: tuple[tuple[DataGapKind, str], ...] = (
    (DataGapKind.SOURCE_HEALTH, "数据源健康"),
    (DataGapKind.ENTITY_COVERAGE, "实体覆盖"),
    (DataGapKind.QUALITY_DIVERGENCE, "原值与调整值背离"),
    (DataGapKind.VERIFICATION, "验证状态"),
    (DataGapKind.BASELINE_HEALTH, "基线健康度"),
)

_GAP_NOTE = (
    "五个区各自独立，不合并计分。存在未关闭的阻断项时，本快照不得用于新的"
    "管理层结论——「未验证」不是 0，也不是「正常」。"
)

_COVERAGE_NOTE = (
    "需求值只在单一口径内排序。缺口按需求规模降序，未观测到需求的标的排在最后，"
    "不按 0 处理。"
)


@router.get("/data-gaps", response_model=DataGapQueue)
def data_gaps(
    session: SessionDep,
    kind: DataGapKind | None = Query(default=None),
    owner_id: str | None = Query(
        default=None, description="Filter by owner. Pass `unassigned` for the pool."
    ),
    only_blocking: bool = Query(default=False),
    limit: Limit = 200,
) -> DataGapQueue:
    stmt = select(DataGap).where(DataGap.status.in_(gap_service.OPEN_STATUSES))
    if kind is not None:
        stmt = stmt.where(DataGap.kind == kind)
    if owner_id == "unassigned":
        stmt = stmt.where(DataGap.owner_id.is_(None))
    elif owner_id:
        stmt = stmt.where(DataGap.owner_id == owner_id)
    if only_blocking:
        stmt = stmt.where(DataGap.is_blocking.is_(True))

    # Blocking first, then impact, then age. A gap that blocks publication outranks a
    # larger one that merely degrades a figure, and an unscored gap sorts below both
    # rather than above them.
    rows = list(
        session.execute(
            stmt.order_by(
                DataGap.is_blocking.desc(),
                DataGap.impact_score.is_(None),
                DataGap.impact_score.desc(),
                DataGap.first_seen_ts,
            ).limit(limit)
        )
        .scalars()
        .all()
    )

    by_kind: dict[DataGapKind, list[DataGap]] = {k: [] for k, _ in _ZONES}
    for row in rows:
        by_kind.setdefault(row.kind, []).append(row)

    zones = [
        DataGapZone(
            kind=zone_kind,
            label=label,
            open_count=len(by_kind.get(zone_kind, [])),
            blocking_count=sum(1 for r in by_kind.get(zone_kind, []) if r.is_blocking),
            rows=[gap_row(r) for r in by_kind.get(zone_kind, [])],
        )
        for zone_kind, label in _ZONES
        if kind is None or zone_kind is kind
    ]

    # Asked of the whole open queue, not of the filtered page. Whether a conclusion is
    # publishable cannot depend on which zone the reader happened to click.
    blocked = session.execute(
        select(func.count())
        .select_from(DataGap)
        .where(DataGap.status.in_(gap_service.OPEN_STATUSES))
        .where(DataGap.is_blocking.is_(True))
    ).scalar_one()

    return DataGapQueue(
        meta=Meta(as_of=utcnow(), note=_GAP_NOTE, row_count=len(rows)),
        zones=zones,
        publication_blocked=bool(blocked),
    )


@router.post("/data-gaps/{gap_id}/assign", response_model=DataGapRow)
def assign_gap(
    gap_id: int, payload: GapAssignRequest, session: SessionDep, actor: WriterDep
) -> DataGapRow:
    gap = _load_gap(session, gap_id)
    gap_service.assign(session, gap, actor, owner_id=payload.owner_id)
    session.commit()
    return gap_row(gap)


@router.post("/data-gaps/{gap_id}/close", response_model=DataGapRow)
def close_gap(
    gap_id: int, payload: GapCloseRequest, session: SessionDep, actor: WriterDep
) -> DataGapRow:
    """Close a gap as fixed, or accept it with a stated reason.

    ``ACCEPTED`` is a real outcome — some sources cannot be collected without
    credentials nobody has. Recording that keeps the queue honest; leaving such a gap
    permanently open trains people to ignore the whole page.
    """
    gap = _load_gap(session, gap_id)
    try:
        gap_service.close(session, gap, actor, status=payload.status, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return gap_row(gap)


@router.get("/coverage", response_model=CoverageList)
def coverage(
    session: SessionDep,
    state: CoverageState | None = Query(default=None),
    limit: Limit = 200,
) -> CoverageList:
    stmt = select(ProductCoverage)
    if state is not None:
        stmt = stmt.where(ProductCoverage.state == state)
    rows = list(
        session.execute(
            stmt.order_by(
                ProductCoverage.demand_value.is_(None),
                ProductCoverage.demand_value.desc(),
            ).limit(limit)
        )
        .scalars()
        .all()
    )
    resolver = Resolver(session)
    scopes = sorted(
        {row.demand_scope for row in rows if row.demand_scope is not None},
        key=lambda s: s.value,
    )
    return CoverageList(
        meta=Meta(
            as_of=utcnow(),
            scopes=scopes,
            note=_COVERAGE_NOTE,
            row_count=len(rows),
        ),
        rows=[coverage_row(row, resolver) for row in rows],
    )


# --- helpers -----------------------------------------------------------------


def _load_gap(session: Session, gap_id: int) -> DataGap:
    gap = session.get(DataGap, gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail=f"unknown data gap {gap_id}")
    if gap.status not in gap_service.OPEN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"data gap {gap_id} is already {gap.status.value}",
        )
    return gap


def gap_row(row: DataGap) -> DataGapRow:
    return DataGapRow(
        id=row.id,
        kind=row.kind,
        status=row.status,
        title=row.title,
        detail=row.detail,
        blocks=row.blocks,
        impact_score=row.impact_score,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        source_id=row.source_id,
        owner_id=row.owner_id,
        is_blocking=row.is_blocking,
        first_seen_ts=row.first_seen_ts,
        last_seen_ts=row.last_seen_ts,
        occurrence_count=row.occurrence_count,
    )


def coverage_row(row: ProductCoverage, resolver: Resolver) -> CoverageRow:
    demand = None
    if row.demand_scope is not None:
        demand = MetricValue(
            value=row.demand_value,
            metric_scope=row.demand_scope,
            metric_dimension=SCOPE_DIMENSION[row.demand_scope],
            # An assessed figure that was never observed is missing, not zero: the
            # coverage refresh skips underlyings it saw no demand for.
            verification_status=(
                VerificationStatus.VERIFIED
                if row.demand_value is not None
                else VerificationStatus.NOT_VERIFIED
            ),
            observed_at=row.assessed_at,
            window="24h",
        )
    return CoverageRow(
        underlying_id=row.underlying_id,
        underlying_name=resolver.name(EntityType.UNDERLYING, row.underlying_id),
        state=row.state,
        product_id=row.product_id,
        demand=demand,
        note=row.note,
        assessed_at=row.assessed_at,
    )


__all__ = ["coverage_row", "gap_row", "router"]
