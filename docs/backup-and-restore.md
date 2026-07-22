# Backup and restore

Never copy an active SQLite file with ordinary `cp`. Use the online backup API:

```bash
.venv/bin/python scripts/backup_daily.py \
  --source /var/lib/trading-assistant/trading_bot.sqlite3 \
  --directory /var/lib/trading-assistant/backups
```

The command writes mode `0600`, runs `integrity_check`, and creates a SHA-256 sidecar. Encrypt and copy backups off-host; the supplied timer only creates the verified local copy.

Restore drill:

```bash
sqlite3 /tmp/restore-test.sqlite3 ".restore '/var/lib/trading-assistant/backups/BACKUP.sqlite3'"
sqlite3 -readonly /tmp/restore-test.sqlite3 "PRAGMA integrity_check; PRAGMA foreign_key_check;"
```

The 2026-07-21 pre-Bybit cutover backup was restored to a separate `/tmp` database: integrity returned `ok`, foreign-key check returned no rows, and the restored counts were users 1, trades 0, journal entries 0, Crisis Radar observations 25,057, and backtest runs 2. After the successful Bybit history/derived-catalog migration, a second verified online backup was created with SHA-256 `a481b2bd4129e0ac96029b38d4d9a515e8b9244aca4d6b487c7aaf90f9b8596a`.

Before production restore, stop both services, preserve the current DB with another online backup, restore to a new path, verify counts/open trades, then atomically change `DATABASE_PATH`. Do not overwrite the only copy.

Target policy: 7 daily and 4 weekly encrypted off-host copies, monthly restore drill, RPO 24 hours, initial RTO 60 minutes. Automated retention/off-host encryption is intentionally not destructive by default.
