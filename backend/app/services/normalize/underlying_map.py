"""Resolve a source symbol to the real-world security behind it.

``SPYB``, ``SPYx`` and ``SPY-ON`` are three issuers' wrappers of one ETF. Without
this step, "is anyone buying the S&P 500?" requires adding six rows by hand across
three issuers, which is how the manual workbook got it wrong.

The safety property is that stripping a suffix only *proposes* an underlying. The
proposal is accepted only if that underlying already exists in ``dim_underlying``.
The system therefore never invents a security, and an unrecognised symbol becomes a
review item rather than a confident wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet

from app.models.enums import MappingStatus

#: (suffix, rule name), tried in order. Case-sensitive: xStocks writes ``AAPLx``
#: with a lowercase x, and treating case loosely turns every symbol ending in X into
#: a mapping candidate.
#:
#: Longest suffixes come first. ``AMDSTOCK`` also ends in a bare ``K`` that no rule
#: claims, but a shorter rule matching first would strip the wrong number of
#: characters and propose a security nobody listed.
SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    # MEXC and Bybit name their tokenized-equity perps AAPLSTOCK / AMDSTOCK. 283 of
    # MEXC's 1,124 USDT contracts use it, so without this the largest RWA perp
    # universe on any venue resolves to nothing.
    ("STOCK", "strip_stock_suffix"),
    ("-ON", "strip_ondo_suffix"),
    # Ondo's own product pages write AAOIon, not AAOI-ON. Both spellings reach us:
    # the hyphenated one via aggregators, this one from ondo.finance directly.
    ("on", "strip_ondo_lower_suffix"),
    ("x", "strip_xstocks_suffix"),
    ("B", "strip_bstocks_suffix"),
    # CoinGecko writes its symbols in lowercase, so every Backed Finance product
    # arrives as ``aaplb`` and the uppercase rule above never fires. That excluded 49
    # tokenized equities — Apple, Nvidia, Tesla, Meta, Microsoft — from every ranking,
    # rollup and alert, because an asset with no resolved underlying tiers as
    # ``NON_RWA`` and ``rwa_tier`` gates all of them. The symptom was a blank spot
    # volume KPI: all 49 pairs in the snapshot were bStocks.
    #
    # A one-character suffix is the widest rule here, and it is safe only because a
    # candidate still has to name a seeded security. Checked against live data rather
    # than assumed: of 585 unresolved symbols, 49 end in a lowercase b, 45 resolve,
    # and every one of the 49 is named "(bStocks Tokenized Stock)" upstream. The four
    # that do not resolve are securities nobody seeded, which is the correct outcome.
    ("b", "strip_bstocks_lower_suffix"),
    ("X", "strip_upper_x_suffix"),
)

#: Symbols that must never be stripped, because the result would name a different
#: security. These are observed traps, not hypotheticals:
#:
#: - ``GOLD``, ``GOLDJM`` and ``GLDMINE`` are three distinct underlyings.
#: - ``SKHX`` and ``SKHY`` differ by one character and trade about 7x apart.
#:
#: Anything added here is a case where an automatic rule was wrong in production.
NEVER_STRIP = frozenset(
    {
        "GOLD",
        "GOLDJM",
        "GLDMINE",
        "SKHX",
        "SKHY",
    }
)

#: Tickers that name both an RWA underlying and a crypto-native token. The bare
#: spelling proves nothing, so it must not resolve even on an exact match — which is
#: why this is checked *above* the exact-match branch rather than in ``NEVER_STRIP``.
#:
#: Both were observed mapping wrongly against live venue data:
#:
#: - ``SPX`` marks at ~$0.31 on all five CEX perp venues. That is SPX6900, a
#:   memecoin; the S&P 500 index is four orders of magnitude away.
#: - ``DIA`` marks at ~$0.13, which is the DIA oracle token. The real Dow ETF trades
#:   on the same venues at ~$534, spelled ``DIASTOCK``.
#:
#: A *suffixed* spelling still resolves: ``DIASTOCK``, ``DIAx`` and ``DIA-ON`` all
#: carry an issuer's wrapper naming, and that suffix is the evidence the bare ticker
#: lacks. Counting memecoin turnover as demand for the Dow is the failure this
#: prevents, and it is invisible in any chart downstream.
#: The rest were found by a venue-internal test rather than by inspection: MEXC lists
#: both ``X_USDT`` and ``XSTOCK_USDT`` for each of them, which is that exchange
#: stating outright that the two are different instruments on different order books.
#: (A cross-venue sweep does not work for this — Gate, Bitget and Binance list
#: tokenized equities under bare tickers, so the stock gets counted against itself.)
#:
#: - ``BB``   BounceBit, against BlackBerry Ltd.
#: - ``C``    a crypto ticker, against Citigroup Inc.
#: - ``CAT``  a memecoin, against Caterpillar Inc.
#: - ``CVX``  Convex Finance, against Chevron Corp.
#: - ``ON``   a crypto ticker, against ON Semiconductor Corp.
#: - ``QNT``  Quant. No security is seeded for it at all, so it never resolves.
#: - ``STX``  Stacks, against Seagate Technology Holdings.
AMBIGUOUS_SYMBOLS = frozenset(
    {"SPX", "DIA", "BB", "C", "CAT", "CVX", "ON", "QNT", "STX"}
)


@dataclass(frozen=True, slots=True)
class MappingResult:
    """One symbol's resolution attempt, including the failures."""

    source_symbol: str
    #: The candidate after suffix stripping, kept even when it did not resolve so a
    #: bad rule can be audited without re-fetching.
    normalized_symbol: str | None
    underlying_id: str | None
    status: MappingStatus
    rule: str | None = None

    @property
    def resolved(self) -> bool:
        return self.underlying_id is not None


def resolve(source_symbol: str, known_underlyings: AbstractSet[str]) -> MappingResult:
    """Map ``source_symbol`` to an underlying, or flag it for review.

    ``known_underlyings`` is the set of ``dim_underlying.underlying_id`` values. A
    candidate outside that set is not a mapping — it is a guess, and guesses about
    which security a token represents are the one error nobody catches by eye.
    """
    symbol = source_symbol.strip()
    if not symbol:
        return MappingResult(source_symbol, None, None, MappingStatus.PENDING_REVIEW)

    upper = symbol.upper()

    # Checked before the exact match, because for these tickers the exact match is
    # precisely the wrong answer: the bare spelling belongs to a crypto-native token
    # that happens to share a name with a security.
    if upper in AMBIGUOUS_SYMBOLS:
        return MappingResult(
            source_symbol, upper, None, MappingStatus.PENDING_REVIEW, "ambiguous_symbol"
        )

    # An exact hit needs no rule and no review: the token is named for its underlying.
    if upper in known_underlyings:
        return MappingResult(
            source_symbol, upper, upper, MappingStatus.AUTO, "exact_match"
        )

    if upper in NEVER_STRIP:
        return MappingResult(
            source_symbol, None, None, MappingStatus.PENDING_REVIEW, "never_strip"
        )

    for suffix, rule in SUFFIX_RULES:
        if not symbol.endswith(suffix) or len(symbol) <= len(suffix):
            continue
        candidate = symbol[: -len(suffix)].upper()
        if candidate in known_underlyings:
            return MappingResult(
                source_symbol, candidate, candidate, MappingStatus.AUTO, rule
            )

    # A candidate was produced but matched nothing, or no rule applied at all. Both
    # are review items; neither is an error worth failing the pipeline over.
    return MappingResult(source_symbol, None, None, MappingStatus.PENDING_REVIEW, None)


def resolve_all(
    symbols: list[str], known_underlyings: AbstractSet[str]
) -> list[MappingResult]:
    """Resolve a batch, preserving input order."""
    return [resolve(s, known_underlyings) for s in symbols]


def pending_review(results: list[MappingResult]) -> list[MappingResult]:
    """The subset a human still has to look at. Surfaced on the data-quality page."""
    return [r for r in results if r.status is MappingStatus.PENDING_REVIEW]
