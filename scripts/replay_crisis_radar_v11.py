from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from trading_bot.crisis_radar.backtest import (
    SignalPoint,
    build_labeled_samples,
    walk_forward_calibrate,
)
from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_V11_VERSION,
    METHODOLOGY_V12_VERSION,
    METHODOLOGY_V13_VERSION,
    bootstrap_v12_catalog,
    bootstrap_v13_catalog,
)
from trading_bot.crisis_radar.replay import replay_scenario
from trading_bot.crisis_radar.replay_v2 import (
    replay_v11_scenario,
    replay_v12_scenario,
    replay_v13_scenario,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.validation import (
    evaluate_calibration_gate,
    threshold_sensitivity,
)
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database


UTC = timezone.utc
VARIANT_ORDER = (
    "v10_baseline",
    "economic_only",
    "historical_only",
    "full",
    "without_trend",
    "without_events",
    "without_contagion",
    "without_dependency_correction",
    "naive_base_rate",
)


def _aware_date(value: str, field: str, *, end_of_day: bool = False) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date or timezone-aware timestamp") from exc
    if parsed.tzinfo is None:
        if "T" in value:
            raise ValueError(f"{field} timestamp must be timezone-aware")
        parsed = datetime.combine(
            parsed.date(), time(23, 59, 59) if end_of_day else time.min, tzinfo=UTC
        )
    return parsed.astimezone(UTC)


def _metrics_payload(result) -> dict:
    payload = asdict(result.metrics)
    return {
        key: format(value, "f") if isinstance(value, Decimal) else value
        for key, value in payload.items()
    }


def _evaluate_signals(
    signals: tuple[SignalPoint, ...],
    events,
    *,
    horizon_days: int,
    coverage_end: datetime,
) -> tuple[dict, object | None]:
    samples = build_labeled_samples(
        signals,
        events,
        horizon=timedelta(days=horizon_days),
        coverage_end=coverage_end,
        exclude_active_events=True,
    )
    if not samples:
        return {
            "sample_count": 0,
            "status": "insufficient_resolved_samples",
            "probability": None,
        }, None
    result = walk_forward_calibrate(samples)
    payload = _metrics_payload(result)
    payload.update(
        {
            "status": "evaluated",
            "probability": None,
            "resolved_sample_count": len(samples),
        }
    )
    return payload, result


def _execute_candidate_comparison(
    repository: CrisisRadarRepository,
    *,
    methodology_version: str,
    replay_candidate,
    manifest_version: str,
    replay_checksum_key: str,
    scenario_code: str,
    started_at: datetime,
    ended_at: datetime,
    cadence_days: int,
    horizon_days: int,
    minimum_coverage: Decimal = Decimal(".70"),
) -> dict:
    catalog = repository.event_catalog_payload(scenario_code)
    if catalog is None:
        raise LookupError("event catalog not found; run Crisis Radar bootstrap first")
    coverage_start_raw = catalog.get("definition", {}).get("coverage_start")
    coverage_end_raw = catalog.get("definition", {}).get("coverage_end")
    if not coverage_start_raw or not coverage_end_raw:
        raise ValueError("event catalog needs explicit coverage_start and coverage_end")
    coverage_start = _aware_date(str(coverage_start_raw), "catalog coverage_start")
    coverage_end = _aware_date(
        str(coverage_end_raw), "catalog coverage_end", end_of_day=True
    )
    effective_start = max(started_at, coverage_start)
    effective_end = min(ended_at, coverage_end)
    if effective_end < effective_start:
        raise ValueError("requested replay does not overlap event catalog coverage")
    step = timedelta(days=cadence_days)
    v10 = replay_scenario(
        repository,
        scenario_code,
        started_at=effective_start,
        ended_at=effective_end,
        step=step,
        minimum_coverage=minimum_coverage,
    )
    candidate = replay_candidate(
        repository,
        scenario_code,
        started_at=effective_start,
        ended_at=effective_end,
        step=step,
        minimum_coverage=minimum_coverage,
    )
    full_signals = tuple(item for item in candidate.signals if item.variant == "full")
    eligibility_reasons = {
        reason: sum(item.eligibility_reason == reason for item in full_signals)
        for reason in sorted({item.eligibility_reason for item in full_signals})
    }
    stage_counts = {
        stage: sum(item.market_stage == stage for item in full_signals)
        for stage in sorted({item.market_stage for item in full_signals})
    }
    events = repository.event_catalog_events(catalog["catalog_id"])
    results: dict[str, dict] = {}
    evaluated = {}
    v10_points = tuple(
        SignalPoint(item.scenario_code, item.signal_at, item.signal_score)
        for item in v10.signals
        if item.backtest_eligible
    )
    results["v10_baseline"], evaluated["v10_baseline"] = _evaluate_signals(
        v10_points, events, horizon_days=horizon_days, coverage_end=coverage_end
    )
    for variant in VARIANT_ORDER[1:-1]:
        points = tuple(
            SignalPoint(scenario_code, item.signal_at, item.signal_score)
            for item in candidate.signals
            if item.variant == variant and item.backtest_eligible
        )
        results[variant], evaluated[variant] = _evaluate_signals(
            points, events, horizon_days=horizon_days, coverage_end=coverage_end
        )
        results[variant]["eligible_signal_count"] = len(points)
    full_result = evaluated.get("full")
    if full_result is None:
        results["naive_base_rate"] = {
            "status": "insufficient_resolved_samples",
            "brier_score": None,
            "probability": None,
        }
        sensitivity = ()
        gate = None
    else:
        results["naive_base_rate"] = {
            "status": "evaluated",
            "brier_score": results["full"].get("baseline_brier_score"),
            "probability": None,
            "note": "Expanding-window base rate used by the causal calibrator.",
        }
        available = tuple(
            item for item in full_result.predictions if item.calibrated_probability is not None
        )
        sensitivity = threshold_sensitivity(
            (item.calibrated_probability for item in available),
            (item.sample.outcome for item in available),
        )
        recalls = [
            Decimal(str(item["recall"]))
            for item in sensitivity
            if item["recall"] is not None
        ]
        stable = bool(len(recalls) >= 3 and max(recalls) - min(recalls) <= Decimal(".20"))
        holdout_start = len(full_result.predictions) * 4 // 5
        holdout_events = len(
            {
                item.sample.event_at
                for item in full_result.predictions[holdout_start:]
                if item.sample.event_at is not None
            }
        )
        gate = evaluate_calibration_gate(
            full_result.metrics,
            holdout_event_count=holdout_events,
            sensitivity_stable=stable,
            region_holdout_passed=False,
            crisis_holdout_passed=False,
        )
    payload = {
        "manifest_version": manifest_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "methodology": methodology_version,
        "candidate_status": "shadow",
        "scenario_code": scenario_code,
        "period": {
            "started_at": effective_start.isoformat(),
            "ended_at": effective_end.isoformat(),
            "cadence_days": cadence_days,
            "horizon_days": horizon_days,
            "minimum_coverage": format(minimum_coverage, "f"),
        },
        "causal_contract": {
            "observed_at_lte_cutoff": True,
            "released_at_lte_cutoff": True,
            "retrospective_revisions_excluded": True,
            "right_censoring": True,
            "active_events_excluded": True,
            "future_observations_allowed": False,
        },
        "catalog": {
            "version": catalog["version"],
            "checksum": catalog["checksum"],
            "event_count": len(catalog["labels"]),
            "regions": sorted({item["region_code"] for item in catalog["labels"]}),
        },
        "checksums": {
            "v10_replay": v10.checksum,
            replay_checksum_key: candidate.checksum,
        },
        "candidate_replay_diagnostics": {
            "cutoff_count": len(full_signals),
            "eligible_cutoff_count": sum(item.backtest_eligible for item in full_signals),
            "input_count_min": min((item.input_count for item in full_signals), default=0),
            "input_count_max": max((item.input_count for item in full_signals), default=0),
            "numeric_coverage_min": format(
                min((item.numeric_coverage for item in full_signals), default=Decimal("0")),
                "f",
            ),
            "numeric_coverage_max": format(
                max((item.numeric_coverage for item in full_signals), default=Decimal("0")),
                "f",
            ),
            "stage_counts": stage_counts,
            "eligibility_reason_counts": eligibility_reasons,
        },
        "results": {name: results[name] for name in VARIANT_ORDER},
        "ablation_findings": {
            "events_numeric_delta_expected": "zero: events do not alter numeric indicator/stage score",
            "contagion_numeric_delta_expected": (
                f"zero: contagion is diagnostic in {methodology_version}"
            ),
            "dependency_correction": "tested by treating every indicator/group as independent",
        },
        "threshold_sensitivity": sensitivity,
        "promotion_gate": (
            {
                "passed": gate.passed,
                "criteria": gate.criteria,
                "reasons": gate.reasons,
            }
            if gate is not None
            else {
                "passed": False,
                "criteria": {},
                "reasons": ("insufficient_resolved_samples",),
            }
        ),
        "live_probability": None,
        "live_probability_reason": (
            f"{methodology_version} remains shadow until all promotion gates pass"
        ),
    }
    global_coverages = tuple(
        item.global_numeric_coverage
        for item in full_signals
        if item.global_numeric_coverage is not None
    )
    if global_coverages:
        diagnostics = payload["candidate_replay_diagnostics"]
        diagnostics["coverage_contract"] = next(
            (
                item.coverage_contract
                for item in full_signals
                if item.coverage_contract is not None
            ),
            None,
        )
        diagnostics["global_numeric_coverage_min"] = format(
            min(global_coverages), "f"
        )
        diagnostics["global_numeric_coverage_max"] = format(
            max(global_coverages), "f"
        )
    checksum_payload = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    payload["manifest_checksum"] = hashlib.sha256(checksum_payload).hexdigest()
    return payload


def execute_v11_comparison(
    repository: CrisisRadarRepository,
    *,
    scenario_code: str,
    started_at: datetime,
    ended_at: datetime,
    cadence_days: int,
    horizon_days: int,
    minimum_coverage: Decimal = Decimal(".70"),
) -> dict:
    return _execute_candidate_comparison(
        repository,
        methodology_version=METHODOLOGY_V11_VERSION,
        replay_candidate=replay_v11_scenario,
        manifest_version="crisis-radar-v11-comparison-v1",
        replay_checksum_key="v11_replay",
        scenario_code=scenario_code,
        started_at=started_at,
        ended_at=ended_at,
        cadence_days=cadence_days,
        horizon_days=horizon_days,
        minimum_coverage=minimum_coverage,
    )


def execute_v12_comparison(
    repository: CrisisRadarRepository,
    *,
    scenario_code: str,
    started_at: datetime,
    ended_at: datetime,
    cadence_days: int,
    horizon_days: int,
    minimum_coverage: Decimal = Decimal(".70"),
) -> dict:
    """Compare replay-only v12 with v10 without changing the live methodology."""

    return _execute_candidate_comparison(
        repository,
        methodology_version=METHODOLOGY_V12_VERSION,
        replay_candidate=replay_v12_scenario,
        manifest_version="crisis-radar-v12-comparison-v1",
        replay_checksum_key="v12_replay",
        scenario_code=scenario_code,
        started_at=started_at,
        ended_at=ended_at,
        cadence_days=cadence_days,
        horizon_days=horizon_days,
        minimum_coverage=minimum_coverage,
    )


def execute_v13_comparison(
    repository: CrisisRadarRepository,
    *,
    scenario_code: str,
    started_at: datetime,
    ended_at: datetime,
    cadence_days: int,
    horizon_days: int,
    minimum_coverage: Decimal = Decimal(".70"),
) -> dict:
    """Compare replay-only v13 scenario coverage without changing live analysis."""

    return _execute_candidate_comparison(
        repository,
        methodology_version=METHODOLOGY_V13_VERSION,
        replay_candidate=replay_v13_scenario,
        manifest_version="crisis-radar-v13-comparison-v1",
        replay_checksum_key="v13_replay",
        scenario_code=scenario_code,
        started_at=started_at,
        ended_at=ended_at,
        cadence_days=cadence_days,
        horizon_days=horizon_days,
        minimum_coverage=minimum_coverage,
    )


def _require_current_schema(database: Database) -> None:
    try:
        with database.connect() as connection:
            version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError("Database is not initialized; run migrate first") from exc
    if version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is {version}, expected {CURRENT_SCHEMA_VERSION}; run migrate first"
        )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Causally compare a shadow candidate, its ablations, v10 and a naive base rate"
        )
    )
    parser.add_argument(
        "--methodology",
        choices=(
            METHODOLOGY_V11_VERSION,
            METHODOLOGY_V12_VERSION,
            METHODOLOGY_V13_VERSION,
        ),
        default=METHODOLOGY_V11_VERSION,
        help="Shadow candidate to replay; v12/v13 are registered disabled/replay-only.",
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--from", dest="started_at", required=True)
    parser.add_argument("--through", dest="ended_at", required=True)
    parser.add_argument("--cadence-days", type=int, default=30)
    parser.add_argument("--horizon-days", type=int, required=True)
    parser.add_argument("--minimum-coverage", default="0.70")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3"))
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.cadence_days < 1 or args.cadence_days > 366:
        raise SystemExit("cadence-days must be between 1 and 366")
    if args.horizon_days < 1 or args.horizon_days > 3650:
        raise SystemExit("horizon-days must be between 1 and 3650")
    database = Database(Path(args.database).expanduser(), auto_migrate=False)
    _require_current_schema(database)
    repository = CrisisRadarRepository(database)
    if args.methodology == METHODOLOGY_V13_VERSION:
        bootstrap_v13_catalog(repository)
        comparison = execute_v13_comparison
    elif args.methodology == METHODOLOGY_V12_VERSION:
        bootstrap_v12_catalog(repository)
        comparison = execute_v12_comparison
    else:
        comparison = execute_v11_comparison
    payload = comparison(
        repository,
        scenario_code=args.scenario,
        started_at=_aware_date(args.started_at, "from"),
        ended_at=_aware_date(args.ended_at, "through", end_of_day=True),
        cadence_days=args.cadence_days,
        horizon_days=args.horizon_days,
        minimum_coverage=Decimal(args.minimum_coverage),
    )
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
