# Architecture

Trading Assistant is a single-host personal application with two processes:

- `trading_bot.main`: Telegram polling and background monitoring.
- `trading_bot.web_app`: authenticated Mini App API bound to loopback.

Both use one SQLite database. Every connection enables foreign keys, WAL, and a five-second busy timeout. Mutations use owner-scoped SQL and durable idempotency keys. Trade validation and Decimal calculations live under `trading_bot/domain`; transport-specific parsing remains in Telegram/FastAPI until the remaining service-layer extraction is complete.

External market data comes from a reusable Binance client with bounded retries, cache, and circuit breaker. Public market observations are estimates and do not prove exchange execution.

User identity is derived only from Telegram-signed `initData`. `ALLOWED_TELEGRAM_USER_IDS` limits personal-mode access in both transports.

