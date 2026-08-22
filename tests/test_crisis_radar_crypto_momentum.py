from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_bot.crisis_radar.crypto_momentum import (
    CryptoMomentumRepository,
    TimeframeMomentum,
    classify_crypto_momentum,
)
from trading_bot.db import Database


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def frame(interval: str, score: int, *, price: float = 100) -> TimeframeMomentum:
    return TimeframeMomentum(
        interval=interval,
        score=score,
        bullish=score >= 75,
        price=price,
        return_pct=8 if score >= 75 else -2,
        sma20=95 if score >= 75 else 105,
        sma50=90 if score >= 75 else 110,
        sma20_slope_pct=2 if score >= 75 else -1,
        volume_ratio=1.2,
        support=94,
    )


def test_confirmed_uptrend_requires_long_horizons_and_derivatives() -> None:
    result = classify_crypto_momentum(
        "BTCUSDT",
        [frame("15m", 75), frame("1h", 100), frame("4h", 100), frame("1d", 100)],
        as_of=NOW,
        funding_rate_pct=0.01,
        oi_7d_change_pct=6,
    )
    assert result.state == "confirmed_uptrend"
    assert result.score >= 90
    assert result.data_quality == 100
    assert result.payload("ru")["execution_allowed"] is False


def test_price_only_signal_stays_early_instead_of_claiming_confirmation() -> None:
    result = classify_crypto_momentum(
        "SOLUSDT",
        [frame("15m", 75), frame("1h", 100), frame("4h", 100), frame("1d", 50)],
        as_of=NOW,
        funding_rate_pct=None,
        oi_7d_change_pct=None,
    )
    assert result.state == "early_uptrend"
    assert result.confidence == "medium"
    assert "confirm_derivatives" in result.next_confirmation


def test_crowded_derivatives_are_overheated_not_a_clean_long_signal() -> None:
    result = classify_crypto_momentum(
        "ETHUSDT",
        [frame("15m", 100), frame("1h", 100), frame("4h", 100), frame("1d", 100)],
        as_of=NOW,
        funding_rate_pct=0.10,
        oi_7d_change_pct=20,
    )
    assert result.state == "overheated"
    assert "crowded_leverage" in result.evidence


def test_previous_uptrend_can_explicitly_break() -> None:
    result = classify_crypto_momentum(
        "BTCUSDT",
        [frame("15m", 25), frame("1h", 25), frame("4h", 25), frame("1d", 25)],
        as_of=NOW,
        funding_rate_pct=-0.01,
        oi_7d_change_pct=-8,
        previous_state="confirmed_uptrend",
    )
    assert result.state == "trend_break"


def test_missing_market_data_is_honestly_unknown() -> None:
    result = classify_crypto_momentum(
        "BTCUSDT", (), as_of=NOW, funding_rate_pct=None, oi_7d_change_pct=None
    )
    assert result.state == "insufficient_data"
    assert result.price is None
    assert result.data_quality == 0


def test_first_snapshot_is_baseline_and_transition_is_deduplicated(tmp_path) -> None:
    repository = CryptoMomentumRepository(Database(tmp_path / "momentum.sqlite3"))
    neutral = classify_crypto_momentum(
        "BTCUSDT",
        [frame("15m", 50), frame("1h", 50), frame("4h", 50), frame("1d", 50)],
        as_of=NOW,
        funding_rate_pct=0,
        oi_7d_change_pct=0,
    )
    confirmed = classify_crypto_momentum(
        "BTCUSDT",
        [frame("15m", 100), frame("1h", 100), frame("4h", 100), frame("1d", 100)],
        as_of=NOW + timedelta(minutes=15),
        funding_rate_pct=0.01,
        oi_7d_change_pct=5,
    )
    assert repository.save(neutral) is False
    assert repository.save(confirmed) is True
    assert repository.latest_states() == {"BTCUSDT": "confirmed_uptrend"}
    assert repository.enqueue_deliveries((42, 42)) == 1
    assert repository.enqueue_deliveries((42,)) == 0
    pending = repository.pending_deliveries()
    assert pending[0].to_state == "confirmed_uptrend"
    repository.mark_sent(pending[0].delivery_id, sent_at=NOW)
    assert repository.pending_deliveries() == []
