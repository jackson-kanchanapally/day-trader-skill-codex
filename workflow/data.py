"""Provider reuse, multi-symbol exports and explicit INR security metadata."""
import csv
import datetime as dt
import gzip
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from india.data import Bar, IST, timestamp, validate, symbol_name
from india.nse import NSEProvider
from india.upstox import UpstoxProvider
from india.market import DEFAULT_CALENDAR, daily_completed
from india.security import security_master, verify_tick


def universe(exchange, instruments=None):
    """Read NSE's official EQ list or a supplied/Upstox public directory.

    Tick values are deliberately not normalized here: raw instrument masters
    do not substitute for dated, explicitly INR-denominated tick evidence.
    """
    if exchange == 'NSE' and not instruments:
        equity_url = 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv'
        etf_url = 'https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv'
        def listing(url):
            request = urllib.request.Request(url, headers={'User-Agent': 'IndianTradingResearch/1.0'})
            with urllib.request.urlopen(request, timeout=20) as response:
                if urllib.parse.urlsplit(response.url).hostname != 'nsearchives.nseindia.com':
                    raise ValueError('NSE security directory redirected away from NSE')
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('NSE security directory exceeds 2 MB')
            return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        equity, etfs = listing(equity_url), listing(etf_url)
        if not equity or not etfs or not {'SYMBOL', ' SERIES', ' MARKET LOT'} <= set(equity[0]) or 'Symbol' not in etfs[0]:
            raise ValueError('Official NSE equity/ETF directory schema is unavailable')
        funds = {r['Symbol'].strip() for r in etfs}
        names = {symbol_name(r['SYMBOL'].strip()+'.NS') for r in equity
                 if r[' SERIES'].strip() == 'EQ' and r[' MARKET LOT'].strip() == '1'
                 and r['SYMBOL'].strip() not in funds}
        if len(names) < 1000:
            raise ValueError('Official NSE EQ directory unexpectedly short')
        return sorted(names), equity_url+'; ETF exclusion: '+etf_url
    url = 'https://assets.upstox.com/market-quote/instruments/exchange/'+exchange+'.json.gz'
    if instruments:
        raw = Path(instruments).read_bytes()
        rows = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
    else:
        from india.upstox import get_json
        rows = get_json(url, compressed=True)
    names = set()
    for row in rows:
        if row.get('segment') != exchange+'_EQ' or row.get('instrument_type') != 'EQ' or row.get('lot_size') != 1 or row.get('security_type', 'NORMAL') != 'NORMAL':
            continue
        product = ' '.join(str(row.get(k, '')) for k in ('name', 'trading_symbol', 'instrument_type'))
        if (any(row.get(k) is True for k in ('is_sme', 'is_suspended', 'suspended'))
                or row.get('series', 'EQ') not in ('EQ', 'A', 'B')
                or re.search(r'\b(ETF|ETN|REIT|INVIT|warrants?|debentures?|bonds?)\b|BEES\b|exchange.traded|mutual fund', product, re.I)):
            continue
        name = row.get('trading_symbol') if exchange == 'NSE' else str(row.get('exchange_token', ''))
        if name:
            names.add(symbol_name(name+('.NS' if exchange == 'NSE' else '.BO')))
    if not names:
        raise ValueError('No eligible cash equities in instrument directory')
    return sorted(names), str(instruments) if instruments else url


class MarketData:
    def __init__(self, as_of, provider='yahoo', csv_path=None, days=60, instruments=None, auto_fallback=True, calendar=None):
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
            raise ValueError('Days must be between 1 and 366')
        self.as_of, self.provider, self.csv_path = as_of, provider, csv_path
        self.days, self.instruments = days, instruments
        self._rows, self._cache = None, {}
        self._errors = {}
        self.fetch_metrics = {}
        self.intraday_range = None
        self._retrieval_attempts = []
        self._last_request = None
        self._nse, self._upstox = None, None
        self.auto_fallback, self.calendar = auto_fallback, calendar or DEFAULT_CALENDAR
        self.sources, self.attempts = {}, {}
        self.source = 'User CSV export' if csv_path else {'yahoo': 'Yahoo unofficial; may be delayed', 'nse': 'NSE experimental website adapter; provider terms apply', 'upstox': 'Upstox V3'}[provider]

    def history(self, symbol, interval='5m'):
        symbol_name(symbol)
        if interval not in ('1m', '5m', '1d'):
            raise ValueError('Supported intervals: 1m, 5m, 1d')
        key = symbol, interval
        if key in self._errors:
            raise ValueError(self._errors[key])
        if key in self._cache:
            self.source = self.sources[key]
            return self._cache[key]
        if self.csv_path:
            path = Path(self.csv_path)
            if path.is_dir():
                path = next((path/(symbol+ext) for ext in ('.csv', '.json', '.json.gz') if (path/(symbol+ext)).is_file()), path/(symbol+'.csv'))
            if str(path).endswith(('.json', '.json.gz')):
                raw = path.read_bytes()
                doc = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
                bars = parse_yahoo(doc, symbol, interval, self.as_of,self.calendar)
            else:
                if self._rows is None or Path(self.csv_path).is_dir():
                    with open(path, newline='', encoding='utf-8') as f:
                        self._rows = list(csv.DictReader(f))
                rows = [r for r in self._rows if r['symbol'] == symbol and r['interval'] == interval]
                minutes = 1 if interval == '1m' else 5
                rows = [r for r in rows if (daily_completed(timestamp(r['time']).date(),self.as_of,self.calendar,'NSE' if symbol.endswith('.NS') else 'BSE') if interval == '1d' else timestamp(r['time'])+dt.timedelta(minutes=minutes) <= self.as_of)]
                bars = [Bar(timestamp(r['time']), *(float(r[k]) for k in ('open', 'high', 'low', 'close', 'volume'))) for r in rows]
        else:
            if self.as_of > dt.datetime.now(IST):
                raise ValueError('Cannot fetch future prices')
            if self._last_request is not None:
                time.sleep(max(0, .3-(time.monotonic()-self._last_request)))
            self._last_request = time.monotonic()
            bars = self._online_snapshot(symbol, interval)
        # Filter future observations before numeric validation for causal replays.
        cutoff = dt.timedelta(minutes=1 if interval == '1m' else 5)
        bars = [b for b in bars if (daily_completed(b.time.date(),self.as_of,self.calendar,'NSE' if symbol.endswith('.NS') else 'BSE') if interval == '1d' else b.time+cutoff <= self.as_of)]
        self._cache[key] = validate(bars)
        self.sources[key] = self.source
        return self._cache[key]

    def prefetch(self, symbols, interval='5m', workers=4, current_only=False):
        """Bounded shared fetching, one immutable snapshot per symbol.

        Only Yahoo chart reads run concurrently. NSE and entitled broker sources
        retain serial access and their existing directory/session cache. No
        candles from different requests/providers are joined.
        """
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
            raise ValueError('workers must be 1..8')
        names = list(dict.fromkeys(symbols))
        pending = [s for s in names if (s, interval) not in self._cache and (s, interval) not in self._errors]
        started = time.monotonic()
        concurrent = self.provider == 'yahoo' and not self.csv_path
        # A fallback adapter may have its own session and access constraints;
        # do not silently parallelize those requests.
        def fetch(symbol):
            child = MarketData(self.as_of, self.provider, self.csv_path, self.days,
                               self.instruments, False, self.calendar)
            child.intraday_range = '1d' if current_only else self.intraday_range
            try:
                rows = child.history(symbol, interval)
                return symbol, rows, child.source, child.attempts, None
            except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
                return symbol, None, child.source, child.attempts, str(error)
        if concurrent:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fetched = list(pool.map(fetch, pending))
            for symbol, rows, source, attempts, error in fetched:
                self.attempts.update(attempts)
                if error:
                    self._errors[(symbol, interval)] = error
                else:
                    self._cache[(symbol, interval)] = rows
                    self.sources[(symbol, interval)] = source
            # Fallback access remains serial. Replace the entire failed/gapped
            # snapshot through the ordinary provider-selection boundary.
            if self.auto_fallback:
                for symbol, rows, _, _, error in fetched:
                    if symbol.endswith('.NS') and (error or not self._coverage(rows, symbol, interval)[0]):
                        key = symbol, interval
                        self._cache.pop(key, None)
                        self._errors.pop(key, None)
                        try:
                            if self._last_request is not None:
                                time.sleep(max(0, .3-(time.monotonic()-self._last_request)))
                            self._last_request = time.monotonic()
                            # Reuse the completed Yahoo attempt and try only
                            # the serial fallback, replacing the whole snapshot.
                            recovered = self._online_snapshot(symbol, interval,
                                primary=(rows, self.attempts.get(key, [])), current_only=current_only)
                            self._cache[key] = recovered
                            self.sources[key] = self.source
                        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
                            self._errors[key] = str(exc)
        else:
            previous_range = self.intraday_range
            if current_only:
                self.intraday_range = '1d'
            try:
                for symbol in pending:
                    try:
                        self.history(symbol, interval)
                    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
                        self._errors[(symbol, interval)] = str(error)
            finally:
                self.intraday_range = previous_range
        self.fetch_metrics = dict(requested=len(names), uncached=len(pending),
                                  workers=workers if concurrent else 1,
                                  elapsed_seconds=round(time.monotonic()-started, 4),
                                  method='bounded Yahoo chart reads' if concurrent else 'serial source/cache reads')
        return self.fetch_metrics

    def _coverage(self, bars, symbol, interval):
        if interval == '1d':
            return True, 0
        minutes = 1 if interval == '1m' else 5
        day = bars[-1].time.date()
        exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
        if not self.calendar.trading_day(day, exchange):
            return False, 100000
        opening = dt.datetime.combine(day, dt.time(9, 15), IST)
        end = min(self.as_of, opening.replace(hour=15, minute=15)) if day == self.as_of.date() else opening.replace(hour=15, minute=15)
        expected = {opening+dt.timedelta(minutes=minutes*i) for i in range(max(0, int((end-opening).total_seconds()//(minutes*60))))}
        received = {b.time for b in bars if b.time.date() == day}
        missing = len(expected-received)
        if day < self.as_of.date() and self.calendar.trading_day(self.as_of.date(), exchange) and self.as_of.time() >= dt.time(9, 15):
            return False, missing+10000
        return missing == 0, missing

    def _online_snapshot(self, symbol, interval, primary=None, current_only=False):
        candidates = [self.provider]
        if self.provider == 'yahoo' and symbol.endswith('.NS') and interval in ('5m', '1d') and self.auto_fallback:
            candidates.append('nse')
        attempts, best = [], None
        if primary is not None:
            rows, previous_attempts = primary
            attempts = list(previous_attempts)
            candidates = [name for name in candidates if name != self.provider]
            if rows:
                usable, missing = self._coverage(rows, symbol, interval)
                best = ((usable, -missing, len(rows)), rows, self.provider)
        labels = {'yahoo': 'Yahoo unofficial; may be delayed', 'nse': 'NSE experimental website adapter; provider terms apply', 'upstox': 'Upstox V3'}
        for name in candidates:
            try:
                if name == 'yahoo':
                    self._retrieval_attempts = []
                    rows = self._yahoo(symbol, interval)
                elif name == 'nse':
                    source_days = 1 if interval == '5m' and (current_only or self.intraday_range == '1d') else min(self.days, 120)
                    if self._nse is None or self._nse.days != source_days:
                        self._nse = NSEProvider(self.as_of, source_days)
                    rows = self._nse.history(symbol, interval)
                else:
                    if self._upstox is None:
                        self._upstox = DirectoryUpstox(self.as_of, min(self.days, 28), self.instruments)
                    rows = self._upstox.history(symbol, interval)
                step = dt.timedelta(minutes=1 if interval == '1m' else 5)
                rows = validate([b for b in rows if (daily_completed(b.time.date(),self.as_of,self.calendar,'NSE' if symbol.endswith('.NS') else 'BSE') if interval == '1d' else b.time+step <= self.as_of)])
                usable, missing = self._coverage(rows, symbol, interval)
                attempts.append(dict(provider=name, usable=usable, missing_strategy_bars=missing,
                                     retrievals=list(self._retrieval_attempts) if name == 'yahoo' else []))
                score = usable, -missing, len(rows)
                if best is None or score > best[0]:
                    best = score, rows, name
                if usable:
                    break
            except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
                attempts.append(dict(provider=name, usable=False, error=str(exc),
                                     retrievals=list(self._retrieval_attempts) if name == 'yahoo' else []))
        self.attempts[(symbol, interval)] = attempts
        if best is None:
            raise ValueError('; '.join(item['provider']+': '+item.get('error', 'incomplete') for item in attempts))
        _, rows, chosen = best
        self.source = labels[chosen]
        return rows

    def _yahoo(self, symbol, interval):
        rng = '3mo' if interval == '1d' else ('7d' if interval == '1m' else self.intraday_range or '60d')
        requests = [(interval, rng)]
        if interval == '5m' and rng == '1d':
            requests.append(('5m', '5d'))
            requests.append(('1m', '1d'))
        best = None
        for request_interval, request_range in requests:
            path = '/v8/finance/chart/'+urllib.parse.quote(symbol)+'?'+urllib.parse.urlencode(dict(range=request_range, interval=request_interval))
            for host in ('query2', 'query1'):
                for retry in range(2):
                    try:
                        req = urllib.request.Request('https://'+host+'.finance.yahoo.com'+path, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req, timeout=20) as response:
                            rows = parse_yahoo(json.load(response), symbol, request_interval, self.as_of, self.calendar)
                        if request_interval == '1m':
                            rows = aggregate_minutes(rows)
                        usable, missing = self._coverage(rows, symbol, interval)
                        self._retrieval_attempts.append(dict(host=host, interval=request_interval, range=request_range,
                                                             usable=usable, missing_strategy_bars=missing,
                                                             observed_bars=len(rows)))
                        score = usable, -missing, len(rows)
                        if best is None or score > best[0]:
                            best = score, rows
                        if usable:
                            return rows
                        break
                    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
                        self._retrieval_attempts.append(dict(host=host, interval=request_interval,
                                                             range=request_range, error=str(exc)))
                        if isinstance(exc, urllib.error.HTTPError):
                            code = exc.code
                            if exc.fp is not None:
                                exc.close()
                            if code == 429 and retry == 0:
                                time.sleep(1)
                                continue
                        break
        if best is not None:
            return best[1]
        raise ValueError('Yahoo unavailable/incomplete: '+'; '.join(r.get('error', 'missing bars') for r in self._retrieval_attempts)+'; use an authorized CSV/API source')


def aggregate_minutes(rows):
    """Use only five observed consecutive minute OHLCV bars per 5m candle."""
    groups = {}
    for bar in rows:
        minute = bar.time.minute - bar.time.minute % 5
        start = bar.time.replace(minute=minute)
        groups.setdefault(start, []).append(bar)
    complete = []
    for start, group in sorted(groups.items()):
        if [bar.time for bar in group] != [start+dt.timedelta(minutes=i) for i in range(5)]:
            continue
        complete.append(Bar(start, group[0].open, max(b.high for b in group),
                            min(b.low for b in group), group[-1].close,
                            sum(b.volume for b in group)))
    return validate(complete)


class DirectoryUpstox(UpstoxProvider):
    """Reuse candle requests while downloading a directory once per exchange/scan."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._directories = {}

    def resolve(self, symbol):
        symbol_name(symbol)
        exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
        if exchange not in self._directories:
            if self.instruments:
                raw = Path(self.instruments).read_bytes()
                rows = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
            else:
                from india.upstox import get_json
                rows = get_json('https://assets.upstox.com/market-quote/instruments/exchange/'+exchange+'.json.gz', compressed=True)
            if not isinstance(rows, list):
                raise ValueError('Instrument directory must be an array')
            lookup = {}
            for row in rows:
                if row.get('segment') == exchange+'_EQ' and row.get('instrument_type') == 'EQ':
                    names = [row.get('trading_symbol')]
                    if exchange == 'BSE':
                        names.append(str(row.get('exchange_token', '')))
                    for name in set(names)-{None, ''}:
                        lookup.setdefault(name, []).append(row)
            self._directories[exchange] = lookup
        matches = self._directories[exchange].get(symbol.rsplit('.', 1)[0], [])
        if len(matches) != 1:
            raise ValueError('Symbol must resolve to exactly one Indian cash equity')
        key = matches[0].get('instrument_key', '')
        if not key.startswith(exchange+'_EQ|') or not key.split('|', 1)[1]:
            raise ValueError('Invalid equity instrument key')
        self.instrument_key = key
        return urllib.parse.quote(key, safe='')


def parse_yahoo(doc, symbol, interval, as_of, calendar=None):
    r = doc['chart']['result'][0]
    meta = r['meta']
    if meta.get('symbol', '').upper() != symbol or meta.get('currency') != 'INR':
        raise ValueError('Yahoo symbol/currency mismatch')
    if meta.get('dataGranularity', interval) != interval:
        raise ValueError('Yahoo interval mismatch')
    quotes = r['indicators']['quote'][0]
    step = 1 if interval == '1m' else 5
    bars = []
    for i, epoch in enumerate(r.get('timestamp', [])):
        if epoch is None:
            continue
        when = dt.datetime.fromtimestamp(epoch, IST)
        if interval != '1d' and (not dt.time(9, 15) <= when.time() < dt.time(15, 30) or when.second or when.minute % step or when+dt.timedelta(minutes=step) > as_of):
            continue
        if interval == '1d' and not daily_completed(when.date(),as_of,calendar,'NSE' if symbol.endswith('.NS') else 'BSE'):
            continue
        values = [quotes.get(k, [])[i] if i < len(quotes.get(k) or []) else None for k in ('open', 'high', 'low', 'close', 'volume')]
        if all(v is None for v in values):
            continue  # a gap remains visible to the strategy, never forward fill
        if any(v is None or isinstance(v, bool) for v in values):
            raise ValueError('Missing Yahoo OHLCV at '+when.isoformat())
        bars.append(Bar(when, *map(float, values)))
    return validate(bars)
