"""Hyperliquid collector — the primary perpetuals source.

HIP-3 lets anyone deploy an independent perp DEX under one exchange. That is why
this is the primary source rather than an aggregator: cross-venue aggregators list a
Top 25 and cannot see a permissionless deployment at all, so a new RWA perp market
would be invisible until it was already large. See ADR 0003.

Everything comes from one endpoint, ``POST /info``, distinguished by a ``type``
field. Open interest arrives in contract units; the USD notional is derived here as
units x mark and *both* are stored, because a derived figure that replaces its input
cannot be re-derived when the derivation turns out to be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.facts import FactPerpContractSnapshot, FactPerpVenueSnapshot
from app.services.ingest.base import Collector, FetchResult, HttpFetcher
from app.services.normalize.dimensions import DimensionCache

SOURCE_ID = "hyperliquid"
EXCHANGE = "Hyperliquid"

INFO_PATH = "/info"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class HyperliquidCollector(Collector):
    """Collects the perp DEX list and contract-level state for each."""

    source_id: str = SOURCE_ID

    def _fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.hyperliquid_base_url,
            # No documented rate limit; observed to be permissive. Held well below
            # any plausible ceiling because being throttled costs a whole snapshot.
            rate_limit_per_minute=60,
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        market_session = classify_session(snapshot_ts)
        results: list[FetchResult] = []
        cache = DimensionCache.load(session)

        with self._fetcher() as fetcher:
            dex_result = fetcher.post_json(INFO_PATH, {"type": "perpDexs"})
            results.append(dex_result)

            # The empty-string entry is the canonical first-party perp DEX. It is
            # included deliberately: dropping it would omit most of the exchange.
            perp_dexs: list[str] = [""]
            if dex_result.ok and isinstance(dex_result.payload, list):
                perp_dexs = [
                    "" if entry is None else str(entry.get("name", ""))
                    for entry in dex_result.payload
                    if entry is None or isinstance(entry, dict)
                ]

            for perp_dex in perp_dexs:
                body: dict[str, Any] = {"type": "metaAndAssetCtxs"}
                if perp_dex:
                    body["dex"] = perp_dex
                result = fetcher.post_json(INFO_PATH, body)
                results.append(result)
                if not result.ok:
                    continue

                contracts = _parse_meta_and_ctxs(result.payload)
                if not contracts:
                    continue

                for contract in contracts:
                    cache.ensure_perp_contract(
                        contract_id=_contract_id(perp_dex, contract.symbol),
                        exchange=EXCHANGE,
                        symbol=contract.symbol,
                        perp_dex=perp_dex,
                    )
                # The dimension rows the fact rows point at have to exist first.
                session.flush()

                for contract in contracts:
                    session.add(
                        FactPerpContractSnapshot(
                            contract_id=_contract_id(perp_dex, contract.symbol),
                            snapshot_ts=snapshot_ts,
                            market_session=market_session,
                            vol_24h=contract.vol_24h,
                            oi_units=contract.oi_units,
                            oi_usd=contract.oi_usd,
                            funding_rate=contract.funding_rate,
                            mark_price=contract.mark_price,
                            index_price=contract.index_price,
                        )
                    )

                session.add(
                    FactPerpVenueSnapshot(
                        exchange=EXCHANGE,
                        perp_dex=perp_dex,
                        segment="all",
                        snapshot_ts=snapshot_ts,
                        market_session=market_session,
                        vol_24h=_sum_or_none([c.vol_24h for c in contracts]),
                        open_interest_usd=_sum_or_none([c.oi_usd for c in contracts]),
                        symbol_count=len(contracts),
                    )
                )

        return results


@dataclass(frozen=True, slots=True)
class ContractState:
    """One perpetual contract as of this snapshot."""

    symbol: str
    mark_price: Decimal | None
    index_price: Decimal | None
    oi_units: Decimal | None
    oi_usd: Decimal | None
    vol_24h: Decimal | None
    funding_rate: Decimal | None


def _contract_id(perp_dex: str, symbol: str) -> str:
    return f"HL:{perp_dex or 'core'}:{symbol}"


def _parse_meta_and_ctxs(payload: Any) -> list[ContractState]:
    """Zip the metadata array against the context array.

    Hyperliquid returns ``[meta, contexts]`` as two parallel arrays rather than one
    array of objects. They are matched by position, so a length mismatch means the
    response is unusable — returning partial rows would silently attach one
    contract's open interest to another's symbol.
    """
    if not isinstance(payload, list) or len(payload) != 2:
        return []

    meta, contexts = payload
    universe = (meta or {}).get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list):
        return []
    if len(universe) != len(contexts):
        return []

    states: list[ContractState] = []
    for entry, context in zip(universe, contexts):
        if not isinstance(entry, dict) or not isinstance(context, dict):
            continue
        mark = _decimal(context.get("markPx"))
        oi_units = _decimal(context.get("openInterest"))
        states.append(
            ContractState(
                symbol=str(entry.get("name", "")),
                mark_price=mark,
                index_price=_decimal(context.get("oraclePx")),
                oi_units=oi_units,
                # Derived, and stored next to its inputs rather than instead of them.
                oi_usd=(oi_units * mark) if oi_units is not None and mark else None,
                vol_24h=_decimal(context.get("dayNtlVlm")),
                funding_rate=_decimal(context.get("funding")),
            )
        )
    return states


def _sum_or_none(values: Sequence[Decimal | None]) -> Decimal | None:
    """Sum observed values, or return ``None`` when nothing was observed.

    Not ``sum(..., 0)``: an all-missing venue would then report zero turnover, which
    reads as "nobody traded here" instead of "we did not see".
    """
    observed = [v for v in values if v is not None]
    if not observed:
        return None
    return sum(observed, start=Decimal(0))
