# Multi-asset market plan

Цель — расширить Trading Assistant за пределы Binance crypto futures и дать пользователю выбор инструментов как в TradingView: крипта, акции, фонды/ETF, индексы, форекс, сырьё и фьючерсы.

## Почему нужен отдельный слой

Сейчас backend получает рынок через Binance endpoints. Этого хватает для `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, но не хватает для:

- stocks: `AAPL`, `NVDA`, `TSLA`;
- ETF/funds: `SPY`, `QQQ`, `SOXL`;
- indices: `SPX`, `NDX`, `DXY`;
- forex: `EURUSD`, `GBPUSD`;
- commodities/futures: `GC`, `CL`, `ES`, `NQ`;
- metals/crypto-like symbols: `XAUUSD`, `XAGUSD`.

Поэтому нельзя просто добавить кнопки категорий в UI: нужен provider router.

## Модель данных

```text
instrument
  symbol: SOLUSDT | AAPL | SPY | DXY
  display_symbol: SOL/USDT | AAPL | SPY | DXY
  asset_class: crypto | stock | etf | index | forex | commodity | futures
  exchange: Binance Futures | NASDAQ | NYSE | CME | FX
  provider: binance | polygon | twelvedata | alpaca | yahoo | tradingview-compatible
  quote_currency: USDT | USD | EUR
```

## API

```text
GET /api/instruments/search?q=sol&asset_class=crypto
GET /api/market/top?asset_class=crypto|stock|etf|index|forex|futures
GET /api/prices?symbols=SOLUSDT,AAPL,DXY
GET /api/klines?symbol=AAPL&asset_class=stock&interval=1h&limit=120
```

## UI

Раздел `Монеты` нужно переименовать в `Инструменты`.

Категории:

- Все
- Криптовалюты
- Акции
- Фонды / ETF
- Фьючерсы
- Форекс
- Индексы
- Облигации / ставки
- Экономика

Карточка инструмента должна показывать источник данных, потому что задержки и доступность у разных рынков разные.

## Первый безопасный релиз

1. Оставить crypto на Binance как сейчас.
2. Добавить UI-категории и search schema без фейковых цен.
3. Добавить provider interface:
   - `get_price`
   - `get_tickers`
   - `get_klines`
   - `search_instruments`
4. Подключить один внешний provider для stocks/ETF/indices/forex.
5. В сделках хранить `asset_class` и `exchange`, чтобы `SOLUSDT` и условный `SOL`-stock не конфликтовали.

## Важное ограничение

Пока provider для не-крипты не подключён, UI не должен показывать выдуманные цены. Если данных нет — показывать “нужен provider” и инструкцию подключения.
