"""Offline Time Machine checks; synthetic fixtures are never live prices."""
import datetime as dt
import json
import math
import time
import unittest
from dataclasses import replace
from pathlib import Path

from india.analysis import report
from india.data import Bar, CSVProvider, IST, timestamp
from india.forecast import forecast, MAX_SIMS, _simulate
from india.market import session


class Forecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = CSVProvider(Path(__file__).parent / 'fixtures/india/synthetic.csv').history('DEMO.NS', '5m')
        cls.now = timestamp('2026-10-19T09:35:00+05:30')
        cls.plan = report(cls.current, cls.now, 'DEMO.NS', .05, 100000, 500)['plan']
        # Reuse the fixture's signal and outcome; explicitly synthetic complete
        # sessions provide a deterministic local bootstrap test pool.
        cls.history = []
        day = dt.date(2026, 9, 1)
        days = []
        while len(days) < 20:
            opening = dt.datetime.combine(day, dt.time(9, 15), IST)
            if session(opening):
                days.append(day)
                rows = [replace(b, time=b.time.replace(year=day.year, month=day.month, day=day.day)) for b in cls.current]
                rows = [b for b in rows if b.time.time() < dt.time(15, 15)]
                while len(rows) < 72:
                    previous = rows[-1]
                    rows.append(Bar(previous.time + dt.timedelta(minutes=5), previous.close,
                                    previous.close + .1, previous.close - .1, previous.close, 1000))
                cls.history.extend(rows)
            day += dt.timedelta(days=1)
        cls.bars = cls.history + cls.current

    def run_forecast(self, bars=None, **kwargs):
        args = dict(bars=self.bars if bars is None else bars, as_of=self.now,
                    plan=self.plan, symbol='DEMO.NS', tick=.05, capital=100000,
                    risk=500, sims=64, seed=17)
        args.update(kwargs)
        return forecast(**args)

    def test_paper_seeded_cost_aware_and_json_safe(self):
        a = self.run_forecast()
        self.assertEqual(a, self.run_forecast())
        self.assertEqual(a['status'], 'AVAILABLE')
        self.assertFalse(a['execution_enabled'])
        self.assertEqual(a['data']['prior_complete_sessions'], 20)
        self.assertEqual(a['mc']['bars_left'], 68)
        self.assertAlmostEqual(sum(a['mc'][k] for k in ('p_stop', 'p_target', 'p_neither')), 1)
        self.assertEqual(sum(a['mc']['counts'].values()), 64)
        self.assertTrue(a['mc']['costs_included'])
        self.assertIn('SIMULATED', a['mc']['kind'])
        self.assertLessEqual(a['plan']['max_loss_estimate'], 500)
        self.assertLessEqual(a['plan']['entry'] * a['plan']['qty'] + a['plan']['costs_at_target']['total'], 100000)
        json.dumps(a, allow_nan=False)

    def test_future_and_forming_candles_do_not_change_report(self):
        causal = [b for b in self.bars if b.time + dt.timedelta(minutes=5) <= self.now]
        a = self.run_forecast(causal)
        future_bad = Bar(self.now, math.nan, math.nan, math.nan, math.nan, math.nan)
        self.assertEqual(a, self.run_forecast(causal + [future_bad]))
        self.assertEqual(a, self.run_forecast())
        self.assertLess(a['data']['pool_to'], self.now.date().isoformat())

    def test_missing_or_insufficient_history_has_no_probabilities(self):
        for bars, reason in (([], 'NO_COMPLETED_DATA'),
                             (self.current, 'INSUFFICIENT_HISTORY'),
                             (self.history[72:] + self.current, 'INSUFFICIENT_HISTORY')):
            with self.subTest(reason=reason, rows=len(bars)):
                r = self.run_forecast(bars)
                self.assertEqual(r['status'], 'UNAVAILABLE')
                self.assertEqual(r['reason'], reason)
                self.assertNotIn('p_target', r['mc'])

    def test_partial_prior_sessions_are_excluded(self):
        r = self.run_forecast(self.bars[1:])
        self.assertEqual(r['reason'], 'INSUFFICIENT_HISTORY')
        self.assertEqual(r['data']['excluded_sessions'][0]['bars'], 71)

    def test_missing_current_candle_blocks_simulation(self):
        bars = [b for b in self.bars if b.time != self.current[1].time]
        r = self.run_forecast(bars)
        self.assertEqual(r['reason'], 'INCOMPLETE_CURRENT_SESSION')
        self.assertNotIn('p_target', r['mc'])

    def test_absent_future_stale_and_after_hours_plan(self):
        self.assertEqual(self.run_forecast(plan=None)['reason'], 'NO_PLAN')
        future = dict(self.plan, signal_time=self.now.isoformat())
        self.assertEqual(self.run_forecast(plan=future)['reason'], 'SIGNAL_NOT_COMPLETED')
        stale = dict(self.plan, signal_time=self.current[0].time.isoformat())
        self.assertEqual(self.run_forecast(plan=stale)['reason'], 'STALE_PLAN')
        r = self.run_forecast(as_of=self.now.replace(hour=15, minute=30))
        self.assertEqual(r['status'], 'UNAVAILABLE')

    def test_historical_fills_are_next_bar_and_costs_are_included(self):
        h = self.run_forecast()['history']
        self.assertEqual(h['status'], 'AVAILABLE')
        self.assertEqual(h['sessions'], 20)
        self.assertTrue(h['trades'])
        for trade in h['trades']:
            self.assertEqual(timestamp(trade['entry_time']) - timestamp(trade['signal_time']), dt.timedelta(minutes=5))
            self.assertLess(trade['net_pnl'], (trade['exit'] - trade['entry']) * trade['qty'])
            self.assertLess(timestamp(trade['entry_time']).date(), self.now.date())

    def test_resizes_untrusted_quantity(self):
        r = self.run_forecast(plan=dict(self.plan, qty=10**10))
        self.assertEqual(r['plan']['qty'], self.plan['qty'])
        self.assertEqual(self.run_forecast(risk=.01)['reason'], 'COST_OR_RISK_LIMIT')

    def test_stop_first_and_gap_loss_exceeds_one_r(self):
        # A wholly synthetic ambiguous candle hits both levels and gaps below
        # stop: preserve upstream conservative precedence but charge gap loss.
        rows = [Bar(self.now + dt.timedelta(minutes=5 * i), 98, 120, 90, 100, 1000) for i in range(72)]
        p = dict(entry=100, stop=99, target=102, qty=1, max_loss_estimate=1)
        r = _simulate([rows], p, 71, 8, 17, 'NSE', None)
        self.assertEqual(r['p_stop'], 1)
        self.assertEqual(r['p_target'], 0)
        self.assertLess(r['mean_net_pnl'], -2)
        self.assertLess(r['avg_R'], -2)

    def test_higher_costs_reduce_simulated_profit_without_changing_paths(self):
        a = self.run_forecast()
        b = self.run_forecast(config={'slippage_bps': 50})
        self.assertEqual(a['mc']['sample_paths'], b['mc']['sample_paths'])
        # Keep quantity fixed to isolate costs rather than changed sizing.
        rows = [Bar(self.now + dt.timedelta(minutes=5 * i), 100, 100, 100, 100, 1000) for i in range(72)]
        p = dict(entry=100, stop=99, target=102, qty=1, max_loss_estimate=1)
        low = _simulate([rows], p, 71, 8, 17, 'NSE', None)
        high = _simulate([rows], p, 71, 8, 17, 'NSE', {'slippage_bps': 50})
        self.assertLess(high['mean_net_pnl'], low['mean_net_pnl'])

    def test_invalid_parameters_and_simulation_budget(self):
        cases = ({'sims': 0}, {'sims': MAX_SIMS + 1}, {'sims': True},
                 {'capital': math.inf}, {'risk': math.nan}, {'tick': 0},
                 {'seed': 1.2}, {'rr': -1}, {'as_of': self.now.replace(tzinfo=None)},
                 {'plan': dict(self.plan, stop=200)},
                 {'plan': dict(self.plan, side='sell')},
                 {'plan': dict(self.plan, target=math.inf)})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_forecast(**kwargs)

    def test_mid_candle_asof_explicitly_anchors_to_completed_close(self):
        r = self.run_forecast(as_of=self.now + dt.timedelta(seconds=30))
        self.assertEqual(r['status'], 'AVAILABLE')
        self.assertEqual(r['mc']['scenario_start'], self.now.isoformat())
        self.assertEqual(r['mc']['completed_close_age_seconds'], 30)
        self.assertEqual(r['mc']['p_target'], self.run_forecast()['mc']['p_target'])

    def test_default_ten_thousand_is_bounded(self):
        started = time.monotonic()
        r = self.run_forecast(sims=10000)
        self.assertEqual(r['mc']['replays'], 10000)
        self.assertEqual(len(r['mc']['sample_paths']), 12)
        self.assertEqual(r['mc']['band_paths'], 1000)
        # Broad bound catches runaway algorithms; not a microbenchmark.
        self.assertLess(time.monotonic() - started, 30)


if __name__ == '__main__':
    unittest.main()
