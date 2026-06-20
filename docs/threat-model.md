# Threat model

Protected assets: Telegram identity, trade/journal records, screenshots, bot token, SQLite database, and backups.

Primary threats and controls:

- Identity spoofing/IDOR: HMAC-verified Telegram `initData`, server-derived user ID, allowlist, owner-scoped repositories.
- Replay/double submit: durable HTTP idempotency keys and Telegram update IDs; atomic terminal transitions.
- Stored XSS: escaped user fields, event delegation, CSP without inline script/style.
- Malicious uploads: streaming size limit, pixel/dimension limit, decode and JPEG re-encode, quota, private storage and owner checks.
- Abuse/DoS: request body limits, per-user API rate limits, Binance timeout/retry/cache/circuit breaker.
- Secret/data disclosure: no secrets in Git, loopback API, HTTPS proxy, restrictive filesystem modes.

Residual risks: legacy REAL money columns, incomplete timezone policy, monitor execution semantics, lack of completed browser automation, and token rotation requiring the owner.

