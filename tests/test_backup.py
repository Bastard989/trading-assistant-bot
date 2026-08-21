from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts import backup_daily
from scripts.backup_operations import (
    apply_retention,
    copy_verified_off_host,
    create_postgres_backup,
    encrypt_backup_age,
    postgres_restore_drill,
    restore_drill,
    retention_plan,
    sha256_file,
    verify_sqlite_backup,
)
from scripts.backup_sqlite import online_backup


def test_online_backup_can_be_restored(tmp_path) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backups" / "backup.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('preserved')")
    digest = online_backup(source, destination)
    assert len(digest) == 64
    with sqlite3.connect(destination) as restored:
        assert restored.execute("SELECT value FROM sample").fetchone()[0] == "preserved"
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_daily_backup_cli_creates_verified_files(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")
    monkeypatch.setattr(sys, "argv", [
        "backup_daily.py", "--source", str(source), "--directory", str(destination),
    ])
    backup_daily.main()
    backups = list(destination.glob("*.sqlite3"))
    assert len(backups) == 1
    assert backups[0].with_suffix(".sqlite3.sha256").exists()


def test_daily_backup_removes_plaintext_only_after_verified_off_host_copy(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.sqlite3"
    local = tmp_path / "local"
    off_host = tmp_path / "off-host"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")

    def fake_encrypt(source_path, destination, *, recipient):
        assert recipient == "age1recipient"
        destination.write_bytes(b"encrypted")
        digest = sha256_file(destination)
        destination.with_suffix(destination.suffix + ".sha256").write_text(
            f"{digest}  {destination.name}\n", encoding="ascii"
        )
        return {"ok": True, "sha256": digest}

    monkeypatch.setattr(backup_daily, "encrypt_backup_age", fake_encrypt)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backup_daily.py",
            "--source",
            str(source),
            "--directory",
            str(local),
            "--age-recipient",
            "age1recipient",
            "--off-host-directory",
            str(off_host),
            "--apply-retention",
            "--remove-local-plaintext-after-off-host",
        ],
    )

    backup_daily.main()

    assert not list(local.glob("*.sqlite3"))
    assert len(list(local.glob("*.sqlite3.age"))) == 1
    assert len(list(off_host.glob("*.sqlite3.age"))) == 1


def test_verify_and_restore_drill_detect_content_and_counts(tmp_path) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "trading-assistant-20260811T100000Z.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO sample(value) VALUES (?)", [("a",), ("b",)])
    online_backup(source, backup)

    verified = verify_sqlite_backup(backup)
    drill = restore_drill(backup)

    assert verified["ok"] is True
    assert verified["table_counts"]["sample"] == 2
    assert drill["ok"] is True
    assert drill["table_counts_match"] is True
    assert drill["destination_preserved"] is False


def test_age_encryption_and_off_host_copy_are_checksum_verified(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "trading-assistant-20260811T100000Z.sqlite3"
    encrypted = backup.with_suffix(".sqlite3.age")
    off_host = tmp_path / "mounted-off-host"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")
    online_backup(source, backup)

    def fake_age(command, **kwargs):
        assert command[:4] == ["age", "--recipient", "age1recipient", "--output"]
        kwargs["stdout"].write(b"age-encrypted-test-payload")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.backup_operations.subprocess.run", fake_age)
    encryption = encrypt_backup_age(backup, encrypted, recipient="age1recipient")
    copied = copy_verified_off_host(encrypted, off_host)

    assert encryption["ok"] is True
    assert copied["ok"] is True
    assert sha256_file(off_host / encrypted.name) == encryption["sha256"]


def test_retention_is_dry_run_first_and_keeps_required_verified_copies(tmp_path) -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=timezone.utc)
    for offset in range(14):
        timestamp = (now - timedelta(days=offset)).strftime("%Y%m%dT%H%M%SZ")
        path = tmp_path / f"trading-assistant-{timestamp}.sqlite3.age"
        path.write_bytes(f"encrypted-{offset}".encode())
        digest = sha256_file(path)
        path.with_suffix(path.suffix + ".sha256").write_text(
            f"{digest}  {path.name}\n", encoding="ascii"
        )

    dry_run = retention_plan(tmp_path, daily=7, weekly=4)
    before = sorted(path.name for path in tmp_path.glob("*.age"))
    applied = apply_retention(tmp_path, daily=7, weekly=4)
    after = sorted(path.name for path in tmp_path.glob("*.age"))

    assert dry_run["ok"] is True
    assert before == sorted(dry_run["keep"] + dry_run["remove"])
    assert applied["applied"] is True
    assert after == sorted(dry_run["keep"])
    assert len(after) >= 7


def test_postgres_backup_and_disposable_restore_do_not_expose_dsn_in_command(
    tmp_path, monkeypatch
) -> None:
    backup = tmp_path / "evidence-memory.dump"
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs.get("env", {})))
        if command[0] == "pg_dump":
            output = Path(command[command.index("--file") + 1])
            output.write_bytes(b"PGDMP-test")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.backup_operations.subprocess.run", fake_run)
    created = create_postgres_backup("postgresql://secret@localhost/radar", backup)
    restored = postgres_restore_drill(
        backup,
        "postgresql://secret@localhost/disposable",
        confirm_disposable_target=True,
    )

    assert created["ok"] is True
    assert restored["ok"] is True
    assert all("secret" not in " ".join(command) for command, _env in commands)
    assert commands[0][1]["PGDATABASE"] == "postgresql://secret@localhost/radar"
    assert commands[-1][1]["PGDATABASE"] == "postgresql://secret@localhost/disposable"
