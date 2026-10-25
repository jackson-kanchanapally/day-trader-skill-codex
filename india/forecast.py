"""Indian paper Time Machine: seeded whole-candle bootstrap, never live prices.

The slot-local OHLC ratio bootstrap and percentile convention adapt the original
``legacy/trader-timemachine/timemachine.py`` under the repository's license.
Unlike that US implementation, the session has 72 bars, fills through a stop can
lose more than 1R, and all outcome returns include the existing Indian costs.
"""
import datetime as dt
import math
import random
import statistics
from collections import defaultdict
from dataclasses import replace

from .analysis import backtest, size, ANALYSIS_END
from .data import IST, symbol_name, timestamp, validate
from .market import costs, session

MIN_SESSIONS = 20
MAX_SIMS = 50000
MAX_POOL_SESSIONS = 120
SESSION_BARS = 72
STEP = dt.timedelta(minutes=5)


def _pct(ordered, fraction):
    # Preserve the upstream percentile convention (input is already sorted).
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _simulate(days, plan, first_slot, sims, seed, exchange, config):
    # Whole observed candles preserve within-bar OHLC relationships. Draw from
    # the same time of day +/- two bars, as in the upstream Time Machine.
    pool = [[] for _ in range(SESSION_BARS)]
    for rows in days:
        for i in range(1, SESSION_BARS):
            previous = rows[i - 1].close
            b = rows[i]
            pool[i].append(tuple(x / previous for x in (b.open, b.high, b.low, b.close)))
    rng = random.Random(seed)
    entry, stop, target, qty = (plan[k] for k in ('entry', 'stop', 'target', 'qty'))
    counts = dict.fromkeys(('target', 'stop', 'close'), 0)
    terminal, pnls, path_columns = [], [], [[] for _ in range(SESSION_BARS - first_slot + 1)]
    sample_paths = []
    for n in range(sims):
        price, outcome, exit_price = entry, None, None
        path = [entry]
        for i in range(first_slot, SESSION_BARS):
            slot = min(SESSION_BARS - 1, max(1, i + rng.randint(-2, 2)))
            o, h, lo, c = rng.choice(pool[slot])
            opening, high, low, closing = (price * x for x in (o, h, lo, c))
            if outcome is None:
                if low <= stop:
                    outcome, exit_price = 'stop', min(opening, stop)
                elif high >= target:
                    outcome, exit_price = 'target', target
            price = closing
            path.append(price)
        outcome = outcome or 'close'
        exit_price = price if exit_price is None else exit_price
        counts[outcome] += 1
        terminal.append(price)
        pnls.append((exit_price - entry) * qty - costs(entry, exit_price, qty, exchange, config)['total'])
        # Bound chart memory independently of requested simulation count.
        if n < 1000:
            for column, value in zip(path_columns, path):
                column.append(value)
        if n < 12:
            sample_paths.append(path)
    ordered, net = sorted(terminal), sorted(pnls)
    return {
        'status': 'AVAILABLE', 'kind': 'SIMULATED_PATHS_NOT_OBSERVED_PRICES',
        'replays': sims, 'seed': seed, 'window': 2,
        'bars_left': SESSION_BARS - first_slot, 'until': '15:15',
        'pool_sessions': len(days), 'counts': counts,
        'p_target': counts['target'] / sims, 'p_stop': counts['stop'] / sims,
        'p_neither': counts['close'] / sims, 'p_green': sum(x > 0 for x in pnls) / sims,
        'median_close': _pct(ordered, .5), 'p5_close': _pct(ordered, .05),
        'p95_close': _pct(ordered, .95), 'mean_net_pnl': statistics.mean(pnls),
        'p5_net_pnl': _pct(net, .05), 'median_net_pnl': _pct(net, .5),
        'p95_net_pnl': _pct(net, .95),
        'avg_R': statistics.mean(pnls) / plan['max_loss_estimate'],
        'costs_included': True, 'band_paths': min(sims, 1000),
        'bands': [[_pct(sorted(column), q) for q in (.05, .25, .5, .75, .95)] for column in path_columns],
        'sample_paths': sample_paths,
    }


def forecast(bars, as_of, plan, symbol, tick, capital, risk, rr=2., config=None,
             calendar=None, sims=10000, seed=17):
    """Return a JSON-safe paper forecast from completed, supplied five-minute bars.

    ``plan`` is the current long opening-range plan from ``analysis.report``
    (or ``proposal``), or None. Quantities are always recalculated. Historical
    evaluation is the existing strategy, next-bar fills and costs, on complete
    sessions strictly before the as-of date. At least twenty are required for
    Monte Carlo; missing sessions are excluded, never interpolated.

    Invalid parameters raise ValueError. Missing/insufficient/obsolete data or
    plans produce an explicit UNAVAILABLE report. No network calls are made.
    """
    if not all(_positive(x) for x in (tick, capital, risk, rr)):
        raise ValueError('tick, capital, risk and reward/risk must be positive finite numbers')
    if isinstance(sims, bool) or not isinstance(sims, int) or not 1 <= sims <= MAX_SIMS:
        raise ValueError('sims must be an integer between 1 and %d' % MAX_SIMS)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('seed must be an integer')
    if not isinstance(as_of, dt.datetime) or as_of.tzinfo is None:
        raise ValueError('as_of must be a timezone-aware datetime')
    symbol_name(symbol)
    as_of = as_of.astimezone(IST)
    exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
    # Validate the cost configuration even when data/plan is unavailable.
    costs(100, 100, 0, exchange, config)
    doc = {
        'symbol': symbol, 'mode': 'paper', 'execution_enabled': False,
        'as_of': as_of.isoformat(), 'status': 'UNAVAILABLE', 'reason': None,
        'sims': sims, 'seed': seed, 'plan': None,
        'mc': {'status': 'UNAVAILABLE', 'sample_sessions': 0, 'minimum_sessions': MIN_SESSIONS},
        'history': {'status': 'UNAVAILABLE', 'sessions': 0, 'trades': []},
        'data': {'source': 'caller-supplied OHLCV; provider identity must be recorded by caller',
                 'interval': '5m', 'timezone': 'Asia/Kolkata',
                 'analysis_window': '09:15-15:15', 'minimum_sessions': MIN_SESSIONS,
                 'pool_session_limit': MAX_POOL_SESSIONS, 'prior_complete_sessions': 0,
                 'excluded_sessions': [], 'last_completed_bar': None},
        'limitations': [
            'Simulated paths are scenarios, not observed prices, predictions or guaranteed probabilities.',
            'Past whole-candle resampling cannot model unprecedented news, halts or regime changes.',
            'Only complete prior sessions are used; missing history is never fabricated.',
            'A supplied long opening-range plan is assumed; historical results evaluate the existing opening-range rule.',
            'Historical trades use a fixed capital/risk budget per trade; this is not portfolio or compounded performance.',
            'Stop is checked before target within each candle; actual liquidity and fills can be worse.',
            'Costs and slippage are estimates; cash/risk limits apply at sizing, not as guaranteed loss caps.',
            'Scenarios start at the latest completed close, not an observed live fill; forming bars are excluded.',
        ],
    }

    def unavailable(reason):
        doc['reason'] = reason
        doc['mc']['reason'] = reason
        return doc

    # Ignore not-yet-completed observations before validation to avoid even
    # letting an invalid future candle affect a historical forecast.
    bars = list(bars)
    if any(b.time.tzinfo is None for b in bars):
        raise ValueError('Bars require timezone-aware timestamps')
    usable = [replace(b, time=b.time.astimezone(IST)) for b in bars
              if b.time + STEP <= as_of]
    if not usable:
        return unavailable('NO_COMPLETED_DATA')
    validate(usable)
    usable = [b for b in usable if session(b.time, calendar, exchange) and b.time.time() < ANALYSIS_END]
    if not usable:
        return unavailable('NO_REGULAR_SESSION_DATA')
    if any(b.time.second or b.time.microsecond or b.time.minute % 5 for b in usable):
        raise ValueError('Forecast requires five-minute grid candles')
    doc['data']['last_completed_bar'] = usable[-1].time.isoformat()
    grouped = defaultdict(list)
    for b in usable:
        grouped[b.time.date()].append(b)
    full = []
    for day, rows in sorted(grouped.items()):
        if day >= as_of.date():
            continue
        opening = dt.datetime.combine(day, dt.time(9, 15), IST)
        if [b.time for b in rows] == [opening + STEP * i for i in range(SESSION_BARS)]:
            full.append((day, rows))
        else:
            doc['data']['excluded_sessions'].append({'date': day.isoformat(), 'bars': len(rows)})
    full = full[-MAX_POOL_SESSIONS:]
    doc['data'].update(prior_complete_sessions=len(full),
                       pool_from=full[0][0].isoformat() if full else None,
                       pool_to=full[-1][0].isoformat() if full else None)
    doc['mc']['sample_sessions'] = len(full)
    if full:
        history = backtest([b for _, rows in full for b in rows], symbol, tick, capital, risk, rr, config, calendar)
        doc['history'] = dict(history, status='AVAILABLE', sessions=len(full),
                              triggered=len(history['trades']), costs_included=True,
                              entry_rule='next-bar open', current_session_excluded=True)
    if plan is None:
        return unavailable('NO_PLAN')
    if not isinstance(plan, dict) or not all(_positive(plan.get(k)) for k in ('entry', 'stop', 'target')):
        raise ValueError('plan requires positive finite entry, stop and target')
    if not plan['stop'] < plan['entry'] < plan['target']:
        raise ValueError('Only long plans with stop < entry < target are supported')
    if plan.get('side', 'buy') not in ('buy', 'long'):
        raise ValueError('Only long paper plans are supported')
    for name in ('entry', 'stop', 'target'):
        if not math.isclose(plan[name] / tick, round(plan[name] / tick), abs_tol=1e-7):
            raise ValueError('Plan prices must align with tick size')
    if not plan.get('signal_time'):
        raise ValueError('plan requires signal_time for causal validation')
    signal = timestamp(plan['signal_time'])
    if signal + STEP > as_of:
        return unavailable('SIGNAL_NOT_COMPLETED')
    if signal.date() != as_of.date() or signal != usable[-1].time:
        return unavailable('STALE_PLAN')
    if not session(as_of, calendar, exchange) or as_of.time() >= ANALYSIS_END:
        return unavailable('OUTSIDE_STRATEGY_WINDOW')
    opening = as_of.replace(hour=9, minute=15, second=0, microsecond=0)
    first_slot = int((as_of - opening).total_seconds() // 300)
    if first_slot < 1 or first_slot >= SESSION_BARS:
        return unavailable('NO_REMAINING_BARS')
    today = grouped.get(as_of.date(), [])
    if [b.time for b in today] != [opening + STEP * i for i in range(first_slot)]:
        return unavailable('INCOMPLETE_CURRENT_SESSION')
    scenario_start = opening + STEP * first_slot
    doc['plan'] = size(dict({k: plan[k] for k in ('entry', 'stop', 'target', 'signal_time')}, tick=tick),
                       capital, risk, exchange, config)
    if not doc['plan']['qty'] or doc['plan']['net_reward_risk'] < 1:
        return unavailable('COST_OR_RISK_LIMIT')
    if len(full) < MIN_SESSIONS:
        doc['limitations'].append('Warmup incomplete: %d of %d required complete prior sessions.' % (len(full), MIN_SESSIONS))
        return unavailable('INSUFFICIENT_HISTORY')
    doc['mc'] = _simulate([rows for _, rows in full], doc['plan'], first_slot, sims, seed, exchange, config)
    doc['mc'].update(sample_sessions=len(full), minimum_sessions=MIN_SESSIONS,
                     scenario_start=scenario_start.isoformat(),
                     completed_close_age_seconds=(as_of - scenario_start).total_seconds())
    doc['status'], doc['reason'] = 'AVAILABLE', None
    return doc
