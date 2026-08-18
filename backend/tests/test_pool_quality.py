"""Tests for the DEX pool reserve plausibility screen.

The failure this exists to prevent was live: GeckoTerminal reported ``AAPLX / USDC``
on Solana holding $192.8bn against $0 of 24h volume, and five pools of that kind
summed to $426bn of "DEX liquidity" on the executive KPI — several hundred times the
tokenized market the page exists to measure.

The number is the source's own, so it cannot be parsed away. What makes it decidable
is that the pools do not trade.
"""

from decimal import Decimal

from app.core.metrics import MetricScope
from app.services.normalize.quality import (
    POOL_RESERVE_FLOOR_USD,
    Pool,
    screen_pools,
)


def _pool(pool_id: str, reserve: str | None, vol: str | None) -> Pool:
    return Pool(
        pool_id=pool_id,
        reserve_usd=None if reserve is None else Decimal(reserve),
        vol_24h=None if vol is None else Decimal(vol),
    )


def test_a_huge_reserve_with_no_trading_is_flagged() -> None:
    """The observed case, at its observed magnitudes."""
    assert _pool("solana_AAPLX", "192838106734", "0").is_flagged


def test_a_huge_reserve_that_actually_trades_is_kept() -> None:
    """Depth is not itself suspicious. A real deep pool turns over."""
    assert not _pool("solana_real", "192838106734", "9000000000").is_flagged


def test_a_small_untraded_pool_is_not_flagged() -> None:
    """Below the floor, being wrong is not a finding — and dead small pools are normal.

    Screening them would flag most of the long tail to protect a total they cannot
    move, which spends the screen's credibility for nothing.
    """
    assert not _pool("solana_dust", "500", "0").is_flagged


def test_unobserved_volume_cannot_convict_a_pool() -> None:
    """``NOT_VERIFIED`` is not zero.

    A pool whose volume failed to parse looks exactly like one that did not trade if
    null is read as 0, and that would turn a fetch problem into a quality verdict.
    """
    assert not _pool("solana_unknown", "192838106734", None).is_flagged


def test_the_screen_reports_both_totals() -> None:
    """Raw and adjusted side by side: the gap is the finding, not a number to hide."""
    result = screen_pools(
        [
            _pool("junk", "192838106734", "0"),
            _pool("real_a", "1000000", "250000"),
            _pool("real_b", "500000", "100000"),
        ]
    )

    assert result.raw.amount == Decimal("192839606734")
    assert result.adjusted.amount == Decimal("1500000")
    assert result.flagged_pairs == 1
    assert result.total_pairs == 3
    assert result.raw.scope is MetricScope.DEX_LIQUIDITY
    # An adjusted total orders of magnitude under raw is exactly the case the UI
    # needs to warn about rather than quietly present.
    assert result.is_materially_divergent


def test_a_pool_with_no_reserve_is_unverified_not_zero() -> None:
    result = screen_pools([_pool("missing", None, "10"), _pool("real", "400", "5")])

    assert result.unverified_pairs == 1
    assert result.raw.amount == Decimal("400")
    # Coverage is incomplete, and the totals must say so rather than read as final.
    assert not result.raw.verified


def test_the_floor_is_the_boundary_it_claims_to_be() -> None:
    """A reserve exactly at the floor is screened; one cent under is not."""
    assert _pool("at", str(POOL_RESERVE_FLOOR_USD), "0").is_flagged
    under = POOL_RESERVE_FLOOR_USD - Decimal("0.01")
    assert not _pool("under", str(under), "0").is_flagged
