# Crisis Radar model card

Дата: 2026-08-13.

## Назначение

Детерминированная self-hosted система глобального наблюдения и раннего
предупреждения. Не торговая стратегия, не автоматическое исполнение и не гарантия
кризиса, даты или прибыли.

## Активные версии

- основной live baseline: `candidate-v10`;
- shadow methodology: `candidate-v11`;
- replay-only depth methodology: `candidate-v12`;
- replay-only scenario-coverage methodology: `candidate-v13`;
- disabled BIS depth collection methodology: `candidate-v14`;
- indicator scoring: `indicator-score-v2-seed-1`;
- stage: `independent-stage-v2-seed-1`;
- dependency graph: `dependency-graph-v2-seed-1`;
- playbook: `crisis-playbook-v2-seed-1`;
- v10 replay: `historical-replay-v1`;
- v11 comparison replay: `causal-v11-replay-v1`.
- v12 comparison replay: `causal-v12-replay-v1`.
- v13 comparison replay: `causal-v13-scenario-replay-v1`.

## Входы и выходы

Входы: causally available numeric observations, thresholds, vintages, official
news/evidence, source health and open-trade exposure.

Проверенный repository official-news registry содержит 14 каналов:
13 RSS (Fed, ECB, SEC, CFTC, BIS, BOJ, RBI, BoE, BoC, FDIC, NBS China,
Bank of Korea и OFAC) и официальный JSON API HKMA. HKMA добавляет Hong
Kong/Greater China banking/liquidity evidence, NBS — официальные китайские
макрорелизы, Bank of Korea — KOR growth/banking/external-balance evidence,
OFAC — sanctions events. GDELT остаётся discovery-only и не подтверждает событие
самостоятельно. Production release остаётся на предыдущих 12 каналах до rollout.

Выходы:

- v10 primary market stage;
- v11 shadow stage, intensity and systemic breadth;
- economic/historical/effective bands and agreement;
- scenario strength/reliability/playbook/recovery;
- numeric/news coverage;
- conditional opportunity map;
- read-only exposure overlay;
- live scorecard;
- probability только после promotion gate.

## Числовой движок

LLM не определяет score, stage, threshold, probability или expected return.
`indicator-score-v2` объединяет economic level, causal historical anomaly,
Theil–Sen trend, acceleration, persistence and regime through a versioned
frequency profile, then applies availability and data quality. Group/stage logic
deduplicates correlated subchannels and clusters.

Точные формулы и seed weights приведены в
[`crisis-radar-guide.md`](crisis-radar-guide.md).

## Evidence и LLM

Локальный/подключённый агент может объяснить только сохранённый deterministic
result. Каждый факт должен ссылаться на allowlisted relational evidence ID.
Vector similarity is retrieval, not truth. Prompt-like source text is data and is
never executed.

## Validation status

Выполнен реальный comparative replay для `financial_stress` на каталоге OFR:

- period: 1998-08-26 — 2016-09-01;
- cadence: 90 days;
- variants: v10, economic-only, historical-only, full, no-trend, no-events,
  no-contagion, no-dependency, naive base rate;
- candidate-v11 eligible signals: 0 because historical numeric coverage failed
  the fail-closed gate;
- promotion: failed (`insufficient_resolved_samples`);
- live probability: `null`;
- manifest checksum:
  `66187057a90d204786af06a658b5ea4c420e694baca3dbaa69641a67b3621aaf`.

Это означает: код причинного replay работает, но прогностическое преимущество v11
не доказано. v11 остаётся shadow; v10 не переписан.

Отдельный `candidate-v12` добавляет десять причинно доступных рядов рынка труда,
кредита, CRE/жилья, долларовой ликвидности, NASDAQ и энергоносителей. Он имеет
собственные immutable-пороги, RU/EN metadata и dependency assignments, но новые
индикаторы остаются выключенными для live. Replay `financial_stress` за
1998-08-26—2016-09-01 проверил 220 месячных cutoff после causal backfill всех
проверенных FRED-рядов: использовалось от 4 до 32 доступных входов, numeric
coverage составил только 4,88–37,80%, поэтому все 220
точек получили `insufficient_data`, promotion не пройден и probability=`null`.

`candidate-v13` не меняет live-расчёт. Он исправляет исследовательский denominator:
для `financial_stress` покрытие считается по фиксированным сценарным группам, а
не по всем глобальным индикаторам. Одна группа является одной единицей покрытия;
несколько коррелированных рядов одной группы не увеличивают denominator и не
создают ложные независимые подтверждения. Допуск требует одновременно:

- scenario coverage не ниже `0.70` (`0.85` считается healthy);
- кредитный, рыночно-ценовой и funding/liquidity каналы;
- США, минимум два региона other-advanced и минимум два emerging-региона.

Глобальное покрытие вычисляется отдельно и не скрывается. Реальный причинный
replay проверил 220 месячных cutoff: 29 прошли scenario gate, 20 имели разрешимый
исход и содержали только три независимых положительных OFR-эпизода. Этого
недостаточно для калибратора: `scored_count=0`, promotion=`false`,
probability=`null`. Sensitivity при coverage 0.70/0.75/0.80/0.85 и горизонтах
15/30/90 дней также не прошла promotion. Это улучшение честности выборки, а не
доказательство прогностического преимущества.

`candidate-v14` добавляет двадцать отключённых официальных BIS-входов для десяти
экономик: отклонение DSR частного нефинансового сектора от предшествующего
60-квартального среднего и реальное годовое изменение цен на жильё. Архивные
контракты проверяют точное имя файла, schema/dimensions, единицы, свободный
normal-status, дубликаты, будущие периоды и размер распаковки. Текущий bulk-файл
является текущей ревизией и не предоставляет точный historical release vintage,
поэтому импортированная история помечается `release_time_estimated`, не считается
causal replay evidence и не даёт права включить v14 в live. Регулярный сбор может
накапливать point-in-time историю с момента подключения.

## Known limitations

- causal history has insufficient breadth/depth for v11 promotion;
- candidate-v13 has only three independent positive financial-stress episodes;
  it is replay-only and cannot emit a calibrated probability;
- candidate-v14 BIS depth inputs are disabled; current bulk history lacks exact
  historical release vintages and cannot be used as causal replay evidence;
- global regions have unequal channel depth;
- ten additional official FRED depth series are disabled live inputs with
  explicit thresholds only inside immutable replay-only `candidate-v12`; they
  cannot affect the live stage until that methodology passes replay, sensitivity
  and canary gates; their
  collection failures are isolated from required FRED source health; an
  isolated ALFRED backfill now provides 15,619 causal initial-release points
  across all ten series, but this improves evidence availability rather than
  proving predictive advantage or authorizing promotion;
- the licensed FRED S&P 500 series rejects the vintage contract and is explicitly
  live-only, so it cannot contribute historical replay evidence;
- 13 repository official-news channels do not constitute exhaustive world-news coverage;
- events and contagion currently do not change numeric v11 stage, so their numeric
  ablation delta is expected to be zero;
- scorecard MFE/MAE is unavailable without validated asset histories;
- TradFi/options capability degrades when no permitted free quote feed exists;
- rare events, revisions and regime change limit historical generalization;
- causal replay rejects `retrospective_revised` rows and late historical imports
  whose release time was only estimated; recent live estimated-release rows remain
  eligible only inside the indicator's maximum staleness window;
- equal numerical values from different vintages are preserved as separate
  observations and revision links are rebuilt in chronological release order;
- 14-day production canary must run after each release and cannot be pre-declared.

## Prohibited claims and uses

- a precise crisis date;
- guaranteed profit or loss range;
- auto-created or auto-executed trades;
- probability when calibration gate failed;
- a single headline or vector match treated as a crisis;
- `stable` when mandatory data coverage is insufficient;
- calling candidate-v11 production-primary before replay and canary pass.
- calling candidate-v12 live or promoted while its coverage gate fails.
- calling candidate-v13 live or promoted while calibration and holdout gates fail.
- calling candidate-v14 live or promoted before point-in-time replay and canary.

## Monitoring and rollback

The radar-specific canary checks HTTP readiness, snapshot lag, false-stable,
numeric/news coverage, source failures, queue growth, backup integrity/age and disk
growth. Manifest continuity survives process restarts. A release needs at least
1210 samples over 14 days with no critical incident. Repeated samples of the same
active incident do not create duplicates: the manifest records opened, active,
resolved and reopened transitions while retaining every unique critical opening.
Rollback keeps the previous immutable methodology and database backup; it never
rewrites prior snapshots.
