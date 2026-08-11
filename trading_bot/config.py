from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
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


def _integer_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


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
            user_id = int(raw_id)
            if user_id <= 0:
                raise ValueError
            allowed_ids.add(user_id)
        except ValueError as exc:
            raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must contain comma-separated integers.") from exc
    if not allowed_ids:
        raise RuntimeError("Set ALLOWED_TELEGRAM_USER_IDS before starting the bot.")

    business_timezone = os.getenv("BUSINESS_TIMEZONE", "Europe/Moscow").strip()
    try:
        ZoneInfo(business_timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("BUSINESS_TIMEZONE must be a valid IANA timezone.") from exc

    web_app_url = os.getenv("WEB_APP_URL", "http://127.0.0.1:8080").strip()
    parsed_web_url = urlsplit(web_app_url)
    if parsed_web_url.scheme not in {"http", "https"} or not parsed_web_url.netloc:
        raise RuntimeError("WEB_APP_URL must be an absolute http/https URL.")
    web_host = os.getenv("WEB_HOST", "127.0.0.1").strip()
    if not web_host:
        raise RuntimeError("WEB_HOST cannot be empty.")

    return Settings(
        telegram_bot_token=token,
        database_path=Path(os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3")).expanduser(),
        market=market,
        top_limit=_integer_env("TOP_LIMIT", 10, minimum=1, maximum=100),
        alert_poll_seconds=_integer_env("ALERT_POLL_SECONDS", 30, minimum=5, maximum=3600),
        web_app_url=web_app_url,
        web_host=web_host,
        web_port=_integer_env("WEB_PORT", 8080, minimum=1, maximum=65535),
        allowed_telegram_user_ids=frozenset(allowed_ids),
        business_timezone=business_timezone,
    )
