"""Deduplicate overlapping CoinGecko categories.

Tokenized Stock, Tokenized ETF, Ondo, xStocks and bStocks overlap by construction —
an xStocks share token sits in at least three of them. Adding the five category
totals double- and triple-counts, and the result is a headline market size that is
wrong by a multiple while looking perfectly reasonable.

Only the deduplicated union is a valid total. The five source rows are still worth
storing and displaying: they are how the market describes itself. They carry
``is_additive = False`` so neither the API nor a chart can treat them as parts of a
whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from app.core.metrics import MetricScope, ScopedValue, safe_sum

#: The union row's identifier. Named rather than derived so the API, the report and
#: the frontend all refer to the same row.
UNION_CATEGORY_ID = "rwa_union"


@dataclass(frozen=True, slots=True)
class CoinObservation:
    """One coin as one category listed it."""

    coin_id: str
    market_cap: Decimal | None = None
    vol_24h: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CategoryRow:
    """A category total, additive or not."""

    category_id: str
    asset_count: int
    market_cap: ScopedValue
    vol_24h: ScopedValue
    #: True only for the union row. Everything downstream must respect it.
    is_additive: bool


def _total(coins: Iterable[CoinObservation], scope: MetricScope) -> ScopedValue:
    values = [
        ScopedValue(
            amount=(
                c.market_cap if scope is MetricScope.SPOT_MARKET_CAP else c.vol_24h
            ),
            scope=scope,
            verified=(
                (c.market_cap if scope is MetricScope.SPOT_MARKET_CAP else c.vol_24h)
                is not None
            ),
        )
        for c in coins
    ]
    if not values:
        return ScopedValue(amount=None, scope=scope, verified=False)
    return safe_sum(values)


def build_rows(
    categories: Mapping[str, Sequence[CoinObservation]],
) -> list[CategoryRow]:
    """Produce one row per source category plus one deduplicated union row.

    A coin appearing in several categories is counted once in the union, keeping the
    first observation seen. Categories are processed in the order given, so a caller
    that wants a particular source to win a conflict simply lists it first.
    """
    rows: list[CategoryRow] = []
    union: dict[str, CoinObservation] = {}

    for category_id, coins in categories.items():
        rows.append(
            CategoryRow(
                category_id=category_id,
                asset_count=len(coins),
                market_cap=_total(coins, MetricScope.SPOT_MARKET_CAP),
                vol_24h=_total(coins, MetricScope.SPOT_VOLUME),
                # Never additive: this row shares coins with its siblings.
                is_additive=False,
            )
        )
        for coin in coins:
            union.setdefault(coin.coin_id, coin)

    deduped = list(union.values())
    rows.append(
        CategoryRow(
            category_id=UNION_CATEGORY_ID,
            asset_count=len(deduped),
            market_cap=_total(deduped, MetricScope.SPOT_MARKET_CAP),
            vol_24h=_total(deduped, MetricScope.SPOT_VOLUME),
            is_additive=True,
        )
    )
    return rows


def overlap_count(categories: Mapping[str, Sequence[CoinObservation]]) -> int:
    """How many listings the union removes.

    Reported on the data-quality page: it is the size of the error that would exist
    if the five categories were simply added.
    """
    listings = sum(len(coins) for coins in categories.values())
    distinct = len({c.coin_id for coins in categories.values() for c in coins})
    return listings - distinct
