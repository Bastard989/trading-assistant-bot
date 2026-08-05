from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum


class RiskDirection(str, Enum):
    HIGHER_IS_WORSE = "higher_is_worse"
    LOWER_IS_WORSE = "lower_is_worse"
    TWO_SIDED = "two_sided"


class IndicatorBand(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    DANGER = "danger"
    CRITICAL = "critical"


class MarketStage(str, Enum):
    INSUFFICIENT_DATA = "insufficient_data"
    STABLE = "stable"
    TENSION = "tension"
    WARNING = "warning"
    CONFIRMATION = "confirmation"
    CRISIS = "crisis"


class ScenarioStatus(str, Enum):
    UNKNOWN = "unknown"
    INACTIVE = "inactive"
    WATCH = "watch"
    ELEVATED = "elevated"
    CONFIRMED = "confirmed"


class ScenarioConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QualityFlag(str, Enum):
    RELEASE_TIME_ESTIMATED = "release_time_estimated"
    RETROSPECTIVE_REVISED = "retrospective_revised"
    PROVISIONAL = "provisional"
    REVISED = "revised"
    DELAYED = "delayed"


class DataFreshness(str, Enum):
    FRESH = "fresh"
    DELAYED = "delayed"
    STALE = "stale"
    MISSING = "missing"


class CoverageStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class CoverageAssessment:
    status: CoverageStatus
    ratio: Decimal
    expected_count: int
    available_count: int
    fresh_count: int
    delayed_count: int
    stale_count: int
    missing_count: int
    available_group_codes: frozenset[str]
    missing_required_groups: tuple[str, ...]
    missing_required_regions: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.ratio.is_finite() or not Decimal("0") <= self.ratio <= Decimal("1"):
            raise ValueError("coverage ratio must be between zero and one")
        counts = (
            self.expected_count,
            self.available_count,
            self.fresh_count,
            self.delayed_count,
            self.stale_count,
            self.missing_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("coverage counts must not be negative")
        if self.available_count != self.fresh_count + self.delayed_count:
            raise ValueError("available coverage count must equal fresh plus delayed")


@dataclass(frozen=True)
class IndicatorThresholds:
    warning: Decimal
    danger: Decimal
    critical: Decimal
    direction: RiskDirection
    reference: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        values = (self.warning, self.danger, self.critical, self.reference)
        if any(not value.is_finite() for value in values):
            raise ValueError("threshold values must be finite")
        if self.direction is RiskDirection.LOWER_IS_WORSE:
            ordered = self.warning > self.danger > self.critical
        else:
            ordered = self.warning < self.danger < self.critical
        if not ordered:
            raise ValueError("thresholds must become stricter from warning to critical")
        if self.direction is RiskDirection.TWO_SIDED and self.warning < 0:
            raise ValueError("two-sided thresholds must be non-negative deviations")


@dataclass(frozen=True)
class ThresholdEvaluation:
    band: IndicatorBand
    distance_to_next: Decimal | None
    evaluated_value: Decimal


@dataclass(frozen=True)
class Observation:
    indicator_code: str
    source_code: str
    value: Decimal
    unit: str
    observed_at: datetime
    released_at: datetime
    fetched_at: datetime
    vintage: str = ""
    quality_flags: frozenset[QualityFlag] = frozenset()
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.indicator_code.strip() or not self.source_code.strip() or not self.unit.strip():
            raise ValueError("observation codes and unit must not be empty")
        if not self.value.is_finite():
            raise ValueError("observation value must be finite")
        for field_name in ("observed_at", "released_at", "fetched_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.released_at > self.fetched_at:
            raise ValueError("released_at must not be later than fetched_at")


@dataclass(frozen=True)
class IndicatorState:
    indicator_code: str
    group_code: str
    band: IndicatorBand
    stress_score: Decimal
    distance_to_next: Decimal | None
    freshness: DataFreshness
    value: Decimal
    unit: str
    snapshot_at: datetime
    observation: Observation
    raw_band: IndicatorBand | None = None
    persistence_count: int = 1
    confirmation_required: int = 1
    held_by_hysteresis: bool = False


@dataclass(frozen=True)
class GroupState:
    group_code: str
    band: IndicatorBand
    stress_score: Decimal
    indicator_count: int
    worsening_count: int
    contributors: tuple[str, ...]


@dataclass(frozen=True)
class MarketOverview:
    stage: MarketStage
    calculated_stage: MarketStage
    snapshot_at: datetime
    groups: tuple[GroupState, ...]
    active_group_count: int
    warning_group_count: int
    danger_group_count: int
    critical_group_count: int
    explanation_ru: str
    explanation_en: str
    coverage: CoverageAssessment | None = None


@dataclass(frozen=True)
class ScenarioState:
    code: str
    status: ScenarioStatus
    confidence: ScenarioConfidence
    horizon: str
    active_group_count: int
    evidence: tuple[tuple[str, IndicatorBand], ...]
    explanation_ru: str
    explanation_en: str


@dataclass(frozen=True)
class FreshnessPolicy:
    max_age: timedelta
    delayed_multiplier: Decimal = Decimal("1.5")

    def __post_init__(self) -> None:
        if self.max_age <= timedelta(0):
            raise ValueError("freshness max_age must be positive")
        if not self.delayed_multiplier.is_finite() or self.delayed_multiplier <= 1:
            raise ValueError("delayed_multiplier must be finite and greater than one")
