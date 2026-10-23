"""Immutable handoffs, independent-review gates and paper-only risk checks."""
import datetime as dt
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from india.__main__ import main
from india.analysis import report
from india.data import Bar, CSVProvider, timestamp, IST
from india.market import DEFAULT_CALENDAR, session
from india.team import (prepare, evaluate, finalize, read_json, write_json,
                        load_session, REQUIRED, ARTIFACTS, budget_plan, portfolio_input, limit_input,
                        preflight, _source_check)

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/india/synthetic.csv'


class Team(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars=CSVProvider(FIXTURE).history('DEMO.NS','5m')
        cls.now=timestamp('2026-10-19T09:35:00+05:30')
        cls.settings=dict(symbol='DEMO.NS',tick=.05,capital=100000,risk=500,rr=2,
                          as_of=cls.now.isoformat(),cost_config=None,calendar=DEFAULT_CALENDAR.document)
        cls.metadata=dict(symbol='DEMO.NS',tick_size=.05,currency='INR',tick_unit='INR',
                          effective_from='2026-10-01',effective_to='2026-10-31',
                          published_at='2026-09-30T18:00:00+05:30',source='https://example.invalid/synthetic',fixture=True)
        cls.settings['security_metadata']=cls.metadata
        cls.baseline=report(cls.bars,cls.now,'DEMO.NS',.05,100000,500)
        cls.history=[]
        day=dt.date(2026,9,1)
        while len(cls.history)<20*72:
            if session(dt.datetime.combine(day,dt.time(9,15),IST)):
                rows=[replace(b,time=b.time.replace(year=day.year,month=day.month,day=day.day))
                      for b in cls.bars if b.time.time()<dt.time(15,15)]
                while len(rows)<72:
                    b=rows[-1]
                    rows.append(Bar(b.time+dt.timedelta(minutes=5),b.close,b.close+.1,b.close-.1,b.close,1000))
                cls.history.extend(rows)
            day+=dt.timedelta(days=1)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'session'

    def prepare(self, rich=False, **kwargs):
        return prepare((self.history if rich else [])+self.bars,self.settings,{'source':'SYNTHETIC TEST FIXTURE'},
                       self.baseline,self.directory,simulations=8,
                       security_metadata=kwargs.pop('security_metadata',{'DEMO.NS':self.metadata}),**kwargs)

    def reviews(self, verdicts=None):
        """Explicit mock review fixtures; these are never native agent claims."""
        context=read_json(self.directory/'context.json')
        agents={r:'UNIT_TEST_MOCK_'+r for r in REQUIRED}
        write_json(self.directory/'delegations.json',dict(schema_version=1,snapshot_id=context['snapshot_id'],agents=agents))
        for role in REQUIRED:
            doc=dict(schema_version=1,role=role,snapshot_id=context['snapshot_id'],as_of=self.settings['as_of'],
                     artifact_sha256=context['artifact_hashes'][role],agent_id=agents[role],
                     verdict=(verdicts or {}).get(role,'PASS'),summary='Mock fixture assertion, not an agent run.',
                     evidence=['Unit-test evidence fixture'],sources=[])
            if role in ('news','analyst'):
                doc['sources']=[dict(url='https://www.nseindia.com/test-fixture',
                                    published_at='2026-10-18T12:00:00+05:30',accessed_at=self.settings['as_of'])]
            write_json(self.directory/'reviews'/(role+'.json'),doc)

    def change_review(self, name, **changes):
        path=self.directory/'reviews'/(name+'.json')
        doc=read_json(path);doc.update(changes);write_json(path,doc)

    def test_prepare_causal_artifacts_and_no_agents_or_orders(self):
        result=self.prepare()
        self.assertFalse(result['native_agents_started'])
        self.assertFalse(result['execution_enabled'])
        context,bars=load_session(self.directory)
        self.assertEqual(len(bars),4)
        self.assertTrue(all(b.time+dt.timedelta(minutes=5)<=self.now for b in bars))
        self.assertEqual(set(context['artifact_hashes']),set(ARTIFACTS))
        self.assertEqual(evaluate(self.directory,'pelosi')['verdict'],'NOT_APPLICABLE')
        ticket=evaluate(self.directory,'broker')['data']
        self.assertFalse(ticket['submitted']);self.assertFalse(ticket['filled'])
        self.assertFalse(evaluate(self.directory,'alerts')['data']['monitoring_started'])
        self.assertEqual(evaluate(self.directory,'timemachine')['data']['reason'],'INSUFFICIENT_HISTORY')

    def test_preflight_flags_actual_roots_without_dispatch(self):
        self.prepare(security_metadata={})
        check=preflight(self.directory)
        self.assertFalse(check['should_dispatch_specialists'])
        self.assertEqual({x['code'] for x in check['issues']},{'TICK_UNVERIFIED','INSUFFICIENT_HISTORY'})
        self.assertIn('tick_unverified',evaluate(self.directory,'risk')['data']['reasons'])
        self.assertIsNone(evaluate(self.directory,'broker')['data']['order'])
        with patch('india.team.code_hash',return_value='upgraded'):
            self.assertFalse(preflight(self.directory)['same_engine'])
            with self.assertRaisesRegex(ValueError,'Engine changed'): evaluate(self.directory,'scanner')

    def test_rich_preflight_ready_still_needs_independent_reviews(self):
        result=self.prepare(rich=True)
        self.assertTrue(result['preflight']['should_dispatch_specialists'])
        self.assertEqual(finalize(self.directory)['status'],'PENDING_AGENT_REVIEWS')

    def test_tick_mismatch_and_real_symbol_fixture_cannot_clear(self):
        self.prepare(rich=True,security_metadata={'DEMO.NS':dict(self.metadata,tick_size=.1)})
        self.assertIn('tick_mismatch',evaluate(self.directory,'risk')['data']['reasons'])
        self.assertIsNone(evaluate(self.directory,'exit')['data']['plan'])
        from india.security import verify_tick
        self.assertEqual(verify_tick('TCS.NS',.05,{'TCS.NS':dict(self.metadata,symbol='TCS.NS')})['status'],'UNVERIFIED')

    def test_date_only_research_uses_end_of_day_bound_without_inventing_clock(self):
        self.prepare()
        context=read_json(self.directory/'context.json')
        source=dict(url='https://nseindia.com/original',published_date='2026-10-18',
                    publication_timezone='Asia/Kolkata',accessed_at=self.settings['as_of'])
        self.assertTrue(_source_check([source],context))
        for changes in (dict(published_date='2026-10-19'),dict(published_date='2026-10-20'),
                        dict(publication_timezone=''),dict(publication_timezone='Unknown'),
                        dict(accessed_at='2026-10-18T12:00:00+05:30'),
                        dict(published_at=self.settings['as_of'])):
            with self.subTest(changes=changes):
                self.assertFalse(_source_check([dict(source,**changes)],context))
        self.reviews()
        self.change_review('analyst',sources=[source])
        self.assertNotIn('analyst',finalize(self.directory)['invalid_reviews'])

    def test_reject_reusing_nonempty_session_directory(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError,'not empty'): self.prepare()

    def test_missing_reviews_fail_closed(self):
        self.prepare()
        result=finalize(self.directory)
        self.assertEqual(result['status'],'PENDING_AGENT_REVIEWS')
        self.assertEqual(len(result['missing_reviews']),10)
        self.assertIsNone(result['paper_ticket'])

    def test_unavailable_numeric_history_cannot_be_overridden_by_model(self):
        self.prepare();self.reviews()
        result=finalize(self.directory)
        self.assertEqual(result['status'],'REVIEW_INCOMPLETE')
        self.assertIn('timemachine',result['engine_unavailable'])

    def test_complete_mock_handoff_yields_only_paper_ticket(self):
        self.prepare(rich=True);self.reviews()
        result=finalize(self.directory)
        self.assertEqual(result['status'],'READY_PAPER_TICKET')
        self.assertEqual(result['orchestration']['distinct_recorded_agent_ids'],10)
        self.assertEqual(result['live_orders_submitted'],0)
        self.assertFalse(result['paper_ticket']['execution_enabled'])
        self.assertLessEqual(result['budget']['sizing']['max_loss_estimate'],500)
        self.assertEqual(finalize(self.directory),result)

    def test_one_independent_block_vetoes_all_positive_reviews(self):
        self.prepare();self.reviews({'news':'BLOCK'})
        result=finalize(self.directory)
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(result['vetoes'],['news'])

    def test_unavailable_sources_and_risk_warn_do_not_clear(self):
        for role,verdict in (('analyst','UNAVAILABLE'),('risk','WARN')):
            with self.subTest(role=role):
                if not self.directory.exists(): self.prepare(rich=True)
                self.reviews({role:verdict})
                self.assertEqual(finalize(self.directory)['status'],'REVIEW_INCOMPLETE')

    def test_stale_hash_cutoff_identity_or_role_invalidates_review(self):
        self.prepare();self.reviews()
        for field,value in (('artifact_sha256','changed'),('as_of','2026-10-19T09:40:00+05:30'),
                            ('snapshot_id','old'),('agent_id','invented'),('role','broker'),('verdict','NOT_APPLICABLE')):
            with self.subTest(field=field):
                self.reviews();self.change_review('risk',**{field:value})
                self.assertIn('risk',finalize(self.directory)['invalid_reviews'])

    def test_single_reviewer_cannot_impersonate_a_team(self):
        self.prepare();self.reviews()
        manifest=read_json(self.directory/'delegations.json')
        for role in REQUIRED:
            manifest['agents'][role]='ONE_MOCK_ID'
            self.change_review(role,agent_id='ONE_MOCK_ID')
        write_json(self.directory/'delegations.json',manifest)
        self.assertIn('agent_identity_reused_across_roles',finalize(self.directory)['invalid_reviews'])

    def test_future_untrusted_or_missing_filings_cannot_pass(self):
        self.prepare();self.reviews()
        for sources in ([],[dict(url='https://evil.example/news',published_at=self.settings['as_of'],accessed_at=self.settings['as_of'])],
                        [dict(url='https://nseindia.com.evil.example/news',published_at=self.settings['as_of'],accessed_at=self.settings['as_of'])],
                        [dict(url='https://nseindia.com/news',published_at='2026-10-20T00:00:00+05:30',accessed_at='2026-10-21T00:00:00+05:30')]):
            self.change_review('news',sources=sources)
            self.assertIn('news',finalize(self.directory)['invalid_reviews'])

    def test_changed_snapshot_artifact_or_engine_rejected(self):
        self.prepare()
        path=self.directory/'entry.json';doc=read_json(path)
        write_json(path,dict(doc,verdict='PASS',summary='tampered'))
        with self.assertRaisesRegex(ValueError,'artifact changed'): evaluate(self.directory,'entry')
        write_json(path,doc)
        with patch('india.team.code_hash',return_value='different'):
            with self.assertRaisesRegex(ValueError,'Engine changed'): evaluate(self.directory,'entry')
        path=self.directory/'snapshot.json';doc=read_json(path);doc['bars'][0]['close']+=.1;write_json(path,doc)
        with self.assertRaisesRegex(ValueError,'integrity'): finalize(self.directory)

    def test_portfolio_caps_and_cost_inclusive_combined_risk(self):
        p=portfolio_input(None,self.settings);limits=limit_input(None,self.settings)
        normal=budget_plan(self.settings,self.baseline,p,limits)
        self.assertEqual(normal['status'],'PASS')
        self.assertLessEqual(normal['order']['qty']*normal['order']['entry'],75000)
        for changes,reason in ((dict(trades_today=3),'daily_trade_cap'),(dict(orders_today=10),'daily_order_cap'),
                               (dict(cash=0),'cash_buffer_or_cash_limit'),
                               (dict(realized_pnl_today=-1500),'daily_loss_or_total_open_risk_limit'),
                               (dict(positions=[dict(symbol='OTHER.NS',qty=200,mark=100,stop=90)]),'daily_loss_or_total_open_risk_limit'),
                               (dict(positions=[dict(symbol='DEMO.NS',qty=750,mark=100,stop=99.99)]),'position_or_order_limit')):
            result=budget_plan(self.settings,self.baseline,dict(p,**changes),limits)
            self.assertEqual(result['status'],'BLOCK');self.assertIn(reason,result['reasons'])

    def test_portfolio_validation_and_limit_unknown_keys(self):
        p=portfolio_input(None,self.settings)
        for changes in (dict(as_of='2026-10-19T09:10:00+05:30'),dict(as_of='2026-10-19T09:40:00+05:30'),
                        dict(trades_today=True),dict(cash=200000),dict(positions=[dict(symbol='DEMO.NS',qty=1,mark=100,stop=101)])):
            with self.assertRaises(ValueError): portfolio_input(dict(p,**changes),self.settings)
        with self.assertRaises(ValueError): limit_input({'typo':1},self.settings)

    def test_cli_fetches_once_and_installed_specialist_dispatches(self):
        output=io.StringIO()
        with patch.object(CSVProvider,'history',return_value=self.bars) as fetch,redirect_stdout(output):
            code=main(['team','DEMO.NS','--csv',str(FIXTURE),'--as-of',self.settings['as_of'],
                       '--tick','.05','--team-dir',str(self.directory)])
        self.assertEqual(code,0);fetch.assert_called_once_with('DEMO.NS','5m')
        result=subprocess.run([sys.executable,str(ROOT/'trader-risk/run.py'),'evaluate','--session',str(self.directory)],
                              cwd=self.temp.name,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['role'],'risk')

    def test_team_record_db_rejected_before_provider_access(self):
        with patch.object(CSVProvider,'history') as fetch,redirect_stderr(io.StringIO()):
            code=main(['team','DEMO.NS','--csv',str(FIXTURE),'--tick','.05','--record-db','unused.sqlite'])
        self.assertEqual(code,1);fetch.assert_not_called()


if __name__=='__main__': unittest.main()
