from __future__ import annotations

import sqlite3

from trading_bot.models import Distance, MarketTicker, RiskCalculation, Sentiment, TradeReview


def money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def signed_money(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{money(value)}"


def format_risk(calc: RiskCalculation) -> str:
    target_line = "Target: -" if calc.target_price is None else f"Target: {money(calc.target_price)}"
    profit_line = "Potential profit: -" if calc.profit_at_target is None else f"Potential profit: {money(calc.profit_at_target)} USDT"
    rr_line = "R/R: -" if calc.reward_to_risk is None else f"R/R: {calc.reward_to_risk:.2f}"
    return (
        f"{calc.symbol} {calc.side.upper()}\n"
        f"Entry: {money(calc.entry_price)}\n"
        f"Stop: {money(calc.stop_price)}\n"
        f"{target_line}\n\n"
        f"Account: {money(calc.account_size)} USDT\n"
        f"Risk: {calc.risk_percent:.2f}% = {money(calc.risk_amount)} USDT\n"
        f"Quantity: {money(calc.quantity)} {calc.symbol.replace('USDT', '')}\n"
        f"Position notional: {money(calc.notional)} USDT\n"
        f"Margin at x{money(calc.leverage)}: {money(calc.margin)} USDT\n"
        f"Loss at stop: -{money(calc.loss_at_stop)} USDT\n"
        f"{profit_line}\n"
        f"{rr_line}"
    )


def format_ticker(ticker: MarketTicker, index: int) -> str:
    return (
        f"{index}. {ticker.symbol}: {money(ticker.price)} | "
        f"24h vol {money(ticker.quote_volume)} USDT | "
        f"change {ticker.price_change_percent:.2f}% | "
        f"range {ticker.intraday_range_percent:.2f}%"
    )


def format_sentiment(sentiment: Sentiment) -> str:
    if sentiment.long_percent is None or sentiment.short_percent is None:
        return f"{sentiment.symbol}: sentiment unavailable. {sentiment.source}"

    bias = "bullish" if sentiment.long_percent > sentiment.short_percent else "bearish"
    return (
        f"{sentiment.symbol} market mood: {bias}\n"
        f"Longs: {sentiment.long_percent:.2f}%\n"
        f"Shorts: {sentiment.short_percent:.2f}%\n"
        f"Long/short ratio: {sentiment.long_short_ratio:.4f}\n"
        f"Source: {sentiment.source}"
    )


def format_trade(row: sqlite3.Row) -> str:
    target = "-" if row["target_price"] is None else money(row["target_price"])
    pnl = "" if row["pnl"] is None else f" | PnL {signed_money(row['pnl'])} USDT"
    return (
        f"#{row['id']} {row['symbol']} {row['side'].upper()} {row['status']}\n"
        f"entry {money(row['entry_price'])} | stop {money(row['stop_price'])} | target {target}\n"
        f"qty {money(row['quantity'])} | risk {money(row['risk_amount'])} | x{money(row['leverage'])}{pnl}"
    )


def format_distance(distance: Distance) -> str:
    sign = "+" if distance.distance_percent > 0 else ""
    return f"{distance.label}: {money(distance.price)} ({sign}{distance.distance_percent:.2f}%, {distance.direction})"


def format_review(review: TradeReview) -> str:
    lines = [
        "Trade Review",
        review.summary,
        f"Likely success: {review.win_probability:.0f}%",
        f"Likely failure: {review.loss_probability:.0f}%",
        f"Severity: {review.severity.upper()}",
    ]
    if review.issues:
        lines.append("\nПочему я торможу:")
        for issue in review.issues[:8]:
            lines.append(f"- {issue.severity.upper()}: {issue.title}. {issue.detail}")
    if review.distances:
        lines.append("\nDistance:")
        for distance in review.distances[:8]:
            lines.append(f"- {format_distance(distance)}")
    return "\n".join(lines)
