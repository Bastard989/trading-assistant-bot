from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from scripts.backup_sqlite import online_backup  # noqa: E402
from scripts.backup_operations import (  # noqa: E402
    apply_retention,
    copy_verified_off_host,
    create_postgres_backup,
    encrypt_backup_age,
    encrypt_verified_file_age,
    postgres_restore_drill,
    restore_drill,
    retention_plan,
    verify_sqlite_backup,
)
from scripts.release_manager import (  # noqa: E402
    activate_release,
    release_status,
    rollback_release,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository  # noqa: E402
from trading_bot.crisis_radar.service import CrisisRadarService  # noqa: E402
from trading_bot.crisis_radar.source_registry import source_registry_payload  # noqa: E402
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database  # noqa: E402


PROFILES = ("basic-local", "advanced-local", "server")
SERVICE_UNITS = ("trading-assistant-api", "trading-assistant-bot")


def _configured(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value and not value.startswith("put_") and value.lower() != "change-me")


def doctor(database_path: Path, *, profile: str) -> dict:
    checks = []

    def check(code: str, ok: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"code": code, "ok": ok, "required": required, "detail": detail})

    check("profile", profile in PROFILES, profile)
    check("telegram_token", _configured("TELEGRAM_BOT_TOKEN"), "configured" if _configured("TELEGRAM_BOT_TOKEN") else "missing")
    check(
        "owner_allowlist",
        _configured("ALLOWED_TELEGRAM_USER_IDS"),
        "configured" if _configured("ALLOWED_TELEGRAM_USER_IDS") else "missing",
    )
    check("database_exists", database_path.exists(), str(database_path))
    if database_path.exists():
        try:
            with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                schema = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            check("database_integrity", integrity == "ok", str(integrity))
            check("schema_current", schema == CURRENT_SCHEMA_VERSION, f"{schema}/{CURRENT_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            check("database_readable", False, type(exc).__name__)
    check("fred_key", _configured("FRED_API_KEY"), "optional official depth", required=False)
    check("bea_key", _configured("BEA_API_KEY"), "optional US GDP", required=False)
    check("eia_key", _configured("EIA_API_KEY"), "optional energy", required=False)
    if profile == "advanced-local":
        check("postgres_dsn", _configured("CRISIS_POSTGRES_DSN"), "advanced evidence memory")
    if profile == "server":
        web_url = os.getenv("WEB_APP_URL", "").strip()
        check("permanent_https", web_url.startswith("https://") and "trycloudflare.com" not in web_url, web_url or "missing")
        check("release_id", _configured("TRADING_ASSISTANT_RELEASE"), "required by canary")
        backup_directory = Path(os.getenv("BACKUP_DIRECTORY", "")).expanduser()
        off_host_directory = Path(os.getenv("OFF_HOST_BACKUP_DIRECTORY", "")).expanduser()
        check(
            "backup_directory",
            _configured("BACKUP_DIRECTORY") and backup_directory.is_dir(),
            "configured directory" if backup_directory.is_dir() else "missing or not a directory",
        )
        check(
            "backup_age_recipient",
            _configured("BACKUP_AGE_RECIPIENT"),
            "configured" if _configured("BACKUP_AGE_RECIPIENT") else "missing",
        )
        check(
            "age_binary",
            shutil.which("age") is not None,
            "available" if shutil.which("age") else "missing",
        )
        check(
            "off_host_backup_directory",
            _configured("OFF_HOST_BACKUP_DIRECTORY") and off_host_directory.is_dir(),
            "configured directory" if off_host_directory.is_dir() else "missing or not mounted",
        )
    required_failed = [item["code"] for item in checks if item["required"] and not item["ok"]]
    return {
        "ok": not required_failed,
        "profile": profile,
        "checks": checks,
        "required_failed": required_failed,
    }


def migrate_dry_run(database_path: Path) -> dict:
    if not database_path.exists():
        raise FileNotFoundError(database_path)
    with tempfile.TemporaryDirectory(prefix="trading-assistant-migrate-") as directory:
        copy = Path(directory) / "dry-run.sqlite3"
        source_digest = online_backup(database_path, copy)
        Database(copy, auto_migrate=True)
        with sqlite3.connect(copy) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            table_count = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
    return {
        "ok": integrity == "ok" and schema == CURRENT_SCHEMA_VERSION and not foreign_keys,
        "source_backup_sha256": source_digest,
        "integrity": integrity,
        "schema": schema,
        "foreign_key_errors": len(foreign_keys),
        "table_count": table_count,
        "working_database_changed": False,
    }


def source_contract_check() -> dict:
    payload = source_registry_payload()
    sources = payload["sources"]
    codes = [item["code"] for item in sources]
    failures = []
    if len(codes) != len(set(codes)):
        failures.append("duplicate_source_code")
    for item in sources:
        if not item["license_or_terms_url"].startswith("https://"):
            failures.append(f"{item['code']}:terms_not_https")
        if not item["expected_frequency"] or not item["publication_lag"]:
            failures.append(f"{item['code']}:missing_frequency_or_lag")
        if not item["rate_limit_policy"] or not item["operational_role"]:
            failures.append(f"{item['code']}:missing_operational_contract")
    return {
        "ok": not failures,
        "version": payload["version"],
        "source_count": len(sources),
        "failures": failures,
    }


def service_action(action: str, *, systemctl: str = "systemctl") -> dict:
    if action not in {"start", "stop", "restart", "status"}:
        raise ValueError("unsupported service action")
    units = list(SERVICE_UNITS)
    if action == "stop":
        units.reverse()
    results = []
    for unit in units:
        completed = subprocess.run(
            [systemctl, action, unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        results.append({"unit": unit, "returncode": completed.returncode})
        if completed.returncode != 0 and action != "status":
            break
    return {
        "ok": all(item["returncode"] == 0 for item in results),
        "action": action,
        "services": results,
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Self-hosted Trading Assistant operations")
    parser.add_argument(
        "command",
        choices=(
            "bootstrap",
            "doctor",
            "migrate-dry-run",
            "source-check",
            "backup-create",
            "backup-verify",
            "restore-drill",
            "backup-encrypt",
            "offhost-copy",
            "retention-plan",
            "retention-apply",
            "postgres-backup",
            "postgres-restore-drill",
            "release-status",
            "update",
            "rollback",
            "start",
            "stop",
            "restart",
            "status",
        ),
    )
    parser.add_argument("--profile", choices=PROFILES, default="basic-local")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3"))
    parser.add_argument("--backup")
    parser.add_argument("--output")
    parser.add_argument("--recipient", default=os.getenv("BACKUP_AGE_RECIPIENT"))
    parser.add_argument("--off-host-directory", default=os.getenv("OFF_HOST_BACKUP_DIRECTORY"))
    parser.add_argument("--backup-directory", default=os.getenv("BACKUP_DIRECTORY"))
    parser.add_argument("--daily", type=int, default=7)
    parser.add_argument("--weekly", type=int, default=4)
    parser.add_argument("--releases-root", default="/opt/trading-assistant/releases")
    parser.add_argument("--current-link", default="/opt/trading-assistant/current")
    parser.add_argument("--release-state", default="/var/lib/trading-assistant/release-state.json")
    parser.add_argument("--release")
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--postgres-dsn", default=os.getenv("CRISIS_POSTGRES_DSN"))
    parser.add_argument("--confirm-disposable-target", action="store_true")
    args = parser.parse_args()
    path = Path(args.database).expanduser()
    if args.command == "bootstrap":
        database = Database(path, auto_migrate=True)
        result = CrisisRadarService(CrisisRadarRepository(database)).bootstrap()
        payload = {"ok": True, "schema": CURRENT_SCHEMA_VERSION, "radar": result}
    elif args.command == "doctor":
        payload = doctor(path, profile=args.profile)
    elif args.command == "migrate-dry-run":
        payload = migrate_dry_run(path)
    elif args.command == "source-check":
        payload = source_contract_check()
    elif args.command == "backup-create":
        if not args.backup:
            parser.error("backup-create requires --backup")
        digest = online_backup(path, Path(args.backup).expanduser())
        payload = {"ok": True, "backup": Path(args.backup).name, "sha256": digest}
    elif args.command == "backup-verify":
        if not args.backup:
            parser.error("backup-verify requires --backup")
        payload = verify_sqlite_backup(Path(args.backup).expanduser())
    elif args.command == "restore-drill":
        if not args.backup:
            parser.error("restore-drill requires --backup")
        payload = restore_drill(
            Path(args.backup).expanduser(),
            destination=Path(args.output).expanduser() if args.output else None,
        )
    elif args.command == "backup-encrypt":
        if not args.backup or not args.recipient:
            parser.error("backup-encrypt requires --backup and --recipient")
        source = Path(args.backup).expanduser()
        output = Path(args.output).expanduser() if args.output else source.with_suffix(source.suffix + ".age")
        payload = encrypt_backup_age(source, output, recipient=args.recipient)
    elif args.command == "offhost-copy":
        if not args.backup or not args.off_host_directory:
            parser.error("offhost-copy requires --backup and --off-host-directory")
        payload = copy_verified_off_host(
            Path(args.backup).expanduser(), Path(args.off_host_directory).expanduser()
        )
    elif args.command in {"retention-plan", "retention-apply"}:
        if not args.backup_directory:
            parser.error(f"{args.command} requires --backup-directory")
        directory = Path(args.backup_directory).expanduser()
        payload = (
            retention_plan(directory, daily=args.daily, weekly=args.weekly)
            if args.command == "retention-plan"
            else apply_retention(directory, daily=args.daily, weekly=args.weekly)
        )
    elif args.command == "postgres-backup":
        if not args.postgres_dsn or not args.backup:
            parser.error("postgres-backup requires --postgres-dsn and --backup")
        backup_path = Path(args.backup).expanduser()
        payload = create_postgres_backup(args.postgres_dsn, backup_path)
        if args.recipient:
            encrypted = Path(args.output).expanduser() if args.output else backup_path.with_suffix(
                backup_path.suffix + ".age"
            )
            payload["encryption"] = encrypt_verified_file_age(
                backup_path, encrypted, recipient=args.recipient
            )
            if args.off_host_directory:
                payload["off_host"] = copy_verified_off_host(
                    encrypted, Path(args.off_host_directory).expanduser()
                )
    elif args.command == "postgres-restore-drill":
        if not args.postgres_dsn or not args.backup:
            parser.error("postgres-restore-drill requires disposable --postgres-dsn and --backup")
        payload = postgres_restore_drill(
            Path(args.backup).expanduser(),
            args.postgres_dsn,
            confirm_disposable_target=args.confirm_disposable_target,
        )
    elif args.command in {"release-status", "update", "rollback"}:
        releases_root = Path(args.releases_root).expanduser()
        current_link = Path(args.current_link).expanduser()
        state_path = Path(args.release_state).expanduser()
        if args.command == "release-status":
            payload = release_status(releases_root, current_link, state_path)
        elif args.command == "update":
            if not args.release:
                parser.error("update requires --release")
            if not args.backup:
                parser.error("update requires a verified pre-update --backup")
            backup_verification = verify_sqlite_backup(Path(args.backup).expanduser())
            migration = migrate_dry_run(path)
            activation = activate_release(
                releases_root,
                args.release,
                current_link,
                state_path,
                expected_schema=CURRENT_SCHEMA_VERSION,
            )
            payload = {
                "ok": backup_verification["ok"] and migration["ok"] and activation["ok"],
                "backup": backup_verification,
                "migration_dry_run": migration,
                "activation": activation,
                "services_restarted": False,
            }
        else:
            with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
                database_schema = connection.execute(
                    "SELECT max(version) FROM schema_migrations"
                ).fetchone()[0]
            payload = rollback_release(
                releases_root,
                current_link,
                state_path,
                expected_schema=database_schema,
            )
    else:
        payload = service_action(args.command, systemctl=args.systemctl)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    raise SystemExit(0 if payload.get("ok") else 1)


if __name__ == "__main__":
    main()
