from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_CODE,
    METHODOLOGY_V11_VERSION,
    V11_INDICATORS,
    V11_SCENARIOS,
)
from trading_bot.crisis_radar.coverage import (
    GLOBAL_V2_REQUIRED_REGIONS,
    V11_REQUIRED_GROUPS,
    ExpectedIndicator,
    assess_coverage,
)
from trading_bot.crisis_radar.domain import CoverageStatus, QualityFlag
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.scenario_v2 import calculate_scenario_v2
from trading_bot.crisis_radar.scoring_v2 import (
    SCORING_VARIANTS,
    score_indicator_v2,
    score_variant,
)
from trading_bot.crisis_radar.stage_v2 import calculate_stage_v2, dependency_for
from trading_bot.crisis_radar.states import build_indicator_state
from trading_bot.crisis_radar.trends import calculate_indicator_features


REPLAY_V2_ENGINE_VERSION = "causal-v11-replay-v1"
ZERO = Decimal("0")


@dataclass(frozen=True)
class V11VariantSignal:
    signal_at: datetime
    variant: str
    signal_score: Decimal
    scenario_status: str
    market_stage: str
    intensity: Decimal
    breadth: Decimal
    numeric_coverage: Decimal
    input_count: int
    backtest_eligible: bool
    eligibility_reason: str
    latest_released_at: datetime | None
    observation_ids: tuple[int, ...]
    input_checksum: str

    def canonical_payload(self) -> dict:
        return {
            "signal_at": self.signal_at.isoformat(),
            "variant": self.variant,
            "signal_score": format(self.signal_score, "f"),
            "scenario_status": self.scenario_status,
            "market_stage": self.market_stage,
            "intensity": format(self.intensity, "f"),
            "breadth": format(self.breadth, "f"),
            "numeric_coverage": format(self.numeric_coverage, "f"),
            "input_count": self.input_count,
            "backtest_eligible": self.backtest_eligible,
            "eligibility_reason": self.eligibility_reason,
            "latest_released_at": (
                None if self.latest_released_at is None else self.latest_released_at.isoformat()
            ),
            "observation_ids": list(self.observation_ids),
            "input_checksum": self.input_checksum,
        }


@dataclass(frozen=True)
class V11ReplayResult:
    scenario_code: str
    started_at: datetime
    ended_at: datetime
    step: timedelta
    minimum_coverage: Decimal
    signals: tuple[V11VariantSignal, ...]

    @property
    def checksum(self) -> str:
        payload = {
            "engine": REPLAY_V2_ENGINE_VERSION,
            "methodology": [METHODOLOGY_CODE, METHODOLOGY_V11_VERSION],
            "scenario_code": self.scenario_code,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "step_seconds": round(self.step.total_seconds()),
            "minimum_coverage": format(self.minimum_coverage, "f"),
            "signals": [item.canonical_payload() for item in self.signals],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def _uncorrected_assignments(assignments):
    return {
        code: replace(
            assignment,
            subchannel_code=code,
            cluster_code=assignment.group_code,
        )
        for code, assignment in assignments.items()
    }


def v11_signals_as_of(
    repository: CrisisRadarRepository,
    *,
    scenario_code: str,
    snapshot_at: datetime,
    minimum_coverage: Decimal = Decimal(".70"),
    previous_context: dict[str, tuple[str | None, Decimal | None]] | None = None,
) -> tuple[V11VariantSignal, ...]:
    """Calculate all v11 replay variants from information released by ``snapshot_at``."""
    if snapshot_at.tzinfo is None or snapshot_at.utcoffset() is None:
        raise ValueError("snapshot_at must be timezone-aware")
    if not ZERO <= minimum_coverage <= Decimal("1"):
        raise ValueError("minimum_coverage must be between zero and one")
    definition = next((item for item in V11_SCENARIOS if item.code == scenario_code), None)
    if definition is None:
        raise ValueError("unknown v11 scenario_code")
    inputs = [
        item
        for item in repository.analysis_inputs_as_of(
            METHODOLOGY_CODE,
            METHODOLOGY_V11_VERSION,
            as_of=snapshot_at,
            causal_only=True,
        )
        if QualityFlag.RETROSPECTIVE_REVISED not in item.observation.quality_flags
    ]
    states = []
    base_scores = []
    assignments = {}
    for item in inputs:
        state = build_indicator_state(
            item.observation,
            group_code=item.group_code,
            thresholds=item.thresholds,
            max_staleness_seconds=item.max_staleness_seconds,
            snapshot_at=snapshot_at,
        )
        states.append(state)
        points = repository.indicator_points_as_of(
            item.observation.indicator_code,
            as_of=snapshot_at,
            exclude_retrospective_revised=True,
            causal_only=True,
        )
        if not points:
            continue
        features = calculate_indicator_features(
            item.observation.indicator_code,
            points,
            snapshot_at=snapshot_at,
            direction=item.thresholds.direction,
        )
        base_scores.append(
            score_indicator_v2(
                indicator_code=item.observation.indicator_code,
                frequency=item.frequency,
                direction=item.thresholds.direction,
                economic_score=state.stress_score,
                features=features,
                history_count=len(points),
                freshness=state.freshness,
                data_quality={
                    "fresh": Decimal("1"),
                    "delayed": Decimal(".70"),
                    "stale": ZERO,
                    "missing": ZERO,
                }[state.freshness.value],
            )
        )
        seed = next(seed for seed in V11_INDICATORS if seed.code == item.observation.indicator_code)
        assignments[seed.code] = dependency_for(
            code=seed.code,
            group_code=seed.group_code,
            region_code=seed.region_code,
        )
    coverage = assess_coverage(
        states,
        expected=tuple(
            ExpectedIndicator(
                code=seed.code,
                group_code=seed.group_code,
                region_code=seed.region_code,
            )
            for seed in V11_INDICATORS
        ),
        required_groups=V11_REQUIRED_GROUPS,
        required_regions=GLOBAL_V2_REQUIRED_REGIONS,
    )
    events = tuple(
        repository.events_payload(days=90, limit=100, as_of=snapshot_at).get("items") or ()
    )
    observation_ids = tuple(sorted(item.observation_id for item in inputs))
    latest_released_at = max(
        (item.observation.released_at for item in inputs), default=None
    )
    results = []
    for variant in SCORING_VARIANTS:
        scores = tuple(score_variant(item, variant) for item in base_scores)
        variant_assignments = (
            _uncorrected_assignments(assignments)
            if variant == "without_dependency_correction"
            else assignments
        )
        previous_stage, previous_peak = (previous_context or {}).get(variant, (None, None))
        stage = calculate_stage_v2(
            scores,
            variant_assignments,
            coverage_status=coverage.status,
            previous_stage=previous_stage,
            previous_peak_intensity=previous_peak,
        )
        event_ids = () if variant == "without_events" else tuple(
            int(event["id"])
            for event in events
            if event.get("taxonomy") in definition.event_taxonomies
            and Decimal(str(event.get("event_score") or 0)) > Decimal(".10")
        )[:20]
        scenario = calculate_scenario_v2(
            definition,
            stage.groups,
            evidence_ids=event_ids,
            numeric_coverage=coverage.ratio,
            news_coverage=Decimal("1"),
        )
        eligible = bool(inputs) and coverage.ratio >= minimum_coverage and (
            coverage.status is not CoverageStatus.INSUFFICIENT_DATA
        )
        reason = (
            ""
            if eligible
            else "no_as_of_inputs"
            if not inputs
            else "insufficient_numeric_coverage"
        )
        checksum_payload = {
            "engine": REPLAY_V2_ENGINE_VERSION,
            "scenario": scenario.input_checksum,
            "stage": stage.input_checksum,
            "variant": variant,
            "observation_ids": observation_ids,
        }
        results.append(
            V11VariantSignal(
                signal_at=snapshot_at,
                variant=variant,
                signal_score=(scenario.strength / Decimal("100")).quantize(Decimal(".0001")),
                scenario_status=scenario.status,
                market_stage=stage.stage,
                intensity=stage.stress_intensity,
                breadth=stage.systemic_breadth,
                numeric_coverage=coverage.ratio,
                input_count=len(inputs),
                backtest_eligible=eligible,
                eligibility_reason=reason,
                latest_released_at=latest_released_at,
                observation_ids=observation_ids,
                input_checksum=hashlib.sha256(
                    json.dumps(
                        checksum_payload, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            )
        )
    return tuple(results)


def replay_v11_scenario(
    repository: CrisisRadarRepository,
    scenario_code: str,
    *,
    started_at: datetime,
    ended_at: datetime,
    step: timedelta,
    minimum_coverage: Decimal = Decimal(".70"),
    max_points: int = 20000,
) -> V11ReplayResult:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    if ended_at.tzinfo is None or ended_at.utcoffset() is None:
        raise ValueError("ended_at must be timezone-aware")
    if ended_at < started_at:
        raise ValueError("ended_at must not precede started_at")
    if step < timedelta(hours=1) or step > timedelta(days=366):
        raise ValueError("step must be between one hour and 366 days")
    point_count = int((ended_at - started_at) // step) + 1
    if point_count > max_points:
        raise ValueError("requested replay exceeds max_points")

    signals = []
    previous: dict[str, tuple[str | None, Decimal | None]] = {}
    signal_at = started_at
    while signal_at <= ended_at:
        current = v11_signals_as_of(
            repository,
            scenario_code=scenario_code,
            snapshot_at=signal_at,
            minimum_coverage=minimum_coverage,
            previous_context=previous,
        )
        signals.extend(current)
        for item in current:
            prior_peak = previous.get(item.variant, (None, None))[1] or ZERO
            previous[item.variant] = (item.market_stage, max(prior_peak, item.intensity))
        signal_at += step
    return V11ReplayResult(
        scenario_code=scenario_code,
        started_at=started_at,
        ended_at=ended_at,
        step=step,
        minimum_coverage=minimum_coverage,
        signals=tuple(signals),
    )
