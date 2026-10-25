"""Causal indicators, opening-range setups, cost-aware sizing and replay."""
import datetime as dt
import importlib.util
import math
from pathlib import Path
from .data import IST, validate
from .market import session, DEFAULT_CALENDAR
from .execution import tick_price, size_position, replay_trade
from .volume import volume_profile

# A strategy window, deliberately conservative for all equities. Since August
# 2026 eligible stocks enter NSE's closing auction at 15:15; auction updates
# must not be interpreted as continuous five-minute trading candles.
ANALYSIS_END = dt.time(15,15)

# Reuse the original tested Wilder RSI implementation.
_spec = importlib.util.spec_from_file_location('legacy_scan', Path(__file__).resolve().parents[1] / 'legacy/trader-scanner/scan.py')
_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scan)

def ema(values, period):
    value = values[0]
    for x in values[1:]:
        value += 2/(period+1)*(x-value)
    return value

def completed(bars, as_of, minutes, calendar=None, exchange='NSE'):
    validate(bars)
    bars = [b for b in bars if b.time + dt.timedelta(minutes=minutes) <= as_of and session(b.time,calendar,exchange)]
    if not bars:
        raise ValueError('No completed regular-session bars as of requested time')
    return bars

def session_vwap(bars):
    """HLC3/volume candle approximation for the latest session, not tick VWAP."""
    if not bars:
        return None
    today = [b for b in bars if b.time.date() == bars[-1].time.date()]
    volume = sum(b.volume for b in today)
    return sum((b.high+b.low+b.close)/3*b.volume for b in today)/volume if volume else None


def indicators(bars, *, minutes=5, lookback=20, min_samples=5, calendar=None, exchange='NSE'):
    if not bars:
        return dict.fromkeys(('rsi14','ema9','ema21','vwap','volume_spike','relative_volume'))
    closes = [b.close for b in bars]
    profile = volume_profile(bars, minutes=minutes, lookback=lookback, min_samples=min_samples,
                             calendar=calendar, exchange=exchange)
    return {'rsi14': _scan.rsi(closes) if len(closes) >= 15 else None,
            'ema9': ema(closes,9), 'ema21': ema(closes,21),
            'vwap': session_vwap(bars),
            'volume_spike': profile['same_time']['value'], 'relative_volume': profile}

def tick_round(x, tick, up=False):
    return tick_price(x, tick, up)

def proposal(bars, tick, rr):
    day = bars[-1].time.date()
    today = [b for b in bars if b.time.date() == day]
    opening = [b for b in today if b.time.time() < dt.time(9,30)]
    if len(opening) != 3 or [b.time.time() for b in opening] != [dt.time(9,15),dt.time(9,20),dt.time(9,25)]:
        return None
    high, low = max(b.high for b in opening), min(b.low for b in opening)
    last = today[-1]
    earlier = [b for b in today if dt.time(9,30) <= b.time.time() and b is not last]
    if last.volume <= 0 or last.time.time() < dt.time(9,30) or last.close <= high or any(b.close > high for b in earlier):
        return None
    entry, stop = tick_round(last.close,tick,True), tick_round(low,tick)
    if entry <= stop:
        return None
    return {'entry': entry, 'stop': stop, 'target': tick_round(entry+rr*(entry-stop),tick,True), 'signal_time': last.time.isoformat(), 'tick': tick, 'interval': '5m', 'flat_by': '15:10'}

def size(plan, capital, risk, exchange, config):
    return size_position(plan, capital, risk, exchange, config)

def report(bars, as_of, symbol, tick, capital, risk, rr=2., config=None, calendar=None):
    if not all(math.isfinite(x) and x > 0 for x in (tick,capital,risk,rr)):
        raise ValueError('tick, capital, risk and reward/risk must be positive finite numbers')
    exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
    (calendar or DEFAULT_CALENDAR).require_strategy_session(as_of.astimezone(IST).date(), exchange)
    bars = completed([b for b in bars if b.time.astimezone(IST).time() < ANALYSIS_END],as_of,5,calendar,exchange)
    today = [b for b in bars if b.time.date() == bars[-1].time.date()]
    if any(b.time.second or b.time.microsecond or b.time.minute % 5 for b in today):
        raise ValueError('Intraday candles must start on the five-minute grid')
    start = dt.datetime.combine(today[0].time.date(),dt.time(9,15),IST)
    end = min(as_of.astimezone(IST),start.replace(hour=ANALYSIS_END.hour,minute=ANALYSIS_END.minute))
    expected = [start+dt.timedelta(minutes=5*i)
                for i in range(max(0,int((end-start).total_seconds()//300)))]
    received = {b.time for b in today}
    missing = [t for t in expected if t not in received]
    # Indicators on a broken time series would hide the feed problem. Use only
    # the continuous prefix from the session open and show its exact cutoff.
    usable = [b for b in today if b.time < missing[0]] if missing else bars
    stale = not usable or (as_of-usable[-1].time-dt.timedelta(minutes=5)).total_seconds() > 600
    eligible = not missing and session(as_of,calendar,exchange) and not stale and as_of.astimezone(IST).time() < ANALYSIS_END
    # Leave a full executable bar before the shared 15:10 flatten boundary.
    eligible = eligible and as_of.astimezone(IST).time() <= dt.time(15, 5)
    plan = proposal(bars,tick,rr) if eligible else None
    sized = size(plan,capital,risk,'NSE' if symbol.endswith('.NS') else 'BSE',config) if plan else None
    if missing:
        status = 'BLOCKED_INCOMPLETE_DATA'
    elif sized and (sized['qty'] == 0 or sized['net_reward_risk'] < 1):
        status = 'BLOCKED_COST_OR_RISK'
    else:
        status = 'PAPER_SETUP' if sized else 'NO_SETUP'
    return {'symbol':symbol,'mode':'paper','execution_enabled':False,'as_of':as_of.isoformat(),
            'last_bar':bars[-1].time.isoformat(),'stale':stale,'status':status,
            'indicators':indicators(usable, calendar=calendar, exchange=exchange),'plan':sized,
            'indicators_as_of':(usable[-1].time+dt.timedelta(minutes=5)).isoformat() if usable else None,
            'data_quality':{'complete':not missing,'expected_bars':len(expected),
                            'received_bars':len(today),'usable_session_bars':sum(b.time.date()==today[0].time.date() for b in usable),
                            'missing_bars':[t.isoformat() for t in missing]},
            'calendar_warnings':(calendar or DEFAULT_CALENDAR).coverage_warnings(as_of.astimezone(IST).date(), exchange),
            'market_open':session(as_of,calendar,exchange),
            'analysis_window':{'start':'09:15','end':'15:15','timezone':'Asia/Kolkata'},
            'strategy_open':session(as_of,calendar,exchange) and as_of.astimezone(IST).time() < ANALYSIS_END}

def backtest(bars, symbol, tick, capital, risk, rr=2., config=None, calendar=None):
    """Fixed range-low ORB adapter over the shared conservative fill engine."""
    validate(bars)
    bars = [b for b in bars if b.time.astimezone(IST).time() < ANALYSIS_END]
    if not all(math.isfinite(x) and x > 0 for x in (tick, capital, risk, rr)):
        raise ValueError('Invalid backtest parameters')
    exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
    cal = calendar or DEFAULT_CALENDAR
    grouped = {}
    for bar in bars:
        if session(bar.time, cal, exchange):
            cal.require_strategy_session(bar.time.date(), exchange)
            start = bar.time.replace(hour=9, minute=15, second=0, microsecond=0)
            if (bar.time-start).total_seconds() % 300:
                raise ValueError('Off-grid five-minute candle')
            grouped.setdefault(bar.time.date(), []).append(bar)
    trades = []
    for day, rows in sorted(grouped.items()):
        start = dt.datetime.combine(day, dt.time(9, 15), IST)
        if rows[0].time != start or any(b.time-a.time != dt.timedelta(minutes=5) for a, b in zip(rows, rows[1:])):
            raise ValueError('Missing intraday bars in backtest')
        for index in range(3, len(rows)-1):
            p = proposal(rows[:index+1], tick, rr)
            if p:
                trade = replay_trade(p, rows[index+1:], capital, risk, exchange, config)
                if trade:
                    trades.append(trade)
                break
    return {'mode': 'paper', 'trades': trades, 'net_pnl': sum(t['net_pnl'] for t in trades)}
