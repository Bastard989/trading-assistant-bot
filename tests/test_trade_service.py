from __future__ import annotations

from decimal import Decimal

import pytest

from trading_bot.db import Database
from trading_bot.domain.trades import TradeValidationError
from trading_bot.repositories import TradeRepository, UserRepository
from trading_bot.services.trades import TradeService


def make_service(tmp_path):
    db = Database(tmp_path / "service.sqlite3")
    UserRepository(db).ensure_user(42)
    repository = TradeRepository(db)
    return TradeService(repository), repository


def test_trade_service_normalizes_and_persists_consistent_risk(tmp_path) -> None:
    service, repository = make_service(tmp_path)

    trade_id = service.create(
        user_id=42,
        symbol="btc",
        side="LONG",
        entry_price="100.10",
        stop_price="99.90",
        target_price="101.00",
        quantity="3",
        leverage="2",
        timeframe="bad-timeframe",
    )

    row = repository.get(42, trade_id)
    assert row["symbol"] == "BTCUSDT"
    assert row["side"] == "long"
    assert row["timeframe"] == "5m"
    assert Decimal(str(row["risk_amount"])) == Decimal("0.6")


def test_trade_service_rejects_market_price_outliers(tmp_path) -> None:
    service, _ = make_service(tmp_path)

    with pytest.raises(TradeValidationError, match="away"):
        service.prepare(
            symbol="ETH",
            side="long",
            entry_price=120,
            stop_price=110,
            target_price=140,
            quantity=1,
            leverage=1,
            current_market_price=100,
        )


def test_trade_service_closes_with_decimal_pnl_and_non_negative_fees(tmp_path) -> None:
    service, repository = make_service(tmp_path)
    trade_id = service.create(
        user_id=42,
        symbol="SOL",
        side="short",
        entry_price="100",
        stop_price="110",
        target_price="80",
        quantity="2",
    )

    row = service.close(user_id=42, trade_id=trade_id, exit_price="90", fees="0.35")

    assert row is not None
    assert row["status"] == "closed"
    assert Decimal(str(row["pnl"])) == Decimal("19.65")
    assert service.close(user_id=42, trade_id=trade_id, exit_price="91") is None


def test_trade_service_rejects_negative_close_fees(tmp_path) -> None:
    service, _ = make_service(tmp_path)
    trade_id = service.create(
        user_id=42,
        symbol="BTC",
        side="long",
        entry_price=100,
        stop_price=90,
        target_price=120,
        quantity=1,
    )

    with pytest.raises(TradeValidationError, match="fees"):
        service.close(user_id=42, trade_id=trade_id, exit_price=101, fees=-1)
