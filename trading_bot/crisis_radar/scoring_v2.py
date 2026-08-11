from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum

from trading_bot.crisis_radar.domain import DataFreshness, IndicatorBand, RiskDirection
from trading_bot.crisis_radar.trends import IndicatorFeatures


SCORING_VERSION = "indicator-score-v2-seed-1"
ZERO = Decimal("0")
ONE = Decimal("1")
INDICATOR_BAND_THRESHOLDS = {
    "warning": Decimal(".25"),
    "danger": Decimal(".50"),
    "critical": Decimal(".75"),
}
HISTORICAL_PERCENTILE_KNOTS = (
    Decimal(".80"),
    Decimal(".95"),
    Decimal(".99"),
)
HISTORICAL_MAD_Z_DENOMINATOR = Decimal("3")
MOVEMENT_PERCENT_DENOMINATOR = Decimal("20")
MOVEMENT_VOLATILITY_DENOMINATOR = Decimal("3")
PERSISTENCE_OBSERVATION_CAP = Decimal("10")
AVAILABILITY_FACTORS = {
    DataFreshness.FRESH: ONE,
    DataFreshness.DELAYED: Decimal(".70"),
    DataFreshness.STALE: ZERO,
    DataFreshness.MISSING: ZERO,
}


class IndicatorAgreement(str, Enum):
    CONFIRMED_STRESS = "confirmed_stress"
    EARLY_ANOMALY = "early_anomaly"
    HIGH_LEVEL_STABILIZING = "high_level_stabilizing"
    MIXED = "mixed"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class ScoreProfile:
    code: str
    economic: Decimal
    historical: Decimal
    trend: Decimal
    acceleration: Decimal
    persistence: Decimal
    regime: Decimal
    minimum_history: int
    preferred_window: str

    def __post_init__(self) -> None:
        weights = (
            self.economic,
            self.historical,
            self.trend,
            self.acceleration,
            self.persistence,
            self.regime,
        )
        if any(value < ZERO for value in weights) or sum(weights) != ONE:
            raise ValueError("score profile weights must be non-negative and sum to one")
        if self.minimum_history < 2:
            raise ValueError("minimum history must contain at least two observations")


PROFILES = {
    "market_daily": ScoreProfile(
        "market_daily", Decimal(".25"), Decimal(".20"), Decimal(".20"),
        Decimal(".10"), Decimal(".10"), Decimal(".15"), 252, "30d"
    ),
    "market_intraday": ScoreProfile(
        "market_intraday", Decimal(".15"), Decimal(".20"), Decimal(".15"),
        Decimal(".15"), Decimal(".10"), Decimal(".25"), 500, "7d"
    ),
    "flow_weekly": ScoreProfile(
        "flow_weekly", Decimal(".30"), Decimal(".15"), Decimal(".20"),
        Decimal(".10"), Decimal(".15"), Decimal(".10"), 104, "90d"
    ),
    "macro_monthly": ScoreProfile(
        "macro_monthly", Decimal(".35"), Decimal(".15"), Decimal(".20"),
        Decimal(".10"), Decimal(".15"), Decimal(".05"), 60, "6m"
    ),
    "macro_quarterly": ScoreProfile(
        "macro_quarterly", Decimal(".45"), Decimal(".15"), Decimal(".15"),
        Decimal(".05"), Decimal(".15"), Decimal(".05"), 24, "12m"
    ),
    "structural_annual": ScoreProfile(
        "structural_annual", Decimal(".55"), Decimal(".15"), Decimal(".10"),
        Decimal(".05"), Decimal(".10"), Decimal(".05"), 15, "12m"
    ),
    "two_sided_leverage": ScoreProfile(
        "two_sided_leverage", Decimal(".15"), Decimal(".20"), Decimal(".15"),
        Decimal(".15"), Decimal(".10"), Decimal(".25"), 252, "30d"
    ),
    "event_reactive": ScoreProfile(
        "event_reactive", Decimal(".25"), Decimal(".10"), Decimal(".15"),
        Decimal(".10"), Decimal(".10"), Decimal(".30"), 30, "30d"
    ),
}


@dataclass(frozen=True)
class IndicatorScoreV2:
    indicator_code: str
    profile: str
    economic_score: Decimal
    economic_band: IndicatorBand
    historical_score: Decimal | None
    historical_band: IndicatorBand | None
    trend_score: Decimal
    acceleration_score: Decimal
    persistence_score: Decimal
    regime_score: Decimal
    data_quality: Decimal
    availability: Decimal
    effective_score: Decimal | None
    effective_band: IndicatorBand | None
    agreement: IndicatorAgreement
    history_count: int
    lineage: dict
    input_checksum: str


SCORING_VARIANTS = (
    "economic_only",
    "historical_only",
    "full",
    "without_trend",
    "without_events",
    "without_contagion",
    "without_dependency_correction",
)


def _clamp(value: Decimal) -> Decimal:
    return max(ZERO, min(ONE, value))


def _quantize(value: Decimal) -> Decimal:
    return _clamp(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def band_from_score(score: Decimal) -> IndicatorBand:
    if score >= INDICATOR_BAND_THRESHOLDS["critical"]:
        return IndicatorBand.CRITICAL
    if score >= INDICATOR_BAND_THRESHOLDS["danger"]:
        return IndicatorBand.DANGER
    if score >= INDICATOR_BAND_THRESHOLDS["warning"]:
        return IndicatorBand.WARNING
    return IndicatorBand.NORMAL


def profile_for(*, frequency: str, direction: RiskDirection, code: str) -> ScoreProfile:
    if direction is RiskDirection.TWO_SIDED and ("funding" in code or "oi_" in code):
        return PROFILES["two_sided_leverage"]
    normalized = frequency.strip().lower()
    if normalized in {"intraday", "funding_interval", "hourly"}:
        return PROFILES["market_intraday"]
    if normalized in {"daily", "business_daily"}:
        return PROFILES["market_daily"]
    if normalized == "weekly":
        return PROFILES["flow_weekly"]
    if normalized == "monthly":
        return PROFILES["macro_monthly"]
    if normalized == "quarterly":
        return PROFILES["macro_quarterly"]
    if normalized == "annual":
        return PROFILES["structural_annual"]
    return PROFILES["event_reactive"]


def _oriented(value: Decimal, direction: RiskDirection) -> Decimal:
    if direction is RiskDirection.LOWER_IS_WORSE:
        return -value
    if direction is RiskDirection.TWO_SIDED:
        return abs(value)
    return value


def _historical_score(
    features: IndicatorFeatures,
    *,
    direction: RiskDirection,
) -> Decimal | None:
    if features.percentile is None and features.mad_zscore is None:
        return None
    percentile = features.percentile
    if percentile is None:
        percentile_component = ZERO
    else:
        adverse = (
            ONE - percentile
            if direction is RiskDirection.LOWER_IS_WORSE
            else max(percentile, ONE - percentile)
            if direction is RiskDirection.TWO_SIDED
            else percentile
        )
        p80, p95, p99 = HISTORICAL_PERCENTILE_KNOTS
        if adverse < p80:
            percentile_component = ZERO
        elif adverse < p95:
            percentile_component = INDICATOR_BAND_THRESHOLDS["warning"] + (
                adverse - p80
            ) / (p95 - p80) * Decimal(".25")
        elif adverse < p99:
            percentile_component = INDICATOR_BAND_THRESHOLDS["danger"] + (
                adverse - p95
            ) / (p99 - p95) * Decimal(".25")
        else:
            percentile_component = INDICATOR_BAND_THRESHOLDS["critical"] + min(
                ONE, (adverse - p99) / (ONE - p99)
            ) * Decimal(".25")
    zscore = features.mad_zscore
    oriented_z = ZERO if zscore is None else _oriented(zscore, direction)
    z_component = _clamp(oriented_z / HISTORICAL_MAD_Z_DENOMINATOR)
    return _quantize(max(percentile_component, z_component))


def _movement_components(
    features: IndicatorFeatures,
    *,
    profile: ScoreProfile,
    direction: RiskDirection,
) -> tuple[Decimal, Decimal]:
    window = features.windows.get(profile.preferred_window)
    if window is None or window.change is None:
        return ZERO, ZERO
    scale = features.volatility
    if scale is None or scale <= ZERO:
        percent = window.percent_change
        if percent is None:
            return ZERO, ZERO
        trend = _oriented(percent, direction) / MOVEMENT_PERCENT_DENOMINATOR
        acceleration = ZERO if window.acceleration is None else (
            _oriented(window.acceleration, direction) / max(abs(percent), Decimal("1"))
        )
        return _quantize(trend), _quantize(acceleration)
    observations = max(2, window.observation_count)
    denominator = (
        scale
        * Decimal(str(observations)).sqrt()
        * MOVEMENT_VOLATILITY_DENOMINATOR
    )
    trend = _oriented(window.change, direction) / denominator
    acceleration = ZERO if window.acceleration is None else (
        _oriented(window.acceleration, direction)
        / (scale * MOVEMENT_VOLATILITY_DENOMINATOR)
    )
    return _quantize(trend), _quantize(acceleration)


def _regime_score(features: IndicatorFeatures) -> Decimal:
    state_scores = {
        None: ZERO,
        "normal": ZERO,
        "inverted": Decimal(".25"),
        "long_inversion": Decimal(".50"),
        "credit_boom_vulnerability": Decimal(".50"),
        "liquidity_contraction": Decimal(".50"),
        "resteepening_after_long_inversion": ONE,
        "credit_bust_after_boom": ONE,
        "emergency_liquidity_reaction": ONE,
        "leverage_build": Decimal(".75"),
        "leverage_unwind": Decimal(".75"),
        "liquidation_unwind": ONE,
    }
    volatility_score = {
        "normal": ZERO,
        "transition": Decimal(".40"),
        "stressed": Decimal(".70"),
        "extreme": ONE,
    }.get(features.volatility_regime, ZERO)
    change_score = Decimal(".50") if features.change_point else ZERO
    return _quantize(max(state_scores.get(features.state_machine, ZERO), volatility_score, change_score))


def _availability(freshness: DataFreshness) -> Decimal:
    return AVAILABILITY_FACTORS[freshness]


def score_indicator_v2(
    *,
    indicator_code: str,
    frequency: str,
    direction: RiskDirection,
    economic_score: Decimal,
    features: IndicatorFeatures,
    history_count: int,
    freshness: DataFreshness,
    data_quality: Decimal = ONE,
) -> IndicatorScoreV2:
    if history_count < 0:
        raise ValueError("history_count must not be negative")
    profile = profile_for(frequency=frequency, direction=direction, code=indicator_code)
    availability = _availability(freshness)
    quality = _quantize(data_quality)
    economic = _quantize(economic_score)
    historical = (
        _historical_score(features, direction=direction)
        if history_count >= profile.minimum_history
        else None
    )
    trend, acceleration = _movement_components(
        features, profile=profile, direction=direction
    )
    persistence = _quantize(
        Decimal(features.persistence_count) / PERSISTENCE_OBSERVATION_CAP
    )
    regime = _regime_score(features)

    if availability == ZERO:
        effective = None
        agreement = IndicatorAgreement.INSUFFICIENT_DATA
    else:
        available_components = {
            "economic": economic,
            "trend": trend,
            "acceleration": acceleration,
            "persistence": persistence,
            "regime": regime,
        }
        weights = {
            "economic": profile.economic,
            "trend": profile.trend,
            "acceleration": profile.acceleration,
            "persistence": profile.persistence,
            "regime": profile.regime,
        }
        if historical is not None:
            available_components["historical"] = historical
            weights["historical"] = profile.historical
        weight_sum = sum(weights.values())
        combined = sum(
            available_components[name] * weight for name, weight in weights.items()
        ) / weight_sum
        combined *= availability * quality
        if economic >= INDICATOR_BAND_THRESHOLDS["critical"]:
            combined = max(combined, economic * availability * quality)
        effective = _quantize(combined)
        if historical is None:
            agreement = IndicatorAgreement.INSUFFICIENT_HISTORY
        elif (
            economic >= INDICATOR_BAND_THRESHOLDS["warning"]
            and historical >= INDICATOR_BAND_THRESHOLDS["warning"]
        ):
            agreement = IndicatorAgreement.CONFIRMED_STRESS
        elif (
            economic < INDICATOR_BAND_THRESHOLDS["warning"]
            and max(historical, trend, acceleration)
            >= INDICATOR_BAND_THRESHOLDS["danger"]
        ):
            agreement = IndicatorAgreement.EARLY_ANOMALY
        elif (
            economic >= INDICATOR_BAND_THRESHOLDS["danger"]
            and trend < INDICATOR_BAND_THRESHOLDS["warning"]
            and acceleration < INDICATOR_BAND_THRESHOLDS["warning"]
        ):
            agreement = IndicatorAgreement.HIGH_LEVEL_STABILIZING
        else:
            agreement = IndicatorAgreement.MIXED

    payload = {
        "version": SCORING_VERSION,
        "indicator_code": indicator_code,
        "profile": asdict(profile),
        "economic_score": format(economic, "f"),
        "historical_score": None if historical is None else format(historical, "f"),
        "trend_score": format(trend, "f"),
        "acceleration_score": format(acceleration, "f"),
        "persistence_score": format(persistence, "f"),
        "regime_score": format(regime, "f"),
        "data_quality": format(quality, "f"),
        "availability": format(availability, "f"),
        "feature_checksum": features.input_checksum,
        "history_count": history_count,
    }
    checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return IndicatorScoreV2(
        indicator_code=indicator_code,
        profile=profile.code,
        economic_score=economic,
        economic_band=band_from_score(economic),
        historical_score=historical,
        historical_band=None if historical is None else band_from_score(historical),
        trend_score=trend,
        acceleration_score=acceleration,
        persistence_score=persistence,
        regime_score=regime,
        data_quality=quality,
        availability=availability,
        effective_score=effective,
        effective_band=None if effective is None else band_from_score(effective),
        agreement=agreement,
        history_count=history_count,
        lineage=payload,
        input_checksum=checksum,
    )


def score_variant(base: IndicatorScoreV2, variant: str) -> IndicatorScoreV2:
    """Return an auditable indicator-score ablation without changing raw components.

    Events and contagion do not currently enter the numeric indicator score. Their
    ablations therefore intentionally preserve the full score; the replay manifest
    records that zero delta instead of pretending those components affect it.
    Dependency correction is applied at group/stage aggregation, so its indicator
    values are also unchanged here.
    """
    if variant not in SCORING_VARIANTS:
        raise ValueError(f"unknown scoring variant: {variant}")
    if variant in {
        "full",
        "without_events",
        "without_contagion",
        "without_dependency_correction",
    }:
        effective = base.effective_score
    elif base.availability == ZERO:
        effective = None
    elif variant == "economic_only":
        effective = _quantize(base.economic_score * base.availability * base.data_quality)
    elif variant == "historical_only":
        effective = (
            None
            if base.historical_score is None
            else _quantize(base.historical_score * base.availability * base.data_quality)
        )
    else:
        profile = PROFILES[base.profile]
        components = {
            "economic": (base.economic_score, profile.economic),
            "historical": (base.historical_score, profile.historical),
            "acceleration": (base.acceleration_score, profile.acceleration),
            "persistence": (base.persistence_score, profile.persistence),
            "regime": (base.regime_score, profile.regime),
        }
        available = tuple(
            (value, weight) for value, weight in components.values() if value is not None
        )
        weight_sum = sum((weight for _, weight in available), ZERO)
        effective = (
            None
            if weight_sum == ZERO
            else _quantize(
                sum((value * weight for value, weight in available), ZERO)
                / weight_sum
                * base.availability
                * base.data_quality
            )
        )
        if base.economic_score >= Decimal(".75") and effective is not None:
            effective = max(
                effective,
                _quantize(base.economic_score * base.availability * base.data_quality),
            )

    payload = {
        "base_checksum": base.input_checksum,
        "variant": variant,
        "effective_score": None if effective is None else format(effective, "f"),
    }
    checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return replace(
        base,
        effective_score=effective,
        effective_band=None if effective is None else band_from_score(effective),
        lineage={**base.lineage, "ablation_variant": variant},
        input_checksum=checksum,
    )
