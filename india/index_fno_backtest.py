"""Replay the index options desk over recent sessions with the live engine (paper research only).

Each past session is driven through `india.index_fno.tick` five minutes at a
time, exactly as the scheduler would. Inputs per session: yesterday's levels
from NSE's free daily history (bars before the session only) and five-minute
NIFTY, BANK NIFTY and India VIX candles from Yahoo (about the last 60 sessions;
Yahoo keeps no more). Premiums are the same India VIX model the live alerts
use.

Two passes:

  live    the configured rules: what Telegram would have sent.
  all     every breakout traded at full size, with the grade threshold,
          blockers and the daily risk guard off, so outcomes can be compared
          by grade.

Read the result as a sanity check, not proof: 60 sessions give at most about
120 breakouts, Yahoo candles are exchange candles where the live engine sees
sampled snapshots, and option P&L rests on modelled premiums.

    python3 -m india.index_fno_backtest --config config/picks.json
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sys
import tempfile

from . import index_fno as F
from . import options as O
from . import picks as P
from .data import IST
from .market import DEFAULT_CALENDAR, Calendar

YAHOO = F.YAHOO


def fetch_intraday(symbol, days, now):
    from .picks_backtest import fetch_intraday as fetch
    return fetch(symbol, days, now)


def rows_for(intraday, day):
    """Recorder-shaped candles for one session from {name: {day: [(start, o, h, l, c)]}}."""
    rows = []
    for name, by_day in intraday.items():
        for start, o, h, l, c in by_day.get(day) or []:
            end = start + dt.timedelta(minutes=5)
            rows.append(dict(symbol='INDEX:' + name, time=start.isoformat(), end=end.isoformat(),
                             known_at=(end + dt.timedelta(seconds=40)).isoformat(),
                             open=o, high=h, low=l, close=c, usable=True))
    return rows


def replay_day(cfg, day, rows, contexts, calendar):
    """Run one session through tick(); returns the final state document."""
    hours = calendar.hours(day, 'NSE')
    if not hours:
        return None
    with tempfile.TemporaryDirectory() as d:
        run = dict(cfg, index_fno=dict(cfg['index_fno'], state_dir=d))
        now = dt.datetime.combine(day, hours[0], IST) + dt.timedelta(minutes=4)
        end = dt.datetime.combine(day, hours[1], IST) + dt.timedelta(minutes=10)
        doc = None
        while now <= end:
            cutoff = now
            doc, _ = F.tick(run, now, calendar,
                            candle_loader=lambda *a, **k: [r for r in rows if F._t(r['known_at']) <= cutoff],
                            context_loader=lambda *a: contexts,
                            market_factory=lambda fno, cal, vix: O.make_market(fno, cal, vix))
            F.save_state(d, doc)
            now += dt.timedelta(minutes=5)
        return doc


def outcome(name, rec, day):
    """One traded signal's result: spot R and modelled option P&L."""
    sig = rec['signal']
    opt = sig['option']
    sign = 1 if sig['side'] == 'BUY' else -1
    spot_r = sum(leg['lots'] / opt['lots'] * sign * (leg['spot'] - sig['entry']) for leg in sig['legs']) / sig['risk']
    exit_kind = sig['events'][-1]['kind'] if sig['events'] else 'OPEN'
    return dict(date=day.isoformat(), index=name, side=sig['side'], grade=sig['grade'], score=sig['score'],
                blocked=bool(sig.get('blockers')), structure=opt['style'], lots=opt['lots'], risk=opt['risk'],
                spot_r=round(spot_r, 3), pnl=rec.get('pnl'), r=rec.get('r'), exit=exit_kind, t1=sig['t1_hit'])


def _mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize(trades):
    if not trades:
        return dict(trades=0)
    rs = [t['spot_r'] for t in trades]
    pnl = [t['pnl'] for t in trades if t['pnl'] is not None]
    wins = [t for t in trades if (t['pnl'] or 0) > 0]
    sd = math.sqrt(sum((r - sum(rs) / len(rs)) ** 2 for r in rs) / (len(rs) - 1)) if len(rs) > 1 else None
    exits = {}
    for t in trades:
        exits[t['exit']] = exits.get(t['exit'], 0) + 1
    option_r = [t['r'] for t in trades if t['r'] is not None]
    return dict(trades=len(trades), win_rate=round(len(wins) / len(trades) * 100, 1),
                avg_spot_r=_mean(rs), avg_option_r=_mean(option_r), se_spot_r=round(sd / math.sqrt(len(rs)), 3) if sd else None,
                net_pnl=round(sum(pnl), 0), avg_pnl=_mean(pnl), t1_rate=round(sum(t['t1'] for t in trades) / len(trades) * 100, 1),
                exits=exits)


def run(cfg, now, calendar=None, daily=None, intraday=None, log=lambda m: None):
    calendar = calendar or DEFAULT_CALENDAR
    fno = cfg['index_fno']
    names = list(fno['indices']) + [fno['vix_index']]
    days = []
    day = now.date() - dt.timedelta(days=92)
    while day < now.date():
        try:
            if calendar.trading_day(day, 'NSE'):
                days.append(day)
        except ValueError:
            pass
        day += dt.timedelta(days=1)
    if daily is None:
        log('fetching NSE daily history')
        daily = F.fetch_daily_bars(fno, months=12)
    if intraday is None:
        intraday = {}
        for name in names:
            log('fetching Yahoo 5m %s' % YAHOO[name])
            intraday[name] = fetch_intraday(YAHOO[name], days, now)
    days = [d for d in days if all(len(intraday[n].get(d) or []) >= 70 for n in names)]
    passes = {
        'live': cfg,
        'all': dict(cfg, index_fno=dict(fno, min_score=0, _ignore_blockers=True, max_open_trades=3,
                                       max_daily_loss=1e9, risk_per_trade=1e6, min_option_rr=0.01, send_skips=False)),
    }
    out = dict(sessions=len(days), first=days[0].isoformat() if days else None,
               last=days[-1].isoformat() if days else None, passes={})
    for label, pcfg in passes.items():
        trades, statuses = [], {}
        for d in days:
            doc = replay_day(pcfg, d, rows_for(intraday, d), F.contexts_for(daily, d), calendar)
            for name, rec in (doc or {}).get('indices', {}).items():
                statuses[rec['status']] = statuses.get(rec['status'], 0) + 1
                if rec['status'] == 'CLOSED':
                    trades.append(outcome(name, rec, d))
        by = lambda key: {k: summarize([t for t in trades if t[key] == k]) for k in sorted({t[key] for t in trades})}
        out['passes'][label] = dict(summary=summarize(trades), statuses=statuses, by_grade=by('grade'),
                                    by_index=by('index'), by_structure=by('structure'), by_side=by('side'),
                                    by_blocked=by('blocked'), trades=trades)
        log('%s pass: %d trades' % (label, len(trades)))
    return out


def _f(x, spec='%+.2f'):
    return '—' if x is None else spec % x


def format_report(rep):
    lines = ['Index options desk replay: %d sessions, %s to %s' % (rep['sessions'], rep['first'], rep['last']), '']
    for label, title in (('live', 'LIVE RULES (what would have been sent)'),
                         ('all', 'EVERY BREAKOUT (grade threshold, blockers and risk guard off)')):
        p = rep['passes'][label]
        s = p['summary']
        lines.append(title)
        if not s['trades']:
            lines.append('  no trades')
        else:
            money = (' · option P&L %s at your budget' % P.inr(s['net_pnl'], 0)) if label == 'live' else ''
            lines.append('  %d trades · win %.0f%% · T1 hit %.0f%% · spot %sR/trade (±%s) · option %sR/trade after charges%s' % (
                s['trades'], s['win_rate'], s['t1_rate'], _f(s['avg_spot_r']), _f(s['se_spot_r'], '%.2f'),
                _f(s['avg_option_r']), money))
            lines.append('  exits: ' + ', '.join('%s %d' % kv for kv in sorted(s['exits'].items())))
            for key, head in (('by_grade', 'grade'), ('by_blocked', 'blocked by a level <1R'), ('by_index', 'index'),
                              ('by_side', 'side'), ('by_structure', 'structure')):
                for k, v in p[key].items():
                    lines.append('  %-30s %3d trades · win %3.0f%% · spot %sR (±%s) · option %sR' % (
                        '%s=%s' % (head, k), v['trades'], v['win_rate'], _f(v['avg_spot_r']), _f(v['se_spot_r'], '%.2f'),
                        _f(v['avg_option_r'])))
        lines.append('  not traded: ' + ', '.join('%s %d' % kv for kv in sorted(p['statuses'].items()) if kv[0] != 'CLOSED'))
        lines.append('')
    lines.append('± is one standard error. Under ~30 trades in a group, differences are mostly noise. Spot R is the')
    lines.append('index move in units of the opening-range stop; option R is modelled (India VIX) P&L / risk, after charges.')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Replay the index options desk over the last ~60 sessions.')
    ap.add_argument('--config', help='picks JSON')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    try:
        cfg = P.load_config(a.config)
        calendar = Calendar.load(cfg['calendar']) if cfg['calendar'] else DEFAULT_CALENDAR
        rep = run(cfg, dt.datetime.now(IST), calendar, log=lambda m: print(m, file=sys.stderr))
        state = Path(cfg['index_fno']['state_dir'])
        state.mkdir(parents=True, exist_ok=True)
        (state / 'backtest-latest.json').write_text(json.dumps(rep, indent=2, default=str))
        print(json.dumps(rep, indent=2, default=str) if a.json else format_report(rep))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
