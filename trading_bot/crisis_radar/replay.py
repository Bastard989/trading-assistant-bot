from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN

from trading_bot.crisis_radar.catalog import METHODOLOGY_CODE, METHODOLOGY_VERSION
from trading_bot.crisis_radar.domain import IndicatorBand, QualityFlag
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.scenarios import SCENARIOS, build_scenario_states
from trading_bot.crisis_radar.stability import STABILITY_POLICY, stabilize_indicator_state
from trading_bot.crisis_radar.states import build_indicator_state, build_market_overview


REPLAY_ENGINE_VERSION = "historical-replay-v1"
FOUR_PLACES = Decimal("0.0001")
ZERO = Decimal("0")
ONE = Decimal("1")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class ReplaySignal:
    scenario_code: str
    signal_at: datetime
    signal_score: Decimal
    scenario_status: str
    data_confidence: str
    coverage: Decimal
    input_count: int
    backtest_eligible: bool
    eligibility_reason: str
    latest_released_at: datetime | None
    observation_ids: tuple[int, ...]
    input_checksum: str
    evidence: tuple[tuple[str, str], ...]

    def canonical_payload(self) -> dict:
        return {
            "scenario_code": self.scenario_code,
            "signal_at": self.signal_at.isoformat(),
            "signal_score": _decimal_text(self.signal_score),
            "scenario_status": self.scenario_status,
            "data_confidence": self.data_confidence,
            "coverage": _decimal_text(self.coverage),
            "input_count": self.input_count,
            "backtest_eligible": self.backtest_eligible,
            "eligibility_reason": self.eligibility_reason,
            "latest_released_at": (
                None if self.latest_released_at is None else self.latest_released_at.isoformat()
            ),
            "observation_ids": list(self.observation_ids),
            "input_checksum": self.input_checksum,
            "evidence": [list(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class ReplayResult:
    scenario_code: str
    methodology_code: str
    methodology_version: str
    engine_version: str
    started_at: datetime
    ended_at: datetime
    step: timedelta
    minimum_coverage: Decimal
    signals: tuple[ReplaySignal, ...]

    @property
    def checksum(self) -> str:
        payload = {
            "scenario_code": self.scenario_code,
            "methodology": [self.methodology_code, self.methodology_version],
            "engine_version": self.engine_version,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "step_seconds": round(self.step.total_seconds()),
            "minimum_coverage": _decimal_text(self.minimum_coverage),
            "signals": [item.canonical_payload() for item in self.signals],
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _scenario_score(groups, definition) -> Decimal:
    selected = [item for item in groups if item.group_code in definition.group_codes]
    if not selected:
        return ZERO
    scores = [item.stress_score for item in selected]
    strongest = max(scores)
    average = sum(scores, ZERO) / Decimal(len(scores))
    active_count = sum(item.band is not IndicatorBand.NORMAL for item in selected)
    breadth_factor = Decimal("0.75") + Decimal("0.25") * min(
        ONE, Decimal(active_count) / Decimal("3")
    )
    score = (strongest * Decimal("0.65") + average * Decimal("0.35")) * breadth_factor
    if definition.anchor_groups and not any(
        item.group_code in definition.anchor_groups and item.band is not IndicatorBand.NORMAL
        for item in selected
    ):
        score = min(score, Decimal("0.24"))
    return max(ZERO, min(ONE, score)).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)


def replay_scenario(
    repository: CrisisRadarRepository,
    scenario_code: str,
    *,
    started_at: datetime,
    ended_at: datetime,
    step: timedelta,
    methodology_code: str = METHODOLOGY_CODE,
    methodology_version: str = METHODOLOGY_VERSION,
    minimum_coverage: Decimal = Decimal("0.50"),
    max_points: int = 20000,
) -> ReplayResult:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    if ended_at.tzinfo is None or ended_at.utcoffset() is None:
        raise ValueError("ended_at must be timezone-aware")
    if ended_at < started_at:
        raise ValueError("ended_at must not precede started_at")
    if step < timedelta(hours=1) or step > timedelta(days=366):
        raise ValueError("step must be between one hour and 366 days")
    if not minimum_coverage.is_finite() or not ZERO <= minimum_coverage <= ONE:
        raise ValueError("minimum_coverage must be between 0 and 1")
    if max_points < 1 or max_points > 20000:
        raise ValueError("max_points must be between 1 and 20000")
    definition = next((item for item in SCENARIOS if item.code == scenario_code), None)
    if definition is None:
        raise ValueError("unknown scenario_code")
    point_count = int((ended_at - started_at) // step) + 1
    if point_count > max_points:
        raise ValueError("requested replay exceeds max_points")

    previous_bands: dict[str, IndicatorBand] = {}
    signals: list[ReplaySignal] = []
    signal_at = started_at
    while signal_at <= ended_at:
        inputs = repository.analysis_inputs_as_of(
            methodology_code,
            methodology_version,
            as_of=signal_at,
            causal_only=True,
        )
        inputs = [
            item
            for item in inputs
            if QualityFlag.RETROSPECTIVE_REVISED not in item.observation.quality_flags
        ]
        states = []
        for item in inputs:
            base_state = build_indicator_state(
                item.observation,
                group_code=item.group_code,
                thresholds=item.thresholds,
                max_staleness_seconds=item.max_staleness_seconds,
                snapshot_at=signal_at,
            )
            confirmation_points = (
                1
                if item.frequency in {"monthly", "quarterly", "annual"}
                else STABILITY_POLICY.confirmation_points
            )
            state = stabilize_indicator_state(
                base_state,
                previous_band=previous_bands.get(item.observation.indicator_code),
                recent_values=repository.recent_indicator_values_as_of(
                    item.observation.indicator_code,
                    as_of=signal_at,
                    limit=max(confirmation_points + 1, 3),
                    causal_only=True,
                ),
                thresholds=item.thresholds,
                confirmation_points=confirmation_points,
            )
            previous_bands[item.observation.indicator_code] = state.band
            states.append(state)
        overview = build_market_overview(states, snapshot_at=signal_at)
        scenario = next(item for item in build_scenario_states(overview.groups) if item.code == scenario_code)
        selected_group_count = sum(
            item.group_code in definition.group_codes for item in overview.groups
        )
        coverage = (
            Decimal(selected_group_count) / Decimal(len(definition.group_codes))
        ).quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)
        anchor_present = not definition.anchor_groups or any(
            item.group_code in definition.anchor_groups for item in overview.groups
        )
        eligible = bool(inputs) and coverage >= minimum_coverage and anchor_present
        if not inputs:
            eligibility_reason = "no_as_of_inputs"
        elif coverage < minimum_coverage:
            eligibility_reason = "insufficient_group_coverage"
        elif not anchor_present:
            eligibility_reason = "missing_anchor_group"
        else:
            eligibility_reason = ""
        signals.append(
            ReplaySignal(
                scenario_code=scenario_code,
                signal_at=signal_at,
                signal_score=_scenario_score(overview.groups, definition),
                scenario_status=scenario.status.value,
                data_confidence=scenario.confidence.value,
                coverage=coverage,
                input_count=len(inputs),
                backtest_eligible=eligible,
                eligibility_reason=eligibility_reason,
                latest_released_at=max(
                    (item.observation.released_at for item in inputs), default=None
                ),
                observation_ids=tuple(sorted(item.observation_id for item in inputs)),
                input_checksum=hashlib.sha256(
                    json.dumps(
                        sorted(item.observation_id for item in inputs),
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                evidence=tuple((code, band.value) for code, band in scenario.evidence),
            )
        )
        signal_at += step

    return ReplayResult(
        scenario_code=scenario_code,
        methodology_code=methodology_code,
        methodology_version=methodology_version,
        engine_version=REPLAY_ENGINE_VERSION,
        started_at=started_at,
        ended_at=ended_at,
        step=step,
        minimum_coverage=minimum_coverage,
        signals=tuple(signals),
    )
