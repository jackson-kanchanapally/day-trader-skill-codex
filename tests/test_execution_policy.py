"""Shared execution parity using original synthetic events, without providers."""
import datetime as dt
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from india.analysis import backtest
from india.data import CSVProvider, timestamp, validate
from india.execution import entry_fill, exit_fill, replay_trade, size_position, tick_price
from workflow.common import config
from workflow.data import security_master
from workflow.paper import Ledger, budget, risk, ticket
from workflow.strategy import entry, historical_replay, plan

ROOT = Path(__file__).resolve().parents[1]
NOW = timestamp('2026-10-19T09:35:00+05:30')


class ExecutionPolicy(unittest.TestCase):
    def setUp(self):
        self.bars = CSVProvider(ROOT/'tests/fixtures/india/synthetic.csv').history('DEMO.NS', '5m')
        self.cfg = config(budget=100000, cash_buffer_pct=0, max_position_pct=100)
        self.master = security_master(ROOT/'tests/fixtures/india/workflow/security-master.csv', NOW)
        self.p = plan(entry(self.bars, 'DEMO.NS', NOW, self.cfg), NOW, .05, self.cfg, 'or-low')
        self.available = 100000

    def replay(self, rows):
        return replay_trade(self.p, rows, self.available, 500, 'NSE')

    def test_two_public_replay_adapters_match_identical_rules_and_events(self):
        original = self.bars
        variants = [original, original[:5],
                    original[:4]+[replace(original[4], open=102.3, high=110, low=99, close=103)],
                    original[:5]+[replace(original[5], open=98, high=99, low=97, close=98)],
                    original[:4]+[replace(original[4], open=110, high=111, low=109, close=110)]]
        keys = ('entry', 'stop', 'target', 'qty', 'entry_time', 'exit', 'exit_bar', 'outcome', 'net_pnl',
                'signal_time', 'signal_confirmed_at', 'submitted_at', 'execution_policy', 'estimated_entry')
        for bars in variants:
            with self.subTest(last=bars[-1]):
                a = backtest(bars, 'DEMO.NS', .05, self.available, 500)['trades']
                b = historical_replay([(NOW.date(), bars)], self.p, self.cfg)['trades']
                self.assertEqual([{k: t[k] for k in keys} for t in a], [{k: t[k] for k in keys} for t in b])

    def test_estimate_precedes_fill_and_no_hindsight_resizing(self):
        sized = size_position(self.p, self.available, 500, 'NSE')
        self.assertEqual(sized['estimated_entry'], 102.4)
        self.assertLess(sized['qty'], size_position(self.p, self.available, 500, 'NSE', buffer_bps=0)['qty'])
        gap = replace(self.bars[4], open=103, high=110, low=102, close=104)
        self.assertIsNone(self.replay([gap]))
        favorable = replace(self.bars[4], open=102, high=110, low=101, close=104)
        self.assertEqual(self.replay([favorable])['qty'], sized['qty'])

    def test_submission_cannot_fill_a_past_open_or_signal_close(self):
        with self.assertRaisesRegex(ValueError, 'submission'):
            entry_fill(self.p, self.bars[3], NOW)
        with self.assertRaisesRegex(ValueError, 'submission'):
            entry_fill(self.p, self.bars[4], NOW+dt.timedelta(seconds=1))
        fill = entry_fill(self.p, self.bars[5], NOW+dt.timedelta(seconds=1))
        self.assertEqual(fill['entry_time'], self.bars[5].time.isoformat())
        self.assertEqual(fill['signal_confirmed_at'], NOW.isoformat())

    def test_delayed_replay_submission_uses_same_open_as_paper_policy(self):
        submitted = NOW+dt.timedelta(seconds=1)
        fill = entry_fill(self.p, self.bars[5], submitted)
        replay = replay_trade(self.p, self.bars[4:], self.available, 500, 'NSE', submitted_at=submitted)
        self.assertEqual(replay['entry'], fill['entry'])
        self.assertEqual(replay['entry_time'], fill['entry_time'])
        with self.assertRaisesRegex(ValueError, 'first available'):
            entry_fill(self.p, self.bars[5], NOW)

    def test_ambiguity_gap_and_flatten(self):
        ambiguous = replace(self.bars[4], open=102.3, high=110, low=98, close=103)
        self.assertEqual(exit_fill(self.p, ambiguous)['exit'], self.p['stop'])
        gap = replace(ambiguous, open=98)
        self.assertEqual(exit_fill(self.p, gap)['exit'], 98)
        target_gap = replace(ambiguous, open=108)
        self.assertEqual(exit_fill(self.p, target_gap)['exit'], self.p['target'])
        flat = replace(self.bars[4], time=NOW.replace(hour=15, minute=5))
        self.assertEqual(exit_fill(self.p, flat)['outcome'], 'close')
        self.assertIsNone(self.replay(self.bars[4:5]))
        self.assertIsNone(self.replay([replace(self.bars[4], volume=0)]))
        self.assertIsNone(self.replay([self.bars[4], self.bars[6]]))

    def test_float_tick_noise_and_invalid_sizing(self):
        self.assertEqual(tick_price(102.30000000000001, .05, True), 102.3)
        self.assertEqual(tick_price(102.301, .05, True), 102.35)
        with self.assertRaises(ValueError):
            size_position(dict(self.p, stop=103), self.available, 500, 'NSE')

    def test_paper_round_trip_matches_replay_after_costs(self):
        expected = self.replay(self.bars[4:])
        order = budget(self.p, NOW, self.cfg)['orders'][0]
        q = int(order['qty'])
        prices = dict(prices={'DEMO.NS': 102.3}, detail={'DEMO.NS': {'time': NOW.isoformat()}})
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'paper.sqlite', self.available)
            self.addCleanup(ledger.close)
            pf = ledger.portfolio()
            check = risk([order], NOW, self.cfg, pf, prices, master=self.master)
            proposal = ticket(order, check, pf, NOW, self.cfg, prices, self.master)
            ledger.approve(proposal, 'yes', NOW)
            fp = proposal['fingerprint']
            with self.assertRaisesRegex(ValueError, 'observed fill candle'):
                ledger.fill(fp, q, 102.3, NOW, self.cfg, 'missing-candle')
            with self.assertRaisesRegex(ValueError, 'canonical'):
                ledger.fill(fp, q, 102.35, NOW, self.cfg, 'wrong-price', bar=self.bars[4])
            self.assertEqual(ledger.portfolio(), pf)
            adverse = replace(self.bars[4], open=103, high=104, low=102, close=103)
            with self.assertRaisesRegex(ValueError, 'risk limits'):
                ledger.fill(fp, q, 103, NOW, self.cfg, 'adverse', bar=adverse)
            self.assertEqual(ledger.portfolio(), pf)
            buy = ledger.fill(fp, q, expected['entry'], NOW, self.cfg, 'buy', bar=self.bars[4])
            self.assertEqual(buy['entry_time'], expected['entry_time'])
            self.assertEqual(buy['qty'], expected['qty'])
            # Explicit approved manual exit at the shared engine's modeled price.
            at = timestamp(expected['exit_bar'])+dt.timedelta(minutes=5)
            sell_order = dict(symbol='DEMO.NS', side='sell', qty=str(q), type='market', time_in_force='day', tick=.05)
            prices = dict(prices={'DEMO.NS': expected['exit']}, detail={'DEMO.NS': {'time': at.isoformat()}})
            pf = ledger.portfolio()
            check = risk([sell_order], at, self.cfg, pf, prices, master=self.master)
            proposal = ticket(sell_order, check, pf, at, self.cfg, prices, self.master)
            ledger.approve(proposal, 'yes', at)
            sold = ledger.fill(proposal['fingerprint'], q, expected['exit'], at, self.cfg, 'sell')
            self.assertAlmostEqual(sold['realized_pnl'], expected['net_pnl'])
            self.assertAlmostEqual(ledger.portfolio()['cash']-self.available, expected['net_pnl'])

    def test_invalid_candles_order_duplicates_and_timezone(self):
        for bars in ([self.bars[0], self.bars[0]], list(reversed(self.bars[:2])),
                     [replace(self.bars[0], high=99)], [replace(self.bars[0], volume=-1)],
                     [replace(self.bars[0], close=float('nan'))], [replace(self.bars[0], close=True)],
                     [replace(self.bars[0], time=NOW.replace(tzinfo=None))]):
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                validate(bars)
        off_grid = [replace(b, time=b.time+dt.timedelta(seconds=1)) for b in self.bars]
        with self.assertRaisesRegex(ValueError, 'Off-grid'):
            backtest(off_grid, 'DEMO.NS', .05, self.available, 500)

    def test_entry_expiry_uses_exact_seconds(self):
        doc = entry(self.bars, 'DEMO.NS', NOW+dt.timedelta(minutes=5, seconds=1), self.cfg)
        self.assertEqual(doc['status'], 'triggered')
        self.assertFalse(doc['eligible'])
        self.assertEqual(doc['signal_confirmed_at'], NOW.isoformat())

    def test_flatten_clock_must_match_candle_grid(self):
        with self.assertRaisesRegex(ValueError, 'align'):
            plan(entry(self.bars, 'DEMO.NS', NOW, self.cfg), NOW, .05, self.cfg, flat_by='15:08')
        with self.assertRaisesRegex(ValueError, 'align'):
            entry_fill(dict(self.p, flat_by='15:08'), self.bars[4], NOW)

    def test_unsupported_config_rejected_even_without_loader(self):
        for key in ('allow_short', 'allow_margin'):
            for value in (True, 'false'):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                    budget(self.p, NOW, dict(self.cfg, **{key: value}))
