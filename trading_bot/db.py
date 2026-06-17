from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    trading_profile TEXT NOT NULL DEFAULT '',
    default_account_size REAL NOT NULL DEFAULT 0,
    default_risk_percent REAL NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.executescript(SCHEMA)
            self._add_column(connection, "trades", "risk_amount", "REAL NOT NULL DEFAULT 0")
            self._add_column(connection, "trades", "setup", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "tags", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "review_score", "REAL")
            self._add_column(connection, "trades", "ignored_warnings", "INTEGER NOT NULL DEFAULT 0")

    def _add_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
