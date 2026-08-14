"""Tests for metric scope isolation — the system's central invariant."""

from decimal import Decimal

import pytest

from app.core.metrics import (
    MetricScope,
    MetricScopeViolation,
    ScopedValue,
    assert_same_axis,
    safe_sum,
)


def _usd(amount: str, scope: MetricScope) -> ScopedValue:
    return ScopedValue(amount=Decimal(amount), scope=scope)


def test_sums_within_one_scope() -> None:
    result = safe_sum(
        [
            _usd("97222012", MetricScope.SPOT_VOLUME),
            _usd("30806261", MetricScope.SPOT_VOLUME),
        ]
    )
    assert result.amount == Decimal("128028273")
    assert result.scope is MetricScope.SPOT_VOLUME
    assert result.verified


def test_refuses_to_add_perp_volume_to_spot_volume() -> None:
    """The headline error this module exists to prevent."""
    with pytest.raises(MetricScopeViolation, match="refusing to add across"):
        safe_sum(
            [
                _usd("209765386", MetricScope.SPOT_VOLUME),
                _usd("4440000000", MetricScope.PERP_VOLUME),
            ]
        )


def test_refuses_to_add_open_interest_to_market_cap() -> None:
    with pytest.raises(MetricScopeViolation):
        safe_sum(
            [
                _usd("2620293541", MetricScope.SPOT_MARKET_CAP),
                _usd("10941040000", MetricScope.PERP_OI),
            ]
        )


def test_unverified_input_is_skipped_not_zeroed() -> None:
    """A missing observation must not be silently counted as zero."""
    result = safe_sum(
        [
            _usd("97222012", MetricScope.SPOT_VOLUME),
            ScopedValue(amount=None, scope=MetricScope.SPOT_VOLUME, verified=False),
        ]
    )
    assert result.amount == Decimal("97222012")
    # Partial coverage must not be presented as a complete total.
    assert not result.verified


def test_all_unverified_yields_no_amount() -> None:
    result = safe_sum(
        [ScopedValue(amount=None, scope=MetricScope.PERP_OI, verified=False)]
    )
    assert result.amount is None
    assert not result.verified


def test_verified_value_must_carry_an_amount() -> None:
    with pytest.raises(ValueError):
        ScopedValue(amount=None, scope=MetricScope.SPOT_VOLUME, verified=True)


def test_empty_sum_is_rejected() -> None:
    with pytest.raises(MetricScopeViolation):
        safe_sum([])


def test_stock_and_flow_cannot_share_an_axis() -> None:
    with pytest.raises(MetricScopeViolation, match="Y axis"):
        assert_same_axis([MetricScope.PERP_OI, MetricScope.PERP_VOLUME])


def test_two_flow_metrics_may_share_an_axis() -> None:
    assert_same_axis([MetricScope.SPOT_VOLUME, MetricScope.PERP_VOLUME])
