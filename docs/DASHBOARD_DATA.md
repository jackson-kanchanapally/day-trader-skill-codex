# Dashboard decision and research data

The [automatic paper account](AUTOMATION.md) is an explicit standing-policy
mode. `GET /api/automation` reads its heartbeat, coverage, rankings, positions,
decisions and reconciled results. Authenticated same-origin
`POST /api/automation/entries` accepts only boolean `enabled` to pause/resume
new simulated entries; it does not initialize accounts or change risk limits.
Protective exits continue during pause. Journal reads select the automatic
account once configured, with `?account=manual` for the original ledger.
Standing-policy fills require their recorded policy hash, order and observed
candle. No endpoint places a live order.

The local dashboard reads saved files. Refreshing the browser does not fetch market
prices, launch Codex, write an approval or execute an order. These separate states
appear on every company page:

- **Setup strength** is the existing daily rule-match score.
- **Tradeability now** checks current calendar coverage, the default entry window,
  daily-data age and availability of a dated execution snapshot. It remains
  incomplete until a separate current completed-bar entry and exact plan exist.
- **Exact-order risk** requires a current numerical check of the proposed order.
  Independent specialist review is optional; neither a high setup score nor a
  historical PASS clears a new order.

An explicit **Run in Codex** action submits the displayed prompt to the local
Codex app-server in a new thread. The Codex request panel streams coordinator
messages, surfaces real questions/tool permissions, supports follow-up turns and
can interrupt work. Its run history lives in `--workspace/dashboard-runs/` and
is not a specialist review, paper approval or fill. Merely polling these files
never starts a turn. The coordinator still follows prerequisite/dispatch rules
and the research contracts below; actual independent specialists retain native
identities and their own sealed evidence/review files. No dashboard endpoint
approves a paper ticket, records a simulated fill or places a live order.

The page checks daily freshness against the most recent completed regular session
at the current server clock. After 15:30 IST a trading day's daily candle becomes
the expected session. Weekend and holiday hours do not make Friday's candle stale
by themselves. A future scan or uncovered calendar year fails closed. The displayed
default entry window is 09:30–15:00 IST; a configured strategy can be stricter and
the numerical risk engine remains authoritative.

Daily coverage lists requested, scanned, failed, unavailable, stale and skipped
symbols. These categories reconcile to the requested universe; capped candidates
are skipped and do not also count as scanned. Coverage exceptions retain their
individual reasons, including insufficient history.

The scanner captures the exact daily source bytes before fetching an ORB
shortlist. New ORB archives carry `daily_origin` with that SHA-256 and cutoff.
Publication checks the current daily file against this origin. A changed daily
file leaves the ORB archive saved with a `SOURCE_CHANGED` publication receipt;
it cannot silently become evidence for the new shortlist. Explicit imports of
older artifacts are labelled `IMPORTED_AFTER_SCAN` in the registry.

Full provider OHLCV snapshots already fetched for an ORB scan are compressed in
its `candles/` directory and hashed in `evidence.json`. Archiving performs no
new price fetch, excludes forming/future bars and creates no candles for
summary-only screening. Failed rows retain provider attempts and observed
missing strategy timestamps. Later failed refreshes preserve the prior expired
trigger as history while current eligibility remains unavailable.

## Research cautions

The following disclosed heuristics annotate research rather than change the saved
score: price at least 10% above MA20, RSI14 at least 70, five-close range at least
10%, relative volume at least 3×, approximate daily turnover below ₹1 crore, and
absolute close-to-close daily change at least 8%. A high relative-volume number is
not a spread/depth measure. A large rise does not prove a circuit lock.

Daily change, opening gap, session range and Wilder ATR14 are calculated only from
the same validated candle snapshot as the scan. ATR is seeded from the first 14
true ranges in that saved history. No missing bars are invented. Corporate actions
and candle-adjustment basis still require source reconciliation.

## Optional execution snapshot

Save a permitted export to `trader/execution-context.json` (or the selected
`--workspace`). See the original synthetic
[schema example](../config/execution-context.example.json). Replace it with actual
authorized evidence for real instruments; fixture records cannot verify real symbols.

The document requires `currency: INR`, `paper: true`, `execution_enabled: false`
and `quotes`. Each record requires an exact symbol, offset-aware `observed_at`,
original HTTPS `source`, positive bid/ask and lower/upper price bands in INR,
and an explicit boolean `halted`. Duplicate symbols, crossed quotes, out-of-band
prices, missing fields and synthetic evidence for a real stock are rejected.

A quote is `CAPTURED` only when its age is 0–60 seconds and it belongs to the
current IST date. Old or future quotes remain stale. This is a freshness check of
a supplied snapshot, not independent authentication, a streaming feed or source
permission. A spread above 20 bps is an explicit research caution. Captured quotes
never grant entry or risk clearance. Depth, actual fills and corporate-action
adjustments cannot be inferred from this schema.

## Saved individual research

`GET /api/research` indexes two explicitly different record kinds:

1. Actual team folders under `--sessions`, opened through the existing identity,
   source and snapshot-hash checks.
2. Coordinator prerequisite assessments under `--workspace/reviews/NAME/readiness.json`.

An assessment uses kind
`COORDINATOR_PREREQUISITE_ASSESSMENT_NOT_INDEPENDENT_AGENT_REVIEWS`, with explicit
`execution_enabled: false`, `independent_team_dispatched: false` and
`independent_risk_clearance: false`. It supplies `symbol`, offset-aware `as_of`,
`original_scan_source` (within the workspace), `original_scan_sha256`,
`original_daily_setup` equal to that symbol's original scan hit, and
`coordinator_role_findings` containing `role`, `status` and `decision`.
The source scan's cutoff must match `original_scan_as_of` when supplied, or the
assessment cutoff otherwise. A later review can retain an earlier source scan by
recording both cutoffs explicitly; a future source scan is rejected. Numerical proposals
(`order`, `quantity`, `stop`, `target`, `cost_estimate`) must be absent or null.
This constrained assessment format never represents a ready ticket.

If setup evidence cannot be obtained at all, a coordinator can instead record
`evidence_status: UNAVAILABLE` with the same explicit non-clearance booleans and
symbol/cutoff, omitting all original scan fields. Findings in this form must use
pending, unavailable, ineligible or no-ticket statuses; completed evidence or PASS
claims are rejected. The research page prominently labels the missing setup.

Optional `user_inputs`, `issuer_source` and `news_findings` retain the original
research record. An adjacent `security-master.json` is revalidated against the
saved cutoff before a recorded tick is shown as verified. Later entry sessions
require applicable dated metadata. Invalid source bindings, path escapes,
symlinks and coordinator claims of independent clearance are excluded and counted.
Records at different cutoffs remain separately labelled; they cannot silently
replace the selected scan's findings.

`GET /api/research/ID` returns the record. Opportunity details link all saved
research for that exact symbol. Research-only requests need a stock symbol and
preserve a selected source cutoff automatically. Date options are optional and
use an IST date/time picker. They continue available company research while
leaving sizing and order clearance pending.

Choosing **Plan a paper trade** uses saved capital and per-trade loss settings;
form entries supply explicit overrides. Other account limits and positions are
optional form details. Codex obtains missing constraints, costs and dated metadata before any actual sizing. The
advanced trade settings also allow an explicit closed-market diagnostic. Copying
the text runs nothing in the browser, and no engine defaults become user choices.

## Paper journal

`GET /api/journal` opens `--workspace/state/paper.sqlite` with SQLite `mode=ro` and
`query_only`. It never initializes a database or writes fills. Missing accounts
show an empty state, without sample trades. JSON portfolio exports are not balances.

Simulated fills must match exact approved order fingerprints and quantities. FIFO
entry/exit allocations, shares, cash and realized P&L must reconcile to the ledger;
otherwise performance is unavailable. P&L includes recorded entry and exit costs.
The realized curve advances on sell fills. Win rate and average P&L are per sell
fill; setup groups allocate each exit to its original FIFO entry setup. Partial
realizations and multiple lots must not be described as distinct strategy trades.

Open positions show remaining book cost. Current unrealized P&L, a calibrated
prediction score and market-regime attribution require additional evidence and are
not invented. Paper results do not establish live fill quality or future returns.
