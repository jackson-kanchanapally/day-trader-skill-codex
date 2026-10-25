"""Prior-session, time-of-day volume comparisons without imputation."""
import datetime as dt
from typing import Optional

from .data import IST, validate
from .market import DEFAULT_CALENDAR

MODES = ('same_time', 'opening_5m', 'opening_15m', 'cumulative')


def relative_volume(bars: list, *, minutes: int = 5, lookback: int = 20,
                    min_samples: int = 5, mode: str = 'same_time',
                    as_of: Optional[dt.datetime] = None, calendar=None, exchange='NSE') -> dict:
    if minutes not in (1, 5) or mode not in MODES:
        raise ValueError('RVOL requires 1m/5m candles and a supported comparison mode')
    if (any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in (lookback, min_samples))
            or min_samples > lookback):
        raise ValueError('RVOL needs 1 <= min_samples <= lookback')
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError('RVOL cutoff requires a timezone')
    step = dt.timedelta(minutes=minutes)
    rows = [b for b in bars if as_of is None or b.time+step <= as_of]
    empty = dict(value=None, samples=0, lookback=lookback, min_samples=min_samples,
                 mode=mode, baseline_volume=None, current_volume=None, excluded_sessions=[],
                 reason='NO_COMPLETED_DATA')
    if not rows:
        return empty
    validate(rows)
    cal = calendar or DEFAULT_CALENDAR
    rows = [b for b in rows if dt.time(9, 15) <= b.time.astimezone(IST).time() < dt.time(15, 15)
            and cal.trading_day(b.time.astimezone(IST).date(), exchange)]
    if not rows:
        return empty
    latest = rows[-1].time.astimezone(IST)
    opening = latest.replace(hour=9, minute=15, second=0, microsecond=0)
    if any((b.time.astimezone(IST)-b.time.astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0)).total_seconds() % (minutes*60) for b in rows):
        raise ValueError('RVOL candles are off-grid')
    if mode == 'same_time':
        slots = [latest.time()]
    else:
        duration = 5 if mode == 'opening_5m' else 15 if mode == 'opening_15m' else int((latest-opening).total_seconds()/60)+minutes
        slots = [(opening+dt.timedelta(minutes=i)).time() for i in range(0, duration, minutes)]
    grouped = {}
    for bar in rows:
        when = bar.time.astimezone(IST)
        grouped.setdefault(when.date(), {})[when.time()] = bar.volume
    today = grouped[latest.date()]
    if any(slot not in today for slot in slots):
        return dict(empty, reason='INCOMPLETE_CURRENT_WINDOW')
    current = sum(today[slot] for slot in slots)
    # Lookback means prior *scheduled sessions*, not the last N available files.
    days, day = [], latest.date()-dt.timedelta(days=1)
    while len(days) < lookback:
        if str(day.year) not in cal.document.get(exchange, {}):
            break
        if cal.trading_day(day, exchange):
            days.append(day)
        day -= dt.timedelta(days=1)
    samples, excluded = [], []
    for day in days:
        history = grouped.get(day, {})
        if any(slot not in history for slot in slots):
            excluded.append(day.isoformat())
        else:
            samples.append(sum(history[slot] for slot in slots))
    mean = sum(samples)/len(samples) if samples else None
    reason = 'INSUFFICIENT_SAMPLES' if len(samples) < min_samples else 'ZERO_BASELINE' if not mean else None
    return dict(empty, value=current/mean if reason is None else None,
                samples=len(samples), baseline_volume=mean, current_volume=current,
                excluded_sessions=excluded, reason=reason,
                as_of=(latest+step).isoformat(), expected_sessions=len(days))


def volume_profile(bars: list, **kwargs) -> dict:
    return {mode: relative_volume(bars, mode=mode, **kwargs) for mode in MODES}
