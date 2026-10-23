"""Failure, causality and reproducibility checks for the paper runtime."""
import copy
from contextlib import closing
import datetime as dt
import fcntl
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from india.__main__ import main
from india.analysis import report
from india.audit import record, replay, load
from india.data import CSVProvider, Bar, timestamp
from india.job import run as run_job
from india.market import Calendar, DEFAULT_CALENDAR, costs, trading_day
from india.operations import health
from india.research import research, metrics

ROOT=Path(__file__).resolve().parents[1]
CSV=ROOT/'tests/fixtures/india/synthetic.csv'
AT=timestamp('2026-10-19T09:35:00+05:30')


def bars():
    return CSVProvider(CSV).history('DEMO.NS','5m')


def days():
    # Synthetic multi-session research fixture, not downloaded/live prices.
    base=[b for b in bars() if b.time.time()<dt.time(15,15)]
    return [Bar(b.time.replace(day=day),b.open,b.high,b.low,b.close,b.volume)
            for day in (19,21,22) for b in base]


def settings(command='analyze'):
    return {'command':command,'symbol':'DEMO.NS','tick':.05,'capital':100000,'risk':500,
            'rr':2.,'cost_config':None,'as_of':AT.isoformat(),'calendar':DEFAULT_CALENDAR.document,
            'train_fraction':.7,'risk_fraction':.005}


class CalendarAndCosts(unittest.TestCase):
    def test_unverified_year_does_not_silently_trade(self):
        with self.assertRaisesRegex(ValueError,'coverage'):
            trading_day(dt.date(2027,1,4))

    def test_explicit_calendar_is_scoped_by_year_and_exchange(self):
        doc={'NSE':{'2027':{'source':'synthetic test calendar, not real holidays',
                           'verified_on':'2026-10-23','holidays':['2027-01-04']}}}
        calendar=Calendar(doc)
        self.assertFalse(trading_day(dt.date(2027,1,4),calendar))
        self.assertTrue(trading_day(dt.date(2027,1,5),calendar))
        with self.assertRaises(ValueError): trading_day(dt.date(2027,1,5),calendar,'BSE')
        with self.assertRaises(ValueError): trading_day(dt.date(2028,1,5),calendar)
        doc['NSE']['2027']['holidays'].clear()
        self.assertFalse(trading_day(dt.date(2027,1,4),calendar))

    def test_calendar_rejects_unattributed_or_wrong_year_data(self):
        for record in ({'holidays':[]},{'source':'test','verified_on':'2026-10-23','holidays':['2028-01-01']}):
            with self.assertRaises(ValueError): Calendar({'NSE':{'2027':record}})

    def test_costs_reject_typo_negative_prices_and_fractional_quantity(self):
        for args in ((100,101,1,'NSE',{'brokerage_cpa':1}),(-1,101,1,'NSE',None),
                     (100,101,.5,'NSE',None),(100,101,1,'UNKNOWN',None)):
            with self.assertRaises(ValueError): costs(*args)


class Research(unittest.TestCase):
    def test_compounding_holdout_and_cost_stress(self):
        r=research(days(),timestamp('2026-10-23T16:00:00+05:30'),'DEMO.NS',.05,100000,500)
        self.assertEqual(r['sessions'],3)
        self.assertEqual([s['slippage_bps'] for s in r['scenarios']],[5.,10.,20.])
        scenario=r['scenarios'][0]
        self.assertLess(scenario['train']['end'],scenario['holdout']['start'])
        train=scenario['train']
        self.assertEqual(train['trades'],2)
        equity=100000
        for trade in train['trade_details']:
            self.assertLessEqual(trade['max_loss_estimate'],min(500,equity*.005))
            equity += trade['net_pnl']
        self.assertAlmostEqual(train['ending_equity'],equity)
        self.assertGreater(train['ending_equity'],100000)
        self.assertLess(r['scenarios'][-1]['train']['net_pnl'],train['net_pnl'])

    def test_holdout_changes_do_not_change_training(self):
        original=days()
        altered=[Bar(b.time,b.open*2,b.high*2,b.low*2,b.close*2,b.volume)
                 if b.time.day==22 else b for b in original]
        a=research(original,timestamp('2026-10-23T16:00:00+05:30'),'DEMO.NS',.05,100000,500)
        b=research(altered,timestamp('2026-10-23T16:00:00+05:30'),'DEMO.NS',.05,100000,500)
        self.assertEqual([s['train'] for s in a['scenarios']],[s['train'] for s in b['scenarios']])

    def test_missing_bars_days_or_insufficient_history_fail(self):
        series=days()
        for value in (series[:72],series[:80]+series[81:],series[:72]+series[144:]):
            with self.assertRaises(ValueError):
                research(value,timestamp('2026-10-23T16:00:00+05:30'),'DEMO.NS',.05,100000,500)

    def test_future_sessions_cannot_enter_evaluation(self):
        with self.assertRaisesRegex(ValueError,'two complete'):
            research(days(),timestamp('2026-10-19T16:00:00+05:30'),'DEMO.NS',.05,100000,500)

    def test_metrics_drawdown_and_empty_trade_set(self):
        trades=[{'exit_bar':'a','net_pnl':100},{'exit_bar':'b','net_pnl':-200}]
        r=metrics(trades,1000)
        self.assertAlmostEqual(r['max_drawdown_fraction'],200/1100)
        self.assertEqual(r['profit_factor'],.5)
        self.assertEqual(r['ending_equity'],900)
        self.assertIsNone(metrics([],1000)['profit_factor'])


class Archive(unittest.TestCase):
    def test_analysis_replays_offline_and_detects_modified_archive(self):
        result=report(bars(),AT,'DEMO.NS',.05,100000,500)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'runs.sqlite'
            saved=record(path,settings(),result,bars())
            with patch('urllib.request.urlopen',side_effect=AssertionError('Replay must remain offline')):
                r=replay(path,saved['run_id'])
            self.assertTrue(r['matches'])
            self.assertTrue(r['same_code'])
            self.assertEqual(r['snapshot_sha256'],saved['snapshot_sha256'])
            with closing(sqlite3.connect(str(path))) as db, db:
                db.execute('UPDATE runs SET payload=? WHERE id=?',('{}',saved['run_id']))
            with self.assertRaisesRegex(ValueError,'integrity'): load(path,saved['run_id'])

    def test_read_does_not_create_database_and_unknown_id_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'absent.sqlite'
            with self.assertRaises(ValueError): load(path,'anything')
            self.assertFalse(path.exists())
            record(path,settings(),report(bars(),AT,'DEMO.NS',.05,100000,500),bars())
            with self.assertRaisesRegex(ValueError,'Unknown'): load(path,'anything')

    def test_cli_record_and_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'runs.sqlite')
            args=['india','analyze','DEMO.NS','--csv',str(CSV),'--as-of',AT.isoformat(),'--tick','.05','--record-db',path]
            with patch('sys.argv',args),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                saved=json.loads(out.getvalue())
            with patch('sys.argv',['india','replay',saved['run_id'],'--db',path]),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                self.assertTrue(json.loads(out.getvalue())['matches'])


class Runtime(unittest.TestCase):
    def test_doctor_fails_on_missing_stale_data(self):
        now=timestamp('2026-10-19T10:00:00+05:30')
        r=health(report(bars()[:4],now,'DEMO.NS',.05,100000,500),now)
        self.assertEqual(r['status'],'FAIL')
        self.assertFalse(r['checks']['fresh_when_strategy_open'])

    def test_doctor_exit_code_and_archive_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'runs.sqlite')
            args=['india','doctor','DEMO.NS','--csv',str(CSV),'--as-of',AT.isoformat(),'--tick','.05','--record-db',path]
            with patch('sys.argv',args),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                result=json.loads(out.getvalue())
            self.assertEqual(result['status'],'PASS')
            self.assertTrue(replay(path,result['run_id'])['matches'])
            args[args.index(AT.isoformat())]='2026-10-19T16:00:00+05:30'
            # Full fixture includes a complete strategy session; stale is
            # expected after hours and must not cause a false health failure.
            with patch('sys.argv',args),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                self.assertEqual(json.loads(out.getvalue())['status'],'PASS')

    def test_explicit_fallback_and_no_implicit_fallback(self):
        from india.nse import NSEProvider
        from india.data import YahooProvider
        args=['india','analyze','DEMO.NS','--provider','nse','--tick','.05','--as-of',AT.isoformat(),'--json-errors']
        with patch.object(NSEProvider,'history',side_effect=ValueError('unavailable')),patch.object(YahooProvider,'history',return_value=bars()) as yahoo:
            with patch('sys.argv',args),patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(main(),1)
                yahoo.assert_not_called()
            with patch('sys.argv',args+['--fallback-provider','yahoo']),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                r=json.loads(out.getvalue())
                self.assertEqual(r['status'],'PAPER_SETUP')
                self.assertEqual([x['provider'] for x in r['provider_attempts']],['nse','yahoo'])

    def test_cost_errors_are_validated_before_network(self):
        with patch('sys.argv',['india','analyze','TCS.NS','--tick','nan','--json-errors']),patch('sys.stdout',new_callable=io.StringIO),patch('urllib.request.urlopen') as net:
            self.assertEqual(main(),1)
            net.assert_not_called()

    def test_yahoo_failure_defaults_to_nse_without_cross_exchange_substitution(self):
        from india.nse import NSEProvider
        from india.data import YahooProvider
        args=['india','analyze','DEMO.NS','--tick','.05','--as-of',AT.isoformat(),'--json-errors']
        with patch.object(YahooProvider,'history',side_effect=ValueError('synthetic Yahoo failure')), patch.object(NSEProvider,'history',return_value=bars()) as nse, patch.object(NSEProvider,'provider_symbol','DEMO-EQ',create=True):
            with patch('sys.argv',args),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                result=json.loads(out.getvalue())
                self.assertIn('NSE',result['source'])
                self.assertEqual([r['provider'] for r in result['provider_attempts']],['yahoo','nse'])
            nse.reset_mock()
            for extra,symbol in ((['--no-fallback'],'DEMO.NS'),([], 'DEMO.BO')):
                request=list(args);request[2]=symbol
                with patch('sys.argv',request+extra),patch('sys.stdout',new_callable=io.StringIO):
                    self.assertEqual(main(),1)
            nse.assert_not_called()


class Job(unittest.TestCase):
    def test_out_of_hours_and_holidays_make_no_requests(self):
        with tempfile.TemporaryDirectory() as temp,patch('subprocess.run') as process:
            config={'symbol':'TCS.NS','tick':.05,'state_dir':temp}
            for at in ('2026-10-19T20:00:00+05:30','2026-10-20T10:00:00+05:30'):
                result,code=run_job(config,timestamp(at))
                self.assertEqual(code,0)
                self.assertEqual(result['status'],'SKIPPED')
            process.assert_not_called()
            self.assertEqual(len((Path(temp)/'paper.jsonl').read_text().splitlines()),2)

    def test_job_is_bounded_and_failures_are_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'symbol':'TCS.NS','tick':.05,'state_dir':temp,'timeout_seconds':1}
            with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('test',1)) as child:
                result,code=run_job(config,AT)
                self.assertEqual(code,1)
                self.assertEqual(result['reason'],'provider_deadline_exceeded')
                self.assertEqual(child.call_args.kwargs['timeout'],1)
            with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps({'status':'PASS'}),'')):
                self.assertEqual(run_job(config,AT)[1],0)

    def test_job_lock_prevents_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp)/'paper.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch('subprocess.run') as process:
                    result,code=run_job({'symbol':'TCS.NS','tick':.05,'state_dir':temp},AT)
                    self.assertEqual(code,75)
                    process.assert_not_called()
