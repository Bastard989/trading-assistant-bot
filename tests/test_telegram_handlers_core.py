from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ApplicationHandlerStop

from trading_bot.models import MarketTicker, Sentiment
from trading_bot.telegram_handlers import (
    AWAITING_OPEN_PHOTO,
    AWAITING_PROFILE,
    BotHandlers,
    command_body,
    command_number,
    extract_first_trade_price,
    extract_leverage,
    extract_price_after,
    format_close_reason_ru,
    guess_symbol,
    local_lan_ip,
    looks_number,
    looks_strict_context_args,
    open_trade_template,
    parse_alert_args,
    parse_float,
    parse_levels_token,
    parse_optional_float,
    parse_trade_caption,
    validate_trade_input,
)


class Message:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[tuple[str, dict[str, object]]] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append((text, kwargs))


class Market:
    def __init__(self) -> None:
        self.fail = False

    async def get_price(self, symbol: str) -> float:
        if self.fail:
            raise RuntimeError("offline")
        return {"BTCUSDT": 65_000.0, "ETHUSDT": 3_000.0}.get(symbol, 100.0)

    async def top_by_activity(self, limit: int):
        if self.fail:
            raise RuntimeError("offline")
        return [MarketTicker("BTCUSDT", 65_000, 10_000_000, 2.5, 66_000, 63_000)][:limit]

    async def get_sentiment(self, symbol: str):
        if self.fail:
            raise RuntimeError("offline")
        return Sentiment(symbol="BTCUSDT", long_percent=55, short_percent=45, long_short_ratio=1.2, source="test")


def repositories() -> dict[str, MagicMock]:
    names = (
        "users alerts trades journal contexts watchlist daily_plans pending_trades "
        "trade_reviews templates idempotency"
    ).split()
    repos = {name: MagicMock(name=name) for name in names}
    repos["users"].get_defaults.return_value = {
        "default_account_size": 1_000.0,
        "default_risk_percent": 1.0,
    }
    repos["users"].get_profile.return_value = ""
    repos["journal"].create.return_value = 11
    repos["contexts"].create.return_value = 12
    return repos


def make_handler(*, url: str = "https://example.test", allowed: frozenset[int] = frozenset({42})):
    repos = repositories()
    market = Market()
    handler = BotHandlers(
        repos["users"],
        repos["alerts"],
        repos["trades"],
        repos["journal"],
        repos["contexts"],
        repos["watchlist"],
        repos["daily_plans"],
        repos["pending_trades"],
        repos["trade_reviews"],
        repos["templates"],
        market,
        top_limit=10,
        alert_poll_seconds=30,
        web_app_url=url,
        allowed_user_ids=allowed,
        idempotency=repos["idempotency"],
        business_timezone="Europe/Moscow",
    )
    return handler, repos, market


def update(text: str = "", *, user_id: int = 42, update_id: int = 7):
    message = Message(text)
    return SimpleNamespace(
        update_id=update_id,
        effective_user=SimpleNamespace(id=user_id),
        message=message,
        effective_message=message,
        callback_query=None,
    )


def context(args: list[str] | None = None):
    return SimpleNamespace(args=args or [], user_data={}, error=None)


class FakeApplication:
    def __init__(self, jobs: bool = True) -> None:
        self.handlers: list[tuple[object, int | None]] = []
        self.errors: list[object] = []
        self.job_queue = MagicMock() if jobs else None

    def add_handler(self, handler: object, group: int | None = None) -> None:
        self.handlers.append((handler, group))

    def add_error_handler(self, handler: object) -> None:
        self.errors.append(handler)


def run(awaitable):
    return asyncio.run(awaitable)


def test_registers_handlers_and_periodic_jobs() -> None:
    handler, _, _ = make_handler()
    app = FakeApplication()
    handler.register(app)
    assert len(app.handlers) >= 35
    assert [group for _, group in app.handlers[:2]] == [-1, 999]
    assert app.errors == [handler.on_error]
    assert app.job_queue.run_repeating.call_count == 2

    no_jobs = FakeApplication(jobs=False)
    handler.register(no_jobs)
    assert len(no_jobs.handlers) == len(app.handlers)


def test_authorization_finalize_and_denial_paths() -> None:
    handler, repos, _ = make_handler()
    repos["idempotency"].begin.return_value = ("new", None)
    allowed = update()
    run(handler.authorize_update(allowed, context()))
    run(handler.finalize_update(allowed, context()))
    repos["idempotency"].complete.assert_called_once()

    repos["idempotency"].begin.return_value = ("completed", "{}")
    with pytest.raises(ApplicationHandlerStop):
        run(handler.authorize_update(allowed, context()))

    denied = update(user_id=99)
    with pytest.raises(ApplicationHandlerStop):
        run(handler.authorize_update(denied, context()))
    assert "закрыт" in denied.message.replies[0][0]

    callback = SimpleNamespace(answer=AsyncMock())
    denied_callback = SimpleNamespace(
        update_id=9,
        effective_user=SimpleNamespace(id=99),
        effective_message=None,
        callback_query=callback,
    )
    with pytest.raises(ApplicationHandlerStop):
        run(handler.authorize_update(denied_callback, context()))
    callback.answer.assert_awaited_once()


def test_start_menu_help_profile_and_defaults() -> None:
    handler, repos, _ = make_handler()
    for method in (handler.start, handler.menu, handler.help):
        item = update()
        run(method(item, context()))
        assert item.message.replies
    repos["users"].ensure_user.assert_called_once_with(42)

    item = update()
    ctx = context()
    repos["users"].get_profile.return_value = "не торговать без стопа"
    run(handler.profile(item, ctx))
    assert ctx.user_data[AWAITING_PROFILE]
    assert "не торговать" in item.message.replies[0][0]

    cases = [([], "Формат"), (["x", "1"], "числами"), (["0", "1"], "больше нуля")]
    for args, expected in cases:
        item = update()
        run(handler.defaults(item, context(args)))
        assert expected in item.message.replies[0][0]
    item = update()
    run(handler.defaults(item, context(["1000", "1,5"])))
    repos["users"].set_defaults.assert_called_once_with(42, 1000.0, 1.5)


def test_simple_market_commands_success_validation_and_failure() -> None:
    handler, _, market = make_handler()
    for method, args, expected in (
        (handler.price, ["BTC"], "BTCUSDT"),
        (handler.top, ["1"], "Активные"),
        (handler.sentiment, ["BTC"], "BTCUSDT"),
        (handler.distance, ["BTC", "64000", "66000"], "Distance"),
        (handler.risk, ["BTC", "long", "65000", "64000", "68000"], "Risk"),
    ):
        item = update()
        run(method(item, context(args)))
        assert expected.lower() in item.message.replies[0][0].lower()

    invalid = (
        (handler.price, [], "Формат"),
        (handler.top, ["abc"], "Формат"),
        (handler.sentiment, [], "Формат"),
        (handler.distance, ["BTC"], "Формат"),
        (handler.distance, ["BTC", "bad"], "числами"),
        (handler.risk, [], "Not enough"),
    )
    for method, args, expected in invalid:
        item = update()
        run(method(item, context(args)))
        assert expected in item.message.replies[0][0]

    market.fail = True
    for method, args, expected in (
        (handler.price, ["BTC"], "Не смог"),
        (handler.top, [], "Не смог"),
        (handler.sentiment, ["BTC"], "Не смог"),
        (handler.distance, ["BTC", "1"], "Не смог"),
    ):
        item = update()
        run(method(item, context(args)))
        assert expected in item.message.replies[0][0]


def test_text_state_machine_and_open_prompt() -> None:
    handler, repos, _ = make_handler()
    item = update("мой профиль")
    ctx = context()
    ctx.user_data[AWAITING_PROFILE] = True
    run(handler.on_text(item, ctx))
    repos["users"].set_profile.assert_called_once_with(42, "мой профиль")

    item = update("простой текст")
    run(handler.on_text(item, context()))
    assert "/help" in item.message.replies[0][0]

    item = update("/open")
    ctx = context()
    run(handler.open_trade_note(item, ctx))
    assert ctx.user_data[AWAITING_OPEN_PHOTO]
    assert "Количество позиций" in item.message.replies[0][0]


def test_journal_context_watch_plan_templates_and_lists() -> None:
    handler, repos, _ = make_handler()
    item = update()
    run(handler.journal_entry(item, context(["BTC", "win", "по плану"])))
    assert "#11" in item.message.replies[0][0]
    item = update()
    run(handler.journal_entry(item, context([])))
    assert "Слишком мало" in item.message.replies[0][0]

    item = update("/note BTC наблюдение")
    run(handler.note_entry(item, context()))
    assert "#11" in item.message.replies[0][0]
    item = update("/note")
    run(handler.note_entry(item, context()))
    assert "Формат" in item.message.replies[0][0]

    item = update()
    run(handler.context_entry(item, context(["BTC", "1h", "long", "levels=1,2", "invalid=0.5", "structure=trend", "note"])))
    assert "#12" in item.message.replies[0][0]
    item = update()
    run(handler.context_entry(item, context([])))
    assert "Нужно минимум" in item.message.replies[0][0]

    repos["watchlist"].list_symbols.return_value = []
    item = update()
    run(handler.watch(item, context()))
    assert "пусто" in item.message.replies[0][0]
    item = update()
    run(handler.watch(item, context(["btc", "eth"])))
    repos["watchlist"].replace.assert_called_once_with(42, ("BTCUSDT", "ETHUSDT"))

    repos["daily_plans"].latest.return_value = None
    item = update()
    run(handler.plan(item, context()))
    assert "Формат" in item.message.replies[0][0]
    item = update()
    run(handler.plan(item, context(["BTC,ETH", "3", "50", "от", "уровней"])))
    repos["daily_plans"].upsert.assert_called_once()

    repos["templates"].list_for_user.return_value = []
    item = update()
    run(handler.templates_list(item, context()))
    assert "пока нет" in item.message.replies[0][0]
    item = update()
    run(handler.template_save(item, context(["scalp", "{symbol}", "{price}"])))
    repos["templates"].upsert.assert_called_once()
    repos["templates"].get.return_value = "Цена {symbol}: {price}"
    item = update()
    run(handler.template_render(item, context(["scalp", "symbol=BTC"])))
    assert "BTCUSDT" in item.message.replies[0][0]


def test_parser_helpers_and_validation() -> None:
    assert parse_float("1,25") == 1.25
    assert command_body("/open@trade_bot  BTC long", "open") == "BTC long"
    assert "Количество позиций" in open_trade_template()
    assert guess_symbol("Монета: sol") == "SOLUSDT"
    assert guess_symbol("торгую биткоин") == "BTCUSDT"
    assert guess_symbol("AVAX long") == "AVAXUSDT"
    assert guess_symbol("только идея") == ""

    parsed = parse_trade_caption(
        "Монета: BTC\nСторона: лонг\nЦена входа: 65000\nСтоп: 64000\nТейк: 68000\nКоличество: 0,01\nПлечо: 5x"
    )
    assert parsed == {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry": 65000.0,
        "stop": 64000.0,
        "target": 68000.0,
        "leverage": 5.0,
        "quantity": 0.01,
    }
    assert parse_trade_caption("BTC без направления") is None
    assert extract_leverage("плечо: 10x") == 10
    assert extract_price_after("стоп около 12,5", ("стоп",)) == 12.5
    assert extract_price_after("нет цены", ("стоп",)) is None
    assert extract_first_trade_price("BTC 100 стоп 90 тейк 120", 90, 120) == 100
    assert looks_strict_context_args(["BTC", "1h", "long"])
    assert not looks_strict_context_args(["BTC", "hour", "long"])
    assert format_close_reason_ru("stop_loss") == "стоп лосс"
    assert format_close_reason_ru("custom_reason") == "custom reason"
    assert command_number(1.23456789, "-") == "1.23456789"
    assert command_number(None, "-") == "-"
    assert command_number("bad", "-") == "-"
    assert parse_optional_float("-") is None
    assert parse_optional_float("1,5") == 1.5
    assert parse_levels_token("1,2;3") == [1, 2, 3]
    assert looks_number("1,2") and not looks_number("no")

    assert parse_alert_args(["btc", ">=", "100"]) == ("BTCUSDT", "above", 100)
    assert parse_alert_args(["btc", "100", "below"]) == ("BTCUSDT", "below", 100)
    with pytest.raises(ValueError):
        parse_alert_args(["btc"])
    with pytest.raises(ValueError):
        parse_alert_args(["btc", "=", "100"])

    validate_trade_input("long", 100, 90, 1, 2, 120)
    validate_trade_input("short", 100, 110, 1, 2, 80)
    invalid = (
        ("neutral", 100, 90, 1, 1, None),
        ("long", 0, 90, 1, 1, None),
        ("long", 100, 110, 1, 1, None),
        ("short", 100, 90, 1, 1, None),
        ("long", 100, 90, 1, 1, 80),
        ("short", 100, 110, 1, 1, 120),
    )
    for args in invalid:
        with pytest.raises(ValueError):
            validate_trade_input(*args)


def test_markup_for_https_local_and_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    secure, _, _ = make_handler(url="https://example.test/app")
    assert secure._miniapp_rows(42)[0][0].web_app.url == "https://example.test/app/"

    monkeypatch.setattr("trading_bot.telegram_handlers.local_lan_ip", lambda: "192.168.1.10")
    local, _, _ = make_handler(url="http://127.0.0.1:8000")
    rows = local._miniapp_rows(42)
    assert rows[0][0].url == "http://127.0.0.1:8000/"
    assert rows[1][0].url == "http://192.168.1.10:8000/"

    plain, _, _ = make_handler(url="http://internal:8000")
    assert plain._miniapp_rows(42)[0][0].url == "http://internal:8000/"
    assert secure._main_markup(42).inline_keyboard
    assert secure._miniapp_markup(42).inline_keyboard


def test_local_lan_ip_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = MagicMock()
    sock.getsockname.return_value = ("10.0.0.2", 123)
    monkeypatch.setattr("trading_bot.telegram_handlers.socket.socket", lambda *_: sock)
    assert local_lan_ip() == "10.0.0.2"
    sock.close.assert_called_once()

    broken = MagicMock()
    broken.connect.side_effect = OSError
    monkeypatch.setattr("trading_bot.telegram_handlers.socket.socket", lambda *_: broken)
    assert local_lan_ip() == "127.0.0.1"
    broken.close.assert_called_once()
