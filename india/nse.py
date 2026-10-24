"""Read-only adapter for the public NSE charting website (not a licensed feed).

The chart frontend sends IST wall-clock seconds encoded as UTC: actual epoch
seconds + 19800. Its responses use the same convention in milliseconds. Intraday
timestamps are the last update inside a candle, not the candle's opening time.
Verified against charting_assets/static/js/main.a0b377c2.js on 2026-10-23 and
captured TCS candles. No dependency on that frontend file or third-party scraper.
"""
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .data import Bar, IST, symbol_name, validate

BASE = 'https://charting.nseindia.com'
OFFSET = 19800


class NSEProvider:
    def __init__(self, as_of, days=7):
        if as_of.tzinfo is None:
            raise ValueError('NSE as-of timestamp requires a timezone')
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 120:
            raise ValueError('--days must be between 1 and 120')
        self.as_of = as_of.astimezone(IST)
        self.days = days
        self._symbols = {}

    def _get(self, path, params):
        url = BASE + path + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            'User-Agent': 'day-trader-skill/1.0',
            'Accept': 'application/json', 'Referer': BASE + '/',
        })
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    doc = json.load(response)
                if not isinstance(doc, dict) or doc.get('status') is not True:
                    raise ValueError('NSE chart returned an unsuccessful response')
                return doc
            except urllib.error.HTTPError as exc:
                code = exc.code
                # Python 3.9 leaves the response wrapper uninitialized without fp.
                if exc.fp is not None:
                    exc.close()
                if code not in (429, 500, 502, 503, 504) or attempt:
                    raise ValueError('NSE chart request failed: HTTP %d' % code) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt:
                    raise ValueError('NSE chart network unavailable') from None
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError('NSE chart returned non-JSON data; website access may be unavailable') from None
            time.sleep(1)

    def history(self, symbol, interval):
        symbol_name(symbol)
        if not symbol.endswith('.NS'):
            raise ValueError('NSE provider supports .NS equities only; choose another provider for BSE')
        if interval not in ('5m', '1d'):
            raise ValueError('NSE provider supports 5m and 1d only')
        name = symbol[:-3]
        if name not in self._symbols:
            doc = self._get('/v1/exchanges/symbolsDynamic', {'symbol': name, 'segment': 'EQ'})
            rows = doc.get('data')
            if not isinstance(rows, list):
                raise ValueError('Unexpected NSE symbol response')
            matches = [r for r in rows if isinstance(r, dict)
                       and r.get('symbol') in (name, name + '-EQ')
                       and r.get('exchange') == 'NSE' and r.get('type') == 'Equity']
            if len(matches) != 1 or not str(matches[0].get('scripcode', '')).isdigit():
                raise ValueError('NSE could not uniquely resolve equity %s' % symbol)
            self._symbols[name] = matches[0]
        info = self._symbols[name]
        self.provider_symbol = info['symbol']
        start = (self.as_of-dt.timedelta(days=self.days-1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # Keep each website request within the existing 28-day window. Request
        # disjoint date ranges from the same source; never fill missing slots.
        bars = []
        cursor = start
        while cursor <= self.as_of:
            end = min(self.as_of, cursor + dt.timedelta(days=28) - dt.timedelta(seconds=1))
            doc = self._get('/v1/charts/symbolHistoricalData', {
                'token': str(info['scripcode']), 'fromDate': int(cursor.timestamp()) + OFFSET,
                'toDate': int(end.timestamp()) + OFFSET,
                'symbol': info['symbol'], 'symbolType': 'Equity',
                'chartType': 'I' if interval == '5m' else 'D',
                'timeInterval': 5 if interval == '5m' else 1,
            })
            rows = self.parse(doc, interval, end)
            bars.extend(b for b in rows if cursor <= b.time <= end)
            cursor = end + dt.timedelta(seconds=1)
        return validate(bars)

    @staticmethod
    def parse(doc, interval, as_of):
        if not isinstance(doc, dict) or doc.get('status') is not True or not isinstance(doc.get('data'), list):
            raise ValueError('Unexpected NSE candle response')
        bars = []
        for row in doc['data']:
            if not isinstance(row, dict):
                raise ValueError('Unexpected NSE candle row')
            fields = ('time', 'open', 'high', 'low', 'close', 'volume')
            if any(row.get(k) is None or isinstance(row[k], bool) for k in fields):
                raise ValueError('NSE candle contains missing OHLCV')
            try:
                # Decode wall time; merely converting the epoch to IST would
                # move every candle 5.5 hours into the future.
                sample = dt.datetime.fromtimestamp(float(row['time']) / 1000, dt.timezone.utc).replace(tzinfo=IST)
                values = [float(row[k]) for k in fields[1:]]
            except (ValueError, TypeError, OverflowError, OSError):
                raise ValueError('Invalid NSE candle values or timestamp') from None
            if sample > as_of:
                continue  # never include later updates in a historical replay
            if interval == '5m':
                if not dt.time(9,15) <= sample.time() < dt.time(15,30):
                    continue
                when = sample.replace(minute=sample.minute//5*5, second=0, microsecond=0)
            else:
                when = sample.replace(hour=9, minute=15, second=0, microsecond=0)
            bars.append(Bar(when, *values))
        # Sorting is safe; duplicate slots are rejected rather than silently
        # picking one revision or mixing quotes into OHLCV bars.
        return validate(sorted(bars, key=lambda b: b.time))
