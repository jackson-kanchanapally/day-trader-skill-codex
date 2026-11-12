# Production research data: NSE and BSE

Decision, 24 October 2026: prioritize licensed **NSE/BSE exchange historical,
security-reference and corporate-action data** for research. Keep broker data
as an optional access path. This is the production target, not a claim that a
subscription, entitlement or production feed has been provisioned.

NSE documents historical capital-market order/trade data through its
[historical subscription service](https://www.nseindia.com/static/market-data/eod-historical-data-subscription).
Its [data product pages](https://www.nseindia.com/all-reports) also expose dated
security/report references. Availability of a webpage does not establish
permission for automated collection, simulation reuse or redistribution.
Use the authorized delivery and formats agreed for the intended research use.

BSE describes historical trading, reference and corporate-action products in
its [Information Products document](https://www.bseindia.com/downloads1/Information_Products_Pricing_Sheet.pdf).
Its subscription and coverage must be verified separately from NSE. The direct
PDF request returned HTTP 403 during this run; the product description was
available through the search-indexed official document. No current price or
coverage guarantee is inferred from it.

The existing Upstox integration remains optional. Its
[Historical Candle V3 documentation](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
describes five-minute history availability from January 2022 and per-request
retrieval limits. Its [instrument files](https://upstox.com/developer/api-documentation/instruments/)
are beginning-of-day instruments. Today's directory is not a substitute for
past listings, delistings, eligibility, symbol changes or instrument-specific
tick history. No Upstox token was configured in this session.

## NSE official MCP

NSE publishes an [MCP service](https://www.nseindia.com/nse-mcp) that needs no
login or key. `india/nse_mcp.py` reads it with the standard library only. The
facts below were checked against the live service on 12 November 2026; NSE can
change tools, fields or terms without notice.

| Endpoint | Content |
| --- | --- |
| `https://mcp.nseindia.in/bhavcopy/cm/mcp` | End-of-day bars for about five years, corporate actions, symbol search, index history |
| `https://mcp.nseindia.in/cmmkt/mcp` | Snapshot of the current session, refreshed about every minute and delayed by one to three minutes |

Use it for daily work:

```sh
python3 trader-scanner/scan.py daily --symbols TCS.NS,INFY.NS --provider nse-mcp
python3 -m india history TCS.NS --provider nse-mcp --days 120 --tick 0.1
python3 -m india.nse_mcp quote TCS.NS
python3 -m india.nse_mcp actions RELIANCE.NS --from 2024-01-01 --to 2026-11-12
python3 -m india.nse_mcp tools --server market
python3 -m india.nse_mcp call get_market_breadth --arguments '{"date":"2026-11-11"}'
```

Boundaries that the adapter enforces:

- **No intraday candles.** The service has none, so a `5m` or `1m` request fails
  explicitly. Opening-range entries, risk checks and paper tickets still need
  a five-minute source. The delayed snapshot is labelled
  `DELAYED_SNAPSHOT_NOT_A_CANDLE` and is never used as a completed price.
- **Unadjusted prices.** Bars are raw bhavcopy values. Each history request
  also reads the symbol's corporate actions. An action with an adjustment
  factor other than one, or with no factor, whose ex-date falls after the
  first bar fails the request and names the ex-date. An incomplete or failed
  corporate-action lookup fails the request too. Dividends pass. A demerger
  that NSE reports with a factor of one is not detected.
- **Causal cutoffs.** A replay asks for bars up to its cutoff date and drops any
  later row. The current day's bar is used only after the session has closed.
- **NSE only.** `.BO` symbols are rejected. The provider is never an automatic
  fallback and is not mixed with another provider's candles.
- **One pinned host.** Only `https://mcp.nseindia.in` is contacted, redirects
  elsewhere fail, responses are size-bounded and each request is retried once.

A full-universe daily scan makes two serial requests per symbol and can take
tens of minutes. NSE states no request quota; keep scans bounded and infrequent.
The service reports a delivery quantity of zero, so delivery analysis is not
available from it.

NSE describes the service as informational and non-commercial, not for
real-time trading, and as granting no right to train AI systems. This project
uses it for local paper research only. Saved scan archives contain the
retrieved bars: keep them private and out of the public source profile. See
[NOTICE.md](../NOTICE.md).

To let a Codex agent call the same tools directly, register the two endpoints
as remote MCP servers. [config/nse-mcp.example.json](../config/nse-mcp.example.json)
holds them in the common `mcpServers` form; for Codex, add this to
`~/.codex/config.toml` and check the current Codex MCP documentation for the
exact keys your version expects:

```toml
[mcp_servers.nse-bhavcopy]
url = "https://mcp.nseindia.in/bhavcopy/cm/mcp"

[mcp_servers.nse-cm-market]
url = "https://mcp.nseindia.in/cmmkt/mcp"
```

The installer does not change global Codex configuration. Numbers an agent
reads through those tools are not sealed evidence: independent-review sessions
still take their prices only from the frozen snapshot.

## Data contract for the implemented importer

`python3 -m research ingest` accepts normalized, raw-price, five-minute OHLCV
CSV with `symbol,interval,time,open,high,low,close,volume`. The format is shared
with the existing providers. Source JSON requires `provider`, `reference` and
`license_note`, including the actual entitled export reference and verified
internal-research permission. Those are supplied assertions, not authentication.
Preserve the original delivered files, checksums and delivery timestamps.
The importer records file/candle hashes and separate observed/received clocks.

Exchange trade/order files require normalization using their actual supplied
schema before this import. An exchange-specific subscription transport or
raw order-file decoder has not been invented without those inputs. Missing
candles stay missing. Do not transform daily bhavcopies into invented intraday
bars, mix different provider prices, or label an adjusted-price series as raw.

Keep `.NS` and `.BO` identities, prices, directories, calendars and fee models
separate. A listing on one venue does not authorize substituting its other
venue's candles. Tests verify separate source provenance and exchange-specific
costs. Supply independently verified BSE holiday/session coverage rather than
assuming its calendar always matches the bundled NSE-based dates.

A useful production research delivery needs historical eligible cohorts,
delisted securities, normalized INR tick units and effective dates, sourced
sectors, split/bonus/corporate-action publications, and enough complete sessions
for the declared train/test windows. Corporate-action coverage and timestamp
conventions must be reconciled against the actual delivery. A current liquid
stock list alone cannot establish an unbiased historical universe.

## What was available locally

The workspace contained one NSE website snapshot with 374 candles spanning
16–23 October 2026, plus original synthetic fixtures and coordinator outputs.
There was no populated research store with a complete dated universe and
verified historical-data entitlement. The snapshot was not silently promoted
to a licensed production validation dataset. Production selection is complete;
real-data edge validation remains blocked on the required entitled history and
dated reference inputs. No credentials, purchases, subscriptions or external
communications were initiated.

The configurable [experiment harness](EXPERIMENTS.md) is independently testable
and remains ready for those inputs. Software test success does not establish
feed quality, dataset completeness, or positive net expectancy.
