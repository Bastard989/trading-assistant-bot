from __future__ import annotations

import hashlib
import hmac
import importlib
import io
import json
import sys
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from PIL import Image


TOKEN = "123456:test-token"


def auth_header(user_id: int) -> dict[str, str]:
    values = {
        "auth_date": str(int(time.time())),
        "query_id": f"query-{user_id}",
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"tma {urlencode(values)}"}


def load_test_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("TRADE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "42,99")
    monkeypatch.setenv("APP_ENV", "production")
    sys.modules.pop("trading_bot.web_app", None)
    return importlib.import_module("trading_bot.web_app")


def test_api_requires_signed_identity_and_ignores_spoofed_query(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.users.ensure_user(42)
    module.users.ensure_user(99)
    module.trades.create(42, "BTCUSDT", "long", 100, 90, 120, 1, 1)
    module.trades.create(99, "ETHUSDT", "short", 100, 110, 80, 1, 1)
    client = TestClient(module.app)

    assert client.get("/api/trades").status_code == 401
    response = client.get("/api/trades?user_id=99", headers=auth_header(42))
    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()["items"]] == ["BTCUSDT"]
    assert client.get("/docs").status_code == 404


def test_attachment_upload_validates_image_and_owner(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.users.ensure_user(42)
    module.users.ensure_user(99)
    trade_id = module.trades.create(42, "BTCUSDT", "long", 100, 90, 120, 1, 1)
    client = TestClient(module.app)

    fake = client.post(
        f"/api/trades/{trade_id}/attachment?filename=fake.jpg",
        content=b"<script>alert(1)</script>", headers={**auth_header(42), "Content-Type": "image/jpeg"},
    )
    assert fake.status_code == 415
    assert client.post(
        f"/api/trades/{trade_id}/attachment?filename=foreign.jpg",
        content=b"not-an-image", headers=auth_header(99),
    ).status_code == 404

    image_bytes = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(image_bytes, "PNG")
    uploaded = client.post(
        f"/api/trades/{trade_id}/attachment?filename=screen.png",
        content=image_bytes.getvalue(), headers={**auth_header(42), "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 200
    attachment_id = uploaded.json()["id"]
    assert client.get(f"/api/trade-attachment/{attachment_id}", headers=auth_header(99)).status_code == 404
    downloaded = client.get(f"/api/trade-attachment/{attachment_id}", headers=auth_header(42))
    assert downloaded.status_code == 200
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.content.startswith(b"\xff\xd8\xff")


def test_security_headers_and_public_health(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    response = TestClient(module.app).get("/health/live")
    assert response.status_code == 200
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
