"""Original-style Indian tool workflows, using only original synthetic fixtures."""
import contextlib
import copy
import csv
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from india.data import Bar, CSVProvider, IST
from india.market import costs, DEFAULT_CALENDAR
from workflow.common import clock, config, digest, read, write
from workflow.data import DirectoryUpstox, MarketData, parse_yahoo, security_master, universe, verify_tick
from workflow.paper import Ledger, budget, one_way_cost, order_hash, risk, ticket
from workflow.research import disclosures, financials, news
from workflow.strategy import entry, historical_replay, plan, scan, timemachine

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT/'tests/fixtures/india/synthetic.csv'
FIX = ROOT/'tests/fixtures/india/workflow'
NOW = clock('2026-10-19T09:35:00+05:30')


class Workflow(unittest.TestCase):
    def setUp(self):
        self.bars = CSVProvider(CSV).history('DEMO.NS', '5m')
        self.cfg = config(budget=100000)
        self.master = security_master(FIX/'security-master.csv', NOW)
        self.signal = entry(self.bars, 'DEMO.NS', NOW, self.cfg, tick=.05, master=self.master)
        self.p = plan(self.signal, NOW, .05, self.cfg, master=self.master)
        self.order = budget(self.p, NOW, self.cfg)['orders'][0]
        self.pf = dict(paper=True, currency='INR', cash=100000, positions=[], realized_pnl=0)
        self.prices = dict(prices={'DEMO.NS': 102.3}, detail={'DEMO.NS': {'time': NOW.isoformat()}})

    def test_original_entry_and_midpoint_exit(self):
        self.assertEqual(self.signal['status'], 'triggered')
        self.assertEqual(self.signal['time'], '09:35')
        self.assertTrue(self.signal['eligible'])
        self.assertEqual(self.p['stop'], 99.95)
        self.assertEqual(self.p['target'], 107)
        self.assertEqual(self.p['tick_verification']['status'], 'VERIFIED')
        self.assertFalse(self.p['execution_enabled'])

    def test_stop_and_target_variants(self):
        for stop in ('or-low', 'or-mid', 'pct:1', 'price:99.5', 'atr:1.5'):
            p = plan(self.signal, NOW, .05, self.cfg, stop, '1.5R', self.bars, master=self.master)
            self.assertLess(p['stop'], p['entry'])
            self.assertGreater(p['target'], p['entry'])
            self.assertAlmostEqual(p['stop']/.05, round(p['stop']/.05))
            self.assertAlmostEqual(p['target']/.05, round(p['target']/.05))
        self.assertEqual(plan(self.signal, NOW, .05, self.cfg, 'or-low', 'price:106')['target'], 106)

    def test_atr_has_no_lookahead(self):
        a = plan(self.signal, NOW, .05, self.cfg, 'atr:1.5', '2R', self.bars)
        b = plan(self.signal, NOW, .05, self.cfg, 'atr:1.5', '2R', self.bars[:4])
        self.assertEqual(a, b)
        with self.assertRaises(ValueError):
            plan(self.signal, NOW, .05, self.cfg, 'atr:1.5', '2R', self.bars[1:])

    def test_level_entry(self):
        r = entry(self.bars, 'DEMO.NS', NOW, self.cfg, setup='level', level=101)
        self.assertEqual(r['status'], 'triggered')
        p = plan(r, NOW, .05, self.cfg, 'pct:1')
        self.assertEqual(p['level'], 101)
        with self.assertRaises(Exception):
            plan(r, NOW, .05, self.cfg)

    def test_missing_stale_and_forming_entries(self):
        self.assertEqual(entry(self.bars[:2]+self.bars[3:], 'DEMO.NS', NOW, self.cfg)['status'], 'no-data')
        self.assertFalse(entry(self.bars, 'DEMO.NS', NOW+dt.timedelta(minutes=10), self.cfg)['eligible'])
        self.assertEqual(entry(self.bars, 'DEMO.NS', NOW-dt.timedelta(minutes=1), self.cfg)['status'], 'waiting')
        self.assertEqual(self.signal, entry(self.bars[:4], 'DEMO.NS', NOW, self.cfg, tick=.05, master=self.master))

    def test_holiday_unknown_year_and_bse(self):
        r = entry(self.bars, 'DEMO.NS', clock('2026-10-20T10:00:00+05:30'), self.cfg)
        self.assertEqual(r['status'], 'no-data')
        with self.assertRaises(ValueError):
            entry(self.bars, 'DEMO.NS', clock('2027-10-19T10:00:00+05:30'), self.cfg)
        self.assertEqual(entry(self.bars, 'DEMO.BO', NOW, self.cfg)['status'], 'triggered')

    def test_scan_preserves_unavailable_symbols(self):
        r = scan(MarketData(NOW, csv_path=CSV), ['DEMO.NS', 'MISSING.NS'], NOW, self.cfg)
        self.assertEqual([h['symbol'] for h in r['hits']], ['DEMO.NS'])
        self.assertEqual(r['failed'][0]['symbol'], 'MISSING.NS')

    def test_daily_setups_reuse_original_rules(self):
        bars = []
        day = dt.date(2026, 7, 1)
        while len(bars) < 40:
            if DEFAULT_CALENDAR.trading_day(day):
                price = 100+len(bars)
                bars.append(Bar(dt.datetime.combine(day, dt.time(9, 15), IST), price, price+1, price-1, price, 1000))
            day += dt.timedelta(days=1)
        class Data:
            source = 'synthetic test'
            def history(self, symbol, interval):
                return bars
        r = scan(Data(), ['DEMO.NS'], NOW, self.cfg, mode='daily')
        self.assertIn('breakout_20d', r['hits'][0]['setups'])

    def test_sizing_and_costs(self):
        doc = budget(self.p, NOW, self.cfg)
        s = doc['sizing']
        self.assertLessEqual(s['max_loss_estimate'], 500)
        self.assertEqual(s['qty'], 195)
        for buy, sell in ((100, 110), (102.3, 99.95)):
            combined = one_way_cost(buy, 100, 'buy', 'NSE')['total']+one_way_cost(sell, 100, 'sell', 'NSE')['total']
            self.assertAlmostEqual(combined, costs(buy, sell, 100)['total'])

    def review(self, **kw):
        args = dict(orders=[self.order], now=NOW, cfg=self.cfg, portfolio=self.pf,
                    prices_doc=self.prices, master=self.master)
        args.update(kw)
        return risk(**args)

    def test_risk_pass_and_tick_gate(self):
        r = self.review()
        self.assertEqual(r['pass'], 1)
        self.assertLessEqual(r['results'][0]['max_loss'], 500)
        self.assertEqual(self.review(master={})['block'], 1)
        bad = copy.deepcopy(self.master)
        bad['DEMO.NS']['tick_size'] = .1
        self.assertEqual(self.review(master=bad)['block'], 1)

    def test_stale_price_and_expired_signal(self):
        q = copy.deepcopy(self.prices)
        q['detail']['DEMO.NS']['time'] = (NOW-dt.timedelta(minutes=16)).isoformat()
        self.assertEqual(self.review(prices_doc=q)['block'], 1)
        self.assertEqual(self.review(now=NOW+dt.timedelta(minutes=6))['block'], 1)

    def test_costs_cannot_be_bypassed_with_gross_risk(self):
        order = copy.deepcopy(self.order)
        order['qty'] = '205'  # gross risk < 500; net stop loss > 500
        r = self.review(orders=[order])
        self.assertEqual(r['block'], 1)
        self.assertTrue(any('costs' in x for x in r['results'][0]['reasons']))

    def test_exact_plan_binding_and_config_change(self):
        review = self.review()
        args = (self.order, review, self.pf, NOW, self.cfg, self.prices, self.master)
        self.assertTrue(ticket(*args)['ready'])
        changed = copy.deepcopy(self.order)
        changed['plan']['stop'] = 90
        self.assertFalse(ticket(changed, *args[1:])['ready'])
        cfg = config(budget=100000, max_loss_per_trade_inr=100)
        self.assertFalse(ticket(self.order, review, self.pf, NOW, cfg, self.prices, self.master)['ready'])

    def test_fractional_and_oversell_rejected(self):
        o = dict(self.order, qty='1.5')
        self.assertEqual(self.review(orders=[o])['block'], 1)
        sell = dict(symbol='DEMO.NS', side='sell', qty='1', type='market', time_in_force='day', tick=.05)
        self.assertEqual(self.review(orders=[sell])['block'], 1)

    def test_us_order_instructions_do_not_clear(self):
        for change in ({'time_in_force': 'gtc'}, {'extended_hours': True}, {'type': 'trailing_stop', 'trail_price': '1'}):
            self.assertEqual(self.review(orders=[dict(self.order, **change)])['block'], 1)

    def test_live_and_us_config_rejected(self):
        for changes in ({'paper': False}, {'broker': 'alpaca'}, {'fractional': True}, {'max_order_usd': 100}, {'allow_margin': True}):
            with self.assertRaises(ValueError):
                config(budget=100000, **changes)

    def test_transactional_paper_fill_and_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'paper.sqlite', 100000)
            self.addCleanup(ledger.close)
            proposal = ticket(self.order, self.review(portfolio=ledger.portfolio()), ledger.portfolio(), NOW, self.cfg, self.prices, self.master)
            ledger.approve(proposal, 'yes', NOW)
            fp = proposal['fingerprint']
            event = ledger.fill(fp, 195, 102.3, NOW, self.cfg, 'test-fill-1')
            self.assertTrue(event['simulation'])
            pf = ledger.portfolio()
            self.assertEqual(pf['positions'][0]['shares'], 195)
            self.assertAlmostEqual(100000-pf['cash'], 195*102.3+event['fees'])
            with self.assertRaises(ValueError):
                ledger.fill(fp, 195, 102.3, NOW, self.cfg, 'test-fill-1')
            self.assertEqual(pf, ledger.portfolio())
            self.assertEqual(self.review(portfolio=pf, state=ledger.state())['block'], 1)

    def test_fill_rollback_and_actual_reply_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'paper.sqlite', 100000)
            self.addCleanup(ledger.close)
            with self.assertRaises(ValueError):
                ledger.fill('not-approved', 1, 100, NOW, self.cfg, 'test-1')
            proposal = ticket(self.order, self.review(portfolio=ledger.portfolio()), ledger.portfolio(), NOW, self.cfg, self.prices, self.master)
            for reply in ('sure', 'yes all', 'yes if it rises', 'yes?', '', 'yes buy TCS.NS'):
                with self.assertRaises(ValueError):
                    ledger.approve(proposal, reply, NOW)
            ledger.approve(proposal, 'yes', NOW)
            before = ledger.portfolio()
            with self.assertRaises(ValueError):
                ledger.fill(proposal['fingerprint'], 195, 106, NOW, self.cfg, 'bad-fill')
            self.assertEqual(before, ledger.portfolio())

    def test_sell_fill_cash_and_realized_pnl(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'paper.sqlite', 100000)
            self.addCleanup(ledger.close)
            proposal = ticket(self.order, self.review(portfolio=ledger.portfolio()), ledger.portfolio(), NOW, self.cfg, self.prices, self.master)
            ledger.approve(proposal, 'yes', NOW)
            buy = ledger.fill(proposal['fingerprint'], 195, 102.3, NOW, self.cfg, 'buy-test')
            pf = ledger.portfolio()
            sell_order = dict(symbol='DEMO.NS', side='sell', qty='195', type='market', time_in_force='day', tick=.05)
            sell_review = self.review(orders=[sell_order], portfolio=pf, state=ledger.state())
            sell_ticket = ticket(sell_order, sell_review, pf, NOW, self.cfg, self.prices, self.master, state=ledger.state())
            self.assertTrue(sell_ticket['ready'], sell_ticket)
            ledger.approve(sell_ticket, 'yes', NOW)
            sell = ledger.fill(sell_ticket['fingerprint'], 195, 103, NOW, self.cfg, 'sell-test')
            final = ledger.portfolio()
            self.assertFalse(final['positions'])
            self.assertAlmostEqual(final['cash']-100000, (103-102.3)*195-buy['fees']-sell['fees'])
            self.assertAlmostEqual(final['realized_pnl'], sell['realized_pnl'])

    def test_approved_ticket_and_initial_capital_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'paper.sqlite'
            ledger = Ledger(path, 100000)
            self.addCleanup(ledger.close)
            pf = ledger.portfolio()
            review = self.review(portfolio=pf)
            proposal = ticket(self.order, review, pf, NOW, self.cfg, self.prices, self.master)
            ledger.approve(proposal, 'yes', NOW)
            self.assertFalse(ticket(self.order, review, pf, NOW, self.cfg, self.prices, self.master, state=ledger.state())['ready'])
            with self.assertRaises(ValueError):
                Ledger(path, 5000)

    def full_days(self, count=20):
        days, day = [], dt.date(2026, 9, 1)
        while len(days) < count:
            if DEFAULT_CALENDAR.trading_day(day):
                opening = dt.datetime.combine(day, dt.time(9, 15), IST)
                rows = [Bar(opening+dt.timedelta(minutes=5*i), 100 if i < 4 else 102.4,
                            100.2 if i < 3 else 102.8, 99.7 if i < 4 else 102,
                            100 if i < 3 else 102.3, 1000 if i < 3 else 2500) for i in range(72)]
                days.append((day, rows))
            day += dt.timedelta(days=1)
        return days

    def test_history_uses_next_bar_not_signal_close(self):
        days = self.full_days(1)
        doc = historical_replay(days, self.p, self.cfg)
        self.assertEqual(doc['triggered'], 1)
        self.assertEqual(doc['trades'][0]['entry'], 102.4)
        self.assertEqual(doc['trades'][0]['entry_time'][11:16], '09:35')

    def test_forecast_prior_sessions_seed_and_coverage(self):
        days = self.full_days()
        bars = [b for _, rows in days for b in rows]+self.bars[:4]
        a = timemachine(bars, self.p, NOW, self.cfg, sims=20)
        b = timemachine(bars+self.bars[4:], self.p, NOW, self.cfg, sims=20)
        self.assertEqual(a, b)
        self.assertEqual(a['status'], 'AVAILABLE', a)
        self.assertEqual(a['mc']['pool_sessions'], 20)
        self.assertEqual(a['mc']['until'], '15:10')
        self.assertTrue(a['mc']['costs_included'])
        self.assertEqual(timemachine(self.bars, self.p, NOW, self.cfg, sims=20)['status'], 'UNAVAILABLE')
        with self.assertRaises(ValueError):
            timemachine(bars, self.p, NOW, self.cfg, sims=0)


class Sources(unittest.TestCase):
    def test_yahoo_failure_automatically_uses_complete_nse_snapshot(self):
        bars = CSVProvider(CSV).history('DEMO.NS', '5m')[:4]
        data = MarketData(NOW)
        with patch.object(MarketData, '_yahoo', side_effect=ValueError('synthetic Yahoo failure')), patch('workflow.data.NSEProvider.history', return_value=bars) as nse:
            self.assertEqual(data.history('DEMO.NS'), bars)
            self.assertEqual(nse.call_count, 1)
        self.assertIn('NSE', data.source)
        self.assertEqual([r['provider'] for r in data.attempts[('DEMO.NS', '5m')]], ['yahoo', 'nse'])

    def test_gapped_yahoo_snapshot_is_replaced_whole_not_patched(self):
        bars = CSVProvider(CSV).history('DEMO.NS', '5m')[:4]
        from dataclasses import replace
        different = [replace(b, open=b.open+10, high=b.high+10, low=b.low+10, close=b.close+10) for b in bars]
        with patch.object(MarketData, '_yahoo', return_value=bars[:2]+bars[3:]), patch('workflow.data.NSEProvider.history', return_value=different):
            self.assertEqual(MarketData(NOW).history('DEMO.NS'), different)

    def test_no_fallback_override_and_bse_never_requests_nse(self):
        with patch.object(MarketData, '_yahoo', side_effect=ValueError('unavailable')), patch('workflow.data.NSEProvider.history') as nse:
            for data, symbol in ((MarketData(NOW, auto_fallback=False), 'DEMO.NS'), (MarketData(NOW), 'DEMO.BO')):
                with self.assertRaises(ValueError):
                    data.history(symbol)
            nse.assert_not_called()

    def test_csv_never_contacts_a_fallback_provider(self):
        with patch('workflow.data.NSEProvider.history') as nse:
            self.assertEqual(len(MarketData(NOW, csv_path=CSV).history('DEMO.NS')), 4)
            nse.assert_not_called()

    def test_multi_symbol_csv_and_future_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bars.csv'
            text = CSV.read_text()
            text += '\n'.join(line.replace('DEMO.NS', 'DEMO.BO') for line in text.splitlines()[1:])+'\n'
            text += 'DEMO.NS,5m,2026-10-19T16:00:00+05:30,bad,bad,bad,bad,bad\n'
            path.write_text(text)
            data = MarketData(NOW, csv_path=path)
            self.assertEqual(len(data.history('DEMO.NS')), 4)
            self.assertEqual(len(data.history('DEMO.BO')), 4)

    def test_tick_units_and_dates(self):
        master = security_master(FIX/'security-master.csv', NOW)
        self.assertEqual(verify_tick('DEMO.NS', .05, master)['status'], 'VERIFIED')
        self.assertEqual(verify_tick('DEMO.NS', .1, master)['status'], 'MISMATCH')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metadata.csv'
            path.write_text((FIX/'security-master.csv').read_text().replace(',INR,INR,', ',INR,paise,'))
            with self.assertRaises(ValueError):
                security_master(path, NOW)
            path.write_text((FIX/'security-master.csv').read_text().replace('2026-09-30T18:00:00', '2026-10-21T18:00:00'))
            with self.assertRaises(ValueError):
                security_master(path, NOW)

    def test_one_minute_provider_does_not_drop_four_of_five_bars(self):
        opening = NOW.replace(hour=9, minute=15)
        rows = [int((opening+dt.timedelta(minutes=i)).timestamp()) for i in range(10)]
        doc = dict(chart=dict(result=[dict(meta=dict(symbol='DEMO.NS', currency='INR', dataGranularity='1m'),
                                          timestamp=rows, indicators=dict(quote=[dict(open=[100]*10, high=[101]*10, low=[99]*10, close=[100]*10, volume=[100]*10)]))]))
        self.assertEqual(len(parse_yahoo(doc, 'DEMO.NS', '1m', NOW)), 10)
        doc['chart']['result'][0]['indicators']['quote'][0]['open'][2] = None
        with self.assertRaises(ValueError):
            parse_yahoo(doc, 'DEMO.NS', '1m', NOW)

    def test_universe_filters_derivatives_and_non_equity(self):
        rows = [dict(segment='NSE_EQ', instrument_type='EQ', lot_size=1, trading_symbol='DEMO'),
                dict(segment='NSE_FO', instrument_type='FUT', lot_size=1, trading_symbol='FUT'),
                dict(segment='NSE_EQ', instrument_type='EQ', lot_size=100, trading_symbol='SME')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'instruments.json'
            write(path, rows)
            self.assertEqual(universe('NSE', path)[0], ['DEMO.NS'])

    def test_scans_reuse_upstox_instrument_directory(self):
        rows = [dict(segment='NSE_EQ', instrument_type='EQ', trading_symbol=symbol, instrument_key='NSE_EQ|'+symbol) for symbol in ('DEMO', 'OTHER')]
        with patch.dict('os.environ', {'UPSTOX_ANALYTICS_TOKEN': 'synthetic-test-token'}), patch('india.upstox.get_json', return_value=rows) as fetch:
            source = DirectoryUpstox(NOW)
            self.assertEqual(source.resolve('DEMO.NS'), 'NSE_EQ%7CDEMO')
            self.assertEqual(source.resolve('OTHER.NS'), 'NSE_EQ%7COTHER')
            self.assertEqual(fetch.call_count, 1)

    def test_news_and_disclosures_deduplicate_and_exclude_future(self):
        doc, state = news(['DEMO.NS'], NOW, {}, offline=FIX)
        self.assertEqual(len(doc['items']), 1)
        self.assertFalse(news(['DEMO.NS'], NOW, state, offline=FIX)[0]['items'])
        doc, state = disclosures(FIX/'disclosures.csv', NOW, {})
        self.assertEqual(len(doc['items']), 1)
        self.assertFalse(disclosures(FIX/'disclosures.csv', NOW, state)[0]['items'])

    def test_financial_units_yoy_and_future_cutoff(self):
        r = financials(FIX/'financials.json', 'DEMO.NS', NOW)
        self.assertEqual(len(r['quarters']), 2)
        self.assertEqual(r['quarters'][-1]['revenue'], 110000000)
        self.assertAlmostEqual(r['quarters'][-1]['revenue_yoy_pct'], 10)
        self.assertIsNone(r['ttm_revenue'])

    def test_bad_financial_basis_or_cumulative_data_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'facts.json'
            doc = read(FIX/'financials.json')
            doc['quarters'][0]['period_start'] = '2025-04-01'
            write(path, doc)
            with self.assertRaises(ValueError):
                financials(path, 'DEMO.NS', NOW)


class Commands(unittest.TestCase):
    def call(self, role, command, workspace, extra=()):
        filename = dict(scanner='scan', entry='entry', exit='exit', budget='size', risk='check', broker='ticket', alerts='alerts')[role]
        args = [sys.executable, str(ROOT/('trader-'+role)/(filename+'.py')), *command,
                '--workspace', str(workspace), '--now', NOW.isoformat(), *extra]
        result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return json.loads(result.stdout)

    def test_original_command_handoffs_to_ready_paper_ticket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shared = ['--csv', str(CSV), '--security-master', str(FIX/'security-master.csv'), '--tick', '.05']
            self.call('scanner', ['orb', '--symbols', 'DEMO.NS'], path, shared)
            self.call('entry', ['check', 'DEMO.NS', '--write'], path, shared)
            self.call('exit', ['plan', '--write'], path, shared)
            self.call('budget', ['--write'], path, ['--capital', '100000', '--risk', '500'])
            extra = shared+['--capital', '100000', '--risk', '500']
            self.assertEqual(self.call('risk', [], path, extra)['pass'], 1)
            self.assertEqual(self.call('broker', ['show'], path, extra)['status'], 'READY_PAPER_TICKET')
            self.assertTrue((path/'state/paper.sqlite').exists())
            self.assertFalse(any(e['status'] == 'placed' for e in read(path/'state/broker.json', {'orders': []})['orders']))

    def test_persistent_alert_rings_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.call('alerts', ['add', '--symbol', 'DEMO.NS', '--above', '101', '--id', 'test-level'], path)
            first = self.call('alerts', ['check'], path, ['--csv', str(CSV)])
            second = self.call('alerts', ['check'], path, ['--csv', str(CSV)])
            self.assertEqual(len(first['rings']), 1)
            self.assertFalse(second['rings'])

    def test_portable_original_tool_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT/'trader-entry/entry.py'), 'check', 'DEMO.NS',
                                     '--csv', str(CSV), '--now', NOW.isoformat(), '--workspace', directory],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'triggered')

    def test_invalid_stop_is_a_structured_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            self.call('entry', ['check', 'DEMO.NS', '--write'], directory, ['--csv', str(CSV), '--tick', '.05'])
            result = subprocess.run([sys.executable, str(ROOT/'trader-exit/exit.py'), 'plan', '--stop', 'bad-stop',
                                     '--workspace', directory, '--tick', '.05', '--now', NOW.isoformat()],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'UNAVAILABLE')
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
