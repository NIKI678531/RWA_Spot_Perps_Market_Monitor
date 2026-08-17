"""Demand by theme — the level a product decision is actually made at.

An issuer does not decide to launch "SPYB"; it decides whether there is demand for
broad index exposure, or for pre-IPO names, or for memory semiconductors. Themes cut
across issuers and venues, so they are the only grouping where that question has an
answer.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from fastapi import APIRouter

from app.api.deps import DatasetDep
from app.core.metrics import MetricScope, ScopedValue, safe_sum
from app.schemas.common import Amount, Meta
from app.schemas.market import ThemeList, ThemeRow
from app.services.report.dataset import (
    UnderlyingAggregates,
    amount_of,
    scoped,
    sort_by_amount,
)

router = APIRouter(tags=["themes"])


@router.get("/themes", response_model=ThemeList)
def themes(data: DatasetDep) -> ThemeList:
    demand = UnderlyingAggregates(data)
    registry = {t.theme_id: t for t in data.themes}

    spot: dict[str, list[Decimal | None]] = {}
    perp: dict[str, list[Decimal | None]] = {}
    members: dict[str, int] = {}
    for underlying in data.underlyings:
        theme_id = underlying.theme_id
        if not theme_id:
            continue
        members[theme_id] = members.get(theme_id, 0) + 1
        spot.setdefault(theme_id, []).append(
            amount_of(demand.spot_adjusted, underlying.underlying_id)
        )
        perp.setdefault(theme_id, []).append(
            amount_of(demand.perp_volume, underlying.underlying_id)
        )

    spot_totals = {
        key: _total(values, MetricScope.SPOT_VOLUME) for key, values in spot.items()
    }
    perp_totals = {
        key: _total(values, MetricScope.PERP_VOLUME) for key, values in perp.items()
    }

    rows = [
        ThemeRow(
            theme_id=key,
            name_zh=registry[key].name_zh if key in registry else None,
            name_en=registry[key].name_en if key in registry else None,
            underlying_count=members.get(key, 0),
            spot_vol_adjusted=Amount.of(spot_totals[key]),
            perp_vol_24h=Amount.of(
                perp_totals.get(key, scoped(None, MetricScope.PERP_VOLUME))
            ),
        )
        for key in sort_by_amount(list(spot_totals), spot_totals)
    ]
    return ThemeList(
        meta=Meta(
            as_of=data.as_of,
            scopes=[MetricScope.SPOT_VOLUME, MetricScope.PERP_VOLUME],
            note=(
                "Spot and perpetual turnover are separate scopes and are listed, not "
                "totalled. On the reference dataset demand clustered in what retail "
                "cannot otherwise buy — pre-IPO names, memory semiconductors, "
                "commodities — rather than in blue chips."
            ),
            row_count=len(rows),
        ),
        rows=rows,
    )


def _total(values: Sequence[Decimal | None], scope: MetricScope) -> ScopedValue:
    if not values:
        return scoped(None, scope)
    return safe_sum([scoped(v, scope) for v in values])
