# Reproducible paper research

The `research` command evaluates the existing fixed opening-range breakout rules.
It splits complete sessions chronologically, compounds realized equity within
each partition, and repeats the evaluation with higher modeled slippage.
It does not fit parameters or select stocks based on future performance.

```bash
python3 -m india research TCS.NS --provider nse --days 28 \
  --tick 0.05 --capital 100000 --risk 500 --risk-fraction 0.005 \
  --train-fraction 0.7 --record-db var/research.sqlite
```

CSV is supported through the existing format with `--csv PATH`. Use `--as-of`
for a fixed historical cutoff. The command needs at least two complete regular
sessions. Gaps, partial sessions and missing whole trading dates inside the
observed date span are errors; it never silently drops an inconvenient day.
For an unfinished current session, use an earlier completed as-of date. A gap
that exists before the first available date cannot be detected from the data
alone; verify the requested versus supplied history yourself.

The default split uses the first 70% of session dates for `train`, with remaining
dates held out. Both partitions use exactly the same rules/settings and begin
with the same initial capital. This is chronological evaluation, not an
optimization engine. If you repeatedly choose parameters after viewing the
holdout, it ceases to be independent evidence.

The per-trade risk budget is `min(--risk, realized_equity * --risk-fraction)`.
Only already-realized outcomes affect later position sizing. Cash affordability,
whole shares, tick rounding, stop risk and estimated costs use the existing
backtester. It is a single-symbol strategy with at most one ORB trade per
session, not a multi-symbol portfolio allocation engine or persistent broker
account simulation. Actual gap-through-stop losses can exceed the sizing budget.

Each slippage scenario reports:

- Trade count, net P&L, return, ending equity and average net P&L per trade.
- Win rate and profit factor (`null` when undefined, rather than infinity).
- Maximum drawdown on the realized-equity curve, and the curve itself.
- Full paper trade records, including entry time and exit candle timestamp.

The exit timestamp identifies the candle containing an exit; it does not claim
an exact intrabar execution time. The 15:05 candle's close represents the
shared 15:10 flatten boundary. An unresolved position is omitted by the base backtester; complete
sessions ensure that a regular strategy close is available. Stop/target
ambiguity remains conservative: after processing known opening gaps, stop first
when both levels are touched within one candle. Quantity is sized from a
conservative pre-fill estimate and revalidated at the actual next open; an
adverse gap cannot trigger hindsight resizing. See [execution policy](EXECUTION.md).

Default slippage scenarios are 5, 10 and 20 basis points per side. Cost overrides
change the baseline. Every scenario sizes independently including its own
costs, so higher slippage can reduce quantity; net P&L need not be monotonic.
The scenario is a sensitivity check, not an estimate of real fill quality.

Research limitations remain material: public feed revisions, source-dependent
auction/volume aggregation, unadjusted corporate actions, stock selection and
survivorship bias, limited history, and absent multi-asset exposure or market
impact modeling. No Sharpe ratio, significance level or profitability claim is
inferred from a handful of trades. Evaluate multiple independent periods and
market conditions before treating a rule as useful.

Every `--record-db` run preserves data, settings, calendar, source and a code
fingerprint. Replay through `python3 -m india replay RUN_ID --db PATH` to verify
the same computation without requesting revised data from the provider. See
[operations](OPERATIONS.md) for backups, health monitoring and deployment.

The [offline research laboratory](RESEARCH_LAB.md) adds versioned local history,
dated universe/action evidence and walk-forward reports around this same engine.
The existing `python3 -m india research` interface remains available.
