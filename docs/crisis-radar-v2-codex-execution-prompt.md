# Исполняющий промпт Codex: Crisis Radar v2

Скопируй этот промпт вместе с файлом `docs/crisis-radar-v2-production-plan.md` в новый Codex-чат, открытый в корне репозитория Trading Assistant.

---

Ты продолжаешь полномасштабную разработку Trading Assistant в текущем репозитории. Твоя задача — полностью реализовать план `docs/crisis-radar-v2-production-plan.md`, не ограничиваясь подготовкой очередного плана, макета, псевдокода или частичной демонстрации.

## Главная цель

Создай проверяемый Crisis Radar v2 с честным `indicator-score-v2`, общей стадией на основе силы напряжения и ширины независимого системного заражения, глубоким мировым покрытием, signed OI, полными threshold metadata, мировым event/news контуром, Crisis Playbook, recovery, causal diff, exposure overlay, live scorecard, понятным RU/EN интерфейсом, self-hosted упаковкой и production-hardening.

Проект распространяется как self-hosted репозиторий. Это не SaaS. Не проектируй массовую мультиарендность и платежи. Покупатель разворачивает собственную копию, подключает собственные ключи и хранит данные у себя.

## Обязательный порядок начала

1. Полностью прочитай `docs/crisis-radar-v2-production-plan.md` до EOF.
2. Прочитай `AGENTS.md`, если он существует, и все непосредственно применимые инструкции репозитория.
3. Проверь текущие `git status`, ветку, историю, незакоммиченные изменения и не уничтожай пользовательскую работу.
4. Изучи существующие `candidate-v10`, migrations, tests, Mini App, bot jobs, server deployment и progress-документацию.
5. Создай или обнови `docs/crisis-radar-v2-production-progress.md` с baseline, этапами, evidence и реальными статусами.
6. Сформируй рабочий план исполнения в инструменте планирования и сразу начни реализацию.
7. Не останавливайся после завершения отдельного этапа и не проси пользователя написать «продолжай». После проверок автоматически переходи к следующему этапу.

## Неподвижные правила

1. Не выдумывай данные, новости, котировки, источники, вероятности, доходности, результаты тестов или backtest.
2. Вероятность остаётся `null`, пока причинный promotion gate не пройден.
3. Не используй LLM как числовой risk engine. LLM может только извлекать структуру с evidence и объяснять сохранённый deterministic result.
4. Не переписывай старые snapshots и выпущенные methodology checksums. Создай новую immutable candidate-версию и shadow comparison.
5. Не делай число источников самоцелью. Каждый новый источник проходит admission contract из плана.
6. Используй только официальные/разрешённые бесплатные источники для обязательного контура. Discovery feeds не становятся источником истины.
7. При подключении или изменении внешнего API проверяй актуальную официальную документацию и terms. Для технических фактов используй первичные источники.
8. Не добавляй нестабильный HTML scraping в required production contour.
9. Не коммить секреты, `.env`, базы, backups, логи, downloaded datasets и персональные данные.
10. Не показывай секреты в выводе команд, логах, документации и финальном ответе.
11. Не создавай, не изменяй и не исполняй сделки. Exposure overlay строго read-only.
12. Не ухудшай торговый журнал, статистику, Telegram handlers, calculator, photo journal и существующую RU/EN локализацию.
13. Все пользовательские названия сначала русские. Английское имя и technical code вторичны и доступны в подробностях.
14. У каждой сложной сущности есть раскрываемое описание: что это, как считается, почему важно, что считается ухудшением, ограничения, источник и technical code.
15. `normal/stressed/transition`, state-machine statuses, group codes, indicator codes и lead-lag не должны оставаться непереведёнными на пользовательской поверхности.
16. Больше данных не должно искусственно увеличивать риск. Коррелированные ряды объединяй dependency graph и проверяй ablation.
17. Отсутствие данных не становится stable/inactive/wait без явной причины. Используй `insufficient_data/unknown`.
18. Любая формула, вес и порог имеют version, rationale, lineage, checksum и promotion status.
19. Векторный поиск не является фактом. Каждый вывод обязан ссылаться на реальный relational evidence ID.
20. Если сложная версия не побеждает baseline, оставь её shadow и сохрани простой production result.

## Разрешённый объём действий

Пользователь этим промптом разрешает в пределах репозитория:

- изменять код, тесты, документацию, migrations, deployment-файлы и UI;
- устанавливать необходимые development dependencies после проверки;
- запускать unit/integration/contract/replay/security/browser tests;
- выполнять live read-only проверки бесплатных официальных sources;
- создавать fixtures только из разрешённых данных без секретов;
- создавать проверяемые backups и мигрировать копии БД;
- использовать существующее безопасное подключение `ssh prod-dev` для read-only проверок и последующего deployment;
- делать commits и push после зелёных quality gates;
- выполнять безопасный server rollout только с backup, restore drill и rollback.

Это разрешение не включает удаление единственной базы/backup, раскрытие секретов, платные покупки, изменение DNS/облачного firewall без отдельного подтверждения или необратимые внешние действия.

## Стратегия реализации

Выполняй этапы плана последовательно:

```text
0 baseline/backup
1 contracts/docs/naming registry
2 indicator-score-v2
3 dependency/group/stage v2
4 signed OI
5 threshold registry v11
6 global numeric depth
7 news coverage/lifecycle
8 evidence memory profiles
9 scenarios/recovery/diff
10 opportunities/exposure/scorecard
11 UI/help/navigation
12 replay/calibration
13 packaging/E2E/security
14 rollout/canary
```

Внутри этапа можешь параллелить независимые задачи, но не допускай конфликтующих правок. После каждого логического блока запускай релевантные тесты. После каждой крупной контрольной точки запускай полный suite.

## Требования к вычислительному ядру

1. Реализуй frequency/profile-aware `indicator-score-v2` из плана.
2. Сохраняй отдельно raw components и итог:
   - economic level/band;
   - historical anomaly/band;
   - trend;
   - acceleration;
   - persistence;
   - regime;
   - data quality;
   - availability;
   - effective score/band;
   - agreement;
   - raw/effective/hysteresis state.
3. Не используй один набор весов для market, monthly, quarterly, annual и leverage series.
4. Реализуй causal robust percentile/MAD z-score с minimum-history gate.
5. Реализуй group score v2 по независимым подканалам.
6. Реализуй versioned dependency graph.
7. Общая стадия должна строиться на:
   - `stress_intensity_0_100`;
   - `systemic_breadth_0_100`;
   - числе независимых кластеров;
   - регионах;
   - anchors;
   - coverage;
   - hysteresis/recovery.
8. На главном экране остаётся одна стадия; две оси показываются как её понятное объяснение.
9. Параллельно сохраняй v10/v11 и причинный diff.
10. Seed weights/stage matrix не объявляй окончательными без replay. Храни их как candidate registry.

## Signed OI

1. Не удаляй старый abs series: он нужен для v10 reproducibility.
2. Подключи signed 1d/7d/30d OI к новой live methodology.
3. Исправь state-machine matching.
4. Сопоставляй знак OI с ценой, funding и volatility.
5. Различай leverage build, orderly deleveraging и liquidation unwind.
6. Добавь migration, fixtures, unit, replay, API и browser tests.

## Thresholds и документация

1. Исправь candidate metadata bug и создай новую immutable methodology.
2. Для каждого индикатора добавь RU/EN:
   - имя;
   - краткое имя;
   - описание;
   - зачем важен;
   - когда ухудшается;
   - как считается;
   - ограничения;
   - источник;
   - technical code.
3. Обнови пользовательскую и техническую методику так, чтобы она точно описывала исполняемый код.
4. Добавь automated documentation consistency tests: versions, formulas, profiles, thresholds и statuses не должны расходиться.
5. Обнови Markdown и DOCX-руководство; для DOCX выполни render-and-visual verification по доступной инструкции документов.

## Глобальные данные

1. Реализуй приоритетные каналы из плана, начиная с наиболее полезных бесплатных official feeds.
2. Не блокируй весь этап отсутствием одного источника: подготовь adapter/config/tests и честную деградацию, продолжи остальные каналы.
3. Если бесплатного разрешённого machine-readable feed нет, не имитируй его. Пометь capability unavailable и объясни причину.
4. Для каждого нового source добавь registry, adapter, fixtures, sync job, source health, provenance, docs и tests.
5. Раздели leading/coincident/lagging и structural context, чтобы annual GDP не управлял быстрым stage.
6. Добавь глубину прежде всего recession, banking/liquidity, commodity, crypto/stablecoin и sovereign scenarios.

## Новости и события

1. Расширь official-first registry по регионам и типам кризисов.
2. Храни оригинальный текст и язык; перевод только производный.
3. Реализуй отдельный news coverage gate.
4. Пересчитывай event time decay на каждый snapshot с taxonomy-specific half-life.
5. После news sync запускай идемпотентный fusion recompute без часовой задержки.
6. Добавь multilingual normalization и evidence-grounded extraction, не позволяя LLM менять deterministic score.
7. Добавь дедуп, corroboration, entity/region/asset links, prompt-injection defense и evidence-ID contracts.

## Evidence memory

1. Basic SQLite profile должен оставаться полностью работоспособным без pgvector.
2. Не объявляй advanced profile готовым, пока PostgreSQL/pgvector реально не участвует в continuous ingestion/search и не покрыт тестами.
3. Реализуй schema migrations, parity, queues, rebuild, backup/restore и hybrid search для advanced profile.
4. Добавь feature flags и безопасный fallback.

## Scenarios, recovery и торговый контекст

1. Реализуй Crisis Playbook для каждого сценария.
2. Покажи causal chain и текущую позицию в ней.
3. Покажи подтверждения, missing confirmations и invalidation.
4. Добавь recovery state без bearish bias.
5. Реализуй causal «Что изменило вывод» для каждого перехода.
6. Реализуй read-only exposure overlay для открытых сделок.
7. Не генерируй направленную opportunity без quote, evidence и historical distribution.
8. Добавь live scorecard и component attribution.

## UI/UX

1. Исправь все raw codes и англоязычные внутренние статусы, видимые на приложенных пользователем скриншотах.
2. Основное имя — русское; ниже допускается English name и code.
3. Добавь единый reusable help dialog/bottom sheet и подключи его ко всем смысловым разделам и карточкам.
4. Добавь click-to-expand без перегрузки главного экрана.
5. Раздел «Разобрать» раздели на внутренние вкладки из плана.
6. Сверни inactive scenarios.
7. Перенеси техническое состояние моделей в настройки/инструменты.
8. Объедини trading journal sections внутренней навигацией, не ломая функции.
9. Округляй пользовательские scores до целых; точность оставь в методике.
10. Полностью покрой RU/EN, accessibility, mobile и desktop.

## Tests и gates

Обязательны:

- unit;
- property/boundary;
- integration;
- source contract;
- migration twice;
- replay/walk-forward;
- sensitivity/ablation;
- chaos/degradation;
- security;
- secret scan;
- dependency audit;
- authenticated browser E2E;
- production smoke.

Подними coverage gates согласно плану. Статические тесты наличия строк в JS не считаются заменой настоящего E2E.

Каждый тестовый результат должен быть реально выполнен. Не пиши `passed`, если команда не запускалась или завершилась ошибкой.

## Database и deployment safety

1. Перед любой live migration создай online backup с SHA-256.
2. Restore-test backup в отдельный путь.
3. Выполни migration dry-run на копии и сравни counts/checksums/FK/integrity.
4. Сохрани предыдущий immutable release и rollback.
5. Переключай live только после локальных и server smoke gates.
6. Не используй quick tunnel как финальный production URL.
7. Поддержи self-hosted permanent HTTPS через собственный домен+Caddy или named Cloudflare Tunnel service.
8. Не меняй firewall/DNS без явного разрешения, если это требуется снаружи.
9. Настрой off-host encrypted backup/retention либо, если отсутствуют внешние credentials, полностью реализуй локальную часть и выдай точную инструкцию владельцу.

## Canary

Текущий HTTP-only soak недостаточен. Реализуй radar-specific canary, который сохраняет:

- health;
- snapshot lag;
- numeric/news coverage;
- source failures;
- false-stable invariant;
- queue depth/retries/duplicates;
- backup freshness/checksum;
- disk growth;
- restarts;
- security incidents.

Canary должен переживать рестарты с непрерывным manifest. Запусти его после rollout. Не объявляй 14 календарных дней пройденными заранее. Весь код и все независимые этапы заверши, а календарный gate оставь `in_progress` с точным start/end и командой проверки.

## Работа с блокерами

Если нужен отсутствующий API key:

1. проверь, действительно ли без него нет разрешённого бесплатного источника;
2. реализуй config, adapter, tests, fixtures и disabled/degraded state;
3. продолжи все независимые этапы;
4. в конце дай короткую инструкцию: где получить ключ, для какого source он нужен, куда добавить и как проверить;
5. никогда не вставляй секрет в tracked file.

Если нужна необратимая внешняя операция, останови только этот пункт. Не останавливай весь план.

## Progress и commits

После каждого этапа обновляй `docs/crisis-radar-v2-production-progress.md`:

- статус;
- commits/files;
- тесты;
- live contracts;
- migrations;
- evidence;
- ограничения;
- следующий этап.

Делай небольшие логические commits. Перед push выполни полный quality gate. Не включай unrelated пользовательские изменения.

## Definition of Done

Не считай задачу завершённой, пока не проверен каждый пункт `Definition of Done` из плана. Для каждого пункта должно быть одно из трёх:

- `completed` с конкретным evidence;
- `in_progress` только для честного календарного canary/live track record;
- `blocked` с конкретной внешней причиной и полностью подготовленной независимой реализацией.

Нельзя заменять реализацию описанием того, как её можно было бы сделать.

## Финальный ответ

После выполнения дай на русском языке:

1. что реализовано по этапам;
2. как теперь считается indicator/group/stage;
3. какие новые данные и news sources реально работают;
4. что изменилось в UI;
5. результаты всех тестов и coverage;
6. migration/backup/restore evidence;
7. deployment URL и состояние API/bot;
8. canary start/status/end;
9. какие probabilities/capabilities честно остались unavailable;
10. какие действия или ключи ещё требуются от владельца;
11. ссылки на plan, progress, guide, model card и runbook;
12. commit SHA и CI URL.

Начинай с чтения плана и baseline-а, затем реализуй этап 0 и непрерывно продолжай до исчерпания всех безопасно выполнимых этапов.

---

Конец исполняющего промпта.
