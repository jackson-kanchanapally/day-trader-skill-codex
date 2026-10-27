"""Foreground entry monitoring and fast, causal replay of authorized bars."""
import datetime as dt
import math

from .common import clock, envelope


def watch(provider, evaluate, start, *, every=30, max_checks=120, max_errors=5,
          replay=False, emit=lambda event: None, sleep=None, now_fn=clock,
          minutes=5, cutoff=dt.time(15, 0)):
    """Callbacks keep clocks, source reads and trigger-file writes explicit.

    Replay advances a local clock and never sleeps or fetches future online
    data. Transient source failures retry; only newly completed candles emit
    bar events. This loop cannot approve, fill or submit an order.
    """
    import time
    sleep = sleep or time.sleep
    if not math.isfinite(every) or every <= 0:
        raise ValueError('every must be positive and finite')
    if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10000
           for v in (max_checks, max_errors)):
        raise ValueError('Watch checks/errors must be integers from 1..10000')
    current, errors, last_seen, result = start, 0, None, None
    for index in range(max_checks):
        if current.date() != start.date() or current.time() > cutoff:
            return dict(result or envelope('entry', current, eligible=False), status='expired',
                        watch_stop_reason='cutoff'), 1
        first_end = current.replace(hour=9, minute=15, second=0, microsecond=0)+dt.timedelta(minutes=minutes)
        if current < first_end:
            emit(envelope('entry', current, event='status', status='waiting', reason='Waiting for first completed candle'))
            if replay:
                current = min(first_end, current+dt.timedelta(seconds=every))
            else:
                sleep(every)
                current = now_fn()
            continue
        try:
            bars, source, attempts = provider(current)
            result = evaluate(bars, current, source)
            result['provider_attempts'] = attempts
            if result.get('status') == 'no-data':
                raise ValueError(result.get('reason', 'Missing session bars'))
            errors = 0
            completed = [bar for bar in bars if bar.time.date() == current.date()
                         and bar.time+dt.timedelta(minutes=minutes) <= current]
            fresh = completed[-1:] if last_seen is None else [b for b in completed if b.time > last_seen]
            for bar in fresh:
                emit(envelope('entry', current, event='bar', bar_start=bar.time.isoformat(),
                              close=bar.close, symbol=result['symbol']))
                last_seen = bar.time
            if result.get('status') == 'triggered':
                result['watch_stop_reason'] = 'trigger' if result.get('eligible') else 'expired_trigger'
                return result, 0 if result.get('eligible') else 1
            if result.get('status') == 'expired':
                result['watch_stop_reason'] = 'expired'
                return result, 1
            emit(dict(result, event='status'))
        except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
            errors += 1
            emit(envelope('entry', current, event='read_error', reason=str(error), consecutive_errors=errors))
            if errors >= max_errors:
                return envelope('entry', current, status='no-data', eligible=False,
                                reason=str(error), watch_stop_reason='read_error_limit'), 1
        if index+1 < max_checks:
            if replay:
                current += dt.timedelta(seconds=every)
            else:
                sleep(every)
                current = now_fn()
    return dict(result or envelope('entry', current, status='no-data', eligible=False),
                watch_stop_reason='check_limit'), 1
