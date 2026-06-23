# Phase 0–1 report — 2026-06-20

## Changed

- One source branch `production-hardening`; runtime copy untouched.
- Verified online backup and restore drill.
- Telegram `initData` authentication, allowlist, URL identity removal, owner checks.
- Safe attachments, stored-XSS fixes, strict CSP, API rate limits.
- Atomic terminal transitions and durable HTTP/Telegram idempotency.
- Reusable resilient Binance client and stable 400/503 error mapping.
- Atomic checksum migrations, FK/WAL/busy timeout and required indexes.
- Heuristic `rule_score` terminology and finite/geometry validation.
- Public-market level observations no longer close trades or claim order execution.
- UTC-aware business-day boundaries are calculated with the configured IANA timezone.
- API and Telegram trade create/update/close flows now share a `TradeService` with Decimal validation, risk, and PnL calculation before legacy persistence.
- Core Mini App mutations now use Pydantic JSON bodies while old query payloads remain temporarily supported for compatibility.
- Cross-user regression tests cover trade update/close/cancel, session archive, and trade-journal linking ownership boundaries.
- Mini App template hardening now sanitizes generated IDs, state classes, media URLs, session metadata, and journal result updates before HTML insertion.
- Telegram `/open` now has a planned/implemented optional screenshot recognition flow: one TradingView/order-panel photo can become a draft, missing fields are clarified, and manual `/open` remains the fallback.
- Obsidian export architecture is documented as a portable Markdown/YAML/JSON Canvas vault design.

## Migrations

- v1 `baseline-schema-v1`
- v2 `idempotency-keys-v2`
- v3 `trade-review-rule-score-v3`
- v4 `trade-level-observations-notify-v4`

All four ran twice on a temporary copy of the fresh live backup. Integrity/FK checks passed; 17 trades and 2822 candles were preserved. No migration was applied to live.

## Verification

- `pytest`: 73 passed.
- Coverage: 40% overall; security/domain/DB critical paths are covered, legacy Telegram presentation code remains low.
- `ruff check .`: passed.
- `node --check mini_app/app.js`: passed.
- Static checks confirm no URL `user_id`, `initDataUnsafe`, inline event handlers, or inline styles in Mini App JS.
- Browser smoke was attempted but the in-app browser controller did not respond; not counted as passed.
- `pip-audit` is installed and in CI. The local external vulnerability query was blocked by network approval/DNS and is not counted as passed.

## Data impact

No live writes, migrations, process restarts, or cutover were performed. The pre-hardening runtime independently closed trades #16/#17 after the audit; this was recorded, not reversed. Source secrets/backups were changed to mode `0600`.

## Rollback

Development rollback is `git revert` of the logical commits on `production-hardening`. Production rollback is not yet applicable because nothing was deployed. At cutover, use the pre-cutover online backup and previous immutable commit as described in `docs/deployment.md`.

## Remaining risks

- Persisted money still uses legacy SQLite REAL columns.
- Service-layer extraction is partial; non-trade flows still call repositories directly.
- Browser viewport suite and dependency audit need successful external tooling.
- Telegram token rotation, HTTPS/domain, live permissions, migration and cutover require owner action/approval.
- Multi-user readiness is not claimed; current configuration targets a personal allowlist.
- Obsidian export is designed but not yet exposed as an API/Mini App download endpoint.
