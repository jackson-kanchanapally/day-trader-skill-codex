---
name: trader-team
description: Trading Team coordinator. Run the modular paper workflow end to end (scanner, news, entry, exit, time machine, budget, risk, paper broker), or, only when explicitly asked, coordinate ten independent native specialist reviews of one frozen NSE/BSE snapshot. Use for "run the full workflow", "walk me through a paper trade", "independent review", "get the team to review". Paper only; never places orders.
---

# trader-team

The team skill coordinates the other eleven. It has two modes. The default is the ordinary modular workflow, where each specialist tool hands its file to the next. The second, used only when the user asks for it, is a sealed review in which ten separate native agents each judge one frozen snapshot.

## Commands

```sh
python3 /absolute/path/to/trader-team/run.py prepare TCS.NS --provider nse --as-of 2026-10-23T10:30:00+05:30 --tick VERIFIED_INR_TICK --security-master /path/to/verified-security-master.json --days 60 --capital 100000 --risk 500 --team-dir /absolute/path/to/new-session
python3 /absolute/path/to/trader-team/run.py preflight --session /absolute/path/to/session
python3 /absolute/path/to/trader-team/run.py finalize --session /absolute/path/to/session
```

`VERIFIED_INR_TICK` is a placeholder, not a value. These commands belong to the independent review only. The modular workflow uses each role's own tool.

## Steps: the modular workflow (default)

1. Use one mutable workspace and the user's provider, capital, risk, verified tick, stop and target choices and as-of time throughout. Use saved configuration plus the user's overrides, show the assumptions in force, and ask only for required choices that are missing.
2. Follow the chain: `$trader-scanner` → `$trader-news` → `$trader-entry` → `$trader-exit` → `$trader-timemachine` → `$trader-budget` → `$trader-risk` → `$trader-broker`. Use each role's SKILL.md for its commands.
3. Add `$trader-alerts` once a plan exists, and `$trader-analyst` or `$trader-pelosi` when the user wants that research.
4. Require a current exact-order risk PASS before showing a ready paper ticket. Obtain the user's actual yes before any simulated approval or fill.
5. Say what is missing. Optional research that is unavailable stays visible and is never described as complete coverage. If the user requires evidence that is unavailable, report the task as incomplete.
6. For a single-role request, use that skill directly.

## Steps: an independent review (only when asked)

Read [references/workflow.md](references/workflow.md) before running a team. It defines the dependencies, the native delegation requirement, the artifact contract and the review schema.

1. Confirm the user asked for independent specialists, individual agent decisions or dashboard lifecycle events.
2. Run `prepare` once with `--security-master FILE` holding verified, dated INR tick evidence. It captures one immutable snapshot.
3. Inspect the returned `preflight`, or run `preflight --session DIR`. If `should_dispatch_specialists` is false, explain the root prerequisites and the dependent no-trade outcomes. Resolve them or wait for an eligible signal. Do not spend a full team run on a closed-market or no-setup snapshot unless the user wants that diagnostic.
4. Delegate each of the 10 required roles to a distinct native Codex subagent. Run scanner, news and analyst independently, then entry → exit → timemachine → budget → risk → broker → alerts. The Pelosi compatibility role stays `NOT_APPLICABLE` and needs no agent.
5. Run `finalize` only after every review exists and its identity, hashes and cutoff match.
6. Report the final status and each role's verdict. Any independent BLOCK vetoes the paper proposal, and risk must PASS for readiness.

## Rules

- Independent reviews are an explicit opt-in. They are not a prerequisite for ordinary scans, research or planning.
- A numerical risk PASS is not independent-team clearance. Do not present the two as interchangeable.
- The sealed engine supports the fixed opening-range-low stop with a configurable R multiple. If the user asks for an independent review of a midpoint, ATR or level plan, explain that boundary. Do not claim the sealed reviews validate a different plan, and do not replace a requested configured-rule workflow with the fixed-rule engine.
- Native agent delegation is required for an independent review. If the host does not offer it, report `MULTI_AGENT_UNAVAILABLE`, stop the team workflow and offer the ordinary analysis without calling it a team.
- Shell subprocesses, role-labelled output from one model and deterministic evidence are not independent agents.
- Provisional capital-based sizing is an estimate until it is checked against paper cash.
- Team preparation defaults to 60 days of NSE history. Require at least 20 complete prior sessions actually returned.
- Respect the available agent slots, and release completed agents when the host supports it.

## What it never does

It never places an order, never authorizes live execution, never invents an agent identity or a review, and never implies that a result is profitable.

## Details

- **Sealed numbers.** All numerical levels come from sealed engine artifacts. Specialists may research original NSE, BSE or issuer sources for news and financial analysis, limited to publication at or before the cutoff. Missing sources or insufficient scenario history stay `UNAVAILABLE`.
- **What the tools restore.** Broad scans, level and opening-range entries, configurable stops, polling, saved alerts and a local paper ledger. They never place orders.
- **Origin.** The upstream project supplies cooperating skills and files; it does not package a native agent runtime. This adaptation adds genuine Codex delegation, and Indian NSE/BSE data with paper-only proposals replaces the US market integrations. Parity with SEC, Congress or brokerage integrations is not claimed.
- **Running it.** Resolve this folder through symlinks and run `run.py` by absolute path. See the [workflow guide](../docs/WORKFLOW.md) for the modular commands.
