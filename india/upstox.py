"""Read-only Upstox V3 candles. Credentials are read only from the environment."""
import datetime as dt
import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from .data import Bar, IST, symbol_name, timestamp, validate


def get_json(url, token=None, compressed=False):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read()
            return json.loads(gzip.decompress(data) if compressed else data)
        except urllib.error.HTTPError as exc:
            code=exc.code
            if exc.fp is not None:
                exc.close()
            if code in (401, 403):
                raise ValueError('Upstox authentication/access denied; renew your Upstox token and check API access') from None
            if code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Upstox request failed (HTTP %d); no fallback data used' % code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise ValueError('Upstox network unavailable after 3 attempts') from None
        time.sleep(2 ** attempt)


def parse_candles(doc):
    if not isinstance(doc, dict) or doc.get('status') != 'success':
        raise ValueError('Upstox returned an unsuccessful response')
    rows = doc.get('data', {}).get('candles')
    if not isinstance(rows, list):
        raise ValueError('Upstox candle payload is malformed')
    bars = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6 or any(v is None for v in row[:6]):
            raise ValueError('Upstox contains missing OHLCV values')
        bars.append(Bar(timestamp(row[0]), *map(float, row[1:6])))
    # API examples are newest first. Duplicate bars remain errors.
    return validate(sorted(bars, key=lambda b: b.time)) if bars else []


class UpstoxProvider:
    def __init__(self, as_of=None, days=7, instruments=None):
        self.token = (os.environ.get('UPSTOX_ANALYTICS_TOKEN', '').strip()
                      or os.environ.get('UPSTOX_ACCESS_TOKEN', '').strip())
        if not self.token:
            raise ValueError('Set free read-only UPSTOX_ANALYTICS_TOKEN (or UPSTOX_ACCESS_TOKEN) in your local environment, or supply --csv. Never paste tokens into chat.')
        if not 1 <= days <= 28:
            raise ValueError('--days must be between 1 and 28')
        self.now = dt.datetime.now(IST)
        self.as_of = as_of or self.now
        if self.as_of > self.now:
            raise ValueError('Cannot fetch future market data')
        self.days, self.instruments = days, instruments
        self.instrument_key = None

    def resolve(self, symbol):
        symbol_name(symbol)
        exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
        if self.instruments:
            with open(self.instruments, 'rb') as f:
                raw = f.read()
            rows = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
        else:
            rows = get_json('https://assets.upstox.com/market-quote/instruments/exchange/%s.json.gz' % exchange, compressed=True)
        if not isinstance(rows, list):
            raise ValueError('Instrument master must be a JSON array')
        name = symbol.rsplit('.', 1)[0]
        matches = [r for r in rows if r.get('segment') == exchange+'_EQ' and r.get('instrument_type') == 'EQ'
                   and (r.get('trading_symbol') == name or (exchange == 'BSE' and str(r.get('exchange_token')) == name))]
        if len(matches) != 1:
            raise ValueError('Symbol must match exactly one equity in the Upstox instrument master')
        key = matches[0].get('instrument_key', '')
        if not key.startswith(exchange+'_EQ|') or not key.split('|',1)[1]:
            raise ValueError('Invalid equity instrument key')
        self.instrument_key = key
        return urllib.parse.quote(key, safe='')

    def history(self, symbol, interval):
        if interval not in ('5m', '1d'):
            raise ValueError('Supported intervals: 5m, 1d')
        key = self.resolve(symbol)
        unit, count = ('minutes', 5) if interval == '5m' else ('days', 1)
        end = self.as_of.astimezone(IST).date()
        start = end - dt.timedelta(days=self.days-1)
        hist_end = min(end, self.now.date()-dt.timedelta(days=1))
        bars = []
        if start <= hist_end:
            url = 'https://api.upstox.com/v3/historical-candle/%s/%s/%d/%s/%s' % (key,unit,count,hist_end,start)
            bars.extend(parse_candles(get_json(url,self.token)))
        if end == self.now.date():
            url = 'https://api.upstox.com/v3/historical-candle/intraday/%s/%s/%d' % (key,unit,count)
            bars.extend(parse_candles(get_json(url,self.token)))
        bars = sorted(bars,key=lambda b:b.time)
        if any(not start <= b.time.date() <= end for b in bars):
            raise ValueError('Upstox returned candles outside the requested date window')
        return validate(bars)
