import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from trading_bot.crisis_radar.catalog import FRED_INDICATORS
from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SeriesRequest
from trading_bot.crisis_radar.sources.fred_client import FredClient, FredClientError
from trading_bot.db import Database


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _fred_payload(value: str, *, series_date: str = "2026-07-18") -> bytes:
    return json.dumps(
        {
            "observations": [
                {
                    "realtime_start": "2026-07-20",
                    "realtime_end": "2026-07-20",
                    "date": series_date,
                    "value": value,
                }
            ]
        }
    ).encode()


def _fred_history_payload(
    values: list[tuple[str, str]], *, releases: dict[str, str] | None = None
) -> bytes:
    return json.dumps(
        {
            "observations": [
                {
                    "realtime_start": (releases or {}).get(series_date, "2026-07-20"),
                    "realtime_end": (releases or {}).get(series_date, "2026-07-20"),
                    "date": series_date,
                    "value": value,
                }
                for series_date, value in values
            ]
        }
    ).encode()


def test_fred_client_retries_rate_limit_without_leaking_key() -> None:
    calls = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["series_id"] == "VIXCLS"
        assert request.url.params["api_key"] == "secret-key"
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return httpx.Response(200, content=_fred_payload("28"))

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await FredClient("secret-key", client=http_client, sleep=fake_sleep).fetch(
                SeriesRequest("vix", "VIXCLS", "index_points")
            )

    payload = asyncio.run(scenario())

    assert calls == 2
    assert sleeps == [0.25]
    assert b'"28"' in payload


def test_fred_client_rejects_non_retryable_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_message": "bad key"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            with pytest.raises(FredClientError, match="HTTP 400"):
                await FredClient("secret-key", client=http_client).fetch(
                    SeriesRequest("vix", "VIXCLS", "index_points")
                )

    asyncio.run(scenario())


def test_fred_history_client_paginates_bounded_rows() -> None:
    offsets = []

    async def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        count = 100 if offset == 0 else 1
        rows = [
            {
                "realtime_start": "2026-07-20",
                "realtime_end": "2026-07-20",
                "date": (date(2000, 1, 1) + timedelta(days=offset + index)).isoformat(),
                "value": "20",
            }
            for index in range(count)
        ]
        return httpx.Response(200, json={"observations": rows})

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            return await FredClient("secret-key", client=http_client).fetch_history(
                SeriesRequest("vix", "VIXCLS", "index_points"),
                observation_start=date(2000, 1, 1),
                observation_end=date(2001, 1, 1),
                page_size=100,
            )

    payload = json.loads(asyncio.run(scenario()))
    assert offsets == [0, 100]
    assert len(payload["observations"]) == 101


class StubFredClient:
    values = {
        "SAHMREALTIME": "0.60",
        "BAMLH0A0HYM2": "6.5",
        "VIXCLS": "35",
        "T10Y2Y": "0.4",
        "NFCI": "-0.5",
    }

    async def fetch(self, request: SeriesRequest, *, limit: int = 24) -> bytes:
        if request.provider_series_id == "SP500":
            return _fred_history_payload([("2026-07-18", "80"), ("2026-06-18", "100")])
        if request.provider_series_id == "WALCL":
            return _fred_history_payload([("2026-07-18", "94"), ("2026-04-18", "100")])
        return _fred_payload(self.values[request.provider_series_id])


class DescendingHistoryFredClient(StubFredClient):
    async def fetch(self, request: SeriesRequest, *, limit: int = 24) -> bytes:
        if request.provider_series_id in {"SP500", "WALCL"}:
            return await super().fetch(request, limit=limit)
        current = self.values[request.provider_series_id]
        return _fred_history_payload([("2026-07-18", current), ("2026-07-17", "0.01")])


class StubBackfillFredClient:
    async def fetch_history(
        self,
        request: SeriesRequest,
        *,
        observation_start: date,
        observation_end: date,
        initial_release: bool = False,
    ) -> bytes:
        if request.provider_series_id == "SP500":
            return _fred_history_payload([("2000-01-01", "100"), ("2000-02-15", "80")])
        if request.provider_series_id == "WALCL":
            return _fred_history_payload(
                [("2000-01-01", "100"), ("2000-05-01", "95")],
                releases=(
                    {"2000-01-01": "2000-01-08", "2000-05-01": "2000-05-08"}
                    if initial_release
                    else None
                ),
            )
        value = StubFredClient.values[request.provider_series_id]
        return _fred_history_payload(
            [("2000-01-01", value), ("2000-02-15", value)],
            releases=(
                {"2000-01-01": "2000-01-08", "2000-02-15": "2000-02-22"}
                if initial_release
                else None
            ),
        )


def test_full_sync_holds_single_point_signals_and_records_source_health(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "service.sqlite3"))
    service = CrisisRadarService(repository)
    result = asyncio.run(service.sync_fred(StubFredClient(), fetched_at=NOW))

    assert result == {
        "sync_run_id": 1,
        "status": "succeeded",
        "rows_fetched": len(FRED_INDICATORS),
        "rows_written": len(FRED_INDICATORS),
        "stage": "tension",
    }
    overview = service.overview(locale="ru")
    assert overview["ready"] is True
    assert overview["stage"] == "tension"
    assert overview["breadth"]["danger_or_worse"] == 1
    pending = next(item for item in overview["indicators"] if item["code"] == "vix")
    assert pending["raw_band"] == "danger"
    assert pending["band"] == "normal"
    assert pending["persistence_count"] == 1
    assert {item["code"] for item in overview["indicators"]} == {seed.code for seed in FRED_INDICATORS}
    fred = next(source for source in overview["sources"] if source["code"] == "fred")
    assert fred["status"] == "succeeded"


def test_second_identical_sync_is_idempotent_but_recomputes(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "repeat.sqlite3"))
    service = CrisisRadarService(repository)
    first = asyncio.run(service.sync_fred(StubFredClient(), fetched_at=NOW))
    second = asyncio.run(service.sync_fred(StubFredClient(), fetched_at=NOW))

    assert first["rows_written"] == len(FRED_INDICATORS)
    assert second["rows_written"] == 0
    with repository.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_observations").fetchone()[0] == len(
            FRED_INDICATORS
        )
        assert connection.execute("SELECT count(*) FROM cr_sync_runs").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM cr_market_snapshots").fetchone()[0] == 1


def test_sync_uses_newest_observed_date_when_provider_returns_descending_history(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "descending.sqlite3")))
    result = asyncio.run(service.sync_fred(DescendingHistoryFredClient(), fetched_at=NOW))

    assert result["stage"] == "tension"
    overview = service.overview(locale="en")
    values = {item["code"]: item["value_text"] for item in overview["indicators"]}
    assert values["sahm_rule"] == "0.60"
    assert values["us_hy_oas"] == "6.5"
    assert values["vix"] == "35"
    assert values["sp500_30d_drawdown"] == "-20.0000"
    assert values["fed_assets_90d_change"] == "-6.0000"


def test_fred_backfill_uses_initial_releases_and_delays_sahm_release(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "backfill.sqlite3"))
    service = CrisisRadarService(repository)

    result = asyncio.run(
        service.backfill_fred(
            StubBackfillFredClient(),
            started_on=date(2000, 1, 1),
            ended_on=date(2000, 6, 1),
            fetched_at=NOW,
            recompute_after=False,
        )
    )

    assert result["status"] == "succeeded"
    assert result["rows_written"] > len(FRED_INDICATORS)
    inputs = repository.analysis_inputs_as_of(
        "crisis-radar", "starter-v8", as_of=datetime(2000, 6, 1, tzinfo=timezone.utc)
    )
    by_code = {item.observation.indicator_code: item.observation for item in inputs}
    assert QualityFlag.RETROSPECTIVE_REVISED not in by_code["us_nfci"].quality_flags
    assert QualityFlag.RELEASE_TIME_ESTIMATED not in by_code["us_nfci"].quality_flags
    assert by_code["us_nfci"].released_at == datetime(2000, 2, 22, tzinfo=timezone.utc)
    assert by_code["sahm_rule"].released_at == datetime(2000, 3, 31, tzinfo=timezone.utc)


def test_snapshot_changes_and_indicator_history_use_saved_evidence(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "history.sqlite3"))
    service = CrisisRadarService(repository)
    service.bootstrap()

    def save_snapshot(at: datetime, values: dict[str, str]) -> None:
        for seed in (item for item in FRED_INDICATORS if item.code in values):
            repository.save_observation(
                Observation(
                    indicator_code=seed.code,
                    source_code="fred",
                    value=Decimal(values[seed.code]),
                    unit=seed.unit,
                    observed_at=at,
                    released_at=at,
                    fetched_at=at,
                    vintage=at.date().isoformat(),
                )
            )
        service.recompute(snapshot_at=at)

    save_snapshot(NOW - timedelta(days=8), {"sahm_rule": "0.10", "us_hy_oas": "3", "vix": "15"})
    save_snapshot(NOW - timedelta(hours=25), {"sahm_rule": "0.30", "us_hy_oas": "5", "vix": "26"})
    save_snapshot(NOW, {"sahm_rule": "0.60", "us_hy_oas": "6.5", "vix": "35"})

    overview = service.overview(locale="en")
    assert overview["changes"]["24h"]["available"] is True
    assert overview["changes"]["24h"]["stage_from"] == "tension"
    assert overview["changes"]["24h"]["stage_to"] == "warning"
    assert overview["changes"]["7d"]["stage_from"] == "stable"
    vix_change = next(item for item in overview["changes"]["24h"]["indicators"] if item["code"] == "vix")
    assert vix_change["absolute"] == "9"

    history = service.indicator_history("vix", limit=20)
    assert history is not None
    assert history["risk_direction"] == "higher_is_worse"
    assert [point["value_text"] for point in history["points"]] == ["15", "26", "35"]
    assert history["thresholds"] == {
        "warning": "25",
        "danger": "30",
        "critical": "40",
        "reference": "0",
    }
    assert history["event_windows"] == []


def test_indicator_history_includes_relevant_historical_event_windows(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "history-events.sqlite3"))
    service = CrisisRadarService(repository)
    service.bootstrap()
    for day, value in ((datetime(2016, 7, 1, tzinfo=timezone.utc), "20"), (datetime(2016, 7, 10, tzinfo=timezone.utc), "30")):
        repository.save_observation(
            Observation(
                indicator_code="vix",
                source_code="fred",
                value=Decimal(value),
                unit="index_points",
                observed_at=day,
                released_at=day,
                fetched_at=day,
                vintage=day.date().isoformat(),
            )
        )

    history = service.indicator_history("vix", limit=20)

    assert history is not None
    assert any(
        window["scenario_code"] == "financial_stress"
        and window["started_at"].startswith("2016-07-07")
        and window["label_status"] == "derived"
        for window in history["event_windows"]
    )


def test_indicator_history_rejects_invalid_or_unknown_code(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "missing-history.sqlite3")))
    service.bootstrap()

    assert service.indicator_history("../../etc/passwd") is None
    assert service.indicator_history("unknown") is None
    with pytest.raises(ValueError, match="between 2 and 500"):
        service.indicator_history("vix", limit=1)
