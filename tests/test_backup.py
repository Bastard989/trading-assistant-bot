from __future__ import annotations

import sqlite3
import sys

from scripts import backup_daily
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
