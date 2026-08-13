from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from trading_bot.crisis_radar.catalog import (
    V13_REPLAY_COVERAGE_CONTRACT,
    IndicatorSeed,
)
from trading_bot.crisis_radar.coverage import normalize_region
from trading_bot.crisis_radar.domain import CoverageStatus, DataFreshness, IndicatorState
from trading_bot.crisis_radar.scenarios import ScenarioDefinition


SCENARIO_REPLAY_COVERAGE_VERSION = str(V13_REPLAY_COVERAGE_CONTRACT["version"])


@dataclass(frozen=True)
class RequiredClass:
    code: str
    alternatives: tuple[str, ...]
    minimum_matches: int = 1

    def __post_init__(self) -> None:
        if not self.code or not self.alternatives or any(not item for item in self.alternatives):
            raise ValueError("coverage class needs a code and non-empty alternatives")
        if self.minimum_matches < 1 or self.minimum_matches > len(self.alternatives):
            raise ValueError("coverage class minimum_matches is outside alternatives")


@dataclass(frozen=True)
class ScenarioCoveragePolicy:
    scenario_code: str
    expected_group_codes: tuple[str, ...]
    required_channel_classes: tuple[RequiredClass, ...]
    required_region_classes: tuple[RequiredClass, ...]


@dataclass(frozen=True)
class ScenarioReplayCoverage:
    status: CoverageStatus
    ratio: Decimal
    expected_count: int
    available_count: int
    fresh_count: int
    delayed_count: int
    stale_count: int
    missing_count: int
    available_group_codes: tuple[str, ...]
    available_region_codes: tuple[str, ...]
    missing_channel_classes: tuple[str, ...]
    missing_region_classes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    input_checksum: str


_financial_contract = V13_REPLAY_COVERAGE_CONTRACT["scenarios"]["financial_stress"]
_FINANCIAL_STRESS_POLICY = ScenarioCoveragePolicy(
    scenario_code="financial_stress",
    expected_group_codes=(
        "credit",
        "market_stress",
        "euro_financial_stress",
        "equity_market_stress",
        "rates_liquidity",
        "us_financial_conditions",
        "global_credit_cycle",
        "banking_stress",
        "dollar_liquidity",
        "canada_market_conditions",
        "uk_market_conditions",
        "china_market_conditions",
        "hong_kong_market_conditions",
        "japan_market_conditions",
        "korea_market_conditions",
        "india_market_conditions",
        "brazil_market_conditions",
        "mexico_market_conditions",
    ),
    required_channel_classes=tuple(
        RequiredClass(code, tuple(alternatives))
        for code, alternatives in _financial_contract[
            "required_channel_classes"
        ].items()
    ),
    required_region_classes=tuple(
        RequiredClass(
            code,
            tuple(specification["alternatives"]),
            int(specification["minimum_matches"]),
        )
        for code, specification in _financial_contract[
            "required_region_classes"
        ].items()
    ),
)


def policy_for(definition: ScenarioDefinition) -> ScenarioCoveragePolicy:
    if definition.code == _FINANCIAL_STRESS_POLICY.scenario_code:
        if definition.group_codes != _FINANCIAL_STRESS_POLICY.expected_group_codes:
            raise ValueError("financial_stress groups do not match the coverage contract")
        return _FINANCIAL_STRESS_POLICY
    return ScenarioCoveragePolicy(
        scenario_code=definition.code,
        expected_group_codes=definition.group_codes,
        required_channel_classes=tuple(
            RequiredClass(f"anchor:{code}", (code,)) for code in definition.anchor_groups
        ),
        required_region_classes=(),
    )


def assess_scenario_replay_coverage(
    states: list[IndicatorState],
    *,
    indicators: tuple[IndicatorSeed, ...],
    definition: ScenarioDefinition,
    minimum_coverage: Decimal = Decimal(
        str(V13_REPLAY_COVERAGE_CONTRACT["minimum_coverage"])
    ),
    healthy_threshold: Decimal = Decimal(
        str(V13_REPLAY_COVERAGE_CONTRACT["healthy_coverage"])
    ),
) -> ScenarioReplayCoverage:
    if not Decimal("0") < minimum_coverage <= healthy_threshold <= Decimal("1"):
        raise ValueError("scenario coverage thresholds must satisfy 0 < minimum <= healthy <= 1")
    policy = policy_for(definition)
    expected_indicators = tuple(
        item for item in indicators if item.group_code in policy.expected_group_codes
    )
    if not expected_indicators:
        raise ValueError("scenario coverage universe must contain indicators")
    by_code = {state.indicator_code: state for state in states}
    group_freshness: dict[str, set[DataFreshness]] = {
        code: set() for code in policy.expected_group_codes
    }
    groups: set[str] = set()
    regions: set[str] = set()
    lineage: dict[str, str] = {}
    for item in expected_indicators:
        state = by_code.get(item.code)
        if state is None or state.freshness is DataFreshness.MISSING:
            lineage[item.code] = "missing"
            continue
        lineage[item.code] = hashlib.sha256(
            json.dumps(
                {
                    "value": format(state.value, "f"),
                    "freshness": state.freshness.value,
                    "observed_at": state.observation.observed_at.isoformat(),
                    "released_at": state.observation.released_at.isoformat(),
                    "vintage": state.observation.vintage,
                    "content_hash": state.observation.content_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        group_freshness[item.group_code].add(state.freshness)
        if state.freshness not in {DataFreshness.FRESH, DataFreshness.DELAYED}:
            continue
        groups.add(item.group_code)
        regions.add(normalize_region(item.region_code))
    fresh = delayed = stale = missing = 0
    covered = Decimal("0")
    for values in group_freshness.values():
        if DataFreshness.FRESH in values:
            fresh += 1
            covered += Decimal("1")
        elif DataFreshness.DELAYED in values:
            delayed += 1
            covered += Decimal(".7")
        elif DataFreshness.STALE in values:
            stale += 1
        else:
            missing += 1
    ratio = (covered / Decimal(len(policy.expected_group_codes))).quantize(
        Decimal(".0001"), rounding=ROUND_HALF_EVEN
    )
    missing_channels = tuple(
        item.code
        for item in policy.required_channel_classes
        if len(groups.intersection(item.alternatives)) < item.minimum_matches
    )
    missing_regions = tuple(
        item.code
        for item in policy.required_region_classes
        if len(regions.intersection(item.alternatives)) < item.minimum_matches
    )
    reasons = []
    if ratio < minimum_coverage:
        reasons.append("scenario_coverage_below_minimum")
    elif ratio < healthy_threshold:
        reasons.append("scenario_coverage_degraded")
    if missing_channels:
        reasons.append("required_channel_class_missing")
    if missing_regions:
        reasons.append("required_region_class_missing")
    if stale:
        reasons.append("stale_indicators")
    if delayed:
        reasons.append("delayed_indicators")
    if missing:
        reasons.append("missing_indicators")
    if ratio < minimum_coverage or missing_channels or missing_regions:
        status = CoverageStatus.INSUFFICIENT_DATA
    elif ratio < healthy_threshold or delayed or stale or missing:
        status = CoverageStatus.DEGRADED
    else:
        status = CoverageStatus.HEALTHY
    canonical = {
        "version": SCENARIO_REPLAY_COVERAGE_VERSION,
        "coverage_unit": V13_REPLAY_COVERAGE_CONTRACT["coverage_unit"],
        "scenario": definition.code,
        "policy": {
            "groups": policy.expected_group_codes,
            "channels": [
                (item.code, item.alternatives, item.minimum_matches)
                for item in policy.required_channel_classes
            ],
            "regions": [
                (item.code, item.alternatives, item.minimum_matches)
                for item in policy.required_region_classes
            ],
        },
        "minimum_coverage": str(minimum_coverage),
        "inputs": lineage,
    }
    checksum = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ScenarioReplayCoverage(
        status=status,
        ratio=ratio,
        expected_count=len(policy.expected_group_codes),
        available_count=fresh + delayed,
        fresh_count=fresh,
        delayed_count=delayed,
        stale_count=stale,
        missing_count=missing,
        available_group_codes=tuple(sorted(groups)),
        available_region_codes=tuple(sorted(regions)),
        missing_channel_classes=missing_channels,
        missing_region_classes=missing_regions,
        reason_codes=tuple(reasons),
        input_checksum=checksum,
    )
