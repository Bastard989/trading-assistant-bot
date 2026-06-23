from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Protocol

from trading_bot.domain.trades import TradeValidationError, calculate_net_pnl, decimal_value, validate_trade
from trading_bot.market import normalize_symbol
from trading_bot.models import TradeDraft


ALLOWED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}
DEFAULT_TIMEFRAME = "5m"
MAX_MARKET_DISTANCE_PERCENT = Decimal("15")


class TradeRepositoryProtocol(Protocol):
    def create(
        self,
        user_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        target_price: float | None,
        quantity: float,
        leverage: float,
        risk_amount: float = 0,
        setup: str = "",
        tags: tuple[str, ...] = (),
        review_score: float | None = None,
        ignored_warnings: bool = False,
        note: str = "",
        timeframe: str = DEFAULT_TIMEFRAME,
    ) -> int:
        ...

    def get(self, user_id: int, trade_id: int):
        ...

    def update(
        self,
        user_id: int,
        trade_id: int,
        entry_price: float,
        stop_price: float,
        target_price: float | None,
        quantity: float,
        timeframe: str,
        note: str = "",
    ):
        ...

    def close(
        self,
        user_id: int,
        trade_id: int,
        exit_price: float,
        fees: float = 0,
        note: str = "",
        close_reason: str = "manual",
        pnl: float | None = None,
    ):
        ...


@dataclass(frozen=True)
class PreparedTrade:
    symbol: str
    side: str
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal | None
    quantity: Decimal
    leverage: Decimal
    risk_amount: Decimal
    timeframe: str

    def to_draft(self, *, setup: str = "", tags: tuple[str, ...] = (), note: str = "") -> TradeDraft:
        return TradeDraft(
            symbol=self.symbol,
            side=self.side,
            entry_price=decimal_to_float(self.entry_price),
            stop_price=decimal_to_float(self.stop_price),
            target_price=decimal_to_float(self.target_price) if self.target_price is not None else None,
            quantity=decimal_to_float(self.quantity),
            leverage=decimal_to_float(self.leverage),
            risk_amount=decimal_to_float(self.risk_amount),
            setup=setup,
            tags=tags,
            note=note,
        )


class TradeService:
    def __init__(self, trades: TradeRepositoryProtocol) -> None:
        self.trades = trades

    def prepare(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: object,
        stop_price: object,
        target_price: object | None,
        quantity: object,
        leverage: object = 1,
        timeframe: str = DEFAULT_TIMEFRAME,
        current_market_price: object | None = None,
    ) -> PreparedTrade:
        normalized_symbol = normalize_symbol(symbol)
        validated = validate_trade(
            side=side,
            entry=entry_price,
            stop=stop_price,
            target=target_price,
            quantity=quantity,
            leverage=leverage,
        )
        if current_market_price is not None:
            market_price = decimal_value(current_market_price, "current_market_price", maximum=Decimal("1000000000"))
            market_distance = abs(validated.entry - market_price) / market_price * Decimal("100")
            if market_distance > MAX_MARKET_DISTANCE_PERCENT:
                raise TradeValidationError(
                    f"entry price is {format_decimal(market_distance)}% away from current {normalized_symbol} price"
                )

        return PreparedTrade(
            symbol=normalized_symbol,
            side=validated.side,
            entry_price=validated.entry,
            stop_price=validated.stop,
            target_price=validated.target,
            quantity=validated.quantity,
            leverage=validated.leverage,
            risk_amount=calculate_risk_amount(validated.entry, validated.stop, validated.quantity),
            timeframe=normalize_timeframe(timeframe),
        )

    def create(
        self,
        *,
        user_id: int,
        symbol: str,
        side: str,
        entry_price: object,
        stop_price: object,
        target_price: object | None,
        quantity: object,
        leverage: object = 1,
        timeframe: str = DEFAULT_TIMEFRAME,
        setup: str = "",
        tags: tuple[str, ...] = (),
        review_score: float | None = None,
        ignored_warnings: bool = False,
        note: str = "",
        current_market_price: object | None = None,
    ) -> int:
        prepared = self.prepare(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            quantity=quantity,
            leverage=leverage,
            timeframe=timeframe,
            current_market_price=current_market_price,
        )
        return self.trades.create(
            user_id=user_id,
            symbol=prepared.symbol,
            side=prepared.side,
            entry_price=decimal_to_float(prepared.entry_price),
            stop_price=decimal_to_float(prepared.stop_price),
            target_price=decimal_to_float(prepared.target_price) if prepared.target_price is not None else None,
            quantity=decimal_to_float(prepared.quantity),
            leverage=decimal_to_float(prepared.leverage),
            risk_amount=decimal_to_float(prepared.risk_amount),
            setup=setup,
            tags=tags,
            review_score=review_score,
            ignored_warnings=ignored_warnings,
            note=note,
            timeframe=prepared.timeframe,
        )

    def create_from_draft(
        self,
        user_id: int,
        draft: TradeDraft,
        *,
        review_score: float | None = None,
        ignored_warnings: bool = False,
        timeframe: str = DEFAULT_TIMEFRAME,
        current_market_price: object | None = None,
    ) -> int:
        return self.create(
            user_id=user_id,
            symbol=draft.symbol,
            side=draft.side,
            entry_price=draft.entry_price,
            stop_price=draft.stop_price,
            target_price=draft.target_price,
            quantity=draft.quantity,
            leverage=draft.leverage,
            timeframe=timeframe,
            setup=draft.setup,
            tags=draft.tags,
            review_score=review_score,
            ignored_warnings=ignored_warnings,
            note=draft.note,
            current_market_price=current_market_price,
        )

    def update(
        self,
        *,
        user_id: int,
        trade_id: int,
        entry_price: object,
        stop_price: object,
        target_price: object | None,
        quantity: object,
        timeframe: str = DEFAULT_TIMEFRAME,
        note: str = "",
    ):
        existing = self.trades.get(user_id, trade_id)
        if not existing or existing["status"] != "open":
            return None
        prepared = self.prepare(
            symbol=str(existing["symbol"]),
            side=str(existing["side"]),
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            quantity=quantity,
            leverage=existing["leverage"],
            timeframe=timeframe,
        )
        return self.trades.update(
            user_id,
            trade_id,
            decimal_to_float(prepared.entry_price),
            decimal_to_float(prepared.stop_price),
            decimal_to_float(prepared.target_price) if prepared.target_price is not None else None,
            decimal_to_float(prepared.quantity),
            prepared.timeframe,
            note,
        )

    def close(
        self,
        *,
        user_id: int,
        trade_id: int,
        exit_price: object,
        fees: object = 0,
        note: str = "",
        close_reason: str = "manual",
    ):
        trade = self.trades.get(user_id, trade_id)
        if not trade or trade["status"] != "open":
            return None
        exit_value = decimal_value(exit_price, "exit_price", maximum=Decimal("1000000000"))
        fees_value = non_negative_decimal(fees, "fees", maximum=Decimal("1000000000"))
        pnl = calculate_net_pnl(
            side=str(trade["side"]),
            entry=Decimal(str(trade["entry_price"])),
            exit_price=exit_value,
            quantity=Decimal(str(trade["quantity"])),
            commission=fees_value,
        )
        return self.trades.close(
            user_id,
            trade_id,
            decimal_to_float(exit_value),
            decimal_to_float(fees_value),
            note,
            close_reason=close_reason,
            pnl=decimal_to_float(pnl),
        )


def calculate_risk_amount(entry: Decimal, stop: Decimal, quantity: Decimal) -> Decimal:
    return (abs(entry - stop) * quantity).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)


def decimal_to_float(value: Decimal) -> float:
    return float(value)


def non_negative_decimal(value: object, field: str, *, maximum: Decimal) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradeValidationError(f"{field} must be a number") from exc
    if not result.is_finite() or result < 0 or result > maximum:
        raise TradeValidationError(f"{field} must be finite, non-negative and within bounds")
    return result


def normalize_timeframe(value: str, *, default: str = DEFAULT_TIMEFRAME) -> str:
    fallback = default if default in ALLOWED_TIMEFRAMES else DEFAULT_TIMEFRAME
    return value if value in ALLOWED_TIMEFRAMES else fallback


def format_decimal(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)}"
