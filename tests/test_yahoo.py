import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch
from india.data import YahooProvider

DOC={'chart':{'result':[{'meta':{'symbol':'DEMO.NS','currency':'INR'},'timestamp':[1792381500], 'indicators':{'quote':[{'open':[100],'high':[101],'low':[99],'close':[100],'volume':[1000]}]}}]}}
def response(): return io.StringIO(json.dumps(DOC))

class Yahoo(unittest.TestCase):
    def test_retry_then_alternate_host(self):
        err=lambda:urllib.error.HTTPError('https://query2.finance.yahoo.com',429,'limited',{},None)
        with patch('urllib.request.urlopen',side_effect=[err(),err(),response()]) as call,patch('time.sleep'):
            self.assertEqual(len(YahooProvider().history('DEMO.NS','5m')),1)
            self.assertIn('query1.',call.call_args[0][0].full_url)
    def test_incomplete_response_tries_other_host(self):
        with patch('urllib.request.urlopen',side_effect=[io.StringIO('{"chart":{"result":[]}}'),response()]) as call:
            self.assertEqual(len(YahooProvider().history('DEMO.NS','5m')),1)
            self.assertEqual(call.call_count,2)
    def test_bounded_failure(self):
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('offline')) as call,patch('time.sleep'):
            with self.assertRaisesRegex(ValueError,'no prices were fabricated'):YahooProvider().history('DEMO.NS','5m')
            self.assertEqual(call.call_count,4)
    def test_cli_default_needs_no_credentials(self):
        from india.__main__ import main
        with patch.dict(os.environ,{},clear=True),patch('sys.argv',['india','history','DEMO.NS','--tick','.05','--as-of','2026-10-23T10:00:00+05:30']),patch('urllib.request.urlopen',side_effect=lambda *a,**k:response()),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(),0)
            self.assertIn('Yahoo',json.loads(out.getvalue())['source'])
