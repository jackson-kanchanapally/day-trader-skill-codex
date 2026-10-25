# React agent observatory

Read-only, local dashboard for saved daily market scans and actual Codex trading-team sessions. React and Vite build the frontend; a Python standard-library HTTP server reads existing handoffs and reviews. No cloud account, AI API key or frontend subscription is required.

From the repository root, using Node 20.19+ or 22.12+ and Python 3.9+:

```sh
npm ci --prefix dashboard
npm run build --prefix dashboard
python3 -m dashboard
```

Open **http://127.0.0.1:8765**. The server binds only to loopback. Stop it with Ctrl-C. After the initial build, Node is not needed to serve the dashboard. To use another team root or port:

```sh
python3 -m dashboard --workspace trader --sessions trader/team --port 8766
```

Run `$trader-team` in Codex while the page is open. “New request” builds a request to copy into Codex: choose “Review one stock”, “Discover NSE universe”, or “Scan NSE opportunities”. Universe discovery saves the eligible NSE symbol list; the opportunities request refreshes that list and runs a daily setup scan across it without a symbol limit, reporting coverage and failures. Saved daily matches appear in Opportunities; start a stock review afterward to follow the independent team in Observatory. The browser itself does not launch agents. The latest session is followed automatically, or select an archived session and turn off “Follow latest”. Empty workspaces display setup instructions rather than fake data.

## Market opportunities

The default view reads every match from `--workspace/watchlist.json` (the default
workspace is `trader/`). It shows the saved cutoff, candle dates, full coverage
counts and every failure reason. Search companies or symbols, filter any matched
setup, sort, paginate through all results or export the filtered/all-match CSV.
No symbol limit is applied by the dashboard. Switching to Observatory preserves
the existing independent-team view; mobile has a view selector in the header.

Company details separate setup strength, current tradeability and independent risk.
Daily freshness uses the current clock and covered calendar, while explicit flags
show extension, unusual volume and volatility. Missing spread, price-band or halt
evidence remains unavailable. Stock research starts with a symbol; bare NSE codes
such as `SSWL` are accepted. The selected scan date is preserved automatically,
and optional date controls use a regular date/time picker. Choose **Plan a paper
trade** to add capital and per-trade risk. Other account settings remain optional
in the form and are obtained before actual sizing; no account values are assumed.

**Individual research** shows both saved coordinator assessments and actual team
sessions, with distinct labels and source bindings. **Trading journal** reads the
authoritative paper ledger without creating an account; reconciled FIFO outcomes
show recorded costs, setup attribution, realized P&L and open positions. See
[data contracts and limitations](../docs/DASHBOARD_DATA.md) for these APIs, optional
dated execution snapshots, caution thresholds and accounting definitions.

Default ranking is a deterministic **setup strength** score out of 100:
strongest matched rule (40 maximum), additional rules (10), relative volume (25),
approximate daily turnover (15) and price position (10). Older candle dates reduce
the score by 50%; ties use turnover and symbol. “How are opportunities ranked?”
shows the precise thresholds. This is an ordering heuristic, not a calibrated
probability, trading recommendation or risk clearance. Oversold depth does not
by itself put a symbol at the top.

Click any company for its score breakdown, original setup evidence, RSI/current
and previous, MA20, closing-high comparison, pre-flag rise, five-close range,
relative volume, provider attempts and a specialist-review prompt. A matching
saved run under `trader/scans/*/` with `candles/SYMBOL.json` enables a daily
close/MA20/volume chart and recent OHLCV table. The report hash, cutoff, symbol,
source, latest candle and recomputed indicator evidence must match. Missing or
inconsistent history displays stored indicators without inventing a chart.
The page does not fetch prices, perform issuer research or clear downstream
entry/tick/risk checks.

Listing names are optional local metadata in `trader/instrument-names.json`, with
shape `{"source":"source URL","fetched_at":"ISO timestamp","names":{"DEMO.NS":"Example listing name"}}`.
Use a permitted instrument directory for real names; symbols are the fallback.
The current EQ universe can include ETFs. Market reports, candle snapshots and
listing metadata remain ignored runtime files, outside public source releases.

The HTTP API provides `/api/opportunities` and
`/api/opportunities/SYMBOL?scan=SCAN_ID`. The detail API rejects unknown symbols
and changed scan IDs; all endpoints remain loopback-only and read-only.
Opportunities refresh from saved files every ten seconds, not from a live feed.

## Independent team

Each specialist has an animated person at a desk: sleeping while queued/dispatched, typing after a recorded running event, and holding a report after a validated review is saved. Completed reviews include BLOCK and UNAVAILABLE outcomes; the verdict badge remains separate. Failed or invalid work shows an attention marker. Click a desk (or focus it and press Enter) to open the agent workspace with its report, numerical evidence, sources and raw JSON. Escape closes it. Open workspaces update with the session, and reduced-motion preferences disable animations.

The specialist board uses compact mini desks. Names, role descriptions, lifecycle
states, verdicts, search/filter controls and the complete detail workspace remain
available; the activity guide is expandable.

- Queued: no native delegation recorded.
- Dispatched: native identity recorded, without a confirmed running event.
- Running: the coordinator recorded a real start event. This is the last recorded state, not a heartbeat.
- Completed: independent review identity, hash, cutoff, verdict and required source metadata pass validation. BLOCK/UNAVAILABLE are completed review outcomes.
- Invalid/failed: mismatched evidence or a recorded failure; never counted as successful completion.

The coordinator lifecycle command is documented in `trader-team/references/workflow.md`. Only the coordinator writes events; independent reviewers continue to write only their own reviews. Activity shows recorded lifecycle events and review-file save times, not hidden reasoning or token streams. Old sessions do not acquire fabricated run timings.

The dashboard shows candles within the configured 09:15–15:15 strategy window, distinguishes numerical artifacts from model verdicts, and hides outdated report clearance after reviews/settings/evidence change. It verifies archived files against the engine without rewriting them. A connection error shows a cached-data warning. A 2-second polling connection is not a live-price feed. Financial/source strings render as text; research links allow HTTPS only.

For frontend development, keep the Python server on port 8765 and run `npm run dev --prefix dashboard` in a second terminal. Vite proxies `/api` to Python. Production uses the single Python URL above. Tests: `python3 -m unittest discover -s tests -p test_dashboard.py` and `python3 -m unittest discover -s tests -p test_opportunities.py`.
