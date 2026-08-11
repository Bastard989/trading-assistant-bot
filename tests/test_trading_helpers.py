from __future__ import annotations

import sqlite3

import pytest

from trading_bot.evaluator import (
    build_distances,
    build_summary,
    parse_levels,
    percent_distance,
    review_trade,
    split_symbols,
)
from trading_bot.formatting import (
    format_close_reason,
    format_distance,
    format_review,
    format_risk,
    format_sentiment,
    format_ticker,
    format_trade,
    money,
    signed_money,
)
from trading_bot.models import Distance, MarketTicker, ReviewIssue, Sentiment, TradeDraft, TradeReview
from trading_bot.risk import RiskInputError, calculate_risk
from trading_bot.templates import (
    SafeValues,
    base_values,
    enrich_trade_math,
    format_value,
    parse_key_values,
    placeholders,
    render_template,
    to_float,
    trade_values,
    valid_template_name,
)
from trading_bot.timeframe_analyzer import analyze_klines


def sqlite_row(**values):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    expressions = ", ".join(f"? AS '{key}'" for key in values)
    row = connection.execute(f"SELECT {expressions}", tuple(values.values())).fetchone()
    connection.close()
    return row


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"side": "flat"}, "Side"),
        ({"entry_price": 0}, "positive"),
        ({"stop_price": 0}, "positive"),
        ({"account_size": 0}, "Account"),
        ({"risk_percent": 0}, "Risk percent"),
        ({"leverage": 0}, "Leverage"),
        ({"entry_fee_percent": -1}, "cannot be negative"),
        ({"holding_hours": -1}, "Holding time"),
        ({"funding_interval_hours": 0}, "Holding time"),
        ({"margin_mode": "portfolio"}, "Margin mode"),
        ({"target_price": 0}, "Target price"),
        ({"stop_price": 101}, "stop must be below"),
        ({"side": "short", "stop_price": 99, "target_price": 90}, "stop must be above"),
        ({"target_price": 99}, "target must be above"),
        ({"side": "short", "stop_price": 110, "target_price": 101}, "target must be below"),
    ),
)
def test_risk_validation_fails_closed(kwargs, message) -> None:
    values = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100,
        "stop_price": 90,
        "account_size": 1_000,
        "risk_percent": 1,
        "target_price": 120,
    }
    values.update(kwargs)
    with pytest.raises(RiskInputError, match=message):
        calculate_risk(**values)


def test_risk_calculates_long_short_cross_fees_funding_and_liquidation() -> None:
    long = calculate_risk(
        "btcusdt",
        "long",
        100,
        90,
        1_000,
        1,
        125,
        leverage=5,
        holding_hours=16,
        funding_rate_percent=0.01,
    )
    short = calculate_risk(
        "ETHUSDT", "short", 100, 110, 1_000, 1, 75, leverage=4
    )
    cross = calculate_risk(
        "SOLUSDT", "long", 100, 90, 1_000, 1, margin_mode="cross"
    )
    extreme_maintenance = calculate_risk(
        "XRPUSDT",
        "long",
        100,
        90,
        1_000,
        1,
        leverage=2,
        maintenance_margin_percent=100,
    )

    assert long.symbol == "BTCUSDT"
    assert long.profit_at_target > 0
    assert long.reward_to_risk > 2
    assert long.funding_payment > 0
    assert long.liquidation_price is not None
    assert short.profit_at_target > 0
    assert short.funding_payment == 0
    assert short.liquidation_price > short.entry_price
    assert cross.liquidation_price is None
    assert cross.liquidation_distance_percent is None
    assert extreme_maintenance.liquidation_price == 0


def test_formatting_covers_optional_and_structured_outputs() -> None:
    calculation = calculate_risk("BTCUSDT", "long", 100, 90, 1_000, 1, 120)
    no_target = calculate_risk("BTCUSDT", "long", 100, 90, 1_000, 1)
    ticker = MarketTicker("BTCUSDT", 100, 5_000_000, 2.5, 105, 95)
    unavailable = Sentiment("BTCUSDT", None, None, None, "Bybit")
    sentiment = Sentiment("BTCUSDT", 60, 40, 1.5, "Bybit")
    distance = Distance("entry", 100, 1.25, "above")
    review = TradeReview(
        score=55,
        severity="medium",
        summary="Needs confirmation",
        issues=(ReviewIssue("medium", "Issue", "Detail", 5),),
        distances=(distance,),
    )
    trade = sqlite_row(
        id=1,
        symbol="BTCUSDT",
        side="long",
        status="closed",
        close_reason="take_profit",
        entry_price=100,
        stop_price=90,
        target_price=120,
        quantity=1,
        risk_amount=10,
        leverage=2,
        pnl=20,
    )

    assert "Potential profit" in format_risk(calculation)
    assert "Target: -" in format_risk(no_target)
    assert "24h vol" in format_ticker(ticker, 1)
    assert "unavailable" in format_sentiment(unavailable)
    assert "bullish" in format_sentiment(sentiment)
    assert "закрыто по тейку" in format_trade(trade)
    assert format_close_reason("custom_reason") == "custom reason"
    assert "+1.25%" in format_distance(distance)
    assert "Почему я торможу" in format_review(review)
    assert "Distance" in format_review(review)
    assert signed_money(3.5) == "+3,5"
    assert signed_money(None) == "-"
    assert money(None) == "-"
    assert money("not-a-number") == "-"  # type: ignore[arg-type]


def test_template_helpers_preserve_unknowns_and_enrich_trade_math(monkeypatch) -> None:
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Europe/Moscow")
    values = parse_key_values(["symbol=BTCUSDT", "side=long", "breakout", "confirmed"])
    enriched = enrich_trade_math(
        {
            **values,
            "entry": "100",
            "stop": "95",
            "target": "115",
            "qty": "2",
            "risk": None,
        }
    )
    rendered = render_template(
        "{symbol} {side_upper} risk={risk} rr={rr} {unknown}", enriched
    )

    assert values["note"] == "breakout confirmed"
    assert enriched["risk"] == 10
    assert enriched["rr"] == 3
    assert "BTCUSDT LONG" in rendered
    assert "{unknown}" in rendered
    assert SafeValues()["missing"] == "{missing}"
    assert placeholders("{b} {a} {b}") == ["a", "b"]
    assert base_values()["date"]
    assert format_value(None) == "-"
    assert format_value(1.23456) == "1.2346"
    assert to_float("1,25") == 1.25
    assert to_float("bad") is None
    assert to_float("-") is None
    assert valid_template_name("entry-v2") is True
    assert valid_template_name("../../secret") is False


def test_trade_values_handles_absent_and_complete_row() -> None:
    assert trade_values(None) == {}
    row = sqlite_row(
        symbol="SOLUSDT",
        side="short",
        entry_price=100,
        stop_price=105,
        target_price=90,
        quantity=2,
        leverage=3,
        risk_amount=10,
        setup="breakdown",
        tags="trend",
        note="confirmed",
        exit_price=92,
        pnl=16,
    )
    values = trade_values(row)
    assert values["side_upper"] == "SHORT"
    assert values["exit"] == 92


def klines(closes: list[float], *, recent_low: float | None = None,
           recent_high: float | None = None) -> list[dict[str, float]]:
    rows = [{"close": close, "high": close + 1, "low": close - 1} for close in closes]
    if recent_low is not None:
        rows[-5]["low"] = recent_low
    if recent_high is not None:
        rows[-5]["high"] = recent_high
    return rows


def test_timeframe_analyzer_covers_all_market_structures() -> None:
    insufficient = analyze_klines("BTCUSDT", "1H", klines([100] * 20))
    uptrend = analyze_klines("BTCUSDT", "1H", klines(list(range(1, 51))))
    downtrend = analyze_klines("BTCUSDT", "1H", klines(list(range(50, 0, -1))))
    range_state = analyze_klines("BTCUSDT", "1H", klines([100] * 50))
    recovery = analyze_klines(
        "BTCUSDT", "1H", klines([100] * 49 + [101], recent_low=90)
    )
    pressure = analyze_klines(
        "BTCUSDT", "1H", klines([100] * 49 + [99], recent_high=110)
    )

    assert insufficient["structure"] == "not_enough_data"
    assert uptrend["structure"] == "uptrend"
    assert downtrend["structure"] == "downtrend"
    assert range_state["structure"] == "range_near_sma20"
    assert recovery["structure"] == "recovery_above_sma20"
    assert pressure["structure"] == "pressure_below_sma20"
    assert len(uptrend["levels"]) == 2
    assert "sma20" in uptrend["note"]


def draft(**overrides) -> TradeDraft:
    values = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100,
        "stop_price": 95,
        "target_price": 115,
        "quantity": 2,
        "leverage": 2,
        "risk_amount": 10,
    }
    values.update(overrides)
    return TradeDraft(**values)


def test_trade_review_covers_good_medium_and_blocked_paths() -> None:
    aligned_context = sqlite_row(timeframe="4H", bias="long", levels="80; bad")
    conflict_context = sqlite_row(timeframe="1D", bias="short", levels="100.1, 120")
    neutral_context = sqlite_row(timeframe="15M", bias="neutral", levels="")
    daily_plan = sqlite_row(
        allowed_symbols="ETHUSDT, SOLUSDT",
        max_daily_risk_percent=1,
        max_daily_loss=25,
    )

    good = review_trade(
        draft(),
        [aligned_context],
        ["BTCUSDT"],
        None,
        account_size=1_000,
        open_risk_total=0,
        today_pnl=0,
        current_price=100,
    )
    medium = review_trade(
        draft(target_price=112, risk_amount=20),
        [neutral_context],
        ["BTCUSDT"],
        None,
        account_size=1_000,
        open_risk_total=0,
        today_pnl=0,
    )
    blocked = review_trade(
        draft(target_price=None, risk_amount=0),
        [conflict_context],
        ["ETHUSDT"],
        daily_plan,
        account_size=1_000,
        open_risk_total=20,
        today_pnl=-30,
        sentiment=Sentiment("BTCUSDT", 80, 20, 4, "Bybit"),
        current_price=105,
    )

    assert good.severity == "low"
    assert good.score > 70
    assert medium.severity == "medium"
    assert blocked.severity == "block"
    titles = {issue.title for issue in blocked.issues}
    assert "Дневной стоп уже достигнут" in titles
    assert "Монеты нет в плане дня" in titles
    assert "Цена далеко от входа" in titles
    assert percent_distance(105, 100) == 5
    assert percent_distance(100, 0) == 0
    assert parse_levels("1; 2 bad,3") == [1.0, 2.0, 3.0]
    assert split_symbols("btc, eth sol") == ["BTC", "ETH", "SOL"]
    assert [item.direction for item in build_distances(100, {"up": 90, "down": 110, "at": 100})] == [
        "above",
        "below",
        "at",
    ]
    assert "жесткое правило" in build_summary("block", 5, 1, 0, None)
    assert "опасная" in build_summary("high", 30, 1, 0, 0.5)
    assert "спорная" in build_summary("medium", 55, 0, 1, 1.2)
    assert "допустимо" in build_summary("low", 80, 0, 2, 3)
