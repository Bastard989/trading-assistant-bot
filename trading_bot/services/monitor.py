from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LevelObservation:
    matched_level: str
    observed_price: float
    level_price: float | None
    candle_high: float | None
    candle_low: float | None
    ambiguity: str = ""


def evaluate_level_observation(trade, candles: list[dict[str, float]], mark_price: float) -> LevelObservation | None:
    entry = float(trade["entry_price"])
    if entry <= 0 or abs(mark_price - entry) / entry * 100 > 25:
        return LevelObservation("anomaly", mark_price, None, None, None, "price_over_25_percent_from_entry")
    highs = [float(item["high"]) for item in candles]
    lows = [float(item["low"]) for item in candles]
    high = max(highs) if highs else mark_price
    low = min(lows) if lows else mark_price
    stop = float(trade["stop_price"])
    target = float(trade["target_price"]) if trade["target_price"] is not None else None
    if trade["side"] == "long":
        stop_touched = low <= stop
        target_touched = target is not None and high >= target
    else:
        stop_touched = high >= stop
        target_touched = target is not None and low <= target
    if stop_touched and target_touched:
        return LevelObservation("ambiguous", mark_price, None, high, low, "stop_and_take_in_same_window")
    if stop_touched:
        return LevelObservation("stop_loss", mark_price, stop, high, low)
    if target_touched:
        return LevelObservation("take_profit", mark_price, target, high, low)
    return None
