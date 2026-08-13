# Crisis Radar v2: план честного вычислительного ядра и self-hosted production

Статус: утверждённая спецификация следующего большого обновления.

Область: личный Trading Assistant, Telegram-бот и Mini App, распространяемые как self-hosted репозиторий. Покупатель разворачивает собственную копию, подключает собственные ключи и хранит данные у себя. Проект не является SaaS и не открывает сделки автоматически.

Главная цель: превратить `candidate-v10` из полезного детерминированного монитора напряжения в проверяемую систему глобального раннего предупреждения с сильным вычислительным ядром, глубоким мировым покрытием, понятным русским интерфейсом и воспроизводимой доказательной базой.

## 1. Неподвижные продуктовые решения

1. Главный экран показывает одну понятную стадию рынка.
2. Внутри стадия строится по двум независимым осям:
   - **сила рыночного напряжения** — насколько сильны текущие неблагоприятные сигналы;
   - **ширина системного заражения** — сколько независимых кластеров, регионов и рынков ухудшается одновременно.
3. Коррелированные сигналы не считаются независимыми подтверждениями.
4. Каждый индикатор показывает четыре результата:
   - `economic_band` — положение относительно экономически объяснимых порогов;
   - `historical_band` — аномальность относительно собственной причинно доступной истории;
   - `effective_band` — итог после объединения уровня, тренда, ускорения, устойчивости и качества данных;
   - `agreement` — согласованы ли экономический и исторический выводы.
5. На главном экране используются русские названия. Английское имя и технический код показываются вторично в подробностях.
6. У каждого раздела, карточки, показателя, статуса и сложного термина есть раскрываемое человеческое объяснение.
7. Больше данных не означает механически больший риск. Каждый новый ряд проходит проверку происхождения, свежести, независимости, истории, задержки и полезности.
8. LLM объясняет сохранённые расчёты и доказательства, но не определяет числовую стадию, пороги, вероятность или доходность.
9. Вероятность остаётся `null`, пока модель не пройдёт причинный walk-forward gate и не победит baseline.
10. Возможности являются сценарной аналитикой. Crisis Radar не создаёт и не исполняет сделки.
11. Все новые формулы сначала работают как новая кандидатная методика параллельно с `candidate-v10`; старая методика не переписывается.
12. Production означает воспроизводимость, наблюдаемость, безопасное обновление и честный live track record, а не наличие большого числа карточек.

## 2. Целевой пользовательский результат

### 2.1. Главное

Пользователь за 10–20 секунд понимает:

- текущую стадию рынка;
- силу напряжения и ширину заражения;
- что изменилось за 24 часа, 7 и 15 дней;
- какой сценарий развивается;
- какие подтверждения уже появились;
- чего ещё не хватает;
- что отменит вывод;
- следующую важную публикацию;
- до трёх условных действий `long / short / hedge / wait`;
- насколько свежи и полны данные.

### 2.2. Разобрать

Вместо одной длинной ленты используются внутренние вкладки:

1. **Сигналы** — главные индикаторы, тренды и изменения.
2. **События** — мировые события и первоисточники.
3. **Регионы** — состояние стран и глобальных каналов.
4. **Сценарии** — причинные цепочки, подтверждения и инвалидация.
5. **Возможности** — условные направления и связь с текущими позициями.
6. **Источники** — здоровье данных и ограничения.

Неактивные сценарии свёрнуты под «Показать остальные».

### 2.3. Методика

Технический уровень показывает:

- русское и английское имя;
- технический код;
- источник и ссылку;
- единицу, частоту и задержку;
- формулу преобразования;
- raw observations и vintages;
- экономические пороги;
- исторические границы;
- все компоненты `indicator-score-v2`;
- качество, freshness и coverage;
- dependency cluster;
- версию методики и checksum;
- ограничения и promotion status;
- историю с кризисными окнами;
- результат shadow-сравнения со старой методикой.

## 3. Вычислительное ядро `indicator-score-v2`

### 3.1. Компоненты

Для каждого индикатора причинно рассчитываются значения от 0 до 1:

```text
economic_level     — положение относительно warning/danger/critical;
historical_anomaly — направленный robust percentile и MAD z-score;
trend              — нормализованный Theil–Sen slope на релевантных горизонтах;
acceleration       — изменение наклона без будущих данных;
persistence        — длительность непрерывного ухудшения;
regime             — causal change-point/state-machine компонент;
data_quality       — качество источника, release time и revision policy;
availability       — fresh/delayed/stale/missing gate.
```

Общий кандидатный контракт:

```text
indicator_score_v2 = availability × data_quality × weighted_profile(
    economic_level,
    historical_anomaly,
    trend,
    acceleration,
    persistence,
    regime
)
```

Один набор весов для всех рядов запрещён. Ввести версионируемые профили:

- `market_daily`;
- `market_intraday`;
- `flow_weekly`;
- `macro_monthly`;
- `macro_quarterly`;
- `structural_annual`;
- `two_sided_leverage`;
- `event_reactive`.

Стартовые веса являются кандидатами и должны пройти sensitivity/ablation/replay. Они хранятся в registry, а не зашиваются без происхождения в UI.

Seed-профили для первого shadow replay:

| Профиль | Economic | Historical | Trend | Acceleration | Persistence | Regime |
|---|---:|---:|---:|---:|---:|---:|
| `market_daily` | 0.25 | 0.20 | 0.20 | 0.10 | 0.10 | 0.15 |
| `market_intraday` | 0.15 | 0.20 | 0.15 | 0.15 | 0.10 | 0.25 |
| `flow_weekly` | 0.30 | 0.15 | 0.20 | 0.10 | 0.15 | 0.10 |
| `macro_monthly` | 0.35 | 0.15 | 0.20 | 0.10 | 0.15 | 0.05 |
| `macro_quarterly` | 0.45 | 0.15 | 0.15 | 0.05 | 0.15 | 0.05 |
| `structural_annual` | 0.55 | 0.15 | 0.10 | 0.05 | 0.10 | 0.05 |
| `two_sided_leverage` | 0.15 | 0.20 | 0.15 | 0.15 | 0.10 | 0.25 |
| `event_reactive` | 0.25 | 0.10 | 0.15 | 0.10 | 0.10 | 0.30 |

Каждая строка суммируется до 1.0. Если компонент недоступен, вес не должен молча
перераспределяться: сначала применяется minimum-component gate, затем разрешённая
ренормализация сохраняется в lineage. При отсутствии economic threshold либо
достаточной истории UI показывает, какой компонент отсутствует.

Минимальная причинно доступная история для первого кандидата:

- intraday: 500 точек и не менее 60 календарных дней;
- daily: 252 точки;
- weekly: 104 точки;
- monthly: 60 точек;
- quarterly: 24 точки;
- annual: 15 точек для exploratory band и 30 для высокой уверенности.

Эти минимумы и веса не продвигаются автоматически: replay может изменить их либо
оставить текущую пороговую методику победителем.

### 3.2. Экономическая полоса

`economic_band` использует объяснимые системные пороги и направление риска:

- `higher_is_worse`;
- `lower_is_worse`;
- `two_sided` с разными семантическими ветками;
- state machine для последовательностей, где одиночный уровень недостаточен.

Порог обязательно содержит:

- warning/danger/critical;
- reference;
- единицу;
- экономическое обоснование RU/EN;
- первичный источник или отметку `internal_candidate`;
- operational role;
- применимый режим и горизонт;
- promotion status;
- checksum;
- дату введения и владельца overlay.

### 3.3. Историческая полоса

`historical_band` строится только из значений, доступных на момент snapshot:

- неблагоприятный percentile 80/95/99;
- направленный MAD z-score 1/2/3;
- минимальная длина истории по профилю;
- отдельные окна спокойного и кризисного режима;
- защита от малой выборки;
- отсутствие полосы при недостаточной истории вместо выдуманного результата.

### 3.4. Согласие и расхождение

Ввести перечисление:

- `confirmed_stress` — economic и historical ухудшаются;
- `early_anomaly` — экономический уровень нормальный, но тренд/аномалия ухудшаются;
- `high_level_stabilizing` — уровень плохой, но динамика улучшается;
- `mixed` — компоненты расходятся;
- `insufficient_history`;
- `insufficient_data`.

Интерфейс обязан объяснять это человеческим языком, например:

> Уровень пока находится в нормальном диапазоне, но скорость ухудшения исторически необычна. Это раннее наблюдение, а не подтверждённый кризисный сигнал.

### 3.5. Итоговая полоса

`effective_band` не является вероятностью. Начальные границы score 0–100 задаются как кандидаты и проверяются replay. Hysteresis, confirmation points и recovery margin применяются после raw score и сохраняются отдельно.

Seed-границы для shadow:

```text
normal    0 <= score < 25
warning  25 <= score < 50
danger   50 <= score < 75
critical 75 <= score <= 100
```

Критическая экономическая полоса не может быть понижена только из-за спокойной
короткой истории. Историческая аномалия может создать `early_anomaly`, но без
economic/anchor confirmation не создаёт системный critical.

## 4. Группы, независимость и общая стадия

### 4.1. Dependency graph

Создать версионируемый граф зависимостей:

- индикатор → подканал;
- подканал → группа риска;
- группа → независимый системный кластер;
- кластер → сценарии;
- события → регионы/сущности/активы/сценарии.

Примеры независимых кластеров:

- реальная экономика;
- рынок труда;
- корпоративный кредит;
- банки и фондирование;
- долларовая ликвидность/FX;
- акции и волатильность;
- суверенный риск;
- сырьё и supply chain;
- жильё/CRE;
- криптоплечи и stablecoins.

### 4.2. Group score v2

Группа считается по независимым подканалам, а не по числу рядов. Кандидатная формула должна включать:

- weighted central tendency;
- top-two independent subchannels;
- breadth;
- acceleration/persistence;
- penalty за зависимость;
- quality/coverage gate.

Точные веса выбираются shadow replay и фиксируются в registry. Группа с одним рядом явно маркируется `thin_group` и не может одна создать системный critical, кроме отдельно определённого официального emergency event.

Первый воспроизводимый seed:

```text
group_raw = 0.35 * weighted_central_tendency
          + 0.30 * mean(top_two_independent_subchannels)
          + 0.20 * stressed_subchannel_breadth
          + 0.15 * group_dynamics

group_score_v2 = group_raw
               * data_quality_gate
               * coverage_gate
               * (1 - dependency_penalty)
```

`group_dynamics` объединяет acceleration и persistence, но не может дать более
25 баллов группе, в которой ни один подканал не пересёк warning и нет
`early_anomaly` с достаточной историей. Один исходный ряд не может одновременно
занять оба места в `top_two_independent_subchannels`.

### 4.3. Две оси общей стадии

Рассчитываются:

```text
stress_intensity_0_100
systemic_breadth_0_100
active_independent_clusters
active_regions
anchor_confirmation
```

`stress_intensity` отвечает: «насколько сильны ухудшения?»

`systemic_breadth` отвечает: «насколько широко они распространились между независимыми системами?»

Seed-расчёт для shadow:

```text
cluster_score = max(independent_group_scores_in_cluster)

stress_intensity = 0.60 * quality_weighted_mean(active_cluster_scores)
                 + 0.40 * mean(top_two_active_cluster_scores)

systemic_breadth = 100 * (
    0.50 * active_cluster_weight / eligible_cluster_weight
  + 0.25 * active_region_weight / eligible_region_weight
  + 0.25 * active_anchor_classes / eligible_anchor_classes
)
```

`eligible` включает только достаточно покрытые сущности, но массовая потеря данных
не уменьшает знаменатель так, чтобы искусственно поднять breadth: состав eligible
фиксируется версией методики, а пропавшие обязательные элементы переводят coverage
в degraded/insufficient. Коррелированные группы внутри одного кластера дают один
cluster score. Structural annual context не может быть market/credit/liquidity
anchor для быстрой стадии.

Единая стадия является state machine над двумя осями, coverage и anchors:

- `stable`;
- `tension`;
- `warning`;
- `confirmation`;
- `crisis`;
- `recovery`;
- `insufficient_data`.

Начальная двумерная матрица порогов является кандидатом. Переходы `confirmation/crisis` требуют нескольких независимых кластеров и как минимум одного market/credit/liquidity anchor. Recovery рассчитывается отдельно и не маскируется под stable.

Seed-матрица для shadow comparison:

| Стадия | Интенсивность | Ширина | Дополнительные условия |
|---|---:|---:|---|
| `stable` | <25 | <20 | нет critical anchor |
| `tension` | ≥25 или | ≥20 | минимум один подтверждённый подканал |
| `warning` | ≥40 | ≥35 | минимум 2 независимых кластера |
| `confirmation` | ≥60 | ≥50 | минимум 3 кластера и market/credit/liquidity anchor |
| `crisis` | ≥75 | ≥65 | минимум 4 кластера, минимум 2 региона и critical anchor |
| `recovery` | снижение ≥15 от пика | ширина <50 | только после `confirmation/crisis`, минимум 2 подтверждающих snapshot |

Если числовой coverage ниже обязательного gate, стадия становится
`insufficient_data` независимо от seed-матрицы. Если numeric coverage здоров, но
новостной контур ослеп, числовая стадия сохраняется с явным снижением общей
надёжности; отсутствие новостей не превращается ни в stable, ни в crisis.

### 4.4. Shadow comparison

Каждый snapshot сохраняет параллельно:

- stage v10;
- stage v11;
- интенсивность;
- ширину;
- различия;
- причины различий;
- checksum входов;
- методологию.

Новая стадия не становится основной до прохождения replay, sensitivity, chaos и live canary.

## 5. Исправление криптовалютного OI

1. Оставить raw OI level.
2. Live рассчитывать подписанные изменения 1d/7d/30d.
3. Разделить состояния:
   - `leverage_build_long`;
   - `leverage_build_short` при доступном направлении;
   - `orderly_deleveraging`;
   - `liquidation_unwind`;
   - `price_down_oi_up`;
   - `price_down_oi_down`;
   - `price_up_oi_up`;
   - `price_up_oi_down`.
4. Объединять OI со знаком funding, ценой, realized volatility и ликвидациями, если источник прошёл contract gate.
5. Старый `_abs_change` сохранить только для воспроизводимости v10.
6. Миграция не переписывает старые snapshots.
7. Добавить boundary, sign, state-machine, replay и API/UI tests.

## 6. Threshold registry v11

Исправить потерю metadata в `candidate-v10` и создать immutable `candidate-v11`:

- `basis`;
- `rationale_ru/en`;
- `source_url`;
- `operational_role`;
- `profile`;
- `promotion_status`;
- `promotion_evidence`;
- `introduced_at`;
- `retired_at`;
- `checksum`;
- системное значение;
- owner overlay без изменения системной истории.

Все 55 существующих рядов получают русское название, английское название, описание, смысл ухудшения, формулу, ограничения и единицы. Raw code никогда не является основным пользовательским названием.

## 7. Расширение глобального числового покрытия

### 7.1. Принцип

Цель — не максимальное число рядов, а достаточная глубина независимых каналов. Каждый ключевой сценарий должен иметь несколько ведущих, совпадающих и запаздывающих индикаторов.

### 7.2. Приоритетные каналы

#### Рынок труда

- initial/continuing claims;
- payrolls и unemployment;
- vacancies/quits;
- temporary employment;
- average weekly hours;
- региональные аналоги при наличии официальных feeds.

#### Кредит

- HY и IG spreads;
- скорость расширения spreads;
- default/delinquency proxies;
- lending standards;
- credit issuance;
- household/corporate debt service.

#### Банки и фондирование

- deposits;
- central-bank emergency borrowing;
- bank equity relative strength;
- funding stress proxies;
- официальные bank failure/resolution events;
- CDS только при легальном и проверяемом feed.

#### Долларовая ликвидность и FX

- broad dollar index;
- major и EM FX stress;
- reserves;
- cross-currency/funding proxies;
- swap-line/emergency liquidity usage;
- capital-control events.

#### Акции и волатильность

- broad drawdown;
- market breadth;
- equal-weight relative performance;
- small caps;
- banks;
- semiconductors/technology;
- Nasdaq;
- realized volatility;
- correlation regime.

#### Облигации

- nominal и real yields;
- curve state machines;
- term premium при доступности;
- bond volatility или разрешённый официальный proxy;
- sovereign spread proxies.

#### Жильё и CRE

- permits/starts/sales;
- prices;
- mortgage stress;
- delinquency;
- commercial real estate lending/vacancy proxies.

#### Сырьё, перевозки и supply chain

- oil/gas;
- industrial metals;
- food;
- freight/shipping;
- delivery times;
- supply-chain pressure;
- inventory shocks.

#### Crypto/stablecoins

- signed OI;
- funding;
- realized volatility;
- liquidations;
- stablecoin price/depeg;
- market cap/supply change;
- exchange inflow/outflow только при проверяемом feed;
- exchange status/reserve attestations как доказательства, не как гарантии.

#### Суверенный риск

- FX drawdown;
- reserves;
- inflation/rates;
- debt-service burden;
- sovereign spreads/proxies;
- default, restructuring, sanctions и capital controls.

### 7.3. Source admission gate

Новый источник не становится обязательным, пока не пройдены:

- официальный статус или разрешённый публичный API;
- terms/robots/licensing review;
- HTTPS и host allowlist;
- schema/units/frequency contract;
- bounded timeout/retry/rate limit;
- payload size и parser hardening;
- duplicate/revision/vintage test;
- release-lag policy;
- минимум один offline fixture;
- live contract test;
- degradation test;
- source-health integration;
- документированное отсутствие платной зависимости.

Нельзя делать нестабильный HTML scraping обязательной частью production.

## 8. Мировые новости и события v2

### 8.1. Покрытие

Расширить official-first registry:

- центральные банки и статистические ведомства ключевых регионов;
- IMF, World Bank, BIS и региональные регуляторы;
- банковские resolution/failure authorities;
- официальные биржи;
- stablecoin issuers;
- SEC/регуляторные filings;
- корпоративные filings о default, bankruptcy и going concern;
- официальные санкционные и торговые публикации;
- GDELT/агрегаторы только как discovery.

Конкретный endpoint подключается только после live проверки актуальной официальной документации.
Текущий проверенный азиатский банковский контур включает официальный JSON API
пресс-релизов HKMA. Он учитывается в news coverage как самостоятельный источник
Hong Kong/Greater China, проходит host allowlist и строгий schema contract и не
маскируется под RSS. GDELT по-прежнему не считается подтверждением.
Санкционные события поступают через официальный OFAC subscription topic
`USTREAS_61`, опубликованный в GovDelivery RSS. Собственный OFAC RSS, закрытый
провайдером в 2025 году, не используется и не имитируется HTML scraping.
Официальный RSS публикаций Национального бюро статистики Китая
(`nbs_news`) даёт отдельный CHN-контур роста, труда, CPI/PPI, промышленности,
спроса и жилья. Его нестандартное время публикации трактуется как China
Standard Time, payload ограничен 6 MB, а китайские deterministic-правила не
превращают обычную статистическую публикацию в crisis event.
Официальный RSS Банка Кореи (`bok_news`) добавляет отдельный KOR-контур: ВВП,
настроения, денежно-кредитная политика, банковская устойчивость, платёжный баланс
и валютные резервы. Для ссылок сохраняются только необходимые `nttId` и
`menuNo=400069`; чужой host, path, лишние или повторные параметры отклоняются.
Поскольку обзоры центрального банка могут упоминать войну или сырьевой шок лишь
как фон, event-кандидат для этой ленты требует кризисной формулировки в заголовке.

### 8.2. Мультиязычность

- хранить original language и original text;
- rule extraction поддерживает минимум RU/EN и расширяемые словари;
- другие языки проходят deterministic normalization либо grounded extraction;
- перевод является производным слоем и не заменяет оригинал;
- каждый факт ссылается на evidence ID и URL;
- prompt-like текст никогда не исполняется.

### 8.3. News coverage gate

Числовое и новостное покрытие хранятся отдельно:

- `numeric_coverage`;
- `news_coverage`;
- `regional_event_coverage`;
- `source_health`;
- `overall_reliability`.

Падение всех новостных feeds не делает числовую стадию stable, но интерфейс обязан показать «новостной контур ослеп» и понизить надёжность event-компонента.

### 8.4. Event lifecycle

При каждом scenario snapshot пересчитывать:

```text
current_event_score = severity × source_quality × corroboration
                    × novelty × relevance × time_decay
```

Half-life зависит от taxonomy:

- emergency bank/exchange/cyber — часы/дни;
- armed conflict/supply disruption — дни/недели;
- sanctions/regulatory restrictions — недели;
- recession/default restructuring — недели/месяцы.

Сохранённый исходный score не переписывается; current decayed score является версионируемой производной. После успешного news sync немедленно пересчитывать event/fusion snapshot либо ставить идемпотентную recompute job.

## 9. Evidence memory

1. SQLite может остаться рабочим transactional store для лёгкой self-hosted установки.
2. PostgreSQL/pgvector становится опциональным `advanced` профилем, а не скрытой обязательной зависимостью.
3. Basic профиль обязан работать без embeddings через relational/full-text evidence search.
4. Advanced профиль получает:
   - PostgreSQL schema migrations;
   - continuous ingestion;
   - embedding queue;
   - rebuild;
   - hybrid search;
   - retention;
   - backup/restore;
   - parity metrics;
   - integration tests.
5. Нельзя писать в документации, что pgvector работает в production, пока сервис реально не развёрнут и не участвует в ingestion/search.
6. Vector match никогда не считается фактом без реляционного evidence ID.

## 10. Scenario engine v2

Для каждого сценария реализовать `Crisis Playbook`:

- человеческое описание;
- causal chain;
- leading/coincident/lagging channels;
- required anchors;
- текущая позиция в цепочке;
- что уже подтверждено;
- чего не хватает;
- invalidation;
- recovery conditions;
- affected assets;
- possible beneficiaries;
- limitations;
- historical episodes;
- current evidence IDs.

Приоритет глубины:

1. глобальная/региональная рецессия;
2. банковский и системный финансовый стресс;
3. долларовая/рыночная ликвидность;
4. сырьевой/supply-chain инфляционный шок;
5. crypto leverage/stablecoin/exchange failure;
6. sovereign/FX crisis;
7. technology/AI repricing.

### Recovery mode

Для каждого сценария симметрично рассчитывать:

- stress no longer broadening;
- anchors recovering;
- credit/liquidity normalization;
- improving breadth;
- scenario invalidated;
- recovery watch/confirmed;
- conditional recovery-long opportunities.

### «Что изменило вывод»

Каждый переход сохраняет causal diff:

- добавленные подтверждения;
- исчезнувшие подтверждения;
- пересечённые пороги;
- изменения breadth/intensity;
- новые/затухшие события;
- изменение coverage;
- причины удержания hysteresis.

## 11. Возможности и связь с торговым журналом

### 11.1. Ограничения

- никаких ордеров;
- никаких обещаний прибыли;
- никаких диапазонов без сохранённого условного распределения;
- никаких TradFi/options идей без свежей проверяемой котировки;
- `wait` является нормальным результатом.

### 11.2. Exposure overlay

Для открытых сделок read-only показывать:

- согласуется ли позиция со сценарием;
- конфликтует ли позиция с ним;
- концентрацию по направлению/активу;
- уязвимость плеча;
- потенциальный hedge-класс;
- какие условия изменят оценку.

Модуль не меняет сделку, стоп, цель или объём.

### 11.3. Live scorecard

Для каждого сигнала/сценария сохранять:

- first detected;
- first elevated/confirmed;
- lead time;
- resolved outcome;
- false alert;
- time to invalidation;
- market reaction horizons;
- maximum favorable/adverse excursion;
- component attribution;
- ablation result;
- baseline comparison.

## 12. Интерфейс, русский язык и объяснения

### 12.1. Naming registry

Для каждого объекта обязательны:

- `name_ru`;
- `name_en`;
- `short_name_ru/en`;
- `description_ru/en`;
- `why_it_matters_ru/en`;
- `worse_when_ru/en`;
- `calculation_ru/en`;
- `limitations_ru/en`;
- `source_name`;
- `technical_code`.

Пример основной подписи:

> Изменение курса корейской воны за 30 дней  
> Korean won 30-day change · `korea_fx_30d_change`

Статусы `NORMAL/STRESSED/TRANSITION`, `lead-lag`, state machines и internal codes не показываются пользователю без русского перевода.

### 12.2. Help system

У каждого смыслового блока есть кнопка `?` или раскрываемая строка «Что это значит». Она открывает единую accessible bottom sheet/dialog:

- что это;
- как считается;
- почему важно;
- что считается ухудшением;
- как читать текущий результат;
- ограничения;
- источник;
- технический код.

Карточки поддерживают click-to-expand. Добавить глобальный словарь и поиск по терминам.

### 12.3. Упрощение навигации

- «Модели» перенести из основной навигации в настройки/инструменты.
- «Сделки / Сессии / Дневник / Аналитика» объединить в один раздел журнала с поднавигацией, не ломая URL/API.
- неактивные сценарии свернуть;
- unavailable asset classes показывать одной компактной строкой;
- пользовательские scores округлять до целых;
- точные значения оставить в методике;
- длинный мировой контур показывать только во вкладке «Регионы».

Все изменения покрыть RU/EN, accessibility, mobile 320–430 px и desktop tests.

## 13. Historical validation и promotion

### 13.1. Causal data rules

- только `observed_at <= cutoff` и `released_at <= cutoff`;
- revisions/vintages не подменяют ранее доступное значение;
- right-censoring;
- embargo gap между train/test;
- time split;
- region holdout;
- crisis holdout;
- no threshold tuning on final holdout.

### 13.2. Сравнение

Сравнивать:

- v10 baseline;
- v11 economic-only;
- v11 historical-only;
- full indicator-score-v2;
- без trend;
- без events;
- без contagion;
- без dependency correction;
- simple base-rate/naive baselines.

### 13.3. Promotion gate

До основной методики новая версия обязана иметь:

- достаточную выборку и независимые positive episodes;
- ненулевой recall;
- Brier/log loss лучше baseline, если выводится вероятность;
- допустимый false-alert rate;
- стабильность соседних порогов и весов;
- region/crisis holdout;
- не ухудшать coverage safety;
- не создавать ложный stable;
- документированный result manifest.

Неуспешная модель остаётся shadow и доступна для аудита. Никаких выдуманных процентов.

## 14. Тестирование и quality gates

### Unit/property

- direction и threshold boundaries;
- economic/historical/effective bands;
- agreement states;
- profile weights;
- signed OI;
- dependency dedup;
- stage matrix;
- hysteresis/recovery;
- event decay;
- coverage/news coverage;
- localization completeness.

### Integration/contract

- каждый официальный adapter на fixtures;
- live contract отдельной opt-in командой;
- DB migrations twice;
- old snapshot compatibility;
- SQLite basic profile;
- PostgreSQL advanced profile;
- agent evidence allowlist;
- Telegram outbox/retry/dedup.

### Chaos/security

- источник timeout/429/schema drift;
- массовая потеря источников;
- news blackout;
- DB lock/disk full simulation;
- queue retry/restart;
- malicious XML/HTML/JSON;
- SSRF/redirect/oversized payload;
- prompt injection;
- secret scan и dependency audit.

### Browser E2E

Добавить настоящий authenticated Playwright E2E, а не только поиск строк в JS:

- RU/EN всего приложения;
- три уровня радара;
- внутренние вкладки;
- help dialogs;
- click-to-expand;
- signed OI states;
- insufficient data;
- recovery;
- scenario filtering;
- exposure overlay;
- mobile and desktop;
- API failure/degraded UI.

### Coverage gates

- overall не ниже 80%;
- crisis core не ниже 90%;
- auth/config/main/jobs/migrations не ниже 80%;
- PostgreSQL memory не ниже 85% до объявления advanced-профиля готовым;
- CI threshold не оставлять на 30%.

## 15. Self-hosted product и распространение репозитория

### 15.1. Профили установки

1. `basic-local`: SQLite, Telegram, Mini App, deterministic radar, без pgvector.
2. `advanced-local`: PostgreSQL/pgvector и embeddings.
3. `server`: systemd, Caddy или именованный Cloudflare Tunnel, backups, monitoring.

### 15.2. Setup/doctor

Добавить безопасные команды:

- install/bootstrap;
- config validation;
- `doctor` без вывода секретов;
- migrate/dry-run;
- source contract check;
- backup/restore verify;
- start/stop/status;
- update/rollback;
- model/embedding optional checks.

Покупатель получает понятный RU/EN setup guide. `.env.example` объясняет назначение каждого ключа, но никогда не содержит реальные секреты.

### 15.3. Данные и лицензии

- не распространять скачанные proprietary datasets;
- пользователь загружает данные в свою БД;
- документировать terms и attribution;
- сделать source registry конфигурируемым;
- платные источники не обязательны;
- отсутствие конкретного feed приводит к честной деградации функции.

## 16. Production hardening

### 16.1. Постоянный публичный контур

- убрать зависимость от случайного `trycloudflare.com`;
- поддержать собственный домен+Caddy либо именованный Cloudflare Tunnel как systemd service;
- preflight DNS/firewall/HTTPS;
- обновлять Telegram Web App URL только после внешнего readiness;
- документировать конфигурацию для каждого покупателя.

### 16.2. Observability

Собирать и алертить:

- live/ready;
- snapshot lag;
- numeric/news coverage;
- source failures/schema drift;
- stage и false-stable invariant;
- queue depth/retries/duplicates;
- alert delivery;
- backup age/checksum;
- disk growth;
- process restarts;
- agent grounding rejection;
- pgvector parity/queue при advanced profile.

### 16.3. Backups

- verified online backup;
- retention;
- encrypted off-host copy;
- monthly restore drill;
- documented RPO/RTO;
- PostgreSQL backup для advanced profile;
- никакого удаления единственной копии.

### 16.4. Настоящий 14-дневный canary

Canary проверяет не только HTTP health, но и все перечисленные operational metrics. Хранится manifest с start/end, release, methodology, restart intervals и incidents. Перезапуск не позволяет потерять историю наблюдения. Promotion требует 14 последовательных календарных дней без:

- ложного `stable` из-за данных;
- дублированных critical alerts;
- невыявленного stale snapshot;
- необнаруженного source blackout;
- проваленного backup;
- high/critical security incident.

Будущий календарный период нельзя объявлять пройденным заранее.

## 17. Порядок реализации

```text
0 baseline/backup
→ 1 contracts/docs/naming registry
→ 2 indicator-score-v2
→ 3 dependency/group/stage v2
→ 4 signed OI
→ 5 threshold registry v11
→ 6 numeric depth
→ 7 news coverage/lifecycle
→ 8 evidence memory profiles
→ 9 scenarios/recovery/diff
→ 10 opportunities/exposure/scorecard
→ 11 UI/help/navigation
→ 12 replay/calibration
→ 13 packaging/E2E/security
→ 14 server rollout/canary
```

После каждого этапа:

1. выполнить релевантные тесты;
2. исправить регрессии;
3. обновить progress log;
4. сохранить evidence/manifest;
5. автоматически перейти к следующему этапу.

Остановка допустима только при реальном блокере: необходим отсутствующий секрет, требуется необратимое внешнее действие, нарушаются terms источника или невозможно безопасно сохранить production-данные. Даже тогда все независимые работы продолжаются.

## 18. Definition of Done

- [x] Документация точно совпадает с исполняемыми формулами; машинный runtime
  contract сравнивается с константами ядра в CI.
- [x] Indicator score v2 работает параллельно с v10 и имеет полный lineage.
- [x] Видны economic/historical/effective bands и agreement.
- [x] Общая стадия использует intensity + independent systemic breadth.
- [x] Dependency clusters исключают двойной счёт.
- [x] Signed OI различает build и liquidation unwind.
- [x] Threshold v11 metadata полна и immutable.
- [x] Все user-facing сущности имеют RU/EN имя и раскрываемое объяснение.
- [ ] Ключевые сценарии имеют глубокие независимые каналы.
- [x] News coverage отделено от numeric coverage.
- [x] Event decay пересчитывается на каждый snapshot.
- [x] После news sync fusion обновляется без часовой задержки.
- [x] Basic профиль не требует pgvector.
- [x] Advanced pgvector профиль реально интегрирован и протестирован.
- [x] Crisis Playbook, recovery и causal diff работают.
- [x] Exposure overlay не изменяет сделки.
- [x] Live scorecard измеряет полезность сигналов.
- [x] Authenticated browser E2E проходит в CI.
- [x] Critical module coverage gates подняты.
- [ ] Постоянный HTTPS не зависит от quick tunnel.
- [ ] Off-host backup и restore drill проверены.
- [ ] Radar-specific canary реально прошёл 14 дней.
- [x] Новая методика не продвигается без replay/live evidence.
- [x] Probability остаётся `null`, пока calibration gate не пройден.

## 19. Честное ограничение

Даже выполненный план не гарантирует кризис, точную дату или прибыль. Цель — быстрее ручного наблюдения обнаруживать подтверждённое ухудшение, не путать корреляцию с независимым подтверждением, показывать происхождение вывода и измерять реальную полезность на истории и в live. Если новая сложная формула не побеждает простой baseline, production обязан сохранить более простой и честный вариант.
