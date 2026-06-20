import pytest

from trading_bot.formatting import money
from trading_bot.risk import RiskInputError, calculate_risk


def test_russian_money_format_preserves_cheap_prices() -> None:
    assert money(1727.07) == "1 727,07"
    assert money(10_000) == "10 000"
    assert money(0.00001234) == "0,00001234"
    assert money(-12.5) == "-12,5"
    assert money(float("nan")) == "-"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_risk_rejects_non_finite_numbers(value) -> None:
    with pytest.raises(RiskInputError, match="finite"):
        calculate_risk("BTCUSDT", "long", value, 90, 1000, 1, 120)


@pytest.mark.parametrize(
    ("side", "target"),
    [("long", 99), ("short", 101)],
)
def test_risk_rejects_target_on_wrong_side(side, target) -> None:
    stop = 90 if side == "long" else 110
    with pytest.raises(RiskInputError, match="target"):
        calculate_risk("BTCUSDT", side, 100, stop, 1000, 1, target)
