# Indian Trading Agents for Codex

Modular NSE/BSE research tools for Codex, with INR position sizing, local paper
records, independent specialist reviews, and an optional React dashboard.

**Experimental, paper-only software. Live order execution is disabled.**

Start with an offline synthetic report. It needs no API key, broker account,
network connection or Codex session:

```sh
python3 -m india analyze DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T09:35:00+05:30 \
  --tick 0.05 --capital 100000 --risk 500
```

Expected: `PAPER_SETUP`, **178 shares**, estimated cost-inclusive stop loss of
**₹499.83**, and `execution_enabled: false`. These are fictional fixture results.
A numerical setup is separate from independent-team clearance.

[Getting started](docs/GETTING_STARTED.md) · [Workflow commands](docs/WORKFLOW.md) ·
[Dashboard](dashboard/README.md) · [Contributing](CONTRIBUTING.md) ·
[Public source release](docs/RELEASE.md)

## What you can do

- Discover an NSE/BSE symbol universe and scan multiple symbols for daily setups or opening-range breakouts.
- Check completed-bar entries, choose tick-rounded stops and targets, and size whole-share positions with estimated Indian charges and slippage.
- Replay historical rules, run holdout research and inspect seeded paper scenarios when sufficient history is available.
- Maintain a local SQLite paper ledger and persistent price/time alert definitions.
- Review supplied issuer financials, dated headlines and public disclosure exports.
- Coordinate ten independent specialists over frozen evidence and inspect their saved reviews in a local dashboard.

The current universe filter can include ETFs. Discovery does not guarantee candle
coverage, verify tick units or identify a trade by itself. Price data can be
delayed, revised, incomplete or unavailable.

## Requirements

| Component | Requirement |
| --- | --- |
| Python runtime | Python 3.9+; standard library only |
| Dashboard build | Node.js 20.19+ or 22.12+ and npm |
| Independent team | A Codex host with native subagent delegation |
| Online data | Source access and permission for the intended use |

macOS and Linux are the tested platforms. The scheduled-job helper uses a Unix
file lock. Run commands from this source directory. No Python package install is
required; Node is needed only to build or develop the optional frontend.

## 1. Try the paper workflow

The command above runs the standalone numerical engine. For the complete
scanner → entry → exit → sizing → risk → paper-ticket handoff, follow the
[offline workflow example](docs/WORKFLOW.md#run-the-complete-offline-paper-example).
Each tool accepts a separate `--workspace DIR`; keep mutable handoffs separate
from sealed independent-review sessions.

`make demo` runs the standalone example. `python3 -m india --help` lists the
numerical, research and operational commands.

## 2. Install the Codex skills

```sh
python3 install_codex.py --dry-run
python3 install_codex.py
```

The installer links twelve skills into `~/.agents/skills`, checks all destinations
before writing, and refuses conflicts. Keep the checkout at a stable path.
Use `--dest PATH` for another skills directory.

For universe discovery, paste this into Codex:

```text
Use $trader-scanner to discover the NSE universe using
scan.py universe --exchange NSE. Save it to trader/universe.txt
and report how many symbols were found.
```

For a full daily scan:

```text
Use $trader-scanner to refresh the NSE universe, then run a daily
setup scan across trader/universe.txt without --limit. Show matching
symbols, their setups, per-symbol candle dates, and counts of
successfully scanned and failed symbols.
```

A full scan may take tens of minutes. `--limit N` deliberately bounds a smaller
run. Daily setup checks need at least 30 completed sessions. See
[the scanner commands](docs/WORKFLOW.md#real-indian-stocks) for CLI usage.

For an independent stock review, supply the exact symbol, permitted CSV/provider,
timezone-aware cutoff, dated tick metadata, capital and risk limits. Ask
`$trader-team` to review those inputs and explain its saved decisions.

## Two workflows

| Workflow | Purpose | Output |
| --- | --- | --- |
| Specialist tools | Scan lists and pass files between entry, exits, sizing, research and risk | Mutable files under `trader/` or `--workspace` |
| Independent team | Review one frozen numerical snapshot using actual native agents | Sealed sessions under `trader/team/`, observed by the dashboard |

The team requires distinct native reviewer identities and prerequisite reviews.
A `BLOCK` vetoes clearance; risk must pass and required evidence must be valid.
If native delegation is unavailable, the workflow reports
`MULTI_AGENT_UNAVAILABLE`. Python subprocesses are not independent model agents.
Optional profiles in `codex-agents/` are not installed automatically.

[Team contracts](docs/TEAM.md) explain review binding, prerequisites and the
separate meanings of numerical readiness and independent clearance.

## 3. Open the React dashboard

```sh
npm ci --prefix dashboard
npm run build --prefix dashboard
python3 -m dashboard
```

Open **http://127.0.0.1:8765**. A fresh workspace starts empty. **New request**
offers copyable prompts for a stock review, NSE universe discovery and a daily
scan. Run the copied request in Codex; the browser does not start agents.
Standalone scanner files do not create independent team sessions.

The animated desks reflect recorded lifecycle events: waiting, working, completed
review, or attention required. Click a desk to inspect its review, engine evidence
and sources. A completed review can still veto a setup. The UI supports mobile
layouts and reduced motion. Its two-second refresh observes local files, not a
live-price feed. Keep the unauthenticated server on loopback.

See [dashboard setup and development](dashboard/README.md).

## Use your own market data

The [workflow guide](docs/WORKFLOW.md) documents CSV schemas, provider selection,
security metadata, calendars and research exports. Use timezone-aware candle
opening times and exact `.NS`/`.BO` provider symbols.

| Source | Selection | Boundary |
| --- | --- | --- |
| Authorized CSV | `--csv FILE` | Offline input; overrides provider selection |
| Yahoo | `--provider yahoo` | Unofficial interface; can be delayed or unavailable |
| NSE website adapter | `--provider nse` | Experimental `.NS` adapter; source terms and access apply |
| Upstox V3 | `--provider upstox` | Local token and appropriate account/data access |

Yahoo can fall back to NSE for supported `.NS` snapshots. `--no-fallback`
disables that choice. Reports preserve provider attempts and use a whole snapshot
from the chosen source; they do not splice candles or substitute NSE prices for
BSE stocks. Source availability does not establish permission for a use case.

[.env.example](.env.example) lists optional credential variable names. The CLI
reads exported environment variables and does not load `.env` files itself.
Never put tokens in prompts, reports, issue bodies or source control.

The software license does not grant rights to third-party data. See
[NOTICE.md](NOTICE.md) and consult the relevant source terms before fetching,
processing or redistributing content.

## Development

```sh
python3 -m unittest discover -s tests
npm ci --prefix dashboard
npm run build --prefix dashboard
python3 scripts/build_release.py --check
```

Tests use mocks and local inputs, without live provider requests. The public
source profile uses only original synthetic fixtures. A development checkout may
also have historical capture-dependent tests; see [fixture provenance](tests/fixtures/README.md).
Loopback HTTP checks may be skipped in restrictive sandboxes.

CI covers Python on Linux/macOS, the React production build, the exported public
source test suite and a CLI container smoke check. For frontend development,
keep the Python server running and start `npm run dev --prefix dashboard` in
another terminal.

To produce the public source directory and archive:

```sh
python3 scripts/build_release.py --archive
```

This creates `dist/day-trader-skill-public/` and a `.tar.gz` with an explicit
manifest and checksums. It excludes Git history, captured provider payloads,
capture-dependent historical tests, private diagnostics and runtime data.
**Publish this reviewed export as a fresh source history; the development
repository's existing history still contains captured fixtures.** Follow
[release validation](docs/RELEASE.md) before publishing. Rebuilding refuses an
existing destination; choose a new `--out PATH` for another candidate.

## Limits

This is research software, without demonstrated profitability or sustained
production validation. The default strategy is long-only, cash-funded and uses
whole shares. It has no broker transport, notification delivery, automatic broker
reconciliation or implicit background monitoring.

Ticks require dated instrument-specific verification. Built-in calendar coverage
is 2026; BSE shares the bundled calendar, and special sessions are unsupported.
Uncovered years fail closed. Costs are estimates. Short samples, liquidity,
corporate actions, selection bias and regime changes limit historical conclusions.
A strategy match or simulated stop cannot guarantee an executable trade or a
maximum realized loss.

## Project layout

```text
india/             Numerical engine, providers, research, archives and team contracts
workflow/          Specialist commands, file handoffs and local paper accounting
trader-*/          Twelve modular skills and portable entrypoints
dashboard/         React UI and read-only local HTTP server
legacy/            Preserved MIT upstream utilities reused by the adapters
config/            Example INR limits, jobs and calendar configuration
tests/             Offline tests and documented fixtures
scripts/           Public source exporter
docs/              Getting started, operations, research and release guidance
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and
[SECURITY.md](SECURITY.md) for private vulnerability reporting. The project does
not provide a hosted trading service or investment advice.

## License and attribution

Software is [MIT licensed](LICENSE), adapted from
[Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).
The original copyright and license are retained. Third-party software and data
retain their respective rights; see [NOTICE.md](NOTICE.md).

This is an independent adaptation, not an official OpenAI, NSE, BSE, Yahoo or
Upstox product.
