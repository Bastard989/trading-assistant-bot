from __future__ import annotations


def analyze_klines(symbol: str, timeframe: str, klines: list[dict[str, float]]) -> dict[str, object]:
    if len(klines) < 30:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bias": "neutral",
            "structure": "not_enough_data",
            "levels": (),
            "confidence": 40,
            "note": "Недостаточно свечей для автоанализа.",
        }

    closes = [row["close"] for row in klines]
    highs = [row["high"] for row in klines]
    lows = [row["low"] for row in klines]
    close = closes[-1]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    previous_high = max(highs[-40:-20])
    previous_low = min(lows[-40:-20])
    change = (close - closes[-10]) / closes[-10] * 100 if closes[-10] else 0

    if close > sma20 > sma50 and recent_low >= previous_low:
        bias = "long"
        structure = "uptrend"
        confidence = 76
    elif close < sma20 < sma50 and recent_high <= previous_high:
        bias = "short"
        structure = "downtrend"
        confidence = 76
    elif abs(close - sma20) / close * 100 < 0.6:
        bias = "neutral"
        structure = "range_near_sma20"
        confidence = 58
    elif close > sma20:
        bias = "long"
        structure = "recovery_above_sma20"
        confidence = 64
    else:
        bias = "short"
        structure = "pressure_below_sma20"
        confidence = 64

    levels = (round(recent_low, 6), round(recent_high, 6))
    note = (
        f"auto {timeframe}: close {close:g}, sma20 {sma20:g}, sma50 {sma50:g}, "
        f"10-candle change {change:.2f}%, support {recent_low:g}, resistance {recent_high:g}"
    )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": bias,
        "structure": structure,
        "levels": levels,
        "confidence": confidence,
        "note": note,
    }
