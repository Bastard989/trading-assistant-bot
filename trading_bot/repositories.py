from __future__ import annotations

import sqlite3

from trading_bot.db import Database


class UserRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_user(self, telegram_id: int) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
                (telegram_id,),
            )

    def set_profile(self, telegram_id: int, profile: str) -> None:
        self.ensure_user(telegram_id)
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE users SET trading_profile = ? WHERE telegram_id = ?",
                (profile.strip(), telegram_id),
            )

    def get_profile(self, telegram_id: int) -> str:
        self.ensure_user(telegram_id)
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT trading_profile FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return row["trading_profile"] if row else ""

    def set_defaults(self, telegram_id: int, account_size: float, risk_percent: float) -> None:
        self.ensure_user(telegram_id)
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET default_account_size = ?, default_risk_percent = ?
                WHERE telegram_id = ?
                """,
                (account_size, risk_percent, telegram_id),
            )

    def get_defaults(self, telegram_id: int) -> sqlite3.Row:
        self.ensure_user(telegram_id)
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT default_account_size, default_risk_percent
                FROM users
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()


class AlertRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, user_id: int, symbol: str, direction: str, target_price: float) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO alerts (user_id, symbol, direction, target_price)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, symbol.upper(), direction, target_price),
            )
            return int(cursor.lastrowid)

    def list_for_user(self, user_id: int, include_inactive: bool = False) -> list[sqlite3.Row]:
        status_filter = "" if include_inactive else "AND status = 'active'"
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT *
                    FROM alerts
                    WHERE user_id = ? {status_filter}
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
            )

    def active(self) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM alerts
                    WHERE status = 'active'
                    ORDER BY created_at ASC
                    """
                )
            )

    def update_last_price(self, alert_id: int, last_price: float) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE alerts SET last_price = ? WHERE id = ?",
                (last_price, alert_id),
            )

    def mark_triggered(self, alert_id: int, last_price: float) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE alerts
                SET status = 'triggered',
                    last_price = ?,
                    triggered_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (last_price, alert_id),
            )

    def cancel(self, user_id: int, alert_id: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE alerts
                SET status = 'cancelled'
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (alert_id, user_id),
            )
            return cursor.rowcount > 0


class TradeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        user_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        target_price: float | None,
        quantity: float,
        leverage: float,
        note: str = "",
    ) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trades (
                    user_id, symbol, side, entry_price, stop_price, target_price,
                    quantity, leverage, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    symbol.upper(),
                    side,
                    entry_price,
                    stop_price,
                    target_price,
                    quantity,
                    leverage,
                    note.strip(),
                ),
            )
            return int(cursor.lastrowid)

    def close(
        self,
        user_id: int,
        trade_id: int,
        exit_price: float,
        fees: float = 0,
        note: str = "",
    ) -> sqlite3.Row | None:
        trade = self.get(user_id, trade_id)
        if not trade or trade["status"] != "open":
            return None

        direction = 1 if trade["side"] == "long" else -1
        pnl = (exit_price - trade["entry_price"]) * trade["quantity"] * direction - fees
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE trades
                SET status = 'closed',
                    exit_price = ?,
                    pnl = ?,
                    fees = ?,
                    note = trim(note || char(10) || ?),
                    closed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (exit_price, pnl, fees, note.strip(), trade_id, user_id),
            )
        return self.get(user_id, trade_id)

    def cancel(self, user_id: int, trade_id: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE trades
                SET status = 'cancelled'
                WHERE id = ? AND user_id = ? AND status = 'open'
                """,
                (trade_id, user_id),
            )
            return cursor.rowcount > 0

    def get(self, user_id: int, trade_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                "SELECT * FROM trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id),
            ).fetchone()

    def list_for_user(self, user_id: int, status: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        status_filter = "AND status = ?" if status else ""
        params: tuple[object, ...] = (user_id, status, limit) if status else (user_id, limit)
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT *
                    FROM trades
                    WHERE user_id = ? {status_filter}
                    ORDER BY opened_at DESC
                    LIMIT ?
                    """,
                    params,
                )
            )

    def stats(self, user_id: int) -> sqlite3.Row:
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                    COALESCE(SUM(pnl), 0) AS net_pnl,
                    COALESCE(AVG(pnl), 0) AS avg_pnl,
                    COALESCE(MAX(pnl), 0) AS best_pnl,
                    COALESCE(MIN(pnl), 0) AS worst_pnl
                FROM trades
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

    def stats_by_symbol(self, user_id: int) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        symbol,
                        COUNT(*) AS total,
                        SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                        SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                        COALESCE(SUM(pnl), 0) AS net_pnl
                    FROM trades
                    WHERE user_id = ?
                    GROUP BY symbol
                    ORDER BY net_pnl DESC, total DESC
                    """,
                    (user_id,),
                )
            )


class JournalRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        user_id: int,
        symbol: str = "",
        outcome: str = "idea",
        theory: str = "",
        description: str = "",
        screenshot_file_id: str = "",
        linked_trade_id: int | None = None,
    ) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO journal_entries (
                    user_id, symbol, outcome, theory, description, screenshot_file_id, linked_trade_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    symbol.upper(),
                    outcome,
                    theory.strip(),
                    description.strip(),
                    screenshot_file_id,
                    linked_trade_id,
                ),
            )
            return int(cursor.lastrowid)

    def list_for_user(self, user_id: int, symbol: str = "", limit: int = 10) -> list[sqlite3.Row]:
        symbol_filter = "AND symbol = ?" if symbol else ""
        params: tuple[object, ...] = (user_id, symbol.upper(), limit) if symbol else (user_id, limit)
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT *
                    FROM journal_entries
                    WHERE user_id = ? {symbol_filter}
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    params,
                )
            )
