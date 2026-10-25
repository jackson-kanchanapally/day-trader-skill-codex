"""G03/G04: sourced sessions, closing-position safety and alert event clocks."""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from india.market import Calendar
from workflow.cli import main, market_status
from workflow.common import clock, config, write
from workflow.paper import Ledger, risk, ticket
import test_workflow as fixtures


def calendar():
    return Calendar({'NSE':{'2026':dict(source='Original synthetic session calendar',verified_on='2026-10-01',
        holidays=['2026-10-20'],sessions={
        '2026-10-24':dict(open='18:00',close='19:00',source='Original synthetic evening'),
        '2026-10-23':dict(open='09:15',close='12:00',source='Original synthetic shortened session')})}})


class SessionRisk(unittest.TestCase):
    def setUp(self):
        w = fixtures.Workflow()
        w.setUp()
        self.cfg,self.master = w.cfg,w.master
        self.now = clock('2026-10-24T18:30:00+05:30')
        self.pf = dict(paper=True,currency='INR',cash=99000,positions=[dict(ticker='DEMO.NS',shares=10,lots=[dict(shares=10,cost=1000)])],realized_pnl=0)
        self.sell = dict(symbol='DEMO.NS',side='sell',qty='4',type='market',time_in_force='day',tick=.05)
        self.prices = dict(prices={'DEMO.NS':105},detail={'DEMO.NS':dict(time=self.now.isoformat())})

    def review(self, **changes):
        args = dict(orders=[self.sell],now=self.now,cfg=self.cfg,portfolio=self.pf,prices_doc=self.prices,master=self.master,calendar=calendar())
        args.update(changes)
        return risk(**args)

    def test_evening_close_passes_but_new_buy_stays_blocked(self):
        self.assertEqual(self.review()['pass'],1)
        buy = dict(self.sell,side='buy')
        with self.assertRaisesRegex(ValueError,'strategy|ORB'):
            self.review(orders=[buy])

    def test_closing_safety_rejects_oversell_bad_tick_stale_quote_and_holiday(self):
        cases = [dict(orders=[dict(self.sell,qty='11')]),dict(master={}),
                 dict(master={'DEMO.NS':dict(self.master['DEMO.NS'],effective_to='2026-10-23')}),
                 dict(prices_doc=dict(self.prices,detail={'DEMO.NS':dict(time=(self.now-dt.timedelta(minutes=16)).isoformat())})),
                 dict(now=clock('2026-10-20T10:00:00+05:30'))]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.review(**changes)['block'],1)

    def test_unknown_calendar_coverage_fails_safely(self):
        with self.assertRaises(ValueError):
            self.review(calendar=Calendar({'BSE':{'2026':dict(source='synthetic',verified_on='2026-01-01',holidays=[])}}))

    def test_approved_partial_position_close_preserves_remaining_cash_and_lots(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'state/paper.sqlite',100000)
            self.addCleanup(ledger.close)
            with ledger.db:
                ledger.db.execute('UPDATE state SET document=? WHERE id=1',(json.dumps(dict(self.pf,initial_capital=100000)),))
            path=Path(directory)
            write(path/'orders.json',dict(orders=[self.sell]))
            write(path/'calendar.json',calendar().document)
            write(path/'metadata.json',list(self.master.values()))
            (path/'bars.csv').write_text('symbol,interval,time,open,high,low,close,volume\nDEMO.NS,5m,2026-10-24T18:25:00+05:30,105,106,104,105,1000\n')
            args=['--workspace',directory,'--now',self.now.isoformat(),'--capital','100000',
                  '--calendar',str(path/'calendar.json'),'--security-master',str(path/'metadata.json'),'--csv',str(path/'bars.csv')]
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main('risk',args),0,out.getvalue())
                self.assertEqual(json.loads(out.getvalue())['pass'],1)
            review = self.review(portfolio=ledger.portfolio())
            proposal = ticket(self.sell,review,ledger.portfolio(),self.now,self.cfg,self.prices,self.master,calendar())
            self.assertTrue(proposal['ready'])
            with self.assertRaisesRegex(ValueError,'approval'):
                ledger.fill(proposal['fingerprint'],4,105,self.now,self.cfg,'unapproved')
            ledger.approve(proposal,'yes',self.now)
            event = ledger.fill(proposal['fingerprint'],4,105,self.now,self.cfg,'close-four')
            self.assertEqual(ledger.portfolio()['positions'][0]['shares'],6)
            self.assertAlmostEqual(ledger.portfolio()['cash'],99000+420-event['fees'])
            self.assertAlmostEqual(event['realized_pnl'],20-event['fees'])
            self.assertFalse(ticket(self.sell,review,ledger.portfolio(),self.now,self.cfg,self.prices,
                                    self.master,calendar(),state=ledger.state())['ready'])
            with self.assertRaises(ValueError):
                ledger.fill(proposal['fingerprint'],4,105,self.now,self.cfg,'duplicate')


class SessionAlerts(unittest.TestCase):
    def test_regular_evening_shortened_and_holiday_metadata(self):
        for value,expected in (('2026-10-19T10:00:00+05:30',('09:15','15:30',True)),
                               ('2026-10-24T18:30:00+05:30',('18:00','19:00',True)),
                               ('2026-10-23T11:00:00+05:30',('09:15','12:00',True)),
                               ('2026-10-20T10:00:00+05:30',(None,None,False))):
            with self.subTest(value=value):
                result = market_status(clock(value),calendar(),'NSE')
                self.assertEqual((result['opens'],result['closes'],result['open']),expected)

    def test_next_open_skips_known_weekend_holiday_and_respects_evening(self):
        result = market_status(clock('2026-10-16T16:00:00+05:30'),calendar(),'NSE')
        self.assertEqual(result['next_open'],'2026-10-19T09:15:00+05:30')
        result = market_status(clock('2026-10-19T16:00:00+05:30'),calendar(),'NSE')
        self.assertEqual(result['next_open'],'2026-10-21T09:15:00+05:30')
        result = market_status(clock('2026-10-24T10:00:00+05:30'),calendar(),'NSE')
        self.assertEqual(result['next_open'],'2026-10-24T18:00:00+05:30')
        result = market_status(clock('2026-12-31T16:00:00+05:30'),calendar(),'NSE')
        self.assertIsNone(result['next_open'])
        self.assertIn('coverage',result['next_open_reason'].lower())
        expired=calendar().document
        expired['NSE']['2026']['valid_until']='2026-10-23'
        with self.assertRaisesRegex(ValueError,'expired'):
            market_status(clock('2026-10-24T18:30:00+05:30'),Calendar(expired),'NSE')

    def test_cli_opening_event_once_and_shortened_before_close_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write(path/'calendar.json',calendar().document)
            write(path/'alerts.json',dict(alerts=[dict(type='opening_bell',id='bell')]))
            args = ['check','--workspace',directory,'--calendar',str(path/'calendar.json'),'--now','2026-10-24T18:30:00+05:30']
            for expected in (1,0):
                with patch('sys.stdout',new_callable=io.StringIO) as out:
                    self.assertEqual(main('alerts',args),0)
                    result = json.loads(out.getvalue())
                    self.assertEqual(len(result['rings']),expected)
                    self.assertEqual(result['market']['closes'],'19:00')
            revised=calendar().document
            revised['NSE']['2026']['sessions']['2026-10-24']['open']='18:10'
            write(path/'calendar.json',revised)
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main('alerts',args),0)
                self.assertEqual(len(json.loads(out.getvalue())['rings']),1)
            write(path/'alerts.json',dict(alerts=[dict(type='time',id='flatten',at='15:10',before_close=True)]))
            args[-1] = '2026-10-23T11:40:00+05:30'
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main('alerts',args),0)
                self.assertEqual(len(json.loads(out.getvalue())['rings']),1)
