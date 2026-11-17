"""Replay the daily picks scorer over years of history before trusting it.

    python3 -m india.picks_backtest --config config/picks.json
    python3 -m india.picks_backtest --config config/picks.json --since 2024-01-01

For every past session it scores the universe exactly as the morning alert
does (the same features, filters, score, trade plan and sector/top_n
selection; learning off) using only candles up to that session, then trades
each candidate on the NEXT session in two ways:

  daily      every session in about four years of Yahoo daily candles. Only
             the next day's open/high/low/close are known, so each trade is
             walked along both simple paths (open->high->low->close and
             open->low->high->close) with the plan's gap skip, trigger, stop,
             T1/breakeven and T2, exiting at the close. The worse and better
             paths bracket the result. The 5-minute trigger close, the entry
             deadline and the 15:10 square-off are not checked.
  intraday   the sessions for which Yahoo still serves 5-minute candles (about
             the last 60 days): the alert's own rules via india.picks.simulate,
             exactly as the paper trades run. The same trades are also run the
             daily way, which shows how far off the daily approximation is.

The report answers three questions:

  1. Would the alert (top_n a day) have made money after costs?
  2. Does a higher score mean a better trade? (every candidate, by score bucket)
  3. Which setup traits are really worse, and how many trades does the
     learning step need before a lesson is more than noise?

Caveats, printed with every report: today's index constituents (survivorship
bias), split-adjusted but not dividend-adjusted Yahoo prices, estimated costs,
no results/events filter in history, and the daily approximation above.
Nothing here places orders.
"""
import argparse
import datetime as dt
import json
import math
import sys
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import picks as P
from . import review
from .data import IST, Bar
from .market import DEFAULT_CALENDAR, Calendar, costs

YAHOO_RANGE = '5y'
WARMUP = 210                # sessions before the first scored day (EMA200 needs 200)
BUCKETS = ((None, 50), (50, 60), (60, 65), (65, 70), (70, 80), (80, None))
CAVEATS = [
    'Universe is today\'s index list: stocks that dropped out (often after falling) are missing, '
    'which flatters the results (survivorship bias).',
    'Yahoo daily prices are split-adjusted but not dividend-adjusted; volumes are as reported.',
    'Costs are estimates (brokerage, STT, fees, GST, stamp duty, 5 bps slippage). No extra slippage for gaps.',
    'History has no results/events filter, which the live alert applies.',
    'Daily results are an approximation (see the intraday comparison for its bias).',
]


# --------------------------------------------------------------------------- simulation

def walk(path, E, SL, T1, T2, NC, qty):
    """Follow the plan along a price path (long scale; straight lines between points).

    Returns (status, fill, legs, reason) with legs [(qty, price)].
    """
    O = path[0]
    if O > NC:
        return 'SKIPPED', None, [], 'gap past no-chase'
    fill = O if O >= E else None          # opened beyond the trigger, inside the no-chase band
    stop, open_q, half, legs, t1 = SL, qty, qty // 2, [], False
    for a, b in zip(path, path[1:]):
        if fill is None:
            if not a <= E < b:
                continue
            fill = E                      # crossed the trigger on the way up
        if b > a:
            if not t1 and b >= T1:
                if half:
                    legs.append((half, T1))
                open_q, stop, t1 = qty - half, fill, True
            if t1 and b >= T2:
                return 'CLOSED', fill, legs + [(open_q, T2)], 'T1+T2'
        elif b <= stop:
            return 'CLOSED', fill, legs + [(open_q, stop)], 'T1+breakeven' if t1 else 'stop'
    if fill is None:
        return 'NOT_TRIGGERED', None, [], 'entry not reached'
    return 'CLOSED', fill, legs + [(open_q, path[-1])], 'T1+close' if t1 else 'close'


def simulate_daily(plan, bar, cost_config=None, path='worse'):
    """Trade one plan on the next session's daily candle.

    Only open, high, low and close are known, so the day is walked both ways,
    open->high->low->close and open->low->high->close, applying the plan's
    gap skip, trigger, stop, T1 (book half, stop to entry), T2 and an exit at
    the close. path='worse' returns the worse of the two, 'better' the better.
    A day that swung more than once can be worse than both.
    """
    sign = 1 if plan['side'] == 'BUY' else -1
    S = lambda x: sign * x
    E, SL, T1, T2, NC = (S(plan[k]) for k in ('entry', 'stop', 'target1', 'target2', 'no_chase'))
    O, C = S(bar.open), S(bar.close)
    H, L = max(S(bar.high), S(bar.low)), min(S(bar.high), S(bar.low))
    results = []
    for points in ((O, H, L, C), (O, L, H, C)):
        status, fill, legs, reason = walk(points, E, SL, T1, T2, NC, plan['qty'])
        if status != 'CLOSED':
            return {'status': status, 'reason': reason, 'r': None, 'net': None}
        gross = sum(n * (x - fill) for n, x in legs)
        cost = 0.0
        for n, x in legs:
            if n:
                buy, sell = (S(fill), S(x)) if sign > 0 else (S(x), S(fill))
                cost += costs(buy, sell, n, 'NSE', cost_config)['total']
        net = gross - cost
        results.append({'status': 'CLOSED', 'reason': reason, 'fill': round(S(fill), 2), 'net': round(net, 2),
                        'r': round(net / plan['max_loss'], 3) if plan['max_loss'] else None})
    results.sort(key=lambda r: r['net'])
    return results[0] if path == 'worse' else results[-1]


def both_orders(plan, bar, cost_config=None):
    """The worse path's result, with the better path's as r_hi / net_hi / reason_hi."""
    lo = simulate_daily(plan, bar, cost_config, 'worse')
    hi = simulate_daily(plan, bar, cost_config, 'better')
    return dict(lo, r_hi=hi['r'], net_hi=hi['net'], reason_hi=hi['reason'])


def generous(trades):
    """The same trades scored on the better of the two daily paths."""
    return [dict(t, r=t['r_hi'], net=t['net_hi']) for t in trades]


# --------------------------------------------------------------------------- statistics

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _sd(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _r(x, places=3):
    return round(x, places) if x is not None else None


def stats(trades):
    """Outcome statistics for trade records ({'status', 'r', 'net', 'date'}), oldest first."""
    closed = [t for t in trades if t['status'] == 'CLOSED']
    rs = [t['r'] for t in closed]
    nets = [t['net'] for t in closed]
    wins, losses = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    peak = total = drawdown = 0.0
    for t in sorted(closed, key=lambda t: t['date']):
        total += t['net']
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    sd = _sd(rs)
    return {
        'signals': len(trades), 'triggered': len(closed),
        'skipped_gap': sum(t['status'] == 'SKIPPED' for t in trades),
        'not_triggered': sum(t['status'] == 'NOT_TRIGGERED' for t in trades),
        'trigger_rate': _r(100 * len(closed) / len(trades), 1) if trades else None,
        'win_rate': _r(100 * sum(x > 0 for x in nets) / len(nets), 1) if nets else None,
        'avg_r': _r(_mean(rs)), 'sd_r': _r(sd), 'se_r': _r(sd / math.sqrt(len(rs))) if sd is not None else None,
        'profit_factor': _r(wins / losses, 2) if losses else None,
        'net_pnl': round(sum(nets), 2), 'max_drawdown': round(drawdown, 2),
    }


def bucket_label(lo, hi):
    return '<%d' % hi if lo is None else '%d+' % lo if hi is None else '%d-%d' % (lo, hi)


def score_buckets(cands):
    out = []
    for lo, hi in BUCKETS:
        group = [c for c in cands if (lo is None or c['score'] >= lo) and (hi is None or c['score'] < hi)]
        if group:
            out.append(dict(stats(group), bucket=bucket_label(lo, hi), avg_r_hi=stats(generous(group))['avg_r']))
    return out


def trait_table(trades, min_trades=8, threshold=-0.25, margin=0.25):
    """Each review trait: closed trades with vs without it, the gap and its standard error."""
    closed = [t for t in trades if t['status'] == 'CLOSED']
    rows = []
    for code, label in review.TRAITS.items():
        mine = [t['r'] for t in closed if code in t['traits']]
        rest = [t['r'] for t in closed if code not in t['traits']]
        if len(mine) < 2 or len(rest) < 2:
            continue
        gap = _mean(mine) - _mean(rest)
        se = review.gap_se(mine, rest)
        verdict = 'worse' if gap <= -2 * se else 'better' if gap >= 2 * se else 'no clear difference'
        rows.append({'trait': code, 'label': label, 'trades': len(mine), 'avg_r': _r(_mean(mine)),
                     'others_avg_r': _r(_mean(rest)), 'gap_r': _r(gap), 'gap_se': _r(se), 'verdict': verdict,
                     'live_rule_lesson': (len(mine) >= min_trades and _mean(mine) <= threshold
                                          and gap <= -margin and -gap >= 2 * se)})
    rows.sort(key=lambda r: r['gap_r'])
    return rows


def noise(trades, margin=0.25):
    """How noisy one trade's R is, and the trades a lesson needs to see a `margin` R gap at 2 SE."""
    rs = [t['r'] for t in trades if t['status'] == 'CLOSED']
    sd = _sd(rs)
    if sd is None:
        return None
    return {'sd_r': _r(sd), 'se_at_8': _r(sd / math.sqrt(8)),
            # gap SE with a large comparison group ~ sd/sqrt(n); 2*sd/sqrt(n) <= margin
            'trades_to_detect': math.ceil((2 * sd / margin) ** 2), 'margin_r': margin}


# --------------------------------------------------------------------------- data

def _bars_to_json(bars):
    return [[b.time.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in bars]


def _bars_from_json(rows):
    return [Bar(dt.datetime.fromisoformat(r[0]), *r[1:]) for r in rows]


def load_history(symbols, now, cfg, fetch=None, refresh=False, calendar=None, log=lambda m: None):
    """{symbol: completed daily bars}, {symbol: error}. Cached per day in state_dir/backtest-cache."""
    fetch = fetch or (lambda s: P.fetch_yahoo_daily(s, YAHOO_RANGE))
    calendar = calendar or DEFAULT_CALENDAR
    cache = Path(cfg['state_dir']) / 'backtest-cache'
    tag = now.date().isoformat()
    cache.mkdir(parents=True, exist_ok=True)
    for old in cache.glob('*.json'):
        if not old.name.endswith('@%s.json' % tag):
            old.unlink()

    def one(sym):
        path = cache / ('%s@%s.json' % (sym.replace('/', '_'), tag))
        if path.is_file() and not refresh:
            try:
                return sym, _bars_from_json(json.loads(path.read_text())), None
            except (OSError, ValueError, TypeError, IndexError):
                pass
        try:
            bars = P.completed_bars(fetch(sym), now, calendar)
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            return sym, None, str(exc)
        path.write_text(json.dumps(_bars_to_json(bars)))
        return sym, bars, None
    with ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
        results = list(pool.map(one, symbols))
    history, failed = {}, {}
    for sym, bars, err in results:
        if err:
            failed[sym] = err
        else:
            history[sym] = bars
    log('history: %d symbols loaded, %d failed' % (len(history), len(failed)))
    return history, failed


def fetch_intraday(symbol, days, now):
    """{day: 5-minute candles} from one Yahoo 60-day request."""
    path = '/v8/finance/chart/%s?range=60d&interval=5m' % urllib.parse.quote(symbol)
    last = 'unavailable'
    for host in ('query2', 'query1'):
        try:
            doc = json.loads(P.http_get('https://%s.finance.yahoo.com%s' % (host, path)))
            return {d: P.parse_yahoo_5m(doc, symbol, d, now) for d in days}
        except urllib.error.HTTPError as exc:
            last = 'HTTP %d' % exc.code
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            last = str(exc) or type(exc).__name__
    raise ValueError('Yahoo 5m: ' + last)


# --------------------------------------------------------------------------- replay

def market_bias_by_date(bars):
    """{date: bullish|bearish|neutral} from the index close vs EMA20/EMA50, as market_bias()."""
    closes = [b.close for b in bars]
    e20, e50 = P.ema_series(closes, 20), P.ema_series(closes, 50)
    out = {}
    for i, b in enumerate(bars):
        if e50[i] is None:
            continue
        c = closes[i]
        out[b.time.date()] = 'bullish' if c > e20[i] > e50[i] else 'bearish' if c < e20[i] < e50[i] else 'neutral'
    return out


def scan_symbol(sym, bars, bias, next_session, cfg, since=None):
    """Every candidate plan in one history: [(signal day, next bar, plan)]."""
    s = P.series(bars)
    out = []
    for i in range(WARMUP, len(bars) - 1):
        day = bars[i].time.date()
        if (since and day < since) or next_session.get(day) != bars[i + 1].time.date():
            continue     # before the window, or a missing session in this stock's data
        try:
            f = P.features(bars, s, i)
            cand, _ = P.evaluate(f, cfg, bias.get(day, 'unknown'))
        except (ValueError, TypeError, ZeroDivisionError):
            continue
        if not cand:
            continue
        plan = P.build_plan(sym, f, cand, cfg)
        if plan is None:
            continue
        plan.update(base_score=plan['score'], market_bias=bias.get(day, 'unknown'), counter_trend=cand['counter_trend'])
        plan['traits'] = review.traits(plan)
        out.append((day, bars[i + 1], plan))
    return out


def _record(day, nxt, plan, result):
    return {'date': day.isoformat(), 'trade_date': nxt.time.date().isoformat(), 'symbol': plan['symbol'],
            'side': plan['side'], 'score': plan['score'], 'traits': plan['traits'], 'entry': plan['entry'],
            'stop': plan['stop'], 'target1': plan['target1'], 'target2': plan['target2'], 'qty': plan['qty'],
            'max_loss': plan['max_loss'], **result}


def replay(cfg, history, market_bars, sectors=None, since=None):
    """(all candidate records, alert records, alert plans) over the shared history."""
    bias = market_bias_by_date(market_bars)
    sessions = [b.time.date() for b in market_bars]
    next_session = dict(zip(sessions, sessions[1:]))
    by_day = {}
    for sym, bars in history.items():
        for day, nxt, plan in scan_symbol(sym, bars, bias, next_session, cfg, since):
            by_day.setdefault(day, []).append((nxt, plan))
    cands, alerts, plans = [], [], []
    for day in sorted(by_day):
        rows = by_day[day]
        nxt_of = {id(p): nxt for nxt, p in rows}
        for nxt, p in rows:
            cands.append(_record(day, nxt, p, both_orders(p, nxt, cfg.get('cost_config'))))
        for p in P.select([p for _, p in rows], cfg, sectors):
            nxt = nxt_of[id(p)]
            alerts.append(_record(day, nxt, p, both_orders(p, nxt, cfg.get('cost_config'))))
            plans.append((nxt.time.date(), p))
    return cands, alerts, plans


def intraday_check(alert_plans, alerts, now, fetch=fetch_intraday, workers=4, log=lambda m: None):
    """Exact 5-minute replay of the alert picks Yahoo still has candles for, vs the daily guess."""
    first = now.date() - dt.timedelta(days=58)
    wanted = {}
    for day, p in alert_plans:
        if first <= day < now.date():
            wanted.setdefault(p['symbol'], []).append((day, p))
    if not wanted:
        return None

    def one(sym):
        try:
            return sym, fetch(sym, sorted({d for d, _ in wanted[sym]}), now), None
        except (ValueError, OSError) as exc:
            return sym, None, str(exc)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        got = list(pool.map(one, wanted))
    daily_by_key = {(a['trade_date'], a['symbol']): a for a in alerts}
    exact, approx, failed = [], [], 0
    for sym, days, err in got:
        if err:
            failed += 1
            continue
        for day, p in wanted[sym]:
            bars = days.get(day) or []
            if not bars:
                continue
            t = P.simulate(p, bars, now)
            if t['status'] in ('WAITING', 'OPEN', 'NO_DATA'):
                continue
            exact.append({'date': day.isoformat(), 'status': t['status'], 'r': t['r_multiple'],
                          'net': t['net_pnl'], 'traits': p.get('traits', [])})
            approx.append(daily_by_key[(day.isoformat(), sym)])
    log('intraday: %d alert trades replayed on 5-minute candles (%d symbols failed)' % (len(exact), failed))
    if not exact:
        return None
    same = sum(e['status'] == a['status'] and (e['r'] is None or (e['r'] > 0) == (a['r'] > 0))
               for e, a in zip(exact, approx))
    return {'from': min(e['date'] for e in exact), 'to': max(e['date'] for e in exact),
            'exact': stats(exact), 'daily_same_trades': stats(approx), 'daily_same_trades_hi': stats(generous(approx)),
            'agreement_pct': round(100 * same / len(exact), 1), 'symbols_failed': failed}


def run(cfg, now, since=None, fetch=None, intraday=True, intraday_fetch=fetch_intraday, refresh=False,
        calendar=None, log=lambda m: None):
    cfg = dict(cfg, learning=False)
    symbols, sectors, source, warnings = P.load_universe(cfg, cfg['state_dir'])
    log('universe: %d symbols from %s' % (len(symbols), source))
    history, failed = load_history(symbols + [P.MARKET_INDEX], now, cfg, fetch, refresh, calendar, log)
    market = history.pop(P.MARKET_INDEX, None)
    if not market or len(market) < WARMUP + 2:
        raise ValueError('NIFTY 50 history unavailable: %s' % failed.get(P.MARKET_INDEX, 'too short'))
    cands, alerts, plans = replay(cfg, history, market, sectors, since)
    if not alerts:
        raise ValueError('no candidates in the backtest window; check the universe and --since')
    noisy = noise(alerts)
    years = {}
    for a in alerts:
        years.setdefault(a['trade_date'][:4], []).append(a)
    report = {
        'generated_at': now.isoformat(), 'universe': len(symbols), 'universe_source': source,
        'loaded': len(history), 'failed': failed, 'warnings': warnings,
        'from': alerts[0]['trade_date'], 'to': alerts[-1]['trade_date'],
        'sessions': len({a['trade_date'] for a in cands}),
        'config': {k: cfg[k] for k in ('top_n', 'min_score', 'max_per_sector', 'allow_short', 'risk_per_trade',
                                       'capital', 'min_rel_volume', 'min_atr_pct', 'max_atr_pct')},
        'alert': stats(alerts), 'alert_hi': stats(generous(alerts)),
        'by_side': {side: stats([a for a in alerts if a['side'] == side]) for side in ('BUY', 'SELL')
                    if any(a['side'] == side for a in alerts)},
        'by_year': {y: stats(v) for y, v in sorted(years.items())},
        'score_buckets': score_buckets(cands),
        'score_r_correlation': correlation([(c['score'], c['r']) for c in cands if c['status'] == 'CLOSED']),
        'traits': trait_table([c for c in cands if c['score'] >= cfg['min_score']],
                              cfg['learn_min_trades']),
        'noise': noisy,
        'learning': {'learn_min_trades': cfg['learn_min_trades'],
                     'note': 'Live lessons need learn_min_trades AND a gap beyond 2 standard errors.'},
        'intraday': intraday_check(plans, alerts, now, intraday_fetch, cfg['workers'], log) if intraday else None,
        'caveats': CAVEATS, 'execution_enabled': False,
        'trades': alerts,
    }
    return report


def correlation(pairs):
    if len(pairs) < 3:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = _mean(xs), _mean(ys)
    sx, sy = _sd(xs), _sd(ys)
    if not sx or not sy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in pairs) / ((len(pairs) - 1) * sx * sy), 3)


# --------------------------------------------------------------------------- text

def _fmt(x, spec='%+.2f', none='—'):
    return none if x is None else spec % x


def format_report(rep):
    a, h = rep['alert'], rep['alert_hi']
    lines = [
        'Picks backtest · %s → %s · %d sessions · %d/%d symbols (%s)' % (
            rep['from'], rep['to'], rep['sessions'], rep['loaded'], rep['universe'], rep['universe_source']),
        'Settings: top %(top_n)d, min score %(min_score)d, risk ₹%(risk_per_trade)s/trade, shorts %(allow_short)s' % rep['config'],
        '',
        'ALERT (top picks a day, daily-candle approximation)',
        '  %d signals · %d triggered (%s%%) · %d gap skips · %d not triggered' % (
            a['signals'], a['triggered'], _fmt(a['trigger_rate'], '%.0f'), a['skipped_gap'], a['not_triggered']),
        '  worse path : win %s%% · avg %sR (± %s SE) · profit factor %s · net %s · max drawdown %s' % (
            _fmt(a['win_rate'], '%.0f'), _fmt(a['avg_r']), _fmt(a['se_r'], '%.2f'), _fmt(a['profit_factor'], '%.2f'),
            P.inr(a['net_pnl'], 0), P.inr(a['max_drawdown'], 0)),
        '  better path: win %s%% · avg %sR (± %s SE) · profit factor %s · net %s · max drawdown %s' % (
            _fmt(h['win_rate'], '%.0f'), _fmt(h['avg_r']), _fmt(h['se_r'], '%.2f'), _fmt(h['profit_factor'], '%.2f'),
            P.inr(h['net_pnl'], 0), P.inr(h['max_drawdown'], 0)),
        '  (paths open-high-low-close and open-low-high-close; the order inside a day is unknown)',
    ]
    for side, s in rep['by_side'].items():
        lines.append('  %-4s %4d trades · win %s%% · avg %sR · net %s' % (
            side, s['triggered'], _fmt(s['win_rate'], '%.0f'), _fmt(s['avg_r']), P.inr(s['net_pnl'], 0)))
    lines += ['', 'BY YEAR (alert)']
    for y, s in rep['by_year'].items():
        lines.append('  %s  %4d trades · win %s%% · avg %sR · net %s' % (
            y, s['triggered'], _fmt(s['win_rate'], '%.0f'), _fmt(s['avg_r']), P.inr(s['net_pnl'], 0)))
    lines += ['', 'SCORE vs OUTCOME (every candidate, not just the top picks)']
    for b in rep['score_buckets']:
        lines.append('  score %-6s %5d trades · win %s%% · avg %sR (± %s) · better path %sR' % (
            b['bucket'], b['triggered'], _fmt(b['win_rate'], '%.0f'), _fmt(b['avg_r']), _fmt(b['se_r'], '%.2f'),
            _fmt(b['avg_r_hi'])))
    lines.append('  correlation of score with R: %s' % _fmt(rep['score_r_correlation'], '%+.3f'))
    lines += ['', 'TRAITS (candidates at or above min score; gap = avg R with trait minus without)']
    for t in rep['traits']:
        lines.append('  %-44s %5d trades · gap %sR ± %s · %s%s' % (
            t['label'][:44], t['trades'], _fmt(t['gap_r']), _fmt(t['gap_se'], '%.2f'), t['verdict'],
            ' · would be a live lesson' if t['live_rule_lesson'] else ''))
    n = rep['noise']
    if n:
        lines += ['', 'NOISE',
                  '  One trade\'s R varies by %.2fR (sd). The average of 8 trades is uncertain by ±%.2fR (1 SE).' % (
                      n['sd_r'], n['se_at_8']),
                  '  Seeing a %.2fR gap at 2 SE takes about %d trades with a trait; learn_min_trades is %d. '
                  'The 2-SE check stops earlier lessons that are only noise.' % (
                      n['margin_r'], n['trades_to_detect'], rep['learning']['learn_min_trades'])]
    i = rep['intraday']
    if i:
        e, d, dh = i['exact'], i['daily_same_trades'], i['daily_same_trades_hi']
        lines += ['', 'INTRADAY CHECK (%s → %s, exact 5-minute rules vs the daily guess on the same picks)' % (i['from'], i['to']),
                  '  5-minute: %d triggered · win %s%% · avg %sR · net %s' % (
                      e['triggered'], _fmt(e['win_rate'], '%.0f'), _fmt(e['avg_r']), P.inr(e['net_pnl'], 0)),
                  '  daily   : %d triggered · avg %sR (worse path) to %sR (better path) · net %s to %s' % (
                      d['triggered'], _fmt(d['avg_r']), _fmt(dh['avg_r']), P.inr(d['net_pnl'], 0), P.inr(dh['net_pnl'], 0)),
                  '  same status and direction: %.0f%% of trades' % i['agreement_pct']]
    lines += ['', 'CAVEATS'] + ['  - ' + c for c in rep['caveats']]
    for w in rep['warnings']:
        lines.append('  - ' + w)
    if rep['failed']:
        lines.append('  - %d symbol(s) had no history: %s' % (len(rep['failed']), ', '.join(sorted(rep['failed'])[:10])))
    lines.append('Research only. No orders were placed.')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Backtest the daily picks scorer on history (research only).')
    ap.add_argument('--config', help='JSON settings (see config/picks.example.json)')
    ap.add_argument('--since', help='first signal day, YYYY-MM-DD (default: as far back as the history allows)')
    ap.add_argument('--refresh', action='store_true', help='refetch history even if today\'s cache exists')
    ap.add_argument('--no-intraday', action='store_true', help='skip the 5-minute replay of recent alerts')
    ap.add_argument('--json', metavar='PATH', help='also write the full report here')
    ap.add_argument('--as-of', help='pretend it is this IST time (ISO 8601 with offset)')
    a = ap.parse_args(argv)
    try:
        cfg = P.load_config(a.config)
        now = dt.datetime.fromisoformat(a.as_of).astimezone(IST) if a.as_of else dt.datetime.now(IST)
        calendar = Calendar.load(cfg['calendar']) if cfg['calendar'] else DEFAULT_CALENDAR
        since = dt.date.fromisoformat(a.since) if a.since else None
        rep = run(cfg, now, since, intraday=not a.no_intraday, refresh=a.refresh, calendar=calendar,
                  log=lambda m: print(m, file=sys.stderr))
        state = Path(cfg['state_dir'])
        state.mkdir(parents=True, exist_ok=True)
        (state / 'backtest-latest.json').write_text(json.dumps(rep, indent=2))
        if a.json:
            Path(a.json).write_text(json.dumps(rep, indent=2))
        print(format_report(rep))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
