from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backup_sqlite import online_backup  # noqa: E402
from scripts.backup_operations import (  # noqa: E402
    apply_retention,
    copy_verified_off_host,
    encrypt_backup_age,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a timestamped verified SQLite backup")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--age-recipient", default=os.getenv("BACKUP_AGE_RECIPIENT"))
    parser.add_argument("--off-host-directory", type=Path, default=os.getenv("OFF_HOST_BACKUP_DIRECTORY"))
    parser.add_argument("--apply-retention", action="store_true")
    parser.add_argument("--remove-local-plaintext-after-off-host", action="store_true")
    parser.add_argument("--daily", type=int, default=7)
    parser.add_argument("--weekly", type=int, default=4)
    args = parser.parse_args()
    if args.off_host_directory and not args.age_recipient:
        parser.error("--off-host-directory requires --age-recipient")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.directory / f"trading-assistant-{timestamp}.sqlite3"
    digest = online_backup(args.source, target)
    payload = {"ok": True, "verified_backup": target.name, "sha256": digest}
    if args.age_recipient:
        encrypted = target.with_suffix(target.suffix + ".age")
        payload["encryption"] = encrypt_backup_age(
            target, encrypted, recipient=args.age_recipient
        )
        if args.off_host_directory:
            payload["off_host"] = copy_verified_off_host(encrypted, args.off_host_directory)
            if args.apply_retention:
                payload["local_retention"] = apply_retention(
                    args.directory, daily=args.daily, weekly=args.weekly
                )
                payload["off_host_retention"] = apply_retention(
                    args.off_host_directory, daily=args.daily, weekly=args.weekly
                )
            if args.remove_local_plaintext_after_off_host:
                sidecar = target.with_suffix(target.suffix + ".sha256")
                target.unlink()
                sidecar.unlink()
                payload["local_plaintext_removed"] = True
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
