# Crisis Radar v2 production progress

Этот журнал относится к `docs/crisis-radar-v2-production-plan.md`. Он фиксирует
только реально выполненные проверки и не содержит секретов.

## Baseline

- Время фиксации: `2026-08-05T12:53:16Z`.
- Ветка: `main`.
- Начальный commit: `380d8e7`.
- Рабочая production-кандидатная методика: `candidate-v10`.
- SQLite schema: `v20` до начала реализации v2.
- Проверяемая локальная копия:
  `data/backups/trading-assistant-v2-baseline-20260805T125257Z.sqlite3`.
- SHA-256 копии:
  `cf87c8b85279ca00b82f328c74a6e7a6d4897228746ac815a92971331a65df32`.
- Backup integrity: `ok`; рабочая БД при создании копии не изменялась.
- Baseline tests: `301 passed`, одно предупреждение совместимости
  Starlette TestClient/httpx.
- Baseline Ruff: passed.
- Примечание запуска: тесты выполняются через `.venv/bin/python -m pytest`;
  прямой `.venv/bin/pytest` в этом окружении не добавляет корень репозитория в
  import path и не является CI-командой.

## Этапы

| Этап | Статус | Evidence |
|---|---|---|
| 0. Baseline/backup | completed | Backup, SHA-256, integrity, 301 tests, Ruff |
| 1. Contracts/docs/naming | completed | v10 runtime отделён от v11 shadow; registry RU/EN для всех v11 indicators сохраняет описание, смысл, расчёт, ограничения и technical code; machine-readable runtime contract проверяется CI; README/model card/guide/deployment/backup docs сверены с shadow-статусом; DOCX отрендерен и визуально проверен на всех 10 страницах |
| 2. Indicator score v2 | completed (shadow) | `indicator-score-v2-seed-1`: economic/historical/effective bands, agreement, frequency profiles, data/history gates, causal lineage; candidate API; tests |
| 3. Dependency/group/stage v2 | completed (shadow) | Independent subchannels/clusters, intensity + systemic breadth, anchors, fail-closed coverage, v10/v11 comparison; tests |
| 4. Signed OI | completed (shadow) | 1d/7d/30d signed OI, leverage build vs liquidation unwind, v10 absolute series preserved; boundary/API/service tests |
| 5. Threshold registry v11 | completed (candidate) | Immutable metadata/checksum/source/role/profile/promotion evidence for v11; v10 untouched |
| 6. Numeric depth | in_progress | 13 scoring FRED v11 series plus 10 disabled causal FRED depth series. `candidate-v13` adds fixed scenario-group coverage and mandatory credit/market/funding plus US/other-advanced/emerging classes. Disabled `candidate-v14` adds official BIS debt-service and real-house-price channels for ten economies. Real contract parsing retained 480 DSR-gap and 1,656 housing observations. Current BIS bulk history lacks exact release vintages, so it is collection evidence rather than causal replay evidence. Non-US depth improved materially but labor, banking funding, sovereign, shipping and stablecoin depth remain incomplete |
| 7. News coverage/lifecycle | in_progress | Separate news coverage, snapshot-time decay, immediate fusion recompute; all 12 configured official channels live-verified on 2026-08-11: 10 RSS, strict HKMA press-release JSON API for Hong Kong/Greater China and OFAC official GovDelivery topic `USTREAS_61` for sanctions events. Offline fixtures cover security-sensitive adapters. RBA rejected after reproducible HTTP 403; the retired native OFAC RSS is not used; broader filings/exchange coverage remains |
| 8. Evidence memory profiles | completed (optional advanced) | Basic SQLite FTS5 works without embeddings. Advanced pgvector has schema, continuous ingestion, embedding queue/fallback, hybrid search, health API and a real local PostgreSQL/pgvector verification with relational evidence ID |
| 9. Scenarios/recovery/diff | completed (shadow seed) | 11 Crisis Playbooks, causal chain, anchors, invalidation, recovery, evidence IDs and persisted causal diff; historical calibration remains stage 12 |
| 10. Exposure/scorecard | completed (shadow seed) | Read-only open-trade overlay and persistent signal lifecycle scorecards; outcome/reaction resolution needs live history |
| 11. UI/help/navigation | completed | RU-first v11 metadata and bands, compact main view, journal subnavigation, models in tools, six analysis tabs, accessible help dialog, scenario expansion, exposure overlay; manually verified in in-app browser at desktop/mobile and automated authenticated Playwright E2E |
| 12. Replay/calibration | implemented; gate failed honestly | Causal v10/v11/v12/v13 comparisons plus ablations and future-release regression tests. v13 replaces the misleading all-indicator denominator with fixed scenario-group coverage and mandatory independent channels/regions. Real replay evaluated 220 monthly cutoffs: 29 eligible, 20 resolved, only 3 independent positive episodes and 0 calibrated scores. Coverage/horizon sensitivity never passed. v11/v12/v13 remain shadow/replay-only and probability is null |
| 13. Packaging/E2E/security | completed for repository candidate | Authenticated RU/EN/mobile/degraded Playwright E2E; CI installs Chromium; overall coverage 80.20%; computational core 90.31%, runtime 90.09%, PostgreSQL memory 96.83%; self-host doctor, source contracts, guarded update/rollback, encrypted off-host backup and isolated restore drill are tested |
| 14. Rollout/canary | in progress on target server | Initial release `7c87903` safely migrated the live DB v20→v23. Active immutable hotfix release `715384d` deduplicates persistent canary incidents; API/bot are active, external temporary HTTPS health is green and all 12 official news channels pass from the server. A fresh radar-specific systemd canary for the active release started `2026-08-11T20:08:48Z` and cannot complete before `2026-08-25T20:08:48Z`. Permanent HTTPS and a real encrypted off-host mount remain external blockers |

Numeric-depth research evidence:
`docs/evidence/crisis-radar-depth-research-20260811.json`. A disposable database
backfill wrote 39 317 points for all ten new series, covered 1990–2026 where the
provider history permits, returned no source errors and passed SQLite integrity.
Of these, 873 unemployment/housing points have causal ALFRED initial-release
times; the other 38 444 points are explicitly `retrospective_revised` research
and cannot enter replay. The working and production databases were not touched
by this verification.

That first collection-only experiment has now been superseded, not rewritten,
by `docs/evidence/crisis-radar-fred-causal-backfill-20260813.json` and
`docs/evidence/crisis-radar-fred-causal-capability-20260812.json`. Provider
capability is verified for 38 configured FRED indicators; the remaining licensed
SP500 series is explicitly `live_only`. The ten disabled depth inputs now retain
15,619 causal initial-release points with zero `retrospective_revised`, zero
estimated-release and zero impossible-release rows. This is data availability,
not promotion evidence: no v11 threshold, checksum, stage or probability changed.

Replay now also rejects legacy `release_time_estimated` observations when they
were fetched later than the indicator's maximum staleness window. This closes the
path where a current revised historical download could appear available at an
old cutoff. Fresh live estimated-release observations remain usable, and ALFRED
initial releases use their actual release dates.

## Неподвижные ограничения

- `candidate-v10` и его snapshots не переписываются.
- `candidate-v11` остаётся shadow, пока не пройдены replay, sensitivity и live
  gates.
- `candidate-v12` является отдельно checksummed replay-only методикой; десять
  новых индикаторов остаются глобально выключенными и не могут войти в live
  v10/v11 без отдельного будущего promotion.
- `candidate-v13` является отдельно checksummed replay-only методикой с
  фиксированным scenario coverage contract. Она не входит в bootstrap live-сервиса
  и не может быть promoted при трёх независимых положительных эпизодах.
- `candidate-v14` является отдельно checksummed disabled collection-кандидатом.
  Его текущая BIS bulk-история не объявляется point-in-time и не может войти в
  live до накопления/получения причинной истории, replay и canary.
- Live probability остаётся `null` до победы над baseline.
- Календарный 14-дневный canary нельзя объявлять завершённым заранее.

## Последнее доказательство (2026-08-13)

- Repository candidate commits:
  `9dedacb` (v11 shadow core/UI), `5bbd69e` (self-host/CI/canary),
  `9ac3d9f` (runtime-aligned documentation), `de15e10` (official HKMA news API),
  `660a254` (official OFAC sanctions feed).
- GitHub Actions CI for `b8c8b37`: passed
  (https://github.com/Bastard989/trading-assistant-bot/actions/runs/31517269168).
- GitHub Actions CI for `de15e10`: passed, including Linux Playwright, coverage
  gates, dependency audit, migrations and gitleaks
  (https://github.com/Bastard989/trading-assistant-bot/actions/runs/31519270936).
- GitHub Actions CI for `660a254`: passed with the same complete Linux gate set
  (https://github.com/Bastard989/trading-assistant-bot/actions/runs/31520769781).
- GitHub Actions CI for documentation commit `7c87903`: passed
  (https://github.com/Bastard989/trading-assistant-bot/actions/runs/31521008770).
- GitHub Actions CI for canary incident-lifecycle hotfix `715384d`: passed
  (https://github.com/Bastard989/trading-assistant-bot/actions/runs/31530885555).
- GitHub Actions CI for causal FRED replay hardening `93e67f9`: passed
  (https://github.com/Bastard989/trading-assistant-bot/actions/runs/31540171731).
- Targeted replay/scoring/validation: `19 passed`.
- Targeted UI/i18n/canary: `12 passed`.
- Authenticated Playwright browser E2E: `1 passed` (RU/EN, six analysis tabs,
  help dialog, mobile 390 px, API degraded state).
- Реальный v11 replay manifest:
  `data/reports/crisis-radar-v11-financial-stress-manifest.json`.
- Manifest не является успешным promotion evidence: числовое историческое
  покрытие v11 ниже fail-closed gate, eligible signals = 0, probability = null.
- Полный regression suite после causal capability/backfill hardening: `427 passed`,
  одно предупреждение совместимости Starlette/httpx, overall coverage `80.20%`.
- Отдельные coverage gates: computational core `90.31%`,
  auth/config/main/jobs/migrations `90.09%`, PostgreSQL memory `96.83%`.
- Full regression after immutable `candidate-v12` registry/replay integration
  and complete verified-FRED replay preparation: `436 passed`, one known
  Starlette/httpx compatibility warning; Ruff passed.
- Full regression after immutable `candidate-v13` scenario coverage, vintage
  preservation and chronological revision-chain hardening: `445 passed`, one
  known Starlette/httpx compatibility warning; Ruff passed.
- Full regression after disabled `candidate-v14` BIS debt-service/housing depth
  integration and live-source contract verification: `453 passed`, one known
  Starlette/httpx compatibility warning; Ruff passed.
- Source registry: 22 версионируемых контракта; offline contract gate проходит.
- Live source contracts: 13/13 scoring FRED v11 series, 10/10 disabled
  next-methodology FRED research series and 12/12 configured official news
  channels (10 RSS + HKMA JSON API + OFAC GovDelivery RSS) вернули валидные
  данные 2026-08-11.
- FRED/ALFRED causal capability audit on 2026-08-13: 38 configured series
  verified, licensed SP500 explicitly `live_only`, zero unknown contracts.
  Isolated depth backfill retained 15,619 causal points across all ten disabled
  research series and passed integrity/foreign-key checks without touching the
  working or production database.
- Immutable `candidate-v12` replay candidate: ten new causal FRED series have
  versioned RU/EN metadata, candidate thresholds, source rationale, dependency
  assignments and a distinct checksum. Real financial-stress replay over
  1998-08-26–2016-09-01 produced 220 cutoffs after all 38 verified FRED series
  were causally backfilled; input count rose from 4 to 32, but numeric coverage
  remained 0.0488–0.3780, therefore eligible cutoffs = 0,
  every stage = `insufficient_data`, promotion = false and probability = null.
  Evidence: `docs/evidence/crisis-radar-v12-financial-stress-replay-20260813.json`.
  Input preparation and isolation evidence:
  `docs/evidence/crisis-radar-v12-replay-input-preparation-20260813.json`.
- Immutable `candidate-v13` replay candidate: scenario coverage is measured over
  fixed independent groups using maximum freshness within each group, while
  global coverage remains a separate diagnostic. The gate requires credit,
  market-price and funding/liquidity channels plus US, two other-advanced and two
  emerging regions. The real 220-cutoff replay produced 29 eligible cutoffs,
  20 resolved samples and only three independent positive OFR episodes;
  `scored_count=0`, promotion=false and probability=null. All stricter coverage
  and 15/30/90-day sensitivity runs also failed promotion. Historical ingestion
  now preserves equal-value distinct vintages and rebuilds chronological revision
  chains; the isolated replay database has integrity `ok` and zero reverse links.
  Evidence:
  `docs/evidence/crisis-radar-v13-financial-stress-replay-20260813.json` and
  `docs/evidence/crisis-radar-v13-replay-input-and-sensitivity-20260813.json`.
- Disabled `candidate-v14` BIS depth candidate adds DSR deviations from the
  preceding 60-quarter mean and real residential-property-price YoY moves for
  US, Canada, UK, China, Hong Kong, Japan, Korea, India, Brazil and Mexico.
  Official live bulk payloads passed strict schema/unit/status/duplicate/future
  and archive-size contracts and produced 480 + 1,656 research observations.
  They remain disabled because current bulk revisions do not prove historical
  release time. Evidence:
  `docs/evidence/crisis-radar-v14-bis-depth-contract-20260813.json`.
- HKMA adapter rejects unsuccessful headers, malformed schema, duplicate/future
  records and URLs outside `www.hkma.gov.hk`; scheduled sync, CLI and news coverage
  use the same client/normalizer router.
- OFAC GovDelivery fixture creates a deterministic `sanctions` event and maps
  Hong Kong designations to the CHN region; live feed returned 25 current items.
- Backup/update tests: verified SQLite restore, age encryption, off-host checksum,
  safe retention, PostgreSQL dump/disposable restore contract, immutable release
  activation and schema-safe rollback.
- Production entrypoints напрямую запускаются из файлов, как в systemd; regression
  test защищает от `ModuleNotFoundError`, включая live FRED/news verifiers.
- `pip-audit`: no known vulnerabilities; local gitleaks and subsequent GitHub
  secret-scan jobs found no leaks;
  schema migration from empty database and repeated migration both returned
  `migrations_ok`.

## Production rollout evidence (2026-08-11)

- Initial schema-migration release: `7c87903`, source commit
  `7c879039aefce8d43416067ff7e035b7e6fc9912`. Active release: `715384d`, source
  commit `715384d4bcf652cbb5744b2b9dd9e1122cfe1b72`. Both releases were built with
  fresh venvs pointing to their own immutable release trees instead of an older
  copied venv. The active tree is root-owned and read-only to the service
  account; API and bot restart successfully from it.
- Initial pre-update online backup used for the v20→v23 migration:
  `pre-update-7c87903-20260811T1910Z.sqlite3`, SHA-256
  `454e5abe65da3c204595e7da639c6b36f98d4abc1f7ab364486f46da118838a4`.
  Integrity `ok`, FK violations `0`, schema `20`, observations `58 628`, trades
  `0`; isolated restore preserved all table counts.
- Pre-update online backup for the active hotfix:
  `pre-update-715384d-20260811T2007Z.sqlite3`, SHA-256
  `1e6be2468137592c92c453a148d1980caa7b84e21b125ece39995c7c46f45134`.
  Integrity `ok`, FK violations `0`, schema `23`, observations `59 690`, trades
  `0`; isolated restore preserved all table counts.
- Shadow migration reached schema `23`, integrity `ok`, FK violations `0` and
  did not change the working database. The controlled live cutover then migrated
  v20→v23 and started API before the bot; `/health/live` and `/health/ready`
  returned success.
- Post-cutover DB: schema `23`, integrity `ok`, FK violations `0`; API and bot
  processes run from release `715384d`.
- Server-side live news verification: 12/12 official channels passed. The first
  v11 snapshot after sync has news coverage `1.0000/healthy`, stage `warning`,
  intensity `58.49`, breadth `56.40`; these values are observations, not a
  calibrated crisis probability.
- Persistent `trading-assistant-canary.timer` samples every 15 minutes. Because
  the monitored executable changed, the active release received a fresh honest
  calendar window: start `2026-08-11T20:08:48.360198Z`; earliest end
  `2026-08-25T20:08:48.360198Z`. The first two live samples have `0` critical
  incidents. One persistent GDELT discovery warning produced one incident, not
  two: live deduplication is therefore verified. The warning does not masquerade
  as healthy official-news coverage and remains visible until it resolves.
- Server doctor now passes token/owner, DB, schema, release, local verified
  backup and installed `age` checks. It correctly remains red for permanent
  HTTPS, an externally retained age recipient/private identity and a separately
  mounted off-host backup directory.
- Machine-readable sanitized evidence:
  `docs/evidence/crisis-radar-v2-server-rollout-20260811.json`.

## Внешние условия, которые нельзя закрыть в репозитории

1. `candidate-v11` не может стать production-primary до появления
   достаточной causal history и победы над baseline.
2. Постоянный HTTPS, age recipient и реально отдельное off-host
   хранилище должны быть настроены и проверены на целевом
   сервере.
3. Canary должен реально проработать 14 календарных дней; текущий release начал
   новый период 11 августа и не может пройти этот gate раньше 25 августа 2026
   года, 20:08 UTC. Этот статус не симулируется тестами.
