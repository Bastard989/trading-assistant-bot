import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.news import (
    HkmaNewsAdapter,
    RssAdapter,
    classify_news,
    normalize_official_news,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.event_pipeline import extract_event_candidate
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.news_clients import (
    HkmaNewsClient,
    NewsSourceError,
    RssClient,
    news_client_for,
)
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
        ("ofac_news", "ofac_news.xml", "content.govdelivery.com"),
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


def test_ofac_govdelivery_feed_creates_a_sanctions_event_candidate() -> None:
    item = RssAdapter("ofac_news").normalize(
        (FIXTURES / "ofac_news.xml").read_bytes(), fetched_at=NOW
    )[0]

    event = extract_event_candidate(item)
    assert item.publisher == "U.S. Treasury Office of Foreign Assets Control"
    assert event is not None
    assert event.taxonomy == "sanctions"
    assert "CHN" in event.regions


def test_hkma_official_api_is_strictly_normalized_and_classified() -> None:
    payload = (FIXTURES / "hkma_news.json").read_bytes()
    items = normalize_official_news("hkma_news", payload, fetched_at=NOW)

    assert len(items) == 2
    assert items[-1].publisher == "Hong Kong Monetary Authority"
    assert items[-1].importance == "high"
    assert items[-1].url.startswith("https://www.hkma.gov.hk/")
    assert items[-1].source_tier == "A"
    assert items[-1].raw_payload_hash != items[-1].content_hash
    evidence = {item.scenario_code for item in classify_news(items[-1])}
    assert {"global_recession", "financial_stress"} <= evidence


def test_hkma_adapter_rejects_failed_untrusted_duplicate_and_future_payloads() -> None:
    payload = (FIXTURES / "hkma_news.json").read_text()
    with pytest.raises(SourcePayloadError, match="unsuccessful"):
        HkmaNewsAdapter().normalize(
            payload.replace('"success": true', '"success": false').encode(),
            fetched_at=NOW,
        )
    with pytest.raises(SourcePayloadError, match="untrusted URL"):
        HkmaNewsAdapter().normalize(
            payload.replace("https://www.hkma.gov.hk", "https://evil.example", 1).encode(),
            fetched_at=NOW,
        )
    duplicate = payload.replace(
        '"datasize": 2',
        '"datasize": 3',
    ).replace(
        '    ]\n  }',
        ',\n      {"title":"Duplicate","link":"https://www.hkma.gov.hk/eng/news-and-media/press-releases/2026/07/20260707-8/","date":"2026-07-07"}\n    ]\n  }',
    )
    with pytest.raises(SourcePayloadError, match="duplicate"):
        HkmaNewsAdapter().normalize(duplicate.encode(), fetched_at=NOW)
    future = payload.replace("2026-07-07", "2027-07-07").replace(
        "2026-07-10", "2027-07-10"
    )
    with pytest.raises(SourcePayloadError, match="no current valid"):
        HkmaNewsAdapter().normalize(future.encode(), fetched_at=NOW)


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


def test_hkma_client_retries_uses_bounded_official_api_and_factory() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "api.hkma.gov.hk"
        assert request.url.params["lang"] == "en"
        assert request.url.params["offset"] == "0"
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=(FIXTURES / "hkma_news.json").read_bytes())

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await HkmaNewsClient(
                client=client, sleep=lambda _: asyncio.sleep(0)
            ).fetch()

    assert asyncio.run(scenario()).startswith(b"{")
    assert calls == 2
    assert isinstance(news_client_for("hkma_news"), HkmaNewsClient)
    assert isinstance(news_client_for("fed_news"), RssClient)


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


def test_news_sync_routes_hkma_official_api_without_treating_it_as_rss(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "hkma.sqlite3"))
    service = CrisisRadarService(repository)
    client = StubRssClient("hkma_news", "hkma_news.json")

    result = asyncio.run(service.sync_news(client, fetched_at=NOW))
    payload = service.news(locale="en", days=14, limit=10, as_of=NOW)

    assert result["status"] == "succeeded"
    assert result["rows_written"] == 2
    assert any(item["title"].startswith("PBOC, HKMA") for item in payload["items"])


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
    assert coverage["expected_source_count"] == 12
    assert coverage["healthy_source_count"] == 1
    assert "EU" in coverage["missing_regions"]
    assert "HKG" in coverage["missing_regions"]
