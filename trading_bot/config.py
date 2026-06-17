from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    database_path: Path
    market: str
    top_limit: int
    alert_poll_seconds: int
    web_app_url: str
    web_host: str
    web_port: int


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "put_your_bot_token_here":
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env before starting the bot.")

    market = os.getenv("MARKET", "futures").strip().lower()
    if market not in {"spot", "futures"}:
        raise RuntimeError("MARKET must be spot or futures.")

    return Settings(
        telegram_bot_token=token,
        database_path=Path(os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3")).expanduser(),
        market=market,
        top_limit=int(os.getenv("TOP_LIMIT", "10")),
        alert_poll_seconds=int(os.getenv("ALERT_POLL_SECONDS", "30")),
        web_app_url=os.getenv("WEB_APP_URL", "http://127.0.0.1:8080").strip(),
        web_host=os.getenv("WEB_HOST", "127.0.0.1").strip(),
        web_port=int(os.getenv("WEB_PORT", "8080")),
    )
