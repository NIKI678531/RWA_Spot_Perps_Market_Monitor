"""Tests for get-or-create of dimension rows.

The property under test throughout is that a collection pass fills blanks and never
overwrites: a reviewer's correction has to survive the next snapshot.
"""

from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.models.dimensions import DimAsset, DimUnderlying, DimVenue
from app.models.enums import AssetClass, MappingStatus, RwaTier, VenueType
from app.models.operations import UnderlyingMap
from app.services.normalize.dimensions import (
    DimensionCache,
    reresolve_unmapped,
    venue_hint,
)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            DimUnderlying(
                underlying_id="SPY", name="SPDR S&P 500 ETF", asset_class=AssetClass.ETF
            )
        )
        session.commit()
        yield session


def test_a_new_wrapper_is_tiered_and_mapped_on_first_sight(session: Session) -> None:
    cache = DimensionCache.load(session)
    asset = cache.ensure_asset(asset_id="spyb", symbol="SPYB", issuer_id="bStocks")
    session.commit()

    assert asset.underlying_id == "SPY"
    assert asset.rwa_tier is RwaTier.CORE_RWA


def test_an_unmappable_symbol_becomes_a_review_item_not_a_guess(
    session: Session,
) -> None:
    """GOLD, GOLDJM and GLDMINE are three securities; stripping would merge them."""
    cache = DimensionCache.load(session)
    asset = cache.ensure_asset(asset_id="goldjm", symbol="GOLDJM")
    session.commit()

    assert asset.underlying_id is None
    assert asset.rwa_tier is RwaTier.NON_RWA
    mapping = session.query(UnderlyingMap).filter_by(source_symbol="GOLDJM").one()
    assert mapping.status is MappingStatus.PENDING_REVIEW
    assert [m.source_symbol for m in cache.pending_review] == ["GOLDJM"]


def test_a_reviewed_mapping_survives_the_next_collection(session: Session) -> None:
    session.add(
        DimAsset(
            asset_id="spcxb",
            symbol="SPCXB",
            rwa_tier=RwaTier.CORE_RWA,
            underlying_id="SPY",  # a human decided this
        )
    )
    session.commit()

    cache = DimensionCache.load(session)
    asset = cache.ensure_asset(asset_id="spcxb", symbol="SPCXB", name="SpaceX bStock")
    session.commit()

    assert asset.underlying_id == "SPY", "a rule must not overrule a reviewer"
    assert asset.rwa_tier is RwaTier.CORE_RWA
    assert asset.name == "SpaceX bStock", "blanks are still filled in"


def test_one_venue_spelled_three_ways_stays_one_venue(session: Session) -> None:
    cache = DimensionCache.load(session)
    first = cache.ensure_venue(name="PancakeSwap V3 (BSC)")
    second = cache.ensure_venue(name="PancakeSwap v3")
    third = cache.ensure_venue(name="pancakeswap-v3")
    session.commit()

    assert first.venue_id == second.venue_id == third.venue_id
    assert session.query(DimVenue).count() == 1
    assert "PancakeSwap v3" in (first.aliases or "")


def test_a_chain_qualifier_is_the_only_venue_type_hint_taken() -> None:
    assert venue_hint("PancakeSwap V3 (BSC)") == (VenueType.DEX, "bsc")
    assert venue_hint("Binance") == (VenueType.CEX, None)
    # A parenthesised qualifier that names no chain proves nothing either way.
    assert venue_hint("Some Exchange (Pro)") == (VenueType.CEX, None)


def test_a_curated_venue_type_is_not_overwritten(session: Session) -> None:
    session.add(DimVenue(venue_id="raydium", name="Raydium", venue_type=VenueType.DEX))
    session.commit()

    cache = DimensionCache.load(session)
    venue = cache.ensure_venue(name="Raydium")
    session.commit()

    # The name carries no chain qualifier, so the hint would have said CEX.
    assert venue.venue_type is VenueType.DEX


def test_a_perpetual_never_counts_as_a_custodied_claim(session: Session) -> None:
    cache = DimensionCache.load(session)
    contract = cache.ensure_perp_contract(
        contract_id="HL:rwa:SPY",
        exchange="Hyperliquid",
        symbol="SPY",
        perp_dex="rwa",
        source_underlying_type="EQUITY",
    )
    session.commit()

    assert contract.underlying_id == "SPY"
    assert contract.source_underlying_type == "EQUITY", "the venue's label, verbatim"


def test_two_coins_sharing_a_symbol_produce_one_mapping_row() -> None:
    """CoinGecko lists two distinct coins both spelled ``spcx``.

    ``SessionLocal`` runs with ``autoflush=False``, so an existence check issued as a
    ``SELECT`` cannot see a row this pass has added but not yet flushed. Both coins
    then read "absent" and insert, and the duplicate key aborts the flush — costing
    the collector every asset, pair and category row it had gathered, not just the
    symbol that clashed. The fixture above cannot catch this: a bare ``Session``
    autoflushes and hides it, which is why this test builds its own.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        cache = DimensionCache.load(session)
        cache.ensure_asset(
            asset_id="spacex-tokenized", symbol="SPCX", source_symbol="spcx"
        )
        cache.ensure_asset(
            asset_id="spcx-another-wrapper", symbol="SPCX", source_symbol="spcx"
        )
        session.commit()

        rows = session.query(UnderlyingMap).filter_by(source_symbol="spcx").all()

    assert len(rows) == 1, "the second coin inserted a duplicate mapping row"


class TestReresolution:
    """Re-running resolution after the rules improve.

    ``ensure_asset`` never rewrites, which strands every asset a broken rule got
    wrong. The live case: a missing lowercase suffix left 49 tokenized equities
    unresolved and therefore NON_RWA, which is the tier that gates them out of every
    ranking, rollup and alert.
    """

    def test_an_asset_stranded_by_an_old_rule_is_picked_up(
        self, session: Session
    ) -> None:
        cache = DimensionCache.load(session)
        # SPZ resolves to nothing, standing in for a symbol no rule handled yet.
        asset = cache.ensure_asset(asset_id="spz", symbol="SPZ")
        session.commit()
        assert asset.rwa_tier is RwaTier.NON_RWA

        session.add(
            DimUnderlying(
                underlying_id="SPZ", name="Some Security", asset_class=AssetClass.EQUITY
            )
        )
        session.commit()
        report = reresolve_unmapped(session)
        session.commit()

        assert (report.resolved, report.retiered) == (1, 1)
        assert asset.underlying_id == "SPZ"
        assert asset.rwa_tier is RwaTier.SYNTHETIC

    def test_the_source_casing_is_tried_because_the_rules_are_case_sensitive(
        self, session: Session
    ) -> None:
        """The display symbol is upper; only ``spyb`` matches the lowercase rule."""
        cache = DimensionCache.load(session)
        asset = DimAsset(asset_id="spy-bstock", symbol="SPYB", rwa_tier=RwaTier.NON_RWA)
        session.add(asset)
        session.commit()
        del cache

        reresolve_unmapped(session)
        session.commit()

        assert asset.underlying_id == "SPY"

    def test_a_reviewers_correction_outranks_the_rules(self, session: Session) -> None:
        """A human who looked at this symbol and left it unmapped meant it."""
        asset = DimAsset(asset_id="spyb", symbol="SPYB", rwa_tier=RwaTier.NON_RWA)
        session.add(asset)
        session.add(
            UnderlyingMap(
                source_symbol="SPYB",
                source_id="coingecko",
                status=MappingStatus.PENDING_REVIEW,
                reviewed_by="an.analyst",
            )
        )
        session.commit()

        report = reresolve_unmapped(session)
        session.commit()

        assert report.resolved == 0
        assert asset.underlying_id is None

    def test_an_already_resolved_asset_is_never_second_guessed(
        self, session: Session
    ) -> None:
        """Only blanks are revisited, so a correct row cannot be churned."""
        cache = DimensionCache.load(session)
        asset = cache.ensure_asset(asset_id="spyb", symbol="SPYB", issuer_id="bStocks")
        session.commit()
        assert asset.rwa_tier is RwaTier.CORE_RWA

        report = reresolve_unmapped(session)

        assert report.examined == 0
        assert asset.rwa_tier is RwaTier.CORE_RWA

    def test_a_symbol_that_still_resolves_to_nothing_stays_non_rwa(
        self, session: Session
    ) -> None:
        """Not knowing which security a token represents is what NON_RWA means."""
        asset = DimAsset(asset_id="wat", symbol="WEIRDCOIN", rwa_tier=RwaTier.NON_RWA)
        session.add(asset)
        session.commit()

        report = reresolve_unmapped(session)

        assert (report.examined, report.resolved) == (1, 0)
        assert asset.rwa_tier is RwaTier.NON_RWA
