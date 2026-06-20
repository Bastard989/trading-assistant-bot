from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    allowed_telegram_user_ids: frozenset[int]
    business_timezone: str


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or token == "put_your_bot_token_here":
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env before starting the bot.")

    market = os.getenv("MARKET", "futures").strip().lower()
    if market not in {"spot", "futures"}:
        raise RuntimeError("MARKET must be spot or futures.")

    allowed_ids: set[int] = set()
    for raw_id in os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            allowed_ids.add(int(raw_id))
        except ValueError as exc:
            raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must contain comma-separated integers.") from exc
    if not allowed_ids:
        raise RuntimeError("Set ALLOWED_TELEGRAM_USER_IDS before starting the bot.")

    business_timezone = os.getenv("BUSINESS_TIMEZONE", "Europe/Moscow").strip()
    try:
        ZoneInfo(business_timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("BUSINESS_TIMEZONE must be a valid IANA timezone.") from exc

    return Settings(
        telegram_bot_token=token,
        database_path=Path(os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3")).expanduser(),
        market=market,
        top_limit=int(os.getenv("TOP_LIMIT", "10")),
        alert_poll_seconds=int(os.getenv("ALERT_POLL_SECONDS", "30")),
        web_app_url=os.getenv("WEB_APP_URL", "http://127.0.0.1:8080").strip(),
        web_host=os.getenv("WEB_HOST", "127.0.0.1").strip(),
        web_port=int(os.getenv("WEB_PORT", "8080")),
        allowed_telegram_user_ids=frozenset(allowed_ids),
        business_timezone=business_timezone,
    )
