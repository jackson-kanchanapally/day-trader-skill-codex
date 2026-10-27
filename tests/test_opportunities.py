"""Saved opportunity ranking, evidence binding and read-only HTTP contracts."""
import copy
import datetime as dt
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from dashboard.__main__ import handler
from dashboard.opportunities import OpportunityStore, ranking
from india.data import IST
from india.market import DEFAULT_CALENDAR
from workflow.common import upstream

CUTOFF = '2026-10-24T19:05:00+05:30'


def row(symbol, tags, price=105, volume=10000, relative=2):
    return dict(symbol=symbol, price=price, volume=volume, volume_x=relative,
                setups=tags, as_of='2026-10-23T09:15:00+05:30', source='Original synthetic test',
                rsi=60, rsi_prev=59, ma20=100, high20=103, run_pct=12, range5_pct=3,
                provider_attempts=[dict(provider='synthetic', usable=True)])


class Opportunities(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = row('ALPHA.NS', ['breakout_20d'])
        self.second = row('BETA.NS', ['oversold'], price=90, volume=1000000, relative=3)
        self.other = row('OTHER.NS', [])
        self.doc = dict(created_by='trader-scanner', mode='daily', currency='INR', paper=True,
                        execution_enabled=False, as_of=CUTOFF, universe=4, scanned=3,
                        hits=[self.first, self.second], results=[self.first, self.second, self.other],
                        failed=[dict(symbol='MISSING.NS', error='Synthetic missing history')])
        self.save()
        self.store = OpportunityStore(self.root)

    def save(self):
        (self.root/'watchlist.json').write_text(json.dumps(self.doc))

    def snapshot(self):
        dates = []
        day = dt.date(2026, 9, 1)
        while day <= dt.date(2026, 10, 23):
            if DEFAULT_CALENDAR.trading_day(day, 'NSE'):
                dates.append(day)
            day += dt.timedelta(days=1)
        bars = [dict(time=dt.datetime.combine(day, dt.time(9, 15), IST).isoformat(),
                     open=100+i, high=101+i, low=99+i, close=100+i, volume=1000+i)
                for i, day in enumerate(dates)]
        tags, values = upstream('scanner').find_setups([b['close'] for b in bars])
        self.first.update(price=bars[-1]['close'], volume=bars[-1]['volume'], setups=tags, **values)
        self.save()
        directory = self.root/'scans'/'synthetic-run'
        (directory/'candles').mkdir(parents=True)
        shutil.copyfile(self.root/'watchlist.json', directory/'watchlist.json')
        path = directory/'candles'/'ALPHA.NS.json'
        path.write_text(json.dumps(dict(symbol='ALPHA.NS', cutoff=CUTOFF,
                                      source=self.first['source'], bars=bars)))
        return path

    def test_all_matches_rank_by_evidence_not_oversold_depth_or_price(self):
        doc = self.store.listing()
        self.assertEqual((doc['universe'], doc['scanned'], doc['failed'], doc['matched']), (4, 3, 1, 2))
        self.assertEqual([r['symbol'] for r in doc['opportunities']], ['ALPHA.NS', 'BETA.NS'])
        self.assertEqual([r['rank'] for r in doc['opportunities']], [1, 2])
        self.assertGreater(doc['opportunities'][0]['score'], doc['opportunities'][1]['score'])
        self.assertEqual(doc['data_dates'], ['2026-10-23'])
        self.assertFalse(doc['execution_enabled'])
        self.assertIn('not a probability', doc['ranking']['description'])

    def test_score_is_bounded_and_old_candles_are_penalized(self):
        strong = row('ALPHA.NS', ['breakout_20d', 'bull_flag', 'reclaim_ma20'], volume=10000000, relative=100)
        current = ranking(strong, '2026-10-23')
        old = ranking(strong, '2026-10-26')
        self.assertEqual(current['score'], 100)
        self.assertEqual(old['score'], 50)
        self.assertEqual(old['freshness'], 'older')

    def test_equal_scores_use_turnover_then_symbol(self):
        self.second.update(self.first, symbol='BETA.NS')
        self.doc['hits'].reverse()
        self.save()
        self.assertEqual([r['symbol'] for r in self.store.listing()['opportunities']], ['ALPHA.NS', 'BETA.NS'])

    def test_names_are_optional_and_refresh_without_replacing_scan(self):
        before = self.store.listing()
        path = self.root/'instrument-names.json'
        path.write_text(json.dumps(dict(names={'ALPHA.NS':'Synthetic Alpha Ltd'})))
        after = self.store.listing()
        self.assertEqual(after['scan_id'], before['scan_id'])
        self.assertEqual(after['opportunities'][0]['name'], 'Synthetic Alpha Ltd')
        path.write_text('[]')
        self.assertEqual(self.store.listing()['opportunities'][0]['name'], 'ALPHA')

    def test_detail_binds_saved_candles_and_explains_original_rules(self):
        self.snapshot()
        doc = self.store.listing()
        detail = self.store.detail('ALPHA.NS', doc['scan_id'])
        self.assertGreaterEqual(len(detail['candles']), 30)
        self.assertEqual(detail['candles'][-1]['close'], detail['price'])
        self.assertEqual(detail['evidence'][0]['key'], 'breakout_20d')
        self.assertIn('prior 20-session', detail['evidence'][0]['explanation'])
        self.assertEqual(detail['decision']['risk']['status'], 'NOT_CLEARED')
        self.assertFalse(detail['decision']['can_prepare_order'])
        self.assertTrue(any(check['label'] == 'Completed-bar entry' and check['status'] == 'REQUIRED' for check in detail['checks']))
        self.assertFalse(detail['execution_enabled'])

    def test_missing_or_changed_history_never_gets_a_fabricated_chart(self):
        detail = self.store.detail('ALPHA.NS')
        self.assertEqual(detail['candles'], [])
        path = self.snapshot()
        self.store = OpportunityStore(self.root)
        payload = json.loads(path.read_text())
        payload['bars'][-2]['close'] += .5  # Keep latest close unchanged while changing MA20/high20.
        path.write_text(json.dumps(payload))
        detail = self.store.detail('ALPHA.NS')
        self.assertEqual(detail['candles'], [])
        self.assertIn('do not match', detail['history_note'])

    def test_unknown_symbol_stale_scan_and_traversal_are_rejected(self):
        for symbol in ('../ALPHA.NS', 'OTHER.NS', 'UNKNOWN.NS'):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.store.detail(symbol)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.detail('ALPHA.NS', 'old-scan-id')

    def test_unbound_hits_and_bad_coverage_fail_closed(self):
        self.doc['hits'][0] = copy.deepcopy(self.first)
        self.doc['hits'][0]['price'] = 123
        self.save()
        with self.assertRaisesRegex(ValueError, 'not present'):
            self.store.listing()
        self.doc['hits'][0] = self.first
        self.doc['scanned'] += 1
        self.save()
        with self.assertRaisesRegex(ValueError, 'coverage counts'):
            self.store.listing()

    def test_daily_coverage_lists_insufficient_history_separately_from_failures(self):
        self.doc['results'].extend([
            dict(symbol='SHORT.NS', status='UNAVAILABLE', reason='Fewer than 30 sessions'),
            dict(symbol='STALE.NS', status='STALE', reason='Latest session missing'),
        ])
        self.doc['universe'] += 2
        self.save()
        result = self.store.listing()
        self.assertEqual(result['coverage'], dict(requested=6, scanned=3, failed=1, unavailable=1, stale=1, skipped=0))
        self.assertEqual({row['symbol'] for row in result['coverage_exceptions']}, {'SHORT.NS','STALE.NS','MISSING.NS'})

    def test_workspace_and_snapshot_symlink_escape_is_rejected(self):
        path = self.snapshot()
        with tempfile.TemporaryDirectory() as other:
            external = Path(other)/'candles.json'
            shutil.copyfile(path, external)
            path.unlink()
            path.symlink_to(external)
            self.assertEqual(self.store.detail('ALPHA.NS')['candles'], [])
            scan = self.root/'watchlist.json'
            external_scan = Path(other)/'scan.json'
            shutil.copyfile(scan, external_scan)
            scan.unlink()
            scan.symlink_to(external_scan)
            with self.assertRaises(ValueError):
                self.store.listing()

    def test_empty_workspace_and_http_opportunity_routes(self):
        self.assertEqual(OpportunityStore(self.root/'empty').listing()['opportunities'], [])
        try:
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.root/'team', self.root))
        except PermissionError:
            self.skipTest('Sandbox disallows loopback sockets')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:'+str(server.server_port)
        with urllib.request.urlopen(base+'/api/opportunities') as response:
            doc = json.load(response)
            self.assertEqual(doc['matched'], 2)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with urllib.request.urlopen(base+'/api/opportunities/ALPHA.NS?scan='+doc['scan_id']) as response:
            self.assertEqual(json.load(response)['symbol'], 'ALPHA.NS')
        for suffix in ('../ALPHA.NS', '%2e%2e%2fALPHA.NS', 'UNKNOWN.NS', 'ALPHA.NS?scan=old'):
            with self.subTest(suffix=suffix), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base+'/api/opportunities/'+suffix)
            self.assertEqual(caught.exception.code, 409)
            caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(base+'/api/opportunities', data=b'{}', method='POST'))
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()


if __name__ == '__main__':
    unittest.main()
