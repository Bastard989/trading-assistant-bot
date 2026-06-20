# Contributing

- Work on a branch and keep one logical behavior plus its regression test per commit.
- Never run writing tests against the live database or send real Telegram messages.
- Use temporary SQLite databases and online backup for active databases.
- Run `ruff check .`, `node --check mini_app/app.js`, and `pytest -q` before committing.
- Do not commit `.env`, DB/media/backups/logs, raw `initData`, auth headers, or personal journal content.
- Production migration/cutover, token rotation, service/firewall/DNS changes, and live data changes require explicit owner approval.

