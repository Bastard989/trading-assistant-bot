from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.crisis_radar.domain import Observation, RiskDirection
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.trends import (
    TimePoint,
    calculate_contagion,
    calculate_indicator_features,
)
from trading_bot.db import Database


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _points(values: list[str], *, start: datetime | None = None) -> tuple[TimePoint, ...]:
    origin = start or NOW - timedelta(days=len(values) - 1)
    return tuple(
        TimePoint(origin + timedelta(days=index), origin + timedelta(days=index), Decimal(value))
        for index, value in enumerate(values)
    )


def test_features_include_15_day_window_and_never_read_future_points() -> None:
    history = _points([str(index) for index in range(40)])
    future = TimePoint(NOW + timedelta(days=1), NOW + timedelta(days=1), Decimal("-1000"))

    baseline = calculate_indicator_features(
        "vix", history, snapshot_at=NOW, direction=RiskDirection.HIGHER_IS_WORSE
    )
    with_future = calculate_indicator_features(
        "vix", history + (future,), snapshot_at=NOW, direction=RiskDirection.HIGHER_IS_WORSE
    )

    assert baseline.input_checksum == with_future.input_checksum
    assert baseline.windows["15d"].observation_count == 16
    assert baseline.windows["30d"].robust_slope_per_day == Decimal("1.0000")
    assert baseline.worsening_score > 0


def test_yield_curve_state_machine_detects_resteepening_after_long_inversion() -> None:
    inverted = _points(["-0.5"] * 70, start=NOW - timedelta(days=70))
    resteepened = inverted + (
        TimePoint(NOW, NOW, Decimal("0.1")),
    )

    features = calculate_indicator_features(
        "us_10y2y_spread",
        resteepened,
        snapshot_at=NOW,
        direction=RiskDirection.LOWER_IS_WORSE,
    )

    assert features.state_machine == "resteepening_after_long_inversion"


def test_contagion_measures_breadth_and_stress_correlation() -> None:
    values = []
    level = 0
    for index in range(30):
        level += 1 + index % 4
        values.append(level)
    left = _points([str(value) for value in values])
    right = _points([str(value * 2) for value in values])
    left_features = calculate_indicator_features(
        "left", left, snapshot_at=NOW, direction=RiskDirection.HIGHER_IS_WORSE
    )
    right_features = calculate_indicator_features(
        "right", right, snapshot_at=NOW, direction=RiskDirection.HIGHER_IS_WORSE
    )

    contagion = calculate_contagion(
        (left_features, right_features),
        {"left": left, "right": right},
        snapshot_at=NOW,
    )

    assert contagion.breadth == Decimal("1.0000")
    assert contagion.mean_absolute_correlation == Decimal("1.0000")
    assert contagion.stress_correlation_regime == "high"


def test_trend_features_are_versioned_and_persisted_by_service(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "trends.sqlite3"))
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(trend_engine_v2=True),
    )
    service.bootstrap()
    for index in range(35):
        observed = NOW - timedelta(days=34 - index)
        for code, value in (("vix", 12 + index), ("us_hy_oas", 3 + index / 10)):
            repository.save_observation(
                Observation(
                    indicator_code=code,
                    source_code="fred",
                    value=Decimal(str(value)),
                    unit="index_points" if code == "vix" else "percent",
                    observed_at=observed,
                    released_at=observed,
                    fetched_at=observed,
                    vintage=observed.date().isoformat(),
                )
            )

    service.recompute(snapshot_at=NOW)
    payload = service.trends()

    assert payload["ready"] is True
    assert payload["feature_version"] == "trend-regime-v1"
    assert {item["code"] for item in payload["indicators"]} == {"us_hy_oas", "vix"}
    assert payload["indicators"][0]["lineage"]["future_observations_allowed"] is False
