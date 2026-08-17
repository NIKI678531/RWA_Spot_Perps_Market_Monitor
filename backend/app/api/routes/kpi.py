"""Executive KPIs: five headline numbers that are never combined.

The screen shows them side by side because that is the only honest arrangement. A
"total RWA market" figure would have to add a market capitalisation to a 24-hour
turnover to a pool reserve, and the result would be a large number describing
nothing.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter

from app.api.deps import AsOf, DatasetDep, SessionDep
from app.core.metrics import MetricScope, ScopedValue, safe_sum
from app.schemas.common import Amount, Meta
from app.schemas.market import ExecutiveKpi, Kpi
from app.services.report.dataset import ReportDataset, load, scoped

router = APIRouter(tags=["kpi"])

#: How far back the period-over-period comparison reaches. A day, not a snapshot:
#: at a 15-minute cadence the previous snapshot is noise, and RWA underlyings move
#: on a daily rhythm because their real-world markets do.
_COMPARISON_WINDOW = timedelta(hours=24)


@router.get("/kpi/executive", response_model=ExecutiveKpi)
def executive(
    data: DatasetDep, session: SessionDep, as_of: AsOf = None
) -> ExecutiveKpi:
    cutoff = data.as_of - _COMPARISON_WINDOW
    current_metrics = _metrics(data)
    previous_metrics = _metrics(load(session, cutoff))
    # A comparison window in which nothing at all was observed is not a baseline of
    # zero, it is an absence of history — the usual state for the first day after
    # deployment. Reporting it as a baseline would render every KPI as +infinity.
    has_history = any(m.value.amount is not None for m in previous_metrics.values())

    return ExecutiveKpi(
        meta=Meta(
            as_of=data.as_of,
            scopes=list(MetricScope),
            note=(
                "五个口径互不相加。市值是存量、成交是流量、持仓量是存量,"
                "任何跨口径合计都没有意义。"
            ),
            row_count=len(current_metrics),
        ),
        previous_as_of=cutoff if has_history else None,
        metrics=[
            _kpi(
                key,
                current_metrics[key],
                previous_metrics[key] if has_history else None,
            )
            for key in _ORDER
        ],
    )


_LABELS: dict[str, tuple[str, str]] = {
    "spot_market_cap": ("代币化市值", "Tokenized market cap"),
    "spot_volume": ("现货成交额 24h（质量调整）", "Spot volume 24h (adjusted)"),
    "dex_liquidity": ("DEX 池储备", "DEX pool reserves"),
    "perp_volume": ("永续成交额 24h", "Perp volume 24h"),
    "perp_oi": ("永续未平仓名义", "Perp open interest"),
}

_ORDER = list(_LABELS)


class _Headline:
    """One scope's total and how many entities it was built from."""

    __slots__ = ("value", "entity_count")

    def __init__(self, value: ScopedValue, entity_count: int) -> None:
        self.value = value
        self.entity_count = entity_count


def _metrics(data: ReportDataset) -> dict[str, _Headline]:
    assets = data.scoped_assets
    pairs = data.scoped_pairs
    return {
        "spot_market_cap": _Headline(
            _sum(
                [
                    scoped(
                        a.snapshot.market_cap if a.snapshot else None,
                        MetricScope.SPOT_MARKET_CAP,
                    )
                    for a in assets
                ],
                MetricScope.SPOT_MARKET_CAP,
            ),
            len(assets),
        ),
        "spot_volume": _Headline(
            _sum(
                [
                    scoped(p.snapshot.adjusted_vol_24h, MetricScope.SPOT_VOLUME)
                    for p in pairs
                ],
                MetricScope.SPOT_VOLUME,
            ),
            len(pairs),
        ),
        "dex_liquidity": _Headline(
            _sum(
                [
                    scoped(p.snapshot.reserve_usd, MetricScope.DEX_LIQUIDITY)
                    for p in data.pools
                ],
                MetricScope.DEX_LIQUIDITY,
            ),
            len(data.pools),
        ),
        "perp_volume": _Headline(
            _sum(
                [
                    scoped(r.snapshot.vol_24h, MetricScope.PERP_VOLUME)
                    for r in data.perp_contracts
                ],
                MetricScope.PERP_VOLUME,
            ),
            len(data.perp_contracts),
        ),
        "perp_oi": _Headline(
            _sum(
                [
                    scoped(r.snapshot.oi_usd, MetricScope.PERP_OI)
                    for r in data.perp_contracts
                ],
                MetricScope.PERP_OI,
            ),
            len(data.perp_contracts),
        ),
    }


def _sum(values: list[ScopedValue], scope: MetricScope) -> ScopedValue:
    """Total one scope, tolerating an empty warehouse.

    ``safe_sum`` refuses an empty sequence because it cannot infer a scope from
    nothing. Here the scope is known, and "no rows yet" is unverified rather than
    zero — the first scheduled run has simply not happened.
    """
    if not values:
        return ScopedValue(amount=None, scope=scope, verified=False)
    return safe_sum(values)


def _kpi(key: str, current: _Headline, previous: _Headline | None) -> Kpi:
    label_zh, label_en = _LABELS[key]
    return Kpi(
        key=key,
        label_zh=label_zh,
        label_en=label_en,
        current=Amount.of(current.value),
        previous=Amount.of(previous.value) if previous else None,
        change_pct=_change(
            current.value.amount, previous.value.amount if previous else None
        ),
        entity_count=current.entity_count,
    )


def _change(current: Decimal | None, previous: Decimal | None) -> float | None:
    """Period-over-period change, or ``None`` when either side is unknown.

    Not zero: a change measured against a baseline nobody observed is a fabricated
    number, and it would render as a reassuring flat line.
    """
    if current is None or previous is None or previous == 0:
        return None
    return float((current - previous) / previous)
