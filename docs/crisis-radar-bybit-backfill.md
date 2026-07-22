# Crisis Radar: historical Bybit backfill

The Bybit backfill uses public V5 market endpoints and does not require an API key:

- [funding history](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate): `startTime`/`endTime`, maximum 200 rows; pages move the inclusive end boundary to one millisecond before the oldest row;
- [daily kline](https://bybit-exchange.github.io/docs/v5/market/kline): `start`/`end`, maximum 1000 rows; pages use the same decreasing-end strategy;
- [open interest](https://bybit-exchange.github.io/docs/v5/market/open-interest): `startTime`/`endTime`, daily interval, maximum 200 rows, with `nextPageCursor` pagination.

Only `BTCUSDT` and `ETHUSDT` linear contracts are read. The backfill writes the existing indicators:

- `*_funding_rate`: the final settled funding rate in each UTC day;
- `*_oi_7d_abs_change`: absolute 7-day OI change, calculated from the current or earlier observations only;
- `*_30d_drawdown`: drawdown from the rolling price peak, calculated from closed daily candles only.

It also stores eight disabled research-only series (`*_close_price`, `*_return_7d`, `*_open_interest`, and signed `*_oi_7d_change`). They never enter the live breadth or threshold calculation. A daily candle is considered available only at the following UTC day boundary, so replay cannot see its close before the candle has completed.

OI and kline requests include 8 and 31 days of warm-up data respectively. Warm-up rows are used only to calculate the first requested values and are not written outside the requested date range. This prevents look-ahead while avoiding an artificial empty prefix.

Every history stream has hard limits of 100 pages, 20,000 received rows, 12 MB in aggregate, and 4 MB per HTTP response. Cursor reuse, a non-decreasing time boundary, conflicting duplicate timestamps, malformed payloads, and exhausted retry attempts fail with sanitized errors. The source-health result is recorded in `cr_sync_runs`. The ordinary sync/backfill path never creates event labels.

Run a bounded backfill after migration and bootstrap:

```bash
.venv/bin/python -m scripts.crisis_radar backfill \
  --source bybit \
  --from 2020-01-01 \
  --through 2026-07-20
```

Use repeatable `--indicator` options to limit work, for example:

```bash
.venv/bin/python -m scripts.crisis_radar backfill \
  --source bybit \
  --from 2024-01-01 \
  --through 2026-07-20 \
  --indicator btc_funding_rate \
  --indicator btc_oi_7d_abs_change
```

The date window is limited to ten years and cannot extend into the future. The existing short-window `sync --source bybit` path remains unchanged for normal hourly refreshes.

## Separate derived research catalog

After inspecting a completed history backfill, an explicit command can build an immutable research catalog for `crypto_leverage_unwind`:

```bash
.venv/bin/python -m scripts.crisis_radar derive-labels \
  --through 2026-07-20
```

The frozen `bybit-derived-v1` rule uses only earlier data at every date: an expanding prior-only fifth percentile for BTC 7-day return, an expanding prior-only fifth percentile for signed BTC OI change, and prior leverage buildup (80th-percentile OI or positive prior funding). At least 365 historical records are required, missing inputs are insufficient rather than false, and triggers within seven days are merged. Input and result checksums, coverage, counts, and limitations are persisted.

These labels are `derived`, not official crisis declarations. They are stored alongside—not instead of—the empty official-source-gap catalog and are eligible only for explicitly selected research replay. Calibration and probability display gates remain unchanged.

The verified 2026-07-21 run stored 29,875 rows from the bounded fetch, produced 2,309 daily research records (1,804 sufficient), and identified five derived episodes between 2021-12-05 and 2025-10-16. Endpoint retention limits older OI coverage.

## Live BTC option opportunity vertical

The opportunities API also reads Bybit's public V5 option tickers (`category=option&baseCoin=BTC`). It accepts only puts with positive bid/ask and sizes, 7–60 days to expiry, and strikes between 70% and 100% of the reported underlying. The selector pairs one approximately 90% strike long put with a lower approximately 80% strike short put at the same expiry, uses the actual ask-minus-bid debit, rejects non-positive debit or payoff, and exposes only a defined-risk spread. Maximum loss is the paid debit; maximum gain is strike width minus debit. Results are cached for 60 seconds and failures are bounded.

If two simultaneously quoted liquid legs do not exist, `market_data_status.bybit_options` is `degraded` and no option idea is created. The verified live check on 2026-07-21 returned this honest degraded state. TradFi options expose a separate degraded adapter status until a permitted free live source is configured. Contracts follow Bybit's official [instruments](https://bybit-exchange.github.io/docs/v5/market/instrument) and [option ticker](https://bybit-exchange.github.io/docs/v5/market/tickers) documentation.
