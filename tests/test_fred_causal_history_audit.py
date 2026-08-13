import asyncio
import json
from datetime import date

import httpx

from scripts.audit_fred_causal_history import audit_one, build_report
from trading_bot.crisis_radar.catalog import FRED_INDICATORS, FRED_V12_RESEARCH_INDICATORS
from trading_bot.crisis_radar.sources.fred_client import FredClient


START = date(2022, 8, 13)
END = date(2026, 8, 12)


def test_fred_client_capability_contracts_are_bounded_and_do_not_leak_key() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.params["api_key"] == "secret"
        if request.url.path.endswith("/vintagedates"):
            return httpx.Response(200, json={"vintage_dates": ["2026-08-01"]})
        assert request.url.params["output_type"] == "4"
        assert request.url.params["limit"] == "1"
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-07-01", "value": "4.2", "realtime_start": "2026-08-01"}
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = FredClient("secret", client=http_client)
            result = await audit_one(
                client,
                FRED_V12_RESEARCH_INDICATORS[1],
                "disabled_research",
                started_on=START,
                ended_on=END,
            )
            assert result.capability == "verified_initial_release"
            assert result.contract_status == "verified"

    asyncio.run(scenario())
    assert len(requests) == 2


def test_capability_audit_fails_closed_and_marks_future_promotion() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/vintagedates"):
            return httpx.Response(200, json={"vintage_dates": []})
        series_id = request.url.params["series_id"]
        rows = (
            [{"date": "2026-07-01", "value": "1", "realtime_start": "2026-08-01"}]
            if series_id == "ICSA"
            else []
        )
        return httpx.Response(200, json={"observations": rows})

    async def scenario() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = FredClient("secret", client=http_client)
            return await build_report(client, started_on=START, ended_on=END)

    report = asyncio.run(scenario())
    encoded = json.dumps(report)
    assert "secret" not in encoded
    results = {item["indicator_code"]: item for item in report["results"]}
    assert results["us_initial_claims"]["contract_status"] == "verified"
    assert results["us_unemployment_rate"]["contract_status"] == "mismatch"
    assert report["counts"]["mismatch"] > 0


def test_capability_audit_does_not_mislabel_provider_error_as_absent() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    async def no_sleep(_delay: float) -> None:
        return None

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = FredClient("secret", client=http_client, sleep=no_sleep)
            return await audit_one(
                client,
                FRED_V12_RESEARCH_INDICATORS[1],
                "disabled_research",
                started_on=START,
                ended_on=END,
            )

    result = asyncio.run(scenario())
    assert result.capability == "provider_rejected_probe"
    assert result.contract_status == "unknown"
    assert result.probe_error == "FredClientError:FRED returned HTTP 429"


def test_vintage_failure_does_not_hide_successful_initial_release_probe() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/vintagedates"):
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-07-01", "value": "4", "realtime_start": "2026-08-01"}
                ]
            },
        )

    async def no_sleep(_delay: float) -> None:
        return None

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = FredClient("secret", client=http_client, sleep=no_sleep)
            return await audit_one(
                client,
                FRED_V12_RESEARCH_INDICATORS[1],
                "disabled_research",
                started_on=START,
                ended_on=END,
            )

    result = asyncio.run(scenario())
    assert result.contract_status == "verified"
    assert result.vintage_count_in_window is None
    assert result.vintage_error == "FredClientError:FRED returned HTTP 503"
    assert result.probe_error is None


def test_capability_audit_validates_selection_and_concurrency() -> None:
    client = FredClient("secret", client=httpx.AsyncClient())
    try:
        for kwargs, message in (
            ({"selected_codes": {"not-configured"}}, "selected_codes"),
            ({"concurrency": 0}, "concurrency"),
        ):
            try:
                asyncio.run(build_report(client, started_on=START, ended_on=END, **kwargs))
            except ValueError as exc:
                assert message in str(exc)
            else:  # pragma: no cover - explicit assertion branch
                raise AssertionError("invalid audit arguments were accepted")
    finally:
        asyncio.run(client._client.aclose())  # noqa: SLF001 - owned test fixture


def test_declared_live_only_series_never_claims_causal_history() -> None:
    live_only = next(item for item in FRED_INDICATORS if item.code == "sp500_30d_drawdown")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = FredClient("secret", client=http_client)
            return await audit_one(
                client,
                live_only,
                "active_scoring",
                started_on=START,
                ended_on=END,
            )

    result = asyncio.run(scenario())
    assert result.declared_backfill_mode == "live_only"
    assert result.contract_status == "live_only"
    assert result.initial_release_probe_rows == 0
