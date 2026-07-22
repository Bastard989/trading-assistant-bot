from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.test_api_security import auth_header, load_test_app, mutation_headers
from trading_bot.crisis_radar.catalog import FRED_INDICATORS
from trading_bot.crisis_radar.domain import Observation


def test_crisis_radar_overview_requires_auth_and_reports_first_run(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    client = TestClient(module.app)

    assert client.get("/api/crisis-radar/overview").status_code == 401
    response = client.get("/api/crisis-radar/overview?locale=ru", headers=auth_header(42))
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert client.get("/api/crisis-radar/overview?locale=de", headers=auth_header(42)).status_code == 422


def test_crisis_radar_overview_returns_deterministic_snapshot(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    values = {"sahm_rule": "0.60", "us_hy_oas": "6.5", "vix": "35"}
    for seed in (item for item in FRED_INDICATORS if item.code in values):
        observation_times = [now]
        if seed.frequency not in {"monthly", "quarterly", "annual"}:
            observation_times.insert(0, now - timedelta(days=1))
        for observed_at in observation_times:
            module.crisis_radar.repository.save_observation(
                Observation(
                    indicator_code=seed.code,
                    source_code="fred",
                    value=Decimal(values[seed.code]),
                    unit=seed.unit,
                    observed_at=observed_at,
                    released_at=observed_at,
                    fetched_at=now,
                    vintage=observed_at.date().isoformat(),
                )
            )
    module.crisis_radar.recompute(snapshot_at=now)

    response = TestClient(module.app).get(
        "/api/crisis-radar/overview?locale=en",
        headers=auth_header(42),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["stage"] == "confirmation"
    assert payload["methodology"]["version"] == "starter-v8"
    assert payload["breadth"]["danger_or_worse"] == 3
    assert len(payload["indicators"]) == 3
    assert len(payload["scenarios"]) == 5
    assert payload["scenarios"][0]["status"] == "confirmed"
    assert "cross-group confirmation" in payload["explanation"]

    history = TestClient(module.app).get(
        "/api/crisis-radar/indicators/vix/history?limit=30",
        headers=auth_header(42),
    )
    assert history.status_code == 200
    assert history.json()["code"] == "vix"
    assert history.json()["points"][0]["value_text"] == "35"


def test_crisis_radar_history_requires_auth_and_validates_input(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    client = TestClient(module.app)

    assert client.get("/api/crisis-radar/indicators/vix/history").status_code == 401
    assert client.get(
        "/api/crisis-radar/indicators/unknown/history", headers=auth_header(42)
    ).status_code == 404
    assert client.get(
        "/api/crisis-radar/indicators/vix/history?limit=1", headers=auth_header(42)
    ).status_code == 422


def test_crisis_radar_calibration_is_authenticated_and_hides_missing_probability(
    monkeypatch, tmp_path
) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    client = TestClient(module.app)

    assert client.get(
        "/api/crisis-radar/scenarios/global_recession/calibration"
    ).status_code == 401
    response = client.get(
        "/api/crisis-radar/scenarios/global_recession/calibration",
        headers=auth_header(42),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ready": False,
        "scenario_code": "global_recession",
        "probability": None,
        "confidence": "insufficient",
        "reason": "no_completed_backtest",
        "historical_backtest": None,
    }
    assert client.get(
        "/api/crisis-radar/scenarios/unknown/calibration", headers=auth_header(42)
    ).status_code == 404
    assert client.get(
        "/api/crisis-radar/backtests/999", headers=auth_header(42)
    ).status_code == 404
    assert client.get(
        "/api/crisis-radar/replays/999", headers=auth_header(42)
    ).status_code == 404
    catalog = client.get(
        "/api/crisis-radar/scenarios/global_recession/event-catalog",
        headers=auth_header(42),
    )
    assert catalog.status_code == 200
    assert catalog.json()["scenario_code"] == "global_recession"
    assert len(catalog.json()["labels"]) == 5


def test_crisis_radar_calendar_requires_auth_and_validates_window(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    client = TestClient(module.app)

    assert client.get("/api/crisis-radar/calendar").status_code == 401
    response = client.get(
        "/api/crisis-radar/calendar?locale=en&days=15", headers=auth_header(42)
    )
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["events"] == []
    assert client.get(
        "/api/crisis-radar/calendar?days=91", headers=auth_header(42)
    ).status_code == 422


def test_crisis_radar_news_requires_auth_and_validates_bounds(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    client = TestClient(module.app)

    assert client.get("/api/crisis-radar/news").status_code == 401
    response = client.get(
        "/api/crisis-radar/news?locale=en&days=14&limit=20",
        headers=auth_header(42),
    )
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["items"] == []
    assert client.get(
        "/api/crisis-radar/news?days=91", headers=auth_header(42)
    ).status_code == 422
    assert client.get(
        "/api/crisis-radar/news?limit=51", headers=auth_header(42)
    ).status_code == 422


def test_crisis_agent_api_is_authenticated_bounded_and_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CRISIS_AGENT_ENABLED", "true")
    module = load_test_app(monkeypatch, tmp_path)
    calls = []

    class FakeAgent:
        client = SimpleNamespace(model="qwen3.5:9b")
        repository = module.crisis_agent.repository

        async def status(self):
            return {
                "provider": "ollama",
                "model": "qwen3.5:9b",
                "read_only": True,
                "available": True,
                "model_loaded": True,
            }

        async def ask(self, **kwargs):
            calls.append(kwargs)
            return {
                "thread_id": 7,
                "mode": kwargs["mode"],
                "messages": [{"role": "assistant", "content": "Read-only answer"}],
                "suggestions": [],
            }

    module.crisis_agent = FakeAgent()
    client = TestClient(module.app)
    assert client.get("/api/crisis-radar/agent/status").status_code == 401
    status = client.get("/api/crisis-radar/agent/status", headers=auth_header(42))
    assert status.json()["read_only"] is True
    assert status.json()["model_loaded"] is True
    assert status.json()["selection_source"] == "backend_environment"
    assert set(status.json()["task_bindings"]) == {
        "crisis_analysis",
        "vision_trade_extraction",
        "journal_summary",
        "obsidian_report",
        "trade_review",
    }
    assert status.json()["task_bindings"]["crisis_analysis"] == {
        "provider": "unknown",
        "model": "qwen3.5:9b",
        "status": "enabled",
        "read_only": True,
    }
    assert "api_key" not in status.text.lower()
    assert "private-test-key" not in status.text

    headers = mutation_headers(42, "crisis-agent-chat-1")
    first = client.post(
        "/api/crisis-radar/agent/chat",
        json={"question": "Что ухудшается?", "locale": "ru", "mode": "deep"},
        headers=headers,
    )
    replay = client.post(
        "/api/crisis-radar/agent/chat",
        json={"question": "Что ухудшается?", "locale": "ru", "mode": "deep"},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["mode"] == "deep"
    assert replay.headers["idempotency-replayed"] == "true"
    assert len(calls) == 1

    blank = client.post(
        "/api/crisis-radar/agent/chat",
        json={"question": "   ", "locale": "ru", "mode": "fast"},
        headers=mutation_headers(42, "crisis-agent-chat-2"),
    )
    assert blank.status_code == 422
    too_long = client.post(
        "/api/crisis-radar/agent/chat",
        json={"question": "x" * 1001},
        headers=mutation_headers(42, "crisis-agent-chat-3"),
    )
    assert too_long.status_code == 422


def test_crisis_agent_threads_do_not_cross_users(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.users.ensure_user(42)
    module.users.ensure_user(99)
    repository = module.crisis_agent.repository
    from trading_bot.crisis_radar.agent import AgentReply

    thread_id, _ = repository.save_exchange(
        user_id=42,
        thread_id=None,
        locale="ru",
        question="Тест",
        reply=AgentReply("Ответ", (), (), (), "qwen3.5:9b", 10),
        evidence=[],
    )
    client = TestClient(module.app)
    assert client.get(
        f"/api/crisis-radar/agent/threads/{thread_id}", headers=auth_header(99)
    ).status_code == 404
    assert client.get(
        f"/api/crisis-radar/agent/threads/{thread_id}", headers=auth_header(42)
    ).json()["messages"][1]["content"] == "Ответ"
