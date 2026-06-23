# Security policy

Trading Assistant is a personal-data application. Do not publish its API before Telegram Mini App authentication, HTTPS, and the owner allowlist are configured.

## Required production settings

- Rotate any Telegram token that has appeared in chat or logs.
- Set `APP_ENV=production` and keep `ENABLE_DEV_AUTH=false`.
- Set `ALLOWED_TELEGRAM_USER_IDS` explicitly.
- Keep the API on `127.0.0.1` behind an HTTPS reverse proxy.
- Store `.env`, SQLite databases, media, logs, and backups outside the checkout with mode `0600` and parent directories mode `0700`.
- Never log the bot token, Authorization header, raw `initData`, journal text, or attachment contents.
- `OPENAI_API_KEY` is optional and must stay secret. If screenshot recognition is enabled, screenshots sent to `/open` are transmitted to the configured OpenAI Responses API model for extraction; do not enable it unless this data flow is acceptable for the owner.
- Obsidian exports must be generated only after authenticated owner checks and must not include `.env`, Telegram tokens, raw `initData`, idempotency keys, or unrelated users' records.

Report a vulnerability privately to the repository owner. Do not include live secrets or personal trading records in the report.
