"""Public instrument metadata and existing bounded candle adapters; no orders."""
import csv
import datetime as dt
import gzip
import hashlib
import io
from pathlib import Path
import urllib.request

from india.security import security_records
from workflow.common import write
from workflow.data import MarketData, universe

INSTRUMENTS = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz'


def normalize_ticks(rows, names, observed_at, source=INSTRUMENTS, rejected=None):
    eligible, found, blocked = set(names), {}, set()
    rejected = [] if rejected is None else rejected
    for row in rows:
        symbol = str(row.get('tradingsymbol', '')).strip()+'.NS'
        if symbol not in eligible or row.get('exchange') != 'NSE_EQ' or row.get('instrument_type') != 'EQUITY':
            continue
        if str(row.get('lot_size')) != '1' or not str(row.get('instrument_key','')).startswith('NSE_EQ|'):
            continue
        if symbol in blocked:
            continue
        if symbol in found:
            del found[symbol]
            blocked.add(symbol)
            rejected.append(dict(symbol=symbol,reason='AMBIGUOUS_TICK_METADATA'))
            continue
        # The documented legacy CSV expresses ticks in the same decimal price
        # units as last_price. JSON tick fields use a different representation;
        # this adapter never guesses a JSON-to-INR conversion.
        try:
            tick = float(row['tick_size'])
            if not 0 < tick <= 100:
                raise ValueError('Invalid instrument tick')
        except (KeyError,TypeError,ValueError):
            blocked.add(symbol)
            rejected.append(dict(symbol=symbol,reason='INVALID_TICK_METADATA'))
            continue
        found[symbol] = dict(symbol=symbol, tick_size=tick, currency='INR', tick_unit='INR',
                             effective_from=observed_at.date().isoformat(), effective_to=observed_at.date().isoformat(),
                             published_at=observed_at.isoformat(), accessed_at=observed_at.isoformat(),
                             publication_basis='public-source observation upper bound', source=source,
                             instrument_key=row['instrument_key'], normalization='documented decimal-price CSV; no JSON unit conversion')
    return security_records(list(found.values()), observed_at)


class OnlineSources:
    def __init__(self, workspace, settings, calendar, now):
        self.root, self.settings, self.calendar, self.now = Path(workspace), settings, calendar, now

    def market(self, cutoff):
        return MarketData(cutoff, self.settings['provider'], auto_fallback=self.settings['auto_fallback'], calendar=self.calendar)

    def universe(self):
        return universe('NSE')

    def metadata(self, names):
        request = urllib.request.Request(INSTRUMENTS, headers={'User-Agent':'IndianTradingResearch/1.0'})
        with urllib.request.urlopen(request, timeout=25) as response:
            if not response.url.startswith('https://assets.upstox.com/'):
                raise ValueError('Instrument directory redirected away from its source')
            raw = response.read(20_000_001)
        if len(raw) > 20_000_000:
            raise ValueError('Instrument download exceeds its size bound')
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
            data = zipped.read(100_000_001)
        if len(data) > 100_000_000:
            raise ValueError('Instrument directory exceeds its size bound')
        observed = self.now()
        rejected = []
        master = normalize_ticks(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))), names, observed, rejected=rejected)
        write(self.root/'automation'/'metadata.json', dict(securities=list(master.values()),
              rejected=rejected, requested=len(names), verified=len(master),
              missing=sorted(set(names)-set(master)), source=INSTRUMENTS,
              accessed_at=observed.isoformat(), sha256=hashlib.sha256(raw).hexdigest()))
        return master
