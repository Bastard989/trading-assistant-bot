from __future__ import annotations

from trading_bot.models import RiskCalculation


class RiskInputError(ValueError):
    pass


def calculate_risk(
    symbol: str,
    side: str,
    entry_price: float,
    stop_price: float,
    account_size: float,
    risk_percent: float,
    target_price: float | None = None,
    leverage: float = 1,
) -> RiskCalculation:
    side = side.lower()
    if side not in {"long", "short"}:
        raise RiskInputError("Side must be long or short.")
    if entry_price <= 0 or stop_price <= 0:
        raise RiskInputError("Entry and stop prices must be positive.")
    if account_size <= 0:
        raise RiskInputError("Account size must be positive.")
    if risk_percent <= 0:
        raise RiskInputError("Risk percent must be positive.")
    if leverage <= 0:
        raise RiskInputError("Leverage must be positive.")
    if target_price is not None and target_price <= 0:
        raise RiskInputError("Target price must be positive.")

    if side == "long" and stop_price >= entry_price:
        raise RiskInputError("For long trades stop must be below entry.")
    if side == "short" and stop_price <= entry_price:
        raise RiskInputError("For short trades stop must be above entry.")

    risk_per_unit = abs(entry_price - stop_price)
    risk_amount = account_size * risk_percent / 100
    quantity = risk_amount / risk_per_unit
    notional = quantity * entry_price
    margin = notional / leverage
    direction = 1 if side == "long" else -1

    profit_at_target = None
    reward_to_risk = None
    if target_price is not None:
        profit_at_target = (target_price - entry_price) * quantity * direction
        reward_to_risk = profit_at_target / risk_amount if risk_amount else None

    return RiskCalculation(
        symbol=symbol.upper(),
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        account_size=account_size,
        risk_percent=risk_percent,
        leverage=leverage,
        risk_amount=risk_amount,
        quantity=quantity,
        notional=notional,
        margin=margin,
        loss_at_stop=risk_amount,
        profit_at_target=profit_at_target,
        reward_to_risk=reward_to_risk,
    )
