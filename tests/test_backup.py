from __future__ import annotations

import sqlite3

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
