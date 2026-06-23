# Obsidian export design

Цель интеграции — дать трейдеру переносимый vault, который можно скачать из Trading Assistant и открыть в Obsidian без ручной раскладки данных.

## Формат

- Основной формат: Markdown-файлы с YAML properties в начале заметки.
- Внутренние связи: `[[...]]`-ссылки между сессиями, сделками, дневником и дневными обзорами.
- Визуальные карты: `.canvas` файлы по JSON Canvas для связей `сессия → сделки → дневник → скриншоты`.
- Вложения: отдельная папка `Attachments/` для экспортированных изображений, если доступны локальные файлы или Telegram file_id уже был скачан.

Такой формат читается самим Obsidian и остаётся полезным даже без community-плагинов. Dataview/дашборды можно подключить поверх properties.

## Структура vault

```text
Trading Assistant Export/
  README.md
  Dashboard.md
  Sessions/
    2026-06-23 SOL разгон 100-150.md
  Trades/
    Trade 12 SOLUSDT long.md
  Journal/
    2026-06-23 Journal 55.md
  Daily/
    2026-06-23.md
  Coins/
    SOLUSDT.md
    BTCUSDT.md
  Canvas/
    Session 7.canvas
  Attachments/
```

## Properties

### Session note

```yaml
---
type: trading-session
session_id: 7
status: active
started_at: 2026-06-23T13:10:00
start_deposit: 100
target_deposit: 150
pnl: -10
tags:
  - trading/session
---
```

### Trade note

```yaml
---
type: trade
trade_id: 12
symbol: SOLUSDT
side: long
status: open
entry: 70.9
stop: 69.8
target: 73
quantity: 1.4
leverage: 1
session: "[[2026-06-23 SOL разгон 100-150]]"
tags:
  - trading/trade
  - coin/SOLUSDT
---
```

### Journal note

```yaml
---
type: journal-entry
journal_id: 55
symbol: SOLUSDT
outcome: idea
linked_trade: "[[Trade 12 SOLUSDT long]]"
created_at: 2026-06-23T13:15:00
tags:
  - trading/journal
---
```

## UX

1. Пользователь выбирает экспорт в Mini App: все данные, конкретную сессию, диапазон дат или одну монету.
2. Backend формирует zip-архив vault через `GET /api/export/obsidian.zip`.
3. Пользователь распаковывает архив в папку Obsidian или импортирует как новый vault.
4. Внутри уже есть `Dashboard.md`, daily notes, ссылки и canvas-карта.

## Безопасность и приватность

- Экспорт создаётся только для аутентифицированного Telegram-пользователя.
- Нельзя экспортировать чужие сделки, сессии, journal entries или attachment references.
- В архив не попадают секреты `.env`, Telegram token, initData или служебные idempotency keys.
- Имена файлов нужно санитизировать: запрещены `..`, `/`, управляющие символы и слишком длинные сегменты.

## Минимальный первый релиз

- Экспорт всех сделок, сессий и дневника текущего пользователя в Markdown.
- Один `Dashboard.md` с простыми ссылками и сводкой.
- `.canvas` карта связей.
- API endpoint `GET /api/export/obsidian.zip`.
- Кнопки скачивания в Mini App: весь vault или активная сессия.

## Расширения

- Dataview snippets для таблиц PnL, winrate, ошибок и сетапов.
- Экспорт только выбранной сессии или монеты.
- Автоматические notes по дням: `Daily/YYYY-MM-DD.md`.
- Вложение скриншотов, если они доступны локально.
