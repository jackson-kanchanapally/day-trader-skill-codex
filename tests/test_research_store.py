"""Historical vintages/eligibility: original synthetic records only."""
from dataclasses import replace
import datetime as dt
from pathlib import Path
import tempfile
import sqlite3
import unittest

from india.data import CSVProvider, timestamp
from research.store import CandleStore

ROOT = Path(__file__).resolve().parents[1]
SOURCE = dict(provider='synthetic',reference='original synthetic fixture',license_note='Invented test data only')
V1 = '2026-10-19T16:00:00+05:30'
V2 = '2026-10-21T16:00:00+05:30'
V3 = '2026-10-22T16:00:00+05:30'


def instrument(symbol='DEMO.NS', **changes):
    row = dict(symbol=symbol,effective_from='2026-09-01',effective_to='2026-12-31',
               published_at='2026-09-01T08:00:00+05:30',source='original synthetic directory',tick=.05,sector='SYNTHETIC',eligible=True)
    row.update(changes)
    return row


class HistoricalStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'candles.sqlite'
        self.store = CandleStore(self.path)
        self.addCleanup(self.store.close)
        self.bars = CSVProvider(ROOT/'tests/fixtures/india/synthetic.csv').history('DEMO.NS','5m')[:4]

    def load(self, vintage=V3):
        return self.store.load('DEMO.NS',start=dt.date(2026,10,19),end=dt.date(2026,10,19),vintage=vintage)

    def ingest(self, bars=None, vintage=V1, **kwargs):
        return self.store.ingest('DEMO.NS',self.bars if bars is None else bars,source=SOURCE,observed_at=vintage,**kwargs)

    def test_idempotency_incremental_history_and_provenance(self):
        first = self.ingest()
        self.assertEqual(first['written'],4)
        self.assertTrue(self.ingest()['idempotent'])
        loaded, provenance = self.load()
        self.assertEqual(loaded,self.bars)
        self.assertEqual(provenance['batches'][0]['batch_id'],first['batch_id'])
        self.assertEqual(provenance['batches'][0]['source'],SOURCE)
        self.assertIsNotNone(provenance['batches'][0]['recorded_at'])
        self.assertEqual(self.store.watermark('DEMO.NS',vintage=V1),self.bars[-1].time.isoformat())
        self.assertIsNone(self.store.watermark('DEMO.NS',vintage='2026-10-18T16:00:00+05:30'))
        later = replace(self.bars[-1],time=self.bars[-1].time+dt.timedelta(minutes=5))
        self.ingest(self.bars+[later],V2)
        self.assertEqual(len(self.load(V1)[0]),4)
        self.assertEqual(len(self.load(V2)[0]),5)

    def test_revisions_are_point_in_time_not_last_import_wins(self):
        original = self.bars
        changed = [replace(b,volume=b.volume+10) for b in original]
        self.ingest(original,V1)
        self.ingest(original,V3)
        self.ingest(changed,V2)  # out-of-order revision must not erase V3 observation
        self.assertEqual(self.load(V1)[0],original)
        self.assertEqual(self.load(V2)[0],changed)
        self.assertEqual(self.load(V3)[0],original)

    def test_conflicting_vintage_rolls_back_entire_batch(self):
        self.ingest()
        changed = [replace(b,volume=b.volume+10) for b in self.bars]
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            self.ingest(changed)
        self.assertEqual(self.load()[0],self.bars)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM batches').fetchone()[0],1)

    def test_future_partial_and_bad_source_fail(self):
        for bars, vintage, source in ((self.bars,'2026-10-19T09:34:00+05:30',SOURCE),
                                      ([replace(self.bars[0],time=self.bars[0].time+dt.timedelta(seconds=1))],V1,SOURCE),
                                      (self.bars,V1,{'provider':'synthetic'}),
                                      (self.bars,'2099-01-01T00:00:00+05:30',SOURCE)):
            with self.subTest(vintage=vintage), self.assertRaises(ValueError):
                self.store.ingest('DEMO.NS',bars,source=source,observed_at=vintage)
        self.assertIsNone(self.store.watermark('DEMO.NS',vintage=V3))

    def test_unsupported_schema_is_not_modified(self):
        path = Path(self.temp.name)/'newer.sqlite'
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        with db:
            db.execute('CREATE TABLE research_schema (version INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO research_schema VALUES (999)')
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            CandleStore(path)
        self.assertEqual(db.execute('SELECT version FROM research_schema').fetchall(),[(999,)])
        self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('research_schema',)])

    def test_missing_readonly_store_is_not_created(self):
        absent = Path(self.temp.name)/'absent.sqlite'
        with self.assertRaisesRegex(ValueError,'does not exist'):
            CandleStore(absent,readonly=True)
        self.assertFalse(absent.exists())

    def test_candle_and_batch_integrity_checks(self):
        self.ingest()
        with self.store.db:
            self.store.db.execute("UPDATE candles SET payload_sha256='corrupt'")
        with self.assertRaisesRegex(ValueError,'integrity'):
            self.load()

    def test_mixed_provider_selection_is_rejected(self):
        self.ingest()
        self.store.ingest('DEMO.NS',[self.bars[-1]],source=dict(SOURCE,provider='other synthetic'),observed_at=V2)
        with self.assertRaisesRegex(ValueError,'mixes providers'):
            self.load()

    def test_ambiguous_overlapping_instrument_records_are_rejected(self):
        self.store.evidence('instrument',[instrument(),instrument(effective_from='2026-08-01')],observed_at=V2)
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            self.store.universe(dt.date(2026,10,19),vintage=V2)

    def test_action_revision_is_resolved_before_effective_date_filter(self):
        action = dict(id='demo',symbol='DEMO.NS',kind='split',effective_date='2026-10-19',
                      published_at='2026-10-16T17:00:00+05:30',source='synthetic',price_factor=.5)
        self.store.evidence('action',[action],observed_at=V1)
        corrected = dict(action,effective_date='2026-10-23',published_at='2026-10-19T17:00:00+05:30')
        self.store.evidence('action',[corrected],observed_at=V2)
        self.assertEqual(self.store.actions('DEMO.NS',vintage=V2,through=dt.date(2026,10,21)),[])
        self.assertEqual(len(self.store.actions('DEMO.NS',vintage=V1,through=dt.date(2026,10,21))),1)

    def test_dated_universe_excludes_future_publications_and_delisted_dates(self):
        rows = [instrument(effective_to='2026-10-19'),instrument('LATER.NS',effective_from='2026-10-21',published_at='2026-10-21T08:00:00+05:30'),
                instrument('FUTURE.NS',published_at='2026-10-21T08:00:00+05:30')]
        self.store.evidence('instrument',rows,observed_at=V2)
        self.assertEqual(list(self.store.universe(dt.date(2026,10,19),vintage=V2)),['DEMO.NS'])
        self.assertEqual(list(self.store.universe(dt.date(2026,10,21),vintage=V2)),['FUTURE.NS','LATER.NS'])
        self.assertFalse(self.store.universe(dt.date(2026,10,19),vintage=V1))
        no = instrument(eligible=False,published_at='2026-10-19T08:30:00+05:30')
        self.store.evidence('instrument',[no],observed_at=V2)
        self.assertNotIn('DEMO.NS',self.store.universe(dt.date(2026,10,19),vintage=V2))

    def test_split_view_adjusts_prices_and_inverse_volume_without_mutating_raw(self):
        self.ingest()
        action = dict(id='demo-split',symbol='DEMO.NS',kind='split',effective_date='2026-10-21',
                      published_at='2026-10-19T17:00:00+05:30',source='synthetic action',price_factor=.5)
        self.store.evidence('action',[action],observed_at=V2)
        adjusted, provenance = self.store.adjusted('DEMO.NS',start=dt.date(2026,10,19),end=dt.date(2026,10,21),vintage=V2)
        self.assertEqual(adjusted[0].close,self.bars[0].close*.5)
        self.assertEqual(adjusted[0].volume,self.bars[0].volume*2)
        self.assertEqual(self.load()[0],self.bars)
        self.assertEqual(len(provenance['actions']),1)
        before, _ = self.store.adjusted('DEMO.NS',start=dt.date(2026,10,19),end=dt.date(2026,10,19),vintage=V1)
        self.assertEqual(before,self.bars)
        with self.assertRaisesRegex(ValueError,'split/bonus'):
            self.store.evidence('action',[dict(action,kind='dividend')],observed_at=V2)

    def test_evidence_is_transactional_and_checked(self):
        with self.assertRaises(ValueError):
            self.store.evidence('instrument',[instrument(),instrument('BAD.NS',tick=-1)],observed_at=V2)
        self.assertFalse(self.store.universe(dt.date(2026,10,19),vintage=V2))
        self.store.evidence('instrument',[instrument()],observed_at=V2)
        with self.store.db:
            self.store.db.execute("UPDATE evidence SET payload_sha256='corrupt'")
        with self.assertRaisesRegex(ValueError,'integrity'):
            self.store.universe(dt.date(2026,10,19),vintage=V2)
