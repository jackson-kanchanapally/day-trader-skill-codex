"""Walk-forward learning for the index options desk over the last ~60 sessions (paper trades only).

Day by day, oldest first:

  1. The session is replayed through the live engine five minutes at a time
     (`index_fno_backtest.replay_day`): at 10:05 only candles known by 10:05
     exist. Every opening-range breakout becomes a paper trade with its
     contract, premium and spot stop, T1/T2, time stop and square-off, and
     the engine records the setup's features as they were at the signal.
  2. Before the day is played, a logistic model trained ONLY on earlier
     days' finished trades predicts each breakout's chance of a win (option
     P&L after charges > 0). At or above `threshold` it is TAKEN, else
     SKIPPED. Until `warmup` trades exist there is no model (WARMUP).
  3. After the close, the day's outcomes join the training set, and the
     next day starts.

Every breakout is followed to its exit whether taken or not (paper), so the
model learns from all of them. It is scored against two baselines over the
same days: every breakout, and the desk's current grade rule. A control
re-runs the whole walk with shuffled win/loss labels: if shuffled labels
filter about as well, the model learned noise.

Sixty sessions give about a hundred breakouts. Read this as a check on
whether anything is learnable, not as a trained edge.

Memory (`<index_fno.state_dir>/learning/`), so learning continues past
Yahoo's 60-day window:

  dataset.jsonl  every learned breakout: signal-time features, outcome, and
                 what the live desk decided (P(win), taken or not).
  model.json     the model trained on all of it; the live desk loads it and,
                 with `index_fno.learning: "decide"`, takes a breakout only
                 at P(win) >= `learn_threshold`.

  --seed     put the 60-day walk's breakouts into memory and train.
  --update   after the close: replay today's session (Yahoo five-minute
             candles, else the recorder's), add every breakout with its
             outcome and the live decision, retrain, and send a short note.
             `--serve` runs this once per session while the switch is on.
  --report   the walk-forward evaluation over everything in memory.

    python3 -m india.index_fno_learn --config config/picks.json
    python3 -m india.index_fno_learn --config config/picks.json --seed
    python3 -m india.index_fno_learn --config config/picks.json --update
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile

from . import index_fno as F
from . import index_fno_backtest as B
from . import picks as P
from .data import IST
from .market import DEFAULT_CALENDAR, Calendar

FEATURES = ('trend_aligned', 'cpr_pct', 'range_pct', 'vix', 'vix_change_pct', 'candle_body', 'room_r',
            'minutes_after_range', 'gap_pct', 'move_from_open_atr', 'atr_pct', 'extension', 'is_buy', 'is_bank')


# --------------------------------------------------------------------------- model

def _solve(a, b):
    """Gaussian elimination with partial pivoting; a is square, b a vector."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def _sigmoid(z):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


class Logit:
    """L2-regularised logistic regression on standardised features (Newton's method)."""

    def __init__(self, l2=1.0, features=FEATURES):
        self.l2, self.features = l2, tuple(features)

    def _x(self, f):
        return [1.0] + [((f.get(k) or 0.0) - mu) / sd for k, mu, sd in zip(self.features, self.mu, self.sd)]

    def fit(self, rows, labels):
        cols = [[(r.get(k) or 0.0) for r in rows] for k in self.features]
        self.mu = [sum(c) / len(c) for c in cols]
        self.sd = [math.sqrt(sum((v - mu) ** 2 for v in c) / len(c)) or 1.0 for c, mu in zip(cols, self.mu)]
        xs = [self._x(r) for r in rows]
        k = len(xs[0])
        w = [0.0] * k
        for _ in range(25):
            grad = [0.0] * k
            hess = [[0.0] * k for _ in range(k)]
            for x, y in zip(xs, labels):
                p = _sigmoid(sum(a * b for a, b in zip(w, x)))
                s = p * (1 - p)
                for i in range(k):
                    grad[i] += (p - y) * x[i]
                    xi = s * x[i]
                    for j in range(k):
                        hess[i][j] += xi * x[j]
            for i in range(1, k):                       # no penalty on the intercept
                grad[i] += self.l2 * w[i]
                hess[i][i] += self.l2
            step = _solve(hess, grad)
            w = [a - b for a, b in zip(w, step)]
            if max(abs(s) for s in step) < 1e-6:
                break
        self.w = w
        return self

    def predict(self, f):
        return _sigmoid(sum(a * b for a, b in zip(self.w, self._x(f))))

    def weights(self):
        """Standardised weights, strongest first: + raises the chance of a win."""
        return sorted(((k, round(w, 3)) for k, w in zip(self.features, self.w[1:])), key=lambda kv: -abs(kv[1]))


# --------------------------------------------------------------------------- candidate trades

def day_trades(doc, day, min_score):
    """Every breakout the replay paper-traded on `day`, with its signal-time features and outcome."""
    out = []
    for name, rec in sorted((doc or {}).get('indices', {}).items()):
        sig = rec.get('signal') or {}
        if rec.get('status') != 'CLOSED' or not sig.get('features') or rec.get('r') is None:
            continue
        o = B.outcome(name, rec, day)
        o.update(time=sig['time'], features=sig['features'], contract=F.contract_name(name, sig['option'],
                                                                                      dict(contracts=F.CONTRACTS)),
                 entry=sig['entry'], stop=sig['initial_stop'], target1=sig['target1'], target2=sig['target2'],
                 win=1 if rec['r'] > 0 else 0, rule_take=sig['score'] >= min_score and not sig.get('blockers'))
        out.append(o)
    return sorted(out, key=lambda t: t['time'])


def collect(cfg, days, daily, intraday, calendar, log=lambda m: None):
    """[(day, trades)] in date order: every breakout traded at full size, guards off (paper)."""
    fno = cfg['index_fno']
    every = dict(cfg, index_fno=dict(fno, min_score=0, _ignore_blockers=True, max_open_trades=3, max_daily_loss=1e9,
                                     risk_per_trade=1e6, min_option_rr=0.01, send_skips=False, learning='off'))
    out = []
    for i, d in enumerate(days):
        rows = intraday[d] if isinstance(intraday.get(d), list) else B.rows_for(intraday, d)
        contexts = daily[d] if isinstance(daily.get(d), dict) else F.contexts_for(daily, d)
        doc = B.replay_day(every, d, rows, contexts, calendar)
        out.append((d, day_trades(doc, d, fno['min_score'])))
        if (i + 1) % 10 == 0:
            log('replayed %d/%d sessions' % (i + 1, len(days)))
    return out


# --------------------------------------------------------------------------- walk forward

def walk(by_day, warmup=25, threshold=0.55, l2=1.0, labels=None):
    """Predict each day with a model fitted on earlier days only; learn from the day after its close.

    `labels` (optional) replaces the true win labels for training only (the shuffled control)."""
    train, train_y, ledger = [], [], []
    for day, trades in by_day:
        model = Logit(l2).fit([t['features'] for t in train], train_y) if len(train) >= warmup and \
            0 < sum(train_y) < len(train_y) else None
        for t in trades:
            p = model.predict(t['features']) if model else None
            ledger.append(dict(t, p_win=None if p is None else round(p, 3), trained_on=len(train),
                               decision='WARMUP' if p is None else 'TAKE' if p >= threshold else 'SKIP'))
        for t in trades:                                # outcomes are known only after the close
            train.append(t)
            train_y.append(labels[id(t)] if labels else t['win'])
    return ledger


def stats(trades, budget):
    if not trades:
        return dict(trades=0)
    rs = [t['r'] for t in trades]
    mean = sum(rs) / len(rs)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)) if len(rs) > 1 else None
    return dict(trades=len(trades), win_rate=round(sum(t['win'] for t in trades) / len(trades) * 100, 1),
                avg_r=round(mean, 3), se_r=round(sd / math.sqrt(len(rs)), 3) if sd else None,
                total_r=round(sum(rs), 2), pnl=round(sum(rs) * budget, 0))


def evaluate(ledger, budget):
    scored = [t for t in ledger if t['decision'] != 'WARMUP']
    out = dict(every=stats(scored, budget), rules=stats([t for t in scored if t['rule_take']], budget),
               model=stats([t for t in scored if t['decision'] == 'TAKE'], budget),
               model_skipped=stats([t for t in scored if t['decision'] == 'SKIP'], budget))
    if scored:
        hits = sum((t['p_win'] >= 0.5) == bool(t['win']) for t in scored)
        brier = sum((t['p_win'] - t['win']) ** 2 for t in scored) / len(scored)
        # The no-skill forecast: yesterday's running win rate.
        base, wins, seen = [], 0, 0
        for t in ledger:
            if t['decision'] != 'WARMUP':
                base.append(((wins / seen) if seen else 0.5, t['win']))
            wins, seen = wins + t['win'], seen + 1
        out['prediction'] = dict(
            accuracy=round(hits / len(scored) * 100, 1), brier=round(brier, 4),
            base_brier=round(sum((p - y) ** 2 for p, y in base) / len(base), 4),
            base_accuracy=round(sum((p >= 0.5) == bool(y) for p, y in base) / len(base) * 100, 1))
    return out


def shuffled_control(by_day, warmup, threshold, l2, real_avg, n=100, seed=7):
    """Re-run the walk with win/loss labels shuffled; share of runs whose TAKEN trades beat the real model."""
    trades = [t for _, ts in by_day for t in ts]
    rng = random.Random(seed)
    better, avgs = 0, []
    for _ in range(n):
        ys = [t['win'] for t in trades]
        rng.shuffle(ys)
        labels = {id(t): y for t, y in zip(trades, ys)}
        taken = [t for t in walk(by_day, warmup, threshold, l2, labels) if t['decision'] == 'TAKE']
        avg = sum(t['r'] for t in taken) / len(taken) if taken else 0.0
        avgs.append(avg)
        better += real_avg is not None and avg >= real_avg
    avgs.sort()
    return dict(runs=n, p_value=round(better / n, 3) if real_avg is not None else None,
                median_avg_r=round(avgs[n // 2], 3), p90_avg_r=round(avgs[int(n * 0.9)], 3))


def run(cfg, now, calendar=None, daily=None, intraday=None, warmup=25, threshold=0.55, l2=1.0, shuffles=100,
        log=lambda m: None):
    calendar = calendar or DEFAULT_CALENDAR
    fno = cfg['index_fno']
    names = list(fno['indices']) + [fno['vix_index']]
    days, day = [], now.date() - dt.timedelta(days=92)
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
            log('fetching Yahoo 5m %s' % B.YAHOO[name])
            intraday[name] = B.fetch_intraday(B.YAHOO[name], days, now)
    days = [d for d in days if all(len(intraday[n].get(d) or []) >= 70 for n in names)][-60:]
    by_day = collect(cfg, days, daily, intraday, calendar, log)
    ledger = walk(by_day, warmup, threshold, l2)
    budget = fno.get('risk_per_trade') or cfg.get('risk_per_trade') or 0
    result = evaluate(ledger, budget)
    if shuffles and result['model'].get('trades'):
        log('shuffled-label control: %d runs' % shuffles)
        result['control'] = shuffled_control(by_day, warmup, threshold, l2, result['model']['avg_r'], shuffles)
    all_trades = [t for _, ts in by_day for t in ts]
    ys = [t['win'] for t in all_trades]
    final = Logit(l2).fit([t['features'] for t in all_trades], ys) if all_trades and 0 < sum(ys) < len(ys) else None
    return dict(sessions=len(days), first=days[0].isoformat() if days else None,
                last=days[-1].isoformat() if days else None, warmup=warmup, threshold=threshold, l2=l2,
                budget=budget, result=result, ledger=ledger,
                model=dict(features=list(FEATURES), weights=final.weights(), w=final.w, mu=final.mu, sd=final.sd,
                           trained_on=len(all_trades), through=days[-1].isoformat()) if final else None)


# --------------------------------------------------------------------------- memory

KEEP = ('date', 'index', 'time', 'side', 'grade', 'score', 'blocked', 'structure', 'contract', 'entry', 'stop',
        'target1', 'target2', 'exit', 't1', 'spot_r', 'r', 'win', 'rule_take', 'features', 'live')


def load_dataset(folder):
    path = Path(folder) / 'dataset.jsonl'
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write(path, text):
    """Atomic write: the dataset is the desk's memory and must never be left half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    os.replace(tmp, path)


def merge(rows, new):
    """Rows keyed by (date, index); a new row replaces an old one for the same session and index."""
    by = {(r['date'], r['index']): r for r in rows}
    for r in new:
        by[(r['date'], r['index'])] = {k: r.get(k) for k in KEEP if k in r}
    return sorted(by.values(), key=lambda r: (r['date'], r['time']))


def save_dataset(folder, rows):
    _write(Path(folder) / 'dataset.jsonl', ''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))


PROOF_RUNS = 100        # shuffled-label runs behind the probation check
PROOF_MIN_TRADES = 30   # scored walk-forward trades before the model can come off probation
PROOF_P = 0.05          # at most this share of shuffled runs may do as well as the model


def evidence(rows, warmup=25, threshold=0.55, l2=1.0, runs=None):
    """Has the model earned its say? Walk-forward over memory plus the shuffled-label control.

    proven: enough scored trades, its TAKEs beat every breakout, and at most PROOF_P of shuffled runs match it."""
    by = {}
    for r in rows:
        by.setdefault(r['date'], []).append(r)
    by_day = [(dt.date.fromisoformat(d), sorted(ts, key=lambda t: t['time'])) for d, ts in sorted(by.items())]
    res = evaluate(walk(by_day, warmup, threshold, l2), 0)
    every, model = res['every'], res['model']
    out = dict(scored=every.get('trades', 0), model_trades=model.get('trades', 0), model_avg_r=model.get('avg_r'),
               every_avg_r=every.get('avg_r'), p_value=None, proven=False)
    if out['scored'] >= PROOF_MIN_TRADES and model.get('trades'):
        ctl = shuffled_control(by_day, warmup, threshold, l2, model['avg_r'], runs or PROOF_RUNS)
        out['p_value'] = ctl['p_value']
        out['proven'] = model['avg_r'] > every['avg_r'] and ctl['p_value'] <= PROOF_P
    return out


def train(folder, rows, l2=1.0, now=None):
    """Fit on every row in memory, test whether it has earned its say, save model.json; None when one class is missing."""
    ys = [r['win'] for r in rows]
    if not rows or not 0 < sum(ys) < len(ys):
        return None
    m = Logit(l2).fit([r['features'] for r in rows], ys)
    proof = evidence(rows, l2=l2)
    doc = dict(features=list(FEATURES), w=m.w, mu=m.mu, sd=m.sd, l2=l2, trained_on=len(rows), wins=sum(ys),
               first=rows[0]['date'], through=rows[-1]['date'], weights=m.weights(), proven=proof['proven'],
               evidence=proof, updated_at=(now or dt.datetime.now(IST)).isoformat(timespec='seconds'))
    _write(Path(folder) / 'model.json', json.dumps(doc, indent=2))
    return doc


def forward_record(rows):
    """How the live desk's model decisions turned out (rows that carry a live P(win))."""
    live = [r for r in rows if (r.get('live') or {}).get('p_win') is not None and r['live'].get('decided_by') == 'model']
    taken = [r for r in live if r['live'].get('taken')]
    skipped = [r for r in live if not r['live'].get('taken')]
    s = lambda ts: dict(trades=len(ts), avg_r=round(sum(t['r'] for t in ts) / len(ts), 3) if ts else None,
                        win_rate=round(sum(t['win'] for t in ts) / len(ts) * 100, 1) if ts else None)
    return dict(taken=s(taken), skipped=s(skipped))


def seed(cfg, report=None, l2=1.0, log=lambda m: None):
    """Put the 60-day walk's breakouts into memory (from learn-latest.json, else a fresh run) and train."""
    fno = cfg['index_fno']
    folder = F.learning_dir(fno)
    latest = Path(fno['state_dir']) / 'learn-latest.json'
    if report is None and latest.is_file():
        report = json.loads(latest.read_text())
        log('seeding from %s' % latest)
    if report is None:
        report = run(cfg, dt.datetime.now(IST), shuffles=0, log=log)
    rows = merge(load_dataset(folder), report['ledger'])
    save_dataset(folder, rows)
    return rows, train(folder, rows, l2)


def live_info(fno, day):
    """What the live desk did on `day`, per index, from its state file."""
    doc = F.load_state(fno['state_dir'], day)
    out = {}
    for name, rec in (doc.get('indices') or {}).items():
        sig = rec.get('signal') or {}
        if not sig:
            continue
        out[name] = dict(status=rec.get('status'), p_win=sig.get('p_win'), decided_by=sig.get('decided_by'),
                         taken=rec.get('status') in ('OPEN', 'CLOSED'), pnl=rec.get('pnl'), r=rec.get('r'),
                         time=sig.get('time'), side=sig.get('side'))
    return out, doc.get('context')


def session_rows(cfg, day, now, intraday=None):
    """Recorder-shaped candles for `day`: Yahoo five-minute candles, else the recorder's own."""
    fno = cfg['index_fno']
    names = list(fno['indices']) + [fno['vix_index']]
    if intraday is None:
        try:
            intraday = {n: B.fetch_intraday(B.YAHOO[n], [day], now) for n in names}
        except (ValueError, OSError):
            intraday = None
    if intraday and all(len(intraday[n].get(day) or []) >= 70 for n in names):
        return B.rows_for(intraday, day), 'Yahoo'
    from .recorder import load_candles
    rows = load_candles(cfg['recorder']['state_dir'], day, usable_only=True)
    if not rows:
        raise ValueError('no five-minute candles for %s (Yahoo and the recorder are both empty)' % day)
    return rows, 'recorder'


def update(cfg, day, now, calendar=None, intraday=None, daily=None, l2=1.0):
    """Learn from one finished session: replay it, store every breakout with its outcome, retrain."""
    calendar = calendar or DEFAULT_CALENDAR
    fno = cfg['index_fno']
    ready = dt.datetime.combine(day, P.hhmm(fno['square_off']), IST) + dt.timedelta(minutes=25)
    if now < ready:
        raise ValueError('the %s session is not finished: learn after %s IST' % (day, ready.strftime('%H:%M')))
    folder = F.learning_dir(fno)
    live, contexts = live_info(fno, day)
    if not contexts:
        contexts = F.contexts_for(daily if daily is not None else F.fetch_daily_bars(fno, months=12), day)
    rows, source = session_rows(cfg, day, now, intraday)
    (_, trades), = collect(cfg, [day], {day: contexts}, {day: rows}, calendar)
    before = load_dataset(folder)
    model_before = F.load_model(dict(fno, learning='decide'))
    for t in trades:
        info = live.get(t['index'])
        if info and info.get('side') == t['side']:
            t['live'] = info
        elif model_before:
            t['live'] = dict(p_win=round(F.model_p(model_before, t['features']), 3), decided_by='shadow',
                             taken=False, status=(info or {}).get('status') or 'NOT_LIVE')
    rows_after = merge(before, trades)
    save_dataset(folder, rows_after)
    model = train(folder, rows_after, l2, now)
    _write(folder / ('learned-%s' % day.isoformat()), now.isoformat())
    taken = [v for v in live.values() if v.get('status') == 'CLOSED']
    desk = F.discipline(fno, day + dt.timedelta(days=1))
    budget = (fno.get('risk_per_trade') or cfg.get('risk_per_trade') or 0) * desk['mult']
    return dict(day=day.isoformat(), source=source, trades=trades, memory=len(rows_after), added=len(rows_after) - len(before),
                model=model, forward=forward_record(rows_after),
                journal=dict(pnl=sum(v.get('pnl') or 0 for v in taken), r=round(sum(v.get('r') or 0 for v in taken), 2),
                             trades=len(taken), desk=desk, next_budget=budget))


def probation_reason(ev):
    if ev['scored'] < PROOF_MIN_TRADES:
        return '%d of %d test trades' % (ev['scored'], PROOF_MIN_TRADES)
    if ev.get('p_value') is None:
        return 'it took no trades in the test'
    return '%.0f%% of shuffled runs did as well, needs ≤%.0f%%' % (ev['p_value'] * 100, PROOF_P * 100)


def update_text(res, fno):
    short = lambda n: fno['contracts'][n]['short']
    lines = ['🧠 <b>Options desk learned from %s</b>' % dt.date.fromisoformat(res['day']).strftime('%a %d %b')]
    if not res['trades']:
        lines.append('No breakout today: nothing new to learn.')
    for t in res['trades']:
        live = t.get('live') or {}
        p = live.get('p_win')
        call = ''
        if p is not None:
            call = ' · model %.2f → %s' % (p, 'TAKEN' if live.get('taken') else 'skipped' if live.get('decided_by') != 'shadow'
                                           else 'not live')
        lines.append('%s %s %s%s · %s %+.2fR %s' % (
            short(t['index']), t['side'], F._t(t['time']).strftime('%H:%M'), call, t['exit'].replace('_', ' ').lower(),
            t['r'], '✅' if t['win'] else '❌'))
    m = res['model']
    rows_wins = (m or {}).get('wins')
    lines.append('Memory: %d breakouts%s · model %s' % (
        res['memory'], ' (%.0f%% wins)' % (rows_wins / m['trained_on'] * 100) if m else '',
        'not trained yet (needs wins and losses)' if not m else 'retrained, PROVEN: it decides' if m.get('proven') else
        'retrained, on probation (%s): the grade rules decide' % probation_reason(m['evidence'])))
    f = res['forward']
    if f['taken']['trades'] or f['skipped']['trades']:
        fmt = lambda s: '%d, %+.2fR avg' % (s['trades'], s['avg_r']) if s['trades'] else '0'
        lines.append('Live model record: taken %s · skipped %s' % (fmt(f['taken']), fmt(f['skipped'])))
    if m:
        lines.append('Leaning on: ' + ', '.join('%s %+.2f' % tuple(kv) for kv in m['weights'][:3]))
    j = res.get('journal')
    if j:
        d = j['desk']
        today = 'today %s (%+.2fR) on %d trade%s' % (P.inr(j['pnl'], 0), j['r'], j['trades'], '' if j['trades'] == 1 else 's') \
            if j['trades'] else 'no trade today'
        tomorrow = d['block'] if d['block'] else 'next session risk %s per trade%s' % (
            P.inr(j['next_budget'], 0), ' (' + '; '.join(d['notes']) + ')' if d['notes'] else '')
        lines.append('📓 Journal: %s · week %+.1fR · equity %+.1fR over %d trades (%.1fR off the peak)' % (
            today, d['week_r'], d['equity_r'], d['trades'], d['drawdown_r']))
        lines.append('➡️ %s' % tomorrow)
    lines.append('<i>Paper research, not advice.</i>')
    return '\n'.join(lines)


def memory_report(cfg, warmup=25, threshold=0.55, l2=1.0, shuffles=100):
    """The walk-forward evaluation over everything in memory (not limited to Yahoo's 60 days)."""
    rows = load_dataset(F.learning_dir(cfg['index_fno']))
    by = {}
    for r in rows:
        by.setdefault(r['date'], []).append(r)
    by_day = [(dt.date.fromisoformat(d), sorted(ts, key=lambda t: t['time'])) for d, ts in sorted(by.items())]
    ledger = walk(by_day, warmup, threshold, l2)
    budget = cfg['index_fno'].get('risk_per_trade') or cfg.get('risk_per_trade') or 0
    result = evaluate(ledger, budget)
    if shuffles and result['model'].get('trades'):
        result['control'] = shuffled_control(by_day, warmup, threshold, l2, result['model']['avg_r'], shuffles)
    result['forward'] = forward_record(rows)
    model = json.loads((F.learning_dir(cfg['index_fno']) / 'model.json').read_text()) \
        if (F.learning_dir(cfg['index_fno']) / 'model.json').is_file() else None
    return dict(sessions=len(by_day), first=by_day[0][0].isoformat() if by_day else None,
                last=by_day[-1][0].isoformat() if by_day else None, warmup=warmup, threshold=threshold, l2=l2,
                budget=budget, result=result, ledger=ledger, model=model)


# --------------------------------------------------------------------------- report

def _r(x, spec='%+.2f'):
    return '—' if x is None else spec % x


def format_report(rep, trades=True):
    res = rep['result']
    lines = ['Walk-forward learning, paper trades: %d sessions, %s to %s' % (rep['sessions'], rep['first'], rep['last']),
             'Model: logistic, trained only on earlier days; TAKE at P(win) ≥ %.2f after %d warm-up trades.' % (
                 rep['threshold'], rep['warmup']), '']
    if trades:
        lines.append('%-10s %-5s %-30s %-4s %8s %8s %8s  %5s %-6s %-10s %6s' % (
            'date', 'time', 'contract', 'side', 'entry', 'stop', 'T2', 'P(win)', 'call', 'exit', 'R'))
        for t in rep['ledger']:
            lines.append('%-10s %-5s %-30s %-4s %8.1f %8.1f %8.1f  %5s %-6s %-10s %+6.2f' % (
                t['date'], F._t(t['time']).strftime('%H:%M'), t['contract'][:30], t['side'], t['entry'], t['stop'],
                t['target2'], '—' if t['p_win'] is None else '%.2f' % t['p_win'], t['decision'], t['exit'], t['r']))
        lines.append('')
    lines.append('After warm-up (same days for each):')
    for key, title in (('every', 'every breakout'), ('rules', 'current grade rule'), ('model', 'model TAKE'),
                       ('model_skipped', 'model SKIP')):
        s = res[key]
        if not s.get('trades'):
            lines.append('  %-20s no trades' % title)
            continue
        lines.append('  %-20s %3d trades · win %3.0f%% · %sR/trade (±%s) · total %sR%s' % (
            title, s['trades'], s['win_rate'], _r(s['avg_r']), _r(s['se_r'], '%.2f'), _r(s['total_r'], '%+.1f'),
            ' · %s at %s risk/trade' % (P.inr(s['pnl'], 0), P.inr(rep['budget'], 0)) if rep['budget'] else ''))
    pr = res.get('prediction')
    if pr:
        lines.append('  win/loss calls right %.0f%% (no-skill running win rate: %.0f%%) · Brier %.3f vs %.3f no-skill' % (
            pr['accuracy'], pr['base_accuracy'], pr['brier'], pr['base_brier']))
    c = res.get('control')
    if c:
        lines.append('  shuffled labels (%d runs): median %sR/trade, 90th pct %sR · share at or above the model: %.0f%%' % (
            c['runs'], _r(c['median_avg_r']), _r(c['p90_avg_r']), c['p_value'] * 100))
    fw = res.get('forward')
    if fw and (fw['taken']['trades'] or fw['skipped']['trades']):
        lines.append('  live model decisions: taken %d (%sR avg) · skipped %d (%sR avg)' % (
            fw['taken']['trades'], _r(fw['taken']['avg_r']), fw['skipped']['trades'], _r(fw['skipped']['avg_r'])))
    if rep.get('model'):
        lines.append('')
        lines.append('What the final model learned (all %d trades; + raises P(win), standardised):' % rep['model']['trained_on'])
        lines.append('  ' + ' · '.join('%s %+.2f' % tuple(kv) for kv in rep['model']['weights'][:8]))
    lines += ['', 'Paper research. ± is one standard error; a shuffled-label share above ~5% means the filter is not',
              'distinguishable from luck. R is modelled option P&L (India VIX premiums) / risk, after charges.']
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Walk-forward learning over the last ~60 sessions (paper trades).')
    ap.add_argument('--config', help='picks JSON')
    ap.add_argument('--warmup', type=int, default=25, help='trades before the model starts deciding (default 25)')
    ap.add_argument('--threshold', type=float, default=0.55, help='TAKE at P(win) at or above this (default 0.55)')
    ap.add_argument('--l2', type=float, default=1.0, help='regularisation strength (default 1.0)')
    ap.add_argument('--shuffles', type=int, default=100, help='shuffled-label control runs (0 = off)')
    ap.add_argument('--summary', action='store_true', help='leave out the trade-by-trade ledger')
    ap.add_argument('--json', action='store_true')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--seed', action='store_true', help='store the 60-day walk in memory and train the model')
    mode.add_argument('--update', action='store_true', help='learn from a finished session (default: today)')
    mode.add_argument('--report', action='store_true', help='walk-forward evaluation over everything in memory')
    ap.add_argument('--day', help='with --update: the session date (YYYY-MM-DD)')
    ap.add_argument('--env-file', default='var/telegram.env', help='KEY=VALUE file with the Telegram settings')
    ap.add_argument('--dry-run', action='store_true', help='with --update: print the note instead of sending it')
    a = ap.parse_args(argv)
    try:
        if not 5 <= a.warmup <= 200 or not 0.3 <= a.threshold <= 0.9 or not 0 < a.l2 <= 100 or not 0 <= a.shuffles <= 1000:
            raise ValueError('--warmup 5-200, --threshold 0.3-0.9, --l2 above 0 to 100, --shuffles 0-1000')
        cfg = P.load_config(a.config)
        calendar = Calendar.load(cfg['calendar']) if cfg['calendar'] else DEFAULT_CALENDAR
        log = lambda m: print(m, file=sys.stderr)
        if a.seed:
            rows, model = seed(cfg, l2=a.l2, log=log)
            print(json.dumps(dict(status='SEEDED', memory=len(rows), model_trained_on=(model or {}).get('trained_on'),
                                  folder=str(F.learning_dir(cfg['index_fno'])))))
            return 0
        if a.update:
            now = dt.datetime.now(IST)
            day = dt.date.fromisoformat(a.day) if a.day else now.date()
            if not calendar.hours(day, 'NSE'):
                print(json.dumps({'status': 'SKIPPED', 'reason': 'not an NSE trading day'}))
                return 0
            res = update(cfg, day, now, calendar, l2=a.l2)
            text = update_text(res, cfg['index_fno'])
            if a.dry_run:
                print(text)
                return 0
            env = dict(P.read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith('TELEGRAM_')})
            token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
            if token and chat:
                try:
                    P.send_telegram(token, chat, text)
                except (ValueError, OSError) as exc:
                    log('learning note not sent: %s' % exc)      # the learning itself is saved
            print(json.dumps(dict(status='LEARNED', day=res['day'], source=res['source'], added=res['added'],
                                  memory=res['memory'])))
            return 0
        if a.report:
            rep = memory_report(cfg, a.warmup, a.threshold, a.l2, a.shuffles)
            print(json.dumps(rep, indent=2, default=str) if a.json else format_report(rep, trades=not a.summary))
            return 0
        rep = run(cfg, dt.datetime.now(IST), calendar, warmup=a.warmup, threshold=a.threshold, l2=a.l2,
                  shuffles=a.shuffles, log=lambda m: print(m, file=sys.stderr))
        state = Path(cfg['index_fno']['state_dir'])
        state.mkdir(parents=True, exist_ok=True)
        (state / 'learn-latest.json').write_text(json.dumps(rep, indent=2, default=str))
        print(json.dumps(rep, indent=2, default=str) if a.json else format_report(rep, trades=not a.summary))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
