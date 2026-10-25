"""Original synthetic capacity scenarios; no inferred quotes or real fills."""
from dataclasses import replace
import datetime as dt
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from india.analysis import proposal
from india.audit import digest
from india.execution import replay_trade, size_position
from india.liquidity import capacity, normalize
from india.market import costs
from research.__main__ import main
from research.metrics import enrich
from research.promotion import assess
from research.strategies import BASELINE, execute, prepare
from workflow.paper import one_way_cost
import test_research_lab as lab_tests
from test_research_lab import policy, synthetic_sessions
from test_research_store import SOURCE, V2, V3, instrument


class SpreadCosts(unittest.TestCase):
    def test_half_spread_on_each_leg_with_no_second_price_adjustment(self):
        for exchange in ('NSE','BSE'):
            with self.subTest(exchange=exchange):
                before = costs(100,110,100,exchange)
                after = costs(100,110,100,exchange,dict(spread_bps=10))
                self.assertAlmostEqual(after['spread'],10.5)
                self.assertAlmostEqual(after['total']-before['total'],10.5)
                for key in before:
                    if key != 'total':
                        self.assertEqual(after[key],before[key])
                buy = one_way_cost(100,100,'buy',exchange,dict(spread_bps=10))
                sell = one_way_cost(110,100,'sell',exchange,dict(spread_bps=10))
                self.assertAlmostEqual(buy['spread'],5.)
                self.assertAlmostEqual(sell['spread'],5.5)
                self.assertAlmostEqual(buy['total']+sell['total'],after['total'])

    def test_zero_spread_preserves_default_values_and_invalid_costs_fail(self):
        before = costs(100,110,100)
        after = costs(100,110,100,config=dict(spread_bps=0))
        self.assertEqual(before,{k:v for k,v in after.items() if k != 'spread'})
        for value in (-1,True,float('nan'),float('inf'),'10'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                costs(100,110,100,config=dict(spread_bps=value))

    def test_spread_affects_sizing_risk_and_reported_net_pnl_once(self):
        _,rows = synthetic_sessions(1)[0]
        plan = proposal(rows[:4],.05,2.)
        plain = size_position(plan,100000,500,'NSE')
        config = dict(spread_bps=20)
        sized = size_position(plan,100000,500,'NSE',config)
        self.assertLess(sized['qty'],plain['qty'])
        trade = replay_trade(plan,rows[4:],100000,500,'NSE',config)
        self.assertEqual(trade['qty'],sized['qty'])
        self.assertEqual(trade['entry'],replay_trade(plan,rows[4:],100000,500,'NSE')['entry'])
        self.assertLessEqual(trade['max_loss_estimate'],500)
        charges = costs(trade['entry'],trade['exit'],trade['qty'],'NSE',config)
        self.assertAlmostEqual(trade['net_pnl'],(trade['exit']-trade['entry'])*trade['qty']-charges['total'])
        detailed = enrich(trade,rows,'NSE',config)
        self.assertEqual(detailed['modeled_spread_inr'],charges['spread'])
        self.assertEqual(detailed['modeled_slippage_inr'],charges['slippage'])


class CapacityModel(unittest.TestCase):
    def setUp(self):
        _,self.rows = synthetic_sessions(1)[0]
        self.plan = proposal(self.rows[:4],.05,2.)
        self.model = dict(max_participation=.1)

    def replay(self, rows=None, **changes):
        args = dict(prior_bar=self.rows[3],liquidity_config=self.model)
        args.update(changes)
        return replay_trade(self.plan,self.rows[4:] if rows is None else rows,100000,500,'NSE',**args)

    def test_configuration_is_explicit_bounded_and_has_no_unknown_fields(self):
        self.assertIsNone(normalize(None))
        self.assertEqual(normalize(dict(max_participation=1)),dict(max_participation=1.))
        for config in ({},[],{'max_participation':True},{'max_participation':0},
                       {'max_participation':1.01},{'max_participation':float('nan')},
                       {'max_participation':.1,'use_future_volume':True}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                normalize(config)

    def test_whole_share_capacity_uses_exact_decimal_floor(self):
        ref = replace(self.rows[3],volume=100)
        check = capacity(29,ref,self.rows[4].time,5,dict(max_participation=.29))
        self.assertEqual(check['maximum_quantity'],29)
        self.assertEqual(check['status'],'PROXY_PASSED')
        self.assertEqual(capacity(30,ref,self.rows[4].time,5,dict(max_participation=.29))['status'],'CAPACITY_EXCEEDED')

    def test_stale_missing_and_future_reference_never_pass(self):
        for ref in (None,self.rows[2],self.rows[4]):
            with self.subTest(reference=ref):
                self.assertEqual(capacity(1,ref,self.rows[4].time,5,self.model)['status'],'UNAVAILABLE')
        zero = replace(self.rows[3],volume=0)
        self.assertEqual(capacity(1,zero,self.rows[4].time,5,self.model)['maximum_quantity'],0)

    def test_oversized_entry_is_rejected_without_hindsight_resizing(self):
        diagnostics = {}
        trade = self.replay(liquidity_config=dict(max_participation=.001),diagnostics=diagnostics)
        self.assertIsNone(trade)
        self.assertEqual(diagnostics['reason'],'ENTRY_CAPACITY_LIMIT')
        self.assertEqual(diagnostics['capacity']['quantity'],171)
        self.assertEqual(diagnostics['capacity']['maximum_quantity'],2)

    def test_entry_screen_ignores_fill_candle_final_volume(self):
        normal = self.replay()
        changed = [replace(self.rows[4],volume=1)]+self.rows[5:]
        low_volume = self.replay(changed)
        self.assertEqual(normal['liquidity']['entry'],low_volume['liquidity']['entry'])
        self.assertEqual(normal['qty'],low_volume['qty'])
        self.assertEqual(normal['entry'],low_volume['entry'])
        self.assertEqual(normal['liquidity']['entry']['reference_time'],self.rows[3].time.isoformat())

    def test_exit_capacity_failure_keeps_gap_loss_and_fixed_quantity(self):
        thin = replace(self.rows[4],volume=1)
        gap = replace(self.rows[5],open=98,high=99,low=97,close=98,volume=1000000)
        trade = self.replay([thin,gap])
        self.assertEqual(trade['exit'],98)
        self.assertLess(trade['net_pnl'],-500)
        self.assertEqual(trade['qty'],171)
        self.assertEqual(trade['liquidity']['status'],'UNRESOLVED_EXIT_CAPACITY')
        self.assertEqual(trade['liquidity']['exit']['reference_volume'],1)
        self.assertEqual(trade['liquidity']['exit']['reference_time'],thin.time.isoformat())

    def test_same_bar_ambiguous_exit_uses_prior_known_volume_and_stop_first(self):
        bar = replace(self.rows[4],high=110,low=98,close=103,volume=1)
        trade = self.replay([bar])
        self.assertEqual(trade['outcome'],'stop')
        self.assertEqual(trade['exit'],self.plan['stop'])
        self.assertEqual(trade['liquidity']['status'],'PROXY_PASSED')
        self.assertEqual(trade['liquidity']['exit']['reference_time'],self.rows[3].time.isoformat())

    def test_delayed_submission_requires_fresh_completed_capacity_reference(self):
        at = self.rows[4].time+dt.timedelta(seconds=1)
        diagnostics = {}
        self.assertIsNone(self.replay(submitted_at=at,diagnostics=diagnostics))
        self.assertEqual(diagnostics['capacity']['status'],'UNAVAILABLE')
        trade = self.replay(submitted_at=at,prior_bar=self.rows[4])
        self.assertEqual(trade['entry_time'],self.rows[5].time.isoformat())

    def test_missing_exit_path_records_entered_exposure(self):
        for path in (self.rows[4:5],[self.rows[4],self.rows[6]],
                     [self.rows[4],replace(self.rows[5],volume=0)]):
            diagnostics = {}
            with self.assertRaisesRegex(ValueError,'Unresolved entered position'):
                self.replay(path,diagnostics=diagnostics)
            self.assertEqual(diagnostics['reason'],'UNRESOLVED_POSITION')
            self.assertEqual(diagnostics['quantity'],171)

    def test_unscreened_replay_remains_identical(self):
        before = replay_trade(self.plan,self.rows[4:],100000,500,'NSE')
        after = self.replay(liquidity_config=None)
        self.assertEqual(before,after)
        self.assertNotIn('liquidity',after)


class LaboratoryLiquidity(unittest.TestCase):
    setUp = lab_tests.Laboratory.setUp
    run_lab = lab_tests.Laboratory.run_lab

    def test_report_freezes_models_costs_and_capacity_diagnostics(self):
        report = self.run_lab(liquidity_config=dict(max_participation=.1),cost_config=dict(spread_bps=10))
        self.assertEqual(report,self.run_lab(liquidity_config=dict(max_participation=.1),cost_config=dict(spread_bps=10)))
        self.assertEqual(report['spec']['liquidity_config'],dict(max_participation=.1))
        test = report['variants'][0]['symbols']['DEMO.NS']['folds'][0]['test']
        self.assertEqual(test['liquidity']['screened_trades'],2)
        self.assertGreater(test['average_modeled_spread_inr'],0)
        self.assertEqual(report['report_sha256'],digest({k:v for k,v in report.items() if k != 'report_sha256'}))
        self.assertFalse(report['execution_enabled'])
        json.dumps(report,allow_nan=False)

    def test_zero_trades_keep_entry_rejection_evidence(self):
        report = self.run_lab(liquidity_config=dict(max_participation=.001))
        for variant in report['variants']:
            self.assertEqual(variant['pooled_trades'],0)
            rejected = variant['symbols']['DEMO.NS']['folds'][0]['test']['rejections']
            self.assertEqual(rejected[0]['reason'],'ENTRY_CAPACITY_LIMIT')
            self.assertEqual(rejected[0]['detail']['reference_time'][11:16],'09:30')

    def test_unresolved_exit_is_counted_and_blocks_research_promotion(self):
        changed = [replace(b,volume=1) if b.time.time() == dt.time(9,35) else b for b in self.bars]
        changed = [replace(b,open=98,high=99,low=97,close=98) if b.time.time() == dt.time(9,40) else b for b in changed]
        self.store.ingest('DEMO.NS',changed,source=SOURCE,observed_at=V3)
        report = self.run_lab(vintage=V3,liquidity_config=dict(max_participation=.1),
                              strategy_configs=[dict(name='five',or_minutes=5)])
        result = report['variants'][0]['symbols']['DEMO.NS']['out_of_sample']
        self.assertEqual(result['trades'],4)
        self.assertEqual(result['liquidity']['unresolved_exit_trades'],4)
        self.assertLess(result['net_pnl'],0)
        self.assertEqual(report['variants'][0]['liquidity']['inference_status'],'UNRESOLVED_CAPACITY')
        self.assertTrue(all(row['execution_status'] == 'UNRESOLVED_CAPACITY' for row in report['baseline_comparisons']))
        gate = assess(report,'RESEARCH',policy())
        self.assertFalse(gate['eligible'])
        self.assertTrue(any('unresolved exit capacity' in reason for reason in gate['reasons']))

    def test_missing_exit_execution_fails_instead_of_dropping_entered_trade(self):
        rows = list(self.days[0][1])
        rows[5] = replace(rows[5],volume=0)
        selected = prepare(rows,'DEMO.NS',.05,BASELINE)
        with self.assertRaisesRegex(ValueError,'Unresolved entered position'):
            execute(selected,rows,'DEMO.NS',.05,100000,500,2,strategy=BASELINE,liquidity_config=dict(max_participation=.1))

    def test_oos_validation_requires_explicit_spread_and_liquidity(self):
        gate = assess(self.run_lab(),'BACKTESTED',policy())
        self.assertTrue(any('liquidity scenario' in reason for reason in gate['reasons']))
        self.assertTrue(any('spread scenario' in reason for reason in gate['reasons']))
        report = self.run_lab(liquidity_config=dict(max_participation=.1),cost_config=dict(spread_bps=10))
        gate = assess(report,'BACKTESTED',policy())
        self.assertFalse(any('scenario is required' in reason for reason in gate['reasons']))

    def test_nse_bse_sources_costs_and_capacity_remain_independent(self):
        self.store.ingest('DEMO.BO',self.bars,source=dict(SOURCE,provider='synthetic-bse'),observed_at=V2)
        self.store.evidence('instrument',[instrument('DEMO.BO')],observed_at=V2)
        report = self.run_lab(liquidity_config=dict(max_participation=.1),cost_config=dict(spread_bps=10))
        symbols = report['variants'][0]['symbols']
        nse,bse = symbols['DEMO.NS']['trades'][0],symbols['DEMO.BO']['trades'][0]
        self.assertEqual(nse['liquidity'],bse['liquidity'])
        self.assertGreater(bse['realized_cost_estimate']['exchange'],nse['realized_cost_estimate']['exchange'])

    def test_offline_cli_accepts_liquidity_file_and_rejects_invalid_model(self):
        model = Path(self.temp.name)/'liquidity.json'
        model.write_text(json.dumps(dict(max_participation=.1)))
        args = ['walk-forward','--db',str(self.path),'--start',str(self.days[0][0]),'--end',str(self.days[-1][0]),
                '--as-of',V3,'--vintage',V2,'--train-sessions','2','--test-sessions','2',
                '--liquidity-config',str(model),'--bootstrap-samples','50','--block-days','1']
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(args),0)
            self.assertEqual(json.loads(output.getvalue())['spec']['liquidity_config']['max_participation'],.1)
        model.write_text('{"max_participation":false}')
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(args),1)
            self.assertEqual(json.loads(output.getvalue())['status'],'UNAVAILABLE')
