# Deployment and rollback

## Preconditions

1. Rotate the exposed Telegram token.
2. Prepare one immutable checkout and a dedicated `trading-assistant` user.
3. Put DB/media/backups under `/var/lib/trading-assistant` (`0700` directories, `0600` files).
4. Install `/etc/trading-assistant.env` as root with mode `0600`; set absolute `DATABASE_PATH`, `TRADE_UPLOAD_DIR`, HTTPS `WEB_APP_URL`, `APP_ENV=production`, `AUTO_MIGRATE=false`, and the owner allowlist.
5. Create and restore-test a pre-cutover online backup.

## Dry run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/migrate.py /tmp/live-copy.sqlite3
```

Compare counts and terminal/open trade IDs before and after the copy migration.

## Cutover (requires owner approval)

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

Stop both services, point `/opt/trading-assistant/current` back to the previous immutable commit, and start the previous code. If a schema incompatibility exists, restore the verified pre-cutover backup to a new DB path and change `DATABASE_PATH`. Never use `git reset --hard` or overwrite the current DB.

After launch, verify health, Telegram `/menu`, Mini App auth failure/success, owner isolation, one temporary journal operation, market outage messaging, logs, and backup timer. Do not send test messages to real users.

## Observation window

Keep the new release under observation before calling the server migration complete:

```bash
.venv/bin/python -m scripts.soak_check \
  --base-url http://127.0.0.1:8080 \
  --duration-seconds 604800 \
  --interval-seconds 30 \
  --max-consecutive-failures 2 | tee /var/lib/trading-assistant/soak-7d.jsonl
journalctl -u trading-assistant-api -u trading-assistant-bot --since today --priority warning
systemctl list-timers trading-assistant-backup.timer
```

Run the soak command under systemd/tmux so logout does not stop it. It probes both liveness and schema/database readiness, emits bounded JSONL samples, aborts after the configured consecutive-failure budget, and returns non-zero if any sample failed. A separate daily review still checks authenticated source health, notification delivery/retry counts, backups, disk and restart counters because those are not safe to expose on a public unauthenticated endpoint.

For the first 24 hours, inspect authenticated Crisis Radar source health after each scheduled macro/global sync and confirm that the snapshot timestamp advances without duplicate alert deliveries. For seven days, review backup sidecars, source failures, restart counts, Telegram delivery retries, and disk growth daily. A future observation period cannot be claimed as passed in advance; record its actual start/end and incidents in the private operations log.
