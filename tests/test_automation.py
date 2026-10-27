"""Automatic paper trading on invented candles; no live accounts or consent text."""
import copy
import fcntl
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
import queue
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from automation.engine import PaperEngine
from automation.policy import checked
from automation.selection import rank_entries,shortlist
from automation.service import Scanner
from automation.service import run as run_worker
from automation.sources import normalize_ticks
from automation.capital import request_change,pending_change,apply_when_flat
from automation.view import snapshot,set_entries
from dashboard.journal import paper_journal
from dashboard.__main__ import handler
from india.data import Bar,CSVProvider
from india.market import DEFAULT_CALENDAR
from workflow.common import clock,read,write,config
from workflow.data import security_master

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/india'
NOW=clock('2026-10-19T09:35:00+05:30')


class Data:
    provider='synthetic'
    csv_path=None
    source='Original invented automation candles'
    def __init__(self,when,bars,daily=None):
        self.when,self.bars,self.daily=when,bars,daily or {}
        self._cache,self.sources,self.attempts={},{},{}
        self.fetch_metrics={}
    def history(self,symbol,interval='5m'):
        if interval=='1d':
            if symbol not in self.daily: raise ValueError('Synthetic unavailable symbol')
            rows=self.daily[symbol]
        else:
            rows=[b for b in self.bars if b.time+dt.timedelta(minutes=5)<=self.when]
        self._cache[(symbol,interval)]=rows
        self.sources[(symbol,interval)]=self.source
        return rows
    def prefetch(self,names,interval='5m',workers=4,**kwargs):
        for name in names:
            try:self.history(name,interval)
            except ValueError:pass
        return {}


class AutomaticPaper(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.settings=checked(dict(selection=dict(min_price=0,min_daily_turnover_inr=0,
            min_session_turnover_inr=0,min_volume_x=0,min_score=0,max_participation=.1)))
        self.master=security_master(FIX/'workflow/security-master.csv',NOW)
        self.bars=[replace(b,volume=100000) for b in CSVProvider(FIX/'synthetic.csv').history('DEMO.NS','5m')]
        self.engine=PaperEngine(self.root,self.settings)
        self.addCleanup(lambda:self.engine.close() if self.engine else None)
        self.candidates=[dict(symbol='DEMO.NS',score=70,daily_setups=['breakout_20d'])]
        write(self.root/'automation/settings.json',self.settings)

    def enter(self,at=NOW):
        doc=self.engine.consider(self.candidates,Data(at,self.bars),self.master,at)
        self.assertEqual(doc['status'],'PENDING_ENTRY',self.engine.ledger.events())
        self.engine.manage(Data(at+dt.timedelta(minutes=5),self.bars),self.master,at+dt.timedelta(minutes=5))
        self.assertTrue(self.engine.ledger.portfolio()['positions'])
        return self.engine.state['positions']['DEMO.NS']

    def test_standing_policy_records_observed_entry_and_reconciled_journal(self):
        position=self.enter()
        approval=next(e for e in self.engine.ledger.events() if e.get('status')=='approved')
        self.assertIsNone(approval['reply'])
        self.assertEqual(approval['authorization']['kind'],'STANDING_PAPER_AUTOMATION_POLICY')
        fill=next(e for e in self.engine.ledger.events() if e.get('status')=='placed')
        self.assertEqual(fill['entry_time'],'2026-10-19T09:35:00+05:30')
        self.assertEqual(fill['observed_at'],'2026-10-19T09:40:00+05:30')
        self.assertLessEqual(fill['max_loss'],500)
        journal=paper_journal(self.root/'automation')
        self.assertEqual(journal['status'],'RECORDED')
        self.assertEqual(len(journal['entries']),1)
        self.assertFalse(journal['execution_enabled'])

    def test_submission_after_boundary_uses_next_open_not_a_past_signal_close(self):
        at=NOW+dt.timedelta(seconds=20)
        decision=self.engine.consider(self.candidates,Data(at,self.bars),self.master,at)
        self.assertEqual(decision['status'],'PENDING_ENTRY')
        self.engine.manage(Data(NOW+dt.timedelta(minutes=5),self.bars),self.master,NOW+dt.timedelta(minutes=5))
        self.assertFalse(self.engine.ledger.portfolio()['positions'])
        self.engine.manage(Data(NOW+dt.timedelta(minutes=10),self.bars),self.master,NOW+dt.timedelta(minutes=10))
        fill=next(e for e in self.engine.ledger.events() if e.get('status')=='placed')
        self.assertEqual(fill['entry_time'],'2026-10-19T09:40:00+05:30')

    def test_stop_wins_ambiguous_bar_and_costs_reconcile(self):
        position=self.enter()
        bar=replace(self.bars[5],high=position['plan']['target']+1,low=position['plan']['stop']-1)
        rows=self.bars[:5]+[bar]+self.bars[6:]
        at=NOW+dt.timedelta(minutes=10)
        self.engine.manage(Data(at,rows),self.master,at)
        journal=paper_journal(self.root/'automation')
        self.assertEqual(journal['status'],'RECORDED')
        self.assertFalse(journal['positions'])
        self.assertEqual(journal['exits'][0]['outcome'],'stop')
        self.assertLess(journal['realized_pnl'],0)
        self.assertAlmostEqual(journal['cash'],100000+journal['realized_pnl'])
        self.assertGreater(journal['total_costs'],0)

    def test_target_gap_uses_planned_target_and_end_of_day_is_automatic(self):
        position=self.enter()
        bar=replace(self.bars[5],open=position['plan']['target']+1,high=position['plan']['target']+2,
                    low=position['plan']['target'],close=position['plan']['target']+1)
        at=NOW+dt.timedelta(minutes=10)
        self.engine.manage(Data(at,self.bars[:5]+[bar]),self.master,at)
        result=paper_journal(self.root/'automation')
        self.assertEqual(result['exits'][0]['price'],position['plan']['target'])
        self.assertGreater(result['realized_pnl'],0)

    def test_flatten_and_missing_data_liquidation_do_not_assume_a_target(self):
        position=self.enter()
        stable=[replace(b,open=102.3,high=102.5,low=102,close=102.3) for b in self.bars]
        at=clock('2026-10-19T15:10:00+05:30')
        self.engine.manage(Data(at,stable),self.master,at)
        result=paper_journal(self.root/'automation')
        self.assertEqual(result['status'],'RECORDED')
        self.assertEqual(result['exits'][0]['outcome'],'close')
        self.assertFalse(result['positions'])

    def test_missing_bar_exits_at_next_observed_open(self):
        self.enter()
        at=NOW+dt.timedelta(minutes=15)
        self.engine.manage(Data(at,self.bars[:5]+self.bars[6:]),self.master,at)
        result=paper_journal(self.root/'automation')
        self.assertEqual(result['exits'][0]['outcome'],'data_gap_exit')
        self.assertEqual(result['exits'][0]['price'],102.5)

    def test_overnight_exposure_exits_at_next_observed_open_with_current_ticks(self):
        self.enter()
        at=NOW+dt.timedelta(days=1)
        while not DEFAULT_CALENDAR.trading_day(at.date()):
            at+=dt.timedelta(days=1)
        bar=replace(self.bars[0],time=at.replace(hour=9,minute=15),open=102,high=103,low=101,close=102)
        at=bar.time+dt.timedelta(minutes=5)
        master={symbol:dict(row,effective_from=at.date().isoformat(),effective_to=at.date().isoformat(),
                            published_at=bar.time.isoformat()) for symbol,row in self.master.items()}
        self.engine.manage(Data(at,[bar]),master,at)
        result=paper_journal(self.root/'automation')
        self.assertEqual(result['status'],'RECORDED')
        self.assertFalse(result['positions'])
        self.assertEqual(result['exits'][0]['outcome'],'late_flatten')
        self.assertEqual(result['exits'][0]['price'],102)

    def test_late_and_adverse_entry_candles_cancel_without_resizing(self):
        self.engine.consider(self.candidates,Data(NOW,self.bars),self.master,NOW)
        delayed=NOW+dt.timedelta(minutes=8)
        self.engine.manage(Data(delayed,self.bars),self.master,delayed)
        self.assertFalse(self.engine.ledger.portfolio()['positions'])
        self.assertIsNone(self.engine.state['pending'])
        self.assertFalse(any(e.get('status')=='placed' for e in self.engine.ledger.events()))

    def test_adverse_fill_gap_cannot_resize_or_exceed_per_trade_risk(self):
        self.engine.consider(self.candidates,Data(NOW,self.bars),self.master,NOW)
        p=self.engine.state['pending']['plan']
        bad=replace(self.bars[4],open=p['entry']+2,high=p['entry']+3,low=p['entry']+1,close=p['entry']+2)
        self.engine.manage(Data(NOW+dt.timedelta(minutes=5),self.bars[:4]+[bad]),self.master,NOW+dt.timedelta(minutes=5))
        self.assertFalse(self.engine.ledger.portfolio()['positions'])
        self.assertIsNone(self.engine.state['pending'])

    def test_worker_lock_prevents_duplicate_automated_workers(self):
        path=self.root/'automation/worker.lock'
        with path.open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'already running'):
                run_worker(self.root,once=True,now=lambda:NOW)

    def test_capital_reduction_waits_for_existing_exposure_and_cancels_pending_entry(self):
        self.engine.consider(self.candidates,Data(NOW,self.bars),self.master,NOW)
        request_change(self.root,5000,500,NOW)
        self.engine.settings=read(self.root/'automation/settings.json')
        self.engine.cfg=config(**self.engine.settings['risk'])
        self.assertIsNone(apply_when_flat(self.root,self.engine,NOW))
        self.engine.manage(Data(NOW,self.bars),self.master,NOW)
        self.assertIsNone(self.engine.state['pending'])
        result=apply_when_flat(self.root,self.engine,NOW)
        self.assertEqual(result['risk']['budget'],5000)
        self.assertEqual(self.engine.ledger.portfolio()['cash'],5000)
        self.assertFalse(any(e.get('status')=='placed' for e in self.engine.ledger.events()))

    def test_capital_change_preserves_exits_history_costs_and_performance(self):
        position=self.enter()
        self.engine.record_equity(NOW+dt.timedelta(minutes=5))
        request_change(self.root,5000,500,NOW+dt.timedelta(minutes=5))
        request=pending_change(self.root)
        with self.assertRaisesRegex(ValueError,'positions close'):
            self.engine.ledger.change_capital(request,NOW+dt.timedelta(minutes=5))
        self.assertIsNone(apply_when_flat(self.root,self.engine,NOW+dt.timedelta(minutes=5)))
        self.engine.settings=read(self.root/'automation/settings.json')
        self.engine.cfg=config(**self.engine.settings['risk'])
        at=NOW+dt.timedelta(minutes=10)
        bar=replace(self.bars[5],low=position['plan']['stop']-1)
        self.engine.manage(Data(at,self.bars[:5]+[bar]),self.master,at)
        settings=apply_when_flat(self.root,self.engine,at)
        self.assertTrue(settings['enabled'])
        self.assertEqual(settings['risk']['max_order_inr'],1250)
        self.engine.record_equity(at)
        result=paper_journal(self.root/'automation')
        self.assertEqual(result['status'],'RECORDED')
        self.assertEqual(len(result['entries']),1);self.assertEqual(len(result['exits']),1)
        self.assertEqual(result['initial_capital'],100000)
        self.assertEqual(result['funded_capital'],5000)
        self.assertAlmostEqual(result['cash'],5000+result['realized_pnl'])
        self.assertEqual(result['net_capital_flows'],-95000)
        view=snapshot(self.root,at)
        self.assertLess(view['performance']['max_drawdown_pct'],1)
        self.assertGreater(view['performance']['return_pct'],-1)
        self.assertIsNone(pending_change(self.root))
        self.assertIsNone(apply_when_flat(self.root,self.engine,at))
        self.engine.close();self.engine=PaperEngine(self.root,settings)
        self.assertEqual(len([e for e in self.engine.ledger.events() if e.get('status')=='capital_adjusted']),1)

    def test_restart_recovers_capital_commit_before_settings_checkpoint(self):
        request_change(self.root,5000,500,NOW)
        request=pending_change(self.root)
        self.engine.ledger.change_capital(request,NOW)
        self.engine.close();self.engine=None
        self.assertEqual(read(self.root/'automation/settings.json')['risk']['budget'],100000)
        run_worker(self.root,once=True,now=lambda:NOW.replace(hour=16))
        self.assertEqual(read(self.root/'automation/settings.json')['risk']['budget'],5000)
        self.assertIsNone(pending_change(self.root))
        self.assertEqual(paper_journal(self.root/'automation')['funded_capital'],5000)

    def test_altered_funding_event_fails_journal_reconciliation(self):
        request_change(self.root,5000,500,NOW)
        apply_when_flat(self.root,self.engine,NOW)
        row=self.engine.ledger.db.execute("SELECT id,document FROM events WHERE id LIKE 'capital-change:%'").fetchone()
        event=json.loads(row[1]);event['amount']=-94000
        with self.engine.ledger.db:
            self.engine.ledger.db.execute('UPDATE events SET document=? WHERE id=?',(json.dumps(event),row[0]))
        self.assertEqual(paper_journal(self.root/'automation')['status'],'UNAVAILABLE')

    def test_invalid_capital_request_does_not_modify_settings(self):
        before=read(self.root/'automation/settings.json')
        with self.assertRaises(ValueError):request_change(self.root,100,500,NOW)
        self.assertEqual(read(self.root/'automation/settings.json'),before)
        self.assertIsNone(pending_change(self.root))

    def test_restart_recovers_a_committed_position_without_duplicating_fills(self):
        self.enter()
        with self.engine.ledger.db:
            self.engine.ledger.db.execute('DELETE FROM automation_state')
        self.engine.close();self.engine=None
        self.engine=PaperEngine(self.root,self.settings)
        at=NOW+dt.timedelta(minutes=5)
        self.engine.manage(Data(at,self.bars),self.master,at)
        self.assertEqual(len([e for e in self.engine.ledger.events() if e.get('status')=='placed']),1)
        self.assertEqual(len(self.engine.ledger.portfolio()['positions']),1)

    def test_worker_restart_with_invalid_settings_still_closes_protected_position(self):
        position=self.enter()
        write(self.root/'automation/validated-settings.json',self.settings)
        write(self.root/'automation/metadata.json',dict(securities=list(self.master.values())))
        write(self.root/'automation/settings.json',None)
        bar=replace(self.bars[5],low=position['plan']['stop']-1)
        rows=self.bars[:5]+[bar]
        class Sources:
            def __init__(self,*args):pass
            def market(self,cutoff):return Data(cutoff,rows)
        at=NOW+dt.timedelta(minutes=10)
        run_worker(self.root,once=True,now=lambda:at,source_factory=Sources)
        result=paper_journal(self.root/'automation')
        self.assertEqual(result['status'],'RECORDED')
        self.assertFalse(result['positions'])
        self.assertEqual(result['exits'][0]['outcome'],'stop')

    def test_pause_cancels_pending_but_keeps_protective_exits(self):
        position=self.enter()
        self.engine.settings=dict(self.settings,enabled=False)
        bar=replace(self.bars[5],low=position['plan']['stop']-1)
        at=NOW+dt.timedelta(minutes=10)
        self.engine.manage(Data(at,self.bars[:5]+[bar]),self.master,at)
        self.assertFalse(self.engine.ledger.portfolio()['positions'])
        self.assertIsNone(self.engine.consider(self.candidates,Data(at,self.bars),self.master,at))

    def test_protective_exit_precedes_and_invalidates_a_pending_new_entry(self):
        position=self.enter()
        other='DEMO.B.NS'
        master=dict(self.master)
        master[other]=dict(master['DEMO.NS'],symbol=other)
        at=NOW+dt.timedelta(minutes=5)
        candidate=[dict(symbol=other,score=65,daily_setups=['breakout_20d'])]
        self.assertEqual(self.engine.consider(candidate,Data(at,self.bars),master,at)['status'],'PENDING_ENTRY')
        bar=replace(self.bars[5],low=position['plan']['stop']-1)
        later=NOW+dt.timedelta(minutes=10)
        self.engine.manage(Data(later,self.bars[:5]+[bar]),master,later)
        self.assertFalse(self.engine.ledger.portfolio()['positions'])
        self.assertIsNone(self.engine.state['pending'])
        self.assertEqual(sum(e.get('side')=='buy' for e in self.engine.ledger.events() if e.get('status')=='placed'),1)

    def test_missing_ticks_or_low_lagged_volume_cannot_create_an_entry(self):
        self.engine.consider(self.candidates,Data(NOW,self.bars),{},NOW)
        self.assertIsNone(self.engine.state['pending'])
        tiny=[replace(b,volume=1) for b in self.bars]
        self.engine.consider(self.candidates,Data(NOW,tiny),self.master,NOW)
        self.assertIsNone(self.engine.state['pending'])

    def test_daily_trade_count_blocks_more_entries_before_data_fetch(self):
        with self.engine.ledger.db:
            for i in range(3):
                event=dict(status='placed',side='buy',date=NOW.date().isoformat())
                self.engine.ledger.db.execute('INSERT INTO events VALUES (?,?)',('test-count-'+str(i),json.dumps(event)))
        self.assertEqual(self.engine.consider(self.candidates,None,self.master,NOW)['status'],'DAILY_TRADE_LIMIT')
        self.assertIsNone(self.engine.state['pending'])

    def test_daily_loss_and_corrupted_policy_do_not_release_more_trades(self):
        with self.engine.ledger.db:
            self.engine.ledger.db.execute('INSERT INTO events VALUES (?,?)',('synthetic-loss',json.dumps(
                dict(status='placed',side='sell',date=NOW.date().isoformat(),realized_pnl=-1500))))
        result=self.engine.consider(self.candidates,None,self.master,NOW)
        self.assertEqual(result['status'],'DAILY_LOSS_LIMIT')
        with self.engine.ledger.db:self.engine.ledger.db.execute("DELETE FROM events WHERE id='synthetic-loss'")
        self.enter()
        row=self.engine.ledger.db.execute("SELECT id,document FROM events WHERE id LIKE 'paper-policy:%'").fetchone()
        doc=json.loads(row[1]);doc['policy']['settings']['risk']['budget']=200000
        with self.engine.ledger.db:self.engine.ledger.db.execute('UPDATE events SET document=? WHERE id=?',(json.dumps(doc),row[0]))
        self.assertEqual(paper_journal(self.root/'automation')['status'],'UNAVAILABLE')

    def test_equity_snapshots_and_read_only_dashboard(self):
        self.engine.record_equity(NOW)
        state=self.engine.state
        from workflow.common import digest
        write(self.root/'automation/status.json',dict(heartbeat_at=NOW.isoformat(),worker_running=True,
              phase='MONITORING',portfolio_sha256=digest(self.engine.ledger.portfolio()),marks={}))
        result=snapshot(self.root,NOW)
        self.assertEqual(result['performance']['equity'],100000)
        self.assertTrue(result['worker_running'])
        stale=snapshot(self.root,NOW+dt.timedelta(minutes=2))
        self.assertFalse(stale['worker_running'])
        set_entries(self.root,False)
        self.assertFalse(read(self.root/'automation/settings.json')['enabled'])

    def test_full_universe_scan_selects_only_eligible_shortlist_then_paper_trades(self):
        days=[];day=NOW.date()-dt.timedelta(days=1)
        while len(days)<40:
            if DEFAULT_CALENDAR.trading_day(day):days.append(day)
            day-=dt.timedelta(days=1)
        daily=[Bar(dt.datetime.combine(day,dt.time(9,15),NOW.tzinfo),100+i,101+i,99+i,100+i,1000000)
               for i,day in enumerate(reversed(days))]
        case=self
        class Sources:
            def universe(self):return ['DEMO.NS','DEMO.B.NS','STALE.NS','MISSING.NS'],'original synthetic universe'
            def metadata(self,names):
                case.assertEqual(len(names),4)
                return case.master
            def market(self,cutoff):return Data(cutoff,case.bars,{'DEMO.NS':daily,'DEMO.B.NS':daily,'STALE.NS':daily[:-1]})
        settings=dict(self.settings,minimum_daily_coverage=.5)
        messages=queue.Queue()
        Scanner(self.root,settings,DEFAULT_CALENDAR,lambda:NOW,messages,Sources()).run()
        saved=read(self.root/'automation/shortlist.json')
        self.assertEqual((saved['requested'],saved['scanned'],saved['failed'],saved['stale']), (4,2,1,1))
        self.assertEqual((saved['retrieval_coverage'],saved['fresh_coverage']),(.75,.5))
        self.assertEqual([r['symbol'] for r in saved['accepted']],['DEMO.NS'])
        candidates=[]
        while not messages.empty():
            message=messages.get()
            if message['kind']=='CANDIDATES':candidates=message['candidates']
        self.assertEqual([r['symbol'] for r in candidates],['DEMO.NS'])
        self.assertEqual(read(self.root/'automation/scanner.json')['phase'],'COMPLETE')
        self.engine.consider(candidates,Data(NOW,self.bars),self.master,NOW)
        self.engine.manage(Data(NOW+dt.timedelta(minutes=5),self.bars),self.master,NOW+dt.timedelta(minutes=5))
        self.assertEqual(len(self.engine.ledger.portfolio()['positions']),1)

    def test_retrieval_outage_keeps_counts_and_publishes_ticks_for_protective_management(self):
        case=self
        class Sources:
            def universe(self):return ['DEMO.NS','MISSING.NS'],'original synthetic universe'
            def metadata(self,names):return case.master
            def market(self,cutoff):return Data(cutoff,case.bars)
        messages=queue.Queue()
        Scanner(self.root,self.settings,DEFAULT_CALENDAR,lambda:NOW,messages,Sources()).run()
        result=read(self.root/'automation/scanner.json')
        self.assertEqual(result['phase'],'ERROR')
        self.assertEqual(result['daily']['requested'],2)
        self.assertEqual(result['daily']['failed'],2)
        self.assertEqual(messages.get_nowait()['kind'],'MASTER')
        self.assertFalse((self.root/'automation/shortlist.json').exists())


class PolicyAndSources(unittest.TestCase):
    def test_ambiguous_instruments_are_excluded_without_blocking_other_stocks(self):
        row=dict(tradingsymbol='DEMO',exchange='NSE_EQ',instrument_type='EQUITY',
                 instrument_key='NSE_EQ|SYNTHETIC',tick_size='0.01',lot_size='1')
        rejected=[]
        rows=[row,dict(row,instrument_key='NSE_EQ|OTHER',tick_size='0.05'),row,
              dict(row,tradingsymbol='GOOD'),dict(row,tradingsymbol='BAD',tick_size='NaN')]
        master=normalize_ticks(rows,['DEMO.NS','GOOD.NS','BAD.NS'],NOW,
                               source='https://example.invalid/original-synthetic',rejected=rejected)
        self.assertEqual(set(master),{'GOOD.NS'})
        self.assertEqual(rejected,[dict(symbol='DEMO.NS',reason='AMBIGUOUS_TICK_METADATA'),
                                   dict(symbol='BAD.NS',reason='INVALID_TICK_METADATA')])

    def test_only_paper_nse_and_bounded_settings_are_accepted(self):
        for raw in (None,[],False,{'paper':False},{'execution_enabled':True},{'exchange':'BSE'},
                    {'selection':{'max_participation':.5}},{'risk':{'budget':100}}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):checked(raw)
    def test_decimal_csv_ticks_and_full_universe_selection(self):
        row=dict(tradingsymbol='DEMO',exchange='NSE_EQ',instrument_type='EQUITY',
                 instrument_key='NSE_EQ|SYNTHETIC',tick_size='0.01',lot_size='1')
        master=normalize_ticks([row],['DEMO.NS'],NOW,source='https://example.invalid/original-synthetic')
        self.assertEqual(master['DEMO.NS']['tick_size'],.01)
        rows=[dict(symbol='DEMO.NS',price=100,volume=10000000,volume_x=2,setups=['breakout_20d']),
              dict(symbol='PENNY.NS',price=2,volume=10000000,volume_x=3,setups=['oversold'])]
        accepted,rejected=shortlist(dict(hits=rows),checked(),master)
        self.assertEqual([r['symbol'] for r in accepted],['DEMO.NS'])
        self.assertEqual(rejected[0]['symbol'],'PENNY.NS')
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(snapshot(Path(folder),NOW)['configured'])


class AutomationHTTP(unittest.TestCase):
    def test_status_is_read_only_and_pause_requires_local_origin_token(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);write(root/'automation/settings.json',checked())
            try:server=ThreadingHTTPServer(('127.0.0.1',0),handler(root/'team',root))
            except PermissionError:self.skipTest('Sandbox disallows loopback sockets')
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            base='http://127.0.0.1:'+str(server.server_port)
            with urllib.request.urlopen(base+'/api/runner') as response:
                controls=json.load(response)
            self.assertFalse(controls['available'])
            with urllib.request.urlopen(base+'/api/automation') as response:
                self.assertTrue(json.load(response)['configured'])
            self.assertFalse((root/'automation/state/paper.sqlite').exists())
            def post(body,authorized):
                headers={'Content-Type':'application/json'}
                if authorized:headers.update({'Origin':base,'X-Trader-Token':controls['token']})
                return urllib.request.urlopen(urllib.request.Request(base+'/api/automation/entries',data=json.dumps(body).encode(),headers=headers,method='POST'))
            with self.assertRaises(urllib.error.HTTPError) as caught:post({'enabled':False},False)
            self.assertEqual(caught.exception.code,403);caught.exception.close()
            with post({'enabled':False},True) as response:self.assertFalse(json.load(response)['enabled'])
            self.assertFalse(read(root/'automation/settings.json')['enabled'])
            with self.assertRaises(urllib.error.HTTPError) as caught:post({'enabled':'true'},True)
            self.assertEqual(caught.exception.code,409);caught.exception.close()
            self.assertFalse((root/'automation/state/paper.sqlite').exists())


if __name__=='__main__':unittest.main()
