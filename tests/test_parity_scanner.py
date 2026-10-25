"""G05: current opportunities require the expected completed exchange session."""
import datetime as dt
from dataclasses import replace
import unittest

from india.data import Bar, IST
from india.market import DEFAULT_CALENDAR
from workflow.common import clock, config
from workflow.data import parse_yahoo
from workflow.strategy import scan


def daily_rows(end):
    days = []
    while len(days)<40:
        if DEFAULT_CALENDAR.trading_day(end): days.append(end)
        end -= dt.timedelta(days=1)
    return [Bar(dt.datetime.combine(day,dt.time(9,15),IST),100+i,101+i,99+i,100+i,1000) for i,day in enumerate(reversed(days))]


class DailyFreshness(unittest.TestCase):
    def run_scan(self,now,rows,other=None):
        class Data:
            source='Original synthetic daily history'
            def history(self,symbol,interval):
                if symbol=='BROKEN.NS': raise OSError('Synthetic provider failure')
                return other if symbol=='OTHER.NS' else rows
        symbols=['DEMO.NS'] if other is None else ['DEMO.NS','OTHER.NS','BROKEN.NS']
        return scan(Data(),symbols,clock(now),config(),mode='daily')

    def test_latest_weekend_holiday_and_historical_cutoffs(self):
        for cutoff,end in (('2026-10-19T10:00:00+05:30','2026-10-16'),
                           ('2026-10-18T10:00:00+05:30','2026-10-16'),
                           ('2026-10-20T10:00:00+05:30','2026-10-19'),
                           ('2026-10-01T10:00:00+05:30','2026-09-30')):
            with self.subTest(cutoff=cutoff):
                result=self.run_scan(cutoff,daily_rows(dt.date.fromisoformat(end)))
                self.assertEqual(len(result['hits']),1)
                self.assertEqual(result['results'][0]['expected_session'],end)
                self.assertEqual(result['results'][0]['status'],'MATCH')

    def test_stale_missing_and_failed_symbols_remain_separate_from_fresh_hits(self):
        recent=daily_rows(dt.date(2026,10,16))
        for old in (daily_rows(dt.date(2026,8,31)),recent[:-1]):
            with self.subTest(last=old[-1].time):
                result=self.run_scan('2026-10-19T10:00:00+05:30',old,recent)
                self.assertEqual([r['symbol'] for r in result['hits']],['OTHER.NS'])
                self.assertEqual(len(result['stale']),1)
                self.assertEqual(result['stale'][0]['symbol'],'DEMO.NS')
                self.assertEqual(result['failed'][0]['symbol'],'BROKEN.NS')

    def test_no_setup_unavailable_and_future_are_not_current_matches(self):
        rows=daily_rows(dt.date(2026,10,16))
        flat=[replace(b,open=100,high=101,low=99,close=100) for b in rows]
        result=self.run_scan('2026-10-19T10:00:00+05:30',flat)
        self.assertEqual(result['results'][0]['status'],'NO_SETUP')
        result=self.run_scan('2026-10-19T10:00:00+05:30',[])
        self.assertEqual(result['results'][0]['status'],'UNAVAILABLE')
        future=rows+[replace(rows[-1],time=clock('2026-10-21T09:15:00+05:30'))]
        result=self.run_scan('2026-10-19T10:00:00+05:30',future)
        self.assertEqual(result['results'][0]['status'],'FAILED')
        self.assertFalse(result['hits'])

    def test_current_daily_bar_only_available_after_actual_session_close(self):
        rows=daily_rows(dt.date(2026,10,19))
        before=self.run_scan('2026-10-19T15:29:00+05:30',rows)
        self.assertEqual(before['hits'][0]['as_of'][:10],'2026-10-16')
        after=self.run_scan('2026-10-19T15:30:00+05:30',rows)
        self.assertEqual(after['hits'][0]['as_of'][:10],'2026-10-19')
        b=rows[-1]
        doc=dict(chart=dict(result=[dict(meta=dict(symbol='DEMO.NS',currency='INR',dataGranularity='1d'),
             timestamp=[int(b.time.timestamp())],indicators=dict(quote=[dict(open=[b.open],high=[b.high],low=[b.low],close=[b.close],volume=[b.volume])]))]))
        self.assertEqual(len(parse_yahoo(doc,'DEMO.NS','1d',clock('2026-10-19T15:30:00+05:30'))),1)
