from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backup_operations import (  # noqa: E402
    apply_retention,
    copy_verified_off_host,
    create_postgres_backup,
    encrypt_verified_file_age,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create, encrypt and copy a verified PostgreSQL custom-format backup"
    )
    parser.add_argument("--dsn", default=os.getenv("CRISIS_POSTGRES_DSN"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--age-recipient", default=os.getenv("BACKUP_AGE_RECIPIENT"))
    parser.add_argument(
        "--off-host-directory", type=Path, default=os.getenv("OFF_HOST_BACKUP_DIRECTORY")
    )
    parser.add_argument("--apply-retention", action="store_true")
    parser.add_argument("--daily", type=int, default=7)
    parser.add_argument("--weekly", type=int, default=4)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or CRISIS_POSTGRES_DSN is required")
    if args.off_host_directory and not args.age_recipient:
        parser.error("--off-host-directory requires --age-recipient")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.directory / f"trading-assistant-postgres-{timestamp}.dump"
    payload = create_postgres_backup(args.dsn, target)
    if args.age_recipient:
        encrypted = target.with_suffix(target.suffix + ".age")
        payload["encryption"] = encrypt_verified_file_age(
            target, encrypted, recipient=args.age_recipient
        )
        if args.off_host_directory:
            payload["off_host"] = copy_verified_off_host(encrypted, args.off_host_directory)
            if args.apply_retention:
                payload["retention"] = apply_retention(
                    args.off_host_directory, daily=args.daily, weekly=args.weekly
                )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
