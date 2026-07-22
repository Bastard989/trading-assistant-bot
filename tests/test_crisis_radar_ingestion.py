import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.sources.base import SeriesRequest, SourcePayloadError
from trading_bot.crisis_radar.sources.fred import FredAdapter
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"


def _observation(*, value: str, vintage: str, fetched_day: int) -> Observation:
    return Observation(
        indicator_code="us_hy_oas",
        source_code="fred",
        value=Decimal(value),
        unit="percent",
        observed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        released_at=datetime(2026, 7, fetched_day, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 7, fetched_day, 1, tzinfo=timezone.utc),
        vintage=vintage,
    )


def _repository(tmp_path) -> CrisisRadarRepository:
    repository = CrisisRadarRepository(Database(tmp_path / "ingestion.sqlite3"))
    repository.register_source("fred", "FRED", base_url="https://api.stlouisfed.org")
    repository.register_indicator(
        "us_hy_oas",
        "US High Yield Option-Adjusted Spread",
        group_code="credit",
        unit="percent",
        frequency="daily",
        risk_direction="higher_is_worse",
        source_code="fred",
        region_code="US",
    )
    return repository


def test_observation_requires_aware_times_and_finite_value() -> None:
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        Observation(
            indicator_code="test",
            source_code="fred",
            value=Decimal("1"),
            unit="index",
            observed_at=datetime(2026, 1, 1),
            released_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="finite"):
        Observation(
            indicator_code="test",
            source_code="fred",
            value=Decimal("NaN"),
            unit="index",
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            released_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )


def test_repository_is_idempotent_and_links_changed_vintages(tmp_path) -> None:
    repository = _repository(tmp_path)
    original = _observation(value="2.71", vintage="2026-07-01", fetched_day=1)
    unchanged_new_vintage = _observation(value="2.71", vintage="2026-07-02", fetched_day=2)
    revised = _observation(value="2.95", vintage="2026-07-15", fetched_day=15)

    first = repository.save_observation(original)
    duplicate = repository.save_observation(original)
    unchanged = repository.save_observation(unchanged_new_vintage)
    revision = repository.save_observation(revised)

    assert first.inserted is True
    assert duplicate == type(duplicate)(first.observation_id, False, False)
    assert unchanged == type(unchanged)(first.observation_id, False, False)
    assert revision.inserted is True
    assert revision.revision_created is True
    with repository.db.connect() as connection:
        values = [row[0] for row in connection.execute("SELECT value_text FROM cr_observations ORDER BY id")]
        revision_count = connection.execute("SELECT count(*) FROM cr_observation_revisions").fetchone()[0]
    assert values == ["2.71", "2.95"]
    assert revision_count == 1


def test_repository_rejects_wrong_unit_and_unknown_pair(tmp_path) -> None:
    repository = _repository(tmp_path)
    wrong_unit = _observation(value="2.71", vintage="2026-07-01", fetched_day=1)
    wrong_unit = replace(wrong_unit, unit="basis_points")
    with pytest.raises(ValueError, match="does not match"):
        repository.save_observation(wrong_unit)

    unknown = replace(wrong_unit, indicator_code="unknown", unit="percent")
    with pytest.raises(ValueError, match="not registered"):
        repository.save_observation(unknown)


def test_concurrent_duplicate_observation_is_stored_once(tmp_path) -> None:
    repository = _repository(tmp_path)
    observation = _observation(value="2.71", vintage="2026-07-01", fetched_day=1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: repository.save_observation(observation), range(24)))

    assert sum(result.inserted for result in results) == 1
    assert len({result.observation_id for result in results}) == 1


def test_fred_fixture_normalizes_values_times_vintage_and_provenance() -> None:
    payload = (FIXTURES / "fred_observations.json").read_bytes()
    fetched_at = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    observations = FredAdapter().normalize(
        payload,
        SeriesRequest("us_hy_oas", "BAMLH0A0HYM2", "percent"),
        fetched_at=fetched_at,
    )

    assert [item.value for item in observations] == [Decimal("2.65"), Decimal("2.71")]
    assert observations[-1].observed_at == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert observations[-1].released_at == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert observations[-1].fetched_at == fetched_at
    assert observations[-1].vintage == "2026-07-01"
    assert observations[-1].quality_flags == frozenset({QualityFlag.RELEASE_TIME_ESTIMATED})
    assert len(observations[-1].content_hash) == 64


@pytest.mark.parametrize(
    "payload",
    [b"not-json", b"{}", json.dumps({"observations": {}}).encode()],
)
def test_fred_adapter_rejects_malformed_payload(payload) -> None:
    with pytest.raises(SourcePayloadError):
        FredAdapter().normalize(
            payload,
            SeriesRequest("us_hy_oas", "BAMLH0A0HYM2", "percent"),
            fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
