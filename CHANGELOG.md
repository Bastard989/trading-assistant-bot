# Changelog

## Unreleased

- Added Telegram Mini App signature verification and personal owner allowlist.
- Removed URL user identity and protected all attachment/media reads.
- Added safe image decode/re-encode, CSP, rate limits, and stable external-error mapping.
- Added atomic trade transitions and durable HTTP/Telegram idempotency.
- Added transactional checksum migrations, indexes, FK enforcement, backup/restore tooling, health endpoints, tests, and CI.
- Replaced probability language with an explicitly heuristic rule score.
- Replaced public-price auto-close with deduplicated, retryable level observations requiring manual execution confirmation.
- Added UTC-aware business timezone boundaries for plans and daily PnL.
- Added a shared trade service for API/Telegram create, update, and close flows with Decimal validation/PnL before legacy REAL persistence.
- Added Pydantic JSON bodies for core mutation endpoints and moved Mini App session/watchlist/trade mutations away from URL payloads.
- Added cross-user mutation regression tests for trade/session/journal ownership boundaries.
- Hardened remaining Mini App HTML templates for sessions, trades, journal results, media URLs, and generated IDs/status values.
- Added optional Telegram `/open` screenshot-to-trade draft recognition with clarification flow before opening.
- Documented the Obsidian vault export architecture for sessions, trades, journal notes, dashboards, and canvas maps.
