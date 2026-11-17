# Changelog

## Unreleased

- Add an optional intraday recorder (`india/recorder.py`, `recorder` settings, off by default) that runs inside `india.picks --serve` and stores evidence under `var/`. It samples NIFTY 50, NIFTY BANK, India VIX and the sector indices from the NSE official MCP every 30 seconds into labelled five-minute candles, and records five-minute stock closes for the alert universe from Yahoo once per completed window (20 symbols per call, in sequence). Each source backs off on its own, nothing is retried immediately, and missing samples stay missing. Index candles carry a strict `complete` flag and a looser `usable` flag for close-based checks. The NSE stock snapshot is kept as an opt-in feed because one call covers about a tenth of the NIFTY 200. `--record-once --dry-run` checks a source without Telegram secrets or saved data. No trade signals or Telegram messages are added. See [Intraday recorder](docs/TELEGRAM_ALERTS.md#intraday-recorder).
- Preserve `--dry-run` in the scheduler's recursive morning/settle jobs.
- Add `python3 -m india.picks`: a daily Telegram bot alert with the top three NSE intraday setups (volume, EMA trend, MACD, RSI, ATR), each with entry, exit and cost-inclusive risk plans. It skips holidays and late wakes, sends once per session and reports failures to the chat. See [docs/TELEGRAM_ALERTS.md](docs/TELEGRAM_ALERTS.md).
- Paper-trade every sent alert on 5-minute candles (`--settle`, every 10 minutes via LaunchAgent) with an end-of-day Telegram scorecard and a new dashboard **Alert trades** page (`/api/alert-trades`). `--dry-run` no longer writes the alert record. A temporary data outage keeps each trade's last good result (marked stale) and delays the scorecard until data returns, or until 18:00 IST.
- Explain every alert paper trade (`india/review.py`): failed breakout, stop too tight, gap or NIFTY against the trade, no follow-through and similar, with excursions in R, in the scorecard and dashboard. Setup traits that lose over at least 8 closed trades become lessons that lower future alert scores (`learning`, `learn_min_trades`, `learn_penalty`, `learn_max_penalty`). Execution patterns are reported as suggestions only.
- Skip alert setups with results or another material board meeting from the signal session through the trade day, or a split/bonus-type ex-date on the trade day, using NSE's public event calendar and corporate-action lists (`india/events.py`, `"events": "skip" | "flag" | "off"`). Dividend ex-dates and minor meetings are flagged. A cached calendar is used if NSE is unreachable; otherwise the alert says the check could not run.
- Send in-session Telegram updates for each alert paper trade: trigger, T1, final exit with net P&L, and gap skips or no trigger by the deadline (`live_updates`). Each is sent once; late milestones are recorded silently and the scorecard covers them. `settle_minutes` (5–30) sets the `--serve` and LaunchAgent settle cadence.
- Add `python3 -m india.picks_backtest`: it replays the alert scorer over about four years of daily candles. Each trade is bracketed by the worse and better intraday path, and the last ~60 days are replayed exactly on 5-minute candles. It reports results by score bucket and by trait with standard errors. The first run found no edge after costs (see docs/TELEGRAM_ALERTS.md).
- Learning lessons now also need the trait's gap to exceed two standard errors. Worse-looking traits inside the noise show as "Within noise".
- Add `docker-compose.yml` + `Dockerfile.app`: always-on `dashboard` and `alerts` (`india.picks --serve`) containers with health checks; `python3 -m dashboard --host` for container binding.
- Fix Yahoo five-minute history failing completely during and after the closing auction: a partly empty candle at or after 15:15 (outside the strategy window) is now dropped instead of rejecting the whole 60-day snapshot. Partly empty candles during continuous trading still fail.
- Research replays now name why a trade was skipped (`NET_REWARD_RISK_BELOW_MINIMUM`, `ZERO_AFFORDABLE_QUANTITY`, `NO_VALID_FILL` with its cause) instead of a generic `EXECUTION_OR_RISK_LIMIT`. A 1R target, for example, can never clear costs against the 1.0 minimum.
- Rewrite all twelve skill instructions after comparing them with the upstream project: trigger phrases in each description, numbered steps, files read and written, rules, explicit "what it never does" boundaries and one shared safety block. Add `agents/openai.yaml` for every skill; `$trader-broker` now runs only when named. A new test parses every printed command with the real tool parser.
- Show the alert clock's session as `NSE:2026-10-19:09:15-15:30` instead of a Python tuple. An opening-bell alert can ring once more for the session in progress after upgrading.

- Add `--provider nse-mcp`: NSE's official MCP end-of-day history for `.NS` daily scans and the `history` command, with a standard-library Streamable HTTP client, a fail-closed split/bonus check for unadjusted prices and `python3 -m india.nse_mcp` for delayed quotes, corporate actions and tool discovery.

- Reconcile capped scan counts; retain failure diagnostics and captured ORB candles; bind shortlist publication to the daily source selected before fetching.
- Validate XBRL unit declarations and revisions, refresh filing caches per invocation, show all dashboard coverage exceptions and accept saved planning limits.

- Restore original modular workflow as the dashboard default; make independent reviews opt-in.
- Restore batched/two-stage market screening, bounded confirmation, explicit skip counts and replay/recovery watches.
- Merge Indian news stories across feeds; expand financial export comparisons, revisions, YTD/Q4 and explicit TTM EPS.
- Add original simulation window/histogram/verdict controls, INR weight allocations and automatic daily chart archives.
- Bind saved ORB scans to the dashboard with live expiry checks; use official NSE EQ/ETF lists for stock universe discovery.
- Add the official NSE integrated-filings index and current RSS, validated XBRL quarters, whole-snapshot intraday recovery and a text scenario chart.

- NSE/BSE paper research tools, daily/ORB scanning and INR risk sizing.
- Twelve modular skills, an independent review workflow and a local React dashboard.
- Copyable dashboard requests for NSE universe discovery and daily setup scans.
- Original synthetic provider payloads for timestamps, gaps, auction exclusion and replay tests.
- An allowlisted public source export with checksums and reproducible archives.
- CI checks for the Python runtime, dashboard build, public export and CLI container.

The public source profile excludes captured data and capture-dependent historical
tests. It includes the unchanged upstream Python utilities used by the Indian
adapters. See [release contents and validation](docs/RELEASE.md).
