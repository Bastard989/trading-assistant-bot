from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from trading_bot.config import Settings
from trading_bot import main as main_module


REPOSITORY_NAMES = (
    "UserRepository",
    "IdempotencyRepository",
    "AlertRepository",
    "TradeRepository",
    "JournalRepository",
    "MarketContextRepository",
    "WatchlistRepository",
    "DailyPlanRepository",
    "PendingTradeRepository",
    "TradeReviewRepository",
    "TemplateRepository",
)


class FakeApplication:
    def __init__(self) -> None:
        self.polling_calls = []

    def run_polling(self, **kwargs) -> None:
        self.polling_calls.append(kwargs)


class FakeBuilder:
    def __init__(self, application: FakeApplication, calls: dict) -> None:
        self.application = application
        self.calls = calls

    def token(self, value):
        self.calls["token"] = value
        return self

    def post_init(self, callback):
        self.calls["post_init"] = callback
        return self

    def build(self):
        return self.application


def settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="123456:test-token",
        database_path=Path(tmp_path / "assistant.sqlite3"),
        market="futures",
        top_limit=10,
        alert_poll_seconds=30,
        web_app_url="https://trading.example.test",
        web_host="127.0.0.1",
        web_port=8080,
        allowed_telegram_user_ids=frozenset({42}),
        business_timezone="Europe/Moscow",
    )


def install_runtime_fakes(monkeypatch, tmp_path) -> tuple[dict, FakeApplication]:
    calls = {}
    application = FakeApplication()
    monkeypatch.setattr(main_module, "load_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(main_module, "Database", lambda path, auto_migrate: (path, auto_migrate))
    for name in REPOSITORY_NAMES:
        monkeypatch.setattr(
            main_module,
            name,
            lambda database, repository_name=name: (repository_name, database),
        )
    monkeypatch.setattr(main_module, "MarketClient", lambda market: ("market", market))
    monkeypatch.setattr(
        main_module,
        "OpenAIPhotoTradeExtractor",
        lambda **kwargs: calls.setdefault("photo", kwargs),
    )
    monkeypatch.setattr(
        main_module,
        "ApplicationBuilder",
        lambda: FakeBuilder(application, calls),
    )

    class Handlers:
        def __init__(self, **kwargs) -> None:
            calls["handlers"] = kwargs

        def register(self, target) -> None:
            calls["handlers_registered"] = target

    monkeypatch.setattr(main_module, "BotHandlers", Handlers)
    return calls, application


def test_post_init_sets_declared_bot_commands() -> None:
    bot = SimpleNamespace(set_my_commands=lambda commands: asyncio.sleep(0, result=commands))
    application = SimpleNamespace(bot=bot)

    asyncio.run(main_module.post_init(application))


def test_main_builds_bot_without_optional_radar(tmp_path, monkeypatch) -> None:
    calls, application = install_runtime_fakes(monkeypatch, tmp_path)
    monkeypatch.setenv("CRISIS_RADAR_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_VISION_MODEL", "vision-model")

    main_module.main()

    assert calls["token"] == "123456:test-token"
    assert calls["handlers"]["allowed_user_ids"] == frozenset({42})
    assert calls["photo"]["model"] == "vision-model"
    assert calls["handlers_registered"] is application
    assert application.polling_calls == [{"allowed_updates": None}]
    assert "radar_jobs" not in calls


def test_main_registers_radar_with_keys_and_minimum_interval(tmp_path, monkeypatch) -> None:
    calls, application = install_runtime_fakes(monkeypatch, tmp_path)
    monkeypatch.setenv("CRISIS_RADAR_ENABLED", "true")
    monkeypatch.setenv("CRISIS_RADAR_SYNC_MINUTES", "1")
    monkeypatch.setenv("FRED_API_KEY", "fred-secret")
    monkeypatch.setenv("BEA_API_KEY", "bea-secret")
    monkeypatch.setenv("EIA_API_KEY", "eia-secret")
    monkeypatch.setattr(main_module, "CrisisRadarRepository", lambda database: ("cr", database))
    monkeypatch.setattr(
        main_module,
        "build_evidence_pipeline_from_environment",
        lambda: "evidence-pipeline",
    )

    class RadarService:
        def __init__(self, repository, *, evidence_pipeline) -> None:
            calls["radar_service"] = (repository, evidence_pipeline)

    class RadarJobs:
        def __init__(self, service, **kwargs) -> None:
            calls["radar_jobs"] = (service, kwargs)

        def register(self, target, *, interval_seconds) -> None:
            calls["radar_register"] = (target, interval_seconds)

    monkeypatch.setattr(main_module, "CrisisRadarService", RadarService)
    monkeypatch.setattr(main_module, "CrisisRadarJobs", RadarJobs)

    main_module.main()

    assert calls["radar_service"][1] == "evidence-pipeline"
    assert calls["radar_jobs"][1]["fred_api_key"] == "fred-secret"
    assert calls["radar_jobs"][1]["alert_user_ids"] == (42,)
    assert calls["radar_register"] == (application, 300)
