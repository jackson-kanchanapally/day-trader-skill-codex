import copy
import datetime as dt
import io
import json
from pathlib import Path
import unittest
import urllib.error
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from india.analysis import backtest, report
from india.data import timestamp
from india.nse import NSEProvider, OFFSET

FIXTURES = Path(__file__).parent / 'fixtures/india'
NOW = timestamp('2026-10-23T20:30:00+05:30')
SYMBOL = {'status': True, 'data': [{
    'symbol': 'TCS-EQ', 'scripcode': '11536', 'type': 'Equity', 'exchange': 'NSE',
}]}


def fixture(name='tcs-nse-2026-10-23.json'):
    return json.loads((FIXTURES/name).read_text())


def response(doc):
    return io.StringIO(json.dumps(doc))


class NSE(unittest.TestCase):
    def test_actual_candles_clock_and_auction_exclusion(self):
        bars = NSEProvider.parse(fixture(), '5m', NOW)
        self.assertEqual(len(bars), 75)
        self.assertEqual(bars[0].time.isoformat(), '2026-10-23T09:15:00+05:30')
        self.assertEqual(bars[-1].time.isoformat(), '2026-10-23T15:25:00+05:30')
        self.assertTrue(all(b.time-a.time == dt.timedelta(minutes=5) for a,b in zip(bars,bars[1:])))
        self.assertEqual(bars[0].close, 2602)
        self.assertEqual(bars[0].volume, 1428749)  # Preserve the published volume.
        r = report(bars, NOW, 'TCS.NS', .05, 100000, 500)
        self.assertTrue(r['data_quality']['complete'])
        self.assertEqual(r['data_quality']['received_bars'], 72)
        self.assertEqual(r['indicators_as_of'], '2026-10-23T15:15:00+05:30')
        self.assertIsNone(r['plan'])
        self.assertFalse(r['strategy_open'])
        self.assertFalse(r['execution_enabled'])

    def test_source_updates_cannot_leak_into_replay(self):
        full = NSEProvider.parse(fixture(), '5m', NOW)
        at = timestamp('2026-10-23T10:00:00+05:30')
        limited = NSEProvider.parse(fixture(), '5m', at)
        self.assertEqual(len(limited), 9)
        self.assertEqual(report(full,at,'TCS.NS',.05,100000,500),
                         report(limited,at,'TCS.NS',.05,100000,500))

    def test_actual_daily_candles(self):
        bars = NSEProvider.parse(fixture('tcs-nse-daily-2026-10-23.json'), '1d', NOW)
        self.assertEqual(len(bars), 15)
        self.assertEqual(bars[0].time.isoformat(), '2026-10-01T09:15:00+05:30')
        self.assertEqual(bars[-1].time.isoformat(), '2026-10-23T09:15:00+05:30')
        self.assertEqual(bars[-1].close, 2530)

    def test_request_encodes_exchange_wall_time_and_exact_symbol(self):
        source = NSEProvider(NOW,1)
        with patch('urllib.request.urlopen', side_effect=[response(SYMBOL),response(fixture())]) as call:
            bars = source.history('TCS.NS','5m')
        self.assertEqual(len(bars),75)
        self.assertEqual(call.call_count,2)
        lookup = parse_qs(urlparse(call.call_args_list[0].args[0].full_url).query)
        self.assertEqual(lookup,{'symbol':['TCS'],'segment':['EQ']})
        args = parse_qs(urlparse(call.call_args_list[1].args[0].full_url).query)
        self.assertEqual(args['toDate'],[str(int(NOW.timestamp())+OFFSET)])
        self.assertEqual(args['token'],['11536'])
        self.assertEqual(args['symbol'],['TCS-EQ'])
        self.assertEqual(args['chartType'],['I'])
        self.assertEqual(args['timeInterval'],['5'])

    def test_long_history_pages_nonoverlapping_windows_and_preserves_gaps(self):
        source=NSEProvider(NOW,60)
        requests=[]
        def get(path, params):
            if path.endswith('symbolsDynamic'): return SYMBOL
            requests.append(params)
            # One observed row in each window, leaving all other slots missing.
            day=dt.datetime.fromtimestamp(params['fromDate'],dt.timezone.utc).date()
            when=dt.datetime.combine(day,dt.time(9,15),dt.timezone.utc)
            row=dict(time=when.timestamp()*1000,open=100,high=101,low=99,close=100,volume=1)
            return {'status':True,'data':[row]}
        with patch.object(source,'_get',side_effect=get):
            bars=source.history('TCS.NS','5m')
        self.assertEqual(len(requests),3)
        self.assertEqual(len(bars),3)
        self.assertTrue(all(a['toDate']+1==b['fromDate'] for a,b in zip(requests,requests[1:])))
        self.assertTrue(all(r['toDate']-r['fromDate']<28*86400 for r in requests))
        self.assertLessEqual(bars[-1].time,NOW)

    def test_wrong_symbol_and_bse_cannot_silently_be_substituted(self):
        wrong = copy.deepcopy(SYMBOL)
        wrong['data'][0]['symbol'] = 'TCSOTHER-EQ'
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k:response(wrong)):
            with self.assertRaisesRegex(ValueError,'uniquely resolve'):
                NSEProvider(NOW).history('TCS.NS','5m')
        with patch('urllib.request.urlopen') as call:
            with self.assertRaisesRegex(ValueError,'.NS equities only'):
                NSEProvider(NOW).history('500325.BO','5m')
            call.assert_not_called()

    def test_missing_and_duplicate_candles(self):
        doc = fixture()
        doc['data'][1]['volume'] = None
        with self.assertRaisesRegex(ValueError,'missing OHLCV'):
            NSEProvider.parse(doc,'5m',NOW)
        doc = fixture()
        doc['data'].append(doc['data'][0])
        with self.assertRaisesRegex(ValueError,'unique increasing'):
            NSEProvider.parse(doc,'5m',NOW)
        for doc in ({'status':False,'data':[]},{'status':True,'data':[]}, {'status':True,'data':{}}):
            with self.assertRaises(ValueError): NSEProvider.parse(doc,'5m',NOW)

    def test_continuous_trading_gap_still_blocks(self):
        bars = NSEProvider.parse(fixture(),'5m',NOW)
        bars = bars[:10]+bars[11:]
        r = report(bars,NOW,'TCS.NS',.05,100000,500)
        self.assertEqual(r['status'],'BLOCKED_INCOMPLETE_DATA')
        self.assertEqual(r['data_quality']['missing_bars'],['2026-10-23T10:05:00+05:30'])
        self.assertIsNone(r['plan'])
        with self.assertRaisesRegex(ValueError,'Missing intraday bars'):
            backtest(bars,'TCS.NS',.05,100000,500)

    def test_retry_is_bounded_and_access_denial_is_final(self):
        err = urllib.error.HTTPError('https://charting.nseindia.com',429,'limited',{},None)
        with patch('urllib.request.urlopen',side_effect=[err,response(SYMBOL),response(fixture())]) as call,patch('india.nse.time.sleep'):
            self.assertEqual(len(NSEProvider(NOW).history('TCS.NS','5m')),75)
            self.assertEqual(call.call_count,3)
        err = urllib.error.HTTPError('https://charting.nseindia.com',403,'denied',{},None)
        with patch('urllib.request.urlopen',side_effect=err) as call:
            with self.assertRaisesRegex(ValueError,'HTTP 403'):
                NSEProvider(NOW).history('TCS.NS','5m')
            self.assertEqual(call.call_count,1)
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('offline')) as call,patch('india.nse.time.sleep'):
            with self.assertRaisesRegex(ValueError,'network unavailable'):
                NSEProvider(NOW).history('TCS.NS','5m')
            self.assertEqual(call.call_count,2)

    def test_cli_real_fixture_analyze_history_and_backtest(self):
        from india.__main__ import main
        for command in ('analyze','history','backtest'):
            doc = fixture('tcs-nse-daily-2026-10-23.json') if command == 'history' else fixture()
            with self.subTest(command=command),patch('sys.argv',['india',command,'TCS.NS','--provider','nse','--tick','.05','--days','28','--as-of',NOW.isoformat()]),patch('urllib.request.urlopen',side_effect=[response(SYMBOL),response(doc)]),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(),0)
                result = json.loads(out.getvalue())
                self.assertIn('NSE public charting',result['source'])
                if command == 'history': self.assertEqual(len(result['bars']),14)
                if command == 'analyze': self.assertTrue(result['data_quality']['complete'])

    def test_invalid_configuration(self):
        for days in (0,121,True):
            with self.assertRaises(ValueError): NSEProvider(NOW,days)
        with self.assertRaises(ValueError): NSEProvider(NOW.replace(tzinfo=None))
