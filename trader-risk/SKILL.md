---
name: trader-risk
description: Inspect stale data, session eligibility, costs and risk limits.
---

Use this module for: inspect stale data, session eligibility, costs and risk limits.

Resolve this skill folder through any symlink. Run its run.py using an absolute path. User CSV/config paths remain relative to the user working directory:

```sh
python3 /absolute/path/to/trader-risk/run.py analyze RELIANCE.NS --tick 0.05
```

The tick above is an example: require the instrument's current exchange/security-master tick size. Never infer it from the last price. Use `.NS` for NSE and `.BO` for BSE provider identifiers. Accept CSV exports with `--csv PATH`; use `--as-of ISO_TIMESTAMP` only for explicit historical replay. See README for the CSV contract and cost overrides.

Read-only and paper only. No order tools or broker connections are available. Never submit, approve, cancel or modify orders. A paper setup does not authorize execution. Keep the provider, timestamp, stale status, indicator warmup limitations, and costs visible. No data means unavailable, never invented prices. Use the deterministic report's numbers; do not recalculate them conversationally.

For news and financial analysis, read original NSE/BSE announcements or issuer investor-relations filings and cite them. No automated Indian filings adapter is included. Do not route to SEC or congressional tools. Alerts require an external scheduler; this command is a single check. All cash and risks are INR. Only regular 2026 sessions are supported; special sessions are excluded.

Archived US utilities under legacy/ are excluded from this workflow. Use only the Indian CLI for numerical results.

## Module workflow

Check stale, status and plan fields. Report BLOCKED_COST_OR_RISK as blocked; NO_SETUP is not authorization to construct a trade. Explain missing bars and insufficient warmup.

The default feed is Yahoo public charts: no API key, account or data subscription is required. Requests use bounded retries and alternate Yahoo hosts. Clearly report delayed, missing or unavailable data; never fabricate candles. CSV works offline. Upstox is optional through --provider upstox and requires locally configured credentials; never request tokens in chat. Yahoo only provides a recent window, so an older --as-of requires a matching CSV or another explicitly selected source.
