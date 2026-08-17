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
SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    ("-ON", "strip_ondo_suffix"),
    ("x", "strip_xstocks_suffix"),
    ("B", "strip_bstocks_suffix"),
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
