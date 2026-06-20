# Data model

All user-owned records carry `user_id`; repository reads and mutations include it in the predicate. Terminal trade states are `closed` and `cancelled`; only `open → terminal` is allowed.

Important infrastructure tables:

- `schema_migrations`: applied version and immutable checksum.
- `idempotency_keys`: user/scope/key reservation and replay response.
- `trade_attachments`: owner, trade, Telegram file ID or private local path.
- `trade_level_observations`: deduplicated public-market level observations and retryable notification state; it never changes trade terminal state.

Current monetary columns inherited from the prototype are SQLite `REAL`. New domain calculations use `Decimal`, but conversion of all persisted money to normalized text/fixed point remains a pre-production migration item.

Timestamps are stored in UTC-compatible SQLite text. User business-timezone boundaries remain a pending migration/service task.
