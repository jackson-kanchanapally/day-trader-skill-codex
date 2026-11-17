"""Local paper research from delayed snapshots, never exchange candles.

Two free sources, both recorded as evidence only:

  indices   NSE official MCP. One bounded call per tick returns every index;
            the configured ones are sampled into five-minute windows.
  stocks    Yahoo's spark endpoint. One call returns today's five-minute
            closes for up to 20 symbols, so the universe is refreshed once per
            completed window, in sequence, with a pause between calls.

The NSE stock snapshot (feed 'equity') stays available but is off by default:
one call returns at most 500 of about 2,000 rows, which covered roughly a
tenth of the NIFTY 200 when checked. No immediate transport retries. Missing
samples, closes and counters stay missing.
"""
import datetime as dt
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from .data import IST, symbol_name, timestamp
from .market import DEFAULT_CALENDAR
from .nse_mcp import MARKET, MCPSession

KIND = 'SAMPLED_DELAYED_SNAPSHOT_5M'
SOURCE = 'NSE official MCP delayed snapshots; sampled, informational paper research only'
YAHOO_KIND = 'YAHOO_5M_CLOSE'
YAHOO_SOURCE = 'Yahoo Finance spark endpoint (unofficial); five-minute closes only, paper research only'
YAHOO_URL = 'https://query2.finance.yahoo.com/v8/finance/spark?symbols=%s&range=1d&interval=5m'
YAHOO_BATCH = 20            # the endpoint rejects more symbols per call
YAHOO_PAUSE = 1.0           # seconds between the calls of one refresh
YAHOO_SETTLE = 20           # seconds after a window ends before its close is read
FEEDS = ('indices', 'equity')
BACKOFF_BASE = 60           # seconds; doubles per consecutive failure of one feed
# Reasons that make the sampled prices themselves untrustworthy (not just sparse).
INTEGRITY = frozenset(('CONFLICTING_SOURCE_TIMESTAMP', 'SESSION_EXTREMA_RESET'))
SECTOR_INDICES = ['NIFTY AUTO', 'NIFTY FIN SERVICE', 'NIFTY FMCG', 'NIFTY IT', 'NIFTY MEDIA',
                  'NIFTY METAL', 'NIFTY PHARMA', 'NIFTY PSU BANK', 'NIFTY PVT BANK', 'NIFTY REALTY',
                  'NIFTY HEALTHCARE', 'NIFTY CONSR DURBL', 'NIFTY OIL AND GAS', 'NIFTY ENERGY']
DEFAULTS = dict(enabled=False, state_dir='var/state/recorder', feeds=['indices'], poll_seconds=30,
                snapshot_limit=500, symbol_filter='', max_age_seconds=180,
                timeout_seconds=10, max_backoff_seconds=900, stock_source='yahoo',
                symbols=[], indices=['NIFTY 50', 'NIFTY BANK', 'INDIA VIX'] + SECTOR_INDICES)


def settings(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError('recorder settings must be an object')
    supplied = value or {}
    if set(supplied) - set(DEFAULTS):
        raise ValueError('Unknown recorder settings: ' + ', '.join(sorted(set(supplied) - set(DEFAULTS))))
    cfg = dict(DEFAULTS, **supplied)
    if type(cfg['enabled']) is not bool:
        raise ValueError('recorder.enabled must be true or false')
    for name, low, high in (('poll_seconds', 30, 300), ('snapshot_limit', 1, 500),
                            ('max_age_seconds', 60, 600), ('timeout_seconds', 1, 15),
                            ('max_backoff_seconds', 300, 3600)):
        if type(cfg[name]) is not int or not low <= cfg[name] <= high:
            raise ValueError('recorder.%s must be a whole number from %d to %d' % (name, low, high))
    if not isinstance(cfg['state_dir'], str) or not cfg['state_dir'].strip():
        raise ValueError('recorder.state_dir must be a nonempty path')
    if not isinstance(cfg['symbol_filter'], str) or len(cfg['symbol_filter']) > 50:
        raise ValueError('recorder.symbol_filter must be a symbol prefix of at most 50 characters')
    feeds = cfg['feeds']
    if (not isinstance(feeds, list) or not feeds or len(set(map(str, feeds))) != len(feeds)
            or any(f not in FEEDS for f in feeds)):
        raise ValueError('recorder.feeds must list "indices" and/or "equity", each once')
    cfg['feeds'] = list(feeds)
    if cfg['stock_source'] not in ('yahoo', 'off'):
        raise ValueError('recorder.stock_source must be "yahoo" or "off"')
    for name in ('symbols', 'indices'):
        values = cfg[name]
        if (not isinstance(values, list) or len(values) > 500
                or any(not isinstance(s, str) or not s.strip() for s in values)
                or len(set(values)) != len(values)):
            raise ValueError('recorder.%s must contain unique nonempty names (at most 500)' % name)
        cfg[name] = list(values)
    for symbol in cfg['symbols']:
        symbol_name(symbol)
        if not symbol.endswith('.NS'):
            raise ValueError('The NSE recorder supports .NS equity symbols only')
    if not cfg['indices']:
        raise ValueError('recorder.indices must include at least one index')
    return cfg


def _number(value, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (value <= 0 if positive else value < 0)):
        raise ValueError('missing or invalid numeric field')
    return float(value)


def _source_time(value, index=False):
    if not isinstance(value, str):
        raise ValueError('missing source timestamp')
    try:
        return dt.datetime.strptime(value, '%d-%b-%Y %H:%M' if index else '%Y-%m-%d %H:%M:%S').replace(tzinfo=IST)
    except ValueError:
        raise ValueError('unrecognized source timestamp') from None


def normalize(doc, feed, symbols, indices, requested_at, received_at, max_age_seconds, calendar):
    """Validate each requested instrument separately; do not substitute cache time."""
    requested_at, received_at = timestamp(requested_at), timestamp(received_at)
    if received_at < requested_at:
        raise ValueError('Snapshot receipt precedes its request')
    if not isinstance(doc, dict) or feed not in ('equity', 'indices'):
        raise ValueError('Unexpected NSE snapshot document')
    wanted = set(symbols if feed == 'equity' else indices)
    if feed == 'equity':
        rows = doc.get('stocks')
    else:
        groups = doc.get('indices')
        if not isinstance(groups, dict) or any(not isinstance(v, list) for v in groups.values()):
            raise ValueError('Unexpected grouped NSE index snapshot')
        rows = [row for group in groups.values() for row in group]
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('Unexpected or oversized NSE snapshot rows')
    quotes, rejected, duplicate, seen = {}, [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_name = row.get('symbol') if feed == 'equity' else row.get('indexName')
        if not isinstance(raw_name, str):
            continue
        name = raw_name + '.NS' if feed == 'equity' else raw_name
        if name not in wanted or (feed == 'equity' and row.get('series') != 'EQ'):
            continue
        if name in seen:
            duplicate.add(name)
            continue
        seen.add(name)
        try:
            observed = _source_time(row.get('latestTimestamp') if feed == 'equity' else row.get('timeVal'), feed == 'indices')
            age = (received_at - observed).total_seconds()
            if observed.date() != requested_at.date() or age < 0 or age > max_age_seconds:
                raise ValueError('stale, future or wrong-session source timestamp')
            hours = calendar.hours(observed.date(), 'NSE')
            if not hours or not hours[0] <= observed.time() < hours[1]:
                raise ValueError('source timestamp is outside the sourced session')
            price, opening, high, low = [_number(row.get(k), True) for k in (
                ('lastTradedPrice', 'openPrice', 'highPrice', 'lowPrice') if feed == 'equity'
                else ('last', 'open', 'high', 'low'))]
            if low > min(price, opening) or high < max(price, opening):
                raise ValueError('invalid session OHLC range')
            volume = _number(row.get('volume')) if feed == 'equity' else None
            if volume is not None and not volume.is_integer():
                raise ValueError('cumulative share volume is not a whole number')
            value = row.get('value') if feed == 'equity' else None
            value = _number(_number(value) * 10_000_000) if value is not None else None
            previous = row.get('preClosePrice' if feed == 'equity' else 'previousClose')
            quotes[name] = dict(symbol=name, feed=feed, observed_at=observed.isoformat(),
                                received_at=received_at.isoformat(), price=price, session_open=opening,
                                session_high=high, session_low=low, cumulative_volume=volume,
                                cumulative_value_inr=value,
                                session_vwap=value / volume if value is not None and volume else None,
                                previous_close=_number(previous, True) if previous is not None else None,
                                timestamp_precision='minute' if feed == 'indices' else 'second',
                                age_seconds=round(age, 3))
        except (TypeError, ValueError) as exc:
            rejected.append(dict(symbol=name, reason=str(exc)))
    for name in duplicate:
        quotes.pop(name, None)
        rejected.append(dict(symbol=name, reason='duplicate requested instrument in response'))
    return dict(feed=feed, requested_at=requested_at.isoformat(), received_at=received_at.isoformat(),
                source=SOURCE, paper=True, execution_enabled=False, quotes=list(quotes.values()),
                source_updated_at=doc.get('updatedAt'), source_warning=doc.get('warning'),
                returned_rows=len(rows), requested=len(wanted), covered=len(quotes),
                missing=sorted(wanted - set(quotes)), rejected=rejected)


def _minute(value):
    return timestamp(value).replace(second=0, microsecond=0)


def sampled_candle(symbol, start, quotes, known_at, session_open=None):
    """Build one causal, explicitly sampled window; completeness means sample coverage.

    complete  every source minute was sampled and the counters are valid.
    usable    the looser test for a close-based check (see the field below).

    Day extrema are attributed only when BOTH bounding samples are in this
    window. A new day extreme across a boundary has no provable candle owner.
    Counter differences retain their actual sampling window, not an invented
    exchange-aligned volume interval.
    """
    start = timestamp(start.isoformat())
    session_open = timestamp(session_open.isoformat()) if session_open else None
    end = start + dt.timedelta(minutes=5)
    known_at = timestamp(known_at)
    if known_at < end:
        raise ValueError('Cannot complete a future candle')
    ordered = sorted((q for q in quotes if timestamp(q['received_at']) <= known_at
                      and timestamp(q['observed_at']).date() == start.date()
                      and timestamp(q['observed_at']) < end),
                     key=lambda q: (q['observed_at'], q['received_at']))
    before = [q for q in ordered if timestamp(q['observed_at']) < start]
    samples = [q for q in ordered if timestamp(q['observed_at']) >= start]
    equity = not symbol.startswith('INDEX:')
    conflicts = False
    if equity:
        # Identical repeated source times are not additional minute observations.
        unique = {}
        for q in samples:
            old = unique.get(q['observed_at'])
            if old and any(old[k] != q[k] for k in ('price', 'cumulative_volume', 'session_high', 'session_low')):
                conflicts = True
            unique.setdefault(q['observed_at'], q)
        samples = list(unique.values())
    # Index times have minute precision: several samples inside one source
    # minute are ordinary, stay in receipt order, and the latest one closes it.
    expected = [start + dt.timedelta(minutes=i) for i in range(5)]
    slots = {_minute(q['observed_at']) for q in samples}
    missing = [t.isoformat() for t in expected if t not in slots]
    reasons = ['MISSING_MINUTE_SAMPLES'] if missing else []
    warnings, recovered = [], 0
    if conflicts:
        reasons.append('CONFLICTING_SOURCE_TIMESTAMP')
    prices = [q['price'] for q in samples]
    high, low = (max(prices), min(prices)) if prices else (None, None)
    opening = prices[0] if prices else None
    if session_open == start and samples:
        # At the sourced session open, the first session extrema belong to
        # this window. Later windows cannot inherit the day's earlier range.
        opening = samples[0]['session_open']
        high, low = max(high, samples[0]['session_high']), min(low, samples[0]['session_low'])
    previous = before[-1] if before else None
    volume, volume_from, volume_to = None, None, None
    for q in samples:
        if previous:
            if q['session_high'] < previous['session_high'] or q['session_low'] > previous['session_low']:
                reasons.append('SESSION_EXTREMA_RESET')
            rises = q['session_high'] > previous['session_high']
            falls = q['session_low'] < previous['session_low']
            if rises or falls:
                if timestamp(previous['observed_at']) >= start:
                    high = max(high, q['session_high']) if rises else high
                    low = min(low, q['session_low']) if falls else low
                    recovered += int(rises) + int(falls)
                else:
                    warnings.append('BOUNDARY_EXTREME_UNASSIGNED')
        previous = q
    if equity:
        baseline = before[-1] if before and _minute(before[-1]['observed_at']) == start - dt.timedelta(minutes=1) else None
        opening_counter = session_open == start
        if not baseline and not opening_counter:
            reasons.append('MISSING_VOLUME_BASELINE')
        path = ([baseline] if baseline else []) + samples
        counters = [q['cumulative_volume'] for q in path]
        if any(v is None for v in counters) or any(b < a for a, b in zip(counters, counters[1:])):
            reasons.append('INVALID_CUMULATIVE_VOLUME')
        elif samples and (baseline or opening_counter):
            # Session cumulative volume starts at zero by definition. This
            # exception never supplies a missing counter for a midday start.
            volume = samples[-1]['cumulative_volume'] - (baseline['cumulative_volume'] if baseline else 0)
            volume_from = baseline['observed_at'] if baseline else session_open.isoformat()
            volume_to = samples[-1]['observed_at']
    return dict(symbol=symbol, time=start.isoformat(), end=end.isoformat(), known_at=known_at.isoformat(),
                open=opening, high=high, low=low, close=prices[-1] if prices else None,
                open_basis='session_open' if session_open == start else 'first_sample',
                volume=volume, volume_window_start=volume_from, volume_window_end=volume_to,
                volume_exchange_aligned=False, session_vwap=samples[-1]['session_vwap'] if samples else None,
                first_sample_at=samples[0]['observed_at'] if samples else None,
                last_sample_at=samples[-1]['observed_at'] if samples else None,
                sample_minutes=len(slots), missing_minutes=missing,
                complete=not reasons, ohlcv_complete=equity and not reasons and volume is not None,
                # Enough for a close-based check: the final minute was sampled, at most one
                # minute is missing and the prices are consistent. Says nothing about volume.
                usable=bool(samples) and expected[-1] in slots and len(missing) <= 1
                and not INTEGRITY.intersection(reasons),
                reasons=sorted(set(reasons)), quality_warnings=sorted(set(warnings)), recovered_extremes=recovered,
                kind=KIND, source=SOURCE, exchange_candle=False, paper=True, execution_enabled=False)


def _load(state_dir, table, day, as_of):
    path = (Path(state_dir) / 'snapshots.sqlite').resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        try:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM %s WHERE day=? ORDER BY time,symbol' % table, (str(day),))]
        except sqlite3.OperationalError:
            return []       # a database written before this table existed
    cutoff = timestamp(as_of) if as_of else None
    return [r for r in rows if cutoff is None or timestamp(r['known_at']) <= cutoff]


def load_candles(state_dir, day, as_of=None, complete_only=False, usable_only=False):
    """Read frozen sampled candles, optionally restricted to evidence known at a cutoff."""
    return [r for r in _load(state_dir, 'candles', day, as_of)
            if (not complete_only or r['complete']) and (not usable_only or r.get('usable'))]


def load_stock_closes(state_dir, day, as_of=None):
    """Read recorded Yahoo five-minute closes, optionally only those known at a cutoff."""
    return _load(state_dir, 'stock_candles', day, as_of)


def http_json(url, timeout=10):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(5_000_001))


def parse_spark(doc, symbols, received_at, calendar, settle_seconds=YAHOO_SETTLE):
    """({symbol: [(window start, close)]}, rejected) for today's completed five-minute windows.

    A window counts only when it lies inside the sourced NSE session, starts on
    the five-minute grid and ended at least settle_seconds before the receipt.
    A null close (no trade, or not published) is left out, never filled.
    """
    received = timestamp(received_at)
    if not isinstance(doc, dict):
        raise ValueError('Unexpected Yahoo spark document')
    hours = calendar.hours(received.date(), 'NSE')
    out, rejected = {}, []
    for symbol in symbols:
        row = doc.get(symbol)
        try:
            if not isinstance(row, dict) or row.get('symbol') != symbol:
                raise ValueError('symbol missing from the response')
            if row.get('dataGranularity') != 300:
                raise ValueError('not five-minute data')
            times, closes = row.get('timestamp'), row.get('close')
            if not isinstance(times, list) or not isinstance(closes, list) or len(times) != len(closes):
                raise ValueError('malformed series')
            bars = {}
            for t, c in zip(times, closes):
                if type(t) is not int or c is None:
                    continue
                start = dt.datetime.fromtimestamp(t, IST)
                if (start.date() != received.date() or start.second or start.minute % 5 or not hours
                        or not hours[0] <= start.time() < hours[1]
                        or start + dt.timedelta(minutes=5, seconds=settle_seconds) > received):
                    continue
                bars[start] = _number(c, True)
            out[symbol] = sorted(bars.items())
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            rejected.append(dict(symbol=symbol, reason=str(exc)))
    return out, rejected


class SnapshotRecorder:
    """Durable scheduler for both sources, shared by --serve and the dry-run check."""
    def __init__(self, config, calendar=None, universe_loader=None, session=None,
                 now_fn=None, dry_run=False, http_get=None, sleep=None):
        self.cfg = settings(config)
        self.calendar = calendar or DEFAULT_CALENDAR
        self.universe_loader = universe_loader
        self.now_fn = now_fn or (lambda: dt.datetime.now(IST))
        self.session = session or MCPSession(MARKET, timeout=self.cfg['timeout_seconds'],
                                             attempts=1, recover_expired=False)
        self.dry_run, self.symbols, self.universe_warnings = dry_run, None, []
        self.http_get = http_get or (lambda url: http_json(url, self.cfg['timeout_seconds']))
        self.sleep = sleep or time.sleep
        self.root = Path(self.cfg['state_dir'])
        if not dry_run:
            self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(':memory:' if dry_run else str(self.root / 'snapshots.sqlite'), timeout=2)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, first_request TEXT NOT NULL,
                opening TEXT NOT NULL, closing TEXT NOT NULL, symbols TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                requested_at TEXT NOT NULL, feed TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candles (day TEXT NOT NULL, symbol TEXT NOT NULL,
                time TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(day,symbol,time));
            CREATE TABLE IF NOT EXISTS stock_candles (day TEXT NOT NULL, symbol TEXT NOT NULL,
                time TEXT NOT NULL, close REAL NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(day,symbol,time));
        ''')

    def close(self):
        self.db.close()

    def _get(self, name, default=None):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, name, value):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (name, json.dumps(value, allow_nan=False)))

    def _universe(self):
        if self.symbols is None:
            self.symbols = list(self.cfg['symbols'])
            if not self.symbols and self.universe_loader:
                symbols, warnings = self.universe_loader()
                self.symbols, self.universe_warnings = list(symbols), list(warnings)
            if not self.symbols or len(set(self.symbols)) != len(self.symbols):
                raise ValueError('Recorder needs a nonempty, unique configured equity universe')
            for symbol in self.symbols:
                symbol_name(symbol)
                if not symbol.endswith('.NS'):
                    raise ValueError('NSE recorder universe must contain .NS equities only')
        return self.symbols

    def _finalize(self, now):
        emitted = 0
        for day, first, opening, closing, names in self.db.execute('SELECT * FROM days').fetchall():
            if dt.date.fromisoformat(day) > now.date() or self._get('finalized_' + day, False):
                continue
            quotes = {s: [] for s in json.loads(names)}
            for (payload,) in self.db.execute('SELECT payload FROM polls WHERE day=? ORDER BY id', (day,)):
                for q in json.loads(payload).get('quotes', []):
                    key = q['symbol'] if q['feed'] == 'equity' else 'INDEX:' + q['symbol']
                    if key in quotes:
                        quotes[key].append(q)
            existing = {(s, t) for s, t in self.db.execute('SELECT symbol,time FROM candles WHERE day=?', (day,))}
            # ISO strings share the IST offset, so the latest source time is the largest string.
            latest = {s: timestamp(max(q['observed_at'] for q in obs)) for s, obs in quotes.items() if obs}
            grace = dt.timedelta(seconds=self.cfg['max_age_seconds'])
            session_open = timestamp(opening)
            offset = max(0, int((timestamp(first) - session_open).total_seconds()) // 300)
            start = session_open + dt.timedelta(minutes=offset * 5)
            while start + dt.timedelta(minutes=5) <= min(now, timestamp(closing)):
                for symbol, observations in quotes.items():
                    if (symbol, start.isoformat()) in existing:
                        continue
                    # Freeze once the source has moved past the window, so a later sample of
                    # the final minute still counts; otherwise wait a bounded grace.
                    end = start + dt.timedelta(minutes=5)
                    if not (symbol in latest and latest[symbol] >= end) and now < end + grace:
                        continue
                    row = sampled_candle(symbol, start, observations, now.isoformat(), session_open)
                    self.db.execute('INSERT OR IGNORE INTO candles VALUES (?,?,?,?)',
                                    (day, symbol, start.isoformat(), json.dumps(row, allow_nan=False)))
                    emitted += 1
                start += dt.timedelta(minutes=5)
            if now >= timestamp(closing) + dt.timedelta(seconds=self.cfg['max_age_seconds']):
                self._set('finalized_' + day, True)
        return emitted

    def tick(self, now=None, feed=None):
        now = timestamp((now or self.now_fn()).isoformat())
        if feed not in (None, 'equity', 'indices'):
            raise ValueError('Recorder feed must be equity or indices')
        if not self.cfg['enabled']:
            return dict(status='DISABLED', paper=True, execution_enabled=False)
        hours = self.calendar.hours(now.date(), 'NSE')
        with self.db:
            finalized = self._finalize(now)
        if not hours or not hours[0] <= now.time() < hours[1]:
            return dict(status='OUTSIDE_SESSION', finalized=finalized, paper=True, execution_enabled=False)
        feeds = self.cfg['feeds']
        symbols = self._universe() if 'equity' in feeds or feed == 'equity' else []
        all_names = (symbols if 'equity' in feeds else []) + ['INDEX:' + name for name in self.cfg['indices']]
        # Reserve before network I/O. Another instance sharing this DB cannot
        # issue a second call in the same slot, including immediately on restart.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            gate = self._get('next_call_at')
            if gate and now < timestamp(gate):
                return dict(status='WAITING', next_poll_at=gate, finalized=finalized)
            # Each feed backs off alone: a bad sample on one never pauses another.
            due = {f: self._get('due_' + f) for f in ([feed] if feed else feeds)}
            ready = [f for f, when in due.items() if not when or now >= timestamp(when)]
            if not ready:
                return dict(status='WAITING', next_poll_at=min(due.values()), finalized=finalized)
            last = self._get('last_feed')
            feed = next((f for f in ready if f != last), ready[0])
            day = now.date().isoformat()
            prior = self.db.execute('SELECT symbols FROM days WHERE day=?', (day,)).fetchone()
            if prior and json.loads(prior[0]) != all_names:
                raise ValueError('Recorder universe changed during a session; use a separate state directory')
            self.db.execute('INSERT OR IGNORE INTO days VALUES (?,?,?,?,?)', (
                day, now.isoformat(), dt.datetime.combine(now.date(), hours[0], IST).isoformat(),
                dt.datetime.combine(now.date(), hours[1], IST).isoformat(), json.dumps(all_names)))
            # Lease the entire handshake + data timeout budget. A second
            # process must not overlap a slow in-flight request; a crashed
            # process leaves a bounded lease rather than an immediate retry.
            lease = self.cfg['poll_seconds'] + 3 * self.cfg['timeout_seconds']
            self._set('next_call_at', (now + dt.timedelta(seconds=lease)).isoformat())
            self._set('last_feed', feed)
        try:
            tool, arguments = ('cm_get_equity_stocks', dict(limit=self.cfg['snapshot_limit'], symbolFilter=self.cfg['symbol_filter'])) \
                if feed == 'equity' else ('cm_get_live_indices', dict(group='all', nameFilter=''))
            doc = self.session.call(tool, arguments)
            finished = timestamp(self.now_fn().isoformat())
            event = normalize(doc, feed, symbols, self.cfg['indices'], now.isoformat(), finished.isoformat(),
                              self.cfg['max_age_seconds'], self.calendar)
            status = 'RECORDED' if event['covered'] else 'NO_USABLE_QUOTES'
        except (ValueError, OSError, KeyError, TypeError) as exc:
            finished = timestamp(self.now_fn().isoformat())
            event = dict(feed=feed, requested_at=now.isoformat(), received_at=finished.isoformat(),
                         quotes=[], requested=len(symbols if feed == 'equity' else self.cfg['indices']), covered=0,
                         missing=sorted(symbols if feed == 'equity' else self.cfg['indices']),
                         error=str(exc), paper=True, execution_enabled=False)
            status = 'UNAVAILABLE'
        with self.db:
            failures = self._get('failures_' + feed, 0) + 1 if status in ('UNAVAILABLE', 'NO_USABLE_QUOTES') else 0
            self._set('failures_' + feed, failures)
            spacing = (finished + dt.timedelta(seconds=self.cfg['poll_seconds'])).isoformat()
            backoff = (finished + dt.timedelta(seconds=min(
                self.cfg['max_backoff_seconds'], BACKOFF_BASE * 2 ** min(failures - 1, 10)))).isoformat() if failures else None
            self._set('next_call_at', spacing)
            self._set('due_' + feed, backoff)
            self.db.execute('INSERT INTO polls(day,requested_at,feed,payload) VALUES (?,?,?,?)',
                            (day, now.isoformat(), feed, json.dumps(event, allow_nan=False)))
            finalized += self._finalize(finished)
            summary = dict(status=status, feed=feed, requested_at=now.isoformat(), received_at=finished.isoformat(),
                           requested=event.get('requested'), covered=event.get('covered', 0),
                           missing=event.get('missing', []), rejected=event.get('rejected', []),
                           returned_rows=event.get('returned_rows'), error=event.get('error'),
                           source_warning=event.get('source_warning'), universe_warnings=self.universe_warnings,
                           finalized=finalized, next_poll_at=backoff or spacing,
                           oldest_quote_age_seconds=max((q['age_seconds'] for q in event['quotes']), default=None),
                           source=SOURCE, kind=KIND, paper=True, execution_enabled=False)
            self._set('last_' + feed, summary)
        self._write_status()
        return summary

    def _write_status(self):
        if self.dry_run:
            return
        target = self.root / 'status.json'
        temporary = self.root / 'status.json.tmp'
        temporary.write_text(json.dumps({name: self._get('last_' + name) for name in ('indices', 'equity', 'stocks')},
                                        indent=2, allow_nan=False) + '\n')
        temporary.replace(target)

    def stocks_tick(self, now=None, force=False):
        """Refresh the universe's five-minute closes from Yahoo, once per completed window.

        Calls run one after another with a pause between them. The first failed
        call ends the refresh and backs the source off; nothing is retried at
        once. Each response carries the whole session so far, so a window missed
        earlier is still recorded later, with the time it actually became known.
        """
        now = timestamp((now or self.now_fn()).isoformat())
        idle = dict(feed='stocks', paper=True, execution_enabled=False)
        if not self.cfg['enabled'] or self.cfg['stock_source'] == 'off':
            return dict(idle, status='DISABLED')
        hours = self.calendar.hours(now.date(), 'NSE')
        if not hours:
            return dict(idle, status='OUTSIDE_SESSION')
        opening = dt.datetime.combine(now.date(), hours[0], IST)
        closing_at = dt.datetime.combine(now.date(), hours[1], IST)
        # The last window ends at the close, so allow one refresh shortly after it.
        if not opening <= now < closing_at + dt.timedelta(minutes=5):
            return dict(idle, status='OUTSIDE_SESSION')
        ready = now - dt.timedelta(seconds=YAHOO_SETTLE)
        window_end = min(ready.replace(minute=ready.minute - ready.minute % 5, second=0, microsecond=0), closing_at)
        if window_end <= opening:
            return dict(idle, status='WAITING', next_poll_at=(opening + dt.timedelta(minutes=5, seconds=YAHOO_SETTLE)).isoformat())
        symbols = self._universe()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            due = self._get('stocks_due')
            if not force and ((due and now < timestamp(due)) or self._get('stocks_window') == window_end.isoformat()):
                return dict(idle, status='WAITING', next_poll_at=due)
            self._set('stocks_window', window_end.isoformat())     # reserved before any network I/O
        day, latest = now.date().isoformat(), (window_end - dt.timedelta(minutes=5)).isoformat()
        stored = {(sym, when): close for sym, when, close in self.db.execute(
            'SELECT symbol,time,close FROM stock_candles WHERE day=?', (day,))}
        calls, error, rejected, fresh, revised, covered = 0, None, [], [], 0, set()
        for i in range(0, len(symbols), YAHOO_BATCH):
            batch = symbols[i:i + YAHOO_BATCH]
            if i:
                self.sleep(YAHOO_PAUSE)
            calls += 1
            try:
                doc = self.http_get(YAHOO_URL % urllib.parse.quote(','.join(batch)))
                received = timestamp(self.now_fn().isoformat())
                bars, bad = parse_spark(doc, batch, received.isoformat(), self.calendar)
            except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
                error = 'HTTP %d' % exc.code if isinstance(exc, urllib.error.HTTPError) else (str(exc) or type(exc).__name__)
                break
            rejected += bad
            for sym, rows in bars.items():
                for start, close in rows:
                    key = (sym, start.isoformat())
                    if key[1] == latest:
                        covered.add(sym)
                    if key in stored:
                        revised += abs(stored[key] - close) > 0.005    # kept as first recorded
                        continue
                    fresh.append((day, sym, key[1], close, json.dumps(dict(
                        symbol=sym, time=key[1], end=(start + dt.timedelta(minutes=5)).isoformat(), close=close,
                        known_at=received.isoformat(), kind=YAHOO_KIND, source=YAHOO_SOURCE, close_only=True,
                        exchange_candle=False, paper=True, execution_enabled=False), allow_nan=False)))
        finished = timestamp(self.now_fn().isoformat())
        status = ('RECORDED' if covered else 'NO_USABLE_QUOTES') if error is None else 'PARTIAL' if fresh else 'UNAVAILABLE'
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO stock_candles VALUES (?,?,?,?,?)', fresh)
            failures = self._get('failures_stocks', 0) + 1 if error or not covered else 0
            self._set('failures_stocks', failures)
            backoff = (finished + dt.timedelta(seconds=min(
                self.cfg['max_backoff_seconds'], 300 * 2 ** min(failures - 1, 10)))).isoformat() if failures else None
            self._set('stocks_due', backoff)
            summary = dict(idle, status=status, window=latest, requested=len(symbols), covered=len(covered),
                           missing=sorted(set(symbols) - covered), rejected=rejected, calls=calls,
                           new_candles=len(fresh), revised_closes=revised, error=error,
                           requested_at=now.isoformat(), received_at=finished.isoformat(), next_poll_at=backoff,
                           universe_warnings=self.universe_warnings, source=YAHOO_SOURCE, kind=YAHOO_KIND)
            self.db.execute('INSERT INTO polls(day,requested_at,feed,payload) VALUES (?,?,?,?)',
                            (day, now.isoformat(), 'stocks', json.dumps(summary, allow_nan=False)))
            self._set('last_stocks', summary)
        self._write_status()
        return summary
