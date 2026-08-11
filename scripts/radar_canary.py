from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from scripts.soak_check import probe, validate_base_url  # noqa: E402
from trading_bot.crisis_radar.canary import (  # noqa: E402
    collect_database_metrics,
    update_canary_manifest,
)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Persistent radar-specific 14-day production canary sample"
    )
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3"))
    parser.add_argument("--backup-directory", default="data/backups")
    parser.add_argument("--manifest", default="data/canary/crisis-radar-v2.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--release", required=True)
    parser.add_argument("--methodology", default="candidate-v11")
    parser.add_argument("--timeout-seconds", type=float, default=5)
    args = parser.parse_args()
    base_url = validate_base_url(args.base_url)
    now = datetime.now(timezone.utc)
    health = {
        "live": probe(base_url, "/health/live", timeout_seconds=args.timeout_seconds).ok,
        "ready": probe(base_url, "/health/ready", timeout_seconds=args.timeout_seconds).ok,
    }
    metrics = collect_database_metrics(
        Path(args.database).expanduser(),
        backup_directory=Path(args.backup_directory).expanduser(),
        now=now,
    )
    manifest = update_canary_manifest(
        Path(args.manifest).expanduser(),
        sample_at=now,
        release=args.release,
        methodology=args.methodology,
        metrics=metrics,
        http_health=health,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "started_at": manifest["started_at"],
                "expected_end_at": manifest["expected_end_at"],
                "last_sample_at": manifest["last_sample_at"],
                "sample_count": manifest["sample_count"],
                "incident_count": manifest["incident_count"],
                "critical_incident_count": manifest["critical_incident_count"],
                "checksum": manifest["checksum"],
            },
            sort_keys=True,
        )
    )
    raise SystemExit(1 if manifest["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
