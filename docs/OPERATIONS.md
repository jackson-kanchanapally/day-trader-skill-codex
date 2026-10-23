# Operating the paper research workflow

This is a paper-only research service. The application contains no order transport.
The commands below work from the repository root on Mac/Linux. No third-party
Python package is needed. Verify the example stock's current tick size first.

## One check with an audit record

```bash
python3 -m india doctor TCS.NS --provider nse --fallback-provider yahoo \
  --tick 0.05 --capital 100000 --risk 500 --record-db var/runs.sqlite
```

`doctor` returns JSON and exits 0 for PASS, 1 for FAIL. It checks missing candles,
the latest expected trading date, and freshness during the strategy window.
After market hours a complete latest session can pass while its analysis is
marked stale; PASS describes feed health, not permission to trade. Inspect
`analysis.status`, `analysis.strategy_open` and `analysis.plan` separately.

Yahoo automatically falls back to NSE for `.NS` stocks when its snapshot is
unavailable or unusable. `--no-fallback` disables this automatic choice;
`--fallback-provider` adds explicit alternatives. Explicit NSE/Upstox selections
do not automatically switch providers. Every attempt and selected source are
reported. Each report uses one entire snapshot; candles are never spliced
together or substituted across exchanges. Switching providers/lookbacks can
change indicators. Authentication/access denials stay visible; retries are bounded.

For machine-readable errors on other commands, add `--json-errors`. Exit 0 on
`analyze` means a report was produced, including NO_SETUP or a blocked setup.
Use `doctor` when an operational failure must return a nonzero exit code.

## Audit and offline replay

Successful commands can use `--record-db var/runs.sqlite`. Records contain the
exact OHLCV snapshot, provider provenance, as-of time, calendar, cost overrides,
strategy settings, result, input checksum and engine-code fingerprint. Tokens
and environment contents are never copied into records.

Engine updates can invalidate earlier sealed team sessions or archived code
fingerprints. Prepare a new review after updating; do not relabel old artifacts
as verified by the new implementation.

Copy `run_id` from the report:

```bash
python3 -m india replay RUN_ID_FROM_REPORT --db var/runs.sqlite
```

Replay makes no network calls. `matches: true` means the numerical result
matches the stored report; otherwise it exits 1. `same_code` detects a changed
engine. Preserve the git revision/container image used for each research period;
replay uses the currently installed engine and does not execute archived code.
The checksum detects accidental payload changes, not a malicious rewrite of both
the record and its checksum. The database is a local audit trail, not a signed
or tamper-proof external ledger.

The archive uses SQLite transactions, WAL and a 10-second busy timeout. Keep
`var/` outside git, restrict access to the host account, and back up through
SQLite's backup API (rather than copying a live database without its WAL):

```bash
python3 - <<'PY'
import sqlite3
from contextlib import closing
with closing(sqlite3.connect('var/runs.sqlite')) as source:
    with closing(sqlite3.connect('var/runs-backup.sqlite')) as destination:
        source.backup(destination)
PY
```

Retain logs and snapshots for the research period you intend to evaluate.
Snapshots grow with each recorded check; periodically archive old databases.

## Scheduled local checks

Review `config/paper.example.json`. All capital/risk values are INR. The sample
tick is illustrative. Paths are relative to the working directory. Secrets stay
in the process environment if Upstox is explicitly selected.

```bash
python3 -m india.job --config config/paper.example.json
```

The job performs one `doctor` check, records its result, and exits. It makes no
provider requests on calendar holidays/weekends or outside 09:35-15:15 IST.
The OS file lock prevents overlapping jobs sharing the state directory; a busy
job exits 75. A child deadline is configurable (default 120 seconds, maximum
300). Timeouts and failed checks exit 1. Provider failures appear in JSON logs.

Logs are JSON lines in `var/state/paper.jsonl`, rotated at 2 MB with five backups.
The lock is released by the OS even if the process crashes. Fetch failures that
produce no snapshot are logged, rather than stored as successful audit runs.
No external alerts or orders are sent. Connect your own process monitor to the
exit code/log stream if unattended fault notification is required.

Example crontab entry (edit the path and Python executable for your machine):

```cron
*/5 * * * * cd /absolute/path/day-trader-skill && /absolute/path/python3 -m india.job --config config/paper.example.json
```

The job checks IST itself, independently of the scheduler's timezone. This
crontab is an example; nothing modifies your crontab automatically.

On Linux, the same command can be run by a systemd oneshot service with
`WorkingDirectory` set to the checkout and a timer with `OnCalendar=*:0/5`.
Use one process account with write permission to `var/`. External systemd/cron
configuration is intentionally left under the operator's control.

## Container

```bash
docker build -t trader-paper:local .

# Offline smoke check; even network access is disabled.
docker run --rm --network none --read-only --tmpfs /tmp trader-paper:local \
  analyze DEMO.NS --csv /app/examples/synthetic.csv \
  --as-of 2026-10-19T09:35:00+05:30 --tick 0.05

# An online one-shot check with persistent host data.
mkdir -p var
docker run --rm --read-only --tmpfs /tmp \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/var,target=/app/var" \
  trader-paper:local doctor TCS.NS --provider nse --tick 0.05 \
  --record-db var/runs.sqlite

# Run the same scheduled job wrapper inside the container.
docker run --rm --read-only --tmpfs /tmp \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD/var,target=/app/var" \
  --entrypoint python trader-paper:local \
  -m india.job --config config/paper.example.json
```

The image runs as an unprivileged UID by default, installs no extra packages,
and excludes `.env`, archives and host skill/credential directories from the build
context. A bind mount uses the host UID/GID so the process can write its own
files. No daemon/server is exposed. Schedule the last command with cron or an
orchestrator. Pin `PYTHON_IMAGE` to an approved image digest and retain the built
image digest for reproducible production deployments; the default is a moving
`python:3.12-slim` tag. [Docker's Python guide](https://docs.docker.com/guides/python/)
describes the underlying container workflow.

## Calendar maintenance

The built-in holiday coverage is 2026. Supply a complete replacement with:

```bash
python3 -m india doctor TCS.NS --provider nse --tick 0.05 \
  --calendar config/calendar.2026.json
```

The JSON maps exchange names (`NSE`, `BSE`) to year strings. Each year requires
`source`, `verified_on` and an explicit `holidays` list. Unknown fields,
unattributed records, out-of-year holidays and uncovered years fail closed.
Use `config/calendar.2026.json` as the schema example. Verify new dates against
the exchange before adding a year. Loading a calendar does not certify its
contents. BSE's bundled dates retain the original shared NSE-calendar assumption;
independently verify BSE exceptions before operating a BSE workflow.

Special sessions and different opening/closing times are unsupported; do not
remove a holiday to pretend that a special session is a normal one. Strategy
analysis remains conservatively limited to 09:15-15:15 for all equities.

## Release evidence and remaining acceptance work

GitHub Actions runs the fixture suite on Linux/macOS with Python 3.12 and 3.14,
and includes a container build/offline smoke job. The workflow uses the published
[checkout](https://github.com/actions/checkout) and
[setup-python](https://github.com/actions/setup-python) actions. A local test
pass does not mean that hosted CI or a container build has run.

Before unattended production use, collect multiple market sessions of health
logs, inspect gap/delay and failure rates, verify backup restoration, and test
provider outages/restarts. A stopped Docker daemon prevents local image
validation; start Docker and run the smoke command before relying on that path.
No claim of 10/10 production readiness or profitable trading is made.
