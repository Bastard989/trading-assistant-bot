from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from trading_bot.crisis_radar.backtest import (
    ScenarioEvent,
    SignalPoint,
    build_labeled_samples,
    walk_forward_calibrate,
)
from trading_bot.crisis_radar.catalog import METHODOLOGY_CODE, METHODOLOGY_VERSION
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_SIGNALS = 20_000
MAX_EVENTS = 500


def _datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def parse_payload(payload: Any) -> tuple[dict, tuple[SignalPoint, ...], tuple[ScenarioEvent, ...]]:
    if not isinstance(payload, dict):
        raise ValueError("backtest input must be a JSON object")
    scenario_code = str(payload.get("scenario_code", "")).strip()
    if not scenario_code or len(scenario_code) > 80 or not scenario_code.replace("_", "").isalnum():
        raise ValueError("scenario_code is invalid")
    methodology = payload.get("methodology", {})
    if not isinstance(methodology, dict):
        raise ValueError("methodology must be an object")
    methodology_code = str(methodology.get("code", METHODOLOGY_CODE)).strip()
    methodology_version = str(methodology.get("version", METHODOLOGY_VERSION)).strip()
    if not methodology_code or not methodology_version:
        raise ValueError("methodology code and version must not be empty")
    horizon_days = _bounded_int(payload.get("horizon_days"), "horizon_days", 1, 3650)

    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list) or not 1 <= len(raw_signals) <= MAX_SIGNALS:
        raise ValueError(f"signals must contain between 1 and {MAX_SIGNALS} items")
    signals = []
    for index, item in enumerate(raw_signals):
        if not isinstance(item, dict):
            raise ValueError(f"signals[{index}] must be an object")
        signals.append(
            SignalPoint(
                scenario_code=scenario_code,
                predicted_at=_datetime(item.get("as_of"), f"signals[{index}].as_of"),
                signal_score=_decimal(item.get("score"), f"signals[{index}].score"),
            )
        )

    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list) or len(raw_events) > MAX_EVENTS:
        raise ValueError(f"events must contain no more than {MAX_EVENTS} items")
    events = []
    for index, item in enumerate(raw_events):
        if not isinstance(item, dict):
            raise ValueError(f"events[{index}] must be an object")
        ended_at = item.get("ended_at")
        events.append(
            ScenarioEvent(
                scenario_code=scenario_code,
                started_at=_datetime(item.get("started_at"), f"events[{index}].started_at"),
                ended_at=(
                    None
                    if ended_at in {None, ""}
                    else _datetime(ended_at, f"events[{index}].ended_at")
                ),
            )
        )

    calibration = payload.get("calibration", {})
    if not isinstance(calibration, dict):
        raise ValueError("calibration must be an object")
    config = {
        "methodology_code": methodology_code,
        "methodology_version": methodology_version,
        "scenario_code": scenario_code,
        "horizon_days": horizon_days,
        "bin_count": _bounded_int(calibration.get("bin_count", 5), "bin_count", 2, 20),
        "min_training_samples": _bounded_int(
            calibration.get("min_training_samples", 20),
            "min_training_samples",
            1,
            MAX_SIGNALS,
        ),
        "min_bin_samples": _bounded_int(
            calibration.get("min_bin_samples", 5), "min_bin_samples", 1, MAX_SIGNALS
        ),
        "min_positive_samples": _bounded_int(
            calibration.get("min_positive_samples", 2),
            "min_positive_samples",
            1,
            MAX_SIGNALS,
        ),
        "min_negative_samples": _bounded_int(
            calibration.get("min_negative_samples", 2),
            "min_negative_samples",
            1,
            MAX_SIGNALS,
        ),
        "min_unique_positive_events": _bounded_int(
            calibration.get("min_unique_positive_events", 3),
            "min_unique_positive_events",
            1,
            MAX_EVENTS,
        ),
        "prior_strength": _decimal(calibration.get("prior_strength", "4"), "prior_strength"),
        "decision_threshold": _decimal(
            calibration.get("decision_threshold", "0.5"), "decision_threshold"
        ),
    }
    return config, tuple(signals), tuple(events)


def _metrics_payload(metrics) -> dict:
    return {
        "sample_count": metrics.sample_count,
        "scored_count": metrics.scored_count,
        "positive_count": metrics.positive_count,
        "positive_event_count": metrics.positive_event_count,
        "coverage": format(metrics.coverage, "f"),
        "brier_score": None if metrics.brier_score is None else format(metrics.brier_score, "f"),
        "baseline_brier_score": (
            None
            if metrics.baseline_brier_score is None
            else format(metrics.baseline_brier_score, "f")
        ),
        "log_loss": None if metrics.log_loss is None else format(metrics.log_loss, "f"),
        "precision": None if metrics.precision is None else format(metrics.precision, "f"),
        "recall": None if metrics.recall is None else format(metrics.recall, "f"),
        "false_alert_rate": (
            None
            if metrics.false_alert_rate is None
            else format(metrics.false_alert_rate, "f")
        ),
        "average_lead_days": (
            None
            if metrics.average_lead_days is None
            else format(metrics.average_lead_days, "f")
        ),
    }


def execute_payload(
    payload: Any,
    *,
    repository: CrisisRadarRepository | None = None,
    completed_at: datetime | None = None,
) -> dict:
    config, signals, events = parse_payload(payload)
    samples = build_labeled_samples(
        signals,
        events,
        horizon=timedelta(days=config["horizon_days"]),
    )
    result = walk_forward_calibrate(
        samples,
        bin_count=config["bin_count"],
        min_training_samples=config["min_training_samples"],
        min_bin_samples=config["min_bin_samples"],
        min_positive_samples=config["min_positive_samples"],
        min_negative_samples=config["min_negative_samples"],
        min_unique_positive_events=config["min_unique_positive_events"],
        prior_strength=config["prior_strength"],
        decision_threshold=config["decision_threshold"],
    )
    parameters = {
        **config,
        "prior_strength": format(config["prior_strength"], "f"),
        "decision_threshold": format(config["decision_threshold"], "f"),
        "engine": "walk-forward-v1",
        "probability_policy": "hidden_until_sufficient_resolved_history",
    }
    run_id = None
    if repository is not None:
        run_id = repository.save_backtest_result(
            result,
            methodology_code=config["methodology_code"],
            methodology_version=config["methodology_version"],
            parameters=parameters,
            completed_at=completed_at,
        )
    latest = result.predictions[-1]
    return {
        "run_id": run_id,
        "engine": "walk-forward-v1",
        "scenario_code": result.scenario_code,
        "horizon_days": config["horizon_days"],
        "probability_policy": "hidden_until_sufficient_resolved_history",
        "latest_probability": (
            None
            if latest.calibrated_probability is None
            else format(latest.calibrated_probability, "f")
        ),
        "latest_confidence": latest.confidence,
        "metrics": _metrics_payload(result.metrics),
        "calibration_bins": [
            {
                "index": item.index,
                "lower": format(item.lower, "f"),
                "upper": format(item.upper, "f"),
                "prediction_count": item.prediction_count,
                "average_probability": format(item.average_probability, "f"),
                "observed_rate": format(item.observed_rate, "f"),
            }
            for item in result.calibration_bins
        ],
    }


def _require_current_schema(database: Database) -> None:
    try:
        with database.connect() as connection:
            version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError("Database is not initialized; run the migrate command first") from exc
    if version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is {version}, expected {CURRENT_SCHEMA_VERSION}; run migrate first"
        )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run a leakage-safe Crisis Radar walk-forward calibration"
    )
    parser.add_argument("--input", required=True, help="JSON file with signals and labeled events")
    parser.add_argument(
        "--database",
        default=os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3"),
        help="SQLite database used to persist the audit trail",
    )
    parser.add_argument("--dry-run", action="store_true", help="Calculate without saving a run")
    args = parser.parse_args()
    input_path = Path(args.input).expanduser()
    if not input_path.is_file() or input_path.stat().st_size > MAX_INPUT_BYTES:
        raise SystemExit("input file is missing or exceeds 2 MB")
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("input file must contain valid UTF-8 JSON") from exc
    repository = None
    if not args.dry_run:
        database = Database(Path(args.database).expanduser(), auto_migrate=False)
        _require_current_schema(database)
        repository = CrisisRadarRepository(database)
    try:
        result = execute_payload(
            payload,
            repository=repository,
            completed_at=datetime.now(timezone.utc),
        )
    except (ValueError, LookupError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
