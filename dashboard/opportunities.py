"""Read saved daily scans and explain a deterministic evidence ranking offline."""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import threading

from india.data import Bar, symbol_name, timestamp, validate
from india.market import DEFAULT_CALENDAR, daily_completed
from workflow.common import upstream
from .decision import candle_metrics, caution_flags, data_health, decision_state, execution_quote, market_clock
from .intraday import overlay

SETUPS = {
    'breakout_20d': ('20-day breakout', 40),
    'bull_flag': ('Bull flag', 32),
    'reclaim_ma20': ('MA20 reclaim', 28),
    'oversold_bounce': ('Oversold bounce', 20),
    'oversold': ('Oversold', 8),
}
RANKING = dict(version='daily-evidence-v1', name='Setup strength', maximum=100,
               description='A rule-based research score, not a probability of profit.',
               rules=[
                   'Strongest setup: breakout 40, bull flag 32, MA20 reclaim 28, oversold bounce 20, oversold 8.',
                   'Additional setups: 5 points each, capped at 10.',
                   'Relative volume: up to 25 points, reaching the cap at 3× the 20-session average.',
                   'Participation: up to 15 points, scaled logarithmically from ₹1 lakh to ₹10 crore of approximate daily turnover.',
                   'Price position: 6 points above MA20 and 4 above the prior 20-session closing high.',
                   'Older candle dates receive a 50% score reduction. Equal scores use turnover, then symbol.'
               ])


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def previous_session(cutoff):
    try:
        return DEFAULT_CALENDAR.last_completed_session(timestamp(cutoff), 'NSE').isoformat()
    except ValueError:
        return None


def ranking(row, expected):
    tags = row['setups']
    turnover = row['price'] * row['volume']
    relative = row.get('volume_x')
    ma20, high20 = row.get('ma20'), row.get('high20')
    parts = [
        dict(label='Strongest setup', points=max(SETUPS[t][1] for t in tags), maximum=40),
        dict(label='Additional setups', points=min(10, (len(tags)-1)*5), maximum=10),
        dict(label='Relative volume', points=25*min(3, max(0, relative))/3 if finite(relative) else 0, maximum=25),
        dict(label='Participation', points=15*min(1, max(0, (math.log10(turnover)-5)/3)) if turnover > 0 else 0, maximum=15),
        dict(label='Price position', points=(6 if finite(ma20) and row['price'] > ma20 else 0) +
             (4 if finite(high20) and row['price'] > high20 else 0), maximum=10),
    ]
    freshness = 'unknown' if expected is None else ('current' if row['as_of'][:10] == expected else 'older')
    multiplier = .5 if freshness == 'older' else 1
    score = round(sum(p['points'] for p in parts) * multiplier, 1)
    for part in parts:
        part['points'] = round(part['points'], 1)
    return dict(score=score, components=parts, freshness=freshness,
                freshness_multiplier=multiplier, turnover=turnover)


def setup_evidence(row):
    def fmt(value, suffix=''):
        return ('%.2f' % value) + suffix if finite(value) else 'unavailable'
    descriptions = {
        'breakout_20d': 'Close ₹%s is above the prior 20-session closing high ₹%s.' % (fmt(row['price']), fmt(row.get('high20'))),
        'bull_flag': 'The preceding 10-session rise is %s (above 10%%); the latest five-close range is %s (below 5%%).' % (fmt(row.get('run_pct'), '%'), fmt(row.get('range5_pct'), '%')),
        'reclaim_ma20': 'The previous close was below its 20-session average; the latest close is above MA20 at ₹%s.' % fmt(row.get('ma20')),
        'oversold': 'Wilder RSI14 is %s, below the 30 threshold. This rule alone does not establish a reversal.' % fmt(row.get('rsi')),
        'oversold_bounce': 'Previous RSI14 was %s, below 30, and the latest close rose from the preceding close.' % fmt(row.get('rsi_prev')),
    }
    return [dict(key=tag, label=SETUPS[tag][0], explanation=descriptions[tag]) for tag in row['setups']]


class OpportunityStore:
    def __init__(self, workspace, research=None, clock=None):
        self.root = Path(workspace).resolve()
        self.research = research
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._key = self._cached = None
        self._lock = threading.RLock()

    def _path(self, relative):
        candidate = self.root / relative
        resolved = candidate.resolve()
        if self.root not in resolved.parents or any(p.is_symlink() for p in (candidate, *candidate.parents) if p != self.root):
            raise ValueError('Scan file is outside the selected workspace')
        return candidate

    def _load(self):
        path = self._path('daily-watchlist.json') if (self.root/'daily-watchlist.json').exists() else self._path('watchlist.json')
        if not path.is_file():
            return None
        names_path = self._path('instrument-names.json')
        stat = path.stat()
        names_stat = names_path.stat() if names_path.is_file() else None
        key = (stat.st_mtime_ns, stat.st_size, names_stat.st_mtime_ns if names_stat else None)
        if key == self._key:
            return self._cached
        payload = path.read_bytes()
        doc = json.loads(payload)
        if (not isinstance(doc, dict) or doc.get('mode') != 'daily' or doc.get('currency') != 'INR'
                or doc.get('paper') is not True or doc.get('execution_enabled') is not False):
            raise ValueError('A saved Indian daily scan is required')
        timestamp(doc['as_of'])
        hits, results, failures = doc['hits'], doc['results'], doc['failed']
        if not all(isinstance(value, list) for value in (hits, results, failures)):
            raise ValueError('Invalid scan rows')
        succeeded = [r for r in results if r.get('status') not in ('FAILED', 'UNAVAILABLE', 'STALE', 'SKIPPED_CONFIRMATION_CAP')]
        result_names = {r.get('symbol') for r in results}
        failure_names = {r.get('symbol') for r in failures}
        if doc['scanned'] != len(succeeded) or doc['universe'] != len(result_names | failure_names):
            raise ValueError('Scan coverage counts do not match its saved rows')
        evidence_rows = {symbol_name(row['symbol']): row for row in results}
        checked = set(evidence_rows)
        failed = {symbol_name(row['symbol']) for row in failures}
        failed_results = {r['symbol'] for r in results if r.get('status') == 'FAILED'}
        if len(checked) != len(results) or len(failed) != len(failures) or (checked & failed)-failed_results:
            raise ValueError('Duplicate or overlapping scan coverage')
        if any(not isinstance(row.get('error'), str) for row in failures):
            raise ValueError('Invalid failure reason')
        exceptions = [dict(row, status='FAILED') for row in failures]
        exceptions.extend(row for row in results if row.get('status') in ('UNAVAILABLE', 'STALE', 'SKIPPED_CONFIRMATION_CAP'))
        coverage = dict(requested=doc['universe'], scanned=doc['scanned'], failed=len(failures),
                        unavailable=sum(row.get('status') == 'UNAVAILABLE' for row in results),
                        stale=sum(row.get('status') == 'STALE' for row in results),
                        skipped=sum(row.get('status') == 'SKIPPED_CONFIRMATION_CAP' for row in results))
        if coverage['requested'] != sum(coverage[key] for key in ('scanned', 'failed', 'unavailable', 'stale', 'skipped')):
            raise ValueError('Scan categories do not reconcile to the requested universe')
        company_names = {}
        if names_stat:
            try:
                metadata = json.loads(names_path.read_text())
                company_names = metadata.get('names', {}) if isinstance(metadata, dict) else {}
            except (ValueError, OSError):
                company_names = {}
            if not isinstance(company_names, dict):
                company_names = {}
        expected = previous_session(doc['as_of'])
        ranked, originals = [], {}
        for hit in hits:
            if not isinstance(hit, dict):
                raise ValueError('Invalid opportunity row')
            symbol = symbol_name(hit['symbol'])
            if (symbol in originals or not isinstance(hit.get('setups'), list) or not hit['setups']
                    or any(t not in SETUPS for t in hit['setups']) or len(set(hit['setups'])) != len(hit['setups'])):
                raise ValueError('Duplicate symbol or unsupported setup')
            if hit != evidence_rows.get(symbol):
                raise ValueError('Opportunity is not present in the scanned evidence')
            if not finite(hit['price']) or hit['price'] <= 0 or not finite(hit['volume']) or hit['volume'] < 0:
                raise ValueError('Invalid opportunity price or volume')
            if not daily_completed(timestamp(hit['as_of']).date(), timestamp(doc['as_of']), exchange='NSE' if symbol.endswith('.NS') else 'BSE'):
                raise ValueError('Daily candle is outside the completed-session cutoff')
            name = company_names.get(symbol)
            name = name.strip()[:180] if isinstance(name, str) and name.strip() else symbol.removesuffix('.NS').removesuffix('.BO')
            detail = ranking(hit, expected)
            row = {k: hit.get(k) for k in ('symbol', 'price', 'volume', 'volume_x', 'setups', 'rsi', 'rsi_prev', 'ma20', 'high20', 'run_pct', 'range5_pct', 'as_of', 'source')}
            row.update(name=name, score=detail['score'], turnover=detail['turnover'], freshness=detail['freshness'])
            ranked.append(row)
            originals[symbol] = hit
        ranked.sort(key=lambda row: (-row['score'], -row['turnover'], row['symbol']))
        for index, row in enumerate(ranked, 1):
            row['rank'] = index
        scan_id = hashlib.sha256(payload).hexdigest()[:20]
        history_root = None
        scans = self._path('scans')
        if scans.is_dir():
            for directory in sorted(scans.iterdir(), reverse=True):
                if directory.is_symlink() or not directory.is_dir():
                    continue
                saved = directory / 'watchlist.json'
                if saved.is_symlink() or not saved.is_file():
                    continue
                if hashlib.sha256(saved.read_bytes()).hexdigest()[:20] == scan_id:
                    history_root = directory
                    break
        dates = sorted({r['as_of'][:10] for r in results if isinstance(r.get('as_of'), str)})
        counts = {key: sum(key in row['setups'] for row in ranked) for key in SETUPS}
        public = dict(scan_id=scan_id, as_of=doc['as_of'], universe=doc['universe'],
                      scanned=doc['scanned'], failed=len(failures), matched=len(ranked),
                      coverage=coverage, coverage_exceptions=exceptions,
                      data_dates=dates, expected_session=expected, setups=counts, ranking=RANKING,
                      opportunities=ranked, failures=failures, execution_enabled=False)
        self._key, self._cached = key, dict(public=public, originals=originals, history_root=history_root,
                                           daily_sha256=hashlib.sha256(payload).hexdigest())
        return self._cached

    def listing(self):
        with self._lock:
            data = self._load()
            if not data:
                return dict(scan_id=None, opportunities=[], failures=[], ranking=RANKING,
                            universe=0, scanned=0, failed=0, matched=0, coverage={}, coverage_exceptions=[], execution_enabled=False)
            public, now = data['public'], self.clock()
            intraday, coverage = overlay(self.root, data['daily_sha256'], public['as_of'],
                                         set(data['originals']), now)
            records = self.research.listing() if self.research else dict(items=[], rejected=0)
            rows = []
            for original in public['opportunities']:
                exchange = 'NSE' if original['symbol'].endswith('.NS') else 'BSE'
                health = data_health(public['as_of'], original['as_of'][:10], now, exchange)
                market = market_clock(now, exchange)
                entry = intraday.get(original['symbol'], dict(status='NOT_CHECKED', eligible=False))
                rows.append(dict(original, flags=caution_flags(original), data_status=health['status'],
                                 entry=entry,
                                 tradeability='BLOCKED' if health['status'] != 'CURRENT_DAILY' or not market['entry_window_open'] else 'INCOMPLETE',
                                 research_count=sum(r['symbol'] == original['symbol'] for r in records['items'])))
            return dict(public, opportunities=rows, observed_at=now.isoformat(), intraday=coverage,
                        market=market_clock(now), stale_count=sum(r['data_status'] != 'CURRENT_DAILY' for r in rows),
                        research_rejected=records['rejected'],
                        caution_note='Review thresholds: MA20 extension ≥10%, RSI ≥70, five-close range ≥10%, volume ≥3×, approximate turnover <₹1 crore, daily |move| ≥8%. These are research flags, not exchange rules or trading probabilities.')

    def detail(self, symbol, scan_id=None):
        symbol_name(symbol)
        with self._lock:
            data = self._load()
            if not data or symbol not in data['originals']:
                raise ValueError('Symbol is not a match in the saved daily scan')
            public = data['public']
            if scan_id and scan_id != public['scan_id']:
                raise ValueError('The saved scan changed. Refresh the opportunities list.')
            original = data['originals'][symbol]
            row = next(r for r in public['opportunities'] if r['symbol'] == symbol)
            evidence = ranking(original, public['expected_session'])
            history, metrics, history_note = [], {}, 'No matching saved candle history is available. Stored scan indicators are shown.'
            if data['history_root']:
                path = data['history_root'] / 'candles' / (symbol+'.json')
                try:
                    if path.resolve().parent != (data['history_root']/'candles').resolve() or path.is_symlink() or path.parent.is_symlink():
                        raise ValueError('Unsafe candle path')
                    saved = json.loads(path.read_text())
                    if (saved.get('symbol') != symbol or saved.get('cutoff') != public['as_of']
                            or saved.get('source') != original.get('source')):
                        raise ValueError('Candle snapshot does not match scan')
                    bars = validate([Bar(timestamp(b['time']), *(float(b[k]) for k in ('open', 'high', 'low', 'close', 'volume'))) for b in saved['bars']])
                    rows = [b for b in bars if daily_completed(b.time.date(), timestamp(public['as_of']), exchange='NSE' if symbol.endswith('.NS') else 'BSE')
                            and str(b.time.year) in DEFAULT_CALENDAR.document.get('NSE' if symbol.endswith('.NS') else 'BSE', {})
                            and DEFAULT_CALENDAR.trading_day(b.time.date(), 'NSE' if symbol.endswith('.NS') else 'BSE')]
                    if (not rows or rows[-1].time.isoformat() != original['as_of']
                            or not math.isclose(rows[-1].close, original['price'], rel_tol=1e-9)
                            or not math.isclose(rows[-1].volume, original['volume'], rel_tol=1e-9)):
                        raise ValueError('Latest candle differs from scan')
                    tags, values = upstream('scanner').find_setups([b.close for b in rows])
                    if tags != original['setups'] or any(
                        value != original.get(key) and not (
                            finite(value) and finite(original.get(key))
                            and math.isclose(value, original[key], rel_tol=1e-8, abs_tol=1e-8))
                        for key, value in values.items()
                    ):
                        raise ValueError('Saved indicator evidence differs from candles')
                    all_history = [dict(time=b.time.isoformat(), open=b.open, high=b.high, low=b.low,
                                        close=b.close, volume=b.volume) for b in rows]
                    metrics = candle_metrics(all_history)
                    history = all_history[-90:]
                    history_note = '%d completed sessions from the same saved provider snapshot.' % len(history)
                except (ValueError, OSError, KeyError, TypeError, IndexError):
                    history_note = 'Saved candles are missing or do not match this scan. Stored indicators remain available.'
            now = self.clock()
            intraday, coverage = overlay(self.root, data['daily_sha256'], public['as_of'],
                                         set(data['originals']), now)
            try:
                context = json.loads(self._path('execution-context.json').read_text())
            except (ValueError, OSError):
                context = {}
            records = self.research.listing() if self.research else dict(items=[], rejected=0)
            linked = [dict(r, same_scan_cutoff=r.get('source_scan_cutoff') == public['as_of']) for r in records['items'] if r['symbol'] == symbol]
            decision = decision_state(row, public['as_of'], now, execution_quote(context, symbol, now),
                                      linked, intraday.get(symbol))
            return dict(**row, scan_id=public['scan_id'], scan_cutoff=public['as_of'], ranking=evidence,
                        entry=intraday.get(symbol, dict(status='NOT_CHECKED', eligible=False)), intraday=coverage,
                        evidence=setup_evidence(original), candles=history, history_note=history_note,
                        metrics=metrics, flags=caution_flags(original, metrics), decision=decision,
                        provider_attempts=original.get('provider_attempts', []),
                        checks=decision['checks'],
                        execution_enabled=False)
