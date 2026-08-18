"""Tests for the normalize layer: mapping, tiering, quality, venues, dedup."""

from decimal import Decimal

import pytest

from app.core.metrics import MetricScope
from app.models.enums import MappingStatus, RwaTier
from app.services.normalize import dedup, quality, underlying_map, venue_registry
from app.services.normalize.tiering import classify_tier

KNOWN = frozenset({"SPY", "AAPL", "TSLA", "QQQ"})


class TestUnderlyingMap:
    def test_three_issuers_wrappers_collapse_to_one_underlying(self) -> None:
        """SPYB, SPYx and SPY-ON are the same ETF wearing three wrappers."""
        results = underlying_map.resolve_all(["SPYB", "SPYx", "SPY-ON"], KNOWN)

        assert [r.underlying_id for r in results] == ["SPY", "SPY", "SPY"]
        assert all(r.status is MappingStatus.AUTO for r in results)

    def test_a_symbol_naming_its_own_underlying_needs_no_rule(self) -> None:
        result = underlying_map.resolve("AAPL", KNOWN)
        assert result.underlying_id == "AAPL"
        assert result.rule == "exact_match"

    def test_stripping_never_invents_an_underlying(self) -> None:
        """MATICX would strip to MATIC, which is not a security we track."""
        result = underlying_map.resolve("MATICX", KNOWN)
        assert result.underlying_id is None
        assert result.status is MappingStatus.PENDING_REVIEW

    def test_known_traps_are_never_stripped(self) -> None:
        """SKHX and SKHY differ by one character and trade about 7x apart."""
        for symbol in ("GOLD", "GOLDJM", "GLDMINE", "SKHX", "SKHY"):
            result = underlying_map.resolve(symbol, KNOWN | {"SKH"})
            assert result.underlying_id is None, symbol
            assert result.rule in (None, "never_strip")

    def test_unresolved_symbols_are_reviewable_rather_than_dropped(self) -> None:
        results = underlying_map.resolve_all(["SPYB", "WEIRDCOIN"], KNOWN)
        pending = underlying_map.pending_review(results)
        assert [r.source_symbol for r in pending] == ["WEIRDCOIN"]

    def test_a_lowercase_bstocks_symbol_resolves(self) -> None:
        """CoinGecko lowercases every symbol, so ``aaplb`` is how Apple arrives.

        This was live: only the uppercase B rule existed, so all 49 bStocks assets
        failed to resolve, tiered NON_RWA, and left the spot volume KPI blank.
        """
        result = underlying_map.resolve("aaplb", KNOWN)

        assert result.underlying_id == "AAPL"
        assert result.status is MappingStatus.AUTO
        assert result.rule == "strip_bstocks_lower_suffix"

    def test_both_spellings_of_a_wrapper_reach_the_same_underlying(self) -> None:
        """A source's capitalisation must not change which security it counts toward.

        Otherwise the same company is two entities: TSLAB from a venue and tslab from
        CoinGecko would rank separately and each look half its real size.
        """
        results = underlying_map.resolve_all(["TSLAB", "tslab"], KNOWN)
        assert [r.underlying_id for r in results] == ["TSLA", "TSLA"]

    def test_the_lowercase_rule_still_needs_a_seeded_security(self) -> None:
        """One character is a wide suffix, and the underlying set is what narrows it.

        ``arb`` is Arbitrum, not a wrapper around a security called AR.
        """
        assert underlying_map.resolve("arb", KNOWN).underlying_id is None


class TestTiering:
    def test_a_custodied_wrapper_is_core_rwa(self) -> None:
        decision = classify_tier(
            symbol="SPYx", issuer_id="xStocks", underlying_id="SPY"
        )
        assert decision.tier is RwaTier.CORE_RWA
        assert decision.in_scope

    def test_a_perpetual_on_a_real_security_is_synthetic_not_core(self) -> None:
        """Nobody holds a claim on a share, so it cannot count as market cap."""
        decision = classify_tier(
            symbol="SPY", issuer_id="xStocks", underlying_id="SPY", is_perpetual=True
        )
        assert decision.tier is RwaTier.SYNTHETIC

    def test_an_issuers_governance_token_is_adjacent_not_tokenized_exposure(
        self,
    ) -> None:
        decision = classify_tier(symbol="ONDO", issuer_id="Ondo", underlying_id=None)
        assert decision.tier is RwaTier.RWA_ADJACENT
        assert decision.in_scope

    def test_a_crypto_native_token_falls_out_of_scope(self) -> None:
        decision = classify_tier(symbol="DOGE", issuer_id=None, underlying_id=None)
        assert decision.tier is RwaTier.NON_RWA
        assert not decision.in_scope

    def test_an_unverified_backing_structure_does_not_reach_core(self) -> None:
        decision = classify_tier(
            symbol="SPYZ", issuer_id="SomeUnknownDesk", underlying_id="SPY"
        )
        assert decision.tier is RwaTier.SYNTHETIC


class TestQualityScreen:
    def test_the_native_bsc_case(self) -> None:
        """~$29.3mn raw against ~$216 adjusted, 17 of 19 pairs flagged."""
        pairs = [
            quality.Pair(f"flagged-{i}", Decimal("1723529.41"), is_quality_anomaly=True)
            for i in range(17)
        ] + [
            quality.Pair("clean-1", Decimal("108")),
            quality.Pair("clean-2", Decimal("108")),
        ]

        screen = quality.screen(pairs)

        assert screen.total_pairs == 19
        assert screen.flagged_pairs == 17
        assert screen.raw.amount is not None and screen.raw.amount > Decimal(
            "29_000_000"
        )
        assert screen.adjusted.amount == Decimal("216")
        assert screen.is_materially_divergent

    def test_both_figures_share_one_scope(self) -> None:
        screen = quality.screen([quality.Pair("p", Decimal("100"))])
        assert screen.raw.scope is MetricScope.SPOT_VOLUME
        assert screen.adjusted.scope is MetricScope.SPOT_VOLUME

    def test_a_venue_with_nothing_left_after_screening_is_divergent(self) -> None:
        """No adjusted figure at all is the widest divergence, not the absence of one.

        A venue whose every observed pair is flagged has an adjusted total of
        ``None`` — unverified, not zero. Reporting that as "raw and adjusted agree"
        would hide exactly the case rule 4 exists to expose.
        """
        pairs = [
            quality.Pair("a", Decimal("1000000"), is_quality_anomaly=True),
            quality.Pair("b", Decimal("500000"), is_quality_stale=True),
        ]

        screen = quality.screen(pairs)

        assert screen.adjusted.amount is None
        assert screen.is_materially_divergent

    def test_a_healthy_venue_is_not_flagged_as_divergent(self) -> None:
        pairs = [
            quality.Pair("a", Decimal("1000")),
            quality.Pair("b", Decimal("100"), is_quality_stale=True),
        ]
        screen = quality.screen(pairs)
        assert not screen.is_materially_divergent
        assert screen.flagged_share == pytest.approx(0.5)

    def test_an_unobserved_pair_is_not_a_zero(self) -> None:
        """A missing feed must not make a venue look idle."""
        screen = quality.screen(
            [quality.Pair("a", Decimal("1000")), quality.Pair("b", None)]
        )
        assert screen.unverified_pairs == 1
        assert screen.raw.amount == Decimal("1000")
        assert not screen.raw.verified  # partial coverage stays visibly partial


class TestVenueRegistry:
    def test_three_spellings_of_one_dex_rank_as_one_venue(self) -> None:
        registry = venue_registry.VenueRegistry()
        registry.register(
            "pancakeswap-v3", "PancakeSwap V3", aliases="PancakeSwap v3\npancakeswap_v3"
        )

        for spelling in ("PancakeSwap V3 (BSC)", "PancakeSwap v3", "pancakeswap_v3"):
            assert registry.resolve(spelling) == "pancakeswap-v3", spelling

    def test_an_unseen_spelling_is_recorded_rather_than_guessed(self) -> None:
        registry = venue_registry.VenueRegistry()
        registry.register("binance", "Binance")

        assert registry.resolve("Brand New DEX") is None
        assert "Brand New DEX" in registry.unknown_names

    def test_ingest_keeps_an_unknown_venues_volume_under_a_stable_id(self) -> None:
        registry = venue_registry.VenueRegistry()
        first = registry.resolve_or_create("Brand New DEX (Base)")
        second = registry.resolve_or_create("brand new dex")
        assert first == second == "brandnewdex"


class TestCategoryDedup:
    def _overlapping(self) -> dict[str, list[dedup.CoinObservation]]:
        spy = dedup.CoinObservation("spy-token", Decimal("100"), Decimal("10"))
        aapl = dedup.CoinObservation("aapl-token", Decimal("50"), Decimal("5"))
        return {
            "tokenized-stock": [spy, aapl],
            "tokenized-etf": [spy],
            "xstocks": [spy, aapl],
        }

    def test_only_the_union_row_is_additive(self) -> None:
        rows = dedup.build_rows(self._overlapping())
        additive = [r for r in rows if r.is_additive]
        assert len(additive) == 1
        assert additive[0].category_id == dedup.UNION_CATEGORY_ID

    def test_the_union_counts_each_coin_once(self) -> None:
        rows = dedup.build_rows(self._overlapping())
        union = next(r for r in rows if r.is_additive)

        assert union.asset_count == 2
        assert union.market_cap.amount == Decimal("150")

        # Adding the source categories instead gives 400 — 2.7x the real figure, and
        # nothing about the number itself would look wrong.
        naive = sum(
            (r.market_cap.amount or Decimal(0) for r in rows if not r.is_additive),
            start=Decimal(0),
        )
        assert naive == Decimal("400")

    def test_the_overlap_is_reportable(self) -> None:
        assert dedup.overlap_count(self._overlapping()) == 3
