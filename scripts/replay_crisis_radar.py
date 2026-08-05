from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from trading_bot.crisis_radar.backtest import SignalPoint, build_labeled_samples, walk_forward_calibrate
from trading_bot.crisis_radar.catalog import METHODOLOGY_CODE, METHODOLOGY_VERSION
from trading_bot.crisis_radar.replay import replay_scenario
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database
from trading_bot.crisis_radar.validation import evaluate_calibration_gate, threshold_sensitivity


UTC = timezone.utc
ALLOWED_HORIZON_DAYS = (1, 7, 15, 30, 90, 365)


def _calibration_acceptable(metrics, validation: dict | None = None) -> bool:
    validation = validation or {}
    return evaluate_calibration_gate(
        metrics,
        holdout_event_count=int(validation.get("holdout_event_count") or 0),
        sensitivity_stable=bool(validation.get("sensitivity_stable")),
        region_holdout_passed=bool(validation.get("region_holdout_passed")),
        crisis_holdout_passed=bool(validation.get("crisis_holdout_passed")),
    ).passed


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


def _catalog_coverage_end(catalog: dict) -> datetime | None:
    value = catalog.get("definition", {}).get("coverage_end")
    return None if not value else _aware_date(str(value), "catalog coverage_end", end_of_day=True)


def _catalog_coverage_start(catalog: dict) -> datetime | None:
    value = catalog.get("definition", {}).get("coverage_start")
    return None if not value else _aware_date(str(value), "catalog coverage_start")


def execute_replay(
    repository: CrisisRadarRepository,
    *,
    scenario_code: str,
    started_at: datetime,
    ended_at: datetime,
    cadence_days: int,
    horizon_days: int,
    catalog_version: str | None = None,
    minimum_coverage: Decimal = Decimal("0.50"),
    persist: bool = False,
) -> dict:
    if cadence_days < 1 or cadence_days > 366:
        raise ValueError("cadence_days must be between 1 and 366")
    if horizon_days not in ALLOWED_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be one of {ALLOWED_HORIZON_DAYS}")
    catalog = repository.event_catalog_payload(scenario_code, version=catalog_version)
    if catalog is None:
        raise LookupError("event catalog not found; run Crisis Radar bootstrap first")
    replay = replay_scenario(
        repository,
        scenario_code,
        started_at=started_at,
        ended_at=ended_at,
        step=timedelta(days=cadence_days),
        minimum_coverage=minimum_coverage,
    )
    replay_run_id = repository.save_replay_result(replay) if persist else None
    events = repository.event_catalog_events(catalog["catalog_id"])
    coverage_start = _catalog_coverage_start(catalog)
    coverage_end = _catalog_coverage_end(catalog)
    eligible_signals = tuple(
        item
        for item in replay.signals
        if item.backtest_eligible
        and (coverage_start is None or item.signal_at >= coverage_start)
        and (coverage_end is None or item.signal_at <= coverage_end)
    )
    signal_points = tuple(
        SignalPoint(item.scenario_code, item.signal_at, item.signal_score)
        for item in eligible_signals
    )
    samples = (
        build_labeled_samples(
            signal_points,
            events,
            horizon=timedelta(days=horizon_days),
            coverage_end=coverage_end,
            exclude_active_events=True,
        )
        if coverage_start is not None and coverage_end is not None
        else ()
    )
    backtest_run_id = None
    metrics = None
    probability = None
    confidence = "insufficient"
    reason = "no_resolved_samples"
    promotion_validation = {
        "holdout_event_count": 0,
        "sensitivity_stable": False,
        "region_holdout_passed": False,
        "crisis_holdout_passed": False,
        "status": "not_run",
    }
    if samples:
        result = walk_forward_calibrate(samples)
        metrics = {
            "sample_count": result.metrics.sample_count,
            "scored_count": result.metrics.scored_count,
            "positive_count": result.metrics.positive_count,
            "positive_event_count": result.metrics.positive_event_count,
            "coverage": format(result.metrics.coverage, "f"),
            "brier_score": (
                None if result.metrics.brier_score is None else format(result.metrics.brier_score, "f")
            ),
            "baseline_brier_score": (
                None
                if result.metrics.baseline_brier_score is None
                else format(result.metrics.baseline_brier_score, "f")
            ),
            "log_loss": (
                None if result.metrics.log_loss is None else format(result.metrics.log_loss, "f")
            ),
            "precision": (
                None if result.metrics.precision is None else format(result.metrics.precision, "f")
            ),
            "recall": None if result.metrics.recall is None else format(result.metrics.recall, "f"),
            "false_alert_rate": (
                None
                if result.metrics.false_alert_rate is None
                else format(result.metrics.false_alert_rate, "f")
            ),
            "average_lead_days": (
                None
                if result.metrics.average_lead_days is None
                else format(result.metrics.average_lead_days, "f")
            ),
        }
        latest = result.predictions[-1]
        available = [
            item for item in result.predictions if item.calibrated_probability is not None
        ]
        sensitivity = threshold_sensitivity(
            (item.calibrated_probability for item in available),
            (item.sample.outcome for item in available),
        )
        holdout_start = max(0, len(result.predictions) * 4 // 5)
        holdout_events = len(
            {
                item.sample.event_at
                for item in result.predictions[holdout_start:]
                if item.sample.event_at is not None
            }
        )
        recalls = [
            Decimal(str(item["recall"]))
            for item in sensitivity
            if item["recall"] is not None
        ]
        sensitivity_stable = bool(
            len(recalls) >= 3 and max(recalls) - min(recalls) <= Decimal("0.20")
        )
        promotion_validation = {
            "holdout_event_count": holdout_events,
            "sensitivity_stable": sensitivity_stable,
            "region_holdout_passed": False,
            "crisis_holdout_passed": False,
            "status": "experimental",
            "threshold_sensitivity": sensitivity,
        }
        acceptable = _calibration_acceptable(result.metrics, promotion_validation)
        probability = (
            format(latest.calibrated_probability, "f")
            if acceptable and latest.calibrated_probability is not None
            else None
        )
        confidence = latest.confidence if probability is not None else "insufficient"
        if latest.calibrated_probability is None:
            reason = "insufficient_resolved_history"
        elif not acceptable:
            reason = "calibration_does_not_beat_baseline"
        else:
            reason = "historical_only"
        if persist:
            assert replay_run_id is not None
            backtest_run_id = repository.save_backtest_result(
                result,
                methodology_code=METHODOLOGY_CODE,
                methodology_version=METHODOLOGY_VERSION,
                parameters={
                    "engine": "walk-forward-v1",
                    "replay_engine": replay.engine_version,
                    "event_catalog_version": catalog["version"],
                    "event_catalog_checksum": catalog["checksum"],
                    "horizon_days": horizon_days,
                    "onset_policy": "exclude_active_events",
                    "right_censor_policy": "exclude_unresolved_horizons",
                    "probability_scope": "historical_only",
                    "promotion_validation": promotion_validation,
                },
            )
            repository.link_backtest_provenance(
                backtest_run_id,
                replay_run_id=replay_run_id,
                event_catalog_id=catalog["catalog_id"],
            )
    return {
        "scenario_code": scenario_code,
        "replay_engine": replay.engine_version,
        "replay_run_id": replay_run_id,
        "backtest_run_id": backtest_run_id,
        "result_checksum": replay.checksum,
        "signal_count": len(replay.signals),
        "eligible_signal_count": len(eligible_signals),
        "resolved_sample_count": len(samples),
        "event_catalog": {
            "version": catalog["version"],
            "checksum": catalog["checksum"],
            "event_count": len(catalog["labels"]),
            "limitations": catalog["limitations"],
        },
        "horizon_days": horizon_days,
        "historical_probability": probability,
        "confidence": confidence,
        "reason": reason,
        "metrics": metrics,
        "promotion_validation": promotion_validation,
        "live_probability": None,
        "live_probability_reason": "historical_calibration_is_not_a_live_prediction",
    }


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
        description="Replay deterministic Crisis Radar signals and run an official-label backtest"
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--from", dest="started_at", required=True)
    parser.add_argument("--through", dest="ended_at", required=True)
    parser.add_argument("--cadence-days", type=int, default=7)
    parser.add_argument("--horizon-days", type=int, choices=ALLOWED_HORIZON_DAYS, required=True)
    parser.add_argument("--catalog-version")
    parser.add_argument("--minimum-coverage", default="0.50")
    parser.add_argument(
        "--database",
        default=os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    database = Database(Path(args.database).expanduser(), auto_migrate=False)
    _require_current_schema(database)
    repository = CrisisRadarRepository(database)
    try:
        payload = execute_replay(
            repository,
            scenario_code=args.scenario,
            started_at=_aware_date(args.started_at, "from"),
            ended_at=_aware_date(args.ended_at, "through", end_of_day=True),
            cadence_days=args.cadence_days,
            horizon_days=args.horizon_days,
            catalog_version=args.catalog_version,
            minimum_coverage=Decimal(args.minimum_coverage),
            persist=not args.dry_run,
        )
    except (ValueError, LookupError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
