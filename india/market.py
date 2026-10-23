"""Regular cash-equity sessions and configurable intraday cost estimates."""
import datetime as dt
import json
import math
from .data import IST

# NSE equity holiday list, verified 2026-10-23. Special sessions excluded.
HOLIDAYS = set('2026-01-15 2026-01-26 2026-03-03 2026-03-26 2026-03-31 2026-04-03 2026-04-14 2026-05-01 2026-05-28 2026-06-26 2026-09-14 2026-10-02 2026-10-20 2026-11-10 2026-11-24 2026-12-25'.split())

class Calendar:
    """Explicit, sourced holiday coverage. Unconfigured years fail closed."""
    def __init__(self, document):
        self.document = json.loads(json.dumps(document))
        if not isinstance(self.document, dict) or not self.document:
            raise ValueError('Calendar must map NSE/BSE to year records')
        for exchange, years in self.document.items():
            if exchange not in ('NSE','BSE') or not isinstance(years, dict) or not years:
                raise ValueError('Invalid calendar exchange/year mapping')
            for year, record in years.items():
                if not str(year).isdigit() or not isinstance(record, dict):
                    raise ValueError('Invalid calendar year record')
                if set(record)-{'source','verified_on','holidays'}:
                    raise ValueError('Unknown calendar fields; special sessions require explicit engine support')
                if not isinstance(record.get('source'),str) or not record.get('source') or not record.get('verified_on') or not isinstance(record.get('holidays'), list):
                    raise ValueError('Calendar requires source, verified_on and holidays')
                dt.date.fromisoformat(record['verified_on'])
                for value in record['holidays']:
                    if dt.date.fromisoformat(value).year != int(year):
                        raise ValueError('Calendar holiday belongs to another year')

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def trading_day(self, day, exchange='NSE'):
        record = self.document.get(exchange, {}).get(str(day.year))
        if record is None:
            raise ValueError('Calendar has no verified %s coverage for %s; supply --calendar' % (exchange,day.year))
        return day.weekday() < 5 and day.isoformat() not in record['holidays']


DEFAULT_CALENDAR = Calendar({exchange:{'2026':{
    'holidays':sorted(HOLIDAYS), 'verified_on':'2026-10-23',
    'source':'https://www.nseindia.com/resources/exchange-communication-holidays',
}} for exchange in ('NSE','BSE')})

def trading_day(day, calendar=None, exchange='NSE'):
    return (calendar or DEFAULT_CALENDAR).trading_day(day,exchange)

def session(t, calendar=None, exchange='NSE'):
    t = t.astimezone(IST)
    return trading_day(t.date(),calendar,exchange) and dt.time(9,15) <= t.time() < dt.time(15,30)

def costs(buy, sell, qty, exchange='NSE', config=None):
    if exchange not in ('NSE','BSE'):
        raise ValueError('Costs require NSE or BSE exchange')
    if (not isinstance(qty,int) or isinstance(qty,bool) or qty < 0
            or not all(isinstance(x,(int,float)) and math.isfinite(x) and x > 0 for x in (buy,sell))):
        raise ValueError('Costs require positive finite prices and nonnegative whole-share quantity')
    c = {'brokerage_rate': .0003, 'brokerage_cap': 20., 'stt_sell': .00025,
         'stamp_buy': .00003, 'sebi': .000001, 'gst': .18,
         'exchange_rate': .0000307 if exchange == 'NSE' else .0000375,
         'slippage_bps': 5.}
    if set(config or {})-set(c):
        raise ValueError('Unknown cost configuration keys: '+', '.join(sorted(set(config)-set(c))))
    c.update(config or {})
    if any(not isinstance(v, (int,float)) or not __import__('math').isfinite(v) or v < 0 for v in c.values()):
        raise ValueError('Cost rates must be finite and nonnegative')
    b, s = buy * qty, sell * qty
    broker = min(c['brokerage_cap'], b*c['brokerage_rate']) + min(c['brokerage_cap'], s*c['brokerage_rate'])
    exchange_fee, sebi = (b+s)*c['exchange_rate'], (b+s)*c['sebi']
    parts = {'brokerage': broker, 'exchange': exchange_fee, 'sebi': sebi,
             'stt': s*c['stt_sell'], 'stamp': b*c['stamp_buy'],
             'gst': (broker+exchange_fee+sebi)*c['gst'], 'slippage': (b+s)*c['slippage_bps']/10000}
    return dict(parts, total=sum(parts.values()))
