# Contributing

Keep changes focused and preserve the paper-only boundary. Open an issue describing a reproducible problem or proposed improvement; include the command, Python/Node versions and a sanitized error. Use the synthetic demo where possible. Do not attach tokens, account identifiers, private portfolios or market-data dumps without redistribution permission.

For code changes:

1. Reuse the deterministic engine and keep providers behind the OHLCV interface. Missing prices must stay missing; never interpolate live candles to make a report pass.
2. Preserve completed-bar causality, explicit IST cutoffs, sourced calendars, instrument tick validation and cost-inclusive sizing. Historical results must not imply live execution or profitability.
3. Keep specialist instructions modular. Native agent identities, actual lifecycle events and independent vetoes must not be replaced with simulated agent outputs.
4. Add focused regression coverage for meaningful behavioral changes. Prefer original synthetic fixtures and document their provenance. Do not add captured provider responses or copyrighted article text without established permissions.
5. Run `python3 -m unittest discover -s tests`. For dashboard changes, also run `npm ci --prefix dashboard` and `npm run build --prefix dashboard`, then check desktop/mobile interactions, empty/error states and reduced motion.

The Python runtime remains standard-library-only. The optional dashboard uses React/Vite with a committed lockfile. Avoid unrelated rewrites or new dependencies without a concrete need.

Preserve existing license/attribution notices. Contributions should be original or accompanied by the rights and attribution required for distribution under the project's software license. Do not relicense third-party data as project code.
