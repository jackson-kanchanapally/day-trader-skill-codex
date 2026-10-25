"""Walk-forward causality, clustered uncertainty and offline CLI contracts."""
import copy
from dataclasses import replace
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from india.audit import digest
from india.data import CSVProvider, IST, timestamp
from india.market import DEFAULT_CALENDAR
from research.__main__ import main
from research.lab import experiment, folds
from research.metrics import enrich, expectancy_interval
from research.promotion import assess
from research.store import CandleStore
from test_research_store import SOURCE, V2, V3, instrument

ROOT = Path(__file__).resolve().parents[1]


def synthetic_sessions(count=6):
    template = CSVProvider(ROOT/'tests/fixtures/india/synthetic.csv').history('DEMO.NS','5m')
    template = [b for b in template if b.time.time() < dt.time(15,15)]
    rows, day = [], dt.date(2026,9,1)
    while len(rows) < count:
        if DEFAULT_CALENDAR.trading_day(day):
            session = [replace(b,time=dt.datetime.combine(day,b.time.time(),IST)) for b in template]
            rows.append((day,session))
        day += dt.timedelta(days=1)
    return rows


def policy(**changes):
    row = dict(min_market_days=1,min_trade_days=1,max_interval_width_r=10,
               max_drawdown_fraction=.5,min_positive_fold_fraction=.5,
               rationale='Synthetic unit-test policy only. Not a statistical adequacy recommendation.')
    row.update(changes)
    return row


class Laboratory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'history.sqlite'
        self.store = CandleStore(self.path)
        self.addCleanup(self.store.close)
        self.days = synthetic_sessions()
        self.bars = [b for _,rows in self.days for b in rows]
        self.store.ingest('DEMO.NS',self.bars,source=SOURCE,observed_at=V2)
        self.store.evidence('instrument',[instrument()],observed_at=V2)

    def run_lab(self, **changes):
        args = dict(start=self.days[0][0],end=self.days[-1][0],as_of=V3,vintage=V2,
                    train_sessions=2,test_sessions=2,bootstrap_samples=50,block_days=1)
        args.update(changes)
        return experiment(self.store,**args)

    def test_folds_are_chronological_nonoverlapping_with_explicit_embargo(self):
        dates = [day for day,_ in self.days]
        windows = folds(dates,2,1,1)
        tests = []
        for window in windows:
            self.assertLess(window['train'][-1],window['embargo'][0])
            self.assertLess(window['embargo'][-1],window['test'][0])
            self.assertFalse(set(window['train']) & set(window['test']))
            tests.extend(window['test'])
        self.assertEqual(len(tests),len(set(tests)))
        for args in ((dates,0,1),(dates,6,1),(list(reversed(dates)),2,1)):
            with self.assertRaises(ValueError): folds(*args)

    def test_report_reuses_engine_is_reproducible_and_cost_stressed(self):
        first = self.run_lab()
        self.assertEqual(first,self.run_lab())
        self.assertEqual([v['slippage_bps'] for v in first['variants']],[5,10,20])
        row = first['variants'][0]['symbols']['DEMO.NS']
        self.assertEqual(len(row['folds']),2)
        self.assertEqual(len(row['trades']),4)
        self.assertEqual(row['trades'][0]['entry_time'][11:16],'09:35')
        self.assertEqual(row['trades'][0]['execution_policy'],'next-available-open-v1')
        self.assertEqual(row['trades'][0]['qty'],171)
        self.assertEqual(row['trades'][0]['sector'],'SYNTHETIC')
        self.assertEqual(row['trades'][0]['regime'],'UNKNOWN')
        self.assertNotIn('return_fraction',row['out_of_sample'])
        self.assertFalse(first['execution_enabled'])
        self.assertEqual(first['report_sha256'],digest({k:v for k,v in first.items() if k != 'report_sha256'}))
        json.dumps(first,allow_nan=False)

    def test_holdout_revisions_do_not_change_earlier_training(self):
        before = self.run_lab()
        changed = [replace(b,open=b.open*2,high=b.high*2,low=b.low*2,close=b.close*2) for b in self.days[2][1]]
        self.store.ingest('DEMO.NS',changed,source=SOURCE,observed_at=V3)
        after = self.run_lab(vintage=V3)
        a = before['variants'][0]['symbols']['DEMO.NS']['folds']
        b = after['variants'][0]['symbols']['DEMO.NS']['folds']
        self.assertEqual(a[0]['train'],b[0]['train'])
        self.assertNotEqual(a[0]['test'],b[0]['test'])
        self.assertEqual(before,self.run_lab(vintage=V2))

    def test_multiple_symbols_remain_independent_not_shared_capital(self):
        self.store.ingest('SECOND.NS',self.bars,source=SOURCE,observed_at=V2)
        self.store.evidence('instrument',[instrument('SECOND.NS')],observed_at=V2)
        report = self.run_lab()
        variant = report['variants'][0]
        self.assertEqual(variant['pooled_trades'],8)
        self.assertEqual(variant['symbols']['SECOND.NS']['trades'][0]['qty'],171)
        self.assertIn('not a portfolio',variant['capital_basis'])

    def test_explicit_universe_gaps_are_not_missing_data_or_survivorship_filters(self):
        self.store.evidence('instrument',[instrument(effective_to=self.days[2][0].isoformat(),published_at='2026-09-01T08:01:00+05:30')],observed_at=V2)
        report = self.run_lab()
        self.assertEqual(report['variants'][0]['symbols']['DEMO.NS']['eligible_sessions'],3)
        self.assertEqual(report['variants'][0]['pooled_trades'],1)

    def test_missing_whole_session_and_missing_candle_fail_closed(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM candles WHERE time LIKE ?', (self.days[2][0].isoformat()+'%',))
        with self.assertRaisesRegex(ValueError,'missing whole'):
            self.run_lab()
        with self.store.db:
            self.store.db.execute('DELETE FROM candles WHERE time=?',(self.bars[0].time.isoformat(),))
        with self.assertRaisesRegex(ValueError,'complete 72-bar'):
            self.run_lab()

    def test_invalid_cost_objects_and_uncompleted_ranges_fail(self):
        with self.assertRaisesRegex(ValueError,'JSON object'):
            self.run_lab(cost_config=[1,2])
        with self.assertRaisesRegex(ValueError,'historical range'):
            self.run_lab(as_of='2026-09-01T09:30:00+05:30')

    def test_rr_sensitivity_is_bounded_and_does_not_select_an_oos_winner(self):
        report = self.run_lab(rr_values=(1.5,2,2.5))
        self.assertEqual(len(report['variants']),9)
        self.assertNotIn('best_strategy',report)
        with self.assertRaisesRegex(ValueError,'1..9'):
            self.run_lab(rr_values=tuple(range(1,11)))

    def test_promotions_are_checklists_and_forward_gate_stays_closed(self):
        report = self.run_lab()
        result = assess(report,'RESEARCH',policy())
        self.assertTrue(result['eligible'],result)
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(result['requested_next_state'],'BACKTESTED')
        result = assess(report,'BACKTESTED',policy())
        self.assertFalse(result['eligible'])  # too few resamples for an uncertainty tail
        result = assess(report,'PAPER_TRADING',policy())
        self.assertFalse(result['eligible'])
        self.assertTrue(any('Forward' in reason for reason in result['reasons']))
        self.assertFalse(assess(report,'RESEARCH',policy(min_market_days=100))['eligible'])
        damaged = copy.deepcopy(report)
        damaged['variants'][0]['pooled_trades'] = 100000
        with self.assertRaisesRegex(ValueError,'intact'):
            assess(damaged,'RESEARCH',policy())
        with self.assertRaises(ValueError):
            assess(report,'RESEARCH',dict(policy(),rationale=''))

    def call(self, args):
        with patch('sys.stdout',new_callable=io.StringIO) as output, patch('urllib.request.urlopen',side_effect=AssertionError('Offline only')):
            code = main(args)
        return code,json.loads(output.getvalue())

    def test_cli_end_to_end_report_immutability_and_gate_exit_code(self):
        report = Path(self.temp.name)/'report.json'
        args = ['walk-forward','--db',str(self.path),'--start',str(self.days[0][0]),'--end',str(self.days[-1][0]),
                '--as-of',V3,'--vintage',V2,'--train-sessions','2','--test-sessions','2',
                '--bootstrap-samples','50','--block-days','1','--out',str(report)]
        code,doc = self.call(args)
        self.assertEqual(code,0,doc)
        saved = report.read_bytes()
        self.assertEqual(json.loads(saved),doc)
        self.assertEqual(self.call(args)[0],1)
        self.assertEqual(report.read_bytes(),saved)
        criteria = Path(self.temp.name)/'policy.json'
        criteria.write_text(json.dumps(policy()))
        code,doc = self.call(['promotion-check','--report',str(report),'--policy',str(criteria),'--state','PAPER_TRADING'])
        self.assertEqual(code,1)
        self.assertFalse(doc['eligible'])

    def test_cli_incremental_csv_ingest_and_evidence(self):
        source = Path(self.temp.name)/'source.json'
        source.write_text(json.dumps(SOURCE))
        database = Path(self.temp.name)/'cli.sqlite'
        args = ['ingest','DEMO.NS','--db',str(database),'--csv',str(ROOT/'tests/fixtures/india/synthetic.csv'),
                '--source',str(source),'--observed-at',V2]
        self.assertEqual(self.call(args)[0],0)
        self.assertTrue(self.call(args)[1]['idempotent'])
        evidence = Path(self.temp.name)/'instruments.json'
        evidence.write_text(json.dumps([instrument()]))
        self.assertEqual(self.call(['evidence','instrument','--file',str(evidence),'--db',str(database),'--observed-at',V2])[1]['written'],1)
        self.assertIsNotNone(self.call(['watermark','DEMO.NS','--db',str(database),'--vintage',V2])[1]['watermark'])


class Uncertainty(unittest.TestCase):
    def trades(self, count=12):
        return [dict(entry_time='2026-09-%02dT09:35:00+05:30' % (i+1),net_r=1 if i%2 else -.5) for i in range(count)]

    def test_day_blocks_keep_same_day_trades_together_and_are_seeded(self):
        trades = self.trades()
        dates = [t['entry_time'][:10] for t in trades]
        a = expectancy_interval(trades,dates,block_days=2)
        self.assertEqual(a,expectancy_interval(trades,dates,block_days=2))
        self.assertEqual(a['status'],'AVAILABLE')
        doubled = expectancy_interval(trades+trades,dates,block_days=2)
        self.assertEqual(a,doubled)  # duplicate correlated trades cannot create fake independence
        self.assertEqual(a['estimate'],.25)
        self.assertLessEqual(a['lower'],a['upper'])

    def test_no_intervals_for_single_day_too_few_blocks_or_tail_draws(self):
        t = self.trades(2)
        dates = [row['entry_time'][:10] for row in t]
        for trades,days,options in ((t[:1],dates,dict(block_days=1)),(t,dates,dict(block_days=2)),
                                    (t,dates,dict(block_days=1,samples=1))):
            self.assertEqual(expectancy_interval(trades,days,**options)['status'],'UNAVAILABLE')
        with self.assertRaises(ValueError):
            expectancy_interval(t,list(reversed(dates)),block_days=1)

    def test_excursions_omit_ambiguous_terminal_bar_extrema(self):
        bars = synthetic_sessions(1)[0][1]
        trade = dict(entry=102.3,exit=99.7,qty=100,max_loss_estimate=280,net_pnl=-280,
                     entry_time=bars[4].time.isoformat(),exit_bar=bars[4].time.isoformat())
        hostile = [replace(bars[4],high=999,low=1)]
        enriched = enrich(trade,hostile,'NSE')
        self.assertEqual(enriched['mfe_r'],0)
        self.assertAlmostEqual(enriched['mae_r'],260/280)
        self.assertEqual(enriched['net_r'],-1)
        self.assertIn('lower bounds',enriched['excursion_method'])
