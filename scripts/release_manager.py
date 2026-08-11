from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _release_directory(releases_root: Path, release: str) -> Path:
    if not release or release in {".", ".."} or "/" in release or "\\" in release:
        raise ValueError("release must be a single directory name")
    root = releases_root.resolve(strict=True)
    candidate = releases_root / release
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("release must be an existing immutable directory")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != root:
        raise ValueError("release is outside releases root")
    return resolved


def validate_release_manifest(
    releases_root: Path, release: str, *, expected_schema: int | None = None
) -> dict:
    directory = _release_directory(releases_root, release)
    path = directory / "release-manifest.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"version", "release", "schema_version", "source_commit", "created_at"}
    if not required.issubset(payload):
        raise ValueError("release manifest is incomplete")
    if payload["version"] != 1 or payload["release"] != release:
        raise ValueError("release manifest identity mismatch")
    if not isinstance(payload["schema_version"], int) or payload["schema_version"] < 1:
        raise ValueError("release manifest schema version is invalid")
    if expected_schema is not None and payload["schema_version"] != expected_schema:
        raise ValueError(
            f"release schema {payload['schema_version']} is incompatible with database schema {expected_schema}"
        )
    if not isinstance(payload["source_commit"], str) or len(payload["source_commit"]) < 7:
        raise ValueError("release manifest source commit is invalid")
    return payload


def _current_release(current_link: Path, releases_root: Path) -> str | None:
    if not current_link.exists() and not current_link.is_symlink():
        return None
    if not current_link.is_symlink():
        raise ValueError("current path must be a symlink")
    target = current_link.resolve(strict=True)
    root = releases_root.resolve(strict=True)
    if target.parent != root:
        raise ValueError("current release points outside releases root")
    return target.name


def _switch_link(current_link: Path, target: Path) -> None:
    current_link.parent.mkdir(parents=True, exist_ok=True)
    temporary = current_link.with_name(f".{current_link.name}.next")
    temporary.unlink(missing_ok=True)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, current_link)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def release_status(releases_root: Path, current_link: Path, state_path: Path) -> dict:
    current = _current_release(current_link, releases_root)
    state = {}
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    return {
        "ok": current is not None,
        "current": current,
        "previous": state.get("previous"),
        "state_file": str(state_path),
    }


def activate_release(
    releases_root: Path,
    release: str,
    current_link: Path,
    state_path: Path,
    *,
    expected_schema: int | None = None,
) -> dict:
    target = _release_directory(releases_root, release)
    manifest = None
    if expected_schema is not None:
        manifest = validate_release_manifest(
            releases_root, release, expected_schema=expected_schema
        )
    previous = _current_release(current_link, releases_root)
    if previous == release:
        return {
            "ok": True,
            "changed": False,
            "current": release,
            "previous": previous,
            "manifest": manifest,
        }
    _switch_link(current_link, target)
    payload = {
        "version": 1,
        "current": release,
        "previous": previous,
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(state_path, payload)
    return {
        "ok": True,
        "changed": True,
        "current": release,
        "previous": previous,
        "manifest": manifest,
    }


def rollback_release(
    releases_root: Path,
    current_link: Path,
    state_path: Path,
    *,
    expected_schema: int | None = None,
) -> dict:
    if not state_path.is_file():
        raise FileNotFoundError(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    previous = state.get("previous")
    if not previous:
        raise RuntimeError("no previous immutable release recorded")
    target = _release_directory(releases_root, previous)
    manifest = None
    if expected_schema is not None:
        manifest = validate_release_manifest(
            releases_root, previous, expected_schema=expected_schema
        )
    current = _current_release(current_link, releases_root)
    _switch_link(current_link, target)
    payload = {
        "version": 1,
        "current": previous,
        "previous": current,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "reason": "rollback",
    }
    write_json_atomic(state_path, payload)
    return {
        "ok": True,
        "changed": True,
        "current": previous,
        "previous": current,
        "manifest": manifest,
    }
