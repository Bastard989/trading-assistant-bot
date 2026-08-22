import asyncio
from types import SimpleNamespace

from trading_bot.crisis_radar.jobs import CrisisRadarJobs
from trading_bot.crisis_radar.repositories import AlertDelivery, DataHealthDelivery, ReportDelivery
from trading_bot.db import Database


class FakeService:
    def __init__(self) -> None:
        self.calls = []
        self.bootstrapped = 0

    def bootstrap(self) -> None:
        self.bootstrapped += 1

    async def sync_fred(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("fred")
        return {"status": "succeeded"}

    async def sync_fred_calendar(self, client) -> dict:
        self.calls.append("fred_calendar")
        return {"status": "succeeded"}

    async def sync_bea(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("bea")
        return {"status": "succeeded"}

    async def sync_eia(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("eia")
        return {"status": "succeeded"}

    async def sync_ecb(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("ecb")
        return {"status": "succeeded"}

    async def sync_eurostat(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("eurostat")
        return {"status": "succeeded"}

    async def sync_world_bank(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("world_bank")
        return {"status": "succeeded"}

    async def sync_bis(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("bis")
        return {"status": "succeeded"}

    async def sync_oecd(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("oecd")
        return {"status": "succeeded"}

    async def sync_new_york_fed(self, client, *, recompute_after: bool = False) -> dict:
        assert recompute_after is False
        self.calls.append("new_york_fed")
        return {"status": "succeeded"}

    async def sync_oecd_labour(
        self, client, *, recompute_after: bool = False
    ) -> dict:
        assert recompute_after is False
        self.calls.append("oecd_labour_research")
        return {"status": "succeeded"}

    async def sync_portwatch(
        self, client, *, recompute_after: bool = False
    ) -> dict:
        assert recompute_after is False
        self.calls.append("imf_portwatch")
        return {"status": "succeeded"}

    async def sync_binance_stablecoin(
        self, client, *, recompute_after: bool = False
    ) -> dict:
        assert recompute_after is False
        self.calls.append("binance_market")
        return {"status": "succeeded"}

    async def sync_bybit_stablecoin(
        self, client, *, recompute_after: bool = False
    ) -> dict:
        assert recompute_after is False
        self.calls.append("bybit_stablecoin_research")
        return {"status": "succeeded"}

    async def sync_news(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append(client.source_code)
        return {"status": "succeeded"}

    async def sync_bybit(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("bybit")
        return {"status": "succeeded"}

    def recompute(self):
        self.calls.append("market")
        return None

    async def sync_gdelt_discovery(self, client) -> dict:
        self.calls.append("gdelt_discovery")
        return {"status": "succeeded"}


class FakeQueue:
    def __init__(self) -> None:
        self.registrations = []
        self.daily = []

    def run_repeating(self, callback, **kwargs) -> None:
        self.registrations.append({"callback": callback, **kwargs})

    def run_daily(self, callback, **kwargs) -> None:
        self.daily.append({"callback": callback, **kwargs})


class FakeApplication:
    def __init__(self) -> None:
        self.job_queue = FakeQueue()


def test_official_scheduler_registers_one_serial_job_for_all_sources() -> None:
    service = FakeService()
    application = FakeApplication()
    jobs = CrisisRadarJobs(
        service,
        fred_api_key="fred",
        bea_api_key="bea",
        eia_api_key="eia",
    )

    assert jobs.register(application, interval_seconds=3600) is True
    assert service.bootstrapped == 1
    assert [item["name"] for item in application.job_queue.registrations] == [
        "crisis-radar-official-sync",
        "crisis-radar-news-sync",
    ]
    assert application.job_queue.registrations[1]["interval"] == 900
    assert [item["name"] for item in application.job_queue.daily] == [
        "crisis-radar-global-daily-sync",
        "crisis-radar-midweek-summary",
        "crisis-radar-weekend-summary",
    ]
    assert application.job_queue.daily[0]["days"] == tuple(range(7))
    assert application.job_queue.daily[1]["days"] == (3,)
    assert application.job_queue.daily[2]["days"] == (6,)
    asyncio.run(jobs.sync(None))
    assert service.calls == [
        "fred", "fred_calendar", "bea", "eia", "ecb", "eurostat", "bybit", "market"
    ]


def test_crypto_scheduler_tolerates_short_event_loop_delays(tmp_path) -> None:
    service = FakeService()
    service.repository = SimpleNamespace(db=Database(tmp_path / "jobs.sqlite3"))
    application = FakeApplication()
    jobs = CrisisRadarJobs(service, fred_api_key="")

    jobs.register(application, interval_seconds=3600)

    registration = next(
        item for item in application.job_queue.registrations
        if item["name"] == "crisis-radar-crypto-momentum"
    )
    assert registration["interval"] == 900
    assert registration["job_kwargs"] == {
        "misfire_grace_time": 300,
        "coalesce": True,
        "max_instances": 1,
    }


def test_scheduler_uses_public_europe_sources_without_private_keys() -> None:
    service = FakeService()
    application = FakeApplication()

    assert CrisisRadarJobs(service, fred_api_key="").register(
        application, interval_seconds=3600
    ) is True
    asyncio.run(application.job_queue.registrations[0]["callback"](None))
    assert service.calls == ["ecb", "eurostat", "bybit", "market"]


def test_scheduler_syncs_official_news_feeds_every_fifteen_minutes() -> None:
    service = FakeService()
    application = FakeApplication()
    jobs = CrisisRadarJobs(service, fred_api_key="")
    jobs.register(application, interval_seconds=3600)

    asyncio.run(application.job_queue.registrations[1]["callback"](None))

    assert service.calls == ["fed_news", "ecb_news", "market"]


def test_scheduler_syncs_slow_global_sources_once_in_daily_job() -> None:
    service = FakeService()
    application = FakeApplication()
    jobs = CrisisRadarJobs(service, fred_api_key="")
    jobs.register(application, interval_seconds=3600)

    asyncio.run(application.job_queue.daily[0]["callback"](None))

    assert service.calls == ["world_bank", "bis", "oecd", "market"]


class FakeAlertRepository:
    def __init__(self) -> None:
        self.sent = []

    def pending_alert_deliveries(self):
        return [
            AlertDelivery(
                delivery_id=7,
                user_id=42,
                event_type="scenario_escalation",
                severity="critical",
                scenario_code="financial_stress",
                from_state="elevated",
                to_state="confirmed",
                payload={
                    "horizon": "24h-3m",
                    "explanation": {"ru": "Два независимых канала подтвердили стресс."},
                },
            )
        ]

    def mark_alert_sent(self, delivery_id, *, sent_at):
        self.sent.append(delivery_id)


class FakeReportRepository:
    def __init__(self) -> None:
        self.sent = []

    def pending_report_deliveries(self):
        return [
            ReportDelivery(
                delivery_id=9,
                user_id=42,
                report_type="weekend",
                report_date="2026-07-18",
                payload={
                    "stage": "warning",
                    "breadth": {"active": 4, "danger_or_worse": 2, "critical": 0},
                    "explanation": "Несколько независимых каналов ухудшаются.",
                    "scenarios": [{"name": "Финансовый стресс", "status": "watch"}],
                    "calendar": [
                        {"release_date": "2026-07-22", "release_name": "Решение FOMC"}
                    ],
                    "news": [
                        {
                            "title": "Lending conditions tightened",
                            "source": {"name": "ECB"},
                        }
                    ],
                },
            )
        ]

    def mark_report_sent(self, delivery_id, *, sent_at):
        self.sent.append(delivery_id)


class FakeDataHealthRepository:
    def __init__(self) -> None:
        self.sent = []

    def pending_data_health_deliveries(self):
        return [
            DataHealthDelivery(
                delivery_id=11,
                user_id=42,
                from_status="healthy",
                to_status="insufficient_data",
                payload={
                    "ratio": "0.62",
                    "missing_regions": ["CHINA"],
                    "missing_groups": ["credit"],
                },
            )
        ]

    def mark_data_health_sent(self, delivery_id, *, sent_at):
        self.sent.append(delivery_id)


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


def test_scenario_alert_is_delivered_as_plain_telegram_message() -> None:
    service = FakeService()
    service.repository = FakeAlertRepository()
    bot = FakeBot()
    context = type("Context", (), {"bot": bot})()
    jobs = CrisisRadarJobs(service, fred_api_key="", alert_user_ids=(42,))

    asyncio.run(jobs._deliver_alerts(context))

    assert service.repository.sent == [7]
    assert bot.messages[0]["chat_id"] == 42
    assert "elevated → confirmed" in bot.messages[0]["text"]


def test_planned_summary_is_delivered_as_plain_telegram_message() -> None:
    service = FakeService()
    service.repository = FakeReportRepository()
    bot = FakeBot()
    context = type("Context", (), {"bot": bot})()
    jobs = CrisisRadarJobs(service, fred_api_key="", alert_user_ids=(42,))

    asyncio.run(jobs._deliver_reports(context))

    assert service.repository.sent == [9]
    assert bot.messages[0]["chat_id"] == 42
    assert "ПРЕДУПРЕЖДЕНИЕ" in bot.messages[0]["text"]
    assert "Решение FOMC" in bot.messages[0]["text"]
    assert "Lending conditions tightened" in bot.messages[0]["text"]


def test_data_outage_is_delivered_separately_from_market_alert() -> None:
    service = FakeService()
    service.repository = FakeDataHealthRepository()
    bot = FakeBot()
    context = type("Context", (), {"bot": bot})()
    jobs = CrisisRadarJobs(service, fred_api_key="", alert_user_ids=(42,))

    asyncio.run(jobs._deliver_data_health_alerts(context))

    assert service.repository.sent == [11]
    assert "СОСТОЯНИЕ ДАННЫХ" in bot.messages[0]["text"]
    assert "не рыночный сигнал" in bot.messages[0]["text"]


def test_scheduler_rejects_too_frequent_interval() -> None:
    jobs = CrisisRadarJobs(FakeService(), fred_api_key="")

    try:
        jobs.register(FakeApplication(), interval_seconds=299)
    except ValueError as exc:
        assert "at least 300" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("unsafe interval must be rejected")


def test_locked_jobs_skip_without_starting_parallel_sync() -> None:
    service = FakeService()
    jobs = CrisisRadarJobs(service, fred_api_key="")

    async def scenario() -> None:
        await jobs._sync_lock.acquire()
        try:
            await jobs.sync(None)
            await jobs.sync_global(None)
            await jobs.sync_news_feeds(None)
        finally:
            jobs._sync_lock.release()

    asyncio.run(scenario())
    assert service.calls == []


def test_global_sync_collects_disabled_gscpi_candidate_when_v11_is_enabled() -> None:
    service = FakeService()
    service.feature_flags = SimpleNamespace(scoring_v11=True)
    jobs = CrisisRadarJobs(service, fred_api_key="")

    asyncio.run(jobs.sync_global(None))

    assert service.calls == [
        "world_bank",
        "bis",
        "oecd",
        "new_york_fed",
        "oecd_labour_research",
        "imf_portwatch",
        "market",
    ]


def test_hourly_sync_collects_disabled_stablecoin_candidate_when_v11_is_enabled() -> None:
    service = FakeService()
    service.feature_flags = SimpleNamespace(scoring_v11=True)
    jobs = CrisisRadarJobs(service, fred_api_key="")

    asyncio.run(jobs.sync(None))

    assert service.calls == [
        "ecb",
        "eurostat",
        "bybit",
        "bybit_stablecoin_research",
        "binance_market",
        "market",
    ]


class SchedulingRepository:
    def __init__(self) -> None:
        self.enqueued_alerts = []
        self.enqueued_health = []
        self.enqueued_reports = []

    def enqueue_alert_deliveries(self, user_ids):
        self.enqueued_alerts.append(tuple(user_ids))

    def enqueue_data_health_deliveries(self, user_ids):
        self.enqueued_health.append(tuple(user_ids))

    def enqueue_report_deliveries(self, **kwargs):
        self.enqueued_reports.append(kwargs)

    def pending_alert_deliveries(self):
        return []

    def pending_data_health_deliveries(self):
        return []

    def pending_report_deliveries(self):
        return []


def test_sync_enqueues_owner_deliveries_and_news_v2_uses_full_registry() -> None:
    service = FakeService()
    service.repository = SchedulingRepository()
    service.feature_flags = SimpleNamespace(news_events_v2=True)
    jobs = CrisisRadarJobs(service, fred_api_key="", alert_user_ids=(42, 42, -1))
    context = SimpleNamespace(bot=FakeBot())

    asyncio.run(jobs.sync(context))
    asyncio.run(jobs.sync_global(context))
    asyncio.run(jobs.sync_news_feeds(context))

    assert jobs.alert_user_ids == (42,)
    assert service.repository.enqueued_alerts == [(42,), (42,)]
    assert service.repository.enqueued_health == [(42,), (42,)]
    assert "gdelt_discovery" in service.calls
    assert "fdic_news" in service.calls
    assert "hkma_news" in service.calls
    assert "nbs_news" in service.calls
    assert "bok_news" in service.calls
    assert "ofac_news" in service.calls


def test_summary_persists_report_payload_and_rejects_unknown_type() -> None:
    service = FakeService()
    service.repository = SchedulingRepository()
    service.overview = lambda **_kwargs: {
        "stage": "warning",
        "as_of": "2026-08-11T00:00:00Z",
        "explanation": "Ухудшаются независимые каналы.",
        "breadth": {"active": 2},
        "scenarios": [{"code": "financial_stress", "status": "watch"}],
    }
    service.calendar = lambda **_kwargs: {"events": [{"release_name": "CPI"}]}
    service.news = lambda **_kwargs: {"items": [{"title": "Official release"}]}
    jobs = CrisisRadarJobs(service, fred_api_key="", alert_user_ids=(42,))
    context = SimpleNamespace(job=SimpleNamespace(data={"report_type": "weekend"}), bot=FakeBot())

    asyncio.run(jobs.summary(context))
    invalid = SimpleNamespace(job=SimpleNamespace(data={"report_type": "daily"}), bot=FakeBot())
    asyncio.run(jobs.summary(invalid))

    report = service.repository.enqueued_reports[0]
    assert report["report_type"] == "weekend"
    assert report["user_ids"] == (42,)
    assert report["payload"]["calendar"][0]["release_name"] == "CPI"
