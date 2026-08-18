"""TradFi reference prices for the underlyings we track. Key-gated.

Every other collector measures a wrapper. This one measures the share, so that a
tokenized price has something to be right or wrong against — the workbook's
``13_TradFi_Benchmark`` sheet, which was a manual paste of quotes, as a live series.

Two properties of the free tier decide how the rows are labelled:

**The feed is IEX, not the consolidated tape.** IEX is a single venue carrying a few
per cent of US equity volume. Its *price* tracks the market closely enough to be a
sanity check on a tokenized quote; its *volume* is a fraction of the real thing and
its daily close is that venue's last print rather than the official closing auction,
which happens at the listing exchange. Hence ``feed`` on every row, share volume kept
out of the money columns, and no claim anywhere that this is a benchmark of record.

**The underlying is shut most of the time.** RWA tokens trade continuously; the NYSE
does not. Outside RTH this collector re-reads the same print, so ``price_ts`` carries
the source's own timestamp and the row records what was true when we looked rather
than pretending to a fresh observation. Reading the price without reading
``price_ts`` turns every weekend into an apparent mispricing.

The universe is US equities and ETFs from ``dim_underlying``. Indices, spot metals
and pre-IPO names are not securities this API quotes, and asking for them would turn
"out of universe" into a fetch failure — two different things that need different
answers on the data-quality page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import classify_session
from app.models.dimensions import DimUnderlying
from app.models.enums import AssetClass, FetchStatus
from app.models.facts import FactUnderlyingReference
from app.services.ingest.base import Collector, FetchResult, HttpFetcher

SOURCE_ID = "alpaca"
SNAPSHOTS_ENDPOINT = "/v2/stocks/snapshots"

#: What this API quotes. An index level and a spot metal fixing are not securities it
#: trades, and a pre-IPO name has no public price at all.
QUOTED_CLASSES = frozenset({AssetClass.EQUITY, AssetClass.ETF})
#: Alpaca is a US broker. A non-US listing would 404 rather than answer.
QUOTED_REGION = "US"

#: Symbols per request. Alpaca allows more, but a long query string is the first
#: thing an intermediate proxy truncates, and a truncated symbol list fails silently
#: as "those symbols returned nothing".
BATCH_SIZE = 50

#: Trades are stamped to the nanosecond; ``datetime`` holds microseconds.
_FRACTION = re.compile(r"\.(\d+)")


def _decimal(value: Any) -> Decimal | None:
    """Parse a number, returning ``None`` for anything unreadable.

    Zero is preserved as zero — a genuine zero-volume session is an observation.
    Only an absent or malformed field becomes ``None``.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_ts(value: Any) -> datetime | None:
    """Parse Alpaca's RFC-3339 timestamp, nanoseconds and all."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # A naive timestamp from a UTC API is UTC; leaving it naive would make it
    # incomparable with snapshot_ts, which is the only comparison it exists for.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _batched(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _snapshots(payload: Any) -> Mapping[str, Any]:
    """Pull the symbol map out, tolerating the wrapped and bare forms."""
    if not isinstance(payload, Mapping):
        return {}
    inner = payload.get("snapshots")
    if isinstance(inner, Mapping):
        return inner
    return {k: v for k, v in payload.items() if isinstance(v, Mapping)}


def _field(row: Mapping[str, Any], block: str, key: str) -> Any:
    """Read ``row[block][key]`` without assuming either level exists."""
    section = row.get(block)
    return section.get(key) if isinstance(section, Mapping) else None


@dataclass
class AlpacaCollector(Collector):
    """Reads last trade and previous close for each tracked US underlying."""

    source_id: str = SOURCE_ID

    @staticmethod
    def is_configured() -> bool:
        """Whether a key pair exists. The scheduler asks before registering this."""
        return bool(settings.alpaca_api_key_id and settings.alpaca_api_secret_key)

    def _fetcher(self) -> HttpFetcher:
        return HttpFetcher(
            source_id=self.source_id,
            base_url=settings.alpaca_base_url,
            rate_limit_per_minute=200,
            headers={
                "APCA-API-KEY-ID": settings.alpaca_api_key_id,
                "APCA-API-SECRET-KEY": settings.alpaca_api_secret_key,
            },
        )

    def collect(self, session: Session, snapshot_ts: datetime) -> list[FetchResult]:
        if not self.is_configured():
            # A missing credential is a missing observation. Logged rather than
            # skipped so the data-quality page can say why the benchmark column is
            # empty instead of leaving a hole nobody can account for.
            return [
                FetchResult(
                    source_id=self.source_id,
                    endpoint=SNAPSHOTS_ENDPOINT,
                    status=FetchStatus.NOT_VERIFIED,
                    error="ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not "
                    "configured; source not collected.",
                )
            ]

        symbols = _tracked_symbols(session)
        if not symbols:
            # Nothing seeded yet. Not a source failure, and reporting it as one would
            # blame Alpaca for an empty dim_underlying.
            return [
                FetchResult(
                    source_id=self.source_id,
                    endpoint=SNAPSHOTS_ENDPOINT,
                    status=FetchStatus.OK,
                    record_count=0,
                )
            ]

        market_session = classify_session(snapshot_ts)
        results: list[FetchResult] = []

        with self._fetcher() as fetcher:
            for batch in _batched(symbols, BATCH_SIZE):
                result = fetcher.get_json(
                    SNAPSHOTS_ENDPOINT,
                    params={"symbols": ",".join(batch), "feed": settings.alpaca_feed},
                )
                if not result.ok:
                    results.append(result)
                    continue

                written = self._write(
                    session, result.payload, batch, snapshot_ts, market_session
                )
                results.append(
                    FetchResult(
                        source_id=self.source_id,
                        endpoint=SNAPSHOTS_ENDPOINT,
                        # A symbol the feed does not quote comes back missing rather
                        # than as an error, so a short answer is partial coverage of
                        # what we asked for, not a clean fetch of everything.
                        status=(
                            FetchStatus.OK
                            if written == len(batch)
                            else FetchStatus.PARTIAL
                        ),
                        http_status=result.http_status,
                        duration_ms=result.duration_ms,
                        record_count=written,
                        error=(
                            None
                            if written == len(batch)
                            else f"{written} of {len(batch)} symbols quoted; the rest "
                            "are not carried by this feed and were left unwritten."
                        ),
                    )
                )
        return results

    def _write(
        self,
        session: Session,
        payload: Any,
        batch: Sequence[str],
        snapshot_ts: datetime,
        market_session: Any,
    ) -> int:
        """Write one row per quoted symbol. Returns how many were written."""
        snapshots = _snapshots(payload)
        written = 0

        for symbol in batch:
            row = snapshots.get(symbol)
            if not isinstance(row, Mapping):
                continue

            # Last trade first, daily close as the fallback: on a feed this thin a
            # symbol can go a whole session without printing, and the bar still
            # carries the level.
            price = _decimal(_field(row, "latestTrade", "p"))
            if price is None:
                price = _decimal(_field(row, "dailyBar", "c"))
            prev_close = _decimal(_field(row, "prevDailyBar", "c"))
            if price is None and prev_close is None:
                # An entry with neither is the API saying it has nothing for this
                # symbol. A row of nulls would claim we looked and found no market.
                continue

            session.add(
                FactUnderlyingReference(
                    underlying_id=symbol,
                    snapshot_ts=snapshot_ts,
                    market_session=market_session,
                    price=price,
                    price_ts=_parse_ts(_field(row, "latestTrade", "t")),
                    prev_close=prev_close,
                    change_24h=_change(price, prev_close),
                    venue_vol_shares=_decimal(_field(row, "dailyBar", "v")),
                    feed=settings.alpaca_feed,
                )
            )
            written += 1
        return written


def _change(price: Decimal | None, prev_close: Decimal | None) -> Decimal | None:
    """Fractional move since the previous close, or ``None`` if it is not computable.

    Guarded on a zero previous close rather than trusting it not to happen: a feed
    glitch that prints 0.00 would otherwise take down the whole pass with a division
    error, costing every symbol after it in the batch.
    """
    if price is None or prev_close is None or prev_close == 0:
        return None
    return (price - prev_close) / prev_close


def _tracked_symbols(session: Session) -> list[str]:
    """The underlyings this API can quote, in a stable order.

    ``underlying_id`` doubles as the request symbol. That holds because the ids are
    the ticker as the listing exchange writes it — ``BRK.B``, not ``BRK-B`` — which is
    also what Alpaca expects. A future non-ticker id would need a mapping column here
    rather than a rule for rewriting it.

    Ordered so that a truncated batch is the same truncation every time; an unordered
    query would rotate which symbols fall off the end between passes and make the gap
    look like intermittent coverage.
    """
    stmt = (
        select(DimUnderlying.underlying_id)
        .where(DimUnderlying.asset_class.in_(sorted(QUOTED_CLASSES)))
        .where(DimUnderlying.region == QUOTED_REGION)
        .where(DimUnderlying.is_pre_ipo.is_(False))
        .order_by(DimUnderlying.underlying_id)
    )
    return [str(row) for row in session.execute(stmt).scalars()]


def build_collectors() -> list[Collector]:
    """The reference-price collector, when it can actually run.

    Returned empty rather than gated inside the collector so that an unconfigured
    deployment does not write a ``NOT_VERIFIED`` row every pass. A daily wall of
    identical known failures is how a data-quality page teaches people to ignore it;
    ``source_registry`` already records that this source is waiting on a key.
    """
    return [AlpacaCollector()] if AlpacaCollector.is_configured() else []
