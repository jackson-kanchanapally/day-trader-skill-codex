# TCS review diagnosis and repairs

The saved review completed successfully as a review, but it had no eligible trade to clear. Ten completed specialists do not imply ten trade approvals. The original `trader/team/TCS.NS-20261024T004722` artifacts, reviews, lifecycle events and summary have not been rewritten.

## What caused the result

| Finding | Evidence | Consequence |
| --- | --- | --- |
| Closed market | Saved cutoff Saturday 24 October 2026, 00:47:22 IST | Friday candles cannot establish a new Saturday entry. |
| No Friday long breakout | Saved 72 strategy bars: 09:15–09:25 opening-range high ₹2,625.00; maximum subsequent completed close ₹2,583.60 | Even a daytime Friday replay would not trigger this fixed long opening-range strategy. |
| Tick unresolved | Supplied ₹0.05; dated exchange record reported raw `BidIntrvl=10` without an established unit conversion | No validated tick, order levels or ticket. |
| History too short | Time Machine counted 18 complete prior sessions; minimum 20 | No current scenario clearance. More simulations cannot supply missing observations. |
| Research timing contract too strict | The saved news/analyst reviews read original dated releases but could not find publication clock times | Useful older research was excluded even where the whole publication date preceded the cutoff. Complete news coverage is a separate issue. |

Exit, sizing, risk, broker and alert outcomes mostly follow from the absent eligible entry. They are not five independent software failures. The current sealed strategy supports a single supplied symbol and a long opening-range breakout; it does not automatically find alternative trades across the exchange.

## Implemented repairs

- Team preparation returns and saves a readiness diagnosis before dispatch. The coordinator skill checks it before starting ten specialists, unless the user explicitly requests a full diagnostic run.
- NSE supports up to 120 calendar days in disjoint requests of at most 28 days. Team preparation defaults to 60 days; Yahoo team requests also request 60 days. The actual 20 complete prior sessions remain mandatory. Upstox retains its 28-day limit.
- Team preparation accepts `--security-master FILE`. The applicable dated INR metadata is sealed into the snapshot. Numerical risk blocks missing or mismatched ticks even if all model reviews vote PASS. Shared metadata validation is used by the ordinary workflow too.
- Source validation accepts either an exact publication timestamp or an explicitly documented publication date and IANA timezone. Date-only evidence is accepted only after the entire local date has ended before both cutoff and access. Same-day ambiguity still fails. The dashboard preserves date-only labels.
- `preflight --session DIR` diagnoses archived evidence without changing it or granting clearance. Evaluation and finalization still reject changed engines and stale hashes.

The issuer's [earnings press release](https://www.tcs.com/who-we-are/newsroom/press-release/tcs-financial-results-q2-fy-2027) has a Mumbai dateline of 8 October 2026. Its date is admissible at the original cutoff under the conservative bound. This establishes one usable research source; it does not establish complete news coverage or replace an independent analyst review.

Saved coordinator diagnostics are in `trader/diagnostics/TCS.NS-20261024T004722/preflight.json` and `research-date-check.json`. They are not new independent reviews.

## Remaining external prerequisites

The original [dated NSE master](https://nsearchives.nseindia.com/content/cm/NSE_CM_security_23102026.csv.gz) and [format circular](https://nsearchives.nseindia.com/content/circulars/CMTR73927.zip) could not be downloaded during this repair: direct requests timed out, and the browsing tool could not read these compressed files. No INR conversion of the raw field was assumed. An applicable original record with established units is still needed; the example metadata is synthetic and cannot verify TCS.

Longer history requests are implemented and tested with controlled responses; live retrieval of sufficient history has not been established in this repair. Verify actual complete-session counts on the next fetch. Do not relabel the old 18-session snapshot as sufficient.

The bundled calendar's next potential regular-session signal cutoff after the saved run is Monday 26 October 2026 at 09:35 IST. This is the earliest completed breakout-bar opportunity, not a scheduled job or a promised signal. Prepare using the actual eligible cutoff and current verified instrument metadata. Do not shift the cutoff automatically or force a TCS trade.

```sh
# Read-only diagnosis of the original session:
python3 trader-team/run.py preflight --session trader/team/TCS.NS-20261024T004722

# Run during an eligible session, replacing the two explicit placeholders.
# Use a NEW directory for each preparation, then inspect its preflight result.
python3 trader-team/run.py prepare TCS.NS --provider nse --days 60 \
  --tick VERIFIED_INR_TICK --security-master /path/to/verified-security-master.json \
  --capital 100000 --risk 500 --issuer-domain tcs.com \
  --team-dir trader/team/NEW_SESSION
```

No approval, simulated fill, live order or monitoring job was created by this repair. Software changes require new snapshots and new reviews for clearance.

## Validation

`python3 -m unittest discover -s tests -q`: 444 tests run, one skipped, no failures. Coverage includes closed-session diagnosis, the longer history default and disjoint request windows, tick mismatches, positive reviews failing to override missing tick evidence, and conservative research-date bounds. `npm run build` in `dashboard/` passed. The original session's snapshot and artifact hashes passed read-only diagnostic validation; it is correctly identified as belonging to the earlier engine.
