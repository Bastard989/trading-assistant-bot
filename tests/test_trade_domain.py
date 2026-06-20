from decimal import Decimal

import pytest

from trading_bot.domain.trades import TradeValidationError, calculate_net_pnl, validate_trade


@pytest.mark.parametrize(
    ("side", "entry", "stop", "target"),
    [("long", 100, 90, 120), ("short", 100, 110, 80)],
)
def test_valid_long_and_short_geometry(side, entry, stop, target) -> None:
    assert validate_trade(side=side, entry=entry, stop=stop, target=target, quantity=1, leverage=10).side == side


@pytest.mark.parametrize(
    ("side", "entry", "stop", "target"),
    [("long", 100, 100, 120), ("long", 100, 90, 99), ("short", 100, 99, 80), ("short", 100, 110, 101)],
)
def test_invalid_geometry_is_rejected(side, entry, stop, target) -> None:
    with pytest.raises(TradeValidationError):
        validate_trade(side=side, entry=entry, stop=stop, target=target, quantity=1, leverage=10)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), "NaN"])
def test_non_positive_and_non_finite_values_are_rejected(value) -> None:
    with pytest.raises(TradeValidationError):
        validate_trade(side="long", entry=value, stop=90, target=120, quantity=1, leverage=1)


def test_decimal_net_pnl_separates_costs() -> None:
    result = calculate_net_pnl(
        side="short", entry=Decimal("100"), exit_price=Decimal("90"), quantity=Decimal("2"),
        commission=Decimal("0.20"), funding=Decimal("0.10"), slippage=Decimal("0.05"),
    )
    assert result == Decimal("19.65000000")
