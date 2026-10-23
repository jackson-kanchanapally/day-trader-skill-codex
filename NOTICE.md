# Attribution and third-party materials

## Software

This project adapts Jake Schincariol's [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill). The original MIT copyright notice remains in [LICENSE](LICENSE). Original US utilities retained under `legacy/` include the Wilder RSI implementation reused by the Indian workflow. Modifications include Indian-market providers and calculations, Codex coordination, research tooling and the React dashboard.

React, React DOM, Vite and their transitive dependencies retain their own licenses and notices. Dependency versions are locked in `dashboard/package-lock.json`; installation obtains their package license files. The animated agent desk illustrations are implemented in this repository as SVG/React components, without downloaded avatar artwork.

## Market data, filings and news

The software license does not relicense third-party responses, prices, exchange/security directories, company filings, news descriptions or other provider content. Captured regression inputs exist under `tests/fixtures/india/` and the legacy `tests/fixtures/trader-*/` directories. Filenames or labels such as `live`, `captured`, or a real ticker are not grants of redistribution permission. Some fixtures are synthetic; do not assume all fixtures are.

Relevant provider references include:

- [NSE Terms of Use](https://www.nseindia.com/static/nse-terms-of-use): restrictions include automated collection and virtual-trading/simulation reuse.
- [NSE Copyright Policy](https://www.nseindia.com/static/nse-copyright): content permissions and third-party rights are distinct from this repository's software license.
- [NSE data policy](https://www.nseindia.com/static/market-data/nse-data-policy).
- [Yahoo exchange/data-provider notices](https://help.yahoo.com/kb/SLN2352.html).
- [Upstox developer documentation](https://upstox.com/developer/api-documentation/).

Publicly accessible information and public upstream repositories do not establish that every included data asset can be redistributed under MIT. Before distributing a public copy, establish permissions/provenance or replace captured inputs with original synthetic regression fixtures and update the dependent assertions. No provider authorization or legal clearance is represented by this notice. Policy references were reviewed on 23 October 2026; current terms and any endpoint-specific permissions still need verification for the intended use.

Private runtime reports, portfolios, logs, archives and downloaded instrument masters are not public examples and should not be published. See [release readiness](docs/RELEASE.md).
