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

