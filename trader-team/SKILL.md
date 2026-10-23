---
name: trader-team
description: Coordinate independent Codex specialist agents to review an NSE/BSE paper setup, with shared immutable evidence and a risk veto.
---

Run a cooperating specialist workflow when the user requests the full trading team, independent agent evaluation or the original day-trader workflow. For a single indicator or standalone role request, use the relevant modular skill directly.

Read [references/workflow.md](references/workflow.md) before running a team. It defines the dependencies, native delegation requirement, artifact contract and review schema. Resolve this directory through symlinks and execute `run.py` by absolute path. Require the instrument's verified tick size and use the user's capital, risk, provider and replay cutoff.

Capture one immutable data snapshot with `prepare`; delegate each of the 10 required review roles to a distinct native Codex subagent; finalize only after all reviews exist and their identities, hashes and cutoffs match. Run scanner, news and analyst independently, then entry → exit → timemachine → budget → risk → broker → alerts. Retain the Pelosi compatibility role as NOT_APPLICABLE to Indian markets; it needs no mandatory agent spawn. Respect available agent slots and release completed agents when the host supports it.

Native agent delegation is required for independent evaluation. If the host does not expose it, report `MULTI_AGENT_UNAVAILABLE` and stop the team workflow; offer the ordinary standalone analysis without calling it a team. Shell subprocesses, role-labelled JSON from one model, and deterministic evidence are not independent agents.

Keep the 11 specialist skills modular. The upstream repository supplies cooperating skills and artifacts; it does not itself package a native spawn runtime. This adaptation adds genuine Codex delegation. Indian NSE/BSE data and paper-only order proposals replace US market integrations; SEC/Congress/Alpaca parity is not claimed.

All numerical levels come from sealed engine artifacts. Specialists may independently research original NSE/BSE/issuer sources for news and financial analysis, constrained to publication at or before the cutoff. Missing sources or insufficient forecast history stay UNAVAILABLE. Any independent BLOCK vetoes the final paper proposal; risk must PASS for readiness. No report authorizes live execution or implies profitability.
