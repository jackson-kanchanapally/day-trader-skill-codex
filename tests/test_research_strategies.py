"""Original synthetic ORB experiments; no downloaded data or profitability claims."""
from dataclasses import replace
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from india.analysis import backtest
from india.audit import digest
from research.__main__ import main
from research.lab import experiment
from research.metrics import paired_daily_difference
from research.store import CandleStore
from research.strategies import BASELINE, execute, filters, normalize, prepare
from test_research_lab import synthetic_sessions
from test_research_store import SOURCE, V2, V3, instrument

ROOT = Path(__file__).resolve().parents[1]


def normalized(**fields):
    return normalize([dict(name='candidate',**fields)])[-1]


class Configurations(unittest.TestCase):
    def test_baseline_is_automatic_and_immutable(self):
        values = normalize([dict(name='five',or_minutes=5)])
        self.assertEqual(values[0],BASELINE)
        self.assertEqual(values[1].or_minutes,5)
        with self.assertRaisesRegex(ValueError,'reserved'):
            normalize([dict(name='baseline',or_minutes=5)])
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            normalize([dict(name='renamed-baseline')])

    def test_invalid_or_filters_stops_and_clocks_fail(self):
        invalid = [dict(or_minutes=10),dict(or_minutes=True),dict(require_vwap='false'),
                   dict(ema_fast=9),dict(ema_fast=21,ema_slow=9),dict(ema_fast=1,ema_slow=73),
                   dict(min_rvol=0),dict(min_rvol=float('nan')),dict(rvol_mode='previous_bar'),
                   dict(rvol_lookback=3,rvol_min_samples=5),dict(min_atr_pct=2,max_atr_pct=1),
                   dict(stop='price:99'),dict(stop='atr:-1'),dict(flat_by='15:08'),
                   dict(entry_cutoff='15:11',flat_by='15:10'),dict(entry_start=None),
                   dict(entry_start='09:15+05:30'),dict(or_minutes=30,entry_cutoff='09:35',flat_by='09:40'),dict(regime='bullish')]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                normalized(**fields)

    def test_numeric_and_stop_aliases_are_normalized(self):
        a = normalized(stop='pct:1.00',confirmation_bps=10)
        b = normalized(stop='pct:1',confirmation_bps=10.,entry_start='09:15:00',flat_by='15:10:00')
        self.assertEqual(a.document(),b.document())
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            normalize([dict(name='a',stop='pct:1.00'),dict(name='b',stop='pct:1')])


class Signals(unittest.TestCase):
    def setUp(self):
        self.day,self.rows = synthetic_sessions(1)[0]

    def replay(self, strategy, rows=None, prior=None, actions=None):
        rows = self.rows if rows is None else rows
        selected = prepare(rows,'DEMO.NS',.05,strategy,prior=prior,actions=actions)
        result = execute(selected,rows,'DEMO.NS',.05,100000,500,2,strategy=strategy)
        return selected,result

    def test_baseline_exactly_matches_established_backtest_trade(self):
        selected,result = self.replay(BASELINE)
        self.assertEqual(result['trades'],backtest(self.rows,'DEMO.NS',.05,100000,500)['trades'])
        self.assertEqual(selected['index'],3)
        self.assertEqual(result['trades'][0]['qty'],171)

    def test_five_fifteen_thirty_minute_ranges_use_completed_bars(self):
        # Original synthetic early breakout, above the opening high after 5m.
        rows = list(self.rows)
        rows[1] = replace(rows[1],open=100.2,high=100.5,low=100.1,close=100.4)
        for minutes,index in ((5,1),(15,3),(30,8)):
            spec = normalized(or_minutes=minutes) if minutes != 15 else BASELINE
            selected,result = self.replay(spec,rows)
            self.assertEqual(selected['index'],index)
            if result['trades']:
                self.assertEqual(result['trades'][0]['entry_time'],rows[index+1].time.isoformat())

    def test_confirmation_threshold_can_select_a_later_close(self):
        selected,_ = self.replay(normalized(confirmation_bps=220))
        self.assertGreater(selected['index'],3)
        self.assertTrue(selected['candidates'][0]['reasons'])
        accepted = selected['candidates'][-1]
        self.assertTrue(accepted['passed'])
        self.assertGreaterEqual(accepted['checks']['confirmation']['value'],220)

    def test_ema_requires_full_intraday_warmup_and_records_rejections(self):
        selected,result = self.replay(normalized(ema_fast=9,ema_slow=21))
        self.assertEqual(selected['index'],20)
        self.assertEqual(selected['candidates'][0]['checks']['ema']['reason'],'INSUFFICIENT_EMA_WARMUP')
        self.assertTrue(selected['candidates'][-1]['checks']['ema']['passed'])
        self.assertEqual(result['trades'][0]['signal_time'],self.rows[20].time.isoformat())

    def test_vwap_uses_session_prefix_and_can_reject_a_breakout(self):
        # A post-range upper wick closes below the range but raises VWAP.
        rows = self.rows[:3]+[replace(self.rows[3],open=100,high=150,low=99,close=100,volume=1000000)]+self.rows[4:5]
        result = filters(rows,normalized(require_vwap=True))
        self.assertFalse(result['checks']['vwap']['passed'])
        normal = filters(self.rows[:4],normalized(require_vwap=True))
        self.assertTrue(normal['checks']['vwap']['passed'])

    def test_atr_filter_warmup_and_extreme_thresholds(self):
        selected,_ = self.replay(normalized(min_atr_pct=.01,max_atr_pct=5))
        self.assertEqual(selected['index'],13)
        self.assertEqual(selected['candidates'][0]['checks']['atr']['reason'],'INSUFFICIENT_ATR_WARMUP')
        selected,result = self.replay(normalized(min_atr_pct=100))
        self.assertIsNone(selected['index'])
        self.assertFalse(result['trades'])
        self.assertTrue(any('atr' in row['reasons'] for row in selected['candidates']))

    def test_supported_stops_and_flatten_clock_use_shared_engine(self):
        for stop in ('or-mid','pct:1','atr:1.5'):
            with self.subTest(stop=stop):
                selected,result = self.replay(normalized(stop=stop))
                self.assertTrue(result['trades'])
                self.assertEqual(result['trades'][0]['execution_policy'],'next-available-open-v1')
                self.assertEqual(result['trades'][0]['stop_spec'],stop)
        selected,result = self.replay(normalized(flat_by='10:00',entry_cutoff='09:55'))
        self.assertEqual(result['trades'][0]['outcome'],'close')
        self.assertEqual(result['trades'][0]['exit_time'][11:16],'10:00')

    def test_atr_stop_waits_for_existing_three_bar_minimum(self):
        rows = list(self.rows)
        rows[1] = replace(rows[1],open=100.2,high=100.5,low=100.1,close=100.4)
        rows[2] = replace(rows[2],open=100.4,high=100.6,low=100.2,close=100.5)
        selected,result = self.replay(normalized(or_minutes=5,stop='atr:1.5'),rows)
        self.assertEqual(selected['index'],2)
        self.assertEqual(selected['candidates'][0]['checks']['atr_stop_warmup']['reason'],'INSUFFICIENT_ATR_STOP_WARMUP')

    def test_signal_selection_does_not_require_a_future_candle(self):
        for spec in (BASELINE,normalized(ema_fast=3,ema_slow=5)):
            selected,_ = self.replay(spec)
            prefix = self.rows[:selected['index']+1]
            causal = prepare(prefix,'DEMO.NS',.05,spec)
            self.assertEqual(causal,selected)
            self.assertFalse(execute(causal,prefix,'DEMO.NS',.05,100000,500,2,strategy=spec)['trades'])

    def test_adverse_gap_rejects_fixed_quantity_without_retry(self):
        rows = list(self.rows)
        rows[4] = replace(rows[4],open=105,high=106,low=104,close=105)
        selected,result = self.replay(normalized(require_vwap=True),rows)
        self.assertEqual(selected['index'],3)
        self.assertFalse(result['trades'])
        self.assertEqual(result['rejection'],'EXECUTION_OR_RISK_LIMIT')

    def test_exit_plan_preserves_raw_sub_paise_precision(self):
        rows = [replace(b,open=100.1,high=100.234,low=99.701,close=100.1) for b in self.rows[:3]]+self.rows[3:]
        rows[3] = replace(rows[3],close=102.303)
        spec = normalized(require_vwap=True)
        selected = prepare(rows,'DEMO.NS',.001,spec)
        result = execute(selected,rows,'DEMO.NS',.001,100000,500,2,strategy=spec)
        self.assertEqual(result['trades'][0]['stop'],99.701)
        self.assertEqual(selected['signal']['price'],102.303)

    def test_signal_is_invariant_to_all_later_prices(self):
        spec = normalized(ema_fast=3,ema_slow=5)
        before,_ = self.replay(spec)
        boundary = before['index']
        future = [replace(b,open=b.open*10,high=b.high*10,low=b.low*10,close=b.close*10,volume=b.volume*100) for b in self.rows[boundary+1:]]
        after,_ = self.replay(spec,self.rows[:boundary+1]+future)
        self.assertEqual(before,after)

    def test_rvol_uses_previous_sessions_only_and_current_share_units(self):
        current = [replace(b,time=b.time.replace(day=8)) for b in self.rows]
        prior = [replace(b,time=b.time.replace(day=7),volume=b.volume/2) for b in self.rows]
        spec = normalized(min_rvol=1.5,rvol_lookback=1,rvol_min_samples=1)
        _,before = self.replay(spec,current,prior)
        self.assertTrue(before['trades'])
        action = dict(id='split',effective_date='2026-09-08',published_at='2026-09-07T18:00:00+05:30',price_factor=.5)
        selected,after = self.replay(spec,current,prior,[action])
        self.assertFalse(after['trades'])
        self.assertEqual(selected['candidates'][0]['checks']['rvol']['value'],1)
        future_action = dict(action,published_at='2026-09-08T12:00:00+05:30')
        _,future = self.replay(spec,current,prior,[future_action])
        self.assertEqual(before['trades'],future['trades'])
        self.assertEqual(prior[0].volume,self.rows[0].volume/2)

    def test_no_rvol_samples_does_not_become_a_pass(self):
        selected,result = self.replay(normalized(min_rvol=1.1))
        self.assertFalse(result['trades'])
        self.assertEqual(selected['candidates'][0]['checks']['rvol']['reason'],'INSUFFICIENT_SAMPLES')
        self.assertFalse(selected['candidates'][0]['checks']['rvol']['passed'])

    def test_filter_input_rejects_future_history_or_mixed_current_dates(self):
        spec = normalized(min_rvol=1.1)
        with self.assertRaisesRegex(ValueError,'explicit NSE/BSE'):
            filters(self.rows[:4],spec)
        with self.assertRaisesRegex(ValueError,'strictly earlier'):
            filters(self.rows[:4],spec,prior=self.rows[:1])
        with self.assertRaisesRegex(ValueError,'strictly earlier'):
            filters(self.rows[:4]+[replace(self.rows[4],time=self.rows[4].time+dt.timedelta(days=1))],spec)


class ExperimentIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'history.sqlite'
        self.store = CandleStore(self.path)
        self.addCleanup(self.store.close)
        self.days = synthetic_sessions(6)
        bars = [b for _,rows in self.days for b in rows]
        self.store.ingest('DEMO.NS',bars,source=SOURCE,observed_at=V2)
        self.store.evidence('instrument',[instrument()],observed_at=V2)

    def run_lab(self, **changes):
        args = dict(start=self.days[0][0],end=self.days[-1][0],as_of=V3,vintage=V2,
                    train_sessions=2,test_sessions=2,bootstrap_samples=50,block_days=1,
                    strategy_configs=[dict(name='ema',ema_fast=3,ema_slow=5)])
        args.update(changes)
        return experiment(self.store,**args)

    def test_report_includes_baseline_rejections_sensitivity_and_paired_comparisons(self):
        report = self.run_lab()
        self.assertEqual(len(report['variants']),6)
        self.assertEqual(report,self.run_lab())
        self.assertEqual(len(report['baseline_comparisons']),3)
        self.assertEqual(len(report['parameter_sensitivity']),2)
        self.assertEqual([row['name'] for row in report['spec']['strategies']],['baseline','ema'])
        self.assertTrue(report['decisions']['ema']['DEMO.NS'][str(self.days[0][0])]['candidates'])
        self.assertEqual(report['report_sha256'],digest({k:v for k,v in report.items() if k != 'report_sha256'}))

    def test_filters_can_return_no_trades_without_forcing_candidates(self):
        report = self.run_lab(strategy_configs=[dict(name='impossible',min_atr_pct=100)])
        variant = next(v for v in report['variants'] if v['strategy']['name'] == 'impossible')
        self.assertEqual(variant['pooled_trades'],0)
        self.assertTrue(variant['symbols']['DEMO.NS']['folds'][0]['test']['rejections'])
        self.assertEqual(report['baseline_comparisons'][0]['candidate_trades'],0)

    def test_earlier_training_and_rvol_decisions_ignore_holdout_revisions(self):
        configs=[dict(name='rvol',min_rvol=1,rvol_lookback=1,rvol_min_samples=1)]
        before = self.run_lab(strategy_configs=configs)
        altered = [replace(b,volume=b.volume*100) for b in self.days[2][1]]
        self.store.ingest('DEMO.NS',altered,source=SOURCE,observed_at=V3)
        after = self.run_lab(strategy_configs=configs,vintage=V3)
        key = str(self.days[1][0])
        self.assertEqual(before['decisions']['rvol']['DEMO.NS'][key],after['decisions']['rvol']['DEMO.NS'][key])
        for first,second in zip(before['variants'],after['variants']):
            self.assertEqual(first['symbols']['DEMO.NS']['folds'][0]['train'],second['symbols']['DEMO.NS']['folds'][0]['train'])

    def test_action_publication_cutoff_selects_earlier_known_revision(self):
        row = dict(id='action',symbol='DEMO.NS',kind='split',effective_date='2026-09-07',
                   published_at='2026-09-04T18:00:00+05:30',source='synthetic only',price_factor=.5)
        later = dict(row,published_at='2026-09-07T12:00:00+05:30',price_factor=.25)
        self.store.evidence('action',[row,later],observed_at=V2)
        known = self.store.actions('DEMO.NS',vintage=V2,through=dt.date(2026,9,7),known_at='2026-09-07T09:15:00+05:30')
        self.assertEqual(known,[row])
        self.assertEqual(self.store.actions('DEMO.NS',vintage=V2,through=dt.date(2026,9,7))[0]['price_factor'],.25)

    def test_total_search_space_is_bounded_and_example_is_valid(self):
        configs=json.loads((ROOT/'config/research-strategies.example.json').read_text())
        self.assertEqual(len(normalize(configs)),8)
        with self.assertRaisesRegex(ValueError,'24'):
            self.run_lab(strategy_configs=configs,rr_values=(1,1.5,2,2.5))

    def test_nse_bse_stay_separate_and_have_exchange_specific_costs(self):
        bars = [b for _,rows in self.days for b in rows]
        self.store.ingest('500001.BO',bars,source=dict(SOURCE,provider='synthetic-BSE'),observed_at=V2)
        self.store.evidence('instrument',[instrument('500001.BO')],observed_at=V2)
        report = self.run_lab(strategy_configs=None)
        symbols = report['variants'][0]['symbols']
        nse, bse = symbols['DEMO.NS']['trades'][0],symbols['500001.BO']['trades'][0]
        self.assertEqual(nse['entry_time'],bse['entry_time'])
        self.assertGreater(bse['realized_cost_estimate']['exchange']/bse['qty'],nse['realized_cost_estimate']['exchange']/nse['qty'])
        self.assertEqual(report['data']['500001.BO']['batches'][0]['source']['provider'],'synthetic-BSE')

    def test_cli_accepts_strategy_file_and_remains_offline(self):
        path=Path(self.temp.name)/'strategies.json'
        path.write_text(json.dumps([dict(name='five',or_minutes=5)]))
        args=['walk-forward','--db',str(self.path),'--start',str(self.days[0][0]),'--end',str(self.days[-1][0]),
              '--as-of',V3,'--vintage',V2,'--train-sessions','2','--test-sessions','2',
              '--strategies',str(path),'--bootstrap-samples','50','--block-days','1']
        with patch('urllib.request.urlopen',side_effect=AssertionError('No feed or order calls')),patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(args),0,output.getvalue())
        report=json.loads(output.getvalue())
        self.assertFalse(report['execution_enabled'])
        self.assertEqual(len(report['variants']),6)


class PairedComparisons(unittest.TestCase):
    def test_daily_zero_trade_observations_are_paired_and_seeded(self):
        dates=['2026-09-%02d' % day for day in range(1,13)]
        base=[dict(entry_time=day+'T09:35:00+05:30',net_r=1) for day in dates]
        candidate=base[:6]
        result=paired_daily_difference(candidate,base,dates,block_days=2)
        self.assertEqual(result,paired_daily_difference(candidate,base,dates,block_days=2))
        self.assertEqual(result['estimate'],-.5)
        self.assertEqual(result['paired_days'],12)
        self.assertEqual(result['daily_difference'][dates[-1]],-1)
        same=paired_daily_difference(base,base,dates,block_days=2)
        self.assertEqual((same['lower'],same['upper']),(0,0))
