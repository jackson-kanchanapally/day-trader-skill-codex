"""Read-only observability, real progress and snapshot gates; no AI/network data."""
import datetime as dt
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from dashboard.__main__ import sessions, session_view, record_activity, handler
from india.team import prepare, read_json, write_json, evaluate
from india.analysis import report
from india.data import CSVProvider, timestamp
from india.market import DEFAULT_CALENDAR

ROOT=Path(__file__).resolve().parents[1]


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.session=self.root/'demo'
        bars=CSVProvider(ROOT/'tests/fixtures/india/synthetic.csv').history('DEMO.NS','5m')
        self.now=timestamp('2026-10-19T09:35:00+05:30')
        settings=dict(symbol='DEMO.NS',tick=.05,capital=100000,risk=500,rr=2,
                      as_of=self.now.isoformat(),cost_config=None,calendar=DEFAULT_CALENDAR.document)
        prepare(bars,settings,{'source':'SYNTHETIC TEST'},report(bars,self.now,'DEMO.NS',.05,100000,500),self.session,simulations=8)

    def delegate(self, role='scanner'):
        m=read_json(self.session/'delegations.json');m['agents'][role]='MOCK_TEST_'+role
        write_json(self.session/'delegations.json',m)

    def review(self, role='scanner'):
        e=evaluate(self.session,role)
        d=dict(schema_version=1,role=role,snapshot_id=e['snapshot_id'],as_of=e['as_of'],
               artifact_sha256=e['artifact_sha256'],agent_id='MOCK_TEST_'+role,
               verdict='WARN',summary='Explicit unit-test review fixture',evidence=['Synthetic fixture'],sources=[])
        write_json(self.session/'reviews'/(role+'.json'),d)
        return d

    def test_prepared_artifacts_do_not_count_as_independent_completions(self):
        v=session_view(self.root,'demo')
        self.assertEqual(len(v['roles']),10)
        self.assertTrue(all(r['phase']=='queued' and r['review'] is None for r in v['roles']))
        self.assertEqual(v['status'],'AWAITING_FINALIZATION')
        self.assertFalse(v['execution_enabled'])
        self.assertEqual(len(v['candles']),4)

    def test_dispatch_does_not_imply_running_and_running_is_recorded(self):
        self.delegate()
        self.assertEqual(session_view(self.root,'demo')['roles'][0]['phase'],'dispatched')
        record_activity(self.session,'scanner','MOCK_TEST_scanner','running')
        self.assertEqual(session_view(self.root,'demo')['roles'][0]['phase'],'running')
        with self.assertRaises(ValueError):record_activity(self.session,'scanner','invented','running')
        with self.assertRaises(ValueError):record_activity(self.session,'scanner','MOCK_TEST_scanner','completed')
        self.review();record_activity(self.session,'scanner','MOCK_TEST_scanner','completed')
        self.assertEqual(session_view(self.root,'demo')['roles'][0]['phase'],'completed')

    def test_invalid_hash_or_id_does_not_count_as_completed(self):
        self.delegate();d=self.review();d['artifact_sha256']='wrong'
        write_json(self.session/'reviews/scanner.json',d)
        v=session_view(self.root,'demo')['roles'][0]
        self.assertEqual(v['phase'],'invalid');self.assertTrue(v['errors'])

    def test_changed_engine_and_stale_report_never_expose_clearance(self):
        write_json(self.session/'report.json',dict(status='READY_PAPER_TICKET',snapshot_id='wrong',reviews={}))
        v=session_view(self.root,'demo')
        self.assertIsNone(v['report']);self.assertTrue(v['report_outdated'])
        with patch('india.team.code_hash',return_value='changed'):
            v=session_view(self.root,'demo')
        self.assertEqual(v['status'],'INTEGRITY_ERROR');self.assertTrue(v['integrity_errors'])

    def test_missing_root_empty_discovery_and_symlink_escape(self):
        self.assertEqual(sessions(self.root/'missing'),[])
        self.assertEqual([s['id'] for s in sessions(self.root)],['demo'])
        (self.root/'bad').symlink_to(self.root.parent,target_is_directory=True)
        self.assertEqual(len(sessions(self.root)),1)
        with self.assertRaises(ValueError):session_view(self.root,'../outside')

    def test_source_metadata_and_duplicate_native_identity_checked(self):
        self.delegate('news');d=self.review('news')
        v=session_view(self.root,'demo')
        self.assertEqual(next(r for r in v['roles'] if r['role']=='news')['phase'],'invalid')
        m=read_json(self.session/'delegations.json');m['agents']['scanner']='MOCK_TEST_news'
        write_json(self.session/'delegations.json',m)
        self.assertTrue(any('reused' in error for r in session_view(self.root,'demo')['roles'] for error in r['errors']))

    def test_http_is_read_only_and_restricts_host_and_files(self):
        try: server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.root))
        except PermissionError: self.skipTest('Sandbox disallows loopback sockets')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base='http://127.0.0.1:'+str(server.server_port)
        with urllib.request.urlopen(base+'/api/sessions') as response:
            self.assertEqual(response.status,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        for path,status in (('/api/sessions/..%2foutside',409),('/assets/../../README.md',404),('/.env',404)):
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(base+path)
            self.assertEqual(e.exception.code,status)
            e.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base+'/api/sessions',headers={'Host':'evil.example'}))
        self.assertEqual(e.exception.code,403)
        e.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base+'/api/sessions',data=b'{}',method='POST'))
        self.assertEqual(e.exception.code,501)
        e.exception.close()


if __name__=='__main__': unittest.main()
