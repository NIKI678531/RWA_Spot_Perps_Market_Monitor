"""Tests for rollups and concentration."""

from decimal import Decimal

import pytest

from app.core.metrics import MetricScope, MetricScopeViolation, ScopedValue
from app.models.enums import RwaTier
from app.services.analytics import concentration
from app.services.analytics.rollups import Contribution, rollup


def _vol(group: str, amount: str | None, **kwargs: object) -> Contribution:
    return Contribution(
        group_id=group,
        value=Decimal(amount) if amount is not None else None,
        scope=MetricScope.SPOT_VOLUME,
        **kwargs,  # type: ignore[arg-type]
    )


class TestRollup:
    def test_groups_and_orders_by_size(self) -> None:
        result = rollup(
            [_vol("binance", "300"), _vol("bybit", "100"), _vol("binance", "200")]
        )

        assert [r.group_id for r in result.rows] == ["binance", "bybit"]
        assert result.rows[0].total.amount == Decimal("500")
        assert result.grand_total.amount == Decimal("600")

    def test_refuses_to_roll_up_across_scopes(self) -> None:
        mixed = [
            _vol("binance", "300"),
            Contribution("binance", Decimal("1"), MetricScope.PERP_OI),
        ]
        with pytest.raises(MetricScopeViolation):
            rollup(mixed)

    def test_out_of_scope_tiers_are_excluded_but_counted(self) -> None:
        """A tokenized-RWA total that includes crypto-native tokens is not one."""
        result = rollup(
            [
                _vol("binance", "300"),
                _vol("binance", "9000", rwa_tier=RwaTier.NON_RWA),
            ]
        )

        assert result.grand_total.amount == Decimal("300")
        assert result.excluded_out_of_scope == 1

    def test_benchmark_rows_can_be_requested_explicitly(self) -> None:
        result = rollup(
            [_vol("binance", "9000", rwa_tier=RwaTier.NON_RWA)],
            include_out_of_scope=True,
        )
        assert result.grand_total.amount == Decimal("9000")

    def test_an_unverified_group_does_not_rank_at_zero(self) -> None:
        result = rollup([_vol("live", "100"), _vol("dark", None, verified=False)])

        assert [r.group_id for r in result.rows] == ["live", "dark"]
        assert result.rows[1].total.amount is None
        assert not result.rows[1].total.verified

    def test_share_is_a_ratio_bound_to_its_weighting_basis(self) -> None:
        result = rollup([_vol("a", "750"), _vol("b", "250")])
        share = result.share_of("a")

        assert share.value == Decimal("0.75")
        assert share.weight_basis is MetricScope.SPOT_VOLUME


class TestConcentration:
    def _scoped(self, amounts: list[str]) -> list[ScopedValue]:
        return [ScopedValue(Decimal(a), MetricScope.PERP_VOLUME) for a in amounts]

    def test_the_binance_tradfi_shape(self) -> None:
        """The top 10 contracts carry 78.2%, the largest alone carries 28.2%."""
        head = ["28.2", "12.0", "9.0", "7.0", "6.0", "5.0", "4.0", "3.0", "2.5", "1.5"]
        tail = ["1.09"] * 20  # the remaining 21.8%, spread thin
        result = concentration.compute(
            self._scoped(head + tail), [f"c{i}" for i in range(30)]
        )

        top10 = result.top_n_share(10)
        assert top10.value is not None
        assert float(top10.value) == pytest.approx(0.782, abs=0.001)
        assert result.shares[0].entity_id == "c0"
        assert float(result.shares[0].share) == pytest.approx(0.282, abs=0.001)

    def test_a_monopoly_scores_the_maximum_hhi(self) -> None:
        result = concentration.compute(self._scoped(["100"]), ["only"])
        assert result.hhi == Decimal(10_000)
        assert result.is_concentrated

    def test_an_even_market_is_unconcentrated(self) -> None:
        result = concentration.compute(
            self._scoped(["10"] * 20), [f"c{i}" for i in range(20)]
        )
        assert float(result.hhi) == pytest.approx(500.0)
        assert not result.is_concentrated

    def test_refuses_to_mix_scopes(self) -> None:
        values = [
            ScopedValue(Decimal("1"), MetricScope.PERP_VOLUME),
            ScopedValue(Decimal("1"), MetricScope.PERP_OI),
        ]
        with pytest.raises(MetricScopeViolation):
            concentration.compute(values, ["a", "b"])

    def test_unverified_competitors_leave_the_denominator_alone(self) -> None:
        """Padding the denominator with zeros would overstate everyone else's share."""
        values = [
            ScopedValue(Decimal("100"), MetricScope.PERP_VOLUME),
            ScopedValue(None, MetricScope.PERP_VOLUME, verified=False),
        ]
        result = concentration.compute(values, ["known", "dark"])

        assert result.unverified_count == 1
        assert result.shares[0].share == Decimal(1)
        assert not result.top_n_share(1).verified
