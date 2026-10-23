# Specialist team and upstream parity

The [original MIT repository](https://github.com/Jakeschincariol/day-trader-skill) provides eleven cooperating skills with file handoffs. Its plugin manifest loads skills; it does not package a native independent-subagent runtime. The early Indian migration preserved the names but reduced most roles to the same analysis command. This version restores distinct handoffs and adds actual Codex delegation through `$trader-team`.

## What runs

```mermaid
flowchart TD
  D[One frozen OHLCV snapshot] --> S[Scanner]
  D --> N[News]
  D --> A[Financial analyst]
  S --> E[Entry]
  N --> E
  A --> E
  E --> X[Exit]
  X --> T[Time Machine]
  T --> B[Budget]
  B --> R[Risk veto]
  R --> P[Paper broker]
  P --> L[Alert definitions]
  L --> F[Verified combined report]
```

Each required role receives its own native agent context and writes its own judgement. Python generates numerical evidence once, checks exact hashes/cutoffs, recomputes the budget boundary and enforces vetoes. A positive model opinion cannot override missing numerical evidence. The coordinator records IDs returned by native spawn calls; these are host provenance records, not signed identity attestation. Host permissions and concurrency apply. No shell subprocess is counted as an independent AI agent.

Detailed dispatch instructions, prerequisite reviews, source rules and JSON schema are in [the workflow contract](../trader-team/references/workflow.md). Optional profiles in `codex-agents/` use the standalone TOML format described in the [official Codex subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents). Skills are installed with `python3 install_codex.py`; named profiles are optional and are not installed automatically. A host can spawn a generic agent and give it the role's SKILL.md. Existing global configuration is not changed.

## Handoffs

| Original role | Indian implementation | Boundary |
| --- | --- | --- |
| Scanner | Supplied-symbol opening-range scan, VWAP, RSI, EMA, volume | No automatic exchange-wide universe scan |
| News | Independent reading of exchange/issuer announcements | No automated filings adapter; unavailable evidence stays explicit |
| Entry | Completed-bar breakout and confirmation | Five-minute, long-only opening-range rule |
| Exit | Tick-rounded entry, opening-range stop, reward/risk target | Other original stop/target strategies not implemented |
| Time Machine | Historical next-bar replay and seeded whole-bar bootstrap | Minimum 20 complete prior sessions; 10,000 paths are scenarios |
| Budget | INR size, costs, cash buffer, position and daily caps | Supplied paper portfolio, no broker balance inference |
| Risk | Independent specialist veto plus numerical recheck | Bound to exact snapshot and order fingerprint |
| Broker | Draft paper ticket, reviewed before release | No execution, fills or broker transport |
| Alerts | Order-bound stop, target and flatten-time definitions | No implicit scheduling, notifications or position monitoring |
| Analyst | Independent financial filing review | No port of the US SEC extraction tool |
| Pelosi | Explicit NOT_APPLICABLE artifact | US congressional disclosure ingestion excluded |

The handoff filenames are `watchlist.json`, `news.json`, `entry.json`, `plan.json`, `timemachine.json`, `orders.json`, `state/risk.json`, `state/broker.json`, `alerts.json`, `metrics.json` and `disclosures.json`. Their schema is specific to this Indian paper workflow; original US consumers are not drop-in compatible. `context.json` seals settings, source, portfolio, limits and code hash; `snapshot.json` contains only completed observations available by the cutoff. `reviews/ROLE.json` holds independent agent decisions, and `report.json` is the combined result.

The NSE adapter's maximum request is 28 calendar days, which usually supplies fewer than 20 complete prior trading sessions after excluding today and holidays. Increasing the simulation count cannot fix this. A full scenario review needs a longer, complete, consistently sourced CSV history collected separately; the short Yahoo request also cannot supply it. Ordinary intraday analysis still works without scenario clearance. News/analyst browsing must be available independently of the market-data adapter.

## Optional paper portfolio and limits

Pass `--portfolio PATH` to preparation. Values below are illustrative paper inputs, not fetched balances:

```json
{
  "as_of": "2026-10-19T09:35:00+05:30",
  "cash": 100000,
  "equity": 100000,
  "positions": [],
  "realized_pnl_today": 0,
  "trades_today": 0,
  "orders_today": 0
}
```

The snapshot must be no later than the analysis cutoff and at most 15 minutes old. Existing long positions require `symbol`, positive whole `qty`, `mark` and untriggered `stop < mark`. Marks/stops are user inputs, not independently reconciled market prices. Combined open risk includes estimated liquidation costs. Shorts and leverage are unsupported.

Defaults for ₹100,000 capital and ₹500 requested risk, overridable with `--team-limits PATH`:

```json
{
  "cash_buffer_fraction": 0.1,
  "max_position_fraction": 0.75,
  "max_order_value": 100000,
  "max_total_risk": 1500,
  "max_daily_loss": 1500,
  "max_trades_per_day": 3,
  "max_orders_per_day": 10,
  "min_net_rr": 1
}
```

Without explicit state, counts/P&L are assumed zero and holdings empty, labelled as a paper assumption. Files are not a persistent account ledger. Reprepare with current state before another proposal. Stops do not guarantee maximum realized loss during gaps.

## Final states

| Status | Meaning |
| --- | --- |
| PENDING_AGENT_REVIEWS | Missing or invalid reviewer identity, hash, cutoff or required evidence fields |
| BLOCKED | Numerical condition or any specialist veto blocks the plan |
| REVIEW_INCOMPLETE | All reviews collected, but required evidence unavailable or risk has not passed |
| READY_PAPER_TICKET | All required reviews valid, no veto, risk PASS and numerical checks pass |

Only READY_PAPER_TICKET exposes the combined paper ticket. Intermediate artifacts contain a draft for inspection, always with execution disabled. Every state submits zero orders. A blocked/incomplete result is a working safety decision, not a reason to invent research. The short fictional fixture should remain incomplete for full-team research; it still exercises entry, exit, costs and sizing.

This preserves the specialist cooperation requested by the user while adapting to NSE/BSE. Exact US behavior, broker integrations, profitability and availability are not claimed. Public feeds and multi-agent model access have their own limits; no lifetime-free service guarantee is possible.
