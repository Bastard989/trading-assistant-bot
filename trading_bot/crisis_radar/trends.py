from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_bot.crisis_radar.domain import RiskDirection


FEATURE_VERSION = "trend-regime-v1"
WINDOWS = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "15d": timedelta(days=15),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "6m": timedelta(days=183),
    "12m": timedelta(days=365),
}


@dataclass(frozen=True)
class TimePoint:
    observed_at: datetime
    released_at: datetime
    value: Decimal

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.released_at.tzinfo is None:
            raise ValueError("trend points must be timezone-aware")
        if not self.value.is_finite():
            raise ValueError("trend values must be finite")


@dataclass(frozen=True)
class WindowFeature:
    change: Decimal | None
    percent_change: Decimal | None
    robust_slope_per_day: Decimal | None
    acceleration: Decimal | None
    observation_count: int


@dataclass(frozen=True)
class IndicatorFeatures:
    indicator_code: str
    snapshot_at: datetime
    windows: dict[str, WindowFeature]
    percentile: Decimal | None
    mad_zscore: Decimal | None
    volatility: Decimal | None
    volatility_regime: str
    change_point: bool
    persistence_count: int
    worsening_score: Decimal
    state_machine: str | None
    input_checksum: str


@dataclass(frozen=True)
class ContagionFeatures:
    snapshot_at: datetime
    breadth: Decimal
    stressed_count: int
    indicator_count: int
    mean_absolute_correlation: Decimal | None
    stress_correlation_regime: str
    lead_lag_edges: tuple[dict, ...]
    input_checksum: str


def _decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _sample(points: tuple[TimePoint, ...], limit: int = 80) -> tuple[TimePoint, ...]:
    if len(points) <= limit:
        return points
    indices = sorted({round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)})
    return tuple(points[index] for index in indices)


def robust_slope(points: tuple[TimePoint, ...]) -> Decimal | None:
    sampled = _sample(points)
    if len(sampled) < 2:
        return None
    slopes = []
    for left_index, left in enumerate(sampled[:-1]):
        for right in sampled[left_index + 1 :]:
            days = Decimal(str((right.observed_at - left.observed_at).total_seconds() / 86400))
            if days > 0:
                slopes.append((right.value - left.value) / days)
    return None if not slopes else Decimal(str(statistics.median(slopes))).quantize(Decimal("0.0001"))


def _window_feature(points: tuple[TimePoint, ...], *, snapshot_at: datetime, window: timedelta) -> WindowFeature:
    selected = tuple(point for point in points if point.observed_at >= snapshot_at - window)
    if len(selected) < 2:
        return WindowFeature(None, None, None, None, len(selected))
    first, last = selected[0], selected[-1]
    change = last.value - first.value
    percent = None if first.value == 0 else change / abs(first.value) * Decimal("100")
    midpoint = len(selected) // 2
    older_slope = robust_slope(selected[: midpoint + 1])
    newer_slope = robust_slope(selected[midpoint:])
    acceleration = None if older_slope is None or newer_slope is None else newer_slope - older_slope
    return WindowFeature(
        change.quantize(Decimal("0.0001")),
        None if percent is None else percent.quantize(Decimal("0.0001")),
        robust_slope(selected),
        None if acceleration is None else acceleration.quantize(Decimal("0.0001")),
        len(selected),
    )


def _mad_z(values: list[float]) -> Decimal | None:
    if len(values) < 5:
        return None
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    if mad == 0:
        return Decimal("0")
    return _decimal(0.67448975 * (values[-1] - median) / mad)


def _cusum(values: list[float]) -> bool:
    if len(values) < 12:
        return False
    history = values[:-1]
    center = statistics.median(history)
    deviations = [abs(value - center) for value in history]
    scale = statistics.median(deviations) * 1.4826
    if scale <= 0:
        return False
    positive = negative = 0.0
    last_hit = -1
    for index, value in enumerate(values):
        standardized = (value - center) / scale
        positive = max(0.0, positive + standardized - 0.5)
        negative = min(0.0, negative + standardized + 0.5)
        if positive > 5 or negative < -5:
            last_hit = index
            positive = negative = 0.0
    return last_hit >= len(values) - max(3, len(values) // 5)


def _state_machine(code: str, points: tuple[TimePoint, ...]) -> str | None:
    if not points:
        return None
    latest = points[-1].value
    if code == "us_10y2y_spread":
        if latest < 0:
            run = []
            for point in reversed(points):
                if point.value >= 0:
                    break
                run.append(point)
            duration = (run[0].observed_at - run[-1].observed_at).days if run else 0
            return "long_inversion" if duration >= 60 else "inverted"
        negative_run = []
        for point in reversed(points[:-1]):
            if point.value < 0:
                negative_run.append(point)
            elif negative_run:
                break
        duration = (
            (negative_run[0].observed_at - negative_run[-1].observed_at).days
            if negative_run
            else 0
        )
        return "resteepening_after_long_inversion" if duration >= 60 else "normal"
    if code.endswith("credit_to_gdp_gap"):
        peak = max(point.value for point in points)
        if peak >= 10 and latest <= peak - 5:
            return "credit_bust_after_boom"
        return "credit_boom_vulnerability" if latest >= 10 else "normal"
    if code == "fed_assets_90d_change":
        prior_contraction = any(point.value <= -3 for point in points[:-1])
        if prior_contraction and latest >= 5:
            return "emergency_liquidity_reaction"
        return "liquidity_contraction" if latest <= -3 else "normal"
    if code.endswith("oi_7d_change"):
        return "leverage_unwind" if latest <= -25 else "leverage_build" if latest >= 25 else "normal"
    return None


def calculate_indicator_features(
    indicator_code: str,
    points: tuple[TimePoint, ...],
    *,
    snapshot_at: datetime,
    direction: RiskDirection,
) -> IndicatorFeatures:
    if snapshot_at.tzinfo is None or snapshot_at.utcoffset() is None:
        raise ValueError("snapshot_at must be timezone-aware")
    safe = tuple(
        sorted(
            (point for point in points if point.observed_at <= snapshot_at and point.released_at <= snapshot_at),
            key=lambda point: (point.observed_at, point.released_at),
        )
    )
    if not safe:
        raise ValueError("at least one causally available point is required")
    values = [float(point.value) for point in safe]
    percentile = (
        Decimal(sum(value <= values[-1] for value in values)) / Decimal(len(values))
    ).quantize(Decimal("0.0001"))
    zscore = _mad_z(values)
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    volatility = None if len(changes) < 3 else _decimal(statistics.pstdev(changes))
    change_point = _cusum(values)
    windows = {
        name: _window_feature(safe, snapshot_at=snapshot_at, window=window)
        for name, window in WINDOWS.items()
    }
    slope = windows["30d"].robust_slope_per_day or windows["90d"].robust_slope_per_day or Decimal("0")
    oriented_z = abs(zscore or Decimal("0")) if direction is RiskDirection.TWO_SIDED else (
        zscore or Decimal("0")
    ) * (Decimal("1") if direction is RiskDirection.HIGHER_IS_WORSE else Decimal("-1"))
    oriented_slope = abs(slope) if direction is RiskDirection.TWO_SIDED else slope * (
        Decimal("1") if direction is RiskDirection.HIGHER_IS_WORSE else Decimal("-1")
    )
    persistence = 0
    for change in reversed(changes):
        worsening = abs(change) > 0 if direction is RiskDirection.TWO_SIDED else (
            change > 0 if direction is RiskDirection.HIGHER_IS_WORSE else change < 0
        )
        if not worsening:
            break
        persistence += 1
    worsening_score = min(
        Decimal("1"),
        max(Decimal("0"), oriented_z / Decimal("3")) * Decimal("0.6")
        + min(Decimal("1"), abs(oriented_slope)) * Decimal("0.25")
        + min(Decimal("1"), Decimal(persistence) / Decimal("5")) * Decimal("0.15"),
    ).quantize(Decimal("0.0001"))
    regime = "extreme" if abs(zscore or Decimal("0")) >= 3 else "stressed" if abs(zscore or Decimal("0")) >= 2 else "transition" if change_point else "normal"
    canonical = "\n".join(
        f"{point.observed_at.isoformat()}|{point.released_at.isoformat()}|{point.value}" for point in safe
    )
    return IndicatorFeatures(
        indicator_code=indicator_code,
        snapshot_at=snapshot_at,
        windows=windows,
        percentile=percentile,
        mad_zscore=zscore,
        volatility=volatility,
        volatility_regime=regime,
        change_point=change_point,
        persistence_count=persistence,
        worsening_score=worsening_score,
        state_machine=_state_machine(indicator_code, safe),
        input_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def calculate_contagion(
    features: tuple[IndicatorFeatures, ...],
    series: dict[str, tuple[TimePoint, ...]],
    *,
    snapshot_at: datetime,
) -> ContagionFeatures:
    stressed = [item for item in features if item.worsening_score >= Decimal("0.5")]
    breadth = (Decimal(len(stressed)) / Decimal(len(features))).quantize(Decimal("0.0001")) if features else Decimal("0")
    returns: dict[str, dict[str, float]] = {}
    for code, points in series.items():
        safe = [point for point in points if point.released_at <= snapshot_at and point.observed_at <= snapshot_at]
        returns[code] = {
            point.observed_at.date().isoformat(): float(point.value - safe[index - 1].value)
            for index, point in enumerate(safe)
            if index > 0
        }
    correlations = []
    edges = []
    codes = sorted(returns)
    for left_index, left_code in enumerate(codes[:-1]):
        for right_code in codes[left_index + 1 :]:
            dates = sorted(set(returns[left_code]) & set(returns[right_code]))[-90:]
            if len(dates) < 10:
                continue
            left = [returns[left_code][date] for date in dates]
            right = [returns[right_code][date] for date in dates]
            if statistics.pstdev(left) == 0 or statistics.pstdev(right) == 0:
                continue
            correlation = statistics.correlation(left, right)
            correlations.append(abs(correlation))
            best = (abs(correlation), correlation, left_code, right_code, 0)
            for lag in range(1, min(6, len(dates) - 5)):
                candidates = (
                    (left[:-lag], right[lag:], left_code, right_code),
                    (right[:-lag], left[lag:], right_code, left_code),
                )
                for leader, follower, from_code, to_code in candidates:
                    if statistics.pstdev(leader) == 0 or statistics.pstdev(follower) == 0:
                        continue
                    lagged = statistics.correlation(leader, follower)
                    candidate = (abs(lagged), lagged, from_code, to_code, lag)
                    if candidate[0] > best[0] + 0.03:
                        best = candidate
            if best[0] >= 0.7:
                edges.append(
                    {
                        "from": best[2],
                        "to": best[3],
                        "correlation": round(best[1], 4),
                        "lag_days": best[4],
                    }
                )
    mean_correlation = None if not correlations else _decimal(statistics.mean(correlations))
    regime = "high" if (mean_correlation or Decimal("0")) >= Decimal("0.7") else "elevated" if (mean_correlation or Decimal("0")) >= Decimal("0.5") else "normal"
    canonical = json.dumps(
        {"features": [item.input_checksum for item in features], "edges": edges},
        sort_keys=True,
        separators=(",", ":"),
    )
    return ContagionFeatures(
        snapshot_at=snapshot_at,
        breadth=breadth,
        stressed_count=len(stressed),
        indicator_count=len(features),
        mean_absolute_correlation=mean_correlation,
        stress_correlation_regime=regime,
        lead_lag_edges=tuple(edges),
        input_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def feature_payload(features: IndicatorFeatures) -> dict:
    payload = asdict(features)
    payload["snapshot_at"] = features.snapshot_at.isoformat()
    payload["windows"] = {name: asdict(value) for name, value in features.windows.items()}
    return payload
