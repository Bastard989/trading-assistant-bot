from __future__ import annotations

import re
from datetime import date
from string import Formatter
from typing import Any


DEFAULT_TEMPLATES: dict[str, str] = {
    "entry": (
        "План сделки\n"
        "Монета: {symbol}\n"
        "Направление: {side_upper}\n"
        "Текущая цена: {price}\n"
        "Цена входа: {entry}\n"
        "Стоп лосс: {stop} ({stop_distance}%)\n"
        "Тейк профит: {target} ({target_distance}%)\n"
        "Qty: {qty}\n"
        "Риск: {risk} USDT\n"
        "R/R: {rr}\n"
        "\n"
        "Почему открыл сделку:\n"
        "{reason}\n"
        "\n"
        "Что отменяет идею: {invalidation}"
    ),
    "journal": (
        "{date} | {symbol} {side_upper}\n"
        "Сетап: {setup}\n"
        "Контекст: {context}\n"
        "Вход/стоп/тейк: {entry} / {stop} / {target}\n"
        "Итог: {result}\n"
        "Ошибка/урок: {lesson}"
    ),
    "context": (
        "Контекст {symbol} {timeframe}\n"
        "Bias: {bias}\n"
        "Структура: {structure}\n"
        "Уровни: {levels}\n"
        "Инвалидация: {invalidation}\n"
        "Комментарий: {note}"
    ),
    "result": (
        "Итог сделки {symbol} {side_upper}\n"
        "Вход: {entry}\n"
        "Выход: {exit}\n"
        "PnL: {pnl} USDT\n"
        "Что сработало: {worked}\n"
        "Что улучшить: {improve}"
    ),
}


PLACEHOLDER_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")


class SafeValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(body: str, values: dict[str, Any]) -> str:
    normalized = {key: format_value(value) for key, value in values.items()}
    return body.format_map(SafeValues(normalized))


def placeholders(body: str) -> list[str]:
    return sorted(set(PLACEHOLDER_RE.findall(body)))


def parse_key_values(tokens: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    note_parts: list[str] = []
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            values[key.strip().lower()] = value.strip()
        else:
            note_parts.append(token)
    if note_parts:
        values.setdefault("note", " ".join(note_parts))
        values.setdefault("reason", " ".join(note_parts))
    return values


def base_values() -> dict[str, Any]:
    today = date.today().isoformat()
    return {
        "date": today,
        "symbol": "",
        "side": "",
        "side_upper": "",
        "price": "-",
        "entry": "-",
        "stop": "-",
        "target": "-",
        "qty": "-",
        "quantity": "-",
        "leverage": "-",
        "risk": "-",
        "rr": "-",
        "stop_distance": "-",
        "target_distance": "-",
        "setup": "",
        "tags": "",
        "reason": "",
        "note": "",
        "context": "",
        "timeframe": "",
        "bias": "",
        "structure": "",
        "levels": "",
        "invalidation": "",
        "result": "",
        "lesson": "",
        "exit": "-",
        "pnl": "-",
        "worked": "",
        "improve": "",
    }


def trade_values(row) -> dict[str, Any]:
    if row is None:
        return {}
    side = row["side"] or ""
    return {
        "symbol": row["symbol"],
        "side": side,
        "side_upper": side.upper(),
        "entry": row["entry_price"],
        "stop": row["stop_price"],
        "target": row["target_price"],
        "qty": row["quantity"],
        "quantity": row["quantity"],
        "leverage": row["leverage"],
        "risk": row["risk_amount"],
        "setup": row["setup"],
        "tags": row["tags"],
        "note": row["note"],
        "exit": row["exit_price"],
        "pnl": row["pnl"],
    }


def enrich_trade_math(values: dict[str, Any]) -> dict[str, Any]:
    entry = to_float(values.get("entry"))
    stop = to_float(values.get("stop"))
    target = to_float(values.get("target"))
    qty = to_float(values.get("qty") or values.get("quantity"))
    risk = to_float(values.get("risk"))
    side = str(values.get("side", "")).lower()
    direction = 1 if side == "long" else -1

    if entry and stop:
        values["stop_distance"] = (stop - entry) / entry * 100
        if risk is None and qty:
            values["risk"] = abs(entry - stop) * qty
    if entry and target:
        values["target_distance"] = (target - entry) / entry * 100
    if entry and stop and target:
        risk_per_unit = abs(entry - stop)
        reward_per_unit = (target - entry) * direction
        if risk_per_unit:
            values["rr"] = reward_per_unit / risk_per_unit
    if values.get("side"):
        values["side_upper"] = str(values["side"]).upper()
    return values


def format_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def to_float(value: Any) -> float | None:
    if value in {None, "", "-"}:
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def valid_template_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", name))
