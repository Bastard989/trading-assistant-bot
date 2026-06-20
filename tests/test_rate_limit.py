from trading_bot.rate_limit import SlidingWindowLimiter


def test_rate_limit_and_window_expiration() -> None:
    now = [100.0]
    limiter = SlidingWindowLimiter(clock=lambda: now[0])
    assert limiter.allow("user:route", limit=2, window_seconds=60) == (True, 0)
    assert limiter.allow("user:route", limit=2, window_seconds=60) == (True, 0)
    allowed, retry_after = limiter.allow("user:route", limit=2, window_seconds=60)
    assert not allowed
    assert retry_after == 61
    now[0] = 161.0
    assert limiter.allow("user:route", limit=2, window_seconds=60) == (True, 0)
