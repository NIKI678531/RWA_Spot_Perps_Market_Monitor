"""Generic history for any entity the warehouse tracks.

One endpoint rather than a per-page chart endpoint, because every fact table has the
same shape: an entity key, a ``snapshot_ts``, and a nullable money column. The
registry below is the whole of the mapping, and its real job is to attach the right
``MetricScope`` to each series — a chart that does not know whether it holds a stock
or a flow will eventually put them on one axis.

Rows written by carry-forward are returned flagged rather than dropped. They are what
the system believed at the time, and hiding them would make a gap in collection look
like a stable market.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import InstrumentedAttribute

from app.api.deps import SessionDep
from app.core.metrics import MetricScope
from app.models.enums import EntityType
from app.models.facts import (
    FactAssetSnapshot,
    FactCategorySnapshot,
    FactPairSnapshot,
    FactPerpContractSnapshot,
    FactPerpVenueSnapshot,
    FactPoolSnapshot,
    FactVenueSnapshot,
)
from app.schemas.common import Meta
from app.schemas.market import Timeseries, TimeseriesPoint

router = APIRouter(tags=["timeseries"])

#: Hard ceiling on points returned. A year of 15-minute snapshots is ~35,000 rows,
#: which no chart can draw and no browser should receive.
_MAX_POINTS = 2000


@dataclass(frozen=True, slots=True)
class _Series:
    """How to read one metric out of one fact table."""

    model: type[Any]
    column: InstrumentedAttribute[Any]
    scope: MetricScope
    #: Columns forming the entity key, in the order ``entity_id`` spells them.
    keys: tuple[InstrumentedAttribute[Any], ...]
    separator: str = ":"


def _series(
    model: type[Any],
    column: str,
    scope: MetricScope,
    keys: Sequence[str],
    separator: str = ":",
) -> _Series:
    return _Series(
        model=model,
        column=getattr(model, column),
        scope=scope,
        keys=tuple(getattr(model, k) for k in keys),
        separator=separator,
    )


#: (entity_type, metric) -> where the series lives. Anything absent is a 400 rather
#: than an empty chart, so a typo in a metric name is visible immediately.
_REGISTRY: dict[tuple[EntityType, str], _Series] = {
    (EntityType.ASSET, "price_usd"): _series(
        FactAssetSnapshot, "price_usd", MetricScope.SPOT_MARKET_CAP, ["asset_id"]
    ),
    (EntityType.ASSET, "market_cap"): _series(
        FactAssetSnapshot, "market_cap", MetricScope.SPOT_MARKET_CAP, ["asset_id"]
    ),
    (EntityType.ASSET, "vol_24h"): _series(
        FactAssetSnapshot, "vol_24h", MetricScope.SPOT_VOLUME, ["asset_id"]
    ),
    (EntityType.PAIR, "raw_vol_24h"): _series(
        FactPairSnapshot,
        "raw_vol_24h",
        MetricScope.SPOT_VOLUME,
        ["asset_id", "venue_id"],
        separator="@",
    ),
    (EntityType.PAIR, "adjusted_vol_24h"): _series(
        FactPairSnapshot,
        "adjusted_vol_24h",
        MetricScope.SPOT_VOLUME,
        ["asset_id", "venue_id"],
        separator="@",
    ),
    (EntityType.VENUE, "raw_vol_24h"): _series(
        FactVenueSnapshot, "raw_vol_24h", MetricScope.SPOT_VOLUME, ["venue_id"]
    ),
    (EntityType.VENUE, "adjusted_vol_24h"): _series(
        FactVenueSnapshot, "adjusted_vol_24h", MetricScope.SPOT_VOLUME, ["venue_id"]
    ),
    (EntityType.POOL, "reserve_usd"): _series(
        FactPoolSnapshot, "reserve_usd", MetricScope.DEX_LIQUIDITY, ["pool_id"]
    ),
    (EntityType.POOL, "vol_24h"): _series(
        FactPoolSnapshot, "vol_24h", MetricScope.SPOT_VOLUME, ["pool_id"]
    ),
    (EntityType.PERP_CONTRACT, "vol_24h"): _series(
        FactPerpContractSnapshot, "vol_24h", MetricScope.PERP_VOLUME, ["contract_id"]
    ),
    (EntityType.PERP_CONTRACT, "oi_usd"): _series(
        FactPerpContractSnapshot, "oi_usd", MetricScope.PERP_OI, ["contract_id"]
    ),
    (EntityType.PERP_VENUE, "vol_24h"): _series(
        FactPerpVenueSnapshot,
        "vol_24h",
        MetricScope.PERP_VOLUME,
        ["exchange", "perp_dex", "segment"],
    ),
    (EntityType.PERP_VENUE, "open_interest_usd"): _series(
        FactPerpVenueSnapshot,
        "open_interest_usd",
        MetricScope.PERP_OI,
        ["exchange", "perp_dex", "segment"],
    ),
    (EntityType.CATEGORY, "market_cap"): _series(
        FactCategorySnapshot, "market_cap", MetricScope.SPOT_MARKET_CAP, ["category_id"]
    ),
    (EntityType.CATEGORY, "vol_24h"): _series(
        FactCategorySnapshot, "vol_24h", MetricScope.SPOT_VOLUME, ["category_id"]
    ),
}


@router.get("/timeseries", response_model=Timeseries)
def timeseries(
    session: SessionDep,
    entity_type: EntityType,
    entity_id: str = Query(
        description=(
            "The entity key. Composite keys are joined by ':', except pairs which "
            "use 'asset_id@venue_id'."
        )
    ),
    metric: str = Query(description="Column name, e.g. adjusted_vol_24h."),
    days: int = Query(default=30, ge=1, le=730),
    until: datetime | None = Query(default=None),
) -> Timeseries:
    spec = _REGISTRY.get((entity_type, metric))
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no series for {entity_type.value}.{metric}; available: "
                + ", ".join(sorted(f"{e.value}.{m}" for e, m in _REGISTRY))
            ),
        )

    end = until or datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    stmt = (
        select(spec.model)
        .where(spec.model.snapshot_ts >= start, spec.model.snapshot_ts <= end)
        .order_by(spec.model.snapshot_ts.desc())
        .limit(_MAX_POINTS)
    )
    for condition in _key_filters(spec, entity_id):
        stmt = stmt.where(condition)

    records = list(session.execute(stmt).scalars().all())
    records.reverse()  # queried newest-first to cap, charted oldest-first

    points = [
        TimeseriesPoint(
            snapshot_ts=record.snapshot_ts,
            value=getattr(record, spec.column.key),
            market_session=record.market_session,
            is_carried_forward=record.is_carried_forward,
        )
        for record in records
    ]
    return Timeseries(
        meta=Meta(
            as_of=points[-1].snapshot_ts if points else end,
            scopes=[spec.scope],
            note=(
                "A null value is a failed observation, not a zero, and must render as "
                "a gap rather than a drop to the axis. is_carried_forward marks a "
                "value reused after a failed fetch; baselines exclude those rows."
            ),
            row_count=len(points),
        ),
        entity_type=entity_type,
        entity_id=entity_id,
        metric=metric,
        scope=spec.scope,
        points=points,
    )


def _key_filters(spec: _Series, entity_id: str) -> list[ColumnElement[bool]]:
    """Split a composite entity id across the columns that form the key."""
    parts = entity_id.split(spec.separator)
    if len(parts) != len(spec.keys):
        expected = spec.separator.join(k.key for k in spec.keys)
        raise HTTPException(
            status_code=400,
            detail=f"entity_id must be spelled {expected!r}, got {entity_id!r}",
        )
    return [column == value for column, value in zip(spec.keys, parts)]
