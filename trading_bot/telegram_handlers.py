from __future__ import annotations

import logging
import re
import socket
from datetime import date, datetime

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.error import TelegramError

from trading_bot.formatting import (
    format_distance,
    format_risk,
    format_review,
    format_sentiment,
    format_ticker,
    format_trade,
    money,
    signed_money,
)
from trading_bot.evaluator import build_distances, review_trade
from trading_bot.market import MarketClient, normalize_symbol
from trading_bot.models import TradeDraft
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
from trading_bot.risk import RiskInputError, calculate_risk
from trading_bot.templates import (
    base_values,
    enrich_trade_math,
    parse_key_values,
    placeholders,
    render_template,
    valid_template_name,
)
from trading_bot.timeframe_analyzer import analyze_klines

logger = logging.getLogger(__name__)

AWAITING_PROFILE = "awaiting_profile"
OUTCOMES = {"win", "loss", "breakeven", "idea"}
BOT_COMMANDS = [
    BotCommand("miniapp", "открыть кабинет трейдера"),
    BotCommand("menu", "кнопки бота"),
    BotCommand("open", "открыть сделку"),
    BotCommand("note", "запись в дневник"),
    BotCommand("trades", "открытые сделки"),
    BotCommand("edit", "изменить сделку или добавить фото"),
    BotCommand("close", "закрыть сделку"),
    BotCommand("stats", "статистика сделок"),
    BotCommand("help", "короткая инструкция"),
]
ALBUMS_KEY = "pending_media_groups"


class BotHandlers:
    def __init__(
        self,
        users: UserRepository,
        alerts: AlertRepository,
        trades: TradeRepository,
        journal: JournalRepository,
        contexts: MarketContextRepository,
        watchlist: WatchlistRepository,
        daily_plans: DailyPlanRepository,
        pending_trades: PendingTradeRepository,
        trade_reviews: TradeReviewRepository,
        templates: TemplateRepository,
        market: MarketClient,
        top_limit: int,
        alert_poll_seconds: int,
        web_app_url: str,
        allowed_user_ids: frozenset[int],
    ) -> None:
        self.users = users
        self.alerts = alerts
        self.trades = trades
        self.journal = journal
        self.contexts = contexts
        self.watchlist = watchlist
        self.daily_plans = daily_plans
        self.pending_trades = pending_trades
        self.trade_reviews = trade_reviews
        self.templates = templates
        self.market = market
        self.top_limit = top_limit
        self.alert_poll_seconds = alert_poll_seconds
        self.web_app_url = web_app_url
        self.allowed_user_ids = allowed_user_ids

    def register(self, application: Application) -> None:
        application.add_handler(TypeHandler(Update, self.authorize_update), group=-1)
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("menu", self.menu))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(CommandHandler("profile", self.profile))
        application.add_handler(CommandHandler("defaults", self.defaults))
        application.add_handler(CommandHandler("price", self.price))
        application.add_handler(CommandHandler("top", self.top))
        application.add_handler(CommandHandler("sentiment", self.sentiment))
        application.add_handler(CommandHandler("distance", self.distance))
        application.add_handler(CommandHandler("risk", self.risk))
        application.add_handler(CommandHandler("open", self.open_trade_note))
        application.add_handler(CommandHandler("trade", self.trade))
        application.add_handler(CommandHandler("close", self.close_trade))
        application.add_handler(CommandHandler("canceltrade", self.cancel_trade))
        application.add_handler(CommandHandler("trades", self.trades_list))
        application.add_handler(CommandHandler("edit", self.edit_trade))
        application.add_handler(CommandHandler("stats", self.stats))
        application.add_handler(CommandHandler("alert", self.alert))
        application.add_handler(CommandHandler("alerts", self.alerts_list))
        application.add_handler(CommandHandler("journal", self.journal_entry))
        application.add_handler(CommandHandler("note", self.note_entry))
        application.add_handler(CommandHandler("entries", self.entries))
        application.add_handler(CommandHandler("context", self.context_entry))
        application.add_handler(CommandHandler("contexts", self.contexts_list))
        application.add_handler(CommandHandler("autocontext", self.autocontext))
        application.add_handler(CommandHandler("watch", self.watch))
        application.add_handler(CommandHandler("plan", self.plan))
        application.add_handler(CommandHandler("miniapp", self.miniapp))
        application.add_handler(CommandHandler("templates", self.templates_list))
        application.add_handler(CommandHandler("template", self.template_save))
        application.add_handler(CommandHandler("render", self.template_render))
        application.add_handler(CallbackQueryHandler(self.on_callback))
        application.add_handler(MessageHandler(filters.PHOTO, self.on_photo))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

        if application.job_queue:
            application.job_queue.run_repeating(
                self.check_alerts,
                interval=self.alert_poll_seconds,
                first=5,
                name="price-alerts",
            )
            application.job_queue.run_repeating(
                self.refresh_auto_contexts,
                interval=3600,
                first=20,
                name="auto-timeframe-context",
            )

    async def authorize_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user and user.id in self.allowed_user_ids:
            return
        if update.callback_query:
            await update.callback_query.answer("Доступ запрещён", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text("Доступ к этому боту закрыт.")
        raise ApplicationHandlerStop

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.users.ensure_user(update.effective_user.id)
        await update.message.reply_text(
            "Я фиксирую твои сделки и дневник.\n\n"
            "Как пользоваться:\n"
            "1. Нажми «Открыть сделку» и заполни шаблон. Количество позиций обязательно.\n"
            "2. Запись в дневник: /note BTC идея/ошибка/наблюдение\n"
            "3. Фото можно отправлять вместе с /open или /note — я сохраню их в дневнике.\n"
            "4. Сделки закроются сами, когда цена Binance дойдет до стопа или тейка.\n\n"
            "Перед учетом сделок лучше задать депозит и риск: /defaults 1000 1",
            reply_markup=self._main_markup(update.effective_user.id),
        )

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Кнопки бота:",
            reply_markup=self._main_markup(update.effective_user.id),
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Бот — для фиксации сделок и дневника.\n\n"
            "Открыть сделку: нажми кнопку в /menu или отправь /open без данных, затем заполни шаблон.\n\n"
            "Запись в дневник:\n"
            "/note BTC идея/ошибка/наблюдение\n\n"
            "С фото: прикрепи скрин и сделай подпись /open ... или /note ...\n\n"
            "/trades — открытые сделки\n"
            "/close 12 66731 — закрыть вручную\n"
            "/stats — статистика\n"
            "/miniapp — цены, топ монет, контекст, таблицы"
        )

    async def profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data[AWAITING_PROFILE] = True
        current = self.users.get_profile(update.effective_user.id)
        text = "Опиши свои правила торговли, сетапы, запреты и что нужно проверять перед входом."
        if current:
            text += f"\n\nСейчас сохранено:\n{current}"
        await update.message.reply_text(text)

    async def defaults(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 2:
            await update.message.reply_text("Формат: /defaults 1000 1")
            return
        try:
            account_size = parse_float(context.args[0])
            risk_percent = parse_float(context.args[1])
        except ValueError:
            await update.message.reply_text("Депозит и риск должны быть числами.")
            return
        if account_size <= 0 or risk_percent <= 0:
            await update.message.reply_text("Депозит и риск должны быть больше нуля.")
            return
        self.users.set_defaults(update.effective_user.id, account_size, risk_percent)
        await update.message.reply_text(f"Сохранил: депозит {money(account_size)} USDT, риск {risk_percent:.2f}%.")

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if context.user_data.pop(AWAITING_PROFILE, False):
            self.users.set_profile(update.effective_user.id, update.message.text.strip())
            await update.message.reply_text("Профиль торговли сохранил.")
            return
        await update.message.reply_text("Принял текст. Для списка команд отправь /help.")

    async def price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 1:
            await update.message.reply_text("Формат: /price BTC")
            return
        symbol = normalize_symbol(context.args[0])
        try:
            price = await self.market.get_price(symbol)
        except Exception:
            logger.exception("Price request failed")
            await update.message.reply_text("Не смог получить цену. Проверь тикер или доступ к Binance.")
            return
        await update.message.reply_text(f"{symbol}: {money(price)} USDT")

    async def top(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        limit = self.top_limit
        if context.args:
            try:
                limit = max(1, min(25, int(context.args[0])))
            except ValueError:
                await update.message.reply_text("Формат: /top или /top 15")
                return
        try:
            tickers = await self.market.top_by_activity(limit)
        except Exception:
            logger.exception("Top request failed")
            await update.message.reply_text("Не смог получить список монет. Проверь интернет на сервере.")
            return
        lines = ["Активные монеты сейчас. Это не сигнал, а список для отбора:"]
        lines.extend(format_ticker(ticker, index) for index, ticker in enumerate(tickers, 1))
        await update.message.reply_text("\n".join(lines))

    async def sentiment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 1:
            await update.message.reply_text("Формат: /sentiment BTC")
            return
        try:
            result = await self.market.get_sentiment(context.args[0])
        except Exception:
            logger.exception("Sentiment request failed")
            await update.message.reply_text("Не смог получить long/short ratio.")
            return
        await update.message.reply_text(format_sentiment(result))

    async def distance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 2:
            await update.message.reply_text("Формат: /distance BTC 64000 66000")
            return
        symbol = normalize_symbol(context.args[0])
        try:
            levels = [parse_float(arg) for arg in context.args[1:]]
            current_price = await self.market.get_price(symbol)
        except ValueError:
            await update.message.reply_text("Уровни должны быть числами.")
            return
        except Exception:
            logger.exception("Distance price request failed")
            await update.message.reply_text("Не смог получить цену для расчета distance.")
            return
        distances = build_distances(current_price, {f"level {index}": level for index, level in enumerate(levels, 1)})
        lines = [f"{symbol} price: {money(current_price)} USDT", "Distance от уровней:"]
        lines.extend(format_distance(distance) for distance in distances)
        await update.message.reply_text("\n".join(lines))

    async def risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            calc = self._parse_risk_args(update.effective_user.id, context.args)
        except RiskInputError as exc:
            await update.message.reply_text(
                f"{exc}\n\nФормат: /risk BTC long 65000 64000 68000 1000 1 5\n"
                "Можно target поставить '-' и использовать сохраненные defaults."
            )
            return
        await update.message.reply_text(format_risk(calc))

    async def open_trade_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        note = command_body(update.effective_message.text or "", "open")
        if not note:
            await update.effective_message.reply_text(open_trade_template())
            return
        text = await self._handle_trade_note(update.effective_user.id, note, [], require_trade=True)
        await update.effective_message.reply_text(text, reply_markup=self._main_markup(update.effective_user.id))

    async def trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            draft = self._parse_trade_args(context.args)
        except ValueError as exc:
            await update.message.reply_text(f"Ошибка в параметрах сделки: {exc}\nФормат: /trade BTC long 65000 64000 68000 0.01 5 заметка")
            return

        review = await self._review_draft(update.effective_user.id, draft)
        if review.severity in {"high", "block", "medium"}:
            pending_id = self.pending_trades.create(update.effective_user.id, draft, review)
            await update.message.reply_text(
                format_review(review),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Игнорировать и внести", callback_data=f"ignore_trade:{pending_id}")],
                        [InlineKeyboardButton("Сохранить как идею", callback_data=f"idea_trade:{pending_id}")],
                        [InlineKeyboardButton("Отменить", callback_data=f"drop_trade:{pending_id}")],
                    ]
                ),
            )
            return

        trade_id = self._save_trade(update.effective_user.id, draft, review_score=review.score)
        self.trade_reviews.create(update.effective_user.id, draft.symbol, draft.side, review, trade_id=trade_id)
        await update.message.reply_text(f"Сделка #{trade_id} сохранена.\n\n{format_review(review)}")

    async def close_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 2:
            await update.message.reply_text("Формат: /close 12 67200 1.5 заметка")
            return
        try:
            trade_id = int(context.args[0])
            exit_price = parse_float(context.args[1])
            fees = 0.0
            note_start = 2
            if len(context.args) > 2 and looks_number(context.args[2]):
                fees = parse_float(context.args[2])
                note_start = 3
            note = " ".join(context.args[note_start:])
        except ValueError:
            await update.message.reply_text("ID, exit_price и fees должны быть числами.")
            return

        trade = self.trades.close(update.effective_user.id, trade_id, exit_price, fees, note, close_reason="manual")
        if not trade:
            await update.message.reply_text("Не нашел открытую сделку с таким ID.")
            return
        await update.message.reply_text(f"Сделка закрыта.\n{format_trade(trade)}")

    async def cancel_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 1:
            await update.message.reply_text("Формат: /canceltrade 12")
            return
        try:
            trade_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("ID сделки должен быть числом.")
            return
        ok = self.trades.cancel(update.effective_user.id, trade_id)
        await update.message.reply_text("Сделку отменил." if ok else "Не нашел открытую сделку с таким ID.")

    async def edit_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        raw = update.message.text.partition(" ")[2].strip()
        text = self._edit_trade_from_text(update.effective_user.id, raw, [])
        await update.message.reply_text(text, reply_markup=self._main_markup(update.effective_user.id))

    async def trades_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        status = context.args[0].lower() if context.args else "open"
        if status == "all":
            status = None
        if status and status not in {"open", "closed", "cancelled"}:
            await update.message.reply_text("Формат: /trades или /trades open|closed|cancelled|all")
            return
        rows = self.trades.list_for_user(update.effective_user.id, status=status)
        if not rows:
            await update.message.reply_text("Открытых сделок нет." if status == "open" else "Сделок пока нет.")
            return
        await update.message.reply_text("\n\n".join(format_trade(row) for row in rows))

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        stats = self.trades.stats(user_id)
        by_symbol = self.trades.stats_by_symbol(user_id)
        closed = int(stats["closed"] or 0)
        wins = int(stats["wins"] or 0)
        losses = int(stats["losses"] or 0)
        winrate = wins / closed * 100 if closed else 0
        lines = [
            "Статистика сделок:",
            f"Total: {int(stats['total'] or 0)} | Closed: {closed}",
            f"Wins: {wins} | Losses: {losses} | Winrate: {winrate:.2f}%",
            f"Net PnL: {signed_money(stats['net_pnl'])} USDT",
            f"Avg PnL: {signed_money(stats['avg_pnl'])} USDT",
            f"Best/Worst: {signed_money(stats['best_pnl'])} / {signed_money(stats['worst_pnl'])} USDT",
        ]
        if by_symbol:
            lines.append("\nПо монетам:")
            for row in by_symbol[:10]:
                lines.append(
                    f"{row['symbol']}: {int(row['total'])} trades, "
                    f"net {signed_money(row['net_pnl'])} USDT, "
                    f"W/L {int(row['wins'] or 0)}/{int(row['losses'] or 0)}"
                )
        await update.message.reply_text("\n".join(lines))

    async def alert(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            symbol, direction, price = parse_alert_args(context.args)
        except ValueError as exc:
            await update.message.reply_text(f"{exc}\nФормат: /alert BTC >= 65000 или /alert ETH below 3000")
            return
        alert_id = self.alerts.create(update.effective_user.id, symbol, direction, price)
        sign = ">=" if direction == "above" else "<="
        await update.message.reply_text(f"Алерт #{alert_id} включен: {symbol} {sign} {money(price)}.")

    async def alerts_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        rows = self.alerts.list_for_user(update.effective_user.id)
        if not rows:
            await update.message.reply_text("Активных алертов нет.")
            return

        for row in rows:
            sign = ">=" if row["direction"] == "above" else "<="
            last = "-" if row["last_price"] is None else money(row["last_price"])
            await update.message.reply_text(
                f"#{row['id']} {row['symbol']} {sign} {money(row['target_price'])}\nLast: {last}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Cancel", callback_data=f"cancel_alert:{row['id']}")]]
                ),
            )

    async def journal_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            entry_id = self._create_journal_from_args(update.effective_user.id, context.args)
        except ValueError as exc:
            await update.message.reply_text(f"{exc}\nФормат: /journal BTC win описание")
            return
        await update.message.reply_text(f"Запись дневника #{entry_id} сохранена.")

    async def note_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        note = update.message.text.partition(" ")[2].strip()
        if not note:
            await update.message.reply_text("Формат: /note BTC описание мысли, ошибки или итога")
            return
        entry_id = self._create_free_note(update.effective_user.id, note)
        await update.message.reply_text(f"Запись дневника #{entry_id} сохранена.", reply_markup=self._main_markup(update.effective_user.id))

    async def on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message or not message.photo:
            return
        caption = message.caption or ""
        file_id = message.photo[-1].file_id
        if message.media_group_id:
            albums = context.application.bot_data.setdefault(ALBUMS_KEY, {})
            key = f"{update.effective_chat.id}:{message.media_group_id}"
            album = albums.setdefault(
                key,
                {
                    "chat_id": update.effective_chat.id,
                    "user_id": update.effective_user.id,
                    "caption": "",
                    "file_ids": [],
                    "scheduled": False,
                },
            )
            if caption:
                album["caption"] = caption
            album["file_ids"].append(file_id)
            if not album["scheduled"] and context.job_queue:
                album["scheduled"] = True
                context.job_queue.run_once(self.process_media_group, 1.2, data={"key": key}, name=f"media_group:{key}")
            return

        text = await self._handle_photo_note(update.effective_user.id, caption, [file_id])
        await message.reply_text(text, reply_markup=self._miniapp_markup(update.effective_user.id))

    async def process_media_group(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        key = context.job.data["key"]
        albums = context.application.bot_data.setdefault(ALBUMS_KEY, {})
        album = albums.pop(key, None)
        if not album:
            return
        text = await self._handle_photo_note(album["user_id"], album["caption"], album["file_ids"])
        await context.bot.send_message(chat_id=album["chat_id"], text=text, reply_markup=self._miniapp_markup(album["user_id"]))

    async def entries(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        symbol = normalize_symbol(context.args[0]) if context.args else ""
        rows = self.journal.list_for_user(update.effective_user.id, symbol=symbol)
        if not rows:
            await update.message.reply_text("Записей дневника пока нет.")
            return
        lines = []
        for row in rows:
            shot = " + screenshot" if row["screenshot_file_id"] else ""
            lines.append(
                f"#{row['id']} {row['symbol'] or '-'} {row['outcome']}{shot}\n"
                f"{row['description']}\n{row['created_at']}"
            )
        await update.message.reply_text("\n\n".join(lines))

    async def context_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        raw_text = update.message.text.partition(" ")[2].strip()
        if raw_text and not looks_strict_context_args(context.args):
            text = await self._handle_trade_note(update.effective_user.id, raw_text, [])
            await update.message.reply_text(text, reply_markup=self._miniapp_markup(update.effective_user.id))
            return

        try:
            context_id = self._create_context_from_args(update.effective_user.id, context.args)
        except ValueError as exc:
            await update.message.reply_text(
                f"{exc}\nМожно проще: /context биткоин 65936 лонг стоп 65614 тейк 66731 причина входа"
            )
            return
        await update.message.reply_text(f"Контекст #{context_id} сохранен.")

    async def contexts_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        symbol = normalize_symbol(context.args[0]) if context.args else ""
        rows = self.contexts.list_for_user(update.effective_user.id, symbol=symbol)
        if not rows:
            await update.message.reply_text("Контекста пока нет.")
            return
        lines = []
        for row in rows:
            shot = " + screenshot" if row["screenshot_file_id"] else ""
            invalid = "" if row["invalidation_level"] is None else f" invalid {money(row['invalidation_level'])}"
            lines.append(
                f"#{row['id']} {row['symbol']} {row['timeframe']} {row['bias'].upper()}{shot}\n"
                f"structure: {row['structure'] or '-'} | levels: {row['levels'] or '-'}{invalid}\n"
                f"{row['note']}"
            )
        await update.message.reply_text("\n\n".join(lines))

    async def autocontext(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        symbols = tuple(normalize_symbol(arg) for arg in context.args) if context.args else tuple(self.watchlist.list_symbols(update.effective_user.id))
        if not symbols:
            await update.message.reply_text("Сначала задай watchlist: /watch BTC ETH SOL или передай монету: /autocontext BTC")
            return
        lines = await self._update_auto_contexts_for_user(update.effective_user.id, symbols, ("1d", "1h", "15m"))
        await update.message.reply_text("\n".join(lines))

    async def watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            symbols = self.watchlist.list_symbols(update.effective_user.id)
            await update.message.reply_text("Watchlist: " + (", ".join(symbols) if symbols else "пусто"))
            return
        symbols = tuple(normalize_symbol(arg) for arg in context.args)
        self.watchlist.replace(update.effective_user.id, symbols)
        await update.message.reply_text("Watchlist обновлен: " + ", ".join(symbols))

    async def plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 3:
            latest = self.daily_plans.latest(update.effective_user.id)
            if latest:
                await update.message.reply_text(
                    f"Последний план {latest['plan_date']}:\n"
                    f"Монеты: {latest['allowed_symbols'] or '-'}\n"
                    f"Риск: {latest['max_daily_risk_percent']}% | стоп: {money(latest['max_daily_loss'])} USDT\n"
                    f"{latest['plan_text']}"
                )
            else:
                await update.message.reply_text("Формат: /plan BTC,ETH,SOL 3 50 торгую только от уровней")
            return
        try:
            symbols = tuple(normalize_symbol(item) for item in context.args[0].replace(";", ",").split(",") if item)
            max_risk = parse_float(context.args[1])
            max_loss = parse_float(context.args[2])
        except ValueError:
            await update.message.reply_text("Риск и дневной стоп должны быть числами.")
            return
        text = " ".join(context.args[3:])
        self.daily_plans.upsert(update.effective_user.id, date.today(), symbols, max_risk, max_loss, text)
        await update.message.reply_text(
            f"План на сегодня сохранен.\nМонеты: {', '.join(symbols)}\nРиск: {max_risk:.2f}% | стоп: {money(max_loss)} USDT"
        )

    async def miniapp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Открыть кабинет трейдера:",
            reply_markup=self._miniapp_markup(update.effective_user.id),
        )

    async def templates_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        rows = self.templates.list_for_user(update.effective_user.id)
        if not rows:
            await update.message.reply_text("Макетов пока нет.")
            return
        lines = ["Макеты:"]
        for row in rows:
            fields = ", ".join(placeholders(row["body"])) or "без полей"
            lines.append(f"{row['name']} ({row['source']}): {fields}")
        lines.append("\nПример: /render entry symbol=BTC side=long entry=65000 stop=64000 target=68000 qty=0.01 reason=пробой")
        await update.message.reply_text("\n".join(lines))

    async def template_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 2:
            await update.message.reply_text(
                "Формат: /template scalp План {symbol} {side_upper}: вход {entry}, стоп {stop}, тейк {target}"
            )
            return
        name = context.args[0].lower()
        if not valid_template_name(name):
            await update.message.reply_text("Имя макета: только буквы, цифры, _ или -, до 32 символов.")
            return
        body = " ".join(context.args[1:]).replace("\\n", "\n")
        self.templates.upsert(update.effective_user.id, name, body)
        fields = ", ".join(placeholders(body)) or "без полей"
        await update.message.reply_text(f"Макет `{name}` сохранен.\nПоля: {fields}", parse_mode="Markdown")

    async def template_render(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 1:
            await update.message.reply_text("Формат: /render entry symbol=BTC entry=65000 stop=64000 target=68000 qty=0.01")
            return
        name = context.args[0].lower()
        body = self.templates.get(update.effective_user.id, name)
        if not body:
            await update.message.reply_text("Не нашел такой макет. Посмотри /templates.")
            return
        try:
            rendered = await self._render_template_for_user(update.effective_user.id, body, context.args[1:])
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        await update.message.reply_text(rendered)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        action, raw_id = query.data.split(":", 1)
        if action == "cmd":
            if raw_id == "templates":
                rows = self.templates.list_for_user(update.effective_user.id)
                lines = ["Макеты:"]
                for row in rows:
                    lines.append(f"{row['name']} ({row['source']})")
                await query.message.reply_text("\n".join(lines))
            elif raw_id == "top":
                tickers = await self.market.top_by_activity(self.top_limit)
                lines = ["Активные монеты сейчас:"]
                lines.extend(format_ticker(ticker, index) for index, ticker in enumerate(tickers, 1))
                await query.message.reply_text("\n".join(lines))
            elif raw_id == "open_trades":
                rows = self.trades.list_for_user(update.effective_user.id, status="open")
                await query.message.reply_text(
                    "\n\n".join(format_trade(row) for row in rows) if rows else "Открытых сделок нет."
                )
            elif raw_id == "open_template":
                await query.message.reply_text(open_trade_template())
            elif raw_id == "trades":
                rows = self.trades.list_for_user(update.effective_user.id)
                await query.message.reply_text("\n\n".join(format_trade(row) for row in rows) if rows else "Сделок пока нет.")
            elif raw_id == "stats":
                await query.message.reply_text("Статистика доступна командой /stats.")
            return

        if action == "cancel_alert":
            ok = self.alerts.cancel(update.effective_user.id, int(raw_id))
            await query.edit_message_text("Алерт отменен." if ok else "Не нашел активный алерт.")
            return

        if action in {"ignore_trade", "drop_trade", "idea_trade"}:
            pending_id = int(raw_id)
            row = self.pending_trades.get(update.effective_user.id, pending_id)
            if not row:
                await query.edit_message_text("Pending-сделка уже не найдена.")
                return

            if action == "drop_trade":
                self.pending_trades.delete(update.effective_user.id, pending_id)
                await query.edit_message_text("Сделку отменил. Хорошая пауза тоже позиция.")
                return

            if action == "idea_trade":
                entry_id = self.journal.create(
                    update.effective_user.id,
                    symbol=row["symbol"],
                    outcome="idea",
                    description=f"Остановленная сделка {row['side'].upper()} entry {money(row['entry_price'])}: {row['note']}",
                )
                self.pending_trades.delete(update.effective_user.id, pending_id)
                await query.edit_message_text(f"Сохранил как идею дневника #{entry_id}.")
                return

            draft = TradeDraft(
                symbol=row["symbol"],
                side=row["side"],
                entry_price=row["entry_price"],
                stop_price=row["stop_price"],
                target_price=row["target_price"],
                quantity=row["quantity"],
                leverage=row["leverage"],
                risk_amount=row["risk_amount"],
                setup=row["setup"],
                tags=tuple(tag for tag in row["tags"].split(",") if tag),
                note=row["note"],
            )
            trade_id = self._save_trade(
                update.effective_user.id,
                draft,
                review_score=row["review_score"],
                ignored_warnings=True,
            )
            self.pending_trades.delete(update.effective_user.id, pending_id)
            await query.edit_message_text(f"Сделка #{trade_id} внесена с пометкой ignored_warnings=1.")

    async def check_alerts(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        rows = self.alerts.active()
        open_trades = self.trades.open_all()
        if not rows and not open_trades:
            return
        symbols = {row["symbol"] for row in rows}
        symbols.update(row["symbol"] for row in open_trades)
        try:
            prices = await self.market.get_prices(symbols)
        except Exception:
            logger.exception("Alert price check failed")
            return

        for trade in open_trades:
            try:
                candles = await self.market.get_klines(trade["symbol"], "1m", limit=3)
                self.trades.save_candles(int(trade["id"]), candles, "1m")
            except Exception:
                logger.info("Could not snapshot candles for trade %s", trade["id"], exc_info=True)

        for row in rows:
            price = prices.get(row["symbol"])
            if price is None:
                continue
            self.alerts.update_last_price(row["id"], price)
            triggered = row["direction"] == "above" and price >= row["target_price"]
            triggered = triggered or row["direction"] == "below" and price <= row["target_price"]
            if not triggered:
                continue
            self.alerts.mark_triggered(row["id"], price)
            sign = ">=" if row["direction"] == "above" else "<="
            try:
                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=f"Price alert #{row['id']}: {row['symbol']} {sign} {money(row['target_price'])}\nNow: {money(price)}",
                )
            except TelegramError:
                logger.info("Could not send price alert to user %s", row["user_id"])

        for trade in open_trades:
            price = prices.get(trade["symbol"])
            if price is None:
                continue
            entry_price = float(trade["entry_price"])
            market_distance = abs(price - entry_price) / entry_price * 100
            if market_distance > 25:
                logger.error(
                    "Auto-close blocked for trade %s: %s market price %s is %.1f%% from entry %s",
                    trade["id"], trade["symbol"], price, market_distance, entry_price,
                )
                continue
            close_reason = ""
            close_price = None
            if trade["side"] == "long":
                if price <= trade["stop_price"]:
                    close_reason = "stop_loss"
                    close_price = trade["stop_price"]
                elif trade["target_price"] is not None and price >= trade["target_price"]:
                    close_reason = "take_profit"
                    close_price = trade["target_price"]
            else:
                if price >= trade["stop_price"]:
                    close_reason = "stop_loss"
                    close_price = trade["stop_price"]
                elif trade["target_price"] is not None and price <= trade["target_price"]:
                    close_reason = "take_profit"
                    close_price = trade["target_price"]

            if close_price is None:
                continue
            closed = self.trades.close(
                trade["user_id"],
                trade["id"],
                close_price,
                note=f"auto close by {close_reason}; market price {money(price)}",
                close_reason=close_reason,
            )
            if not closed:
                continue
            result = "плюс" if float(closed["pnl"] or 0) >= 0 else "убыток"
            try:
                await context.bot.send_message(
                    chat_id=trade["user_id"],
                    text=(
                        f"Сделка #{trade['id']} {trade['symbol']} {trade['side'].upper()} закрылась: {format_close_reason_ru(close_reason)}.\n"
                        f"Открыта: {trade['opened_at']}\n"
                        f"Выход: {money(close_price)}\n"
                        f"PnL: {signed_money(closed['pnl'])} USDT ({result})"
                    ),
                )
            except TelegramError:
                logger.info("Could not send auto-close message to user %s", trade["user_id"])

    async def refresh_auto_contexts(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now()
        intervals = ["15m"]
        if now.hour % 4 == 0:
            intervals.append("1h")
        if now.hour == 10:
            intervals.append("1d")

        for user_id in self.users.list_user_ids():
            symbols = tuple(self.watchlist.list_symbols(user_id))
            if not symbols:
                continue
            lines = await self._update_auto_contexts_for_user(user_id, symbols, tuple(intervals))
            if lines:
                try:
                    await context.bot.send_message(chat_id=user_id, text="\n".join(lines[:12]))
                except TelegramError:
                    logger.info("Could not send auto-context update to user %s", user_id)

    async def _update_auto_contexts_for_user(self, user_id: int, symbols: tuple[str, ...], intervals: tuple[str, ...]) -> list[str]:
        interval_labels = {"1d": "1D", "1h": "1H", "15m": "15M"}
        lines = ["Auto context обновлен:"]
        for symbol in symbols:
            for interval in intervals:
                try:
                    klines = await self.market.get_klines(symbol, interval, limit=120)
                    analysis = analyze_klines(symbol, interval_labels[interval], klines)
                    context_id = self.contexts.create(
                        user_id=user_id,
                        symbol=symbol,
                        timeframe=str(analysis["timeframe"]),
                        bias=str(analysis["bias"]),
                        structure=str(analysis["structure"]),
                        levels=tuple(float(level) for level in analysis["levels"]),
                        note=str(analysis["note"]),
                        confidence=float(analysis["confidence"]),
                    )
                    levels = ",".join(f"{level:g}" for level in analysis["levels"])
                    lines.append(
                        f"#{context_id} {symbol} {analysis['timeframe']} {str(analysis['bias']).upper()} "
                        f"{analysis['structure']} levels={levels}"
                    )
                except Exception:
                    logger.exception("Auto context update failed for %s %s", symbol, interval)
                    lines.append(f"{symbol} {interval_labels[interval]}: не смог обновить")
        return lines

    def _parse_trade_args(self, args: list[str]) -> TradeDraft:
        if len(args) < 6:
            raise ValueError("слишком мало аргументов")
        symbol = normalize_symbol(args[0])
        side = args[1].lower()
        entry = parse_float(args[2])
        stop = parse_float(args[3])
        target = parse_optional_float(args[4])
        quantity = parse_float(args[5])
        leverage = 1.0
        note_start = 6
        if len(args) > 6 and looks_number(args[6]):
            leverage = parse_float(args[6])
            note_start = 7
        note = " ".join(args[note_start:])
        validate_trade_input(side, entry, stop, quantity, leverage)
        tags = tuple(token[1:].lower() for token in args[note_start:] if token.startswith("#") and len(token) > 1)
        setup = tags[0] if tags else ""
        risk_amount = abs(entry - stop) * quantity
        return TradeDraft(
            symbol=symbol,
            side=side,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            quantity=quantity,
            leverage=leverage,
            risk_amount=risk_amount,
            setup=setup,
            tags=tags,
            note=note,
        )

    async def _review_draft(self, user_id: int, draft: TradeDraft):
        defaults = self.users.get_defaults(user_id)
        account_size = float(defaults["default_account_size"] or 0)
        contexts = self.contexts.latest_for_symbol(user_id, draft.symbol)
        watchlist_symbols = self.watchlist.list_symbols(user_id)
        daily_plan = self.daily_plans.get(user_id, date.today())
        open_risk_total = self.trades.open_risk_total(user_id)
        today_pnl = self.trades.closed_pnl_for_date(user_id, date.today())
        sentiment = None
        current_price = None
        try:
            sentiment = await self.market.get_sentiment(draft.symbol)
        except Exception:
            logger.info("Sentiment unavailable for review", exc_info=True)
        try:
            current_price = await self.market.get_price(draft.symbol)
        except Exception:
            logger.info("Current price unavailable for review", exc_info=True)
        return review_trade(
            draft=draft,
            contexts=contexts,
            watchlist_symbols=watchlist_symbols,
            daily_plan=daily_plan,
            account_size=account_size,
            open_risk_total=open_risk_total,
            today_pnl=today_pnl,
            sentiment=sentiment,
            current_price=current_price,
        )

    def _save_trade(
        self,
        user_id: int,
        draft: TradeDraft,
        review_score: float | None = None,
        ignored_warnings: bool = False,
    ) -> int:
        return self.trades.create(
            user_id,
            draft.symbol,
            draft.side,
            draft.entry_price,
            draft.stop_price,
            draft.target_price,
            draft.quantity,
            draft.leverage,
            risk_amount=draft.risk_amount,
            setup=draft.setup,
            tags=draft.tags,
            review_score=review_score,
            ignored_warnings=ignored_warnings,
            note=draft.note,
        )

    def _main_markup(self, user_id: int) -> InlineKeyboardMarkup:
        miniapp_rows = self._miniapp_rows(user_id)
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Открыть сделку", callback_data="cmd:open_template")],
                [InlineKeyboardButton("Открытые сделки", callback_data="cmd:open_trades")],
                *miniapp_rows,
            ]
        )

    def _miniapp_markup(self, user_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(self._miniapp_rows(user_id))

    def _miniapp_rows(self, user_id: int) -> list[list[InlineKeyboardButton]]:
        url = f"{self.web_app_url.rstrip('/')}/"
        if url.startswith("https://"):
            return [[InlineKeyboardButton("Открыть Mini App", web_app=WebAppInfo(url=url))]]
        if re.match(r"http://(?:127\.0\.0\.1|localhost)(?=[:/])", url):
            phone_url = re.sub(r"http://(?:127\.0\.0\.1|localhost)", f"http://{local_lan_ip()}", url, count=1)
            return [
                [InlineKeyboardButton("Mini App на этом компьютере", url=url)],
                [InlineKeyboardButton("Mini App на телефоне", url=phone_url)],
            ]
        return [[InlineKeyboardButton("Открыть Mini App", url=url)]]

    def _parse_risk_args(self, user_id: int, args: list[str]):
        if len(args) < 5:
            raise RiskInputError("Not enough arguments.")

        symbol = normalize_symbol(args[0])
        side = args[1].lower()
        entry = parse_float(args[2])
        stop = parse_float(args[3])
        target = parse_optional_float(args[4])

        defaults = self.users.get_defaults(user_id)
        account_size = float(defaults["default_account_size"])
        risk_percent = float(defaults["default_risk_percent"])
        leverage = 1.0

        if len(args) >= 7:
            account_size = parse_float(args[5])
            risk_percent = parse_float(args[6])
        if len(args) >= 8:
            leverage = parse_float(args[7])
        if len(args) > 8:
            raise RiskInputError("Too many arguments.")

        return calculate_risk(symbol, side, entry, stop, account_size, risk_percent, target, leverage)

    def _create_journal_from_args(
        self,
        user_id: int,
        args: list[str],
        screenshot_file_id: str = "",
    ) -> int:
        if len(args) < 3:
            raise ValueError("Слишком мало аргументов.")
        symbol = normalize_symbol(args[0])
        outcome = args[1].lower()
        if outcome not in OUTCOMES:
            raise ValueError("Outcome должен быть win, loss, breakeven или idea.")
        description = " ".join(args[2:])
        return self.journal.create(
            user_id=user_id,
            symbol=symbol,
            outcome=outcome,
            description=description,
            screenshot_file_id=screenshot_file_id,
        )

    def _create_free_note(self, user_id: int, note: str, screenshot_file_id: str = "") -> int:
        return self.journal.create(
            user_id=user_id,
            symbol=guess_symbol(note),
            outcome="idea",
            description=note.strip() or "Заметка без описания",
            screenshot_file_id=screenshot_file_id,
        )

    def _create_context_from_args(
        self,
        user_id: int,
        args: list[str],
        screenshot_file_id: str = "",
    ) -> int:
        if len(args) < 3:
            raise ValueError("Нужно минимум: symbol timeframe bias.")
        symbol = normalize_symbol(args[0])
        timeframe = args[1].upper()
        bias = args[2].lower()
        if bias not in {"long", "short", "neutral"}:
            raise ValueError("Bias должен быть long, short или neutral.")

        structure = ""
        levels: list[float] = []
        invalidation = None
        confidence = 70.0
        note_parts: list[str] = []
        for token in args[3:]:
            if token.startswith("levels="):
                levels.extend(parse_levels_token(token.removeprefix("levels=")))
            elif token.startswith("level="):
                levels.extend(parse_levels_token(token.removeprefix("level=")))
            elif token.startswith("invalid="):
                invalidation = parse_float(token.removeprefix("invalid="))
            elif token.startswith("confidence="):
                confidence = parse_float(token.removeprefix("confidence="))
            elif token.startswith("structure="):
                structure = token.removeprefix("structure=")
            elif "," in token and all(looks_number(part) for part in token.split(",") if part):
                levels.extend(parse_levels_token(token))
            elif looks_number(token):
                levels.append(parse_float(token))
            else:
                note_parts.append(token)

        return self.contexts.create(
            user_id=user_id,
            symbol=symbol,
            timeframe=timeframe,
            bias=bias,
            structure=structure,
            levels=tuple(levels),
            invalidation_level=invalidation,
            note=" ".join(note_parts),
            screenshot_file_id=screenshot_file_id,
            confidence=confidence,
        )

    async def _handle_photo_note(self, user_id: int, caption: str, file_ids: list[str]) -> str:
        joined_file_ids = ",".join(file_ids)
        caption = caption.strip()
        if not caption:
            return (
                "Фото не сохранено: добавь подпись-команду и отправь его еще раз.\n\n"
                "/note BTC описание — запись в дневник\n"
                "/open SOL лонг вход 70 стоп 69 тейк 73 количество 1 — новая сделка\n"
                "/edit 12 стоп 69.5 тейк 74 — добавить фото или изменить сделку"
            )
        if caption.startswith("/edit"):
            return self._edit_trade_from_text(user_id, caption.partition(" ")[2].strip(), file_ids)
        if caption.startswith("/open"):
            raw_note = command_body(caption, "open") or "Фото без описания"
            return await self._handle_trade_note(user_id, raw_note, file_ids, require_trade=True)

        if caption.startswith("/note"):
            raw_note = caption.partition(" ")[2].strip() or "Фото без описания"
            entry_id = self._create_free_note(user_id, raw_note, screenshot_file_id=joined_file_ids)
            return f"Запись дневника #{entry_id} сохранена. Фото: {len(file_ids)}."

        if caption.startswith("/context"):
            raw_note = caption.partition(" ")[2].strip()
            if raw_note and not looks_strict_context_args(raw_note.split()):
                return await self._handle_trade_note(user_id, raw_note, file_ids)
            try:
                context_id = self._create_context_from_args(user_id, caption.split()[1:], screenshot_file_id=joined_file_ids)
            except ValueError as exc:
                return f"{exc}\nМожно проще: /context биткоин 65936 лонг стоп 65614 тейк 66731 причина входа"
            return f"Контекст #{context_id} сохранен. Фото в записи: {len(file_ids)}."

        if caption.startswith("/journal"):
            args = caption.split()[1:]
            try:
                entry_id = self._create_journal_from_args(user_id, args, screenshot_file_id=joined_file_ids)
            except ValueError as exc:
                return f"{exc}\nCaption формат: /journal BTC win описание"
            return f"Скриншоты и запись дневника #{entry_id} сохранены. Фото: {len(file_ids)}."

        return await self._handle_trade_note(user_id, caption or "Фото без описания", file_ids)

    def _edit_trade_from_text(self, user_id: int, text: str, file_ids: list[str]) -> str:
        match = re.match(r"\s*(\d+)\b(.*)", text, flags=re.DOTALL)
        if not match:
            return "Формат: /edit 10 стоп 64000 тейк 62000 количество 0.02 комментарий\nФото можно приложить к этому же сообщению."
        trade_id = int(match.group(1))
        body = match.group(2).strip()
        trade = self.trades.get(user_id, trade_id)
        if not trade:
            return "Не нашел сделку с таким ID."
        if trade["status"] != "open":
            return "Изменять уровни можно только у открытой сделки. Фото к закрытой сделке добавь через /note."

        entry = extract_price_after(body.lower(), ("вход", "entry")) or float(trade["entry_price"])
        stop = extract_price_after(body.lower(), ("стоп лосс", "стоплосс", "стоп", "sl")) or float(trade["stop_price"])
        target = extract_price_after(body.lower(), ("тейк профит", "тейкпрофит", "тейк", "профит", "tp"))
        target = target if target is not None else trade["target_price"]
        quantity = extract_price_after(body.lower(), ("количество", "qty", "объем", "обьем")) or float(trade["quantity"])
        timeframe_match = re.search(r"\b(1m|5m|15m|1h|4h|1d)\b", body.lower())
        timeframe = timeframe_match.group(1) if timeframe_match else str(trade["timeframe"] or "5m")
        try:
            validate_trade_input(str(trade["side"]), entry, stop, quantity, float(trade["leverage"]))
        except ValueError as exc:
            return f"Не изменил сделку: {exc}"
        updated = self.trades.update(user_id, trade_id, entry, stop, float(target) if target else None, quantity, timeframe, body)
        if not updated:
            return "Не удалось изменить сделку."
        for file_id in file_ids:
            self.trades.add_attachment(user_id, trade_id, telegram_file_id=file_id, caption=body)
        return (
            f"Сделка #{trade_id} обновлена.\n"
            f"Вход: {money(entry)} | Стоп: {money(stop)} | Тейк: {money(target) if target else '-'}\n"
            f"Количество: {quantity:g} | ТФ: {timeframe} | Фото добавлено: {len(file_ids)}"
        )

    async def _handle_trade_note(
        self,
        user_id: int,
        note: str,
        file_ids: list[str],
        require_trade: bool = False,
    ) -> str:
        joined_file_ids = ",".join(file_ids)
        note = note.strip()
        draft = parse_trade_caption(note)
        symbol = guess_symbol(note)
        if not draft:
            if require_trade:
                return (
                    "Сделку не открыл и в дневник не записал: не смог надежно разобрать сторону, вход, стоп и тейк.\n"
                    "Пример: /open солана 69,75 лонг стоп 69,55 тейк 70,55 количество 6 причина входа"
                )
            entry_id = self.journal.create(
                user_id=user_id,
                symbol=symbol,
                outcome="idea",
                description=note,
                screenshot_file_id=joined_file_ids,
            )
            photo_text = f" Фото: {len(file_ids)}." if file_ids else ""
            return f"Заметка дневника #{entry_id} сохранена.{photo_text}"

        if require_trade and draft.get("quantity") is None:
            return (
                "Сделку не открыл и в дневник не записал: укажи количество позиций.\n\n"
                + open_trade_template()
            )

        price = None
        try:
            price = await self.market.get_price(str(draft["symbol"]))
            draft["entry"] = draft.get("entry") or price
        except Exception:
            logger.info("Could not fetch current price for parsed trade note", exc_info=True)

        entry_id = None
        trade_id = None
        review_score = None
        context_id = None
        warning = ""
        if draft.get("entry") and draft.get("stop"):
            defaults = self.users.get_defaults(user_id)
            account_size = float(defaults["default_account_size"] or 0)
            risk_percent = float(defaults["default_risk_percent"] or 1)
            leverage = float(draft.get("leverage") or 1)
            quantity = draft.get("quantity")
            try:
                if price is None:
                    raise ValueError("не удалось получить реальную цену Binance Futures")
                market_distance_percent = abs(float(draft["entry"]) - price) / price * 100
                if market_distance_percent > 15:
                    raise ValueError(
                        f"цена входа {money(draft['entry'])} не похожа на текущую цену "
                        f"{draft['symbol']} {money(price)} (разница {market_distance_percent:.1f}%). "
                        "Проверь монету и цену"
                    )
                stop_distance_percent = abs(float(draft["entry"]) - float(draft["stop"])) / float(draft["entry"]) * 100
                if stop_distance_percent > 20:
                    raise ValueError(f"Стоп находится слишком далеко: {stop_distance_percent:.1f}% от входа. Похоже на опечатку")
                if quantity:
                    quantity = float(quantity)
                    validate_trade_input(
                        str(draft["side"]), float(draft["entry"]), float(draft["stop"]),
                        quantity, leverage, float(draft["target"]) if draft.get("target") else None,
                    )
                    trade_draft = TradeDraft(
                        symbol=normalize_symbol(str(draft["symbol"])),
                        side=str(draft["side"]),
                        entry_price=float(draft["entry"]),
                        stop_price=float(draft["stop"]),
                        target_price=float(draft["target"]) if draft.get("target") else None,
                        quantity=quantity,
                        leverage=leverage,
                        risk_amount=abs(float(draft["entry"]) - float(draft["stop"])) * quantity,
                        note=note,
                    )
                elif account_size > 0:
                    calc = calculate_risk(
                        str(draft["symbol"]),
                        str(draft["side"]),
                        float(draft["entry"]),
                        float(draft["stop"]),
                        account_size,
                        risk_percent,
                        draft.get("target"),
                        leverage,
                    )
                    trade_draft = TradeDraft(
                        symbol=calc.symbol,
                        side=calc.side,
                        entry_price=calc.entry_price,
                        stop_price=calc.stop_price,
                        target_price=calc.target_price,
                        quantity=calc.quantity,
                        leverage=calc.leverage,
                        risk_amount=calc.risk_amount,
                        note=note,
                    )
                else:
                    quantity = 1.0
                    validate_trade_input(
                        str(draft["side"]), float(draft["entry"]), float(draft["stop"]),
                        quantity, leverage, float(draft["target"]) if draft.get("target") else None,
                    )
                    trade_draft = TradeDraft(
                        symbol=normalize_symbol(str(draft["symbol"])),
                        side=str(draft["side"]),
                        entry_price=float(draft["entry"]),
                        stop_price=float(draft["stop"]),
                        target_price=float(draft["target"]) if draft.get("target") else None,
                        quantity=quantity,
                        leverage=leverage,
                        risk_amount=abs(float(draft["entry"]) - float(draft["stop"])) * quantity,
                        note=note,
                    )
                    warning = "Qty не указан, открыл как отслеживаемую сделку с условным qty 1. Для точного PnL задай /defaults или напиши qty."
                existing = self.trades.find_recent_open(
                    user_id,
                    trade_draft.symbol,
                    trade_draft.side,
                    trade_draft.entry_price,
                    trade_draft.stop_price,
                    trade_draft.target_price,
                )
                if existing:
                    for file_id in file_ids:
                        self.trades.add_attachment(user_id, int(existing["id"]), telegram_file_id=file_id, caption=note)
                    return (
                        f"Сделка #{existing['id']} уже открыта, дубль не создавал.\n"
                        f"{trade_draft.symbol} {trade_draft.side.upper()} | вход {money(trade_draft.entry_price)} | "
                        f"стоп {money(trade_draft.stop_price)} | тейк {money(trade_draft.target_price) if trade_draft.target_price else '-'}\n"
                        f"Новых фото добавлено: {len(file_ids)}"
                    )
                review = await self._review_draft(user_id, trade_draft)
                review_score = review.score
                trade_id = self._save_trade(user_id, trade_draft, review_score=review.score)
                self.trade_reviews.create(user_id, trade_draft.symbol, trade_draft.side, review, trade_id=trade_id)
            except ValueError as exc:
                logger.info("Rejected trade note: %s", exc)
                warning = f"Сделку не открыл: {exc}."
            except Exception:
                logger.info("Could not create trade from note", exc_info=True)
                warning = "Сделку не открыл: проверь вход/стоп/тейк."

        if require_trade and trade_id is None:
            return (
                f"{warning or 'Сделку не открыл: не хватает корректных данных.'}\n"
                "Дневник не изменен. Исправь уровни и отправь /open еще раз."
            )

        levels = tuple(float(value) for value in (draft.get("entry"), draft.get("stop"), draft.get("target")) if value)
        try:
            context_id = self.contexts.create(
                user_id=user_id,
                symbol=str(draft["symbol"]),
                timeframe="MANUAL",
                bias=str(draft["side"]),
                structure="trade-note",
                levels=levels,
                note=note,
                screenshot_file_id=joined_file_ids,
                confidence=70,
            )
        except Exception:
            logger.info("Could not create manual context from note", exc_info=True)

        entry_id = self.journal.create(
            user_id=user_id,
            symbol=str(draft["symbol"]),
            outcome="idea",
            theory="trade-plan",
            description=note,
            screenshot_file_id=joined_file_ids,
            linked_trade_id=trade_id,
        )
        lines = [
            "Сохранил.",
            f"Дневник #{entry_id}" + (f" | Сделка #{trade_id}" if trade_id else "") + (f" | Контекст #{context_id}" if context_id else ""),
            f"{draft['symbol']} {str(draft['side']).upper()}",
            f"Текущая: {money(price) if price else '-'}",
            f"Вход: {money(draft['entry']) if draft.get('entry') else '-'}",
            f"Стоп: {money(draft['stop']) if draft.get('stop') else '-'}",
            f"Тейк: {money(draft['target']) if draft.get('target') else '-'}",
        ]
        if review_score is not None:
            lines.append(f"Оценка сделки: {review_score:.0f}/100")
        if file_ids:
            lines.append(f"Фото в записи: {len(file_ids)}")
        if warning:
            lines.append(warning)
        return "\n".join(lines)

    async def _render_template_for_user(self, user_id: int, body: str, args: list[str]) -> str:
        raw_values = parse_key_values(args)
        values = base_values()

        trade_id = raw_values.pop("trade", raw_values.pop("trade_id", ""))
        if trade_id:
            if not trade_id.isdigit():
                raise ValueError("trade должен быть ID сделки, например trade=12.")
            trade = self.trades.get(user_id, int(trade_id))
            if not trade:
                raise ValueError("Не нашел сделку с таким ID.")
            from trading_bot.templates import trade_values

            values.update(trade_values(trade))

        values.update(raw_values)
        if "quantity" in values and "qty" not in values:
            values["qty"] = values["quantity"]
        if "qty" in values and "quantity" not in values:
            values["quantity"] = values["qty"]
        if "current_price" in values and "price" not in values:
            values["price"] = values["current_price"]

        symbol = str(values.get("symbol") or "").strip()
        if symbol:
            normalized_symbol = normalize_symbol(symbol)
            values["symbol"] = normalized_symbol
            if values.get("price") in {"", "-"}:
                try:
                    values["price"] = await self.market.get_price(normalized_symbol)
                except Exception:
                    logger.info("Template current price unavailable", exc_info=True)

        values = enrich_trade_math(values)
        return render_template(body, values)


def parse_float(value: str) -> float:
    return float(value.replace(",", "."))


def command_body(text: str, command: str) -> str:
    return re.sub(rf"^/{re.escape(command)}(?:@\w+)?\s*", "", text.strip(), count=1, flags=re.IGNORECASE).strip()


def open_trade_template() -> str:
    return (
        "/open\n"
        "Монета: \n"
        "Сторона: \n"
        "Цена входа: \n"
        "Стоп: \n"
        "Тейк: \n"
        "Количество позиций: \n"
        "Плечо: 1\n"
        "Причина входа: "
    )


def local_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def guess_symbol(text: str) -> str:
    value = text.lower().replace("ё", "е")
    explicit = re.search(r"(?im)^\s*монета\s*:\s*([^\n\r]+)", value)
    if explicit:
        candidate = explicit.group(1).strip().split()[0].strip(".,;:()[]")
        return normalize_symbol(candidate)

    aliases = {
        "BTCUSDT": ("биткоин", "биток", "биточек", "bitcoin", "btc"),
        "ETHUSDT": ("эфириум", "ефириум", "эфир", "ефир", "ethereum", "eth"),
        "SOLUSDT": ("солана", "солянка", "солик", "соль", "sol"),
    }
    matches = [
        (match.start(), symbol)
        for symbol, words in aliases.items()
        for word in words
        if (match := re.search(rf"(?<![a-zа-я0-9]){re.escape(word)}(?![a-zа-я0-9])", value))
    ]
    if matches:
        return min(matches)[1]

    match = re.search(r"\b(?!open\b|trade\b|long\b|short\b)([a-zA-Z]{2,12})(?:usdt)?\b", text, re.IGNORECASE)
    if match:
        return normalize_symbol(match.group(1))
    return ""


def parse_trade_caption(text: str) -> dict[str, object] | None:
    value = text.lower()
    # The first direction in the trade header is authoritative. The rationale
    # may legitimately mention an opposite future scenario.
    header = re.split(r"\b(?:причина(?:\s+входа)?|почему\s+вош[её]л|описание)\b", value, maxsplit=1)[0]
    explicit_side = re.search(r"(?im)^\s*сторона\s*:\s*(лонг|long|шорт|short)\b", value)
    side_match = explicit_side or re.search(r"\b(лонг|long|шорт|short)\b", header)
    if not side_match:
        side_match = re.search(r"\b(лонг|long|шорт|short)\b", value)
    side = ""
    if side_match:
        side = "long" if side_match.group(1) in {"лонг", "long"} else "short"
    symbol = guess_symbol(text)
    stop = extract_price_after(value, ("стоп лосс", "стоплосс", "стоп", "sl"))
    target = extract_price_after(value, ("тейк профит", "тейкпрофит", "тейкт профит", "тейк", "профит", "tp"))
    entry = extract_price_after(value, ("вход", "entry", "открыл по", "цена входа"))
    leverage = extract_leverage(value)
    quantity = extract_price_after(value, ("qty", "количество", "объем", "обьем", "размер позиции"))
    if entry is None:
        entry = extract_first_trade_price(value, stop, target)
    if not side or not symbol or (stop is None and target is None):
        return None
    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "leverage": leverage or 1,
        "quantity": quantity,
    }


def extract_leverage(text: str) -> float | None:
    match = re.search(r"(?:плечо|leverage)\s*:\s*(\d+(?:[,.]\d+)?)\s*(?:[:%]\s*1|[xх])?", text)
    if match:
        return parse_float(match.group(1))
    return extract_price_after(text, ("плечо", "leverage"))


def extract_price_after(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        pattern = rf"{re.escape(label)}[^\d]{{0,24}}(\d+(?:[,.]\d+)?)"
        match = re.search(pattern, text)
        if match:
            try:
                return parse_float(match.group(1))
            except ValueError:
                return None
    return None


def extract_first_trade_price(text: str, stop: float | None, target: float | None) -> float | None:
    ignored = {round(value, 8) for value in (stop, target) if value is not None}
    for match in re.finditer(r"(?<!\d)(\d+(?:[,.]\d+)?)(?!\d)", text):
        try:
            value = parse_float(match.group(1))
        except ValueError:
            continue
        if round(value, 8) not in ignored:
            return value
    return None


def looks_strict_context_args(args: list[str]) -> bool:
    if len(args) < 3:
        return False
    return args[2].lower() in {"long", "short", "neutral"} and bool(re.search(r"\d", args[1]))


def format_close_reason_ru(reason: str) -> str:
    return {
        "stop_loss": "стоп лосс",
        "take_profit": "тейк профит",
        "manual": "ручное закрытие",
    }.get(reason, reason.replace("_", " "))


def command_number(value: object, fallback: str) -> str:
    if value in {None, ""}:
        return fallback
    try:
        return f"{float(value):.10g}"
    except (TypeError, ValueError):
        return fallback


def parse_optional_float(value: str) -> float | None:
    if value in {"-", "none", "None", "null"}:
        return None
    return parse_float(value)


def parse_levels_token(value: str) -> list[float]:
    return [parse_float(part) for part in value.replace(";", ",").split(",") if part]


def looks_number(value: str) -> bool:
    try:
        parse_float(value)
    except ValueError:
        return False
    return True


def validate_trade_input(
    side: str,
    entry: float,
    stop: float,
    quantity: float,
    leverage: float,
    target: float | None = None,
) -> None:
    if side not in {"long", "short"}:
        raise ValueError("side должен быть long или short")
    if entry <= 0 or stop <= 0 or quantity <= 0 or leverage <= 0:
        raise ValueError("цены, qty и leverage должны быть больше нуля")
    if side == "long" and stop >= entry:
        raise ValueError("для long стоп должен быть ниже входа")
    if side == "short" and stop <= entry:
        raise ValueError("для short стоп должен быть выше входа")
    if target is not None and side == "long" and target <= entry:
        raise ValueError("для long тейк должен быть выше входа")
    if target is not None and side == "short" and target >= entry:
        raise ValueError("для short тейк должен быть ниже входа")


def parse_alert_args(args: list[str]) -> tuple[str, str, float]:
    if len(args) != 3:
        raise ValueError("Нужно 3 аргумента.")

    symbol = normalize_symbol(args[0])
    if args[1] in {">=", ">", "above"}:
        return symbol, "above", parse_float(args[2])
    if args[1] in {"<=", "<", "below"}:
        return symbol, "below", parse_float(args[2])

    if args[2] in {">=", ">", "above"}:
        return symbol, "above", parse_float(args[1])
    if args[2] in {"<=", "<", "below"}:
        return symbol, "below", parse_float(args[1])

    raise ValueError("Условие должно быть >=, <=, above или below.")
