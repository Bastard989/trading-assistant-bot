from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from trading_bot.crisis_radar.domain import (
    CoverageAssessment,
    CoverageStatus,
    DataFreshness,
    IndicatorState,
)


@dataclass(frozen=True)
class ExpectedIndicator:
    code: str
    group_code: str
    region_code: str
    weight: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.code or not self.group_code or not self.region_code:
            raise ValueError("coverage indicator codes must not be empty")
        if not self.weight.is_finite() or self.weight <= 0:
            raise ValueError("coverage weight must be positive and finite")


DEFAULT_REQUIRED_GROUPS = (
    "market_stress",
    "credit",
    "rates_liquidity",
    "us_financial_conditions",
    "euro_financial_stress",
    "china_leading_cycle",
    "global_leading_cycle",
    "inflation_commodities",
    "crypto_price_stress",
)

DEFAULT_REQUIRED_REGIONS = ("US", "EU", "CHINA", "GLOBAL", "CRYPTO")
GLOBAL_V2_REQUIRED_REGIONS = (
    "US",
    "EU",
    "CHINA",
    "CAN",
    "GBR",
    "HKG",
    "JPN",
    "KOR",
    "IND",
    "BRA",
    "MEX",
    "GLOBAL",
    "CRYPTO",
)


def normalize_region(region_code: str) -> str:
    if region_code in {"US", "USA"}:
        return "US"
    if region_code in {"EU", "EA20", "EUR"}:
        return "EU"
    if region_code in {"CN", "CHN", "CHINA"}:
        return "CHINA"
    if region_code == "CRYPTO":
        return "CRYPTO"
    if region_code in {"G20", "WLD", "GLOBAL"}:
        return "GLOBAL"
    return region_code


def assess_coverage(
    states: list[IndicatorState],
    *,
    expected: tuple[ExpectedIndicator, ...],
    required_groups: tuple[str, ...] = DEFAULT_REQUIRED_GROUPS,
    required_regions: tuple[str, ...] = DEFAULT_REQUIRED_REGIONS,
    healthy_threshold: Decimal = Decimal("0.85"),
    degraded_threshold: Decimal = Decimal("0.70"),
) -> CoverageAssessment:
    if not expected:
        return CoverageAssessment(
            status=CoverageStatus.INSUFFICIENT_DATA,
            ratio=Decimal("0"),
            expected_count=0,
            available_count=0,
            fresh_count=0,
            delayed_count=0,
            stale_count=0,
            missing_count=0,
            available_group_codes=frozenset(),
            missing_required_groups=tuple(sorted(required_groups)),
            missing_required_regions=tuple(sorted(required_regions)),
            reason_codes=("no_expected_indicators",),
        )
    if not (Decimal("0") < degraded_threshold < healthy_threshold <= Decimal("1")):
        raise ValueError("coverage thresholds must satisfy 0 < degraded < healthy <= 1")

    by_code = {state.indicator_code: state for state in states}
    expected_codes = {item.code for item in expected}
    if len(expected_codes) != len(expected):
        raise ValueError("expected coverage indicators must have unique codes")

    total_weight = sum((item.weight for item in expected), Decimal("0"))
    covered_weight = Decimal("0")
    fresh_count = delayed_count = stale_count = missing_count = 0
    available_groups: set[str] = set()
    available_regions: set[str] = set()

    for item in expected:
        state = by_code.get(item.code)
        if state is None or state.freshness is DataFreshness.MISSING:
            missing_count += 1
            continue
        if state.freshness is DataFreshness.FRESH:
            fresh_count += 1
            covered_weight += item.weight
        elif state.freshness is DataFreshness.DELAYED:
            delayed_count += 1
            covered_weight += item.weight * Decimal("0.7")
        else:
            stale_count += 1
            continue
        available_groups.add(item.group_code)
        available_regions.add(normalize_region(item.region_code))

    ratio = (covered_weight / total_weight).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_EVEN
    )
    missing_groups = tuple(sorted(set(required_groups) - available_groups))
    missing_regions = tuple(sorted(set(required_regions) - available_regions))
    reasons: list[str] = []
    if ratio < degraded_threshold:
        reasons.append("coverage_below_minimum")
    elif ratio < healthy_threshold:
        reasons.append("coverage_degraded")
    if missing_groups:
        reasons.append("required_groups_missing")
    if missing_regions:
        reasons.append("required_regions_missing")
    if stale_count:
        reasons.append("stale_indicators")
    if delayed_count:
        reasons.append("delayed_indicators")
    if missing_count:
        reasons.append("missing_indicators")

    if ratio < degraded_threshold or len(missing_groups) >= 2 or len(missing_regions) >= 2:
        status = CoverageStatus.INSUFFICIENT_DATA
    elif ratio < healthy_threshold or delayed_count or missing_groups or missing_regions:
        status = CoverageStatus.DEGRADED
    else:
        status = CoverageStatus.HEALTHY

    return CoverageAssessment(
        status=status,
        ratio=ratio,
        expected_count=len(expected),
        available_count=fresh_count + delayed_count,
        fresh_count=fresh_count,
        delayed_count=delayed_count,
        stale_count=stale_count,
        missing_count=missing_count,
        available_group_codes=frozenset(available_groups),
        missing_required_groups=missing_groups,
        missing_required_regions=missing_regions,
        reason_codes=tuple(reasons),
    )
