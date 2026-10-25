# Public source release

The public distribution is an allowlisted source export. It includes the Indian
runtime, skills, dashboard source, upstream MIT utilities and a synthetic test
suite. It excludes the development checkout's captured provider payloads,
capture-dependent historical tests, personal session diagnostics and Git history.

The existing development history still contains captured data. Publishing that
history is a separate decision: generating an export does not remove historical
blobs, rewrite commits or change repository visibility.

## Build a candidate

From the development source root:

```sh
python3 scripts/build_release.py --check
python3 scripts/build_release.py --archive
```

The default outputs are:

- `dist/day-trader-skill-public/`: source files ready for review and a fresh repository.
- `dist/day-trader-skill-public.tar.gz`: the same tree with fixed archive metadata.
- `RELEASE.json` inside the tree: the public profile, test modules, synthetic inputs and exclusion categories.
- `SHA256SUMS` inside the tree: SHA-256 hashes of all selected files and `RELEASE.json`.

The exporter never overwrites an existing directory or archive. For another
candidate, choose a new destination with `--out dist/candidate-name --archive`.
It does not copy Git configuration, remotes, history, installed dependencies or
local runtime artifacts. It copies the current file contents, including reviewed
working-tree changes, rather than silently exporting an older commit.

`release-manifest.json` declares source globs, test modules and individual
synthetic fixture paths. Missing patterns, symlink inputs, private/generated
paths, common credential patterns and broken relative Markdown file links fail
the build. These are scoped checks, not a complete secret detector or permission
audit. Review every manifest change and the resulting candidate.

## Validate the exported tree

Run the commands below **inside the newly exported directory**:

```sh
python3 -m unittest discover -s tests
python3 -m india analyze DEMO.NS \
  --csv tests/fixtures/india/synthetic.csv \
  --as-of 2026-10-19T09:35:00+05:30 \
  --tick 0.05 --capital 100000 --risk 500
python3 install_codex.py --dry-run
npm ci --prefix dashboard
npm run build --prefix dashboard
shasum -a 256 -c SHA256SUMS
```

Use Python 3.9+ and a Node version accepted by `dashboard/package.json`. The demo
must return 171 fictional shares and `execution_enabled: false`. Tests should
not need provider credentials or live requests. A sandbox may prevent the
loopback HTTP test from running; report that skip separately from failures.

Start `python3 -m dashboard` and inspect the empty state, request chooser,
clipboard prompts and responsive layouts. The browser observes independent team
sessions; a standalone daily scan does not populate team desks.

CI runs the Python suite, public-export tests and offline demo, dashboard build,
and container smoke check. Local completion does not claim a remote CI run or
browser session. `npm ci` and action setup need network access; the numerical
demo does not.

## Data provenance and preserved coverage

The public profile's input list is documented in
[fixture provenance](../tests/fixtures/README.md). Original generated payloads
exercise NSE/Yahoo parsing, exchange-clock conversion, missing/duplicate candles,
auction exclusion, causal replays and bounded provider failures. Active workflow
checks cover the reused entry/exit rules, sizing, costs, risk gates, alerts,
paper accounting, financial/disclosure exports and snapshot integrity.

The extra historical US tests in the development checkout depend on captured
charts, news or disclosure documents and are intentionally outside this public
profile. The unchanged MIT utilities they exercise remain included because the
Indian adapters import selected pure functions from them. A public source
release does not claim the full historical capture-based regression suite.

Do not add captures to the export merely because they are publicly downloadable,
present in an upstream repository or labelled `live`/`fixture`. Establish their
provenance and applicable permissions first. Keep the original license and
[third-party notices](../NOTICE.md). No provider authorization or legal clearance
is established by the build process.

## Publish the reviewed source

Review the exported file list, checksums and working changes before creating a
fresh repository from that tree. Keep the experimental/paper-only description
and retained attribution. Do not initialize a public repository from the old
`.git` directory as a shortcut around the export.

Repository hosting settings are owner-controlled: enable suitable secret
scanning, dependency alerts, private vulnerability reporting and branch checks
where available. See [SECURITY.md](../SECURITY.md). No push, visibility change,
credential rotation or Git history rewrite is performed by the release tools.
