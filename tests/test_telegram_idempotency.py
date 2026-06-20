import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from trading_bot.db import Database
from trading_bot.repositories import IdempotencyRepository
from trading_bot.telegram_handlers import BotHandlers


def make_handler(tmp_path) -> tuple[BotHandlers, IdempotencyRepository]:
    repository = IdempotencyRepository(Database(tmp_path / "telegram.sqlite3"))
    handler = object.__new__(BotHandlers)
    handler.allowed_user_ids = frozenset({42})
    handler.idempotency = repository
    return handler, repository


def fake_update(update_id: int = 1001):
    return SimpleNamespace(
        update_id=update_id,
        effective_user=SimpleNamespace(id=42),
        callback_query=None,
        effective_message=None,
    )


def test_duplicate_telegram_update_is_stopped(tmp_path) -> None:
    handler, repository = make_handler(tmp_path)
    update = fake_update()
    asyncio.run(handler.authorize_update(update, None))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(handler.authorize_update(update, None))
    asyncio.run(handler.finalize_update(update, None))
    state, _ = repository.begin(42, "telegram:update", "1001", "1001")
    assert state == "completed"
