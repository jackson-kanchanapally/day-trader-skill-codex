"""Bind saved ORB snapshots to an exact daily scan for the local dashboard."""
import hashlib
import json
from pathlib import Path

from india.data import timestamp
from .common import read, write


class DailySourceChanged(ValueError):
    """The dashboard daily scan changed after the ORB source was captured."""


def capture_daily(path):
    """Read selection and its byte hash once, before any price fetching."""
    payload = Path(path).read_bytes()
    doc = json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: '+value)))
    if not isinstance(doc, dict) or doc.get('mode') != 'daily':
        return doc, None
    if doc.get('paper') is not True or doc.get('execution_enabled') is not False or doc.get('currency') != 'INR':
        raise ValueError('Expected a paper INR daily source scan')
    timestamp(doc['as_of'])
    return doc, dict(sha256=hashlib.sha256(payload).hexdigest(), as_of=doc['as_of'])


def publish(workspace, scan_path):
    snapshot = Path(scan_path).resolve()
    root = Path(workspace).resolve()
    if root != snapshot and root not in snapshot.parents:
        raise ValueError('Intraday snapshot must be inside the dashboard workspace')
    daily_path = root / 'daily-watchlist.json'
    if not daily_path.is_file() or not snapshot.is_file():
        return None
    daily, binding = capture_daily(daily_path)
    intraday = read(snapshot)
    if (daily.get('mode') != 'daily' or intraday.get('mode') != 'orb'
            or daily.get('paper') is not True or intraday.get('paper') is not True
            or daily.get('execution_enabled') is not False or intraday.get('execution_enabled') is not False
            or daily.get('currency') != 'INR' or intraday.get('currency') != 'INR'):
        raise ValueError('Expected paper INR daily and ORB snapshots')
    origin = intraday.get('daily_origin')
    if origin is not None and origin != binding:
        raise DailySourceChanged('Daily source changed after ORB selection; saved ORB archive was not published')
    if timestamp(intraday['as_of']) < timestamp(daily['as_of']):
        raise ValueError('ORB cutoff predates the daily scan')
    symbols = {r['symbol'] for r in daily['hits']}
    observed = {r['symbol'] for r in intraday['results']} | {r['symbol'] for r in intraday['failed']}
    if not observed or not observed <= symbols or intraday['universe'] != len(observed):
        raise ValueError('ORB universe must be a subset of saved daily matches')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    daily_hash, snapshot_hash = binding['sha256'], sha(snapshot)
    index_path = root / 'intraday-index.json'
    index = read(index_path, {'version': 1, 'snapshots': []})
    if index.get('version') != 1 or not isinstance(index.get('snapshots'), list):
        raise ValueError('Invalid intraday snapshot index')
    entry = dict(path=str(snapshot.relative_to(root)), sha256=snapshot_hash,
                 daily_sha256=daily_hash, daily_cutoff=daily['as_of'],
                 as_of=intraday['as_of'], requested=len(observed), scope='full_shortlist' if observed == symbols else 'subset',
                 source_binding='CAPTURED_BEFORE_FETCH' if origin else 'IMPORTED_AFTER_SCAN')
    index['snapshots'] = [r for r in index['snapshots'] if r.get('path') != entry['path'] or r.get('sha256') != snapshot_hash]
    index['snapshots'].append(entry)
    write(index_path, index)
    return entry
