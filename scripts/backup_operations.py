from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


BACKUP_NAME = re.compile(
    r"^trading-assistant-(?P<timestamp>\d{8}T\d{6}Z)\.sqlite3(?:\.age)?$"
)
POSTGRES_BACKUP_NAME = re.compile(
    r"^trading-assistant-postgres-(?P<timestamp>\d{8}T\d{6}Z)\.dump(?:\.age)?$"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sidecar(path: Path) -> str:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        raise FileNotFoundError(sidecar)
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
        raise ValueError("invalid backup checksum sidecar")
    return parts[0]


def verify_file_checksum(path: Path) -> str:
    expected = _read_sidecar(path)
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError("backup checksum mismatch")
    return actual


def inspect_sqlite(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {}
        for table in tables:
            quoted_table = table.replace('"', '""')
            counts[table] = connection.execute(
                f'SELECT count(*) FROM "{quoted_table}"'
            ).fetchone()[0]
        schema = None
        if "schema_migrations" in tables:
            schema = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    return {
        "integrity": integrity,
        "foreign_key_errors": len(foreign_keys),
        "schema": schema,
        "table_counts": counts,
    }


def verify_sqlite_backup(path: Path, *, require_sidecar: bool = True) -> dict:
    digest = verify_file_checksum(path) if require_sidecar else sha256_file(path)
    inspection = inspect_sqlite(path)
    return {
        "ok": inspection["integrity"] == "ok" and inspection["foreign_key_errors"] == 0,
        "backup": path.name,
        "sha256": digest,
        **inspection,
    }


def restore_drill(backup: Path, *, destination: Path | None = None) -> dict:
    verified = verify_sqlite_backup(backup)
    if not verified["ok"]:
        raise RuntimeError("backup verification failed")
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if destination is None:
        temporary_directory = tempfile.TemporaryDirectory(prefix="trading-assistant-restore-")
        destination = Path(temporary_directory.name) / "restored.sqlite3"
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError(destination)
    with sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True, timeout=10) as source:
        with sqlite3.connect(destination) as target:
            source.backup(target, pages=256, sleep=0.05)
    destination.chmod(0o600)
    restored = inspect_sqlite(destination)
    same_counts = restored["table_counts"] == verified["table_counts"]
    result = {
        "ok": restored["integrity"] == "ok"
        and restored["foreign_key_errors"] == 0
        and same_counts,
        "source_backup": backup.name,
        "source_sha256": verified["sha256"],
        "restored_schema": restored["schema"],
        "restored_table_counts": restored["table_counts"],
        "table_counts_match": same_counts,
        "destination_preserved": temporary_directory is None,
    }
    if temporary_directory is not None:
        temporary_directory.cleanup()
    return result


def encrypt_backup_age(
    source: Path,
    destination: Path,
    *,
    recipient: str,
    age_binary: str = "age",
) -> dict:
    if not recipient.strip():
        raise ValueError("age recipient is required")
    verified = verify_sqlite_backup(source)
    if not verified["ok"]:
        raise RuntimeError("backup verification failed")
    result = encrypt_verified_file_age(
        source,
        destination,
        recipient=recipient,
        age_binary=age_binary,
    )
    result["plaintext_sha256"] = verified["sha256"]
    return result


def encrypt_verified_file_age(
    source: Path,
    destination: Path,
    *,
    recipient: str,
    age_binary: str = "age",
) -> dict:
    if not recipient.strip():
        raise ValueError("age recipient is required")
    source_digest = verify_file_checksum(source)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() or destination.with_suffix(destination.suffix + ".sha256").exists():
        raise FileExistsError(destination)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(descriptor)
    temp = Path(temp_name)
    try:
        with temp.open("wb") as output:
            subprocess.run(
                [age_binary, "--recipient", recipient, "--output", "-", str(source)],
                stdout=output,
                stderr=subprocess.PIPE,
                check=True,
                timeout=300,
            )
            output.flush()
            os.fsync(output.fileno())
        if temp.stat().st_size == 0:
            raise RuntimeError("age produced an empty encrypted backup")
        temp.chmod(0o600)
        os.replace(temp, destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    digest = sha256_file(destination)
    sidecar = destination.with_suffix(destination.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    sidecar.chmod(0o600)
    return {
        "ok": True,
        "encrypted_backup": destination.name,
        "sha256": digest,
        "plaintext_sha256": source_digest,
    }


def create_postgres_backup(
    dsn: str,
    destination: Path,
    *,
    pg_dump_binary: str = "pg_dump",
    pg_restore_binary: str = "pg_restore",
) -> dict:
    if not dsn.strip():
        raise ValueError("PostgreSQL DSN is required")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() or destination.with_suffix(destination.suffix + ".sha256").exists():
        raise FileExistsError(destination)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    environment = os.environ.copy()
    environment["PGDATABASE"] = dsn
    try:
        subprocess.run(
            [pg_dump_binary, "--format=custom", "--no-owner", "--file", str(temporary)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=1800,
        )
        if temporary.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty backup")
        subprocess.run(
            [pg_restore_binary, "--list", str(temporary)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=300,
        )
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    digest = sha256_file(destination)
    sidecar = destination.with_suffix(destination.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    sidecar.chmod(0o600)
    return {"ok": True, "backup": destination.name, "sha256": digest, "format": "pg-custom"}


def postgres_restore_drill(
    backup: Path,
    target_dsn: str,
    *,
    confirm_disposable_target: bool,
    pg_restore_binary: str = "pg_restore",
) -> dict:
    if not confirm_disposable_target:
        raise ValueError("explicit disposable-target confirmation is required")
    if not target_dsn.strip():
        raise ValueError("disposable PostgreSQL target DSN is required")
    digest = verify_file_checksum(backup)
    environment = os.environ.copy()
    environment["PGDATABASE"] = target_dsn
    subprocess.run(
        [
            pg_restore_binary,
            "--exit-on-error",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--dbname=",
            str(backup),
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
        timeout=1800,
    )
    return {
        "ok": True,
        "backup": backup.name,
        "sha256": digest,
        "target": "disposable target (DSN redacted)",
    }


def copy_verified_off_host(source: Path, destination_directory: Path) -> dict:
    if source.suffix != ".age":
        raise ValueError("off-host copy accepts only age-encrypted backups")
    digest = verify_file_checksum(source)
    destination_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = destination_directory / source.name
    sidecar = destination.with_suffix(destination.suffix + ".sha256")
    if destination.exists() or sidecar.exists():
        raise FileExistsError(destination)
    temporary = destination.with_name(f".{destination.name}.partial")
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o600)
        if sha256_file(temporary) != digest:
            raise RuntimeError("off-host copy checksum mismatch")
        os.replace(temporary, destination)
        sidecar.write_text(f"{digest}  {destination.name}\n", encoding="ascii")
        sidecar.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "source": source.name,
        "destination": str(destination),
        "sha256": digest,
    }


def _backup_timestamp(path: Path) -> datetime | None:
    match = BACKUP_NAME.match(path.name) or POSTGRES_BACKUP_NAME.match(path.name)
    if not match:
        return None
    return datetime.strptime(match.group("timestamp"), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )


def retention_plan(directory: Path, *, daily: int = 7, weekly: int = 4) -> dict:
    if daily < 1 or weekly < 0:
        raise ValueError("retention requires at least one daily copy")
    candidates = []
    for path in directory.glob("trading-assistant-*.sqlite3.age"):
        timestamp = _backup_timestamp(path)
        if timestamp is None:
            continue
        try:
            verify_file_checksum(path)
        except (FileNotFoundError, ValueError):
            continue
        candidates.append((timestamp, path))
    candidates.sort(reverse=True)
    keep: set[Path] = set()
    seen_days = set()
    for timestamp, path in candidates:
        if len(seen_days) >= daily:
            break
        if timestamp.date() not in seen_days:
            keep.add(path)
            seen_days.add(timestamp.date())
    seen_weeks = set()
    for timestamp, path in candidates:
        week = timestamp.isocalendar()[:2]
        if len(seen_weeks) >= weekly:
            break
        if week not in seen_weeks:
            keep.add(path)
            seen_weeks.add(week)
    remove = [path for _, path in candidates if path not in keep]
    return {
        "ok": bool(candidates),
        "verified_candidates": len(candidates),
        "keep": [path.name for path in sorted(keep)],
        "remove": [path.name for path in sorted(remove)],
        "policy": {"daily": daily, "weekly": weekly},
    }


def apply_retention(directory: Path, *, daily: int = 7, weekly: int = 4) -> dict:
    plan = retention_plan(directory, daily=daily, weekly=weekly)
    if not plan["ok"]:
        raise RuntimeError("no verified encrypted backups; refusing retention")
    if len(plan["keep"]) < 1:
        raise RuntimeError("retention would remove every backup")
    removed = []
    for name in plan["remove"]:
        path = directory / name
        sidecar = path.with_suffix(path.suffix + ".sha256")
        path.unlink()
        sidecar.unlink()
        removed.append(name)
    return {**plan, "applied": True, "removed": removed}
