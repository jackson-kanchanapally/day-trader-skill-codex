"""Synthetic decision gates, research provenance and read-only paper accounting."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest

from dashboard.decision import caution_flags, data_health, decision_state, execution_quote, market_clock
from dashboard.journal import paper_journal
from dashboard.opportunities import OpportunityStore
from dashboard.research import ASSESSMENT, ResearchStore
from india.data import timestamp
from workflow.paper import order_hash

NOW = timestamp('2026-10-24T19:05:00+05:30')
ROW = dict(symbol='DEMO.NS', price=120., volume=1000000., volume_x=9., score=95.,
           ma20=100., high20=110., rsi=72., range5_pct=17., setups=['breakout_20d'],
           as_of='2026-10-23T09:15:00+05:30', source='Original synthetic test')


class DecisionWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def quote(self, **overrides):
        row = dict(symbol='DEMO.NS', observed_at=NOW.isoformat(), source='https://example.invalid/synthetic',
                   bid=119., ask=120., lower_price_band=90., upper_price_band=130., halted=False, fixture=True)
        row.update(overrides)
        return dict(currency='INR', paper=True, execution_enabled=False, quotes=[row])

    def assessment(self):
        scan = dict(mode='daily', currency='INR', paper=True, execution_enabled=False, as_of=NOW.isoformat(), hits=[ROW])
        source = self.root/'scans'/'original.json'
        source.parent.mkdir()
        source.write_text(json.dumps(scan))
        doc = dict(kind=ASSESSMENT, symbol='DEMO.NS', as_of=NOW.isoformat(), execution_enabled=False,
                   independent_team_dispatched=False, independent_risk_clearance=False,
                   original_scan_source='scans/original.json', original_scan_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                   original_daily_setup=ROW, coordinator_role_findings=[dict(role='entry', status='INELIGIBLE', decision='Synthetic closed session')])
        directory = self.root/'reviews'/'demo'
        directory.mkdir(parents=True)
        path = directory/'readiness.json'
        path.write_text(json.dumps(doc))
        store = ResearchStore(self.root, self.root/'team', lambda _: [], lambda *_: None)
        return store, path, source

    def ledger(self):
        path = self.root/'state'/'paper.sqlite'
        path.parent.mkdir()
        events = []
        for identifier, side, qty, price, fee, setup, pnl in (
                ('entry-a', 'buy', 2, 100., 1., 'orb', 0.),
                ('entry-b', 'buy', 1, 100., 1., 'level', 0.),
                ('exit-a', 'sell', 2, 110., 1., 'flatten', 18.),
                ('exit-b', 'sell', 1, 110., .5, 'flatten', 8.5)):
            order = dict(symbol='DEMO.NS', side=side, qty=str(qty), plan=dict(setup=setup))
            fingerprint = order_hash(order)
            events.append(dict(id='approval:'+fingerprint, status='approved', fingerprint=fingerprint, order=order))
            events.append(dict(id=identifier, status='placed', simulation=True, paper=True, execution_enabled=False,
                               fingerprint=fingerprint, symbol='DEMO.NS', side=side, qty=qty, price=price, fees=fee,
                               at=NOW.isoformat(), realized_pnl=pnl))
        portfolio = dict(paper=True, currency='INR', cash=1026.5, initial_capital=1000., positions=[], realized_pnl=26.5)
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE events (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES (1, ?)', (json.dumps(portfolio),))
            db.executemany('INSERT INTO events VALUES (?, ?)', [(e['id'], json.dumps(e)) for e in events])
            db.commit()
        return path

    def test_closed_weekend_with_current_daily_data_still_cannot_clear_trade(self):
        decision = decision_state(ROW, NOW.isoformat(), NOW)
        self.assertEqual(decision['data']['status'], 'CURRENT_DAILY')
        self.assertEqual(decision['market']['status'], 'MARKET_CLOSED')
        self.assertEqual(decision['tradeability']['status'], 'BLOCKED')
        self.assertFalse(decision['can_prepare_order'])
        self.assertEqual(decision['risk']['status'], 'NOT_CLEARED')

    def test_freshness_moves_forward_without_changing_saved_cutoff(self):
        before = data_health(NOW.isoformat(), '2026-10-23', NOW)
        after = data_health(NOW.isoformat(), '2026-10-23', timestamp('2026-10-26T16:00:00+05:30'))
        self.assertEqual(before['status'], 'CURRENT_DAILY')
        self.assertEqual(after['status'], 'STALE')
        self.assertEqual(after['expected_session'], '2026-10-26')
        self.assertEqual(after['scan_cutoff'], NOW.isoformat())
        self.assertEqual(data_health(NOW.isoformat(), '2026-10-23', NOW-dt.timedelta(minutes=1))['status'], 'FUTURE')

    def test_cached_scan_rechecks_current_clock_on_every_listing(self):
        clock = [NOW]
        scan = dict(mode='daily', currency='INR', paper=True, execution_enabled=False,
                    as_of=NOW.isoformat(), universe=1, scanned=1, hits=[ROW], results=[ROW], failed=[])
        (self.root/'watchlist.json').write_text(json.dumps(scan))
        store = OpportunityStore(self.root, clock=lambda: clock[0])
        first = store.listing()
        clock[0] = timestamp('2026-10-26T16:00:00+05:30')
        latest = store.listing()
        self.assertEqual(first['opportunities'][0]['data_status'], 'CURRENT_DAILY')
        self.assertEqual(latest['opportunities'][0]['data_status'], 'STALE')
        self.assertEqual(first['scan_id'], latest['scan_id'])
        self.assertEqual(latest['stale_count'], 1)

    def test_uncovered_calendar_and_entry_boundaries_fail_closed(self):
        self.assertEqual(market_clock(timestamp('2027-01-04T10:00:00+05:30'))['status'], 'CALENDAR_UNAVAILABLE')
        self.assertFalse(market_clock(timestamp('2026-10-26T09:29:00+05:30'))['entry_window_open'])
        self.assertTrue(market_clock(timestamp('2026-10-26T09:30:00+05:30'))['entry_window_open'])
        self.assertFalse(market_clock(timestamp('2026-10-26T15:00:00+05:30'))['entry_window_open'])

    def test_high_evidence_score_preserves_extension_cautions(self):
        flags = caution_flags(ROW, dict(change_pct=20.))
        self.assertTrue({'EXTENDED','HIGH_RSI','WIDE_RANGE','VOLUME_SURGE','LARGE_MOVE'} <= {f['code'] for f in flags})
        self.assertEqual(ROW['score'], 95.)
        self.assertTrue(all(f['severity'] == 'CAUTION' for f in flags))

    def test_quotes_require_explicit_currency_freshness_units_and_real_instruments(self):
        self.assertEqual(execution_quote(self.quote(), 'DEMO.NS', NOW)['status'], 'CAPTURED')
        for changes in (dict(observed_at=(NOW-dt.timedelta(seconds=61)).isoformat()),
                        dict(observed_at=(NOW+dt.timedelta(seconds=1)).isoformat())):
            self.assertEqual(execution_quote(self.quote(**changes), 'DEMO.NS', NOW)['status'], 'STALE')
        for changes in (dict(ask=118), dict(halted='false'), dict(bid=float('nan')), dict(source='javascript:alert(1)')):
            self.assertEqual(execution_quote(self.quote(**changes), 'DEMO.NS', NOW)['status'], 'UNAVAILABLE')
        self.assertEqual(execution_quote(self.quote(symbol='REAL.NS'), 'REAL.NS', NOW)['status'], 'UNAVAILABLE')
        self.assertEqual(execution_quote(dict(self.quote(), currency='USD'), 'DEMO.NS', NOW)['status'], 'UNAVAILABLE')

    def test_even_captured_quote_in_open_window_does_not_clear_daily_match(self):
        now = timestamp('2026-10-26T09:35:00+05:30')
        q = execution_quote(self.quote(observed_at=now.isoformat()), 'DEMO.NS', now)
        d = decision_state(ROW, NOW.isoformat(), now, q)
        self.assertEqual(d['tradeability']['status'], 'INCOMPLETE')
        self.assertFalse(d['can_prepare_order'])
        self.assertEqual(d['risk']['status'], 'NOT_CLEARED')

    def test_coordinator_record_is_discoverable_without_becoming_independent_clearance(self):
        store, _, _ = self.assessment()
        (self.root/'instrument-names.json').write_text(json.dumps(dict(names={'DEMO.NS':'Original synthetic listing'})))
        index = store.listing()
        self.assertEqual(index['items'][0]['symbol'], 'DEMO.NS')
        self.assertEqual(index['items'][0]['name'], 'Original synthetic listing')
        detail = store.detail(index['items'][0]['id'])
        self.assertEqual(detail['kind'], 'ASSESSMENT')
        self.assertFalse(detail['independent_risk_clearance'])
        self.assertEqual(detail['tick']['status'], 'UNVERIFIED')
        self.assertEqual(detail['findings'][0]['status'], 'INELIGIBLE')

    def test_forged_clearance_tampered_scan_and_path_escape_are_excluded(self):
        store, path, source = self.assessment()
        doc = json.loads(path.read_text())
        path.write_text(json.dumps(dict(doc, independent_risk_clearance=True)))
        self.assertEqual(store.listing()['rejected'], 1)
        path.write_text(json.dumps(doc))
        source.write_text('{}')
        self.assertEqual(store.listing()['rejected'], 1)
        with self.assertRaises(ValueError):
            store.detail('assessment:../../outside')

    def test_later_review_retains_earlier_scan_but_future_evidence_is_rejected(self):
        store, path, _ = self.assessment()
        doc = json.loads(path.read_text())
        path.write_text(json.dumps(dict(doc, original_scan_as_of=doc['as_of'], as_of='2026-10-26T09:35:00+05:30')))
        detail = store.detail('assessment:demo')
        self.assertEqual(detail['source_scan_cutoff'], NOW.isoformat())
        self.assertEqual(detail['as_of'], '2026-10-26T09:35:00+05:30')
        path.write_text(json.dumps(dict(doc, original_scan_as_of=doc['as_of'], as_of='2026-10-24T18:00:00+05:30')))
        self.assertEqual(store.listing()['rejected'], 1)

    def test_missing_setup_can_be_saved_only_as_an_explicit_unavailable_note(self):
        store, path, _ = self.assessment()
        doc = json.loads(path.read_text())
        for key in ('original_scan_source','original_scan_sha256','original_daily_setup'):
            doc.pop(key)
        doc.update(evidence_status='UNAVAILABLE', coordinator_role_findings=[dict(role='scanner',status='UNAVAILABLE',decision='No permitted snapshot supplied')])
        path.write_text(json.dumps(doc))
        detail = store.detail('assessment:demo')
        self.assertEqual(detail['evidence_status'],'UNAVAILABLE')
        self.assertIsNone(detail['daily_setup'])
        doc['coordinator_role_findings'][0]['status']='PASS'
        path.write_text(json.dumps(doc))
        self.assertEqual(store.listing()['rejected'],1)

    def test_journal_missing_account_does_not_create_any_files(self):
        self.assertEqual(paper_journal(self.root)['status'], 'NO_ACCOUNT')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_journal_reconciles_fifo_costs_setup_outcomes_and_does_not_mutate(self):
        path = self.ledger()
        before = path.read_bytes()
        result = paper_journal(self.root)
        self.assertEqual(result['status'], 'RECORDED')
        self.assertEqual(result['realized_pnl'], 26.5)
        self.assertEqual(result['total_costs'], 3.5)
        self.assertEqual(result['closed_exits'], 2)
        self.assertEqual({g['setup']:g['net_pnl'] for g in result['groups']}, {'orb':18., 'level':8.5})
        self.assertEqual(result['win_rate'], 100.)
        self.assertEqual(path.read_bytes(), before)

    def test_journal_with_inconsistent_cash_or_fills_hides_performance(self):
        path = self.ledger()
        with closing(sqlite3.connect(path)) as db:
            pf = json.loads(db.execute('SELECT document FROM state').fetchone()[0])
            pf['cash'] += 1
            db.execute('UPDATE state SET document=?', (json.dumps(pf),))
            db.commit()
        result = paper_journal(self.root)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertIsNone(result['realized_pnl'])


if __name__ == '__main__':
    unittest.main()
