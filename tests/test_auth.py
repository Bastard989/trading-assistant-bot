from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from trading_bot.auth import InitDataError, verify_init_data


TOKEN = "123456:test-token"


def signed_init_data(*, user_id: int = 42, auth_date: int = 1_700_000_000) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAE-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_init_data_derives_signed_user() -> None:
    identity = verify_init_data(signed_init_data(), TOKEN, now=1_700_000_100)
    assert identity.user_id == 42
    assert identity.query_id == "AAE-test"


@pytest.mark.parametrize("payload", ["", "hash=nope", "auth_date=1&auth_date=2&hash=" + "0" * 64])
def test_malformed_init_data_is_rejected(payload: str) -> None:
    with pytest.raises(InitDataError):
        verify_init_data(payload, TOKEN, now=1_700_000_100)


def test_tampered_user_is_rejected() -> None:
    payload = signed_init_data().replace("%3A42", "%3A99")
    with pytest.raises(InitDataError):
        verify_init_data(payload, TOKEN, now=1_700_000_100)


def test_expired_and_future_init_data_are_rejected() -> None:
    with pytest.raises(InitDataError, match="Expired"):
        verify_init_data(signed_init_data(auth_date=1_699_999_000), TOKEN, now=1_700_000_000)
    with pytest.raises(InitDataError, match="Expired"):
        verify_init_data(signed_init_data(auth_date=1_700_000_031), TOKEN, now=1_700_000_000)
