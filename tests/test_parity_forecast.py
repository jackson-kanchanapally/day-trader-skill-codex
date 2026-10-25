"""G01/G02: active forecast event ordering, fees and completed-bar horizons."""
import datetime as dt
from dataclasses import replace
import unittest
from unittest.mock import patch

from india.data import Bar, IST
from india.execution import exit_fill, replay_trade
from india.forecast import _simulate
from india.market import costs
from workflow.common import config
from workflow.strategy import timemachine
import test_forecast as fixtures
import test_workflow as workflow_fixtures
import test_team as team_fixtures


class ExactDraw:
    def __init__(self, seed): pass
    def randint(self, a, b): return 0
    def choice(self, values): return values[0]


class ScenarioEvents(unittest.TestCase):
    def test_cross_engine_replay_gross_exit_and_net_costs(self):
        opening = dt.datetime(2026,10,19,9,15,tzinfo=IST)
        for values in ((92,105,90,100),(115,118,94,108),(100,112,94,103),(101,112,99,111)):
            with self.subTest(values=values):
                rows=[Bar(opening+dt.timedelta(minutes=5*i),100,101,99,100,1000) for i in range(72)]
                rows[70]=Bar(rows[70].time,*values,1000)
                p=dict(entry=100,stop=95,target=110,tick=.05,signal_time=rows[68].time.isoformat(),flat_by='15:15')
                charges=dict(spread_bps=10)
                replay=replay_trade(p,rows[69:],100000,500,'NSE',charges,buffer_bps=0)
                with patch('india.forecast.random.Random',ExactDraw):
                    simulated=_simulate([rows],dict(p,qty=replay['qty'],max_loss_estimate=replay['max_loss_estimate']),70,8,19,'NSE',charges)
                self.assertEqual(simulated['counts'][replay['outcome']],8)
                self.assertAlmostEqual(simulated['mean_net_pnl'],replay['net_pnl'])

    def test_tc_g01_01_to_06_canonical_events_and_scenario_cost_parity(self):
        cases = [((92,105,90,100),'stop',92),((115,118,94,108),'target',110),
                 ((100,112,94,103),'stop',95),((101,112,99,111),'target',110),
                 ((100,108,97,103),None,None)]
        opening = dt.datetime(2026,10,19,9,15,tzinfo=IST)
        for values,outcome,price in cases:
            with self.subTest(values=values):
                plan = dict(entry=100,stop=95,target=110,qty=10,max_loss_estimate=50,flat_by='15:15')
                bar = Bar(opening+dt.timedelta(minutes=20),*values,1000)
                event = exit_fill(plan,bar)
                self.assertEqual(event['outcome'] if event else None,outcome)
                if event: self.assertEqual(event['exit'],price)
                rows = [Bar(opening+dt.timedelta(minutes=i*5),100,101,99,100,1000) for i in range(72)]
                rows[-1] = replace(bar,time=rows[-1].time)
                with patch('india.forecast.random.Random',ExactDraw):
                    result = _simulate([rows],plan,71,8,19,'NSE',dict(spread_bps=10))
                marked = price if price is not None else values[3]
                self.assertEqual(result['counts'][outcome or 'close'],8)
                self.assertAlmostEqual(result['mean_net_pnl'],(marked-100)*10-costs(100,marked,10,'NSE',dict(spread_bps=10))['total'])
                if price == 92: self.assertLess(result['avg_R'],-1)


class ForecastHorizons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.Forecast.setUpClass()

    def run_forecast(self, **changes):
        return fixtures.Forecast().run_forecast(sims=8,**changes)

    def test_configured_horizons_match_paths_bands_and_counts(self):
        for flat,remaining in (('10:00',5),('15:10',67),('15:15',68)):
            with self.subTest(flat=flat):
                result = self.run_forecast(plan=dict(fixtures.Forecast.plan,flat_by=flat))
                self.assertEqual(result['status'],'AVAILABLE')
                self.assertEqual(result['mc']['until'],flat)
                self.assertEqual(result['mc']['bars_left'],remaining)
                self.assertEqual(len(result['mc']['bands']),remaining+1)
                self.assertTrue(all(len(p)==remaining+1 for p in result['mc']['sample_paths']))
                self.assertEqual(sum(result['mc']['counts'].values()),8)

    def test_after_flatten_history_cannot_affect_price_paths_or_pnl(self):
        plan = dict(fixtures.Forecast.plan,flat_by='10:00')
        before = self.run_forecast(plan=plan)
        changed = [replace(b,open=10000,high=11000,low=9000,close=10000)
                   if b.time.date()<fixtures.Forecast.now.date() and b.time.time()>=dt.time(10) else b
                   for b in fixtures.Forecast.bars]
        after = self.run_forecast(plan=plan,bars=changed)
        self.assertEqual(before['mc'],after['mc'])
        self.assertEqual(before['history'],after['history'])

    def test_off_grid_flatten_is_rejected_not_rounded_forward(self):
        with self.assertRaisesRegex(ValueError,'align'):
            self.run_forecast(plan=dict(fixtures.Forecast.plan,flat_by='10:02'))

    def test_no_horizon_returns_unavailable_without_paths(self):
        result = self.run_forecast(plan=dict(fixtures.Forecast.plan,flat_by='09:35'))
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertEqual(result['reason'],'NO_REMAINING_BARS')
        self.assertNotIn('sample_paths',result['mc'])

    def test_modular_rejects_expired_plan_and_accepts_explicit_boundary(self):
        w = workflow_fixtures.Workflow()
        w.setUp()
        bars = [b for _,rows in w.full_days() for b in rows]+w.bars
        result = timemachine(bars,w.p,fixtures.Forecast.now+dt.timedelta(minutes=6),w.cfg,sims=8)
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertEqual(result['reason'],'STALE_PLAN')
        result = timemachine(bars,dict(w.p,flat_by='15:15'),fixtures.Forecast.now,w.cfg,sims=8)
        self.assertEqual(result['status'],'AVAILABLE')
        self.assertEqual(result['mc']['until'],'15:15')

    def test_default_flatten_and_config_explicit_1515(self):
        self.assertEqual(self.run_forecast()['mc']['until'],'15:10')
        self.assertEqual(config(flat_by='15:15')['flat_by'],'15:15')

    def test_sealed_team_numerical_forecast_and_alert_agree_on_default_flatten(self):
        team_fixtures.Team.setUpClass()
        t=team_fixtures.Team()
        t.setUp()
        try:
            t.prepare(rich=True)
            from india.team import evaluate
            mc=evaluate(t.directory,'timemachine')['data']['mc']
            alerts=evaluate(t.directory,'alerts')['data']['alerts']
            self.assertEqual(mc['until'],'15:10')
            self.assertEqual(next(a['level'] for a in alerts if a['kind']=='flat_by'),'15:10')
        finally:
            t.doCleanups()
