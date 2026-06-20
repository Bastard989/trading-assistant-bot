from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN


class TradeValidationError(ValueError):
    pass


MAX_PRICE = Decimal("1000000000")
MAX_QUANTITY = Decimal("1000000000000")
MAX_LEVERAGE = Decimal("1000")


def decimal_value(value: object, field: str, *, maximum: Decimal) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradeValidationError(f"{field} must be a number") from exc
    if not result.is_finite() or result <= 0 or result > maximum:
        raise TradeValidationError(f"{field} must be finite, positive and within bounds")
    return result


@dataclass(frozen=True)
class ValidatedTrade:
    side: str
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    quantity: Decimal
    leverage: Decimal


def validate_trade(
    *, side: str, entry: object, stop: object, target: object | None,
    quantity: object, leverage: object,
) -> ValidatedTrade:
    normalized_side = side.strip().lower()
    if normalized_side not in {"long", "short"}:
        raise TradeValidationError("side must be long or short")
    entry_value = decimal_value(entry, "entry", maximum=MAX_PRICE)
    stop_value = decimal_value(stop, "stop", maximum=MAX_PRICE)
    target_value = None if target is None else decimal_value(target, "target", maximum=MAX_PRICE)
    quantity_value = decimal_value(quantity, "quantity", maximum=MAX_QUANTITY)
    leverage_value = decimal_value(leverage, "leverage", maximum=MAX_LEVERAGE)
    if normalized_side == "long" and not (stop_value < entry_value and (target_value is None or entry_value < target_value)):
        raise TradeValidationError("LONG requires stop < entry < target")
    if normalized_side == "short" and not (stop_value > entry_value and (target_value is None or entry_value > target_value)):
        raise TradeValidationError("SHORT requires target < entry < stop")
    return ValidatedTrade(normalized_side, entry_value, stop_value, target_value, quantity_value, leverage_value)


def calculate_net_pnl(
    *, side: str, entry: Decimal, exit_price: Decimal, quantity: Decimal,
    commission: Decimal = Decimal("0"), funding: Decimal = Decimal("0"),
    slippage: Decimal = Decimal("0"),
) -> Decimal:
    direction = Decimal("1") if side == "long" else Decimal("-1")
    gross = (exit_price - entry) * quantity * direction
    return (gross - commission - funding - slippage).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
