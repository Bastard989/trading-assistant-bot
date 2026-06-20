from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backup_sqlite import online_backup  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a timestamped verified SQLite backup")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.directory / f"trading-assistant-{timestamp}.sqlite3"
    digest = online_backup(args.source, target)
    print(f"verified_backup={target.name} sha256={digest}")


if __name__ == "__main__":
    main()
