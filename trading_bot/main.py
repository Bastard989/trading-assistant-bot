from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder

from trading_bot.config import load_settings
from trading_bot.db import Database
from trading_bot.market import MarketClient
from trading_bot.repositories import (
    AlertRepository,
    DailyPlanRepository,
    JournalRepository,
    MarketContextRepository,
    PendingTradeRepository,
    TemplateRepository,
    TradeRepository,
    TradeReviewRepository,
    UserRepository,
    WatchlistRepository,
)
from trading_bot.telegram_handlers import BotHandlers


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    db = Database(settings.database_path)

    users = UserRepository(db)
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

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
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
    ).register(application)

    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
