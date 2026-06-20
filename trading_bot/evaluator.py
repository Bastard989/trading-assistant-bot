from __future__ import annotations

import sqlite3
from datetime import date

from trading_bot.models import Distance, Sentiment, TradeDraft, TradeReview, ReviewIssue


HIGHER_TIMEFRAMES = {"1D", "4H", "1H", "15M"}
LONG_BIAS = {"long"}
SHORT_BIAS = {"short"}


def percent_distance(current_price: float, base_price: float) -> float:
    if base_price <= 0:
        return 0
    return (current_price - base_price) / base_price * 100


def build_distances(current_price: float, levels: dict[str, float]) -> tuple[Distance, ...]:
    distances: list[Distance] = []
    for label, price in levels.items():
        if price is None or price <= 0:
            continue
        value = percent_distance(current_price, price)
        direction = "above" if value > 0 else "below" if value < 0 else "at"
        distances.append(Distance(label=label, price=price, distance_percent=value, direction=direction))
    return tuple(distances)


def review_trade(
    draft: TradeDraft,
    contexts: list[sqlite3.Row],
    watchlist_symbols: list[str],
    daily_plan: sqlite3.Row | None,
    account_size: float,
    open_risk_total: float,
    today_pnl: float,
    sentiment: Sentiment | None = None,
    current_price: float | None = None,
) -> TradeReview:
    issues: list[ReviewIssue] = []
    score = 74.0
    direction = 1 if draft.side == "long" else -1

    reward = None
    rr = None
    if draft.target_price is not None:
        reward = (draft.target_price - draft.entry_price) * draft.quantity * direction
        rr = reward / draft.risk_amount if draft.risk_amount > 0 else None

    if draft.risk_amount <= 0:
        issues.append(issue("high", "Не посчитан риск", "У сделки нет положительного риска до стопа.", 18))
    elif account_size > 0:
        risk_percent = draft.risk_amount / account_size * 100
        if risk_percent > 3:
            issues.append(issue("high", "Слишком большой риск", f"Риск сделки {risk_percent:.2f}% от депозита.", 18))
        elif risk_percent > 1.5:
            issues.append(issue("medium", "Риск выше спокойного", f"Риск сделки {risk_percent:.2f}% от депозита.", 8))

    if rr is None:
        issues.append(issue("medium", "Нет тейка", "Без цели нельзя оценить R/R и качество выхода.", 8))
    elif rr < 1:
        issues.append(issue("high", "Плохой R/R", f"R/R {rr:.2f}: потенциальная прибыль меньше риска.", 20))
    elif rr < 1.5:
        issues.append(issue("medium", "Слабый R/R", f"R/R {rr:.2f}: запас по прибыли небольшой.", 10))
    elif rr >= 2.5:
        score += 5

    if watchlist_symbols and draft.symbol not in watchlist_symbols:
        issues.append(issue("medium", "Монеты нет в watchlist", "Сделка вне списка монет на наблюдение.", 7))

    if daily_plan:
        allowed = split_symbols(daily_plan["allowed_symbols"])
        if allowed and draft.symbol not in allowed:
            issues.append(issue("high", "Монеты нет в плане дня", f"Сегодня в плане: {', '.join(allowed)}.", 14))

        max_risk_percent = float(daily_plan["max_daily_risk_percent"] or 0)
        if account_size > 0 and max_risk_percent > 0:
            used_risk = (open_risk_total + draft.risk_amount) / account_size * 100
            if used_risk > max_risk_percent:
                issues.append(
                    issue(
                        "high",
                        "Превышен дневной риск",
                        f"После входа открытый риск будет {used_risk:.2f}% при лимите {max_risk_percent:.2f}%.",
                        18,
                    )
                )

        max_loss = float(daily_plan["max_daily_loss"] or 0)
        if max_loss > 0 and today_pnl <= -max_loss:
            issues.append(issue("block", "Дневной стоп уже достигнут", f"PnL сегодня {today_pnl:.2f} USDT.", 35))

    higher_contexts = [row for row in contexts if row["timeframe"] in HIGHER_TIMEFRAMES]
    aligned = 0
    conflicts = 0
    nearby_levels = []
    for row in higher_contexts:
        bias = row["bias"]
        if bias == "neutral":
            continue
        if (draft.side == "long" and bias in LONG_BIAS) or (draft.side == "short" and bias in SHORT_BIAS):
            aligned += 1
        else:
            conflicts += 1
            issues.append(
                issue(
                    "high",
                    f"{row['timeframe']} против сделки",
                    f"Старший контекст {row['timeframe']} = {bias.upper()}, а сделка {draft.side.upper()}.",
                    13,
                )
            )

        for level in parse_levels(row["levels"]):
            distance = abs(percent_distance(draft.entry_price, level))
            if distance <= 0.5:
                nearby_levels.append((row["timeframe"], level, distance))

    if aligned:
        score += min(8, aligned * 3)
    if not contexts:
        issues.append(issue("medium", "Нет старшего контекста", "Перед входом добавь /context хотя бы 1D/4H/1H.", 9))

    for timeframe, level, distance in nearby_levels[:3]:
        title = "Цена рядом с уровнем"
        detail = f"{timeframe} уровень {level:g}, расстояние от входа {distance:.2f}%."
        issues.append(issue("medium", title, detail, 5))

    if sentiment and sentiment.long_percent is not None and sentiment.short_percent is not None:
        crowd_long = sentiment.long_percent - sentiment.short_percent
        crowd_short = sentiment.short_percent - sentiment.long_percent
        if draft.side == "long" and crowd_long > 25:
            issues.append(issue("medium", "Толпа сильно в лонге", f"Longs {sentiment.long_percent:.1f}%, риск crowded long.", 6))
        if draft.side == "short" and crowd_short > 25:
            issues.append(issue("medium", "Толпа сильно в шорте", f"Shorts {sentiment.short_percent:.1f}%, риск crowded short.", 6))

    if current_price:
        entry_distance = abs(percent_distance(current_price, draft.entry_price))
        if entry_distance > 1:
            issues.append(issue("medium", "Цена далеко от входа", f"Текущая цена отличается от входа на {entry_distance:.2f}%.", 5))

    for item in issues:
        score -= item.penalty

    score = max(5, min(95, score))
    if any(item.severity == "block" for item in issues):
        severity = "block"
    elif any(item.severity == "high" for item in issues) or score < 45:
        severity = "high"
    elif any(item.severity == "medium" for item in issues) or score < 65:
        severity = "medium"
    else:
        severity = "low"

    summary = build_summary(severity, score, conflicts, aligned, rr)
    levels = {
        "entry": draft.entry_price,
        "stop": draft.stop_price,
    }
    if draft.target_price is not None:
        levels["target"] = draft.target_price
    for row in contexts[:4]:
        for index, level in enumerate(parse_levels(row["levels"])[:3], start=1):
            levels[f"{row['timeframe']} level {index}"] = level
    distances = build_distances(current_price or draft.entry_price, levels)

    return TradeReview(
        score=score,
        severity=severity,
        summary=summary,
        issues=tuple(issues),
        distances=distances,
    )


def issue(severity: str, title: str, detail: str, penalty: float) -> ReviewIssue:
    return ReviewIssue(severity=severity, title=title, detail=detail, penalty=penalty)


def parse_levels(raw: str) -> list[float]:
    levels: list[float] = []
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        if not chunk:
            continue
        try:
            levels.append(float(chunk))
        except ValueError:
            continue
    return levels


def split_symbols(raw: str) -> list[str]:
    return [item.strip().upper() for item in raw.replace(" ", ",").split(",") if item.strip()]


def build_summary(severity: str, score: float, conflicts: int, aligned: int, rr: float | None) -> str:
    if severity == "block":
        prefix = "Стоп: сделка нарушает жесткое правило."
    elif severity == "high":
        prefix = "Сделка опасная, я бы тормознул вход."
    elif severity == "medium":
        prefix = "Сделка спорная, нужны подтверждения."
    else:
        prefix = "Сделка выглядит допустимо по текущим правилам."

    rr_text = "-" if rr is None else f"{rr:.2f}"
    return f"{prefix} Score {score:.0f}/100, aligned TF {aligned}, conflict TF {conflicts}, R/R {rr_text}."


def today() -> date:
    return date.today()
