# React agent observatory

Read-only, local dashboard for actual Codex trading-team sessions. React and Vite build the frontend; a Python standard-library HTTP server reads existing handoffs and reviews. No cloud account, AI API key or frontend subscription is required.

From the repository root, using Node 20.19+ or 22.12+ and Python 3.9+:

```sh
npm ci --prefix dashboard
npm run build --prefix dashboard
python3 -m dashboard
```

Open **http://127.0.0.1:8765**. The server binds only to loopback. Stop it with Ctrl-C. After the initial build, Node is not needed to serve the dashboard. To use another team root or port:

```sh
python3 -m dashboard --sessions trader/team --port 8766
```

Run `$trader-team` in Codex while the page is open. “New request” builds a request to copy into Codex: choose “Review one stock”, “Discover NSE universe”, or “Scan NSE opportunities”. Universe discovery saves the eligible NSE symbol list; the opportunities request refreshes that list and runs a daily setup scan across it without a symbol limit, reporting coverage and failures. These scanner results are saved by Codex; start a stock review afterward to follow the independent team here. The browser itself does not launch agents. The latest session is followed automatically, or select an archived session and turn off “Follow latest”. Empty workspaces display setup instructions rather than fake data.

Each specialist has an animated person at a desk: sleeping while queued/dispatched, typing after a recorded running event, and holding a report after a validated review is saved. Completed reviews include BLOCK and UNAVAILABLE outcomes; the verdict badge remains separate. Failed or invalid work shows an attention marker. Click a desk (or focus it and press Enter) to open the agent workspace with its report, numerical evidence, sources and raw JSON. Escape closes it. Open workspaces update with the session, and reduced-motion preferences disable animations.

- Queued: no native delegation recorded.
- Dispatched: native identity recorded, without a confirmed running event.
- Running: the coordinator recorded a real start event. This is the last recorded state, not a heartbeat.
- Completed: independent review identity, hash, cutoff, verdict and required source metadata pass validation. BLOCK/UNAVAILABLE are completed review outcomes.
- Invalid/failed: mismatched evidence or a recorded failure; never counted as successful completion.

The coordinator lifecycle command is documented in `trader-team/references/workflow.md`. Only the coordinator writes events; independent reviewers continue to write only their own reviews. Activity shows recorded lifecycle events and review-file save times, not hidden reasoning or token streams. Old sessions do not acquire fabricated run timings.

The dashboard shows candles within the configured 09:15–15:15 strategy window, distinguishes numerical artifacts from model verdicts, and hides outdated report clearance after reviews/settings/evidence change. It verifies archived files against the engine without rewriting them. A connection error shows a cached-data warning. A 2-second polling connection is not a live-price feed. Financial/source strings render as text; research links allow HTTPS only.

For frontend development, keep the Python server on port 8765 and run `npm run dev --prefix dashboard` in a second terminal. Vite proxies `/api` to Python. Production uses the single Python URL above. Tests: `python3 -m unittest discover -s tests -p test_dashboard.py`.
