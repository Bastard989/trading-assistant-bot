# Telegram trading assistant bot

Бот для личного трейдинг-дневника, риск-менеджмента и ценовых уведомлений.

Он не дает гарантированных сигналов и не обещает прибыль. Его задача — помогать считать риск, фиксировать сделки, собирать статистику и быстро смотреть активные монеты.

## Что умеет

- `/defaults account risk%` — сохранить депозит и риск по умолчанию.
- `/price BTC` — получить текущую цену.
- `/top` — топ USDT-монет по смеси ликвидности и волатильности за 24 часа.
- `/sentiment BTC` — long/short ratio по Binance Futures.
- `/risk BTC long entry stop target account risk% leverage` — рассчитать позицию от риска.
- `/trade BTC long entry stop target qty leverage заметка` — сохранить открытую сделку.
- `/close trade_id exit_price fees заметка` — закрыть сделку и посчитать PnL.
- `/trades [open|closed|cancelled]` — список сделок.
- `/stats` — статистика прибыли/убытков, winrate и разрез по монетам.
- `/alert BTC >= 65000` — уведомление, когда цена достигнет уровня.
- `/alerts` — список активных алертов.
- `/journal BTC win описание` — запись в дневник трейдера.
- `/entries [BTC]` — последние записи дневника.
- Фото сделки можно отправить с подписью: `/journal BTC loss описание`.

## Быстрый запуск

1. Создай бота у [@BotFather](https://t.me/BotFather) и получи токен.
2. Скопируй настройки:

```bash
cp .env.example .env
```

3. Впиши токен в `.env`.
4. Установи зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

5. Запусти:

```bash
python -m trading_bot.main
```

## Настройки

```env
TELEGRAM_BOT_TOKEN=put_your_bot_token_here
DATABASE_PATH=data/trading_bot.sqlite3
MARKET=futures
TOP_LIMIT=10
ALERT_POLL_SECONDS=30
```

- `MARKET=futures` использует Binance Futures public API.
- `MARKET=spot` использует Binance Spot public API, но `/sentiment` доступен только для futures.
- `ALERT_POLL_SECONDS` задает частоту проверки цен.

## Примеры

Сохранить депозит 1000 USDT и риск 1%:

```text
/defaults 1000 1
```

Рассчитать позицию:

```text
/risk BTC long 65000 64000 68000 1000 1 5
```

Сохранить сделку:

```text
/trade BTC long 65000 64000 68000 0.01 5 пробой уровня
```

Закрыть сделку:

```text
/close 1 67200 1.5 вышел у сопротивления
```

Поставить алерт:

```text
/alert ETH <= 3000
```

## Данные

Все личные данные хранятся в SQLite:

- `users` — настройки пользователя.
- `alerts` — ценовые уведомления.
- `trades` — сделки и PnL.
- `journal_entries` — дневник, описания и Telegram `file_id` скриншотов.

Потом из этой базы можно сделать экспорт, аналитику по паттернам, учебник по удачным/неудачным сделкам или подключить отдельного агента для разбора истории.
