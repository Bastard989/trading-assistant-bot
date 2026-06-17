from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from trading_bot.formatting import (
    format_risk,
    format_sentiment,
    format_ticker,
    format_trade,
    money,
    signed_money,
)
from trading_bot.market import MarketClient, normalize_symbol
from trading_bot.repositories import AlertRepository, JournalRepository, TradeRepository, UserRepository
from trading_bot.risk import RiskInputError, calculate_risk

logger = logging.getLogger(__name__)

AWAITING_PROFILE = "awaiting_profile"
OUTCOMES = {"win", "loss", "breakeven", "idea"}


class BotHandlers:
    def __init__(
        self,
        users: UserRepository,
        alerts: AlertRepository,
        trades: TradeRepository,
        journal: JournalRepository,
        market: MarketClient,
        top_limit: int,
        alert_poll_seconds: int,
    ) -> None:
        self.users = users
        self.alerts = alerts
        self.trades = trades
        self.journal = journal
        self.market = market
        self.top_limit = top_limit
        self.alert_poll_seconds = alert_poll_seconds

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(CommandHandler("profile", self.profile))
        application.add_handler(CommandHandler("defaults", self.defaults))
        application.add_handler(CommandHandler("price", self.price))
        application.add_handler(CommandHandler("top", self.top))
        application.add_handler(CommandHandler("sentiment", self.sentiment))
        application.add_handler(CommandHandler("risk", self.risk))
        application.add_handler(CommandHandler("trade", self.trade))
        application.add_handler(CommandHandler("close", self.close_trade))
        application.add_handler(CommandHandler("canceltrade", self.cancel_trade))
        application.add_handler(CommandHandler("trades", self.trades_list))
        application.add_handler(CommandHandler("stats", self.stats))
        application.add_handler(CommandHandler("alert", self.alert))
        application.add_handler(CommandHandler("alerts", self.alerts_list))
        application.add_handler(CommandHandler("journal", self.journal_entry))
        application.add_handler(CommandHandler("entries", self.entries))
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

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.users.ensure_user(update.effective_user.id)
        await update.message.reply_text(
            "Я твой торговый бот: считаю риск, веду сделки, дневник и ценовые алерты.\n\n"
            "Сначала задай размер депозита и риск: /defaults 1000 1\n"
            "Потом можешь считать: /risk BTC long 65000 64000 68000 1000 1 5"
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "/defaults account risk% - сохранить депозит и риск\n"
            "/profile - сохранить правила своей торговли\n"
            "/price BTC - цена монеты\n"
            "/top - топ активных USDT-монет по ликвидности и волатильности\n"
            "/sentiment BTC - long/short настроение рынка\n"
            "/risk BTC long entry stop target account risk% leverage - расчет позиции\n"
            "/trade BTC long entry stop target qty leverage заметка - сохранить сделку\n"
            "/close trade_id exit_price fees заметка - закрыть сделку\n"
            "/trades [open|closed] - список сделок\n"
            "/stats - статистика по сделкам и монетам\n"
            "/alert BTC >= 65000 - пуш при достижении цены\n"
            "/alerts - активные алерты\n"
            "/journal BTC win описание - запись в дневник\n"
            "/entries [BTC] - последние записи дневника\n\n"
            "Скриншот сделки можно отправить фото с caption: /journal BTC loss описание"
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

    async def trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 6:
            await update.message.reply_text("Формат: /trade BTC long 65000 64000 68000 0.01 5 заметка")
            return
        try:
            symbol = normalize_symbol(context.args[0])
            side = context.args[1].lower()
            entry = parse_float(context.args[2])
            stop = parse_float(context.args[3])
            target = parse_optional_float(context.args[4])
            quantity = parse_float(context.args[5])
            leverage = 1.0
            note_start = 6
            if len(context.args) > 6 and looks_number(context.args[6]):
                leverage = parse_float(context.args[6])
                note_start = 7
            note = " ".join(context.args[note_start:])
            validate_trade_input(side, entry, stop, quantity, leverage)
        except ValueError as exc:
            await update.message.reply_text(f"Ошибка в параметрах сделки: {exc}")
            return

        trade_id = self.trades.create(
            update.effective_user.id,
            symbol,
            side,
            entry,
            stop,
            target,
            quantity,
            leverage,
            note,
        )
        risk_amount = abs(entry - stop) * quantity
        target_text = "-" if target is None else money(target)
        await update.message.reply_text(
            f"Сделка #{trade_id} сохранена.\n"
            f"{symbol} {side.upper()} entry {money(entry)} stop {money(stop)} target {target_text}\n"
            f"Qty {money(quantity)} | risk at stop {money(risk_amount)} USDT"
        )

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

        trade = self.trades.close(update.effective_user.id, trade_id, exit_price, fees, note)
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

    async def trades_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        status = context.args[0].lower() if context.args else None
        if status and status not in {"open", "closed", "cancelled"}:
            await update.message.reply_text("Формат: /trades или /trades open|closed|cancelled")
            return
        rows = self.trades.list_for_user(update.effective_user.id, status=status)
        if not rows:
            await update.message.reply_text("Сделок пока нет.")
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

    async def on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        caption = update.message.caption or ""
        if not caption.startswith("/journal"):
            await update.message.reply_text("Фото получил. Чтобы сохранить в дневник, добавь caption: /journal BTC win описание")
            return
        args = caption.split()[1:]
        file_id = update.message.photo[-1].file_id
        try:
            entry_id = self._create_journal_from_args(update.effective_user.id, args, screenshot_file_id=file_id)
        except ValueError as exc:
            await update.message.reply_text(f"{exc}\nCaption формат: /journal BTC win описание")
            return
        await update.message.reply_text(f"Скриншот и запись дневника #{entry_id} сохранены.")

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

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        action, raw_id = query.data.split(":", 1)
        if action == "cancel_alert":
            ok = self.alerts.cancel(update.effective_user.id, int(raw_id))
            await query.edit_message_text("Алерт отменен." if ok else "Не нашел активный алерт.")

    async def check_alerts(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        rows = self.alerts.active()
        if not rows:
            return
        symbols = {row["symbol"] for row in rows}
        try:
            prices = await self.market.get_prices(symbols)
        except Exception:
            logger.exception("Alert price check failed")
            return

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
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"Price alert #{row['id']}: {row['symbol']} {sign} {money(row['target_price'])}\nNow: {money(price)}",
            )

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


def parse_float(value: str) -> float:
    return float(value.replace(",", "."))


def parse_optional_float(value: str) -> float | None:
    if value in {"-", "none", "None", "null"}:
        return None
    return parse_float(value)


def looks_number(value: str) -> bool:
    try:
        parse_float(value)
    except ValueError:
        return False
    return True


def validate_trade_input(side: str, entry: float, stop: float, quantity: float, leverage: float) -> None:
    if side not in {"long", "short"}:
        raise ValueError("side должен быть long или short")
    if entry <= 0 or stop <= 0 or quantity <= 0 or leverage <= 0:
        raise ValueError("цены, qty и leverage должны быть больше нуля")
    if side == "long" and stop >= entry:
        raise ValueError("для long стоп должен быть ниже входа")
    if side == "short" and stop <= entry:
        raise ValueError("для short стоп должен быть выше входа")


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
