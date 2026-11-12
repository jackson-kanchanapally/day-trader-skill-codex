# Changelog

## Unreleased

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
