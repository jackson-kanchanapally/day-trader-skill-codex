# Spread and candle-volume scenarios (2026-10-25)

This Phase 1 increment adds explicit spread costs and an optional causal
capacity screen to the existing execution engine. It supports the NSE/BSE
research laboratory. It does not establish actual spread, depth, capacity or
strategy profitability. Defaults preserve the earlier unscreened replay.

## Configure a scenario

Add these options to your existing `python3 -m research walk-forward` command:

```sh
--cost-config config/research-costs.example.json \
--liquidity-config config/research-liquidity.example.json
```

The example files contain **illustrative stress assumptions**, not measured
NSE/BSE estimates or recommended production limits. The cost file declares
5 bps slippage and a 10 bps full spread; the liquidity file limits an order to
1% of the immediately preceding completed candle's share volume. Calibrate
separate venue/instrument/date scenarios against entitled observations. Nothing
downloads data, buys a subscription, starts a scheduler or places an order.
The existing [NSE/BSE source decision](DATA_SOURCES.md) still applies.

`--liquidity-config` requires exactly one field, `max_participation`, a finite
number in `(0,1]`. Unknown fields, booleans and invalid fractions fail clearly.
The normalized model is frozen in `spec.liquidity_config` and the report hash.
An absent file leaves the existing fill path and quantities unchanged.

## Spread costs and sizing

`india.market.costs` accepts optional `spread_bps`, default zero. It represents
a **full** spread assumption. Each buy/sell leg pays half its notional spread:

```text
spread INR = (buy_price + sell_price) * quantity * spread_bps / 20,000
```

This is an additional cash cost. It does not alter candle prices, stop/target
touches or the separately charged slippage assumption. For example, buying
100 shares at INR 100 and selling at INR 110 with a 10 bps full-spread scenario
adds INR 10.50 across both legs. It creates no extra exchange fee or tax base.
Spread participates in conservative pre-fill sizing, stop-risk and reward/risk
checks, cash checks, replay P&L and paper-ledger one-way charges. Costs reject
boolean rates. The existing entry buffer remains a sizing reserve only.

OHLCV candles contain trade prices, not midpoint quotes. Charging a half-spread
at each leg is an explicit sensitivity assumption, not reconstructed bid/ask
execution. Existing slippage estimates may already include spread; document
the calibration before adding both to avoid counting the same effect twice.
The existing slippage stress grid remains in place; spread stays fixed within
a report. Compare additional declared spread assumptions in separate runs.
NSE/BSE fee selection remains venue-specific and otherwise unchanged; configured
fee defaults are estimates rather than newly verified tariff schedules.

## Causal capacity screen

`india.liquidity` implements `lagged-completed-volume-v1`. At an entry open,
the screen uses only the immediately preceding completed candle, which is
normally the signal candle. It computes:

```text
maximum full-order quantity = floor(previous_completed_volume * max_participation)
```

It accepts or rejects the already risk-sized full quantity. It does not resize
after looking at a fill, search for a later opportunity, or create a partial
fill. Missing, stale, future or zero-volume references cannot pass. Delayed
submission needs a fresh preceding completed candle. Decimal flooring avoids
losing a whole share to a binary floating-point fraction.

The entry candle's final volume never sets its own capacity budget. The
existing positive-volume/valid-candle requirement still applies. Exit capacity
also uses the preceding completed candle at the exit bar's open; even a timed
close uses this same conservative information cutoff. Same-bar entry/exit
uses the signal candle for both independent leg checks. A pass is labeled
`PROXY_PASSED`, not a verified fill. There is no queue, depth, impact curve,
combined multi-order volume budget, circuit/price-band or partial-fill model.
Accounts remain independent per symbol/fold. Raw share volume is used within
the same session, so historical split/bonus RVOL adjustment is not involved.

## Failed exits and inference

An oversized entry produces `ENTRY_CAPACITY_LIMIT` with the full quantity,
reference time/volume, fraction and maximum quantity in rejection diagnostics.
There is no entered position in that case.

If an entered position's modeled exit exceeds the proxy capacity budget,
the trade remains in cash/R results at the canonical marked exit, including
gap losses. Its status is `UNRESOLVED_EXIT_CAPACITY`. Partition, out-of-sample
and variant summaries count these trades and mark `UNRESOLVED_CAPACITY`.
Paired baseline comparisons carry the same inference status. The reported
P&L and intervals then describe a marked scenario, not executable expectancy;
the model has not solved how the position could be liquidated. Subsequent
cash attribution remains conditional on that marked exit.

Missing/noncontiguous/zero-volume history after an accepted entry raises an
`Unresolved entered position` error in capacity-enabled replay. The CLI returns
`UNAVAILABLE`; it does not produce a complete report by dropping that exposure
or inventing a final close. Legacy unscreened replay retains its prior `None`
behavior for incomplete paths.

Promotion checks inspect both training and test trades in every scenario.
Any unresolved exit capacity blocks promotion, including RESEARCH to BACKTESTED.
BACKTESTED and later states additionally require an explicitly configured
liquidity scenario and a positive spread scenario. These are documented
modeling requirements, not proof that the assumptions are realistic. Existing
sample, uncertainty, fold, drawdown, independent review and forward-validation
requirements remain. Live execution remains disabled.

## Validation and files

Baseline: `python3 -m unittest discover -s tests -v` ran 563 tests in 22.838s,
no failures/errors, two existing loopback socket skips. The baseline includes
dashboard changes already present when this increment began; those files are
preserved. New tests use original synthetic events and dated metadata.

The 20 new tests cover spread accounting across venues and paper legs,
cost-inclusive sizing, raw-fill preservation, model validation, exact share
flooring, stale/future references, no future-volume entry screening, fixed-size
entry rejection, delayed submissions, stop-first ambiguity, retained gap losses,
missing exit paths, promotion vetoes, reproducible reports and the offline CLI.

| Check | Result |
| --- | --- |
| Baseline `python3 -m unittest discover -s tests -v` | 563 tests, 22.838s, no failures/errors, 2 skips |
| `python3 -m unittest discover -s tests -p test_liquidity.py -v` | 20 tests passed, 0.273s |
| Final `python3 -m unittest discover -s tests -v` | 583 tests, 21.943s, no failures/errors, 2 skips |
| Fresh public export: `python3 -m unittest discover -s tests -v` | 282 tests, 7.627s, no failures/errors, 2 skips |
| `python3 scripts/build_release.py --check` | PASS: 185 source files, 20 test modules |
| `python3 -m research walk-forward --help` and `git diff --check` | PASS |

Both skips are existing HTTP tests blocked by sandbox loopback restrictions.
No frontend files were changed by this increment. No real-market edge was
measured and no paid source, credential or external execution was provisioned.

Added: `india/liquidity.py`, `tests/test_liquidity.py`,
`config/research-costs.example.json`, `config/research-liquidity.example.json`,
and this guide. Modified for this increment: `india/execution.py`,
`india/market.py`, `workflow/paper.py`, `research/strategies.py`,
`research/metrics.py`, `research/lab.py`, `research/promotion.py`,
`research/__main__.py`, `README.md`, `docs/EXECUTION.md`,
`docs/RESEARCH_LAB.md`, `docs/EXPERIMENTS.md`, `release-manifest.json`.

Phase 1 remains incomplete. Actual market-data/quote validation, broader action
coverage, sourced regime evidence, independent universe validation, persisted
promotion decisions and forward results remain. Later phases are not completed
by this increment. No commit or push is made.
