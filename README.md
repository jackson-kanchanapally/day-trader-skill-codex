# Indian Day Trader Skills for Codex

Modular OpenAI Codex skills for **NSE/BSE stock analysis, historical replay, and paper-trading reports**. Python performs the calculations; Codex runs the tools and explains the results.

**Free public Yahoo data is the default. No broker account, market-data subscription, or API key is required for that workflow. Live order execution is disabled.**

The Python CLI runs independently of Codex and makes no AI API calls. Codex access, internet, and hosting are separate. Public data can be delayed, incomplete, or unavailable; continued free access is not guaranteed.

## Features

- Five-minute intraday analysis and daily historical OHLCV retrieval.
- Session VWAP, Wilder RSI14, EMA9/EMA21, and relative volume.
- Opening-range breakout detection using completed candles.
- Tick-rounded entry, stop, and target levels.
- Whole-share position sizing in INR with estimated costs and slippage.
- Historical replay with next-bar entry and conservative exit handling.
- Yahoo, offline CSV, and optional Upstox V3 data providers.
- Eleven modular Codex skills with portable command-line runners.

## Requirements

- Python 3.8 or newer; no third-party Python packages required.
- Codex for conversational skill use, or a terminal for direct commands.
- Internet access for online providers. The included fixture works offline.

Run the commands below from your cloned repository's root directory.

## Quick start: offline demo

Start with the included synthetic fixture to verify the complete analysis workflow:

```bash
python3 -m india analyze DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T09:35:00+05:30 \
  --tick 0.05 \
  --capital 100000 \
  --risk 500
```

Expected report values:

| Field | Result |
| --- | --- |
| Status | `PAPER_SETUP` |
| Entry | ₹102.30 |
| Stop | ₹99.70 |
| Target | ₹107.50 |
| Quantity | 178 shares |
| Estimated loss at stop, including costs | ₹499.83 |
| Estimated net reward at target | ₹886.98 |
| Net reward/risk | About 1.77:1 |
| Execution enabled | `false` |

`DEMO.NS` is synthetic test data, not a listed stock or a real price feed. RSI14 is unavailable at this replay time because too few candles have completed.

## Install the Codex skills

```bash
python3 install_codex.py --dry-run
python3 install_codex.py
```

The installer creates links under `~/.agents/skills`, checks all destinations for conflicts before linking, and refuses to overwrite unrelated skills. Re-running it is safe. Keep this checkout at a stable location: the links point to it. Use `--dest PATH` for a custom installation directory.

In Codex, try:

> Use $trader-scanner to analyze DEMO.NS from tests/fixtures/india/synthetic.csv as of 2026-10-19T09:35:00+05:30, with tick size 0.05, capital ₹100,000 and risk ₹500. Explain the paper report.

Each skill also has a `run.py` entry point. Calling its absolute path works from another working directory; relative CSV/config paths are resolved from that working directory.

See the [official Codex skill documentation](https://learn.chatgpt.com/docs/build-skills) for skill discovery and installation details.

## Analyze market data for free

Yahoo is selected automatically:

```bash
# Current intraday analysis
python3 -m india analyze RELIANCE.NS --tick 0.05 --capital 100000 --risk 500

# Completed daily historical bars
python3 -m india history RELIANCE.NS --tick 0.05

# Replay the available intraday history
python3 -m india backtest RELIANCE.NS --tick 0.05 --capital 100000 --risk 500
```

**The tick sizes above are examples.** Supply the instrument's verified tick size for the relevant trading date. The application does not infer tick sizes from the latest price.

Use `.NS` for NSE symbols, such as `RELIANCE.NS`, and `.BO` for BSE symbols/codes, such as `500325.BO`. Provider coverage can differ.

Yahoo requests the latest five days of five-minute candles or one month of daily candles. It uses `query2` and `query1` hosts with bounded retries. An alternate host may provide a complete response, but this is not an independent data source. Missing candles are never invented or forward-filled.

`--as-of` enables historical replay and filters available candles. It cannot retrieve arbitrary older Yahoo history. Use matching CSV data or the optional Upstox provider for another date range.

## Data providers

| Provider | Selection | Access | Notes |
| --- | --- | --- | --- |
| Yahoo | Default, or `--provider yahoo` | No account or API key | Unofficial public feed; may lag or fail |
| CSV | `--csv PATH` | Offline, no credentials | User-supplied OHLCV; overrides online provider selection |
| Upstox V3 | `--provider upstox` | Account and local token | Documented broker-data endpoints |

The program never silently switches between Yahoo and Upstox. If the selected source fails validation, it reports the problem.

### CSV format

Required columns:

```csv
symbol,interval,time,open,high,low,close,volume
DEMO.NS,5m,2026-10-19T09:15:00+05:30,99.9,100.2,99.7,100,1000
```

The example row is synthetic. Use `5m` for analysis/backtesting and `1d` for daily history. Timestamps represent **bar opening times** and must include a timezone. Rows must match the requested symbol and interval, be chronological and unique, and contain valid OHLCV values.

Only completed candles enter intraday analysis. Stale data cannot generate a fresh paper setup. A replay timestamp is explicitly labeled in the report.

### Optional Upstox setup

Upstox documents a free, read-only Analytics Token with one-year validity and no static-IP requirement for market-data calls. An Upstox account is required. Generate the token through the Analytics tab in Developer Apps and set `UPSTOX_ANALYTICS_TOKEN` in your local environment or secret manager. Never commit tokens or paste them into chat.

The adapter prefers `UPSTOX_ANALYTICS_TOKEN`; `UPSTOX_ACCESS_TOKEN` remains supported. Generating another analytics token revokes the previous one. Check the [official Analytics Token documentation](https://upstox.com/developer/api-documentation/announcements/analytics-token/) for current access conditions.

```bash
# Requires a token already configured in the local environment.
python3 -m india analyze RELIANCE.NS --provider upstox --tick 0.05
python3 -m india history RELIANCE.NS --provider upstox --tick 0.05 --days 28
python3 -m india backtest RELIANCE.NS --provider upstox --tick 0.05 --days 14
```

`--days` applies only to Upstox: 1-28 calendar days, default 7. The adapter combines historical and current-day endpoints when appropriate, resolves equities through the instrument master, and retries transient failures at most three times. Authentication failures do not retry.

To reuse a downloaded master, supply `--instruments /path/to/NSE.json.gz`. JSON and compressed JSON are supported; keep the file current. Tick size remains an explicit INR input.

Provider references: [historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/), [intraday candles](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/), [instrument master](https://upstox.com/developer/api-documentation/instruments/).

Upstox has mocked-response test coverage; account-specific connectivity requires validation with your credentials.

## Available skills

| Skill | Purpose |
| --- | --- |
| `$trader-scanner` | Analyze supplied symbols for rule-based setups and indicators |
| `$trader-entry` | Explain a completed-bar breakout trigger |
| `$trader-exit` | Explain tick-rounded stop and target levels |
| `$trader-budget` | Size a cash-funded position within the cost-inclusive risk budget |
| `$trader-risk` | Review freshness, setup eligibility, costs, and risk results |
| `$trader-timemachine` | Replay historical intraday setups |
| `$trader-broker` | Display paper plans; execution is unavailable |
| `$trader-alerts` | Run a single setup check; repeated monitoring needs an external scheduler |
| `$trader-news` | Read and cite exchange or issuer announcements |
| `$trader-analyst` | Review original Indian company financial reports |
| `$trader-pelosi` | Explain that US congressional disclosures are outside this workflow |

News and financial-report skills guide source reading; automated Indian filings adapters are not included. Numerical skills share the same deterministic analysis engine. Watchlist scanning runs analysis per supplied symbol; automatic discovery of all Indian stocks is not implemented.

## Analysis and risk rules

The intraday setup is the first completed five-minute close above the 09:15-09:30 opening range. The stop uses the opening-range low. The default target is 2R, configurable with `--rr`.

Position sizing uses whole shares, available capital, and the specified risk budget including estimated stop-exit costs. A proposed trade must also meet a net reward/risk floor of 1. Default capital is ₹100,000 and default risk is ₹500; pass your own values explicitly.

Reports include source, timestamps, stale status, indicators, proposed levels, estimated costs, quantity, and paper status. RSI requires warmup; EMA uses first-close seeding. A signal timestamp denotes the bar opening time; confirmation occurs at its close.

Costs model a resident retail intraday equity schedule, including brokerage, exchange charges, STT, stamp duty, SEBI charges, GST, and default slippage of 5 basis points per side. The schedule in [india/market.py](india/market.py) is an estimate, not a broker contract note. Override its rate keys using `--cost-config FILE`.

A data provider choice does not change the brokerage cost profile. Verify your own broker's charges separately. Reference schedule: [Zerodha charges](https://zerodha.com/charges), checked during development on 23 October 2026.

## Backtesting

```bash
python3 -m india backtest DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T15:30:00+05:30 \
  --tick 0.05 --capital 100000 --risk 500
```

Signals use only preceding and current completed bars. Entries fill at the next bar's open. When a candle touches both stop and target, the stop is evaluated first. Gap-through-stop exits and estimated trading costs are included. Unresolved trades are omitted when sufficient exit data is unavailable.

This is fixed-budget, per-trade replay. It does not simulate portfolio equity, simultaneous exposure, daily-loss enforcement, or compounding. A short backtest does not establish profitability.

## Limitations

- Regular sessions use IST 09:15-15:30, with an entry cutoff of 15:15.
- The intraday calendar supports 2026 only; unsupported years fail closed. Special/Muhurat sessions are excluded.
- BSE uses the common cash-equity calendar here; verify exchange-specific exceptional closures. Calendar reference: [NSE holidays](https://www.nseindia.com/resources/exchange-communication-holidays).
- Free public data is not a guaranteed real-time feed. If both Yahoo hosts fail, retry later or provide complete CSV data.
- No live orders, derivatives, short selling, leverage, delivery-cost accounting, or portfolio-wide risk controls are implemented by the Indian workflow.
- Stops and modeled costs cannot guarantee actual execution prices or maximum losses.

This project is research and paper-trading software, not financial advice or an unattended trading system.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

The latest code validation passed **325 tests**. Coverage includes indicators, historical replay, stale/missing data, market hours, costs, look-ahead prevention, provider errors/retries, and installed Codex runners. Tests run offline using fixtures and mocks; passing tests does not establish online feed availability.

## Repository layout

```text
india/                  Shared Indian-market CLI, providers, analysis, and costs
trader-*/SKILL.md        Modular Codex instructions
trader-*/run.py          Portable runners for the shared CLI
install_codex.py         Codex skill-link installer
legacy/                 Original US utilities and regression inputs
tests/                 Offline tests and fixtures
LICENSE                 Original MIT license
```

The original US utilities remain under `legacy/` for regression coverage and reusable calculations, including Wilder RSI. They are excluded from the active Indian skill workflow. Claude plugin manifests and broker connector configuration have been removed.

## License and attribution

MIT licensed. Adapted from [Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill). The original copyright and license are preserved in [LICENSE](LICENSE).
