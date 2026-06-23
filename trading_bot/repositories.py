from __future__ import annotations

import json
import math
import sqlite3
from datetime import date

from trading_bot.db import Database
from trading_bot.models import TradeDraft, TradeReview
from trading_bot.templates import DEFAULT_TEMPLATES
from trading_bot.services.clock import utc_day_bounds


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


class IdempotencyRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def begin(self, user_id: int, scope: str, key: str, request_hash: str) -> tuple[str, sqlite3.Row | None]:
        with self.db.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO idempotency_keys
                        (user_id, scope, idempotency_key, request_hash, state)
                    VALUES (?, ?, ?, ?, 'in_progress')
                    """,
                    (user_id, scope, key, request_hash),
                )
                return "new", None
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT * FROM idempotency_keys
                    WHERE user_id = ? AND scope = ? AND idempotency_key = ?
                    """,
                    (user_id, scope, key),
                ).fetchone()
                if row is None or row["request_hash"] != request_hash:
                    return "conflict", row
                return str(row["state"]), row

    def complete(self, user_id: int, scope: str, key: str, status: int, body: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE idempotency_keys
                SET state = 'completed', response_status = ?, response_body = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND scope = ? AND idempotency_key = ?
                  AND state = 'in_progress'
                """,
                (status, body, user_id, scope, key),
            )

    def release(self, user_id: int, scope: str, key: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                DELETE FROM idempotency_keys
                WHERE user_id = ? AND scope = ? AND idempotency_key = ?
                  AND state = 'in_progress'
                """,
                (user_id, scope, key),
            )


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


class TradingSessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, user_id: int, name: str, start_balance: float, target_balance: float | None = None, note: str = "") -> int:
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE trading_sessions SET status = 'archived', archived_at = CURRENT_TIMESTAMP WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            cursor = connection.execute(
                "INSERT INTO trading_sessions (user_id, name, start_balance, target_balance, note) VALUES (?, ?, ?, ?, ?)",
                (user_id, name.strip(), start_balance, target_balance, note.strip()),
            )
            return int(cursor.lastrowid)

    def activate(self, user_id: int, session_id: int) -> bool:
        with self.db.connect() as connection:
            row = connection.execute("SELECT id FROM trading_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
            if not row:
                return False
            connection.execute("UPDATE trading_sessions SET status = 'archived', archived_at = CURRENT_TIMESTAMP WHERE user_id = ? AND status = 'active'", (user_id,))
            connection.execute("UPDATE trading_sessions SET status = 'active', archived_at = NULL WHERE id = ?", (session_id,))
            return True

    def archive(self, user_id: int, session_id: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "UPDATE trading_sessions SET status = 'archived', archived_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            return cursor.rowcount > 0

    def active(self, user_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                "SELECT * FROM trading_sessions WHERE user_id = ? AND status = 'active' ORDER BY started_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()

    def list_for_user(self, user_id: int) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(connection.execute(
                """
                SELECT s.*,
                    COUNT(t.id) AS trade_count,
                    SUM(CASE WHEN t.status = 'closed' THEN 1 ELSE 0 END) AS closed_count,
                    SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    COALESCE(SUM(CASE WHEN t.status = 'closed' THEN t.pnl ELSE 0 END), 0) AS realized_pnl
                FROM trading_sessions s
                LEFT JOIN trades t ON t.session_id = s.id
                WHERE s.user_id = ?
                GROUP BY s.id
                ORDER BY s.started_at DESC
                """,
                (user_id,),
            ))


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
        timeframe: str = "5m",
    ) -> int:
        with self.db.connect() as connection:
            session = connection.execute(
                "SELECT id, name FROM trading_sessions WHERE user_id = ? AND status = 'active' ORDER BY started_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            tag_values = list(tags)
            tag_values.append(f"coin:{symbol.upper().replace('USDT', '')}")
            if session:
                tag_values.append(f"session:{session['name']}")
            cursor = connection.execute(
                """
                INSERT INTO trades (
                    user_id, symbol, side, entry_price, stop_price, target_price,
                    quantity, leverage, risk_amount, setup, tags, review_score,
                    ignored_warnings, note, session_id, timeframe
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ",".join(dict.fromkeys(tag_values)),
                    review_score,
                    1 if ignored_warnings else 0,
                    note.strip(),
                    int(session["id"]) if session else None,
                    timeframe,
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
        pnl: float | None = None,
    ) -> sqlite3.Row | None:
        if (
            not math.isfinite(exit_price)
            or exit_price <= 0
            or not math.isfinite(fees)
            or fees < 0
            or (pnl is not None and not math.isfinite(pnl))
        ):
            return None
        with self.db.connect() as connection:
            row = connection.execute(
                """
                UPDATE trades
                SET status = 'closed',
                    exit_price = ?,
                    pnl = COALESCE(
                        ?,
                        (? - entry_price) * quantity * CASE WHEN side = 'long' THEN 1 ELSE -1 END - ?
                    ),
                    fees = ?,
                    close_reason = ?,
                    note = trim(note || char(10) || ?),
                    closed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND status = 'open'
                RETURNING *
                """,
                (exit_price, pnl, exit_price, fees, fees, close_reason.strip(), note.strip(), trade_id, user_id),
            ).fetchone()
        return row

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

    def find_recent_open(
        self,
        user_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        target_price: float | None,
        minutes: int = 10,
    ) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM trades
                WHERE user_id = ? AND symbol = ? AND side = ? AND status = 'open'
                  AND abs(entry_price - ?) < 0.0000001
                  AND abs(stop_price - ?) < 0.0000001
                  AND ((target_price IS NULL AND ? IS NULL) OR abs(target_price - ?) < 0.0000001)
                  AND opened_at >= datetime('now', ?)
                ORDER BY opened_at DESC LIMIT 1
                """,
                (user_id, symbol.upper(), side, entry_price, stop_price, target_price, target_price, f"-{minutes} minutes"),
            ).fetchone()

    def update(
        self,
        user_id: int,
        trade_id: int,
        entry_price: float,
        stop_price: float,
        target_price: float | None,
        quantity: float,
        timeframe: str,
        note: str = "",
    ) -> sqlite3.Row | None:
        trade = self.get(user_id, trade_id)
        if not trade or trade["status"] != "open":
            return None
        risk_amount = abs(entry_price - stop_price) * quantity
        with self.db.connect() as connection:
            row = connection.execute(
                """
                UPDATE trades SET entry_price = ?, stop_price = ?, target_price = ?,
                    quantity = ?, timeframe = ?, risk_amount = ?,
                    note = CASE WHEN ? = '' THEN note ELSE trim(note || char(10) || ?) END
                WHERE id = ? AND user_id = ? AND status = 'open'
                RETURNING *
                """,
                (entry_price, stop_price, target_price, quantity, timeframe, risk_amount, note.strip(), note.strip(), trade_id, user_id),
            ).fetchone()
        return row

    def set_leverage(self, user_id: int, trade_id: int, leverage: float) -> sqlite3.Row | None:
        if leverage <= 0:
            return None
        with self.db.connect() as connection:
            row = connection.execute(
                "UPDATE trades SET leverage = ? WHERE id = ? AND user_id = ? AND status = 'open' RETURNING *",
                (leverage, trade_id, user_id),
            ).fetchone()
        return row

    def add_attachment(self, user_id: int, trade_id: int, telegram_file_id: str = "", local_path: str = "", caption: str = "") -> int | None:
        if not self.get(user_id, trade_id):
            return None
        with self.db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO trade_attachments (trade_id, user_id, telegram_file_id, local_path, caption) VALUES (?, ?, ?, ?, ?)",
                (trade_id, user_id, telegram_file_id, local_path, caption.strip()),
            )
            return int(cursor.lastrowid)

    def attachments(self, user_id: int, trade_id: int) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(connection.execute(
                "SELECT * FROM trade_attachments WHERE user_id = ? AND trade_id = ? ORDER BY created_at ASC",
                (user_id, trade_id),
            ))

    def attachment(self, user_id: int, attachment_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                "SELECT * FROM trade_attachments WHERE id = ? AND user_id = ?",
                (attachment_id, user_id),
            ).fetchone()

    def owns_telegram_file(self, user_id: int, file_id: str) -> bool:
        if not file_id or len(file_id) > 1024:
            return False
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT 1 FROM trade_attachments
                WHERE user_id = ? AND telegram_file_id = ?
                UNION ALL
                SELECT 1 FROM journal_entries
                WHERE user_id = ?
                  AND instr(',' || screenshot_file_id || ',', ',' || ? || ',') > 0
                UNION ALL
                SELECT 1 FROM market_contexts
                WHERE user_id = ? AND screenshot_file_id = ?
                LIMIT 1
                """,
                (user_id, file_id, user_id, file_id, user_id, file_id),
            ).fetchone() is not None

    def record_level_observation(self, user_id: int, trade_id: int, observation) -> sqlite3.Row | None:
        trade = self.get(user_id, trade_id)
        if not trade or trade["status"] != "open":
            return None
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO trade_level_observations (
                    trade_id, user_id, symbol, source, observed_price, level_price,
                    matched_level, candle_high, candle_low, ambiguity
                ) VALUES (?, ?, ?, 'binance_public', ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id, user_id, trade["symbol"], observation.observed_price,
                    observation.level_price, observation.matched_level,
                    observation.candle_high, observation.candle_low, observation.ambiguity,
                ),
            )
            return connection.execute(
                """
                SELECT * FROM trade_level_observations
                WHERE trade_id = ? AND user_id = ? AND matched_level = ?
                  AND notification_status = 'pending'
                """,
                (trade_id, user_id, observation.matched_level),
            ).fetchone()

    def mark_level_observation_sent(self, user_id: int, observation_id: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE trade_level_observations
                SET notification_status = 'sent', notified_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND notification_status = 'pending'
                """,
                (observation_id, user_id),
            )
            return cursor.rowcount > 0

    def pending_level_observation(self, user_id: int, trade_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM trade_level_observations
                WHERE trade_id = ? AND user_id = ? AND notification_status = 'pending'
                ORDER BY observed_at ASC LIMIT 1
                """,
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

    def list_for_session(self, user_id: int, session_id: int, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        status_filter = "AND status = ?" if status else ""
        params: tuple[object, ...] = (user_id, session_id, status, limit) if status else (user_id, session_id, limit)
        with self.db.connect() as connection:
            return list(connection.execute(
                f"SELECT * FROM trades WHERE user_id = ? AND session_id = ? {status_filter} ORDER BY opened_at DESC LIMIT ?",
                params,
            ))

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

    def save_candles(self, trade_id: int, candles: list[dict[str, float]], interval: str = "1m") -> None:
        with self.db.connect() as connection:
            for candle in candles:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO trade_candles (trade_id, open_time, open, high, low, close, volume, interval)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade_id, int(candle["open_time"]), candle["open"], candle["high"],
                        candle["low"], candle["close"], candle.get("volume", 0), interval,
                    ),
                )

    def candles(self, trade_id: int, interval: str = "1m", limit: int = 240) -> list[sqlite3.Row]:
        with self.db.connect() as connection:
            return list(connection.execute(
                """
                SELECT open_time, open, high, low, close, volume
                FROM trade_candles
                WHERE trade_id = ? AND interval = ?
                ORDER BY open_time ASC
                LIMIT ?
                """,
                (trade_id, interval, limit),
            ))

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

    def stats_for_session(self, user_id: int, session_id: int) -> sqlite3.Row:
        with self.db.connect() as connection:
            return connection.execute(
                """
                SELECT COUNT(*) AS total,
                    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                    COALESCE(SUM(pnl), 0) AS net_pnl,
                    COALESCE(AVG(pnl), 0) AS avg_pnl,
                    COALESCE(MAX(pnl), 0) AS best_pnl,
                    COALESCE(MIN(pnl), 0) AS worst_pnl
                FROM trades WHERE user_id = ? AND session_id = ?
                """,
                (user_id, session_id),
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

    def closed_pnl_for_date(self, user_id: int, plan_date: date, timezone_name: str = "UTC") -> float:
        start, end = utc_day_bounds(plan_date, timezone_name)
        start_text = start.strftime("%Y-%m-%d %H:%M:%S")
        end_text = end.strftime("%Y-%m-%d %H:%M:%S")
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(pnl), 0) AS pnl
                FROM trades
                WHERE user_id = ?
                  AND status = 'closed'
                  AND closed_at >= ? AND closed_at < ?
                """,
                (user_id, start_text, end_text),
            ).fetchone()
        return float(row["pnl"] or 0)


class PendingTradeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, user_id: int, draft: TradeDraft, review: TradeReview) -> int:
        payload = {
            "rule_score": review.score,
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

    def add(self, user_id: int, symbol: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, symbol) VALUES (?, ?)",
                (user_id, symbol.upper()),
            )

    def remove(self, user_id: int, symbol: str) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?",
                (user_id, symbol.upper()),
            )
            return cursor.rowcount > 0

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
                    user_id, trade_id, symbol, side, score, rule_score, win_probability,
                    loss_probability, severity, summary, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    trade_id,
                    symbol.upper(),
                    side,
                    review.score,
                    review.score,
                    review.score,
                    100 - review.score,
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
            session = connection.execute(
                "SELECT id FROM trading_sessions WHERE user_id = ? AND status = 'active' ORDER BY started_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO journal_entries (
                    user_id, symbol, outcome, theory, description, screenshot_file_id, linked_trade_id, session_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    symbol.upper(),
                    outcome,
                    theory.strip(),
                    description.strip(),
                    screenshot_file_id,
                    linked_trade_id,
                    int(session["id"]) if session else None,
                ),
            )
            return int(cursor.lastrowid)

    def list_for_user(self, user_id: int, symbol: str = "", limit: int = 10) -> list[sqlite3.Row]:
        symbol_filter = "AND j.symbol = ?" if symbol else ""
        params: tuple[object, ...] = (user_id, symbol.upper(), limit) if symbol else (user_id, limit)
        with self.db.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT j.*,
                        t.status AS trade_status,
                        t.pnl AS trade_pnl,
                        t.close_reason AS trade_close_reason,
                        t.exit_price AS trade_exit_price,
                        t.side AS trade_side,
                        t.entry_price AS trade_entry_price,
                        t.quantity AS trade_quantity
                    FROM journal_entries j
                    LEFT JOIN trades t ON t.id = j.linked_trade_id AND t.user_id = j.user_id
                    WHERE j.user_id = ? {symbol_filter}
                    ORDER BY j.created_at DESC
                    LIMIT ?
                    """,
                    params,
                )
            )

    def get(self, user_id: int, entry_id: int) -> sqlite3.Row | None:
        with self.db.connect() as connection:
            return connection.execute(
                "SELECT * FROM journal_entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            ).fetchone()

    def link_trade(self, user_id: int, entry_id: int, trade_id: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "UPDATE journal_entries SET linked_trade_id = ? WHERE id = ? AND user_id = ?",
                (trade_id, entry_id, user_id),
            )
            return cursor.rowcount > 0

    def merge(self, user_id: int, keep_id: int, remove_id: int) -> bool:
        with self.db.connect() as connection:
            keep = connection.execute(
                "SELECT * FROM journal_entries WHERE id = ? AND user_id = ?",
                (keep_id, user_id),
            ).fetchone()
            remove = connection.execute(
                "SELECT * FROM journal_entries WHERE id = ? AND user_id = ?",
                (remove_id, user_id),
            ).fetchone()
            if not keep or not remove or keep_id == remove_id:
                return False
            file_ids = [
                item
                for value in (keep["screenshot_file_id"], remove["screenshot_file_id"])
                for item in str(value or "").split(",")
                if item
            ]
            connection.execute(
                """
                UPDATE journal_entries
                SET screenshot_file_id = ?,
                    linked_trade_id = COALESCE(linked_trade_id, ?)
                WHERE id = ? AND user_id = ?
                """,
                (",".join(dict.fromkeys(file_ids)), remove["linked_trade_id"], keep_id, user_id),
            )
            connection.execute(
                "DELETE FROM journal_entries WHERE id = ? AND user_id = ?",
                (remove_id, user_id),
            )
            return True
