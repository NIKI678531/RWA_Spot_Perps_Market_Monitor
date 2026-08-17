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
from app.services.normalize.dimensions import DimensionCache, venue_hint


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
