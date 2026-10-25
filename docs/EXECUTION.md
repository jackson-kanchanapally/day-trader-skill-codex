# Execution correctness work (2026-10-24)

The subsequent [spread/liquidity extension](LIQUIDITY.md) adds opt-in cash spread
costs and a completed-volume capacity screen. The baseline policy below remains
the default. Capacity-enabled replay fails explicitly on missing post-entry
paths and retains marked exits whose capacity cannot be supported.

Baseline: clean working tree; `python3 -m unittest discover -s tests -v`
ran 477 tests in 20.534 seconds, passed, 2 skipped. The two HTTP tests were
skipped because the sandbox disallows loopback sockets; counts are from this run.

Inspection found two replay loops (`india.analysis.backtest` and
`workflow.strategy.historical_replay`). Both bought at a next candle open,
but used different end-of-data rules. The ledger accepted a caller-supplied
price, including the signal close, without candle evidence. Thus paper fills
were not constrained to the replay event model. `volume_spike` compared the
latest candle with preceding candles rather than prior sessions at the same
time. The configuration loader already rejected short selling and margin.

Implementation order:

1. Freeze deterministic regression cases before refactoring.
2. Share entry/exit fills, conservative sizing and fill revalidation; preserve
   the two strategy APIs and their distinct default stop rules.
3. Add historical same-time RVOL, sourced exceptional calendar windows,
   coverage warnings and data-integrity guards.
4. Run focused and full regression suites and the public-release checks.

No strategy edge, profitability, live execution, or completion of later
platform phases is asserted by these changes.

## Canonical policy

`india/execution.py` owns sizing, entry fills and exit-bar processing for both
replay APIs. Signal timestamps remain candle **open** timestamps for compatibility.
`signal_confirmed_at` is the close of that candle; `submitted_at` is when the
modeled order becomes active; `entry_time` is the first grid-aligned candle open
at/after submission. Default historical submission is instantaneous at signal
confirmation. That is an explicit zero-latency assumption, not measured live
execution. The shared replay API also accepts delayed submission timestamps.
Approval is the submission event in the ledger. An approval after an open must
wait for the next open, subject to the existing one-interval signal expiry.

Buy fills require observed candle evidence and cannot use an arbitrary historical
close. CLI: pass `--fill-bar FILE` alongside the existing fingerprint, quantity,
price and fill ID. FILE is one JSON object using the existing Bar field names:
`time,open,high,low,close,volume`. API: `Ledger.fill(..., bar=observed_bar)`.
Evidence must be IST, grid-aligned, nonzero-volume and not in the future; it is
caller supplied and not independently authenticated. Existing calls without a
buy candle fail with an actionable error. Price-only manual sell tickets remain
supported. The ledger still requires explicit exact-ticket approval; it does not
automatically execute brackets. Round-trip parity tests use an approved manual
exit at the canonical modeled exit price, including ledger costs.

Pre-fill quantity uses the signal reference plus a configurable 5 bps reserve,
rounded up to tick. The quantity is fixed before observing the fill. An adverse
fill that breaches cash, cost-inclusive risk or minimum net R/R is rejected,
not resized using hindsight. The reserve is not a price debit: modeled slippage
remains in transaction costs once. Workflow configuration can set
`entry_buffer_bps` to zero; the pure sizing/replay APIs accept `buffer_bps`.
Costs and reserve are estimates, not liquidity or execution guarantees.

Known opening gaps take precedence: gap below stop fills at open; gap above
target fills conservatively at target. Within a bar, a stop touch wins if both
levels are touched. Intrabar fill times are unknown (`exit_time: null`), with
`exit_bar` identifying the candle. A scheduled flatten fills at the close of
the candle ending at `flat_by` (default 15:10). Incomplete histories do not invent
an exit at the last available close. Zero-volume fills are rejected. Floating
point noise within 1e-8 of a tick unit is snapped before directional rounding.

Both adapters preserve their public call signatures and distinct signal/stop
rules: `india.analysis` uses fixed 15-minute range-low ORB; `workflow.strategy`
retains configurable ORB/level and midpoint/ATR/other stops. Equivalence tests
align those configurations and capital limits. `india.analysis` now also flattens
at 15:10 (previously the close of the 15:10 candle, i.e. 15:15).
The sampled scenario bootstrap remains a separately labeled scenario model;
it is not an executable historical strategy replay or evidence of an edge.

## RVOL and integrity

`india/volume.py` compares only earlier scheduled sessions, at matching candle
slots. It exposes same-time, opening 5-minute, opening 15-minute and cumulative
windows. Defaults: 20 prior scheduled sessions, at least 5 valid matching
windows. Missing sessions/windows are excluded and counted; no filling or
borrowing older sessions beyond the lookback. Zero historical mean or
insufficient samples returns a null value with a reason. A zero current volume
with a valid positive baseline returns zero. Future/forming candles are removed
at an explicit cutoff. Without a cutoff, callers must supply completed bars.

`volume_spike` is retained as an alias for same-time RVOL; it is null without
sufficient history. Intraday `min_volume_x` uses the same basis and can consider
a later qualifying breakout after an earlier volume rejection. RVOL settings
are replayed using only the accumulated earlier sessions. RSI/EMA formulas and
completed daily scan volume comparisons remain unchanged.

Provider candles must be increasing, unique, timezone-aware and normalized to
IST; existing providers already normalize. Invalid OHLCV, nonfinite or boolean
values, off-grid replay candles and missing opening/session bars are rejected
or block signals. Short/margin settings already failed in the config loader;
budget, risk and ledger entry points now validate even directly supplied configs.

## Calendar coverage

Existing sourced holiday lists and the documented bundled BSE/NSE shared-calendar
assumption are unchanged. No future holiday or Muhurat dates were invented.
Optional `valid_until` restricts coverage within a year. A warning is emitted
in the last 30 days; analysis/entry reports also expose `calendar_warnings`.
Expired or absent coverage fails closed. `sessions` accepts sourced, date-scoped
exceptional windows with `open`, `close`, `source`, optional `kind`. Calendar
clock checks support those windows even on weekends/holidays. Full-day ORB
analysis fails explicitly for evening/shortened windows. Adapting strategies,
providers and complete-session sampling to those clocks is deferred.

## Validation and scope

Nine original synthetic golden tests were run successfully before refactoring.
The captured zero-reserve sizing expectation (195 shares, estimated loss
498.8769558825 INR) remains reproducible in the golden test. The tick-aware
workflow default is now 188 shares at an estimated 102.40 entry, with estimated
loss 499.7858747708 INR. The signal reference remains 102.30.

Later phases were not implemented. Historical multi-asset stores, corporate
action adjustments, promotion gates, live feeds and statistical edge validation
remain separate work. No external credentials or paid datasets were needed for
this deterministic implementation and its synthetic tests.

### Recorded checks

Baseline revision: `f66b2ed` (clean before these changes).

| Check | Result |
| --- | --- |
| Pre-refactor `python3 -m unittest discover -s tests -p test_execution_golden.py -v` | 9 tests passed, 0.003s |
| Final `python3 -m unittest discover -s tests -p 'test_execution*.py' -v` | 20 tests passed, 0.048s |
| Final `python3 -m unittest discover -s tests -p test_volume_calendar.py -v` | 12 tests passed, 0.012s |
| `python3 -m unittest discover -s tests -p test_workflow.py -v` | 38 tests passed, 2.208s (also covered by final full run) |
| Final `python3 -m unittest discover -s tests -v` | 509 tests, 19.111s, 0 failures/errors, 2 skipped |
| `python3 scripts/build_release.py --check` | PASS: 166 source files, 16 test modules |
| Fresh allowlisted export: `python3 -m unittest discover -s tests -v` | 208 tests, 4.991s, 0 failures/errors, 2 skipped |
| Exported offline `python3 -m india analyze DEMO.NS --csv tests/fixtures/india/synthetic.csv --as-of 2026-10-19T09:35:00+05:30 --tick 0.05 --capital 100000 --risk 500` | PAPER_SETUP, 171 fictional shares, execution disabled |
| `git diff --check` | PASS |

Both skips are HTTP socket tests: sandbox loopback restrictions. No frontend
source changed, so no frontend build was required. Tests use mocked/synthetic
inputs, not live-market or paid-data validation.

### File inventory

Added:

- `india/execution.py`, `india/volume.py`
- `tests/test_execution_golden.py`, `tests/test_execution_policy.py`, `tests/test_volume_calendar.py`
- `docs/EXECUTION.md`

Modified:

- `india/analysis.py`, `india/data.py`, `india/forecast.py`, `india/market.py`
- `workflow/cli.py`, `workflow/common.py`, `workflow/paper.py`, `workflow/strategy.py`
- `tests/test_india.py`, `tests/test_workflow.py`
- `config/india-workflow.example.json`, `release-manifest.json`
- `README.md`, `docs/GETTING_STARTED.md`, `docs/OPERATIONS.md`, `docs/RELEASE.md`, `docs/RESEARCH.md`, `docs/WORKFLOW.md`
