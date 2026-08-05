from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.crisis_radar.domain import (
    CoverageStatus,
    GroupState,
    IndicatorBand,
    ScenarioStatus,
)
from trading_bot.crisis_radar.scenario_fusion import fuse_scenario
from trading_bot.crisis_radar.scenarios import SCENARIOS


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def group(code: str, band: IndicatorBand, score: str) -> GroupState:
    return GroupState(code, band, Decimal(score), 2, int(band is not IndicatorBand.NORMAL), (code,))


def fuse(groups=(), events=(), *, coverage=CoverageStatus.HEALTHY, ratio="1"):
    return fuse_scenario(
        SCENARIOS[0],
        groups=tuple(groups),
        features=(),
        indicator_groups={},
        contagion=None,
        events=tuple(events),
        snapshot_at=NOW,
        coverage_status=coverage,
        coverage_ratio=Decimal(ratio),
        available_group_codes=frozenset(item.group_code for item in groups),
    )


def test_discovery_news_alone_can_only_create_watch() -> None:
    state = fuse(
        groups=[group(code, IndicatorBand.NORMAL, "0") for code in SCENARIOS[0].group_codes],
        events=[
            {
                "id": 1,
                "taxonomy": "recession_signal",
                "status": "discovery",
                "event_score": "1",
                "source_count": 1,
                "official_source_count": 0,
            }
        ],
    )
    assert state.status is ScenarioStatus.WATCH
    assert state.news.score <= Decimal("5")
    assert state.strength < Decimal("20")


def test_confirmed_requires_independent_numeric_clusters() -> None:
    one_cluster = fuse(
        groups=[
            group("labor", IndicatorBand.CRITICAL, "1"),
            group("real_economy", IndicatorBand.CRITICAL, "1"),
            *[
                group(code, IndicatorBand.NORMAL, "0")
                for code in SCENARIOS[0].group_codes
                if code not in {"labor", "real_economy"}
            ],
        ]
    )
    assert one_cluster.independent_numeric_clusters == 1
    assert one_cluster.status is not ScenarioStatus.CONFIRMED

    independent = fuse(
        groups=[
            group("labor", IndicatorBand.CRITICAL, "1"),
            group("credit", IndicatorBand.CRITICAL, "1"),
            group("china_leading_cycle", IndicatorBand.CRITICAL, "1"),
            *[
                group(code, IndicatorBand.NORMAL, "0")
                for code in SCENARIOS[0].group_codes
                if code not in {"labor", "credit", "china_leading_cycle"}
            ],
        ]
    )
    assert independent.independent_numeric_clusters == 3
    assert independent.status in {ScenarioStatus.ELEVATED, ScenarioStatus.CONFIRMED}


def test_insufficient_coverage_is_unknown_even_with_normal_groups() -> None:
    state = fuse(
        groups=[group(code, IndicatorBand.NORMAL, "0") for code in SCENARIOS[0].group_codes],
        coverage=CoverageStatus.INSUFFICIENT_DATA,
        ratio="0.40",
    )
    assert state.status is ScenarioStatus.UNKNOWN
    assert "не оценивается" in state.explanation_ru


def test_fusion_is_deterministic() -> None:
    groups = [group(code, IndicatorBand.WARNING, "0.5") for code in SCENARIOS[0].group_codes]
    left = fuse(groups=groups)
    right = fuse(groups=groups)
    assert left.input_checksum == right.input_checksum
    assert left.strength == right.strength
