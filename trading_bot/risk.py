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
    entry_fee_percent: float = 0.05,
    exit_fee_percent: float = 0.05,
    slippage_percent: float = 0.02,
    funding_rate_percent: float = 0,
    holding_hours: float = 0,
    funding_interval_hours: float = 8,
    maintenance_margin_percent: float = 0.5,
    margin_mode: str = "isolated",
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
    if min(entry_fee_percent, exit_fee_percent, slippage_percent, maintenance_margin_percent) < 0:
        raise RiskInputError("Fees, slippage and maintenance margin cannot be negative.")
    if holding_hours < 0 or funding_interval_hours <= 0:
        raise RiskInputError("Holding time must be valid.")
    margin_mode = margin_mode.lower()
    if margin_mode not in {"isolated", "cross"}:
        raise RiskInputError("Margin mode must be isolated or cross.")
    if target_price is not None and target_price <= 0:
        raise RiskInputError("Target price must be positive.")

    if side == "long" and stop_price >= entry_price:
        raise RiskInputError("For long trades stop must be below entry.")
    if side == "short" and stop_price <= entry_price:
        raise RiskInputError("For short trades stop must be above entry.")

    direction = 1 if side == "long" else -1
    funding_intervals = holding_hours / funding_interval_hours
    funding_per_unit = entry_price * funding_rate_percent / 100 * funding_intervals * direction
    entry_fee_per_unit = entry_price * entry_fee_percent / 100
    stop_fee_per_unit = stop_price * exit_fee_percent / 100
    stop_slippage_per_unit = (entry_price + stop_price) * slippage_percent / 100
    risk_per_unit = (
        abs(entry_price - stop_price)
        + entry_fee_per_unit
        + stop_fee_per_unit
        + stop_slippage_per_unit
        + abs(funding_per_unit)
    )
    risk_amount = account_size * risk_percent / 100
    quantity = risk_amount / risk_per_unit
    notional = quantity * entry_price
    margin = notional / leverage
    gross_loss_at_stop = abs(entry_price - stop_price) * quantity
    entry_fee = entry_fee_per_unit * quantity
    stop_exit_fee = stop_fee_per_unit * quantity
    stop_slippage = stop_slippage_per_unit * quantity
    funding_payment = funding_per_unit * quantity
    net_loss_at_stop = gross_loss_at_stop + entry_fee + stop_exit_fee + stop_slippage + funding_payment

    gross_profit_at_target = None
    target_exit_fee = None
    target_slippage = None
    net_profit_at_target = None
    reward_to_risk = None
    if target_price is not None:
        gross_profit_at_target = (target_price - entry_price) * quantity * direction
        target_exit_fee = target_price * quantity * exit_fee_percent / 100
        target_slippage = (entry_price + target_price) * quantity * slippage_percent / 100
        net_profit_at_target = gross_profit_at_target - entry_fee - target_exit_fee - target_slippage - funding_payment
        reward_to_risk = net_profit_at_target / net_loss_at_stop if net_loss_at_stop else None

    maintenance_rate = maintenance_margin_percent / 100
    entry_fee_rate = entry_fee_percent / 100
    liquidation_price = None
    if margin_mode == "isolated":
        if side == "long" and maintenance_rate < 1:
            liquidation_price = entry_price * (1 - 1 / leverage + entry_fee_rate) / (1 - maintenance_rate)
        elif side == "short":
            liquidation_price = entry_price * (1 + 1 / leverage - entry_fee_rate) / (1 + maintenance_rate)
        liquidation_price = max(liquidation_price or 0, 0)
    liquidation_distance_percent = (
        abs(liquidation_price - entry_price) / entry_price * 100 if liquidation_price is not None else None
    )
    minimum_leverage = notional / account_size

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
        loss_at_stop=net_loss_at_stop,
        profit_at_target=net_profit_at_target,
        reward_to_risk=reward_to_risk,
        gross_loss_at_stop=gross_loss_at_stop,
        gross_profit_at_target=gross_profit_at_target,
        entry_fee=entry_fee,
        stop_exit_fee=stop_exit_fee,
        target_exit_fee=target_exit_fee,
        stop_slippage=stop_slippage,
        target_slippage=target_slippage,
        funding_payment=funding_payment,
        net_loss_at_stop=net_loss_at_stop,
        net_profit_at_target=net_profit_at_target,
        liquidation_price=liquidation_price,
        liquidation_distance_percent=liquidation_distance_percent,
        minimum_leverage=minimum_leverage,
        margin_sufficient=margin <= account_size,
        margin_mode=margin_mode,
        maintenance_margin_percent=maintenance_margin_percent,
    )
