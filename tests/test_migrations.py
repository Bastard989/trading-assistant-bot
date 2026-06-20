import sqlite3

import pytest

from trading_bot.db import Database


def test_migration_is_repeatable_and_checksum_is_enforced(tmp_path) -> None:
    path = tmp_path / "repeatable.sqlite3"
    Database(path)
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=2")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        Database(path)


def test_failed_migration_rolls_back_all_schema_changes(tmp_path, monkeypatch) -> None:
    path = tmp_path / "failed.sqlite3"
    database = object.__new__(Database)
    database.path = path

    def fail(*args, **kwargs):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(database, "_add_column", fail)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        database.migrate()
    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    assert tables == []


def test_auto_migrate_can_be_disabled(tmp_path) -> None:
    path = tmp_path / "not-migrated.sqlite3"
    Database(path, auto_migrate=False)
    assert not path.exists()
