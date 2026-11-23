import datetime as dt
import json
import random
import tempfile
import unittest
from pathlib import Path

from india import gold as G
from india import gold_lab as L
from india.data import IST, Bar

UTC = dt.timezone.utc


def walk(n, seed=3, start=1000.0, drift=0.0004, vol=0.011, day=dt.date(2020, 1, 1)):
    """Weekday random-walk bars with gaps between sessions; deterministic for a seed."""
    rng, out, prev = random.Random(seed), [], start
    while len(out) < n:
        if day.weekday() < 5:
            o = prev * (1 + rng.gauss(0, vol / 3))
            c = o * (1 + rng.gauss(drift, vol))
            h, l = max(o, c) * (1 + abs(rng.gauss(0, vol / 2))), min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
            out.append(Bar(dt.datetime.combine(day, dt.time(), UTC), o, h, l, c, 0.0))
            prev = c
        day += dt.timedelta(days=1)
    return out


DXY = walk(1200, 4, start=100.0, drift=0.0, vol=0.004)


def market(n=900, seed=3, upto=None, **kw):
    """A synthetic market; ``upto`` keeps only the first sessions, as a live run on that day would see."""
    bars = walk(n, seed, **kw)[:upto]
    return L.Market(bars, [(b.close, 1.0) for b in bars], DXY, None, basis=1.0)


def zero_costs():
    return G.settings({'costs': {k: 0 for k in G.COST_DEFAULTS}})


def lab(**over):
    raw = dict({'capital': 1e7, 'carry_pct': 0.0, 'roll_monthly': False, 'rebalance_band': 0.0}, **over)
    return L.settings(raw, G.settings({'state_dir': over.pop('gold_state', 'var/state/gold')}))


class LookAheadTests(unittest.TestCase):
    """Every registered strategy, now and in future, must be causal."""

    def test_no_strategy_uses_future_sessions(self):
        full = market(900)
        for name, s in L.REGISTRY.items():
            whole = s.exposures(full)
            for k in (450, 620, 899):
                part = s.exposures(market(900, upto=k))
                self.assertEqual(part, whole[:k], '%s changes past decisions when sessions after %d are added' % (name, k))

    def test_exposures_are_in_range(self):
        m = market(900)
        for name, s in L.REGISTRY.items():
            xs = s.exposures(m)
            self.assertEqual(len(xs), len(m))
            self.assertTrue(all(x is None or 0.0 <= x <= 1.0 for x in xs), name)
            self.assertTrue(any(x for x in xs), '%s never takes a position' % name)


class EngineTests(unittest.TestCase):
    def test_frictionless_buy_and_hold_tracks_price_from_the_next_open(self):
        m = market(400)
        run = L.run_engine(m, L.s_buy_hold(m), lab(rebalance_band=1.0), zero_costs(), 10, vol_target=False)
        lots = run['lots'][0]
        self.assertEqual(set(run['lots']), {lots})
        equity = 1e7 + lots * (m.c[-1] - m.o[11])
        self.assertAlmostEqual(run['equity'][-1], equity, places=4)
        self.assertEqual(lots, int(1e7 // m.c[10]))

    def test_a_decision_only_moves_equity_from_the_next_session(self):
        m = market(200)
        raw = [0.0] * len(m)
        raw[100] = 1.0
        run = L.run_engine(m, raw, lab(), zero_costs(), 50, vol_target=False)
        # rets[k] is session 51 + k. Bought at the 101 open, sold at the 102 open:
        # session 101's open-to-close and the overnight gap into 102.
        rets = run['rets']
        self.assertTrue(all(r == 0 for k, r in enumerate(rets) if k not in (50, 51)))
        lots = int(1e7 // m.c[100])
        self.assertAlmostEqual(rets[50], lots * (m.c[101] - m.o[101]) / 1e7, places=12)
        self.assertAlmostEqual(rets[51], lots * (m.o[102] - m.c[101]) / run['equity'][50], places=12)
        self.assertEqual(run['entries'], 1)

    def test_costs_rolls_and_carry_reduce_returns(self):
        m = market(400)
        base = L.run_engine(m, L.s_buy_hold(m), lab(capital=1e5), zero_costs(), 10, vol_target=False)['final_equity']
        costly = L.run_engine(m, L.s_buy_hold(m), lab(capital=1e5), G.settings(None), 10, vol_target=False)['final_equity']
        rolled = L.run_engine(m, L.s_buy_hold(m), lab(capital=1e5, roll_monthly=True), G.settings(None), 10,
                              vol_target=False)['final_equity']
        carried = L.run_engine(m, L.s_buy_hold(m), lab(capital=1e5, roll_monthly=True, carry_pct=6.5), G.settings(None), 10,
                               vol_target=False)['final_equity']
        self.assertGreater(base, costly)
        self.assertGreater(costly, rolled)
        self.assertGreater(rolled, carried)

    def test_cash_rate_credits_idle_equity(self):
        m = market(300)
        flat = [0.0] * len(m)
        run = L.run_engine(m, flat, lab(cash_rate_pct=6.3), zero_costs(), 0)
        self.assertAlmostEqual(run['final_equity'], 1e7 * (1 + 0.063 / 252) ** (len(m) - 1), places=2)

    def test_side_cost_matches_the_round_trip_estimate(self):
        g = G.settings(None)
        rt = L.side_cost(5, 10000.0, g, True) + L.side_cost(5, 10200.0, g, False)
        self.assertAlmostEqual(rt, G.trade_costs(10000.0, 10200.0, 5, g), places=6)

    def test_target_lots_scales_by_volatility_and_respects_the_band(self):
        l, g = lab(vol_target_pct=10, max_leverage=2.0, rebalance_band=0.25), zero_costs()
        self.assertEqual(L.target_lots(1.0, 0.20, 1e6, 1000, 0, l, g), 500)        # 10/20 = 0.5x
        self.assertEqual(L.target_lots(1.0, 0.02, 1e6, 1000, 0, l, g), 2000)       # capped at 2x
        self.assertEqual(L.target_lots(1.0, 0.20, 1e6, 1000, 450, l, g), 450)      # within 25%: no trade
        self.assertEqual(L.target_lots(0.0, 0.20, 1e6, 1000, 450, l, g), 0)        # signal off: always exit
        self.assertEqual(L.target_lots(1.0, None, 1e6, 1000, 0, l, g), 0)          # no volatility yet


class StatsTests(unittest.TestCase):
    def test_metrics_and_bootstrap(self):
        rng = random.Random(1)
        a = [rng.gauss(0.001, 0.01) for _ in range(1500)]
        b = [x - 0.001 for x in a]
        self.assertGreater(L.bootstrap_prob(a, b, 200), 0.95)
        self.assertLess(L.bootstrap_prob(b, a, 200), 0.05)
        mt = L.metrics(a)
        self.assertGreater(mt['sharpe'], 0)
        self.assertGreaterEqual(mt['max_dd_pct'], 0)
        self.assertEqual(L.haircut(1, 10), 0.0)
        self.assertAlmostEqual(L.haircut(20, 16), (2 * 2.9957) ** 0.5 / 4, places=2)

    def test_walk_forward_picks_with_past_years_only(self):
        days = [dt.date(2010, 1, 4) + dt.timedelta(days=i) for i in range(365 * 8)]
        days = [d for d in days if d.weekday() < 5]
        rng = random.Random(5)
        noise = [rng.gauss(0, 0.01) for _ in days]
        # A wins up to 2015, B from 2016: the 2016 pick must still be A.
        ra = [n + (0.002 if d.year < 2016 else -0.002) for d, n in zip(days, noise)]
        rb = [n + (-0.002 if d.year < 2016 else 0.002) for d, n in zip(days, noise)]
        results = {'tsmom_63': {'dates': days, 'rets': ra}, 'tsmom_126': {'dates': days, 'rets': rb}}
        strategies = [L.REGISTRY['tsmom_63'], L.REGISTRY['tsmom_126']]
        wf = L.walk_forward(results, lab(first_test_year=2015, train_years=3), strategies)
        self.assertEqual(wf['tsmom']['choices'][2016][0], 'tsmom_63')
        self.assertEqual(wf['tsmom']['choices'][2015][0], 'tsmom_63')


class LedgerTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 19, 9, 0, tzinfo=IST)

    def test_chain_detects_edits_and_refuses_repeats(self):
        with tempfile.TemporaryDirectory() as d:
            L.append_decision(d, {'data_date': '2026-11-17', 'strategies': {'a': {'version': 1, 'raw': 1.0}}})
            L.append_decision(d, {'data_date': '2026-11-18', 'strategies': {'a': {'version': 1, 'raw': 0.0}}})
            self.assertEqual(L.verify_chain(L.load_decisions(d)), (True, None))
            with self.assertRaises(ValueError):
                L.append_decision(d, {'data_date': '2026-11-18', 'strategies': {}})
            path = Path(d) / 'decisions.jsonl'
            path.write_text(path.read_text().replace('"raw": 1.0', '"raw": 0.5'))
            ok, problem = L.verify_chain(L.load_decisions(d))
            self.assertFalse(ok)
            self.assertIn('edited', problem)
            with self.assertRaises(ValueError):
                L.append_decision(d, {'data_date': '2026-11-19', 'strategies': {}})

    def test_daily_logs_once_and_forward_pnl_comes_from_the_log(self):
        m = market(700)
        with tempfile.TemporaryDirectory() as d:
            cfg = lab(state_dir=d, capital=1e6)
            g = zero_costs()
            dry, _ = L.daily(m, cfg, g, self.NOW, dry_run=True)
            self.assertEqual(dry['status'], 'DECIDED')
            self.assertFalse((Path(d) / 'decisions.jsonl').exists())
            # Replay three sessions as if live: each day sees only the data up to then.
            for k in (697, 698, 699):
                res, texts = L.daily(market(700, upto=k + 1), cfg, g, self.NOW)
                self.assertEqual(res['status'], 'DECIDED')
            again, _ = L.daily(m, cfg, g, self.NOW)
            self.assertEqual(again['status'], 'SKIPPED')
            decisions = L.load_decisions(d)
            self.assertEqual([x['data_date'] for x in decisions], [b.time.date().isoformat() for b in m.bars[697:700]])
            self.assertEqual(L.verify_chain(decisions), (True, None))
            fwd = L.forward_accounts(m, decisions, cfg, g)
            self.assertEqual(fwd['buy_hold']['sessions'], 2)
            self.assertEqual(L.decision_mismatch(m, decisions, 'tsmom_63'), 0.0)
            self.assertIn(json.loads((Path(d) / 'champion.json').read_text())['name'], L.REGISTRY)

    def test_mismatch_flags_a_replay_that_disagrees_with_the_log(self):
        m = market(400)
        real = L.REGISTRY['tsmom_63'].exposures(m)
        decisions = [{'data_date': m.dates[i].isoformat(),
                      'strategies': {'tsmom_63': {'version': 1, 'raw': 1.0 - real[i]}}} for i in range(300, 320)]
        self.assertEqual(L.decision_mismatch(m, decisions, 'tsmom_63'), 1.0)

    def test_a_version_bump_restarts_the_forward_record(self):
        m = market(400)
        decisions = [{'data_date': m.dates[i].isoformat(),
                      'strategies': {'tsmom_63': {'version': 0 if i < 310 else 1, 'raw': 1.0}}} for i in range(300, 320)]
        start, _ = L.forward_raw(m, decisions, 'tsmom_63', version=1)
        self.assertEqual(start, 310)
        self.assertEqual(L.forward_raw(m, decisions, 'tsmom_63')[0], 300)


class PromotionTests(unittest.TestCase):
    def rep(self, champ_sharpe=0.3, chal_sharpe=0.6, p=0.9):
        return {'haircut': 0.5, 'strategies': [
            {'name': 'swing_v1', 'test': {'sharpe': champ_sharpe}, 'p_vs_champion': None},
            {'name': 'donchian_100', 'test': {'sharpe': chal_sharpe}, 'p_vs_champion': p}]}

    def fwd(self, n, drift=0.0):
        return {'sessions': n, 'rets': [0.001 + drift] * n}

    def test_promotes_only_when_every_rule_passes(self):
        n = L.PROMOTION['min_forward_sessions']
        forward = {'swing_v1': self.fwd(n), 'donchian_100': self.fwd(n)}
        win, verdicts = L.promotion_check(self.rep(), forward, {}, 'swing_v1')
        self.assertEqual(win, 'donchian_100')
        self.assertTrue(verdicts['donchian_100']['eligible'])

    def test_each_rule_blocks(self):
        n = L.PROMOTION['min_forward_sessions']
        ok = {'swing_v1': self.fwd(n), 'donchian_100': self.fwd(n)}
        cases = [
            (self.rep(), {'swing_v1': self.fwd(n), 'donchian_100': self.fwd(n - 1)}, {}, 'forward sessions'),
            (self.rep(chal_sharpe=0.35), ok, {}, 'Sharpe edge'),
            (self.rep(p=0.6), ok, {}, 'P(beats champion)'),
            (self.rep(), ok, {'donchian_100': 0.2}, 'replay disagrees'),
            (self.rep(), {'swing_v1': self.fwd(n), 'donchian_100': {'sessions': n, 'rets': [0.001 + (0.02 if i % 2 else -0.03)
                                                                                         for i in range(n)]}},
             {}, 'trails the champion'),
        ]
        for rep, forward, mm, text in cases:
            win, verdicts = L.promotion_check(rep, forward, mm, 'swing_v1')
            self.assertIsNone(win, text)
            self.assertTrue(any(text in b for b in verdicts['donchian_100']['blocking']), (text, verdicts))


class SettingsTests(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            L.settings({'bogus': 1})
        with self.assertRaises(ValueError):
            L.settings({'max_leverage': 50})
        with self.assertRaises(ValueError):
            L.settings({'initial_champion': 'nope'})
        cfg = L.settings(None, G.settings({'capital': 250000, 'state_dir': 'x/gold'}))
        self.assertEqual(cfg['capital'], 250000)
        self.assertEqual(cfg['state_dir'], str(Path('x/gold_lab')))

    def test_example_config_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        L.settings(json.loads((root / 'config' / 'gold_lab.example.json').read_text()))


if __name__ == '__main__':
    unittest.main()
