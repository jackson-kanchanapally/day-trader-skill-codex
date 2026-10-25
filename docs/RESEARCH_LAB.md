# Offline strategy validation laboratory

The Phase 1 foundation provides local historical provenance, dated instrument
eligibility, split/bonus analytical views, and repeatable out-of-sample evaluation
of the established ORB strategy. The [experiment extension](EXPERIMENTS.md) adds
5/15/30-minute ranges and entry filters with an unchanged reference baseline. It extends
`india.research` and uses the shared `india.execution` engine. The existing
`python3 -m india research` command and paper workflows remain available.
The earlier execution-correctness changes in this working tree are preserved.

Baseline for this increment: `python3 -m unittest discover -s tests -v`,
509 tests in 18.990 seconds, no failures/errors, 2 loopback socket skips.

## Local history and provenance

The new package uses SQLite and the Python standard library. No network call,
subscription, order, or background scheduler is started. Import an authorized
single-symbol CSV using the existing
`symbol,interval,time,open,high,low,close,volume` columns and `5m` interval:

```sh
python3 -m research ingest SYMBOL.NS --db var/history.sqlite \
  --csv /path/to/authorized-history.csv --source /path/to/source.json \
  --observed-at 2026-10-23T16:00:00+05:30
```

Use actual symbols, dates and metadata for your dataset. A source JSON object
requires `provider`, `reference`, and `license_note`, all nonempty strings.
The reference identifies the actual export/feed; the license note records the
user's verified permissions. Importing a file does not establish those rights.
The tool hashes the input bytes and normalized candles. A dataset must contain
raw, INR, grid-aligned 09:15-15:30 IST five-minute observations; the strategy
excludes the 15:15 onward auction region. This store currently rejects evening
sessions rather than assigning them the regular clock.

Three clocks remain distinct:

- Candle `time` is market event time, representing its open.
- `observed_at` is the supplied source-vintage time. It must follow candle
  completion and cannot be in the future. The tool does not authenticate it.
- `recorded_at` is when this database actually received that import.

`--vintage` in a research run freezes which observations/revisions are visible.
A later revision cannot change a run using an earlier vintage. An unchanged
observation at a later vintage is retained too, so out-of-order imports cannot
make an intervening revision override it. Conflicting values at the exact same
vintage fail transactionally. Identical repeated imports are idempotent. Earlier
source provenance and versions are retained; selected candles, batches and
instrument/action evidence have integrity checks. These detect accidental
changes, not a malicious party who can rewrite both data and checksums.

Loading a history selection that mixes providers fails. Use separate stores
for competing sources and compare runs explicitly. A source's revised history
may itself contain retrospective corrections. A frozen retrospective snapshot
is reproducible; it is not proof of original historical data availability.

For nightly incremental use, invoke the same bounded one-shot import from your
existing scheduler with an authorized new/overlapping export. Existing records
are never overwritten; overlap can capture revisions. Query a source-vintage
watermark to plan the next requested window:

```sh
python3 -m research watermark SYMBOL.NS --db var/history.sqlite \
  --vintage 2026-10-23T16:00:00+05:30
```

No unattended market-data collection or OS schedule is installed. Credentials,
provider entitlements and requested-versus-delivered history coverage remain
external requirements. Do not rely only on a watermark to detect revisions or
holes before it; overlapping re-imports and complete-session validation matter.

## Point-in-time instrument universe

Import a JSON array of sourced instrument records:

```sh
python3 -m research evidence instrument --db var/history.sqlite \
  --file /path/to/dated-instruments.json --observed-at 2026-10-23T16:00:00+05:30
```

Each record requires exactly:

| Field | Meaning |
| --- | --- |
| `symbol` | Provider symbol ending `.NS` or `.BO` |
| `effective_from`, `effective_to` | Inclusive validity dates, ISO YYYY-MM-DD |
| `published_at` | Explicit timezone-aware publication timestamp |
| `source` | Original source/export reference |
| `tick` | Positive INR tick, normalized by the supplying data pipeline |
| `sector` | Sourced sector string |
| `eligible` | Explicit JSON boolean for the permitted equity universe |

Selection uses the latest publication known by 09:15 on the session date,
within the requested source vintage. Revisions are resolved before applying
validity windows, so a superseded open-ended record cannot resurrect a delisted
instrument. Instruments valid in earlier dates remain in those earlier cohorts;
the current symbol directory is never substituted. An ineligible record is an
explicit exclusion. Missing dated metadata is not inferred from candle files.
Universe completeness and eligibility claims are supplied evidence, not exchange
verification or proof of an unbiased historical cohort. Include delisted names
and retain original directory coverage when preparing real datasets.

## Corporate actions

`research evidence action` accepts an array with exactly `id`, `symbol`, `kind`,
`effective_date`, `published_at`, `source`, `price_factor`. Supported kinds are
`split` and `bonus`; factor must be explicitly supplied, positive and finite.
No ratio or adjustment is inferred from a price gap. Revisions of an action ID
are resolved by publication and source vintage. Cash dividends and other action
types fail explicitly; there is no claim of comprehensive corporate-action data.

`CandleStore.adjusted(...)` exposes a backward analytical view: prices preceding
an effective split/bonus are multiplied by the factor and volume is divided by
it. Actions after the view's end date or outside its vintage do not enter it.
Raw observations remain intact. Execution always uses raw intraday prices and
date-specific ticks; adjusted values must not be fed into the execution engine.
The current fixed rule holds no overnight position and resets its opening range
each day. RVOL experiments now normalize share counts using decision-time-known
split/bonus records; their completeness still needs independent validation.
Cross-session price-indicator experiments remain unsupported.

## Walk-forward evaluation

```sh
python3 -m research walk-forward --db var/history.sqlite \
  --start 2026-09-01 --end 2026-10-23 \
  --as-of 2026-10-23T16:00:00+05:30 --vintage 2026-10-23T16:00:00+05:30 \
  --train-sessions 20 --test-sessions 5 \
  --capital 100000 --risk 500 --risk-fraction 0.005 \
  --rr 2 --bootstrap-samples 1000 --block-days 5 --seed 17 \
  --out var/research/run-001.json
```

This command requires your populated store and enough complete sessions. It
never downloads missing data. End must precede the completed-session cutoff.
Missing eligible dates at either requested boundary, whole missing sessions and
missing candles fail; only explicitly ineligible dates are excluded. Calendar
coverage and exceptional-session restrictions use the existing calendar.

Training windows expand, test windows do not overlap, and an optional
`--embargo-sessions` separates them. Intraday outcomes close within their own
session, so default embargo is zero. A final shorter test window is reported
rather than discarded. Training results never select parameters or stocks for
the test. Earlier training output is invariant to later test-data revisions.
Former test periods can enter later training windows, as would already-known
history in a walk-forward process.

Each symbol/fold/partition starts with its own capital and compounds realized
equity within that partition using the existing evaluation routine. Multiple
symbols are **independent account experiments**. Their pooled R statistics are
not shared-capital returns. Overall cash P&L is a sum of fold attributions;
maximum drawdown is reported per fold and as the worst fold. No misleading
cross-fold compounded-equity curve is exposed.

Sizing, costs, zero-latency default submission, next-open fills, gap handling,
stop-first intrabar ambiguity and flattening use the
[canonical policy](EXECUTION.md). Signals do not use LLM calls. Estimated
slippage is charged once in the cost model. Each experiment repeats the existing
5/10/20 bps default stress scenarios (or the configured baseline and larger
stress levels). Supply verified cost overrides with `--cost-config FILE`.
Optional [spread and liquidity scenarios](LIQUIDITY.md) use `spread_bps` in
that cost file and `--liquidity-config FILE` for a preceding-candle volume cap.
They add no observed quotes or depth data; unresolved exit capacity stays in
marked results and blocks promotion. Legacy defaults remain unscreened.

`--rr 1.5 2 2.5` declares a bounded sensitivity comparison of up to nine values.
Every value is evaluated and reported. There is no winning-parameter picker.
Use `--strategies FILE` for the [declared configuration format](EXPERIMENTS.md);
the baseline is included automatically and the total strategy/RR count is capped at 24.
Inspecting multiple results still creates selection risk; these are conditional
experiments rather than independently validated winners. OR lengths and
VWAP/EMA/RVOL/ATR filters are now supported by the experiment extension;
additional strategy families remain deferred.

## Metrics and uncertainty

Reports contain trade count, win rate, average win/loss, net expectancy in R,
profit factor, realized-equity drawdown, modeled slippage, and grouping by entry hour and sourced
sector. Optional [sourced regime evidence](REGIMES.md) supplies labels frozen
at 09:15 IST under an explicitly selected model; missing context stays `UNKNOWN`.
No market regime is invented from a symbol's future returns.
R divides net P&L by initial cost-inclusive stop risk.
Gap-through-stop losses can exceed 1R.

MAE/MFE are **lower bounds**, calculated from complete held bars before the exit
bar plus entry/exit prices. The exit bar's extrema are excluded because their
ordering relative to an intrabar exit is unknown. They are not tick-path MAE/MFE.
Slippage is modeled, not an observed broker-fill measurement.

The seeded moving-block bootstrap resamples consecutive supplied market days,
keeps all same-day symbol trades together, and includes zero-trade test dates.
It estimates trade-weighted mean net R. The block length remains a configurable
assumption; test block-length sensitivity and regime stability before drawing
conclusions. Optional embargo or eligibility gaps mean adjacency refers to the
supplied evaluation-session sequence, not necessarily every exchange session.
Default bootstrap requires at least two blocks and two traded days to calculate
an interval, and at least ten expected draws per percentile tail. These are
computational floors, **not** evidence of adequate statistical power. Too few
blocks gets an instability warning. No independent-trade or normality assumption
is imposed, but stationarity, dependence and interval calibration remain limits.

Moving-block uncertainty is conditional on the sample. Block length affects
calibration; see [Shao and Politis, 2012](https://arxiv.org/abs/1204.1035).
The implementation uses ordinary percentile intervals, not that paper's
fixed-b calibration. Repeated strategy selection can overfit backtests; see
[Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
This lab does not implement their PBO statistic or a multiple-comparison correction.

## Promotion checklist

```sh
python3 -m research promotion-check --report var/research/run-001.json \
  --state BACKTESTED --policy /path/to/documented-policy.json
```

The known states are
`RESEARCH → BACKTESTED → OOS_VALIDATED → PAPER_TRADING → LIVE_ELIGIBLE`.
This command evaluates the next transition; it never mutates a state or trading
permission. Report contents must match their hash. A policy requires exactly:
`min_market_days`, `min_trade_days`, `max_interval_width_r`,
`max_drawdown_fraction`, `min_positive_fold_fraction`, and a nonempty `rationale`.
Thresholds must be justified for the research question; there is no bundled
arbitrary sample-size rule pretending to establish an edge.

The checklist checks positive net expectancy in every cost/RR scenario,
coverage, fold robustness and drawdown. Beyond RESEARCH it additionally requires
a positive lower uncertainty bound and an interval within the documented width.
Unresolved capacity blocks every transition. BACKTESTED and later states also
require explicit liquidity and positive spread scenarios; see the
[modeling requirements](LIQUIDITY.md).
They also require explicit [regime evidence and complete eligible-session coverage](REGIMES.md).
A failed checklist returns nonzero. It is independent of LLM confidence.
Transitions beyond OOS_VALIDATED remain blocked. Sensitivity summaries now
exist, but independent stability/data/liquidity review and forward paper-result
validation are still required. This run cannot grant LIVE_ELIGIBLE or enable live trading.
A passing research checklist does not authenticate data or prove profitability.

## Reproducibility and remaining Phase 1 work

Reports freeze calendar/settings, point-in-time instrument evidence, source
batch IDs, selected-candle hashes, source vintages, corporate-action records,
bootstrap seed/options, and a fingerprint of research/India/workflow/upstream
Python code. Output files are created exclusively; an existing report is never
overwritten. Re-run the same inputs/vintage to compare `report_sha256`.
This is numerical reproducibility, not production certification.

Remaining Phase 1 capabilities include validating the new capacity/spread scenarios against observed data, broader action coverage,
independent universe/data verification, validation of supplied regime evidence, broader parameter
stability, persisted promotion decisions, and forward-result evaluation. No
market feed, AI desk or live order capability was added. Real positive expectancy
cannot be assessed without suitable authorized historical and forward data.
The production source choice is [licensed NSE/BSE data](DATA_SOURCES.md).

## Validation recorded for this increment

| Command | Result |
| --- | --- |
| Baseline `python3 -m unittest discover -s tests -v` | 509 tests, 18.990s, no failures/errors, 2 skips |
| `python3 -m unittest discover -s tests -p 'test_research*.py' -v` | 27 tests passed, 0.497s |
| `python3 -m unittest discover -s tests -p test_hardening.py -v` | 20 tests passed, 0.082s |
| Final `python3 -m unittest discover -s tests -v` | 536 tests, 19.732s, no failures/errors, 2 skips |
| Fresh public export: `python3 -m unittest discover -s tests -v` | 235 tests, 5.603s, no failures/errors, 2 skips |
| `python3 scripts/build_release.py --check` | PASS: 175 source files, 18 test modules |
| `python3 -m research --help` and `git diff --check` | PASS |

The two skips are existing HTTP tests blocked by sandbox loopback restrictions.
New tests cover transactional/idempotent imports, conflicting and out-of-order
vintages, integrity/schema protection, source isolation, dated eligibility and
action revisions, no leakage from test revisions into earlier training,
independent multi-symbol accounts, bounded sensitivity, complete-session
requirements, day-cluster resampling, excursion bounds, promotion vetoes and
offline CLI handoffs. Synthetic test data derives from the existing fictional
DEMO fixture and original in-memory metadata; no new provider captures or real
profitability results were introduced. No frontend code changed.

Files added for this increment: `research/__init__.py`, `research/__main__.py`,
`research/store.py`, `research/lab.py`, `research/metrics.py`,
`research/promotion.py`, `tests/test_research_store.py`,
`tests/test_research_lab.py`, and this guide. Files modified for this increment:
`india/research.py`, `release-manifest.json`, `README.md`, and `docs/RESEARCH.md`.
Earlier Phase -1/0 working-tree changes remain intact; no commit was pushed.
