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
