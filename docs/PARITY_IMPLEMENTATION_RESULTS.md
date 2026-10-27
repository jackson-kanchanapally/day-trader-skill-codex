# Current workflow restoration — 2026-10-26

The product defaults to the original modular Indian skill chain. The dashboard
now makes independent reviews opt-in, uses saved configuration plus explicit
account overrides, and preserves daily shortlist/chart evidence across ORB
scans. Full command behavior and permanent market/transport differences are
in [the compatibility contract](ORIGINAL_WORKFLOW.md).

| Area | Active restoration | Acceptance evidence |
| --- | --- | --- |
| G07 | Yahoo 20-symbol close batches, same-window daily confirmation, bounded chart fetching, two-stage ORB, optional confirmation caps, per-symbol accounting, known product exclusions | BroadScanner tests: 43/500 names, batch boundaries, outages, source cache/concurrency, exact frozen evaluator comparisons, saved-universe default |
| G08 | Simulated-clock CSV watches, initial/latest and new-bar events, bounded read recovery, cutoff/expiry/check limit, fresh-trigger auto-write and explicit no-write | Watcher tests and full CLI handoff |
| G09 | Transitive ID/link/title-day merge, persisted aliases, NSE/BSE/issuer attribution, repeated feeds, dated sources/errors | NewsParity tests, existing source-cutoff regressions |
| G10 | Directory/companies import discovery, batches, units/basis, QoQ/YoY/margins, revisions, YTD/Q4 derivation, explicit comparable TTM EPS, newer-filing/result-age warnings; bounded official NSE integrated-index/RSS/XBRL source collection | FinancialParity and OfficialFacts tests; index is bounded and older archive coverage can be incomplete |
| G13 | Resampling window/until, histogram and original verdict/notes; INR weights/top/caps/whole-share costs/idle cash | OtherCompatibility tests; allocation is research until individual trade plans pass risk |
| G14 | Matching skill/CLI docs, original workflow default and opt-in independent UI, compatible public export | Existing contract/export tests and new active regression module |

## Live gap closure and display binding, 26 October 2026

- Default NSE universe discovery now reads NSE's current EQ-series list and
  removes symbols in its ETF list. A read-only check returned 2,360 normal EQ
  single-share symbols; LIQUIDADD was excluded. The prior 2,665-symbol Upstox
  audit is kept unchanged as historical evidence.
- Incomplete Yahoo five-minute snapshots try the alternate host and bounded
  range, then five-minute candles assembled only from five consecutive observed
  minute bars. Provider/range/missing-bar attempts are recorded. A five-stock
  real sample that failed earlier still had 1–9 missing five-minute bars at
  13:00 IST; those entries remain unavailable. Recovery never fills absent bars.
- Nested ORB scan archives are bound by SHA-256 to the exact saved daily scan.
  The dashboard combines the last full-shortlist scan with later subset checks
  and recomputes entry expiry at display time. The earlier 2,665-symbol run is
  archived. The latest official-stock run requested 2,360 daily names, scanned
  2,318, failed 2, left 40 with insufficient history, skipped none, and found
  807 daily matches at 13:23:11 IST. Its full ORB pass requested 807, scanned
  447, failed 360, skipped none, recorded 371 earlier breakouts and one entry
  eligible at its 13:25:37 cutoff. SADHNANIQ confirmed at 13:25 and expired
  at 13:30. A 14:36:46 subset refresh failed on missing candles, so the
  dashboard shows the expired prior trigger, the newer data failure and zero
  current eligible entries. The exact files are in
  `trader/scan-runs/NSE-20261026-official-EQ/`.
- The analyst fetched an official current NSE integrated XBRL for BFUTILITIE
  at 13:00 IST. The standalone 2026-03-31 quarter was sourced, hashed and
  reported stale; the consolidated basis remained unavailable for that filing.
  The paginated index returned seven TCS consolidated quarters through
  2026-09-30 and a sourced revenue/profit TTM. The news tool
  attributed that filing from the official feed. The index is bounded and RSS
  feeds have short current windows; an unlimited company-facts archive,
  PDF-only facts and guaranteed current data coverage are not implied.
- The text Time Machine chart and shortened-session alert option are exposed
  through the modular CLI. The dashboard build, local API and synthetic
  recovery/source/expiry tests passed. The historical phase-one matrix below
  records its earlier checkpoint; current validation was 679 tests (676 pass,
  three sandbox socket skips), public export 378 tests (375 pass, three socket
  skips), and separate loopback dashboard suites passed 30/30.

The earlier G01–G06 fixes remain intact. All eleven archived source utilities
remain unchanged. No broker transport, permission bypass or fabricated data was
added. G11 external rule/data authentication and G12 forward promotion are
separate validation/platform work; neither is silently counted as original
workflow completion.

## Follow-up correctness verification — 26 October 2026

- Capped ORB candidates no longer count as scanned. Both direct and batched
  paths reconcile requested = scanned + failed + unavailable + stale + skipped;
  `counts.scanned` matches the top-level field. Failure rows retain provider
  attempts and exact missing strategy candle timestamps.
- Daily source selection and SHA-256 are captured once before ORB fetching.
  A daily-file replacement during fetching retains the original scan archive
  and records `SOURCE_CHANGED` in `publication.json`; it never binds that
  result to the replacement shortlist. Captured full OHLCV is compressed and
  hashed in `evidence.json` without refetching or creating summary-only candles.
- Prefetch fallback reuses the existing Yahoo attempt. Current-session NSE
  fallback requests one day, while historical RVOL keeps its necessary history.
- XBRL parsing resolves actual currency and per-share unit declarations and
  namespaces, following the [XBRL unit contract](https://specifications.xbrl.org/reporting-requirements.html).
  Wrong-currency labels, duplicate contexts and conflicting same-time quarters
  fail explicitly. Invalid optional EPS retains a field reason. Newer unreadable
  indexed results remain visible as stale-result warnings; a later revision
  cannot enter a past cutoff through an older RSS publication. Cache scope is
  one invocation. NSE news timestamps remain IST when the caller uses UTC.
- The dashboard displays every coverage category and reason. Its live API
  verified the saved 13:23:11 daily scan: 2,360 requested, 2,318 scanned,
  2 failed, 40 unavailable, zero stale/skipped and 807 matches. The page exposes
  all 42 exceptions. Planning uses saved capital/risk unless explicitly
  overridden; missing choices are obtained before sizing. Repeated failed
  ORB refreshes preserve the prior expired trigger as history.

Acceptance: 690 repository tests completed (687 pass, three sandbox socket
skips); clean public export 389 tests (386 pass, three socket skips); separate
dashboard loopback suites 31/31 passed. The React build and public manifest
check passed (210 source files, 29 test modules), as did `git diff --check`.
The stricter read-only TCS CLI returned four displayed consolidated quarters
through 2026-09-30 with zero acquisition errors. This pass did not rerun the
market universe scan or approve/place an order. Archived original utilities
remain unchanged.

A seven-stock read-only Yahoo scan at 11:41 IST fetched all seven without
errors (one summary request, four chart workers, 1.28 seconds observed), found
six historical triggers, and returned zero fresh eligible entries. This is a
sample validation, not a full-universe latency or profitability claim.

The new active regression module includes a synthetic scan → news → analyst →
watch → midpoint exit → scenarios → sizing → risk PASS → paper-ticket display
→ alert-rule handoff, with twenty complete prior sessions. It approves no
paper fill and makes no live order. Synthetic 500-symbol batching is checked
separately from the real-provider sample.

The phase-one report below is preserved as historical implementation evidence.
Its deferred status rows describe that earlier stopping point.

---

# Parity implementation results — Phase 1, 2026-10-26

G01–G06 are implemented in the active Indian runtime. This run stops at the
requested clean Phase 1 boundary; G07–G13 are deferred, and G14 is partial for
phase-specific documentation/packaging. No full parity, production readiness,
profitable expectancy or live-provider coverage is claimed.

The starting tree was clean at `f50d9f068639b7e526f5d1b36a1133142a2aa10d`.
The complete audit, including its original observational probes, was read and
the current code inspected. Its 605-test context was remeasured: 605 tests in
24.245s, 603 passed, 0 failed, 0 errored, 2 existing socket skips. No legacy source
was changed, no tests were deleted, and no commit or push was made.

## Gap results and acceptance evidence

Test names below are in the named active test module, not the archived US
suites. Parameterized subtests include the requested scenario variants.

| Gap ID | Status | Modified files | Tests | Expected result | Actual result | Remaining risk |
| --- | --- | --- | --- | --- | --- | --- |
| G01 | Fixed | `india/execution.py`, `india/forecast.py`, `tests/test_parity_forecast.py` | `ScenarioEvents.test_tc_g01_01_to_06_canonical_events_and_scenario_cost_parity`; `ScenarioEvents.test_cross_engine_replay_gross_exit_and_net_costs` | Open ₹92 through stop ₹95 exits at ₹92; open ₹115 through target ₹110 exits at ₹110 under existing policy; ambiguous touches exit at stop ₹95; target-only exits at ₹110; no touch remains open until flatten. Deterministic held paths agree on outcome and net costs across replay/scenarios. | Pass, including adverse gaps beyond 1R and identical spread/cost configuration | Resampling remains a scenario model, not a quote/queue or executable-liquidity guarantee. Its origin is a supplied completed close; replay entry still uses canonical next open. |
| G02 | Fixed | `india/execution.py`, `india/analysis.py`, `india/forecast.py`, `india/team.py`, `workflow/strategy.py`, `workflow/common.py`, `tests/test_parity_forecast.py`, `tests/test_forecast.py` | `ForecastHorizons.test_configured_horizons_match_paths_bands_and_counts`; `test_after_flatten_history_cannot_affect_price_paths_or_pnl`; `test_off_grid_flatten_is_rejected_not_rounded_forward`; `test_no_horizon_returns_unavailable_without_paths`; `test_modular_rejects_expired_plan_and_accepts_explicit_boundary`; `test_default_flatten_and_config_explicit_1515`; `test_sealed_team_numerical_forecast_and_alert_agree_on_default_flatten` | 10:00/15:10/15:15 produce 5/67/68 remaining bars at 09:35. Bands/paths have horizon+1 points. Later historical prices cannot alter sampled or historical P&L. 10:02 fails; expired/no-horizon plans unavailable. Sealed defaults agree at 15:10. | Pass | Strict bar-grid rejection is retained; no new evening ORB or arbitrary configured-plan support is added to the sealed team's fixed strategy. |
| G03 | Fixed | `workflow/paper.py`, `tests/test_parity_sessions.py` | `SessionRisk.test_evening_close_passes_but_new_buy_stays_blocked`; `test_closing_safety_rejects_oversell_bad_tick_stale_quote_and_holiday`; `test_unknown_calendar_coverage_fails_safely`; `test_approved_partial_position_close_preserves_remaining_cash_and_lots` | Evening closing sell passes active risk CLI/API; new buy remains blocked. Oversell, missing/expired tick, stale quote and closed holiday fail. Exact approval required; closing 4 of 10 shares leaves 6, correct fees/cash/P&L; duplicate fill/ticket blocked. | Pass | Source authenticity is still external. Online providers may not deliver evening bars; tests use explicit synthetic CSV and sourced synthetic calendars. |
| G04 | Fixed | `india/market.py`, `workflow/cli.py`, `tests/test_parity_sessions.py` | `SessionAlerts.test_regular_evening_shortened_and_holiday_metadata`; `test_next_open_skips_known_weekend_holiday_and_respects_evening`; `test_cli_opening_event_once_and_shortened_before_close_rule` | Report 09:15–15:30, 18:00–19:00 and 09:15–12:00 correctly; holidays closed. Next open skips known closures, becomes null/reason beyond coverage. Same configured bell rings once; changed session event is independent. Before-close rule shifts with actual close. | Pass | No real Muhurat schedule is invented. Persisted alert checks remain single-process file operations, not a notification service or concurrent-delivery guarantee. |
| G05 | Fixed | `india/market.py`, `workflow/strategy.py`, `workflow/data.py`, `tests/test_parity_scanner.py`, `tests/test_workflow.py` | `DailyFreshness.test_latest_weekend_holiday_and_historical_cutoffs`; `test_stale_missing_and_failed_symbols_remain_separate_from_fresh_hits`; `test_no_setup_unavailable_and_future_are_not_current_matches`; `test_current_daily_bar_only_available_after_actual_session_close` | Current expected session accepted; old/missing latest session excluded as STALE; one bad symbol does not discard good symbols. Empty/short input unavailable, malformed/future input failed, no setup distinct. Current day excluded before close, accepted after actual close; explicit historical cutoff preserved. | Pass | Recency checks do not authenticate a provider's daily close or establish complete universe coverage. Fetch batching remains deferred. Providers discard later-than-cutoff rows before calculation; direct scanner future-dated input fails explicitly. |
| G06 | Fixed | `workflow/paper.py`, `workflow/cli.py`, `tests/test_parity_budget.py` | `AccountSizing.test_cash_and_exposure_context_bound_cost_inclusive_sizing`; `test_minimum_order_uses_limit_not_original_signal_value`; `test_fresh_price_and_cost_risk_monotonicity`; `test_limit_is_tick_aligned_bounded_and_not_assumed_filled`; `test_valid_limit_proposal_reaches_exact_ticket_and_adverse_fill_stays_blocked`; `test_cli_limit_prices_and_portfolio_inputs`; `test_approved_limit_fill_keeps_quantity_and_rolls_back_adverse_open`; `test_existing_ledger_is_authoritative_and_mismatched_export_is_rejected` | ₹5,000 cash bounds quantity and costs; insufficient cash/exposure blocks; existing holdings keep the conservative current mark even for a lower buy limit. Minimum order uses the proposed reference/limit, not the old signal value. Higher prices/costs cannot increase size. Stale explicit quote and off-tick limit fail. Valid limit goes through risk/approval/canonical fill, adverse open rolls back, quantity fixed. Changed evidence invalidates prior risk. No account evidence means labelled estimate. | Pass | Supplied snapshots/quotes are not authenticated broker data. No intrabar queue, partial execution fill or external account synchronization. |
| G07 | Deferred | None for batching/eligibility work | No new G07 acceptance suite | Bounded permitted shared fetching, all-symbol accounting, confirmation caps, deterministic ranking and measured benchmark | Not implemented in this phase | Sequential scanning remains; no claim of 500-symbol/live-market performance. |
| G08 | Deferred | None | No new G08 acceptance suite | Simulated-clock replay, per-new-bar events and bounded watch recovery | Not implemented | Current bounded foreground watcher remains. |
| G09 | Deferred | None | No new G09 acceptance suite | Cross-feed story merge and authenticated Indian company attribution | Not implemented | Stable-ID dedup only; original audit gap remains. |
| G10 | Deferred | None | No new G10 acceptance suite | Expanded Indian quarter/restatement/YTD/EPS/source/batch analysis | Not implemented | Normalized supplied-quarter parser remains; no fabricated filings. |
| G11 | Deferred | No fee/calendar data or liquidity profile added; G03 revalidates already-supplied tick applicability | Existing metadata/cost/liquidity suites retained; no new tariff-certification suite | Independent dated source and contract-note reconciliation, observed liquidity validation | Full existing suite retained, not real-data certification | Independent BSE calendar/tariff verification, circuit/ASM/GSM and real spread/depth evidence remain unresolved. |
| G12 | Deferred | None | Existing promotion/team veto tests retained; no forward-result implementation | Immutable strategy-bound forward observations and reviewed paper-state transitions | Not implemented | Forward evidence and persistent promotion remain blocked; no automatic live eligibility. |
| G13 | Deferred | None for optional compatibility modes | Existing rejection tests retained | Explicit bounded implementation or exclusion decisions for weights, histograms/window, batches/transcripts, partial fills and optional US utilities | No new optional mode | Existing US disclosure boundary and paper-only exclusions retained; optional scope remains open. |
| G14 | Partial | `docs/WORKFLOW.md`, `docs/UPSTREAM_PARITY_AUDIT.md`, this report, `release-manifest.json`, seven relevant `trader-*/SKILL.md`, `tests/test_parity_contracts.py` | `ActiveContracts.test_budget_and_broker_required_options_are_in_help_and_skill_instructions`; `test_public_profile_includes_implementation_report_audit_and_active_regressions`; existing installer/release suites | New active options match help/instructions; observed buy-bar requirement documented; audit/results and active tests included in public profile; clean install unchanged | Phase-one contract tests pass after docs/manifest updates; final full/export checks recorded below | Instructions for deferred capabilities will need corresponding updates when those features exist. |

## Implementation decisions and invariants

- `exit_event` is shared by observed canonical exits and sampled price events;
  no second stop/target implementation remains in the sampler. Target-gap
  execution stays at the planned target, preserving the established policy.
- `flatten_slot` strictly validates the 5-minute grid and boundary. Numerical
  historical replay now receives the same `flat_by`; the modular sampler no
  longer clones a function with modified globals. Sealed forecast and alert
  defaults agree. Paths after an earlier exit describe the underlying scenario,
  not additional position P&L.
- Closing-position eligibility is separated from ORB entry support. Every
  order still needs an open sourced session and fresh price; supplied tick
  evidence is re-evaluated at the current date. Approvals freeze calendar
  evidence and fills outside those hours fail. Exact quantity, price, account,
  config, idempotency and cost checks remain.
- Budget context is optional and transparent. Without a ledger/snapshot,
  configured capital is an estimate. CLI uses an existing ledger and rejects a
  conflicting export. Limit sizing uses the maximum approved buy price with
  no reserve beyond that limit; market sizing retains its conservative reserve.
- Market-evidence hashes bind observations and provenance, excluding only the
  timestamp of an otherwise identical query. A changed calendar or market
  snapshot requires rerunning risk. Actual adverse fills never trigger a
  hindsight quantity increase.
- No source, holiday, equity restriction, company filing, price, liquidity,
  provider entitlement or native-agent review was invented. Short/margin/live
  settings remain rejected. No external message/order transport was added.

## Test-first evidence and earlier test adjustments

Before production edits, the new forecast, session, scanner and budget suites
were run against the existing implementation. Expected failures included the
gap-above-target incorrectly becoming a stop; ignored horizons/off-grid times;
exceptional-session closing errors; hardcoded clocks/missing next-open fields;
stale hits; missing budget context kwargs/limit CLI. Later edge regressions
also failed before their fixes: stale reused tick metadata, a changed session
bell event, and historical P&L changing after a requested earlier flatten.
Documentation/export contracts failed before the corresponding updates.
Final budget review also added failing regressions for minimum order value at
the limit and held exposure at the current quote; both were fixed before the
final verification run.
All completed gap tests then passed on active modules and CLI paths.

No expected result was changed to conceal a defect. Three existing forecast
checks needed explicit policy corrections: default remaining bars 68→67 because
15:10 is now honored; two direct sampler tests explicitly request 15:15 to keep
their intended final-slot fee/gap checks. Their loss/cost assertions remain.
The existing daily setup test was moved from an accidentally stale July/August
history to the latest 40 completed sessions; its formula assertion remains, and
new tests explicitly require stale histories to be excluded.

## Verification record

Required commands are `python3 -m unittest discover -s tests -v` and
`python3 scripts/build_release.py --check`. New focused suites use
`python3 -m unittest discover -s tests -p 'test_parity_*.py' -v`.
Neighboring forecast, workflow and execution suites were run during development;
the final full suite also covers research, providers, team vetoes, installer,
dashboard contracts and legacy behavior.

| Verification | Total | Passed | Failed | Errored | Skipped | Time / result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Baseline full suite | 605 | 603 | 0 | 0 | 2 | 24.245s, OK |
| Final focused `test_parity_*.py` suites | 30 | 30 | 0 | 0 | 0 | 1.966s, OK |
| Final repository full suite | 635 | 633 | 0 | 0 | 2 | 23.898s, OK |
| Clean public export full suite | 334 | 332 | 0 | 0 | 2 | 9.883s, OK |
| Release manifest check | — | — | — | — | — | PASS: 196 source files, 26 test modules, 5 synthetic fixtures |
| `git diff --check` | — | — | — | — | — | PASS |

The increase is 30 active test methods across five new modules, with additional
parameterized cases. The two skips are existing loopback/socket tests restricted
by this environment; no new regression was skipped. The public profile excludes
301 archived implementation tests, so its count differs deliberately from the
repository suite. Installer and export tests pass; the generated public tree
also passes its own full suite in a separate temporary directory. Timing is
observational, not a scanner-performance benchmark.

## Exact file inventory and remaining boundary

Runtime changes: `india/execution.py`, `india/forecast.py`, `india/analysis.py`,
`india/market.py`, `india/team.py`, `workflow/common.py`, `workflow/cli.py`,
`workflow/strategy.py`, `workflow/data.py`, `workflow/paper.py`.

New tests: `tests/test_parity_forecast.py`, `tests/test_parity_sessions.py`,
`tests/test_parity_scanner.py`, `tests/test_parity_budget.py`,
`tests/test_parity_contracts.py`. Adjusted active tests:
`tests/test_forecast.py`, `tests/test_workflow.py`.

Instructions: `trader-budget/SKILL.md`, `trader-broker/SKILL.md`,
`trader-timemachine/SKILL.md`, `trader-scanner/SKILL.md`,
`trader-risk/SKILL.md`, `trader-alerts/SKILL.md`, `trader-exit/SKILL.md`.
Documentation/packaging: `docs/WORKFLOW.md`, `docs/UPSTREAM_PARITY_AUDIT.md`,
`docs/PARITY_IMPLEMENTATION_RESULTS.md`, `release-manifest.json`.

The original audit/probes remain historical evidence, with a current status
matrix at its top. They intentionally assert old defects and are not the new
acceptance suite. Legacy tests remain distinct from active tests. No frontend
change, live provider trial, subscription, notification service, native-agent
team run or deployment was performed. Reliable NSE/BSE paper trading still
depends on dated verified metadata, source availability, measured data quality,
observed capacity and forward validation. Next work starts with G07 after this
verified phase boundary; none of G07–G13 is claimed complete.
