# Backup and restore

Never copy an active SQLite file with ordinary `cp`. Use the online backup API.
The supported daily command creates an atomic SQLite backup, runs integrity and
foreign-key checks, writes a SHA-256 sidecar and mode `0600`:

```bash
.venv/bin/python scripts/backup_daily.py \
  --source /var/lib/trading-assistant/trading_bot.sqlite3 \
  --directory /var/lib/trading-assistant/backups
```

For the production server, configure an age public recipient and a mounted
off-host directory in `/etc/trading-assistant.env`:

```dotenv
BACKUP_DIRECTORY=/var/lib/trading-assistant/backups
BACKUP_AGE_RECIPIENT=age1...
OFF_HOST_BACKUP_DIRECTORY=/var/lib/trading-assistant/off-host-backups
```

Then the same command can encrypt the verified copy, copy only the `.age` file
off-host, verify the destination checksum and apply the safe 7-daily/4-weekly
retention policy:

```bash
.venv/bin/python scripts/backup_daily.py \
  --source /var/lib/trading-assistant/trading_bot.sqlite3 \
  --directory /var/lib/trading-assistant/backups \
  --age-recipient "$BACKUP_AGE_RECIPIENT" \
  --off-host-directory /var/lib/trading-assistant/off-host-backups \
  --apply-retention --remove-local-plaintext-after-off-host \
  --daily 7 --weekly 4
```

After the encrypted off-host copy and its checksum are verified, the production
unit removes only the new local plaintext staging file. Both the local encrypted
set and the off-host encrypted set receive the 7-daily/4-weekly policy. A failed
encryption/copy never reaches plaintext removal. The retention command refuses an
empty/unverified set and never deletes every copy. It accepts only backups with
valid checksum sidecars. Test the plan before applying it:

```bash
python -m scripts.self_host retention-plan \
  --backup-directory /var/lib/trading-assistant/off-host-backups
```

SQLite verification and isolated restore drill (the live DB is not overwritten):

```bash
python -m scripts.self_host backup-verify --backup /var/lib/trading-assistant/backups/BACKUP.sqlite3
python -m scripts.self_host restore-drill \
  --backup /var/lib/trading-assistant/backups/BACKUP.sqlite3 \
  --output /tmp/trading-assistant-restore-drill.sqlite3
```

The JSON result contains integrity, foreign-key status, schema and per-table
counts. Compare users, open trades, journal rows, observations and snapshots with
the source before approving a real restore.

Advanced PostgreSQL/pgvector profile:

```bash
python scripts/backup_postgres_daily.py \
  --directory /var/lib/trading-assistant/backups \
  --age-recipient "$BACKUP_AGE_RECIPIENT" \
  --off-host-directory /var/lib/trading-assistant/off-host-backups

python -m scripts.self_host postgres-restore-drill \
  --backup /var/lib/trading-assistant/backups/BACKUP.dump \
  --postgres-dsn "$DISPOSABLE_RESTORE_DSN" \
  --confirm-disposable-target
```

The PostgreSQL drill requires the explicit disposable-target flag. Never point it
at the production database. DSNs are passed to `pg_dump`/`pg_restore` through the
process environment and are not printed in command arguments or JSON output.

The 2026-07-21 pre-Bybit cutover backup was restored to a separate `/tmp` database: integrity returned `ok`, foreign-key check returned no rows, and the restored counts were users 1, trades 0, journal entries 0, Crisis Radar observations 25,057, and backtest runs 2. After the successful Bybit history/derived-catalog migration, a second verified online backup was created with SHA-256 `a481b2bd4129e0ac96029b38d4d9a515e8b9244aca4d6b487c7aaf90f9b8596a`.

Before production restore, stop both services, preserve the current DB with another online backup, restore to a new path, verify counts/open trades, then atomically change `DATABASE_PATH`. Do not overwrite the only copy.

Target policy: 7 daily and 4 weekly encrypted off-host copies, monthly restore
drill, RPO 24 hours, initial RTO 60 minutes. A local backup alone is not an
off-host backup. Losing the mount, age binary, recipient or recent verified copy
must make the server doctor/canary fail.
