"""Get-or-create for dimension rows.

A fact row references a dimension by foreign key, so a collector that writes an
observation for a symbol nobody has seen before needs that symbol's dimension row to
exist first. This module is where that happens, and it lives in ``normalize`` rather
than ``ingest`` because deciding an asset's tier and underlying is a normalization
judgement, not a fetch.

The rule throughout: **fill blanks, never overwrite**. A reviewer who has corrected
an asset's underlying, or an operator who has renamed a venue, must not have that
work silently reverted by the next collection pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dimensions import (
    DimAsset,
    DimIssuer,
    DimPerpContract,
    DimPool,
    DimUnderlying,
    DimVenue,
)
from app.models.enums import MappingStatus, RwaTier, VenueType
from app.models.operations import UnderlyingMap
from app.services.normalize import underlying_map
from app.services.normalize.tiering import classify_tier
from app.services.normalize.venue_registry import VenueRegistry

#: Chains named in a venue's trailing qualifier, e.g. "PancakeSwap V3 (BSC)". The
#: qualifier is the only venue-type hint the ticker endpoint offers, and it is a hint
#: rather than a fact — a curated ``dim_venue`` row always wins, because ensure_venue
#: never rewrites an existing row.
KNOWN_CHAINS = frozenset(
    {
        "arbitrum",
        "avalanche",
        "base",
        "bsc",
        "ethereum",
        "optimism",
        "polygon",
        "solana",
        "sui",
        "ton",
        "tron",
    }
)


@dataclass
class DimensionCache:
    """One collection pass's view of the dimension tables.

    Holds the known underlyings and the venue registry so a pass over a few thousand
    tickers does not issue a query per ticker.
    """

    session: Session
    known_underlyings: set[str] = field(default_factory=set)
    venues: VenueRegistry = field(default_factory=VenueRegistry)
    _assets: dict[str, DimAsset] = field(default_factory=dict)
    _venue_rows: dict[str, DimVenue] = field(default_factory=dict)
    _contracts: dict[str, DimPerpContract] = field(default_factory=dict)
    _issuers: dict[str, DimIssuer] = field(default_factory=dict)
    _pools: dict[str, DimPool] = field(default_factory=dict)
    #: ``(source_id, source_symbol)`` pairs that already have an ``underlying_map``
    #: row, including ones added earlier in this pass. See ``_record_mapping``.
    _mapped: set[tuple[str, str]] = field(default_factory=set)
    #: Symbols this pass could not map. Surfaced on the data-quality page rather than
    #: guessed at; see ``underlying_map``.
    unmapped: list[underlying_map.MappingResult] = field(default_factory=list)

    @classmethod
    def load(cls, session: Session) -> DimensionCache:
        cache = cls(session=session)
        cache.known_underlyings = {
            row
            for row in session.execute(select(DimUnderlying.underlying_id)).scalars()
        }
        for venue in session.execute(select(DimVenue)).scalars():
            cache.venues.register(venue.venue_id, venue.name, venue.aliases)
            cache._venue_rows[venue.venue_id] = venue
        for asset in session.execute(select(DimAsset)).scalars():
            cache._assets[asset.asset_id] = asset
        for contract in session.execute(select(DimPerpContract)).scalars():
            cache._contracts[contract.contract_id] = contract
        for issuer in session.execute(select(DimIssuer)).scalars():
            cache._issuers[issuer.issuer_id] = issuer
        for pool in session.execute(select(DimPool)).scalars():
            cache._pools[pool.pool_id] = pool
        cache._mapped = {
            (source_id, source_symbol)
            for source_id, source_symbol in session.execute(
                select(UnderlyingMap.source_id, UnderlyingMap.source_symbol)
            ).all()
        }
        return cache

    # --- issuers -----------------------------------------------------------

    def ensure_issuer(self, issuer_id: str, name: str | None = None) -> DimIssuer:
        """Return the issuer row, creating a minimal one if it is new.

        ``official_product_count`` is deliberately left null: it comes from the
        issuer's own site, not from an aggregator, and a placeholder here would be
        used as a coverage denominator and quietly understate the market.
        """
        existing = self._issuers.get(issuer_id)
        if existing is not None:
            return existing
        issuer = DimIssuer(issuer_id=issuer_id, name=name or issuer_id)
        self.session.add(issuer)
        self._issuers[issuer_id] = issuer
        return issuer

    # --- assets ------------------------------------------------------------

    def ensure_asset(
        self,
        *,
        asset_id: str,
        symbol: str,
        name: str | None = None,
        chain: str | None = None,
        coin_id: str | None = None,
        issuer_id: str | None = None,
        source_symbol: str | None = None,
    ) -> DimAsset:
        """Return the asset row, creating and classifying it if it is new.

        ``symbol`` is what gets displayed; ``source_symbol`` is what gets resolved,
        defaulting to it. They differ when a collector normalises case for display —
        the suffix rules are case-sensitive (``AAPLx`` is an xStocks wrapper, ``AAPLX``
        is an unknown ticker ending in X), so resolution must see the original.
        """
        existing = self._assets.get(asset_id)
        if existing is not None:
            # Fill blanks only. Tier and underlying are never rewritten here: a
            # reviewer's correction outranks a rule.
            existing.name = existing.name or name
            existing.chain = existing.chain or chain
            existing.coin_id = existing.coin_id or coin_id
            existing.issuer_id = existing.issuer_id or issuer_id
            return existing

        mapping = underlying_map.resolve(
            source_symbol or symbol, self.known_underlyings
        )
        if not mapping.resolved:
            self.unmapped.append(mapping)
        decision = classify_tier(
            symbol=symbol, issuer_id=issuer_id, underlying_id=mapping.underlying_id
        )
        asset = DimAsset(
            asset_id=asset_id,
            coin_id=coin_id,
            symbol=symbol,
            name=name,
            chain=chain,
            rwa_tier=decision.tier,
            underlying_id=mapping.underlying_id,
            issuer_id=issuer_id,
        )
        self.session.add(asset)
        self._assets[asset_id] = asset
        self._record_mapping("coingecko", mapping)
        return asset

    def assets_by_symbol(self) -> dict[str, DimAsset]:
        """Known assets keyed on upper-case symbol.

        Lets a collector recognise a symbol another source already classified,
        instead of creating a dimension row from a bare exchange ticker and putting
        an unclassified token into the rankings. Later duplicates lose to earlier
        ones, which is arbitrary but stable.
        """
        by_symbol: dict[str, DimAsset] = {}
        for asset in self._assets.values():
            by_symbol.setdefault(asset.symbol.upper(), asset)
        return by_symbol

    def _record_mapping(
        self, source_id: str, mapping: underlying_map.MappingResult
    ) -> None:
        """Persist how a symbol was resolved, including when it was not.

        An automatic mapping nobody can explain is worse than no mapping, and an
        unresolved symbol that leaves no trace is one nobody ever reviews.

        The already-seen set is held in memory rather than re-queried per symbol.
        That is not only cheaper — it is the only thing that works here. ``SessionLocal``
        sets ``autoflush=False``, so a ``SELECT`` cannot see rows this same pass has
        added but not yet flushed, and two coins sharing a symbol would each read
        "absent" and insert. CoinGecko lists several: two distinct coins both spelled
        ``spcx``. The resulting duplicate key aborted the flush and cost the collector
        every asset, pair and category row it had gathered.
        """
        key = (source_id, mapping.source_symbol)
        if key in self._mapped:
            return
        self._mapped.add(key)
        self.session.add(
            UnderlyingMap(
                source_id=source_id,
                source_symbol=mapping.source_symbol,
                normalized_symbol=mapping.normalized_symbol,
                underlying_id=mapping.underlying_id,
                status=mapping.status,
                rule=mapping.rule,
            )
        )

    # --- venues ------------------------------------------------------------

    def ensure_venue(
        self,
        *,
        name: str,
        venue_type: VenueType | None = None,
        chain: str | None = None,
    ) -> DimVenue:
        """Resolve a venue spelling to its row, creating one for a new spelling.

        The provisional id is the canonical key, so the same venue keeps the same id
        across snapshots even before anyone names it properly.
        """
        venue_id = self.venues.resolve_or_create(name)
        existing = self._venue_rows.get(venue_id)
        if existing is not None:
            # Record every literal spelling seen, not just ones that resolve
            # differently. The alias list is what lets a human confirm that three
            # rows really were one venue rather than take the merge on trust.
            if name != existing.name:
                self._add_alias(existing, name)
                self.venues.register(venue_id, existing.name, existing.aliases)
            existing.chain = existing.chain or chain
            return existing

        guessed_type, guessed_chain = venue_hint(name)
        venue = DimVenue(
            venue_id=venue_id,
            name=name,
            venue_type=venue_type or guessed_type,
            chain=chain or guessed_chain,
        )
        self.session.add(venue)
        self._venue_rows[venue_id] = venue
        self.venues.register(venue_id, name)
        return venue

    @staticmethod
    def _add_alias(venue: DimVenue, name: str) -> None:
        aliases = [a for a in (venue.aliases or "").splitlines() if a.strip()]
        if name not in aliases:
            aliases.append(name)
            venue.aliases = "\n".join(aliases)

    # --- DEX pools ---------------------------------------------------------

    #: Quote tokens whose USD value needs no second price feed. A pool quoted in
    #: anything else reports USD figures that depend on another, weaker conversion,
    #: which the quality screen has to know about.
    CANONICAL_QUOTES = frozenset({"USDC", "USDT", "DAI", "USDC.E", "USDB", "FDUSD"})

    def ensure_pool(
        self,
        *,
        pool_id: str,
        network: str,
        dex: str,
        pool_address: str | None = None,
        base_asset_id: str | None = None,
        quote_token: str | None = None,
    ) -> DimPool:
        existing = self._pools.get(pool_id)
        if existing is not None:
            existing.pool_address = existing.pool_address or pool_address
            existing.base_asset_id = existing.base_asset_id or base_asset_id
            existing.quote_token = existing.quote_token or quote_token
            return existing

        pool = DimPool(
            pool_id=pool_id,
            network=network,
            dex=dex,
            pool_address=pool_address,
            base_asset_id=base_asset_id,
            quote_token=quote_token,
            is_canonical_quote=(quote_token or "").upper() in self.CANONICAL_QUOTES,
        )
        self.session.add(pool)
        self._pools[pool_id] = pool
        return pool

    # --- perpetual contracts ----------------------------------------------

    def ensure_perp_contract(
        self,
        *,
        contract_id: str,
        exchange: str,
        symbol: str,
        perp_dex: str | None = None,
        source_underlying_type: str | None = None,
        source_symbol: str | None = None,
    ) -> DimPerpContract:
        """Return the contract row, creating and mapping it if it is new.

        ``symbol`` is the exchange's own contract name, kept verbatim for
        reconciliation; ``source_symbol`` is what gets resolved, defaulting to it.
        They differ on venues whose contract name carries a quote suffix —
        ``AAPL-USDT-SWAP`` resolves to nothing, while the ``AAPL`` left after the
        caller strips its own quote grammar resolves exactly. Without this, every
        contract on such a venue lands in the review queue despite having mapped.
        """
        existing = self._contracts.get(contract_id)
        if existing is not None:
            # The exchange's own label is stored verbatim and only ever filled in,
            # never corrected: reconciling our numbers against theirs needs it intact.
            existing.source_underlying_type = (
                existing.source_underlying_type or source_underlying_type
            )
            return existing

        mapping = underlying_map.resolve(
            source_symbol or symbol, self.known_underlyings
        )
        if not mapping.resolved:
            self.unmapped.append(mapping)
        contract = DimPerpContract(
            contract_id=contract_id,
            exchange=exchange,
            perp_dex=perp_dex or None,
            symbol=symbol,
            source_underlying_type=source_underlying_type,
            analysis_group=None,
            underlying_id=mapping.underlying_id,
        )
        self.session.add(contract)
        self._contracts[contract_id] = contract
        self._record_mapping(exchange.lower(), mapping)
        return contract

    # --- reporting ---------------------------------------------------------

    @property
    def pending_review(self) -> list[underlying_map.MappingResult]:
        return [m for m in self.unmapped if m.status is MappingStatus.PENDING_REVIEW]


def venue_hint(name: str) -> tuple[VenueType, str | None]:
    """Guess a new venue's type and chain from its spelling.

    The ticker endpoint gives no venue type, so the trailing chain qualifier is all
    there is: "PancakeSwap V3 (BSC)" is on-chain, "Binance" is not. It is a hint —
    a DEX without a qualifier (Raydium) lands on the wrong side of it — which is why
    it only ever applies to a row being created, never to one that exists.
    """
    start = name.rfind("(")
    if start == -1 or not name.rstrip().endswith(")"):
        return VenueType.CEX, None
    qualifier = name[start + 1 : name.rstrip().rfind(")")].strip().lower()
    if qualifier in KNOWN_CHAINS:
        return VenueType.DEX, qualifier
    return VenueType.CEX, None


def known_underlying_ids(session: Session) -> set[str]:
    return set(session.execute(select(DimUnderlying.underlying_id)).scalars())


@dataclass(frozen=True, slots=True)
class Reresolution:
    """What a re-resolution pass changed, for the operator who ran it."""

    examined: int
    resolved: int
    retiered: int


def reresolve_unmapped(session: Session) -> Reresolution:
    """Re-run resolution for assets that never resolved, after the rules improve.

    ``ensure_asset`` fills blanks and never rewrites, which is right for a reviewer's
    correction and wrong for a corrected *rule*: an asset classified by a broken rule
    stays broken forever, because nothing ever asks it again. That is not theoretical.
    A missing lowercase suffix left 49 tokenized equities — Apple, Nvidia, Tesla —
    resolving to nothing and therefore tiered NON_RWA, which excluded them from every
    ranking, rollup and alert. Widening ``dim_underlying`` strands rows the same way.

    Deliberately narrow, so it can be run without reading the diff first:

    - Only assets with no underlying. A resolved mapping is never second-guessed.
    - Only mappings no human has touched. ``reviewed_by`` is the veto, and it wins.
    - Tier is recomputed from the asset's *current* issuer, so a row that gained an
      issuer since it was created lands at CORE_RWA rather than staying SYNTHETIC.

    An asset that still does not resolve is left exactly as it is, including its
    NON_RWA tier — being unable to name the underlying security is precisely the
    reason that tier exists.
    """
    known = known_underlying_ids(session)
    # Case-folded, unlike resolution itself. Both spellings tried below come from one
    # asset, so the question is whether a human has ruled on *this* asset — and if
    # they have, the conservative reading of their silence is to leave it alone.
    # Erring this way costs an unresolved row somebody can fix by hand; erring the
    # other way overwrites a decision they already made.
    reviewed = {
        symbol.lower()
        for symbol in session.execute(
            select(UnderlyingMap.source_symbol).where(
                UnderlyingMap.reviewed_by.is_not(None)
            )
        ).scalars()
    }
    stranded = list(
        session.execute(
            select(DimAsset).where(DimAsset.underlying_id.is_(None))
        ).scalars()
    )

    resolved = retiered = 0
    for asset in stranded:
        mapping = _resolve_either_casing(asset.symbol, known, reviewed)
        if mapping is None:
            continue

        asset.underlying_id = mapping.underlying_id
        resolved += 1
        decision = classify_tier(
            symbol=asset.symbol,
            issuer_id=asset.issuer_id,
            underlying_id=mapping.underlying_id,
        )
        if decision.tier is not asset.rwa_tier:
            asset.rwa_tier = decision.tier
            retiered += 1

    return Reresolution(examined=len(stranded), resolved=resolved, retiered=retiered)


def _resolve_either_casing(
    symbol: str, known: set[str], reviewed: set[str]
) -> underlying_map.MappingResult | None:
    """Resolve a display symbol, trying the casing its source would have sent.

    ``ensure_asset`` resolves the source spelling, but only the display symbol
    survives on the row. Both casings are tried because the suffix rules are
    case-sensitive on purpose and it is the source's own casing that maps: CoinGecko
    sends ``aaplb``, a venue sends ``AAPLB``, and they are the same asset.
    """
    if symbol.lower() in reviewed:
        return None
    for spelling in (symbol, symbol.lower()):
        mapping = underlying_map.resolve(spelling, known)
        if mapping.resolved:
            return mapping
    return None


def tiers_of(assets: Iterable[DimAsset]) -> dict[str, RwaTier]:
    return {a.asset_id: a.rwa_tier for a in assets}
