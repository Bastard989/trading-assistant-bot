from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException


class InitDataError(ValueError):
    """Raised when Telegram Mini App authentication data is invalid."""


@dataclass(frozen=True)
class TelegramIdentity:
    user_id: int
    auth_date: int
    query_id: str | None


def parse_allowed_user_ids(value: str | None) -> frozenset[int]:
    try:
        return frozenset(int(item.strip()) for item in (value or "").split(",") if item.strip())
    except ValueError as exc:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must contain comma-separated integers.") from exc


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 300,
    now: int | None = None,
) -> TelegramIdentity:
    if not init_data or len(init_data) > 16_384:
        raise InitDataError("Malformed Telegram initData")
    if not bot_token:
        raise InitDataError("Server authentication is not configured")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("Malformed Telegram initData") from exc
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise InitDataError("Duplicate Telegram initData field")
        values[key] = value

    supplied_hash = values.pop("hash", "")
    if len(supplied_hash) != 64:
        raise InitDataError("Malformed Telegram signature")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash):
        raise InitDataError("Invalid Telegram signature")

    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InitDataError("Malformed Telegram identity") from exc
    current_time = int(time.time()) if now is None else now
    if auth_date > current_time + 30 or current_time - auth_date > max_age_seconds:
        raise InitDataError("Expired Telegram initData")
    if user_id <= 0:
        raise InitDataError("Malformed Telegram identity")
    return TelegramIdentity(user_id=user_id, auth_date=auth_date, query_id=values.get("query_id"))


def require_telegram_user(
    authorization: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None),
) -> int:
    return authenticate_telegram_user(authorization, x_telegram_init_data, x_dev_user_id)


def authenticate_telegram_user(
    authorization: str | None,
    x_telegram_init_data: str | None,
    x_dev_user_id: str | None,
) -> int:
    app_env = os.getenv("APP_ENV", "production").strip().lower()
    allowed = parse_allowed_user_ids(os.getenv("ALLOWED_TELEGRAM_USER_IDS"))
    if app_env != "production" and os.getenv("ENABLE_DEV_AUTH", "false").lower() == "true" and x_dev_user_id:
        try:
            user_id = int(x_dev_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Invalid development identity") from exc
    else:
        raw = x_telegram_init_data
        if authorization and authorization.lower().startswith("tma "):
            raw = authorization[4:]
        try:
            identity = verify_init_data(
                raw or "",
                os.getenv("TELEGRAM_BOT_TOKEN", ""),
                max_age_seconds=int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE_SECONDS", "300")),
            )
        except InitDataError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        user_id = identity.user_id

    if not allowed or user_id not in allowed:
        raise HTTPException(status_code=403, detail="User is not allowed")
    return user_id
