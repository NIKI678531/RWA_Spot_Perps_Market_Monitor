"""GeckoTerminal collector — DEX pool reserves, turnover and trade direction.

This is the only source in the system that reports **buy and sell counts**. Every
other feed answers "how much traded"; this one answers "which way", which is the
question the whole monitor is built around — a product nobody was buying that
suddenly has buyers. Detectors X2 and X3 read nothing else.

Pools are found by searching each in-scope asset's symbol. Coverage is therefore
incomplete by construction: a pool whose name does not contain the symbol is invisible
here. That is recorded as partial coverage rather than papered over, because a DEX
liquidity figure presented as complete when it is not is worse than one labelled
partial.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.dimensions import DimAsset
from app.models.enums import IN_SCOPE_TIERS
from app.models.facts import FactPoolSnapshot
from app.services.ingest.base import Collector, FetchResult, HttpFetcher
from app.services.normalize.dimensions import DimensionCache

SOURCE_ID = "geckoterminal"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class PoolState:
    """One pool as of this snapshot, before any dimension work."""

    pool_id: str
    network: str
    dex: str
    address: str | None
    #: The base leg of the pool name. What decides which asset the pool belongs to.
    base_symbol: str | None
    quote_token: str | None
    reserve_usd: Decimal | None
    vol_24h: Decimal | None
    buys_24h: int | None
    sells_24h: int | None
    tx_count_24h: int | None


@dataclass
class GeckoTerminalCollector(Collector):
    """Searches pools for in-scope symbols and stores their state."""

    source_id: str = SOURCE_ID
    #: How many symbols to search per pass. One request each, against a public
    #: endpoint with no documented allowance, so this is the pass's cost knob.
    symbol_depth: int = 40

    def _fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.geckoterminal_base_url,
            rate_limit_per_minute=30,
            headers={"Accept": "application/json;version=20230302"},
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        market_session = classify_session(snapshot_ts)
        results: list[FetchResult] = []
        cache = DimensionCache.load(session)
        seen: set[str] = set()

        with self._fetcher() as fetcher:
            for asset in _scoped_assets(session, self.symbol_depth):
                result = fetcher.get_json(
                    "/search/pools", params={"query": asset.symbol, "page": 1}
                )
                results.append(result)
                if not result.ok or not isinstance(result.payload, dict):
                    continue

                # Two symbols can surface the same pool. Writing it twice would
                # violate the fact table's composite primary key.
                #
                # ``/search/pools`` is a fuzzy string search: querying "AAPL" returns
                # every pool whose name contains it, including other issuers' wrappers
                # and unrelated tokens that happen to embed the letters. Attributing
                # the whole result to the queried asset would credit one product with
                # another's liquidity, so each pool is claimed by whichever known asset
                # its base leg actually names. A pool whose base leg names nothing we
                # know is dropped: an unidentified pool is not this asset's, and the
                # docstring's coverage caveat already says the search is incomplete.
                by_symbol = cache.assets_by_symbol()
                owned: list[tuple[PoolState, DimAsset]] = []
                for state in _parse_pools(result.payload):
                    if state.pool_id in seen:
                        continue
                    owner = by_symbol.get((state.base_symbol or "").upper())
                    if owner is None:
                        continue
                    owned.append((state, owner))

                if not owned:
                    continue
                seen.update(state.pool_id for state, _ in owned)

                for state, owner in owned:
                    cache.ensure_pool(
                        pool_id=state.pool_id,
                        network=state.network,
                        dex=state.dex,
                        pool_address=state.address,
                        base_asset_id=owner.asset_id,
                        quote_token=state.quote_token,
                    )
                # The pool dimension rows must land before the facts pointing at them.
                session.flush()

                for state, _ in owned:
                    session.add(
                        FactPoolSnapshot(
                            pool_id=state.pool_id,
                            snapshot_ts=snapshot_ts,
                            market_session=market_session,
                            reserve_usd=state.reserve_usd,
                            vol_24h=state.vol_24h,
                            buys_24h=state.buys_24h,
                            sells_24h=state.sells_24h,
                            tx_count_24h=state.tx_count_24h,
                        )
                    )

        return results


def _scoped_assets(session: Session, limit: int) -> list[DimAsset]:
    """In-scope assets, most recently created first.

    ``NON_RWA`` is excluded here rather than after the fetch: spending a request on a
    benchmark token costs an in-scope one on a rate-limited source.
    """
    stmt = (
        select(DimAsset)
        .where(DimAsset.rwa_tier.in_(tuple(IN_SCOPE_TIERS)))
        .order_by(DimAsset.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def _parse_pools(payload: Mapping[str, Any]) -> list[PoolState]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    states: list[PoolState] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        pool_id = str(entry.get("id") or "")
        attributes = entry.get("attributes")
        if not pool_id or not isinstance(attributes, dict):
            continue

        relationships = entry.get("relationships")
        relationships = relationships if isinstance(relationships, dict) else {}
        transactions = attributes.get("transactions")
        window = transactions.get("h24") if isinstance(transactions, dict) else None
        window = window if isinstance(window, dict) else {}
        volume = attributes.get("volume_usd")
        volume = volume if isinstance(volume, dict) else {}

        buys = _int(window.get("buys"))
        sells = _int(window.get("sells"))
        states.append(
            PoolState(
                pool_id=pool_id,
                # The pool id is "<network>_<address>", and the network relationship
                # is not always present. The prefix is, so it is the fallback.
                network=_related(relationships, "network") or pool_id.split("_", 1)[0],
                dex=_related(relationships, "dex") or "unknown",
                address=attributes.get("address"),
                base_symbol=_leg(attributes.get("name"), 0),
                quote_token=_leg(attributes.get("name"), 1),
                reserve_usd=_decimal(attributes.get("reserve_in_usd")),
                vol_24h=_decimal(volume.get("h24")),
                buys_24h=buys,
                sells_24h=sells,
                # Derived, and null when neither side was reported: a zero here would
                # claim the pool was observed and idle.
                tx_count_24h=(
                    (buys or 0) + (sells or 0)
                    if buys is not None or sells is not None
                    else None
                ),
            )
        )
    return states


def _related(relationships: Mapping[str, Any], key: str) -> str | None:
    node = relationships.get(key)
    if not isinstance(node, dict):
        return None
    data = node.get("data")
    if not isinstance(data, dict):
        return None
    value = data.get("id")
    return str(value) if value else None


def _leg(name: Any, index: int) -> str | None:
    """One side of a ``"AAPLX / USDC"`` pool name.

    The base leg identifies the pool; the quote leg decides whether the price is
    canonical. A pool priced in something exotic reports USD figures that lean on a
    second, weaker feed, and the quality screen has to be able to see that.
    """
    if not isinstance(name, str) or "/" not in name:
        return None
    return name.split("/", 1)[index].strip() or None
