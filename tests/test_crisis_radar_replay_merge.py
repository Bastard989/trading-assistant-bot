from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.merge_crisis_replay_history import merge_history
from trading_bot.crisis_radar.catalog import bootstrap_v12_catalog
from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.db import Database


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _repository(path):
    repository = CrisisRadarRepository(Database(path))
    bootstrap_v12_catalog(repository)
    return repository


def _observation(*, value: str, vintage: str, flags=frozenset()) -> Observation:
    return Observation(
        indicator_code="us_initial_claims",
        source_code="fred",
        observed_at=NOW - timedelta(days=7),
        released_at=NOW,
        fetched_at=NOW,
        value=Decimal(value),
        unit="persons",
        vintage=vintage,
        quality_flags=flags,
    )


def test_replay_history_merge_is_idempotent_and_excludes_revised_rows(tmp_path) -> None:
    source_path = tmp_path / "source.sqlite3"
    destination_path = tmp_path / "destination.sqlite3"
    source = _repository(source_path)
    _repository(destination_path)
    source.save_observation(_observation(value="300000", vintage="initial"))
    source.save_observation(
        _observation(
            value="310000",
            vintage="revised",
            flags=frozenset({QualityFlag.RETROSPECTIVE_REVISED}),
        )
    )

    first = merge_history(source_path, destination_path, source_codes={"fred"})
    second = merge_history(source_path, destination_path, source_codes={"fred"})

    assert first["rows_read"] == 2
    assert first["rows_inserted"] == 1
    assert first["rows_excluded_retrospective_revised"] == 1
    assert first["rows_unknown_indicator"] == 0
    assert first["causal_eligibility_enforced_by_replay"] is True
    assert first["integrity"] == "ok"
    assert first["foreign_key_violations"] == 0
    assert second["rows_inserted"] == 0
    assert second["rows_duplicate"] == 1
    with Database(destination_path).connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_observations").fetchone()[0] == 1


def test_replay_history_merge_rejects_same_database_and_empty_filter(tmp_path) -> None:
    path = tmp_path / "same.sqlite3"
    _repository(path)

    with pytest.raises(ValueError, match="must differ"):
        merge_history(path, path)
    other = tmp_path / "other.sqlite3"
    _repository(other)
    with pytest.raises(ValueError, match="must not be empty"):
        merge_history(path, other, source_codes=set())
