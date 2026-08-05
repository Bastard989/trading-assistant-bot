from pathlib import Path


ROOT = Path(__file__).parents[1]
INDEX_HTML = (ROOT / "mini_app" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "mini_app" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "mini_app" / "styles.css").read_text(encoding="utf-8")


def test_crisis_radar_is_a_first_class_mini_app_tab() -> None:
    assert 'data-view="crisis-radar"' in INDEX_HTML
    assert 'id="crisis-radar"' in INDEX_HTML
    assert "/api/crisis-radar/overview" in APP_JS
    assert "/api/crisis-radar/calendar" in APP_JS
    assert "/api/crisis-radar/news" in APP_JS
    assert "/api/crisis-radar/world" in APP_JS
    assert "/api/crisis-radar/sources/health" in APP_JS
    assert "/api/crisis-radar/opportunities" in APP_JS
    assert "/api/crisis-radar/events" in APP_JS
    assert "/api/crisis-radar/scenarios/fusion" in APP_JS
    assert "/api/crisis-radar/trends" in APP_JS
    assert "/api/crisis-radar/agent/status" in APP_JS
    assert "/api/crisis-radar/agent/chat" in APP_JS
    assert 'id="crisisAgentForm"' in INDEX_HTML
    assert "Это объяснение данных, а не финансовый совет" in INDEX_HTML
    assert 'id="crisisCalendar"' in INDEX_HTML
    assert 'id="crisisNews"' in INDEX_HTML
    assert 'id="crisisWorld"' in INDEX_HTML
    assert 'id="crisisOpportunities"' in INDEX_HTML


def test_crisis_radar_supports_ru_en_and_progressive_disclosure() -> None:
    assert 'data-app-locale="ru"' in INDEX_HTML
    assert 'data-app-locale="en"' in INDEX_HTML
    assert "let crisisLocale = appLocale" in APP_JS
    assert 'id="crisisTechnical"' in INDEX_HTML
    assert 'data-crisis-level="analysis" hidden' in INDEX_HTML
    assert 'data-crisis-level="methodology" hidden' in INDEX_HTML
    assert 'id="crisisTopSignals"' in INDEX_HTML
    assert 'id="crisisNextEvent"' in INDEX_HTML
    assert 'id="crisisLeadScenario"' in INDEX_HTML
    assert 'id="crisisBestOpportunity"' in INDEX_HTML
    assert 'data-action="crisis-help"' in INDEX_HTML
    assert 'id="crisisHelpDialog"' in INDEX_HTML
    assert "renderCrisisToday" in APP_JS
    assert "openCrisisIndicator" in APP_JS
    assert "crisisDetailed" in APP_JS
    assert 'let crisisViewLevel = "overview"' in APP_JS
    assert "crisisCopy" in APP_JS
    assert 'id="crisisCoverage"' in INDEX_HTML
    assert "insufficient_data" in APP_JS
    assert '["24h", "7d", "15d"]' in APP_JS


def test_crisis_radar_renders_threshold_context_and_mobile_layout() -> None:
    assert "distance_to_next_text" in APP_JS
    assert "item.thresholds" in APP_JS
    assert ".crisis-thresholds" in STYLES
    assert "@media (max-width: 860px)" in STYLES
    assert 'id="crisisChanges"' in INDEX_HTML
    assert "toggle-crisis-history" in APP_JS
    assert "/history?limit=500" in APP_JS
    assert "drawCrisisHistory" in APP_JS
    assert "event_windows" in APP_JS
    assert "crisis-event-window" in APP_JS
    assert ".crisis-history-chart" in STYLES
    assert "item.persistence_count" in APP_JS
    assert "item.held_by_hysteresis" in APP_JS
    assert ".crisis-stability-note" in STYLES
    assert ".crisis-news-item" in STYLES
    assert "const explanation = [...new Set(" in APP_JS
    assert "g20_cli_6m_change" in APP_JS
    assert "china_cli_6m_change" in APP_JS


def test_crisis_radar_does_not_present_a_magic_crash_probability() -> None:
    overview_renderer = APP_JS.split("function renderCrisisRadar", 1)[1].split(
        "function loadCrisisRadar", 1
    )[0]
    assert "probability" not in overview_renderer.lower()


def test_crisis_radar_renders_joint_scenarios_in_both_locales() -> None:
    assert 'id="crisisScenarios"' in INDEX_HTML
    assert "crisisScenarioLabels" in APP_JS
    assert "active_group_count" in APP_JS
    assert ".crisis-scenario-grid" in STYLES


def test_world_and_opportunity_views_are_honest_and_calculator_connected() -> None:
    assert "available_asset_classes" in APP_JS
    assert "execution_allowed" not in INDEX_HTML
    assert "opportunity-calculator" in APP_JS
    assert "transferOpportunityToCalculator" in APP_JS
    assert ".crisis-opportunity-grid" in STYLES
    assert ".crisis-world-grid" in STYLES
    assert "No trade is opened automatically" in APP_JS


def test_model_view_reports_backend_bindings_without_collecting_secrets() -> None:
    assert 'type="password"' not in INDEX_HTML
    assert "Mini App не принимает API-ключ" in INDEX_HTML
    assert 'id="bindingVision"' in INDEX_HTML
    assert 'id="bindingCrisis"' in INDEX_HTML
    assert "state.task_bindings" in APP_JS
    assert "selection_source" not in INDEX_HTML
