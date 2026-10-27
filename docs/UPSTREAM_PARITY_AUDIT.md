# Upstream feature parity audit

## Current workflow update — 2026-10-26

The active original-workflow restoration is documented in
[ORIGINAL_WORKFLOW.md](ORIGINAL_WORKFLOW.md). G07 scanning, G08 watching and
G09 story merging now have active implementations and regressions. G10 financial
imports include bounded official NSE integrated-index/RSS/XBRL acquisition;
older archive coverage remains a source limitation. G13 controls/allocation
are expanded; external broker receipts remain an integration boundary. G14
commands/instructions/packaging are updated. The phase-one matrix below is
historical and is not the current status of those added features.

## Phase-one implementation update — 2026-10-26

The original audit and Appendix B below are retained as historical pre-fix
evidence. Current implementation evidence is in
[PARITY_IMPLEMENTATION_RESULTS.md](PARITY_IMPLEMENTATION_RESULTS.md).
The original observational probes for fixed defects are expected to stop
passing; use active `tests/test_parity_*.py` acceptance regressions instead.

| Gap | Current status | Compatibility implication |
| --- | --- | --- |
| G01 | Fixed | Sampled and historical exits share canonical event precedence and cost tests |
| G02 | Fixed | Numerical, modular and sealed default forecasts honor plan flattening; strict off-grid rejection |
| G03 | Fixed | Sourced-session closing sells retain price/tick/holding/approval gates |
| G04 | Fixed | Actual calendar clocks, next opening and per-session bell deduplication |
| G05 | Fixed | Stale daily histories excluded from current hits; historical as-of retained |
| G06 | Fixed | Optional quote/cash/exposure context and tick-aligned INR limit proposals |
| G07 | Deferred | Per-symbol fetching; no new full-market coverage claim |
| G08 | Deferred | No simulated-clock watch replay yet |
| G09 | Deferred | Cross-story merge/issuer attribution remains incomplete |
| G10 | Deferred | Financial extraction/expansion remains incomplete |
| G11 | Deferred | Dated-source reconciliation and observed liquidity validation remain incomplete |
| G12 | Deferred | Forward-paper/persisted promotion evidence remains incomplete |
| G13 | Deferred | Optional compatibility scope still needs decisions/implementations |
| G14 | Partial | Phase-one instructions, help contracts and public packaging updated |

Active feature parity remains **PARTIAL**. Broker transport, shorts, margin and
unsupported instruments remain intentionally disabled.

## Finding and scope

**Active NSE/BSE feature parity is PARTIAL.** All eleven original Python
utilities are preserved byte for byte, and the core long-entry, daily-setup,
exit-planning and risk arithmetic is substantially reused. Preservation in
`legacy/` does not make an upstream feature available through an active Indian
skill. Several operational capabilities were narrowed, omitted or replaced.
The most important confirmed gaps concern sampled execution ordering, forecast
flattening, exceptional-session exits/clocks, stale daily scans, broad-scan
fetching, historical watch replay, budget modes, news deduplication and financial
analysis depth. None requires weakening the current risk controls or enabling
live orders.

This is a source and executable-behavior audit, not a README scorecard. It
covers all eleven upstream skills, their Python tools, instructions, artifacts
and downstream consumers, plus the Indian/Codex boundaries and added architecture.
No application code, configuration, tests or skill instructions were changed.
The sole repository addition is this report. Temporary upstream checkout,
test logs, synthetic audit probes and release export were kept outside the repo.
No real broker approval, order, notification, account provisioning or credential
inspection was performed. Unit tests exercise fictional paper approvals only.

### Pinned inputs

| Input | Audited identity |
| --- | --- |
| Upstream | [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), commit [`3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd`](https://github.com/Jakeschincariol/day-trader-skill/tree/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd) |
| Upstream commit metadata | 2026-10-20; the fetched default branch resolved to the commit above |
| Local base | `f66b2ed4ad51e5c60381e97e04047e1a61683471` **plus the existing dirty working tree**, including the execution/research/regime work and pre-existing dashboard edits |
| Local audit snapshot | 247 tracked/unignored files before this report; aggregate SHA-256 of the sorted filename-to-SHA-256 JSON map: `9c81fc28ea232b760bc6f5ffe42720362b0123355282847c9440ee46c8fd8ff2` |
| Python | The available Python 3 runtime; CLI help identifies Python 3.14. Other advertised Python/OS combinations and hosted CI were not executed in this audit |

Reproducing against local HEAD alone will **not** reproduce this working-tree
audit. Retain the audited working tree or its source export. The report is not
an assessment of any later upstream revision.

### Classification rules

- **FULL**: the named, bounded capability has an active Indian implementation
  and executable evidence. This does not imply real-provider reliability,
  identical US/Indian numbers or production certification.
- **PARTIAL**: an active implementation exists, but an upstream sub-capability,
  operational contract or necessary Indian boundary is incomplete.
- **MISSING**: no active Codex/Indian equivalent was found. Archived functions
  and upstream tests alone do not change this classification.
- **INTENTIONALLY DIFFERENT**: a documented market, host, execution-policy or
  safety replacement. These differences are not automatic implementation tasks.

An overall skill rating does not override its more precise capability rows.
An audit probe that confirms a defect **passes as an observation**; it does not
mean the defective capability has achieved parity.

## 1. All original skills: source and instruction mapping

Each upstream link below is pinned. The Indian `trader-*/<tool>.py` files are
small entrypoint adapters into `workflow.cli.main(role)`, not equivalent copies
of the upstream programs. Their companion `run.py` routes ordinary commands
through `workflow.cli.dispatch`; `analyze`/`evaluate` use the separate numerical
and sealed-review modes through `india/skill_runner.py`.

| Original skill and pinned evidence | Active Codex equivalent and reused source | Overall assessment |
| --- | --- | --- |
| Scanner: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-scanner/SKILL.md), [scan.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-scanner/scan.py) | [Codex instructions](../trader-scanner/SKILL.md), `trader-scanner/scan.py`, `workflow/strategy.py:scan`, `workflow/data.py:universe`, `legacy/trader-scanner/scan.py` | **PARTIAL**: daily rules and ORB checks survive; batching, two-stage coverage, sorting and directory exclusions differ or are absent. |
| Entry: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-entry/SKILL.md), [entry.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-entry/entry.py) | [Codex instructions](../trader-entry/SKILL.md), `workflow/strategy.py:entry`, `workflow/cli.py` entry branch, `legacy/trader-entry/entry.py:evaluate` | **PARTIAL**: completed-bar ORB/level checks work; foreground polling replaces the richer original watcher and simulated-clock replay. |
| Exit: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-exit/SKILL.md), [exit.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-exit/exit.py) | [Codex instructions](../trader-exit/SKILL.md), `workflow/strategy.py:plan`, `legacy/trader-exit/exit.py:compute/atr_of` | **FULL** for long stop/target planning: all five original stop types, R/price targets, explicit inputs and plan/show have active evidence. Tick/session changes are deliberate. |
| Time Machine: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-timemachine/SKILL.md), [timemachine.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-timemachine/timemachine.py) | [Codex instructions](../trader-timemachine/SKILL.md), `workflow/strategy.py:timemachine/historical_replay`, `india/forecast.py`, `india/execution.py` | **PARTIAL**: seeded scenarios/history exist; ordering, one forecast horizon, original controls and output diagnostics have gaps. |
| Budget: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-budget/SKILL.md), [size.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-budget/size.py) | [Codex instructions](../trader-budget/SKILL.md), `workflow/paper.py:budget`, `india/execution.py:size_position`, archived `size.py` | **PARTIAL**: stronger cost-aware single-trade sizing; limit-order creation, weighted allocation and proposal-time account/quote inputs are not preserved. |
| Risk: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-risk/SKILL.md), [check.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-risk/check.py) | [Codex instructions](../trader-risk/SKILL.md), `workflow/paper.py:risk/ticket`, `legacy/trader-risk/check.py:check` | **PARTIAL**: original cash/long checks plus stronger Indian controls; exceptional-session strategy guard also rejects closing sells. |
| Broker: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-broker/SKILL.md), [ticket.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-broker/ticket.py) | [Codex instructions](../trader-broker/SKILL.md), `workflow/paper.py:Ledger/ticket`, `workflow/cli.py` broker branch | **INTENTIONALLY DIFFERENT**: transactional local paper ledger replaces the external connector handoff. Partial execution fills remain unsupported. |
| News: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-news/SKILL.md), [news.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-news/news.py) | [Codex instructions](../trader-news/SKILL.md), `workflow/research.py:news`, `workflow/cli.py` news branch, archived RSS/tag parser | **PARTIAL**: dated RSS, tags, moves and stable-ID state work; cross-story merging, Indian mention matching and automatic filing collection do not match upstream. |
| Alerts: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-alerts/SKILL.md), [alerts.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-alerts/alerts.py) | [Codex instructions](../trader-alerts/SKILL.md), `workflow/cli.py` alerts branch, `legacy/trader-alerts/alerts.py:evaluate/rules_from_plan` | **PARTIAL**: core persistent rules survive; special-session clock metadata and next-opening information are incomplete. |
| Analyst: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-analyst/SKILL.md), [facts.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-analyst/facts.py) | [Codex instructions](../trader-analyst/SKILL.md), `workflow/research.py:financials`, `workflow/cli.py` analyst branch | **PARTIAL**: sourced normalized-quarter analysis exists; original extraction, discovery and several analytical outputs are archive-only. |
| Pelosi: [instructions](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-pelosi/SKILL.md), [ptr.py](https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-pelosi/ptr.py) | [Codex instructions](../trader-pelosi/SKILL.md), `workflow/research.py:disclosures`, `workflow/cli.py` pelosi branch | **INTENTIONALLY DIFFERENT**: `NOT_APPLICABLE` or supplied Indian public-insider CSV; no claim of an Indian Congress equivalent. |

### Instruction and host changes

The eleven original skill descriptions and operational sections were compared
against their local `SKILL.md` files, not just their filenames. Claude-specific
`${CLAUDE_SKILL_DIR}`, plugin/slash-command installation, Alpaca/IBKR setup and
the host approval card have been replaced by absolute symlink-resolved Codex
entrypoints, the installer and explicit paper-only boundaries. That is a
deliberate host adaptation, not preservation of those external integrations.
The local instructions generally disclose foreground watches, manual input
exports, incomplete RSS, JSON charts and no automatic notification delivery.
They also add frozen-evidence and source-trust requirements for team reviews.

`trader-team` is a twelfth, added skill; upstream did not package a native agent
spawning runtime. Its instructions distinguish the configured modular workflow
from the fixed range-low sealed numerical engine. The audit read these
instructions as source material; it did not invoke a team or fabricate native
reviewers. See `install_codex.py`, `trader-team/references/workflow.md`,
`india/team.py`, `tests/test_repo.py`, `tests/test_team.py`.

## 2. Capability matrix

Evidence codes **A01–A16** refer to the complete reproducible audit probes in
Appendix B. **W** means `tests/test_workflow.py`; **E** means
`tests/test_execution_policy.py`; **V** means `tests/test_volume_calendar.py`.
Named upstream test files are in the pinned upstream checkout; local original
skill test files load `legacy/` through `tests/_load.py`.

### Scanner, entry and exits

| Capability | Classification | Source evidence and executable evidence |
| --- | --- | --- |
| Original Python utilities retained | FULL, archival scope | All eleven `legacy/trader-*/*.py` files match pinned upstream bytes, A01. This is not active-path certification. |
| NSE/BSE directory discovery, exact `.NS`/`.BO` identities | PARTIAL | `workflow/data.py:universe`; W `Sources.test_universe_filters_derivatives_and_non_equity`; Upstox BSE resolver tests. No full entitled-market coverage run or dated eligibility reconciliation. |
| Original ETF/unit/warrant exclusions | PARTIAL | Upstream `scan.py:classify/parse_symdir`; local directory checks EQ/lot/security-type but no equivalent product/name classifier. A06 includes an EQ-typed synthetic ETF. No claim that every real provider ETF has that encoding. |
| Five daily setup formulas, Wilder RSI | FULL, calculation scope | Upstream `scan.py:find_setups/rsi` reused by `workflow/strategy.py:scan`; A03 executes breakout, bull flag, MA20 reclaim, oversold and bounce cases against both modules. |
| Current daily-bar exclusion, >=30-bar warmup | FULL, bounded behavior | `workflow/strategy.py:scan`, `workflow/data.py:history`; W daily scan and future-row tests. Current-day daily bars are always excluded, a conservative difference from upstream post-close inclusion. |
| Daily recency and complete requested-session coverage | PARTIAL | Last supplied prior close can be more than a month old and still become a hit, A04. Historical research has stricter completeness; scanner does not reuse it. |
| Daily/ORB broad-universe batching | MISSING in active scanner | Upstream `fetch_spark` batches 20 symbols; local `scan` loops `data.history` once per symbol, A05. Per-instance caching/directory reuse helps repeated work but is not batching. |
| Two-stage ORB candidate pass, ranked confirmation cap and explicit skipped count | MISSING in active scanner | Upstream `orb_candidate/scan_orb`, `--max-confirm`; local full-OHLCV per-symbol loop has no stage-1 or confirm-cap equivalent. Correct confirmed ORB detection is retained. |
| Turnover ordering, detailed fetched/liquid/usable/short/candidate counts | PARTIAL | `workflow/strategy.py:scan` keeps input hit order and `universe/scanned/failed/results`; upstream sorts by turnover and separates stages. A05; W missing-symbol test. Dashboard heuristic ranking is a separate product behavior. |
| Failure retention, scan limits, no forced candidate | FULL, offline scope | Local `scan`, `workflow/cli.py:symbols`; W `test_scan_preserves_unavailable_symbols`, scanner CLI handoff, opportunity tests. Empty completed scan is distinct from provider failure. |
| First completed close above OR high; equality/wick not a trigger | FULL, long rule | Archived `entry.py:evaluate` used by local `entry`; W entry/forming/missing tests; E parity; upstream `tests/test_entry.py`. |
| Explicit price-level entry and watchlist checks | FULL, calculation/handoff scope | Local `entry(...setup='level')`, CLI `watchlist`; W `test_level_entry`; original level evaluator retained. Live multi-symbol watch acceptance is not demonstrated. |
| 1m and 5m entry bars | PARTIAL across providers | Active evaluator and Yahoo/CSV parsing support both; W one-minute parsing and V one-minute RVOL. `NSEProvider.history` and `UpstoxProvider.history` explicitly accept only 5m/1d. |
| Volume confirmation | INTENTIONALLY DIFFERENT | Upstream compares trigger volume with opening-range average. `india/volume.py` and local `entry` use prior-session time-of-day RVOL with warmup/missing handling, V. Threshold values are not numerically interchangeable. |
| Signal freshness, missing bars, expired/forming status | FULL, tested boundaries | `workflow/strategy.py:regular/entry`; W, E exact-second expiry and V causal cutoffs. `eligible` must be inspected separately from historical `status='triggered'`. |
| Foreground entry monitoring | PARTIAL | Local CLI polls with `--every/--max-checks`, exits immediately on no-data, and prints snapshots; original `watch_loop` has simulated clock, per-bar emissions and consecutive-read retries. Active multi-poll/stop/recovery behavior lacks equivalent integration tests. |
| Fast offline/historical watch replay | MISSING | Original `SimClock/cmd_watch`; upstream `Cli.test_watch_replays_the_file_and_writes_the_entry`. Local pinned-clock watch requires `--max-checks 1`, A07. |
| Automatic trigger file from watch | INTENTIONALLY DIFFERENT | Local instructions require `--write`; upstream writes on a watch trigger automatically. A08 confirms local default. Callers must adapt, not assume an `entry.json` exists. |
| Midpoint, range-low, price, percent and ATR stops; R/price targets | FULL, long planning scope | `workflow/strategy.py:plan` calls archived `parse_stop/parse_target/compute/atr_of`; W stop variants, explicit target, invalid stop and ATR lookahead tests. |
| Tick-aware directional levels and minimum stop-distance guard | INTENTIONALLY DIFFERENT, implemented | Original cents become supplied INR ticks; `tick_price`, local `rounded`, patched Decimal helpers and upstream `compute` keep positivity/tight-stop checks. E tick-noise tests; W tick alignment. |
| Plan/show and explicit-price inputs, configurable early flatten | FULL, regular-session scope | `workflow/cli.py` exit branch, `plan`; W handoffs and E flatten-grid tests. Evening/short sessions are explicitly rejected for this ORB strategy. |

### Time Machine, sizing, risk and paper execution

| Capability | Classification | Source evidence and executable evidence |
| --- | --- | --- |
| 10,000 seeded remaining-session samples and prior-only pools | FULL, scenario mechanism | `workflow/strategy.py:timemachine`, `india/forecast.py:_simulate`; W forecast seed/coverage, `tests/test_forecast.py` default-ten-thousand and leakage tests. Samples are not independent historical observations. |
| Prior complete-session warmup and missing-history refusal | FULL | Both implementations require >=20 complete prior sessions. Local pools require 72 bars, max120 sessions; W and forecast missing/partial tests. Actual source delivery is not guaranteed. |
| Configured-rule historical replay | FULL, declared long/5m scope | `workflow/strategy.py:historical_replay` re-evaluates ORB/level and original stop choices; W history/ATR tests, E shared-fill parity. Research experiment tests add OR/filter sensitivity. |
| Historical fill assumptions and Indian costs | INTENTIONALLY DIFFERENT | Original historical Time Machine enters at signal close. `india/execution.py` sizes first, fills next available open, checks gaps/ambiguity and revalidates fixed quantity. E and golden tests establish the changed rule. Original TSLA odds are not parity targets for NSE/BSE. |
| Scenario gap/stop/target event ordering | PARTIAL; confirmed mismatch | `_simulate` checks low against stop before considering an opening gap above target; canonical `exit_fill` prioritizes the known open. A13 yields 10/10 scenario stops versus a canonical target on the same OHLC relation. |
| Scenario flatten boundary | PARTIAL across entrypoints | Modular `timemachine` slices to plan `flat_by`, W. Numerical `india.forecast.forecast` discards that field and samples to 15:15; A14 supplies 10:00 and still gets 15:15. |
| Configurable resampling window, histogram and verdict | PARTIAL | Original `--window`, 24-bin `hist`, `verdict/lean` are not active equivalents. `_simulate` hardcodes +/-2 slots and lacks `hist`/verdict, A14. A calibrated conclusion must not be substituted with marketing probabilities. |
| Chart bands/sample paths/terminal display | INTENTIONALLY DIFFERENT, partly reduced | Active chart is portable JSON. Original separate 2,000 band paths and 160 sample paths become up to1,000 band paths and 12 examples; no original ASCII chart. Disclosed in `docs/WORKFLOW.md`; numerical mechanism tested in forecast suite. |
| Long/short mirrored scenarios | INTENTIONALLY DIFFERENT | Original Time Machine can mirror sell plans; active runtime remains long-only and rejects unsupported configuration, A16. No restoration of short execution is proposed. |
| Cost-inclusive whole-share single-plan sizing | FULL, declared constraints | `india/execution.py:size_position`, `workflow/paper.py:budget`; W sizing, `tests/test_liquidity.py` spread/cash accounting and E reserve/fill-risk tests. |
| Fresh quote and current cash in initial sizing proposal | PARTIAL | Original `size_trade(...price,cash)`/`run_plan` consumes price/portfolio files; active budget uses configured starting capital and plan only. Risk/ledger recheck actual cash and price later, so this is proposal quality, not evidence of a fill bypass. |
| Limit-order generation from budget CLI | MISSING | Original `size.py --limit [PRICE]`; active budget parser has no limit option and `budget` creates market entries. A09. Handcrafted limit tickets can be checked, but that is not equivalent proposal generation. |
| Weighted allocation, top-N, cap redistribution, idle cash | MISSING in active Indian skill | Original `parse_weights/allocate/whole_shares/size`, `--weights/--targets/--top` remain archived. A09 and upstream budget weight tests. Local instructions deliberately narrow the role to one day trade, but an INR counterpart is absent. |
| Blocked symbols, formats, stops, RR, cash/position/order caps, duplicates and daily limits | FULL, cash-long rules | `workflow/paper.py:risk` calls original `check`, then adds tick/cost/age rules. W cost bypass, stale price, duplicate/account tests; legacy risk tests prove original code only. |
| Entry-only window/day checks with closing sells still allowed | PARTIAL | Original exit checks downgrade entry-only limits to warnings. Active `cal.require_strategy_session` runs before examining sides; A15 rejects a closing sell in a recognized evening session. |
| Exact order/plan/config/portfolio binding and freshness | FULL, local paper scope | `order_hash`, `risk`, `ticket`, `Ledger`; W edited-plan/config/portfolio tests and E observed fill tests. Rechecking a current quote is stronger than accepting yesterday's PASS. |
| Options, margin, shorts, fractional orders, PDT and extended instruction enablement | INTENTIONALLY DIFFERENT | Active `config`/risk fail unsupported settings instead of enabling US flags; A16, W US-instruction tests. Preserve this boundary. |
| Explicit exact-ticket consent, decline, local fills, FIFO lots, cash/P&L log | FULL, offline paper mechanism | `Ledger.approve/fill`, CLI show/decline/fill/flatten/log/portfolio; W rollback, duplicate, rejection, sell/FIFO/cash tests and E round trip. Tests use synthetic consent; no actual user approval was recorded during audit. |
| Partial execution fills and broker status reconciliation | PARTIAL | One full fill per approved quantity only; positions can be partly closed through a separate approved sell. Upstream `result`/manual fill and external broker acknowledgements have no corresponding active reconciliation path. |
| Alpaca/IBKR order handoff and host second approval | INTENTIONALLY DIFFERENT | Original Python formats/prints connector input; Claude executes the external tool. Active config permits only `local-paper` and contains no broker transport. This is a required safety difference, not a request to add live execution. |
| Protective brackets automatically held/executed by broker | INTENTIONALLY DIFFERENT | Local bracket fields are plans, not resting protective orders. Exits require explicit paper action; no automatic stop daemon. Upstream depended on an external broker bracket after its connector handoff. |

### News, alerts, financials, disclosures and Codex operation

| Capability | Classification | Source evidence and executable evidence |
| --- | --- | --- |
| Watchlist/plan/portfolio symbol handoff | FULL, local files | `workflow/cli.py:symbols` reuses original `watch_from_files`; W complete handoff and command tests. Ledger exports, not an external account, provide portfolio context. |
| Dated RSS headlines, newest first, ID-based persistence and error reporting | FULL, bounded input behavior | `workflow/research.py:news`; W source cutoff/dedup tests and A11 repeated-call check. Remote coverage/reliability is unverified. |
| Same-story dedup by ID/link/title across symbols/feeds | PARTIAL; regression | Original `dedupe` merges the story and touches; adapter does not call it and keys state by symbol+ID. A11 produces two items where upstream produces one. |
| Event tags and Indian company/venue mention matching | PARTIAL | Adapter adds RBI/SEBI/NSE/BSE event keywords but reuses US-oriented `mention_patterns`, passing names as `None`. `NSE:TCS`/company-name cross-mentions are not a complete Indian equivalent. Source inspection; active tests only establish basic item flow. |
| Automatic SEC forms/earnings release collection | INTENTIONALLY DIFFERENT for SEC; Indian counterpart PARTIAL | Original `collect` fetches SEC feeds/forms. Adapter accepts issuer/exchange announcement JSON instead, with no automatic authoritative Indian collector. This boundary is disclosed; original API is not applicable to Indian filings. |
| Stock moves and threshold flagging | PARTIAL | CLI computes completed-candle moves; no original `--move-pct` filter and no official previous-close reconciliation. Code labels `previous_completed_session_candle_close`; source/staleness errors remain visible. |
| Strict publication cutoff | INTENTIONALLY DIFFERENT, stronger | Adapter excludes all later publications; original `select` tolerates ten minutes ahead. Preserve causal cutoff, W and research/team tests. |
| Price/stop/target/percentage/time/opening-bell alert evaluation | FULL, regular-session/local-rule scope | Original evaluator and `rules_from_plan` reused; W persistent CLI alert plus legacy pure-rule tests. Price inputs are completed candles, not ticks. |
| Persistent once-only state, rearm, list and reset | FULL, evaluator/file mechanism | CLI alert state JSON plus original `evaluate`; W repeated checks and upstream `tests/test_alerts.py:Rules`. Multi-process notification delivery is not demonstrated. |
| Session clock, exceptional hours and next opening | PARTIAL | `Calendar.hours` supports explicit sessions, but `workflow/cli.py:market_status` hardcodes 09:15/15:30 and has no next-opening field. A10. Strategy adapters also patch legacy session clocks to fixed daytime hours. |
| Stooq quote option and general rule/state path flags | INTENTIONALLY DIFFERENT | Indian CSV/Yahoo/NSE/Upstox inputs plus `--workspace` replace the original US Stooq path and separate files. Some optional rule flags such as custom ref/before-close are not exposed by the active add CLI. Existing JSON evaluator support is not CLI parity. |
| Background alerts, push/Telegram delivery | MISSING as packaged service in both | Both provide checks and scheduling instructions, not a built-in notification transport. Local docs explicitly disclose this. Do not count it as a newly lost upstream daemon. |
| INR units, reporting basis, quarter growth, EPS and optional margins | FULL, supplied-export arithmetic | `workflow/research.py:financials`; W units/YoY/YTD/cutoff tests; A12 exercises positive gross/operating/net margins. Supplied HTTPS source is not independent filing authentication. |
| Four contiguous-quarter TTM revenue and profit | FULL, supplied-export arithmetic | `financials` checks contiguous periods; A12 proves 400 revenue/40 profit on four fictional quarters. W's two-quarter test alone would not prove this positive case. |
| TTM diluted EPS, QoQ/narrative changes and stale-filing comparison | MISSING in active analyst | Original `analyse/changes/stale_note`; active result lacks these outputs, A12. Automatically summing EPS needs explicit share/restatement conventions. |
| Company facts, YTD/Q4 derivation, issuer/filing/exhibit discovery | MISSING as Indian automation | Original `facts_for/quarterly/series/latest_filings/exhibit_name` are archived; active analyst requires a normalized `--filing` JSON. Manual source reading in instructions is not an extractor implementation. |
| Multi-company analyst batch and supplied earnings-call review guidance | PARTIAL | Original CLI analyzes multiple tickers and instructions cover pasted transcripts. Active analyst analyzes one symbol; the generic extra `items` positionals are not used by its branch. Deterministic quarter analysis exists, but equivalent transcript-specific workflow is not carried through. No claim of a coded upstream transcript parser. |
| House index/new/fetch/PDF parsing, option terms, filing delay and holdings flags | INTENTIONALLY DIFFERENT for market; MISSING as active optional US mode | Archived `ptr.py` retains these functions; active `index/check` is Indian CSV or NOT_APPLICABLE. Original `new/fetch/parse` are not active aliases. No Indian politician-trade analogue is invented. |
| Dated Indian public-insider exports and once-per-record state | FULL, supplied CSV scope | `workflow/research.py:disclosures`; W cutoff/quantity/source/dedup tests. No comprehensive feed or PDF extraction claim. |
| Codex installation, twelve modular skills, portable entrypoints | FULL, filesystem/CLI scope | `install_codex.py`, wrappers, A02 and `tests/test_repo.py` installation/idempotence/conflict/foreign-directory tests. Claude marketplace/package syntax is intentionally not retained. |
| Native independent specialist workflow | PARTIAL acceptance evidence; added architecture | `india/team.py` validates distinct IDs, hashes, prerequisites, sources and vetoes; 22 local team tests use mocks/artifacts. Actual host-spawned multi-agent execution was not run in this read-only audit. A Python role subprocess is not independent evidence. |
| Dashboard, research store, promotion/operations | PARTIAL, added capabilities rather than upstream substitutes | `dashboard/`, `research/`, `india/audit.py`, `india/job.py` have active tests. They do not restore missing scanner flags or filing automation. Forward-paper promotion evidence and live eligibility remain blocked. |

## 3. Data flow and operational contracts

Upstream's cooperation is a file-handoff workflow, not a Python service that
automatically executes every role. The Indian modular path preserves the main
handoffs with different implementation boundaries:

```text
directory/universe.txt -> scanner -> watchlist.json
watchlist/portfolio/plan -> news -> news.json + state/news.json
watchlist/symbol -> entry -> entry.json (--write)
entry.json -> exit -> plan.json
plan + history -> timemachine -> timemachine.json (--write)
plan + configured capital -> budget -> orders.json (--write)
orders + ledger + fresh candles + dated ticks -> risk -> state/risk.json
exact current risk PASS -> broker show -> actual user approval -> local full fill
local ledger -> portfolio.json / journal; plan -> persisted alert rules/checks
```

`tests/test_workflow.py:Commands.test_original_command_handoffs_to_ready_paper_ticket`
executes scanner, entry, exit, sizing, risk and ticket display. It deliberately
stops before an approval. News and Time Machine are separate optional checks,
not mandatory standalone risk prerequisites, consistent with the distinction
between cooperating skills and a single automatic orchestrator.

Important contract differences:

1. `state/paper.sqlite` is authoritative; editing `portfolio.json` does not
   reconcile a real account. Broker exits do not automatically follow alert rings.
2. Missing watch `--write` means no entry handoff (A08); fixed-clock watching
   cannot replay a session (A07). Shell return codes are not identical: for
   example a complete empty local scan can exit0, while upstream empty scans
   exit1; local alert checks use errors rather than “something rang” as failure.
3. Risk and ticket checks are stronger, but initial budget proposals are not
   account-aware. A rejected later ticket must not be described as funded sizing.
4. Sealed team artifacts are a different path from mutable CLI handoffs. The
   team's fixed 15m range-low engine does not certify a midpoint/ATR/level plan.
5. News/analyst positive source verdicts in sealed team mode are stricter than
   the standalone export parsers. The latter validate source fields, not domain
   ownership or the truth of an issuer's supplied numbers.
6. Repeated checks require an explicit foreground watch or operator scheduler.
   `india/job.py` is one bounded health job, not a full-market streaming engine.

## 4. NSE/BSE verification

| Boundary | Verified behavior and unresolved evidence |
| --- | --- |
| Timezone | Bar timestamps normalize to aware IST; duplicates/order/OHLCV checks are in `india/data.py:validate`. Original-style naive CLI clocks intentionally mean IST (`workflow/common.py:clock`); prefer offsets. NSE wall-clock decoding has dedicated synthetic/provider tests. US DST helpers remain archived, not Indian calendar rules. |
| Market hours | `Calendar.hours` regular window is 09:15–15:30. Strategies intentionally use 09:15–15:15 with earlier default flatten15:10. NSE's [market timings](https://www.nseindia.com/static/market-data/market-timings) and [3 August 2026 CAS release](https://nsearchives.nseindia.com/web/pressrelease/2026-08/PR_cc_03082026_20260803232411.pdf) distinguish continuous trading and phased auction coverage. The universal earlier strategy cutoff is conservative; it is not a complete dated instrument-specific auction calendar. A10/A15 identify exceptional-session inconsistencies. |
| Holidays | The bundled 16 weekday NSE dates match the base [CMTR71775 calendar](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf) plus [CMTR72260's January15 addition](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf). Weekend handling is explicit. BSE copies NSE dates/source in `config/calendar.2026.json`; independent BSE holiday/exception completeness was not established. |
| Coverage and special sessions | `Calendar` warns near expiry, rejects uncovered/expired years and can represent sourced weekend/Muhurat windows; V tests prove this. Only2026 is bundled, versus upstream2026/2027 NYSE coverage. No actual evening windows are bundled. ORB rejects unsupported windows safely, but alerts and closing sells need the fixes above. |
| Ticks | `india/security.py` requires dated INR tick units and rejects mismatches, future publications and synthetic metadata for real symbols. W verifies units/date and risk blocking. A universal0.05 is not justified: [NSE CMTR62174](https://nsearchives.nseindia.com/content/circulars/CMTR62174.pdf) describes price-linked monthly ticks; BSE's [2025 equity master circular](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=8a7fec3a-95bc-4bd2-82de-76e2a84e2915&id=20250429-51) documents differing categories. Those dated documents illustrate why current instrument evidence is required, not a certified2026 tick resolver. Directory raw ticks are deliberately not silently converted. |
| Transaction costs | `india/market.py:costs` includes brokerage/cap, sell STT, buy stamp, exchange charge, SEBI, GST, slippage and optional spread. NSE `exchange_rate=0.0000307` equals307/crore; [NSE FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf), effective1 March2026, has306.99 transaction charge plus0.01 IPFT per crore each side. The aggregate matches; its internal label does not separate IPFT. BSE0.0000375 and all broker/product/tax applicability were not independently certified for every security/date. Model arithmetic is tested, not a complete tariff/contract-note reconciliation. |
| Cost arithmetic example | For fictional100 shares bought100/sold110: NSE brokerage6.30, exchange0.6447, SEBI0.021, STT2.75, stamp0.30, GST1.253826, slippage10.50; total21.769526 INR. BSE default total21.93803. W and liquidity tests check one-way sums and explicit half-spread on each leg. Defaults are intraday estimates, not delivery/overnight tariff coverage. |
| Liquidity | `india/liquidity.py` offers an opt-in lagged-completed-volume proxy in research; entry rejects oversize, unresolved exit capacity retains marked losses and blocks promotion. Twenty liquidity tests pass. It is not observed spread/depth, queue priority, market impact, combined-order capacity, circuit/ASM/GSM screening or a paper-ledger live-liquidity check. These remain gaps, not fabricated validation. |
| Provider failure behavior | `workflow/data.py` caches by symbol/interval, paces requests, records attempts and selects a whole snapshot. Yahoo→NSE fallback applies to supported `.NS` only; BSE never substitutes NSE. NSE/Upstox retries are bounded; auth errors are tested for redaction. W, `test_nse`, `test_upstox`, `test_yahoo` and hardening tests pass offline. If every intraday snapshot is incomplete, downstream entry still blocks missing bars. Daily recency is weaker (A04). |
| Actual source availability | This audit made no price-feed calls or entitled full NSE/BSE scan. It did not validate rate-limit quotas, directory counts, sustained availability, real tick accuracy, source rights, complete historical universes or profitable expectancy. Primary exchange documents were read only to check market conventions. |

## 5. Documentation and test-coverage findings

The current documentation is generally explicit about experimental/paper-only
limits. Nevertheless these statements need narrower interpretation or follow-up:

| Claim or implied expectation | Finding |
| --- | --- |
| `docs/WORKFLOW.md`: roles, commands and file flow are restored | Main handoffs work; literal command/capability restoration is not complete. Budget limit/weights, watch replay, Time Machine controls and other original flags are absent. The same guide warns about unsupported flags, but needs an explicit compatibility table. |
| Twelve skill names installed, original tests pass | Not active parity evidence. A02 proves help/dispatch only. `tests/_load.py` points original tests at `legacy/`, not the Indian wrappers. |
| Time Machine applies gaps and the configured flatten boundary | True for parts of modular replay; not uniform across `_simulate`, canonical exits and numerical forecast (A13/A14). Separate scenario labeling does not remove the ordering/horizon discrepancy. |
| Calendar supports exceptional windows | Calendar object does; active alert metadata and closing-sell handling do not propagate them consistently (A10/A15). |
| Broad NSE/BSE scan | A multi-symbol loop exists, not upstream batch/two-stage coverage or demonstrated full-market execution. A05 is a call/order observation, not a large-universe latency benchmark. |
| Financial TTM | Positive four-quarter revenue/profit case is implemented and verified by A12. TTM EPS, QoQ narrative, stale-filing comparison and filing extraction are absent. Optional gross/operating margins were not asserted by the existing W financial tests; A12 exercises them during this audit. |
| Paper broker fill instructions | `trader-broker/SKILL.md` lists fingerprint/quantity/price/ID but omits the now-required observed buy candle in that paragraph. `docs/WORKFLOW.md` and CLI `--fill-bar` help explain it correctly. This is a local instruction gap, not permission to use arbitrary signal-close prices. |
| Independent agents/dashboard lifecycle | Artifact/identity checks have mocked tests; they do not prove real host scheduling, reviewer independence or unattended operation. No native team was started during audit. |
| Phase1 laboratory/promotion | Store, filters, spreads, regimes and uncertainty are implemented/tested. Real-data validation, forward-paper promotion evidence and persisted promotion decisions remain incomplete and are already disclosed. Their presence is not proof of edge or automatic live eligibility. |

The local original-role test suites contain301 tests targeting archived tools;
the remaining304 tests exercise current runtime/research/dashboard/repository
contracts. Upstream has315 tests:304 role tests plus11 repository tests. Three
role tests not retained locally are the Claude desktop config example test and
the entry/exit original `test_skill_md` checks; upstream repository tests were
replaced with Codex checks. This is packaging/instruction coverage loss, not
evidence that the underlying original trading tests were silently discarded.
The separate upstream run executed all315 successfully.

## 6. Improvements to retain

1. **Paper-only boundary:** `workflow/common.py:config` rejects broker/live,
   short, margin, option and fractional enablement. Retain exact user approval;
   do not restore original permissive broker capabilities as an audit “fix.”
2. **Canonical event clocks and risk:** `india/execution.py` distinguishes signal,
   confirmation, submission and fill, sizes with a reserve and costs, rejects
   hindsight resizing, and checks gap/stop ordering. Keep shared replay/paper
   behavior; close the remaining scenario divergence rather than duplicating it.
3. **Transactional paper accounting:** SQLite approval/fill/cash/FIFO updates,
   immutable exact-order fingerprints, full-state rechecks and rollback are
   stronger than loose JSON records and unbound manual fills.
4. **Causal input validation:** aware IST bars, duplicates/gaps/finite values,
   dated tick provenance, historical RVOL, cutoff checks and bounded retries.
   Preserve whole-snapshot provider provenance and the NSE/BSE identity boundary.
5. **Research reproducibility:** append-only vintages, point-in-time eligibility,
   split/bonus evidence, explicit regimes, walk-forward partitions, day-block
   intervals, sensitivity reporting and read-only promotion checks. Do not turn
   a positive sample or model opinion into permission to trade.
6. **Honest capability reporting:** unavailable history/filings remain unavailable;
   data rights and hypothetical liquidity remain explicit. Preserve these limits
   while implementing ingestion, rather than inventing India replacements.
7. **Codex review integrity:** sealed snapshots, code/artifact/source bindings,
   distinct native reviewer IDs, prerequisite checks and independent vetoes.
   Expand configured-plan support only with matching contracts and tests.
8. **Operations and distribution:** one-shot health checks, locks/deadlines,
   audit/replay records, read-only loopback dashboard and allowlisted public
   export. Captured upstream fixtures are kept out of the public source export;
   do not relabel them as synthetic or copy them into this report.

## 7. Prioritized implementation roadmap and acceptance criteria

P1 means correctness or a core operational gap to resolve before claiming a
reliable end-to-end Indian paper workflow. P2 means functional parity/data-depth
work. P3 means optional compatibility/presentation. These are follow-up changes;
none was implemented during this audit. No P0 live-execution bypass was found
in the audited active paths; that is not a security certification.

| ID / priority | Gap, reproduction and required acceptance |
| --- | --- |
| G01 / P1 | **Unify scenario event ordering.** A13. Route modeled exits through an equivalent canonical open-gap/stop/target ordering. Test opening below stop, above target, exact levels, both intrabar touches, timed close, costs and loss beyond1R. Historical and sampled engines must agree on identical deterministic paths while preserving stop-first ambiguity when ordering is unknown. |
| G02 / P1 | **Honor plan flatten across numerical/team forecasts.** A14 versus W modular flatten test. Retain `flat_by` in the numerical plan, calculate remaining slots consistently and verify no path, band or P&L consumes bars after that boundary. Test default15:10, earlier10:00, off-grid time, stale signal and no remaining bars. Keep zero-latency scenario assumptions explicit. |
| G03 / P1 | **Separate closing-position risk checks from entry-strategy session support.** A15. A valid closing DAY sell in a sourced special session must not require an ORB-compatible daytime session. Retain tick, held quantity, cash/account integrity and approval requirements; keep unsupported new entries blocked. Include holiday, closed-session policy, partial position exit, stale inputs and missing metadata tests. |
| G04 / P1 | **Propagate actual calendar hours to alerts and next-open reporting.** A10. Use `Calendar.hours` throughout clock/opening-bell/time rules; report actual exceptional open/close and next supported trading session. Test a synthetic weekend evening, shortened session, holiday, coverage expiry and one-ring-per-event behavior; do not invent a Muhurat schedule. |
| G05 / P1 | **Make scanner recency explicit.** A04 and W daily setup test. Compare latest daily date with the expected completed session for the supplied calendar/cutoff; reject or clearly mark stale data as non-current, without presenting an old setup as today's opportunity. Include missing whole days, exchange holidays, as-of boundaries and deliberately stale per-symbol histories. Preserve older data for explicitly requested historical scans. |
| G06 / P1 | **Complete proposal-time budget inputs and limit generation.** A09; inspect original `size_trade/run_plan`. Add an INR limit-order interface and optional authoritative local cash/held exposure/fresh completed quote inputs. Quantity must be fixed before fill, include costs/ticks, fit all caps, and still pass exact-order risk/approval. Test price moving toward target, insufficient remaining cash, adverse fills and limit violations. Do not remove the later ledger checks. |
| G07 / P2 | **Restore efficient, transparent broad scans.** A05/A06; upstream batch/stage tests. Implement only provider-permitted batching or bounded shared fetching/cache, with explicit requested/fetched/eligible/stale/failed/candidate/confirmed/skipped counts. Test the whole supplied universe, batch boundaries, outages and confirmation caps; compare confirmed hits with an uncapped per-symbol baseline on the same frozen data. Define ETF/SME/suspension/product eligibility using dated metadata. Measure latency on a documented dataset; do not reuse US timing claims. |
| G08 / P2 | **Restore simulated-clock watch and robust foreground monitoring.** A07/A08; upstream watch CLI and loop tests. Replay saved bars without real sleeps, emit one event per newly completed bar, recover from a bounded number of transient reads, stop on cutoff/expiry/interrupt/check limit, and make trigger-file semantics explicit. Never call broker/approval from a watcher. Add active-wrapper tests, not only archived evaluator tests. |
| G09 / P2 | **Restore story merging and Indian mention attribution.** A11. Merge same ID/link/normalized title+date across symbols while retaining all affected instruments/sources and publication precision. Test NSE/BSE symbols, issuer names, changed GUIDs, multiple feeds, future items, source failure and persisted rechecks. Preserve strict cutoff and disclose incomplete announcement coverage. |
| G10 / P2 | **Complete Indian financial research ingestion and outputs.** A12; original analyst tests. Add sourced issuer/exchange discovery or authorized import adapters, with audited units/basis/fiscal periods/restatements. Implement TTM EPS only with documented share conventions, QoQ/narrative deltas, filing staleness and explicit Q4/YTD derivation. Test original filings or clearly synthetic exports, reconciliation, late publications, missing metrics, losses and overlapping/revised quarters. Do not treat a source URL as verification. |
| G11 / P2 | **Validate dated market metadata/cost/capacity profiles.** NSE cost aggregate was verified, but independent BSE calendars/tariffs and instrument-specific session/eligibility/ticks remain incomplete. Reconcile dated exchange rules and paper contract-note examples; record broker, product, dates and all charge components. Validate spread/lagged-volume scenarios against entitled observations. Add tests for fee changes, non-default ticks, price bands, unavailable depth and unresolved exits. Keep no-trade gates on missing required evidence. |
| G12 / P2 | **Add forward-paper reconciliation and persistent promotion evidence.** Existing `research/promotion.py` intentionally blocks later progression. Bind forward results to strategy/version/declared rules, store planned versus modeled/observed paper fills, missing/rejected signals and drift, and persist reviewed transitions. Require documented independent review and adequate uncertainty/coverage. No live orders, automatic LIVE_ELIGIBLE or model-confidence override. |
| G13 / P3 | **Choose explicit compatibility scope.** Weighted INR allocation, histogram/window controls, richer analyst batches/transcript instructions, partial execution fills and optional US-only disclosure commands are not active parity. Either restore bounded paper/read-only equivalents with tests or publish an explicit permanent-exclusions contract. Partial fills must retain per-fill idempotency, cost/risk totals and transaction rollback. Do not add a broker transport. |
| G14 / P3 | **Tighten instructions and packaging.** Add a per-command compatibility table; update the broker skill's observed-bar requirement; state which tests hit legacy versus active modules. If this audit should ship in the public export, add it to the manifest in a separately authorized follow-up. No manifest or other code/config was changed for this report. |

Suggested order: G01–G05 correctness first; G06–G10 functional parity next;
G11–G12 real-data/forward validation before promotion; G13–G14 explicit optional
scope and instruction cleanup. Some tasks can proceed independently, but later
scanner/live-monitoring/strategy phases must not be labelled complete merely
because archived utilities or simulated samples pass.

## 8. Executed checks and limits

| Executed check | Actual result |
| --- | --- |
| Pinned upstream `python3 -m unittest discover -s tests -v` | **315 tests**,15.301s, no failures/errors/skips |
| Local working-tree same command | **605 tests**,22.053s, no failures/errors,2 skips |
| Fresh public export same command | **304 tests**,8.940s, no failures/errors,2 skips |
| Appendix B audit observations | **16 probes**,0.664s, all passed after correcting the synthetic bull-flag fixture to exceed its documented10% threshold |
| `python3 scripts/build_release.py --check` | **PASS**,189 source files,21 test modules |
| Eleven preserved implementation byte comparisons | **11/11 identical** to the pinned upstream files |
| Existing source-content protection | All247 pre-audit files unchanged; report is the only added repository file |

The two local/export skips are existing loopback HTTP socket checks, not market
provider failures. Upstream historical captures and local synthetic tests serve
different purposes. Neither proves live provider delivery, actual Codex host
delegation, actual broker execution, statutory completeness, liquidity or edge.
No npm build, container run, new OS schedule or online full-universe market scan
was performed; this audit changed no frontend or runtime source.

### Appendix A: reproducible baseline and targeted checks

Run from the audited local working tree. Use a new temporary destination if the
upstream path already exists. Network is needed only to fetch public upstream;
the tests/probes themselves use offline inputs/mocks.

```sh
git clone https://github.com/Jakeschincariol/day-trader-skill.git /tmp/day-trader-upstream-parity-audit
git -C /tmp/day-trader-upstream-parity-audit checkout --detach 3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd
git -C /tmp/day-trader-upstream-parity-audit rev-parse HEAD
```

In that checkout: `python3 -m unittest discover -s tests -v`.
In the Indian checkout: the same full command, plus these focused selections:

```sh
python3 -m unittest discover -s tests -p test_workflow.py -v
python3 -m unittest discover -s tests -p 'test_execution*.py' -v
python3 -m unittest discover -s tests -p test_forecast.py -v
python3 -m unittest discover -s tests -p test_volume_calendar.py -v
python3 -m unittest discover -s tests -p test_liquidity.py -v
python3 -m unittest discover -s tests -p 'test_research*.py' -v
python3 -m unittest discover -s tests -p test_nse.py -v
python3 -m unittest discover -s tests -p test_upstox.py -v
python3 -m unittest discover -s tests -p test_team.py -v
python3 scripts/build_release.py --check
```

Those focused cases were included in the full605-test run; the commands above
are reproducible selections, not a claim of separately measured audit timings.
For a new export, run `python3 scripts/build_release.py --out /tmp/NEW_PUBLIC_DIR`
then run the full suite from that directory. The report itself is currently
outside the export allowlist, consistent with the no-code/config-change constraint.

### Appendix B: complete independent audit observations

Copy the following Python block to `/tmp/day_trader_parity_probes.py` and run
`python3 /tmp/day_trader_parity_probes.py` from this repo root. Optional
`AUDIT_LOCAL` and `AUDIT_UPSTREAM` environment variables select other snapshot
locations. These assertions deliberately reproduce both retained behavior and
the currently observed gaps; after fixes, update them into acceptance tests
instead of preserving defective observations. They do not modify repository
source or record user consent.

```python
"""Audit observations: passing tests confirm behavior, including known gaps."""
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('AUDIT_LOCAL',Path.cwd())).resolve()
UP = Path(os.environ.get('AUDIT_UPSTREAM','/tmp/day-trader-upstream-parity-audit')).resolve()
sys.path[:0] = [str(ROOT),str(ROOT/'tests')]
from india.data import Bar, CSVProvider, IST
from india.execution import exit_fill
from india.forecast import _simulate
from india.market import Calendar, DEFAULT_CALENDAR
from workflow.cli import main, market_status, parser
from workflow.common import clock, config, upstream
from workflow.data import universe
from workflow.research import financials, news
from workflow.strategy import scan
import test_forecast
import test_workflow

NOW = clock('2026-10-19T09:35:00+05:30')
CSV = ROOT/'tests/fixtures/india/synthetic.csv'


def original(role,filename):
    spec = importlib.util.spec_from_file_location('audit_'+role,UP/('trader-'+role)/(filename+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParityAudit(unittest.TestCase):
    def test_A01_eleven_archived_sources_identical(self):
        paths = list(UP.glob('trader-*/*.py'))
        self.assertEqual(len(paths),11)
        for path in paths:
            self.assertEqual(path.read_bytes(),(ROOT/'legacy'/path.relative_to(UP)).read_bytes())

    def test_A02_all_eleven_active_entrypoints_have_help(self):
        files = dict(scanner='scan',entry='entry',exit='exit',timemachine='timemachine',
                     budget='size',risk='check',broker='ticket',news='news',alerts='alerts',analyst='facts',pelosi='ptr')
        for role,name in files.items():
            result = subprocess.run([sys.executable,str(ROOT/('trader-'+role)/(name+'.py')),'--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_A03_all_five_daily_rule_families_match_original_math(self):
        src,adapted = original('scanner','scan'),upstream('scanner')
        cases = [list(range(100,140)),[100]*24+list(range(100,122,2))+[121]*5,
                 [100]*38+[90,110],list(range(140,100,-1)),list(range(140,101,-1))+[103]]
        found = set()
        for closes in cases:
            a,b = src.find_setups(closes),adapted.find_setups(closes)
            self.assertEqual(a,b)
            found.update(a[0])
        self.assertEqual(found,{'breakout_20d','bull_flag','reclaim_ma20','oversold','oversold_bounce'})

    def daily(self):
        rows,day = [],dt.date(2026,7,1)
        while len(rows)<40:
            if DEFAULT_CALENDAR.trading_day(day):
                p = 100+len(rows)
                rows.append(Bar(dt.datetime.combine(day,dt.time(9,15),IST),p,p+1,p-1,p,1000))
            day += dt.timedelta(days=1)
        return rows

    def test_A04_daily_stale_history_is_still_a_hit(self):
        rows = self.daily()
        class Data:
            source='Original synthetic audit data'
            def history(self,symbol,interval): return rows
        result = scan(Data(),['DEMO.NS'],NOW,config(),mode='daily')
        self.assertEqual(len(result['hits']),1)
        self.assertGreater((NOW.date()-rows[-1].time.date()).days,30)
        self.assertFalse(result['failed'])

    def test_A05_daily_scan_calls_each_symbol_and_keeps_input_order(self):
        rows,calls = self.daily(),[]
        class Data:
            source='Original synthetic audit data'
            def history(self,symbol,interval):
                calls.append((symbol,interval))
                from dataclasses import replace
                return [replace(b,volume=1000000 if symbol=='SECOND.NS' else 1000) for b in rows]
        result = scan(Data(),['FIRST.NS','SECOND.NS'],NOW,config(),mode='daily')
        self.assertEqual(len(calls),2)
        self.assertEqual([h['symbol'] for h in result['hits']],['FIRST.NS','SECOND.NS'])

    def test_A06_directory_does_not_exclude_an_equity_typed_etf_name(self):
        rows=[dict(segment='NSE_EQ',instrument_type='EQ',lot_size=1,security_type='NORMAL',trading_symbol='DEMOETF',name='Synthetic ETF')]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'instruments.json'
            path.write_text(json.dumps(rows))
            self.assertEqual(universe('NSE',path)[0],['DEMOETF.NS'])

    def test_A07_historical_watch_replay_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as out:
            result=main('entry',['watch','DEMO.NS','--csv',str(CSV),'--now',NOW.isoformat(),'--max-checks','2','--workspace',directory])
            self.assertEqual(result,1)
            self.assertIn('fixed clock',out.getvalue())

    def test_A08_watch_without_write_does_not_save_trigger(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as out:
            result=main('entry',['watch','DEMO.NS','--csv',str(CSV),'--now',NOW.isoformat(),'--max-checks','1','--workspace',directory])
            self.assertEqual(result,0)
            self.assertEqual(json.loads(out.getvalue())['status'],'triggered')
            self.assertFalse((Path(directory)/'entry.json').exists())

    def test_A09_budget_limit_and_weights_flags_are_absent(self):
        for args in (['--limit','102'],['--weights','DEMO.NS=100']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as exc:
                parser('budget').parse_args(args)
            self.assertEqual(exc.exception.code,2)

    def test_A10_special_session_alert_clock_reports_regular_hours(self):
        cal=Calendar({'NSE':{'2026':dict(source='Original synthetic session',verified_on='2026-10-01',holidays=[],sessions={
            '2026-10-24':dict(open='18:00',close='19:00',source='Original synthetic session')})}})
        status=market_status(clock('2026-10-24T18:30:00+05:30'),cal,'NSE')
        self.assertTrue(status['open'])
        self.assertEqual(status['opens'],'09:15')
        self.assertEqual(status['closes'],'15:30')
        self.assertNotIn('next_open',status)

    def test_A11_same_news_story_is_duplicated_across_symbols(self):
        xml='<rss><channel><item><title>Synthetic issuer update</title><link>https://example.invalid/story</link><guid>same-story</guid><pubDate>Mon, 19 Oct 2026 04:00:00 GMT</pubDate></item></channel></rss>'
        with tempfile.TemporaryDirectory() as directory:
            for sym in ('DEMO.NS','OTHER.NS'):
                (Path(directory)/('yahoo-'+sym+'.xml')).write_text(xml)
            result,state = news(['DEMO.NS','OTHER.NS'],NOW,{},offline=directory)
            self.assertEqual(len(result['items']),2)
            self.assertFalse(news(['DEMO.NS','OTHER.NS'],NOW,state,offline=directory)[0]['items'])
        src=original('news','news')
        rows=src.parse_rss(xml,'DEMO')+src.parse_rss(xml,'OTHER')
        self.assertEqual(len(src.dedupe(src.tag(rows,{'DEMO':None,'OTHER':None}))),1)

    def test_A12_contiguous_financial_ttm_has_no_eps_or_qoq_summary(self):
        periods=[('2025-10-01','2025-12-31','2026-01-20'),('2026-01-01','2026-03-31','2026-04-20'),
                 ('2026-04-01','2026-06-30','2026-07-20'),('2026-07-01','2026-09-30','2026-10-15')]
        rows=[dict(period_start=a,period_end=b,published_at=c+'T18:00:00+05:30',source='https://example.invalid/synthetic',revenue=100,gross_profit=40,operating_profit=20,net_profit=10,diluted_eps=1) for a,b,c in periods]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'financials.json'
            path.write_text(json.dumps(dict(symbol='DEMO.NS',currency='INR',unit='INR',basis='consolidated',quarters=rows)))
            result=financials(path,'DEMO.NS',NOW)
        self.assertEqual(result['ttm_revenue'],400)
        self.assertEqual(result['ttm_net_profit'],40)
        self.assertEqual(result['quarters'][-1]['gross_margin_pct'],40)
        self.assertEqual(result['quarters'][-1]['operating_margin_pct'],20)
        self.assertEqual(result['quarters'][-1]['net_margin_pct'],10)
        self.assertNotIn('ttm_diluted_eps',result)
        self.assertNotIn('changes',result)

    def test_A13_scenario_gap_ordering_differs_from_canonical_exit(self):
        opening=NOW.replace(hour=9,minute=15)
        rows=[Bar(opening+dt.timedelta(minutes=5*i),110,111,90,100,1000) for i in range(72)]
        p=dict(entry=100,stop=95,target=105,qty=1,max_loss_estimate=5,flat_by='15:10')
        mc=_simulate([rows],p,1,10,19,'NSE',None)
        self.assertEqual(mc['counts']['stop'],10)
        self.assertEqual(exit_fill(p,rows[1])['outcome'],'target')

    def test_A14_numerical_forecast_ignores_plan_flat_by(self):
        test_forecast.Forecast.setUpClass()
        fixture=test_forecast.Forecast()
        result=fixture.run_forecast(sims=10,plan=dict(fixture.plan,flat_by='10:00'))
        self.assertEqual(result['status'],'AVAILABLE')
        self.assertEqual(result['mc']['until'],'15:15')
        self.assertNotIn('hist',result['mc'])
        self.assertNotIn('verdict',result)

    def test_A15_closing_sell_is_blocked_by_strategy_session_guard(self):
        fixture=test_workflow.Workflow()
        fixture.setUp()
        cal=Calendar({'NSE':{'2026':dict(source='Original synthetic session',verified_on='2026-10-01',holidays=[],sessions={
            '2026-10-24':dict(open='18:00',close='19:00',source='Original synthetic session')})}})
        pf=dict(fixture.pf,positions=[dict(ticker='DEMO.NS',shares=1,lots=[dict(shares=1,cost=100)])])
        sell=dict(symbol='DEMO.NS',side='sell',qty='1',type='market',time_in_force='day',tick=.05)
        with self.assertRaisesRegex(ValueError,'Exceptional session'):
            fixture.review(orders=[sell],portfolio=pf,now=clock('2026-10-24T18:30:00+05:30'),calendar=cal)

    def test_A16_long_only_paper_controls_cannot_be_enabled_by_config(self):
        for fields in (dict(paper=False),dict(broker='alpaca'),dict(allow_short=True),dict(allow_margin=True),dict(fractional=True)):
            with self.assertRaises(ValueError): config(**fields)


if __name__=='__main__':
    unittest.main(verbosity=2)
```
