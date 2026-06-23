from __future__ import annotations

import logging
import os

from telegram.ext import ApplicationBuilder

from trading_bot.config import load_settings
from trading_bot.db import Database
from trading_bot.market import MarketClient
from trading_bot.repositories import (
    AlertRepository,
    DailyPlanRepository,
    JournalRepository,
    IdempotencyRepository,
    MarketContextRepository,
    PendingTradeRepository,
    TemplateRepository,
    TradeRepository,
    TradeReviewRepository,
    UserRepository,
    WatchlistRepository,
)
from trading_bot.services.photo_trade import OpenAIPhotoTradeExtractor
from trading_bot.telegram_handlers import BotHandlers
from trading_bot.telegram_handlers import BOT_COMMANDS


async def post_init(application) -> None:
    await application.bot.set_my_commands(BOT_COMMANDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    settings = load_settings()
    db = Database(settings.database_path, auto_migrate=False)

    users = UserRepository(db)
    idempotency = IdempotencyRepository(db)
    alerts = AlertRepository(db)
    trades = TradeRepository(db)
    journal = JournalRepository(db)
    contexts = MarketContextRepository(db)
    watchlist = WatchlistRepository(db)
    daily_plans = DailyPlanRepository(db)
    pending_trades = PendingTradeRepository(db)
    trade_reviews = TradeReviewRepository(db)
    templates = TemplateRepository(db)
    market = MarketClient(settings.market)
    photo_trade_extractor = OpenAIPhotoTradeExtractor(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-5.5"),
    )

    application = ApplicationBuilder().token(settings.telegram_bot_token).post_init(post_init).build()
    BotHandlers(
        users=users,
        alerts=alerts,
        trades=trades,
        journal=journal,
        contexts=contexts,
        watchlist=watchlist,
        daily_plans=daily_plans,
        pending_trades=pending_trades,
        trade_reviews=trade_reviews,
        templates=templates,
        market=market,
        top_limit=settings.top_limit,
        alert_poll_seconds=settings.alert_poll_seconds,
        web_app_url=settings.web_app_url,
        allowed_user_ids=settings.allowed_telegram_user_ids,
        idempotency=idempotency,
        business_timezone=settings.business_timezone,
        photo_trade_extractor=photo_trade_extractor,
    ).register(application)

    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
