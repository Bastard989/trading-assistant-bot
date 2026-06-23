from __future__ import annotations

import pytest

from trading_bot.services.photo_trade import (
    PhotoTradeCandidate,
    PhotoTradeExtractionUnavailable,
    candidate_to_open_note,
    merge_candidate_with_text,
    missing_fields,
    parse_photo_trade_response,
)
from trading_bot.telegram_handlers import parse_trade_caption


def test_parse_photo_trade_response_normalizes_and_infers_short() -> None:
    candidate = parse_photo_trade_response(
        """
        ```json
        {
          "symbol": "eth",
          "side": null,
          "entry_price": "1652,43",
          "stop_price": "1655.78",
          "target_price": "1590.34",
          "quantity": "0.01",
          "leverage": "10:1",
          "confidence": 0.82
        }
        ```
        """
    )

    assert candidate.symbol == "ETHUSDT"
    assert candidate.side == "short"
    assert candidate.entry_price == pytest.approx(1652.43)
    assert candidate.stop_price == pytest.approx(1655.78)
    assert candidate.target_price == pytest.approx(1590.34)
    assert candidate.quantity == pytest.approx(0.01)
    assert candidate.leverage == pytest.approx(10)
    assert candidate.confidence == pytest.approx(0.82)


def test_parse_photo_trade_response_rejects_non_json() -> None:
    with pytest.raises(PhotoTradeExtractionUnavailable):
        parse_photo_trade_response("я не уверен, но это ETH")


def test_missing_fields_requires_reason_before_open() -> None:
    candidate = PhotoTradeCandidate(
        symbol="ETHUSDT",
        side="short",
        entry_price=1652.43,
        stop_price=1655.78,
        target_price=1590.34,
        quantity=0.01,
        leverage=10,
    )

    assert missing_fields(candidate) == ("reason",)


def test_merge_candidate_with_plain_text_fills_reason_when_only_reason_missing() -> None:
    candidate = PhotoTradeCandidate(
        symbol="ETHUSDT",
        side="short",
        entry_price=1652.43,
        stop_price=1655.78,
        target_price=1590.34,
        quantity=0.01,
        leverage=10,
    )

    merged = merge_candidate_with_text(candidate, "ретест уровня и слабость после импульса")

    assert merged.reason == "ретест уровня и слабость после импульса"
    assert missing_fields(merged) == ()


def test_candidate_to_open_note_is_compatible_with_existing_parser() -> None:
    candidate = PhotoTradeCandidate(
        symbol="ETHUSDT",
        side="short",
        entry_price=1652.43,
        stop_price=1655.78,
        target_price=1590.34,
        quantity=0.01,
        leverage=10,
        reason="ретест уровня",
    )

    parsed = parse_trade_caption(candidate_to_open_note(candidate))

    assert parsed is not None
    assert parsed["symbol"] == "ETHUSDT"
    assert parsed["side"] == "short"
    assert parsed["entry"] == pytest.approx(1652.43)
    assert parsed["stop"] == pytest.approx(1655.78)
    assert parsed["target"] == pytest.approx(1590.34)
    assert parsed["quantity"] == pytest.approx(0.01)
    assert parsed["leverage"] == pytest.approx(10)
