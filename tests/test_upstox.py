import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from india.data import timestamp
from india.upstox import UpstoxProvider, parse_candles, get_json

ROW=['2026-10-19T09:15:00+05:30',100,101,99,100.5,1000,0]
MASTER=[{'segment':'NSE_EQ','instrument_type':'EQ','trading_symbol':'DEMO','instrument_key':'NSE_EQ|TEST'}]

class Upstox(unittest.TestCase):
    def provider(self):
        with patch.dict(os.environ,{'UPSTOX_ACCESS_TOKEN':'test-secret'}):
            return UpstoxProvider(timestamp('2026-10-19T10:00:00+05:30'))
    def test_free_analytics_token_preferred(self):
        with patch.dict(os.environ, {'UPSTOX_ANALYTICS_TOKEN':'read-only-test', 'UPSTOX_ACCESS_TOKEN':'old-test'}, clear=True):
            self.assertEqual(UpstoxProvider().token,'read-only-test')
        with patch.dict(os.environ, {'UPSTOX_ANALYTICS_TOKEN':'read-only-test'}, clear=True):
            self.assertEqual(UpstoxProvider().token,'read-only-test')

    def test_missing_credentials(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'UPSTOX_ACCESS_TOKEN'): UpstoxProvider()
    def test_descending_and_invalid_candles(self):
        later=list(ROW);later[0]='2026-10-19T09:20:00+05:30'
        doc={'status':'success','data':{'candles':[later,ROW]}}
        self.assertLess(parse_candles(doc)[0].time,parse_candles(doc)[1].time)
        doc['data']['candles']=[ROW,ROW]
        with self.assertRaises(ValueError):parse_candles(doc)
        bad=list(ROW);bad[4]=None;doc['data']['candles']=[bad]
        with self.assertRaises(ValueError):parse_candles(doc)
    def test_historical_url_and_symbol(self):
        p=self.provider()
        with patch('india.upstox.get_json',side_effect=[MASTER,{'status':'success','data':{'candles':[ROW]}}]) as call:
            self.assertEqual(len(p.history('DEMO.NS','5m')),1)
            self.assertIn('NSE_EQ%7CTEST/minutes/5/2026-10-19/2026-10-13',call.call_args[0][0])
        with patch('india.upstox.get_json',return_value=MASTER):
            with self.assertRaises(ValueError):p.resolve('WRONG.NS')
    def test_current_day_uses_separate_endpoint(self):
        p=self.provider();p.now=timestamp('2026-10-19T10:00:00+05:30')
        empty={'status':'success','data':{'candles':[]}}
        with patch('india.upstox.get_json',side_effect=[MASTER,empty,{'status':'success','data':{'candles':[ROW]}}]) as call:
            p.history('DEMO.NS','5m')
            self.assertIn('/intraday/',call.call_args[0][0])
    def test_bse_master(self):
        p=self.provider()
        row={'segment':'BSE_EQ','instrument_type':'EQ','exchange_token':'500325','instrument_key':'BSE_EQ|TEST'}
        with patch('india.upstox.get_json',return_value=[row]):
            self.assertEqual(p.resolve('500325.BO'),'BSE_EQ%7CTEST')
    def test_auth_does_not_retry_or_leak(self):
        error=urllib.error.HTTPError('https://api.upstox.com',401,'test-secret',{},None)
        with patch('urllib.request.urlopen',side_effect=error) as call:
            with self.assertRaises(ValueError) as ctx:get_json('https://api.upstox.com','test-secret')
            self.assertNotIn('test-secret',str(ctx.exception));self.assertEqual(call.call_count,1)
    def test_retry_bounded(self):
        error=urllib.error.HTTPError('https://api.upstox.com',429,'limited',{},None)
        with patch('urllib.request.urlopen',side_effect=error) as call,patch('time.sleep'):
            with self.assertRaises(ValueError):get_json('https://api.upstox.com','test-secret')
            self.assertEqual(call.call_count,3)

class CLI(unittest.TestCase):
    def test_optional_upstox_requires_credentials_but_csv_does_not(self):
        import subprocess,sys
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        env=dict(os.environ);env.pop('UPSTOX_ACCESS_TOKEN',None);env.pop('UPSTOX_ANALYTICS_TOKEN',None)
        command=[sys.executable,'-m','india','analyze','DEMO.NS','--tick','.05','--provider','upstox']
        r=subprocess.run(command,cwd=root,env=env,text=True,capture_output=True)
        self.assertEqual(r.returncode,1)
        self.assertIn('UPSTOX_ACCESS_TOKEN',r.stderr)
        r=subprocess.run(command+['--csv','tests/fixtures/india/synthetic.csv','--as-of','2026-10-19T09:35:00+05:30'],cwd=root,env=env,text=True,capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)['source'],'CSV export')
