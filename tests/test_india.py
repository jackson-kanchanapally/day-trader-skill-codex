import datetime as dt
import unittest
from pathlib import Path
from india.data import CSVProvider, timestamp, Bar, validate
from india.analysis import report, backtest, indicators
from india.market import session, costs

class India(unittest.TestCase):
    def setUp(self):
        self.bars=CSVProvider(Path(__file__).parent/'fixtures/india/synthetic.csv').history('DEMO.NS','5m')
        self.now=timestamp('2026-10-19T09:35:00+05:30')
    def test_signal_and_cost_sizing(self):
        r=report(self.bars,self.now,'DEMO.NS',.05,100000,500)
        self.assertEqual(r['status'],'PAPER_SETUP')
        self.assertLessEqual(r['plan']['max_loss_estimate'],500)
        self.assertFalse(r['execution_enabled'])
    def test_no_lookahead(self):
        a=report(self.bars,self.now,'DEMO.NS',.05,100000,500)
        self.assertEqual(a,report(self.bars[:4],self.now,'DEMO.NS',.05,100000,500))
        self.assertEqual(backtest(self.bars[:4],'DEMO.NS',.05,100000,500)['trades'],[])
        trades=backtest(self.bars,'DEMO.NS',.05,100000,500)['trades']
        self.assertEqual(len(trades),1)
        self.assertAlmostEqual(trades[0]['entry'],self.bars[4].open)  # normalized provider float noise
    def test_missing_and_stale(self):
        gap=report(self.bars[:2]+self.bars[3:],self.now,'DEMO.NS',.05,100000,500)
        self.assertEqual(gap['status'],'BLOCKED_INCOMPLETE_DATA')
        self.assertIsNone(gap['plan'])
        self.assertEqual(gap['data_quality']['missing_bars'],['2026-10-19T09:25:00+05:30'])
        self.assertEqual(gap['indicators'],indicators(self.bars[:2]))
        r=report(self.bars[:4],timestamp('2026-10-19T10:00:00+05:30'),'DEMO.NS',.05,100000,500)
        self.assertTrue(r['stale']);self.assertIsNone(r['plan'])
        with self.assertRaises(ValueError):validate([])
    def test_hours(self):
        self.assertTrue(session(timestamp('2026-10-19T09:15:00+05:30')))
        self.assertFalse(session(timestamp('2026-10-19T15:30:00+05:30')))
        self.assertFalse(session(timestamp('2026-10-20T10:00:00+05:30')))
        self.assertFalse(session(timestamp('2026-10-18T10:00:00+05:30')))
    def test_missing_open_blocks_all_indicators(self):
        r=report(self.bars[1:],self.now,'DEMO.NS',.05,100000,500)
        self.assertEqual(r['status'],'BLOCKED_INCOMPLETE_DATA')
        self.assertIsNone(r['plan'])
        self.assertIsNone(r['indicators_as_of'])
        self.assertTrue(all(v is None for v in r['indicators'].values()))
    def test_backtest_exits_before_auction(self):
        bars=self.bars[:5]
        when=timestamp('2026-10-19T09:40:00+05:30')
        while when.time() < dt.time(15,15):
            bars.append(Bar(when,103,103.2,102.8,103,1000))
            when += dt.timedelta(minutes=5)
        # Auction prices cannot turn an earlier paper trade into a target hit.
        bars.append(Bar(when,200,201,199,200,1000000))
        result=backtest(bars,'DEMO.NS',.05,100000,500)
        self.assertEqual(len(result['trades']),1)
        self.assertEqual(result['trades'][0]['exit'],103)
    def test_costs(self):
        c=costs(100,110,100)
        self.assertAlmostEqual(c['stt'],2.75)
        self.assertAlmostEqual(c['stamp'],.3)
        self.assertAlmostEqual(c['brokerage'],6.3)
        self.assertAlmostEqual(c['gst'],(6.3+21000*.0000307+.021)*.18)
        self.assertGreater(costs(100,110,100,'BSE')['exchange'],c['exchange'])
    def test_indicators(self):
        i=indicators(self.bars[:3]);self.assertIsNone(i['rsi14'])
        self.assertAlmostEqual(i['vwap'],(100.2+99.7+100)/3)
        self.assertIsNotNone(indicators(self.bars[:22])['rsi14'])

class ProviderData(unittest.TestCase):
    def test_trailing_empty_and_interior_missing(self):
        import io,json
        from unittest.mock import patch
        from india.data import YahooProvider
        q={k:[100, None] for k in ('open','high','low','close','volume')}
        result={'meta':{'symbol':'DEMO.NS','currency':'INR'},'timestamp':[1792381500,1792381800], 'indicators':{'quote':[q]}}
        def response(): return io.StringIO(json.dumps({'chart':{'result':[result]}}))
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k:response()):
            self.assertEqual(len(YahooProvider().history('DEMO.NS','5m')),1)
            for k in q: q[k].append(100)
            result['timestamp'].append(1792382100)
            self.assertEqual(len(YahooProvider().history('DEMO.NS','5m')),2)
            q['close'][1]=100  # A partially populated candle remains an error.
            with self.assertRaisesRegex(ValueError,'missing OHLCV'):YahooProvider().history('DEMO.NS','5m')
