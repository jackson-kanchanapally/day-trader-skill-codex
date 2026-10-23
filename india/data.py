"""Provider boundary. Timestamps represent bar OPEN, always timezone-aware."""
import csv
import datetime as dt
import json
import math
import re
import urllib.request
import urllib.error
import time
from dataclasses import dataclass
from typing import Protocol

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

@dataclass(frozen=True)
class Bar:
    time: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

class Provider(Protocol):
    def history(self, symbol, interval): ...

def timestamp(s):
    t = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timestamps require an explicit timezone')
    return t.astimezone(IST)

def symbol_name(symbol):
    if not re.fullmatch(r'[A-Z0-9&.-]+\.(NS|BO)', symbol):
        raise ValueError('Use a provider symbol such as RELIANCE.NS or 500325.BO')
    return symbol

def validate(bars):
    if not bars:
        raise ValueError('No OHLCV data available')
    previous = None
    for b in bars:
        vals = [b.open, b.high, b.low, b.close, b.volume]
        if not all(math.isfinite(v) for v in vals) or min(vals[:4]) <= 0 or b.volume < 0:
            raise ValueError('Invalid or missing OHLCV values')
        if b.low > min(b.open, b.close) or b.high < max(b.open, b.close) or b.low > b.high:
            raise ValueError('Invalid OHLC range')
        if b.time.tzinfo is None or (previous is not None and b.time <= previous):
            raise ValueError('Bars must have unique increasing aware timestamps')
        previous = b.time
    return bars

class CSVProvider:
    def __init__(self, path):
        self.path = path
    def history(self, symbol, interval):
        symbol_name(symbol)
        with open(self.path, newline='') as f:
            rows = list(csv.DictReader(f))
        if any(r['symbol'] != symbol or r['interval'] != interval for r in rows):
            raise ValueError('CSV symbol/interval does not match request')
        return validate([Bar(timestamp(r['time']), *(float(r[k]) for k in ('open','high','low','close','volume'))) for r in rows])

class YahooProvider:
    """Public, unofficial historical feed; never described as live or guaranteed."""
    def history(self, symbol, interval):
        symbol_name(symbol)
        from urllib.parse import quote
        path = '/v8/finance/chart/%s?range=%s&interval=%s' % (quote(symbol), '1mo' if interval == '1d' else '5d', interval)
        last_error = 'unavailable'
        for host in ('query2', 'query1'):
            for attempt in range(2):
                req = urllib.request.Request('https://%s.finance.yahoo.com%s' % (host,path), headers={'User-Agent':'Mozilla/5.0'})
                try:
                    with urllib.request.urlopen(req, timeout=20) as response:
                        doc = json.load(response)
                    return self.parse(doc,symbol)
                except urllib.error.HTTPError as exc:
                    code = exc.code
                    exc.close()
                    last_error = 'HTTP %d' % code
                    if code not in (429,500,502,503,504):
                        raise ValueError('Yahoo request failed: '+last_error) from None
                except (urllib.error.URLError, TimeoutError, OSError):
                    last_error = 'network unavailable'
                except (ValueError,KeyError,TypeError,IndexError) as exc:
                    # An alternate Yahoo host may have a complete response.
                    last_error = str(exc)
                    break
                if attempt == 0:
                    time.sleep(1)
        raise ValueError('Yahoo unavailable or incomplete: %s. Supply --csv or retry later; no prices were fabricated.' % last_error)

    @staticmethod
    def parse(doc,symbol):
        results = doc.get('chart', {}).get('result')
        if not results:
            raise ValueError('Provider returned no chart')
        r = results[0]
        if r['meta'].get('symbol', '').upper() != symbol or r['meta'].get('currency') != 'INR':
            raise ValueError('Provider symbol/currency mismatch')
        q = r['indicators']['quote'][0]
        bars = []
        times = list(r.get('timestamp', []))
        # Yahoo may append an unpopulated current bar. Exclude only trailing
        # empty rows; interior gaps still fail and freshness is checked later.
        while times and any(q[k][len(times)-1] is None for k in ('open','high','low','close','volume')):
            times.pop()
        for i, t in enumerate(times):
            values = [q[k][i] for k in ('open','high','low','close','volume')]
            if any(v is None for v in values):
                raise ValueError('Provider contains missing OHLCV; use a complete CSV export')
            bars.append(Bar(dt.datetime.fromtimestamp(t, IST), *map(float, values)))
        return validate(bars)
