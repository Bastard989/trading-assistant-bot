from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskCalculation:
    symbol: str
    side: str
    entry_price: float
    stop_price: float
    target_price: float | None
    account_size: float
    risk_percent: float
    leverage: float
    risk_amount: float
    quantity: float
    notional: float
    margin: float
    loss_at_stop: float
    profit_at_target: float | None
    reward_to_risk: float | None
    gross_loss_at_stop: float
    gross_profit_at_target: float | None
    entry_fee: float
    stop_exit_fee: float
    target_exit_fee: float | None
    stop_slippage: float
    target_slippage: float | None
    funding_payment: float
    net_loss_at_stop: float
    net_profit_at_target: float | None
    liquidation_price: float | None
    liquidation_distance_percent: float | None
    minimum_leverage: float
    margin_sufficient: bool
    margin_mode: str
    maintenance_margin_percent: float


@dataclass(frozen=True)
class MarketTicker:
    symbol: str
    price: float
    quote_volume: float
    price_change_percent: float
    high_price: float
    low_price: float

    @property
    def intraday_range_percent(self) -> float:
        if self.low_price <= 0:
            return 0
        return (self.high_price - self.low_price) / self.low_price * 100

    @property
    def activity_score(self) -> float:
        return self.quote_volume * (abs(self.price_change_percent) + self.intraday_range_percent + 1)


@dataclass(frozen=True)
class Sentiment:
    symbol: str
    long_percent: float | None
    short_percent: float | None
    long_short_ratio: float | None
    source: str


@dataclass(frozen=True)
class TradeDraft:
    symbol: str
    side: str
    entry_price: float
    stop_price: float
    target_price: float | None
    quantity: float
    leverage: float
    risk_amount: float
    setup: str = ""
    tags: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Distance:
    label: str
    price: float
    distance_percent: float
    direction: str


@dataclass(frozen=True)
class ReviewIssue:
    severity: str
    title: str
    detail: str
    penalty: float


@dataclass(frozen=True)
class TradeReview:
    score: float
    severity: str
    summary: str
    issues: tuple[ReviewIssue, ...]
    distances: tuple[Distance, ...]
