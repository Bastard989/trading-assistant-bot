# GitHub profile card

Use this file when publishing or pinning the repository on a GitHub profile.

## Repository name

`trading-assistant`

Alternative:

`telegram-trading-assistant`

## Short description

Telegram bot and Mini App for crypto trade journaling, risk control, live market context, session analytics, screenshot workflows, and Obsidian exports.

## Pinned repository text

Full-stack trading journal built with Python, FastAPI, Telegram Bot API, SQLite, and a custom Mini App UI. Tracks trade plans, screenshots, risk, live PnL, sessions, journal notes, and exports an Obsidian vault for post-trade analysis.

## Topics

```text
telegram-bot
telegram-mini-app
fastapi
python
sqlite
trading-journal
crypto
risk-management
obsidian
fintech
market-data
security
portfolio-project
```

## Social preview idea

Dark trading cockpit UI with:

- `Trading Assistant` title
- live BTC/ETH/SOL cards
- open trade panel
- Obsidian export badge
- Telegram Mini App badge

Avoid screenshots with real journal text, real Telegram IDs, real PnL if privacy matters. Use demo data only.

## Public release checklist

- [ ] Rotate any Telegram token that appeared in chat, terminal, screenshots, or logs.
- [ ] Confirm `.env` is not tracked: `git ls-files .env`.
- [ ] Confirm databases are not tracked: `git ls-files '*.sqlite3' '*.db'`.
- [ ] Run secret scan: `gitleaks detect --source .`.
- [ ] Run tests: `ruff check .`, `node --check mini_app/app.js`, `pytest -q`.
- [ ] Replace private screenshots with demo screenshots.
- [ ] Set GitHub repository description from this file.
- [ ] Add topics from this file.
- [ ] Pin the repo on the GitHub profile.
- [ ] Keep production database, screenshots, backups, logs, and media outside the repository.

## Suggested GitHub “About” sidebar

Description:

> Telegram bot + Mini App for trade journaling, risk control, market context, session analytics, and Obsidian exports.

Website:

> leave empty until a public demo/video exists

Topics:

> use the topics listed above
