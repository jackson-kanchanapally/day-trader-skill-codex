# Indian Day Trader Skills for Codex

Modular OpenAI Codex skills for **NSE/BSE stock analysis, historical replay, and paper-trading reports**. Python performs the calculations; Codex can coordinate independent specialist agents to review the same frozen evidence and explain their decisions.

**Free public Yahoo data is the default; direct NSE website data is available with `--provider nse`. Neither requires a broker account, market-data subscription, or API key. Live order execution is disabled.**

The Python CLI runs independently of Codex and makes no AI API calls. Codex access, internet, and hosting are separate. Public data can be delayed, incomplete, or unavailable; continued free access is not guaranteed.

## Features

- Five-minute intraday analysis and daily historical OHLCV retrieval.
- Candle-based VWAP, Wilder RSI14, EMA9/EMA21, and relative volume.
- Opening-range breakout detection using completed candles.
- Tick-rounded entry, stop, and target levels.
- Whole-share position sizing in INR with estimated costs and slippage.
- Historical replay with next-bar entry and conservative exit handling.
- Yahoo, direct NSE charts, offline CSV, and optional Upstox V3 data providers.
- Eleven modular specialist skills plus a Codex team coordinator with ten distinct reviewers and a risk veto.
- Shared, checksummed handoff files; portfolio cash/exposure/daily limits; paper ticket and alert definitions.
- Seeded 10,000-path scenario analysis when at least 20 complete prior sessions are available.
- SQLite snapshot archives and deterministic offline numerical replay.
- Health checks, explicit provider fallback, bounded scheduled jobs and rotated JSON logs.
- Chronological holdout research with compounded equity, drawdown and slippage stress tests.
- Configurable sourced holiday calendars, Linux/macOS CI and a non-root Docker image definition.

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

## Run the independent specialist team

After installing the skills, use a Codex host with native subagent delegation:

> Use $trader-team to independently review TCS.NS using the NSE provider, tick size 0.05, capital ₹100,000 and risk ₹500. Run the specialist agents and explain their disagreements and final paper decision.

Verify the tick before use. The coordinator prepares one immutable snapshot and delegates scanner, news, analyst, entry, exit, timemachine, budget, risk, broker and alerts to **ten distinct native agents**. It preserves the eleventh, US congressional disclosure role as NOT_APPLICABLE. Independent roles can run in parallel; dependent reviews wait for their prerequisites. Available worker slots determine concurrency. A single `$trader-scanner` invocation remains a standalone analysis.

Any specialist BLOCK vetoes the proposal, and risk must PASS. Missing required filings, history or reviews produce an incomplete result; the coordinator does not force approval. News and financial reviewers must read dated exchange/issuer sources published by the cutoff. Official issuer domains require an explicit allowlist. These source checks validate metadata and domains; source content still needs actual agent review.

The CLI prepares and validates evidence; **it does not itself start AI agents**:

```bash
# Offline preparation only. The fixture is fictional and has insufficient forecast history.
python3 trader-team/run.py prepare DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T09:35:00+05:30 \
  --tick 0.05 --capital 100000 --risk 500 --team-dir trader/demo-team

# Inspect a specialist's numerical evidence. This is not an agent judgement.
python3 trader-risk/run.py evaluate --session trader/demo-team

# After Codex has run the actual agents and recorded their reviews:
python3 trader-team/run.py finalize --session trader/demo-team
```

Use a new directory for every preparation. Without actual agent reviews, finalization stays PENDING_AGENT_REVIEWS. The DEMO fixture cannot produce a cleared full-team ticket: issuer research and 20-session scenario history are unavailable. Online NSE requests are bounded to 28 calendar days and usually also need a longer, separately collected complete CSV history for scenario clearance. Ordinary fixture analysis still produces the numerical PAPER_SETUP shown above. No command places orders, assumes a fill or starts alert monitoring.

`--portfolio FILE` supplies a dated paper cash/equity/position snapshot; without it the team explicitly assumes an empty paper portfolio funded by `--capital`. `--team-limits FILE` overrides cash buffer, position/order, total open risk, daily loss/count and minimum net reward/risk caps. See [team contracts and examples](docs/TEAM.md) and the [coordinator instructions](trader-team/SKILL.md).

Native delegation must be exposed by the Codex host; otherwise the skill reports MULTI_AGENT_UNAVAILABLE. Optional named agent profiles are in `codex-agents/`; generic native agents can also load each specialist's instructions. More agents use additional Codex capacity. Python uses no AI API and needs no OpenAI API key.

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

Yahoo requests the latest available session of five-minute candles (`range=1d`) or one month of daily candles. It uses `query2` and `query1` hosts with bounded retries. If a response contains an internal gap, it tries the alternate host for an intact response. The two hosts are not independent data sources. Missing candles are never invented or forward-filled.

If a candle is absent inside the 09:15-15:15 strategy window, analysis returns a partial paper report with `status: BLOCKED_INCOMPLETE_DATA` and `plan: null`. `data_quality.missing_bars` identifies the missing slots. Indicators use only uninterrupted candles from the session open up to the first gap; `indicators_as_of` shows their cutoff. `last_bar` identifies the newest received strategy candle and may be later than the indicator cutoff. Partially populated candles still fail validation with the timestamp and missing fields. A successful command means the report was produced; it does not mean a trade is eligible.

After market hours the report shows `market_open: false`; old candles are marked stale. To explicitly replay today's data before a later gap, use a matching timestamp, for example:

```bash
python3 -m india analyze TCS.NS --tick 0.05 --capital 100000 --risk 500 \
  --as-of 2026-10-23T10:00:00+05:30
```

This is historical replay, not a live quote. The date must fall within the provider's available session. Backtests continue to reject internal gaps rather than produce results across missing bars.

`--as-of` enables historical replay and filters available candles. It cannot retrieve arbitrary older Yahoo history. NSE supports a bounded date request, subject to website retention; CSV and Upstox are other options.

### Direct NSE website data: no account or token

```bash
python3 -m india analyze TCS.NS --provider nse --tick 0.05 --capital 100000 --risk 500
python3 -m india history TCS.NS --provider nse --tick 0.05 --days 28
python3 -m india backtest TCS.NS --provider nse --tick 0.05 --capital 100000 --risk 500

# Explicit historical replay, verified against actual NSE data on this date.
python3 -m india analyze TCS.NS --provider nse --tick 0.05 \
  --as-of 2026-10-23T10:30:00+05:30
```

The adapter reads the JSON endpoints behind [NSE Technical Charting](https://charting.nseindia.com/). It resolves the exact NSE equity symbol and token, requests five-minute or daily bars, and converts the website's IST wall-clock timestamps to timezone-aware candle opening times. No third-party packages are required. Symbol lookups are cached within the provider instance. Transient HTTP/network failures retry once; access denials stop immediately. No proxy rotation, CAPTCHA bypass, paid feed, or silent provider switching is used.

Use `.NS` symbols; this adapter does not support `.BO` securities. `--days` selects 1-28 calendar days, default 7. Requested dates remain subject to the website's available history. Missing, invalid and duplicate candles are checked. Indicator warmup depends on the requested lookback.

Website OHLCV values are preserved as published. Opening-candle auction treatment, volume and OHLC aggregation can differ from Yahoo; do not mix snapshots or assume matching indicator values across providers. VWAP is an approximation using candle typical prices and reported bar volumes, rather than a reconstruction from every exchange trade. Website access, schemas, retention and freshness are not guaranteed; this is not an exchange subscription feed.

Actual online checks on 23 October 2026 returned complete 72-bar strategy sessions for TCS and RELIANCE. TCS replay at 10:30 returned 15 completed current-session bars, and its seven-calendar-day paper backtest completed. These checks establish observed access, not continuous availability or profitability.

## Data providers

| Provider | Selection | Access | Notes |
| --- | --- | --- | --- |
| Yahoo | Default, or `--provider yahoo` | No account or API key | Unofficial public feed; may lag or fail |
| NSE website | `--provider nse` | No account or API key | Direct public charts; `.NS` equities, 5m/1d, website availability limits |
| CSV | `--csv PATH` | Offline, no credentials | User-supplied OHLCV; overrides online provider selection |
| Upstox V3 | `--provider upstox` | Account and local token | Documented broker-data endpoints |

The program never silently switches between providers. If the selected source fails validation, it reports the problem.

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

`--days` applies to NSE and Upstox: 1-28 calendar days, default 7. The Upstox adapter combines historical and current-day endpoints when appropriate, resolves equities through the instrument master, and retries transient failures at most three times. Authentication failures do not retry.

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

Analysis and backtesting use a conservative **09:15-15:15 IST strategy window for all equities**, including stocks that can trade continuously later. From August 2026, eligible NSE stocks enter the [Closing Auction Session](https://www.nseindia.com/static/products-services/closing-auction-session) at 15:15. Auction rows are excluded rather than interpreted as ordinary five-minute candles. A missing 15:20 row therefore does not invalidate this strategy's data. Reports expose `analysis_window` and `strategy_open`; `market_open` is the broad cash-market calendar check, not proof that a particular stock is in continuous trading.

Position sizing uses whole shares, available capital, and the specified risk budget including estimated stop-exit costs. A proposed trade must also meet a net reward/risk floor of 1. Default capital is ₹100,000 and default risk is ₹500; pass your own values explicitly.

Reports include source, timestamps, stale status, indicators, proposed levels, estimated costs, quantity, and paper status. RSI requires warmup; EMA uses first-close seeding. A signal timestamp denotes the bar opening time; confirmation occurs at its close.

Costs model a resident retail intraday equity schedule, including brokerage, exchange charges, STT, stamp duty, SEBI charges, GST, and default slippage of 5 basis points per side. The schedule in [india/market.py](india/market.py) is an estimate, not a broker contract note. Override its rate keys using `--cost-config FILE`.

Unknown cost keys and invalid prices/quantities fail validation. This prevents an override typo from silently retaining an unintended rate.

A data provider choice does not change the brokerage cost profile. Verify your own broker's charges separately. Reference schedule: [Zerodha charges](https://zerodha.com/charges), checked during development on 23 October 2026.

## Backtesting

```bash
python3 -m india backtest DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T15:30:00+05:30 \
  --tick 0.05 --capital 100000 --risk 500
```

Signals use only preceding and current completed bars. Entries fill at the next bar's open. When a candle touches both stop and target, the stop is evaluated first. Gap-through-stop exits and estimated trading costs are included. Remaining paper positions exit at the 15:10 candle's close (15:15), before the closing auction. Unresolved trades are omitted when sufficient exit data is unavailable.

This is fixed-budget, per-trade replay. It does not simulate portfolio equity, simultaneous exposure, daily-loss enforcement, or compounding. A short backtest does not establish profitability.

## Research and scheduled paper operation

```bash
# Feed health, paper analysis and an auditable snapshot. No orders.
python3 -m india doctor TCS.NS --provider nse --fallback-provider yahoo \
  --tick 0.05 --record-db var/runs.sqlite

# Fixed-rule chronological holdout with compounded equity and cost stress.
python3 -m india research TCS.NS --provider nse --days 7 \
  --tick 0.05 --capital 100000 --risk 500 --record-db var/research.sqlite

# Substitute the run_id printed by a recorded command. Works offline.
python3 -m india replay RUN_ID_FROM_REPORT --db var/runs.sqlite

# One bounded check, suitable for an external five-minute scheduler.
python3 -m india.job --config config/paper.example.json
```

`doctor` exits 0 for healthy data and 1 for failures. After-hours staleness is expected when the latest session is complete; health PASS does not mean a trade is eligible. `--fallback-provider` is opt-in and keeps whole snapshots separate. `--record-db` preserves exact data, settings, calendar and code fingerprint; replay reports mismatches instead of silently using revised provider data. No credentials are archived.

The scheduled job checks IST and holidays itself, skips outside 09:35-15:15, prevents overlapping runs with an OS lock, enforces a child timeout, and rotates local JSON logs. It does not install a scheduler or send messages. Review the example's tick size and INR limits before use.

The new `research` command is separate from the original fixed-budget `backtest`. It sizes each trade from realized equity and evaluates fixed rules on chronological training/holdout partitions under higher slippage assumptions. It rejects partial/missing sessions and remains single-symbol research. A five-session online check is not statistical evidence of profitability.

See [research methodology](docs/RESEARCH.md) and [local/container operations](docs/OPERATIONS.md) for full commands, calendar overrides, backups, CI, Docker and remaining acceptance work. These additions improve operational tooling; they do not establish 10/10 readiness without sustained market-hours observations.

## Limitations

- The strategy uses IST 09:15-15:15. It excludes closing auctions and the final 15 minutes of non-auction stocks; it does not infer security-specific auction eligibility.
- Bundled holiday coverage is 2026. `--calendar PATH` accepts sourced NSE/BSE year records; uncovered years fail closed. Special/Muhurat sessions remain excluded.
- BSE uses the common cash-equity calendar here; verify exchange-specific exceptional closures. Calendar reference: [NSE holidays](https://www.nseindia.com/resources/exchange-communication-holidays).
- Free public data is not a guaranteed real-time feed. If both Yahoo hosts fail, retry later or provide complete CSV data.
- No live orders, derivatives, short selling, leverage, delivery-cost accounting, automatic broker reconciliation or multi-asset backtest simulation. Team risk checks use supplied paper portfolio exposure.
- Stops and modeled costs cannot guarantee actual execution prices or maximum losses.

This project is research and paper-trading software, not financial advice or an unattended trading system.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

Coverage includes indicators, historical replay, stale/missing data, market hours, costs, look-ahead prevention, NSE wall-clock conversion, exact symbol matching, auction exclusion, provider errors/retries, installed Codex runners, scenario simulation and team handoff integrity/vetoes. Tests run offline using fixtures and mocks, including captured NSE and Yahoo responses. Mock agent IDs in unit tests are explicitly labelled; passing tests does not establish online feed availability or real agent independence.

## Repository layout

```text
india/                  Shared Indian-market CLI, providers, analysis, and costs
config/                 Example job and sourced 2026 calendar documents
docs/                   Research methodology and operating instructions
Dockerfile              Non-root, standard-library-only runtime image
.github/workflows/      Offline tests and container smoke CI
trader-*/SKILL.md        Modular Codex instructions
trader-*/run.py          Portable runners for the shared CLI
trader-team/             Native Codex delegation instructions and evidence helper
codex-agents/            Optional named specialist agent profiles
install_codex.py         Codex skill-link installer
legacy/                 Original US utilities and regression inputs
tests/                 Offline tests and fixtures
LICENSE                 Original MIT license
```

The original US utilities remain under `legacy/` for regression coverage and reusable calculations, including Wilder RSI. They are excluded from the active Indian skill workflow. Claude plugin manifests and broker connector configuration have been removed.

## License and attribution

MIT licensed. Adapted from [Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill). The original copyright and license are preserved in [LICENSE](LICENSE).
