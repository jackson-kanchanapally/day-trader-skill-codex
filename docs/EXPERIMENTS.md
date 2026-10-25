# Declared NSE/BSE ORB experiments

Optional [spread and liquidity scenarios](LIQUIDITY.md) extend all configurations
with explicit spread costs and a causal candle-volume screen. Defaults retain
the original unscreened execution. Capacity failures cannot erase entered losses.

This Phase 1 increment adds configurable experiments to the existing offline
research lab. It retains the original baseline and shared execution engine.
Production research prioritizes [licensed NSE/BSE history and reference data](DATA_SOURCES.md).
No profitable strategy or live-market validation is claimed.

## Run an explicit experiment set

```sh
python3 -m research walk-forward --db var/history.sqlite \
  --start 2026-09-01 --end 2026-10-23 \
  --as-of 2026-10-23T16:00:00+05:30 --vintage 2026-10-23T16:00:00+05:30 \
  --train-sessions 20 --test-sessions 5 \
  --strategies config/research-strategies.example.json \
  --rr 1.5 2 2.5 --bootstrap-samples 1000 --block-days 5 --seed 17 \
  --out var/research/experiment-001.json
```

Use a populated, authorized historical store with dated universe metadata.
The command performs no data downloads. The supplied example contains seven
candidate configurations plus the automatic baseline: 24 strategy/RR
combinations with these three R/R values, each evaluated under cost stress.
The example is a schema/demo parameter set, not a recommendation or validated
threshold selection.

`--strategies` accepts an explicit JSON array, not a Cartesian parameter search.
The original 15-minute range-low strategy is always included as `baseline`;
its name and parameters cannot be replaced. Renamed duplicate parameter sets
are rejected. Numeric/stop spelling differences are normalized. At most 12
strategies including the baseline and 24 strategy/RR combinations are allowed
before cost stress. Existing calls without this option retain baseline trades.
Every configuration is reported; no test-driven winner is selected.

## Configuration fields

All fields except `name` are optional. Unknown keys and unsupported values fail.

| Field | Default | Semantics |
| --- | --- | --- |
| `name` | required | Unique readable identifier; `baseline` is reserved |
| `or_minutes` | 15 | 5, 15 or 30; range consists of completed bars from 09:15 |
| `confirmation_bps` | 0 | Completed close must exceed range high and meet this additional basis-point distance |
| `require_vwap` | false | Close at/above current-session HLC3/volume VWAP approximation |
| `ema_fast`, `ema_slow` | null | Both set or neither; fast < slow <= 72; close >= fast > slow |
| `min_rvol` | null | Minimum prior-session normalized volume; missing samples fail the filter |
| `rvol_mode` | same_time | same_time, opening_5m, opening_15m or cumulative |
| `rvol_lookback` | 20 | Prior scheduled sessions, at most 252 |
| `rvol_min_samples` | 5 | Required valid comparison windows, <= lookback |
| `min_atr_pct`, `max_atr_pct` | null | Inclusive ATR/close percentage bounds |
| `stop` | or-low | or-low, or-mid, pct:X or atr:X |
| `entry_start` | 09:15 | Earliest signal confirmation, local IST HH:MM |
| `entry_cutoff` | 15:05 | Latest confirmation, inclusive; must precede flattening |
| `flat_by` | 15:10 | Scheduled exit at the preceding candle's close; must align to 5m |

The research entry clock differs from the paper workflow's default 09:30
entry start. A 5-minute range ends at 09:20, its first post-range candle can
confirm at 09:25, and its default next-open fill can occur then. A 30-minute
range ends at 09:45 and its first post-range confirmation is 09:50. Prices
must actually qualify; no candidate is forced. New short-side or other
strategy families are not introduced here.

EMA and ATR filters reset at the session open. EMA requires at least `ema_slow`
completed session bars. ATR filtering requires 14 completed session bars and
reuses the existing arithmetic average of the last 14 true ranges; the first
bar uses high-low, without an overnight gap. ATR stops retain the existing
14-bar calculation with a three-completed-bar minimum, explicitly recorded
in the plan. These are distinct warmup rules. VWAP is a candle approximation,
not trade-by-trade VWAP. All values stop at the candidate's completed candle.

The original baseline retains `india.analysis.proposal`. Configurable entries
reuse `workflow.strategy.entry`, exits reuse `workflow.strategy.plan`, and
fills/sizing use `india.execution.replay_trade`. Directional rounding now uses
the canonical float-noise-aware tick helper. Raw signal/range prices enter
planning; rounded public display prices do not discard precision.

## Rejections, history and corporate actions

A price breakout can fail one or more filters. The next completed close may
then qualify. The selection policy is first qualified completed close. Once
selected, an invalid plan or a next-open gap/risk failure ends that session's
opportunity; it does not trigger hindsight resizing or a later retry. Signal
selection itself does not require seeing a future candle. Without one, a
selected signal remains unfilled.

Reports expose `decisions[strategy][symbol][date]` with the selected signal,
per-candidate feature values, failed checks, sample counts and warmup reasons.
Partition `rejections` distinguish no breakout, filter rejection, invalid exit
plans and execution/risk rejection. These are research diagnostics, not an
automatic paper ledger or a live signal journal.

RVOL uses only earlier eligible sessions in the frozen dataset, including
training history for later test decisions. Earlier values never consume later
test candles. Early dates with inadequate warmup remain unavailable rather than
borrowing future history. Intraday EMA/VWAP/ATR use current-session data only.

Historical share volumes are expressed in the current session's share units
using explicitly supplied split/bonus factors whose publication was known by
09:15 on that session date. Action revisions are resolved at that publication
cutoff before applying effective dates. Future publications cannot change an
earlier decision. This adjustment affects only RVOL share counts; execution
prices and stored raw observations remain intact. Corporate-action completeness
and publication provenance still require independent validation. No factor is
inferred from a price gap, and no cash-dividend handling is added.

## Baseline comparisons and sensitivity

Every candidate is compared with the unchanged baseline at matching R/R and
slippage assumptions over the same out-of-sample dates. `baseline_comparisons`
reports the daily difference in sums of net R, including zero-trade days, plus
a seeded paired day-block interval. These are independent-account attributions
across symbols, not portfolio returns or per-trade expectancy differences.
The bootstrap computational floors and limitations from the
[research guide](RESEARCH_LAB.md) still apply. No claim of statistical superiority
is made from merely positive observed differences.

`parameter_sensitivity` reports the number of cost/RR scenarios with trades,
positive scenarios and the expectancy range for each declared strategy. It is
a descriptive summary. There is no multiple-testing correction, parameter
optimizer, automatic winner selection or automatic strategy promotion.
Independent stability/data/liquidity review and forward results remain required;
live eligibility and trading stay disabled.

## Scope and validation record

Baseline for this increment: `python3 -m unittest discover -s tests -v` ran
536 tests in 19.844 seconds, no failures/errors, two loopback socket skips.
All earlier working-tree changes are preserved. New tests use original
synthetic NSE/BSE events and dated metadata; no source captures are relabeled.
Full verification results are recorded after the final implementation checks.

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -p 'test_research*.py' -v` | 53 tests passed, 2.251s |
| Final `python3 -m unittest discover -s tests -v` | 562 tests, 21.825s, no failures/errors, 2 skips |
| Fresh public export: `python3 -m unittest discover -s tests -v` | 261 tests, 7.168s, no failures/errors, 2 skips |
| `python3 scripts/build_release.py --check` | PASS: 180 source files, 19 test modules |
| `python3 -m research walk-forward --help` and `git diff --check` | PASS |
| Original synthetic end-to-end example | 8 configurations, 24 cost scenarios, 21 baseline comparisons; explicitly labeled synthetic, execution disabled |

The 26 added tests cover completed-range timing, raw price precision, exact
baseline parity, delayed qualification, EMA/ATR warmup, VWAP rejection, all
supported stops, flatten timing, adverse gaps without retries, causal signal
selection without future candles, RVOL share-unit normalization and publication
cutoffs, strategy validation/search bounds, paired zero-trade-day comparisons,
unchanged earlier training under holdout revisions, NSE/BSE cost/source
separation, and the offline CLI. The two skips are existing HTTP tests blocked
by sandbox loopback restrictions. No frontend code changed. No real-market edge
was measured and no subscription, credential or external order was created.

Added files: `research/strategies.py`, `tests/test_research_strategies.py`,
`config/research-strategies.example.json`, `docs/EXPERIMENTS.md`,
`docs/DATA_SOURCES.md`. Modified for this increment: `research/lab.py`,
`research/metrics.py`, `research/promotion.py`, `research/__main__.py`,
`research/store.py`, `india/analysis.py`, `india/research.py`,
`workflow/strategy.py`, `docs/RESEARCH_LAB.md`, `README.md`,
`release-manifest.json`. Earlier changes are preserved; no commits were pushed.
