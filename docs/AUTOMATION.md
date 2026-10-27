# Automatic NSE paper trading

The background worker discovers the complete current NSE EQ stock universe,
scans completed daily history without a symbol limit, ranks fresh five-minute
opening-range entries, and manages a dedicated local simulated account.
There is no brokerage transport or live execution mode.

The selected defaults are ₹1,00,000 simulated capital, ₹500 maximum estimated
cost-inclusive risk per trade, ₹1,500 daily risk/loss allowance and at most
three new trades per day. Returns are measured after simulated costs. Scores
and settings do not guarantee a profit; gaps can exceed the planned stop loss.

## Start and operate

```sh
python3 -m automation enable --workspace trader
python3 -m automation run --workspace trader
```

For a persistent macOS worker that starts at login and restarts after failure:

```sh
python3 -m automation install --workspace trader
```

Installation writes a workspace-specific LaunchAgent under the current user's
`Library/LaunchAgents`, fixing the checkout/interpreter paths. It does not change
power settings: the computer must be awake with working data access. On Linux,
supervise `python3 -m automation run` through the normal process supervisor.
A file lock prevents duplicate workers for the same account.

```sh
python3 -m automation status --workspace trader
python3 -m automation pause --workspace trader
python3 -m automation resume --workspace trader
```

Pause blocks new entries and cancels pending unfilled entries. Protective exits
continue. The dashboard offers the same control. The account is isolated under
`trader/automation`; manual paper balances are not transferred. Changing initial
capital is recorded separately from trading P&L. See [settings](../config/automation.example.json).
`enable` creates runtime settings when absent. Invalid settings disable new
entries while positions retain their original protective plan and policy.

To change an existing account's capital and daily loss allowance:

```sh
python3 -m automation resize --workspace trader --capital 5000 --daily-loss 500
```

The daily limit applies immediately. New entries pause and pending entries
cancel; existing positions keep their original protective exits. Once the
account is flat, the worker adjusts simulated funding to the requested amount
and resumes automatically. Original fills, costs, P&L and daily trade counts
remain intact. A lower balance retains realized P&L: cash becomes the new funded
capital plus cumulative realized P&L. Position/order caps scale to fit the new
capital. Capital withdrawals do not count as trading losses or drawdown; the
performance curve removes funding flows, and return uses original starting
capital. The journal retains the full capital-adjustment audit trail. A restart
recovers a committed adjustment even if writing its settings was interrupted.

## Selection and scheduling

1. On each covered session from 08:45 IST, refresh the official NSE EQ list,
   excluding ETFs. Scan every saved symbol using bounded batches and workers.
   Require ≥95% successful daily retrievals; transport failures beyond that
   threshold retry after 15 minutes. Track fresh usable coverage separately.
   Insufficient histories and stale data are excluded from setups individually;
   they never become entry candidates. All failure counts remain visible.
2. Cross-match current equities against documented decimal-price ticks in the
   public Upstox CSV. Do not guess JSON tick conversions. Record source identity,
   retrieval and the conservative observed upper bound on publication time.
   Ambiguous or invalid instrument records are audited and blocked from trading;
   their stocks remain in the full daily scan.
3. Filter daily breakout, bull-flag, MA20-reclaim and oversold-bounce matches.
   Defaults require price ≥₹50, daily turnover ≥₹10 crore, disclosed rule score
   ≥35, and extension above MA20 ≤10%. Plain oversold alone is excluded.
4. Monitor every qualifying stock in new five-minute buckets. Require session
   turnover ≥₹1 crore and breakout volume ≥1.2 times the opening-range average.
   Recheck current data before submission. Rank daily setup strength and
   participation plus confirmed breakout volume; scores are not profit odds.
5. Trade from 09:30 through 14:45 IST with an opening-range midpoint stop, 2R
   target and estimated net reward/risk ≥1.5 after costs. Keep a 10% cash reserve,
   cap a position at ₹25,000, and hold at most three positions. Quantity must fit
   1% of lagged completed candle volume. Flatten at 15:10 IST.

The default worker uses bounded Yahoo reads; serial NSE fallback is opt-in.
Data may be delayed or unavailable. Position management runs independently of
the scanner with up to two priority chart workers. Missing calendar coverage or
unsupported exceptional strategy sessions block new entries.

## Simulation and standing permission

An explicit request for fully automatic paper trading authorizes a
`STANDING_PAPER_AUTOMATION_POLICY`. The ledger stores its hash. Every order still
passes sizing, exact-order numerical risk and ticket validation. Automatic
authorizations cite that policy and have no fabricated user reply. Manual
ticket approval behavior remains available separately.

Freeze quantity before observing a fill candle. Use the first permitted open
after submission and recognize it only after the candle is observed completed.
Cancel if entry data arrive more than 120 seconds late, if bars are missing,
or if an adverse gap, volume, portfolio change or limits invalidate the order.
There is no hindsight resizing. Records separate execution time and observation.

Protective levels and flatten time are fixed before future candles. Apply known
opening gaps first, stop first on ambiguous intrabar touches, and cap target
fills at the target. Charge modeled fees, taxes and slippage. A gap in position
data triggers liquidation at the next observed open, labelled `data_gap_exit`.
Never invent an exit price. Unresolved exposure blocks new entries. Overdue
liquidations, including next-session exposure, remain explicit.

The existing SQLite ledger reconciles fills, cash and FIFO lots. Restart
recovery reconstructs committed approvals and positions even when checkpoint
writing was interrupted. Repeated polls cannot duplicate a fill. Protective
fills and the journal verify standing policy hashes and original entry plans.

## Dashboard and verification

Open `http://127.0.0.1:8765/#automation` or **Auto paper trading**. Inspect worker
heartbeat, coverage, candidates, pending entries, positions, rejection reasons,
costs, P&L, return and maximum observed drawdown. Missing marks leave equity
unavailable; stale heartbeats show the worker offline. The Trading journal
selects this account once configured, with a separate manual-account option.
To keep an existing dashboard's active Codex run connected, open an additional
monitor with `python3 -m dashboard --workspace trader --port 8766 --monitor-only`.
This monitor supports paper pause/resume without modifying saved Codex runs.

Runtime settings, metadata, status, decisions and `state/paper.sqlite` live
under `trader/automation`. Ordinary scan archives preserve candle evidence and
daily/ORB bindings. Runtime files and downloaded data are excluded from release.

```sh
python3 -m unittest discover -s tests -p 'test_automation.py' -v
python3 -m unittest discover -s tests -q
npm run build --prefix dashboard
python3 scripts/build_release.py --check
```

Tests use invented candles for full-universe selection through paper fills,
stops/targets/flatten, missing data, costs, policy integrity, limits, pause and
restart idempotency. They establish code behavior, not future returns.
