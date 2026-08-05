from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.crisis_radar.coverage import ExpectedIndicator, assess_coverage
from trading_bot.crisis_radar.catalog import FRED_INDICATORS
from trading_bot.crisis_radar.domain import (
    CoverageStatus,
    IndicatorThresholds,
    MarketStage,
    Observation,
    RiskDirection,
    ScenarioStatus,
)
from trading_bot.crisis_radar.scenarios import build_scenario_states
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.states import build_indicator_state, build_market_overview
from trading_bot.db import Database


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
THRESHOLDS = IndicatorThresholds(
    warning=Decimal("1"),
    danger=Decimal("2"),
    critical=Decimal("3"),
    direction=RiskDirection.HIGHER_IS_WORSE,
)


def _state(code: str, group: str, *, days_old: int = 0):
    released = NOW - timedelta(days=days_old)
    return build_indicator_state(
        Observation(
            indicator_code=code,
            source_code="test",
            value=Decimal("0"),
            unit="index",
            observed_at=released,
            released_at=released,
            fetched_at=NOW,
        ),
        group_code=group,
        thresholds=THRESHOLDS,
        max_staleness_seconds=4 * 86400,
        snapshot_at=NOW,
    )


def test_coverage_gate_never_turns_missing_inputs_into_stable() -> None:
    expected = (
        ExpectedIndicator("market", "market_stress", "US"),
        ExpectedIndicator("credit", "credit", "US"),
        ExpectedIndicator("euro", "euro_financial_stress", "EU"),
        ExpectedIndicator("china", "china_leading_cycle", "CHINA"),
        ExpectedIndicator("global", "global_leading_cycle", "GLOBAL"),
        ExpectedIndicator("crypto", "crypto_price_stress", "CRYPTO"),
    )
    coverage = assess_coverage([_state("market", "market_stress")], expected=expected)
    overview = build_market_overview(
        [_state("market", "market_stress")], snapshot_at=NOW, coverage=coverage
    )

    assert coverage.status is CoverageStatus.INSUFFICIENT_DATA
    assert coverage.ratio == Decimal("0.1667")
    assert overview.calculated_stage is MarketStage.STABLE
    assert overview.stage is MarketStage.INSUFFICIENT_DATA


def test_delayed_values_are_discounted_and_reported() -> None:
    expected = tuple(
        ExpectedIndicator(f"signal_{index}", f"group_{index}", "US")
        for index in range(10)
    )
    states = [
        _state(item.code, item.group_code, days_old=5 if index < 5 else 0)
        for index, item in enumerate(expected)
    ]
    coverage = assess_coverage(
        states,
        expected=expected,
        required_groups=(),
        required_regions=("US",),
    )

    assert coverage.status is CoverageStatus.DEGRADED
    assert coverage.fresh_count == 5
    assert coverage.delayed_count == 5
    assert coverage.ratio == Decimal("0.8500")


def test_missing_scenario_anchor_is_unknown_not_inactive() -> None:
    states = build_scenario_states(
        (), available_group_codes=frozenset({"market_stress", "crypto_price_stress"})
    )
    by_code = {item.code: item for item in states}

    assert by_code["crypto_leverage_unwind"].status is ScenarioStatus.UNKNOWN
    assert by_code["oil_stagflation"].status is ScenarioStatus.UNKNOWN
    assert by_code["china_hard_landing"].status is ScenarioStatus.UNKNOWN


def test_service_persists_coverage_and_unknown_scenarios(tmp_path) -> None:
    service = CrisisRadarService(
        CrisisRadarRepository(Database(tmp_path / "coverage.sqlite3")),
        feature_flags=CrisisRadarFeatureFlags(coverage_gate=True),
    )
    service.bootstrap()
    seed = FRED_INDICATORS[0]
    service.repository.save_observation(
        Observation(
            indicator_code=seed.code,
            source_code="fred",
            value=Decimal("0.1"),
            unit=seed.unit,
            observed_at=NOW,
            released_at=NOW,
            fetched_at=NOW,
        )
    )

    computed = service.recompute(snapshot_at=NOW)
    payload = service.overview(locale="en")

    assert computed is not None
    assert computed.stage is MarketStage.INSUFFICIENT_DATA
    assert payload["stage"] == "insufficient_data"
    assert payload["calculated_stage"] == "stable"
    assert payload["coverage"]["enabled"] is True
    assert payload["coverage"]["status"] == "insufficient_data"
    assert payload["coverage"]["expected_count"] == 23
    assert {item["status"] for item in payload["scenarios"]} == {"unknown"}
    opportunity_payload = service.opportunities(as_of=NOW, locale="en")
    assert opportunity_payload["ideas"][0]["side"] == "wait"

    with service.repository.db.connect() as connection:
        methodology_id = connection.execute(
            "SELECT id FROM cr_methodology_versions WHERE version='starter-v8'"
        ).fetchone()[0]
        assert connection.execute("SELECT count(*) FROM cr_data_health_events").fetchone()[0] == 0
    assert service.repository.record_data_health_transition(
        methodology_id=methodology_id,
        snapshot_at=NOW + timedelta(hours=1),
        status="healthy",
        ratio=Decimal("1"),
        missing_groups=(),
        missing_regions=(),
        reason_codes=(),
    ) is True
    assert service.repository.enqueue_data_health_deliveries((42,)) == 1
    assert service.repository.pending_data_health_deliveries()[0].to_status == "healthy"
