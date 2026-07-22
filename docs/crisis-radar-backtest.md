# Crisis Radar walk-forward calibration

`walk-forward-v1` answers a narrow question: given an existing deterministic scenario score at a historical timestamp, how often did the labeled event begin inside a fixed forward horizon?

The score is an input signal between 0 and 1. It is **not** a probability. A historical probability is returned only after sufficient earlier, fully resolved samples exist and the calibrated model demonstrably beats its walk-forward base-rate baseline. Historical calibration is never presented as a current live forecast.

## Official catalog and replay path

Schema v14 adds an auditable path that does not require a hand-built signal file:

```text
versioned official event catalog
        +
as-of observations available at timestamp T
        ↓
historical-replay-v1 deterministic score timeline
        ↓
right-censored onset samples
        ↓
walk-forward-v1 calibration and baseline comparison
```

Each catalog version is immutable by SHA-256 checksum and records its source, formal definition, coverage, limitations, date precision, and labels. Every replay signal stores the exact observation IDs and an input checksum. The replay keeps its own threshold-state history and never reads or writes live market snapshots or alert outboxes.

The bundled reviewed official catalogs are:

- World Bank global recession years: five annual episodes since 1960;
- OFR systemic financial-stress proxy: merged ±28-day windows around Appendix B policy interventions;
- World Bank oil-stagflation era: one broad annual episode, intentionally insufficient for a probability;
- crypto leverage unwind and China hard landing: explicit empty official-source-gap catalogs until a defensible primary-source label definition is adopted.

An additional immutable `bybit-derived-v1` research catalog can be generated from saved BTC/ETH data with `scripts.crisis_radar derive-labels`. It is labeled `derived`, keeps its own checksums and limitations, and does not replace or masquerade as the official-source-gap catalog.

## Input contract

```json
{
  "methodology": {
    "code": "crisis-radar",
    "version": "starter-v8"
  },
  "scenario_code": "global_recession",
  "horizon_days": 90,
  "signals": [
    {"as_of": "2000-01-01T00:00:00+00:00", "score": "0.15"},
    {"as_of": "2000-02-01T00:00:00+00:00", "score": "0.35"}
  ],
  "events": [
    {
      "started_at": "2000-03-15T00:00:00+00:00",
      "ended_at": "2000-11-01T00:00:00+00:00"
    }
  ],
  "calibration": {
    "bin_count": 5,
    "min_training_samples": 20,
    "min_bin_samples": 5,
    "min_positive_samples": 2,
    "min_negative_samples": 2,
    "prior_strength": "4",
    "decision_threshold": "0.5"
  }
}
```

All timestamps must include a timezone. One file represents one scenario and one horizon. Signal timestamps must be unique. Events are retrospective labels and do not enter the deterministic live calculation.

## Leakage guard

For a prediction at time `T`, a previous sample is eligible for training only when its `horizon_end <= T`. A 90-day prediction made 30 days ago therefore cannot train the next prediction, even though its retrospective outcome is known to the backtest runner.

Historical observations are selected by both `observed_at <= T` and `released_at <= T`. For revised observations, the latest vintage released by `T` is selected. `fetched_at` is not used as historical availability because an official vintage may be downloaded years later during dataset construction.

Every persisted prediction records `latest_training_horizon_end`. This makes the leakage rule independently auditable.

## Calibration and metrics

- Score bins use empirical event frequencies with a pooled prior.
- Adjacent bins are monotonically pooled so a higher deterministic score cannot receive a lower fitted event rate solely from small-sample noise.
- A probability stays unavailable until total history, both outcome classes, at least three independent positive episodes, and the current score bin meet their configured minimums.
- Brier score is compared with a walk-forward historical-base-rate baseline.
- Log loss, precision, recall, false-alert rate, mean lead time, coverage, and a calibration curve are persisted.
- The display gate additionally requires a strictly better Brier score than the baseline and non-zero recall. A result equal to the baseline remains unavailable.

`probability: null` and `confidence: insufficient` are valid results. They mean the program does not yet have enough resolved evidence to display a percentage.

## Commands

```bash
python -m scripts.backtest_crisis_radar --input /absolute/private/input.json --dry-run
python -m scripts.crisis_radar migrate
python -m scripts.backtest_crisis_radar --input /absolute/private/input.json

# Build deterministic historical signals directly from saved as-of observations.
python -m scripts.replay_crisis_radar \
  --scenario financial_stress \
  --from 1998-08-26 \
  --through 2016-09-01 \
  --cadence-days 7 \
  --horizon-days 30 \
  --minimum-coverage 0.25 \
  --dry-run

# After inspecting the dry-run, omit --dry-run to persist replay lineage.
python -m scripts.replay_crisis_radar \
  --scenario financial_stress \
  --from 1998-08-26 \
  --through 2016-09-01 \
  --cadence-days 7 \
  --horizon-days 30 \
  --minimum-coverage 0.25
```

Research-only crypto replay selects the derived catalog explicitly:

```bash
python -m scripts.replay_crisis_radar \
  --scenario crypto_leverage_unwind \
  --from 2020-08-12 \
  --through 2026-07-20 \
  --cadence-days 7 \
  --horizon-days 15 \
  --catalog-version bybit-derived-v1-20260721-e104b9ebfbcd \
  --minimum-coverage 0.75
```

The first command validates and calculates without writing. The persisted command requires the current database schema and a bootstrapped methodology/scenario catalog.

For FRED/ALFRED history, the backfill command requests original-release observations where the API supports them. Current retrospectively revised history is tagged and excluded from strict replay:

```bash
python -m scripts.crisis_radar backfill \
  --source fred \
  --from 1990-01-01 \
  --through 2026-07-21
```

The API exposes official catalog and lineage separately:

- `GET /api/crisis-radar/scenarios/{code}/event-catalog`
- `GET /api/crisis-radar/replays/{run_id}`
- `GET /api/crisis-radar/backtests/{run_id}`
- `GET /api/crisis-radar/scenarios/{code}/calibration`

The last endpoint always keeps top-level live `probability` at `null` until a fitted historical model is explicitly and reproducibly applied to a current live score. A poor or sparse historical run is returned for audit with an explanatory reason, never silently promoted to a forecast.

The verified 15-day and 30-day crypto replays both kept the percentage unavailable: five independent derived episodes were present, but the fitted calibration did not beat the walk-forward base-rate baseline and recall remained zero. This is the intended fail-closed result, not a missing implementation.
