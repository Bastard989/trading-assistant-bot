import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.bybit import (
    BybitAdapter,
    BybitClient,
    BybitSourceError,
)
from trading_bot.crisis_radar.bybit_options import build_defined_risk_put_spread
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def test_bybit_adapter_normalizes_funding_oi_change_and_drawdown() -> None:
    adapter = BybitAdapter()
    funding = adapter.normalize_funding(
        (FIXTURES / "bybit_funding.json").read_bytes(), symbol="BTCUSDT", fetched_at=NOW
    )
    oi = adapter.normalize_oi_change(
        (FIXTURES / "bybit_open_interest.json").read_bytes(),
        symbol="BTCUSDT",
        fetched_at=NOW,
    )
    drawdown = adapter.normalize_drawdown(
        (FIXTURES / "bybit_klines.json").read_bytes(), symbol="BTCUSDT", fetched_at=NOW
    )

    assert funding[-1].value == Decimal("-0.0700")
    assert oi[-1].value == Decimal("30.0000")
    assert drawdown[-1].value == Decimal("-25.0000")


@pytest.mark.parametrize("method", ["normalize_funding", "normalize_oi_change", "normalize_drawdown"])
def test_bybit_adapter_rejects_malformed_payload(method) -> None:
    with pytest.raises(SourcePayloadError):
        getattr(BybitAdapter(), method)(b"{}", symbol="BTCUSDT", fetched_at=NOW)


def test_bybit_public_client_uses_documented_v5_contract_and_retries() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"retCode": 0, "result": {"list": []}})

    async def no_sleep(delay: float) -> None:
        assert delay == 1

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await BybitClient(client=client, sleep=no_sleep).fetch_funding("BTCUSDT")

    assert b'"retCode":0' in asyncio.run(scenario()).replace(b" ", b"")
    assert calls[-1].url.path == "/v5/market/funding/history"
    assert calls[-1].url.params["category"] == "linear"
    assert calls[-1].url.params["symbol"] == "BTCUSDT"


def test_bybit_option_client_uses_public_option_ticker_contract() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"retCode": 0, "result": {"category": "option", "list": []}, "time": 1784505600000},
        )

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await BybitClient(client=client).fetch_option_tickers("BTC")

    assert b'"category":"option"' in asyncio.run(scenario()).replace(b" ", b"")
    assert calls[0].url.path == "/v5/market/tickers"
    assert calls[0].url.params["category"] == "option"
    assert calls[0].url.params["baseCoin"] == "BTC"


def test_bybit_option_vertical_builds_only_quoted_defined_risk_spread() -> None:
    payload = json.dumps(
        {
            "retCode": 0,
            "result": {
                "category": "option",
                "list": [
                    {
                        "symbol": "BTC-28AUG26-60000-P",
                        "bid1Price": "900",
                        "ask1Price": "1000",
                        "bid1Size": "2",
                        "ask1Size": "3",
                        "openInterest": "80",
                        "turnover24h": "50000",
                        "underlyingPrice": "66000",
                    },
                    {
                        "symbol": "BTC-28AUG26-52000-P",
                        "bid1Price": "350",
                        "ask1Price": "400",
                        "bid1Size": "4",
                        "ask1Size": "4",
                        "openInterest": "100",
                        "turnover24h": "60000",
                        "underlyingPrice": "66000",
                    },
                    {
                        "symbol": "BTC-28AUG26-50000-P",
                        "bid1Price": "0",
                        "ask1Price": "200",
                        "bid1Size": "0",
                        "ask1Size": "1",
                        "openInterest": "0",
                        "turnover24h": "0",
                        "underlyingPrice": "66000",
                    },
                ],
            },
            "time": int(NOW.timestamp() * 1000),
        }
    ).encode()

    quote = build_defined_risk_put_spread(payload, base_coin="BTC", fetched_at=NOW)

    assert quote is not None
    assert quote.symbol == "BTC-28AUG26-60000-P/BTC-28AUG26-52000-P"
    assert quote.price == Decimal("650.0000")
    assert quote.option_risk_profile == "defined_risk"
    assert quote.max_loss_pct == Decimal("100")
    assert quote.max_gain_pct is not None


def test_bybit_option_vertical_degrades_when_two_liquid_legs_are_unavailable() -> None:
    payload = json.dumps(
        {
            "retCode": 0,
            "result": {
                "category": "option",
                "list": [
                    {
                        "symbol": "BTC-28AUG26-60000-P",
                        "bid1Price": "900",
                        "ask1Price": "1000",
                        "bid1Size": "2",
                        "ask1Size": "3",
                        "openInterest": "80",
                        "turnover24h": "50000",
                        "underlyingPrice": "66000",
                    }
                ],
            },
            "time": int(NOW.timestamp() * 1000),
        }
    ).encode()

    assert build_defined_risk_put_spread(payload, base_coin="BTC", fetched_at=NOW) is None


def test_bybit_history_clients_use_documented_time_and_cursor_pagination() -> None:
    calls: list[httpx.Request] = []
    oi_page = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal oi_page
        calls.append(request)
        if request.url.path.endswith("/funding/history"):
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "category": "linear",
                        "list": [
                            {
                                "symbol": "BTCUSDT",
                                "fundingRate": "0.0001",
                                "fundingRateTimestamp": "1784073600000",
                            }
                        ],
                    },
                },
            )
        if request.url.path.endswith("/kline"):
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "symbol": "BTCUSDT",
                        "list": [["1784073600000", "1", "1", "1", "1", "1", "1"]],
                    },
                },
            )
        oi_page += 1
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "result": {
                    "symbol": "BTCUSDT",
                    "list": [
                        {
                            "openInterest": str(100 + oi_page),
                            "timestamp": str(1784073600000 - oi_page * 86400000),
                        }
                    ],
                    "nextPageCursor": "page-2" if oi_page == 1 else "",
                },
            },
        )

    async def scenario() -> tuple[bytes, bytes, bytes]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = BybitClient(client=client)
            start = datetime(2026, 7, 1, tzinfo=timezone.utc)
            end = datetime(2026, 7, 19, 23, tzinfo=timezone.utc)
            return (
                await source.fetch_funding_history("BTCUSDT", started_at=start, ended_at=end),
                await source.fetch_kline_history("BTCUSDT", started_at=start, ended_at=end),
                await source.fetch_open_interest_history(
                    "BTCUSDT", started_at=start, ended_at=end
                ),
            )

    funding, klines, open_interest = asyncio.run(scenario())
    assert len(json.loads(funding)["result"]["list"]) == 1
    assert len(json.loads(klines)["result"]["list"]) == 1
    assert len(json.loads(open_interest)["result"]["list"]) == 2
    funding_call = next(item for item in calls if item.url.path.endswith("/funding/history"))
    assert {"startTime", "endTime", "limit"} <= set(funding_call.url.params)
    kline_call = next(item for item in calls if item.url.path.endswith("/kline"))
    assert kline_call.url.params["interval"] == "D"
    assert {"start", "end", "limit"} <= set(kline_call.url.params)
    oi_calls = [item for item in calls if item.url.path.endswith("/open-interest")]
    assert len(oi_calls) == 2
    assert "cursor" not in oi_calls[0].url.params
    assert oi_calls[1].url.params["cursor"] == "page-2"


def test_bybit_history_detects_cursor_loops_and_enforces_page_budget() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "result": {
                    "symbol": "BTCUSDT",
                    "list": [{"openInterest": "100", "timestamp": "1784073600000"}],
                    "nextPageCursor": "same",
                },
            },
        )

    async def scenario(max_pages: int) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await BybitClient(client=client, max_history_pages=max_pages).fetch_open_interest_history(
                "BTCUSDT",
                started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ended_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            )

    with pytest.raises(BybitSourceError, match="pagination loop"):
        asyncio.run(scenario(10))
    with pytest.raises(BybitSourceError, match="page limit"):
        asyncio.run(scenario(1))


@pytest.mark.parametrize(
    ("limits", "message"),
    (({"max_history_rows": 1}, "row limit"), ({"max_history_bytes": 1}, "size limit")),
)
def test_bybit_history_enforces_row_and_aggregate_byte_budgets(limits, message) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "result": {
                    "symbol": "BTCUSDT",
                    "list": [
                        {"openInterest": "100", "timestamp": "1784073600000"},
                        {"openInterest": "101", "timestamp": "1783987200000"},
                    ],
                    "nextPageCursor": "",
                },
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await BybitClient(client=client, **limits).fetch_open_interest_history(
                "BTCUSDT",
                started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ended_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            )

    with pytest.raises(BybitSourceError, match=message):
        asyncio.run(scenario())


def _history_payloads(symbol: str) -> tuple[bytes, bytes, bytes]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    funding_rows = []
    oi_rows = []
    kline_rows = []
    for offset in range(20):
        timestamp = int((start + timedelta(days=offset)).timestamp() * 1000)
        funding_rows.extend(
            (
                {
                    "symbol": symbol,
                    "fundingRate": "0.0001",
                    "fundingRateTimestamp": str(timestamp + 8 * 3600 * 1000),
                },
                {
                    "symbol": symbol,
                    "fundingRate": "0.0002",
                    "fundingRateTimestamp": str(timestamp + 16 * 3600 * 1000),
                },
            )
        )
        oi_rows.append({"openInterest": str(100 + offset), "timestamp": str(timestamp)})
        close = 120 - offset if offset < 10 else 100 - offset
        kline_rows.append([str(timestamp), "100", "121", "90", str(close), "1", "1"])
    def envelope(rows: list) -> bytes:
        return json.dumps(
            {
                "retCode": 0,
                "result": {"symbol": symbol, "category": "linear", "list": rows},
            },
            separators=(",", ":"),
        ).encode()

    return envelope(funding_rows), envelope(oi_rows), envelope(kline_rows)


def test_bybit_history_normalization_is_daily_and_has_no_lookahead() -> None:
    adapter = BybitAdapter()
    funding_payload, oi_payload, kline_payload = _history_payloads("BTCUSDT")
    started_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    ended_at = datetime(2026, 7, 20, 23, tzinfo=timezone.utc)
    fetched_at = datetime(2026, 7, 20, 20, tzinfo=timezone.utc)

    funding = adapter.normalize_daily_funding_history(
        funding_payload,
        symbol="BTCUSDT",
        fetched_at=fetched_at,
        started_at=started_at,
        ended_at=ended_at,
    )
    oi = adapter.normalize_oi_change_history(
        oi_payload,
        symbol="BTCUSDT",
        fetched_at=fetched_at,
        started_at=started_at,
        ended_at=ended_at,
    )
    drawdown = adapter.normalize_drawdown_history(
        kline_payload,
        symbol="BTCUSDT",
        fetched_at=fetched_at,
        started_at=started_at,
        ended_at=ended_at,
    )
    oi_research = adapter.normalize_oi_research_history(
        oi_payload,
        symbol="BTCUSDT",
        fetched_at=fetched_at,
        started_at=started_at,
        ended_at=ended_at,
    )
    price_research = adapter.normalize_price_research_history(
        kline_payload,
        symbol="BTCUSDT",
        fetched_at=fetched_at,
        started_at=started_at,
        ended_at=ended_at,
    )

    assert len(funding) == 11
    assert all(item.observed_at.hour == 16 for item in funding)
    assert len({item.observed_at.date() for item in funding}) == len(funding)
    assert oi[0].observed_at == started_at
    assert all(item.observed_at <= fetched_at for item in oi)
    assert drawdown[-1].observed_at == datetime(2026, 7, 19, tzinfo=timezone.utc)
    assert all(item.observed_at + timedelta(days=1) <= fetched_at for item in drawdown)
    assert {item.indicator_code for item in oi_research} == {
        "btc_open_interest",
        "btc_oi_7d_change",
    }
    assert {item.indicator_code for item in price_research} == {
        "btc_close_price",
        "btc_return_7d",
    }
    assert all(
        item.released_at == item.observed_at + timedelta(days=1)
        for item in price_research
    )


def test_bybit_backfill_can_store_disabled_research_series(tmp_path) -> None:
    database = Database(tmp_path / "bybit-research.sqlite3")
    service = CrisisRadarService(CrisisRadarRepository(database))

    result = asyncio.run(
        service.backfill_bybit(
            StubBybitHistoryClient(),
            started_on=date(2026, 7, 10),
            ended_on=date(2026, 7, 19),
            fetched_at=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
            recompute_after=False,
            indicator_codes={
                "btc_close_price",
                "btc_return_7d",
                "btc_open_interest",
                "btc_oi_7d_change",
            },
        )
    )

    assert result["status"] == "succeeded"
    assert result["rows_written"] == 40
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT indicator.code, indicator.enabled, count(*) AS count
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator ON indicator.id = observation.indicator_id
            GROUP BY indicator.code, indicator.enabled ORDER BY indicator.code
            """
        ).fetchall()
    assert {(row["code"], row["enabled"], row["count"]) for row in rows} == {
        ("btc_close_price", 0, 10),
        ("btc_open_interest", 0, 10),
        ("btc_oi_7d_change", 0, 10),
        ("btc_return_7d", 0, 10),
    }


class StubBybitClient:
    @staticmethod
    def _payload(name: str, symbol: str) -> bytes:
        return (FIXTURES / name).read_bytes().replace(b"BTCUSDT", symbol.encode())

    async def fetch_funding(self, symbol: str) -> bytes:
        return self._payload("bybit_funding.json", symbol)

    async def fetch_open_interest(self, symbol: str) -> bytes:
        return self._payload("bybit_open_interest.json", symbol)

    async def fetch_daily_klines(self, symbol: str) -> bytes:
        return self._payload("bybit_klines.json", symbol)


def test_bybit_sync_builds_crypto_groups_without_private_key(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "bybit.sqlite3")))

    result = asyncio.run(service.sync_bybit(StubBybitClient(), fetched_at=NOW))

    assert result["status"] == "succeeded"
    assert result["rows_written"] == 12
    overview = service.overview(locale="en")
    assert overview["methodology"]["version"] == "starter-v8"
    assert {item["code"] for item in overview["groups"]} == {
        "crypto_leverage",
        "crypto_price_stress",
    }
    assert len(overview["indicators"]) == 6


class StubBybitHistoryClient:
    def __init__(self) -> None:
        self.windows: list[tuple[str, datetime, datetime]] = []

    async def fetch_funding_history(
        self, symbol: str, *, started_at: datetime, ended_at: datetime
    ) -> bytes:
        self.windows.append(("funding", started_at, ended_at))
        return _history_payloads(symbol)[0]

    async def fetch_open_interest_history(
        self, symbol: str, *, started_at: datetime, ended_at: datetime
    ) -> bytes:
        self.windows.append(("oi", started_at, ended_at))
        return _history_payloads(symbol)[1]

    async def fetch_kline_history(
        self, symbol: str, *, started_at: datetime, ended_at: datetime
    ) -> bytes:
        self.windows.append(("kline", started_at, ended_at))
        return _history_payloads(symbol)[2]


def test_bybit_backfill_writes_bounded_daily_history_and_source_journal(tmp_path) -> None:
    database = Database(tmp_path / "bybit-backfill.sqlite3")
    service = CrisisRadarService(CrisisRadarRepository(database))
    client = StubBybitHistoryClient()

    result = asyncio.run(
        service.backfill_bybit(
            client,
            started_on=date(2026, 7, 10),
            ended_on=date(2026, 7, 19),
            fetched_at=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
            recompute_after=False,
            indicator_codes={
                "btc_funding_rate",
                "btc_oi_7d_abs_change",
                "btc_30d_drawdown",
            },
        )
    )

    assert result == {
        "sync_run_id": result["sync_run_id"],
        "status": "succeeded",
        "rows_fetched": 30,
        "rows_written": 30,
        "stage": None,
        "errors": [],
    }
    assert {item[0] for item in client.windows} == {"funding", "oi", "kline"}
    starts = {kind: started for kind, started, _ in client.windows}
    assert starts["funding"] == datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert starts["oi"] == datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert starts["kline"] == datetime(2026, 6, 9, tzinfo=timezone.utc)
    with database.connect() as connection:
        journal = connection.execute(
            "SELECT status, rows_fetched, rows_written, error_detail FROM cr_sync_runs"
        ).fetchone()
    assert tuple(journal) == ("succeeded", 30, 30, "")
