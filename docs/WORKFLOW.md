# Original day-trader workflow for Indian stocks

The reference is [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), commit `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd`. All eleven original Python utilities remain unchanged under `legacy/`. Active Indian entrypoints reuse selected original pure functions through explicit adapters in `workflow/`; they never invoke the US CLI or its broker connector instructions.

```text
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> paper ticket
alerts: persist stop / target / flat-by rules and check them repeatedly
news / analyst / public disclosures: separate research commands
```

Codex follows the modular SKILL.md instructions and runs Python. No model API is called by these tools. The separate independent-review mode still supports native specialist agents and the React dashboard.

## What has been restored

| Original tool | Active Indian entrypoint | Behavior |
| --- | --- | --- |
| Scanner | `trader-scanner/scan.py` | Cash-equity directory discovery, daily multi-symbol setup scan, ORB watchlist |
| Entry | `trader-entry/entry.py` | Original ORB/price-level evaluator, optional volume confirmation, bounded foreground watch |
| Exit | `trader-exit/exit.py` | Midpoint, range-low, price, percentage and ATR stops; R/price targets |
| Time Machine | `trader-timemachine/timemachine.py` | Configured-rule history replay, seeded 10,000-path simulation, portable chart bands |
| Budget | `trader-budget/size.py` | Whole-share INR sizing, estimated costs, cash buffer and order/concentration caps |
| Risk | `trader-risk/check.py` | Original risk rules plus cost-inclusive loss, tick metadata, signal/quote age and exact-plan checks |
| Broker | `trader-broker/ticket.py` | Exact-ticket approval, transactional local simulated fills, cash/FIFO lots, flatten proposals |
| Alerts | `trader-alerts/alerts.py` | Original condition evaluator, persisted once-only/rearm state, IST clock and levels |
| News | `trader-news/news.py` | Original RSS parser, Indian symbols, dated announcement imports and deduplication |
| Analyst | `trader-analyst/facts.py` | Sourced Indian quarterly exports, units, matching-period growth and contiguous TTM |
| Pelosi | `trader-pelosi/ptr.py` | Explicit US boundary; optional dated Indian public-insider/PIT exports |

The roles, commands and file flow are restored; this is not a claim of identical market integrations. The live brokerage connector is intentionally absent. Financial and public-disclosure inputs require authorized exports instead of pretending that US SEC/House APIs cover Indian companies. `chart` returns JSON percentile bands/sample paths rather than the original terminal chart. Some original US-specific flags are intentionally unsupported; use each entrypoint's `--help`.

## Run the complete offline paper example

All prices, metadata, financials and news in this example are original **synthetic fixtures**. `DEMO.NS` is fictional. The explicit replay clock is essential.

```bash
mkdir -p trader/original-demo
cp config/india-workflow.example.json trader/original-demo/config.json

python3 trader-scanner/scan.py orb --symbols DEMO.NS \
  --workspace trader/original-demo --csv tests/fixtures/india/synthetic.csv \
  --now 2026-10-19T09:35:00+05:30

python3 trader-entry/entry.py check DEMO.NS --tick 0.05 --write \
  --workspace trader/original-demo --csv tests/fixtures/india/synthetic.csv \
  --now 2026-10-19T09:35:00+05:30

python3 trader-exit/exit.py plan --stop or-mid --target 2R --tick 0.05 --write \
  --workspace trader/original-demo \
  --security-master tests/fixtures/india/workflow/security-master.csv \
  --now 2026-10-19T09:35:00+05:30

python3 trader-budget/size.py --write --workspace trader/original-demo \
  --now 2026-10-19T09:35:00+05:30

python3 trader-risk/check.py --workspace trader/original-demo \
  --csv tests/fixtures/india/synthetic.csv \
  --security-master tests/fixtures/india/workflow/security-master.csv \
  --now 2026-10-19T09:35:00+05:30

python3 trader-broker/ticket.py show --workspace trader/original-demo \
  --csv tests/fixtures/india/synthetic.csv \
  --security-master tests/fixtures/india/workflow/security-master.csv \
  --now 2026-10-19T09:35:00+05:30

python3 trader-alerts/alerts.py add --from-plan --workspace trader/original-demo \
  --now 2026-10-19T09:35:00+05:30
python3 trader-alerts/alerts.py check --workspace trader/original-demo \
  --csv tests/fixtures/india/synthetic.csv --now 2026-10-19T09:35:00+05:30
```

Expected: entry ₹102.30, midpoint stop ₹99.95, 2R target ₹107.00, 195 shares, estimated loss including costs ₹498.88, net reward/risk approximately 1.75 and `READY_PAPER_TICKET`. **No approval or fill is recorded by these commands.** The short fixture cannot supply the twenty prior sessions required for a simulation:

```bash
python3 trader-timemachine/timemachine.py run --write --workspace trader/original-demo \
  --csv tests/fixtures/india/synthetic.csv --now 2026-10-19T09:35:00+05:30
```

That command reports unavailable simulation. The original-style numerical risk check does not require every research skill to PASS; missing research must remain visible. If the user requires complete independent clearance, use the stricter independent-team mode instead. Do not present these two clearance meanings as interchangeable.

## Real Indian stocks

Discover a directory without requesting candles or requiring an account:

```bash
python3 trader-scanner/scan.py universe --exchange NSE
python3 trader-scanner/scan.py daily --universe trader/universe.txt --limit 50
python3 trader-scanner/scan.py orb --symbols TCS.NS,RELIANCE.NS --provider upstox
python3 trader-entry/entry.py watch TCS.NS --provider upstox --every 30 --max-checks 120 --write
```

Universe discovery reads [Upstox's public BOD directory](https://upstox.com/developer/api-documentation/instruments/), filtered to single-share cash equities. `--instruments FILE` supplies a saved directory. NSE symbols retain `.NS`; BSE uses the directory's exchange token plus `.BO`. Directory discovery does **not** validate raw tick units. Exact exchange eligibility and source permissions still require verification.

Yahoo requests one year of daily bars, sixty days of five-minute bars, or seven days of one-minute bars. It is an unofficial interface, can be delayed or unavailable, and is never described as live. NSE supports up to 120 calendar days in disjoint requests of at most 28 days; Upstox caps requests at 28 days. Both support five-minute/daily candles, not one-minute input in this implementation. An unavailable interval fails explicitly. Use longer authorized CSV history for the Time Machine when needed. There is no guaranteed lifetime-free market feed.

For `.NS` stocks, unavailable or incomplete Yahoo five-minute/daily snapshots automatically fall back to the existing NSE provider. `--no-fallback` disables this automatic choice; `--provider nse` selects NSE directly. Reports retain per-symbol provider attempts and provenance, and use a whole snapshot from the selected provider. CSV never contacts a fallback. `.BO` stocks never use NSE prices; there is no verified direct BSE website OHLCV adapter, so use Yahoo, configured Upstox BSE or an authorized BSE CSV. This change does not create website/data permissions.

`--csv FILE` accepts one or multiple symbols/intervals with the existing `symbol,interval,time,open,high,low,close,volume` schema. `--offline DIR` accepts per-symbol CSV or Yahoo chart JSON files. Future/forming rows are excluded before numeric analysis; null slots remain gaps, and partial OHLCV values fail. No resampling, fabricated volume or mixed-provider candle reconstruction occurs.

Regular cash-equity clock checks use the configured calendar. The strategy conservatively excludes all data at/after 15:15 IST to avoid closing-auction contamination. Defaults are no entries before 09:30, none after 15:00 and flatten at 15:10; earlier custom boundaries are supported. Built-in coverage remains 2026 and BSE shares the bundled calendar. Supply a verified calendar for other years/exchange-specific differences. Special sessions are unsupported.

NSE website access remains experimental and subject to the restrictions documented in [NOTICE.md](../NOTICE.md). This migration does not establish provider permission or remove release blockers.

## Security metadata

For a real paper ticket, supply `--security-master FILE` to exit/risk/broker. CSV and a JSON array (or `{"securities": [...]}`) are supported. Required fields:

```csv
symbol,tick_size,currency,tick_unit,effective_from,effective_to,published_at,source
SYMBOL.NS,0.05,INR,INR,2026-10-01,2026-10-31,2026-09-30T18:00:00+05:30,https://official-source.example/security-master
```

The example is a schema, not verified metadata for any real symbol. Obtain the actual current instrument/date record from an authorized exchange/security-master source, verify its unit conversion, and preserve its source and publication time. A raw `5` in a broker file must not silently become ₹5 or ₹0.05. Supplied metadata is validated and matched to the tick; it is not independently authenticated by this tool. Future or ambiguous-unit records fail. Missing/mismatched metadata blocks the paper ticket. Synthetic DEMO metadata is labelled as such and cannot verify a real stock.

## Paper records and approvals

The authoritative ledger is `trader/state/paper.sqlite`, or the corresponding path under `--workspace`. SQLite transactions keep approval, fill, cash and FIFO lots consistent. Initial capital is fixed for that paper account; choose another workspace to start a differently funded account. `portfolio.json` and `state/broker.json` are exports; editing them does not change ledger balances. There is no broker account reconciliation or general external-portfolio import.

`ticket.py show` rechecks the order, configuration, portfolio, fresh completed prices, applicable metadata and signal age. Only an actual user reply beginning with an unambiguous yes for the one shown ticket may be passed to `ticket.py approve --reply ...`. Approval and `fill --fingerprint ... --qty ... --price ... --fill-id ...` record **local simulated events**, never an order. A fill requires the approved full quantity, dated approval, unchanged state/configuration and an explicit tick-aligned simulated price. Duplicate IDs/orders, stale approvals, risk breaches and oversells fail without changing cash.

The ledger's `placed` event label is an internal compatibility label for a **simulated** full fill, with `simulation: true` and `execution_enabled: false`; it is not a brokerage acknowledgement. Partial fills, automatic bracket execution and externally confirmed fills are unsupported. Stop/target alerts provide information; they do not update holdings automatically. Use `flatten` to create a sell proposal, run risk again, then obtain the user's approval before recording a simulated exit.

Sizing/costs retain the intraday cash-equity assumptions from `india/market.py`; `costs` in the workflow configuration can override verified rates. The example is not a broker fee schedule. No delivery taxation, short selling, options, fractional shares, margin or live mode is supported.

## Research inputs

News: `news.py --tickers SYMBOL.NS --announcements FILE` accepts `{"items": [{"symbol", "title", "published_at", "source"}]}` with original source links. Default RSS uses Indian symbols; an authorized HTTPS issuer RSS template can be selected through `--feed-url 'https://issuer.example/feed?symbol={symbol}'`. Stored IDs deduplicate events. Move checks compare completed candles against the previous session's last available candle; they are not live quotes or official closing-price reconciliation. Use `--no-moves` for headlines only or `--prices-csv FILE` for a separate authorized OHLCV export. Headlines are leads, not verified facts or complete announcement coverage.

Financials: `facts.py SYMBOL.NS --filing FILE --write` requires `symbol`, `currency: "INR"`, `basis` (`consolidated` or `standalone`), `unit` (`INR`, `INR_thousand`, `INR_lakh`, `INR_million`, `INR_crore`) and `quarters`. Each quarter requires `period_start`, `period_end`, `published_at`, original HTTPS `source`, `revenue`, `net_profit` and `diluted_eps` (INR/share). Supply standalone quarters, not cumulative YTD. Reconcile basis, corporate actions, restatements and units against original filings. See the clearly synthetic [financial example](../tests/fixtures/india/workflow/financials.json). No automatic Indian equivalent of SEC company-facts extraction is claimed.

Indian public disclosures: `ptr.py check --csv FILE` requires `symbol,person,transaction_date,published_at,transaction_type,quantity,source`, with `buy`/`sell` and original HTTPS source. Publications after cutoff are excluded; once-only state persists. It is a public-insider/PIT input adapter, not comprehensive politician-trade surveillance. Without input this compatibility role returns NOT_APPLICABLE.

## Independent agents and dashboard

For explicit independent review, `$trader-team` retains the frozen-snapshot engine, ten native agents, hash/cutoff validation and final veto rules documented in [TEAM.md](TEAM.md). Its numerical strategy remains the original Indian engine's fixed range-low/2R default unless `--rr` changes the multiple. Those sealed artifacts have their own schema; they are not interchangeable with a mutable original-style workspace or configurable midpoint/ATR plan.

The React dashboard observes those independent review sessions and actual lifecycle events. It does not automatically display ordinary CLI subprocesses as AI agents. No browser UI starts agents or executes orders. The original-style skill chain and the optional independent review mode remain explicitly distinct.

## Validation

Run `python3 -m unittest discover -s tests`. Workflow coverage includes original stop choices, level/ORB entry, daily scanning, data gaps, session/calendar behavior, tick units/date applicability, news/disclosure deduplication, costs, next-open historical fills, seeded prior-only simulation and transactional paper accounting. These tests establish implemented behavior, not provider reliability, profitability or production certification.
