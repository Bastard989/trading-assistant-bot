from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_manager import write_json_atomic  # noqa: E402
from trading_bot.db import CURRENT_SCHEMA_VERSION  # noqa: E402


def git_commit(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    commit = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("git returned an invalid commit")
    return commit


def build_release_manifest(
    release_directory: Path,
    *,
    release: str,
    source_commit: str,
    schema_version: int = CURRENT_SCHEMA_VERSION,
) -> dict:
    if release_directory.name != release:
        raise ValueError("release name must match immutable release directory")
    if not re.fullmatch(r"[0-9a-f]{7,40}", source_commit):
        raise ValueError("source commit must be a hexadecimal git commit")
    if schema_version < 1:
        raise ValueError("schema version must be positive")
    payload = {
        "version": 1,
        "release": release,
        "schema_version": schema_version,
        "source_commit": source_commit,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = release_directory / "release-manifest.json"
    if path.exists():
        raise FileExistsError(path)
    write_json_atomic(path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an immutable self-host release manifest")
    parser.add_argument("--release-directory", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    commit = args.source_commit or git_commit(args.repository)
    payload = build_release_manifest(
        args.release_directory,
        release=args.release,
        source_commit=commit,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
