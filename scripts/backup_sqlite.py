from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def online_backup(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError(destination)
    source_uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=10) as source_db:
        with sqlite3.connect(destination) as target_db:
            source_db.backup(target_db, pages=256, sleep=0.05)
    destination.chmod(0o600)
    with sqlite3.connect(f"file:{destination.resolve()}?mode=ro", uri=True) as restored:
        integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity}")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    destination.with_suffix(destination.suffix + ".sha256").chmod(0o600)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an online SQLite backup")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    digest = online_backup(args.source, args.destination)
    print(f"backup_ok sha256={digest} created_at={datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
