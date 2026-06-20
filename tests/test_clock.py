from datetime import date, datetime, timezone

import pytest

from trading_bot.db import Database
from trading_bot.repositories import TradeRepository, UserRepository
from trading_bot.services.clock import business_date, utc_day_bounds


def test_moscow_business_date_crosses_before_utc_midnight() -> None:
    assert business_date(
        "Europe/Moscow", now=datetime(2026, 6, 20, 20, 59, tzinfo=timezone.utc)
    ) == date(2026, 6, 20)
    assert business_date(
        "Europe/Moscow", now=datetime(2026, 6, 20, 21, 0, tzinfo=timezone.utc)
    ) == date(2026, 6, 21)


def test_naive_business_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        business_date("Europe/Moscow", now=datetime(2026, 6, 20, 21, 0))


def test_utc_bounds_for_moscow_day() -> None:
    start, end = utc_day_bounds(date(2026, 6, 21), "Europe/Moscow")
    assert start == datetime(2026, 6, 20, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 21, 21, 0, tzinfo=timezone.utc)


def test_daily_pnl_uses_business_timezone_boundaries(tmp_path) -> None:
    db = Database(tmp_path / "clock.sqlite3")
    UserRepository(db).ensure_user(42)
    repository = TradeRepository(db)
    with db.connect() as connection:
        for closed_at, pnl in [
            ("2026-06-20 20:59:59", 10),
            ("2026-06-20 21:00:00", 20),
            ("2026-06-21 20:59:59", 30),
            ("2026-06-21 21:00:00", 40),
        ]:
            connection.execute(
                """
                INSERT INTO trades (
                    user_id, symbol, side, entry_price, stop_price, quantity,
                    status, exit_price, pnl, closed_at
                ) VALUES (42, 'BTCUSDT', 'long', 100, 90, 1, 'closed', 101, ?, ?)
                """,
                (pnl, closed_at),
            )
    assert repository.closed_pnl_for_date(42, date(2026, 6, 21), "Europe/Moscow") == 50
