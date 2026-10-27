"""Read bound intraday snapshots and re-evaluate entry freshness at display time."""
import datetime as dt
import hashlib
from pathlib import Path

from india.data import IST, timestamp
from india.market import DEFAULT_CALENDAR
from workflow.common import read


def overlay(root, daily_sha, daily_cutoff, symbols, now):
    root = Path(root).resolve()
    index_path = root / 'intraday-index.json'
    empty = dict(as_of=None, scope='none', requested=0, scanned=0, failed=0,
                 skipped=0, eligible_at_scan=0, eligible_now=0, snapshots=0)
    if not index_path.is_file():
        return {}, empty
    index = read(index_path)
    if index.get('version') != 1 or not isinstance(index.get('snapshots'), list):
        raise ValueError('Invalid intraday snapshot index')
    seen, summary = {}, dict(empty)
    valid = []
    for entry in index['snapshots']:
        if entry.get('daily_sha256') != daily_sha or entry.get('daily_cutoff') != daily_cutoff:
            continue
        relative = Path(entry['path'])
        path = root / relative
        resolved = path.resolve()
        if (relative.is_absolute() or '..' in relative.parts or root not in resolved.parents
                or path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256')):
            raise ValueError('Intraday snapshot path or hash differs from its saved index')
        doc = read(path)
        cutoff = timestamp(doc['as_of'])
        if cutoff > now:
            continue
        if (doc.get('mode') != 'orb' or doc.get('paper') is not True or doc.get('execution_enabled') is not False
                or doc.get('currency') != 'INR' or doc['as_of'] != entry['as_of']
                or cutoff < timestamp(daily_cutoff)):
            raise ValueError('Invalid intraday snapshot metadata')
        if doc.get('daily_origin') is not None and doc['daily_origin'] != dict(sha256=daily_sha, as_of=daily_cutoff):
            raise ValueError('Intraday origin differs from the daily scan selected before fetching')
        rows = {r['symbol']: r for r in doc['results']}
        failures = {r['symbol']: r for r in doc['failed']}
        if (len(rows) != len(doc['results']) or len(failures) != len(doc['failed'])
                or not set(rows | failures) <= symbols
                or len(rows | failures) != doc['universe']):
            raise ValueError('Intraday coverage differs from saved daily matches')
        valid.append((cutoff, entry, doc, rows, failures))
    for cutoff, entry, doc, rows, failures in sorted(valid, key=lambda v: v[0]):
        for symbol in rows.keys() | failures.keys():
            row = rows.get(symbol)
            failure = failures.get(symbol)
            previous = seen.get(symbol)
            previous_trigger = (previous if previous and previous.get('signal_confirmed_at') else
                                previous.get('previous_trigger') if previous else None)
            status = 'UNAVAILABLE' if failure else row.get('status', 'UNKNOWN')
            confirmed = timestamp(row['signal_confirmed_at']) if row and row.get('signal_confirmed_at') else None
            window = dt.timedelta(minutes=1 if row and row.get('interval') == '1m' else 5)
            expiry = confirmed + window if confirmed else None
            local_now = now.astimezone(IST)
            exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
            hours = DEFAULT_CALENDAR.hours(local_now.date(), exchange)
            market_open = bool(hours and hours[0] <= local_now.time() < hours[1])
            eligible = bool(row and row.get('eligible') and row.get('data_complete') is True and
                            status == 'triggered' and confirmed and
                            cutoff.date() == local_now.date() and market_open and
                            confirmed <= now <= expiry and now <= dt.datetime.combine(now.astimezone(IST).date(), dt.time.fromisoformat(row.get('no_entries_after', '15:00')), IST))
            if status == 'triggered':
                status = 'ELIGIBLE' if eligible else 'EXPIRED' if expiry and now > expiry else 'NOT_ELIGIBLE'
            seen[symbol] = dict(status=status, eligible=eligible, eligible_at_scan=bool(row and row.get('eligible')),
                                cutoff=doc['as_of'], source=row.get('source') if row else None,
                                reason=failure.get('error') if failure else row.get('reason'),
                                signal_confirmed_at=confirmed.isoformat() if confirmed else None,
                                expires_at=expiry.isoformat() if expiry else None,
                                opening_range=row.get('opening_range') if row else None,
                                trigger=row.get('trigger') if row else None,
                                missing_bars=failure.get('missing_bars', []) if failure else row.get('missing_bars', []),
                                provider_attempts=failure.get('provider_attempts', []) if failure else row.get('provider_attempts', []),
                                data_complete=row.get('data_complete') if row else False,
                                snapshot_scope=entry['scope'],
                                previous_trigger=dict(cutoff=previous_trigger['cutoff'],
                                                      signal_confirmed_at=previous_trigger['signal_confirmed_at'],
                                                      expires_at=previous_trigger['expires_at'],
                                                      status=previous_trigger['status']) if failure and previous_trigger else None)
    if valid:
        latest = max(valid, key=lambda v: v[0])
        full = max((v for v in valid if v[1]['scope'] == 'full_shortlist'),
                   key=lambda v: v[0], default=None)
        summary.update(as_of=latest[2]['as_of'], scope=latest[1]['scope'],
                       requested=latest[2]['counts']['requested'], scanned=latest[2]['scanned'],
                       failed=len(latest[2]['failed']), skipped=len(latest[2]['skipped']),
                       eligible_at_scan=latest[2]['counts']['eligible'],
                       eligible_now=sum(r['eligible'] for r in seen.values()), snapshots=len(valid),
                       covered=len(seen), full_shortlist=dict(
                           as_of=full[2]['as_of'], requested=full[2]['counts']['requested'],
                           scanned=full[2]['scanned'], failed=len(full[2]['failed']),
                           skipped=len(full[2]['skipped']), eligible_at_scan=full[2]['counts']['eligible']) if full else None)
    return seen, summary
