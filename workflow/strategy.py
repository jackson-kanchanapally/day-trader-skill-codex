"""Scanner, bar-by-bar entries and configurable exit plans using upstream rules."""
import datetime as dt
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from india.analysis import indicators, size
from india.data import IST, validate
from india.forecast import _simulate, MIN_SESSIONS, MAX_SIMS
from india.market import DEFAULT_CALENDAR, costs
from .common import clock, envelope, exchange_of, indian_words, number, upstream, utc
from .data import verify_tick


def regular(bars, now, interval, calendar, exchange):
    minutes = 1 if interval == '1m' else 5
    completed = [b for b in bars if b.time+dt.timedelta(minutes=minutes) <= now]
    if completed:
        validate(completed)
    rows = [b for b in completed if b.time.date() == now.date() and
            dt.time(9, 15) <= b.time.time() < dt.time(15, 15) and calendar.trading_day(b.time.date(), exchange)]
    opening = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = min(now, opening.replace(hour=15, minute=15))
    expected = [opening+dt.timedelta(minutes=minutes*i) for i in range(max(0, int((end-opening).total_seconds()//(minutes*60))))]
    if any(b.time.second or b.time.microsecond or (b.time-opening).total_seconds() % (minutes*60) for b in rows):
        raise ValueError('Off-grid '+interval+' candle')
    received = {b.time for b in rows}
    return rows, [t.isoformat() for t in expected if t not in received]


def entry(bars, symbol, now, cfg, setup='orb', level=None, interval='5m', or_minutes=15,
          min_volume_x=None, calendar=None, source='caller-supplied', tick=None, master=None):
    cal, exchange = calendar or DEFAULT_CALENDAR, exchange_of(symbol)
    if interval not in ('1m', '5m'):
        raise ValueError('Entry requires 1m or 5m bars')
    if not isinstance(or_minutes, int) or not 1 <= or_minutes <= 120:
        raise ValueError('Opening range must be 1..120 minutes')
    if level is not None:
        number(level, 'level')
    if min_volume_x is not None:
        number(min_volume_x, 'min_volume_x')
    cal.trading_day(now.date(), exchange)
    rows, missing = regular(bars, now, interval, cal, exchange)
    m = upstream('entry', cal, exchange)
    chart = {'symbol': symbol, 'bars': [dict(t=int(b.time.timestamp()), o=b.open, h=b.high, l=b.low, c=b.close, v=b.volume) for b in rows], 'data_time': None, 'prev_close': None}
    result = m.evaluate(chart, utc(now), setup=setup, level=level, interval=interval,
                        or_minutes=or_minutes, min_volume_x=min_volume_x, cfg=cfg)
    doc = dict(m.public(result), **{k: v for k, v in m.entry_doc(result, utc(now)).items() if k not in m.public(result)})
    doc.update(source=source, currency='INR', timezone='Asia/Kolkata', paper=True,
               execution_enabled=False, checked_at=now.isoformat(), as_of=now.isoformat(),
               missing_bars=missing, data_complete=not missing, indicators=indicators(rows),
               strategy_window='09:15-15:15 IST')
    if missing:
        doc.update(status='no-data', reason='Missing required session candles', trigger=None,
                   price=None, time=None, bar_index=None)
    trigger = doc.get('trigger')
    eligible = bool(trigger and doc['status'] == 'triggered' and doc.get('minutes_since', 0) <= (1 if interval == '1m' else 5)
                    and dt.time.fromisoformat(cfg['no_entries_before']) <= now.time() <= dt.time.fromisoformat(cfg['no_entries_after']))
    doc['eligible'] = eligible
    if trigger:
        doc.update(price=trigger['price'], time=trigger['close_time'],
                   bar_index=trigger['bar_index'], bar_start=trigger['bar_start'])
        signal = dt.datetime.combine(now.date(), dt.time.fromisoformat(trigger['bar_start']), IST)
        doc['signal_time'] = signal.isoformat()
    if tick is not None:
        doc['tick'] = tick
        doc['tick_verification'] = verify_tick(symbol, tick, master or {})
    return indian_words(doc)


def scan(data, symbols, now, cfg, mode='orb', calendar=None, min_price=0, min_volume=0, **kwargs):
    cal = calendar or DEFAULT_CALENDAR
    hits, failed, checked = [], [], []
    m = upstream('scanner', cal)
    for symbol in symbols:
        try:
            exchange = exchange_of(symbol)
            bars = data.history(symbol, '1d' if mode == 'daily' else '5m')
            if mode == 'daily':
                rows = [b for b in bars if b.time.date() < now.date() and str(b.time.year) in cal.document.get(exchange, {}) and cal.trading_day(b.time.date(), exchange)]
                if len(rows) < m.MIN_DAILY_BARS:
                    raise ValueError('Daily scan requires at least 30 completed sessions')
                tags, values = m.find_setups([b.close for b in rows])
                item = dict(symbol=symbol, price=rows[-1].close, volume=rows[-1].volume,
                            setups=tags, as_of=rows[-1].time.isoformat(), source=data.source,
                            provider_attempts=getattr(data, 'attempts', {}).get((symbol, '1d'), []), **values)
                average = sum(b.volume for b in rows[-20:])/20
                item['volume_x'] = rows[-1].volume/average if average else None
                if tags and item['price'] >= min_price and item['volume'] >= min_volume:
                    hits.append(item)
                checked.append(item)
            else:
                item = entry(bars, symbol, now, cfg, calendar=cal, source=data.source, **kwargs)
                item['provider_attempts'] = getattr(data, 'attempts', {}).get((symbol, '5m'), [])
                checked.append(item)
                if item['status'] == 'no-data':
                    raise ValueError(item['reason'])
                if item['status'] == 'triggered' and item['price'] >= min_price and sum(b.volume for b in bars if b.time.date() == now.date()) >= min_volume:
                    hits.append(dict(symbol=symbol, setups=['orb'], **{k: v for k, v in item.items() if k != 'symbol'}))
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            failed.append({'symbol': symbol, 'error': str(exc)})
    used_sources = sorted({row['source'] for row in checked if row.get('source')})
    return envelope('scanner', now, mode=mode, as_of=now.isoformat(), source=used_sources[0] if len(used_sources) == 1 else 'See per-symbol sources',
                    universe=len(symbols), scanned=len(symbols)-len(failed), failed=failed,
                    hits=hits, results=checked)


def rounded(value, tick, direction=ROUND_HALF_UP):
    return float((Decimal(str(value))/Decimal(str(tick))).to_integral_value(rounding=direction)*Decimal(str(tick)))


def plan(info, now, tick, cfg, stop=None, target='2R', bars=None, calendar=None,
         master=None, flat_by=None):
    symbol, exchange = info['symbol'], exchange_of(info['symbol'])
    cal = calendar or DEFAULT_CALENDAR
    number(tick, 'tick')
    if stop is None:
        if info.get('setup', 'orb') != 'orb':
            raise ValueError('A level setup needs an explicit percentage, ATR or price stop')
        stop = 'or-mid'
    day = dt.date.fromisoformat(info.get('date') or now.date().isoformat())
    if day > now.date() or not cal.trading_day(day, exchange):
        raise ValueError('Plan date must be an available trading session')
    m = upstream('exit', cal, exchange)
    step = Decimal(str(tick))
    m.cents = lambda value: (m.D(value)/step).to_integral_value(rounding=ROUND_HALF_UP)*step
    m.floor_cents = lambda value: (m.D(value)/step).to_integral_value(rounding=ROUND_FLOOR)*step
    stop_rule, stop_param = m.parse_stop(stop)
    target_rule, target_param = m.parse_target(target)
    e = rounded(info.get('price', info.get('entry')), tick, ROUND_CEILING)
    opening_range = info.get('opening_range') or {}
    atr, atr_n = None, None
    signal = clock(info['signal_time']) if info.get('signal_time') else None
    if signal is None and info.get('time'):
        signal = dt.datetime.combine(day, dt.time.fromisoformat(info['time']), IST)-dt.timedelta(minutes=1 if info.get('interval') == '1m' else 5)
    if signal and signal+dt.timedelta(minutes=1 if info.get('interval') == '1m' else 5) > now:
        raise ValueError('Plan signal has not completed')
    if stop_rule == 'atr':
        if signal is None or bars is None:
            raise ValueError('ATR requires candles and a dated completed entry signal')
        used = [b for b in bars if b.time.date() == day and dt.time(9, 15) <= b.time.time() and b.time <= signal]
        if len(used) < 3:
            raise ValueError('ATR requires at least 3 completed bars by entry')
        expected_step = dt.timedelta(minutes=1 if info.get('interval') == '1m' else 5)
        if used[0].time.time() != dt.time(9, 15) or any(b.time-a.time != expected_step for a, b in zip(used, used[1:])):
            raise ValueError('ATR session contains missing candles')
        atr, atr_n = m.atr_of([dict(h=b.high, l=b.low, c=b.close) for b in used])
    computed = m.compute(e, stop_rule, stop_param, target_rule, target_param,
                         opening_range.get('high'), opening_range.get('low'), atr, atr_n)
    flat = flat_by or cfg['flat_by']
    at = dt.time.fromisoformat(flat)
    confirmation = signal+dt.timedelta(minutes=1 if info.get('interval') == '1m' else 5) if signal else None
    if not dt.time(9, 15) < at < dt.time(15, 15) or (confirmation and confirmation.time() >= at):
        raise ValueError('Flat-by must follow entry and precede 15:15 IST')
    target_price = rounded(float(computed['target']), tick, ROUND_CEILING)
    risk_per_share = e-float(computed['stop'])
    return envelope('exit', now, symbol=symbol, side='buy', date=day.isoformat(), setup=info.get('setup', 'orb'),
                    entry=e, entry_time=confirmation.strftime('%H:%M') if confirmation else None,
                    signal_time=signal.isoformat() if signal else None,
                    entry_bar=info.get('bar_index'), interval=info.get('interval', '5m'),
                    opening_range=opening_range or None, level=info.get('level'), stop=float(computed['stop']),
                    stop_rule=stop_rule, stop_spec=stop, stop_label=computed['stop_label'],
                    target=target_price, target_spec=target, target_r=(target_price-e)/risk_per_share,
                    risk_per_share=risk_per_share, reward_risk=(target_price-e)/risk_per_share,
                    flat_by=flat, atr=atr, atr_bars=atr_n, tick=tick,
                    tick_verification=verify_tick(symbol, tick, master or {}),
                    source=info.get('source', 'explicit paper inputs'),
                    entry_eligible=info.get('eligible', False), as_of=now.isoformat())


def historical_replay(days, current_plan, cfg, calendar=None):
    """Replay the actual configured rule with causal next-open fills and costs."""
    trades = []
    symbol, tick = current_plan['symbol'], current_plan['tick']
    for day, rows in days:
        for index in range(len(rows)-1):
            now = rows[index].time+dt.timedelta(minutes=5)
            candidate = entry(rows[:index+1], symbol, now, cfg, setup=current_plan['setup'],
                              level=current_plan.get('level'), or_minutes=(current_plan.get('opening_range') or {}).get('minutes', 15),
                              calendar=calendar)
            if candidate['status'] != 'triggered':
                continue
            p = plan(candidate, now, tick, cfg, current_plan['stop_spec'], current_plan['target_spec'], rows[:index+1], calendar)
            p['entry'] = rounded(rows[index+1].open, tick, ROUND_CEILING)
            if not p['stop'] < p['entry'] < p['target']:
                break
            from .paper import budget
            sized = budget(p, now, cfg)['sizing']
            if not sized['qty'] or sized['net_reward_risk'] < cfg['min_reward_risk']:
                break
            outcome, exit_price, last = None, None, None
            for row in rows[index+1:]:
                if row.time.time() >= dt.time.fromisoformat(p['flat_by']):
                    break
                last = row
                if row.low <= p['stop']:
                    outcome, exit_price = 'stop', min(row.open, p['stop'])
                    break
                if row.high >= p['target']:
                    outcome, exit_price = 'target', p['target']
                    break
            if last is None:
                break
            outcome, exit_price = outcome or 'close', last.close if exit_price is None else exit_price
            pnl = (exit_price-p['entry'])*sized['qty']-costs(p['entry'], exit_price, sized['qty'], exchange_of(symbol), cfg.get('costs'))['total']
            trades.append(dict(date=day.isoformat(), signal_time=rows[index].time.isoformat(),
                               entry_time=rows[index+1].time.isoformat(), entry=p['entry'], stop=p['stop'],
                               target=p['target'], qty=sized['qty'], exit=exit_price, outcome=outcome, net_pnl=pnl))
            break
    return dict(sessions=len(days), triggered=len(trades), trades=trades,
                net_pnl=sum(t['net_pnl'] for t in trades), costs_included=True, entry_rule='next-bar open')


def timemachine(bars, p, now, cfg, calendar=None, sims=10000, seed=19):
    if not isinstance(sims, int) or isinstance(sims, bool) or not 1 <= sims <= MAX_SIMS:
        raise ValueError('Replays must be 1..'+str(MAX_SIMS))
    if p.get('interval') != '5m':
        raise ValueError('Time Machine requires five-minute candles')
    from collections import defaultdict
    cal, symbol = calendar or DEFAULT_CALENDAR, p['symbol']
    cutoff = clock(p['as_of'])
    if cutoff > now:
        raise ValueError('Plan cutoff is in the future')
    grouped = defaultdict(list)
    for b in bars:
        if b.time+dt.timedelta(minutes=5) <= cutoff and b.time.date() < cutoff.date() and dt.time(9, 15) <= b.time.time() < dt.time(15, 15) and cal.trading_day(b.time.date(), exchange_of(symbol)):
            grouped[b.time.date()].append(b)
    days, excluded = [], []
    for day, rows in sorted(grouped.items()):
        opening = dt.datetime.combine(day, dt.time(9, 15), IST)
        if [b.time for b in rows] == [opening+dt.timedelta(minutes=5*i) for i in range(72)]:
            days.append((day, rows))
        else:
            excluded.append(day.isoformat())
    days = days[-120:]
    history = historical_replay(days, p, cfg, cal)
    doc = envelope('timemachine', now, symbol=symbol, as_of=cutoff.isoformat(), status='UNAVAILABLE',
                   reason='INSUFFICIENT_HISTORY', prior_sessions=len(days), minimum_sessions=MIN_SESSIONS,
                   excluded_sessions=excluded, history=history, mc=None,
                   limitations=['Sampled scenarios are not forecasts or independent observations.', 'Historical fills use next-bar open, stop-first ambiguity and estimated costs.'])
    if len(days) < MIN_SESSIONS:
        return doc
    if not p.get('signal_time') or clock(p['signal_time'])+dt.timedelta(minutes=5) != cutoff:
        doc['reason'] = 'STALE_PLAN'
        return doc
    current, missing = regular(bars, cutoff, '5m', cal, exchange_of(symbol))
    if missing or not current:
        doc['reason'] = 'INCOMPLETE_CURRENT_SESSION'
        return doc
    if abs(current[-1].close-p['entry']) > p['tick']+1e-8:
        doc['reason'] = 'ENTRY_DIFFERS_FROM_LATEST_CLOSE'
        return doc
    from .paper import budget
    sized = budget(p, cutoff, cfg)['sizing']
    if not sized['qty']:
        doc['reason'] = 'NO_AFFORDABLE_POSITION'
        return doc
    opening = cutoff.replace(hour=9, minute=15, second=0, microsecond=0)
    slot = int((cutoff-opening).total_seconds()//300)
    until = int((dt.datetime.combine(cutoff.date(), dt.time.fromisoformat(p['flat_by']), IST)-opening).total_seconds()//300)
    if not 1 <= slot < until <= 72:
        doc['reason'] = 'OUTSIDE_HOLDING_WINDOW'
        return doc
    # Reuse the existing cost-aware, gap-aware whole-bar bootstrap. Slice each
    # complete session at the configured flatten boundary, without global mutation.
    from types import FunctionType
    scope = dict(_simulate.__globals__, SESSION_BARS=until)
    simulate = FunctionType(_simulate.__code__, scope)
    mc = simulate([rows[:until] for _, rows in days], sized, slot, sims, seed, exchange_of(symbol), cfg.get('costs'))
    mc['until'] = p['flat_by']
    doc.update(status='AVAILABLE', reason=None, mc=mc)
    return doc
