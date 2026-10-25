# Getting started

Run these commands from the extracted source directory or repository root.
Python 3.9+ is required; the runtime uses only the standard library. The optional
dashboard needs Node.js 20.19+ or 22.12+ and npm to build. macOS and Linux are the
tested platforms; the scheduled-job helper uses a Unix file lock.

## Try an offline report

```sh
python3 -m india analyze DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T09:35:00+05:30 \
  --tick 0.05 --capital 100000 --risk 500
```

The fictional example returns `PAPER_SETUP`, 171 shares and
`execution_enabled: false`. It needs no credentials, network access or Codex
session. The data and tick are synthetic. `make demo` runs the same command.

For the full file-handoff example, follow [the workflow guide](WORKFLOW.md#run-the-complete-offline-paper-example).

## Use the skills in Codex

Preview the destinations, then install links from this stable checkout:

```sh
python3 install_codex.py --dry-run
python3 install_codex.py
```

The installer refuses conflicting destinations. `--dest PATH` selects a different
skills directory. Keep the source directory in place while using its links.
The [main README](../README.md#2-install-the-codex-skills) explains the standalone
skills and independent-team requirements.

To discover an NSE symbol list, paste this into Codex:

```text
Use $trader-scanner to discover the NSE universe using
scan.py universe --exchange NSE. Save it to trader/universe.txt
and report how many symbols were found.
```

To scan the whole list:

```text
Use $trader-scanner to refresh the NSE universe, then run a daily
setup scan across trader/universe.txt without --limit. Show matching
symbols, their setups, per-symbol candle dates, and counts of
successfully scanned and failed symbols.
```

Online operations require appropriate source access. Universe discovery reads a
directory; it does not fetch candles. The current EQ directory filter can include
ETFs. A full scan makes many requests, can take tens of minutes, and may return
short or unavailable histories. Reports preserve failures. Use `--limit N` when
you deliberately want a smaller scan. Daily scans require at least 30 completed
sessions and their setup matches are separate from an eligible entry.

## Open the dashboard

```sh
npm ci --prefix dashboard
npm run build --prefix dashboard
python3 -m dashboard
```

Open **http://127.0.0.1:8765**. A fresh workspace starts empty. Choose **New
request** to copy a stock review, NSE universe or daily scan prompt into Codex.
Opportunities lists every saved daily match by setup strength. Click a company
for its technical evidence, score breakdown and any matching saved daily chart.
Search/filter the full list or export it as CSV. Scanner files are written under
`trader/`; independent team sessions are observed
under `trader/team/`. A standalone scan does not create a team session.

For frontend development, keep the Python server running and start
`npm run dev --prefix dashboard` in another terminal. See [dashboard details](../dashboard/README.md).

## Bring your own data

Use a chronological OHLCV CSV with explicit timezones. Start with the
[CSV schema and provider options](../README.md#use-your-own-market-data).
Credentials are optional for offline work; [.env.example](../.env.example)
lists the supported environment variable names. The CLI does not load `.env`
files automatically. Supply current, dated tick metadata for real instruments.

Runtime reports, credentials and generated builds stay local. See
[SECURITY.md](../SECURITY.md) before sharing an error report and
[the release guide](RELEASE.md) before distributing source.
