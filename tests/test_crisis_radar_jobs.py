import asyncio

from trading_bot.crisis_radar.jobs import CrisisRadarJobs
from trading_bot.crisis_radar.repositories import AlertDelivery, ReportDelivery


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

    async def sync_news(self, client) -> dict:
        self.calls.append(client.source_code)
        return {"status": "succeeded"}

    async def sync_bybit(self, client, *, recompute_after: bool = True) -> dict:
        assert recompute_after is False
        self.calls.append("bybit")
        return {"status": "succeeded"}

    def recompute(self):
        self.calls.append("market")
        return None


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

    assert service.calls == ["fed_news", "ecb_news"]


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
