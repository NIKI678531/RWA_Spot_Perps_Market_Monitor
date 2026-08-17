"""CoinGecko collector: the spot asset master, category totals and tickers.

Five categories are collected — Tokenized Stock, Tokenized ETF, Ondo, xStocks,
bStocks — and they overlap by construction. This collector stores all five as fetched
and leaves deduplication to ``normalize.dedup``; merging here would destroy the
ability to say how the market describes itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.enums import FetchStatus
from app.models.facts import FactAssetSnapshot, FactCategorySnapshot, FactPairSnapshot
from app.services.ingest.base import Collector, FetchResult, HttpFetcher
from app.services.normalize.dedup import CoinObservation, build_rows
from app.services.normalize.dimensions import DimensionCache

SOURCE_ID = "coingecko"

#: The five overlapping categories, in the order the union prefers them. Tokenized
#: Stock first: it is the broadest and its metadata is the most complete, so it wins
#: conflicts on shared coins.
CATEGORIES: tuple[str, ...] = (
    "tokenized-stock",
    "tokenized-etf",
    "ondo-finance-ecosystem",
    "xstocks",
    "bstocks",
)

#: Which issuer a category implies. Only the single-issuer categories appear: a coin
#: found under "tokenized-stock" says nothing about who wrapped it, and inventing an
#: issuer there would put the wrong name on a competitive ranking.
CATEGORY_ISSUERS: Mapping[str, str] = {
    "xstocks": "xStocks",
    "bstocks": "bStocks",
    "ondo-finance-ecosystem": "Ondo",
}

#: CoinGecko's own data-hygiene markers. They describe the quote, not the market.
_ANOMALY_KEY = "is_anomaly"
_STALE_KEY = "is_stale"


def _decimal(value: Any) -> Decimal | None:
    """Convert a JSON number, preserving *missing* as ``None`` rather than zero."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _nested(payload: Mapping[str, Any], outer: str, inner: str) -> Any:
    """Read ``payload[outer][inner]`` tolerating an explicit JSON ``null``.

    ``dict.get(key, {})`` returns ``None`` — not the default — when the key is present
    and null, and CoinGecko does send ``"converted_volume": null`` for tickers it could
    not price. Chaining ``.get`` off that raises, and one such ticker would cost the
    whole collection pass.
    """
    section = payload.get(outer)
    if not isinstance(section, Mapping):
        return None
    return section.get(inner)


@dataclass
class CoinGeckoCollector(Collector):
    """Fetches assets, categories and tickers. Stores raw; normalizes nothing."""

    source_id: str = SOURCE_ID
    categories: Sequence[str] = CATEGORIES
    #: How many coins per category to pull tickers for. Ticker calls are one request
    #: per coin, which is the binding constraint on a 30 req/min budget.
    ticker_depth: int = 25

    def _fetcher(self) -> HttpFetcher:
        headers = (
            {"x-cg-demo-api-key": settings.coingecko_api_key}
            if settings.coingecko_api_key
            else {}
        )
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.coingecko_base_url,
            rate_limit_per_minute=30,
            headers=headers,
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        market_session = classify_session(snapshot_ts)
        results: list[FetchResult] = []
        per_category: dict[str, list[CoinObservation]] = {}
        # Dimension rows must exist before the facts that reference them; the cache
        # also classifies tier and underlying once per new symbol rather than per row.
        cache = DimensionCache.load(session)

        with self._fetcher() as fetcher:
            # Every category is fetched before any asset is written. The issuer a coin
            # belongs to is only knowable once all five have been read: xStocks tokens
            # also appear under "tokenized-stock", which names no issuer, and writing
            # them as they arrive would stamp the first category that mentioned them.
            # Issuer is what ``classify_tier`` reads to decide CORE_RWA, so getting it
            # from iteration order would silently demote every custodied wrapper.
            coins_by_id: dict[str, Mapping[str, Any]] = {}
            issuer_of: dict[str, str] = {}

            for category in self.categories:
                result = fetcher.get_json(
                    "/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "category": category,
                        "order": "market_cap_desc",
                        "per_page": 250,
                        "page": 1,
                        "price_change_percentage": "24h,7d,30d",
                    },
                )
                results.append(result)

                if not result.ok or not isinstance(result.payload, list):
                    # A failed category is a missing observation. It contributes no
                    # rows, and the union below is marked partial by safe_sum.
                    per_category[category] = []
                    continue

                coins = [c for c in result.payload if isinstance(c, dict)]
                per_category[category] = [
                    CoinObservation(
                        coin_id=str(coin.get("id")),
                        market_cap=_decimal(coin.get("market_cap")),
                        vol_24h=_decimal(coin.get("total_volume")),
                    )
                    for coin in coins
                ]

                issuer_id = CATEGORY_ISSUERS.get(category)
                if issuer_id:
                    cache.ensure_issuer(issuer_id)

                for coin in coins:
                    coin_id = str(coin.get("id"))
                    # First category wins the *metadata* (CATEGORIES is ordered by
                    # completeness) but the first issuer-bearing category wins the
                    # issuer, whichever order the two arrived in.
                    coins_by_id.setdefault(coin_id, coin)
                    if issuer_id:
                        issuer_of.setdefault(coin_id, issuer_id)

            # Ranked by market cap so ``ticker_depth`` cuts the tail, not an arbitrary
            # slice: iterating a set would hand a different sample to every process.
            ranked = sorted(
                coins_by_id,
                key=lambda cid: (
                    _decimal(coins_by_id[cid].get("market_cap")) is None,
                    -(_decimal(coins_by_id[cid].get("market_cap")) or Decimal(0)),
                    cid,
                ),
            )

            for coin_id in ranked:
                entry = coins_by_id[coin_id]
                source_symbol = str(entry.get("symbol") or coin_id)
                cache.ensure_asset(
                    asset_id=coin_id,
                    # Display upper, resolve verbatim: xStocks writes AAPLx and the
                    # suffix rules are case-sensitive on purpose.
                    symbol=source_symbol.upper(),
                    source_symbol=source_symbol,
                    name=entry.get("name"),
                    coin_id=coin_id,
                    issuer_id=issuer_of.get(coin_id),
                )
                session.add(_asset_snapshot(entry, snapshot_ts, market_session))
            # Dimension rows have to be on the database before the fact rows that
            # reference them, or the insert fails on the foreign key.
            session.flush()

            for row in build_rows(per_category):
                session.add(
                    FactCategorySnapshot(
                        category_id=row.category_id,
                        snapshot_ts=snapshot_ts,
                        market_session=market_session,
                        asset_count=row.asset_count,
                        market_cap=row.market_cap.amount,
                        vol_24h=row.vol_24h.amount,
                        is_additive=row.is_additive,
                    )
                )

            for coin_id in ranked[: self.ticker_depth]:
                result = fetcher.get_json(f"/coins/{coin_id}/tickers")
                results.append(result)
                if not result.ok or not isinstance(result.payload, dict):
                    continue
                pairs = _pair_snapshots(
                    coin_id,
                    result.payload.get("tickers") or [],
                    snapshot_ts,
                    market_session,
                    cache,
                )
                # Any venue met for the first time was added inside the call above.
                session.flush()
                for pair in pairs:
                    session.add(pair)

        return results


def _asset_snapshot(
    coin: Mapping[str, Any], snapshot_ts: datetime, market_session: Any
) -> FactAssetSnapshot:
    return FactAssetSnapshot(
        asset_id=str(coin.get("id")),
        snapshot_ts=snapshot_ts,
        market_session=market_session,
        price_usd=_decimal(coin.get("current_price")),
        market_cap=_decimal(coin.get("market_cap")),
        fdv=_decimal(coin.get("fully_diluted_valuation")),
        vol_24h=_decimal(coin.get("total_volume")),
        circulating_supply=_decimal(coin.get("circulating_supply")),
        change_24h=_decimal(coin.get("price_change_percentage_24h_in_currency")),
        change_7d=_decimal(coin.get("price_change_percentage_7d_in_currency")),
        change_30d=_decimal(coin.get("price_change_percentage_30d_in_currency")),
    )


def _pair_snapshots(
    coin_id: str,
    tickers: Sequence[Any],
    snapshot_ts: datetime,
    market_session: Any,
    cache: DimensionCache,
) -> list[FactPairSnapshot]:
    """One row per (asset, venue), with raw and adjusted turnover side by side.

    Adjusted excludes quality-flagged tickers; raw keeps them. Reporting only one of
    the two hides either a venue's overstatement or its own claim about itself.
    """
    by_venue: dict[str, dict[str, Any]] = {}

    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        market = ticker.get("market") or {}
        venue_name = str(market.get("name") or market.get("identifier") or "unknown")
        venue_id = cache.ensure_venue(name=venue_name).venue_id
        volume = _decimal(_nested(ticker, "converted_volume", "usd"))
        flagged_anomaly = bool(ticker.get(_ANOMALY_KEY))
        flagged_stale = bool(ticker.get(_STALE_KEY))

        entry = by_venue.setdefault(
            venue_id,
            {
                "raw": None,
                "adjusted": None,
                "price": _decimal(_nested(ticker, "converted_last", "usd")),
                "spread": _decimal(ticker.get("bid_ask_spread_percentage")),
                "trust": ticker.get("trust_score"),
                "anomaly": False,
                "stale": False,
            },
        )
        entry["anomaly"] = entry["anomaly"] or flagged_anomaly
        entry["stale"] = entry["stale"] or flagged_stale

        if volume is None:
            continue
        entry["raw"] = (entry["raw"] or Decimal(0)) + volume
        if not (flagged_anomaly or flagged_stale):
            entry["adjusted"] = (entry["adjusted"] or Decimal(0)) + volume

    return [
        FactPairSnapshot(
            asset_id=coin_id,
            venue_id=venue_id,
            snapshot_ts=snapshot_ts,
            market_session=market_session,
            raw_vol_24h=entry["raw"],
            adjusted_vol_24h=entry["adjusted"],
            price_usd=entry["price"],
            spread_pct=entry["spread"],
            trust_score=entry["trust"],
            is_quality_anomaly=entry["anomaly"],
            is_quality_stale=entry["stale"],
        )
        for venue_id, entry in by_venue.items()
    ]


def is_usable(results: Sequence[FetchResult]) -> bool:
    """Whether enough of the pass succeeded to treat the snapshot as observed."""
    return any(r.status is FetchStatus.OK for r in results)
