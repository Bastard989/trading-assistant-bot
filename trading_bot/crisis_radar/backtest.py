from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Iterable


ZERO = Decimal("0")
ONE = Decimal("1")
FOUR_PLACES = Decimal("0.0001")
SIX_PLACES = Decimal("0.000001")


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _probability(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < ZERO or value > ONE:
        raise ValueError(f"{field} must be between 0 and 1")


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        SIX_PLACES, rounding=ROUND_HALF_EVEN
    )


@dataclass(frozen=True)
class SignalPoint:
    scenario_code: str
    predicted_at: datetime
    signal_score: Decimal

    def __post_init__(self) -> None:
        if not self.scenario_code.strip():
            raise ValueError("scenario_code must not be empty")
        _aware(self.predicted_at, "predicted_at")
        _probability(self.signal_score, "signal_score")


@dataclass(frozen=True)
class ScenarioEvent:
    scenario_code: str
    started_at: datetime
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.scenario_code.strip():
            raise ValueError("scenario_code must not be empty")
        _aware(self.started_at, "started_at")
        if self.ended_at is not None:
            _aware(self.ended_at, "ended_at")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must not be earlier than started_at")


@dataclass(frozen=True)
class BacktestSample:
    scenario_code: str
    predicted_at: datetime
    horizon_end: datetime
    signal_score: Decimal
    outcome: bool
    event_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.scenario_code.strip():
            raise ValueError("scenario_code must not be empty")
        _aware(self.predicted_at, "predicted_at")
        _aware(self.horizon_end, "horizon_end")
        if self.horizon_end <= self.predicted_at:
            raise ValueError("horizon_end must be later than predicted_at")
        _probability(self.signal_score, "signal_score")
        if self.event_at is not None:
            _aware(self.event_at, "event_at")
            if not self.predicted_at < self.event_at <= self.horizon_end:
                raise ValueError("event_at must fall inside the forward horizon")
        if self.outcome != (self.event_at is not None):
            raise ValueError("outcome must agree with event_at")


@dataclass(frozen=True)
class CalibratedPrediction:
    sample: BacktestSample
    calibrated_probability: Decimal | None
    baseline_probability: Decimal | None
    confidence: str
    training_sample_count: int
    calibration_bin: int
    latest_training_horizon_end: datetime | None


@dataclass(frozen=True)
class CalibrationBin:
    index: int
    lower: Decimal
    upper: Decimal
    prediction_count: int
    average_probability: Decimal
    observed_rate: Decimal


@dataclass(frozen=True)
class BacktestMetrics:
    sample_count: int
    scored_count: int
    positive_count: int
    positive_event_count: int
    coverage: Decimal
    brier_score: Decimal | None
    baseline_brier_score: Decimal | None
    log_loss: Decimal | None
    precision: Decimal | None
    recall: Decimal | None
    false_alert_rate: Decimal | None
    average_lead_days: Decimal | None


@dataclass(frozen=True)
class BacktestResult:
    scenario_code: str
    horizon: timedelta
    predictions: tuple[CalibratedPrediction, ...]
    calibration_bins: tuple[CalibrationBin, ...]
    metrics: BacktestMetrics


def build_labeled_samples(
    signals: Iterable[SignalPoint],
    events: Iterable[ScenarioEvent],
    *,
    horizon: timedelta,
    coverage_end: datetime | None = None,
    exclude_active_events: bool = False,
) -> tuple[BacktestSample, ...]:
    if horizon <= timedelta(0) or horizon > timedelta(days=3650):
        raise ValueError("horizon must be between 1 second and 10 years")
    ordered_signals = sorted(signals, key=lambda item: item.predicted_at)
    if not ordered_signals:
        return ()
    if coverage_end is not None:
        _aware(coverage_end, "coverage_end")
    scenario_codes = {item.scenario_code for item in ordered_signals}
    if len(scenario_codes) != 1:
        raise ValueError("one backtest run must contain exactly one scenario")
    timestamps = [item.predicted_at for item in ordered_signals]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("signal timestamps must be unique inside one run")
    scenario_code = ordered_signals[0].scenario_code
    relevant_events = sorted(
        (item for item in events if item.scenario_code == scenario_code),
        key=lambda item: item.started_at,
    )
    samples = []
    for signal in ordered_signals:
        horizon_end = signal.predicted_at + horizon
        if coverage_end is not None and horizon_end > coverage_end:
            continue
        if exclude_active_events and any(
            event.started_at <= signal.predicted_at
            and (event.ended_at is None or signal.predicted_at <= event.ended_at)
            for event in relevant_events
        ):
            continue
        event_at = next(
            (
                event.started_at
                for event in relevant_events
                if signal.predicted_at < event.started_at <= horizon_end
            ),
            None,
        )
        samples.append(
            BacktestSample(
                scenario_code=scenario_code,
                predicted_at=signal.predicted_at,
                horizon_end=horizon_end,
                signal_score=signal.signal_score,
                outcome=event_at is not None,
                event_at=event_at,
            )
        )
    return tuple(samples)


def _bin_index(value: Decimal, bin_count: int) -> int:
    return min(int(value * bin_count), bin_count - 1)


def _monotonic_rates(
    samples: list[BacktestSample],
    *,
    bin_count: int,
    overall_rate: Decimal,
    prior_strength: Decimal,
) -> tuple[dict[int, Decimal], dict[int, int]]:
    counts = {index: 0 for index in range(bin_count)}
    positives = {index: 0 for index in range(bin_count)}
    for sample in samples:
        index = _bin_index(sample.signal_score, bin_count)
        counts[index] += 1
        positives[index] += int(sample.outcome)

    blocks: list[dict] = []
    for index in range(bin_count):
        count = counts[index]
        if not count:
            continue
        weight = Decimal(count) + prior_strength
        rate = (Decimal(positives[index]) + prior_strength * overall_rate) / weight
        blocks.append({"indices": [index], "weight": weight, "rate": rate})
        while len(blocks) >= 2 and blocks[-2]["rate"] > blocks[-1]["rate"]:
            right = blocks.pop()
            left = blocks.pop()
            merged_weight = left["weight"] + right["weight"]
            merged_rate = (
                left["rate"] * left["weight"] + right["rate"] * right["weight"]
            ) / merged_weight
            blocks.append(
                {
                    "indices": [*left["indices"], *right["indices"]],
                    "weight": merged_weight,
                    "rate": merged_rate,
                }
            )
    rates = {
        index: block["rate"].quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)
        for block in blocks
        for index in block["indices"]
    }
    return rates, counts


def _calibration_curve(
    predictions: list[CalibratedPrediction], *, bin_count: int
) -> tuple[CalibrationBin, ...]:
    buckets: dict[int, list[CalibratedPrediction]] = {}
    for prediction in predictions:
        probability = prediction.calibrated_probability
        if probability is None:
            continue
        buckets.setdefault(_bin_index(probability, bin_count), []).append(prediction)
    result = []
    width = ONE / Decimal(bin_count)
    for index, items in sorted(buckets.items()):
        probabilities = [item.calibrated_probability for item in items]
        average = sum((item for item in probabilities if item is not None), ZERO) / Decimal(
            len(items)
        )
        observed = Decimal(sum(item.sample.outcome for item in items)) / Decimal(len(items))
        result.append(
            CalibrationBin(
                index=index,
                lower=(Decimal(index) * width).quantize(FOUR_PLACES),
                upper=(Decimal(index + 1) * width).quantize(FOUR_PLACES),
                prediction_count=len(items),
                average_probability=average.quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN),
                observed_rate=observed.quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN),
            )
        )
    return tuple(result)


def _metrics(
    predictions: list[CalibratedPrediction], *, decision_threshold: Decimal
) -> BacktestMetrics:
    available = [item for item in predictions if item.calibrated_probability is not None]
    sample_count = len(predictions)
    scored_count = len(available)
    positive_count = sum(item.sample.outcome for item in predictions)
    positive_event_count = len(
        {item.sample.event_at for item in predictions if item.sample.event_at is not None}
    )
    coverage = _ratio(scored_count, sample_count) or ZERO
    if not available:
        return BacktestMetrics(
            sample_count=sample_count,
            scored_count=0,
            positive_count=positive_count,
            positive_event_count=positive_event_count,
            coverage=coverage,
            brier_score=None,
            baseline_brier_score=None,
            log_loss=None,
            precision=None,
            recall=None,
            false_alert_rate=None,
            average_lead_days=None,
        )

    brier_terms = []
    baseline_terms = []
    log_terms = []
    true_positive = false_positive = false_negative = 0
    lead_days = []
    for item in available:
        probability = item.calibrated_probability
        baseline = item.baseline_probability
        assert probability is not None and baseline is not None
        outcome = ONE if item.sample.outcome else ZERO
        brier_terms.append((probability - outcome) ** 2)
        baseline_terms.append((baseline - outcome) ** 2)
        clipped = max(Decimal("0.0001"), min(Decimal("0.9999"), probability))
        log_terms.append(
            -(float(outcome) * math.log(float(clipped)) + float(ONE - outcome) * math.log(float(ONE - clipped)))
        )
        predicted_positive = probability >= decision_threshold
        true_positive += int(predicted_positive and item.sample.outcome)
        false_positive += int(predicted_positive and not item.sample.outcome)
        false_negative += int(not predicted_positive and item.sample.outcome)
        if predicted_positive and item.sample.event_at is not None:
            seconds = (item.sample.event_at - item.sample.predicted_at).total_seconds()
            lead_days.append(Decimal(str(seconds)) / Decimal("86400"))

    brier = sum(brier_terms, ZERO) / Decimal(scored_count)
    baseline_brier = sum(baseline_terms, ZERO) / Decimal(scored_count)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    false_alert_rate = _ratio(false_positive, true_positive + false_positive)
    average_lead = (
        (sum(lead_days, ZERO) / Decimal(len(lead_days))).quantize(
            FOUR_PLACES, rounding=ROUND_HALF_EVEN
        )
        if lead_days
        else None
    )
    return BacktestMetrics(
        sample_count=sample_count,
        scored_count=scored_count,
        positive_count=positive_count,
        positive_event_count=positive_event_count,
        coverage=coverage,
        brier_score=brier.quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN),
        baseline_brier_score=baseline_brier.quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN),
        log_loss=Decimal(str(sum(log_terms) / scored_count)).quantize(
            SIX_PLACES, rounding=ROUND_HALF_EVEN
        ),
        precision=precision,
        recall=recall,
        false_alert_rate=false_alert_rate,
        average_lead_days=average_lead,
    )


def walk_forward_calibrate(
    samples: Iterable[BacktestSample],
    *,
    bin_count: int = 5,
    min_training_samples: int = 20,
    min_bin_samples: int = 5,
    min_positive_samples: int = 2,
    min_negative_samples: int = 2,
    min_unique_positive_events: int = 3,
    prior_strength: Decimal = Decimal("4"),
    decision_threshold: Decimal = Decimal("0.5"),
) -> BacktestResult:
    if bin_count < 2 or bin_count > 20:
        raise ValueError("bin_count must be between 2 and 20")
    if min_training_samples < 1 or min_bin_samples < 1:
        raise ValueError("minimum sample counts must be positive")
    if min_positive_samples < 1 or min_negative_samples < 1:
        raise ValueError("minimum class counts must be positive")
    if min_unique_positive_events < 1:
        raise ValueError("minimum unique positive events must be positive")
    if not prior_strength.is_finite() or prior_strength < ZERO:
        raise ValueError("prior_strength must be finite and non-negative")
    _probability(decision_threshold, "decision_threshold")

    ordered = sorted(samples, key=lambda item: item.predicted_at)
    if not ordered:
        raise ValueError("backtest requires at least one sample")
    scenario_codes = {item.scenario_code for item in ordered}
    if len(scenario_codes) != 1:
        raise ValueError("one backtest run must contain exactly one scenario")
    horizons = {item.horizon_end - item.predicted_at for item in ordered}
    if len(horizons) != 1:
        raise ValueError("one backtest run must use one fixed horizon")
    timestamps = [item.predicted_at for item in ordered]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("sample timestamps must be unique inside one run")

    predictions = []
    for sample in ordered:
        resolved = [item for item in ordered if item.horizon_end <= sample.predicted_at]
        training_count = len(resolved)
        positives = sum(item.outcome for item in resolved)
        unique_positive_events = len(
            {item.event_at for item in resolved if item.event_at is not None}
        )
        negatives = training_count - positives
        probability = baseline = None
        confidence = "insufficient"
        index = _bin_index(sample.signal_score, bin_count)
        if (
            training_count >= min_training_samples
            and positives >= min_positive_samples
            and negatives >= min_negative_samples
            and unique_positive_events >= min_unique_positive_events
        ):
            overall_rate = Decimal(positives) / Decimal(training_count)
            rates, counts = _monotonic_rates(
                resolved,
                bin_count=bin_count,
                overall_rate=overall_rate,
                prior_strength=prior_strength,
            )
            if counts[index] >= min_bin_samples and index in rates:
                probability = rates[index]
                baseline = overall_rate.quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)
                confidence = (
                    "high"
                    if counts[index] >= min_bin_samples * 4
                    else "medium"
                    if counts[index] >= min_bin_samples * 2
                    else "low"
                )
        latest_resolved = max((item.horizon_end for item in resolved), default=None)
        predictions.append(
            CalibratedPrediction(
                sample=sample,
                calibrated_probability=probability,
                baseline_probability=baseline,
                confidence=confidence,
                training_sample_count=training_count,
                calibration_bin=index,
                latest_training_horizon_end=latest_resolved,
            )
        )

    return BacktestResult(
        scenario_code=ordered[0].scenario_code,
        horizon=next(iter(horizons)),
        predictions=tuple(predictions),
        calibration_bins=_calibration_curve(predictions, bin_count=bin_count),
        metrics=_metrics(predictions, decision_threshold=decision_threshold),
    )
