"""Product coverage: where market demand meets our own shelf.

"Theme X is heating up" is an observation. "Theme X is heating up and we list nothing
against four of its five largest underlyings" is a decision prompt. This module is
the join that turns the first into the second.

The demand figure carried on each coverage row is a single ``MetricScope``, stated
explicitly. Coverage gaps get sorted by size, and a sort that mixed spot turnover
with open interest would rank them by nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.metrics import MetricScope
from app.db.base import utcnow
from app.models.dimensions import DimOwnProduct, DimUnderlying
from app.models.enums import CoverageState
from app.models.workflow import ProductCoverage
from app.services.workflow import audit
from app.services.workflow.alert_lifecycle import Actor


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """One underlying with demand and no product of ours against it."""

    underlying_id: str
    underlying_name: str
    demand_value: Decimal | None
    demand_scope: MetricScope | None
    state: CoverageState
    note: str | None


def refresh(
    session: Session,
    *,
    demand_by_underlying: Mapping[str, Decimal | None],
    scope: MetricScope,
    actor: Actor | None = None,
) -> list[ProductCoverage]:
    """Re-assess coverage against a fresh demand reading.

    The demand map is passed in rather than computed here: this module is in the
    business layer and must not reach into the analytics rollups directly, or a
    coverage refresh becomes a second, divergent definition of "demand".

    A missing entry means we did not observe demand for that underlying this pass.
    It is left alone rather than written as zero — the same rule as everywhere else.
    """
    now = utcnow()
    products = {
        product.underlying_id: product
        for product in session.execute(
            select(DimOwnProduct).where(DimOwnProduct.is_active.is_(True))
        )
        .scalars()
        .all()
        if product.underlying_id
    }
    existing = {
        row.underlying_id: row
        for row in session.execute(select(ProductCoverage)).scalars().all()
    }

    touched: list[ProductCoverage] = []
    for underlying_id, demand in demand_by_underlying.items():
        if demand is None:
            continue
        product = products.get(underlying_id)
        row = existing.get(underlying_id)
        state = CoverageState.LIVE if product is not None else CoverageState.GAP

        if row is None:
            row = ProductCoverage(underlying_id=underlying_id)
            session.add(row)
            existing[underlying_id] = row
        # A manually recorded PLANNED or NOT_APPLICABLE is a human judgement and
        # outranks the automatic reading; overwriting it every hour would make the
        # page unusable for the people maintaining it.
        if row.state in (CoverageState.PLANNED, CoverageState.NOT_APPLICABLE):
            state = row.state

        row.state = state
        row.product_id = product.product_id if product else None
        row.demand_value = demand
        row.demand_scope = scope
        row.assessed_at = now
        row.assessed_by = actor.user_id if actor else None
        touched.append(row)

    return touched


def gaps(session: Session, *, limit: int = 50) -> list[CoverageGap]:
    """Underlyings with observed demand and nothing of ours against them.

    Sorted by demand descending, within one scope. Rows whose demand was never
    observed sort last rather than as zero.
    """
    stmt = (
        select(ProductCoverage, DimUnderlying)
        .join(
            DimUnderlying,
            DimUnderlying.underlying_id == ProductCoverage.underlying_id,
        )
        .where(ProductCoverage.state == CoverageState.GAP)
        .order_by(
            ProductCoverage.demand_value.is_(None),
            ProductCoverage.demand_value.desc(),
        )
        .limit(limit)
    )
    return [
        CoverageGap(
            underlying_id=row.underlying_id,
            underlying_name=underlying.name,
            demand_value=row.demand_value,
            demand_scope=row.demand_scope,
            state=row.state,
            note=row.note,
        )
        for row, underlying in session.execute(stmt).all()
    ]


def set_state(
    session: Session,
    coverage_row: ProductCoverage,
    actor: Actor,
    *,
    state: CoverageState,
    note: str | None = None,
    product_id: str | None = None,
) -> ProductCoverage:
    """Record a human judgement about coverage."""
    before = {
        "state": coverage_row.state.value,
        "product_id": coverage_row.product_id,
        "note": coverage_row.note,
    }
    coverage_row.state = state
    if product_id is not None:
        coverage_row.product_id = product_id
    if note is not None:
        coverage_row.note = note
    coverage_row.assessed_at = utcnow()
    coverage_row.assessed_by = actor.user_id

    audit.record(
        session,
        entity_kind="product_coverage",
        entity_id=coverage_row.underlying_id,
        action="set_state",
        actor_id=actor.user_id,
        actor_role=actor.role,
        before=before,
        after={
            "state": state.value,
            "product_id": coverage_row.product_id,
            "note": coverage_row.note,
        },
    )
    return coverage_row


def by_underlying(
    session: Session, underlying_ids: Sequence[str]
) -> dict[str, ProductCoverage]:
    if not underlying_ids:
        return {}
    stmt = select(ProductCoverage).where(
        ProductCoverage.underlying_id.in_(tuple(underlying_ids))
    )
    return {row.underlying_id: row for row in session.execute(stmt).scalars().all()}


__all__ = ["CoverageGap", "by_underlying", "gaps", "refresh", "set_state"]
