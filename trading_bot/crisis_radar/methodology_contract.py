from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from trading_bot.crisis_radar.scoring_v2 import (
    AVAILABILITY_FACTORS,
    HISTORICAL_MAD_Z_DENOMINATOR,
    HISTORICAL_PERCENTILE_KNOTS,
    INDICATOR_BAND_THRESHOLDS,
    MOVEMENT_PERCENT_DENOMINATOR,
    MOVEMENT_VOLATILITY_DENOMINATOR,
    PERSISTENCE_OBSERVATION_CAP,
    PROFILES,
    SCORING_VERSION,
)
from trading_bot.crisis_radar.stage_v2 import (
    ACTIVE_SCORE_THRESHOLD,
    BREADTH_WEIGHTS,
    DEPENDENCY_GRAPH_VERSION,
    GROUP_SCORE_WEIGHTS,
    INTENSITY_WEIGHTS,
    RECOVERY_INTENSITY_DROP,
    RECOVERY_MAX_BREADTH,
    STAGE_GATES,
    STAGE_VERSION,
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def runtime_methodology_contract() -> dict[str, Any]:
    """Return the machine-readable contract of the live v11 shadow formulas."""

    profiles = {}
    for code, profile in PROFILES.items():
        payload = asdict(profile)
        profiles[code] = _json_value(payload)
    return {
        "schema_version": 1,
        "status": "shadow",
        "versions": {
            "indicator_scoring": SCORING_VERSION,
            "stage": STAGE_VERSION,
            "dependency_graph": DEPENDENCY_GRAPH_VERSION,
        },
        "indicator": {
            "band_thresholds": _json_value(INDICATOR_BAND_THRESHOLDS),
            "historical_percentile_knots": _json_value(
                HISTORICAL_PERCENTILE_KNOTS
            ),
            "historical_mad_z_denominator": _json_value(
                HISTORICAL_MAD_Z_DENOMINATOR
            ),
            "movement_percent_denominator": _json_value(
                MOVEMENT_PERCENT_DENOMINATOR
            ),
            "movement_volatility_denominator": _json_value(
                MOVEMENT_VOLATILITY_DENOMINATOR
            ),
            "persistence_observation_cap": _json_value(
                PERSISTENCE_OBSERVATION_CAP
            ),
            "availability_factors": {
                freshness.value: _json_value(factor)
                for freshness, factor in AVAILABILITY_FACTORS.items()
            },
            "profiles": profiles,
        },
        "aggregation": {
            "active_score_threshold": _json_value(ACTIVE_SCORE_THRESHOLD),
            "group_score_weights": _json_value(GROUP_SCORE_WEIGHTS),
            "intensity_weights": _json_value(INTENSITY_WEIGHTS),
            "breadth_weights": _json_value(BREADTH_WEIGHTS),
            "stage_gates": _json_value(STAGE_GATES),
            "recovery": {
                "minimum_intensity_drop": _json_value(
                    RECOVERY_INTENSITY_DROP
                ),
                "maximum_breadth": _json_value(RECOVERY_MAX_BREADTH),
            },
        },
    }
