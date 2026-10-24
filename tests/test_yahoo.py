import io
import json
import os
import unittest
import urllib.error
import datetime as dt
from unittest.mock import patch
from india.data import YahooProvider, IST
from _provider_fixtures import yahoo_intraday

DOC={'chart':{'result':[{'meta':{'symbol':'DEMO.NS','currency':'INR'},'timestamp':[1792381500], 'indicators':{'quote':[{'open':[100],'high':[101],'low':[99],'close':[100],'volume':[1000]}]}}]}}
def response(): return io.StringIO(json.dumps(DOC))

class Yahoo(unittest.TestCase):
    def test_intraday_ignores_nulls_outside_session(self):
        day=dt.date.today()-dt.timedelta(days=1)
        starts=[dt.datetime.combine(day,dt.time(8,0),IST),
                dt.datetime.combine(day,dt.time(9,15),IST),
                dt.datetime.combine(day,dt.time(9,20),IST)]
        doc={'chart':{'result':[{'meta':{'symbol':'DEMO.NS','currency':'INR','dataGranularity':'5m'},
             'timestamp':[int(x.timestamp()) for x in starts],
             'indicators':{'quote':[{'open':[None,100,101],'high':[None,102,103],
                'low':[None,99,100],'close':[None,101,102],'volume':[None,1000,1200]}]}}]}}
        # The completed prior-session candles remain, while the out-of-session
        # null slot is ignored.
        self.assertEqual(len(YahooProvider.parse(doc,'DEMO.NS')),2)

    def test_intraday_still_rejects_missing_regular_session_candle(self):
        day=dt.date.today()-dt.timedelta(days=1)
        starts=[dt.datetime.combine(day,t,IST) for t in (dt.time(9,15),dt.time(9,20),dt.time(9,25))]
        doc={'chart':{'result':[{'meta':{'symbol':'DEMO.NS','currency':'INR','dataGranularity':'5m'},
             'timestamp':[int(x.timestamp()) for x in starts],
             'indicators':{'quote':[{'open':[100,100,101],'high':[102,None,103],
                'low':[99,None,100],'close':[101,None,102],'volume':[1000,None,1200]}]}}]}}
        with self.assertRaisesRegex(ValueError,'missing OHLCV.*high'):
            YahooProvider.parse(doc,'DEMO.NS')

    def test_synthetic_auction_gap_is_outside_strategy_window(self):
        from india.analysis import report, indicators, backtest
        from india.data import timestamp
        doc=yahoo_intraday()
        bars=YahooProvider.parse(doc,'DEMO.NS','5m')
        r=report(bars,timestamp('2026-10-23T20:30:00+05:30'),'DEMO.NS',.05,100000,500)
        self.assertEqual(r['status'],'NO_SETUP')
        self.assertFalse(r['execution_enabled'])
        self.assertIsNone(r['plan'])
        self.assertFalse(r['market_open'])
        self.assertTrue(r['stale'])
        self.assertTrue(r['data_quality']['complete'])
        self.assertEqual(r['data_quality']['missing_bars'],[])
        self.assertEqual(r['data_quality']['received_bars'],72)
        self.assertEqual(r['data_quality']['usable_session_bars'],72)
        self.assertEqual(r['indicators_as_of'],'2026-10-23T15:15:00+05:30')
        self.assertEqual(r['indicators'],indicators(bars[:72]))
        backtest(bars,'DEMO.NS',.05,100000,500)
        # A gap inside continuous trading must still block reports/backtests.
        broken=bars[:30]+bars[31:]
        self.assertEqual(report(broken,timestamp('2026-10-23T20:30:00+05:30'),'DEMO.NS',.05,100000,500)['status'],'BLOCKED_INCOMPLETE_DATA')
        with self.assertRaisesRegex(ValueError,'Missing intraday bars'):
            backtest(broken,'DEMO.NS',.05,100000,500)

    def test_synthetic_future_gap_does_not_affect_replay(self):
        from india.analysis import report
        from india.data import timestamp
        doc=yahoo_intraday()
        bars=YahooProvider.parse(doc,'DEMO.NS','5m')
        r=report(bars,timestamp('2026-10-23T10:00:00+05:30'),'DEMO.NS',.05,100000,500)
        self.assertTrue(r['data_quality']['complete'])
        self.assertFalse(r['stale'])
        self.assertEqual(r,report(bars[:9],timestamp('2026-10-23T10:00:00+05:30'),'DEMO.NS',.05,100000,500))

    def test_synthetic_cli_returns_json(self):
        from india.__main__ import main
        raw=json.dumps(yahoo_intraday())
        with patch('sys.argv',['india','analyze','DEMO.NS','--tick','.05','--as-of','2026-10-23T20:30:00+05:30']),patch('urllib.request.urlopen',side_effect=lambda *a,**k:io.StringIO(raw)),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(),0)
            self.assertEqual(json.loads(out.getvalue())['status'],'NO_SETUP')

    def test_gap_prefers_complete_alternate_host_without_mixing(self):
        import copy
        gap=copy.deepcopy(DOC)
        result=gap['chart']['result'][0]
        result['timestamp']=[1792381500,1792381800,1792382100]
        result['indicators']['quote'][0]={k:[100,None,100] for k in ('open','high','low','close','volume')}
        intact=copy.deepcopy(gap)
        intact['chart']['result'][0]['indicators']['quote'][0]={k:[101,101,101] for k in ('open','high','low','close','volume')}
        with patch('urllib.request.urlopen',side_effect=[io.StringIO(json.dumps(gap)),io.StringIO(json.dumps(intact))]) as call:
            bars=YahooProvider().history('DEMO.NS','5m')
        self.assertEqual(call.call_count,2)
        self.assertEqual([b.close for b in bars],[101,101,101])

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


class HTTPErrorCleanup(unittest.TestCase):
    def providers(self):
        from india.data import timestamp
        from india.nse import NSEProvider
        from india.upstox import get_json
        from workflow.data import MarketData
        cutoff = timestamp('2026-10-23T20:30:00+05:30')
        return [
            ('nse', lambda: NSEProvider(cutoff).history('DEMO.NS', '5m'), 1),
            ('upstox', lambda: get_json('https://example.invalid'), 1),
            ('yahoo', lambda: YahooProvider().history('DEMO.NS', '5m'), 1),
            ('workflow-yahoo', lambda: MarketData(cutoff, auto_fallback=False).history('DEMO.NS', '1d'), 2),
        ]

    def test_bodyless_errors_skip_uninitialized_response_cleanup(self):
        for name, fetch, attempts in self.providers():
            with self.subTest(provider=name):
                error = urllib.error.HTTPError('https://example.invalid', 403, 'denied', {}, None)
                if error.fp is not None:
                    self.addCleanup(error.close)
                # Newer Python initializes an empty stream. Reproduce the 3.9
                # no-body state so this regression stays covered on every version.
                error.fp = None
                with patch.object(error, 'close', side_effect=KeyError('file')) as close, \
                     patch('urllib.request.urlopen', side_effect=error) as request:
                    with self.assertRaises(ValueError):
                        fetch()
                    close.assert_not_called()
                    self.assertEqual(request.call_count, attempts)

    def test_error_response_bodies_are_still_closed(self):
        for name, fetch, attempts in self.providers():
            with self.subTest(provider=name):
                body = io.BytesIO(b'denied')
                error = urllib.error.HTTPError('https://example.invalid', 403, 'denied', {}, body)
                with patch('urllib.request.urlopen', side_effect=error) as request:
                    with self.assertRaises(ValueError):
                        fetch()
                    self.assertEqual(request.call_count, attempts)
                self.assertTrue(body.closed)
