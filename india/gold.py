"""MCX GOLD PETAL swing setups from free data, sent to Telegram (research only).

    python3 -m india.gold --config config/picks.json --dry-run       # today's view, printed
    python3 -m india.gold --config config/picks.json --calibrate 12450  # GOLDPETAL LTP from your broker app
    python3 -m india.gold --config config/picks.json --backtest       # ~20 years of the same rules

MCX does not publish a free price API, and Yahoo carries no MCX contracts. MCX
gold is priced off international gold and the rupee, so this module builds a
rupee-per-gram proxy from two free Yahoo series:

    proxy (INR/g) = COMEX gold (GC=F, USD/oz) x USD/INR / 31.1035 x basis

``basis`` covers import duty and the domestic premium or discount. It
defaults to 1.06 and is replaced by ``--calibrate PRICE``, which divides a
GOLDPETAL price you read off your broker app by the proxy at that moment.

Signals use COMPLETED daily bars (a COMEX day ends 17:00 New York, before the
08:30 IST alert) with a weekly-trend filter, and two swing setups:

    PULLBACK  daily and weekly trend agree, price dips to EMA20 and closes back
              in the trend direction
    BREAKOUT  close beyond the prior 20-session high/low with ADX >= 20, weekly
              trend not against it

Scores (out of 100) weigh weekly alignment, daily structure, momentum, ADX,
setup quality and macro context (dollar index and US 10-year yield, both
from Yahoo). Plans are swing trades: an SL-order trigger beyond the signal
bar, a structure/ATR stop, 1.5R and 3R targets and a 10-session time stop,
sized in GOLDPETAL lots (1 g) to the configured rupee risk with MCX cost
estimates. Every alert is paper-traded on the proxy's daily bars.

This is rule-based research output, not investment advice. Nothing here
places orders.
"""
import argparse
import bisect
import datetime as dt
import html
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .data import IST, Bar
from . import alert_switches as S
from . import picks as P

TROY_OUNCE_G = 31.1034768
GOLD, FX, DXY, US10Y = 'GC=F', 'USDINR=X', 'DX-Y.NYB', '^TNX'
UTC = dt.timezone.utc
# A COMEX daily bar is labelled with its New York trade date and ends at 17:00
# New York, which is 21:00 UTC in summer and 22:00 UTC in winter. 22:00 UTC is
# safe all year and still well before the 08:30 IST (03:00 UTC) alert.
COMEX_DAY_END_UTC = dt.time(22, 0)
MIN_BARS = 210
OPEN_STATES = ('PENDING', 'OPEN')

# FOMC decision days (second day of each meeting), from the Federal Reserve's
# published 2026 schedule. Add later years, CPI or payrolls under "event_dates".
FOMC_2026 = ('2026-01-28', '2026-03-18', '2026-04-29', '2026-06-17',
             '2026-07-29', '2026-09-16', '2026-10-28', '2026-12-09')

COST_DEFAULTS = {
    # Estimates for MCX non-agri futures at a discount broker. Check your broker.
    'brokerage_per_order': 20.0,     # INR per executed order
    'ctt_sell': 0.0001,              # commodity transaction tax, 0.01% of sell value
    'exchange_rate': 0.000021,       # MCX transaction charge, both sides
    'sebi': 0.000001,                # SEBI fee, INR 10 per crore, both sides
    'stamp_buy': 0.00002,            # stamp duty, 0.002% of buy value
    'gst': 0.18,                     # on brokerage, exchange and SEBI fees
    'slippage_ticks': 1.0,           # per side
}
DEFAULTS = {
    'enabled': False,                # --serve sends the morning gold alert when true
    'state_dir': 'var/state/gold',
    'alert_time': '08:30',           # IST, Monday-Friday; before MCX opens at 09:00
    'capital': 100000,               # INR; notional is capped at capital x max_leverage
    'risk_per_trade': 1000,          # INR loss at the stop, including estimated costs
    'max_leverage': 1.0,             # 1.0 = no leverage beyond the capital
    'max_lots': 100,
    'min_score': 60,
    'allow_short': False,            # shorts lost money in every backtest variant (docs/GOLD_ALERTS.md)
    'contract': 'GOLDPETAL',
    'lot_grams': 1,                  # GOLDPETAL: 1 gram per lot, quoted per gram
    'tick': 1.0,                     # INR per gram
    'margin_pct': 10.0,              # estimated initial margin, % of notional
    'basis': 1.15,                   # fallback when there is no fresh calibration and no MCX quote in the news
    'calibration_max_days': 7,       # warn when the calibration is older than this
    'entry_valid_sessions': 3,
    'max_hold_sessions': 40,
    'target1_r': 1.0,                # book half here and move the stop to the entry
    'target2_r': 3.0,                # only used when trail_atr is 0
    'trail_atr': 2.0,                # after T1, trail the rest by this many ATRs; 0 = fixed T2 instead
    'event_dates': [],               # extra [{"date": "YYYY-MM-DD", "name": "US CPI"}]
    'costs': {},                     # overrides for COST_DEFAULTS
    'live_watch': True,              # --serve: Telegram alert when a paper trade's level is crossed intraday
    'watch_minutes': 15,             # live-watch cadence during MCX hours
    'mcx_hours': ['09:00', '23:30'], # IST; live watch runs inside these hours, Monday-Friday
    'commands': True,                # --serve answers /gold /news /price /trades /calibrate /help from the alert chat
    'news': 'flag',                  # flag | skip | off: news, positioning and risk mood (india.gold_news)
}


def settings(raw):
    """Validated gold settings. None (no "gold" object in the config) means disabled defaults."""
    cfg = dict(DEFAULTS)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError('gold must be an object')
    unknown = set(raw) - set(DEFAULTS) - {'_comment'}
    if unknown:
        raise ValueError('Unknown gold config keys: ' + ', '.join(sorted(unknown)))
    cfg.update({k: v for k, v in raw.items() if k != '_comment'})

    def number(key, low, high):
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('gold.%s must be a number from %s to %s' % (key, low, high))

    for key in ('enabled', 'allow_short', 'live_watch', 'commands'):
        if not isinstance(cfg[key], bool):
            raise ValueError('gold.%s must be true or false' % key)
    P.hhmm(cfg['alert_time'])
    number('capital', 1000, 1e10)
    number('risk_per_trade', 10, 1e9)
    number('max_leverage', 0.1, 20)
    number('min_score', 0, 100)
    number('tick', 0.01, 1000)
    number('margin_pct', 1, 100)
    number('basis', 0.9, 1.3)
    number('target1_r', 0.5, 10)
    number('target2_r', 0.5, 20)
    number('trail_atr', 0, 10)
    if cfg['target2_r'] <= cfg['target1_r']:
        raise ValueError('gold.target2_r must be above gold.target1_r')
    hours = cfg['mcx_hours']
    if not isinstance(hours, list) or len(hours) != 2 or P.hhmm(hours[0]) >= P.hhmm(hours[1]):
        raise ValueError('gold.mcx_hours must be ["HH:MM", "HH:MM"] with the open before the close')
    for key, low, high in (('watch_minutes', 5, 60), ('max_lots', 1, 100000), ('lot_grams', 1, 1000), ('calibration_max_days', 1, 365),
                           ('entry_valid_sessions', 1, 10), ('max_hold_sessions', 2, 60)):
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise ValueError('gold.%s must be a whole number from %d to %d' % (key, low, high))
    if cfg['news'] not in ('flag', 'skip', 'off'):
        raise ValueError('gold.news must be "flag", "skip" or "off"')
    if not isinstance(cfg['contract'], str) or not cfg['contract'].strip():
        raise ValueError('gold.contract must be a name such as GOLDPETAL')
    events = []
    for item in cfg['event_dates']:
        if not isinstance(item, dict) or set(item) - {'date', 'name'}:
            raise ValueError('gold.event_dates entries are {"date": "YYYY-MM-DD", "name": "..."}')
        dt.date.fromisoformat(item['date'])
        events.append({'date': item['date'], 'name': str(item.get('name') or 'Event')})
    cfg['event_dates'] = events
    if not isinstance(cfg['costs'], dict) or set(cfg['costs']) - set(COST_DEFAULTS):
        raise ValueError('gold.costs keys: ' + ', '.join(sorted(COST_DEFAULTS)))
    costs_cfg = dict(COST_DEFAULTS, **cfg['costs'])
    for k, v in costs_cfg.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError('gold.costs.%s must be a nonnegative number' % k)
    cfg['costs'] = costs_cfg
    return cfg


# --------------------------------------------------------------------------- data

def parse_daily(doc, symbol):
    """Daily bars labelled with the exchange's own trade date (midnight UTC of that date)."""
    results = (doc.get('chart') or {}).get('result')
    if not results:
        raise ValueError('no chart')
    r = results[0]
    meta = r.get('meta') or {}
    if meta.get('symbol', '').upper() != symbol.upper():
        raise ValueError('symbol mismatch')
    offset = int(meta.get('gmtoffset') or 0)
    q = (r.get('indicators') or {}).get('quote', [{}])[0]
    out = {}
    for i, t in enumerate(r.get('timestamp') or []):
        try:
            o, h, l, c = (q[k][i] for k in ('open', 'high', 'low', 'close'))
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c):
            continue        # holidays and blank rows: skipped, never invented
        o, h, l, c = float(o), float(h), float(l), float(c)
        if min(o, h, l, c) <= 0 or l > min(o, c) * 1.0001 or h < max(o, c) * 0.9999:
            continue
        day = dt.datetime.fromtimestamp(t + offset, UTC).date()
        out[day] = Bar(dt.datetime.combine(day, dt.time(), UTC), o, h, l, c, 0.0)  # a late row replaces an earlier one
    return [out[d] for d in sorted(out)]


def fetch_daily(symbol, start, now=None):
    """Yahoo daily bars from ``start`` (a date). period1/period2 keeps daily granularity for long ranges."""
    p1 = int(dt.datetime.combine(start, dt.time(), UTC).timestamp())
    p2 = int((now or dt.datetime.now(UTC)).timestamp()) + 86400
    path = '/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d' % (urllib.parse.quote(symbol, safe=''), p1, p2)
    last = 'unavailable'
    for host in ('query2', 'query1'):
        for attempt in range(2):
            try:
                return parse_daily(json.loads(P.http_get('https://%s.finance.yahoo.com%s' % (host, path))), symbol)
            except urllib.error.HTTPError as exc:
                last = 'HTTP %d' % exc.code
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                last = str(exc) or type(exc).__name__
            if attempt == 0:
                time.sleep(1.5)
    raise ValueError('Yahoo %s: %s' % (symbol, last))


def completed(bars, now):
    """COMEX bars whose trade date has ended (17:00 New York) by ``now``."""
    now = now.astimezone(UTC)
    return [b for b in bars if now >= dt.datetime.combine(b.time.date(), COMEX_DAY_END_UTC, UTC)]


def proxy(gold, fx, basis):
    """INR/gram bars: each COMEX bar x the latest USD/INR close on or before its date x basis.

    Returns (bars, refs) where refs[i] = (COMEX close, USD/INR used). A bar with
    no FX quote in the five days before it is dropped rather than guessed."""
    fx_days = [b.time.date() for b in fx]
    bars, refs = [], []
    for b in gold:
        day = b.time.date()
        j = bisect.bisect_right(fx_days, day) - 1
        if j < 0 or (day - fx_days[j]).days > 5:
            continue
        rate = fx[j].close
        k = rate / TROY_OUNCE_G * basis
        bars.append(Bar(b.time, b.open * k, b.high * k, b.low * k, b.close * k, 0.0))
        refs.append((b.close, rate))
    return bars, refs


class Lookup:
    """Causal 'latest value on or before a date' over a daily series, with an N-bar change."""

    def __init__(self, bars, back=20):
        self.days = [b.time.date() for b in bars]
        self.close = [b.close for b in bars]
        self.back = back

    def at(self, day):
        j = bisect.bisect_right(self.days, day) - 1
        if j < self.back or (day - self.days[j]).days > 7:
            return None
        return {'close': self.close[j], 'change': self.close[j] - self.close[j - self.back],
                'change_pct': (self.close[j] / self.close[j - self.back] - 1) * 100}


# --------------------------------------------------------------------------- indicators

def adx_series(bars, n=14):
    """Wilder ADX, +DI and -DI; None until ready (ADX needs 2n bars)."""
    size = len(bars)
    adx, pdi, mdi = [None] * size, [None] * size, [None] * size
    if size < 2 * n + 1:
        return adx, pdi, mdi
    tr, pdm, mdm = [], [], []
    for p, b in zip(bars, bars[1:]):
        up, down = b.high - p.high, p.low - b.low
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)))
    s_tr, s_p, s_m = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n])
    dx = []
    for i in range(n, len(tr) + 1):
        if i > n:
            s_tr += tr[i - 1] - s_tr / n
            s_p += pdm[i - 1] - s_p / n
            s_m += mdm[i - 1] - s_m / n
        p_ = 100 * s_p / s_tr if s_tr else 0.0
        m_ = 100 * s_m / s_tr if s_tr else 0.0
        pdi[i], mdi[i] = p_, m_
        dx.append(100 * abs(p_ - m_) / (p_ + m_) if p_ + m_ else 0.0)
    a = None
    for j, v in enumerate(dx):
        if j == n - 1:
            a = sum(dx[:n]) / n
        elif j >= n:
            a = (a * (n - 1) + v) / n
        if a is not None:
            adx[n + j] = a
    return adx, pdi, mdi


def weekly_series(bars, n=10, slope=3):
    """Per day: (trend +1/0/-1, weekly EMA) from completed weeks plus the current partial week.

    Up: close above the 10-week EMA and that EMA higher than 3 completed weeks ago."""
    k = 2.0 / (n + 1)
    closes, emas, ema, out, prev_key = [], [], None, [], None
    for i, b in enumerate(bars):
        key = b.time.date().isocalendar()[:2]
        if prev_key is not None and key != prev_key:
            closes.append(bars[i - 1].close)
            if len(closes) == n:
                ema = sum(closes) / n
            elif len(closes) > n:
                ema = closes[-1] * k + ema * (1 - k)
            emas.append(ema)
        prev_key = key
        if ema is None or len(emas) <= slope or emas[-1 - slope] is None:
            out.append((0, None))
            continue
        current = b.close * k + ema * (1 - k)
        rising = emas[-1] > emas[-1 - slope]
        if b.close > current and rising:
            out.append((1, current))
        elif b.close < current and not rising:
            out.append((-1, current))
        else:
            out.append((0, current))
    return out


def series(bars):
    """All indicator series; every value at index i uses bars[:i + 1] only."""
    s = P.series(bars)
    s['adx'], s['plus_di'], s['minus_di'] = adx_series(bars)
    s['weekly'] = weekly_series(bars)
    return s


def features(bars, s, i, refs=None, dxy=None, us10y=None):
    if i + 1 < MIN_BARS:
        raise ValueError('needs at least %d completed sessions, got %d' % (MIN_BARS, i + 1))
    b, prev = bars[i], bars[i - 1]
    atr = s['atr'][i]
    rng = b.high - b.low
    day = b.time.date()
    wk, wema = s['weekly'][i]
    d = dxy.at(day) if dxy else None
    y = us10y.at(day) if us10y else None
    return {
        'date': day.isoformat(),
        'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close,
        'prev_close': prev.close, 'prev_high': prev.high, 'prev_low': prev.low,
        'change_pct': (b.close / prev.close - 1) * 100,
        'ema20': s['ema20'][i], 'ema50': s['ema50'][i], 'ema200': s['ema200'][i],
        'ema50_prev5': s['ema50'][i - 5], 'ema200_prev10': s['ema200'][i - 10],
        'rsi': s['rsi'][i], 'rsi_min5': min(s['rsi'][i - 4:i + 1]), 'rsi_max5': max(s['rsi'][i - 4:i + 1]),
        'macd': s['macd'][i], 'macd_signal': s['signal'][i],
        'macd_hist': s['hist'][i], 'macd_hist_prev': s['hist'][i - 1],
        'atr': atr, 'atr_pct': atr / b.close * 100,
        'adx': s['adx'][i] or 0.0, 'plus_di': s['plus_di'][i] or 0.0, 'minus_di': s['minus_di'][i] or 0.0,
        'high20': max(x.high for x in bars[i - 20:i]), 'low20': min(x.low for x in bars[i - 20:i]),
        'high3': max(x.high for x in bars[i - 2:i + 1]), 'low3': min(x.low for x in bars[i - 2:i + 1]),
        'swing_high5': max(x.high for x in bars[i - 4:i + 1]), 'swing_low5': min(x.low for x in bars[i - 4:i + 1]),
        'close_loc': (b.close - b.low) / rng if rng > 0 else 0.5,
        'true_range': max(rng, abs(b.high - prev.close), abs(b.low - prev.close)),
        'weekly': wk, 'weekly_ema': wema,
        'dxy': d['close'] if d else None, 'dxy_change_pct': d['change_pct'] if d else None,
        'us10y': y['close'] if y else None, 'us10y_change': y['change'] if y else None,
        'comex': refs[i][0] if refs else None, 'usdinr': refs[i][1] if refs else None,
    }


# --------------------------------------------------------------------------- signals

def _setup(f, sign):
    """('BREAKOUT' | 'PULLBACK' | None, reason) for one direction."""
    c, e20, e50, atr = f['close'], f['ema20'], f['ema50'], f['atr']
    loc = f['close_loc'] if sign > 0 else 1 - f['close_loc']
    rsi = f['rsi'] if sign > 0 else 100 - f['rsi']
    weekly = f['weekly'] * sign
    ext = sign * (c - e20) / atr
    level = f['high20'] if sign > 0 else f['low20']
    if sign * (c - level) > 0:
        if weekly < 0:
            return None, 'breakout against the weekly trend'
        if f['adx'] < 20:
            return None, 'breakout without trend strength (ADX < 20)'
        if loc < 0.6:
            return None, 'breakout bar closed weak'
        if rsi > 78 or ext > 3:
            return None, 'breakout too stretched'
        if sign * (c - e50) <= 0:
            return None, 'breakout below EMA50'
        return 'BREAKOUT', None
    trend = sign * (e20 - e50) > 0 and sign * (c - e50) > 0 and sign * (e50 - f['ema50_prev5']) > 0
    if not trend:
        return None, 'no daily trend'
    if weekly <= 0:
        return None, 'weekly trend does not agree'
    touched = f['low3'] <= e20 + 0.25 * atr if sign > 0 else f['high3'] >= e20 - 0.25 * atr
    if not touched:
        return None, 'no pullback to EMA20'
    if not (sign * (c - e20) > 0 and sign * (c - f['open']) > 0 and loc >= 0.5):
        return None, 'pullback not yet turning'
    if not 42 <= rsi <= 70:
        return None, 'RSI outside the pullback band'
    if ext > 1.2:
        return None, 'too far from EMA20'
    return 'PULLBACK', None


def score(f, setup, sign):
    """Points out of 100 and the reasons behind them."""
    c, e20, e50, e200 = f['close'], f['ema20'], f['ema50'], f['ema200']
    parts, why = {}, []
    word = 'up' if sign > 0 else 'down'
    weekly = f['weekly'] * sign
    parts['weekly'] = 20 if weekly > 0 else 8 if weekly == 0 else 0
    why.append('weekly trend %s' % (word if weekly > 0 else 'flat' if weekly == 0 else 'against'))
    st = 0
    if sign * (e20 - e50) > 0:
        st += 6
        why.append('EMA20 %s EMA50' % ('>' if sign > 0 else '<'))
    if e200 is not None and sign * (c - e200) > 0:
        st += 6
        why.append('%s EMA200' % ('above' if sign > 0 else 'below'))
    if sign * (e50 - f['ema50_prev5']) > 0:
        st += 4
    if e200 is not None and f['ema200_prev10'] is not None and sign * (e200 - f['ema200_prev10']) > 0:
        st += 4
    parts['structure'] = st
    m = 0
    if sign * (f['macd_hist'] - f['macd_hist_prev']) > 0:
        m += 5
        why.append('MACD histogram turning %s' % word)
    if sign * (f['macd'] - f['macd_signal']) > 0:
        m += 5
    r = f['rsi'] if sign > 0 else 100 - f['rsi']
    m += 5 if 50 <= r <= 65 else 2.5 if 45 <= r <= 70 else 0
    why.append('RSI %.0f' % f['rsi'])
    parts['momentum'] = m
    a = 15 * min(1.0, max(0.0, (f['adx'] - 15) / 15))
    if sign * (f['plus_di'] - f['minus_di']) <= 0:
        a *= 0.5
    parts['adx'] = a
    why.append('ADX %.0f' % f['adx'])
    loc = f['close_loc'] if sign > 0 else 1 - f['close_loc']
    q = 5 if loc >= 0.75 else 3 if loc >= 0.6 else 0
    if setup == 'BREAKOUT':
        level = f['high20'] if sign > 0 else f['low20']
        margin = sign * (c - level) / f['atr']
        q += 5 if 0.1 <= margin <= 1.0 else 2
        if f['true_range'] > f['atr']:
            q += 5
            why.append('range expansion')
        why.insert(0, 'closed beyond the 20-day %s' % ('high' if sign > 0 else 'low'))
    else:
        if sign * (c - (f['prev_high'] if sign > 0 else f['prev_low'])) > 0:
            q += 5
            why.append("closed beyond the prior day's %s" % ('high' if sign > 0 else 'low'))
        if (f['rsi_min5'] <= 50) if sign > 0 else (f['rsi_max5'] >= 50):
            q += 5
            why.append('RSI reset to 50 and turned')
        why.insert(0, 'pulled back to EMA20 and turned %s' % word)
    parts['setup'] = q
    mac = 0.0
    if f['dxy_change_pct'] is None:
        mac += 4
    else:
        tail = -sign * f['dxy_change_pct']
        mac += 8 if tail > 0.5 else 4 if tail >= -0.5 else 0
        if tail > 0.5:
            why.append('dollar index %s %.1f%% in 20 sessions' % ('down' if sign > 0 else 'up', abs(f['dxy_change_pct'])))
    if f['us10y_change'] is None:
        mac += 3.5
    else:
        tail = -sign * f['us10y_change']
        mac += 7 if tail > 0.10 else 3.5 if tail >= -0.10 else 0
        if tail > 0.10:
            why.append('US 10Y yield %s %.2f pts' % ('down' if sign > 0 else 'up', abs(f['us10y_change'])))
    parts['macro'] = mac
    return round(sum(parts.values()), 1), {k: round(v, 1) for k, v in parts.items()}, why


def evaluate(f, cfg):
    """(candidate or None, reject reason or None)."""
    reasons = []
    for side, sign in (('BUY', 1), ('SELL', -1)):
        if sign < 0 and not cfg['allow_short']:
            continue
        setup, reason = _setup(f, sign)
        if setup:
            total, parts, why = score(f, setup, sign)
            return {'side': side, 'setup': setup, 'score': total, 'components': parts, 'reasons': why}, None
        reasons.append('%s: %s' % (side.lower(), reason))
    return None, '; '.join(reasons)


# --------------------------------------------------------------------------- plan

def trade_costs(buy, sell, lots, cfg):
    """Estimated INR costs for one round trip of ``lots`` (buy and sell legs plus slippage)."""
    if lots <= 0:
        return 0.0
    c, g = cfg['costs'], cfg['lot_grams']
    b, s = buy * g * lots, sell * g * lots
    broker = 2 * c['brokerage_per_order']
    fees = (b + s) * (c['exchange_rate'] + c['sebi'])
    return (broker + fees + s * c['ctt_sell'] + b * c['stamp_buy'] + (broker + fees) * c['gst']
            + 2 * c['slippage_ticks'] * cfg['tick'] * g * lots)


def _legs(sign, entry, exit_price, lots, cfg):
    buy, sell = (entry, exit_price) if sign > 0 else (exit_price, entry)
    gross = sign * (exit_price - entry) * lots * cfg['lot_grams']
    return gross, trade_costs(buy, sell, lots, cfg)


def build_plan(f, cand, cfg, basis):
    """Swing plan in INR/gram on the proxy scale. None if one lot already exceeds the risk budget."""
    side, atr, tick = cand['side'], f['atr'], cfg['tick']
    sign = 1 if side == 'BUY' else -1
    up, down = ('up', 'down') if sign > 0 else ('down', 'up')
    entry = P.to_tick((f['high'] if sign > 0 else f['low']) + sign * 0.1 * atr, tick, up)
    swing = f['swing_low5'] - 0.25 * atr if sign > 0 else f['swing_high5'] + 0.25 * atr
    risk = min(max(sign * (entry - swing), 1.0 * atr), 2.5 * atr)
    stop = P.to_tick(entry - sign * risk, tick, down)
    risk = abs(entry - stop)
    t1 = P.to_tick(entry + sign * cfg['target1_r'] * risk, tick, up)
    t2 = None if cfg['trail_atr'] else P.to_tick(entry + sign * cfg['target2_r'] * risk, tick, up)
    chase = P.to_tick(entry + sign * 0.5 * atr, tick, up)
    g = cfg['lot_grams']
    budget = cfg['risk_per_trade']
    lots = min(int(budget // (risk * g)), int(cfg['capital'] * cfg['max_leverage'] // (entry * g)), cfg['max_lots'])
    while lots > 0:
        gross, cost = _legs(sign, entry, stop, lots, cfg)
        if -gross + cost <= budget:
            break
        lots -= 1
    if lots <= 0:
        return None
    loss_gross, loss_cost = _legs(sign, entry, stop, lots, cfg)
    max_loss = -loss_gross + loss_cost
    half = lots // 2
    plan_net = 0.0
    # Trailing: the rest has no fixed target, so the estimate is the floor (everything out at T1).
    for price, n in ((t1, half), (t2 or t1, lots - half)):
        if n:
            gross, cost = _legs(sign, entry, price, n, cfg)
            plan_net += gross - cost
    close = f['close']

    def pct(x):
        return round((x / close - 1) * 100, 2)
    signal_day = dt.date.fromisoformat(f['date'])
    # Events in the entry window and the first two weeks of the trade, where a gap hurts most.
    sessions = cfg['entry_valid_sessions'] + min(cfg['max_hold_sessions'], 10)
    horizon = signal_day + dt.timedelta(days=int(math.ceil(sessions * 7 / 5)) + 2)
    flags = []
    for ev in upcoming_events(cfg, signal_day, horizon):
        flags.append('%s on %s, early in the trade; expect a volatile session' % (
            ev['name'], dt.date.fromisoformat(ev['date']).strftime('%d %b')))
    if f['atr_pct'] >= 2.0:
        flags.append('High volatility (ATR %.1f%% of price); the stop is wide' % f['atr_pct'])
    r = f['rsi'] if sign > 0 else 100 - f['rsi']
    if r >= 70:
        flags.append('RSI %.0f is stretched; a pullback before follow-through is common' % f['rsi'])
    if f['weekly'] * sign == 0:
        flags.append('Weekly trend is flat; consider half size')
    macro_against = sum(1 for v, th in ((f['dxy_change_pct'], 0.5), (f['us10y_change'], 0.10))
                        if v is not None and sign * v > th)
    if macro_against:
        flags.append('Dollar and/or US yields rising against the trade' if sign > 0 else
                     'Dollar and/or US yields falling against the trade')
    if f['atr_pct'] < 1.2 and not flags:
        rating = 'Low'
    elif f['atr_pct'] < 2.0 and len(flags) <= 1:
        rating = 'Medium'
    else:
        rating = 'High'
    if sign > 0:
        support, resistance = max(f['ema20'], f['swing_low5']), max(f['high20'], f['high'])
    else:
        support, resistance = min(f['low20'], f['low']), min(f['ema20'], f['swing_high5'])
    return {
        'contract': cfg['contract'], 'side': side, 'setup': cand['setup'], 'score': cand['score'],
        'components': cand['components'], 'reasons': cand['reasons'],
        'signal_date': f['date'], 'basis': basis,
        'entry': entry, 'no_chase': chase, 'stop': stop, 'target1': t1, 'target2': t2,
        'entry_pct': pct(entry), 'no_chase_pct': pct(chase), 'stop_pct': pct(stop),
        'target1_pct': pct(t1), 'target2_pct': pct(t2) if t2 else None, 'trail_atr': cfg['trail_atr'],
        'risk_per_gram': round(risk, 2), 'lots': lots, 'grams': lots * g,
        'notional': round(lots * g * entry, 2), 'margin': round(lots * g * entry * cfg['margin_pct'] / 100, 2),
        'max_loss': round(max_loss, 2), 'est_costs_at_stop': round(loss_cost, 2), 'net_plan': round(plan_net, 2),
        'target1_r': cfg['target1_r'], 'target2_r': cfg['target2_r'],
        'entry_valid_sessions': cfg['entry_valid_sessions'], 'max_hold_sessions': cfg['max_hold_sessions'],
        'support': round(support, 2), 'resistance': round(resistance, 2),
        'risk_rating': rating, 'flags': flags,
        'features': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()},
    }


def upcoming_events(cfg, start, end):
    """Built-in FOMC days plus configured events strictly after ``start`` and up to ``end``."""
    found = [{'date': d, 'name': 'FOMC decision'} for d in FOMC_2026] + list(cfg['event_dates'])
    return sorted((e for e in found if start < dt.date.fromisoformat(e['date']) <= end), key=lambda e: e['date'])


# --------------------------------------------------------------------------- paper trade

def simulate(plan, bars, cfg):
    """Walk the plan over the daily proxy bars AFTER its signal date.

    Entry: a stop order at the entry level; an open beyond it fills at the
    open, an open beyond the no-chase level is skipped. Not triggered within
    entry_valid_sessions: expired. Stop-first when a bar touches both the stop
    and a target. T1 books half and moves the stop to the entry. With
    trail_atr set, the stop on the rest then trails the best price since entry
    by trail_atr x the signal-day ATR, updated at each session's close, and
    there is no T2; otherwise T2 exits the rest. Anything open after
    max_hold_sessions exits at that session's close.
    """
    sign = 1 if plan['side'] == 'BUY' else -1
    entry, stop, t1, t2, chase = plan['entry'], plan['stop'], plan['target1'], plan['target2'], plan['no_chase']
    lots, g = plan['lots'], cfg['lot_grams']
    out = {'status': 'PENDING', 'events': [], 'fill': None, 'fill_date': None, 'exit_reason': None,
           'exit_index': None, 'stop': stop, 'sessions_seen': len(bars)}
    legs, remaining, t1_done, k_fill, best = [], lots, False, None, None
    trail = plan.get('trail_atr') or 0
    trail_step = trail * plan['features']['atr']

    def stop_reason():
        if not t1_done:
            return 'STOP'
        return 'TRAIL' if sign * (stop - out['fill']) > 0 else 'BREAKEVEN'

    def fav(b):
        return b.high if sign > 0 else b.low

    def adv(b):
        return b.low if sign > 0 else b.high

    def event(kind, b, price, n):
        out['events'].append({'date': b.time.date().isoformat(), 'kind': kind,
                              'price': round(price, 2) if price is not None else None, 'lots': n})

    def close_out(price, reason, b, k):
        nonlocal remaining
        legs.append((price, remaining))
        event(reason, b, price, remaining)
        remaining = 0
        out.update(status='CLOSED', exit_reason=reason, exit_index=k)

    for k, b in enumerate(bars):
        first = False
        if out['status'] == 'PENDING':
            if k >= cfg['entry_valid_sessions']:
                out.update(status='EXPIRED', exit_index=k - 1)
                event('EXPIRED', bars[k - 1], None, 0)
                break
            if sign * (b.open - chase) > 0:
                out.update(status='SKIPPED', exit_index=k)
                event('GAP_SKIP', b, b.open, 0)
                break
            if sign * (b.open - entry) >= 0:
                fill = b.open
            elif sign * (fav(b) - entry) >= 0:
                fill = entry
            else:
                continue
            out.update(status='OPEN', fill=round(fill, 2), fill_date=b.time.date().isoformat())
            k_fill, first = k, True
            event('TRIGGER', b, fill, lots)
        if out['status'] != 'OPEN':
            continue
        if not first and sign * (b.open - stop) <= 0:
            close_out(b.open, stop_reason(), b, k)
            break
        if sign * (adv(b) - stop) <= 0:
            close_out(stop, stop_reason(), b, k)
            break
        if not t1_done and sign * (fav(b) - t1) >= 0:
            price = b.open if not first and sign * (b.open - t1) > 0 else t1
            half = lots // 2
            if half:
                legs.append((price, half))
                remaining -= half
            event('TARGET1', b, price, half)
            t1_done = True
            stop = out['fill']      # breakeven on the rest
            out['stop'] = stop
        if t1_done and t2 is not None and sign * (fav(b) - t2) >= 0:
            close_out(b.open if not first and sign * (b.open - t2) > 0 else t2, 'TARGET2', b, k)
            break
        if k - k_fill + 1 >= cfg['max_hold_sessions']:
            close_out(b.close, 'TIME', b, k)
            break
        best = fav(b) if best is None else (max(best, b.high) if sign > 0 else min(best, b.low))
        if trail and t1_done and sign * (best - sign * trail_step - stop) > 0:
            stop = P.to_tick(best - sign * trail_step, cfg['tick'], 'down' if sign > 0 else 'up')
            out['stop'] = stop
    fill = out['fill']
    if fill is not None:
        gross = cost = 0.0
        for price, n in legs:
            gr, co = _legs(sign, fill, price, n, cfg)
            gross, cost = gross + gr, cost + co
        if out['status'] == 'OPEN' and bars:
            last = bars[-1].close
            out['last_close'] = round(last, 2)
            out['unrealised'] = round(sign * (last - fill) * remaining * g, 2)
            out['open_r'] = round((gross - cost + out['unrealised']) / plan['max_loss'], 2) if plan['max_loss'] else None
            out['sessions_held'] = len(bars) - k_fill
        out['gross'], out['costs'] = round(gross, 2), round(cost, 2)
        out['net'] = round(gross - cost, 2)
        out['r'] = round((gross - cost) / plan['max_loss'], 2) if out['status'] == 'CLOSED' and plan['max_loss'] else None
        out['t1_hit'] = t1_done
    return out


# --------------------------------------------------------------------------- state

def load_calibration(state_dir):
    path = Path(state_dir) / 'calibration.json'
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text())
        if 0.9 <= float(doc['basis']) <= 1.3:
            return doc
    except (ValueError, KeyError, TypeError):
        pass
    return None


def calibrate(cfg, price, now, fetch_quote=None):
    """Store basis = GOLDPETAL price / proxy at the latest COMEX and USD/INR quotes."""
    if not 1000 <= price <= 100000:
        raise ValueError('Enter the GOLDPETAL price per gram in rupees, e.g. 12450')
    fetch_quote = fetch_quote or latest_quote
    gc, fx = fetch_quote(GOLD), fetch_quote(FX)
    raw = gc['price'] * fx['price'] / TROY_OUNCE_G
    basis = price / raw
    if not 0.9 <= basis <= 1.3:
        raise ValueError('Basis %.4f is implausible (COMEX $%.1f x USD/INR %.2f = INR %.0f/g raw). '
                         'Check that the price is GOLDPETAL per gram.' % (basis, gc['price'], fx['price'], raw))
    doc = {'basis': round(basis, 5), 'mcx_price': price, 'comex': gc['price'], 'usdinr': fx['price'],
           'comex_time': gc['time'], 'usdinr_time': fx['time'], 'calibrated_at': now.isoformat()}
    state = Path(cfg['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    (state / 'calibration.json').write_text(json.dumps(doc, indent=2))
    return doc


def latest_quote(symbol):
    path = '/v8/finance/chart/%s?range=1d&interval=5m' % urllib.parse.quote(symbol, safe='')
    doc = json.loads(P.http_get('https://query2.finance.yahoo.com' + path))
    meta = doc['chart']['result'][0]['meta']
    if meta.get('symbol', '').upper() != symbol.upper() or not meta.get('regularMarketPrice'):
        raise ValueError('no live quote for %s' % symbol)
    return {'price': float(meta['regularMarketPrice']),
            'time': dt.datetime.fromtimestamp(int(meta.get('regularMarketTime') or 0), UTC).isoformat()}


def load_trades(state_dir):
    path = Path(state_dir) / 'trades.json'
    return json.loads(path.read_text()) if path.is_file() else []


def save_trades(state_dir, trades):
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    tmp = state / 'trades.json.tmp'
    tmp.write_text(json.dumps(trades, indent=2, default=str))
    tmp.replace(state / 'trades.json')


def update_trades(trades, gold, fx, cfg):
    """Re-walk every unfinished paper trade on the latest bars. Returns new milestone events per trade."""
    cache, news = {}, []
    for t in trades:
        if t.get('result') and t['result']['status'] not in OPEN_STATES:
            continue
        plan = t['plan']
        basis = plan['basis']
        if basis not in cache:
            cache[basis] = proxy(gold, fx, basis)[0]
        after = [b for b in cache[basis] if b.time.date().isoformat() > plan['signal_date']]
        result = simulate(plan, after, cfg)
        t['result'] = result
        seen = set(t.get('reported') or [])
        fresh = [e for e in result['events'] if '%s:%s' % (e['kind'], e['date']) not in seen]
        if fresh:
            news.append((t, fresh))
    return news


# --------------------------------------------------------------------------- job

def implied_basis(found, fx_bars, now, fetch_bars=None):
    """Median basis implied by MCX prices quoted in headlines, each against the proxy when it was published."""
    gc = (fetch_bars or fetch_intraday)(GOLD, now)
    fx_days = [b.time.date() for b in fx_bars]
    rows = []
    for x in found:
        t = dt.datetime.fromisoformat(x['published'])
        before = [b for b in gc if b.time + dt.timedelta(minutes=15) <= t]
        j = bisect.bisect_right(fx_days, t.date()) - 1
        if not before or j < 0:
            continue
        raw = before[-1].close * fx_bars[j].close / TROY_OUNCE_G
        basis = x['per_gram'] / raw
        if 0.95 <= basis <= 1.35:
            rows.append(dict(x, basis=round(basis, 4)))
    if not rows:
        return None
    import statistics
    return {'basis': round(statistics.median(r['basis'] for r in rows), 4), 'quotes': rows}


def gather_context(cfg, now, fx_bars, warnings, fetch=None, fetch_bars=None, log=lambda msg: None):
    """News sentiment, MCX quotes in the news, CFTC positioning and risk mood. Each part fails on its own."""
    from . import gold_news as N
    from xml.etree.ElementTree import ParseError
    utc_now = now.astimezone(UTC)
    out = {}
    try:
        items, errors = N.gather(utc_now)
        if errors:
            warnings.append('%d of %d news feeds unavailable' % (len(errors), len(N.FEEDS)))
        if items:
            out['news'] = N.sentiment(items, utc_now)
            found = N.implied_mcx(items, utc_now)
            if found:
                out['implied'] = implied_basis(found, fx_bars, utc_now, fetch_bars)
        else:
            warnings.append('No news headlines available; news not scored')
    except (OSError, ValueError, KeyError, TypeError, ParseError) as exc:
        warnings.append('News unavailable (%s)' % exc)
    try:
        out['positioning'] = N.cot()
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        warnings.append('CFTC positioning unavailable (%s)' % exc)
    try:
        out['mood'] = N.risk_mood(fetch or fetch_daily, now)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        warnings.append('VIX/S&P 500 unavailable (%s)' % exc)
    log('gold context: news %s, positioning %s, mood %s' % (
        out.get('news', {}).get('score'), out.get('positioning', {}).get('state'), out.get('mood', {}).get('state')))
    return out


def choose_basis(cfg, now, implied=None):
    """(basis, description, calibration): a fresh calibration, else MCX quotes in the news, else a stale calibration, else the default."""
    cal = load_calibration(cfg['state_dir'])
    age = (now - dt.datetime.fromisoformat(cal['calibrated_at'])).days if cal else None
    if cal and age <= cfg['calibration_max_days']:
        return cal['basis'], 'calibrated %s' % ('today' if age == 0 else '%d d ago' % age), cal
    if implied:
        return implied['basis'], 'from %d MCX quote%s in the news' % (
            len(implied['quotes']), '' if len(implied['quotes']) == 1 else 's'), None
    if cal:
        return cal['basis'], 'calibrated %d d ago, stale' % age, cal
    return cfg['basis'], 'default, not calibrated', None


def run(cfg, now, fetch=None, log=lambda msg: None, fetch_quote=None, context_fn=None):
    """Fetch, compute today's view and update the paper trades. Returns a context dict."""
    fetch = fetch or fetch_daily
    start = now.date() - dt.timedelta(days=1100)
    warnings = []
    gold = completed(fetch(GOLD, start, now), now)
    if not gold:
        raise ValueError('no completed COMEX gold bars')
    data_day = gold[-1].time.date()
    if (now.date() - data_day).days > 5:
        warnings.append('Latest COMEX bar is %s; data may be stale' % data_day)
    fx = [b for b in fetch(FX, start, now) if b.time.date() <= data_day]
    macro = {}
    for sym in (DXY, US10Y):
        try:
            macro[sym] = Lookup([b for b in fetch(sym, start, now) if b.time.date() <= data_day])
        except (ValueError, OSError) as exc:
            macro[sym] = None
            warnings.append('%s unavailable (%s); macro score neutral' % ('Dollar index' if sym == DXY else 'US 10Y', exc))
    # News is about now: a replay of a past date (--as-of) leaves it out rather than mixing times.
    live_clock = abs((dt.datetime.now(UTC) - now.astimezone(UTC)).total_seconds()) < 3600
    context = {}
    if cfg['news'] != 'off':
        if context_fn:
            context = context_fn(cfg, now, fx, warnings)
        elif live_clock:
            context = gather_context(cfg, now, fx, warnings, fetch, log=log)
        else:
            warnings.append('News, positioning and risk mood are skipped for a past date')
    basis, basis_note, cal = choose_basis(cfg, now, context.get('implied'))
    age = (now - dt.datetime.fromisoformat(cal['calibrated_at'])).days if cal else None
    implied = context.get('implied')
    if basis_note.startswith('default'):
        warnings.append('Basis not calibrated and no MCX quote in the news; levels use the default %.2f. '
                        'Send /calibrate with the GOLDPETAL price' % basis)
    elif 'stale' in basis_note:
        warnings.append('Basis calibrated %d days ago; send /calibrate with the current GOLDPETAL price' % age)
    if cal and implied and abs(implied['basis'] / basis - 1) > 0.02:
        warnings.append('MCX quotes in the news imply a basis of %.3f, %+.1f%% from yours; recalibrate if your '
                        'broker agrees' % (implied['basis'], (implied['basis'] / basis - 1) * 100))
    bars, refs = proxy(gold, fx, basis)
    if not bars or bars[-1].time.date() != data_day:
        raise ValueError('no USD/INR quote near %s' % data_day)
    s = series(bars)
    f = features(bars, s, len(bars) - 1, refs, macro[DXY], macro[US10Y])
    cand, reason = evaluate(f, cfg)
    plan = None
    if cand and cand['score'] >= cfg['min_score']:
        plan = build_plan(f, cand, cfg, basis)
        if plan is None:
            reason = 'one lot exceeds the risk budget'
    elif cand:
        reason = '%s %s scored %.0f, below %d' % (cand['side'].lower(), cand['setup'].lower(), cand['score'], cfg['min_score'])
    live = None
    if live_clock or fetch_quote:       # a replay of a past date has no "now" price
        try:
            live = live_price(cfg, basis, fetch_quote)
            live['change_pct'] = (live['price'] / f['close'] - 1) * 100
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            warnings.append('Live COMEX quote unavailable (%s)' % exc)
    if plan and live:
        sign = 1 if plan['side'] == 'BUY' else -1
        if sign * (live['price'] - plan['no_chase']) > 0:
            plan['flags'].insert(0, 'Overnight COMEX is already beyond the no-chase level (live %s); expect a gap skip '
                                    'at the MCX open' % P.inr(live['price'], 0))
        elif sign * (live['price'] - plan['entry']) >= 0:
            plan['flags'].insert(0, 'Overnight COMEX is already through the trigger (live %s); the SL order may fill at '
                                    'the open. Skip if MCX opens beyond the no-chase level' % P.inr(live['price'], 0))
    from . import gold_news as N
    bd = N.backdrop(f, context.get('news'), context.get('positioning'), context.get('mood'))
    if plan:
        apply_backdrop(plan, bd, context, cfg)
    trades = load_trades(cfg['state_dir'])
    news = update_trades(trades, gold, fx, cfg)
    active = [t for t in trades if t['result']['status'] in OPEN_STATES]
    if plan and active:
        plan['suppressed'] = 'already in a %s trade from %s; manage that one' % (
            active[0]['plan']['side'], active[0]['plan']['signal_date'])
    if live_clock and not basis_note.startswith('default'):
        state = Path(cfg['state_dir'])
        state.mkdir(parents=True, exist_ok=True)
        (state / 'basis.json').write_text(json.dumps({'basis': basis, 'source': basis_note, 'at': now.isoformat()}))
    log('gold: data %s, proxy %.0f, %s' % (data_day, f['close'], plan and plan['setup'] or reason))
    return {'data_date': data_day.isoformat(), 'generated_at': now.isoformat(), 'features': f,
            'basis': basis, 'basis_note': basis_note, 'calibration': cal, 'calibration_age_days': age,
            'context': context, 'backdrop': bd, 'allow_short': cfg['allow_short'],
            'candidate': cand, 'reason': reason, 'plan': plan, 'trades': trades, 'news': news, 'live': live,
            'watch': watch_levels(f), 'warnings': warnings, 'execution_enabled': False}


def apply_backdrop(plan, bd, context, cfg):
    """Conviction, news/positioning flags and (news="skip") suppression for a plan. Mutates the plan."""
    sign = 1 if plan['side'] == 'BUY' else -1
    aligned = bd['total'] * sign
    news, pos = context.get('news'), context.get('positioning')
    plan['backdrop'] = {'total': bd['total'], 'aligned': aligned, 'label': bd['label'],
                        'news_score': news['score'] if news else None,
                        'positioning': pos['state'] if pos else None}
    plan['conviction'] = 'High' if plan['score'] >= 70 and aligned >= 2 else 'Low' if aligned <= -2 else 'Medium'
    against = 'negative' if sign > 0 else 'supportive'
    if news and news['label'] == against:
        top = ', '.join('%s %d' % kv for kv in list(news['drivers'].items())[:3])
        plan['flags'].insert(0, 'News flow is %s for gold (score %+.0f: %s); consider half size or wait for a '
                                'second confirming close' % (news['label'], news['score'], top))
        plan['risk_rating'] = 'High'
        if cfg['news'] == 'skip':
            plan['suppressed'] = 'news flow against the trade (score %+.0f); news is set to "skip"' % news['score']
    if pos and pos['state'] == ('crowded long' if sign > 0 else 'washed out'):
        plan['flags'].append('Managed money is %s (%d%% of 3 years); a crowded side unwinds fast' % (
            pos['state'], pos['percentile']))


def format_context(ctx, limit=5):
    """The news, positioning, risk-mood and backdrop block (Telegram HTML)."""
    from . import gold_news as N
    c, bd = ctx.get('context') or {}, ctx.get('backdrop')
    news, pos, mood, implied = c.get('news'), c.get('positioning'), c.get('mood'), c.get('implied')
    if not (news or pos or mood):
        return None
    lines = []
    utc_now = dt.datetime.fromisoformat(ctx['generated_at']).astimezone(UTC)

    def ago(iso):
        hours = (utc_now - dt.datetime.fromisoformat(iso)).total_seconds() / 3600
        return '%d min ago' % (hours * 60) if hours < 1 else '%.0f h ago' % hours

    if news:
        lines.append('📰 <b>News flow: %s for gold</b> (%+.0f) · %d headlines in 48 h, %d scored' % (
            news['label'], news['score'], news['headlines'], news['scored']))
        arrows = {tag: '▲' if sign > 0 else '▼' for _, sign, tag, _ in N.CUES}
        drivers = ' · '.join('%s %s %d' % (arrows.get(tag, ''), tag, n) for tag, n in list(news['drivers'].items())[:6])
        if drivers:
            lines.append('Drivers (▲ supports gold, ▼ weighs): ' + html.escape(drivers))
        shown = 0
        for it in news['top']:
            if shown == limit:
                break
            lines.append('%s <a href="%s">%s</a> <i>(%s%s, %s)</i>' % (
                '🟢' if it['sign'] > 0 else '🔴', html.escape(it['link'], quote=True), html.escape(it['title'][:110]),
                '🇮🇳 ' if it['region'] == 'india' else '', html.escape(it['source'][:30]), ago(it['published'])))
            shown += 1
    if implied:
        q = implied['quotes'][0]
        lines.append('🇮🇳 MCX in the news: %s/g (%s, %s) → basis ≈ %.3f' % (
            P.inr(q['per_gram'], 0), html.escape(q['source'][:30]), ago(q['published']), implied['basis']))
    if pos:
        lines.append('📊 Positioning (CFTC, %s): managed money net long %s (%+.1fk w/w), %d%% of 3 y → %s' % (
            dt.date.fromisoformat(pos['date']).strftime('%d %b'), '{:,}'.format(pos['net']), pos['change'] / 1000,
            pos['percentile'], pos['state']))
    if mood:
        lines.append('😨 Risk mood: VIX %.1f (%+.0f%% in 5 d) · S&P 500 %s EMA20 → %s' % (
            mood['vix'], mood['vix_change_pct'], 'above' if mood['spx_above_ema20'] else 'below', mood['state']))
    if bd:
        votes = ' · '.join('%s %s' % (k, {1: '▲', 0: '▬', -1: '▼'}[v]) for k, (v, _) in bd['votes'].items())
        lines.append('🧭 <b>Backdrop for gold longs: %s (%+d)</b> · %s' % (bd['label'], bd['total'], votes))
        if bd['total'] <= -2 and not ctx.get('plan') and not ctx.get('allow_short', False):
            lines.append('<i>A short would fit this backdrop, but shorts are off: they lost in every backtest. '
                         'Waiting for the trend to turn is the plan.</i>')
    return '\n'.join(lines)


def watch_levels(f):
    """What would turn into a setup, for a no-trade day."""
    c, e20, e50 = f['close'], f['ema20'], f['ema50']
    if c > e50 and e20 > e50:
        return 'Uptrend: watch for a dip toward EMA20 %s or a close above the 20-day high %s' % (
            P.inr(e20, 0), P.inr(f['high20'], 0))
    if c < e50 and e20 < e50:
        return 'Downtrend: watch for a bounce toward EMA20 %s or a close below the 20-day low %s' % (
            P.inr(e20, 0), P.inr(f['low20'], 0))
    return 'No clear trend: range %s to %s; wait for a close outside it' % (P.inr(f['low20'], 0), P.inr(f['high20'], 0))


# --------------------------------------------------------------------------- message

ARROW = {1: '▲ up', 0: '▬ flat', -1: '▼ down'}
EVENT_TEXT = {'TRIGGER': 'triggered', 'TARGET1': 'T1 hit, half booked, stop to entry', 'TARGET2': 'T2 hit, closed',
              'TRAIL': 'trailing stop hit, closed',
              'STOP': 'stopped out', 'BREAKEVEN': 'stopped at breakeven', 'TIME': 'time stop, closed',
              'EXPIRED': 'not triggered, cancelled', 'GAP_SKIP': 'gapped past the no-chase level, skipped'}


def money(x):
    return ('+' if x >= 0 else '−') + P.inr(abs(x), 0)


def trade_line(t):
    p, r = t['plan'], t['result']
    icon = '🟢' if p['side'] == 'BUY' else '🔴'
    head = '%s %s %s (%s)' % (icon, p['side'], dt.date.fromisoformat(p['signal_date']).strftime('%d %b'), p['setup'].lower())
    if r['status'] == 'PENDING':
        return '%s · waiting for %s, %d of %d sessions used' % (
            head, P.inr(p['entry'], 0), r['sessions_seen'], p['entry_valid_sessions'])
    if r['status'] == 'OPEN':
        return '%s · open @ %s since %s · now %s (%s, %+.1fR) · stop %s%s' % (
            head, P.inr(r['fill'], 0), dt.date.fromisoformat(r['fill_date']).strftime('%d %b'), P.inr(r['last_close'], 0),
            money(r['net'] + r['unrealised']), r['open_r'] or 0, P.inr(r['stop'], 0),
            '' if r['t1_hit'] else ' · T1 %s' % P.inr(p['target1'], 0))
    if r['status'] == 'CLOSED':
        return '%s · %s · net %s (%+.2fR)' % (head, EVENT_TEXT[r['exit_reason']], money(r['net']), r['r'])
    return '%s · %s' % (head, EVENT_TEXT['EXPIRED' if r['status'] == 'EXPIRED' else 'GAP_SKIP'])


NEWS_WORDS = {'gold price down': 'gold falling', 'gold price up': 'gold rising', 'dollar up': 'strong dollar',
              'dollar down': 'weak dollar', 'Fed/rates hawkish': 'Fed hawkish', 'Fed/rates dovish': 'Fed dovish',
              'yields up': 'yields rising', 'yields down': 'yields falling', 'geopolitical risk': 'geopolitical risk',
              'tensions easing': 'tensions easing', 'rupee weak (MCX +)': 'weak rupee', 'rupee strong (MCX -)': 'strong rupee',
              'forecast cut': 'forecasts cut', 'forecast raised': 'forecasts raised'}
ARROWS = {1: '▲', 0: '▬', -1: '▼'}


def pct_text(x, ref):
    return '%+.1f%%' % ((x / ref - 1) * 100)


def backdrop_lines(ctx):
    """One line per backdrop vote, arrow first (▲ helps a gold buyer, ▼ hurts)."""
    from . import gold_news as N
    f, c, bd = ctx['features'], ctx.get('context') or {}, ctx.get('backdrop')
    votes = bd['votes'] if bd else {}
    trend = 1 if f['close'] > f['ema20'] > f['ema50'] else -1 if f['close'] < f['ema20'] < f['ema50'] else 0
    word = {1: 'up', 0: 'flat', -1: 'down'}
    if f['weekly'] == trend:
        t = 'Trend %s, weekly and daily' % word[trend]
    else:
        t = 'Trend mixed: weekly %s, daily %s' % (word[f['weekly']], word[trend])
    lines = ['%s %s · RSI %.0f · ADX %.0f' % (ARROWS[votes.get('trend', (0,))[0]], t, f['rsi'], f['adx'])]
    macro = []
    if f['dxy_change_pct'] is not None:
        macro.append('Dollar %+.1f%%' % f['dxy_change_pct'])
    if f['us10y_change'] is not None:
        macro.append('US 10Y %.2f%% (%+.2f)' % (f['us10y'], f['us10y_change']))
    if macro:
        lines.append('%s %s, 20 d' % (ARROWS[votes.get('macro', (0,))[0]], ' · '.join(macro)))
    news = c.get('news')
    if news:
        signs = {tag: sign for _, sign, tag, _ in N.CUES}
        want = {'negative': -1, 'supportive': 1}.get(news['label'])
        tags = [t for t in news['drivers'] if want is None or signs.get(t) == want][:3]
        lines.append('%s News %+.0f%s' % (ARROWS[votes['news'][0]], news['score'],
                                          ': ' + ', '.join(NEWS_WORDS.get(t, t) for t in tags) if tags else ''))
    pos = c.get('positioning')
    if pos:
        lines.append('%s Funds %s (%d%% of 3 y)' % (ARROWS[votes['positioning'][0]], pos['state'], pos['percentile']))
    mood = c.get('mood')
    if mood:
        lines.append('%s %s: VIX %.0f (%+.0f%% in 5 d)' % (
            ARROWS[votes['risk mood'][0]], {'risk-off': 'Fear high', 'calm': 'Markets calm'}.get(mood['state'], 'Fear normal'),
            mood['vix'], mood['vix_change_pct']))
    return lines


def format_plan(p, cfg):
    """The compact trade plan: entry, stop, target, size and up to three warnings."""
    e = html.escape
    buy = p['side'] == 'BUY'
    close = p['features']['close']
    if p.get('target2') is None:
        target = '🎯 Target %s (%s, %gR) → %s, then trail %s behind the best price' % (
            P.inr(p['target1'], 0), pct_text(p['target1'], close), p['target1_r'],
            'book half, stop to entry' if p['lots'] > 1 else 'stop to entry',
            P.inr(p['trail_atr'] * p['features']['atr'], 0))
    else:
        target = '🎯 T1 %s (%s) book half · T2 %s (%s)' % (
            P.inr(p['target1'], 0), pct_text(p['target1'], close), P.inr(p['target2'], 0), pct_text(p['target2'], close))
    lines = [
        '▶ Entry: SL-%s %s %s, limit %s · valid %d sessions' % (
            'buy' if buy else 'sell', 'above' if buy else 'below', P.inr(p['entry'], 0), P.inr(p['no_chase'], 0),
            p['entry_valid_sessions']),
        '✖ Stop %s (%s)' % (P.inr(p['stop'], 0), pct_text(p['stop'], close)),
        target,
        '📦 %d lot%s · max loss %s · margin ≈ %s · exit by session %d' % (
            p['lots'], '' if p['lots'] == 1 else 's', P.inr(p['max_loss'], 0), P.inr(p['margin'], 0), p['max_hold_sessions']),
        '<i>Why: %s</i>' % e(', '.join(p['reasons'][:5])),
    ]
    lines += ['⚠️ %s' % e(flag) for flag in p['flags'][:3]]
    return '\n'.join(lines)


def format_messages(ctx, cfg):
    """The alert as one short card: verdict, price, backdrop, plan or watch levels, two headlines."""
    f, c = ctx['features'], ctx.get('context') or {}
    bd, plan, live = ctx.get('backdrop'), ctx['plan'], ctx.get('live')
    day = dt.date.fromisoformat(ctx['generated_at'][:10]).strftime('%a %d %b')
    if plan and not plan.get('suppressed'):
        verdict = '%s <b>%s setup</b> (%s)%s · score %.0f' % (
            '🟢' if plan['side'] == 'BUY' else '🔴', plan['side'],
            'pullback' if plan['setup'] == 'PULLBACK' else 'breakout',
            ' · conviction %s' % plan['conviction'] if plan.get('conviction') else '', plan['score'])
    elif plan:
        verdict = '⏸ <b>Signal held back</b>'
    else:
        verdict = '⛔ <b>NO TRADE, stay flat</b>'
    lines = ['🟡 <b>%s</b> · %s · %s' % (html.escape(cfg['contract']), day, verdict)]
    if live:
        lines.append('%s/g now (%+.1f%% vs close %s)' % (P.inr(live['price'], 0), live['change_pct'], P.inr(f['close'], 0)))
    else:
        lines.append('%s/g at the %s close (%+.1f%%)' % (
            P.inr(f['close'], 0), dt.date.fromisoformat(ctx['data_date']).strftime('%d %b'), f['change_pct']))
    lines.append('')
    if bd:
        lines.append('🧭 <b>Backdrop for buyers: %s (%+d)</b>' % (bd['label'].upper(), bd['total']))
    lines += backdrop_lines(ctx)
    lines.append('')
    if plan and not plan.get('suppressed'):
        lines.append(format_plan(plan, cfg))
    else:
        if plan:
            lines.append('%s %s signal not added: %s' % (plan['side'], plan['setup'].lower(), html.escape(plan['suppressed'])))
        trend_down = f['close'] < f['ema50'] and f['ema20'] < f['ema50']
        trend_up = f['close'] > f['ema50'] and f['ema20'] > f['ema50']
        if trend_up:
            watch = 'dip to %s (EMA20) · close above %s (20-day high)' % (P.inr(f['ema20'], 0), P.inr(f['high20'], 0))
        elif trend_down:
            watch = 'bounce to %s (EMA20) · close below %s (20-day low)' % (P.inr(f['ema20'], 0), P.inr(f['low20'], 0))
        else:
            watch = 'a close outside %s to %s (20-day range)' % (P.inr(f['low20'], 0), P.inr(f['high20'], 0))
        lines.append('👀 Watch: ' + watch)
        if bd and bd['total'] <= -2 and not ctx.get('allow_short', False):
            lines.append('<i>Shorts would fit, but they are off: they lost in every backtest.</i>')
    live_trades = [t for t in ctx['trades'] if t['result']['status'] in OPEN_STATES]
    news_trades = [t for t, _ in ctx['news']]
    shown = live_trades + [t for t in news_trades if t not in live_trades]
    if shown:
        lines.append('')
        lines += ['📒 ' + html.escape(trade_line(t)) for t in shown[:3]]
        done = [t for t in ctx['trades'] if t['result']['status'] == 'CLOSED']
        if done:
            lines.append('Record: %d closed · %d won · %+.1fR · net %s' % (
                len(done), sum(1 for t in done if t['result']['net'] > 0), sum(t['result']['r'] or 0 for t in done),
                money(sum(t['result']['net'] for t in done))))
    news = c.get('news')
    if news and news['top']:
        lines.append('')
        want = {'negative': -1, 'supportive': 1}.get(news['label'])
        heads = [it for it in news['top'] if want is None or it['sign'] == want] or news['top']
        for it in heads[:2]:
            lines.append('📰 <a href="%s">%s</a> · %s' % (html.escape(it['link'], quote=True), html.escape(it['title'][:90]),
                                                         html.escape(it['source'][:25])))
    lines.append('')
    source = (ctx.get('basis_note') or '').replace('from ', '').replace(' in the news', ' in news')
    note = 'Basis %.3f%s' % (ctx['basis'], ' (%s)' % source if source else '')
    lines.append('<i>%s · rule-based, not advice · /news /price /trades</i>' % html.escape(note))
    lines += ['<i>⚠️ %s</i>' % html.escape(w.split(';')[0]) for w in ctx['warnings'][:2]]
    text = '\n'.join(lines)
    if len(text) <= P.TELEGRAM_LIMIT:
        return [text]
    cut = text.rfind('\n', 0, P.TELEGRAM_LIMIT)
    return [text[:cut], text[cut + 1:]]


# --------------------------------------------------------------------------- backtest

def backtest(cfg, since=dt.date(2006, 1, 1), now=None, fetch=None, log=lambda msg: None, until=None):
    """Run the live rules over history, one position at a time, costs included.

    Uses a constant basis (R multiples do not depend on it). Returns a report dict."""
    now = now or dt.datetime.now(UTC)
    fetch = fetch or fetch_daily
    start = dt.date(2000, 1, 1)
    gold = completed(fetch(GOLD, start, now), now)
    fx = fetch(FX, start, now)
    dxy, us10y = Lookup(fetch(DXY, start, now)), Lookup(fetch(US10Y, start, now))
    bars, refs = proxy(gold, fx, cfg['basis'])
    log('backtest: %d proxy sessions %s to %s' % (len(bars), bars[0].time.date(), bars[-1].time.date()))
    s = series(bars)
    signals, trades, busy_until = [], [], -1
    for i in range(MIN_BARS - 1, len(bars) - 1):
        if bars[i].time.date() < since or (until and bars[i].time.date() > until):
            continue
        f = features(bars, s, i, refs, dxy, us10y)
        cand, _ = evaluate(f, cfg)
        if not cand:
            continue
        plan = build_plan(f, cand, cfg, cfg['basis'])
        if plan is None:
            continue
        sim = simulate(plan, bars[i + 1:], cfg)
        if sim['status'] in OPEN_STATES:
            continue        # unfinished at the end of the data
        rec = {'date': f['date'], 'side': plan['side'], 'setup': plan['setup'], 'score': plan['score'],
               'status': sim['status'], 'exit': sim['exit_reason'], 'r': sim.get('r'), 'net': sim.get('net'),
               'held': (sim['exit_index'] - next((k for k, b in enumerate(bars[i + 1:])
                                                  if b.time.date().isoformat() == sim['fill_date']), 0) + 1)
               if sim['status'] == 'CLOSED' else None}
        signals.append(rec)
        if plan['score'] >= cfg['min_score'] and i > busy_until:
            trades.append(rec)
            busy_until = i + 1 + sim['exit_index']
    return {'from': since.isoformat(), 'to': bars[-1].time.date().isoformat(), 'sessions': len(bars),
            'settings': {k: cfg[k] for k in ('min_score', 'allow_short', 'target1_r', 'target2_r', 'trail_atr',
                                             'entry_valid_sessions', 'max_hold_sessions', 'basis')},
            'alerts': stats(trades), 'by_side': group(trades, 'side'), 'by_setup': group(trades, 'setup'),
            'by_year': group(trades, lambda t: t['date'][:4]),
            'by_score_all_signals': group(signals, lambda t: '<50' if t['score'] < 50 else '50-59' if t['score'] < 60
                                          else '60-69' if t['score'] < 70 else '70-79' if t['score'] < 80 else '80+'),
            'trades': trades}


def stats(rows):
    closed = [t for t in rows if t['status'] == 'CLOSED']
    rs = [t['r'] for t in closed]
    out = {'signals': len(rows), 'triggered': len(closed),
           'expired': sum(1 for t in rows if t['status'] == 'EXPIRED'),
           'skipped_gap': sum(1 for t in rows if t['status'] == 'SKIPPED')}
    if not rs:
        return out
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    peak = equity = dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    mean = sum(rs) / len(rs)
    sd = (sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)) ** 0.5 if len(rs) > 1 else 0.0
    exits = {}
    for t in closed:
        exits[t['exit']] = exits.get(t['exit'], 0) + 1
    out.update(win_rate=round(len(wins) / len(rs) * 100, 1), avg_r=round(mean, 3),
               se_r=round(sd / len(rs) ** 0.5, 3), total_r=round(sum(rs), 1),
               profit_factor=round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
               max_drawdown_r=round(dd, 1), avg_held=round(sum(t['held'] for t in closed) / len(closed), 1),
               exits=exits)
    return out


def group(rows, key):
    fn = key if callable(key) else (lambda t: t[key])
    buckets = {}
    for t in rows:
        buckets.setdefault(fn(t), []).append(t)
    return {k: stats(v) for k, v in sorted(buckets.items())}


def format_backtest(rep):
    a = rep['alerts']

    def line(name, x):
        if not x.get('triggered'):
            return '%-10s %4d signals, none triggered' % (name, x['signals'])
        return '%-10s %4d trades  win %5.1f%%  avg %+.2fR ±%.2f  total %+6.1fR  PF %s  maxDD %.1fR' % (
            name, x['triggered'], x['win_rate'], x['avg_r'], x['se_r'], x['total_r'],
            x['profit_factor'] if x['profit_factor'] is not None else '-', x['max_drawdown_r'])
    out = ['Gold swing backtest %s to %s (%d sessions, costs included, one position at a time)' % (
        rep['from'], rep['to'], rep['sessions']),
        line('ALL', a)]
    if a.get('triggered'):
        out.append('           %d signals: %d expired, %d skipped on a gap · avg hold %.1f sessions · exits %s' % (
            a['signals'], a['expired'], a['skipped_gap'], a['avg_held'],
            ', '.join('%s %d' % kv for kv in sorted(a['exits'].items()))))
    for title, key in (('By side', 'by_side'), ('By setup', 'by_setup'), ('By year', 'by_year'),
                       ('By score (every signal, overlapping)', 'by_score_all_signals')):
        out.append('')
        out.append(title)
        out += ['  ' + line(k, v) for k, v in rep[key].items()]
    out += ['', 'Caveats: proxy bars, not MCX prices (basis, MCX hours and rollovers differ); GC=F is an unadjusted '
            'front-month series with roll jumps; fills assume stop orders at the level and stop-first bars; '
            'costs are estimates. Past results do not guarantee future ones.']
    return '\n'.join(out)


# --------------------------------------------------------------------------- live

def parse_intraday(doc, symbol, now, step=900):
    """Completed ``step``-second bars (time = bar start, UTC).

    The bar still forming and Yahoo's trailing live tick (a timestamp off the
    bar grid) are dropped."""
    results = (doc.get('chart') or {}).get('result')
    if not results:
        raise ValueError('no chart')
    r = results[0]
    if (r.get('meta') or {}).get('symbol', '').upper() != symbol.upper():
        raise ValueError('symbol mismatch')
    q = (r.get('indicators') or {}).get('quote', [{}])[0]
    stamps = r.get('timestamp') or []
    out = {}
    for i, t in enumerate(stamps):
        try:
            o, h, l, c = (q[k][i] for k in ('open', 'high', 'low', 'close'))
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or t % step:
            continue
        start = dt.datetime.fromtimestamp(t, UTC)
        if start + dt.timedelta(seconds=step) > now:
            continue
        out[start] = Bar(start, float(o), float(h), float(l), float(c), 0.0)
    return [out[k] for k in sorted(out)]


def fetch_intraday(symbol, now, rng='5d'):
    path = '/v8/finance/chart/%s?range=%s&interval=15m' % (urllib.parse.quote(symbol, safe=''), rng)
    return parse_intraday(json.loads(P.http_get('https://query2.finance.yahoo.com' + path)), symbol, now.astimezone(UTC))


def comex_day(t):
    """COMEX trade date of a moment: its session runs from about 22:00 UTC the day before to 22:00 UTC."""
    return (t.astimezone(UTC) + dt.timedelta(hours=2)).date()


def current_basis(cfg, now=None):
    """Basis for live prices: a fresh calibration, else the morning alert's choice (within 3 days), else the default."""
    now = now or dt.datetime.now(IST)
    cal = load_calibration(cfg['state_dir'])
    if cal and (now - dt.datetime.fromisoformat(cal['calibrated_at'])).days <= cfg['calibration_max_days']:
        return cal['basis'], cal
    path = Path(cfg['state_dir']) / 'basis.json'
    if path.is_file():
        doc = json.loads(path.read_text())
        if now - dt.datetime.fromisoformat(doc['at']) <= dt.timedelta(days=3):
            return doc['basis'], doc
    return (cal['basis'], cal) if cal else (cfg['basis'], None)


def live_price(cfg, basis=None, fetch_quote=None):
    """Delayed live proxy price from the latest COMEX and USD/INR quotes."""
    fetch_quote = fetch_quote or latest_quote
    basis = basis or current_basis(cfg)[0]
    gc, fx = fetch_quote(GOLD), fetch_quote(FX)
    return {'price': gc['price'] * fx['price'] / TROY_OUNCE_G * basis, 'comex': gc['price'], 'usdinr': fx['price'],
            'time': gc['time'], 'basis': basis}


def clock(t):
    return dt.datetime.fromisoformat(t).astimezone(IST).strftime('%H:%M IST') if isinstance(t, str) \
        else t.astimezone(IST).strftime('%H:%M IST')


def watch(cfg, now, fetch_bars=None, fetch_quote=None):
    """Check pending and open paper trades against delayed 15-minute COMEX bars.

    Returns Telegram texts for levels crossed since the last check: near the
    trigger, trigger, T1 and the stop. Each is sent once per trade. These are
    heads-ups; the daily paper record in the morning alert stays the official
    result. Trigger and T1 are marked as reported so the morning alert does not
    repeat them; a stop is confirmed there with its net P&L."""
    fetch_bars = fetch_bars or fetch_intraday
    trades = load_trades(cfg['state_dir'])
    active = [t for t in trades if (t.get('result') or {}).get('status') in OPEN_STATES]
    if not active:
        return []
    gc = fetch_bars(GOLD, now)
    fx = (fetch_quote or latest_quote)(FX)['price']
    texts = []
    for t in active:
        p, r = t['plan'], t['result']
        sign = 1 if p['side'] == 'BUY' else -1
        k = fx / TROY_OUNCE_G * p['basis']
        start = dt.datetime.combine(dt.date.fromisoformat(p['signal_date']), COMEX_DAY_END_UTC, UTC)
        last = dt.datetime.fromisoformat(t['watched_until']) if t.get('watched_until') else start
        bars = [b for b in gc if b.time >= max(last, start)]
        if not bars:
            continue
        seen = set(t.get('live') or [])
        reported = set(t.get('reported') or [])
        status, stop = r['status'], r.get('stop', p['stop'])
        fill = r.get('fill') or p['entry']
        t1_hit = r.get('t1_hit', False)
        near = p['entry'] - sign * 0.25 * p['features']['atr']
        name = '%s %s %s' % (html.escape(p['contract']), p['side'], dt.date.fromisoformat(p['signal_date']).strftime('%d %b'))
        for b in bars:
            hi, lo = b.high * k, b.low * k
            fav, adv = (hi, lo) if sign > 0 else (lo, hi)
            when = clock(b.time + dt.timedelta(minutes=15))
            if status == 'PENDING':
                if sign * (fav - p['entry']) >= 0:
                    key = 'TRIGGER:%s' % comex_day(b.time)
                    if 'trigger' not in seen:
                        seen.add('trigger')
                        reported.add(key)
                        texts.append('🔔 <b>%s: trigger crossed</b> · %s\nProxy reached %s, through the trigger %s. Your SL-%s '
                                     'order should have filled; confirm with your broker.\nStop %s · T1 %s · then trail %g × ATR' % (
                                         name, when, P.inr(fav, 0), P.inr(p['entry'], 0), 'buy' if sign > 0 else 'sell',
                                         P.inr(p['stop'], 0), P.inr(p['target1'], 0), p.get('trail_atr') or 0))
                    status, fill = 'OPEN', p['entry']
                elif sign * (fav - near) >= 0 and 'near' not in seen:
                    seen.add('near')
                    texts.append('⏳ <b>%s: near the trigger</b> · %s\nProxy %s, trigger %s (%.2f%% away). Make sure the '
                                 'SL order is placed.' % (name, when, P.inr(fav, 0), P.inr(p['entry'], 0),
                                                          abs(p['entry'] / fav - 1) * 100))
                    continue
                else:
                    continue
            if sign * (adv - stop) <= 0:
                if 'stop' not in seen:
                    seen.add('stop')
                    kind = 'TRAIL' if t1_hit and sign * (stop - fill) > 0 else 'BREAKEVEN' if t1_hit else 'STOP'
                    texts.append('🛑 <b>%s: %s reached</b> · %s\nProxy %s through the stop %s. Exit the %s if your '
                                 'broker stop has not; the morning alert confirms the paper result.' % (
                                     name, {'STOP': 'stop-loss', 'BREAKEVEN': 'breakeven stop', 'TRAIL': 'trailing stop'}[kind],
                                     when, P.inr(adv, 0), P.inr(stop, 0), 'position' if not t1_hit else 'rest'))
                break
            if not t1_hit and sign * (fav - p['target1']) >= 0:
                t1_hit = True
                if 't1' not in seen:
                    seen.add('t1')
                    reported.add('TARGET1:%s' % comex_day(b.time))
                    texts.append('🎯 <b>%s: T1 reached</b> · %s\nProxy %s through T1 %s. %s and move the stop to the '
                                 'entry %s; the rest trails %g × ATR from here.' % (
                                     name, when, P.inr(fav, 0), P.inr(p['target1'], 0),
                                     'Book half' if p['lots'] > 1 else 'With 1 lot, keep it',
                                     P.inr(fill, 0), p.get('trail_atr') or 0))
                stop = fill
        t['live'] = sorted(seen)
        t['reported'] = sorted(reported)
        t['watched_until'] = (bars[-1].time + dt.timedelta(minutes=15)).isoformat()
    save_trades(cfg['state_dir'], trades)
    return texts


# --------------------------------------------------------------------------- telegram commands

HELP = ('🟡 <b>Gold bot commands</b>\n'
        '/gold: today\'s swing view (a preview; nothing is recorded)\n'
        '/news: news flow, positioning, risk mood and the backdrop vote\n'
        '/price: delayed live GOLDPETAL proxy and distance to your trade levels\n'
        '/trades: paper trades and the running record\n'
        '/calibrate 12450: set the basis from the GOLDPETAL price in your broker app\n'
        '/lab: gold strategy lab, champion and forward paper scoreboard\n'
        '/capital 50000: set your trading capital (min ₹5,000); /capital shows capital and risk\n'
        '/risk 1.5: risk per trade as % of capital (0.1-5%, default 1%), or /risk ₹750\n'
        '/help: this list')


def get_updates(token, offset, timeout=20):
    query = urllib.parse.urlencode({'offset': offset, 'timeout': 0, 'allowed_updates': '["message"]'})
    try:
        with urllib.request.urlopen('https://api.telegram.org/bot%s/getUpdates?%s' % (token, query), timeout=timeout) as response:
            doc = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError('Telegram getUpdates HTTP %d' % exc.code) from None
    if not doc.get('ok'):
        raise ValueError('Telegram getUpdates failed: %s' % doc.get('description'))
    return doc.get('result') or []


def price_text(cfg, now, fetch_quote=None):
    live = live_price(cfg, fetch_quote=fetch_quote)
    lines = ['💹 <b>%s proxy %s/g</b> · COMEX $%s × USD/INR %.2f × basis %.4f' % (
        html.escape(cfg['contract']), P.inr(live['price'], 0), '{:,.1f}'.format(live['comex']), live['usdinr'], live['basis']),
        '<i>COMEX quote %s, delayed; shift by your broker\'s price if they differ</i>' % clock(live['time'])]
    for t in load_trades(cfg['state_dir']):
        r, p = t.get('result') or {}, t['plan']
        if r.get('status') not in OPEN_STATES:
            continue
        k = live['basis'] / p['basis']          # plan levels were set with the plan's basis
        price = live['price'] / k
        if r['status'] == 'PENDING':
            lines.append('Pending %s: trigger %s is %+.2f%% away' % (p['side'], P.inr(p['entry'], 0), (p['entry'] / price - 1) * 100))
        else:
            sign = 1 if p['side'] == 'BUY' else -1
            lines.append('Open %s from %s: stop %s (%+.2f%%)%s · open P&L ≈ %s' % (
                p['side'], P.inr(r['fill'], 0), P.inr(r['stop'], 0), (r['stop'] / price - 1) * 100,
                '' if r.get('t1_hit') else ' · T1 %s (%+.2f%%)' % (P.inr(p['target1'], 0), (p['target1'] / price - 1) * 100),
                money(sign * (price - r['fill']) * p['lots'] * cfg['lot_grams'])))
    return '\n'.join(lines)


def trades_text(cfg):
    trades = load_trades(cfg['state_dir'])
    if not trades:
        return '📒 No gold paper trades yet. A setup is sent in the morning alert when one qualifies.'
    lines = ['📒 <b>Gold paper trades</b>'] + [html.escape(trade_line(t)) for t in trades[-10:]]
    done = [t for t in trades if t['result']['status'] == 'CLOSED']
    if done:
        lines.append('Record: %d closed · %d won · %+.1fR · net %s' % (
            len(done), sum(1 for t in done if t['result']['net'] > 0), sum(t['result']['r'] or 0 for t in done),
            money(sum(t['result']['net'] for t in done))))
    return '\n'.join(lines)


def handle_command(text, cfg, now, fetch_quote=None):
    """Reply texts for one chat command."""
    cmd, _, arg = text.strip().partition(' ')
    cmd = cmd.split('@')[0].lower()
    try:
        if cmd == '/capital':
            from . import account
            path = cfg.get('_account_path')
            if not path:
                return ['Capital settings are unavailable here.']
            if arg.strip():
                try:
                    account.write(path, capital=arg, source='telegram')
                except ValueError as exc:
                    return ['⚠️ %s. Example: <code>/capital 50000</code>' % html.escape(str(exc))]
                return ['✅ ' + account.text(path, cfg.get('_picks')) + '\nAll alert streams size from it from the next run.']
            return [account.text(path, cfg.get('_picks'))]
        if cmd == '/risk':
            from . import account
            path = cfg.get('_account_path')
            if not path:
                return ['Risk settings are unavailable here.']
            if arg.strip():
                try:
                    account.write(path, risk_pct=arg, source='telegram',
                                  base_capital=(cfg.get('_picks') or {}).get('capital'))
                except ValueError as exc:
                    return ['⚠️ %s. Example: <code>/risk 1.5</code>' % html.escape(str(exc))]
                return ['✅ ' + account.text(path, cfg.get('_picks')) + '\nAll alert streams size from it from the next run.']
            return [account.text(path, cfg.get('_picks'))]
        if cmd == '/price':
            return [price_text(cfg, now, fetch_quote)]
        if cmd == '/trades':
            return [trades_text(cfg)]
        if cmd == '/calibrate':
            try:
                price = float(arg.replace(',', '').replace('₹', '').strip())
            except ValueError:
                return ['Send the GOLDPETAL price per gram, e.g. <code>/calibrate 12450</code>']
            doc = calibrate(cfg, price, now, fetch_quote)
            return ['✅ Calibrated: basis %.4f (GOLDPETAL %s vs COMEX $%s × USD/INR %.2f). Alerts use it from now on.' % (
                doc['basis'], P.inr(doc['mcx_price'], 0), '{:,.1f}'.format(doc['comex']), doc['usdinr'])]
        if cmd == '/news':
            ctx = run(cfg, now, fetch_quote=fetch_quote)
            return [format_context(ctx) or 'News, positioning and risk mood are unavailable right now.']
        if cmd == '/gold':
            ctx = run(cfg, now, fetch_quote=fetch_quote)
            messages = format_messages(ctx, cfg)
            messages[0] = '<i>On-demand view; nothing is recorded. The morning alert is the official one.</i>\n\n' + messages[0]
            return messages
        if cmd == '/lab':
            from . import gold_lab as L
            lab = L.load_settings(L.DEFAULT_LAB_CONFIG, cfg)
            m = L.load_market(now.astimezone(UTC), current_basis(cfg, now)[0])
            return [L.status_text(m, lab, cfg)]
        return [HELP]
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return ['⚠️ %s failed: %s' % (html.escape(cmd), html.escape(str(exc)))]


def poll_commands(cfg, now, token, chat, send=None, fetch_updates=None, fetch_quote=None, max_age=600):
    """Answer commands sent from the alert chat since the last poll. Returns how many were answered.

    Messages from any other chat are ignored, and commands older than
    ``max_age`` seconds (for example after downtime) are skipped, not replayed."""
    send = send or P.send_telegram
    path = Path(cfg['state_dir']) / 'telegram-offset.json'
    offset = json.loads(path.read_text())['offset'] if path.is_file() else 0
    updates = (fetch_updates or get_updates)(token, offset)
    answered = 0
    for u in updates:
        offset = max(offset, u['update_id'] + 1)
        msg = u.get('message') or {}
        text = (msg.get('text') or '').strip()
        if str((msg.get('chat') or {}).get('id')) != str(chat) or not text.startswith('/'):
            continue
        if now.timestamp() - msg.get('date', 0) > max_age:
            continue
        for reply in handle_command(text, cfg, now, fetch_quote):
            send(token, chat, reply)
        answered += 1
    if updates:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'offset': offset}))
    return answered


# --------------------------------------------------------------------------- CLI

def _scratch_copy(state_dir):
    """A temporary copy of the state directory, so a dry run never changes the real one."""
    import shutil
    import tempfile
    target = Path(tempfile.mkdtemp(prefix='gold-dry-'))
    if Path(state_dir).is_dir():
        for name in ('trades.json', 'calibration.json'):
            if (Path(state_dir) / name).is_file():
                shutil.copy(Path(state_dir) / name, target / name)
    return str(target)


def _config_only(path):
    """capital and risk_per_trade from the config file itself, without the saved account."""
    with open(path) as fh:
        user = json.load(fh)
    return dict(capital=user.get('capital', P.DEFAULTS['capital']),
                risk_per_trade=user.get('risk_per_trade', P.DEFAULTS['risk_per_trade']))


def main(argv=None):
    ap = argparse.ArgumentParser(description='MCX GOLD PETAL swing setups to Telegram (research only, free data).')
    ap.add_argument('--config', help='picks JSON with a "gold" object (see config/picks.example.json)')
    ap.add_argument('--env-file', default='var/telegram.env')
    ap.add_argument('--dry-run', action='store_true', help='print instead of sending; saves nothing')
    ap.add_argument('--force', action='store_true', help='run on a weekend or resend for the same COMEX session')
    ap.add_argument('--as-of', help='pretend it is this time (ISO 8601 with offset)')
    ap.add_argument('--json', action='store_true', help='print the full result as JSON')
    ap.add_argument('--calibrate', type=float, metavar='PRICE', help='GOLDPETAL price per gram from your broker app, now')
    ap.add_argument('--backtest', action='store_true', help='replay the rules over history and print a report')
    ap.add_argument('--since', default='2006-01-01', help='backtest start date')
    ap.add_argument('--watch', action='store_true', help='check open paper trades against delayed 15-minute bars and alert')
    ap.add_argument('--commands', action='store_true', help='answer Telegram commands sent since the last poll')
    a = ap.parse_args(argv)
    log = lambda msg: print(msg, file=sys.stderr)
    token = chat = None
    muted = False
    try:
        full = P.load_config(a.config)
        cfg = full['gold']
        # The dashboard's gold switch silences Telegram; commands still get answers.
        muted = not S.enabled(S.path_for(full), 'gold')
        now = dt.datetime.fromisoformat(a.as_of).astimezone(IST) if a.as_of else dt.datetime.now(IST)
        if a.calibrate is not None:
            doc = calibrate(cfg, a.calibrate, now)
            print('Calibrated: basis %.4f (GOLDPETAL %s vs COMEX $%.1f x USD/INR %.2f). Saved to %s/calibration.json' % (
                doc['basis'], P.inr(doc['mcx_price'], 0), doc['comex'], doc['usdinr'], cfg['state_dir']))
            return 0
        if a.backtest:
            rep = backtest(cfg, dt.date.fromisoformat(a.since), log=log)
            state = Path(cfg['state_dir'])
            state.mkdir(parents=True, exist_ok=True)
            (state / 'backtest-latest.json').write_text(json.dumps(rep, indent=2))
            print(json.dumps(rep, indent=2) if a.json else format_backtest(rep))
            return 0
        env = dict(P.read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith('TELEGRAM_')})
        token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        if not a.dry_run and not (token and chat):
            raise ValueError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (environment or %s), or use --dry-run' % a.env_file)
        if a.commands:
            if a.dry_run:
                return 0            # polling consumes updates; a dry run must not
            from . import account
            answered = poll_commands(dict(cfg, _account_path=str(account.path_for(full)),
                                          _picks=P.load_config(None) if not a.config else _config_only(a.config)),
                                     now, token, chat)
            if answered:
                log('gold: answered %d command(s)' % answered)
            return 0
        if a.watch:
            if a.dry_run:
                print('\n\n'.join(watch(dict(cfg, state_dir=_scratch_copy(cfg['state_dir'])), now)) or 'gold watch: nothing new')
                return 0
            # Watch even when muted so re-enabling does not replay old crossings.
            texts = watch(cfg, now)
            for text in ([] if muted else texts):
                P.send_telegram(token, chat, text)
            if texts:
                log('gold watch: %s %d update(s)' % ('muted' if muted else 'sent', len(texts)))
            return 0
        if now.weekday() >= 5 and not a.force:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'weekend'}))
            return 0
        state = Path(cfg['state_dir'])
        if muted and not a.dry_run:
            # No alert and no new paper trade, but the ran- marker lets the gold lab log its day.
            state.mkdir(parents=True, exist_ok=True)
            (state / ('ran-%s' % now.date().isoformat())).write_text(now.isoformat())
            print(json.dumps({'status': 'MUTED', 'reason': 'gold alerts are switched off in the dashboard'}))
            return 0
        ctx = run(cfg, now, log=log)
        marker = state / ('sent-%s' % ctx['data_date'])
        ran = state / ('ran-%s' % now.date().isoformat())
        if marker.exists() and not a.force and not a.dry_run:
            ran.write_text(now.isoformat())
            print(json.dumps({'status': 'SKIPPED', 'reason': 'no new COMEX session since the last alert',
                              'data_date': ctx['data_date']}))
            return 0
        messages = format_messages(ctx, cfg)
        if a.dry_run:
            print('\n\n----- message -----\n\n'.join(messages))
        else:
            for text in messages:
                P.send_telegram(token, chat, text)
            trades = ctx['trades']
            for t, events in ctx['news']:
                t['reported'] = sorted(set(t.get('reported') or []) | {'%s:%s' % (e['kind'], e['date']) for e in events})
            plan = ctx['plan']
            if plan and not plan.get('suppressed') and not any(t['plan']['signal_date'] == plan['signal_date'] for t in trades):
                trades.append({'plan': plan, 'sent_at': now.isoformat(),
                               'result': {'status': 'PENDING', 'events': [], 'sessions_seen': 0}, 'reported': []})
            save_trades(state, trades)
            record = {k: v for k, v in ctx.items() if k not in ('trades', 'news')}
            (state / ('gold-%s.json' % ctx['data_date'])).write_text(json.dumps(record, indent=2, default=str))
            marker.write_text(now.isoformat())
            ran.write_text(now.isoformat())
        if a.json:
            print(json.dumps({k: v for k, v in ctx.items() if k != 'news'}, indent=2, default=str))
        log('gold %s for %s: %s' % ('prepared' if a.dry_run else 'sent', ctx['data_date'],
                                    ctx['plan']['setup'] if ctx['plan'] else 'no setup'))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        try:
            # --watch and --commands repeat every few minutes: log their failures, don't page.
            if not a.dry_run and not muted and token and chat and a.calibrate is None \
                    and not (a.backtest or a.watch or a.commands):
                P.send_telegram(token, chat, '⚠️ Gold alert failed: %s' % html.escape(str(exc)))
        except (ValueError, OSError):
            pass
        return 1


if __name__ == '__main__':
    sys.exit(main())
