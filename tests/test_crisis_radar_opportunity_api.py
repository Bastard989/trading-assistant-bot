from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.test_api_security import auth_header, load_test_app
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database
from trading_bot.crisis_radar.crypto_momentum import TimeframeMomentum, classify_crypto_momentum


def test_opportunity_service_returns_honest_wait_without_market_quotes(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "opportunity.sqlite3")))
    service.bootstrap()

    payload = service.opportunities(locale="en")

    assert payload["quote_count"] == 0
    assert payload["available_asset_classes"] == []
    assert payload["ideas"][0]["side"] == "wait"
    assert payload["ideas"][0]["execution_allowed"] is False
    assert payload["ideas"][0]["analysis_only"] is True
    assert payload["ideas"][0]["reference_price"] is None
    assert payload["limitations"]


def test_opportunity_endpoint_requires_owner_auth_and_validates_locale(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    module._bybit_option_cache = (datetime.now(timezone.utc), None)
    client = TestClient(module.app)

    assert client.get("/api/crisis-radar/opportunities").status_code == 401
    response = client.get(
        "/api/crisis-radar/opportunities?locale=en&limit=3",
        headers=auth_header(42),
    )
    assert response.status_code == 200
    assert response.json()["ideas"][0]["side"] == "wait"
    assert response.json()["ideas"][0]["execution_allowed"] is False
    assert response.json()["market_data_status"]["bybit_options"]["status"] == "degraded"
    assert response.json()["market_data_status"]["tradfi_options"]["status"] == "degraded"
    assert client.get(
        "/api/crisis-radar/opportunities?locale=de",
        headers=auth_header(42),
    ).status_code == 422


def test_crypto_momentum_endpoint_is_localized_and_never_executable(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    frames = tuple(
        TimeframeMomentum(
            interval=interval, score=100, bullish=True, price=100,
            return_pct=5, sma20=95, sma50=90, sma20_slope_pct=1,
            volume_ratio=1.1, support=94,
        )
        for interval in ("15m", "1h", "4h", "1d")
    )
    result = classify_crypto_momentum(
        "BTCUSDT", frames, as_of=now, funding_rate_pct=0.01, oi_7d_change_pct=4
    )

    async def fake_all(_previous):
        return (result,)

    monkeypatch.setattr(module.crypto_momentum_monitor, "analyze_all", fake_all)
    module._crypto_momentum_cache = None
    client = TestClient(module.app)
    assert client.get("/api/crisis-radar/crypto-momentum").status_code == 401
    payload = client.get(
        "/api/crisis-radar/crypto-momentum?locale=ru", headers=auth_header(42)
    ).json()
    assert payload["items"][0]["state"] == "confirmed_uptrend"
    assert payload["items"][0]["state_label"] == "Подтверждённый рост"
    assert payload["execution_allowed"] is False
    assert payload["score_is_probability"] is False
