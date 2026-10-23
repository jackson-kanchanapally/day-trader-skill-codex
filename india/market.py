"""Regular cash-equity sessions and configurable intraday cost estimates."""
import datetime as dt
from .data import IST

# NSE equity holiday list, verified 2026-10-23. Special sessions excluded.
HOLIDAYS = set('2026-01-15 2026-01-26 2026-03-03 2026-03-26 2026-03-31 2026-04-03 2026-04-14 2026-05-01 2026-05-28 2026-06-26 2026-09-14 2026-10-02 2026-10-20 2026-11-10 2026-11-24 2026-12-25'.split())

def trading_day(day):
    if day.year != 2026:
        raise ValueError('Calendar supports 2026 only; update verified exchange holidays first')
    return day.weekday() < 5 and day.isoformat() not in HOLIDAYS

def session(t):
    t = t.astimezone(IST)
    return trading_day(t.date()) and dt.time(9,15) <= t.time() < dt.time(15,30)

def costs(buy, sell, qty, exchange='NSE', config=None):
    c = {'brokerage_rate': .0003, 'brokerage_cap': 20., 'stt_sell': .00025,
         'stamp_buy': .00003, 'sebi': .000001, 'gst': .18,
         'exchange_rate': .0000307 if exchange == 'NSE' else .0000375,
         'slippage_bps': 5.}
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
