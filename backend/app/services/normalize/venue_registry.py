"""Canonical venue names.

One DEX arrives as "PancakeSwap V3 (BSC)", "PancakeSwap v3" and "pancakeswap-v3"
from three endpoints. Left alone, it occupies three rows of a venue ranking and each
one understates it. Deduplicating in the chart layer is too late — by then the
shares have already been computed against a wrong denominator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Trailing chain qualifiers to drop before matching. The chain lives in its own
#: column; carrying it inside the name makes "Uniswap V3" and "Uniswap V3 (Base)"
#: look like different venues when the ranking is by venue.
_CHAIN_SUFFIX = re.compile(r"\s*\((?:[^)]+)\)\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def canonical_key(name: str) -> str:
    """Reduce a venue spelling to a comparison key.

    Lowercased, chain qualifier removed, punctuation and spacing collapsed. This is a
    matching key only — the display name always comes from ``dim_venue``, because a
    ranking that shows "pancakeswapv3" to a reader is not a report.
    """
    without_chain = _CHAIN_SUFFIX.sub("", name.strip())
    return _NON_ALNUM.sub("", without_chain.lower())


@dataclass
class VenueRegistry:
    """Maps any observed spelling to a stable ``venue_id``.

    Built from ``dim_venue`` rows and their newline-separated ``aliases``. Unknown
    spellings are recorded rather than silently assigned, so a venue rename shows up
    as a new entry to confirm instead of a venue that quietly drops to zero volume.
    """

    _by_key: dict[str, str] = field(default_factory=dict)
    _unknown: set[str] = field(default_factory=set)

    def register(self, venue_id: str, name: str, aliases: str | None = None) -> None:
        self._by_key[canonical_key(name)] = venue_id
        self._by_key[canonical_key(venue_id)] = venue_id
        for alias in (aliases or "").splitlines():
            if alias.strip():
                self._by_key[canonical_key(alias)] = venue_id

    def resolve(self, name: str) -> str | None:
        """Return the canonical ``venue_id``, or ``None`` for an unseen spelling."""
        key = canonical_key(name)
        venue_id = self._by_key.get(key)
        if venue_id is None:
            self._unknown.add(name)
        return venue_id

    def resolve_or_create(self, name: str) -> str:
        """Resolve, falling back to the canonical key as a provisional id.

        Used during ingest, where dropping an unrecognised venue would lose its
        volume entirely. The provisional id is deterministic, so the same venue keeps
        the same id across snapshots until someone names it properly.
        """
        return self.resolve(name) or canonical_key(name)

    @property
    def unknown_names(self) -> frozenset[str]:
        """Spellings seen but not registered. Surfaced on the data-quality page."""
        return frozenset(self._unknown)
