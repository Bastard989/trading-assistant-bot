# Verification baseline — 2026-06-20

## Confirmed without changing production

- Source checkout: `/Users/kirill/Desktop/трейдинг /trading-assistant-bot`.
- Runtime checkout: `/Users/kirill/Documents/фриланс`.
- Source baseline: `main` at `01e0def`, clean and equal to `origin/main` before hardening.
- Working branch: `production-hardening`.
- Two Python processes have the runtime checkout as cwd.
- API PID 68575 listens on `*:8080`; this remains an unresolved production exposure until cutover.
- Code copies match by checksum except README and source-only development directories; runtime was not modified.
- Source and runtime `.env`, live DB, and existing backup were mode `0644`; directories were `0755`.
- Existing backup passed `PRAGMA integrity_check` and `foreign_key_check` and contains 17 trades.
- Existing backup counts: users 2, trades 17, journal entries 18, sessions 2, candles 2612, attachments 4, watchlist 6.
- Restore from the existing backup into a temporary directory passed integrity check.
- New schema migration ran twice on a temporary copy of that backup, passed integrity/FK checks, and created six required indexes.

## Production checks still pending explicit access/action

- A fresh online backup of the active SQLite database could not be completed inside the filesystem sandbox. The source DB was opened read-only and was not changed.
- Fresh live counts, open-trade snapshot, and live `foreign_key_check` remain to be captured from that online backup.
- No production process was stopped, rebound, restarted, or replaced.
- No live migration, chmod, token rotation, cutover, firewall, DNS, or HTTPS change was performed.

An unsuccessful sandboxed backup attempt left a zero-byte file named `trading_bot.phase0_20260620.sqlite3`. It is not a valid backup and must never be used for restore.
