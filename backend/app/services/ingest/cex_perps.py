"""Cross-venue perpetual collectors for the five largest keyless CEX venues.

The workbook took its cross-venue perp picture from the Loris public page, which
shows a Top 25 with no contract rows and no history. That page renders its numbers
client-side, so the HTML carries venue *names* and no figures at all, and the API
behind it answers ``401 Missing API key``. This module rebuilds the same view from
the exchanges themselves, which publish it without a key. See ADR 0006.

Every venue here is read through the same two questions — what traded, and what is
still open — but each answers them in its own units:

=========  =====================  ==========================================
venue      24h turnover           open interest in USD
=========  =====================  ==========================================
OKX        ``volCcy24h x last``   ``oiUsd``, published directly
Bybit      ``turnover24h``        ``openInterestValue``, published directly
Gate       ``volume_24h_quote``   ``total_size x quanto_multiplier x mark``
MEXC       ``amount24``           ``holdVol x contractSize x fairPrice``
Bitget     ``usdtVolume``         ``holdingAmount x lastPr``
=========  =====================  ==========================================

Two rules hold across all five.

**Only contracts that resolve to a known underlying are stored.** These venues list
400-1,100 contracts each and the overwhelming majority are crypto-native, which
``rwa_tier`` puts out of scope. Resolution runs before any row is written, so a
thousand crypto tickers never reach ``dim_perp_contract`` or the review queue.

**A symbol is never matched by prefix.** The venues disagree on convention —
``AAPL-USDT-SWAP``, ``AAPLUSDT``, ``AAPLX_USDT``, ``AAPLSTOCK_USDT`` — and prefix
matching finds ``HOOD`` inside MEXC's ``HOODRAT_USDT``, which is a memecoin and not
Robinhood. The quote suffix is stripped using each venue's own contract grammar, and
the remainder is then handed to ``underlying_map``, which accepts it only if that
underlying already exists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.dimensions import DimUnderlying
from app.models.enums import AssetClass
from app.models.facts import FactPerpContractSnapshot, FactPerpVenueSnapshot
from app.services.ingest.base import Collector, FetchResult, HttpFetcher
from app.services.normalize import underlying_map
from app.services.normalize.dimensions import DimensionCache

#: The workbook's two segments. ``stock`` is a strict subset of ``all`` — they are
#: separate rows sharing an exchange, and adding them double-counts every equity
#: contract. Nothing in the API or the charts may sum across this column.
SEGMENT_ALL = "all"
SEGMENT_STOCK = "stock"

#: Asset classes that count as the "stock perps" segment. Index and commodity
#: contracts are RWA but are not equity, and the workbook reports them apart.
_STOCK_CLASSES = frozenset({AssetClass.EQUITY, AssetClass.PRE_IPO})


def _decimal(value: Any) -> Decimal | None:
    """Convert a JSON number, preserving *missing* as ``None`` rather than zero."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _product(*values: Decimal | None) -> Decimal | None:
    """Multiply, returning ``None`` if any factor was not observed.

    A derived open interest with a missing factor is not a small number; it is an
    unknown one. Substituting zero for the absent factor would publish a venue as
    having no open positions.
    """
    result = Decimal(1)
    for value in values:
        if value is None:
            return None
        result *= value
    return result


def _rows(payload: Any, *keys: str) -> list[Mapping[str, Any]]:
    """Dig out the list of ticker objects, tolerating each venue's envelope."""
    node: Any = payload
    for key in keys:
        if not isinstance(node, Mapping):
            return []
        node = node.get(key)
    if not isinstance(node, list):
        return []
    return [row for row in node if isinstance(row, Mapping)]


def _strip_quote(symbol: str, quotes: Sequence[str]) -> str:
    """Remove a trailing quote asset from a concatenated contract symbol.

    Bybit and Bitget write ``AAPLUSDT`` with no separator, so the quote has to be
    taken off by name. Longest first: stripping ``USD`` from ``AAPLUSDT`` would leave
    ``AAPLT``, a symbol for nothing.
    """
    for quote in sorted(quotes, key=len, reverse=True):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


@dataclass(frozen=True, slots=True)
class PerpRow:
    """One perpetual contract as the venue reported it."""

    #: The venue's own contract symbol, kept verbatim for reconciliation.
    symbol: str
    #: The symbol with the venue's quote grammar removed, for underlying resolution.
    base: str
    vol_24h: Decimal | None
    oi_usd: Decimal | None
    mark_price: Decimal | None
    funding_rate: Decimal | None


class PerpVenueAdapter(ABC):
    """One exchange's contract grammar and field names.

    Everything venue-specific lives here; the collector below is shared. Splitting it
    the other way — a collector per venue — would copy the resolution and the
    fact-writing five times, and those are the parts where a mistake is silent.
    """

    #: Matches a ``source_registry.source_id`` row.
    source_id: str
    #: Display name. Matches the workbook's spelling so the two can be compared.
    exchange: str
    rate_limit_per_minute: int = 60

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Read off settings at call time so tests can point it elsewhere."""

    @abstractmethod
    def collect_rows(
        self, fetcher: HttpFetcher
    ) -> tuple[list[PerpRow], list[FetchResult]]:
        """Fetch this venue and return its contracts plus every fetch outcome."""


class OkxAdapter(PerpVenueAdapter):
    """OKX. ``instId`` is ``BASE-QUOTE-SWAP``; open interest arrives pre-converted."""

    source_id = "okx"
    exchange = "OKX"

    @property
    def base_url(self) -> str:
        return settings.okx_base_url

    def collect_rows(
        self, fetcher: HttpFetcher
    ) -> tuple[list[PerpRow], list[FetchResult]]:
        tickers = fetcher.get_json(
            "/api/v5/market/tickers", params={"instType": "SWAP"}
        )
        open_interest = fetcher.get_json(
            "/api/v5/public/open-interest", params={"instType": "SWAP"}
        )
        results = [tickers, open_interest]

        oi_by_id = {
            str(row.get("instId")): _decimal(row.get("oiUsd"))
            for row in _rows(open_interest.payload, "data")
        }

        rows = []
        for row in _rows(tickers.payload, "data"):
            inst_id = str(row.get("instId") or "")
            parts = inst_id.split("-")
            if len(parts) < 3:
                continue
            last = _decimal(row.get("last"))
            rows.append(
                PerpRow(
                    symbol=inst_id,
                    base=parts[0],
                    # volCcy24h is denominated in the base currency, so the USD
                    # notional needs the price. Verified against vol24h x ctVal x
                    # last, which agrees to the cent.
                    vol_24h=_product(_decimal(row.get("volCcy24h")), last),
                    oi_usd=oi_by_id.get(inst_id),
                    mark_price=last,
                    funding_rate=None,
                )
            )
        return rows, results


class BybitAdapter(PerpVenueAdapter):
    """Bybit. One call carries turnover and USD open interest together."""

    source_id = "bybit"
    exchange = "Bybit"

    QUOTES = ("USDT", "USDC", "PERP")

    @property
    def base_url(self) -> str:
        return settings.bybit_base_url

    def collect_rows(
        self, fetcher: HttpFetcher
    ) -> tuple[list[PerpRow], list[FetchResult]]:
        tickers = fetcher.get_json("/v5/market/tickers", params={"category": "linear"})
        rows = [
            PerpRow(
                symbol=str(row.get("symbol") or ""),
                base=_strip_quote(str(row.get("symbol") or ""), self.QUOTES),
                vol_24h=_decimal(row.get("turnover24h")),
                oi_usd=_decimal(row.get("openInterestValue")),
                mark_price=_decimal(row.get("markPrice")),
                funding_rate=_decimal(row.get("fundingRate")),
            )
            for row in _rows(tickers.payload, "result", "list")
            if row.get("symbol")
        ]
        return rows, [tickers]


class GateAdapter(PerpVenueAdapter):
    """Gate. Open interest is in contracts; the multiplier is on a second endpoint."""

    source_id = "gate"
    exchange = "Gate.io"

    @property
    def base_url(self) -> str:
        return settings.gate_base_url

    def collect_rows(
        self, fetcher: HttpFetcher
    ) -> tuple[list[PerpRow], list[FetchResult]]:
        tickers = fetcher.get_json("/api/v4/futures/usdt/tickers")
        contracts = fetcher.get_json("/api/v4/futures/usdt/contracts")
        results = [tickers, contracts]

        multiplier = {
            str(row.get("name")): _decimal(row.get("quanto_multiplier"))
            for row in _rows(contracts.payload)
        }

        rows = []
        for row in _rows(tickers.payload):
            contract = str(row.get("contract") or "")
            if not contract:
                continue
            mark = _decimal(row.get("mark_price"))
            rows.append(
                PerpRow(
                    symbol=contract,
                    base=contract.split("_")[0],
                    vol_24h=_decimal(row.get("volume_24h_quote")),
                    oi_usd=_product(
                        _decimal(row.get("total_size")),
                        multiplier.get(contract),
                        mark,
                    ),
                    mark_price=mark,
                    funding_rate=_decimal(row.get("funding_rate")),
                )
            )
        return rows, results


class MexcAdapter(PerpVenueAdapter):
    """MEXC. Names tokenized equity ``AAPLSTOCK_USDT``; 283 of its contracts do."""

    source_id = "mexc"
    exchange = "MEXC"

    @property
    def base_url(self) -> str:
        return settings.mexc_futures_base_url

    def collect_rows(
        self, fetcher: HttpFetcher
    ) -> tuple[list[PerpRow], list[FetchResult]]:
        tickers = fetcher.get_json("/api/v1/contract/ticker")
        detail = fetcher.get_json("/api/v1/contract/detail")
        results = [tickers, detail]

        contract_size = {
            str(row.get("symbol")): _decimal(row.get("contractSize"))
            for row in _rows(detail.payload, "data")
        }

        rows = []
        for row in _rows(tickers.payload, "data"):
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            fair = _decimal(row.get("fairPrice"))
            rows.append(
                PerpRow(
                    symbol=symbol,
                    base=symbol.split("_")[0],
                    vol_24h=_decimal(row.get("amount24")),
                    oi_usd=_product(
                        _decimal(row.get("holdVol")),
                        contract_size.get(symbol),
                        fair,
                    ),
                    mark_price=fair,
                    funding_rate=_decimal(row.get("fundingRate")),
                )
            )
        return rows, results


class BitgetAdapter(PerpVenueAdapter):
    """Bitget. Open interest is in base coin, so it needs the last price."""

    source_id = "bitget"
    exchange = "Bitget"

    QUOTES = ("USDT", "USDC")

    @property
    def base_url(self) -> str:
        return settings.bitget_base_url

    def collect_rows(
        self, fetcher: HttpFetcher
    ) -> tuple[list[PerpRow], list[FetchResult]]:
        tickers = fetcher.get_json(
            "/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"}
        )
        rows = []
        for row in _rows(tickers.payload, "data"):
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            last = _decimal(row.get("lastPr"))
            rows.append(
                PerpRow(
                    symbol=symbol,
                    base=_strip_quote(symbol, self.QUOTES),
                    vol_24h=_decimal(row.get("usdtVolume")),
                    oi_usd=_product(_decimal(row.get("holdingAmount")), last),
                    mark_price=_decimal(row.get("markPrice")) or last,
                    funding_rate=_decimal(row.get("fundingRate")),
                )
            )
        return rows, [tickers]


ADAPTERS: tuple[PerpVenueAdapter, ...] = (
    OkxAdapter(),
    BybitAdapter(),
    GateAdapter(),
    MexcAdapter(),
    BitgetAdapter(),
)


@dataclass
class CexPerpCollector(Collector):
    """Collects one exchange's RWA perpetual contracts and rolls them to a venue."""

    adapter: PerpVenueAdapter
    source_id: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.source_id = self.adapter.source_id

    def _fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=self.adapter.base_url,
            rate_limit_per_minute=self.adapter.rate_limit_per_minute,
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        market_session = classify_session(snapshot_ts)
        cache = DimensionCache.load(session)
        stock_underlyings = _stock_underlyings(session)

        with self._fetcher() as fetcher:
            rows, results = self.adapter.collect_rows(fetcher)

        # Resolve before writing anything. These venues list hundreds of crypto-native
        # contracts, and calling ensure_perp_contract on all of them would fill
        # dim_perp_contract with out-of-scope rows and bury the genuine unmapped
        # symbols in a review queue nobody could then read.
        matched: list[tuple[PerpRow, str]] = []
        for row in rows:
            mapping = underlying_map.resolve(row.base, cache.known_underlyings)
            if mapping.underlying_id is None:
                if _looks_tokenized(row.base, mapping.rule):
                    # Dropping every unresolved symbol would be silent about the one
                    # case that matters: a symbol whose own naming says "tokenized
                    # equity" but which maps to no underlying we hold. That is either
                    # a product nobody has seeded yet or a rule that stopped working,
                    # and both need a human. The ~3,000 plain crypto tickers alongside
                    # it are dropped without a trace, which is what keeps this queue
                    # readable enough to actually be read.
                    cache.unmapped.append(mapping)
                continue
            matched.append((row, mapping.underlying_id))

        if not matched:
            # Nothing resolved. That is a real state — a venue may genuinely list no
            # RWA perps — but it is reported as an absence of rows, not as a venue
            # row of zeros.
            return results

        for row, _ in matched:
            cache.ensure_perp_contract(
                contract_id=self._contract_id(row.symbol),
                exchange=self.adapter.exchange,
                # The contract name as the venue writes it, and separately the base
                # symbol to resolve on. Passing only the former would re-resolve
                # AAPL-USDT-SWAP, which matches nothing, and would file 200 already
                # mapped contracts as pending review.
                symbol=row.symbol,
                source_symbol=row.base,
            )
        # The dimension rows the facts point at have to be on the database first.
        session.flush()

        for row, _ in matched:
            session.add(
                FactPerpContractSnapshot(
                    contract_id=self._contract_id(row.symbol),
                    snapshot_ts=snapshot_ts,
                    market_session=market_session,
                    vol_24h=row.vol_24h,
                    # Contract-unit open interest is not comparable across venues
                    # with different multipliers, so only the USD figure is kept.
                    oi_units=None,
                    oi_usd=row.oi_usd,
                    funding_rate=row.funding_rate,
                    mark_price=row.mark_price,
                    index_price=None,
                )
            )

        all_rows = [row for row, _ in matched]
        stock_rows = [
            row for row, underlying_id in matched if underlying_id in stock_underlyings
        ]
        session.add(self._venue_row(all_rows, snapshot_ts, market_session, SEGMENT_ALL))
        # Written only when the venue actually lists equity perps. An empty stock row
        # would be a venue reporting zero equity turnover, which is a claim; its
        # absence is the truthful statement that there was nothing to report.
        if stock_rows:
            session.add(
                self._venue_row(stock_rows, snapshot_ts, market_session, SEGMENT_STOCK)
            )

        return results

    def _venue_row(
        self,
        rows: Sequence[PerpRow],
        snapshot_ts: datetime,
        market_session: Any,
        segment: str,
    ) -> FactPerpVenueSnapshot:
        with_oi = [r for r in rows if r.oi_usd is not None]
        return FactPerpVenueSnapshot(
            exchange=self.adapter.exchange,
            # Only Hyperliquid has permissionless sub-DEXs; a conventional exchange
            # is one venue and uses the empty string the column defaults to.
            perp_dex="",
            segment=segment,
            snapshot_ts=snapshot_ts,
            market_session=market_session,
            vol_24h=_sum_or_none([r.vol_24h for r in rows]),
            open_interest_usd=_sum_or_none([r.oi_usd for r in rows]),
            symbol_count=len(rows),
            # Volume and open interest can come from different calls, so the two
            # totals on one row can cover different contract counts. Stated rather
            # than left null, which would read as unknown coverage.
            oi_symbol_count=len(with_oi),
        )

    def _contract_id(self, symbol: str) -> str:
        return f"{self.adapter.source_id.upper()}:{symbol}"


def _looks_tokenized(base: str, rule: str | None) -> bool:
    """Whether an unresolved symbol is worth a human's attention.

    True when the symbol wears an issuer's wrapper naming — ``NVDAxyz``, ``FOOSTOCK``
    — or when it was held back as ambiguous. Those are claims about a security we do
    not recognise. A bare crypto ticker makes no such claim and is not queued.
    """
    if rule == "ambiguous_symbol":
        return True
    return any(base.endswith(suffix) for suffix, _ in underlying_map.SUFFIX_RULES)


def _stock_underlyings(session: Session) -> set[str]:
    """Underlyings that belong to the equity segment of the workbook's perp view."""
    stmt = select(DimUnderlying.underlying_id).where(
        DimUnderlying.asset_class.in_(tuple(_STOCK_CLASSES))
    )
    return set(session.execute(stmt).scalars())


def _sum_or_none(values: Sequence[Decimal | None]) -> Decimal | None:
    """Sum observed values, or return ``None`` when nothing was observed.

    Not ``sum(..., 0)``: a venue whose figures all failed to parse would otherwise
    report zero turnover, which reads as "nobody traded here" rather than "we did
    not see".
    """
    observed = [v for v in values if v is not None]
    if not observed:
        return None
    return sum(observed, start=Decimal(0))


def build_collectors() -> list[CexPerpCollector]:
    """One collector per venue, in the order the scheduler should run them."""
    return [CexPerpCollector(adapter=adapter) for adapter in ADAPTERS]
