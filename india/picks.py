"""Daily top-N NSE trade ideas with entry, exit and risk plans, sent to Telegram.

    python3 -m india.picks --config config/picks.example.json --dry-run
    python3 -m india.picks --config config/picks.json          # sends to Telegram

Runs once per trading day (schedule it before the open, e.g. 08:45 IST). It
scores a liquid universe (NIFTY 200 by default) on COMPLETED daily candles:

    trend      price vs EMA20/EMA50 (and EMA200 when available), EMA20 slope
    momentum   MACD(12,26,9) side, fresh crossover, expanding histogram
    RSI        Wilder RSI(14) in a momentum band, not stretched
    volume     last session's volume vs its 20-session average
    structure  close vs the prior 20-session high/low
    liquidity  20-session average traded value

and keeps at most ``top_n`` setups that pass every filter and ``min_score``.
Fewer than ``top_n`` -- or none -- is a valid answer and is said so.

Plans are INTRADAY: a trigger beyond the previous session's high (buy) or low
(sell), an ATR-sized stop, 1R/2R targets, a no-chase limit, an entry deadline
and a hard square-off. Sizes use the configured per-trade risk and India
cash-intraday cost estimates (india.market.costs).

Every alert is paper-traded (--settle) and each outcome is explained by
india.review. Setup traits that keep losing over enough paper trades become
lessons that lower future scores (``learning``); see docs/TELEGRAM_ALERTS.md.

This is rule-based research output, not investment advice. Nothing here places
orders; execution stays disabled.
"""
import argparse
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import IST, Bar, symbol_name
from .market import DEFAULT_CALENDAR, Calendar, costs, daily_completed
from . import review

DEFAULTS = {
    'universe': 'nifty200',          # nifty50 | nifty100 | nifty200 | nifty500 | path to a symbols file
    'symbols': [],                   # explicit list overrides 'universe'
    'capital': 100000,               # INR available for one position (no leverage assumed)
    'risk_per_trade': 1000,          # INR loss at stop, including estimated costs
    'max_daily_loss': None,          # INR; default 2 x risk_per_trade
    'top_n': 3,
    'min_score': 60,
    'min_price': 50,
    'min_avg_turnover_cr': 25,       # 20-session average traded value, INR crore
    'min_rel_volume': 1.2,           # last session volume / 20-session average
    'min_atr_pct': 1.0,
    'max_atr_pct': 5.0,
    'allow_short': True,             # intraday short only (cash segment cannot carry shorts overnight)
    'max_per_sector': 1,
    'alert_time': '08:45',           # --serve sends the morning alert from this IST time
    'entry_after': '09:30',
    'entry_deadline': '11:30',
    'square_off': '15:10',
    'workers': 4,
    'state_dir': 'var/state/picks',
    'calendar': None,
    'cost_config': None,
    'tick': None,                    # fixed price increment; default is a tick-safe price band
    'learning': True,                # lower scores for setup traits that keep losing in paper trades
    'learn_min_trades': 8,           # closed paper trades with a trait before it can become a lesson
    'learn_penalty': 10,             # points subtracted per active lesson
    'learn_max_penalty': 20,         # cap on the total subtracted from one setup
}
INDEX_FILES = {
    'nifty50': 'ind_nifty50list.csv', 'nifty100': 'ind_nifty100list.csv',
    'nifty200': 'ind_nifty200list.csv', 'nifty500': 'ind_nifty500list.csv',
}
INDEX_URL = 'https://nsearchives.nseindia.com/content/indices/'
# Last-resort universe when NSE's archive is unreachable and nothing is cached.
FALLBACK_NIFTY50 = (
    'ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL '
    'BHARTIARTL CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE '
    'HEROMOTOCO HINDALCO HINDUNILVR ICICIBANK INDUSINDBK INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT '
    'M&M MARUTI NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA '
    'TATACONSUM TATAMOTORS TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO').split()
MARKET_INDEX = '^NSEI'
TELEGRAM_LIMIT = 4000   # Telegram rejects messages above 4096 characters
CRORE, LAKH = 1e7, 1e5


# --------------------------------------------------------------------------- indicators

def ema_series(values, n):
    """EMA seeded with the SMA of the first n values; None before that."""
    out = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    out[n - 1] = e
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def macd_series(closes, fast=12, slow=26, signal=9):
    f, s = ema_series(closes, fast), ema_series(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_tail = ema_series(line[start:], signal)
    sig = [None] * start + sig_tail
    hist = [m - g if m is not None and g is not None else None for m, g in zip(line, sig)]
    return line, sig, hist


def rsi_series(closes, n=14):
    """Wilder RSI; None until n changes exist."""
    out = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n

    def value(g, l):
        return 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    out[n] = value(ag, al)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i + 1] = value(ag, al)
    return out


def atr_series(bars, n=14):
    """Wilder ATR over true range; None until n true ranges exist."""
    out = [None] * len(bars)
    if len(bars) < n + 1:
        return out
    tr = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:])]
    a = sum(tr[:n]) / n
    out[n] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i + 1] = a
    return out


def features(bars):
    """Indicator snapshot for the last completed daily bar. Needs >= 60 bars."""
    if len(bars) < 60:
        raise ValueError('needs at least 60 completed sessions, got %d' % len(bars))
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    last, prev = bars[-1], bars[-2]
    e20, e50, e200 = ema_series(closes, 20), ema_series(closes, 50), ema_series(closes, 200)
    line, sig, hist = macd_series(closes)
    rsi = rsi_series(closes)
    atr = atr_series(bars)
    avg_vol = sum(vols[-21:-1]) / 20
    turnover = sum(b.close * b.volume for b in bars[-20:]) / 20
    crossed = None
    for back in range(1, 4):   # a crossover within the last three sessions
        a, b = line[-back - 1] - sig[-back - 1], line[-back] - sig[-back]
        if a <= 0 < b:
            crossed = 'bullish'
        elif a >= 0 > b:
            crossed = 'bearish'
        if crossed:
            break
    p = (last.high + last.low + last.close) / 3
    return {
        'date': last.time.date().isoformat(),
        'open': last.open, 'high': last.high, 'low': last.low, 'close': last.close,
        'prev_close': prev.close, 'change_pct': (last.close / prev.close - 1) * 100,
        'volume': last.volume, 'avg_volume': avg_vol,
        'rel_volume': last.volume / avg_vol if avg_vol > 0 else 0.0,
        'avg_turnover': turnover,
        'ema20': e20[-1], 'ema50': e50[-1], 'ema200': e200[-1], 'ema20_prev5': e20[-6],
        'macd': line[-1], 'macd_signal': sig[-1], 'macd_hist': hist[-1], 'macd_hist_prev': hist[-2],
        'macd_cross': crossed,
        'rsi': rsi[-1], 'atr': atr[-1], 'atr_pct': atr[-1] / last.close * 100,
        'high20': max(b.high for b in bars[-21:-1]), 'low20': min(b.low for b in bars[-21:-1]),
        'swing_high10': max(b.high for b in bars[-10:]), 'swing_low10': min(b.low for b in bars[-10:]),
        'pivot': p, 'r1': 2 * p - last.low, 's1': 2 * p - last.high,
        'r2': p + (last.high - last.low), 's2': p - (last.high - last.low),
        'sessions': len(bars),
    }


# --------------------------------------------------------------------------- scoring

def market_bias(bars):
    """bullish / bearish / neutral from the index close vs its EMA20 and EMA50."""
    if not bars or len(bars) < 50:
        return 'unknown', None
    closes = [b.close for b in bars]
    c, e20, e50 = closes[-1], ema_series(closes, 20)[-1], ema_series(closes, 50)[-1]
    detail = {'close': c, 'ema20': e20, 'ema50': e50, 'date': bars[-1].time.date().isoformat(),
              'change_pct': (c / closes[-2] - 1) * 100}
    if c > e20 > e50:
        return 'bullish', detail
    if c < e20 < e50:
        return 'bearish', detail
    return 'neutral', detail


def evaluate(f, cfg, bias='unknown'):
    """Score one symbol's features. Returns (candidate or None, reject reason or None)."""
    c = f['close']
    if c < cfg['min_price']:
        return None, 'price below minimum'
    if f['avg_turnover'] < cfg['min_avg_turnover_cr'] * CRORE:
        return None, 'low traded value'
    if f['rel_volume'] < cfg['min_rel_volume']:
        return None, 'volume not expanding'
    if not cfg['min_atr_pct'] <= f['atr_pct'] <= cfg['max_atr_pct']:
        return None, 'volatility outside range'
    e20, e50, rsi = f['ema20'], f['ema50'], f['rsi']
    macd_up = f['macd'] > f['macd_signal']
    if c > e20 > e50 and macd_up and 50 <= rsi <= 72:
        side = 'BUY'
    elif cfg['allow_short'] and c < e20 < e50 and not macd_up and 28 <= rsi <= 50:
        side = 'SELL'
    else:
        return None, 'trend, MACD and RSI not aligned'
    sign = 1 if side == 'BUY' else -1
    if sign * (c - e20) > 2 * f['atr']:
        return None, 'too extended from EMA20'

    reasons, parts = [], {}
    # Trend (20)
    trend = 12
    reasons.append('price %s EMA20 %s EMA50' % (('>', '>') if sign > 0 else ('<', '<')))
    if f['ema200'] is not None and sign * (c - f['ema200']) > 0:
        trend += 5
        reasons.append('%s EMA200' % ('above' if sign > 0 else 'below'))
    if sign * (e20 - f['ema20_prev5']) > 0:
        trend += 3
    parts['trend'] = trend
    # MACD (20)
    m = 0
    fresh = f['macd_cross'] == ('bullish' if sign > 0 else 'bearish')
    if fresh:
        m += 10
        reasons.append('fresh MACD %s crossover' % f['macd_cross'])
    else:
        reasons.append('MACD %s signal' % ('above' if sign > 0 else 'below'))
    if sign * (f['macd_hist'] - f['macd_hist_prev']) > 0:
        m += 6
        reasons.append('histogram expanding')
    if sign * f['macd'] > 0:
        m += 4
    parts['macd'] = m
    # RSI (15)
    r = rsi if sign > 0 else 100 - rsi      # mirror shorts onto the long scale
    parts['rsi'] = 15 if 55 <= r <= 68 else 8 if r < 55 else 6
    reasons.append('RSI %.0f' % rsi)
    # Volume (25)
    rv = f['rel_volume']
    vol = 20 * min(1.0, max(0.0, (rv - 1) / 2))
    if sign * (c - f['open']) > 0:
        vol += 5
    parts['volume'] = vol
    reasons.append('volume %.1fx average' % rv)
    # Structure (10)
    if sign > 0:
        s = 10 if c >= f['high20'] else 5 if c >= f['high20'] * 0.98 else 0
        if s == 10:
            reasons.append('closed at a 20-day high')
    else:
        s = 10 if c <= f['low20'] else 5 if c <= f['low20'] * 1.02 else 0
        if s == 10:
            reasons.append('closed at a 20-day low')
    parts['structure'] = s
    # Liquidity (10): 25 cr -> 0, 500 cr -> 10, log scale
    lo, hi = math.log10(25 * CRORE), math.log10(500 * CRORE)
    parts['liquidity'] = 10 * min(1.0, max(0.0, (math.log10(f['avg_turnover']) - lo) / (hi - lo)))

    score = sum(parts.values())
    counter = (bias == 'bullish' and side == 'SELL') or (bias == 'bearish' and side == 'BUY')
    if counter:
        score -= 10
    return {'side': side, 'score': round(score, 1), 'components': {k: round(v, 1) for k, v in parts.items()},
            'reasons': reasons, 'counter_trend': counter}, None


# --------------------------------------------------------------------------- trade plan

def tick_for(price, fixed=None):
    """A price increment valid under NSE's 0.05 and price-band tick regimes."""
    if fixed:
        return fixed
    for limit, tick in ((1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.0)):
        if price < limit:
            return tick
    return 5.0


def to_tick(x, tick, mode):
    steps = x / tick
    steps = math.ceil(steps - 1e-9) if mode == 'up' else math.floor(steps + 1e-9) if mode == 'down' else round(steps)
    return round(steps * tick, 2)


def hhmm(text):
    return dt.time.fromisoformat(text)


def build_plan(sym, f, cand, cfg, exchange='NSE'):
    side, atr = cand['side'], f['atr']
    sign = 1 if side == 'BUY' else -1
    level = f['high'] if sign > 0 else f['low']
    tick = tick_for(level, cfg.get('tick'))
    buffer = max(tick, level * 0.001)
    up, down = ('up', 'down') if sign > 0 else ('down', 'up')
    entry = to_tick(level + sign * buffer, tick, up)
    # Stop: half a daily ATR, kept between 0.6% and 2.5% of price.
    risk_ps = min(max(0.5 * atr, 0.006 * entry), 0.025 * entry)
    stop = to_tick(entry - sign * risk_ps, tick, down)
    risk_ps = abs(entry - stop)
    t1 = to_tick(entry + sign * risk_ps, tick, up)
    t2 = to_tick(entry + sign * 2 * risk_ps, tick, up)
    chase = to_tick(entry + sign * 0.25 * atr, tick, up)

    # Size: the stop-out loss including estimated costs stays within risk_per_trade.
    budget, capital = cfg['risk_per_trade'], cfg['capital']
    qty = min(int(budget // risk_ps), int(capital // entry))

    def legs(exit_price, q):
        buy, sell = (entry, exit_price) if sign > 0 else (exit_price, entry)
        gross = sign * (exit_price - entry) * q
        cost = costs(buy, sell, q, exchange, cfg.get('cost_config'))['total'] if q else 0.0
        return gross, cost
    while qty > 0:
        gross, cost = legs(stop, qty)
        if -gross + cost <= budget:
            break
        qty -= 1
    if qty <= 0:
        return None
    loss_gross, loss_cost = legs(stop, qty)
    t1_gross, t1_cost = legs(t1, qty)
    t2_gross, t2_cost = legs(t2, qty)
    max_loss = -loss_gross + loss_cost
    # Planned exit: half at T1, half at T2.
    half = qty // 2
    plan_gross = sign * ((t1 - entry) * half + (t2 - entry) * (qty - half))
    plan_cost = (legs(t1, half)[1] if half else 0) + legs(t2, qty - half)[1]
    plan_net = plan_gross - plan_cost

    flags = []
    if side == 'SELL':
        flags.append('Short sell is intraday-only (MIS) in the cash segment; must square off same day')
    if cand['counter_trend']:
        flags.append('Against the NIFTY 50 trend; consider half size')
    if f['atr_pct'] >= 3:
        flags.append('High volatility (ATR %.1f%% of price); expect wide swings' % f['atr_pct'])
    if (sign > 0 and f['rsi'] >= 68) or (sign < 0 and f['rsi'] <= 32):
        flags.append('RSI near an extreme; pullback risk after the open')
    if f['rel_volume'] >= 3:
        flags.append('Unusual volume (%.1fx); check news, results or block deals before trading' % f['rel_volume'])
    if abs(f['change_pct']) >= 4:
        flags.append('Large previous-day move (%+.1f%%); gap risk at the open' % f['change_pct'])
    if f['atr_pct'] < 1.8 and not flags:
        rating = 'Low'
    elif f['atr_pct'] < 3 and len(flags) <= 1:
        rating = 'Medium'
    else:
        rating = 'High'
    if side == 'BUY':
        support, resistance = max(f['s1'], f['ema20']), max(f['r1'], f['swing_high10'])
    else:
        support, resistance = min(f['s1'], f['swing_low10']), min(f['r1'], f['ema20'])
    return {
        'symbol': sym, 'side': side, 'score': cand['score'], 'components': cand['components'],
        'reasons': cand['reasons'], 'tick': tick,
        'entry': entry, 'no_chase': chase, 'stop': stop, 'target1': t1, 'target2': t2,
        'risk_per_share': round(risk_ps, 2), 'qty': qty, 'exposure': round(qty * entry, 2),
        'max_loss': round(max_loss, 2), 'est_costs_at_stop': round(loss_cost, 2),
        'net_t1': round(t1_gross - t1_cost, 2), 'net_t2': round(t2_gross - t2_cost, 2),
        'net_plan': round(plan_net, 2),
        'reward_risk': 2.0, 'net_reward_risk': round(plan_net / max_loss, 2) if max_loss > 0 else None,
        'support': round(support, 2), 'resistance': round(resistance, 2),
        'risk_rating': rating, 'flags': flags,
        'entry_after': cfg['entry_after'], 'entry_deadline': cfg['entry_deadline'], 'square_off': cfg['square_off'],
        'features': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()},
    }


def select(evaluated, cfg, sectors=None):
    """Highest scores first, at most max_per_sector per sector, at most top_n."""
    sectors = sectors or {}
    ranked = sorted(evaluated, key=lambda p: (-p['score'], -p['features']['avg_turnover'], p['symbol']))
    picked, count = [], {}
    for p in ranked:
        if p['score'] < cfg['min_score']:
            continue
        sector = sectors.get(p['symbol'])
        if sector and count.get(sector, 0) >= cfg['max_per_sector']:
            continue
        count[sector] = count.get(sector, 0) + 1
        p['sector'] = sector
        picked.append(p)
        if len(picked) == cfg['top_n']:
            break
    return picked


# --------------------------------------------------------------------------- message

def inr(x, places=2):
    """Indian digit grouping: 1,23,456.70"""
    neg, x = x < 0, abs(x)
    whole, frac = ('%.*f' % (places, x)).split('.') if places else ('%.0f' % x, '')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ','.join(groups + [tail])
    return ('-' if neg else '') + '₹' + whole + ('.' + frac if frac else '')


def compact(x):
    return '%s L' % ('%.2f' % (x / LAKH)) if x < CRORE else '%.2f Cr' % (x / CRORE)


def format_pick(i, p):
    e = html.escape
    f = p['features']
    buy = p['side'] == 'BUY'
    name = p['symbol'][:-3] if p['symbol'].endswith(('.NS', '.BO')) else p['symbol']
    head = '%s <b>%d. %s · %s (intraday)</b> · score %.0f/100' % ('🟢' if buy else '🔴', i, e(name), p['side'], p['score'])
    if p.get('sector'):
        head += '\n<i>%s</i>' % e(p['sector'])
    macd = ('fresh %s crossover' % f['macd_cross']) if f['macd_cross'] else ('above signal' if f['macd'] > f['macd_signal'] else 'below signal')
    lines = [
        head,
        'Close %s (%+.1f%%) · Vol %.1f× avg · RSI %.0f · MACD %s · ATR %.1f%%' % (
            inr(f['close']), f['change_pct'], f['rel_volume'], f['rsi'], macd, f['atr_pct']),
        '<i>Why: %s</i>' % e(', '.join(p['reasons'])),
        '',
        '<b>Entry plan</b>',
        '• Buy above %s' % inr(p['entry']) if buy else '• Sell (short) below %s' % inr(p['entry']),
        '• Wait for a 5-min candle to close %s it, after %s IST' % ('above' if buy else 'below', p['entry_after']),
        "• Don't chase %s %s · cancel if not triggered by %s" % ('above' if buy else 'below', inr(p['no_chase']), p['entry_deadline']),
        '• Skip if it opens %s %s (gap)' % ('above' if buy else 'below', inr(p['no_chase'])),
        '',
        '<b>Exit plan</b>',
        '• Stop-loss %s (%s/share)' % (inr(p['stop']), inr(p['risk_per_share'])),
        '• Target 1 %s (1R) → book 50%%, move stop to entry' % inr(p['target1']),
        '• Target 2 %s (2R) → exit the rest' % inr(p['target2']),
        '• Square off everything by %s IST' % p['square_off'],
        '',
        '<b>Risk</b>',
        '• Qty %d · exposure %s · risk level %s' % (p['qty'], compact(p['exposure']), p['risk_rating']),
        '• Max loss ≈ %s incl. ~%s costs' % (inr(p['max_loss'], 0), inr(p['est_costs_at_stop'], 0)),
        '• Plan reward ≈ %s (T1 %s / T2 %s net) · R:R 1:2' % (inr(p['net_plan'], 0), inr(p['net_t1'], 0), inr(p['net_t2'], 0)),
        '• Support %s · Resistance %s' % (inr(p['support']), inr(p['resistance'])),
    ]
    lines += ['⚠️ %s' % e(flag) for flag in p['flags']]
    for lesson in p.get('lessons') or []:
        lines.append('📚 −%d: %s' % (lesson['penalty'], e(lesson['evidence'])))
    return '\n'.join(lines)


def format_messages(picks, ctx, cfg):
    """List of Telegram HTML messages (each under the size limit)."""
    day = ctx['trade_date']
    bias = ctx['bias']
    mood = {'bullish': '🟢 bullish', 'bearish': '🔴 bearish', 'neutral': '⚪ neutral'}.get(bias, 'unknown')
    head = ['📊 <b>Top %d NSE trade setups · %s</b>' % (cfg['top_n'], day.strftime('%a %d %b %Y')),
            'Based on the %s close · NIFTY 50: %s' % (
                dt.date.fromisoformat(ctx['data_date']).strftime('%d %b') if ctx.get('data_date') else 'latest', mood)]
    if ctx.get('market'):
        m = ctx['market']
        head.append('NIFTY %s (%+.2f%%) · EMA20 %s · EMA50 %s' % (
            inr(m['close'], 0)[1:], m['change_pct'], inr(m['ema20'], 0)[1:], inr(m['ema50'], 0)[1:]))
    head.append('Scanned %d/%d · %d qualified · showing %d' % (
        ctx['scanned'], ctx['universe'], ctx['qualified'], len(picks)))
    for w in ctx.get('warnings', []):
        head.append('⚠️ ' + html.escape(w))
    learning = ctx.get('learning')
    if learning and learning['active']:
        head.append('📚 Learning from %d paper trades · %d lesson(s) active%s' % (
            learning['trades'], len(learning['active']),
            ' · %d setup(s) dropped below the bar' % learning['demoted'] if learning['demoted'] else ''))
    if not picks:
        head += ['', '<b>No setup met the bar today.</b> Nothing qualified on volume, trend, MACD and RSI together '
                 '(min score %d). Staying flat is a valid trade.' % cfg['min_score']]
    elif len(picks) < cfg['top_n']:
        head.append('Only %d setup(s) met the minimum score of %d; not padding the list.' % (len(picks), cfg['min_score']))
    max_daily = cfg['max_daily_loss'] or 2 * cfg['risk_per_trade']
    foot = ('<b>Day rules</b>: risk %s per trade · stop for the day after %s total loss · '
            'one entry per stock, no averaging down · check news/results before entry.\n'
            '<i>Rule-based research from delayed end-of-day data, not investment advice. '
            'Verify prices and tick size with your broker.</i>') % (inr(cfg['risk_per_trade'], 0), inr(max_daily, 0))
    blocks = ['\n'.join(head)] + [format_pick(i, p) for i, p in enumerate(picks, 1)] + [foot]
    messages, current = [], ''
    for block in blocks:
        candidate = current + ('\n\n' if current else '') + block
        if len(candidate) > TELEGRAM_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    messages.append(current)
    return messages


# --------------------------------------------------------------------------- data

def http_get(url, headers=None, timeout=20):
    request = urllib.request.Request(url, headers=headers or {'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(5_000_001)


def parse_yahoo_daily(doc, symbol):
    results = (doc.get('chart') or {}).get('result')
    if not results:
        raise ValueError('no chart')
    r = results[0]
    meta = r.get('meta') or {}
    if meta.get('symbol', '').upper() != symbol.upper() or meta.get('currency') != 'INR':
        raise ValueError('symbol/currency mismatch')
    q = r['indicators']['quote'][0]
    bars, seen = [], set()
    for i, t in enumerate(r.get('timestamp') or []):
        vals = [(q.get(k) or [None] * (i + 1))[i] if i < len(q.get(k) or []) else None
                for k in ('open', 'high', 'low', 'close', 'volume')]
        if any(v is None for v in vals):
            continue        # holiday/blank rows: skip, never invent prices
        o, h, l, c, v = map(float, vals)
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c) or v < 0:
            continue
        day = dt.datetime.fromtimestamp(t, IST).date()
        if day in seen:
            bars.pop()      # a late duplicate (the live tick) replaces the earlier row
        seen.add(day)
        bars.append(Bar(dt.datetime.combine(day, dt.time(9, 15), IST), o, h, l, c, v))
    return bars


def fetch_yahoo_daily(symbol, rng='1y'):
    path = '/v8/finance/chart/%s?range=%s&interval=1d' % (urllib.parse.quote(symbol), rng)
    last = 'unavailable'
    for host in ('query2', 'query1'):
        for attempt in range(2):
            try:
                return parse_yahoo_daily(json.loads(http_get('https://%s.finance.yahoo.com%s' % (host, path))), symbol)
            except urllib.error.HTTPError as exc:
                last = 'HTTP %d' % exc.code
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                last = str(exc) or type(exc).__name__
            if attempt == 0:
                time.sleep(1.5)
    raise ValueError('Yahoo: ' + last)


def completed_bars(bars, now, calendar, exchange='NSE'):
    return [b for b in bars if daily_completed(b.time.date(), now, calendar, exchange)]


def load_universe(cfg, state_dir):
    """(symbols, {symbol: sector}, source, warnings)."""
    if cfg['symbols']:
        return [symbol_name(s) for s in cfg['symbols']], {}, 'config symbols', []
    name = cfg['universe']
    if name not in INDEX_FILES:
        lines = Path(name).read_text().split()
        return [symbol_name(s if s.endswith(('.NS', '.BO')) else s + '.NS') for s in lines], {}, name, []
    cache = Path(state_dir) / ('universe-%s.csv' % name)
    warnings = []
    try:
        raw = http_get(INDEX_URL + INDEX_FILES[name], {'User-Agent': 'IndianTradingResearch/1.0'}).decode('utf-8-sig')
        if 'Symbol' not in raw.splitlines()[0]:
            raise ValueError('unexpected NSE index file')
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw)
        source = 'NSE %s constituents' % name.upper()
    except (OSError, ValueError, IndexError) as exc:
        if cache.is_file():
            raw = cache.read_text()
            source = 'cached %s list (NSE unreachable)' % name.upper()
            warnings.append('Universe from cache; NSE index list unreachable (%s)' % exc)
        else:
            warnings.append('NSE index list unreachable; fell back to a built-in NIFTY 50 list')
            return [s + '.NS' for s in FALLBACK_NIFTY50], {}, 'built-in NIFTY 50 fallback', warnings
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(raw)))
    symbols, sectors = [], {}
    for row in rows:
        if (row.get('Series') or 'EQ').strip() != 'EQ':
            continue
        try:
            s = symbol_name(row['Symbol'].strip() + '.NS')
        except ValueError:
            continue
        symbols.append(s)
        sectors[s] = (row.get('Industry') or '').strip()
    return symbols, sectors, source, warnings


# --------------------------------------------------------------------------- job

def load_config(path):
    cfg = dict(DEFAULTS)
    if path:
        with open(path) as fh:
            user = json.load(fh)
        unknown = set(user) - set(DEFAULTS) - {'_comment'}
        if unknown:
            raise ValueError('Unknown picks config keys: ' + ', '.join(sorted(unknown)))
        cfg.update({k: v for k, v in user.items() if k != '_comment'})
    for key in ('capital', 'risk_per_trade'):
        if not isinstance(cfg[key], (int, float)) or isinstance(cfg[key], bool) or not cfg[key] > 0:
            raise ValueError('%s must be a positive number' % key)
    if not isinstance(cfg['top_n'], int) or not 1 <= cfg['top_n'] <= 10:
        raise ValueError('top_n must be 1-10')
    if not isinstance(cfg['workers'], int) or not 1 <= cfg['workers'] <= 8:
        raise ValueError('workers must be 1-8')
    for key in ('alert_time', 'entry_after', 'entry_deadline', 'square_off'):
        hhmm(cfg[key])
    if not isinstance(cfg['learning'], bool):
        raise ValueError('learning must be true or false')
    for key, low in (('learn_min_trades', 3), ('learn_penalty', 0), ('learn_max_penalty', 0)):
        if not isinstance(cfg[key], int) or isinstance(cfg[key], bool) or not low <= cfg[key] <= 100:
            raise ValueError('%s must be a whole number from %d to 100' % (key, low))
    return cfg


def read_env_file(path):
    """KEY=VALUE lines; exported environment variables take precedence."""
    values = {}
    p = Path(path)
    if not p.is_file():
        return values
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def trade_date(now, calendar):
    """The session the plan is for: today before the close, else the next session."""
    hours = calendar.hours(now.date(), 'NSE')
    if hours and now.time() < hours[1]:
        return now.date()
    return calendar.next_open(now, 'NSE').date()


def launchd_plist(label, python, workdir, config, hour=8, minute=45):
    """Mon-Fri at hour:minute local time. The job itself skips NSE holidays."""
    from xml.sax.saxutils import escape as x
    days = ''.join('\n\t\t<dict><key>Weekday</key><integer>%d</integer><key>Hour</key><integer>%d</integer>'
                   '<key>Minute</key><integer>%d</integer></dict>' % (d, hour, minute) for d in range(1, 6))
    args = ''.join('\n\t\t<string>%s</string>' % x(s) for s in [python, '-m', 'india.picks', '--config', config])
    log = x(str(Path(workdir) / 'var' / 'picks.log'))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            '\t<key>Label</key>\n\t<string>%s</string>\n'
            '\t<key>ProgramArguments</key>\n\t<array>%s\n\t</array>\n'
            '\t<key>WorkingDirectory</key>\n\t<string>%s</string>\n'
            '\t<key>StartCalendarInterval</key>\n\t<array>%s\n\t</array>\n'
            '\t<key>StandardOutPath</key>\n\t<string>%s</string>\n'
            '\t<key>StandardErrorPath</key>\n\t<string>%s</string>\n'
            '</dict>\n</plist>\n') % (x(label), args, x(str(workdir)), days, log, log)


def run(cfg, now, fetch=fetch_yahoo_daily, calendar=None, log=lambda msg: None):
    """Scan, score and plan. Returns (picks, context). No network besides fetch/universe."""
    calendar = calendar or DEFAULT_CALENDAR
    warnings = list(calendar.coverage_warnings(now.date(), 'NSE'))
    data_day = calendar.last_completed_session(now, 'NSE')
    learned = (review.lessons(cfg['state_dir'], cfg['learn_min_trades'], cfg['learn_penalty'])
               if cfg['learning'] else None)
    symbols, sectors, source, uw = load_universe(cfg, cfg['state_dir'])
    warnings += uw
    log('universe: %d symbols from %s' % (len(symbols), source))

    def one(sym):
        try:
            bars = completed_bars(fetch(sym), now, calendar)
            if not bars or bars[-1].time.date() != data_day:
                return sym, None, 'stale data (last %s, expected %s)' % (bars[-1].time.date() if bars else 'none', data_day)
            return sym, features(bars), None
        except (ValueError, OSError, KeyError, TypeError, IndexError, ZeroDivisionError) as exc:
            return sym, None, str(exc)

    try:
        bias, market = market_bias(completed_bars(fetch(MARKET_INDEX), now, calendar))
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        bias, market = 'unknown', None
        warnings.append('NIFTY 50 trend unavailable; market filter not applied')
    with ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
        results = list(pool.map(one, symbols))
    scanned, failed, rejects, evaluated = 0, {}, {}, []
    for sym, f, err in results:
        if err:
            failed[sym] = err
            continue
        scanned += 1
        cand, reason = evaluate(f, cfg, bias)
        if reason:
            rejects[reason] = rejects.get(reason, 0) + 1
            continue
        plan = build_plan(sym, f, cand, cfg)
        if plan is None:
            rejects['position too small for risk budget'] = rejects.get('position too small for risk budget', 0) + 1
            continue
        plan.update(base_score=plan['score'], market_bias=bias, counter_trend=cand['counter_trend'])
        plan['traits'] = review.traits(plan)
        review.apply(plan, learned, cfg['learn_max_penalty'])
        evaluated.append(plan)
    if symbols and scanned < 0.8 * len(symbols):
        warnings.append('Only %d of %d symbols had fresh data' % (scanned, len(symbols)))
    picks = select(evaluated, cfg, sectors)
    ctx = {'trade_date': trade_date(now, calendar),
           'data_date': data_day.isoformat(), 'bias': bias, 'market': market,
           'universe': len(symbols), 'universe_source': source, 'scanned': scanned,
           'qualified': sum(1 for p in evaluated if p['score'] >= cfg['min_score']),
           'failed': failed, 'rejects': rejects, 'warnings': warnings, 'generated_at': now.isoformat(),
           'learning': {'trades': learned['trades'], 'active': [r['label'] for r in learned['active']],
                        'demoted': sum(1 for p in evaluated
                                       if p['base_score'] >= cfg['min_score'] > p['score'])} if learned else None,
           'execution_enabled': False}
    return picks, ctx


def send_telegram(token, chat_id, text, timeout=20):
    body = urllib.parse.urlencode({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
                                   'disable_web_page_preview': 'true'}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot%s/sendMessage' % token, data=body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            doc = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get('description', '')
        except ValueError:
            detail = ''
        raise ValueError('Telegram HTTP %d %s' % (exc.code, detail)) from None
    if not doc.get('ok'):
        raise ValueError('Telegram rejected the message: %s' % doc.get('description'))


# --------------------------------------------------------------------------- paper trading

FINAL_AFTER = dt.time(15, 40)   # Yahoo's last 5-minute candles settle a few minutes after the close
GIVE_UP_AFTER = dt.time(18, 0)   # after this, a final scorecard goes out even if some data is missing


def parse_yahoo_5m(doc, symbol, day, now):
    """Completed regular-session 5-minute candles for one IST date: [(start, o, h, l, c)]."""
    results = (doc.get('chart') or {}).get('result')
    if not results:
        raise ValueError('no chart')
    r = results[0]
    if (r.get('meta') or {}).get('symbol', '').upper() != symbol.upper():
        raise ValueError('symbol mismatch')
    q = r['indicators']['quote'][0]
    out = {}
    for i, t in enumerate(r.get('timestamp') or []):
        start = dt.datetime.fromtimestamp(t, IST)
        if (start.date() != day or start.second or start.minute % 5 or not dt.time(9, 15) <= start.time() < dt.time(15, 30)
                or start + dt.timedelta(minutes=5) > now):
            continue
        vals = [(q.get(k) or [])[i] if i < len(q.get(k) or []) else None for k in ('open', 'high', 'low', 'close')]
        if any(v is None for v in vals):
            continue
        o, h, l, c = map(float, vals)
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
            continue
        out[start] = (start, o, h, l, c)
    return [out[k] for k in sorted(out)]


def fetch_yahoo_5m(symbol, day, now):
    rng = '5d' if (now.date() - day).days <= 4 else '60d'
    path = '/v8/finance/chart/%s?range=%s&interval=5m' % (urllib.parse.quote(symbol), rng)
    last = 'unavailable'
    for host in ('query2', 'query1'):
        try:
            return parse_yahoo_5m(json.loads(http_get('https://%s.finance.yahoo.com%s' % (host, path))), symbol, day, now)
        except urllib.error.HTTPError as exc:
            last = 'HTTP %d' % exc.code
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            last = str(exc) or type(exc).__name__
        time.sleep(1)
    raise ValueError('Yahoo 5m: ' + last)


def simulate(plan, bars, now, session_close=dt.time(15, 30)):
    """Paper-trade one alert on 5-minute candles, following the alert's own rules.

    Entry: the first candle ENDING between entry_after and entry_deadline that
    closes beyond the entry level; filled at that close. A first candle opening
    beyond the no-chase level, or a trigger candle closing beyond it, is skipped.
    Exits, checked candle by candle (stop before targets within one candle, the
    conservative order): stop; T1 books half and moves the stop to the fill;
    T2 exits the rest; anything open at square_off exits at that candle's open.
    """
    sign = 1 if plan['side'] == 'BUY' else -1
    S = lambda x: sign * x
    E, SL, T1, T2, NC = (S(plan[k]) for k in ('entry', 'stop', 'target1', 'target2', 'no_chase'))
    after, deadline, square = hhmm(plan['entry_after']), hhmm(plan['entry_deadline']), hhmm(plan['square_off'])
    qty = plan['qty']
    base = {'symbol': plan['symbol'], 'side': plan['side'], 'score': plan['score'], 'qty_planned': qty,
            'entry_planned': plan['entry'], 'stop': plan['stop'], 'target1': plan['target1'],
            'target2': plan['target2'], 'no_chase': plan['no_chase'], 'max_loss_planned': plan['max_loss'],
            'events': [], 'net_pnl': None, 'r_multiple': None}
    day = bars[0][0].date() if bars else now.date()
    closed_day = now >= dt.datetime.combine(day, FINAL_AFTER, IST) or now.date() > day
    if not bars:
        return dict(base, status='NO_DATA' if closed_day else 'WAITING',
                    note='No 5-minute candles for the session' if closed_day else 'Waiting for the market')
    # Mirror shorts onto the long scale: high/low swap under negation.
    rows = [(t, S(o), max(S(h), S(l)), min(S(h), S(l)), S(c)) for t, o, h, l, c in bars]
    end = lambda t: (t + dt.timedelta(minutes=5)).time()
    if rows[0][0].time() == dt.time(9, 15) and rows[0][1] > NC:
        return dict(base, status='SKIPPED', note='Gapped past the no-chase level at the open (%s)' % inr(S(rows[0][1])))
    fill = k = None
    for j, (t, o, h, l, c) in enumerate(rows):
        if end(t) < after:
            continue
        if end(t) > deadline:
            break
        if c > E:
            if c > NC:
                return dict(base, status='SKIPPED', note='Trigger candle closed past the no-chase level (%s)' % inr(S(c)))
            fill, k = c, j
            break
    if fill is None:
        if end(rows[-1][0]) >= deadline or closed_day:
            return dict(base, status='NOT_TRIGGERED', note='Entry level not reached by %s' % plan['entry_deadline'])
        return dict(base, status='WAITING', note='Watching for a 5-minute close beyond %s' % inr(plan['entry']))

    entry_time = rows[k][0] + dt.timedelta(minutes=5)
    events = [{'time': entry_time.isoformat(), 'action': 'BUY' if sign > 0 else 'SELL SHORT', 'qty': qty,
               'price': round(S(fill), 2), 'reason': 'Entry trigger'}]
    half = qty // 2
    stop, open_q, legs, reason, last_close, t1_done = SL, qty, [], None, fill, False
    exit_action = 'SELL' if sign > 0 else 'BUY TO COVER'

    def leg(n, price, at, why):
        legs.append((n, price))
        events.append({'time': at.isoformat(), 'action': exit_action, 'qty': n, 'price': round(S(price), 2), 'reason': why})
    for t, o, h, l, c in rows[k + 1:]:
        last_close = c
        if t.time() >= square:
            leg(open_q, o, t, 'Square-off %s' % plan['square_off'])
            reason, open_q = reason + ' then square-off' if reason else 'Square-off', 0
            break
        if l <= stop:
            px = min(stop, o)     # a gap through the stop fills at the open
            why = 'Stop-loss' if stop == SL else 'Breakeven stop'
            leg(open_q, px, t, why)
            reason, open_q = (reason + ' then breakeven stop') if reason else 'Stop-loss', 0
            break
        if not t1_done and h >= T1:
            if half:
                leg(half, T1, t, 'Target 1: booked half')
            open_q, stop, reason, t1_done = qty - half, fill, 'Target 1', True
        if t1_done and h >= T2:
            leg(open_q, T2, t, 'Target 2')
            reason, open_q = 'Target 1 + Target 2', 0
            break

    def pnl(legs_):
        gross = sum(n * (x - fill) for n, x in legs_)
        cost = 0.0
        for n, x in legs_:
            if n:
                buy, sell = (S(fill), S(x)) if sign > 0 else (S(x), S(fill))
                cost += costs(buy, sell, n, 'NSE')['total']
        return gross, cost
    status = 'CLOSED'
    if open_q:
        if closed_day:
            leg(open_q, last_close, rows[-1][0] + dt.timedelta(minutes=5), 'Last available candle (feed ended early)')
            reason = (reason + ' then ' if reason else '') + 'closed at last candle'
        else:
            status = 'OPEN'
    realized = legs if status == 'CLOSED' else legs + [(open_q, last_close)]
    gross, cost = pnl(realized)
    net = gross - cost
    out = dict(base, status=status, events=events, entry_time=entry_time.isoformat(), entry_price=round(S(fill), 2),
               qty=qty, gross_pnl=round(gross, 2), costs=round(cost, 2), net_pnl=round(net, 2),
               r_multiple=round(net / plan['max_loss'], 2) if plan['max_loss'] else None,
               exit_reason=reason, open_qty=open_q)
    if status == 'OPEN':
        out.update(note='Open: %d share(s) marked at %s' % (open_q, inr(S(last_close))), mark=round(S(last_close), 2))
    return out


def ledger(state_dir, cfg=None):
    """All settled sessions, newest first, with running statistics and lessons."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    state = Path(state_dir)
    sessions = []
    for path in sorted(state.glob('results-*.json')):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get('trades'), list):
            sessions.append(doc)
    trades = [dict(t, session=s['session']) for s in sessions for t in s['trades']]
    done = [t for t in trades if t['status'] == 'CLOSED']
    nets = [t['net_pnl'] for t in done]
    curve, total = [], 0.0
    for s in sessions:
        if s.get('final'):
            day = sum(t['net_pnl'] for t in s['trades'] if t['status'] == 'CLOSED')
            total += day
            curve.append({'session': s['session'], 'day_pnl': round(day, 2), 'cumulative': round(total, 2)})
    wins = [x for x in nets if x > 0]
    losses = [-x for x in nets if x < 0]
    summary = {
        'sessions': len(sessions), 'alerts': len(trades), 'triggered': len(done) + sum(t['status'] == 'OPEN' for t in trades),
        'closed': len(done), 'open': sum(t['status'] == 'OPEN' for t in trades),
        'not_triggered': sum(t['status'] == 'NOT_TRIGGERED' for t in trades),
        'skipped': sum(t['status'] == 'SKIPPED' for t in trades),
        'net_pnl': round(sum(nets), 2), 'costs': round(sum(t['costs'] for t in done), 2),
        'win_rate': round(100 * len(wins) / len(nets), 1) if nets else None,
        'avg_r': round(sum(t['r_multiple'] for t in done) / len(done), 3) if done else None,
        'profit_factor': round(sum(wins) / sum(losses), 2) if losses else None,
        'best': max(nets) if nets else None, 'worst': min(nets) if nets else None,
        'open_pnl': round(sum(t['net_pnl'] for t in trades if t['status'] == 'OPEN'), 2),
    }
    by_side = {}
    for side in ('BUY', 'SELL'):
        s = [t for t in done if t['side'] == side]
        if s:
            by_side[side] = {'closed': len(s), 'net_pnl': round(sum(t['net_pnl'] for t in s), 2),
                             'win_rate': round(100 * sum(t['net_pnl'] > 0 for t in s) / len(s), 1)}
    summary['by_side'] = by_side
    settled = {s['session'] for s in sessions}
    upcoming = []
    for path in sorted(state.glob('picks-*.json')):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        day = doc.get('trade_date') if isinstance(doc, dict) else None
        if day and path.name == 'picks-%s.json' % day and day not in settled:
            upcoming.append({'session': day, 'picks': [{k: p.get(k) for k in (
                'symbol', 'side', 'score', 'base_score', 'lesson_penalty', 'lessons', 'entry', 'no_chase', 'stop', 'target1',
                'target2', 'qty', 'max_loss', 'risk_rating')}
                for p in doc.get('picks', [])]})
    learned = review.lessons(state, cfg['learn_min_trades'], cfg['learn_penalty'])
    learned['enabled'] = bool(cfg['learning'])
    return {'summary': summary, 'curve': curve, 'sessions': list(reversed(sessions)), 'upcoming': upcoming,
            'lessons': learned, 'execution_enabled': False,
            'note': 'Simulated fills on delayed 5-minute candles, following each alert\'s rules. '
                    'Costs are estimates. No orders were placed.'}


def settle(cfg, day, now, fetch=None):
    """Paper-trade every alert sent for `day`. Returns the results document, or None without an alert."""
    fetch = fetch or fetch_yahoo_5m
    path = Path(cfg['state_dir']) / ('picks-%s.json' % day.isoformat())
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    if record.get('trade_date') != day.isoformat():
        raise ValueError('%s does not describe the %s session' % (path.name, day))
    out = Path(cfg['state_dir']) / ('results-%s.json' % day.isoformat())
    previous = {}
    if out.is_file():
        try:
            previous = {t['symbol']: t for t in json.loads(out.read_text()).get('trades', [])
                        if t.get('status') != 'ERROR'}
        except (ValueError, KeyError, TypeError, AttributeError):
            previous = {}
    trades, missing = [], False
    try:
        market = fetch(MARKET_INDEX, day, now)
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        market = None      # the review just leaves out NIFTY's move
    for plan in record.get('picks', []):
        try:
            bars = fetch(plan['symbol'], day, now)
            trade = simulate(plan, bars, now)
            trade['traits'] = plan.get('traits') or review.traits(plan)
            try:
                trade['diagnosis'] = review.diagnose(plan, trade, bars, market)
            except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as exc:
                trade['diagnosis'] = {'outcome': None, 'tags': [], 'why': '', 'error': str(exc)}
            trades.append(trade)
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            missing = True
            if plan['symbol'] in previous:
                # A transient outage (e.g. DNS right after a container restart) must not
                # wipe a good result; keep the last one and say it is stale.
                kept = dict(previous[plan['symbol']], stale=True)
                kept['note'] = 'data unavailable (%s); last result kept' % exc
                trades.append(kept)
                continue
            trades.append({'symbol': plan['symbol'], 'side': plan['side'], 'score': plan['score'],
                           'entry_planned': plan['entry'], 'stop': plan['stop'], 'target1': plan['target1'],
                           'target2': plan['target2'], 'qty_planned': plan['qty'], 'events': [],
                           'status': 'ERROR', 'note': str(exc), 'net_pnl': None, 'r_multiple': None})
    final = now >= dt.datetime.combine(day, FINAL_AFTER, IST) or now.date() > day
    if final and missing and now < dt.datetime.combine(day, GIVE_UP_AFTER, IST):
        final = False    # retry on the next settle instead of scoring incomplete data
    doc = {'session': day.isoformat(), 'updated_at': now.isoformat(), 'final': final, 'trades': trades,
           'execution_enabled': False}
    out.write_text(json.dumps(doc, indent=2))
    return doc


def format_scorecard(doc, book):
    day = dt.date.fromisoformat(doc['session'])
    lines = ['📒 <b>Paper results · %s</b>' % day.strftime('%a %d %b %Y')]
    if not doc['trades']:
        lines.append('No setups were alerted for this session.')
    for t in doc['trades']:
        name = html.escape(t['symbol'].rsplit('.', 1)[0])
        if t['status'] == 'CLOSED':
            icon = '✅' if t['net_pnl'] > 0 else '❌' if t['net_pnl'] < 0 else '➖'
            entry = t['events'][0]
            exits = ', '.join('%d @ %s %s' % (e['qty'], inr(e['price']), e['time'][11:16]) for e in t['events'][1:])
            lines.append('%s <b>%s %s</b> · in %s @ %s → out %s\n    %s · net <b>%s</b> (%+.2fR)' % (
                icon, name, t['side'], entry['time'][11:16], inr(entry['price']), exits,
                html.escape(t['exit_reason']), inr(t['net_pnl'], 0), t['r_multiple']))
        else:
            lines.append('➖ <b>%s %s</b> · %s' % (name, t['side'], html.escape(t.get('note') or t['status'])))
        why = (t.get('diagnosis') or {}).get('why')
        if why:
            lines.append('    <i>Why: %s</i>' % html.escape(why))
    day_net = sum(t['net_pnl'] for t in doc['trades'] if t['status'] == 'CLOSED')
    s = book['summary']
    lines += ['', 'Day: <b>%s</b>' % inr(day_net, 0),
              'Running total: <b>%s</b> over %d closed trade(s) · win rate %s · avg %sR' % (
                  inr(s['net_pnl'], 0), s['closed'], '%.0f%%' % s['win_rate'] if s['win_rate'] is not None else '—',
                  '%+.2f' % s['avg_r'] if s['avg_r'] is not None else '—')]
    learned = book.get('lessons')
    if learned:
        if learned['active']:
            lines.append('📚 Active lessons (lower future scores): ' + '; '.join(
                html.escape(r['evidence']) for r in learned['active']))
        else:
            lines.append('📚 Learning: %d closed trade(s) so far; a trait needs %d before it can become a lesson.' % (
                learned['trades'], learned['min_trades']))
        for p in learned['patterns']:
            if p['notable']:
                lines.append('💡 ' + html.escape(p['suggestion']))
    lines += ['<i>Paper trades on delayed 5-minute candles; costs estimated. Details in the dashboard.</i>']
    return '\n'.join(lines)


def run_settle(cfg, now, day, calendar, token, chat, dry_run, log):
    try:
        trading = calendar.trading_day(day, 'NSE')
    except ValueError as exc:
        return {'status': 'SKIPPED', 'reason': 'calendar: %s' % exc, 'date': day.isoformat()}
    if not trading:
        return {'status': 'SKIPPED', 'reason': 'not an NSE trading day', 'date': day.isoformat()}
    state = Path(cfg['state_dir'])
    scored = state / ('scored-%s' % day.isoformat())
    if scored.exists() and not dry_run:
        return {'status': 'SKIPPED', 'reason': 'session already settled', 'date': day.isoformat()}
    if now < dt.datetime.combine(day, dt.time(9, 20), IST):
        return {'status': 'SKIPPED', 'reason': 'market not open yet', 'date': day.isoformat()}
    doc = settle(cfg, day, now)
    if doc is None:
        return {'status': 'SKIPPED', 'reason': 'no alert was sent for this session', 'date': day.isoformat()}
    log('settled %s: %s' % (day, ', '.join('%s %s' % (t['symbol'], t['status']) for t in doc['trades'])))
    if doc['final']:
        text = format_scorecard(doc, ledger(state, cfg))
        if dry_run:
            print(text)
        elif token and chat:
            send_telegram(token, chat, text)
            scored.write_text(now.isoformat())
    return {'status': 'FINAL' if doc['final'] else 'PROVISIONAL', 'date': day.isoformat(),
            'trades': [{k: t.get(k) for k in ('symbol', 'status', 'net_pnl', 'r_multiple')} for t in doc['trades']]}


def launchd_settle_plist(label, python, workdir, config, every=600):
    """Every `every` seconds; the job itself does nothing outside NSE sessions."""
    from xml.sax.saxutils import escape as x
    args = ''.join('\n\t\t<string>%s</string>' % x(s) for s in [python, '-m', 'india.picks', '--config', config, '--settle'])
    log = x(str(Path(workdir) / 'var' / 'picks-settle.log'))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            '\t<key>Label</key>\n\t<string>%s</string>\n'
            '\t<key>ProgramArguments</key>\n\t<array>%s\n\t</array>\n'
            '\t<key>WorkingDirectory</key>\n\t<string>%s</string>\n'
            '\t<key>StartInterval</key>\n\t<integer>%d</integer>\n'
            '\t<key>StandardOutPath</key>\n\t<string>%s</string>\n'
            '\t<key>StandardErrorPath</key>\n\t<string>%s</string>\n'
            '</dict>\n</plist>\n') % (x(label), args, x(str(workdir)), every, log, log)


def serve(base_args, cfg, calendar, now_fn=lambda: dt.datetime.now(IST), sleep=time.sleep,
          main_fn=None, iterations=None, log=lambda msg: print(msg, flush=True)):
    """Long-running scheduler for a container: morning alert + 10-minute settles.

    The alert runs once per session between alert_time and entry_deadline on NSE
    trading days, retried at most 3 times, 20 minutes apart. Settling runs every
    10 minutes; it does nothing outside sessions or once a session is scored.
    A heartbeat file lets the container health check see the loop is alive.
    """
    main_fn = main_fn or main
    state = Path(cfg['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    attempts, last_settle, n = {}, None, 0
    log('scheduler started: alert %s IST on NSE trading days, settle every 10 minutes' % cfg['alert_time'])
    while iterations is None or n < iterations:
        n += 1
        now = now_fn()
        today = now.date()
        try:
            trading = calendar.trading_day(today, 'NSE')
        except ValueError:
            trading = today.weekday() < 5    # let the alert job report the calendar gap
        in_window = hhmm(cfg['alert_time']) <= now.time() < hhmm(cfg['entry_deadline'])
        if trading and in_window and not (state / ('sent-%s' % today.isoformat())).exists():
            count, last = attempts.get(today, (0, None))
            if count < 3 and (last is None or now - last >= dt.timedelta(minutes=20)):
                attempts[today] = (count + 1, now)
                log('%s running morning alert (attempt %d)' % (now.isoformat(timespec='seconds'), count + 1))
                try:
                    main_fn(base_args)
                except Exception as exc:          # never let one run kill the service
                    log('alert run crashed: %r' % exc)
        if last_settle is None or now - last_settle >= dt.timedelta(minutes=10):
            last_settle = now
            try:
                main_fn(base_args + ['--settle'])
            except Exception as exc:
                log('settle run crashed: %r' % exc)
        (state / 'heartbeat').write_text(now.isoformat())
        if iterations is None or n < iterations:
            sleep(30)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Send the daily top NSE trade setups to Telegram (research only).')
    ap.add_argument('--config', help='JSON settings (see config/picks.example.json)')
    ap.add_argument('--env-file', default='var/telegram.env', help='KEY=VALUE file with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID')
    ap.add_argument('--dry-run', action='store_true', help='print the message instead of sending it')
    ap.add_argument('--force', action='store_true', help='run on a holiday or resend today')
    ap.add_argument('--as-of', help='pretend it is this IST time (ISO 8601 with offset), for testing')
    ap.add_argument('--test-message', action='store_true', help='send a one-line Telegram test and exit')
    ap.add_argument('--json', action='store_true', help='print the full result as JSON')
    ap.add_argument('--print-launchd', metavar='HH:MM', help='print a macOS LaunchAgent plist for Mon-Fri at HH:MM and exit')
    ap.add_argument('--settle', action='store_true',
                    help='paper-trade the alerts sent for a session on 5-minute candles; after the close, send the scorecard')
    ap.add_argument('--date', help='session to settle, YYYY-MM-DD (default: today)')
    ap.add_argument('--print-launchd-settle', action='store_true', help='print a LaunchAgent plist that settles every 10 minutes')
    ap.add_argument('--serve', action='store_true', help='run forever: morning alert at alert_time and settles every 10 minutes (for Docker)')
    a = ap.parse_args(argv)
    if a.print_launchd or a.print_launchd_settle:
        config = str(Path(a.config or 'config/picks.json').resolve())
        if a.print_launchd_settle:
            print(launchd_settle_plist('com.daytrader.picks.settle', sys.executable, os.getcwd(), config))
        else:
            when = hhmm(a.print_launchd)
            print(launchd_plist('com.daytrader.picks', sys.executable, os.getcwd(), config, when.hour, when.minute))
        return 0
    log = lambda msg: print(msg, file=sys.stderr)
    token = chat = None
    try:
        cfg = load_config(a.config)
        env = dict(read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith('TELEGRAM_')})
        token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        if not a.dry_run and not (token and chat):
            raise ValueError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (environment or %s), or use --dry-run' % a.env_file)
        if a.test_message:
            send_telegram(token, chat, '✅ Trade-setup alerts are connected. Daily picks will arrive here before the open.')
            print('Telegram test message sent.')
            return 0
        now = dt.datetime.fromisoformat(a.as_of).astimezone(IST) if a.as_of else dt.datetime.now(IST)
        calendar = Calendar.load(cfg['calendar']) if cfg['calendar'] else DEFAULT_CALENDAR
        state = Path(cfg['state_dir'])
        if a.serve:
            base = (['--config', a.config] if a.config else []) + ['--env-file', a.env_file]
            serve(base, cfg, calendar)
            return 0
        if a.settle:
            day = dt.date.fromisoformat(a.date) if a.date else now.date()
            print(json.dumps(run_settle(cfg, now, day, calendar, token, chat, a.dry_run, log)))
            return 0
        try:
            trading = calendar.trading_day(now.date(), 'NSE')
        except ValueError as exc:
            raise ValueError('NSE holiday calendar does not cover %s; add the new year to the calendar file (%s)' % (now.date(), exc))
        if not trading and not a.force:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'not an NSE trading day', 'date': now.date().isoformat()}))
            return 0
        # A sleeping Mac runs a missed launchd job on wake. Mid-session the
        # morning plan is stale (entries close at entry_deadline): skip it.
        hours = calendar.hours(now.date(), 'NSE')
        if hours and hhmm(cfg['entry_deadline']) <= now.time() < hours[1] and not a.force:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'too late for today\'s entry window', 'date': now.date().isoformat()}))
            return 0
        session_day = trade_date(now, calendar).isoformat()
        sent_marker = state / ('sent-%s' % session_day)
        if sent_marker.exists() and not a.force and not a.dry_run:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'already sent for this session', 'date': session_day}))
            return 0
        picks, ctx = run(cfg, now, calendar=calendar, log=log)
        messages = format_messages(picks, ctx, cfg)
        record = dict(ctx, trade_date=ctx['trade_date'].isoformat(), picks=picks, config=cfg)
        if a.dry_run:
            print('\n\n----- message -----\n\n'.join(messages))
        else:
            for text in messages:
                send_telegram(token, chat, text)
            # Only a sent alert is recorded: it is what the paper trades follow.
            state.mkdir(parents=True, exist_ok=True)
            (state / ('picks-%s.json' % session_day)).write_text(json.dumps(record, indent=2, default=str))
            sent_marker.write_text(now.isoformat())
        if a.json:
            print(json.dumps(record, indent=2, default=str))
        log('%s %d pick(s); scanned %d/%d; failed %d' % ('prepared' if a.dry_run else 'sent', len(picks),
                                                          ctx['scanned'], ctx['universe'], len(ctx['failed'])))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        # Tell the user the alert failed instead of failing silently.
        try:
            # The settle job repeats every 10 minutes: log its failures, don't page.
            if not a.dry_run and not a.test_message and not a.settle and token and chat:
                send_telegram(token, chat, '⚠️ Daily trade-setup alert failed: %s' % html.escape(str(exc)))
        except (ValueError, OSError):
            pass
        return 1


if __name__ == '__main__':
    sys.exit(main())
