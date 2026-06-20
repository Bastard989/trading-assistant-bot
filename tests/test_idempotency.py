from concurrent.futures import ThreadPoolExecutor

from trading_bot.db import Database
from trading_bot.repositories import IdempotencyRepository


def test_only_one_concurrent_idempotency_reservation_wins(tmp_path) -> None:
    repository = IdempotencyRepository(Database(tmp_path / "idempotency.sqlite3"))

    with ThreadPoolExecutor(max_workers=20) as pool:
        states = list(pool.map(
            lambda _: repository.begin(42, "POST:/api/trades", "same-key", "same-hash")[0],
            range(100),
        ))

    assert states.count("new") == 1
    assert states.count("in_progress") == 99


def test_completed_response_can_be_replayed_and_key_mismatch_conflicts(tmp_path) -> None:
    repository = IdempotencyRepository(Database(tmp_path / "idempotency.sqlite3"))
    assert repository.begin(42, "close", "key", "hash")[0] == "new"
    repository.complete(42, "close", "key", 200, '{"ok":true}')
    state, row = repository.begin(42, "close", "key", "hash")
    assert state == "completed"
    assert row["response_body"] == '{"ok":true}'
    assert repository.begin(42, "close", "key", "different")[0] == "conflict"


def test_schema_records_idempotency_migration(tmp_path) -> None:
    db = Database(tmp_path / "migration.sqlite3")
    with db.connect() as connection:
        versions = dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
    assert versions[2] == "idempotency-keys-v2"
    assert versions[3] == "trade-review-rule-score-v3"
