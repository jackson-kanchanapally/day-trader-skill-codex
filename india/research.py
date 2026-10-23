"""Chronological, cost-stressed research using the existing ORB backtester.

No parameter fitting or profitability claim: train/holdout are chronological
evaluation partitions of the same fixed rules. One cash-funded position at a
time, one ORB opportunity per symbol/session. This is single-symbol research.
"""
import datetime as dt
import math
from .analysis import backtest, ANALYSIS_END
from .data import IST, validate
from .market import trading_day, costs


def metrics(trades, capital):
    equity, peak, drawdown = capital, capital, 0.
    gains = sum(max(0,t['net_pnl']) for t in trades)
    losses = -sum(min(0,t['net_pnl']) for t in trades)
    curve = []
    for trade in trades:
        equity += trade['net_pnl']
        peak = max(peak,equity)
        drawdown = max(drawdown,(peak-equity)/peak)
        curve.append({'time':trade['exit_bar'],'equity':equity})
    return {'trades':len(trades),'net_pnl':equity-capital,'ending_equity':equity,
            'return_fraction':equity/capital-1,'max_drawdown_fraction':drawdown,
            'win_rate':sum(t['net_pnl'] > 0 for t in trades)/len(trades) if trades else None,
            'profit_factor':gains/losses if losses else None,
            'expectancy':(gains-losses)/len(trades) if trades else None,
            'equity_curve':curve}


def evaluate(days, symbol, tick, capital, risk, rr, config, calendar, risk_fraction):
    equity, trades = capital, []
    for _, bars in days:
        if equity <= 0:
            break
        # Size from realized equity only. No future outcome enters sizing.
        budget = min(risk,equity*risk_fraction)
        result = backtest(bars,symbol,tick,equity,budget,rr,config,calendar)
        for trade in result['trades']:
            trades.append(trade)
            equity += trade['net_pnl']
    return dict(metrics(trades,capital),trade_details=trades)


def research(bars, as_of, symbol, tick, capital, risk, rr=2., config=None,
             calendar=None, train_fraction=.7, risk_fraction=.005):
    if not (0 < train_fraction < 1 and 0 < risk_fraction <= 1):
        raise ValueError('train_fraction must be between 0 and 1; risk_fraction must be in (0,1]')
    if not all(math.isfinite(x) and x > 0 for x in (tick,capital,risk,rr)):
        raise ValueError('Research parameters must be positive finite numbers')
    costs(1,1,0,'NSE' if symbol.endswith('.NS') else 'BSE',config)
    validate(bars)
    grouped = {}
    exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
    for b in bars:
        when=b.time.astimezone(IST)
        if (when+dt.timedelta(minutes=5) <= as_of
                and dt.time(9,15) <= when.time() < ANALYSIS_END
                and trading_day(when.date(),calendar,exchange)):
            grouped.setdefault(when.date(),[]).append(b)
    days, excluded = [], []
    for day, rows in sorted(grouped.items()):
        start=dt.datetime.combine(day,dt.time(9,15),IST)
        if len(rows) != 72 or any(b.time != start+dt.timedelta(minutes=5*i) for i,b in enumerate(rows)):
            excluded.append(day.isoformat())
        else:
            days.append((day,rows))
    if excluded:
        raise ValueError('Research requires complete 72-bar sessions; incomplete dates: '+', '.join(excluded))
    if len(days) < 2:
        raise ValueError('Research requires at least two complete trading sessions for chronological holdout')
    missing = []
    day=days[0][0]
    while day <= days[-1][0]:
        if trading_day(day,calendar,exchange) and day not in grouped:
            missing.append(day.isoformat())
        day += dt.timedelta(days=1)
    if missing:
        raise ValueError('Research contains missing whole trading sessions: '+', '.join(missing))
    split = min(len(days)-1,max(1,int(len(days)*train_fraction)))
    baseline = (config or {}).get('slippage_bps',5.)
    # Even a zero-slippage baseline gets a meaningful positive stress case.
    scenarios = sorted(set((baseline,max(5.,baseline*2),max(10.,baseline*4))))
    results = []
    for slippage in scenarios:
        cost_config = dict(config or {},slippage_bps=slippage)
        parts = {}
        for label, subset in (('train',days[:split]),('holdout',days[split:])):
            parts[label] = dict(evaluate(subset,symbol,tick,capital,risk,rr,cost_config,calendar,risk_fraction),
                                start=subset[0][0].isoformat(),end=subset[-1][0].isoformat(),sessions=len(subset))
        results.append(dict(slippage_bps=slippage,**parts))
    return {'mode':'paper_research','execution_enabled':False,'symbol':symbol,
            'as_of':as_of.isoformat(),'strategy':'opening_range_breakout',
            'sessions':len(days),'train_fraction':train_fraction,
            'risk_fraction':risk_fraction,'capital':capital,'risk_cap':risk,
            'method':'Fixed-rule chronological holdout; no parameter fitting. Each partition starts with the same capital.',
            'scenarios':results,
            'limitations':['Single symbol, long only; no multi-asset portfolio simulation.',
                           'No survivorship/corporate-action correction or profitability guarantee.',
                           'Gap-through-stop losses may exceed the sizing budget.']}
