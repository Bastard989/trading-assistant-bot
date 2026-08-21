# Deployment and rollback

## Preconditions

1. Rotate the exposed Telegram token.
2. Prepare versioned immutable release directories and a dedicated `trading-assistant` user.
3. Put DB/media/backups under `/var/lib/trading-assistant` (`0700` directories, `0600` files).
4. Install `/etc/trading-assistant.env` as root with mode `0600`; set absolute `DATABASE_PATH`, `TRADE_UPLOAD_DIR`, HTTPS `WEB_APP_URL`, `APP_ENV=production`, `AUTO_MIGRATE=false`, and the owner allowlist.
5. Create and restore-test a pre-cutover online backup.
6. Generate `release-manifest.json` for every immutable release and never edit a release after activation.

## Dry run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/migrate.py /tmp/live-copy.sqlite3
python -m scripts.self_host source-check
python -m scripts.self_host doctor --profile server
```

Compare counts and terminal/open trade IDs before and after the copy migration.

## Immutable update (requires owner approval)

Create a release directory from the reviewed artifact, install dependencies,
generate its manifest, then run the guarded switch:

```bash
python -m scripts.release_manifest \
  --release RELEASE_ID \
  --release-directory /opt/trading-assistant/releases/RELEASE_ID

python -m scripts.self_host update \
  --profile server \
  --database /var/lib/trading-assistant/trading_bot.sqlite3 \
  --backup /var/lib/trading-assistant/backups/PRE_UPDATE.sqlite3 \
  --release RELEASE_ID
```

`update` verifies the backup, performs migration on an isolated online copy,
checks the release manifest/schema and atomically switches the `current` symlink.
It intentionally does not restart services. Inspect its JSON before the cutover:

```bash
sudo systemctl stop trading-assistant-bot trading-assistant-api
sudo -u trading-assistant /opt/trading-assistant/current/.venv/bin/python \
  /opt/trading-assistant/current/scripts/migrate.py /var/lib/trading-assistant/trading_bot.sqlite3
sudo systemctl start trading-assistant-api
curl --fail http://127.0.0.1:8080/health/ready
sudo systemctl start trading-assistant-bot
```

Install the supplied systemd units and Caddyfile only after reviewing paths/domain. Uvicorn must remain on `127.0.0.1`; only Caddy is public.

## Rollback

Stop both services and run `python -m scripts.self_host rollback --profile server`.
Rollback accepts only the recorded previous immutable release and refuses a
manifest whose schema differs from the current DB. If schemas are incompatible,
restore the verified pre-update backup to a new DB path and change
`DATABASE_PATH`. Never use `git reset --hard` or overwrite the current DB.

After launch, verify health, Telegram `/menu`, Mini App auth failure/success, owner isolation, one temporary journal operation, market outage messaging, logs, and backup timer. Do not send test messages to real users.

## Radar-specific observation window

The generic soak check is useful, but it does not replace the radar canary. Enable
the supplied `trading-assistant-canary.timer`; its persistent state must record
the release ID, start time, samples and incidents. Production requires at least
1,210 successful 15-minute samples spanning 14 real calendar days with no
unresolved blocker.

```bash
.venv/bin/python -m scripts.soak_check \
  --base-url http://127.0.0.1:8080 \
  --duration-seconds 1209600 \
  --interval-seconds 30 \
  --max-consecutive-failures 2 | tee /var/lib/trading-assistant/soak-7d.jsonl
journalctl -u trading-assistant-api -u trading-assistant-bot --since today --priority warning
systemctl list-timers trading-assistant-backup.timer
```

Run long checks through systemd so logout does not stop them. The radar canary
checks HTTP readiness, snapshot lag, false-stable protection, numeric and news
coverage, source failures, notification queues, backup checksum/age, database and
disk growth. It also records database/WAL/backup-directory sizes and derived
snapshot count. A database growth rate above 256 MiB/day and backup staging above
50 GiB are explicit warnings. The manifest deduplicates an incident while it remains active,
records its resolution, and counts it again only if it reopens. It never turns
elapsed time into a simulated pass.
Required-source, discovery-only aggregator and disabled research-collector
failures are recorded as separate incident codes. A GDELT, GSCPI, OECD labour,
IMF PortWatch or Binance stablecoin candidate outage therefore remains visible but cannot be misread as
failure of an official live coverage channel. Bybit stablecoin collection is
also persisted under its own `research_candidate` health identity instead of the
required BTC/ETH sync run. Clients use bounded retries; stored failure reasons
are sanitized.

The news scheduler persists one analytical graph after the complete feed batch,
not after every source. If a storage warning opens, first run
`scripts/radar_snapshot_retention.py` without `--apply`. Applying the plan
requires an automatically created and verified online backup; `--vacuum` is only
allowed in a stopped-service maintenance window.

For the first 24 hours, inspect authenticated Crisis Radar source health after each scheduled macro/global sync and confirm that the snapshot timestamp advances without duplicate alert deliveries. For fourteen days, review backup sidecars, source failures, restart counts, Telegram delivery retries, and disk growth daily. A future observation period cannot be claimed as passed in advance; record its actual start/end and incidents in the private operations log.
