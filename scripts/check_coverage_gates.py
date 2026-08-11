from __future__ import annotations

import argparse
import json
from pathlib import Path


GROUPS = {
    "crisis_computational_core": {
        "minimum": 90.0,
        "suffixes": (
            "trading_bot/crisis_radar/coverage.py",
            "trading_bot/crisis_radar/scenario_fusion.py",
            "trading_bot/crisis_radar/scenario_v2.py",
            "trading_bot/crisis_radar/scoring_v2.py",
            "trading_bot/crisis_radar/stage_v2.py",
            "trading_bot/crisis_radar/states.py",
            "trading_bot/crisis_radar/thresholds.py",
            "trading_bot/crisis_radar/trends.py",
        ),
    },
    "auth_config_runtime_migrations": {
        "minimum": 80.0,
        "suffixes": (
            "trading_bot/auth.py",
            "trading_bot/config.py",
            "trading_bot/main.py",
            "trading_bot/crisis_radar/jobs.py",
            "trading_bot/db.py",
        ),
    },
    "postgres_evidence_memory": {
        "minimum": 85.0,
        "suffixes": ("trading_bot/crisis_radar/postgres_memory.py",),
    },
}


def evaluate_coverage(payload: dict) -> dict:
    files = payload.get("files", {})
    results = []
    for name, contract in GROUPS.items():
        matched = [
            data
            for path, data in files.items()
            if any(path.replace("\\", "/").endswith(suffix) for suffix in contract["suffixes"])
        ]
        if len(matched) != len(contract["suffixes"]):
            results.append(
                {
                    "name": name,
                    "ok": False,
                    "minimum": contract["minimum"],
                    "coverage": None,
                    "reason": "coverage report is missing required modules",
                }
            )
            continue
        statements = sum(item["summary"]["num_statements"] for item in matched)
        covered = sum(item["summary"]["covered_lines"] for item in matched)
        coverage = 100.0 if statements == 0 else covered / statements * 100
        results.append(
            {
                "name": name,
                "ok": coverage >= contract["minimum"],
                "minimum": contract["minimum"],
                "coverage": round(coverage, 2),
                "statements": statements,
            }
        )
    return {"ok": all(item["ok"] for item in results), "groups": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Enforce critical module coverage gates")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    result = evaluate_coverage(json.loads(args.report.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
