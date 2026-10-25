"""Original synthetic golden cases, captured before execution consolidation.

All mutated candles are invented test inputs derived from synthetic.csv.
"""
import datetime as dt
from dataclasses import replace
from pathlib import Path
import unittest

from india.analysis import backtest, proposal, report, size
from india.data import CSVProvider, timestamp
from india.market import costs, session
from india.execution import size_position
from workflow.common import config
from workflow.strategy import entry

CSV = Path(__file__).parent/'fixtures/india/synthetic.csv'
NOW = timestamp('2026-10-19T09:35:00+05:30')


class Golden(unittest.TestCase):
    def setUp(self):
        self.bars = CSVProvider(CSV).history('DEMO.NS', '5m')
        self.cfg = config(budget=100000)

    def test_opening_range_and_no_trade(self):
        p = proposal(self.bars[:4], .05, 2)
        self.assertEqual(p['entry'], 102.3)
        self.assertEqual(p['stop'], 99.7)
        self.assertEqual(p['target'], 107.5)
        self.assertIsNone(proposal(self.bars[:3], .05, 2))
        self.assertEqual(entry(self.bars[:4], 'DEMO.NS', NOW, self.cfg)['status'], 'triggered')

    def test_false_breakout_wick_does_not_confirm(self):
        rows = self.bars[:3]+[replace(self.bars[3], close=100.1, low=100)]
        self.assertIsNone(proposal(rows, .05, 2))
        self.assertEqual(entry(rows, 'DEMO.NS', NOW, self.cfg)['status'], 'waiting')

    def test_missing_and_stale(self):
        r = report(self.bars[:2]+self.bars[3:], NOW, 'DEMO.NS', .05, 100000, 500)
        self.assertEqual(r['data_quality']['missing_bars'], ['2026-10-19T09:25:00+05:30'])
        self.assertEqual(r['status'], 'BLOCKED_INCOMPLETE_DATA')
        self.assertIsNone(r['plan'])
        r = report(self.bars[:4], NOW+dt.timedelta(minutes=25), 'DEMO.NS', .05, 100000, 500)
        self.assertTrue(r['stale'])
        self.assertIsNone(r['plan'])

    def replay(self, replacement):
        return backtest(self.bars[:4]+[replacement], 'DEMO.NS', .05, 100000, 500)['trades']

    def test_gap_through_entry_levels_is_rejected(self):
        for row in (replace(self.bars[4], open=110, high=111, low=109, close=110),
                    replace(self.bars[4], open=98, high=99, low=97, close=98)):
            self.assertEqual(self.replay(row), [])

    def test_same_bar_stop_and_target_uses_stop(self):
        row = replace(self.bars[4], open=102.3, high=110, low=99, close=103)
        trade = self.replay(row)[0]
        self.assertEqual(trade['exit'], 99.7)
        self.assertEqual(trade['entry_time'], NOW.isoformat())

    def test_gap_through_stop(self):
        rows = self.bars[:5]+[replace(self.bars[5], open=98, high=99, low=97, close=98)]
        self.assertEqual(backtest(rows, 'DEMO.NS', .05, 100000, 500)['trades'][0]['exit'], 98)

    def test_incomplete_history_cannot_invent_exit(self):
        self.assertEqual(backtest(self.bars[:5], 'DEMO.NS', .05, 100000, 500)['trades'], [])

    def test_sizing_and_transaction_costs(self):
        p = dict(entry=102.3, stop=99.95, target=107)
        # Preserve the captured 195-share baseline with the reserve disabled.
        s = size_position(p, 75000, 500, 'NSE', None, buffer_bps=0)
        self.assertEqual(size(p, 75000, 500, 'NSE', None)['qty'], 191)
        self.assertEqual(s['qty'], 195)
        self.assertAlmostEqual(s['max_loss_estimate'], 498.87695588249886)
        self.assertAlmostEqual(costs(100, 110, 100)['total'], 21.769526)

    def test_session_boundaries(self):
        for at, expected in (('09:14', False), ('09:15', True), ('15:29', True), ('15:30', False)):
            self.assertEqual(session(timestamp('2026-10-19T'+at+':00+05:30')), expected)
        self.assertFalse(session(timestamp('2026-10-20T09:35:00+05:30')))
