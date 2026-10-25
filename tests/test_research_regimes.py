"""Original synthetic regime evidence, causal attribution and uncertainty."""
import datetime as dt
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from india.audit import digest
from research.__main__ import main
from research.metrics import expectancy_interval, regime_breakdown
from research.promotion import assess
from research.regimes import record
from research.store import CandleStore
import test_research_lab as lab_tests
import test_research_store as store_tests
from test_research_lab import policy
from test_research_store import SOURCE, V1, V2, V3, instrument

MODEL = 'synthetic-regime-v1'


def regime(**changes):
    row = dict(exchange='NSE',model=MODEL,benchmark='ORIGINAL_SYNTHETIC_INDEX',
               method='Invented deterministic test labels, version 1; no market evidence',
               label='TREND_UP',effective_from='2026-09-01',effective_to='2026-09-30',
               data_through='2026-08-31T15:30:00+05:30',published_at='2026-09-01T08:00:00+05:30',
               source='Original synthetic regime records only')
    row.update(changes)
    return row


class RegimeStore(unittest.TestCase):
    setUp = store_tests.HistoricalStore.setUp

    def selected(self, day='2026-09-03', exchange='NSE', vintage=V2, model=MODEL):
        return self.store.regime(dt.date.fromisoformat(day),exchange,model,vintage=vintage)

    def test_strict_schema_and_timestamps(self):
        invalid = [dict(label='UNKNOWN'),dict(label='trend up'),dict(exchange='US'),dict(model=''),
                   dict(model=True),dict(benchmark=''),dict(method=' '),dict(source=''),
                   dict(effective_from='2026-10-01'),dict(effective_from='20260901'),
                   dict(data_through='2026-09-01T08:01:00+05:30'),dict(data_through='2026-08-31T15:30:00'),
                   dict(published_at='2026-09-01T08:00:00'),dict(data_through=None),dict(data_through=123),dict(extra=True)]
        for changes in invalid:
            with self.subTest(changes=changes),self.assertRaises((ValueError,TypeError)):
                self.store.evidence('regime',[regime(**changes)],observed_at=V2)
        self.assertFalse(self.store.regime_records(vintage=V2))

    def test_timezone_normalization_and_publication_before_observation(self):
        row = regime(published_at='2026-09-01T02:30:00Z',data_through='2026-08-31T10:00:00Z')
        self.store.evidence('regime',[row],observed_at=V2)
        self.assertEqual(self.selected()['published_at'],'2026-09-01T08:00:00+05:30')
        with self.assertRaisesRegex(ValueError,'vintage precedes publication'):
            self.store.evidence('regime',[regime(published_at=V3)],observed_at=V2)

    def test_idempotent_reimports_and_frozen_source_vintages(self):
        row = regime()
        self.assertEqual(self.store.evidence('regime',[row],observed_at=V1),1)
        self.assertEqual(self.store.evidence('regime',[row],observed_at=V1),0)
        self.store.evidence('regime',[dict(row,label='RANGE')],observed_at=V2)
        self.store.evidence('regime',[row],observed_at=V3)
        self.assertEqual(self.selected(vintage=V1)['label'],'TREND_UP')
        self.assertEqual(self.selected(vintage=V2)['label'],'RANGE')
        self.assertEqual(self.selected(vintage=V3)['label'],'TREND_UP')

    def test_same_vintage_conflict_rolls_back_all_rows(self):
        self.store.evidence('regime',[regime()],observed_at=V2)
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            self.store.evidence('regime',[regime(exchange='BSE'),regime(label='RANGE')],observed_at=V2)
        self.assertIsNone(self.selected(exchange='BSE'))
        self.assertEqual(self.selected()['label'],'TREND_UP')

    def test_future_publication_never_relabels_earlier_sessions(self):
        self.store.evidence('regime',[regime(),regime(label='RANGE',published_at='2026-09-03T09:16:00+05:30')],observed_at=V2)
        self.assertEqual(self.selected('2026-09-03')['label'],'TREND_UP')
        self.assertEqual(self.selected('2026-09-04')['label'],'RANGE')
        self.store.evidence('regime',[regime(label='TREND_DOWN',published_at='2026-09-03T09:15:00+05:30')],observed_at=V2)
        self.assertEqual(self.selected('2026-09-03')['label'],'TREND_DOWN')

    def test_revision_resolves_before_validity_without_resurrecting_old_window(self):
        self.store.evidence('regime',[regime()],observed_at=V1)
        revised = regime(effective_to='2026-09-02',published_at='2026-09-03T08:00:00+05:30')
        self.store.evidence('regime',[revised],observed_at=V2)
        self.assertIsNone(self.selected('2026-09-04'))
        self.assertIsNotNone(self.selected('2026-09-02'))
        self.assertIsNotNone(self.selected('2026-09-04',vintage=V1))

    def test_ambiguous_top_publication_fails_and_later_publication_resolves(self):
        rows = [regime(),regime(effective_from='2026-08-01',label='RANGE')]
        self.store.evidence('regime',rows,observed_at=V1)
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            self.selected()
        self.store.evidence('regime',[regime(label='TREND_DOWN',published_at='2026-09-02T08:00:00+05:30')],observed_at=V2)
        self.assertEqual(self.selected()['label'],'TREND_DOWN')

    def test_model_definitions_are_versioned_and_imports_transactional(self):
        self.store.evidence('regime',[regime()],observed_at=V1)
        for changes in (dict(method='different rule'),dict(benchmark='DIFFERENT_SYNTHETIC_INDEX')):
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'new versioned model'):
                self.store.evidence('regime',[regime(exchange='BSE'),regime(**changes)],observed_at=V2)
            self.assertIsNone(self.selected(exchange='BSE'))
        with self.assertRaisesRegex(ValueError,'new versioned model'):
            self.store.evidence('regime',[regime(exchange='BSE',method='different rule')],observed_at=V2)
        self.store.evidence('regime',[regime(model='synthetic-v2',method='different rule')],observed_at=V2)
        self.assertIsNotNone(self.selected(model='synthetic-v2'))

    def test_nse_and_bse_never_substitute_each_others_labels(self):
        self.store.evidence('regime',[regime()],observed_at=V2)
        self.assertIsNone(self.selected(exchange='BSE'))
        self.store.evidence('regime',[regime(exchange='BSE',label='RANGE',benchmark='OTHER_SYNTHETIC_INDEX')],observed_at=V2)
        self.assertEqual(self.selected()['label'],'TREND_UP')
        self.assertEqual(self.selected(exchange='BSE')['label'],'RANGE')

    def test_existing_schema_and_readonly_reopening_remain_supported(self):
        self.store.evidence('regime',[regime()],observed_at=V2)
        with self.assertRaisesRegex(ValueError,'model'):
            self.selected(model='invalid model')
        other = CandleStore(self.path,readonly=True)
        self.addCleanup(other.close)
        self.assertEqual(other.db.execute('SELECT version FROM research_schema').fetchall(),[(1,)])
        self.assertEqual(other.regime(dt.date(2026,9,3),'NSE',MODEL,vintage=V2),self.selected())

    def test_stored_regime_integrity_is_checked(self):
        self.store.evidence('regime',[regime()],observed_at=V2)
        with self.store.db:
            self.store.db.execute("UPDATE evidence SET payload_sha256='corrupt' WHERE kind='regime'")
        with self.assertRaisesRegex(ValueError,'integrity'):
            self.selected()


class RegimeLaboratory(unittest.TestCase):
    setUp = lab_tests.Laboratory.setUp
    run_lab = lab_tests.Laboratory.run_lab

    def test_defaults_remain_unknown_and_existing_trades_unchanged(self):
        before = self.run_lab()
        self.store.evidence('regime',[regime()],observed_at=V2)
        after = self.run_lab()
        self.assertEqual(before,after)  # Imported labels are never selected implicitly.
        self.assertEqual(after['regimes']['coverage']['unknown_symbol_sessions'],6)
        self.assertEqual(after['variants'][0]['symbols']['DEMO.NS']['trades'][0]['regime'],'UNKNOWN')

    def test_sourced_labels_provenance_and_report_are_reproducible(self):
        baseline = self.run_lab()
        self.store.evidence('regime',[regime()],observed_at=V2)
        report = self.run_lab(regime_model=MODEL)
        self.assertEqual(report,self.run_lab(regime_model=MODEL))
        self.assertEqual(report['regimes']['coverage'],dict(eligible_symbol_sessions=6,sourced_symbol_sessions=6,unknown_symbol_sessions=0))
        row = report['regimes']['sessions'][str(self.days[0][0])]['NSE']
        self.assertEqual(row['evidence_sha256'],digest(record(regime())))
        self.assertEqual(row['evidence']['source'],regime()['source'])
        a = baseline['variants'][0]['symbols']['DEMO.NS']['trades']
        b = report['variants'][0]['symbols']['DEMO.NS']['trades']
        self.assertEqual([{k:v for k,v in t.items() if k != 'regime'} for t in a],
                         [{k:v for k,v in t.items() if k != 'regime'} for t in b])
        self.assertTrue(all(t['regime'] == 'TREND_UP' for t in b))
        self.assertEqual(report['decisions']['baseline']['DEMO.NS'][str(self.days[0][0])]['regime'],row)
        self.assertEqual(report['report_sha256'],digest({k:v for k,v in report.items() if k != 'report_sha256'}))
        json.dumps(report,allow_nan=False)

    def test_partial_coverage_stays_unknown_and_is_counted(self):
        self.store.evidence('regime',[regime(effective_to=str(self.days[1][0]))],observed_at=V2)
        report = self.run_lab(regime_model=MODEL)
        self.assertEqual(report['regimes']['coverage']['unknown_symbol_sessions'],4)
        self.assertEqual(set(report['variants'][0]['by_regime']),{'UNKNOWN'})
        train = report['variants'][0]['symbols']['DEMO.NS']['folds'][0]['train']
        self.assertEqual(train['by']['regime']['TREND_UP']['eligible_market_days'],2)

    def test_holdout_regime_publications_do_not_change_earlier_training(self):
        self.store.evidence('regime',[regime()],observed_at=V2)
        before = self.run_lab(regime_model=MODEL)
        change = regime(label='RANGE',published_at=str(self.days[2][0])+'T08:00:00+05:30')
        self.store.evidence('regime',[change],observed_at=V3)
        after = self.run_lab(regime_model=MODEL,vintage=V3)
        old,new = before['variants'][0]['symbols']['DEMO.NS']['folds'],after['variants'][0]['symbols']['DEMO.NS']['folds']
        self.assertEqual(old[0]['train'],new[0]['train'])
        self.assertNotEqual(old[0]['test'],new[0]['test'])
        self.assertEqual(before,self.run_lab(regime_model=MODEL,vintage=V2))

    def test_after_open_publication_does_not_label_same_day_signal(self):
        self.store.evidence('regime',[regime(published_at=str(self.days[0][0])+'T09:16:00+05:30')],observed_at=V2)
        report = self.run_lab(regime_model=MODEL)
        rows = report['regimes']['sessions']
        self.assertEqual(rows[str(self.days[0][0])]['NSE']['label'],'UNKNOWN')
        self.assertEqual(rows[str(self.days[1][0])]['NSE']['label'],'TREND_UP')

    def test_zero_trade_regime_days_are_reported(self):
        self.store.evidence('regime',[regime()],observed_at=V2)
        report = self.run_lab(regime_model=MODEL,liquidity_config=dict(max_participation=.001))
        row = report['variants'][0]['by_regime']['TREND_UP']
        self.assertEqual(row['trades'],0)
        self.assertEqual(row['eligible_market_days'],4)
        self.assertEqual(row['zero_trade_days'],4)
        self.assertIsNone(row['net_expectancy_r'])
        self.assertEqual(row['expectancy_interval']['status'],'UNAVAILABLE')

    def test_pooled_nse_bse_days_keep_distinct_regimes_and_sources(self):
        self.store.ingest('DEMO.BO',self.bars,source=dict(SOURCE,provider='synthetic-bse'),observed_at=V2)
        self.store.evidence('instrument',[instrument('DEMO.BO')],observed_at=V2)
        self.store.evidence('regime',[regime(),regime(exchange='BSE',label='RANGE',source='Other synthetic exchange source')],observed_at=V2)
        report = self.run_lab(regime_model=MODEL)
        self.assertEqual(report['regimes']['coverage']['sourced_symbol_sessions'],12)
        buckets = report['variants'][0]['by_regime']
        self.assertEqual(set(buckets),{'RANGE','TREND_UP'})
        self.assertEqual(buckets['RANGE']['trades'],4)
        self.assertEqual(buckets['TREND_UP']['trades'],4)
        for row in buckets.values():
            self.assertEqual(row['eligible_market_days'],4)
            self.assertEqual(row['expectancy_interval']['market_days'],4)

    def test_promotion_requires_selected_model_and_complete_eligible_coverage(self):
        before = assess(self.run_lab(),'BACKTESTED',policy())
        self.assertTrue(any('sourced regime model' in r for r in before['reasons']))
        self.store.evidence('regime',[regime(effective_to=str(self.days[1][0]))],observed_at=V2)
        partial = assess(self.run_lab(regime_model=MODEL),'BACKTESTED',policy())
        self.assertTrue(any('every eligible symbol session' in r for r in partial['reasons']))
        self.store.evidence('regime',[regime(published_at='2026-09-01T08:01:00+05:30')],observed_at=V2)
        report = self.run_lab(regime_model=MODEL)
        complete = assess(report,'BACKTESTED',policy())
        self.assertFalse(any('regime' in r for r in complete['reasons']))
        self.assertTrue(assess(report,'RESEARCH',policy())['eligible'])
        self.assertFalse(assess(report,'PAPER_TRADING',policy())['eligible'])

    def test_offline_cli_import_and_explicit_model_selection(self):
        path = Path(self.temp.name)/'regimes.json'
        path.write_text(json.dumps([regime()]))
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(['evidence','regime','--db',str(self.path),'--file',str(path),'--observed-at',V2]),0)
            self.assertEqual(json.loads(output.getvalue())['written'],1)
        args = ['walk-forward','--db',str(self.path),'--start',str(self.days[0][0]),'--end',str(self.days[-1][0]),
                '--as-of',V3,'--vintage',V2,'--train-sessions','2','--test-sessions','2',
                '--bootstrap-samples','50','--block-days','1','--regime-model',MODEL]
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(args),0)
            self.assertEqual(json.loads(output.getvalue())['regimes']['coverage']['sourced_symbol_sessions'],6)
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(args[:-1]+['invalid model']),1)
            self.assertEqual(json.loads(output.getvalue())['status'],'UNAVAILABLE')


class RegimeUncertainty(unittest.TestCase):
    def test_full_day_adjacency_and_seed_are_preserved(self):
        days = [str(day) for day,_ in lab_tests.synthetic_sessions(8)]
        labels = {day:['TREND_UP' if i % 2 else 'RANGE'] for i,day in enumerate(days)}
        trades = [dict(entry_time=day+'T09:35:00+05:30',regime=labels[day][0],net_r=float(i-3),net_pnl=float(i-3)*100)
                  for i,day in enumerate(days)]
        opts = dict(samples=1000,block_days=2,seed=19)
        rows = regime_breakdown(trades,days,labels,**opts)
        selected = [t for t in trades if t['regime'] == 'TREND_UP']
        self.assertEqual(rows['TREND_UP']['expectancy_interval'],expectancy_interval(selected,days,**opts))
        self.assertEqual(rows,regime_breakdown(trades,days,labels,**opts))
        self.assertEqual(rows['TREND_UP']['expectancy_interval']['market_days'],8)
        self.assertEqual(rows['TREND_UP']['eligible_market_days'],4)
        self.assertEqual(rows['TREND_UP']['net_expectancy_r'],1.)

    def test_rare_regime_remains_unavailable_and_bad_assignments_fail(self):
        days = [str(day) for day,_ in lab_tests.synthetic_sessions(4)]
        labels = {day:['RANGE'] for day in days}
        labels[days[-1]] = ['RARE']
        trade = dict(entry_time=days[-1]+'T09:35:00+05:30',regime='RARE',net_r=1.,net_pnl=100.)
        rows = regime_breakdown([trade],days,labels,samples=1000,block_days=1)
        self.assertEqual(rows['RARE']['expectancy_interval']['status'],'UNAVAILABLE')
        self.assertEqual(rows['RANGE']['zero_trade_days'],3)
        with self.assertRaisesRegex(ValueError,'does not match'):
            regime_breakdown([dict(trade,regime='WRONG')],days,labels)
        with self.assertRaisesRegex(ValueError,'every evaluation session'):
            regime_breakdown([],days,{})
