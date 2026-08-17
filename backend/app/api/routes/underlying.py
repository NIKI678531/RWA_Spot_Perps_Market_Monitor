"""The 360 view of one real-world security.

"Is anyone buying the S&P 500?" cannot be answered from a token symbol. SPY arrives
as SPYB, SPYx and SPY-ON from three issuers on four venues, plus a perpetual. This
endpoint is where those rows become one answer — reported by scope, never totalled
across scopes.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DatasetDep, SessionDep
from app.api.routes.alerts import alert_row
from app.core.metrics import MetricScope
from app.models.alerts import Alert
from app.models.enums import EntityType
from app.schemas.common import Amount, Meta
from app.schemas.market import (
    AlertRow,
    PerpExposureRow,
    Underlying360,
    VenueBreakdownRow,
    WrapperRow,
)
from app.services.report.dataset import (
    UnderlyingAggregates,
    group_sum,
    scoped,
    sort_by_amount,
)

router = APIRouter(tags=["underlying"])

_SPOT = MetricScope.SPOT_VOLUME

_SCOPE_NOTE = (
    "现货成交与永续成交为不同口径，页面并列展示，不提供合计。"
    "市值为存量、成交为流量、持仓量为存量，三者不可相加。"
)


@router.get("/underlying/{underlying_id}", response_model=Underlying360)
def underlying(
    underlying_id: str, data: DatasetDep, session: SessionDep
) -> Underlying360:
    record = next(
        (u for u in data.underlyings if u.underlying_id == underlying_id), None
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"unknown underlying {underlying_id!r}"
        )

    demand = UnderlyingAggregates(data)
    wrappers = [a for a in data.scoped_assets if a.asset.underlying_id == underlying_id]
    wrapper_ids = {a.asset.asset_id for a in wrappers}
    pairs = [p for p in data.scoped_pairs if p.snapshot.asset_id in wrapper_ids]
    perps = [
        r
        for r in data.perp_contracts
        if r.contract and r.contract.underlying_id == underlying_id
    ]

    venue_totals = group_sum(
        pairs,
        lambda p: p.snapshot.venue_id,
        lambda p: p.snapshot.adjusted_vol_24h,
        _SPOT,
    )
    venues = {v.venue_id: v for v in data.venues}

    return Underlying360(
        meta=Meta(
            as_of=data.as_of,
            scopes=[
                MetricScope.SPOT_MARKET_CAP,
                _SPOT,
                MetricScope.PERP_VOLUME,
                MetricScope.PERP_OI,
            ],
            note=_SCOPE_NOTE,
            row_count=len(wrappers),
        ),
        underlying_id=underlying_id,
        name=record.name,
        asset_class=record.asset_class,
        region=record.region,
        is_pre_ipo=record.is_pre_ipo,
        theme_id=record.theme_id,
        benchmark_id=record.benchmark_id,
        tokenized_wrappers=[
            WrapperRow(
                asset_id=a.asset.asset_id,
                symbol=a.asset.symbol,
                issuer=a.issuer.name if a.issuer else None,
                chain=a.asset.chain,
                rwa_tier=a.asset.rwa_tier,
                market_cap=Amount.raw(
                    a.snapshot.market_cap if a.snapshot else None,
                    MetricScope.SPOT_MARKET_CAP,
                ),
                vol_24h=Amount.raw(a.snapshot.vol_24h if a.snapshot else None, _SPOT),
            )
            for a in sorted(wrappers, key=lambda a: a.asset.symbol)
        ],
        venue_breakdown=[
            VenueBreakdownRow(
                venue_id=key,
                venue=venues[key].name if key in venues else key,
                venue_type=venues[key].venue_type if key in venues else None,
                adjusted_vol_24h=Amount.of(venue_totals[key]),
            )
            for key in sort_by_amount(list(venue_totals), venue_totals)
        ],
        perp_exposure=[
            PerpExposureRow(
                exchange=r.exchange,
                perp_dex=r.perp_dex or "core",
                contract=r.symbol,
                vol_24h=Amount.raw(r.snapshot.vol_24h, MetricScope.PERP_VOLUME),
                open_interest_usd=Amount.raw(r.snapshot.oi_usd, MetricScope.PERP_OI),
            )
            for r in sorted(
                perps,
                key=lambda r: (
                    r.snapshot.vol_24h is None,
                    -(r.snapshot.vol_24h or Decimal(0)),
                ),
            )
        ],
        spot_market_cap=Amount.of(
            demand.market_cap.get(
                underlying_id, scoped(None, MetricScope.SPOT_MARKET_CAP)
            )
        ),
        spot_vol_adjusted=Amount.of(
            demand.spot_adjusted.get(underlying_id, scoped(None, _SPOT))
        ),
        perp_vol_24h=Amount.of(
            demand.perp_volume.get(underlying_id, scoped(None, MetricScope.PERP_VOLUME))
        ),
        perp_oi_usd=Amount.of(
            demand.perp_oi.get(underlying_id, scoped(None, MetricScope.PERP_OI))
        ),
        scope_note=_SCOPE_NOTE,
        active_alerts=_alerts(session, underlying_id),
    )


def _alerts(session: Session, underlying_id: str) -> list[AlertRow]:
    """Open alerts naming this underlying.

    Only ``UNDERLYING``-scoped alerts: an alert on one wrapper is about that wrapper's
    listing, and surfacing it here would attribute a venue problem to the security.
    """
    stmt = (
        select(Alert)
        .where(
            Alert.entity_type == EntityType.UNDERLYING,
            Alert.entity_id == underlying_id,
            Alert.resolved_ts.is_(None),
        )
        .order_by(Alert.score.desc(), Alert.last_seen_ts.desc())
    )
    return [alert_row(a) for a in session.execute(stmt).scalars().all()]
