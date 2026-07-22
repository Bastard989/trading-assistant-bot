from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.crisis_radar.domain import (
    GroupState,
    IndicatorBand,
    ScenarioConfidence,
    ScenarioState,
    ScenarioStatus,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.scenarios import build_scenario_states
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


def _group(code: str, band: IndicatorBand) -> GroupState:
    return GroupState(code, band, Decimal("0.5"), 1, int(band is not IndicatorBand.NORMAL), ())


def test_scenarios_require_cross_group_confirmation_and_respect_anchor() -> None:
    scenarios = {item.code: item for item in build_scenario_states(())}
    assert scenarios["global_recession"].status is ScenarioStatus.INACTIVE
    assert scenarios["global_recession"].confidence is ScenarioConfidence.LOW

    scenarios = {
        item.code: item
        for item in build_scenario_states(
            (
                _group("labor", IndicatorBand.DANGER),
                _group("credit", IndicatorBand.DANGER),
                _group("real_economy", IndicatorBand.NORMAL),
                _group("euro_growth", IndicatorBand.NORMAL),
                _group("inflation_commodities", IndicatorBand.NORMAL),
            )
        )
    }
    assert scenarios["global_recession"].status is ScenarioStatus.CONFIRMED
    assert scenarios["oil_stagflation"].status is ScenarioStatus.INACTIVE


def test_scenario_transition_creates_one_retryable_delivery(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "scenarios.sqlite3"))
    bootstrapped = CrisisRadarService(repository).bootstrap()
    methodology_id = int(bootstrapped["methodology_id"])
    first = datetime(2026, 7, 20, tzinfo=timezone.utc)

    def state(status: ScenarioStatus) -> ScenarioState:
        return ScenarioState(
            code="financial_stress",
            status=status,
            confidence=ScenarioConfidence.HIGH,
            horizon="24h-3m",
            active_group_count=2 if status is ScenarioStatus.ELEVATED else 0,
            evidence=(),
            explanation_ru="Проверка",
            explanation_en="Check",
        )

    repository.save_scenario_snapshot(
        (state(ScenarioStatus.INACTIVE),), methodology_id=methodology_id, snapshot_at=first
    )
    repository.save_scenario_snapshot(
        (state(ScenarioStatus.ELEVATED),),
        methodology_id=methodology_id,
        snapshot_at=first + timedelta(hours=1),
    )
    repository.save_scenario_snapshot(
        (state(ScenarioStatus.ELEVATED),),
        methodology_id=methodology_id,
        snapshot_at=first + timedelta(hours=1),
    )

    assert repository.enqueue_alert_deliveries((42, 42)) == 1
    pending = repository.pending_alert_deliveries()
    assert len(pending) == 1
    assert pending[0].to_state == "elevated"
    repository.mark_alert_failed(
        pending[0].delivery_id,
        error="temporary",
        retry_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert len(repository.pending_alert_deliveries()) == 1
    repository.mark_alert_sent(pending[0].delivery_id, sent_at=datetime.now(timezone.utc))
    assert repository.pending_alert_deliveries() == []


def test_alert_cooldown_suppresses_repeat_but_allows_more_severe_event(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "cooldown.sqlite3"))
    methodology_id = int(CrisisRadarService(repository).bootstrap()["methodology_id"])
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)

    def state(status: ScenarioStatus) -> ScenarioState:
        return ScenarioState(
            code="financial_stress",
            status=status,
            confidence=ScenarioConfidence.HIGH,
            horizon="24h-3m",
            active_group_count=2,
            evidence=(),
            explanation_ru="Проверка cooldown",
            explanation_en="Cooldown check",
        )

    for hour, status in enumerate(
        (
            ScenarioStatus.INACTIVE,
            ScenarioStatus.ELEVATED,
            ScenarioStatus.INACTIVE,
            ScenarioStatus.ELEVATED,
            ScenarioStatus.CONFIRMED,
        )
    ):
        repository.save_scenario_snapshot(
            (state(status),),
            methodology_id=methodology_id,
            snapshot_at=start + timedelta(hours=hour),
        )

    with repository.db.connect() as connection:
        events = connection.execute(
            "SELECT event_type, severity FROM cr_alert_events ORDER BY snapshot_at"
        ).fetchall()
    assert [tuple(row) for row in events] == [
        ("scenario_escalation", "warning"),
        ("scenario_recovery", "info"),
        ("scenario_escalation", "critical"),
    ]
