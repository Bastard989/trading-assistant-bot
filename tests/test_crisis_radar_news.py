import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.news import RssAdapter, classify_news
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.news_clients import NewsSourceError, RssClient
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def test_fed_rss_is_sanitized_and_classified_without_llm() -> None:
    items = RssAdapter("fed_news").normalize(
        (FIXTURES / "fed_monetary_news.xml").read_bytes(), fetched_at=NOW
    )

    assert len(items) == 2
    assert items[-1].summary == "Economic projections and the monetary policy statement."
    assert items[-1].importance == "high"
    evidence = {item.scenario_code: item for item in classify_news(items[-1])}
    assert set(evidence) == {"global_recession", "financial_stress"}
    assert evidence["financial_stress"].severity == "watch"
    assert evidence["financial_stress"].rule_codes == ("monetary_policy",)


def test_ecb_double_slash_url_is_canonical_and_digital_euro_is_not_crypto_stress() -> None:
    items = RssAdapter("ecb_news").normalize(
        (FIXTURES / "ecb_press_news.xml").read_bytes(), fetched_at=NOW
    )

    assert items[-1].url.startswith("https://www.ecb.europa.eu/press/")
    assert {item.scenario_code for item in classify_news(items[-1])} == {
        "global_recession",
        "financial_stress",
    }
    assert classify_news(items[0]) == ()


@pytest.mark.parametrize(
    ("source_code", "fixture", "expected_host"),
    (
        ("boe_news", "boe_news.xml", "www.bankofengland.co.uk"),
        ("boc_news", "boc_news.xml", "www.bankofcanada.ca"),
        ("fdic_news", "fdic_news.xml", "content.govdelivery.com"),
    ),
)
def test_new_official_feeds_have_offline_contract_fixtures(
    source_code: str, fixture: str, expected_host: str
) -> None:
    items = RssAdapter(source_code).normalize(
        (FIXTURES / fixture).read_bytes(), fetched_at=NOW
    )

    assert len(items) == 1
    assert expected_host in items[0].url
    assert items[0].source_tier == "A"


def test_rss_rejects_entities_and_untrusted_item_links() -> None:
    with pytest.raises(SourcePayloadError, match="DTD and entities"):
        RssAdapter("fed_news").normalize(
            b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "bad">]><rss/>',
            fetched_at=NOW,
        )
    payload = (FIXTURES / "fed_monetary_news.xml").read_text().replace(
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260714a.htm",
        "https://evil.example/item",
        1,
    )
    with pytest.raises(SourcePayloadError, match="untrusted URL"):
        RssAdapter("fed_news").normalize(payload.encode(), fetched_at=NOW)


def test_rss_client_retries_and_sanitizes_errors() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"feed")

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await RssClient(
                "fed_news", client=client, sleep=lambda _: asyncio.sleep(0)
            ).fetch()

    assert asyncio.run(scenario()) == b"feed"
    assert calls == 2

    async def failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def failure_scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
            with pytest.raises(NewsSourceError, match="HTTP 404"):
                await RssClient("ecb_news", client=client).fetch()

    asyncio.run(failure_scenario())


class StubRssClient:
    def __init__(self, source_code: str, fixture: str) -> None:
        self.source_code = source_code
        self.fixture = fixture

    async def fetch(self) -> bytes:
        return (FIXTURES / self.fixture).read_bytes()


def test_news_sync_is_idempotent_and_does_not_change_market_stage(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "news.sqlite3"))
    service = CrisisRadarService(repository)
    service.bootstrap()

    fed = StubRssClient("fed_news", "fed_monetary_news.xml")
    ecb = StubRssClient("ecb_news", "ecb_press_news.xml")
    first_fed = asyncio.run(service.sync_news(fed, fetched_at=NOW))
    first_ecb = asyncio.run(service.sync_news(ecb, fetched_at=NOW))
    second_fed = asyncio.run(service.sync_news(fed, fetched_at=NOW))

    assert first_fed["rows_written"] == 2
    assert first_ecb["rows_written"] == 2
    assert second_fed["rows_written"] == 0
    assert second_fed["evidence_written"] == 0
    payload = service.news(locale="ru", days=14, limit=20, as_of=NOW)
    assert len(payload["items"]) == 3
    assert payload["items"][0]["title"].startswith("Survey on the Access")
    assert payload["items"][0]["scenarios"][0]["explanation"].startswith(
        "Официальная публикация"
    )
    with repository.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_market_snapshots").fetchone()[0] == 0


def test_news_coverage_is_separate_from_numeric_coverage_and_fails_closed(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "coverage.sqlite3"))
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(scoring_v11=True),
    )
    bootstrap = service.bootstrap()
    methodology_id = int(bootstrap["shadow_v11"]["methodology_id"])
    run_id = repository.start_sync_run("fed_news", started_at=NOW)
    repository.finish_sync_run(
        run_id,
        finished_at=NOW,
        status="succeeded",
        rows_fetched=1,
        rows_written=1,
    )

    coverage = repository.save_news_coverage_snapshot(
        methodology_id=methodology_id,
        snapshot_at=NOW,
    )

    assert coverage["status"] == "insufficient_data"
    assert coverage["expected_source_count"] == 10
    assert coverage["healthy_source_count"] == 1
    assert "EU" in coverage["missing_regions"]
