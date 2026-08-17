"""Binance collector — bStocks spot pairs and the TradFi perpetual contracts.

Two things here are load-bearing:

**Open interest is derived.** ``/fapi/v1/openInterest`` reports contract *units*, not
notional. USD open interest is computed as units x mark and stored next to
``oi_units``, never instead of it — a derived figure that has eaten its own input
cannot be re-derived when the derivation turns out to be wrong.

**The exchange's own label is preserved verbatim.** Binance classifies some ETFs and
leveraged ETPs as ``EQUITY`` in ``underlyingSubType``. That is stored as
``source_underlying_type`` exactly as received, with our own classification alongside
it. Overwriting it would make our numbers impossible to reconcile against theirs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.enums import VenueType
from app.models.facts import (
    FactPairSnapshot,
    FactPerpContractSnapshot,
    FactPerpVenueSnapshot,
)
from app.services.ingest.base import Collector, FetchResult, HttpFetcher
from app.services.normalize.dimensions import DimensionCache

SOURCE_ID = "binance"
EXCHANGE = "Binance"
VENUE_NAME = "Binance"

#: Binance's own contract-type markers for the tokenized-equity complex. Matched
#: against ``underlyingType`` / ``underlyingSubType`` on the exchange info payload.
TRADFI_TYPES = frozenset({"EQUITY", "INDEX", "COMMODITY", "FOREX", "PREMARKET"})

#: bStocks spot symbols quote in these. Anything else is a crypto cross and is not a
#: tokenized-equity market.
SPOT_QUOTES = ("USDT", "USDC", "FDUSD")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class PerpState:
    """One TradFi perpetual as of this snapshot."""

    symbol: str
    source_underlying_type: str | None
    vol_24h: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    funding_rate: Decimal | None
    oi_units: Decimal | None

    @property
    def oi_usd(self) -> Decimal | None:
        """Notional open interest, derived. Null when either input is missing."""
        if self.oi_units is None or not self.mark_price:
            return None
        return self.oi_units * self.mark_price


@dataclass
class BinanceCollector(Collector):
    """Collects the TradFi perpetual complex and the bStocks spot pairs."""

    source_id: str = SOURCE_ID
    #: Open interest is one request per symbol. Capped so a single source cannot
    #: consume the whole snapshot window.
    oi_depth: int = 60

    def _spot_fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.binance_base_url,
            rate_limit_per_minute=120,
        )

    def _futures_fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.binance_fapi_base_url,
            rate_limit_per_minute=120,
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        market_session = classify_session(snapshot_ts)
        cache = DimensionCache.load(session)
        results: list[FetchResult] = []

        results.extend(self._collect_perps(session, snapshot_ts, market_session, cache))
        results.extend(self._collect_spot(session, snapshot_ts, market_session, cache))
        return results

    # --- perpetuals --------------------------------------------------------

    def _collect_perps(
        self,
        session: Session,
        snapshot_ts: datetime,
        market_session: Any,
        cache: DimensionCache,
    ) -> list[FetchResult]:
        results: list[FetchResult] = []

        with self._futures_fetcher() as fetcher:
            info = fetcher.get_json("/fapi/v1/exchangeInfo")
            results.append(info)
            if not info.ok or not isinstance(info.payload, dict):
                # Without the symbol list there is nothing to ask open interest
                # about. The failure is logged; no rows are written, and no zeros.
                return results

            tradfi = _tradfi_symbols(info.payload)
            if not tradfi:
                return results

            tickers = fetcher.get_json("/fapi/v1/ticker/24hr")
            results.append(tickers)
            premiums = fetcher.get_json("/fapi/v1/premiumIndex")
            results.append(premiums)

            volumes = _by_symbol(tickers.payload, "quoteVolume")
            marks = _by_symbol(premiums.payload, "markPrice")
            indexes = _by_symbol(premiums.payload, "indexPrice")
            fundings = _by_symbol(premiums.payload, "lastFundingRate")

            # Volume, mark, index and funding arrive in two bulk calls, so every TradFi
            # contract gets a row regardless of the cap. Only open interest costs a
            # request per symbol, and only it is capped — truncating the universe here
            # would drop contracts out of the venue total and out of every ranking
            # while the row still looked like the whole book.
            #
            # Ranked by turnover so the cap takes the tail: the top contracts carry
            # most of the volume (78.2% in the top ten on the baseline snapshot), and
            # exchangeInfo order is arbitrary.
            ranked = sorted(
                tradfi.items(),
                key=lambda item: (
                    volumes.get(item[0]) is None,
                    -(volumes.get(item[0]) or Decimal(0)),
                    item[0],
                ),
            )

            states: list[PerpState] = []
            for position, (symbol, underlying_type) in enumerate(ranked):
                oi_units = None
                if position < self.oi_depth:
                    oi = fetcher.get_json(
                        "/fapi/v1/openInterest", params={"symbol": symbol}
                    )
                    results.append(oi)
                    oi_units = (
                        _decimal(oi.payload.get("openInterest"))
                        if oi.ok and isinstance(oi.payload, dict)
                        else None
                    )
                states.append(
                    PerpState(
                        symbol=symbol,
                        source_underlying_type=underlying_type,
                        vol_24h=volumes.get(symbol),
                        mark_price=marks.get(symbol),
                        index_price=indexes.get(symbol),
                        funding_rate=fundings.get(symbol),
                        oi_units=oi_units,
                    )
                )

        for state in states:
            cache.ensure_perp_contract(
                contract_id=_contract_id(state.symbol),
                exchange=EXCHANGE,
                symbol=state.symbol,
                source_underlying_type=state.source_underlying_type,
            )
        session.flush()

        for state in states:
            session.add(
                FactPerpContractSnapshot(
                    contract_id=_contract_id(state.symbol),
                    snapshot_ts=snapshot_ts,
                    market_session=market_session,
                    vol_24h=state.vol_24h,
                    oi_units=state.oi_units,
                    oi_usd=state.oi_usd,
                    funding_rate=state.funding_rate,
                    mark_price=state.mark_price,
                    index_price=state.index_price,
                )
            )

        session.add(
            FactPerpVenueSnapshot(
                exchange=EXCHANGE,
                # Binance has no HIP-3 equivalent; the empty string is the venue's
                # one and only book, matching the composite key's default.
                perp_dex="",
                segment="tradfi",
                snapshot_ts=snapshot_ts,
                market_session=market_session,
                vol_24h=_sum_or_none([s.vol_24h for s in states]),
                open_interest_usd=_sum_or_none([s.oi_usd for s in states]),
                symbol_count=len(states),
                # The two totals do not cover the same contracts, and the row says so.
                # A capped open-interest sum is a floor on the venue's book, not the
                # venue's book.
                oi_symbol_count=min(len(states), self.oi_depth),
            )
        )
        return results

    # --- spot --------------------------------------------------------------

    def _collect_spot(
        self,
        session: Session,
        snapshot_ts: datetime,
        market_session: Any,
        cache: DimensionCache,
    ) -> list[FetchResult]:
        """bStocks spot pairs, matched against the assets CoinGecko already indexed.

        Symbols are not invented here. A Binance ticker only produces a row when its
        base leg is an asset the spot collector has already classified, because
        creating a dimension row from a bare exchange symbol would put an unclassified
        token into the rankings.
        """
        results: list[FetchResult] = []
        by_symbol = cache.assets_by_symbol()
        if not by_symbol:
            return results

        with self._spot_fetcher() as fetcher:
            tickers = fetcher.get_json("/api/v3/ticker/24hr")
            results.append(tickers)
            if not tickers.ok or not isinstance(tickers.payload, list):
                return results

        venue_id = cache.ensure_venue(
            name=VENUE_NAME, venue_type=VenueType.CEX
        ).venue_id
        session.flush()

        seen: set[str] = set()
        for ticker in tickers.payload:
            if not isinstance(ticker, dict):
                continue
            base = _base_leg(str(ticker.get("symbol") or ""))
            asset = by_symbol.get(base) if base else None
            if asset is None or asset.asset_id in seen:
                continue
            seen.add(asset.asset_id)

            volume = _decimal(ticker.get("quoteVolume"))
            session.add(
                FactPairSnapshot(
                    asset_id=asset.asset_id,
                    venue_id=venue_id,
                    snapshot_ts=snapshot_ts,
                    market_session=market_session,
                    raw_vol_24h=volume,
                    # Binance publishes no data-hygiene markers of its own, so raw and
                    # adjusted are equal here. They stay two columns: the screen that
                    # sets them apart runs downstream and may yet flag this pair.
                    adjusted_vol_24h=volume,
                    price_usd=_decimal(ticker.get("lastPrice")),
                )
            )
        return results


def _contract_id(symbol: str) -> str:
    return f"BN:{symbol}"


def _tradfi_symbols(payload: Mapping[str, Any]) -> dict[str, str | None]:
    """Perpetual symbols Binance itself classifies as non-crypto underlyings.

    The label is read, not judged. ``EQUITY`` here may cover an ETF or a leveraged
    ETP; correcting that is ``analysis_group``'s job, downstream.
    """
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        return {}

    found: dict[str, str | None] = {}
    for entry in symbols:
        if not isinstance(entry, dict):
            continue
        if entry.get("contractType") != "PERPETUAL":
            continue
        label = entry.get("underlyingSubType") or entry.get("underlyingType")
        label = _first_label(label)
        if label and label.upper() in TRADFI_TYPES:
            found[str(entry.get("symbol"))] = label
    return found


def _first_label(value: Any) -> str | None:
    """``underlyingSubType`` arrives as a list on some symbols and a string on others."""
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def _by_symbol(payload: Any, key: str) -> dict[str, Decimal | None]:
    if not isinstance(payload, list):
        return {}
    return {
        str(row["symbol"]): _decimal(row.get(key))
        for row in payload
        if isinstance(row, dict) and row.get("symbol")
    }


def _base_leg(symbol: str) -> str | None:
    for quote in SPOT_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return None


def _sum_or_none(values: Sequence[Decimal | None]) -> Decimal | None:
    """Sum what was observed, or return ``None`` when nothing was.

    Not ``sum(..., 0)``: an all-missing venue would report zero turnover, which reads
    as "nobody traded here" rather than "we did not see".
    """
    observed = [v for v in values if v is not None]
    if not observed:
        return None
    return sum(observed, start=Decimal(0))
