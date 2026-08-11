from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from playwright.sync_api import sync_playwright

from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(base_url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("browser E2E server exited before readiness")
        try:
            if httpx.get(f"{base_url}/health/live", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    raise RuntimeError("browser E2E server did not become ready")


def test_authenticated_crisis_radar_browser_ru_en_mobile_and_degraded(tmp_path) -> None:
    database_path = tmp_path / "browser-e2e.sqlite3"
    repository = CrisisRadarRepository(Database(database_path))
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            coverage_gate=True,
            global_sources_v2=True,
            trend_engine_v2=True,
            news_events_v2=True,
            scoring_v11=True,
        ),
    )
    service.bootstrap()
    now = datetime.now(timezone.utc)
    repository.save_observation(
        Observation(
            indicator_code="vix",
            source_code="fred",
            value=Decimal("24"),
            unit="index_points",
            observed_at=now,
            released_at=now,
            fetched_at=now,
            vintage=now.date().isoformat(),
        )
    )
    service.recompute(snapshot_at=now)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "APP_ENV": "development",
        "ENABLE_DEV_AUTH": "true",
        "ALLOWED_TELEGRAM_USER_IDS": "1",
        "DATABASE_PATH": str(database_path),
        "AUTO_MIGRATE": "false",
        "CRISIS_RADAR_ENABLED": "true",
        "CRISIS_RADAR_V2_ENABLED": "true",
        "CRISIS_RADAR_SCORING_V11_ENABLED": "true",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "trading_bot.web_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-server-header",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(base_url, process)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(extra_http_headers={"X-Dev-User-Id": "1"})
            page = context.new_page()
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="Кризис-радар", exact=True).click()
            page.get_by_role("heading", name="Что происходит с мировым рынком").wait_for()
            page.get_by_role("button", name="Разобрать", exact=True).click()
            analysis = page.get_by_role("navigation", name="Разделы подробного анализа")
            analysis.wait_for()
            assert analysis.get_by_role("button").count() == 6
            analysis.get_by_role("button", name="Сценарии", exact=True).click()
            scenario_sections = page.locator('[data-crisis-panel="scenarios"]')
            scenario_sections.first.wait_for(state="visible")
            assert page.locator('[data-crisis-panel="signals"]').first.is_hidden()
            scenario_sections.last.locator('[data-action="crisis-help"]').click()
            assert page.get_by_role("dialog").is_visible()
            page.get_by_role("button", name="Закрыть").click()

            page.get_by_role("button", name="EN", exact=True).click()
            page.get_by_role("navigation", name="Detailed analysis sections").wait_for()
            assert page.get_by_role("button", name="Scenarios", exact=True).count() >= 1
            page.set_viewport_size({"width": 390, "height": 844})
            width = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
            assert width == [390, 390]

            degraded_context = browser.new_context(
                extra_http_headers={"X-Dev-User-Id": "1"}
            )
            degraded = degraded_context.new_page()
            degraded.set_viewport_size({"width": 390, "height": 844})
            degraded.route(
                "**/api/crisis-radar/overview?**",
                lambda route: route.fulfill(status=503, body='{"detail":"offline"}'),
            )
            degraded.goto(base_url, wait_until="networkidle")
            degraded.get_by_role("button", name="Кризис-радар", exact=True).click()
            degraded.get_by_text("Crisis Radar пока недоступен", exact=False).wait_for()
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
