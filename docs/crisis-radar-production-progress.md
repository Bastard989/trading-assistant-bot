# Crisis Radar production progress

Этот файл фиксирует проверяемый прогресс по `docs/crisis-radar-production-plan.md`. Секреты и значения ключей сюда не записываются.

## Baseline

- Начальный commit: `e3c5a59b158390bb8b91282c1f69c9442e5fc2da`.
- Начальная методика: `crisis-radar / starter-v8`.
- Проверенный локальный backup: `trading-assistant-20260804T204122Z.sqlite3`.
- SHA-256 backup: `cf87c8b85279ca00b82f328c74a6e7a6d4897228746ac815a92971331a65df32`.
- Baseline tests: `263 passed`, одно предупреждение о совместимости Starlette/httpx.
- Ruff: passed.
- JavaScript syntax: passed.

## Этапы

| Этап | Статус | Доказательства / заметки |
|---|---|---|
| 0. Baseline и защита | completed | Проверены backup/checksum/restore, fail-closed component flags и master flag; `starter-v8` сохранён с выпущенным immutable checksum, а запросы `candidate-v10` больше не могут получить снимок старой методики. Торговля, журнал, статистика и калькулятор сохранены. |
| 1. Coverage gate | completed | Введены `healthy/degraded/insufficient_data`, обязательные группы и регионы, discount для delayed, `unknown` для сценариев без свежих anchors. Chaos/unit/API/UI tests запрещают ложный `stable`; data-health transitions и отдельная очередь уведомлений реализованы. |
| 2. Threshold registry v2 | completed (candidate) | `candidate-v9/v10`, immutable rationale/basis/promotion state, signed/two-sided branches, owner-scoped overlays, reproducible checksum и boundary tests. Порог остаётся кандидатом до replay promotion; UI показывает точные числа только на уровне «Методика». |
| 3. Глобальные числовые данные | completed (free official contour) | Добавлены registry/contract adapters для FRED, World Bank, BIS и OECD плюс прежние BEA/EIA/ECB/Eurostat/Bybit. Live contract 2026-08-05: World Bank `70`, BIS `1745`, OECD `360`, FRED `1405` строк, все обязательные проверки succeeded. Нестабильный HTML scraping исключён. |
| 4. Мировые новости и события | completed (official + optional discovery) | Официальные Fed/ECB/SEC/CFTC/BIS/BOJ/RBI RSS прошли live contract; документы дедуплицируются, кластеризуются, получают provenance/hash/taxonomy/evidence. GDELT — только optional discovery и на проверке 2026-08-05 честно вернул failed; обязательный официальный контур остался passed. Prompt-like текст не исполняется. |
| 5. Доказательная память | completed (shadow) | Добавлены PostgreSQL/pgvector schema, relational documents/events/edges, 768-d embedding contracts, hybrid temporal search, rebuild tool. Shadow migration копии SQLite: `44` таблицы, `61 866` строк, row-count/checksum parity; исходная SQLite не переключалась. Проверена перестраиваемость индекса и evidence-ID gate. |
| 6. Trend/regime/contagion | completed | `trend-regime-v1`: causal slopes/acceleration/MAD z-score/percentiles/persistence/change-point, breadth, rolling connectedness и lead-lag. Lineage/checksum сохраняются; future-input, determinism, synthetic regime и independence tests прошли. |
| 7. Scenario fusion и возможности | completed (analytics only) | `scenario-fusion-v1`, dependency clusters, anchor channels, numeric/event/contagion evidence и 11 сценариев. Возможности требуют свежую котировку и в production — сохранённое историческое распределение (`n>=5`); иначе результат `wait`. Найденный E2E edge case `unknown` исправлен: endpoint больше не возвращает 500. Сделки не создаются. |
| 8. Backtest и calibration | completed (engine/gates; probabilities not promoted) | Causal replay, right-censoring, checksums, sensitivity/ablation/holdout gates и model card реализованы. Реальные Bybit 15/30d replay (`2 309` записей, `1 804` sufficient, `5` episodes) не победили baseline и дали recall `0`; поэтому вероятность остаётся `null`. Для v10 ещё нет ≥30 независимых разрешённых событий — прогностическая сила экспериментальная. |
| 9. Интерфейс | completed | Три уровня `Главное / Разобрать / Методика`, человеческое объяснение, 24h/7d/15d, события, тренды, сценарии, возможности, источники и раскрываемые `?`. Полная RU/EN локализация, включая глобальную навигацию и accessibility labels; браузерный smoke выполнен на `candidate-v10`. |
| 10. Уведомления | completed | Отдельные market/data-health сообщения, multi-channel confirmation, causal delta, cooldown, dedup, recovery и retry queues; первая загрузка создаёт baseline без ложного market alert. Две недельные сводки используют существующий scheduler. |
| 11. Production hardening | in progress (time-gated canary) | На 2026-08-05: `301 passed`, Ruff/JS/diff-check passed, `pip-audit` — no known vulnerabilities; локальный browser/API E2E passed. Predeploy SQLite backup SHA-256 совпал с baseline, отдельный restore дал integrity `ok`, `45` таблиц, `55 084` наблюдения, `0` сделок. Runbook/model card/пользовательская и техническая методика `.md/.docx` готовы. Серверный deploy/smoke и старт 14-дневного canary фиксируются ниже. |

## Последняя контрольная точка

- Время: `2026-08-05T01:20Z`.
- Full suite: `301 passed`, coverage `71.06%`; единственное предупреждение — deprecation совместимости Starlette TestClient/httpx, не runtime vulnerability.
- Dependency audit после обновления pytest до `9.0.3`: `No known vulnerabilities found`.
- Local browser smoke: overview/analysis/methodology, RU/EN, help dialogs, thresholds, trends и opportunities; все Crisis Radar endpoints returned `200`.
- Live official feeds: required numeric and RSS contracts passed; optional GDELT discovery degraded without влияния на required gate.
- Документация: `docs/crisis-radar-guide.md`, `.docx`, model card и runbook.

## Решения

- Компоненты v2 включаются master flag `CRISIS_RADAR_V2_ENABLED` либо отдельными флагами.
- Отсутствующий или некорректный флаг трактуется безопасно: v2 выключен.
- Покрытие данных и рыночный риск хранятся раздельно: массовый отказ источников не может стать «стабильным рынком».
- Числовой объект `thresholds` сохраняет обратную совместимость; происхождение и статус порога отдаются отдельно как `threshold_methodology`.
- Источник истины для фактов остаётся реляционным; будущий векторный индекс будет производным.
- `candidate-v10` остаётся candidate/shadow до прохождения статистических и календарных gates; наличие кода не считается доказанной прогнозной силой.
- `ready` снимка и методология фильтруются совместно: интерфейс никогда не смешивает `starter-v8` и `candidate-v10`.
- `unknown/insufficient_data` разрешены во всех downstream API и приводят к `wait`, а не к искусственной идее или ошибке 500.

## Внешние и календарные ограничения

- Обязательный 14-дневный live canary нельзя объявить пройденным заранее. До его фактического окончания production promotion остаётся time-gated.
- GDELT discovery может отвечать rate-limit/failure; это optional Tier C, а не обязательный источник истины.
- Бесплатного проверенного machine-readable TradFi options feed нет; Bybit option idea создаётся только при наличии одновременно ликвидных котируемых ног.
- Вероятности v10 не показываются до накопления достаточного event catalog и победы walk-forward модели над baseline.
