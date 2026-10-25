"""G06: whole-share account/quote-aware proposals with unchanged later gates."""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from india.execution import validate_fill
from workflow.cli import main
from workflow.common import config, write
from workflow.paper import Ledger, budget, ticket
import test_workflow as fixtures


class AccountSizing(unittest.TestCase):
    def setUp(self):
        self.w=fixtures.Workflow()
        self.w.setUp()
        self.now=fixtures.NOW
        self.p=dict(self.w.p,entry=100,stop=95,target=115)
        self.cfg=config(budget=100000,cash_buffer_pct=0,max_position_pct=100)
        self.pf=dict(paper=True,currency='INR',cash=5000,positions=[],realized_pnl=0)

    def test_cash_and_exposure_context_bound_cost_inclusive_sizing(self):
        row=budget(self.p,self.now,self.cfg,portfolio=self.pf)
        self.assertLessEqual(row['sizing']['qty']*row['sizing']['estimated_entry']+row['sizing']['costs_at_target']['total'],5000)
        self.assertEqual(row['context']['cash_basis'],'supplied-paper-snapshot')
        poor=budget(self.p,self.now,self.cfg,portfolio=dict(self.pf,cash=1))
        self.assertFalse(poor['orders'])
        held=dict(self.pf,positions=[dict(ticker='DEMO.NS',shares=9,lots=[dict(shares=9,cost=900)])])
        cfg=config(budget=1000,cash_buffer_pct=0,max_position_pct=95)
        self.assertFalse(budget(self.p,self.now,cfg,portfolio=held)['orders'])
        quote=dict(prices={'DEMO.NS':200},detail={'DEMO.NS':dict(time=self.now.isoformat())})
        self.assertFalse(budget(self.p,self.now,config(budget=1000,cash_buffer_pct=0,max_position_pct=100),
                                portfolio=held,prices_doc=quote,limit_price=99.5)['orders'])

    def test_minimum_order_uses_limit_not_original_signal_value(self):
        cfg=config(budget=100000,cash_buffer_pct=0,max_position_pct=100,max_loss_per_trade_inr=5,min_order_inr=100)
        result=budget(self.p,self.now,cfg,limit_price=99.5)
        self.assertEqual(result['sizing']['qty'],1)
        self.assertEqual(result['reason'],'BELOW_MIN_ORDER')
        self.assertFalse(result['orders'])

    def test_fresh_price_and_cost_risk_monotonicity(self):
        base=budget(self.p,self.now,self.cfg,portfolio=self.pf)
        quote=dict(prices={'DEMO.NS':102},detail={'DEMO.NS':dict(time=self.now.isoformat())})
        worse=budget(self.p,self.now,self.cfg,portfolio=self.pf,prices_doc=quote)
        costly=budget(self.p,self.now,dict(self.cfg,costs={'slippage_bps':50}),portfolio=self.pf)
        self.assertLessEqual(worse['sizing']['qty'],base['sizing']['qty'])
        self.assertLessEqual(costly['sizing']['qty'],base['sizing']['qty'])
        stale=copy.deepcopy(quote)
        stale['detail']['DEMO.NS']['time']=(self.now-dt.timedelta(minutes=16)).isoformat()
        with self.assertRaisesRegex(ValueError,'fresh'):
            budget(self.p,self.now,self.cfg,prices_doc=stale)
        future=copy.deepcopy(quote)
        future['detail']['DEMO.NS']['time']=(self.now+dt.timedelta(minutes=1)).isoformat()
        for invalid in (future,{}):
            with self.assertRaisesRegex(ValueError,'fresh'):
                budget(self.p,self.now,self.cfg,prices_doc=invalid)
        self.assertEqual(budget(self.p,self.now,self.cfg)['context']['cash_basis'],'configured-capital-estimate')

    def test_limit_is_tick_aligned_bounded_and_not_assumed_filled(self):
        quote=dict(prices={'DEMO.NS':102},detail={'DEMO.NS':dict(time=self.now.isoformat())})
        row=budget(self.p,self.now,self.cfg,portfolio=self.pf,prices_doc=quote,limit_price=99.5)
        order=row['orders'][0]
        self.assertEqual(order['type'],'limit')
        self.assertEqual(float(order['limit_price']),99.5)
        self.assertEqual(row['sizing']['estimated_entry'],99.5)
        self.assertFalse(row['context']['assumed_filled'])
        with self.assertRaisesRegex(ValueError,'tick'):
            budget(self.p,self.now,self.cfg,limit_price=99.51)

    def test_valid_limit_proposal_reaches_exact_ticket_and_adverse_fill_stays_blocked(self):
        row=budget(self.w.p,self.now,self.w.cfg,portfolio=self.w.pf,limit_price=102.3)
        order=row['orders'][0]
        review=self.w.review(orders=[order])
        self.assertEqual(review['pass'],1)
        self.assertTrue(ticket(order,review,self.w.pf,self.now,self.w.cfg,self.w.prices,self.w.master)['ready'])
        with self.assertRaises(ValueError):
            validate_fill(order['plan'],106,int(order['qty']),100000,500,'NSE')
        changed=copy.deepcopy(self.w.prices)
        changed['prices']['DEMO.NS']=102.35
        self.assertFalse(ticket(order,review,self.w.pf,self.now,self.w.cfg,changed,self.w.master)['ready'])

    def test_cli_limit_prices_and_portfolio_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            write(path/'plan.json',self.p)
            write(path/'portfolio.json',self.pf)
            write(path/'config.json',self.cfg)
            quote=dict(prices={'DEMO.NS':102},detail={'DEMO.NS':dict(time=self.now.isoformat())})
            write(path/'quote.json',quote)
            args=['--workspace',directory,'--now',self.now.isoformat(),'--capital','100000','--limit','99.5',
                  '--portfolio',str(path/'portfolio.json'),'--prices',str(path/'quote.json')]
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main('budget',args),0,out.getvalue())
                self.assertEqual(json.loads(out.getvalue())['orders'][0]['type'],'limit')

    def test_approved_limit_fill_keeps_quantity_and_rolls_back_adverse_open(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=Ledger(Path(directory)/'paper.sqlite',100000)
            self.addCleanup(ledger.close)
            pf=ledger.portfolio()
            row=budget(self.w.p,self.now,self.w.cfg,portfolio=pf,limit_price=102.3)
            order=row['orders'][0]
            review=self.w.review(orders=[order],portfolio=pf)
            proposal=ticket(order,review,pf,self.now,self.w.cfg,self.w.prices,self.w.master)
            self.assertTrue(proposal['ready'])
            ledger.approve(proposal,'yes',self.now)
            from dataclasses import replace
            adverse=replace(self.w.bars[4],open=103,high=104,low=102,close=103)
            with self.assertRaises(ValueError):
                ledger.fill(proposal['fingerprint'],int(order['qty']),103,self.now,self.w.cfg,'bad',bar=adverse)
            self.assertEqual(ledger.portfolio(),pf)
            fill=ledger.fill(proposal['fingerprint'],int(order['qty']),102.3,self.now,self.w.cfg,'good',bar=self.w.bars[4])
            self.assertEqual(fill['qty'],int(order['qty']))
            with self.assertRaises(ValueError):
                ledger.fill(proposal['fingerprint'],int(order['qty']),102.3,self.now,self.w.cfg,'again',bar=self.w.bars[4])

    def test_existing_ledger_is_authoritative_and_mismatched_export_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            ledger=Ledger(path/'state/paper.sqlite',100000)
            ledger.close()
            write(path/'plan.json',self.p)
            write(path/'wrong.json',self.pf)
            args=['--workspace',directory,'--now',self.now.isoformat(),'--capital','100000','--portfolio',str(path/'wrong.json')]
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main('budget',args),1)
                self.assertIn('authoritative',out.getvalue())
