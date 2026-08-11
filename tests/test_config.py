from __future__ import annotations

from pathlib import Path

import pytest

from trading_bot.config import load_settings


ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "ALLOWED_TELEGRAM_USER_IDS",
    "MARKET",
    "BUSINESS_TIMEZONE",
    "DATABASE_PATH",
    "TOP_LIMIT",
    "ALERT_POLL_SECONDS",
    "WEB_APP_URL",
    "WEB_HOST",
    "WEB_PORT",
)


def clean_environment(monkeypatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def valid_environment(monkeypatch, tmp_path) -> None:
    clean_environment(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "42, 7,42")
    monkeypatch.setenv("MARKET", "SPOT")
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "assistant.sqlite3"))
    monkeypatch.setenv("TOP_LIMIT", "25")
    monkeypatch.setenv("ALERT_POLL_SECONDS", "45")
    monkeypatch.setenv("WEB_APP_URL", "https://trading.example.test")
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "8443")


def test_load_settings_parses_and_bounds_values(tmp_path, monkeypatch) -> None:
    valid_environment(monkeypatch, tmp_path)

    settings = load_settings()

    assert settings.telegram_bot_token == "123456:test-token"
    assert settings.allowed_telegram_user_ids == frozenset({7, 42})
    assert settings.database_path == Path(tmp_path / "assistant.sqlite3")
    assert settings.market == "spot"
    assert settings.top_limit == 25
    assert settings.alert_poll_seconds == 45
    assert settings.web_port == 8443


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("TELEGRAM_BOT_TOKEN", "put_your_bot_token_here", "TELEGRAM_BOT_TOKEN"),
        ("MARKET", "options", "MARKET"),
        ("ALLOWED_TELEGRAM_USER_IDS", "42,not-an-id", "comma-separated integers"),
        ("ALLOWED_TELEGRAM_USER_IDS", "-1", "comma-separated integers"),
        ("BUSINESS_TIMEZONE", "Mars/Olympus", "IANA timezone"),
        ("TOP_LIMIT", "many", "TOP_LIMIT must be an integer"),
        ("TOP_LIMIT", "0", "TOP_LIMIT must be between"),
        ("ALERT_POLL_SECONDS", "4", "ALERT_POLL_SECONDS must be between"),
        ("WEB_APP_URL", "telegram.example.test", "absolute http/https"),
        ("WEB_HOST", "", "WEB_HOST cannot be empty"),
        ("WEB_PORT", "70000", "WEB_PORT must be between"),
    ),
)
def test_load_settings_fails_closed_on_invalid_configuration(
    key, value, message, tmp_path, monkeypatch
) -> None:
    valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match=message):
        load_settings()


def test_load_settings_requires_nonempty_owner_allowlist(tmp_path, monkeypatch) -> None:
    valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", " , ")

    with pytest.raises(RuntimeError, match="ALLOWED_TELEGRAM_USER_IDS"):
        load_settings()
