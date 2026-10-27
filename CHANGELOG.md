# Changelog

## Unreleased

- Reconcile capped scan counts; retain failure diagnostics and captured ORB candles; bind shortlist publication to the daily source selected before fetching.
- Validate XBRL unit declarations and revisions, refresh filing caches per invocation, show all dashboard coverage exceptions and accept saved planning limits.

- Restore original modular workflow as the dashboard default; make independent reviews opt-in.
- Restore batched/two-stage market screening, bounded confirmation, explicit skip counts and replay/recovery watches.
- Merge Indian news stories across feeds; expand financial export comparisons, revisions, YTD/Q4 and explicit TTM EPS.
- Add original simulation window/histogram/verdict controls, INR weight allocations and automatic daily chart archives.
- Bind saved ORB scans to the dashboard with live expiry checks; use official NSE EQ/ETF lists for stock universe discovery.
- Add the official NSE integrated-filings index and current RSS, validated XBRL quarters, whole-snapshot intraday recovery and a text scenario chart.

- NSE/BSE paper research tools, daily/ORB scanning and INR risk sizing.
- Twelve modular skills, an independent review workflow and a local React dashboard.
- Copyable dashboard requests for NSE universe discovery and daily setup scans.
- Original synthetic provider payloads for timestamps, gaps, auction exclusion and replay tests.
- An allowlisted public source export with checksums and reproducible archives.
- CI checks for the Python runtime, dashboard build, public export and CLI container.

The public source profile excludes captured data and capture-dependent historical
tests. It includes the unchanged upstream Python utilities used by the Indian
adapters. See [release contents and validation](docs/RELEASE.md).
