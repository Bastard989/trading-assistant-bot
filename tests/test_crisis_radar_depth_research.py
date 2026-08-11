import asyncio
import json
from datetime import date, datetime, timezone

from trading_bot.crisis_radar.catalog import (
    FRED_V12_RESEARCH_INDICATORS,
    METHODOLOGY_CODE,
    METHODOLOGY_V11_VERSION,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SeriesRequest
from trading_bot.crisis_radar.sources.fred_client import FredClientError
from trading_bot.db import Database


NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def _history_payload() -> bytes:
    return json.dumps(
        {
            "observations": [
                {
                    "realtime_start": "2026-08-11",
                    "realtime_end": "2026-08-11",
                    "date": "2026-01-01",
                    "value": "100",
                },
                {
                    "realtime_start": "2026-08-11",
                    "realtime_end": "2026-08-11",
                    "date": "2026-05-01",
                    "value": "80",
                },
            ]
        }
    ).encode()


class ResearchFredClient:
    async def fetch_history(
        self,
        request: SeriesRequest,
        *,
        observation_start: date,
        observation_end: date,
        initial_release: bool = False,
    ) -> bytes:
        assert request.provider_series_id in {
            item.provider_series_id for item in FRED_V12_RESEARCH_INDICATORS
        }
        assert observation_start == date(2026, 1, 1)
        assert observation_end == date(2026, 5, 1)
        assert initial_release is False
        return _history_payload()


class LiveFredClient:
    async def fetch(self, request: SeriesRequest, *, limit: int = 24) -> bytes:
        if request.provider_series_id == "DHHNGSP":
            raise FredClientError("research series unavailable")
        return _history_payload()


def test_depth_research_backfill_collects_but_never_scores_v11(tmp_path) -> None:
    database = Database(tmp_path / "depth-research.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    research_codes = {item.code for item in FRED_V12_RESEARCH_INDICATORS}

    result = asyncio.run(
        service.backfill_fred(
            ResearchFredClient(),
            started_on=date(2026, 1, 1),
            ended_on=date(2026, 5, 1),
            fetched_at=NOW,
            recompute_after=False,
            indicator_codes=research_codes,
        )
    )

    assert result["status"] == "succeeded"
    assert result["rows_written"] == 15
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT indicator.code, indicator.enabled, count(observation.id)
            FROM cr_indicator_definitions AS indicator
            JOIN cr_observations AS observation
              ON observation.indicator_id=indicator.id
            WHERE indicator.code IN ({})
            GROUP BY indicator.code, indicator.enabled
            """.format(",".join("?" for _ in research_codes)),
            tuple(sorted(research_codes)),
        ).fetchall()
    assert {row[0] for row in stored} == research_codes
    assert all(row[1] == 0 and row[2] >= 1 for row in stored)
    scored_codes = {
        item.observation.indicator_code
        for item in repository.analysis_inputs_as_of(
            METHODOLOGY_CODE,
            METHODOLOGY_V11_VERSION,
            as_of=NOW,
        )
    }
    assert research_codes.isdisjoint(scored_codes)


def test_research_collection_failure_does_not_degrade_required_fred_health(tmp_path) -> None:
    database = Database(tmp_path / "depth-research-live.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(
        service.sync_fred(
            LiveFredClient(),
            fetched_at=NOW,
            recompute_after=False,
        )
    )

    assert result["status"] == "succeeded"
    assert result["research_errors"] == [
        "henry_hub_gas_90d_change:FredClientError"
    ]
    with database.connect() as connection:
        sync = connection.execute(
            "SELECT status, error_code, error_detail FROM cr_sync_runs"
        ).fetchone()
    assert tuple(sync) == ("succeeded", "", "")
