# Original workflow, Indian market boundaries

The default product is the original modular day-trader workflow with NSE/BSE
symbols, IST sessions, INR ticks/costs and a local paper ledger. Dashboard
requests follow that workflow. Independent reviewers, frozen team snapshots,
regime research and promotion experiments are optional additions.

Reference: [original source at the audited commit](https://github.com/Jakeschincariol/day-trader-skill/tree/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd).
The eleven original Python utilities remain unchanged under `legacy/`; active
Indian wrappers reuse their applicable calculations. Source preservation alone
is not acceptance evidence: `tests/test_indian_parity.py` exercises the active
wrappers and the full paper-ticket handoff.

## Commands and handoffs

| Original job | Active Indian command | Behavior and handoff |
| --- | --- | --- |
| Discover universe | `trader-scanner/scan.py universe --exchange NSE` | Official NSE EQ-series and ETF lists by default; supplied directory/master overrides; `universe.txt` |
| Scan daily setups | `trader-scanner/scan.py daily` | Saved/discovered full universe by default, five original formulas, expected completed session; Yahoo batches screen closes before full-candle confirmation |
| Scan opening-range breakouts | `trader-scanner/scan.py orb --eligible-only` | Close screening then full OHLCV confirmation; `--max-confirm` is optional and reports skipped symbols; `eligible_hits` separates fresh entries |
| Watch an entry | `trader-entry/entry.py watch SYMBOL.NS` | Initial latest completed bar then new-bar events, bounded error recovery, fresh trigger auto-write; `--no-write` disables writes |
| Replay the watch | `trader-entry/entry.py watch SYMBOL.NS --csv FILE --now ISO` | Simulated clock, no real sleep, future/forming bars excluded, same entry evaluator |
| Plan exits | `trader-exit/exit.py plan --stop or-mid --target 2R --write` | Original midpoint/range-low/ATR/percentage/price choices and R/price targets, tick rounding, `plan.json` |
| Time Machine | `trader-timemachine/timemachine.py run --write` | Configured-rule history and sampled scenarios, `--window 0..10`, `--until`, 24-bin histogram and original verdict/notes, `timemachine.json` |
| Size one trade | `trader-budget/size.py --write` | INR cash, whole shares, estimated fees/slippage/risk, optional fresh price/account/limit, `orders.json` |
| Allocate weights | `trader-budget/size.py --weights A.NS=60,B.NS=40 --prices FILE --write` | Normalization, top-N, caps, redistribution, cost-inclusive whole shares and idle cash; research allocation in `allocation.json`, not a ready trade without individual plans |
| Check risk | `trader-risk/check.py` | Exact-order numerical PASS/BLOCK, dated ticks, current price, cash and daily limits; no model confidence override |
| Review ticket | `trader-broker/ticket.py show` | Paper proposal only; approval/fill require separate actual consent and candle evidence |
| Alerts | `trader-alerts/alerts.py add --from-plan`, then `check` | Persisted price/stop/target/time/opening-bell rules, one ring per event; no automatic notification delivery or fills |
| News | `trader-news/news.py --tickers SYMBOL.NS` | Yahoo headlines and current official NSE announcement/financial/insider RSS, optional feeds/exports, story merging and publication cutoff |
| Financials | `trader-analyst/facts.py SYMBOL.NS --basis standalone --write` | Automatic bounded NSE integrated index/RSS/XBRL quarter retrieval; optional normalized exports for older history, QoQ/YoY/TTM and YTD/Q4 derivation |
| Public disclosures | `trader-pelosi/ptr.py check --csv FILE` | Supplied dated Indian PIT/insider records; US Congress commands are not applicable to Indian securities |

Run from the repository root, or resolve installed skill entrypoints to their
absolute paths. Each tool accepts `--workspace`; keep each stock's trade
handoffs separate. Existing saved INR configuration and explicit overrides
apply. Obtain missing capital/risk choices; label provisional capital-based
sizing until cash is checked against the paper ledger. Do not invent an
authenticated balance or adopt example capital as a user instruction.

## Finding an opportunity

```sh
python3 trader-scanner/scan.py universe --exchange NSE
python3 trader-scanner/scan.py daily --universe trader/universe.txt
python3 trader-scanner/scan.py orb --watchlist trader/daily-watchlist.json --eligible-only
```

Yahoo summary batches default to 20; full-chart concurrency defaults to 4 and
is bounded at 8. NSE/broker fallback reads remain serial. Close-only summaries
never become OHLCV candles or paper orders. Daily summaries and full candles
use the same three-month window so Wilder RSI seeding does not diverge merely
because of a different requested lookback. Incomplete summaries take the full
data path. No confirmation cap or symbol limit is applied unless requested.
All failed/stale/unavailable/skipped symbols remain visible. Counts distinguish summary-screened names, requested/full-fetched snapshots, confirmed historical triggers and currently eligible entries. Broker/NSE sources
retain their existing whole-snapshot provider boundary.

The daily scan writes `daily-watchlist.json` and dated matching candle evidence.
An ORB scan writes the modular `watchlist.json` without destroying the dashboard's
daily shortlist. Nested separate workspaces publish hash-bound ORB archives to
`intraday-index.json`; the dashboard shows full-shortlist coverage, subset
refreshes and expiry at display time. The dashboard score is a research ordering, not odds of profit.
The daily hash and selection are captured before fetching. If the daily source
changes during a run, the ORB archive is retained with a rejected publication
receipt and cannot bind to the replacement shortlist. Already fetched full
OHLCV is preserved as compressed candle evidence with a hash manifest.
Refresh the shortlisted entry at the actual current cutoff before planning;
a historical trigger is not a fresh entry after a slow scan.

CLI volume confirmation defaults to the original opening-range volume average.
Use `--volume-basis same-time` explicitly for the added prior-session RVOL rule.
The library API retains its existing same-time default for compatibility;
saved plans record the selected basis so historical replay uses the same rule. A Time Machine `--until` override changes that scenario report; use exit planning `--flat-by` to change the actual trade plan.

## Explicit market and integration differences

- `.NS`/`.BO`, INR, sourced exchange sessions/ticks and estimated Indian costs
  replace US symbols, dollars, NYSE clocks and US-specific account rules.
- The shared replay engine keeps next-bar entry fills, known opening-gap
  precedence, stop-first unknown intrabar ordering and costs. It does not copy
  the original signal-close historical fill assumption. Default flatten is
  15:10 IST, not 15:55 New York time.
- Indian financial acquisition reads the official paginated integrated-filings
  index plus current NSE results RSS and verifies XBRL symbol, quarter context,
  INR units, basis and publication cutoff. `--basis` chooses standalone or
  consolidated. The index can supply several recent quarters, but it is not
  an unlimited company-facts archive; older periods may need sourced normalized
  exports in `workspace/financials/SYMBOL.NS.json` or `--filing`. PDF-only,
  unsupported taxonomy and absent filings remain unavailable. XBRL hashes,
  source URLs and acquisition errors are reported.
  Currency and per-share units are resolved through the filing's namespace and
  unit declarations, not the spelling of an identifier. Duplicate contexts,
  conflicting same-time facts and later revisions cannot supply a guessed
  quarter. A newer unreadable indexed filing remains visible as a stale-result
  warning. Feed/file caches last only for a single analysis invocation.
- Explicit cumulative rows require `period_type: ytd`/`annual` and the matching
  fiscal predecessor. EPS subtraction/summing additionally requires
  `eps_ttm_method: sum_comparable_quarters` and a documented common
  `eps_share_basis`. Revisions require `revision_id`/`supersedes`. Silent EPS,
  share or restatement reconciliation is not performed.
- The external broker connector, live orders, shorts/margin/options and
  broker partial-fill receipts remain outside this cash-equity paper project.
  Paper approvals freeze an exact ticket; an observed eligible next candle
  is required for a simulated buy fill. Alerts do not execute protective legs.
- Independent team reviews are opt-in. They support the separately documented
  fixed range-low strategy and cannot substitute for a configured midpoint/ATR
  modular plan. A numerical PASS is not independent-team clearance.

These source/transport differences prevent a claim of literal end-to-end
integration parity. They do not prevent the ordinary scan-to-paper-ticket
workflow from running on valid inputs. Neither test results nor a sampled
scenario demonstrate profitability or a guaranteed executable price.

## Acceptance evidence

`tests/test_indian_parity.py` covers batched 43/500-symbol synthetic scans,
candidate-only full fetches, explicit caps, source failures, bounded shared
fetching, universe exclusions, replay/recovery/expiry and trigger writes,
original volume confirmation, merged news, financial revisions/YTD/Q4/TTM,
INR allocations, histogram/window controls, saved chart evidence, and a full
active CLI chain through news, analyst, entry, exit, scenarios, sizing, risk,
ticket display and alert rules. It creates no approved fill or live order.

On 26 October 2026 at 11:41:07 IST, a bounded seven-symbol read-only Yahoo ORB
check returned all seven snapshots with no failures: one summary request and
four chart workers, 1.28 seconds observed elapsed time. Six historical triggers
were confirmed and zero current eligible entries were returned. This validates
that sample, not sustained availability, a full-market benchmark or profitability.
The 500-symbol test measures deterministic synthetic batching/accounting, not
real network throughput.
