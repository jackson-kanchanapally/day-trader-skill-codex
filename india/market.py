"""Regular cash-equity sessions and configurable intraday cost estimates."""
import datetime as dt
import json
import math
import warnings
from .data import IST

# NSE equity holiday list, verified 2026-10-23. Special sessions excluded.
HOLIDAYS = set('2026-01-15 2026-01-26 2026-03-03 2026-03-26 2026-03-31 2026-04-03 2026-04-14 2026-05-01 2026-05-28 2026-06-26 2026-09-14 2026-10-02 2026-10-20 2026-11-10 2026-11-24 2026-12-25'.split())

class CalendarCoverageWarning(UserWarning):
    """Calendar is approaching or beyond the supplied coverage boundary."""


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
                if set(record)-{'source','verified_on','holidays','sessions','valid_until'}:
                    raise ValueError('Unknown calendar fields')
                if not isinstance(record.get('source'),str) or not record.get('source') or not record.get('verified_on') or not isinstance(record.get('holidays'), list):
                    raise ValueError('Calendar requires source, verified_on and holidays')
                dt.date.fromisoformat(record['verified_on'])
                for value in record['holidays']:
                    if dt.date.fromisoformat(value).year != int(year):
                        raise ValueError('Calendar holiday belongs to another year')
                until = dt.date.fromisoformat(record.get('valid_until', year+'-12-31'))
                if until.year != int(year):
                    raise ValueError('Calendar coverage boundary belongs to another year')
                if not isinstance(record.get('sessions', {}), dict):
                    raise ValueError('Calendar sessions must map dates to sourced windows')
                for date, window in record.get('sessions', {}).items():
                    if dt.date.fromisoformat(date).year != int(year) or not isinstance(window, dict):
                        raise ValueError('Exceptional session belongs to another year')
                    if (set(window)-{'open', 'close', 'source', 'kind'} or not isinstance(window.get('source'), str)
                            or not window.get('source') or not {'open', 'close'} <= set(window)):
                        raise ValueError('Exceptional sessions require explicit source, open and close')
                    clocks = [dt.time.fromisoformat(window[k]) for k in ('open', 'close')]
                    if any(t.tzinfo is not None or t.second or t.microsecond for t in clocks):
                        raise ValueError('Session windows require minute-aligned local IST clocks')
                    if not clocks[0] < clocks[1]:
                        raise ValueError('Session open must precede close on the same IST date')


    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def coverage_warnings(self, day, exchange='NSE'):
        years = self.document.get(exchange, {})
        record = years.get(str(day.year))
        if record is None:
            return ['Calendar has no verified %s coverage for %s; supply --calendar' % (exchange, day.year)]
        until = dt.date.fromisoformat(record.get('valid_until', str(day.year)+'-12-31'))
        # Contiguous next-year coverage extends the warning horizon.
        while until.month == 12 and until.day == 31 and str(until.year+1) in years:
            next_year = str(until.year+1)
            until = dt.date.fromisoformat(years[next_year].get('valid_until', next_year+'-12-31'))
        left = (until-day).days
        if left < 0:
            return ['Calendar %s coverage expired on %s; supply --calendar' % (exchange, until)]
        if left <= 30:
            return ['Calendar %s coverage expires on %s; supply verified subsequent coverage' % (exchange, until)]
        return []

    def _record(self, day, exchange):
        messages = self.coverage_warnings(day, exchange)
        for message in messages:
            warnings.warn(message, CalendarCoverageWarning, stacklevel=3)
        record = self.document.get(exchange, {}).get(str(day.year))
        if record is None:
            raise ValueError(messages[0])
        until = dt.date.fromisoformat(record.get('valid_until', str(day.year)+'-12-31'))
        if day > until:
            raise ValueError('Calendar coverage expired; supply --calendar')
        return record

    def hours(self, day, exchange='NSE'):
        record = self._record(day, exchange)
        exceptional = record.get('sessions', {}).get(day.isoformat())
        if exceptional:
            return (dt.time.fromisoformat(exceptional['open']), dt.time.fromisoformat(exceptional['close']))
        if day.weekday() >= 5 or day.isoformat() in record['holidays']:
            return None
        return dt.time(9, 15), dt.time(15, 30)

    def trading_day(self, day, exchange='NSE'):
        return self.hours(day, exchange) is not None

    def require_strategy_session(self, day, exchange='NSE'):
        hours = self.hours(day, exchange)
        if hours and (hours[0] != dt.time(9, 15) or hours[1] < dt.time(15, 15)):
            raise ValueError('Exceptional session is recognized by the calendar; this ORB strategy requires 09:15-15:15 IST coverage')



DEFAULT_CALENDAR = Calendar({exchange:{'2026':{
    'holidays':sorted(HOLIDAYS), 'verified_on':'2026-10-23',
    'source':'https://www.nseindia.com/resources/exchange-communication-holidays',
}} for exchange in ('NSE','BSE')})

def trading_day(day, calendar=None, exchange='NSE'):
    return (calendar or DEFAULT_CALENDAR).trading_day(day,exchange)

def session(t, calendar=None, exchange='NSE'):
    t = t.astimezone(IST)
    hours = (calendar or DEFAULT_CALENDAR).hours(t.date(), exchange)
    return bool(hours and hours[0] <= t.time() < hours[1])

def costs(buy, sell, qty, exchange='NSE', config=None):
    if exchange not in ('NSE','BSE'):
        raise ValueError('Costs require NSE or BSE exchange')
    if (not isinstance(qty,int) or isinstance(qty,bool) or qty < 0
            or not all(isinstance(x,(int,float)) and math.isfinite(x) and x > 0 for x in (buy,sell))):
        raise ValueError('Costs require positive finite prices and nonnegative whole-share quantity')
    c = {'brokerage_rate': .0003, 'brokerage_cap': 20., 'stt_sell': .00025,
         'stamp_buy': .00003, 'sebi': .000001, 'gst': .18,
         'exchange_rate': .0000307 if exchange == 'NSE' else .0000375,
         'slippage_bps': 5., 'spread_bps': 0.}
    if set(config or {})-set(c):
        raise ValueError('Unknown cost configuration keys: '+', '.join(sorted(set(config)-set(c))))
    c.update(config or {})
    if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in c.values()):
        raise ValueError('Cost rates must be finite and nonnegative')
    b, s = buy * qty, sell * qty
    broker = min(c['brokerage_cap'], b*c['brokerage_rate']) + min(c['brokerage_cap'], s*c['brokerage_rate'])
    exchange_fee, sebi = (b+s)*c['exchange_rate'], (b+s)*c['sebi']
    parts = {'brokerage': broker, 'exchange': exchange_fee, 'sebi': sebi,
             'stt': s*c['stt_sell'], 'stamp': b*c['stamp_buy'],
             'gst': (broker+exchange_fee+sebi)*c['gst'], 'slippage': (b+s)*c['slippage_bps']/10000}
    # Full quoted spread: half is paid on each leg. Raw candle prices remain
    # unchanged; this is an explicit cash scenario, separate from slippage.
    if config is not None and 'spread_bps' in config:
        parts['spread'] = (b+s)*c['spread_bps']/20000
    return dict(parts, total=sum(parts.values()))
