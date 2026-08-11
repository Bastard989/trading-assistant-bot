# Crisis Radar: руководство владельца и техническая методика

Версия документа: 2026-08-11.

Основной live-вывод: `candidate-v10`. Исследовательский shadow-расчёт:
`candidate-v11`, `indicator-score-v2-seed-1`, `independent-stage-v2-seed-1`.
Машинно проверяемая копия всех исполняемых seed-порогов и весов находится в
`docs/crisis-radar-v2-runtime-contract.json`. CI сравнивает её непосредственно
с константами вычислительного ядра.

Этот документ описывает исполняемый код. Если документ и код расходятся,
автоматический consistency test должен упасть, а кандидатная методика не может
быть продвинута.

## 1. Коротко для человека без технической подготовки

### Что делает радар

Crisis Radar собирает числовые показатели и официальные публикации, сохраняет их
происхождение и ищет совместное ухудшение независимых частей мировой системы:
экономики, труда, кредита, банков, ликвидности, рынков, сырья, жилья,
суверенного риска и криптоплечей.

Радар не предсказывает точную дату кризиса и не гарантирует прибыль. Он отвечает
на более проверяемые вопросы:

- где уже есть напряжение;
- насколько оно сильное;
- насколько широко оно распространяется;
- какие подтверждения появились и каких не хватает;
- что отменит сценарий;
- какие позиции владельца конфликтуют с ним;
- насколько свежи и полны данные.

### Что смотреть каждый день

1. **Стадия рынка.** Основная стадия v10 остаётся рабочим production-сигналом.
2. **Сила напряжения и ширина заражения.** Это две оси v11 shadow. Они объясняют,
   сильны ли ухудшения и сколько независимых систем они затронули.
3. **Покрытие данных.** `Недостаточно данных` важнее красивой зелёной карточки:
   при провале gate радар не имеет права показывать ложное `стабильно`.
4. **Что изменилось за 24 часа, 7 и 15 дней.** Пустое значение означает, что
   сохранённой истории пока недостаточно, а не отсутствие изменений в мире.
5. **Сценарий и цепочка причин.** Откройте карточку: там есть подтверждения,
   следующие звенья, инвалидация, recovery и ограничения.
6. **События и источники.** Новость является доказательным контекстом. Один
   заголовок не может сам объявить кризис.
7. **Возможности и позиции.** Это read-only анализ `long/short/hedge/wait`; ордер,
   стоп, цель и объём радар не меняет.

### Как понимать числа

- **Сила сценария 0–100** — выраженность текущих подтверждений, не вероятность.
- **Интенсивность 0–100** — сила стрессов в активных независимых кластерах.
- **Системная ширина 0–100** — распространение по кластерам, регионам и anchor-классам.
- **Вероятность** показывается только после причинной исторической калибровки.
  Сейчас live probability равна `null`.
- **Надёжность** описывает покрытие и качество данных, а не шанс кризиса.

### Почему v10 и v11 показываются одновременно

v10 — более простой пороговый монитор с накопленным рабочим поведением. v11
добавляет историческую аномальность, тренд, ускорение, persistence, режимы и
коррекцию зависимостей. Сложность сама по себе не является преимуществом, поэтому
v11 остаётся shadow до победы над baseline на replay и live canary.

Реальный financial-stress replay 2026-08-05 обнаружил нулевое число v11-точек с
достаточным историческим покрытием. Это не успех модели и не провал идеи радара:
это честный отказ promotion gate. Нужно накопить/загрузить причинно корректную
историю всех обязательных каналов. Процент не создаётся.

## 2. Пороговые ориентиры

Экономический порог — объяснимая граница наблюдения, а не магическое
предсказание. Значения v11 имеют metadata, rationale, source URL, profile,
operational role, checksum и статус `candidate`. Персональные overlays не
переписывают системную историю.

| Показатель | Warning | Danger | Critical | Контекст |
|---|---:|---:|---:|---|
| Sahm Rule, п.п. | 0,25 | 0,50 | 1,00 | 0,50 — официальный рецессионный триггер |
| US HY OAS, % | 4,5 | 6,0 | 8,0 | важна также скорость расширения |
| US IG OAS, % | 2,0 | 3,0 | 5,0 | отдельный кредитный подканал |
| VIX | 25 | 30 | 40 | одиночный всплеск не равен системному кризису |
| S&P 500 drawdown | −10% | −20% | −30% | сравнивается с breadth/credit/liquidity |
| 10Y–2Y | 0 | −0,5 | −1,0 | важна цепочка инверсия → распрямление |
| NFCI | 0,0 | 0,5 | 1,0 | ужесточение финансовых условий |
| STLFSI | 0,0 | 1,0 | 2,5 | недельный официальный stress proxy |
| Повторные claims США | 1,9 млн | 2,5 млн | 4,0 млн | ухудшение труда |
| Payrolls, изменение | +100 тыс. | 0 | −300 тыс. | monthly confirmation |
| US bank deposits, 90d | −1% | −3% | −6% | устойчивость банковского фондирования |
| Primary credit ФРС | $1 млрд | $10 млрд | $50 млрд | emergency borrowing proxy |
| Housing permits, 90d | −5% | −15% | −30% | опережающий housing-канал |
| Broad USD, 30d | +3% | +6% | +10% | глобальное долларовое давление |
| Euro CISS | 0,20 | 0,35 | 0,55 | системный стресс еврозоны |
| G20/China CLI, 6m | −0,2 | −0,6 | −1,2 | направление важнее уровня 100 |
| Funding BTC/ETH | ±0,05% | ±0,10% | ±0,20% | знак сопоставляется с ценой и OI |
| Signed OI BTC/ETH, 7d | 20% | 35% | 60% | различает build и unwind в v11 |
| BTC/ETH drawdown, 30d | −15% | −25% | −40% | дополняется OI, funding и volatility |

Полный набор точных порогов доступен в UI «Методика» и в immutable registry. В
таблице выше приведены ориентиры, а не замена реестра.

## 3. Причинность и происхождение данных

Для snapshot с временем `cutoff` допускаются только строки:

```text
observed_at <= cutoff
released_at <= cutoff
```

Replay исключает `retrospective_revised`. Каждая точка хранит source, vintage,
observed/released/fetched time и quality flags. Будущий релиз не может изменить
прошлый replay-сигнал — это покрыто regression test.

Просроченное значение не становится нейтральным: availability обнуляет его вклад,
а coverage gate может вернуть `insufficient_data`.

## 4. Основная методика v10

v10 переводит свежий уровень между `reference → warning → danger → critical` в
`stress_score 0..1` линейной интерполяцией. Полосы:

```text
normal    0,00 <= score < 0,25
warning   0,25 <= score < 0,50
danger    0,50 <= score < 0,75
critical  0,75 <= score <= 1,00
```

После raw band применяются confirmation points и recovery margin. Critical не
задерживается. Monthly/quarterly/annual ряды подтверждаются одной новой точкой;
более быстрые не-критические переходы требуют устойчивости.

Группа v10:

```text
group_score = 0,70 × strongest_indicator_score
            + 0,30 × mean_indicator_score
```

Стадия v10:

```text
crisis       critical groups >= 3
confirmation critical groups >= 2 OR danger groups >= 3
warning      danger groups >= 2 OR warning groups >= 3
tension      warning groups >= 1
stable       иначе
```

При провале coverage вычисленная стадия сохраняется для аудита, но пользователю
показывается `insufficient_data`.

## 5. Indicator score v2 (candidate-v11 shadow)

Для каждого индикатора отдельно сохраняются:

```text
economic_score / economic_band
historical_score / historical_band
trend_score
acceleration_score
persistence_score
regime_score
data_quality
availability
effective_score / effective_band
agreement
history_count
lineage + input_checksum
```

Общий контракт:

```text
effective = availability × data_quality × weighted_profile(
    economic, historical, trend, acceleration, persistence, regime
)
```

Доступность умножает результат на `1,00` для fresh и на `0,70` для delayed.
Для stale/missing итоговый score отсутствует. Полосы score заданы точно:
`normal < 0,25`, `warning ≥ 0,25`, `danger ≥ 0,50`,
`critical ≥ 0,75`.

Профили и seed-веса:

| Profile | Econ | Hist | Trend | Accel | Persist | Regime | Min history |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_daily | .25 | .20 | .20 | .10 | .10 | .15 | 252 |
| market_intraday | .15 | .20 | .15 | .15 | .10 | .25 | 500 |
| flow_weekly | .30 | .15 | .20 | .10 | .15 | .10 | 104 |
| macro_monthly | .35 | .15 | .20 | .10 | .15 | .05 | 60 |
| macro_quarterly | .45 | .15 | .15 | .05 | .15 | .05 | 24 |
| structural_annual | .55 | .15 | .10 | .05 | .10 | .05 | 15 |
| two_sided_leverage | .15 | .20 | .15 | .15 | .10 | .25 | 252 |
| event_reactive | .25 | .10 | .15 | .10 | .10 | .30 | 30 |

Если history gate не пройден, historical component отсутствует и разрешённая
ренормализация фиксируется в lineage. Critical economic level не понижается
только из-за спокойной короткой истории.

Historical anomaly использует кусочно-линейные узлы adverse percentile
`0,80 / 0,95 / 0,99`. Направленный robust MAD z-score преобразуется точно как
`clamp(oriented_mad_z / 3, 0, 1)`; итог исторической аномалии — максимум
percentile- и z-компонентов. При отсутствии надёжной volatility trend для
процентного изменения нормируется на `20`; при наличии volatility изменение
нормируется на `volatility × √observations × 3`. Acceleration нормируется на
`volatility × 3`, persistence — на 10 последовательных наблюдений. Regime
берёт максимум из change point, state machine и volatility state.

Agreement:

- `confirmed_stress` — economic и historical подтверждают ухудшение;
- `early_anomaly` — уровень ещё нормальный, но аномалия/динамика необычны;
- `high_level_stabilizing` — уровень плохой, но динамика успокаивается;
- `mixed` — компоненты расходятся;
- `insufficient_history` / `insufficient_data` — честная нехватка входов.

## 6. Dependency graph, группы и стадия v11

Индикаторы объединяются в независимые subchannels, группы и кластеры. Один ряд не
может дважды занять top-two. Коррелированные группы внутри кластера дают один
cluster score.

```text
group_raw = 0,35 × central tendency
          + 0,30 × mean(top two independent subchannels)
          + 0,20 × stressed subchannel breadth
          + 0,15 × dynamics
```

Текущий исполняемый seed пока не применяет отдельные multiplicative
quality/dependency penalties внутри group formula: качество уже входит в
indicator effective score, а dependency correction выполняется через subchannel
и cluster dedup. Это кандидатное ограничение явно проверяется ablation.

Интенсивность:

```text
0,60 × mean(active cluster scores)
+ 0,40 × mean(top two active cluster scores)
```

Ширина:

```text
100 × (
  0,50 × active clusters / eligible clusters
  + 0,25 × active regions / eligible regions
  + 0,25 × active anchor classes / 3
)
```

Seed-матрица: tension от 25/20, warning от 40/35 и двух кластеров,
confirmation от 60/50 и трёх кластеров с anchor, crisis от 75/65, четырёх
кластеров, двух регионов и critical anchor. Recovery требует снижения интенсивности
минимум на 15 от подтверждённого пика и breadth ниже 50.

## 7. Signed OI

v10 сохраняет абсолютное 7d-изменение для воспроизводимости. v11 использует signed
1d/7d/30d и сопоставляет знак с ценой, funding и volatility. State machine
различает leverage build, orderly deleveraging, price/OI quadrants и liquidation
unwind. Отсутствующая проверяемая liquidation feed не имитируется.

## 8. Новости, события и память доказательств

Official-first feeds: Fed, ECB, SEC, CFTC, BIS, BOJ, RBI, BoE, BoC, FDIC и HKMA.
Первые десять источников используют официальные RSS; HKMA подключён через
официальный JSON API пресс-релизов и даёт отдельный банковский и ликвидностный
контур Hong Kong/Greater China. GDELT используется только для discovery.
Оригинальный язык и текст сохраняются; перевод производный. Dedupe, URL allowlist,
bounded payload, XML/JSON hardening и prompt-like text defence применяются до
извлечения событий.

Каждый источник имеет отдельный health status. В HKMA-парсере обязательны
`success=true`, `err_code=0000`, согласованный `datasize`, 1–100 записей,
дата `YYYY-MM-DD` и ссылка только на `www.hkma.gov.hk`. Неуспешный ответ,
подмена домена, дубликаты, некорректная схема и публикации из будущего не
попадают в память доказательств. Успешная live-проверка 11 августа 2026 года
подтвердила 11 из 11 настроенных официальных новостных каналов; это проверка
доступности и контракта, а не доказательство полноты всех мировых событий.

```text
current_event_score = severity × source_quality × corroboration
                    × novelty × relevance × time_decay
```

Decay пересчитывается относительно каждого snapshot; half-life зависит от
taxonomy. После news sync fusion пересчитывается сразу. News coverage и numeric
coverage независимы: news blackout не превращает числовую стадию в `stable`, но
понижает event reliability и создаёт operational incident.

Basic profile использует SQLite FTS и relational evidence IDs. Advanced profile
добавляет PostgreSQL/pgvector, continuous ingestion, embedding queue и hybrid
search. Vector match без relational evidence ID не считается фактом.

## 9. Сценарии, recovery, exposure и scorecard

У каждого v11-сценария есть Crisis Playbook: causal chain, anchors, подтверждения,
missing confirmations, invalidation, recovery, уязвимые классы, возможные
beneficiaries, ограничения и evidence IDs.

Exposure overlay читает открытые сделки и показывает conflict/alignment,
концентрацию и leverage vulnerability. Он не имеет методов изменения сделки.

Live scorecard сохраняет first detected/elevated/confirmed, peak strength,
invalidation и attribution. `resolved`/`false_alert` выставляется только для
сценариев с versioned event catalog и после полного горизонта. MFE/MAE остаются
`null`, пока нет валидированной исторической цены соответствующего asset class.

## 10. Replay, ablation и вероятность

Сравниваются v10, economic-only, historical-only, full, without trend, without
events, without contagion, without dependency correction и expanding-window base
rate. Events и contagion сейчас не входят в числовой indicator/stage score, поэтому
их ожидаемый числовой ablation delta равен нулю; manifest фиксирует это явно.

Promotion требует минимум 30 независимых positive episodes, 5 holdout events,
ненулевой recall, приемлемый false-alert rate, Brier лучше base rate, sensitivity,
region/crisis holdout, coverage safety и отсутствие false stable.

Текущий статус:

```text
candidate-v11: shadow
eligible historical financial-stress samples: 0
promotion: failed (insufficient_resolved_samples)
live_probability: null
```

Manifest: `data/reports/crisis-radar-v11-financial-stress-manifest.json`.

## 11. Установка и эксплуатация

Профили: `basic-local`, `advanced-local`, `server`.

```bash
python -m scripts.self_host bootstrap --profile basic-local
python -m scripts.self_host doctor --profile basic-local
python -m scripts.self_host migrate-dry-run
python -m scripts.self_host backup-verify --backup data/backups/manual.sqlite3
python -m scripts.self_host restore-drill --backup data/backups/manual.sqlite3
python -m scripts.self_host source-check
python -m scripts.crisis_radar sync --source all
python -m scripts.replay_crisis_radar_v11 --scenario financial_stress \
  --from 1998-08-26 --through 2016-09-01 --cadence-days 90 --horizon-days 90
```

Server profile использует immutable release directories, systemd, Caddy
или named Cloudflare Tunnel, verified age-encrypted off-host backups и
persistent radar canary. Quick tunnel не является production URL. Точные
команды обновления, rollback и restore drill описаны в
`docs/deployment.md` и `docs/backup-and-restore.md`.

Canary должен набрать минимум 1210 успешных пятнадцатиминутных samples за 14
календарных дней. Он проверяет HTTP, snapshot lag, false stable, numeric/news
coverage, source failures, delivery queues, backup checksum/age и disk size.

## 12. Ограничения

- Редкие кризисы дают мало независимых примеров.
- Макроданные выходят с задержкой и пересмотрами.
- Глобальное покрытие широко, но глубина по регионам неодинакова.
- Бесплатного подтверждённого TradFi-options feed нет.
- Bybit options может не найти две ликвидные ноги; тогда идея отсутствует.
- Annual structural context не является быстрым market anchor.
- Кризис может возникнуть из канала, которого нет в бесплатном контуре.
- Даже прошедший replay не гарантирует будущую прибыль.

Радар — система наблюдения и проверки гипотез. Решение о сделке остаётся за
владельцем.
