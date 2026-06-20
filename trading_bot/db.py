from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    trading_profile TEXT NOT NULL DEFAULT '',
    default_account_size REAL NOT NULL DEFAULT 0,
    default_risk_percent REAL NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    start_balance REAL NOT NULL,
    target_balance REAL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    note TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('above', 'below')),
    target_price REAL NOT NULL,
    last_price REAL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'triggered', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    triggered_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('long', 'short')),
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL,
    quantity REAL NOT NULL,
    leverage REAL NOT NULL DEFAULT 1,
    risk_amount REAL NOT NULL DEFAULT 0,
    setup TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    review_score REAL,
    ignored_warnings INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed', 'cancelled')),
    exit_price REAL,
    pnl REAL,
    fees REAL NOT NULL DEFAULT 0,
    close_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS pending_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('long', 'short')),
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL,
    quantity REAL NOT NULL,
    leverage REAL NOT NULL DEFAULT 1,
    risk_amount REAL NOT NULL DEFAULT 0,
    setup TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    review_score REAL NOT NULL DEFAULT 0,
    review_payload TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'idea' CHECK(outcome IN ('win', 'loss', 'breakeven', 'idea')),
    theory TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    screenshot_file_id TEXT NOT NULL DEFAULT '',
    linked_trade_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id),
    FOREIGN KEY(linked_trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS market_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bias TEXT NOT NULL CHECK(bias IN ('long', 'short', 'neutral')),
    structure TEXT NOT NULL DEFAULT '',
    levels TEXT NOT NULL DEFAULT '',
    invalidation_level REAL,
    note TEXT NOT NULL DEFAULT '',
    screenshot_file_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 70,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_date TEXT NOT NULL,
    allowed_symbols TEXT NOT NULL DEFAULT '',
    max_daily_risk_percent REAL NOT NULL DEFAULT 3,
    max_daily_loss REAL NOT NULL DEFAULT 0,
    plan_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, plan_date),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS trade_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    trade_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    score REAL NOT NULL,
    win_probability REAL NOT NULL,
    loss_probability REAL NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id),
    FOREIGN KEY(trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS note_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS trade_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    interval TEXT NOT NULL DEFAULT '1m',
    UNIQUE(trade_id, open_time, interval),
    FOREIGN KEY(trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS trade_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    telegram_file_id TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(trade_id) REFERENCES trades(id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_user_status_opened
    ON trades(user_id, status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_user_created
    ON journal_entries(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_status_symbol
    ON alerts(status, symbol);
CREATE INDEX IF NOT EXISTS idx_contexts_user_symbol_created
    ON market_contexts(user_id, symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attachments_user_trade
    ON trade_attachments(user_id, trade_id);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.executescript(SCHEMA)
            self._add_column(connection, "trades", "risk_amount", "REAL NOT NULL DEFAULT 0")
            self._add_column(connection, "trades", "setup", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "tags", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "review_score", "REAL")
            self._add_column(connection, "trades", "ignored_warnings", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(connection, "trades", "close_reason", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "session_id", "INTEGER")
            self._add_column(connection, "trades", "timeframe", "TEXT NOT NULL DEFAULT '5m'")
            self._add_column(connection, "journal_entries", "session_id", "INTEGER")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_user_session_status "
                "ON trades(user_id, session_id, status)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, checksum) VALUES (?, ?)",
                (1, "baseline-schema-v1"),
            )

    def _add_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
