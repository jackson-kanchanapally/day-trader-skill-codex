# Public release readiness

Assessment date: 23 October 2026. This document records a scoped local review, not a legal opinion, security certification or approval to publish third-party data.

## Completed checks

- The original MIT license and Jake Schincariol attribution are retained. Software modifications can be distributed under those terms when the license notices are preserved.
- A pattern-based scan checked 162 current tracked/unignored release-candidate files for common private-key, AWS access-key, OpenAI-style secret, GitHub token, JWT-shaped token and credential-bearing URL patterns. No credential-pattern matches were found. One filesystem-path-like match was a publisher article URL, not a contributor home directory.
- A reachable-history scan checked three commits and 169 unique Git blobs for private-key, AWS, OpenAI, GitHub and JWT-shaped patterns, with no matches. The history scan did not cover every candidate pattern, unreachable objects, remote forks, private remotes, arbitrary credential formats or external secret stores.
- No tracked `trader/`, `var/`, credential-key files, SQLite archives, dashboard dependency trees or generated dashboard builds were found in the reviewed checkout. Runtime reports stay local. New ignore rules add coverage for environment variants, private configuration directories and key formats; ignore rules are not a historical scrub.
- Public setup examples use the original synthetic demo. The README distinguishes CLI tools, independent Codex agents and the read-only React observer. No saved personal trading reports are advertised as included in a fresh clone.
- Live execution remains disabled. Native review identities are recorded provenance, not signed attestation. No production-readiness or profitability claim is made.

- Release checks passed: 397 Python tests, the React production build, the offline DEMO analysis (178 shares, execution disabled), installer dry-run, relative documentation links and private/generated-path ignore rules. CI or container deployment was not run as part of this review.

## Remaining release issue: provider content and permissions

**Do not treat the current data bundle as cleared for unrestricted public redistribution.**

Some tracked fixtures contain captured NSE/Yahoo data, news descriptions, exchange directories and disclosure documents. The original software license and attribution do not establish the redistribution permissions for those materials, even if they came from a public upstream repository. Audit each captured input and applicable provider/content-owner terms.

NSE's current [terms](https://www.nseindia.com/static/nse-terms-of-use) restrict automated collection and virtual-trading/simulation reuse. Its [copyright policy](https://www.nseindia.com/static/nse-copyright) also distinguishes content permissions from third-party rights. The existing website adapter's successful network response does not establish permission for the intended use. Resolve authorization or distribute/use an appropriate authorized data path rather than promoting the website adapter as an approved free simulation feed.

If redistribution permission is not established, replace captured content with clearly documented original synthetic fixtures and adapt the corresponding assertions. Preserve regression coverage for parser shape, timestamp conversion, missing data, auction exclusion and causality. Do not simply delete fixtures and leave tests broken, relabel captured data as synthetic, or publish personal runtime snapshots as substitutes.

Captured fixtures are already present in reachable commits. Deleting them in a later commit would not remove them from a public push of that history. Use a reviewed, clean release history/export if required after the replacement work; do not rewrite or discard the current repository's history without the owner's explicit authorization.

See [NOTICE.md](../NOTICE.md) for attribution and provider references. A disclaimer alone does not grant data permissions.

## Owner actions before publishing

1. Resolve the provider/fixture rights issue above and choose the intended release contents/history.
2. Review `git diff`, `git status --short` and the staged file list. Dashboard source and `package-lock.json` belong in the release; private sessions, archives, credentials, `node_modules` and `dist` do not.
3. Run the Python tests and React production build from the release candidate. Confirm the offline demo works without network access and that a fresh dashboard starts empty.
4. Enable repository secret scanning, dependency alerts and private vulnerability reporting where available. These are repository settings; they were not changed by this local work.
5. Publish the code with the MIT license, third-party notices and experimental/paper-only description. Do not market it as an authorized exchange feed, an investment-advice service, guaranteed lifetime-free infrastructure or a production trading system.

No remote was pushed, repository visibility changed, credential rotated, history rewritten or provider authorization obtained during this review.
