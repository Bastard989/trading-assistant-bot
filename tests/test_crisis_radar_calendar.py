import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx

from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.fred_calendar import FredCalendarAdapter
from trading_bot.crisis_radar.sources.fred_client import FredClient
from trading_bot.db import Database


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _payload() -> bytes:
    return json.dumps(
        {
            "release_dates": [
                {
                    "release_id": 10,
                    "release_name": "Consumer Price Index",
                    "date": "2026-07-22",
                    "release_last_updated": "2026-07-01 08:00:00-05",
                },
                {
                    "release_id": 50,
                    "release_name": "Employment Situation",
                    "date": "2026-08-01",
                },
                {
                    "release_id": 999,
                    "release_name": "Minor Regional Survey",
                    "date": "2026-07-23",
                },
                {
                    "release_id": 345,
                    "release_name": "Research Consumer Price Index",
                    "date": "2026-07-24",
                },
                {
                    "release_id": 263,
                    "release_name": "Debt to Gross Domestic Product Ratios",
                    "date": "2026-07-25",
                },
                {
                    "release_id": 11,
                    "release_name": "Producer Price Index",
                    "date": "2026-09-30",
                },
            ]
        }
    ).encode()


def test_fred_calendar_filters_importance_and_requested_window() -> None:
    events = FredCalendarAdapter().normalize(
        _payload(),
        fetched_at=NOW,
        start_date=date(2026, 7, 20),
        end_date=date(2026, 8, 20),
    )

    assert [(item.release_name, item.importance) for item in events] == [
        ("Consumer Price Index", "high"),
        ("Employment Situation", "high"),
    ]
    assert events[0].source_url.endswith("rid=10")


def test_fred_client_requests_future_dates_including_no_data() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, content=_payload())

    async def run() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FredClient("test-key", client=client).fetch_release_dates(
                start_date=date(2026, 7, 20), end_date=date(2026, 8, 20)
            )

    assert asyncio.run(run()) == _payload()
    assert captured["include_release_dates_with_no_data"] == "true"
    assert captured["sort_order"] == "asc"
    assert captured["realtime_start"] == "2026-07-20"
    assert captured["realtime_end"] == "2026-08-20"


def test_calendar_persists_idempotently_and_localizes_without_inventing_time(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "calendar.sqlite3"))
    service = CrisisRadarService(repository)
    service.bootstrap()
    events = FredCalendarAdapter().normalize(
        _payload(),
        fetched_at=NOW,
        start_date=date(2026, 7, 20),
        end_date=date(2026, 8, 20),
    )

    repository.save_release_events(events)
    repository.save_release_events(events)
    calendar = repository.upcoming_release_payload(
        locale="ru", start_date=date(2026, 7, 20), days=30
    )

    with repository.db.connect() as connection:
        count = connection.execute("SELECT count(*) FROM cr_release_events").fetchone()[0]
    assert count == 2
    assert calendar["ready"] is True
    assert calendar["events"][0]["release_name"] == "Индекс потребительских цен США (CPI)"
    assert calendar["events"][0]["scheduled_at"] is None
    assert calendar["events"][0]["time_confirmed"] is False


def test_report_outbox_is_exactly_once_and_supports_retry(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "reports.sqlite3"))
    payload = {"stage": "warning"}

    first = repository.enqueue_report_deliveries(
        report_key="midweek:2026-07-22",
        report_type="midweek",
        report_date=date(2026, 7, 22),
        payload=payload,
        user_ids=(42, 42),
    )
    duplicate = repository.enqueue_report_deliveries(
        report_key="midweek:2026-07-22",
        report_type="midweek",
        report_date=date(2026, 7, 22),
        payload=payload,
        user_ids=(42,),
    )

    assert first == 1
    assert duplicate == 0
    delivery = repository.pending_report_deliveries()[0]
    repository.mark_report_failed(
        delivery.delivery_id, error="TimeoutError", retry_at=NOW - timedelta(minutes=1)
    )
    retry = repository.pending_report_deliveries()[0]
    repository.mark_report_sent(retry.delivery_id, sent_at=NOW)
    assert repository.pending_report_deliveries() == []
