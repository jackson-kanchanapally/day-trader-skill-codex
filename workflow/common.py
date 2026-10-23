"""Shared file, clock, configuration and preserved-upstream boundaries."""
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile

from india.data import IST, symbol_name
from india.market import DEFAULT_CALENDAR

ROOT = Path(__file__).resolve().parents[1]
FILES = dict(scanner='scan', entry='entry', exit='exit', timemachine='timemachine',
             budget='size', risk='check', broker='ticket', alerts='alerts',
             news='news', analyst='facts', pelosi='ptr')
DEFAULTS = dict(cash_buffer_pct=10, max_position_pct=75, max_order_inr=100000,
                min_order_inr=1, max_loss_per_trade_inr=500, max_daily_loss_inr=1500,
                max_trades_per_day=3, max_orders_per_day=10, min_reward_risk=1,
                no_entries_before='09:30', no_entries_after='15:00', flat_by='15:10',
                paper=True, fractional=False, blocked=[], broker='local-paper',
                allow_options=False, allow_margin=False, allow_short=False)


def read(path, default=None):
    path = Path(path)
    if not path.exists() and default is not None:
        return json.loads(json.dumps(default))
    return json.loads(path.read_text(encoding='utf-8'),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON: '+x)))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
            name = f.name
            json.dump(value, f, indent=2, allow_nan=False)
            f.write('\n')
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def number(value, name='value', zero=False):
    if isinstance(value, bool):
        raise ValueError(name+' must be a finite number')
    x = float(value)
    if not math.isfinite(x) or (x < 0 if zero else x <= 0):
        raise ValueError(name+' must be '+('nonnegative' if zero else 'positive'))
    return x


def clock(value=None):
    if not value:
        return dt.datetime.now(IST).replace(microsecond=0)
    result = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return result.replace(tzinfo=IST) if result.tzinfo is None else result.astimezone(IST)


def utc(value):
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def envelope(role, now, **fields):
    return dict(created_by='trader-'+role, created_at=now.isoformat(), currency='INR',
                timezone='Asia/Kolkata', paper=True, execution_enabled=False, **fields)


def config(path=None, **overrides):
    c = dict(DEFAULTS)
    if path:
        c.update(read(path))
    c.update({k: v for k, v in overrides.items() if v is not None})
    if c.get('paper') is not True or c.get('broker') != 'local-paper':
        raise ValueError('Only local-paper is supported; live/broker order execution is disabled')
    for key in ('fractional', 'allow_options', 'allow_margin', 'allow_short', 'pdt_guard'):
        if c.get(key):
            raise ValueError(key+' is unsupported for cash-equity paper workflows')
    if any(k.endswith('_usd') for k in c):
        raise ValueError('Use *_inr configuration keys; USD settings are not silently converted')
    for key in ('budget', 'max_order_inr', 'max_loss_per_trade_inr', 'max_daily_loss_inr', 'min_reward_risk'):
        if key in c:
            number(c[key], key)
    number(c['min_order_inr'], 'min_order_inr', zero=True)
    if not 0 <= float(c['cash_buffer_pct']) < 100 or not 0 < float(c['max_position_pct']) <= 100:
        raise ValueError('Invalid cash buffer or position percentage')
    for key in ('max_trades_per_day', 'max_orders_per_day'):
        if isinstance(c[key], bool) or int(c[key]) != c[key] or c[key] < 1:
            raise ValueError(key+' must be a positive integer')
    times = [dt.time.fromisoformat(c[k]) for k in ('no_entries_before', 'no_entries_after', 'flat_by')]
    if not dt.time(9, 15) <= times[0] < times[1] < times[2] < dt.time(15, 15):
        raise ValueError('Require 09:15 <= entry start < entry cutoff < flat-by < 15:15 IST')
    return c


def upstream(role, calendar=None, exchange='NSE'):
    """Load unchanged MIT tools and explicitly replace their market boundary.

    Each call owns its module globals; adapters never mutate the archived files
    or another caller's calendar. No original network/order CLI is invoked.
    """
    path = ROOT/'legacy'/('trader-'+role)/(FILES[role]+'.py')
    spec = importlib.util.spec_from_file_location('indian_'+role, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for name, value in list(vars(m).items()):
        if name.endswith('Error') and isinstance(value, type) and value.__module__ == m.__name__ and issubclass(value, Exception):
            setattr(m, name, type(name, (ValueError,), {'__module__': m.__name__}))
    cal = calendar or DEFAULT_CALENDAR
    m.to_eastern = lambda x: x.replace(tzinfo=dt.timezone.utc).astimezone(IST).replace(tzinfo=None) if x.tzinfo is None else x.astimezone(IST).replace(tzinfo=None)
    m.eastern_to_utc = lambda x: x.replace(tzinfo=IST).astimezone(dt.timezone.utc).replace(tzinfo=None)
    m.OPEN, m.CLOSE = dt.time(9, 15), dt.time(15, 15)
    m.NYSE_EARLY_CLOSE = {}
    m.CALENDAR_YEARS = tuple(int(x) for x in cal.document.get(exchange, {}))
    m.trading_day = lambda day: (cal.trading_day(day, exchange), 'exchange holiday or weekend')
    m.session = lambda day: (m.OPEN, m.CLOSE) if cal.trading_day(day, exchange) else None
    if hasattr(m, 'DEFAULTS'):
        m.DEFAULTS.update({k: v for k, v in DEFAULTS.items() if k in m.DEFAULTS})
    return m


def indian_words(value):
    if isinstance(value, dict):
        return {k.replace('_usd', '_inr').replace('now_new_york', 'now_ist'): indian_words(v) for k, v in value.items()}
    if isinstance(value, list):
        return [indian_words(v) for v in value]
    if isinstance(value, str):
        return value.replace('New York', 'IST').replace('NYSE', 'exchange').replace('$', '₹').replace('US stock or ETF', 'NSE/BSE equity').replace('place_stock_order', 'local paper ticket').replace('opens at 9:30', 'opens at 09:15')
    return value


def exchange_of(symbol):
    symbol_name(symbol)
    return 'NSE' if symbol.endswith('.NS') else 'BSE'
