from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from trading_bot.market import normalize_symbol


class PhotoTradeExtractionUnavailable(RuntimeError):
    """Raised when screenshot recognition is not configured or unavailable."""


@dataclass(frozen=True)
class PhotoTradeCandidate:
    symbol: str = ""
    side: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    quantity: float | None = None
    leverage: float | None = None
    reason: str = ""
    confidence: float = 0.0
    questions: tuple[str, ...] = ()
    source: str = "photo"


class PhotoTradeExtractor(Protocol):
    async def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> PhotoTradeCandidate:
        """Extract a trade candidate from a TradingView/order-panel screenshot."""


class DisabledPhotoTradeExtractor:
    async def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> PhotoTradeCandidate:
        raise PhotoTradeExtractionUnavailable("OPENAI_API_KEY is not configured")


class OpenAIPhotoTradeExtractor:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.5",
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 45,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-5.5"
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> PhotoTradeCandidate:
        if not self.api_key:
            raise PhotoTradeExtractionUnavailable("OPENAI_API_KEY is not configured")
        if not image_bytes:
            raise PhotoTradeExtractionUnavailable("empty image")

        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Ты извлекаешь черновик крипто-сделки из скриншота TradingView или панели заявки. "
                                "Верни только JSON без markdown. Не додумывай: если поле не видно, ставь null. "
                                "Поля: symbol (например ETHUSDT), side (long/short/null), entry_price, "
                                "stop_price, target_price, quantity, leverage, reason, confidence от 0 до 1, "
                                "questions массив коротких вопросов для недостающих данных. "
                                "Подсказки: красный/верхний стоп выше входа и тейк ниже входа обычно short; "
                                "стоп ниже входа и тейк выше входа обычно long. "
                                "Количество — размер позиции/количество монет, плечо — значение вроде 10:1 или 10x."
                            ),
                        },
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/responses", headers=headers, json=payload)
            response.raise_for_status()
        return parse_photo_trade_response(extract_response_text(response.json()))


def extract_response_text(payload: dict[str, Any]) -> str:
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return text

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            value = content.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def parse_photo_trade_response(text: str) -> PhotoTradeCandidate:
    raw = strip_json_fence(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PhotoTradeExtractionUnavailable("vision response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PhotoTradeExtractionUnavailable("vision response was not a JSON object")
    return candidate_from_mapping(payload)


def candidate_from_mapping(payload: dict[str, Any]) -> PhotoTradeCandidate:
    symbol = as_text(first_present(payload, "symbol", "ticker", "pair", "coin")).upper()
    side = normalize_side(first_present(payload, "side", "direction"))
    entry = as_float(first_present(payload, "entry_price", "entry", "entryPrice", "price"))
    stop = as_float(first_present(payload, "stop_price", "stop_loss", "stopLoss", "sl"))
    target = as_float(first_present(payload, "target_price", "take_profit", "takeProfit", "tp", "target"))
    quantity = as_float(first_present(payload, "quantity", "qty", "amount", "position_size", "positionSize"))
    leverage = as_float(first_present(payload, "leverage", "lev"))
    if not side:
        side = infer_side(entry, stop, target)

    questions = first_present(payload, "questions", "missing_questions")
    if isinstance(questions, list):
        clean_questions = tuple(str(item).strip() for item in questions if str(item).strip())
    elif isinstance(questions, str) and questions.strip():
        clean_questions = (questions.strip(),)
    else:
        clean_questions = ()

    return PhotoTradeCandidate(
        symbol=normalize_symbol(symbol) if symbol else "",
        side=side,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        quantity=quantity,
        leverage=leverage,
        reason=as_text(first_present(payload, "reason", "description", "note")),
        confidence=max(0.0, min(1.0, as_float(payload.get("confidence")) or 0.0)),
        questions=clean_questions,
    )


def merge_candidate_with_text(candidate: PhotoTradeCandidate, text: str) -> PhotoTradeCandidate:
    value = text.strip()
    if not value:
        return candidate

    lower = value.lower().replace("ё", "е")
    updates: dict[str, Any] = {}
    symbol = extract_labeled_text(value, ("монета", "symbol", "тикер"))
    if symbol:
        updates["symbol"] = normalize_symbol(symbol.split()[0])
    side_match = re.search(r"\b(лонг|long|шорт|short|buy|sell|купить|продать)\b", lower)
    if side_match:
        updates["side"] = normalize_side(side_match.group(1))

    label_map = {
        "entry_price": ("цена входа", "вход", "entry"),
        "stop_price": ("стоп лосс", "стоплосс", "стоп", "stop", "sl"),
        "target_price": ("тейк профит", "тейкпрофит", "тейк", "target", "take", "tp"),
        "quantity": ("количество позиций", "количество", "объем", "обьем", "qty"),
        "leverage": ("кредитное плечо", "плечо", "leverage"),
    }
    for field, labels in label_map.items():
        number = extract_number_after(lower, labels)
        if number is not None:
            updates[field] = number

    reason = extract_labeled_text(value, ("причина входа", "причина", "описание", "reason", "note"))
    if reason:
        updates["reason"] = reason
    elif missing_fields(candidate) == ("reason",):
        updates["reason"] = value

    merged = replace(candidate, **updates)
    if not merged.side:
        merged = replace(merged, side=infer_side(merged.entry_price, merged.stop_price, merged.target_price))
    return merged


def missing_fields(candidate: PhotoTradeCandidate) -> tuple[str, ...]:
    missing: list[str] = []
    if not candidate.symbol:
        missing.append("symbol")
    if candidate.side not in {"long", "short"}:
        missing.append("side")
    if candidate.entry_price is None:
        missing.append("entry_price")
    if candidate.stop_price is None:
        missing.append("stop_price")
    if candidate.target_price is None:
        missing.append("target_price")
    if candidate.quantity is None:
        missing.append("quantity")
    if candidate.leverage is None:
        missing.append("leverage")
    if not candidate.reason.strip():
        missing.append("reason")
    return tuple(missing)


def candidate_to_open_note(candidate: PhotoTradeCandidate) -> str:
    side = {"long": "лонг", "short": "шорт"}.get(candidate.side, candidate.side)
    return "\n".join(
        [
            "/open",
            f"Монета: {candidate.symbol}",
            f"Сторона: {side}",
            f"Цена входа: {format_number(candidate.entry_price)}",
            f"Стоп: {format_number(candidate.stop_price)}",
            f"Тейк: {format_number(candidate.target_price)}",
            f"Количество позиций: {format_number(candidate.quantity)}",
            f"Плечо: {format_number(candidate.leverage or 1)}",
            f"Причина входа: {candidate.reason.strip()}",
        ]
    )


def format_candidate_summary(candidate: PhotoTradeCandidate) -> str:
    side = {"long": "лонг", "short": "шорт"}.get(candidate.side, "-")
    return (
        f"{candidate.symbol or '-'} {side} | вход {format_number(candidate.entry_price) or '-'} | "
        f"стоп {format_number(candidate.stop_price) or '-'} | тейк {format_number(candidate.target_price) or '-'} | "
        f"qty {format_number(candidate.quantity) or '-'} | плечо {format_number(candidate.leverage) or '-'}"
    )


def field_prompt(field: str) -> str:
    labels = {
        "symbol": "монету/тикер",
        "side": "сторону: лонг или шорт",
        "entry_price": "цену входа",
        "stop_price": "стоп-лосс",
        "target_price": "тейк-профит",
        "quantity": "количество позиции",
        "leverage": "плечо",
        "reason": "причину входа/описание сетапа",
    }
    return labels.get(field, field)


def first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def strip_json_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    return match.group(0) if match else value


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    match = re.search(r"-?\d+(?:[,.]\d+)?", str(value).replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def normalize_side(value: Any) -> str:
    text = as_text(value).lower()
    if text in {"long", "лонг", "buy", "купить"}:
        return "long"
    if text in {"short", "шорт", "sell", "продать"}:
        return "short"
    return ""


def infer_side(entry: float | None, stop: float | None, target: float | None) -> str:
    if entry is None or stop is None or target is None:
        return ""
    if stop < entry < target:
        return "long"
    if target < entry < stop:
        return "short"
    return ""


def extract_number_after(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[^\d-]{{0,24}}(-?\d+(?:[,.]\d+)?)", text)
        if match:
            return as_float(match.group(1))
    return None


def extract_labeled_text(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+)$", text)
        if match:
            return match.group(1).strip()
    return ""


def format_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"
