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
| 6. Numeric depth | in_progress | 13 official FRED series live-verified again on 2026-08-11; labor, IG credit, deposits, primary credit, housing, STLFSI, real yield, broad USD; historical backfill/replay and non-US depth remain |
| 7. News coverage/lifecycle | in_progress | Separate news coverage, snapshot-time decay, immediate fusion recompute; all 10 configured official feeds (Fed/ECB/SEC/CFTC/BIS/BoJ/RBI/BoE/BoC/FDIC) live-verified on 2026-08-11; offline fixtures cover security-sensitive adapters. RBA rejected from production after reproducible HTTP 403 |
| 8. Evidence memory profiles | completed (optional advanced) | Basic SQLite FTS5 works without embeddings. Advanced pgvector has schema, continuous ingestion, embedding queue/fallback, hybrid search, health API and a real local PostgreSQL/pgvector verification with relational evidence ID |
| 9. Scenarios/recovery/diff | completed (shadow seed) | 11 Crisis Playbooks, causal chain, anchors, invalidation, recovery, evidence IDs and persisted causal diff; historical calibration remains stage 12 |
| 10. Exposure/scorecard | completed (shadow seed) | Read-only open-trade overlay and persistent signal lifecycle scorecards; outcome/reaction resolution needs live history |
| 11. UI/help/navigation | completed | RU-first v11 metadata and bands, compact main view, journal subnavigation, models in tools, six analysis tabs, accessible help dialog, scenario expansion, exposure overlay; manually verified in in-app browser at desktop/mobile and automated authenticated Playwright E2E |
| 12. Replay/calibration | implemented; gate failed honestly | Causal v10/v11 comparison plus economic/historical/full/no-trend/no-events/no-contagion/no-dependency/base-rate variants; future-release regression test; real financial-stress manifest checksum `66187057a90d204786af06a658b5ea4c420e694baca3dbaa69641a67b3621aaf`. Historical v11 coverage produced zero eligible samples, so v11 remains shadow and probability is null |
| 13. Packaging/E2E/security | completed for repository candidate | Authenticated RU/EN/mobile/degraded Playwright E2E; CI installs Chromium; overall coverage 80.12%; computational core 90.31%, runtime 90.09%, PostgreSQL memory 96.83%; self-host doctor, source contracts, guarded update/rollback, encrypted off-host backup and isolated restore drill are tested |
| 14. Rollout/canary | implemented; external run pending | Radar-specific persistent canary code + systemd timer checks HTTP, snapshot lag, false-stable, numeric/news state, source failures, queues, backup checksum/age, disk size and sample density. Permanent HTTPS, real off-host mount and a new 14-day calendar run must be verified on the target server |

## Неподвижные ограничения

- `candidate-v10` и его snapshots не переписываются.
- `candidate-v11` остаётся shadow, пока не пройдены replay, sensitivity и live
  gates.
- Live probability остаётся `null` до победы над baseline.
- Календарный 14-дневный canary нельзя объявлять завершённым заранее.

## Последнее доказательство (2026-08-11)

- Repository candidate commits:
  `9dedacb` (v11 shadow core/UI), `5bbd69e` (self-host/CI/canary),
  `9ac3d9f` (runtime-aligned documentation).
- Targeted replay/scoring/validation: `19 passed`.
- Targeted UI/i18n/canary: `12 passed`.
- Authenticated Playwright browser E2E: `1 passed` (RU/EN, six analysis tabs,
  help dialog, mobile 390 px, API degraded state).
- Реальный v11 replay manifest:
  `data/reports/crisis-radar-v11-financial-stress-manifest.json`.
- Manifest не является успешным promotion evidence: числовое историческое
  покрытие v11 ниже fail-closed gate, eligible signals = 0, probability = null.
- Полный CI-эквивалент: `402 passed`, одно предупреждение
  совместимости Starlette/httpx, overall coverage `80.12%`.
- Отдельные coverage gates: computational core `90.31%`,
  auth/config/main/jobs/migrations `90.09%`, PostgreSQL memory `96.83%`.
- Source registry: 20 версионируемых контрактов; offline contract gate проходит.
- Live source contracts: 13/13 FRED v11 series и 10/10 configured official
  RSS feeds вернули валидные данные 2026-08-11.
- Backup/update tests: verified SQLite restore, age encryption, off-host checksum,
  safe retention, PostgreSQL dump/disposable restore contract, immutable release
  activation and schema-safe rollback.
- Production entrypoints напрямую запускаются из файлов, как в systemd; regression
  test защищает от `ModuleNotFoundError`.

## Внешние условия, которые нельзя закрыть в репозитории

1. `candidate-v11` не может стать production-primary до появления
   достаточной causal history и победы над baseline.
2. Постоянный HTTPS, age recipient и реально отдельное off-host
   хранилище должны быть настроены и проверены на целевом
   сервере.
3. Canary должен реально проработать 14 календарных дней;
   этот статус не симулируется тестами.
