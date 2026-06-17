from __future__ import annotations

import json
import sqlite3
from datetime import date

from trading_bot.db import Database
from trading_bot.models import TradeDraft, TradeReview
from trading_bot.templates import DEFAULT_TEMPLATES


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

    def list_user_ids(self) -> list[int]:
        with self.db.connect() as connection:
            rows = connection.execute("SELECT telegram_id FROM users ORDER BY telegram_id").fetchall()
        return [int(row["telegram_id"]) for row in rows]


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
        risk_amount: float = 0,
        setup: str = "",
        tags: tuple[str, ...] = (),
        review_score: float | None = None,
        ignored_warnings: bool = False,
        note: str = "",
    ) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trades (
                    user_id, symbol, side, entry_price, stop_price, target_price,
                    quantity, leverage, risk_amount, setup, tags, review_score,
                    ignored_warnings, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    risk_amount,
                    setup.strip(),
                    ",".join(tags),
                    review_score,
                    1 if ignored_warnings else 0,
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
        close_reason: str = "manual",
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
                    close_reason = ?,
                    note = trim(note || char(10) || ?),
                    closed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (exit_price, pnl, fees, close_reason.strip(), note.strip(), trade_id, user_id),
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

    def open_all(self) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM trades
                    WHERE status = 'open'
                    ORDER BY opened_at ASC
                    """
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

    def open_risk_total(self, user_id: int) -> float:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(risk_amount), 0) AS open_risk
                FROM trades
                WHERE user_id = ? AND status = 'open'
                """,
                (user_id,),
            ).fetchone()
        return float(row["open_risk"] or 0)

    def closed_pnl_for_date(self, user_id: int, plan_date: date) -> float:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(pnl), 0) AS pnl
                FROM trades
                WHERE user_id = ?
                  AND status = 'closed'
                  AND date(closed_at) = ?
                """,
                (user_id, plan_date.isoformat()),
            ).fetchone()
        return float(row["pnl"] or 0)


class PendingTradeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, user_id: int, draft: TradeDraft, review: TradeReview) -> int:
        payload = {
            "score": review.score,
            "win_probability": review.win_probability,
            "loss_probability": review.loss_probability,
            "severity": review.severity,
            "summary": review.summary,
            "issues": [issue.__dict__ for issue in review.issues],
            "distances": [distance.__dict__ for distance in review.distances],
        }
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pending_trades (
                    user_id, symbol, side, entry_price, stop_price, target_price,
                    quantity, leverage, risk_amount, setup, tags, note,
                    review_score, review_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    draft.symbol,
                    draft.side,
                    draft.entry_price,
                    draft.stop_price,
                    draft.target_price,
                    draft.quantity,
                    draft.leverage,
                    draft.risk_amount,
                    draft.setup,
                    ",".join(draft.tags),
                    draft.note,
                    review.score,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def get(self, user_id: int, pending_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                "SELECT * FROM pending_trades WHERE id = ? AND user_id = ?",
                (pending_id, user_id),
            ).fetchone()

    def delete(self, user_id: int, pending_id: int) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "DELETE FROM pending_trades WHERE id = ? AND user_id = ?",
                (pending_id, user_id),
            )


class MarketContextRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        user_id: int,
        symbol: str,
        timeframe: str,
        bias: str,
        structure: str = "",
        levels: tuple[float, ...] = (),
        invalidation_level: float | None = None,
        note: str = "",
        screenshot_file_id: str = "",
        confidence: float = 70,
        expires_at: str | None = None,
    ) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO market_contexts (
                    user_id, symbol, timeframe, bias, structure, levels,
                    invalidation_level, note, screenshot_file_id, confidence, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    symbol.upper(),
                    timeframe.upper(),
                    bias,
                    structure.strip(),
                    ",".join(str(level) for level in levels),
                    invalidation_level,
                    note.strip(),
                    screenshot_file_id,
                    confidence,
                    expires_at,
                ),
            )
            return int(cursor.lastrowid)

    def latest_for_symbol(self, user_id: int, symbol: str, limit: int = 8) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM market_contexts
                    WHERE user_id = ? AND symbol = ?
                      AND (expires_at IS NULL OR datetime(expires_at) > datetime('now'))
                    ORDER BY
                        CASE timeframe
                            WHEN '1D' THEN 1
                            WHEN '4H' THEN 2
                            WHEN '1H' THEN 3
                            WHEN '15M' THEN 4
                            WHEN '5M' THEN 5
                            WHEN '1M' THEN 6
                            ELSE 7
                        END,
                        created_at DESC
                    LIMIT ?
                    """,
                    (user_id, symbol.upper(), limit),
                )
            )

    def list_for_user(self, user_id: int, symbol: str = "", limit: int = 20) -> list[sqlite3.Row]:
        symbol_filter = "AND symbol = ?" if symbol else ""
        params: tuple[object, ...] = (user_id, symbol.upper(), limit) if symbol else (user_id, limit)
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT *
                    FROM market_contexts
                    WHERE user_id = ? {symbol_filter}
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    params,
                )
            )


class WatchlistRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def replace(self, user_id: int, symbols: tuple[str, ...]) -> None:
        with self.db.connect() as connection:
            connection.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))
            for symbol in symbols:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO watchlist (user_id, symbol)
                    VALUES (?, ?)
                    """,
                    (user_id, symbol.upper()),
                )

    def list_symbols(self, user_id: int) -> list[str]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol
                FROM watchlist
                WHERE user_id = ?
                ORDER BY priority ASC, symbol ASC
                """,
                (user_id,),
            ).fetchall()
        return [row["symbol"] for row in rows]

    def contains(self, user_id: int, symbol: str) -> bool:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM watchlist WHERE user_id = ? AND symbol = ?",
                (user_id, symbol.upper()),
            ).fetchone()
        return row is not None


class DailyPlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        user_id: int,
        plan_date: date,
        allowed_symbols: tuple[str, ...],
        max_daily_risk_percent: float,
        max_daily_loss: float,
        plan_text: str,
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_plans (
                    user_id, plan_date, allowed_symbols, max_daily_risk_percent,
                    max_daily_loss, plan_text
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, plan_date) DO UPDATE SET
                    allowed_symbols = excluded.allowed_symbols,
                    max_daily_risk_percent = excluded.max_daily_risk_percent,
                    max_daily_loss = excluded.max_daily_loss,
                    plan_text = excluded.plan_text
                """,
                (
                    user_id,
                    plan_date.isoformat(),
                    ",".join(allowed_symbols),
                    max_daily_risk_percent,
                    max_daily_loss,
                    plan_text.strip(),
                ),
            )

    def get(self, user_id: int, plan_date: date) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM daily_plans
                WHERE user_id = ? AND plan_date = ?
                """,
                (user_id, plan_date.isoformat()),
            ).fetchone()

    def latest(self, user_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM daily_plans
                WHERE user_id = ?
                ORDER BY plan_date DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()


class TradeReviewRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        user_id: int,
        symbol: str,
        side: str,
        review: TradeReview,
        trade_id: int | None = None,
    ) -> int:
        payload = {
            "issues": [issue.__dict__ for issue in review.issues],
            "distances": [distance.__dict__ for distance in review.distances],
        }
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO trade_reviews (
                    user_id, trade_id, symbol, side, score, win_probability,
                    loss_probability, severity, summary, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    trade_id,
                    symbol.upper(),
                    side,
                    review.score,
                    review.win_probability,
                    review.loss_probability,
                    review.severity,
                    review.summary,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)


class TemplateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, user_id: int, name: str, body: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO note_templates (user_id, name, body)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    body = excluded.body,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, name.lower(), body.strip()),
            )

    def get(self, user_id: int, name: str) -> str | None:
        name = name.lower()
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT body
                FROM note_templates
                WHERE user_id = ? AND name = ?
                """,
                (user_id, name),
            ).fetchone()
        if row:
            return row["body"]
        return DEFAULT_TEMPLATES.get(name)

    def delete(self, user_id: int, name: str) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM note_templates WHERE user_id = ? AND name = ?",
                (user_id, name.lower()),
            )
            return cursor.rowcount > 0

    def list_for_user(self, user_id: int) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = [
            {"name": name, "body": body, "source": "default"}
            for name, body in sorted(DEFAULT_TEMPLATES.items())
        ]
        with self.db.connect() as connection:
            custom = connection.execute(
                """
                SELECT name, body
                FROM note_templates
                WHERE user_id = ?
                ORDER BY name ASC
                """,
                (user_id,),
            ).fetchall()
        custom_names = {row["name"] for row in custom}
        rows = [row for row in rows if row["name"] not in custom_names]
        rows.extend({"name": row["name"], "body": row["body"], "source": "custom"} for row in custom)
        rows.sort(key=lambda row: row["name"])
        return rows


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
