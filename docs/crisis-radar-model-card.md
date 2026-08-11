# Crisis Radar model card

Дата: 2026-08-11.

## Назначение

Детерминированная self-hosted система глобального наблюдения и раннего
предупреждения. Не торговая стратегия, не автоматическое исполнение и не гарантия
кризиса, даты или прибыли.

## Активные версии

- основной live baseline: `candidate-v10`;
- shadow methodology: `candidate-v11`;
- indicator scoring: `indicator-score-v2-seed-1`;
- stage: `independent-stage-v2-seed-1`;
- dependency graph: `dependency-graph-v2-seed-1`;
- playbook: `crisis-playbook-v2-seed-1`;
- v10 replay: `historical-replay-v1`;
- v11 comparison replay: `causal-v11-replay-v1`.

## Входы и выходы

Входы: causally available numeric observations, thresholds, vintages, official
news/evidence, source health and open-trade exposure.

Проверенный official-news registry содержит 12 активных каналов: десять RSS
(Fed, ECB, SEC, CFTC, BIS, BOJ, RBI, BoE, BoC, FDIC), официальный JSON API
HKMA и официальный OFAC subscription topic через GovDelivery RSS. HKMA добавляет
Hong Kong/Greater China banking/liquidity evidence, OFAC — sanctions events;
GDELT остаётся discovery-only и не подтверждает событие самостоятельно.

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

## Known limitations

- causal history has insufficient breadth/depth for v11 promotion;
- global regions have unequal channel depth;
- 12 official-news channels do not constitute exhaustive world-news coverage;
- events and contagion currently do not change numeric v11 stage, so their numeric
  ablation delta is expected to be zero;
- scorecard MFE/MAE is unavailable without validated asset histories;
- TradFi/options capability degrades when no permitted free quote feed exists;
- rare events, revisions and regime change limit historical generalization;
- 14-day production canary must run after each release and cannot be pre-declared.

## Prohibited claims and uses

- a precise crisis date;
- guaranteed profit or loss range;
- auto-created or auto-executed trades;
- probability when calibration gate failed;
- a single headline or vector match treated as a crisis;
- `stable` when mandatory data coverage is insufficient;
- calling candidate-v11 production-primary before replay and canary pass.

## Monitoring and rollback

The radar-specific canary checks HTTP readiness, snapshot lag, false-stable,
numeric/news coverage, source failures, queue growth, backup integrity/age and disk
growth. Manifest continuity survives process restarts. A release needs at least
1210 samples over 14 days with no critical incident. Rollback keeps the previous
immutable methodology and database backup; it never rewrites prior snapshots.
