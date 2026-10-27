"""Scanner, bar-by-bar entries and configurable exit plans using upstream rules."""
import datetime as dt
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from india.analysis import indicators
from india.execution import replay_trade, tick_price, flatten_slot
from india.volume import relative_volume
from india.data import IST, validate
from india.forecast import _simulate, MIN_SESSIONS, MAX_SIMS
from india.market import DEFAULT_CALENDAR, daily_completed
from .common import clock, envelope, exchange_of, indian_words, number, upstream, utc
from .data import verify_tick


def regular(bars, now, interval, calendar, exchange):
    minutes = 1 if interval == '1m' else 5
    calendar.require_strategy_session(now.date(), exchange)
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
          min_volume_x=None, calendar=None, source='caller-supplied', tick=None, master=None,
          volume_basis='same-time'):
    cal, exchange = calendar or DEFAULT_CALENDAR, exchange_of(symbol)
    if interval not in ('1m', '5m'):
        raise ValueError('Entry requires 1m or 5m bars')
    if not isinstance(or_minutes, int) or not 1 <= or_minutes <= 120:
        raise ValueError('Opening range must be 1..120 minutes')
    if level is not None:
        number(level, 'level')
    if min_volume_x is not None:
        number(min_volume_x, 'min_volume_x')
    if volume_basis not in ('same-time', 'opening-range'):
        raise ValueError('volume-basis must be opening-range or same-time')
    cal.trading_day(now.date(), exchange)
    rows, missing = regular(bars, now, interval, cal, exchange)
    m = upstream('entry', cal, exchange)
    chart = {'symbol': symbol, 'bars': [dict(t=int(b.time.timestamp()), o=b.open, h=b.high, l=b.low, c=b.close, v=b.volume) for b in rows], 'data_time': None, 'prev_close': None}
    completed_history = [b for b in bars if b.time+dt.timedelta(minutes=1 if interval == '1m' else 5) <= now]
    volume_args = dict(minutes=1 if interval == '1m' else 5,
                       lookback=cfg.get('rvol_lookback', 20), min_samples=cfg.get('rvol_min_samples', 5),
                       calendar=cal, exchange=exchange)
    excluded_volume = []
    evaluation_cfg = dict(cfg)
    while True:
        result = m.evaluate(chart, utc(now), setup=setup, level=level, interval=interval,
                            or_minutes=or_minutes, min_volume_x=min_volume_x if volume_basis == 'opening-range' else None, cfg=evaluation_cfg)
        trig = result.get('trigger')
        if not trig:
            break
        if volume_basis == 'opening-range':
            trig['relative_volume'] = dict(value=trig.get('volume_x'), mode='opening_range_average',
                                          current_volume=trig.get('volume'), baseline_volume=result['opening_range'].get('avg_volume'))
            break
        cutoff = dt.datetime.combine(now.date(), dt.time.fromisoformat(trig['close_time']), IST)
        rvol = relative_volume(completed_history, as_of=cutoff, **volume_args)
        trig['volume_x'] = rvol['value']
        trig['relative_volume'] = rvol
        if min_volume_x is None or (rvol['value'] is not None and rvol['value'] >= min_volume_x):
            break
        excluded_volume.append(dict(signal_time=cutoff.isoformat(), relative_volume=rvol))
        # Search subsequent confirmations without changing any market data.
        evaluation_cfg['no_entries_before'] = (cutoff+dt.timedelta(minutes=1)).strftime('%H:%M')
    result['min_volume_x'] = min_volume_x
    result['volume_basis'] = volume_basis
    result['no_entries_before'] = cfg['no_entries_before']
    if excluded_volume and not result.get('trigger'):
        result['reason'] = 'No breakout met prior-session time-of-day RVOL requirements'

    indicator_rows = [b for b in rows if b.time < clock(missing[0])] if missing else completed_history
    doc = dict(m.public(result), **{k: v for k, v in m.entry_doc(result, utc(now)).items() if k not in m.public(result)})
    doc.update(source=source, currency='INR', timezone='Asia/Kolkata', paper=True,
               execution_enabled=False, checked_at=now.isoformat(), as_of=now.isoformat(),
               missing_bars=missing, data_complete=not missing,
               volume_rejections=excluded_volume, indicators=indicators(indicator_rows, **volume_args),
               strategy_window='09:15-15:15 IST', calendar_warnings=cal.coverage_warnings(now.date(), exchange))
    if missing:
        doc.update(status='no-data', reason='Missing required session candles', trigger=None,
                   price=None, time=None, bar_index=None)
    trigger = doc.get('trigger')
    eligible = bool(trigger and doc['status'] == 'triggered' and doc.get('minutes_since', 0) <= (1 if interval == '1m' else 5)
                    and trigger.get('volume', 0) > 0
                    and dt.time.fromisoformat(cfg['no_entries_before']) <= now.time() <= dt.time.fromisoformat(cfg['no_entries_after']))
    doc['eligible'] = eligible
    if trigger:
        doc.update(price=trigger['price'], time=trigger['close_time'],
                   bar_index=trigger['bar_index'], bar_start=trigger['bar_start'])
        signal = dt.datetime.combine(now.date(), dt.time.fromisoformat(trigger['bar_start']), IST)
        doc['signal_time'] = signal.isoformat()
        confirmation = signal+dt.timedelta(minutes=1 if interval == '1m' else 5)
        doc['signal_confirmed_at'] = confirmation.isoformat()
        doc['eligible'] = doc['eligible'] and dt.timedelta(0) <= now-confirmation <= dt.timedelta(minutes=1 if interval == '1m' else 5)
    if tick is not None:
        doc['tick'] = tick
        doc['tick_verification'] = verify_tick(symbol, tick, master or {})
    return indian_words(doc)


def scan(data, symbols, now, cfg, mode='orb', calendar=None, min_price=0, min_volume=0,
         workers=4, max_confirm=None, eligible_only=False, batch_size=20, use_summaries=True, **kwargs):
    cal = calendar or DEFAULT_CALENDAR
    hits, failed, checked, stale, unavailable = [], [], [], [], []
    m = upstream('scanner', cal)
    symbols = list(dict.fromkeys(symbols))
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ValueError('workers must be 1..8')
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 20:
        raise ValueError('batch-size must be 1..20')
    if max_confirm is not None and (isinstance(max_confirm, bool) or not isinstance(max_confirm, int) or max_confirm < 1):
        raise ValueError('max-confirm must be a positive integer')
    if use_summaries and getattr(data, 'provider', None) == 'yahoo' and not data.csv_path:
        from .scanning import batch_scan
        return batch_scan(data, symbols, now, cfg, mode, cal, min_price, min_volume,
                          workers, max_confirm, eligible_only, batch_size, kwargs)
    if hasattr(data, 'prefetch'):
        data.prefetch(symbols, '1d' if mode == 'daily' else '5m', workers,
                      current_only=mode == 'orb' and kwargs.get('min_volume_x') is None)
    stage, skipped, candidates = {}, [], []
    if mode == 'orb':
        minutes = kwargs.get('or_minutes', 15)
        if minutes < 5 or minutes > 120 or minutes % 5:
            raise ValueError('Opening range must be a multiple of 5 from 5..120 minutes')
        n = minutes//5
        for symbol in symbols:
            try:
                bars = data.history(symbol, '5m')
                source = data.source
                rows, missing = regular(bars, now, '5m', cal, exchange_of(symbol))
                if missing:
                    stage[symbol] = dict(error='Missing required session candles', missing_bars=missing,
                                         source=source)
                    raise ValueError('Missing required session candles')
                turnover = sum(b.close*b.volume for b in rows)
                candidate = len(rows) > n and any(b.close >= max(r.close for r in rows[:n]) for b in rows[n:])
                liquid = bool(rows and rows[-1].close >= min_price and sum(b.volume for b in rows) >= min_volume)
                stage[symbol] = dict(bars=bars, source=source, turnover=turnover, candidate=candidate and liquid)
                if candidate and liquid:
                    candidates.append(symbol)
            except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
                stage.setdefault(symbol, dict(error=str(exc)))
        candidates.sort(key=lambda s: (-stage[s]['turnover'], s))
        skipped = candidates[max_confirm:] if max_confirm else []
    selected = set(candidates)-set(skipped)
    for symbol in symbols:
        try:
            exchange = exchange_of(symbol)
            if mode == 'orb':
                first = stage[symbol]
                if 'error' in first:
                    raise ValueError(first['error'])
                if symbol not in selected:
                    checked.append(dict(symbol=symbol, status='SKIPPED_CONFIRMATION_CAP' if symbol in skipped else 'NO_CANDIDATE',
                                        source=first['source'], eligible=False, turnover_inr=first['turnover']))
                    continue
                bars = first['bars']
                data.source = first['source']
            else:
                bars = data.history(symbol, '1d')
            if mode == 'daily':
                expected = cal.last_completed_session(now,exchange)
                if any(b.time.date() > now.date() for b in bars):
                    raise ValueError('Future-dated daily candle')
                if bars:
                    validate(bars)
                rows = [b for b in bars if daily_completed(b.time.date(),now,cal,exchange) and str(b.time.year) in cal.document.get(exchange, {}) and cal.trading_day(b.time.date(), exchange)]
                if len(rows) < m.MIN_DAILY_BARS:
                    item = dict(symbol=symbol,status='UNAVAILABLE',reason='Daily scan requires at least 30 completed sessions',expected_session=expected.isoformat(),source=data.source)
                    unavailable.append(item)
                    checked.append(item)
                    continue
                if rows[-1].time.date() != expected:
                    item = dict(symbol=symbol,status='STALE',reason='Latest completed trading session is missing',expected_session=expected.isoformat(),as_of=rows[-1].time.isoformat(),source=data.source)
                    stale.append(item)
                    checked.append(item)
                    continue
                tags, values = m.find_setups([b.close for b in rows])
                item = dict(symbol=symbol, price=rows[-1].close, volume=rows[-1].volume,
                            setups=tags, as_of=rows[-1].time.isoformat(), source=data.source,
                            provider_attempts=getattr(data, 'attempts', {}).get((symbol, '1d'), []), **values)
                average = sum(b.volume for b in rows[-20:])/20
                item['volume_x'] = rows[-1].volume/average if average else None
                item.update(status='MATCH' if tags else 'NO_SETUP',expected_session=expected.isoformat())
                if tags and item['price'] >= min_price and item['volume'] >= min_volume:
                    hits.append(item)
                checked.append(item)
            else:
                item = entry(bars, symbol, now, cfg, calendar=cal, source=data.source, **kwargs)
                item['provider_attempts'] = getattr(data, 'attempts', {}).get((symbol, '5m'), [])
                checked.append(item)
                if item['status'] == 'no-data':
                    raise ValueError(item['reason'])
                if item['status'] == 'triggered' and (not eligible_only or item['eligible']) and item['price'] >= min_price and sum(b.volume for b in bars if b.time.date() == now.date()) >= min_volume:
                    hits.append(dict(symbol=symbol, setups=['orb'], **{k: v for k, v in item.items() if k != 'symbol'}))
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            interval = '1d' if mode == 'daily' else '5m'
            diagnostic = dict(symbol=symbol, error=str(exc),
                              provider_attempts=getattr(data, 'attempts', {}).get((symbol, interval), []))
            if mode == 'orb' and stage.get(symbol, {}).get('missing_bars'):
                diagnostic['missing_bars'] = stage[symbol]['missing_bars']
                diagnostic['source'] = stage[symbol]['source']
            failed.append(diagnostic)
            if mode == 'daily':
                checked.append(dict(symbol=symbol,status='FAILED',reason=str(exc)))
    used_sources = sorted({row['source'] for row in checked if row.get('source')})
    hits.sort(key=lambda r: (-(r['price']*r['volume'] if mode == 'daily' else stage[r['symbol']]['turnover']), r['symbol']))
    scanned = len(symbols)-len(failed)-len(stale)-len(unavailable)-len(skipped)
    counts = dict(requested=len(symbols), scanned=scanned, fetched=len(symbols)-len(failed), failed=len(failed),
                  stale=len(stale), unavailable=len(unavailable), candidates=len(candidates),
                  confirmed=sum(r.get('status') == 'triggered' for r in checked),
                  skipped=len(skipped), eligible=sum(bool(r.get('eligible')) for r in checked), hits=len(hits))
    return envelope('scanner', now, mode=mode, as_of=now.isoformat(), source=used_sources[0] if len(used_sources) == 1 else 'See per-symbol sources',
                    universe=len(symbols), scanned=scanned, failed=failed,
                    stale=stale,unavailable=unavailable,
                    hits=hits, results=checked, counts=counts,
                    skipped=[dict(symbol=s, reason='confirmation cap') for s in skipped],
                    fetch=getattr(data, 'fetch_metrics', {}),
                    eligible_hits=[r for r in hits if r.get('eligible')],
                    note='Historical trigger hits are separate from current eligible entries; confirmation caps leave explicit unreviewed candidates.')


def rounded(value, tick, direction=ROUND_HALF_UP):
    if direction in (ROUND_CEILING, ROUND_FLOOR):
        return tick_price(value,tick,direction == ROUND_CEILING)
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
    minutes = 1 if info.get('interval') == '1m' else 5
    if at.second or at.microsecond or (at.hour*60+at.minute-9*60-15) % minutes:
        raise ValueError('Flat-by must align with the candle interval')
    confirmation = signal+dt.timedelta(minutes=minutes) if signal else None
    if not dt.time(9, 15) < at <= dt.time(15, 15) or (confirmation and confirmation.time() >= at):
        raise ValueError('Flat-by must follow entry and precede 15:15 IST')
    target_price = rounded(float(computed['target']), tick, ROUND_CEILING)
    risk_per_share = e-float(computed['stop'])
    return envelope('exit', now, symbol=symbol, side='buy', date=day.isoformat(), setup=info.get('setup', 'orb'),
                    entry=e, entry_time=confirmation.strftime('%H:%M') if confirmation else None,
                    signal_time=signal.isoformat() if signal else None,
                    entry_bar=info.get('bar_index'), interval=info.get('interval', '5m'),
                    opening_range=opening_range or None, level=info.get('level'), min_volume_x=info.get('min_volume_x'),
                    volume_basis=info.get('volume_basis', 'same-time'), stop=float(computed['stop']),
                    stop_rule=stop_rule, stop_spec=stop, stop_label=computed['stop_label'],
                    target=target_price, target_spec=target, target_r=(target_price-e)/risk_per_share,
                    risk_per_share=risk_per_share, reward_risk=(target_price-e)/risk_per_share,
                    flat_by=flat, atr=atr, atr_bars=atr_n, tick=tick,
                    tick_verification=verify_tick(symbol, tick, master or {}),
                    source=info.get('source', 'explicit paper inputs'),
                    entry_eligible=info.get('eligible', False), as_of=now.isoformat())


def historical_replay(days, current_plan, cfg, calendar=None):
    """Configured-rule adapter; signals stay separate from canonical fills."""
    trades = []
    symbol, tick = current_plan['symbol'], current_plan['tick']
    if current_plan.get('interval', '5m') != '5m':
        raise ValueError('Historical strategy replay requires five-minute candles')
    available = min(cfg['budget']*(1-cfg['cash_buffer_pct']/100),
                    cfg['budget']*cfg['max_position_pct']/100, cfg['max_order_inr'])
    prior = []
    for day, rows in days:
        validate(rows)
        for index in range(len(rows)-1):
            now = rows[index].time+dt.timedelta(minutes=5)
            candidate = entry(prior+rows[:index+1], symbol, now, cfg, setup=current_plan['setup'],
                              level=current_plan.get('level'), or_minutes=(current_plan.get('opening_range') or {}).get('minutes', 15),
                              min_volume_x=current_plan.get('min_volume_x'), calendar=calendar,
                              volume_basis=current_plan.get('volume_basis', 'same-time'))
            if not candidate['eligible']:
                continue
            p = plan(candidate, now, tick, cfg, current_plan['stop_spec'], current_plan['target_spec'],
                     rows[:index+1], calendar, flat_by=current_plan.get('flat_by'))
            trade = replay_trade(p, rows[index+1:], available, cfg['max_loss_per_trade_inr'],
                                 exchange_of(symbol), cfg.get('costs'), cfg['min_reward_risk'],
                                 cfg.get('entry_buffer_bps', 5.))
            if trade:
                trades.append(dict(trade, date=day.isoformat()))
            break
        prior.extend(rows)
    return dict(sessions=len(days), triggered=len(trades), trades=trades,
                net_pnl=sum(t['net_pnl'] for t in trades), costs_included=True, entry_rule='next-bar open')


def timemachine(bars, p, now, cfg, calendar=None, sims=10000, seed=19, window=2):
    if not isinstance(sims, int) or isinstance(sims, bool) or not 1 <= sims <= MAX_SIMS:
        raise ValueError('Replays must be 1..'+str(MAX_SIMS))
    if p.get('interval') != '5m':
        raise ValueError('Time Machine requires five-minute candles')
    if isinstance(window, bool) or not isinstance(window, int) or not 0 <= window <= 10:
        raise ValueError('window must be an integer from 0..10 bars')
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
    if not p.get('signal_time') or clock(p['signal_time'])+dt.timedelta(minutes=5) != cutoff or now-cutoff > dt.timedelta(minutes=5):
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
    until = flatten_slot(p)
    if not 1 <= slot < until <= 72:
        doc['reason'] = 'OUTSIDE_HOLDING_WINDOW'
        return doc
    mc = _simulate([rows for _, rows in days], sized, slot, sims, seed, exchange_of(symbol), cfg.get('costs'), window)
    m = upstream('timemachine', cal, exchange_of(symbol))
    doc.update(status='AVAILABLE', reason=None, mc=mc, verdict=m.verdict(mc), verdict_notes=m.verdict_notes(mc))
    return doc
