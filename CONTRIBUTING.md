# Contributing

Keep changes focused and preserve the paper-only boundary. Open an issue describing a reproducible problem or proposed improvement; include the command, Python/Node versions and a sanitized error. Use the synthetic demo where possible. Do not attach tokens, account identifiers, private portfolios or market-data dumps without redistribution permission.

For code changes:

1. Reuse the deterministic engine and keep providers behind the OHLCV interface. Missing prices must stay missing; never interpolate live candles to make a report pass.
2. Preserve completed-bar causality, explicit IST cutoffs, sourced calendars, instrument tick validation and cost-inclusive sizing. Historical results must not imply live execution or profitability.
3. Keep specialist instructions modular. Native agent identities, actual lifecycle events and independent vetoes must not be replaced with simulated agent outputs.
4. Add focused regression coverage for meaningful behavioral changes. Prefer original synthetic fixtures and document their provenance. Do not add captured provider responses or copyrighted article text without established permissions.
5. Run `python3 -m unittest discover -s tests`. For dashboard changes, also run `npm ci --prefix dashboard` and `npm run build --prefix dashboard`, then check desktop/mobile interactions, empty/error states and reduced motion.

The Python runtime remains standard-library-only. The optional dashboard uses React/Vite with a committed lockfile. Avoid unrelated rewrites or new dependencies without a concrete need.

Use Python 3.9+ on macOS or Linux. [Getting started](docs/GETTING_STARTED.md)
includes the offline demo and dashboard development commands. `make test` runs
the Python suite; `make dashboard-build` installs locked frontend dependencies
and builds the UI.

For changes to distributed files, run `python3 scripts/build_release.py --check`.
Add new source files to the explicit patterns in `release-manifest.json` where
needed. New fixture inputs require an individual synthetic-provenance entry and
documentation in [fixture provenance](tests/fixtures/README.md). Validate the
exported source tree using [the release guide](docs/RELEASE.md); the development
checkout has extra historical capture-dependent tests that are outside the
public profile. Release candidates never include your Git history or local
reports. Do not use an existing candidate directory as scratch space.

Preserve existing license/attribution notices. Contributions should be original or accompanied by the rights and attribution required for distribution under the project's software license. Do not relicense third-party data as project code.
