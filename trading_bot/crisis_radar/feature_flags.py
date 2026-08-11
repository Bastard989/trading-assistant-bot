from __future__ import annotations

import os
from dataclasses import dataclass


def _enabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CrisisRadarFeatureFlags:
    """Fail-closed switches used to release Radar v2 components independently."""

    coverage_gate: bool = False
    thresholds_v2: bool = False
    global_sources_v2: bool = False
    news_events_v2: bool = False
    evidence_memory_v2: bool = False
    trend_engine_v2: bool = False
    scenario_fusion_v2: bool = False
    scoring_v11: bool = False

    @classmethod
    def from_environment(cls) -> "CrisisRadarFeatureFlags":
        master = _enabled("CRISIS_RADAR_V2_ENABLED", default=False)
        return cls(
            coverage_gate=_enabled("CRISIS_RADAR_COVERAGE_GATE_ENABLED", default=master),
            thresholds_v2=_enabled("CRISIS_RADAR_THRESHOLDS_V2_ENABLED", default=master),
            global_sources_v2=_enabled("CRISIS_RADAR_GLOBAL_SOURCES_V2_ENABLED", default=master),
            news_events_v2=_enabled("CRISIS_RADAR_NEWS_EVENTS_V2_ENABLED", default=master),
            evidence_memory_v2=_enabled("CRISIS_RADAR_EVIDENCE_MEMORY_V2_ENABLED", default=master),
            trend_engine_v2=_enabled("CRISIS_RADAR_TREND_ENGINE_V2_ENABLED", default=master),
            scenario_fusion_v2=_enabled("CRISIS_RADAR_SCENARIO_FUSION_V2_ENABLED", default=master),
            scoring_v11=_enabled("CRISIS_RADAR_SCORING_V11_ENABLED", default=master),
        )

    def as_dict(self) -> dict[str, bool]:
        return {
            "coverage_gate": self.coverage_gate,
            "thresholds_v2": self.thresholds_v2,
            "global_sources_v2": self.global_sources_v2,
            "news_events_v2": self.news_events_v2,
            "evidence_memory_v2": self.evidence_memory_v2,
            "trend_engine_v2": self.trend_engine_v2,
            "scenario_fusion_v2": self.scenario_fusion_v2,
            "scoring_v11": self.scoring_v11,
        }
