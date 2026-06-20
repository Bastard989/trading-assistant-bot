import asyncio

import httpx
import pytest

from trading_bot.market import InvalidSymbolError, MarketClient, MarketUnavailableError


def run(coro):
    return asyncio.run(coro)


def client_for(handler, **kwargs) -> MarketClient:
    http_client = httpx.AsyncClient(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
    )
    return MarketClient(client=http_client, base_delay=0, **kwargs)


def test_timeout_is_retried_then_reported_as_outage() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    market = client_for(handler, retries=2)
    with pytest.raises(MarketUnavailableError):
        run(market.get_price("BTC"))
    assert calls == 3
    run(market._client.aclose())


def test_429_retry_after_and_500_are_retried() -> None:
    responses = [
        (429, {"Retry-After": "0"}, {"code": -1003}),
        (500, {}, {"msg": "temporary"}),
        (200, {}, {"price": "123.45"}),
    ]

    def handler(request):
        status, headers, payload = responses.pop(0)
        return httpx.Response(status, headers=headers, json=payload, request=request)

    market = client_for(handler, retries=2)
    assert run(market.get_price("BTC")) == 123.45
    assert responses == []
    run(market._client.aclose())


def test_invalid_symbol_is_not_reported_as_outage() -> None:
    def handler(request):
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."}, request=request)

    market = client_for(handler)
    with pytest.raises(InvalidSymbolError):
        run(market.get_price("NOPE"))
    run(market._client.aclose())


def test_invalid_json_is_reported_as_outage() -> None:
    def handler(request):
        return httpx.Response(200, text="not-json", request=request)

    market = client_for(handler, retries=0)
    with pytest.raises(MarketUnavailableError):
        run(market.get_price("BTC"))
    run(market._client.aclose())


def test_cache_avoids_duplicate_safe_gets() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"price": "100"}, request=request)

    market = client_for(handler, cache_ttl=30)
    assert run(market.get_price("BTC")) == 100
    assert run(market.get_price("BTC")) == 100
    assert calls == 1
    run(market._client.aclose())


def test_circuit_breaker_opens_after_repeated_failures() -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"msg": "down"}, request=request)

    market = client_for(handler, retries=0)
    for _ in range(5):
        with pytest.raises(MarketUnavailableError):
            run(market.get_price("BTC"))
    with pytest.raises(MarketUnavailableError, match="circuit breaker"):
        run(market.get_price("BTC"))
    assert calls == 5
    run(market._client.aclose())
