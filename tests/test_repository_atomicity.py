from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from trading_bot.db import Database
from trading_bot.repositories import TradeRepository, UserRepository


def test_one_of_100_concurrent_closes_wins(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    UserRepository(db).ensure_user(42)
    repository = TradeRepository(db)
    trade_id = repository.create(
        42, "BTCUSDT", "long", 100, 90, 120, 2, 1, risk_amount=20
    )

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda price: repository.close(42, trade_id, price), range(101, 201)))

    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    stored = repository.get(42, trade_id)
    assert stored is not None
    assert stored["status"] == "closed"
    assert stored["exit_price"] == winners[0]["exit_price"]


def test_close_rejects_non_positive_and_non_finite_prices(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    UserRepository(db).ensure_user(42)
    repository = TradeRepository(db)
    trade_id = repository.create(42, "BTCUSDT", "short", 100, 110, 80, 1, 1)
    for value in (0, -1, float("nan"), float("inf")):
        assert repository.close(42, trade_id, value) is None
    assert repository.get(42, trade_id)["status"] == "open"


def test_foreign_keys_and_required_indexes_are_enabled(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    with db.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )}
    assert "idx_trades_user_status_opened" in indexes
    assert "idx_attachments_user_trade" in indexes


def test_terminal_trade_cannot_be_updated(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    UserRepository(db).ensure_user(42)
    repository = TradeRepository(db)
    trade_id = repository.create(42, "BTCUSDT", "long", 100, 90, 120, 1, 1)
    assert repository.close(42, trade_id, 110) is not None
    assert repository.update(42, trade_id, 101, 91, 121, 2, "5m") is None
    assert repository.set_leverage(42, trade_id, 20) is None
