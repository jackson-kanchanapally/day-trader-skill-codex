"""Causal indicators, opening-range setups, cost-aware sizing and replay."""
import datetime as dt
import importlib.util
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
from .data import IST, validate
from .market import session, costs

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

def completed(bars, as_of, minutes):
    validate(bars)
    bars = [b for b in bars if b.time + dt.timedelta(minutes=minutes) <= as_of and session(b.time)]
    if not bars:
        raise ValueError('No completed regular-session bars as of requested time')
    return bars

def indicators(bars):
    if not bars:
        return dict.fromkeys(('rsi14','ema9','ema21','vwap','volume_spike'))
    closes = [b.close for b in bars]
    today = [b for b in bars if b.time.date() == bars[-1].time.date()]
    volume = sum(b.volume for b in today)
    prior = bars[-21:-1]
    mean = sum(b.volume for b in prior)/len(prior) if prior else 0
    return {'rsi14': _scan.rsi(closes) if len(closes) >= 15 else None,
            'ema9': ema(closes,9), 'ema21': ema(closes,21),
            'vwap': sum((b.high+b.low+b.close)/3*b.volume for b in today)/volume if volume else None,
            'volume_spike': bars[-1].volume/mean if mean else None}

def tick_round(x, tick, up=False):
    t = Decimal(str(tick))
    return float((Decimal(str(x))/t).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*t)

def proposal(bars, tick, rr):
    day = bars[-1].time.date()
    today = [b for b in bars if b.time.date() == day]
    opening = [b for b in today if b.time.time() < dt.time(9,30)]
    if len(opening) != 3 or [b.time.time() for b in opening] != [dt.time(9,15),dt.time(9,20),dt.time(9,25)]:
        return None
    high, low = max(b.high for b in opening), min(b.low for b in opening)
    last = today[-1]
    earlier = [b for b in today if dt.time(9,30) <= b.time.time() and b is not last]
    if last.time.time() < dt.time(9,30) or last.close <= high or any(b.close > high for b in earlier):
        return None
    entry, stop = tick_round(last.close,tick,True), tick_round(low,tick)
    if entry <= stop:
        return None
    return {'entry': entry, 'stop': stop, 'target': tick_round(entry+rr*(entry-stop),tick,True), 'signal_time': last.time.isoformat()}

def size(plan, capital, risk, exchange, config):
    e,s,t = (plan[k] for k in ('entry','stop','target'))
    lo, hi = 0, int(min(capital/e, risk/(e-s)))
    while lo < hi:
        q = (lo+hi+1)//2
        fee = costs(e,s,q,exchange,config)['total']
        if q*(e-s)+fee <= risk and q*e+fee <= capital:
            lo = q
        else:
            hi = q-1
    loss = lo*(e-s)+costs(e,s,lo,exchange,config)['total']
    reward = lo*(t-e)-costs(e,t,lo,exchange,config)['total']
    return dict(plan, qty=lo, max_loss_estimate=loss, net_reward=reward,
                net_reward_risk=reward/loss if loss else 0, costs_at_target=costs(e,t,lo,exchange,config))

def report(bars, as_of, symbol, tick, capital, risk, rr=2., config=None):
    if not all(math.isfinite(x) and x > 0 for x in (tick,capital,risk,rr)):
        raise ValueError('tick, capital, risk and reward/risk must be positive finite numbers')
    bars = completed([b for b in bars if b.time.astimezone(IST).time() < ANALYSIS_END],as_of,5)
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
    eligible = not missing and session(as_of) and not stale and as_of.astimezone(IST).time() < dt.time(15,15)
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
            'indicators':indicators(usable),'plan':sized,
            'indicators_as_of':(usable[-1].time+dt.timedelta(minutes=5)).isoformat() if usable else None,
            'data_quality':{'complete':not missing,'expected_bars':len(expected),
                            'received_bars':len(today),'usable_session_bars':sum(b.time.date()==today[0].time.date() for b in usable),
                            'missing_bars':[t.isoformat() for t in missing]},
            'market_open':session(as_of),
            'analysis_window':{'start':'09:15','end':'15:15','timezone':'Asia/Kolkata'},
            'strategy_open':session(as_of) and as_of.astimezone(IST).time() < ANALYSIS_END}

def backtest(bars, symbol, tick, capital, risk, rr=2., config=None):
    bars = [b for b in bars if b.time.astimezone(IST).time() < ANALYSIS_END]
    validate(bars)
    if not all(math.isfinite(x) and x > 0 for x in (tick,capital,risk,rr)):
        raise ValueError('Invalid backtest parameters')
    for a,b in zip(bars,bars[1:]):
        if a.time.date() == b.time.date() and b.time-a.time != dt.timedelta(minutes=5):
            raise ValueError('Missing intraday bars in backtest')
    trades=[]
    for i in range(3,len(bars)-1):
        b,nxt=bars[i],bars[i+1]
        if not session(b.time) or b.time.time() >= dt.time(15,10): continue
        p=proposal(bars[:i+1],tick,rr)
        if not p or nxt.time-b.time != dt.timedelta(minutes=5): continue
        p['entry']=tick_round(nxt.open,tick,True)  # fill only at next bar open
        if p['entry'] <= p['stop'] or p['entry'] >= p['target']: continue
        p=size(p,capital,risk,'NSE' if symbol.endswith('.NS') else 'BSE',config)
        if not p['qty'] or p['net_reward_risk'] < 1: continue
        future=[x for x in bars[i+1:] if x.time.date()==b.time.date() and session(x.time)]
        exit_price=None
        for x in future:
            if x.low <= p['stop']:
                exit_price=min(x.open,p['stop']); break
            if x.high >= p['target']:
                exit_price=p['target']; break
            if x.time.time() >= dt.time(15,10):
                exit_price=x.close; break
        if exit_price is None: continue  # incomplete session, never invent a close
        fee=costs(p['entry'],exit_price,p['qty'],'NSE' if symbol.endswith('.NS') else 'BSE',config)['total']
        trades.append(dict(p,exit=exit_price,net_pnl=(exit_price-p['entry'])*p['qty']-fee))
    return {'mode':'paper','trades':trades,'net_pnl':sum(t['net_pnl'] for t in trades)}
