from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.test_api_security import auth_header, load_test_app
from trading_bot.services.obsidian_export import build_obsidian_vault_zip, safe_filename


def test_obsidian_zip_contains_dashboard_notes_and_canvas() -> None:
    content = build_obsidian_vault_zip(
        sessions=[
            {
                "id": 7,
                "name": "SOL: разгон 100 → 150",
                "status": "active",
                "start_balance": 100,
                "target_balance": 150,
                "started_at": "2026-06-23 13:10:00",
                "archived_at": None,
                "note": "focus session",
                "realized_pnl": -10,
            }
        ],
        trades=[
            {
                "id": 12,
                "session_id": 7,
                "symbol": "SOLUSDT",
                "side": "long",
                "status": "open",
                "entry_price": 70.9,
                "stop_price": 69.8,
                "target_price": 73,
                "quantity": 1.4,
                "leverage": 1,
                "pnl": None,
                "opened_at": "2026-06-23 13:15:00",
                "closed_at": None,
                "note": "отбой от уровня",
            }
        ],
        journal_entries=[
            {
                "id": 55,
                "session_id": 7,
                "linked_trade_id": 12,
                "symbol": "SOLUSDT",
                "outcome": "idea",
                "theory": "trade-plan",
                "description": "описание входа",
                "screenshot_file_id": "telegram-file-id",
                "created_at": "2026-06-23 13:16:00",
            }
        ],
        generated_at=datetime(2026, 6, 23, tzinfo=timezone.utc),
    )

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        assert "Trading Assistant Export/Dashboard.md" in names
        assert "Trading Assistant Export/Canvas/Trading Map.canvas" in names
        trade_file = next(name for name in names if name.startswith("Trading Assistant Export/Trades/Trade 12"))
        trade_note = archive.read(trade_file).decode()

    assert "type: trade" in trade_note
    assert "symbol: SOLUSDT" in trade_note
    assert "[[2026-06-23 Session 7 SOL разгон 100 → 150]]" in trade_note


def test_safe_filename_removes_path_traversal_and_forbidden_characters() -> None:
    assert safe_filename("../bad/name:*?") == "bad name"


def test_obsidian_export_api_is_owner_scoped(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.users.ensure_user(42)
    module.users.ensure_user(99)
    owner_session = module.sessions.create(42, "Owner Session", 1000)
    module.trades.create(42, "BTCUSDT", "long", 100, 90, 120, 1, 1)
    module.journal.create(42, "BTCUSDT", "idea", "owner note")
    module.sessions.create(99, "Foreign Session", 1000)
    module.trades.create(99, "ETHUSDT", "short", 100, 110, 80, 1, 1)
    client = TestClient(module.app)

    forbidden_query = client.get("/api/export/obsidian.zip?user_id=99", headers=auth_header(42))
    assert forbidden_query.status_code == 400

    response = client.get("/api/export/obsidian.zip", headers=auth_header(42))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        dashboard = archive.read("Trading Assistant Export/Dashboard.md").decode()
        all_text = "\n".join(archive.read(name).decode(errors="ignore") for name in archive.namelist())

    assert "Trades: **1**" in dashboard
    assert "BTCUSDT" in all_text
    assert "ETHUSDT" not in all_text

    missing = client.get("/api/export/obsidian.zip?session_id=999", headers=auth_header(42))
    assert missing.status_code == 404

    scoped = client.get(f"/api/export/obsidian.zip?session_id={owner_session}", headers=auth_header(42))
    assert scoped.status_code == 200
