"""Gold strategy lab: champion/challenger research and forward paper trading for MCX gold.

Layers, each usable on its own:

1. Market      one daily INR/gram proxy series (COMEX x USD/INR x basis, from india.gold),
               plus the dollar index and US 10-year yield.
2. Strategies  each is a pure, causal function: history -> target exposure per session in
               [0, 1]. The same function drives the backtest, the walk-forward test and the
               live ledger, so live and backtest decisions cannot silently diverge.
               tests/test_gold_lab.py checks every registered strategy for look-ahead.
3. Engine      turns exposures into whole GOLDPETAL lots with volatility targeting, trades
               at the next session's open, and charges MCX costs, monthly rolls and the
               futures carry.
4. Evaluation  full-history metrics, a yearly walk-forward (parameters picked on the
               previous train_years only), and block-bootstrap comparisons.
5. Ledger      an append-only, hash-chained decision log written before outcomes exist.
               Forward P&L is recomputed from it on every run; nothing that could be
               edited later is stored.
6. Review      pre-registered promotion rules (PROMOTION) decide whether a challenger
               replaces the champion. The review also flags implementation drift (logged
               decisions that differ from a replay) and writes the brief that the monthly
               self-improvement run reads (automation/gold-lab-improve.md).

Research and paper trading only: nothing here places orders.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
import random
import sys
from pathlib import Path

from . import gold as G
from . import alert_switches as S
from . import picks as P
from .data import IST

UTC = dt.timezone.utc
YEAR = 252
DEFAULT_LAB_CONFIG = 'config/gold_lab.json'
EVAL_START = dt.date(2006, 1, 1)

LAB_DEFAULTS = {
    'state_dir': None,            # None = a gold_lab folder next to gold.state_dir (var/state/gold_lab)
    'capital': None,              # INR; None = gold.capital from picks.json
    'vol_target_pct': 15.0,       # yearly volatility the position is scaled to
    'vol_lookback': 40,           # sessions of realised volatility
    'max_leverage': 1.0,          # notional / equity cap; 1.0 = no leverage
    'rebalance_band': 0.25,       # change lots only when the target moves this fraction (or the signal flips)
    'carry_pct': 6.5,             # yearly cost of holding MCX futures instead of spot (INR rates), on notional
    'cash_rate_pct': 0.0,         # yearly interest on the account's cash; set ~6.5 if margin is a pledged liquid fund
    'roll_monthly': True,         # GOLDPETAL expires monthly: a held position pays a round trip each month
    'train_years': 5,             # walk-forward training window
    'first_test_year': 2011,
    'bootstrap_samples': 400,
    'telegram': True,             # champion changes, promotions and the weekly scoreboard
    'review_weekday': 0,          # the daily run also reviews (and may promote) on this weekday; 0 = Monday
    'initial_champion': 'swing_v1',   # the rules the morning alert already sends, so lab and alert start in agreement
}

# Pre-registered promotion rules. They are part of the experiment: the monthly
# self-improvement run must not change them (automation/gold-lab-improve.md).
PROMOTION = {
    'min_forward_sessions': 40,   # forward paper sessions a challenger needs before it can be promoted
    'sharpe_margin': 0.10,        # test-year Sharpe above the champion's
    'min_bootstrap_prob': 0.80,   # block bootstrap P(challenger's Sharpe > champion's) over the test years
    'max_decision_mismatch': 0.05,  # share of logged decisions a replay may disagree with (look-ahead / data drift)
    'forward_tolerance_sd': 1.0,  # forward return may trail the champion's by at most this many s.d. of the difference
}


def settings(raw=None, gold_cfg=None):
    cfg = dict(LAB_DEFAULTS)
    raw = raw or {}
    unknown = set(raw) - set(LAB_DEFAULTS) - {'_comment'}
    if unknown:
        raise ValueError('Unknown gold lab config keys: ' + ', '.join(sorted(unknown)))
    cfg.update({k: v for k, v in raw.items() if k != '_comment'})
    gold_cfg = gold_cfg or G.settings(None)
    if cfg['capital'] is None:
        cfg['capital'] = gold_cfg['capital']
    if cfg['state_dir'] is None:
        cfg['state_dir'] = str(Path(gold_cfg['state_dir']).parent / 'gold_lab')
    for key, low, high in (('capital', 1000, 1e10), ('vol_target_pct', 1, 100), ('max_leverage', 0.1, 10),
                           ('rebalance_band', 0, 1), ('carry_pct', 0, 30), ('cash_rate_pct', 0, 30)):
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
            raise ValueError('gold lab %s must be a number from %s to %s' % (key, low, high))
    for key, low, high in (('vol_lookback', 5, 250), ('train_years', 1, 15), ('first_test_year', 2008, 2100),
                           ('bootstrap_samples', 50, 10000), ('review_weekday', 0, 4)):
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise ValueError('gold lab %s must be a whole number from %d to %d' % (key, low, high))
    if not isinstance(cfg['telegram'], bool) or not isinstance(cfg['roll_monthly'], bool):
        raise ValueError('gold lab telegram and roll_monthly must be true or false')
    if cfg['initial_champion'] not in REGISTRY:
        raise ValueError('gold lab initial_champion must be one of: ' + ', '.join(REGISTRY))
    return cfg


def load_settings(path, gold_cfg):
    """Lab settings from an optional JSON file (default config/gold_lab.json); absent = defaults."""
    p = Path(path) if path else None
    raw = json.loads(p.read_text()) if p and p.is_file() else None
    return settings(raw, gold_cfg)


# --------------------------------------------------------------------------- market

class Market:
    """Daily proxy bars plus cached causal indicator series (value i uses sessions <= i)."""

    def __init__(self, bars, refs=None, dxy=None, us10y=None, basis=1.0):
        self.bars, self.refs, self.basis = bars, refs, basis
        self.dates = [b.time.date() for b in bars]
        self.o = [b.open for b in bars]
        self.h = [b.high for b in bars]
        self.l = [b.low for b in bars]
        self.c = [b.close for b in bars]
        self.dxy_lookup = G.Lookup(dxy) if dxy else None
        self.us10y_lookup = G.Lookup(us10y) if us10y else None
        self.dxy_ret63 = None
        if dxy:
            lk = G.Lookup(dxy, back=63)
            self.dxy_ret63 = [(lambda x: x['change_pct'] / 100 if x else None)(lk.at(d)) for d in self.dates]
        self._cache = {}

    def __len__(self):
        return len(self.c)

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def ema(self, n):
        def calc():
            k, out, e = 2 / (n + 1), [], None
            for x in self.c:
                e = x if e is None else e + k * (x - e)
                out.append(e)
            return out
        return self._memo(('ema', n), calc)

    def vol(self, n):
        """Annualised close-to-close volatility over the last n sessions (None during warm-up)."""
        def calc():
            lr = [0.0] + [math.log(self.c[i] / self.c[i - 1]) for i in range(1, len(self.c))]
            out, s, s2 = [], 0.0, 0.0
            for i, x in enumerate(lr):
                s, s2 = s + x, s2 + x * x
                if i > n:
                    y = lr[i - n]
                    s, s2 = s - y, s2 - y * y
                if i < n:
                    out.append(None)
                    continue
                var = max(s2 / n - (s / n) ** 2, 0.0) * n / (n - 1)
                out.append(math.sqrt(var * YEAR))
            return out
        return self._memo(('vol', n), calc)

    def rsi(self, n):
        def calc():
            out, up, dn = [None], None, None
            for i in range(1, len(self.c)):
                d = self.c[i] - self.c[i - 1]
                g, l = max(d, 0.0), max(-d, 0.0)
                if up is None:
                    up, dn = g, l
                else:
                    up, dn = up + (g - up) / n, dn + (l - dn) / n
                out.append(None if i < n else (100.0 if dn == 0 else 100 - 100 / (1 + up / dn)))
            return out
        return self._memo(('rsi', n), calc)

    def prior_max(self, n):
        """Highest close of the n sessions before i (excludes i)."""
        return self._memo(('pmax', n), lambda: [None if i < n else max(self.c[i - n:i]) for i in range(len(self.c))])

    def prior_min(self, n):
        return self._memo(('pmin', n), lambda: [None if i < n else min(self.c[i - n:i]) for i in range(len(self.c))])

    def index_of(self, day):
        """Index of the last session on or before ``day`` (-1 if none)."""
        import bisect
        return bisect.bisect_right(self.dates, day) - 1


def load_market(now, basis, fetch=None, log=lambda msg: None):
    fetch = fetch or G.fetch_daily
    start = dt.date(2000, 1, 1)
    gold = G.completed(fetch(G.GOLD, start, now), now)
    fx = fetch(G.FX, start, now)
    extra = {}
    for key, sym in (('dxy', G.DXY), ('us10y', G.US10Y)):
        try:
            extra[key] = fetch(sym, start, now)
        except ValueError as exc:
            log('gold lab: %s unavailable (%s); macro strategies read it as missing' % (sym, exc))
            extra[key] = None
    bars, refs = G.proxy(gold, fx, basis)
    if len(bars) < 300:
        raise ValueError('gold lab needs at least 300 proxy sessions, got %d' % len(bars))
    return Market(bars, refs, extra['dxy'], extra['us10y'], basis)


# --------------------------------------------------------------------------- strategies
# Each function returns one target exposure per session in [0, 1] (None = warm-up,
# read as flat). Exposure i may only use data up to and including session i.

def s_buy_hold(m):
    return [1.0] * len(m)


def s_tsmom(m, lookback):
    """Time-series momentum: long while the close is above the close ``lookback`` sessions ago."""
    c = m.c
    return [None if i < lookback else (1.0 if c[i] > c[i - lookback] else 0.0) for i in range(len(c))]


def s_tsmom_blend(m, lookbacks=(21, 63, 126, 252)):
    """Share of the lookbacks with positive momentum: a graded trend signal."""
    sigs = [s_tsmom(m, n) for n in lookbacks]
    return [None if any(s[i] is None for s in sigs) else sum(s[i] for s in sigs) / len(sigs) for i in range(len(m))]


def s_ma_trend(m, fast, slow):
    ef, es = m.ema(fast), m.ema(slow)
    return [None if i < slow else (1.0 if ef[i] > es[i] else 0.0) for i in range(len(m))]


def s_donchian(m, entry, exit=None):
    """Long on a close above the prior ``entry``-session high; out on a close below the prior ``exit``-session low."""
    exit = exit or max(entry // 2, 2)
    hi, lo, c = m.prior_max(entry), m.prior_min(exit), m.c
    out, on = [], False
    for i in range(len(c)):
        if hi[i] is None or lo[i] is None:
            out.append(None)
            continue
        if not on and c[i] > hi[i]:
            on = True
        elif on and c[i] < lo[i]:
            on = False
        out.append(1.0 if on else 0.0)
    return out


def s_dip_buy(m, threshold, max_days=10):
    """Short-term mean reversion inside an uptrend: RSI(2) below ``threshold`` while above EMA200;
    out on a close above EMA5 or after ``max_days`` sessions."""
    r2, e5, e200, c = m.rsi(2), m.ema(5), m.ema(200), m.c
    out, held = [], None
    for i in range(len(c)):
        if i < 200 or r2[i] is None:
            out.append(None)
            continue
        if held is None and c[i] > e200[i] and r2[i] < threshold:
            held = 0
        elif held is not None:
            held += 1
            if c[i] > e5[i] or held >= max_days:
                held = None
        out.append(1.0 if held is not None else 0.0)
    return out


def s_macro_tsmom(m, dxy_limit):
    """tsmom_blend at half size while the dollar index is up more than ``dxy_limit`` over 63 sessions."""
    base = s_tsmom_blend(m)
    d = m.dxy_ret63 or [None] * len(m)
    return [None if b is None else (b * 0.5 if d[i] is not None and d[i] > dxy_limit else b)
            for i, b in enumerate(base)]


def s_ensemble_trend(m):
    """Equal-weight blend of three trend models: tsmom_blend, EMA 50/200 and Donchian 55/27."""
    parts = [s_tsmom_blend(m), s_ma_trend(m, 50, 200), s_donchian(m, 55)]
    return [None if any(p[i] is None for p in parts) else sum(p[i] for p in parts) / 3 for i in range(len(m))]


def s_swing_v1(m):
    """The live morning-alert rules (india.gold): in from the fill to the exit, half after T1.

    Approximation: the lab trades at the next open, while the alert's orders fill
    intraday at the trigger and stop. docs/GOLD_ALERTS.md has its exact R-based backtest."""
    cfg = G.settings({'news': 'off', 'risk_per_trade': 1e7, 'capital': 1e10, 'max_lots': 100000})
    n = len(m)
    out = [None] * min(G.MIN_BARS, n) + [0.0] * max(n - G.MIN_BARS, 0)
    if n <= G.MIN_BARS:
        return out
    s = G.series(m.bars)
    busy = -1
    for i in range(G.MIN_BARS - 1, n - 1):
        if i <= busy:
            continue
        f = G.features(m.bars, s, i, m.refs, m.dxy_lookup, m.us10y_lookup)
        cand, _ = G.evaluate(f, cfg)
        if not cand or cand['side'] != 'BUY' or cand['score'] < cfg['min_score']:
            continue
        plan = G.build_plan(f, cand, cfg, m.basis)
        if plan is None:
            continue
        sim = G.simulate(plan, m.bars[i + 1:], cfg)
        if sim['status'] == 'PENDING':
            break                     # the entry order is still live at the end of the data
        busy = i + 1 + sim['exit_index'] if sim['exit_index'] is not None else n
        if sim['status'] not in ('OPEN', 'CLOSED'):
            continue
        fill = m.index_of(dt.date.fromisoformat(sim['fill_date']))
        stop = busy if sim['status'] == 'CLOSED' else n
        t1 = next((e['date'] for e in sim['events'] if e['kind'] == 'TARGET1'), None)
        for k in range(fill, min(stop, n)):
            out[k] = 0.5 if t1 and m.dates[k].isoformat() >= t1 else 1.0
    return out


def s_blend(m, parts, mode='avg'):
    """Combine other registered strategies' exposures: 'avg' (mean) or 'sum' (capped at 1)."""
    series = [REGISTRY[p].exposures(m) for p in parts]
    out = []
    for i in range(len(m)):
        xs = [s[i] for s in series]
        if any(x is None for x in xs):
            out.append(None)
        else:
            out.append(sum(xs) / len(xs) if mode == 'avg' else min(1.0, sum(xs)))
    return out


class Strategy:
    """A registered strategy. ``posthoc`` marks one designed after looking at the
    research results: its backtest is in-sample by construction, so only its
    forward paper record is clean evidence."""

    def __init__(self, name, family, fn, params=None, version=1, added='2026-11-19', vol_target=True, note='',
                 posthoc=False):
        self.name, self.family, self.fn, self.params = name, family, fn, params or {}
        self.version, self.added, self.vol_target, self.note = version, added, vol_target, note
        self.posthoc = posthoc
        self.source = 'india/gold_lab.py'

    def exposures(self, m):
        out = self.fn(m, **self.params)
        if len(out) != len(m):
            raise ValueError('%s returned %d exposures for %d sessions' % (self.name, len(out), len(m)))
        return out


def _registry():
    r = [
        Strategy('buy_hold', 'benchmark', s_buy_hold, vol_target=False, note='Hold 1x notional; the bar to beat'),
        Strategy('buy_hold_vt', 'benchmark', s_buy_hold, note='Always long, volatility-targeted'),
        Strategy('tsmom_blend', 'tsmom_blend', s_tsmom_blend, note='Graded 1/3/6/12-month momentum'),
        Strategy('ensemble_trend', 'ensemble', s_ensemble_trend, note='tsmom_blend + EMA 50/200 + Donchian 55'),
        Strategy('swing_v1', 'swing', s_swing_v1, note='The live morning-alert rules (approximate fills)'),
    ]
    for n in (63, 126, 252):
        r.append(Strategy('tsmom_%d' % n, 'tsmom', s_tsmom, {'lookback': n}))
    for f, s in ((10, 50), (20, 100), (50, 200)):
        r.append(Strategy('ma_%d_%d' % (f, s), 'ma_trend', s_ma_trend, {'fast': f, 'slow': s}))
    for n in (20, 55, 100):
        r.append(Strategy('donchian_%d' % n, 'donchian', s_donchian, {'entry': n}))
    for t in (5, 10, 20):
        r.append(Strategy('dip_rsi2_%d' % t, 'dip_buy', s_dip_buy, {'threshold': t}))
    for lim in (0.0, 0.02):
        r.append(Strategy('macro_tsmom_%g' % (lim * 100), 'macro_tsmom', s_macro_tsmom, {'dxy_limit': lim}))
    return {s.name: s for s in r}


REGISTRY = _registry()
CORE_STRATEGIES = frozenset(REGISTRY)
PLUGIN_DIR = Path(__file__).resolve().parent / 'gold_lab_challengers'
PLUGIN_ERRORS = {}


def load_plugins(registry, directory=None, errors=None):
    """Add challengers from ``gold_lab_challengers/c_*.py``.

    Each file defines ``strategies(lab)``, which receives this module and returns
    a list of Strategy objects. This is the only interface an AI improvement run
    may use (india/gold_lab_improve.py enforces it), so core code, PROMOTION and
    the ledger never need to change. A broken file is skipped and reported in
    ``errors``, never allowed to stop the daily run. Plugin strategies must be
    post-hoc and may not reuse an existing name."""
    import importlib.util
    errors = PLUGIN_ERRORS if errors is None else errors
    directory = Path(directory or PLUGIN_DIR)
    added = {}
    for path in sorted(directory.glob('c_*.py')):
        try:
            spec = importlib.util.spec_from_file_location('india.gold_lab_challengers.' + path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            items = list(mod.strategies(sys.modules[__name__]))
            for st in items:
                if not isinstance(st, Strategy):
                    raise ValueError('strategies() must return Strategy objects')
                if st.name in registry or st.name in added:
                    raise ValueError('strategy name %r is already registered' % st.name)
                if not st.posthoc:
                    raise ValueError('%s: plugin strategies must set posthoc=True' % st.name)
                if not isinstance(st.name, str) or not st.name.replace('_', '').isalnum():
                    raise ValueError('strategy names use letters, digits and underscores')
                st.source = path.name
            added.update({st.name: st for st in items})
        except Exception as exc:          # a bad plugin must never stop the daily run
            errors[path.name] = '%s: %s' % (type(exc).__name__, exc)
    registry.update(added)
    return added


load_plugins(REGISTRY)


# --------------------------------------------------------------------------- engine

def side_cost(lots, price, gcfg, buy):
    """Estimated INR cost of one order for ``lots`` (fees, GST, stamp or CTT, slippage)."""
    if lots <= 0:
        return 0.0
    c, grams = gcfg['costs'], lots * gcfg['lot_grams']
    notional = grams * price
    fees = c['brokerage_per_order'] + notional * (c['exchange_rate'] + c['sebi'])
    tax = notional * (c['stamp_buy'] if buy else c['ctt_sell'])
    return fees * (1 + c['gst']) + tax + grams * c['slippage_ticks'] * gcfg['tick']


def target_lots(raw, vol, equity, price, lots_now, lab, gcfg, vol_target=True):
    """Whole lots for an exposure, volatility-scaled, with a no-trade band against churn."""
    raw = raw or 0.0
    if raw <= 0:
        return 0
    scale = lab['max_leverage']
    if vol_target:
        scale = min(lab['max_leverage'], lab['vol_target_pct'] / 100 / vol) if vol else 0.0
    want = int(raw * scale * equity // (price * gcfg['lot_grams']))
    want = max(want, 0)
    if lots_now and want and abs(want - lots_now) < max(1.0, lab['rebalance_band'] * lots_now):
        return lots_now
    return want


def run_engine(m, raw, lab, gcfg, start, end=None, capital=None, vol_target=True):
    """Simulate a paper account from session ``start``: decide at each close, trade at the next open.

    Returns daily dates/returns/equity/lots for sessions start+1..end-1 and the
    order for the next open after the last decision (``next_lots``)."""
    end = len(m) if end is None else end
    capital = capital or lab['capital']
    vol = m.vol(lab['vol_lookback'])
    g = gcfg['lot_grams']
    equity, lots = float(capital), 0
    dates, rets, eq, lots_path = [], [], [], []
    entries = costs_paid = 0
    for i in range(start, end):
        new = target_lots(raw[i], vol[i], equity, m.c[i], lots, lab, gcfg, vol_target)
        if i + 1 >= end:
            return {'dates': dates, 'rets': rets, 'equity': eq, 'lots': lots_path, 'entries': entries,
                    'costs': round(costs_paid, 2), 'next_lots': new, 'last_lots': lots, 'final_equity': equity}
        o, c1 = m.o[i + 1], m.c[i + 1]
        pnl = lots * g * (o - m.c[i])
        cost = side_cost(new - lots, o, gcfg, True) if new > lots else side_cost(lots - new, o, gcfg, False)
        if new and lab['roll_monthly'] and m.dates[i + 1].month != m.dates[i].month:
            cost += side_cost(new, o, gcfg, False) + side_cost(new, o, gcfg, True)
        if new and not lots:
            entries += 1
        pnl += new * g * (c1 - o) - new * g * c1 * lab['carry_pct'] / 100 / YEAR
        pnl += equity * lab['cash_rate_pct'] / 100 / YEAR
        before, equity, lots = equity, equity + pnl - cost, new
        costs_paid += cost
        dates.append(m.dates[i + 1])
        rets.append(equity / before - 1 if before > 0 else 0.0)
        eq.append(equity)
        lots_path.append(lots)
        if equity <= 0:
            lots = 0
            break
    return {'dates': dates, 'rets': rets, 'equity': eq, 'lots': lots_path, 'entries': entries,
            'costs': round(costs_paid, 2), 'next_lots': 0, 'last_lots': lots, 'final_equity': equity}


# --------------------------------------------------------------------------- metrics

def metrics(rets, lots=None, entries=None, rf=0.0):
    """Return statistics. ``rf`` is the daily cash rate: Sharpe and Sortino use returns above it."""
    n = len(rets)
    if n < 2:
        return {'sessions': n}
    mean = sum(rets) / n - rf
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    down = math.sqrt(sum(min(r - rf, 0) ** 2 for r in rets) / n)
    equity = peak = 1.0
    dd = 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
    years = n / YEAR
    cagr = equity ** (1 / years) - 1 if equity > 0 else -1.0
    sharpe = mean / sd * math.sqrt(YEAR) if sd > 0 else 0.0
    out = {'sessions': n, 'total_pct': round((equity - 1) * 100, 2), 'cagr_pct': round(cagr * 100, 2),
           'vol_pct': round(sd * math.sqrt(YEAR) * 100, 2), 'sharpe': round(sharpe, 3),
           'sharpe_se': round(math.sqrt((1 + 0.5 * sharpe ** 2) / years), 3),
           'sortino': round(mean / down * math.sqrt(YEAR), 3) if down > 0 else None,
           'max_dd_pct': round(dd * 100, 2), 'calmar': round(cagr / dd, 3) if dd > 0 else None}
    if lots is not None:
        out['exposure_pct'] = round(sum(1 for x in lots if x) / max(len(lots), 1) * 100, 1)
    if entries is not None:
        out['entries_per_year'] = round(entries / years, 1)
    return out


def by_year(dates, rets):
    out = {}
    for d, r in zip(dates, rets):
        out[d.year] = out.get(d.year, 1.0) * (1 + r)
    return {y: round((v - 1) * 100, 2) for y, v in sorted(out.items())}


def bootstrap_prob(a, b, samples=400, block=20, seed=7):
    """Moving-block bootstrap P(Sharpe(a) > Sharpe(b)) for aligned daily returns.

    Sharpe, not mean return, so a strategy that is in cash half the time is not
    penalised for its lower volatility."""
    n = min(len(a), len(b))
    if n < block * 2:
        return None
    a, b = a[:n], b[:n]
    # Prefix sums let each block's sums be read in O(1).
    ext_a, ext_b = a + a[:block], b + b[:block]
    pre = [[0.0] * (n + block + 1) for _ in range(4)]
    for i in range(n + block):
        x, y = ext_a[i], ext_b[i]
        pre[0][i + 1] = pre[0][i] + x
        pre[1][i + 1] = pre[1][i] + x * x
        pre[2][i + 1] = pre[2][i] + y
        pre[3][i + 1] = pre[3][i] + y * y
    blocks = (n + block - 1) // block
    rng, wins = random.Random(seed), 0
    for _ in range(samples):
        sa = sa2 = sb = sb2 = 0.0
        for _ in range(blocks):
            s, e = (lambda k: (k, k + block))(rng.randrange(n))
            sa += pre[0][e] - pre[0][s]
            sa2 += pre[1][e] - pre[1][s]
            sb += pre[2][e] - pre[2][s]
            sb2 += pre[3][e] - pre[3][s]
        m = blocks * block
        va, vb = sa2 / m - (sa / m) ** 2, sb2 / m - (sb / m) ** 2
        sha = sa / m / math.sqrt(va) if va > 0 else 0.0
        shb = sb / m / math.sqrt(vb) if vb > 0 else 0.0
        wins += sha > shb
    return round(wins / samples, 3)


def daily_rf(lab):
    return lab['cash_rate_pct'] / 100 / YEAR


def haircut(n_configs, years):
    """Expected best Sharpe among n_configs skill-less strategies: E[max] of n normals x Sharpe s.e."""
    if n_configs < 2 or years <= 0:
        return 0.0
    return round(math.sqrt(2 * math.log(n_configs)) / math.sqrt(years), 3)


# --------------------------------------------------------------------------- research

def evaluate_all(m, lab, gcfg, strategies=None, since=EVAL_START, log=lambda msg: None):
    """Full-history paper accounts for every strategy from ``since``. Returns {name: result}."""
    strategies = strategies or list(REGISTRY.values())
    start = max(m.index_of(since), 0)
    out = {}
    for s in strategies:
        raw = s.exposures(m)
        run = run_engine(m, raw, lab, gcfg, start, vol_target=s.vol_target)
        run['raw'] = raw
        out[s.name] = run
        log('gold lab: %-16s %s' % (s.name, metrics(run['rets'], rf=daily_rf(lab))))
    return out


def walk_forward(results, lab, strategies=None):
    """Each test year, every family uses the config with the best Sharpe over the previous train_years.

    Also a 'meta' selector that picks the best family's config each year. Returns
    {family: {'rets', 'dates', 'choices', 'metrics'}}."""
    strategies = strategies or list(REGISTRY.values())
    any_run = next(iter(results.values()))
    dates = any_run['dates']
    years = sorted({d.year for d in dates if d.year >= lab['first_test_year']})
    families = {}
    for s in strategies:
        families.setdefault(s.family, []).append(s.name)
    index = {}
    for i, d in enumerate(dates):
        index.setdefault(d.year, []).append(i)

    rf = daily_rf(lab)

    def sharpe(name, idx):
        rs = [results[name]['rets'][i] for i in idx if i < len(results[name]['rets'])]
        return metrics(rs, rf=rf).get('sharpe', -9)

    out = {}
    meta = {'rets': [], 'dates': [], 'choices': {}}
    for fam, names in families.items():
        rec = {'rets': [], 'dates': [], 'choices': {}}
        for y in years:
            train = [i for yy in range(y - lab['train_years'], y) for i in index.get(yy, [])]
            if len(train) < YEAR:
                continue
            best = max(names, key=lambda nm: sharpe(nm, train))
            rec['choices'][y] = (best, sharpe(best, train))
            for i in index.get(y, []):
                if i < len(results[best]['rets']):
                    rec['rets'].append(results[best]['rets'][i])
                    rec['dates'].append(dates[i])
        rec['metrics'] = metrics(rec['rets'], rf=rf)
        out[fam] = rec
    for y in years:
        cands = [(fam, out[fam]['choices'][y]) for fam in out if y in out[fam]['choices'] and fam != 'benchmark']
        if not cands:
            continue
        fam, (name, _) = max(cands, key=lambda x: x[1][1])
        meta['choices'][y] = (name, round(out[fam]['choices'][y][1], 3))
        for i in index.get(y, []):
            if i < len(results[name]['rets']):
                meta['rets'].append(results[name]['rets'][i])
                meta['dates'].append(dates[i])
    meta['metrics'] = metrics(meta['rets'], rf=rf)
    out['meta_selector'] = meta
    return out


def test_window(results, name, first_year):
    run = results[name]
    idx = [i for i, d in enumerate(run['dates']) if d.year >= first_year]
    return [run['rets'][i] for i in idx], [run['lots'][i] for i in idx]


def research_report(m, lab, gcfg, results=None, log=lambda msg: None):
    results = results or evaluate_all(m, lab, gcfg, log=log)
    wf = walk_forward(results, lab)
    first, rf = lab['first_test_year'], daily_rf(lab)
    bench, _ = test_window(results, 'buy_hold', first)
    rows = []
    for name, run in results.items():
        s = REGISTRY[name]
        rets, lots = test_window(results, name, first)
        rows.append({'name': name, 'family': s.family, 'version': s.version, 'note': s.note, 'posthoc': s.posthoc,
                     'full': metrics(run['rets'], run['lots'], run['entries'], rf),
                     'test': metrics(rets, lots, rf=rf),
                     'p_beats_buy_hold': bootstrap_prob([r - rf for r in rets], [r - rf for r in bench], lab['bootstrap_samples']),
                     'by_year': by_year(run['dates'], run['rets'])})
    rows.sort(key=lambda r: -r['test'].get('sharpe', -9))
    test_years = len(bench) / YEAR
    return {'generated_at': dt.datetime.now(UTC).isoformat(timespec='seconds'),
            'data': {'from': results['buy_hold']['dates'][0].isoformat(), 'to': m.dates[-1].isoformat(),
                     'sessions': len(results['buy_hold']['dates']), 'basis': m.basis},
            'settings': {k: lab[k] for k in ('capital', 'vol_target_pct', 'max_leverage', 'carry_pct', 'cash_rate_pct', 'roll_monthly',
                                              'rebalance_band', 'train_years', 'first_test_year')},
            'configs': len(results), 'test_years': round(test_years, 1),
            'haircut': haircut(len(results), test_years), 'strategies': rows,
            'walk_forward': {fam: {'metrics': rec['metrics'],
                                   'choices': {str(y): c[0] for y, c in sorted(rec['choices'].items())}}
                             for fam, rec in wf.items()}}


def format_research(rep):
    st = rep['settings']
    out = ['Gold lab: %s to %s, %d sessions, capital %s, vol target %g%%, max leverage %gx, carry %g%%/y, cash earns %g%%/y, costs and monthly rolls included' % (
        rep['data']['from'], rep['data']['to'], rep['data']['sessions'], P.inr(st['capital'], 0),
        st['vol_target_pct'], st['max_leverage'], st['carry_pct'], st['cash_rate_pct']),
        'CAGR is the account\'s total return; Sharpe uses returns above the cash rate.', '',
        'Test years %d+ (%.1f y). %d configs tried: a skill-less best would show Sharpe +%.2f by luck alone (haircut).' % (
            rep['settings']['first_test_year'], rep['test_years'], rep['configs'], rep['haircut']), '',
        '%-16s %7s %7s %6s %7s %6s %6s %8s   %s' % ('strategy', 'CAGR', 'vol', 'Sharpe', 'maxDD', 'expo', 'trd/y', 'P(S>B&H)', 'full-history Sharpe')]
    for r in rep['strategies']:
        t, f = r['test'], r['full']
        out.append('%-16s %6.1f%% %6.1f%% %6.2f %6.1f%% %5.0f%% %6.1f %8s   %.2f' % (
            r['name'] + ('*' if r['posthoc'] else ''), t.get('cagr_pct', 0), t.get('vol_pct', 0), t.get('sharpe', 0), t.get('max_dd_pct', 0),
            t.get('exposure_pct', 0), f.get('entries_per_year', 0),
            '-' if r['p_beats_buy_hold'] is None else '%.2f' % r['p_beats_buy_hold'], f.get('sharpe', 0)))
    out += ['', 'Walk-forward (each year uses the config that was best over the previous %d years):' % rep['settings']['train_years']]
    for fam, w in sorted(rep['walk_forward'].items(), key=lambda kv: -kv[1]['metrics'].get('sharpe', -9)):
        mt = w['metrics']
        picks = sorted(set(w['choices'].values()))
        out.append('  %-14s Sharpe %5.2f  CAGR %5.1f%%  maxDD %5.1f%%   used: %s' % (
            fam, mt.get('sharpe', 0), mt.get('cagr_pct', 0), mt.get('max_dd_pct', 0), ', '.join(picks)))
    out += ['', '* post-hoc: designed after seeing these results, so only its forward paper record counts.',
            'Proxy prices (COMEX x USD/INR), next-open fills, estimated costs. Past results do not guarantee future ones.']
    return '\n'.join(out)


# --------------------------------------------------------------------------- ledger

def _hash(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_decisions(state_dir):
    path = Path(state_dir) / 'decisions.jsonl'
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify_chain(decisions):
    """(ok, problem): every line's hash matches its content and links to the line before."""
    prev = None
    for k, d in enumerate(decisions):
        body = {x: y for x, y in d.items() if x != 'hash'}
        if d.get('prev') != prev:
            return False, 'line %d (%s) does not link to the line before' % (k + 1, d.get('data_date'))
        if _hash(body) != d.get('hash'):
            return False, 'line %d (%s) was edited after it was written' % (k + 1, d.get('data_date'))
        prev = d['hash']
    return True, None


def append_decision(state_dir, doc):
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    decisions = load_decisions(state)
    ok, problem = verify_chain(decisions)
    if not ok:
        raise ValueError('gold lab ledger is broken: %s' % problem)
    if decisions and decisions[-1]['data_date'] >= doc['data_date']:
        raise ValueError('gold lab already decided for %s' % decisions[-1]['data_date'])
    body = dict(doc, prev=decisions[-1]['hash'] if decisions else None)
    body['hash'] = _hash(body)
    with open(state / 'decisions.jsonl', 'a') as fh:
        fh.write(json.dumps(body, sort_keys=True) + '\n')
    return body


def decide(m, now, strategies=None):
    """Today's committed decision: each strategy's exposure at the last completed session."""
    strategies = strategies or list(REGISTRY.values())
    i = len(m) - 1
    out = {}
    for s in strategies:
        raw = s.exposures(m)[i]
        out[s.name] = {'version': s.version, 'raw': None if raw is None else round(raw, 6)}
    tail = json.dumps([round(x, 4) for x in m.c[-60:]])
    return {'data_date': m.dates[i].isoformat(), 'recorded_at': now.isoformat(timespec='seconds'),
            'close': round(m.c[i], 2), 'basis': m.basis,
            'data_sha': hashlib.sha256(tail.encode()).hexdigest()[:16], 'strategies': out}


def forward_raw(m, decisions, name, version=None):
    """(start index, exposure list) from logged decisions; sessions with no log hold the last one.

    With ``version``, only that version's lines count: a version bump restarts the record."""
    raw, start, last = [None] * len(m), None, None
    logged = {}
    for d in decisions:
        x = d['strategies'].get(name)
        if x is not None and (version is None or x['version'] == version):
            logged[d['data_date']] = x['raw']
    if not logged:
        return None, raw
    first = min(logged)
    for i, day in enumerate(m.dates):
        iso = day.isoformat()
        if iso < first:
            continue
        if start is None:
            start = i
        if iso in logged:
            last = logged[iso]
        raw[i] = last
    return start, raw


def forward_accounts(m, decisions, lab, gcfg):
    """Forward paper account per strategy, replayed from its logged decisions only."""
    out = {}
    names = sorted({n for d in decisions for n in d['strategies']})
    for name in names:
        s = REGISTRY.get(name)
        start, raw = forward_raw(m, decisions, name, s.version if s else None)
        if start is None:
            continue
        run = run_engine(m, raw, lab, gcfg, start, vol_target=s.vol_target if s else True)
        run['metrics'] = metrics(run['rets'], run['lots'], run['entries'], daily_rf(lab))
        run['since'] = m.dates[start].isoformat()
        run['sessions'] = len(run['rets'])
        out[name] = run
    return out


def decision_mismatch(m, decisions, name):
    """Share of logged decisions that a replay on today's data disagrees with (look-ahead or revised data)."""
    s = REGISTRY.get(name)
    if s is None:
        return None
    replay = s.exposures(m)
    checked = differ = 0
    for d in decisions:
        x = d['strategies'].get(name)
        i = m.index_of(dt.date.fromisoformat(d['data_date']))
        if x is None or i < 0 or m.dates[i].isoformat() != d['data_date'] or x['version'] != s.version:
            continue
        checked += 1
        a, b = x['raw'] or 0.0, replay[i] or 0.0
        differ += abs(a - b) > 1e-6
    return round(differ / checked, 3) if checked else None


# --------------------------------------------------------------------------- champion and review

def load_champion(state_dir, lab):
    path = Path(state_dir) / 'champion.json'
    if path.is_file():
        return json.loads(path.read_text())
    return {'name': lab['initial_champion'], 'since': None, 'reason': 'initial champion: the morning alert\'s rules (chosen 2026-11-19 after the first research run)', 'history': []}


def save_champion(state_dir, doc):
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    (Path(state_dir) / 'champion.json').write_text(json.dumps(doc, indent=2))


def promotion_check(rep, forward, mismatches, champion):
    """Apply PROMOTION to every challenger. Returns (winner or None, per-challenger verdicts)."""
    rows = {r['name']: r for r in rep['strategies']}
    champ = rows.get(champion)
    verdicts = {}
    if champ is None:
        return None, verdicts
    cf = forward.get(champion)
    best = None
    for name, r in rows.items():
        if name == champion:
            continue
        why = []
        f = forward.get(name)
        n = f['sessions'] if f else 0
        if n < PROMOTION['min_forward_sessions']:
            why.append('%d/%d forward sessions' % (n, PROMOTION['min_forward_sessions']))
        edge = r['test'].get('sharpe', -9) - champ['test'].get('sharpe', -9)
        if edge < PROMOTION['sharpe_margin']:
            why.append('test Sharpe edge %+.2f < %.2f' % (edge, PROMOTION['sharpe_margin']))
        p = r.get('p_vs_champion')
        if p is None or p < PROMOTION['min_bootstrap_prob']:
            why.append('P(beats champion) %s < %.2f' % ('-' if p is None else '%.2f' % p, PROMOTION['min_bootstrap_prob']))
        mm = mismatches.get(name)
        if mm is not None and mm > PROMOTION['max_decision_mismatch']:
            why.append('replay disagrees with %.0f%% of logged decisions' % (mm * 100))
        if f and cf and n >= 2:
            k = min(len(f['rets']), len(cf['rets']))
            a, b = f['rets'][-k:], cf['rets'][-k:]
            diff = [x - y for x, y in zip(a, b)]
            sd = math.sqrt(sum(x * x for x in diff) / max(len(diff) - 1, 1)) * math.sqrt(len(diff))
            if sum(diff) < -PROMOTION['forward_tolerance_sd'] * sd:
                why.append('forward return trails the champion by more than %.1f s.d.' % PROMOTION['forward_tolerance_sd'])
        verdicts[name] = {'eligible': not why, 'blocking': why, 'edge': round(edge, 3)}
        if not why and (best is None or edge > verdicts[best]['edge']):
            best = name
    return best, verdicts


def review(m, lab, gcfg, now, promote=True, log=lambda msg: None):
    state = Path(lab['state_dir'])
    results = evaluate_all(m, lab, gcfg, log=log)
    rep = research_report(m, lab, gcfg, results, log)
    champion = load_champion(state, lab)
    cname = champion['name']
    rf = daily_rf(lab)
    c_rets = [x - rf for x in test_window(results, cname, lab['first_test_year'])[0]]
    for r in rep['strategies']:
        rets = [x - rf for x in test_window(results, r['name'], lab['first_test_year'])[0]]
        r['p_vs_champion'] = None if r['name'] == cname else bootstrap_prob(rets, c_rets, lab['bootstrap_samples'])
    decisions = load_decisions(state)
    ok, problem = verify_chain(decisions)
    forward = forward_accounts(m, decisions, lab, gcfg) if ok else {}
    mismatches = {n: decision_mismatch(m, decisions, n) for n in forward}
    winner, verdicts = promotion_check(rep, forward, mismatches, cname)
    promoted = None
    if winner and promote and ok:
        champion['history'].append({'name': cname, 'until': now.date().isoformat()})
        champion.update(name=winner, since=now.date().isoformat(),
                        reason='promoted by the pre-registered rules: test Sharpe edge %+.2f, P %.2f' % (
                            verdicts[winner]['edge'], next(r['p_vs_champion'] for r in rep['strategies'] if r['name'] == winner)))
        save_champion(state, champion)
        promoted = winner
    doc = dict(rep, champion=champion, promoted=promoted, ledger_ok=ok, ledger_problem=problem,
               forward={n: dict(f['metrics'], since=f['since'], lots_now=f['last_lots']) for n, f in forward.items()},
               mismatch=mismatches, verdicts=verdicts, promotion_rules=PROMOTION)
    rdir = state / 'reviews'
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / ('%s.json' % now.date().isoformat())).write_text(json.dumps(doc, indent=2, default=str))
    (rdir / ('%s.md' % now.date().isoformat())).write_text(format_review(doc))
    (state / 'review-latest.json').write_text(json.dumps(doc, indent=2, default=str))
    return doc


def format_review(doc):
    out = ['# Gold lab review, %s' % doc['generated_at'][:10], '',
           'Champion: **%s** (%s)%s' % (doc['champion']['name'], doc['champion']['reason'],
                                        ', promoted today' if doc['promoted'] else ''), '']
    if not doc['ledger_ok']:
        out += ['**Ledger check failed: %s.** Forward results are not trusted until this is resolved.' % doc['ledger_problem'], '']
    out += ['## Forward paper trading (decisions logged before outcomes)', '',
            '| Strategy | Since | Sessions | Return | Sharpe | Max DD | Lots now | Replay mismatch |', '|---|---|---|---|---|---|---|---|']
    for name, f in sorted(doc['forward'].items(), key=lambda kv: -kv[1].get('total_pct', -999)):
        mm = doc['mismatch'].get(name)
        out.append('| %s | %s | %d | %+.2f%% | %s | %.2f%% | %s | %s |' % (
            name, f['since'], f['sessions'], f.get('total_pct', 0), f.get('sharpe', '-'), f.get('max_dd_pct', 0),
            f['lots_now'], '-' if mm is None else '%.0f%%' % (mm * 100)))
    out += ['', '## Research (test years %d+, after costs, rolls and carry)' % doc['settings']['first_test_year'], '',
            'Multiple-testing haircut: +%.2f Sharpe (%d configs, %.1f test years).' % (doc['haircut'], doc['configs'], doc['test_years']), '',
            '| Strategy | CAGR | Sharpe | Max DD | P(beats B&H) | P(beats champion) | Promotion |', '|---|---|---|---|---|---|---|']
    for r in doc['strategies']:
        t, v = r['test'], doc['verdicts'].get(r['name'])
        status = 'champion' if r['name'] == doc['champion']['name'] else ('eligible' if v and v['eligible'] else
                                                                           '; '.join(v['blocking']) if v else '-')
        out.append('| %s | %.1f%% | %.2f | %.1f%% | %s | %s | %s |' % (
            r['name'], t.get('cagr_pct', 0), t.get('sharpe', 0), t.get('max_dd_pct', 0),
            '-' if r['p_beats_buy_hold'] is None else '%.2f' % r['p_beats_buy_hold'],
            '-' if r.get('p_vs_champion') is None else '%.2f' % r['p_vs_champion'], status))
    out += ['', '## Walk-forward', '']
    for fam, w in sorted(doc['walk_forward'].items(), key=lambda kv: -kv[1]['metrics'].get('sharpe', -9)):
        out.append('- %s: Sharpe %.2f, CAGR %.1f%%, max DD %.1f%%' % (
            fam, w['metrics'].get('sharpe', 0), w['metrics'].get('cagr_pct', 0), w['metrics'].get('max_dd_pct', 0)))
    out += ['', 'Promotion rules (pre-registered): `%s`' % json.dumps(doc['promotion_rules'])]
    return '\n'.join(out) + '\n'


# --------------------------------------------------------------------------- daily run and messages

def daily(m, lab, gcfg, now, dry_run=False):
    """Log today's decisions (once per data date) and return (status, messages)."""
    state = Path(lab['state_dir'])
    decisions = load_decisions(state)
    doc = decide(m, now)
    if decisions and decisions[-1]['data_date'] >= doc['data_date']:
        return {'status': 'SKIPPED', 'reason': 'already decided for %s' % decisions[-1]['data_date']}, []
    champion = load_champion(state, lab)
    doc['champion'] = champion['name']
    if not dry_run:
        if champion['since'] is None:
            champion['since'] = now.date().isoformat()
            save_champion(state, champion)
        append_decision(state, doc)
        decisions = load_decisions(state)
    else:
        decisions = decisions + [dict(doc, hash='dry-run', prev=None)]
    forward = forward_accounts(m, decisions, lab, gcfg)
    texts = []
    cf = forward.get(champion['name'])
    if cf:
        prev_lots, next_lots = cf['last_lots'], cf['next_lots']
        first = len(decisions) == 1
        if next_lots != prev_lots or first:
            texts.append(champion_text(champion['name'], prev_lots, next_lots, m, gcfg, cf, forward.get('buy_hold'),
                                       first=first, capital=lab['capital'], strategies=len(doc['strategies'])))
    return {'status': 'DECIDED', 'data_date': doc['data_date'], 'champion': champion['name'],
            'champion_raw': doc['strategies'][champion['name']]['raw'],
            'champion_lots': cf['next_lots'] if cf else None}, texts


def champion_text(name, prev, new, m, gcfg, run, bench, first=False, capital=None, strategies=None):
    day = m.dates[-1].strftime('%a %d %b')
    contract = gcfg['contract']
    if new > prev:
        order = '🟢 BUY %d %s at the MCX open' % (new - prev, contract)
    elif new < prev:
        order = '🔴 SELL %d %s at the MCX open' % (prev - new, contract)
    else:
        order = '▬ No order'
    pos = 'LONG %d lot%s' % (new, '' if new == 1 else 's') if new else 'FLAT'
    lines = ['🧪 <b>Gold lab</b> · %s close' % day]
    if first:
        lines.append('Forward paper trading starts: %d strategies, %s each. Decisions are logged before outcomes exist.' % (
            strategies or 0, P.inr(capital or 0, 0)))
        lines.append('Champion <b>%s</b>: %s' % (html.escape(name), pos))
    else:
        lines.append('Champion <b>%s</b>: %s (was %d)' % (html.escape(name), pos, prev))
    lines += [order, 'Proxy close %s/g · basis %.3f' % (P.inr(m.c[-1], 0), m.basis)]
    mt = run['metrics']
    if run['sessions']:
        lines.append('Paper since %s: %+.2f%%%s' % (
            run['since'], mt.get('total_pct', 0),
            ' · buy & hold %+.2f%%' % bench['metrics'].get('total_pct', 0) if bench and bench['sessions'] else ''))
    lines.append('Paper trade, rule-based, not advice · /lab')
    return '\n'.join(lines)


def scoreboard_text(doc, limit=6):
    rows = sorted(doc['forward'].items(), key=lambda kv: -kv[1].get('total_pct', -999))
    champ = doc['champion']['name']
    lines = ['🧪 <b>Gold lab weekly scoreboard</b>',
             'Champion: <b>%s</b>%s' % (html.escape(champ), ' (promoted today)' if doc['promoted'] else '')]
    if rows:
        lines.append('Forward paper results (since %s):' % min(f['since'] for _, f in rows))
        for name, f in rows[:limit]:
            lines.append('%s %s %+.2f%% · DD %.1f%%' % ('🏆' if name == champ else '•', html.escape(name),
                                                        f.get('total_pct', 0), f.get('max_dd_pct', 0)))
    eligible = [n for n, v in doc['verdicts'].items() if v['eligible']]
    lines.append('Eligible challengers: %s' % (', '.join(eligible) if eligible else 'none yet (%d forward sessions needed)'
                                                 % PROMOTION['min_forward_sessions']))
    if not doc['ledger_ok']:
        lines.append('⚠️ Ledger check failed: %s' % html.escape(doc['ledger_problem']))
    return '\n'.join(lines)


def status_text(m, lab, gcfg):
    state = Path(lab['state_dir'])
    decisions = load_decisions(state)
    ok, problem = verify_chain(decisions)
    champion = load_champion(state, lab)
    lines = ['🧪 <b>Gold lab</b> · champion <b>%s</b>' % html.escape(champion['name']),
             'Ledger: %d decisions, %s' % (len(decisions), 'chain intact' if ok else 'BROKEN: %s' % problem)]
    if not decisions:
        return '\n'.join(lines + ['No forward decisions yet.'])
    forward = forward_accounts(m, decisions, lab, gcfg)
    for name, f in sorted(forward.items(), key=lambda kv: -kv[1]['metrics'].get('total_pct', -999)):
        lines.append('%s %s %+.2f%% · %d lots' % ('🏆' if name == champion['name'] else '•', html.escape(name),
                                                  f['metrics'].get('total_pct', 0), f['next_lots']))
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Gold strategy lab: research, forward paper ledger, champion/challenger review.')
    ap.add_argument('--config', help='picks JSON with a "gold" object')
    ap.add_argument('--lab-config', default=DEFAULT_LAB_CONFIG, help='optional lab settings (absent = defaults)')
    ap.add_argument('--env-file', default='var/telegram.env')
    ap.add_argument('--dry-run', action='store_true', help='print instead of sending; logs nothing')
    ap.add_argument('--as-of', help='pretend it is this time (ISO 8601 with offset)')
    ap.add_argument('--json', action='store_true')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--research', action='store_true', help='full-history and walk-forward report for every strategy')
    mode.add_argument('--review', action='store_true', help='research + forward results + promotion check; writes reviews/')
    mode.add_argument('--status', action='store_true', help='forward paper results so far')
    mode.add_argument('--verify', action='store_true', help='check the decision ledger hash chain')
    a = ap.parse_args(argv)
    log = lambda msg: print(msg, file=sys.stderr)
    try:
        full = P.load_config(a.config)
        gcfg = full['gold']
        lab = load_settings(a.lab_config, gcfg)
        now = dt.datetime.fromisoformat(a.as_of).astimezone(IST) if a.as_of else dt.datetime.now(IST)
        state = Path(lab['state_dir'])
        if a.verify:
            decisions = load_decisions(state)
            ok, problem = verify_chain(decisions)
            print('%d decisions, %s' % (len(decisions), 'chain intact' if ok else 'BROKEN: ' + problem))
            return 0 if ok else 1
        basis = G.current_basis(gcfg, now)[0]
        m = load_market(now.astimezone(UTC), basis, log=log)
        env = dict(P.read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith('TELEGRAM_')})
        token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        # The dashboard's gold switch mutes these notices; decisions are still logged.
        muted = not S.enabled(S.path_for(full), 'gold')
        send = (lambda text: P.send_telegram(token, chat, text)) \
            if lab['telegram'] and not muted and token and chat and not a.dry_run \
            else (lambda text: print(text + '\n'))
        if a.research:
            rep = research_report(m, lab, gcfg)
            state.mkdir(parents=True, exist_ok=True)
            (state / 'research-latest.json').write_text(json.dumps(rep, indent=2, default=str))
            print(json.dumps(rep, indent=2, default=str) if a.json else format_research(rep))
            return 0
        if a.status:
            print(status_text(m, lab, gcfg))
            return 0
        if a.review:
            doc = review(m, lab, gcfg, now, promote=not a.dry_run)
            print(json.dumps(doc, indent=2, default=str) if a.json else format_review(doc))
            return 0
        if now.weekday() >= 5:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'weekend'}))
            return 0
        result, texts = daily(m, lab, gcfg, now, dry_run=a.dry_run)
        if result['status'] == 'DECIDED' and now.weekday() == lab['review_weekday']:
            doc = review(m, lab, gcfg, now, promote=not a.dry_run)
            texts.append(scoreboard_text(doc))
        for text in texts:
            send(text)
        if not a.dry_run:
            state.mkdir(parents=True, exist_ok=True)
            (state / ('ran-%s' % now.date().isoformat())).write_text(now.isoformat())
        print(json.dumps(result, default=str))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
