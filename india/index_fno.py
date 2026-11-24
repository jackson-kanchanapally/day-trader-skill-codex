"""NIFTY / BANK NIFTY index option signals to Telegram, run like an options desk (paper research only).

Before the open (`brief_time`, 09:05) a desk brief: yesterday's high/low/close,
the central pivot range (CPR) and R1/S1, the daily trend (EMA20/EMA50), ATR,
the India VIX regime and the structure it implies, the expiry in use and, with
a live chain, PCR, max pain and the call/put OI walls.

Signals, per index, once per session:

  1. Opening range: high and low of the first `range_minutes` (09:15-09:45)
     from the recorder's sampled five-minute index candles, all usable.
  2. Breakout: the first five-minute CLOSE beyond the range by a buffer
     (`breakout_buffer_pct` of price or 10% of the range, whichever is more)
     before `entry_deadline`. Range width outside min/max, India VIX above
     `max_vix`, an `event_dates` day, or a breakout seen more than
     `fresh_minutes` late means no trade.
  3. Grade (0-100) from: daily-trend alignment, CPR width (narrow = trend
     day), room to the next level (PDH/PDL, R1/S1, OI wall) in R, candle
     strength, range quality, the VIX regime and PCR. A level closer than 1R
     blocks the trade. Only grades at or above `min_score` are traded.
  4. Structure: IV at or below `buy_iv_max` buys the option outright (ATM, or
     one strike in the money when the expiry is three sessions or less away);
     above it, a debit spread (buy ATM, sell at the T2 strike) caps the vega
     and theta paid. Never a naked short. The expiry is the first one at
     least `min_dte` sessions away. Lots are sized so the stop costs at most
     `risk_per_trade` including charges.
     Greeks: each structure's net delta, gamma, theta and vega (from the
     chain, else Black-Scholes) decide too. Net delta under `min_delta` is
     refused (the premium barely follows the index); theta paid by the time
     stop above `max_theta_share` of the premium gain at T1 is refused. Vega:
     India VIX tends to fall as the index rises, so IV is cut by
     `iv_shock_pct` wherever the index rallies (a call's T1/T2, a put's
     stop). High-vega structures therefore earn less or risk more, carry
     fewer lots and must still clear `min_option_rr`.
  5. Management on the spot index: the stop is where the breakout is proven
     wrong (`stop_mode` "candle": back through the breakout candle, at least a
     quarter inside the range; "range": the far side). T1 at 1R books half
     and moves the stop to cost; the rest trails each completed 5-minute
     low/high (`runner` "trail") or exits at T2, else at the theta time stop
     (`time_stop_minutes` without T1) or `square_off`.
  6. A professional's account rules: one losing trade ends the day's entries
     (`one_loss_a_day`); a weekly loss of `weekly_loss_limit_r` stops new
     trades until Monday; `streak_half_after` losses in a row or a drawdown of
     `drawdown_half_r` from the equity peak halve the size. The brief shows
     the overnight global cues and the desk's record; the evening learning
     note carries the journal.

Everything is free and needs no broker account: spot from NSE's official MCP,
premiums estimated with Black-Scholes from NSE's India VIX (scaled per index by
the realised-volatility ratio to NIFTY). Alerts therefore lead with the
index levels, show premiums as approximate, and give the option's delta so a
different broker premium can be traded off the same index levels.
`chain_source: "upstox"` is an opt-in live chain for existing Upstox users.
Spot candles are sampled from NSE's delayed snapshot, not exchange candles.

    python3 -m india.index_fno --config config/picks.json --dry-run
"""
import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from . import alert_switches as S
from . import options as O
from . import picks as P
from .data import IST, timestamp
from .market import DEFAULT_CALENDAR, Calendar
from .recorder import load_candles

CONTRACTS = {
    # Verify lot sizes and expiry days against NSE circulars; NSE revises them.
    'NIFTY 50': dict(short='NIFTY', step=50, lot=65, expiry_weekday=1, monthly=False, iv_scale=1.0,
                     upstox_key='NSE_INDEX|Nifty 50', history='Nifty 50'),
    'NIFTY BANK': dict(short='BANKNIFTY', step=100, lot=30, expiry_weekday=1, monthly=True, iv_scale=1.2,
                       upstox_key='NSE_INDEX|Nifty Bank', history='Nifty Bank'),
}
DEFAULTS = dict(
    state_dir='var/state/index_fno',
    indices=['NIFTY 50', 'NIFTY BANK'],
    vix_index='INDIA VIX',
    brief_time='09:05',
    range_minutes=30,
    entry_deadline='13:30',
    square_off='15:15',
    min_range_pct=0.15,
    max_range_pct=1.2,
    breakout_buffer_pct=0.05,
    max_vix=25.0,            # null disables the VIX ceiling
    buy_iv_max=16.0,         # IV (VIX-based) at or below: buy outright; above: debit spread
    min_score=60,
    target_r=[1.0, 2.0],
    fresh_minutes=10,
    time_stop_minutes=60,
    stop_mode='candle',      # range: far side of the opening range | candle: the breakout candle's far end, >= 1/4 inside
    runner='trail',          # after T1 the rest: target exits at T2 | trail follows each completed 5-min low/high
    min_dte=2,
    risk_per_trade=None,     # INR at the stop incl. charges; null uses the picks risk_per_trade
    max_daily_loss=None,     # INR: realised losses + open risk across all indices; null = 2 x risk_per_trade
    max_open_trades=1,       # options trades open at once across indices
    max_spread_pct=5.0,      # skip when an option's bid/ask spread exceeds this % of its price
    max_premium_loss_pct=45.0,
    min_option_rr=1.5,       # premium reward to T2 per rupee risked, after charges
    min_delta=0.10,          # net delta of the structure at or above: the premium must follow the index
    max_theta_share=0.25,    # theta paid by the time stop, at most this share of the premium gain at T1
    iv_shock_pct=5.0,        # vega: IV falls this % where the index rallies (calls' targets, puts' stop); 0 = off
    rate=0.065,
    chain_source='model',    # model (free, India VIX) | upstox (opt-in; needs an Upstox account token)
    send_skips=True,         # one line when a breakout is not traded, with the reason
    event_dates=[],          # YYYY-MM-DD: RBI policy, budget, results of heavyweights...
    contracts={},            # per-index overrides of CONTRACTS
    learning='decide',       # decide | advise | off: the model learned from past breakouts (india/index_fno_learn.py)
    learn_threshold=0.55,    # with "decide": take a breakout when the model's P(win) is at least this
    learn_min_trades=25,     # below this many learned breakouts the grade rules decide
    one_loss_a_day=True,     # a losing trade ends the day's new entries (no revenge trades)
    weekly_loss_limit_r=3.0, # realised loss this week, in R, that stops new trades until Monday; null = off
    streak_half_after=2,     # this many losing trades in a row halve the size until a winner; null = off
    drawdown_half_r=5.0,     # equity this many R below its peak halves the size; null = off
    global_cues=True,        # overnight US/Asia/crude/rupee moves in the brief (Yahoo, free)
)
FINAL = frozenset(('NO_RANGE', 'SKIPPED_RANGE', 'SKIPPED_VIX', 'SKIPPED_EVENT', 'SKIPPED_WEAK', 'SKIPPED_OPTION',
                   'SKIPPED_RISK', 'SKIPPED_MODEL',
                   'NO_BREAKOUT', 'MISSED', 'CLOSED'))
SETTLE = dt.timedelta(minutes=3)      # wait this long after a window for the recorder to finalize it


def _positive(cfg, key, high):
    v = cfg[key]
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= high:
        raise ValueError('index_fno.%s must be a number above 0 and at most %s' % (key, high))


def settings(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError('index_fno settings must be an object')
    supplied = value or {}
    unknown = set(supplied) - set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown index_fno settings: ' + ', '.join(sorted(unknown)))
    cfg = dict(DEFAULTS, **supplied)
    if not isinstance(cfg['state_dir'], str) or not cfg['state_dir'].strip():
        raise ValueError('index_fno.state_dir must be a nonempty path')
    overrides = cfg['contracts']
    if not isinstance(overrides, dict) or any(not isinstance(v, dict) for v in overrides.values()):
        raise ValueError('index_fno.contracts maps index names to objects')
    contracts = {}
    for name in set(CONTRACTS) | set(overrides):
        extra = set(overrides.get(name, {})) - set(CONTRACTS['NIFTY 50'])
        if extra:
            raise ValueError('Unknown index_fno.contracts.%s keys: %s' % (name, ', '.join(sorted(extra))))
        c = dict(CONTRACTS.get(name, {}), **overrides.get(name, {}))
        if set(c) != set(CONTRACTS['NIFTY 50']):
            raise ValueError('index_fno.contracts.%s needs: %s' % (name, ', '.join(sorted(CONTRACTS['NIFTY 50']))))
        if any(not isinstance(c[k], int) or isinstance(c[k], bool) or c[k] <= 0 for k in ('step', 'lot')) \
                or c['expiry_weekday'] not in range(5) or not isinstance(c['monthly'], bool) \
                or not isinstance(c['iv_scale'], (int, float)) or not 0.5 <= c['iv_scale'] <= 3:
            raise ValueError('index_fno.contracts.%s has an invalid step, lot, expiry_weekday, monthly or iv_scale' % name)
        contracts[name] = c
    cfg['contracts'] = contracts
    names = cfg['indices']
    if (not isinstance(names, list) or not names or len(names) > 5 or len(set(names)) != len(names)
            or any(n not in contracts for n in names)):
        raise ValueError('index_fno.indices must list 1-5 unique indices with contract details (%s)' % ', '.join(sorted(contracts)))
    if not isinstance(cfg['vix_index'], str) or not cfg['vix_index'].strip():
        raise ValueError('index_fno.vix_index must be an NSE index name')
    if cfg['range_minutes'] not in (5, 10, 15, 30):
        raise ValueError('index_fno.range_minutes must be 5, 10, 15 or 30')
    for key in ('brief_time', 'entry_deadline', 'square_off'):
        P.hhmm(cfg[key])
    if not P.hhmm(cfg['brief_time']) < dt.time(9, 15) <= P.hhmm(cfg['entry_deadline']) < P.hhmm(cfg['square_off']) <= dt.time(15, 25):
        raise ValueError('index_fno needs brief_time < 09:15 <= entry_deadline < square_off <= 15:25')
    for key, high in (('min_range_pct', 5), ('max_range_pct', 5), ('breakout_buffer_pct', 1), ('buy_iv_max', 100),
                      ('max_spread_pct', 50), ('max_premium_loss_pct', 90), ('rate', 0.2), ('min_option_rr', 10),
                      ('min_delta', 1), ('max_theta_share', 1)):
        _positive(cfg, key, high)
    if cfg['min_range_pct'] >= cfg['max_range_pct']:
        raise ValueError('index_fno.min_range_pct must be below max_range_pct')
    if cfg['max_vix'] is not None:
        _positive(cfg, 'max_vix', 100)
    v = cfg['iv_shock_pct']
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 50:
        raise ValueError('index_fno.iv_shock_pct must be a number from 0 to 50')
    t = cfg['target_r']
    if (not isinstance(t, list) or len(t) != 2 or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in t)
            or not 0 < t[0] < t[1] <= 5):
        raise ValueError('index_fno.target_r must be [T1, T2] with 0 < T1 < T2 <= 5')
    for key, low, high in (('fresh_minutes', 5, 30), ('time_stop_minutes', 15, 240), ('min_dte', 0, 10),
                           ('min_score', 0, 100)):
        v = cfg[key]
        if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high:
            raise ValueError('index_fno.%s must be a whole number from %d to %d' % (key, low, high))
    for key in ('risk_per_trade', 'max_daily_loss'):
        if cfg[key] is not None:
            _positive(cfg, key, 1e7)
    if cfg['max_open_trades'] not in (1, 2, 3):
        raise ValueError('index_fno.max_open_trades must be 1, 2 or 3')
    if cfg['learning'] not in ('decide', 'advise', 'off'):
        raise ValueError('index_fno.learning must be "decide", "advise" or "off"')
    v = cfg['learn_threshold']
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.3 <= v <= 0.9:
        raise ValueError('index_fno.learn_threshold must be a number from 0.3 to 0.9')
    v = cfg['learn_min_trades']
    if not isinstance(v, int) or isinstance(v, bool) or not 10 <= v <= 1000:
        raise ValueError('index_fno.learn_min_trades must be a whole number from 10 to 1000')
    if not isinstance(cfg['one_loss_a_day'], bool) or not isinstance(cfg['global_cues'], bool):
        raise ValueError('index_fno.one_loss_a_day and global_cues must be true or false')
    for key in ('weekly_loss_limit_r', 'drawdown_half_r'):
        if cfg[key] is not None:
            _positive(cfg, key, 50)
    v = cfg['streak_half_after']
    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 10):
        raise ValueError('index_fno.streak_half_after must be a whole number from 1 to 10, or null')
    if cfg['stop_mode'] not in ('range', 'candle'):
        raise ValueError('index_fno.stop_mode must be "range" or "candle"')
    if cfg['runner'] not in ('target', 'trail'):
        raise ValueError('index_fno.runner must be "target" or "trail"')
    if cfg['chain_source'] not in ('model', 'upstox'):
        raise ValueError('index_fno.chain_source must be "model" (free) or "upstox"')
    if not isinstance(cfg['send_skips'], bool):
        raise ValueError('index_fno.send_skips must be true or false')
    dates = cfg['event_dates']
    if not isinstance(dates, list):
        raise ValueError('index_fno.event_dates must be a list of YYYY-MM-DD dates')
    for d in dates:
        dt.date.fromisoformat(d)
    cfg['indices'], cfg['target_r'], cfg['event_dates'] = list(names), [float(x) for x in t], list(dates)
    return cfg


# --------------------------------------------------------------------------- daily context

def _ema(values, n):
    k, out = 2 / (n + 1), None
    for v in values:
        out = v if out is None else out + k * (v - out)
    return out


def daily_context(bars):
    """Levels a desk marks before the open, from completed daily bars (oldest first)."""
    if not bars:
        return None
    y = bars[-1]
    h, l, c = y['high'], y['low'], y['close']
    pivot = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pivot - bc
    closes = [b['close'] for b in bars]
    trs = [max(b['high'], p['close']) - min(b['low'], p['close']) for p, b in zip(bars, bars[1:])]
    rets = [math.log(b / a) for a, b in zip(closes[-21:], closes[-20:]) if a > 0 and b > 0]
    rv20 = None
    if len(rets) >= 10:
        mean = sum(rets) / len(rets)
        rv20 = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) * 252) * 100
    ema20 = _ema(closes[-60:], 20) if len(closes) >= 20 else None
    ema50 = _ema(closes[-150:], 50) if len(closes) >= 50 else None
    trend = 'unknown'
    if ema20 and ema50:
        trend = 'up' if c > ema20 > ema50 else 'down' if c < ema20 < ema50 else 'mixed'
    return dict(date=y['date'], open=y['open'], high=h, low=l, close=c,
                pivot=round(pivot, 2), bc=round(min(bc, tc), 2), tc=round(max(bc, tc), 2),
                cpr_pct=round(abs(tc - bc) / pivot * 100, 3),
                r1=round(2 * pivot - l, 2), s1=round(2 * pivot - h, 2),
                r2=round(pivot + (h - l), 2), s2=round(pivot - (h - l), 2),
                ema20=round(ema20, 2) if ema20 else None, ema50=round(ema50, 2) if ema50 else None, trend=trend,
                atr=round(sum(trs[-14:]) / len(trs[-14:]), 2) if len(trs) >= 14 else None,
                rv20=round(rv20, 2) if rv20 else None)


def fetch_daily_bars(cfg, months=8, end='today'):
    """Daily bars per index and India VIX from NSE's historical MCP: {name: [bar, ...]} oldest first."""
    from .nse_mcp import BHAVCOPY, MCPSession
    session = MCPSession(BHAVCOPY)
    names = {n: cfg['contracts'][n]['history'] for n in cfg['indices']}
    names[cfg['vix_index']] = cfg['vix_index'].title().replace('Vix', 'VIX')
    out = {}
    for name, history in names.items():
        doc = session.call('get_index_history', {'indexName': history, 'months': months, 'endDate': end})
        rows = [r for r in doc.get('data') or [] if r.get('date') and r.get('close') is not None]
        out[name] = sorted((dict(date=r['date'], open=float(r.get('open') or r['close']),
                                 high=float(r.get('high') or r['close']), low=float(r.get('low') or r['close']),
                                 close=float(r['close'])) for r in rows), key=lambda b: b['date'])
        if not out[name]:
            raise ValueError('NSE returned no daily history for %s' % history)
    return out


def contexts_for(daily, day):
    """Each series' context from bars strictly before `day` (no look-ahead)."""
    return {name: daily_context([b for b in bars if b['date'] < day.isoformat()]) for name, bars in daily.items()}


def fetch_context(cfg, today):
    """Yesterday's levels per index plus India VIX's last close, from NSE's historical MCP."""
    return contexts_for(fetch_daily_bars(cfg), today)


# --------------------------------------------------------------------------- grading and structure

def _num(x, places=1):
    return '{:,.{p}f}'.format(x, p=places)


def _t(iso):
    return timestamp(iso).astimezone(IST)


def _price(c, key):
    return c[key] if c.get(key) is not None else c['close']


def vix_regime(vix, cfg):
    if vix is None:
        return 'unknown', 'India VIX unavailable'
    if cfg['max_vix'] is not None and vix > cfg['max_vix']:
        return 'extreme', 'VIX %.1f: no new longs on premium' % vix
    if vix <= cfg['buy_iv_max']:
        return 'cheap', 'VIX %.1f: premiums cheap, buy outright' % vix
    return 'elevated', 'VIX %.1f: premiums rich, prefer ITM or spreads' % vix


def levels_ahead(side, entry, ctx, summary):
    """Reference levels beyond the entry in the trade's direction, nearest first."""
    sign = 1 if side == 'BUY' else -1
    named = []
    if ctx:
        for key, label in (('high', 'PDH'), ('low', 'PDL'), ('r1', 'R1'), ('s1', 'S1'), ('r2', 'R2'), ('s2', 'S2'),
                           ('tc', 'CPR top'), ('bc', 'CPR bottom')):
            named.append((ctx[key], label))
    if summary:
        if summary.get('call_wall'):
            named.append((summary['call_wall'], 'call OI wall'))
        if summary.get('put_wall'):
            named.append((summary['put_wall'], 'put OI wall'))
    ahead = [(lvl, lab) for lvl, lab in named if sign * (lvl - entry) > 0]
    return sorted(ahead, key=lambda x: sign * (x[0] - entry))


def assess(side, entry, risk, candle, rng, ctx, vix, summary, cfg):
    """Score 0-100 with reasons; blockers stop the trade whatever the score."""
    score, why, blockers = 30, [], []
    sign = 1 if side == 'BUY' else -1
    trend = (ctx or {}).get('trend', 'unknown')
    if trend in ('up', 'down'):
        if (trend == 'up') == (side == 'BUY'):
            score += 20
            why.append('with the daily %strend' % trend)
        else:
            score -= 20
            why.append('against the daily %strend' % trend)
    elif trend == 'mixed':
        why.append('daily trend mixed')
    if ctx:
        if ctx['cpr_pct'] <= 0.25:
            score += 10
            why.append('narrow CPR (%.2f%%): trend day likely' % ctx['cpr_pct'])
        elif ctx['cpr_pct'] >= 0.6:
            score -= 10
            why.append('wide CPR (%.2f%%): range day likely' % ctx['cpr_pct'])
        outside = entry > ctx['tc'] if side == 'BUY' else entry < ctx['bc']
        score += 5 if outside else -5
        why.append('%s CPR' % ('beyond' if outside else 'inside/against'))
    ahead = levels_ahead(side, entry, ctx, summary)
    if ahead:
        lvl, label = ahead[0]
        room = abs(lvl - entry) / risk
        if room < 1:
            blockers.append('%s %s only %.1fR away' % (label, _num(lvl), room))
        elif room >= 2:
            score += 10
            why.append('clear room to %s (%.1fR)' % (label, room))
        else:
            why.append('%s %s at %.1fR caps T2' % (label, _num(lvl), room))
    else:
        score += 10
        why.append('no marked level ahead')
    body = abs(candle['close'] - candle['open']) if candle.get('open') is not None else None
    span = _price(candle, 'high') - _price(candle, 'low')
    if body is not None and span > 0 and body / span >= 0.5 and sign * (candle['close'] - candle['open']) > 0:
        score += 10
        why.append('strong breakout candle')
    if 0.25 <= rng['width_pct'] <= 0.8:
        score += 5
        why.append('healthy opening range %.2f%%' % rng['width_pct'])
    regime, _ = vix_regime(vix, cfg)
    if regime == 'cheap':
        score += 5
    if summary and summary.get('pcr') is not None:
        pcr = summary['pcr']
        if (side == 'BUY' and pcr >= 1.0) or (side == 'SELL' and pcr <= 0.8):
            score += 10
            why.append('PCR %.2f agrees' % pcr)
        elif (side == 'BUY' and pcr < 0.7) or (side == 'SELL' and pcr > 1.3):
            score -= 10
            why.append('PCR %.2f disagrees' % pcr)
    return max(0, min(100, score)), why, blockers


def signal_features(name, side, entry, risk, candle, rng, ctx, vix, vix_known, candles, opening, start, summary):
    """Numbers describing a breakout from what was known at its close only (for learning which setups work)."""
    sign = 1 if side == 'BUY' else -1
    ctx = ctx or {}
    first = next((x for x in candles if _t(x['time']) >= opening), None)
    day_open = first.get('open') if first else None
    prev = ctx.get('close')
    atr = ctx.get('atr')
    width = rng['high'] - rng['low']
    span = _price(candle, 'high') - _price(candle, 'low')
    body = (candle['close'] - candle['open']) if candle.get('open') is not None else 0.0
    ahead = levels_ahead(side, entry, ctx or None, summary)
    trend = ctx.get('trend')
    vix_open = vix_known[0].get('open') if vix_known and vix_known[0].get('open') else None
    return dict(
        trend_aligned=0.0 if trend not in ('up', 'down') else (1.0 if (trend == 'up') == (side == 'BUY') else -1.0),
        cpr_pct=float(ctx.get('cpr_pct') or 0.0),
        range_pct=float(rng['width_pct']),
        vix=float(vix or 0.0),
        vix_change_pct=round((vix / vix_open - 1) * 100, 3) if vix and vix_open else 0.0,
        candle_body=round(sign * body / span, 3) if span > 0 else 0.0,
        room_r=round(min(5.0, abs(ahead[0][0] - entry) / risk), 3) if ahead else 5.0,
        minutes_after_range=round((start - _t(rng['end'])).total_seconds() / 60, 1),
        gap_pct=round(sign * (day_open - prev) / prev * 100, 3) if day_open and prev else 0.0,
        move_from_open_atr=round(sign * (entry - day_open) / atr, 3) if day_open and atr else 0.0,
        atr_pct=round(atr / prev * 100, 3) if atr and prev else 0.0,
        extension=round(sign * (entry - (rng['high'] if side == 'BUY' else rng['low'])) / width, 3) if width else 0.0,
        is_buy=1.0 if side == 'BUY' else 0.0,
        is_bank=1.0 if name == 'NIFTY BANK' else 0.0,
    )


def learning_dir(fno):
    return Path(fno['state_dir']) / 'learning'


def load_model(fno):
    """The learned model (index_fno_learn writes it after each session), or None when off, missing or too young."""
    if fno['learning'] == 'off':
        return None
    path = learning_dir(fno) / 'model.json'
    try:
        m = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or (m.get('trained_on') or 0) < fno['learn_min_trades'] \
            or len(m.get('w') or []) != len(m.get('features') or []) + 1:
        return None
    return m


def model_p(m, features):
    """P(win) for a breakout from the saved logistic weights (standardised features)."""
    z = m['w'][0] + sum(w * ((features.get(k) or 0.0) - mu) / (sd or 1.0)
                        for w, k, mu, sd in zip(m['w'][1:], m['features'], m['mu'], m['sd']))
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def grade(score):
    return 'A' if score >= 75 else 'B' if score >= 60 else 'C'


def targets(side, entry, risk, ctx, summary, cfg):
    sign = 1 if side == 'BUY' else -1
    t1 = entry + sign * cfg['target_r'][0] * risk
    t2 = entry + sign * cfg['target_r'][1] * risk
    capped = None
    for lvl, label in levels_ahead(side, entry, ctx, summary):
        r = abs(lvl - entry) / risk
        if 1.2 <= r < cfg['target_r'][1]:
            t2, capped = lvl, label
            break
    return round(t1, 2), round(t2, 2), capped


SESSION_MINUTES = 375      # 09:15-15:30: theta is paid over one session per calendar day of decay (conservative)


def _value(legs, spot, at, expiry, rate, iv_mult=1.0):
    t = O.years_to(expiry, at)
    return sum((1 if leg['action'] == 'BUY' else -1)
               * O.bs_price(spot, leg['strike'], t, leg['sigma'] * iv_mult, leg['type'], rate) for leg in legs)


def structure_greeks(legs, spot, now, expiry, rate):
    """Net Greeks per unit of the structure: delta, gamma (per index point), theta (per day), vega (per IV point).

    Each leg uses the chain's Greeks when the quote carried all four, else Black-Scholes at the leg's IV."""
    t = O.years_to(expiry, now)
    net = dict(delta=0.0, gamma=0.0, theta=0.0, vega=0.0)
    for leg in legs:
        q = leg.pop('_quote_greeks', None) or {}
        if all(q.get(k) is not None for k in net):
            g, leg['greeks_from'] = q, 'chain'
        else:
            g, leg['greeks_from'] = O.bs_greeks(spot, leg['strike'], t, leg['sigma'], leg['type'], rate), 'model'
        leg.update(delta=round(g['delta'], 3), gamma=round(g['gamma'], 5), theta=round(g['theta'], 2),
                   vega=round(g['vega'], 2))
        sign = 1 if leg['action'] == 'BUY' else -1
        for k in net:
            net[k] += sign * g[k]
    return net


def _price_structure(kind_name, legs, name, side, entry, stop, t1, t2, now, market, vix, expiry, lot, budget, cfg):
    """Price one candidate structure. Returns (structure, None) or (None, reason)."""
    c = cfg['contracts'][name]
    kind = legs[0]['type']
    legs = [dict(leg) for leg in legs]
    for leg in legs:
        q = market.quote(name, expiry, leg['strike'], kind, entry, now)
        bid, ask, ltp = q.get('bid'), q.get('ask'), q.get('ltp')
        if not ltp or ltp <= 0:
            return None, 'no usable price for %s %s' % (leg['strike'], kind)
        if bid and ask and ask >= bid:
            width = (ask - bid) / ((ask + bid) / 2) * 100
            if width > cfg['max_spread_pct']:
                return None, '%s %s bid/ask %.1f%% wide' % (leg['strike'], kind, width)
        leg['price'] = (ask if leg['action'] == 'BUY' else bid) or ltp
        iv = q.get('iv')
        sigma = iv / 100 if iv else O.implied_vol(ltp, entry, leg['strike'], O.years_to(expiry, now), kind, cfg['rate'])
        leg['sigma'] = sigma or (vix or 15) / 100 * c['iv_scale']
        leg.update(iv=round(leg['sigma'] * 100, 2), oi=q.get('oi'))
        if market.source != 'model':
            leg['_quote_greeks'] = {k: q.get(k) for k in ('delta', 'gamma', 'theta', 'vega')}
    spread = len(legs) == 2
    net = legs[0]['price'] - (legs[1]['price'] if spread else 0)
    if net <= 0:
        return None, 'debit is not positive'
    # Model each leg from its entry price, so the stop/targets are consistent with the quotes paid.
    shift = net - _value(legs, entry, now, expiry, cfg['rate'])
    at = lambda minutes: now + dt.timedelta(minutes=minutes)
    # Vega: IV is cut where the index rallies (calls: the targets; puts: the stop), as India VIX usually falls then.
    crush = 1 - cfg['iv_shock_pct'] / 100
    up_iv, down_iv = (crush, 1.0) if kind == 'CE' else (1.0, crush)
    stop_p = max(0.05, _value(legs, stop, at(20), expiry, cfg['rate'], down_iv) + shift)
    t1_p = _value(legs, t1, at(30), expiry, cfg['rate'], up_iv) + shift
    t2_p = _value(legs, t2, at(60), expiry, cfg['rate'], up_iv) + shift
    # Rupee effect of that IV fall per unit: on the targets for a call, on the stop for a put.
    if kind == 'CE':
        vega_cost = _value(legs, t2, at(60), expiry, cfg['rate']) + shift - t2_p
    else:
        vega_cost = max(0.05, _value(legs, stop, at(20), expiry, cfg['rate']) + shift) - stop_p
    loss_pct = (net - stop_p) / net * 100
    if not spread and loss_pct > cfg['max_premium_loss_pct']:
        return None, '%s: the spot stop costs %.0f%% of the premium' % (kind_name, loss_pct)
    g = structure_greeks(legs, entry, now, expiry, cfg['rate'])
    delta = abs(g['delta'])
    if delta < cfg['min_delta']:
        return None, '%s: net Δ%.2f, the premium barely follows %s (min %.2f)' % (
            kind_name, delta, c['short'], cfg['min_delta'])
    gain_t1 = t1_p - net
    if gain_t1 <= 0:
        return None, '%s: no premium gain at T1' % kind_name
    theta_to_stop = max(0.0, -g['theta']) * cfg['time_stop_minutes'] / SESSION_MINUTES
    theta_share = theta_to_stop / gain_t1
    if theta_share > cfg['max_theta_share']:
        return None, '%s: theta by the time stop eats %.0f%% of the T1 gain (max %.0f%%)' % (
            kind_name, theta_share * 100, cfg['max_theta_share'] * 100)
    orders = 2 * len(legs)
    per_lot = (net - stop_p) * lot + O.charges(net * lot, stop_p * lot, orders)
    reward = (t2_p - net) * lot - O.charges(net * lot, t2_p * lot, orders)
    rr = reward / per_lot if per_lot > 0 else 0
    lots = int(budget // per_lot) if per_lot > 0 else 0
    if lots < 1:
        return None, '%s: 1 lot risks %s at the stop; budget %s (%s)' % (
            kind_name, P.inr(per_lot, 0), P.inr(budget, 0), cfg.get('_budget_why') or 'raise index_fno.risk_per_trade')
    if rr < cfg['min_option_rr']:
        return None, '%s: reward to T2 only %.1f× the risk after charges' % (kind_name, rr)
    warnings = []
    if spread:
        warnings.append('max profit %s per lot' % P.inr((abs(legs[1]['strike'] - legs[0]['strike']) - net) * lot, 0))
    units = lot * lots
    move = abs(t1 - entry)
    greeks = dict(
        delta=round(delta, 3), gamma=round(g['gamma'], 5), theta=round(g['theta'], 2), vega=round(g['vega'], 2),
        delta_at_t1=round(min(1.0, max(0.0, delta + g['gamma'] * move)), 3),
        rs_per_point=round(delta * units, 0), theta_day=round(g['theta'] * units, 0),
        theta_to_time_stop=round(theta_to_stop * units, 0), theta_share=round(theta_share, 3),
        vega_rs=round(g['vega'] * units, 0), iv_shock_pct=cfg['iv_shock_pct'],
        iv_crush_rs=round(vega_cost * units, 0),
        breakeven_pts_hr=round(max(0.0, -g['theta']) / (SESSION_MINUTES / 60) / delta, 1),
        source='chain' if all(leg['greeks_from'] == 'chain' for leg in legs) else 'model')
    return dict(kind='DEBIT_SPREAD' if spread else 'LONG', style=kind_name, option_type=kind, expiry=expiry.isoformat(),
                lot=lot, lots=lots, legs=legs, net=round(net, 2), stop=round(stop_p, 2), t1=round(t1_p, 2),
                t2=round(t2_p, 2), shift=round(shift, 4), risk=round(per_lot * lots, 0), budget=budget,
                rr=round(rr, 2), greeks=greeks, source=market.source, label=market.label, warnings=warnings), None


def plan_option(name, side, entry, stop, t1, t2, now, market, vix, budget, cfg, calendar):
    """Pick the option structure for a spot signal. Returns (structure, None) or (None, reason).

    Candidates by IV regime: cheap IV buys outright (ATM, or one strike in the
    money near expiry); rich IV compares a one-strike-ITM buy (less extrinsic
    value to bleed) with a debit spread at least four strikes wide (sold
    premium offsets IV). The candidate with the best reward to T2 per rupee
    risked, after charges, wins; it must clear `min_option_rr`."""
    c = cfg['contracts'][name]
    expiry, lot = market.expiry(name, now.date())
    dte = O.trading_days_between(now.date(), expiry, calendar)
    kind = 'CE' if side == 'BUY' else 'PE'
    sign = 1 if side == 'BUY' else -1
    step = c['step']
    atm = O.round_strike(entry, step)
    itm = atm - sign * step
    short = O.round_strike(t2, step)
    if sign * (short - atm) < 4 * step:
        short = atm + sign * 4 * step
    long_leg = lambda k: [dict(action='BUY', strike=k, type=kind)]
    spread_legs = [dict(action='BUY', strike=atm, type=kind), dict(action='SELL', strike=short, type=kind)]
    regime, _ = vix_regime(vix, cfg)
    if regime == 'cheap':
        first = ('ITM buy', long_leg(itm)) if dte <= 3 else ('ATM buy', long_leg(atm))
        candidates = [first, ('debit spread', spread_legs)]
    else:
        candidates = [('ITM buy', long_leg(itm)), ('debit spread', spread_legs), ('ATM buy', long_leg(atm))]
    best, reasons = None, []
    for style, legs in candidates:
        opt, reason = _price_structure(style, legs, name, side, entry, stop, t1, t2, now, market, vix, expiry, lot,
                                       budget, cfg)
        if not opt:
            reasons.append(reason)
            continue
        # Cheap IV: take the first that qualifies (an outright buy); rich IV: the best reward/risk.
        if regime == 'cheap':
            best = opt
            break
        if best is None or opt['rr'] > best['rr'] + 0.1:
            best = opt
    if not best:
        return None, '; '.join(reasons)
    best['dte'] = dte
    return best, None


def mark(opt, spot, at, market, name, rate):
    """Structure value at an exit: live chain bid/ask when available, else the model."""
    expiry = dt.date.fromisoformat(opt['expiry'])
    if market is not None and market.source != 'model':
        try:
            total = 0.0
            for leg in opt['legs']:
                q = market.quote(name, expiry, leg['strike'], leg['type'], spot, at)
                px = (q.get('bid') if leg['action'] == 'BUY' else q.get('ask')) or q['ltp']
                total += px if leg['action'] == 'BUY' else -px
            return round(max(total, 0.0), 2), 'live'
        except (ValueError, OSError, KeyError, TypeError):
            pass
    return round(max(_value(opt['legs'], spot, at, expiry, rate) + opt['shift'], 0.0), 2), 'est.'


def contract_name(name, opt, cfg):
    c = cfg['contracts'][name]
    expiry = dt.date.fromisoformat(opt['expiry']).strftime('%d %b').upper()
    legs = opt['legs']
    if opt['kind'] == 'LONG':
        return '%s %s %d %s' % (c['short'], expiry, legs[0]['strike'], legs[0]['type'])
    return '%s %s %d/%d %s spread' % (c['short'], expiry, legs[0]['strike'], legs[1]['strike'], legs[0]['type'])


def _rs(v):
    return ('−' if v < 0 else '') + P.inr(abs(v), 0)


def greeks_lines(gk, kind, short):
    """The position's Greeks and what they mean for this trade, in rupees."""
    crush = ''
    if gk.get('iv_shock_pct') and gk.get('iv_crush_rs'):
        crush = '; a %g%% IV drop (priced in) costs ≈%s at %s' % (gk['iv_shock_pct'], P.inr(gk['iv_crush_rs'], 0),
                                                                  'T2' if kind == 'CE' else 'the stop')
    return [
        '📐 Greeks%s: Δ%.2f → %.2f at T1 (Γ) · %s per %s point · Θ %s/day, ≈%s by the time stop (%.0f%% of the T1 gain)' % (
            '' if gk.get('source') == 'chain' else ' (model)', gk['delta'], gk['delta_at_t1'], P.inr(gk['rs_per_point'], 0),
            short, _rs(gk['theta_day']), P.inr(gk['theta_to_time_stop'], 0), gk['theta_share'] * 100),
        '🌡 Vega %s per IV point%s · %s must move %s pts/hr your way to cover theta' % (
            P.inr(gk['vega_rs'], 0), crush, short, _num(gk['breakeven_pts_hr'])),
    ]


def signal_text(name, sig, opt, cfg, rng, opening, range_end, warning):
    c = cfg['contracts'][name]
    side, lots, lot = sig['side'], opt['lots'], opt['lot']
    model = opt['source'] == 'model'
    px = (lambda v: '≈₹' + _num(v)) if model else (lambda v: '₹' + _num(v))
    legs = opt['legs']
    if opt['kind'] == 'LONG':
        action = 'BUY %d lot%s %d %s at %s' % (lots, 's' if lots > 1 else '', legs[0]['strike'], legs[0]['type'],
                                              px(opt['net']))
        chase = 'Skip if the premium is above ₹%s.' % _num(opt['net'] * 1.08)
    else:
        action = 'BUY %d %s / SELL %d %s, %d lot%s, net debit %s' % (
            legs[0]['strike'], legs[0]['type'], legs[1]['strike'], legs[1]['type'], lots, 's' if lots > 1 else '',
            px(opt['net']))
        chase = 'Skip if the net debit is above ₹%s.' % _num(opt['net'] * 1.08)
    deltas = [leg.get('delta') for leg in legs]
    delta = None if any(d is None for d in deltas) else abs(deltas[0] - (deltas[1] if len(deltas) > 1 else 0))
    book = 'book %d lot%s, stop to cost' % (lots // 2, 's' if lots // 2 > 1 else '') if lots > 1 else 'stop to cost'
    time_stop = (_t(sig['time']) + dt.timedelta(minutes=cfg['time_stop_minutes'])).strftime('%H:%M')
    lines = [
        '%s <b>%s · %s</b> · Grade %s (%d)' % ('🟢' if side == 'BUY' else '🔴', html.escape(contract_name(name, opt, cfg)),
                                             'BULLISH' if side == 'BUY' else 'BEARISH', sig['grade'], sig['score']),
        '%s expiry (%d session%s) · lot %d · %s' % (dt.date.fromisoformat(opt['expiry']).strftime('%d %b'), opt['dte'],
                                                  '' if opt['dte'] == 1 else 's', lot, html.escape(opt['label'])),
        '▶ %s. %s' % (action, chase),
        '🛑 Stop: %s %s %s (premium %s)' % (c['short'], 'below' if side == 'BUY' else 'above', _num(sig['stop']),
                                            px(opt['stop'])),
        '🎯 T1: %s %s (%s) → %s' % (c['short'], _num(sig['target1']), px(opt['t1']), book),
        ('🏁 T2: %s %s (%s)%s' % (c['short'], _num(sig['target2']), px(opt['t2']),
                                 ' just before %s' % sig['t2_level'] if sig.get('t2_level') else '')
         if cfg['runner'] == 'target' else
         '🏃 Runner: after T1 the rest trails each 5-min %s; no fixed exit (T2 %s ≈%s is the reference)' % (
             'low' if side == 'BUY' else 'high', _num(sig['target2']), _num(opt['t2']))),
        '⏱ Time stop %s if T1 has not hit (theta) · square off by %s' % (time_stop, cfg['square_off']),
        '💰 Risk %s of %s budget%s · reward/risk %.1f · %s%s' % (
            P.inr(opt['risk'], 0), P.inr(opt['budget'], 0),
            ' (%.1f%% of capital)' % (opt['risk'] / cfg['_capital'] * 100) if cfg.get('_capital') else '', opt['rr'], opt['style'],
                                       ' · ' + ' · '.join(opt['warnings']) if opt['warnings'] else ''),
        '📋 Why: %s-%s range %s-%s broken with a close at %s · %s · %s' % (
            opening.strftime('%H:%M'), range_end.strftime('%H:%M'), _num(rng['low']), _num(rng['high']),
            _num(sig['entry']), ' · '.join(html.escape(w) for w in sig['why']), html.escape(sig['regime'])),
    ]
    if sig.get('p_win') is not None:
        if sig.get('decided_by') == 'model':
            role = 'decides (take at %.2f+)%s' % (cfg['learn_threshold'], '' if sig.get('rules_take') else
                                                  '; the grade rule alone would have skipped')
        elif cfg['learning'] == 'decide':
            role = 'on probation until it beats luck; the grade rules decide'
        else:
            role = 'advisory only'
        lines.append('🧠 Learned model: P(win) %.2f from %d past breakouts · %s' % (sig['p_win'], sig['model_trades'], role))
    gk = opt.get('greeks')
    if gk:
        lines += greeks_lines(gk, opt['option_type'], c['short'])
    if model:
        lines.append('ℹ️ Premiums are estimates (IV %s%%%s). Your broker\'s price may differ; the %s levels are the plan.' % (
            _num(legs[0]['iv']), ', Δ%.2f: ≈₹%.2f per %s point' % (delta, delta, c['short']) if delta else '', c['short']))
    if warning:
        lines.append('⚠️ %s' % html.escape(warning))
    lines.append('<i>Spot from NSE\'s delayed snapshot (1-3 min). Paper research, not advice.</i>')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- per-index state machine

def process(name, rec, candles, vix, now, opening, cfg, ctx=None, market=None, calendar=None, warning=None,
            vix_prev=None):
    """Advance one index's state with candles known at `now`. Returns Telegram texts.

    `candles` are this index's usable sampled candles for the day, oldest first;
    `vix` the India VIX ones (`vix_prev`, yesterday's close, when none yet);
    `ctx` yesterday's levels; `market` the option price source. `rec` is
    updated in place."""
    texts = []
    if rec.get('status') in FINAL:
        return texts
    calendar = calendar or DEFAULT_CALENDAR
    day = opening.date()
    range_end = opening + dt.timedelta(minutes=cfg['range_minutes'])
    c = cfg['contracts'][name]
    label = c['short']

    def skip(status, note):
        rec.update(status=status, note=note)
        if cfg['send_skips'] and status in ('SKIPPED_WEAK', 'SKIPPED_OPTION', 'SKIPPED_VIX', 'SKIPPED_RISK',
                                            'SKIPPED_MODEL'):
            texts.append('⚪ <b>%s: breakout not traded</b> · %s IST\n%s' % (
                label, now.strftime('%H:%M'), html.escape(note)))

    if rec.get('status', 'WAITING') == 'WAITING':
        if day.isoformat() in cfg['event_dates']:
            rec.update(status='SKIPPED_EVENT', note='event day in index_fno.event_dates')
            return texts
        if now < range_end + SETTLE:
            rec['status'] = 'WAITING'
            return texts
        inside = [x for x in candles if opening <= _t(x['time']) < range_end]
        if len(inside) < cfg['range_minutes'] // 5:
            rec.update(status='NO_RANGE', note='%d of %d opening-range candles usable' % (
                len(inside), cfg['range_minutes'] // 5))
            return texts
        high, low = max(_price(x, 'high') for x in inside), min(_price(x, 'low') for x in inside)
        width = (high - low) / ((high + low) / 2) * 100
        rec.update(status='ARMED', range=dict(high=high, low=low, width_pct=round(width, 3),
                                              start=opening.isoformat(), end=range_end.isoformat()))
        if not cfg['min_range_pct'] <= width <= cfg['max_range_pct']:
            rec.update(status='SKIPPED_RANGE', note='opening range %.2f%% is outside %.2f-%.2f%%' % (
                width, cfg['min_range_pct'], cfg['max_range_pct']))
            return texts
    after = lambda x: rec.get('checked_until') is None or x['time'] > rec['checked_until']
    if rec['status'] == 'ARMED':
        deadline = dt.datetime.combine(day, P.hhmm(cfg['entry_deadline']), IST)
        rng = rec['range']
        buffer = max(rng['high'] * cfg['breakout_buffer_pct'] / 100, 0.1 * (rng['high'] - rng['low']))
        for x in candles:
            start = _t(x['time'])
            if start < range_end or not after(x):
                continue
            end = start + dt.timedelta(minutes=5)
            if end > deadline:
                rec['status'] = 'NO_BREAKOUT'
                break
            rec['checked_until'] = x['time']
            side = 'BUY' if x['close'] > rng['high'] + buffer else 'SELL' if x['close'] < rng['low'] - buffer else None
            if not side:
                continue
            known = [v for v in vix if _t(v['time']) <= start]
            level = known[-1]['close'] if known else vix_prev
            if now - end > dt.timedelta(minutes=cfg['fresh_minutes']):
                rec.update(status='MISSED', note='%s breakout at %s seen at %s' % (
                    side, end.strftime('%H:%M'), now.strftime('%H:%M')))
                break
            regime, regime_text = vix_regime(level, cfg)
            if regime == 'extreme':
                skip('SKIPPED_VIX', 'India VIX %.2f above %.2f: premium too expensive to buy' % (level, cfg['max_vix']))
                break
            entry = x['close']
            stop = rng['low'] if side == 'BUY' else rng['high']
            if cfg['stop_mode'] == 'candle':
                # Invalidation: back through the breakout candle (at least a quarter inside the range).
                quarter = 0.25 * (rng['high'] - rng['low'])
                stop = min(_price(x, 'low'), rng['high'] - quarter) if side == 'BUY' else \
                    max(_price(x, 'high'), rng['low'] + quarter)
            risk = abs(entry - stop)
            summary = None
            if market is not None:
                try:
                    expiry, _ = market.expiry(name, day)
                    summary = market.summary(name, expiry, entry)
                except (ValueError, OSError, KeyError, TypeError):
                    summary = None
            score, why, blockers = assess(side, entry, risk, x, rng, ctx, level, summary, cfg)
            t1, t2, capped = targets(side, entry, risk, ctx, summary, cfg)
            features = signal_features(name, side, entry, risk, x, rng, ctx, level, known, candles, opening, start,
                                       summary)
            model = cfg.get('_model')
            p_win = round(model_p(model, features), 3) if model else None
            rules_take = not blockers and score >= cfg['min_score']
            by_model = p_win is not None and cfg['learning'] == 'decide' and bool(model.get('proven'))
            base = dict(side=side, time=end.isoformat(), entry=entry, stop=stop, initial_stop=stop,
                        risk=round(risk, 2), target1=t1, target2=t2, t2_level=capped, vix=level, score=score,
                        grade=grade(score), why=why, regime=regime_text, summary=summary, features=features,
                        p_win=p_win, model_trades=model['trained_on'] if model else None,
                        decided_by='model' if by_model else 'rules', rules_take=rules_take)
            base['blockers'] = blockers
            if by_model and p_win < cfg['learn_threshold']:
                rec['signal'] = base
                skip('SKIPPED_MODEL', '%s grade %s: learned model P(win) %.2f is below %.2f (from %d past breakouts)%s' % (
                    side, grade(score), p_win, cfg['learn_threshold'], model['trained_on'],
                    '; the grade rule alone would have taken it' if rules_take else ''))
                break
            if not by_model and ((blockers and not cfg.get('_ignore_blockers')) or score < cfg['min_score']):
                rec['signal'] = base
                skip('SKIPPED_WEAK', '%s grade %s (%d/%d): %s' % (
                    side, grade(score), score, cfg['min_score'], '; '.join(blockers + why)))
                break
            if cfg.get('_block'):
                rec['signal'] = base
                skip('SKIPPED_RISK', '%s grade %s: %s' % (side, grade(score), cfg['_block']))
                break
            if market is None:
                rec['signal'] = base
                skip('SKIPPED_OPTION', 'no option price source: %s' % (warning or 'unavailable'))
                break
            try:
                opt, reason = plan_option(name, side, entry, stop, t1, t2, now, market, level, cfg['_budget'], cfg, calendar)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                opt, reason = None, str(exc)
            if not opt:
                rec['signal'] = base
                skip('SKIPPED_OPTION', '%s grade %s: %s' % (side, grade(score), reason))
                break
            rec.update(status='OPEN', signal=dict(base, t1_hit=False, legs=[], events=[], option=opt))
            texts.append(signal_text(name, rec['signal'], opt, cfg, rng, opening, range_end, warning))
            break
        if rec['status'] == 'ARMED' and now >= deadline + SETTLE:
            rec['status'] = 'NO_BREAKOUT'
    if rec['status'] == 'OPEN':
        texts += manage(name, rec, candles, now, day, cfg, market)
    return texts


def manage(name, rec, candles, now, day, cfg, market):
    texts = []
    sig, opt = rec['signal'], rec['signal']['option']
    sign = 1 if sig['side'] == 'BUY' else -1
    square = dt.datetime.combine(day, P.hhmm(cfg['square_off']), IST)
    time_stop = _t(sig['time']) + dt.timedelta(minutes=cfg['time_stop_minutes'])
    head = html.escape(contract_name(name, opt, cfg))
    lots, lot = opt['lots'], opt['lot']
    after = lambda x: rec.get('checked_until') is None or x['time'] > rec['checked_until']

    def book(kind, n, spot, at):
        premium, src = mark(opt, spot, at, market, name, cfg['rate'])
        sig['legs'].append(dict(lots=n, premium=premium, spot=spot, kind=kind, time=at.isoformat(), source=src))
        sig['events'].append(dict(kind=kind, spot=spot, premium=premium, time=at.isoformat()))
        return premium, src

    def close(kind, spot, at, text):
        remaining = lots - sum(leg['lots'] for leg in sig['legs'])
        premium, src = book(kind, remaining, spot, at)
        gross = sum(leg['lots'] * lot * (leg['premium'] - opt['net']) for leg in sig['legs'])
        sells = sum(leg['lots'] * lot * leg['premium'] for leg in sig['legs'])
        orders = len(opt['legs']) * (1 + len(sig['legs']))
        cost = O.charges(opt['net'] * lot * lots, sells, orders)
        net = round(gross - cost, 0)
        rec.update(status='CLOSED', pnl=net, gross=round(gross, 0), charges=cost,
                   r=round(net / opt['risk'], 2) if opt['risk'] else None)
        texts.append('%s <b>%s: %s</b> · %s IST\n%s\nExit ₹%s (%s) · P&amp;L %s after ~%s charges (%+.2fR)' % (
            {'STOP': '🛑', 'BREAKEVEN': '⚖️', 'TARGET2': '🏁', 'SQUARE_OFF': '⏰', 'TIME_STOP': '⏱', 'TRAIL': '📈'}[kind], head,
            {'STOP': 'stop-loss hit', 'BREAKEVEN': 'stopped at cost', 'TARGET2': 'T2 reached', 'SQUARE_OFF': 'square-off',
             'TRAIL': 'trailing stop hit, runner closed',
             'TIME_STOP': 'time stop, T1 not reached'}[kind], at.strftime('%H:%M'), text, _num(premium), src,
            P.inr(net, 0), P.inr(cost, 0), rec['r'] or 0))

    for x in candles:
        start = _t(x['time'])
        if start < _t(sig['time']) or not after(x):
            continue
        rec['checked_until'] = x['time']
        end = start + dt.timedelta(minutes=5)
        fav = _price(x, 'high') if sign > 0 else _price(x, 'low')
        adv = _price(x, 'low') if sign > 0 else _price(x, 'high')
        if sign * (adv - sig['stop']) <= 0:
            kind = ('TRAIL' if sig.get('trailed') else 'BREAKEVEN') if sig['t1_hit'] else 'STOP'
            close(kind, sig['stop'], end, 'Spot %s through the %sstop %s. Exit %s.' % (
                _num(adv), 'trailing ' if kind == 'TRAIL' else '', _num(sig['stop']),
                'the rest' if sig['t1_hit'] else 'the position'))
            break
        if not sig['t1_hit'] and sign * (fav - sig['target1']) >= 0:
            sig['t1_hit'] = True
            half = lots // 2
            premium, src = book('TARGET1', half, sig['target1'], end) if half else mark(opt, sig['target1'], end, market, name, cfg['rate'])
            sig['stop'] = sig['entry']
            texts.append('🎯 <b>%s: T1 reached</b> · %s IST\nSpot %s through T1 %s · premium ₹%s (%s, entry ₹%s).\n%s '
                         'Stop moves to cost (spot %s); %s.' % (
                             head, end.strftime('%H:%M'), _num(fav), _num(sig['target1']), _num(premium), src,
                             _num(opt['net']), ('Book %d of %d lots.' % (half, lots)) if half else 'With 1 lot, hold.',
                             _num(sig['entry']), 'the rest trails behind each 5-minute %s from here' % (
                                 'low' if sign > 0 else 'high') if cfg['runner'] == 'trail' else
                             'the rest aims for T2 %s' % _num(sig['target2'])))
        if sig['t1_hit'] and cfg['runner'] == 'target' and sign * (fav - sig['target2']) >= 0:
            close('TARGET2', sig['target2'], end, 'Spot %s through T2 %s. Exit the rest.' % (
                _num(fav), _num(sig['target2'])))
            break
        if not sig['t1_hit'] and end >= time_stop:
            close('TIME_STOP', x['close'], end, 'Spot %s after %d minutes without T1: theta%s is now the main risk. Exit.' % (
                _num(x['close']), cfg['time_stop_minutes'],
                ' (≈%s paid)' % P.inr(opt['greeks']['theta_to_time_stop'], 0) if opt.get('greeks') else ''))
            break
        if end >= square:
            close('SQUARE_OFF', x['close'], end, 'Exit what is left at spot %s.' % _num(x['close']))
            break
        if sig['t1_hit'] and cfg['runner'] == 'trail':
            # The runner's stop follows each completed candle's low (long) or high (short); never loosens.
            level = _price(x, 'low') if sign > 0 else _price(x, 'high')
            if sign * (level - sig['stop']) > 0:
                sig['stop'] = level
                sig['trailed'] = True
    if rec['status'] == 'OPEN' and now >= square + dt.timedelta(minutes=15):
        last = candles[-1]['close'] if candles else sig['entry']
        close('SQUARE_OFF', last, square, 'No usable candle at %s; marked at the last close %s.' % (
            cfg['square_off'], _num(last)))
    return texts


# --------------------------------------------------------------------------- brief

def atm_greeks(name, spot, expiry, now, market, cfg):
    """ATM call and put at the expiry in use, with Greeks (chain's when given, else Black-Scholes)."""
    strike = O.round_strike(spot, cfg['contracts'][name]['step'])
    t = O.years_to(expiry, now)
    out = dict(strike=strike)
    for kind in ('CE', 'PE'):
        q = market.quote(name, expiry, strike, kind, spot, now)
        if any(q.get(k) is None for k in ('delta', 'gamma', 'theta', 'vega')):
            sigma = (q.get('iv') or 0) / 100 or (market.sigma(name) if hasattr(market, 'sigma') else 0)
            if not sigma:
                return None
            q = dict(q, **O.bs_greeks(spot, strike, t, sigma, kind, cfg['rate']))
        out[kind] = {k: q[k] for k in ('ltp', 'delta', 'gamma', 'theta', 'vega')}
    return out


def atm_text(atm, ctx, c, model):
    ce, pe = atm['CE'], atm['PE']
    theta = (abs(ce['theta']) + abs(pe['theta'])) / 2
    pts_hr = theta / (SESSION_MINUTES / 60) / max(abs(ce['delta']), 0.05)
    lines = ['ATM %d: CE %s₹%s Δ%.2f · PE %s₹%s Δ%.2f · Γ %.5f (Δ %+.2f per 100 pts)' % (
        atm['strike'], '≈' if model else '', _num(ce['ltp']), ce['delta'], '≈' if model else '', _num(pe['ltp']),
        pe['delta'], ce['gamma'], ce['gamma'] * 100)]
    hourly = ctx.get('atr') / (SESSION_MINUTES / 60) if ctx.get('atr') else None
    lines.append('Θ −₹%s/day (%s/lot) · vega ₹%s per IV pt (%s/lot) · theta needs %s pts/hr%s' % (
        _num(theta), P.inr(theta * c['lot'], 0), _num(ce['vega']), P.inr(ce['vega'] * c['lot'], 0), _num(pts_hr),
        ' vs ~%s pts/hr ATR pace: %s' % (_num(hourly, 0), 'buying is cheap' if pts_hr <= 0.1 * hourly else
                                         'theta is heavy, prefer ITM or spreads') if hourly else ''))
    return lines


def desk_line(desk, cfg):
    """The account's state the way a trader reads it before the open."""
    if not desk:
        return None
    if desk['block']:
        return '⛔ %s' % desk['block']
    size = P.inr(cfg['_budget'], 0) if cfg.get('_budget') else '—'
    record = 'equity %+.1fR over %d trades · week %+.1fR · %d loss%s in a row' % (
        desk['equity_r'], desk['trades'], desk['week_r'], desk['streak'], '' if desk['streak'] == 1 else 'es') \
        if desk['trades'] else 'no closed trades yet'
    return '📒 Desk: %s · risk today %s per trade%s' % (
        record, size, ' (' + '; '.join(desk['notes']) + ')' if desk['notes'] else ' (full size)')


def brief_text(cfg, now, contexts, vix, markets_info, warning, late=False, doc=None, desk=None, cues=None):
    regime, regime_text = vix_regime(vix, cfg)
    lines = ['🧭 <b>Index options desk · %s%s</b>' % (now.strftime('%a %d %b'), ' · LATE %s' % now.strftime('%H:%M') if late else ''),
             '%s · strategy: %s' % (html.escape(regime_text), {
                 'cheap': 'buy ATM options on clean breakouts',
                 'elevated': 'ITM buys or debit spreads, whichever pays better',
                 'extreme': 'stand aside on buying; no signals',
                 'unknown': 'wait for VIX'}[regime])]
    line, mood = global_read(cues)
    if line:
        lines.append('%s → %s overnight%s' % (line, mood, {
            'risk-off': ': expect weakness at the open; let the range settle, size stays as planned',
            'risk-on': ': expect strength at the open; do not chase a gap',
            'neutral': ''}.get(mood, '')))
    dl = desk_line(desk, cfg)
    if dl:
        lines.append(dl)
    for name in cfg['indices']:
        ctx = contexts.get(name)
        c = cfg['contracts'][name]
        if not ctx:
            lines.append('\n<b>%s</b>: daily levels unavailable' % c['short'])
            continue
        cpr = 'narrow, trend day likely' if ctx['cpr_pct'] <= 0.25 else 'wide, range day likely' if ctx['cpr_pct'] >= 0.6 else 'normal'
        lines.append('\n<b>%s</b> · prev close %s · daily trend %s%s' % (
            c['short'], _num(ctx['close']), ctx['trend'], (' · ATR %s' % _num(ctx['atr'], 0)) if ctx.get('atr') else ''))
        lines.append('PDH %s · PDL %s · CPR %s-%s (%s)' % (_num(ctx['high']), _num(ctx['low']), _num(ctx['bc']),
                                                          _num(ctx['tc']), cpr))
        lines.append('R1 %s · S1 %s' % (_num(ctx['r1']), _num(ctx['s1'])))
        if vix:
            scale = (cfg.get('_iv_scale') or {}).get(name) or c['iv_scale']
            lines.append('IV used ≈ %.1f%% (VIX × %.2f%s)' % (vix * scale, scale,
                                                         ', realised-vol ratio to NIFTY' if name != 'NIFTY 50' else ''))
        info = markets_info.get(name) or {}
        if info.get('expiry'):
            lines.append('Expiry used: %s (%d sessions) · lot %d' % (
                dt.date.fromisoformat(info['expiry']).strftime('%d %b'), info['dte'], info['lot']))
        if info.get('atm'):
            lines += atm_text(info['atm'], ctx, dict(c, lot=info.get('lot') or c['lot']), cfg['chain_source'] == 'model')
        s = info.get('summary')
        if s:
            lines.append('PCR %.2f · max pain %s · call wall %s · put wall %s' % (
                s['pcr'], _num(s['max_pain'], 0), _num(s['call_wall'], 0) if s['call_wall'] else '—',
                _num(s['put_wall'], 0) if s['put_wall'] else '—'))
    if late:
        lines.append('\n⏰ Late: the morning data was not reachable at %s. Signals resume from now; a breakout that '
                     'happened while offline is not chased.' % cfg['brief_time'])
        for name in cfg['indices']:
            rng = ((doc or {}).get('indices', {}).get(name) or {}).get('range')
            if rng:
                lines.append('%s opening range %s-%s (%.2f%%)' % (cfg['contracts'][name]['short'], _num(rng['low']),
                                                                 _num(rng['high']), rng['width_pct']))
    lines.append('\nPlan: trade only a five-minute close beyond the %s-%s range. Stop where the breakout is proven '
                 'wrong%s. One trade at a time%s; no trade is also a position.' % (
                     '09:15', (dt.datetime.combine(now.date(), dt.time(9, 15)) + dt.timedelta(minutes=cfg['range_minutes'])).strftime('%H:%M'),
                     '; book half at 1R and trail the rest' if cfg['runner'] == 'trail' else '; book half at 1R, the rest at T2',
                     ', one loss and done' if cfg['one_loss_a_day'] else ''))
    if warning:
        lines.append('⚠️ %s' % html.escape(warning))
    lines.append('<i>Paper research, not advice.</i>')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- state and entry point

def load_state(state_dir, day):
    path = Path(state_dir) / ('signals-%s.json' % day.isoformat())
    if path.is_file():
        return json.loads(path.read_text())
    return dict(date=day.isoformat(), indices={})


def save_state(state_dir, doc):
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(root), prefix='.signals-')
    with os.fdopen(fd, 'w') as fh:
        json.dump(doc, fh, indent=2, default=str)
    os.replace(temporary, root / ('signals-%s.json' % doc['date']))


def trade_history(state_dir, before):
    """Closed desk trades from the daily state files before `before`, oldest first: [(date, r, pnl)]."""
    out = []
    for path in sorted(Path(state_dir).glob('signals-*.json')):
        day = path.stem[len('signals-'):]
        try:
            if dt.date.fromisoformat(day) >= before:
                continue
            doc = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        for rec in sorted((doc.get('indices') or {}).values(), key=lambda r: (r.get('signal') or {}).get('time', '')):
            if rec.get('status') == 'CLOSED' and rec.get('r') is not None:
                out.append((dt.date.fromisoformat(day), rec['r'], rec.get('pnl') or 0))
    return out


def discipline(fno, today, history=None):
    """A professional's account rules from the trade record: weekly stop, losing streak and drawdown sizing.

    Returns dict(mult, block, notes, week_r, streak, drawdown_r, equity_r, trades)."""
    hist = trade_history(fno['state_dir'], today) if history is None else history
    monday = today - dt.timedelta(days=today.weekday())
    week_r = round(sum(r for d, r, _ in hist if d >= monday), 2)
    streak = 0
    for _, r, _ in reversed(hist):
        if r >= 0:
            break
        streak += 1
    equity = peak = 0.0
    for _, r, _ in hist:
        equity += r
        peak = max(peak, equity)
    drawdown = round(peak - equity, 2)
    mult, block, notes = 1.0, None, []
    if fno['weekly_loss_limit_r'] is not None and week_r <= -fno['weekly_loss_limit_r']:
        block = 'weekly stop: %+.1fR this week (limit −%gR). Flat until Monday.' % (week_r, fno['weekly_loss_limit_r'])
    if fno['streak_half_after'] and streak >= fno['streak_half_after']:
        mult = 0.5
        notes.append('%d losses in a row: half size until a winner' % streak)
    if fno['drawdown_half_r'] and drawdown >= fno['drawdown_half_r']:
        mult = 0.5
        notes.append('%.1fR below the equity peak: half size' % drawdown)
    return dict(mult=mult, block=block, notes=notes, week_r=week_r, streak=streak, drawdown_r=drawdown,
                equity_r=round(equity, 2), trades=len(hist))


GLOBAL = (('^GSPC', 'S&P 500'), ('^IXIC', 'Nasdaq'), ('^VIX', 'US VIX'), ('^N225', 'Nikkei'), ('^HSI', 'Hang Seng'),
          ('CL=F', 'Crude'), ('INR=X', 'USD/INR'))


def fetch_global(now):
    """Last change of each overnight cue from Yahoo daily candles: {label: pct}. Missing ones are left out."""
    from urllib.parse import quote
    out = {}
    for symbol, label in GLOBAL:
        try:
            doc = json.loads(P.http_get('https://query2.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=1d' % quote(symbol)))
            closes = [c for c in doc['chart']['result'][0]['indicators']['quote'][0]['close'] if c]
            if len(closes) >= 2:
                out[label] = round((closes[-1] / closes[-2] - 1) * 100, 2)
        except (ValueError, OSError, KeyError, TypeError, IndexError):
            continue
    return out


def global_read(cues):
    """(line, mood) from the overnight cues; the mood is shown, not traded on (untested)."""
    if not cues:
        return None, 'unknown'
    spx, vix = cues.get('S&P 500'), cues.get('US VIX')
    mood = 'risk-off' if (spx is not None and spx <= -1) or (vix is not None and vix >= 10) else \
        'risk-on' if spx is not None and spx >= 1 else 'neutral'
    line = 'Global: ' + ' · '.join('%s %+.1f%%' % (k, v) for k, v in cues.items())
    return line, mood


def risk_guard(doc, fno, name):
    """Budget and block for a new trade on `name`, given today's other trades.

    At most `max_open_trades` open at once, and realised losses plus the risk
    of open trades stay within `max_daily_loss`; a new trade is sized to the
    room left under that cap."""
    base = fno['_budget']
    cap = fno['max_daily_loss'] or 2 * base
    others = {n: r for n, r in doc['indices'].items() if n != name}
    open_ = [n for n, r in others.items() if r.get('status') == 'OPEN']
    at_risk = sum(r['signal']['option']['risk'] for r in others.values() if r.get('status') == 'OPEN')
    lost = max(0.0, -sum(r.get('pnl') or 0 for r in others.values() if r.get('status') == 'CLOSED'))
    room = cap - at_risk - lost
    short = lambda n: fno['contracts'][n]['short']
    if fno.get('_desk_block'):
        return dict(_block=fno['_desk_block'])
    losers = [n for n, r in others.items() if r.get('status') == 'CLOSED' and (r.get('pnl') or 0) < 0]
    if fno['one_loss_a_day'] and losers:
        return dict(_block='one loss a day: %s already lost today. No revenge trade.' % ', '.join(short(n) for n in losers))
    if len(open_) >= fno['max_open_trades']:
        return dict(_block='%s trade already open (max_open_trades %d)' % (
            ', '.join(short(n) for n in open_), fno['max_open_trades']))
    if room <= 0:
        return dict(_block='daily loss cap %s reached (lost %s, %s at risk)' % (
            P.inr(cap, 0), P.inr(lost, 0), P.inr(at_risk, 0)))
    if room < base:
        return dict(_budget=room, _budget_why='daily loss cap %s: %s lost, %s at risk' % (
            P.inr(cap, 0), P.inr(lost, 0), P.inr(at_risk, 0)))
    return {}


def iv_scales(fno, contexts):
    """Per-index multiple of India VIX: 20-day realised vol relative to NIFTY's, held to 0.8-2.0."""
    base = (contexts.get('NIFTY 50') or {}).get('rv20')
    out = {}
    for name in fno['indices']:
        rv = (contexts.get(name) or {}).get('rv20')
        if name == 'NIFTY 50':
            out[name] = 1.0
        elif base and rv:
            out[name] = round(min(2.0, max(0.8, rv / base)), 3)
    return out


YAHOO = {'NIFTY 50': '^NSEI', 'NIFTY BANK': '^NSEBANK', 'INDIA VIX': '^INDIAVIX'}
BACKFILL_EVERY = dt.timedelta(minutes=5)


def yahoo_candles(name, day, now):
    """Completed five-minute candles for one index from Yahoo's free chart API, recorder-shaped."""
    symbol = YAHOO.get(name)
    if not symbol:
        return []
    rows = []
    for start, o, h, l, c in P.fetch_yahoo_5m(symbol, day, now):
        end = start + dt.timedelta(minutes=5)
        rows.append(dict(symbol='INDEX:' + name, time=start.isoformat(), end=end.isoformat(), known_at=now.isoformat(),
                         open=o, high=h, low=l, close=c, usable=True, source='yahoo'))
    return rows


def missing_windows(series, opening, now):
    """Start times of completed five-minute windows since the open with no usable candle."""
    have = {_t(r['time']) for r in series}
    out, start = [], opening
    while start + dt.timedelta(minutes=5) + SETTLE <= now:
        if start not in have:
            out.append(start)
        start += dt.timedelta(minutes=5)
    return out


def backfill(doc, by, names, opening, now, loader, until):
    """Fill gaps in the recorder's candles (an outage, a sleeping laptop) from Yahoo.

    The engine trades the FIRST breakout, so the whole gap is filled, not just
    the opening range: a breakout that happened while offline is then seen as
    stale and skipped instead of being chased after the reconnect. Recorder
    candles always win; each index is tried at most every five minutes."""
    tried = doc.setdefault('backfill_at', {})
    filled = doc.setdefault('backfilled', {})
    for name in names:
        key = 'INDEX:' + name
        gaps = [g for g in missing_windows(by.get(key, []), opening, now) if g < until]
        last = tried.get(name)
        if not gaps or (last and now - _t(last) < BACKFILL_EVERY):
            continue
        tried[name] = now.isoformat()
        try:
            extra = {_t(r['time']): r for r in loader(name, opening.date(), now)}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            doc.setdefault('backfill_errors', {})[name] = str(exc)[:200]
            continue
        added = [extra[g] for g in gaps if g in extra]
        if added:
            by[key] = sorted(by.get(key, []) + added, key=lambda r: r['time'])
            filled[name] = sorted(set(filled.get(name, [])) | {r['time'] for r in added})


def merge_backfilled(doc, by, names, loader_cache):
    """Re-apply candles backfilled on earlier ticks (they are not in the recorder's database)."""
    for name in names:
        times = set((doc.get('backfilled') or {}).get(name, []))
        rows = loader_cache.get(name) or []
        if not times or not rows:
            continue
        key = 'INDEX:' + name
        have = {r['time'] for r in by.get(key, [])}
        by[key] = sorted(by.get(key, []) + [r for r in rows if r['time'] in times and r['time'] not in have],
                         key=lambda r: r['time'])


def tick(cfg, now, calendar=None, candle_loader=None, context_loader=None, market_factory=None,
         backfill_loader=None, tagged=False, global_loader=None):
    """One pass: the brief, then every configured index. Returns (state, texts); saves nothing.

    With `tagged`, texts are (index or None, kind, text) for the outbox.
    `backfill_loader(name, day, now)` fills recorder gaps (default: none)."""
    fno = dict(cfg['index_fno'], _budget=cfg['index_fno']['risk_per_trade'] or cfg['risk_per_trade'])
    fno['_model'] = load_model(fno)
    desk = discipline(fno, now.date())
    fno['_budget'] = round(fno['_budget'] * desk['mult'], 0)
    fno['_desk_block'] = desk['block']
    fno['_capital'] = cfg.get('capital')
    calendar = calendar or DEFAULT_CALENDAR
    hours = calendar.hours(now.date(), 'NSE')
    if not hours:
        return None, []
    opening = dt.datetime.combine(now.date(), hours[0], IST)
    range_end = opening + dt.timedelta(minutes=fno['range_minutes'])
    deadline = dt.datetime.combine(now.date(), P.hhmm(fno['entry_deadline']), IST)
    doc = load_state(fno['state_dir'], now.date())
    out = []
    retry = doc.get('context_error_at')
    cached = doc.get('context') or {}
    stale = not cached or any('rv20' not in (cached.get(n) or {}) for n in fno['indices'])
    if stale and (not retry or now - _t(retry) >= dt.timedelta(minutes=5)):
        try:
            doc['context'] = (context_loader or fetch_context)(fno, now.date())
            doc.pop('context_error', None)
            doc.pop('context_error_at', None)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            doc.update(context_error=str(exc)[:300], context_error_at=now.isoformat())
    contexts = doc.get('context') or {}
    rows = (candle_loader or load_candles)(cfg['recorder']['state_dir'], now.date(), as_of=now.isoformat(),
                                           usable_only=True)
    by = {}
    for row in rows:
        by.setdefault(row['symbol'], []).append(row)
    for series in by.values():
        series.sort(key=lambda r: r['time'])
    if backfill_loader is not None and now >= opening:
        names = needed_indices(cfg)
        cache = {}
        if doc.get('backfilled'):
            for name in doc['backfilled']:
                try:
                    cache[name] = backfill_loader(name, now.date(), now)
                except (ValueError, OSError, KeyError, TypeError):
                    cache[name] = []
            merge_backfilled(doc, by, names, cache)
        # Only while a decision can still depend on it: before the entry deadline, or with a trade open.
        trading = now < deadline + SETTLE or any(r.get('status') == 'OPEN' for r in doc['indices'].values())
        if trading:
            backfill(doc, by, names, opening, now,
                     lambda n, d, t: cache[n] if n in cache else backfill_loader(n, d, t), until=now)
    vix_rows = by.get('INDEX:' + fno['vix_index'], [])
    vix = vix_rows[-1]['close'] if vix_rows else (contexts.get(fno['vix_index']) or {}).get('close')
    fno['_iv_scale'] = iv_scales(fno, contexts)
    market, warning = None, doc.get('context_error') and 'Daily levels unavailable: %s' % doc['context_error']
    if market_factory is not None:
        try:
            market, note = market_factory(fno, calendar, vix)
            warning = '; '.join(w for w in (warning, note) if w) or None
        except (ValueError, OSError) as exc:
            warning = '; '.join(w for w in (warning, 'option prices unavailable: %s' % exc) if w)
    if now >= opening:
        for name in fno['indices']:
            rec = doc['indices'].setdefault(name, dict(status='WAITING'))
            for text in process(name, rec, by.get('INDEX:' + name, []), vix_rows, now, opening,
                                dict(fno, **risk_guard(doc, fno, name)),
                                ctx=contexts.get(name), market=market, calendar=calendar, warning=warning,
                                vix_prev=(contexts.get(fno['vix_index']) or {}).get('close')):
                out.append((name, kind_of(text), text))
    # The brief waits for the daily levels (an offline morning) and is sent late, up to the entry deadline.
    if not doc.get('brief_sent') and P.hhmm(fno['brief_time']) <= now.time() and now < deadline and contexts:
        info = {}
        for name in fno['indices']:
            if market is None:
                continue
            try:
                expiry, lot = market.expiry(name, now.date())
                ctx = contexts.get(name) or {}
                info[name] = dict(expiry=expiry.isoformat(), lot=lot,
                                  dte=O.trading_days_between(now.date(), expiry, calendar),
                                  summary=market.summary(name, expiry, ctx.get('close') or 0))
                if ctx.get('close'):
                    info[name]['atm'] = atm_greeks(name, ctx['close'], expiry, now, market, fno)
            except (ValueError, OSError, KeyError, TypeError):
                pass
        late = now >= range_end + SETTLE
        # Built after this tick's candles so a late brief can show the opening range; sent first.
        if global_loader is not None and fno['global_cues'] and 'global' not in doc:
            try:
                doc['global'] = global_loader(now)
            except (ValueError, OSError, KeyError, TypeError):
                doc['global'] = {}
        out.insert(0, (None, 'brief', brief_text(fno, now, contexts, vix, info, warning, late=late, doc=doc,
                                                 desk=desk, cues=doc.get('global'))))
        doc['brief_sent'] = now.isoformat()
    doc['checked_at'] = now.isoformat()
    return doc, (out if tagged else [text for _, _, text in out])


def kind_of(text):
    """Outbox class of a message from its leading marker."""
    if text.startswith(('🟢', '🔴')):
        return 'signal'
    if text.startswith('⚪'):
        return 'skip'
    return 'update'


# --------------------------------------------------------------------------- outbox

def expires(message, now, fno):
    """When an undelivered message stops being worth sending.

    A new entry goes stale fast (acting late is worse than missing it); a
    skip note after 30 minutes; the brief at the entry deadline; trade
    updates and notes by the end of the session."""
    created = _t(message['created'])
    day = created.date()
    return {
        'signal': created + dt.timedelta(minutes=fno['fresh_minutes']),
        'skip': created + dt.timedelta(minutes=30),
        'brief': dt.datetime.combine(day, P.hhmm(fno['entry_deadline']), IST),
    }.get(message['kind'], dt.datetime.combine(day, dt.time(16, 0), IST))


def enqueue(doc, tagged, now, fno):
    """Add new messages to the outbox and drop the stale ones. Returns the expired signals."""
    box = doc.setdefault('outbox', [])
    undelivered = set(doc.setdefault('undelivered', []))
    for index, kind, text in tagged:
        if kind == 'update' and index in undelivered:
            continue                      # no follow-ups for an entry that never reached you
        box.append(dict(index=index, kind=kind, text=text, created=now.isoformat()))
    expired = [m for m in box if now >= expires(m, now, fno)]
    lost = [m for m in expired if m['kind'] == 'signal']
    undelivered |= {m['index'] for m in lost}
    keep = [m for m in box if m not in expired and not (m['kind'] == 'update' and m['index'] in undelivered)]
    if lost:
        keep.append(dict(index=None, kind='note', created=now.isoformat(), text=(
            '⚠️ <b>%d options signal%s expired undelivered</b> (no connection): %s. Entries more than %d minutes old '
            'are not sent; their follow-ups are dropped too.' % (
                len(lost), '' if len(lost) == 1 else 's',
                ', '.join('%s %s' % (fno['contracts'][m['index']]['short'], _t(m['created']).strftime('%H:%M'))
                          for m in lost), fno['fresh_minutes']))))
    doc['outbox'] = keep
    doc['undelivered'] = sorted(undelivered)
    return lost


def flush(doc, now, send, log=lambda m: None):
    """Send queued messages in order; stop at the first failure and keep the rest. Returns how many were sent."""
    box, sent = doc.get('outbox') or [], 0
    while box:
        m = box[0]
        delay = (now - _t(m['created'])).total_seconds() / 60
        text = ('⌛ <i>Delayed %d min (no connection).</i>\n' % delay + m['text']) if delay >= 2 else m['text']
        try:
            send(text)
        except (ValueError, OSError) as exc:
            log('index F&O send failed, %d message(s) queued: %s' % (len(box), exc))
            break
        box.pop(0)
        sent += 1
    doc['outbox'] = box
    return sent


def needed_indices(cfg):
    fno = cfg['index_fno']
    return list(dict.fromkeys(fno['indices'] + [fno['vix_index']]))


def main(argv=None):
    ap = argparse.ArgumentParser(description='NIFTY / BANK NIFTY index option signals (paper research only).')
    ap.add_argument('--config', help='picks JSON with optional "index_fno" and "recorder" objects')
    ap.add_argument('--env-file', default='var/telegram.env', help='KEY=VALUE file: Telegram and optional UPSTOX_ANALYTICS_TOKEN')
    ap.add_argument('--dry-run', action='store_true', help='print instead of sending; ignores the switch; saves nothing')
    ap.add_argument('--as-of', help='pretend it is this time (ISO 8601 with offset)')
    a = ap.parse_args(argv)
    log = lambda msg: print(msg, file=sys.stderr)
    try:
        cfg = P.load_config(a.config)
        if not a.dry_run and not S.enabled(S.path_for(cfg), 'index_fno'):
            print(json.dumps({'status': 'MUTED', 'reason': 'index F&O alerts are switched off in the dashboard'}))
            return 0
        now = dt.datetime.fromisoformat(a.as_of).astimezone(IST) if a.as_of else dt.datetime.now(IST)
        calendar = Calendar.load(cfg['calendar']) if cfg['calendar'] else DEFAULT_CALENDAR
        env = dict(P.read_env_file(a.env_file), **{k: v for k, v in os.environ.items()
                                                   if k.startswith('TELEGRAM_') or k.startswith('UPSTOX_')})
        upstox = (env.get('UPSTOX_ANALYTICS_TOKEN') or env.get('UPSTOX_ACCESS_TOKEN') or '').strip() or None
        doc, tagged = tick(cfg, now, calendar,
                           market_factory=lambda fno, cal, vix: O.make_market(fno, cal, vix, upstox),
                           backfill_loader=yahoo_candles, tagged=True, global_loader=fetch_global)
        if doc is None:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'not an NSE trading day'}))
            return 0
        if a.dry_run:
            print('\n\n'.join(text for _, _, text in tagged)
                  or json.dumps({k: v.get('status') for k, v in doc['indices'].items()}))
            return 0
        token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        fno = cfg['index_fno']
        enqueue(doc, tagged, now, fno)
        if doc['outbox'] and not (token and chat):
            raise ValueError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (environment or %s)' % a.env_file)
        # Save the queue first: nothing is lost if the process stops mid-send, and a
        # failed send stays queued for the next tick instead of being dropped.
        save_state(fno['state_dir'], doc)
        if doc['outbox']:
            sent = flush(doc, now, lambda text: P.send_telegram(token, chat, text), log)
            save_state(fno['state_dir'], doc)
            if sent:
                log('index F&O: sent %d message(s)' % sent)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
