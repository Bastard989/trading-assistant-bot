from trading_bot.db import Database
from trading_bot.repositories import TradeRepository, UserRepository
from trading_bot.services.monitor import evaluate_level_observation


def trade(side="long", stop=90, target=120):
    return {"entry_price": 100, "stop_price": stop, "target_price": target, "side": side}


def candle(low, high):
    return {"low": low, "high": high}


def test_long_and_short_intrabar_touch() -> None:
    assert evaluate_level_observation(trade(), [candle(89, 105)], 100).matched_level == "stop_loss"
    assert evaluate_level_observation(trade(), [candle(95, 121)], 100).matched_level == "take_profit"
    assert evaluate_level_observation(trade("short", 110, 80), [candle(79, 105)], 100).matched_level == "take_profit"
    assert evaluate_level_observation(trade("short", 110, 80), [candle(95, 111)], 100).matched_level == "stop_loss"


def test_both_levels_and_anomaly_require_manual_review() -> None:
    both = evaluate_level_observation(trade(), [candle(89, 121)], 100)
    assert both.matched_level == "ambiguous"
    assert both.ambiguity == "stop_and_take_in_same_window"
    anomaly = evaluate_level_observation(trade(), [], 130)
    assert anomaly.matched_level == "anomaly"


def test_observation_never_closes_trade_and_notification_is_retryable(tmp_path) -> None:
    db = Database(tmp_path / "monitor.sqlite3")
    UserRepository(db).ensure_user(42)
    repository = TradeRepository(db)
    trade_id = repository.create(42, "BTCUSDT", "long", 100, 90, 120, 1, 1)
    observation = evaluate_level_observation(trade(), [candle(89, 105)], 89)
    pending = repository.record_level_observation(42, trade_id, observation)
    assert pending is not None
    assert repository.get(42, trade_id)["status"] == "open"
    assert repository.pending_level_observation(42, trade_id)["id"] == pending["id"]
    assert repository.mark_level_observation_sent(42, pending["id"])
    assert repository.pending_level_observation(42, trade_id) is None
