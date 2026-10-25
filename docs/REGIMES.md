# Sourced NSE/BSE regime attribution (2026-10-25)

This Phase 1 increment adds dated market-regime evidence and conditional
performance reporting to the existing research laboratory. A regime is a
supplied annotation under an explicitly selected, versioned model. It never
changes ORB signal selection, execution, sizing or risk controls. Missing
evidence remains `UNKNOWN`. No market classifier, benchmark prices or actual
regime labels are fabricated from a stock's subsequent return.

## Import evidence and select a model

```sh
python3 -m research evidence regime --db var/history.sqlite \
  --file /path/to/authorized-regimes.json --observed-at 2026-10-23T16:00:00+05:30
```

Add `--regime-model YOUR_VERSIONED_MODEL` to your existing
`python3 -m research walk-forward` command. Importing evidence alone does not
select it. Without that option, the prior `UNKNOWN` annotations remain. A model
with no matching evidence at the selected vintage also reports `UNKNOWN`;
inspect the explicit coverage counts rather than interpreting absence as a
neutral market classification.

[The example JSON](../config/research-regimes.example.json) is deliberately
fictional, with an invented benchmark and one synthetic label. It describes
the schema, not a production rule or actual NSE/BSE market condition. Replace
it with sourced records under a documented causal method before validation.
The [licensed NSE/BSE source decision](DATA_SOURCES.md) remains in effect.
This increment does not fetch prices or authenticate a supplying classifier.

Each record requires exactly these fields:

| Field | Meaning |
| --- | --- |
| `exchange` | `NSE` or `BSE`; neither venue supplies the other's missing label |
| `model` | Explicit versioned identifier, up to 64 letters/digits/underscore/dot/hyphen, starting with a letter |
| `benchmark` | Nonempty identifier of the actual supplying benchmark/index |
| `method` | Nonempty classification definition/version, including rules and input conventions |
| `label` | Uppercase identifier, up to 64 letters/digits/underscore/hyphen, starting with a letter; `UNKNOWN` is reserved |
| `effective_from`, `effective_to` | Inclusive ISO YYYY-MM-DD validity dates |
| `data_through` | Latest market/input timestamp consumed by the classification, with explicit timezone |
| `published_at` | Actual timezone-aware publication/availability timestamp of that label revision |
| `source` | Original export/record reference supporting the label and its dating |

`data_through` cannot follow publication. Publication cannot follow the supplied
source `observed_at` vintage; observation cannot be in the future. Dates,
timestamps, required fields, labels and venues are validated before insertion.
Times normalize to IST. The method must remain identical for a model across
both exchanges, and each exchange/model's benchmark must remain identical.
Use a new model ID for a changed definition. Different daily source references
are allowed. This prevents silently pooling unrelated definitions under one ID;
it does not verify that the stated rules were followed or inputs are complete.

## Point-in-time selection

The evidence uses the existing append-only, hashed SQLite table and schema 1.
No database migration or dependency is required. Imports are transactional and
idempotent; conflicts at the same identity/vintage fail. The store retains
source-vintage corrections and supports out-of-order imports. Model-definition
checks and inserts share a write transaction to serialize concurrent writers.

`published-by-session-open-v1` freezes each venue's annotation at **09:15 IST**.
Publication at 09:15 is eligible; publication at 09:16 cannot label that day's
later breakout. This is a pre-open/session-open context, not a dynamically
updated intraday regime. A later date can consume that publication if it remains
within its explicit validity window. No label is carried past `effective_to`.

For each exchange/model/effective-from identity, resolve the latest publication
known by that session open, within the requested source vintage, before checking
validity. A shortened revised window cannot resurrect its older open-ended
version. Among valid overlapping records, the latest publication wins. Multiple
records tied at that latest publication fail as ambiguous. A later unambiguous
publication can resolve an older overlap.

The importer accepts retrospective exports with asserted historical publication
times, as does the existing instrument store. A hash, input cutoff and supplied
date do not prove contemporaneous availability. Verify the original dated
source and classifier inputs independently. A later source-vintage correction
can change a later-vintage report; it cannot alter an earlier frozen vintage.

## Reports and uncertainty

`spec.regime_model` and `spec.regime_decision_policy` freeze the selection rule.
`regimes.sessions` retains each selected venue/date label, source record and its
SHA-256, or an explicit unknown entry. Unselected future publications do not
enter that snapshot. Coverage counts eligible **symbol sessions**, including
those with no breakout or no fill: sourced, unknown and total. Empty-universe
dates contribute no symbol sessions. NSE and BSE coverage are independent.

All signal decisions include their regime evidence, including rejected signals.
Trades use the same session/venue label through the existing dated metadata
adapter. Per-partition and per-symbol out-of-sample `by.regime` and pooled
variant `by_regime` report counts, eligible days, zero-trade days, net P&L,
expectancy in R, win rate, average winning/losing P&L, profit factor, liquidity
status and uncertainty. A supplied regime with zero trades remains visible;
its expectancy and interval are unavailable, not invented zeros.

The existing seeded market-day block bootstrap resamples the **full evaluation
timeline** for each regime's selected trades, keeping non-regime days as days
without selected trades. It does not concatenate separated periods with the
same label. All trades from a market day stay together. `eligible_market_days`
counts the regime's observed days; interval `market_days` counts the full
evaluation sequence. A pooled date can belong to different NSE and BSE regime
buckets, each counted once per label. Cash/R pooling remains an independent
account attribution, not a shared-capital portfolio return.
The interval's `effective_blocks` describes the full timeline; it is not a count
of independent occurrences of the selected regime.

Intervals are conditional on the labels, data, stationarity and block length.
Rare regimes may be uncomputable or unstable; the existing two-block/two-traded-
day floors and tail precision rules remain computational checks rather than
proof of sample adequacy. There is no multiple-testing correction, regime
optimizer, automatic trade filter or claim of robust positive expectancy.
See the [statistical limitations](RESEARCH_LAB.md#metrics-and-uncertainty).

## Promotion and remaining work

RESEARCH to BACKTESTED retains the existing checklist behavior. BACKTESTED and
later states additionally require an explicitly selected sourced regime model
and coverage of every eligible symbol session in the requested experiment.
Missing labels cannot silently satisfy OOS validation. This completeness rule
introduces no arbitrary per-regime sample threshold. It does not establish
adequate independent samples or require positive performance in every bucket;
those judgments still need a documented strategy scope and independent review.
Spread, capacity, uncertainty, drawdown and fold checks remain in place.

This increment does not implement forward-paper result reconciliation, persisted
promotion decisions, new strategy families or any later phase. Independent
review and forward evidence still block later transitions. Live execution
remains disabled. Real benchmark inputs, original dated classification evidence,
universe completeness and real-data validation remain external requirements.

## Validation and files

Baseline: `python3 -m unittest discover -s tests -v` ran 583 tests in 21.431s,
no failures/errors, two existing loopback socket skips. The 22 added tests use
original synthetic records. They cover import/schema validation, atomicity,
vintages, integrity, model versions, publication cutoffs, revised validity,
ambiguous overlaps, venue separation, zero-trade regimes, full day adjacency,
unchanged execution, holdout isolation, coverage gates and the offline CLI.

| Check | Result |
| --- | --- |
| Baseline `python3 -m unittest discover -s tests -v` | 583 tests, 21.431s, no failures/errors, 2 skips |
| `python3 -m unittest discover -s tests -p test_research_regimes.py -v` | 22 tests passed, 0.439s |
| Final `python3 -m unittest discover -s tests -v` | 605 tests, 23.849s, no failures/errors, 2 skips |
| Fresh public export: `python3 -m unittest discover -s tests -v` | 304 tests, 8.022s, no failures/errors, 2 skips |
| `python3 scripts/build_release.py --check` | PASS: 189 source files, 21 test modules |
| `python3 -m research evidence --help`, `python3 -m research walk-forward --help`, `git diff --check` | PASS |

Both skips are existing HTTP tests blocked by sandbox loopback restrictions.
No frontend files changed in this increment. No real-market edge was measured,
no paid data or credential was provisioned, and no live execution was enabled.

Added: `research/regimes.py`, `tests/test_research_regimes.py`,
`config/research-regimes.example.json`, and this guide. Modified for this
increment: `research/store.py`, `research/lab.py`, `research/metrics.py`,
`research/promotion.py`, `research/__main__.py`, `README.md`,
`docs/RESEARCH_LAB.md`, `release-manifest.json`. Earlier workspace and dashboard
changes are preserved. No commit or push is made.
