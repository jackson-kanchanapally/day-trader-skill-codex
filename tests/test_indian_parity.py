"""Active original-workflow compatibility; all market inputs are synthetic."""
import contextlib
import copy
import csv
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.parse
from dataclasses import replace
from unittest.mock import patch

from dashboard.opportunities import OpportunityStore
from india.data import Bar, CSVProvider, IST
from india.forecast import _simulate
from india.market import DEFAULT_CALENDAR
from workflow.allocation import allocate
from workflow.cli import main
from workflow.common import clock, config, read, write
from workflow.data import MarketData, universe
from workflow.financials import financials
from workflow.research import news
from workflow.scanning import summaries
from workflow.strategy import entry, scan
from workflow.watch import watch

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tests/fixtures/india'
NOW = clock('2026-10-19T09:35:00+05:30')
BARS = CSVProvider(FIX/'synthetic.csv').history('DEMO.NS', '5m')[:4]


def spark_result(symbol, rows, volume=1000):
    return dict(symbol=symbol, response=[dict(meta=dict(symbol=symbol, currency='INR', dataGranularity='5m',
                regularMarketTime=int(NOW.timestamp()), regularMarketVolume=volume),
                timestamp=[int(b.time.timestamp()) for b in rows],
                indicators=dict(quote=[dict(close=[b.close for b in rows])]))])


def payload(doc):
    return contextlib.closing(io.BytesIO(json.dumps(doc).encode()))


def documents(text):
    out = []
    decoder = json.JSONDecoder()
    while text.strip():
        item, end = decoder.raw_decode(text.lstrip())
        out.append(item)
        text = text.lstrip()[end:]
    return out


class BroadScanner(unittest.TestCase):
    def test_malformed_or_duplicate_summary_falls_back_to_full_candles(self):
        bodies = [[], dict(spark=None), dict(spark=dict(result=[None])),
                  dict(spark=dict(result=[spark_result('DEMO.NS', BARS)]*2))]
        for body in bodies:
            with self.subTest(body=body), patch('urllib.request.urlopen', side_effect=lambda *a, **kw: payload(body)), patch.object(MarketData, '_yahoo', return_value=BARS) as fetch:
                doc = scan(MarketData(NOW, auto_fallback=False), ['DEMO.NS'], NOW, config())
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(len(doc['hits']), 1)
                self.assertTrue(doc['fetch']['summary_errors'])

    def test_daily_batch_hits_equal_full_snapshot_rules_and_account_for_missing_data(self):
        days, day = [], dt.date(2026,10,16)
        while len(days)<40:
            if DEFAULT_CALENDAR.trading_day(day): days.append(day)
            day -= dt.timedelta(days=1)
        rising = [Bar(dt.datetime.combine(d, dt.time(9,15), IST),100+i,101+i,99+i,100+i,1000) for i,d in enumerate(reversed(days))]
        flat = [replace(b, open=100, high=101, low=99, close=100) for b in rising]
        histories = {'DEMO.NS':rising, 'FLAT.NS':flat, 'SHORT.NS':rising[-10:]}
        raw = []
        for symbol, bars in histories.items():
            row = spark_result(symbol, bars)
            row['response'][0]['meta']['dataGranularity'] = '1d'
            raw.append(row)
        class Data:
            source = 'Original synthetic daily snapshot'
            def history(self, symbol, interval): return histories[symbol]
        baseline = scan(Data(), list(histories), NOW, config(), mode='daily')
        with patch('urllib.request.urlopen', side_effect=lambda *a, **kw: payload(dict(spark=dict(result=raw)))), patch.object(MarketData, '_yahoo', lambda self,symbol,interval: histories[symbol]):
            doc = scan(MarketData(NOW, auto_fallback=False), list(histories), NOW, config(), mode='daily')
        self.assertEqual([r['symbol'] for r in doc['hits']], [r['symbol'] for r in baseline['hits']])
        self.assertEqual(doc['scanned'], 2)
        self.assertEqual(doc['unavailable'][0]['symbol'], 'SHORT.NS')
        with tempfile.TemporaryDirectory() as directory:
            write(Path(directory)/'watchlist.json', doc)
            self.assertEqual(OpportunityStore(directory).listing()['matched'], 1)

    def test_500_symbol_screen_is_batched_and_matches_frozen_baseline(self):
        names = ['S%03d.NS'%i for i in range(500)]
        rows = [replace(b, open=100, high=101, low=98, close=100) for b in BARS]
        rows[-1] = replace(rows[-1], open=99, close=99)
        calls = []
        def fetch(request, **kwargs):
            batch = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)['symbols'][0].split(',')
            calls.append(len(batch))
            return payload(dict(spark=dict(result=[spark_result(s, rows) for s in batch])))
        with patch('urllib.request.urlopen', side_effect=fetch), patch.object(MarketData, '_yahoo', side_effect=AssertionError('No full snapshot needed')):
            doc = scan(MarketData(NOW, auto_fallback=False), names, NOW, config())
        self.assertEqual(len(calls), 25)
        self.assertEqual(doc['universe'], 500)
        self.assertEqual(doc['scanned'], 500)
        self.assertEqual(len(doc['results']), 500)
        self.assertFalse(doc['hits'])
        self.assertEqual(doc['counts']['full_snapshots'], 0)

    def test_default_scan_uses_universe_instead_of_old_watchlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'universe.txt').write_text('DEMO.NS\nOTHER.NS\n')
            write(path/'watchlist.json', dict(hits=[dict(symbol='OLD.NS')]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main('scanner', ['orb','--workspace',directory,'--csv',str(FIX/'synthetic.csv'),'--now',NOW.isoformat(),'--no-write'])
            doc = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(doc['universe'], 2)
            self.assertEqual(doc['failed'][0]['symbol'], 'OTHER.NS')

    def test_batches_cover_every_symbol_and_keep_indian_suffix(self):
        names = ['S%03d.NS'%i for i in range(43)]
        calls = []
        def fetch(request, **kwargs):
            batch = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)['symbols'][0].split(',')
            calls.append(batch)
            return payload(dict(spark=dict(result=[spark_result(s, BARS) for s in batch])))
        with patch('urllib.request.urlopen', side_effect=fetch):
            rows, errors, count = summaries(names, NOW, 'orb', DEFAULT_CALENDAR)
        self.assertEqual([len(c) for c in calls], [20, 20, 3])
        self.assertEqual(set(rows), set(names))
        self.assertEqual(count, 3)
        self.assertFalse(errors)
        self.assertTrue(all(row['candidate'] for row in rows.values()))

    def test_close_only_screen_never_invents_ohlcv_and_confirms_only_candidates(self):
        flat = [replace(b, open=100, high=101, low=99, close=100) for b in BARS]
        flat[-1] = replace(flat[-1], open=99, high=100, low=98, close=99)
        body = dict(spark=dict(result=[spark_result('DEMO.NS', BARS), spark_result('FLAT.NS', flat)]))
        fetched = []
        def chart(source, symbol, interval):
            fetched.append(symbol)
            return BARS
        with patch('urllib.request.urlopen', return_value=payload(body)), patch.object(MarketData, '_yahoo', chart):
            result = scan(MarketData(NOW, auto_fallback=False), ['DEMO.NS', 'FLAT.NS'], NOW, config())
        self.assertEqual(fetched, ['DEMO.NS'])
        self.assertEqual([r['symbol'] for r in result['hits']], ['DEMO.NS'])
        self.assertEqual(result['counts']['full_snapshots'], 1)
        flat_row = next(r for r in result['results'] if r['symbol'] == 'FLAT.NS')
        self.assertIsNone(flat_row['volume'])
        self.assertNotIn('high', flat_row)

    def test_confirmation_cap_saves_fetches_and_exposes_skipped_symbols(self):
        body = dict(spark=dict(result=[spark_result('SMALL.NS', BARS, 100), spark_result('BIG.NS', BARS, 10000)]))
        with patch('urllib.request.urlopen', return_value=payload(body)), patch.object(MarketData, '_yahoo', return_value=BARS) as fetch:
            result = scan(MarketData(NOW, auto_fallback=False), ['SMALL.NS', 'BIG.NS'], NOW, config(), max_confirm=1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result['hits'][0]['symbol'], 'BIG.NS')
        self.assertEqual(result['skipped'][0]['symbol'], 'SMALL.NS')
        self.assertEqual(result['counts']['requested'], 2)
        self.assertEqual(result['counts']['skipped'], 1)
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(result['counts']['scanned'], 1)
        self.assertEqual(result['counts']['requested'], result['scanned']+len(result['failed'])+len(result['skipped']))

    def test_direct_confirmation_cap_and_missing_candle_diagnostics(self):
        class Data:
            source = 'original synthetic snapshot'
            attempts = {('GAPPED.NS', '5m'): [dict(provider='synthetic', usable=False)]}
            def history(self, symbol, interval):
                return BARS[:1]+BARS[2:] if symbol == 'GAPPED.NS' else BARS
        result = scan(Data(), ['BIG.NS', 'SMALL.NS', 'GAPPED.NS'], NOW, config(), max_confirm=1)
        self.assertEqual((result['scanned'], len(result['failed']), len(result['skipped'])), (1, 1, 1))
        self.assertEqual(result['counts']['requested'], sum(result['counts'][key] for key in ('scanned', 'failed', 'skipped', 'stale', 'unavailable')))
        self.assertEqual(result['failed'][0]['missing_bars'], [BARS[1].time.isoformat()])
        self.assertEqual(result['failed'][0]['provider_attempts'][0]['provider'], 'synthetic')

    def test_batch_outage_and_missing_summary_fall_back_without_losing_names(self):
        def chart(source, symbol, interval):
            if symbol == 'BROKEN.NS':
                raise OSError('synthetic outage')
            return BARS
        with patch('urllib.request.urlopen', side_effect=OSError('batch outage')), patch.object(MarketData, '_yahoo', chart):
            result = scan(MarketData(NOW, auto_fallback=False), ['DEMO.NS', 'BROKEN.NS'], NOW, config())
        self.assertEqual(result['universe'], 2)
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(result['failed'][0]['symbol'], 'BROKEN.NS')
        self.assertTrue(result['fetch']['summary_errors'])

    def test_uncapped_stage_matches_original_evaluator_on_same_frozen_rows(self):
        class Data:
            source = 'original synthetic snapshot'
            def history(self, symbol, interval):
                return BARS if symbol != 'FLAT.NS' else [replace(b, open=100, high=101, low=99, close=100) for b in BARS]
        data = Data()
        names = ['DEMO.NS', 'OTHER.NS', 'FLAT.NS']
        expected = {s for s in names if entry(data.history(s, '5m'), s, NOW, config())['status'] == 'triggered'}
        result = scan(data, names, NOW, config())
        self.assertEqual({r['symbol'] for r in result['hits']}, expected)
        late = scan(data, names, NOW+dt.timedelta(minutes=10), config(), eligible_only=True)
        self.assertFalse(late['eligible_hits'])

    def test_parallel_fetch_is_bounded_and_cached(self):
        active = peak = 0
        lock = threading.Lock()
        def chart(source, symbol, interval):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.02)
            with lock:
                active -= 1
            return BARS
        data = MarketData(NOW, auto_fallback=False)
        names = ['S%d.NS'%i for i in range(12)]
        with patch.object(MarketData, '_yahoo', chart) as fetch:
            data.prefetch(names, workers=3)
            data.prefetch(names, workers=3)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 3)
        for symbol in names:
            self.assertEqual(data.history(symbol), BARS)
        self.assertEqual(data.fetch_metrics['uncached'], 0)

    def test_universe_excludes_etfs_sme_and_suspended_products(self):
        def row(symbol, **kw):
            return dict(segment='NSE_EQ', instrument_type='EQ', lot_size=1, trading_symbol=symbol, **kw)
        rows = [row('DEMO'), row('NIFTYBEES', name='Nifty ETF'), row('SME', is_sme=True), row('HALT', suspended=True), row('BOND', name='Example Bond')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'master.json'
            write(path, rows)
            self.assertEqual(universe('NSE', path)[0], ['DEMO.NS'])


class Watcher(unittest.TestCase):
    def test_cli_replay_advances_without_real_sleep_and_writes_fresh_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch('time.sleep', side_effect=AssertionError('Replay must not sleep')):
                code = main('entry', ['watch', 'DEMO.NS', '--csv', str(FIX/'synthetic.csv'), '--now', '2026-10-19T09:25:00+05:30', '--every', '60', '--max-checks', '20', '--workspace', directory])
            self.assertEqual(code, 0, output.getvalue())
            events = documents(output.getvalue())
            bars = [e['bar_start'] for e in events if e.get('event') == 'bar']
            self.assertEqual(len(bars), 3)  # initial latest bar, then each newly completed bar
            self.assertEqual(len(set(bars)), 3)
            self.assertEqual(events[-1]['signal_confirmed_at'], NOW.isoformat())
            self.assertTrue(read(Path(directory)/'entry.json')['eligible'])
            self.assertFalse((Path(directory)/'state/paper.sqlite').exists())

    def test_read_failures_recover_then_stop_at_consecutive_error_limit(self):
        reads = []
        def provider(now):
            reads.append(now)
            if len(reads) == 1:
                raise OSError('temporary read failure')
            return BARS, 'synthetic', []
        evaluate = lambda rows, now, source: entry(rows, 'DEMO.NS', now, config(), source=source)
        result, code = watch(provider, evaluate, NOW-dt.timedelta(minutes=1), every=60, replay=True)
        self.assertEqual(code, 0)
        self.assertTrue(result['eligible'])
        errors = []
        def bad(now):
            raise OSError('offline')
        result, code = watch(bad, evaluate, NOW, replay=True, max_errors=3, emit=errors.append)
        self.assertEqual(code, 1)
        self.assertEqual(result['watch_stop_reason'], 'read_error_limit')
        self.assertEqual(len(errors), 3)

    def test_cutoff_and_no_write_are_enforced(self):
        evaluate = lambda rows, now, source: entry(rows, 'DEMO.NS', now, config())
        result, code = watch(lambda now: self.fail('No read after cutoff'), evaluate, NOW.replace(hour=15, minute=1))
        self.assertEqual(result['watch_stop_reason'], 'cutoff')
        self.assertEqual(code, 1)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main('entry', ['watch', 'DEMO.NS', '--csv', str(FIX/'synthetic.csv'), '--now', NOW.isoformat(), '--workspace', directory, '--no-write']), 0)
            self.assertFalse((Path(directory)/'entry.json').exists())

    def test_original_opening_volume_filter_and_optional_same_time_basis(self):
        original = entry(BARS, 'DEMO.NS', NOW, config(), min_volume_x=2, volume_basis='opening-range')
        self.assertTrue(original['eligible'])
        self.assertEqual(original['trigger']['relative_volume']['mode'], 'opening_range_average')
        self.assertFalse(entry(BARS, 'DEMO.NS', NOW, config(), min_volume_x=2, volume_basis='same-time')['eligible'])


class NewsParity(unittest.TestCase):
    def test_same_story_merges_symbols_sources_names_and_changed_guids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for symbol, guid in [('DEMO.NS', 'one'), ('OTHER.NS', 'two')]:
                (path/('yahoo-'+symbol+'.xml')).write_text('<rss><channel><item><guid>'+guid+'</guid><title>NSE:OTHER and Demo Industries announce agreement</title><link>https://example.invalid/story</link><pubDate>Mon, 19 Oct 2026 03:30:00 GMT</pubDate></item></channel></rss>')
            doc, state = news(['DEMO.NS', 'OTHER.NS'], NOW, {}, offline=path, names={'DEMO.NS':'Demo Industries'})
            self.assertEqual(len(doc['items']), 1)
            self.assertEqual(doc['items'][0]['touches'], ['DEMO.NS', 'OTHER.NS'])
            self.assertEqual(len(doc['items'][0]['publications']), 2)
            self.assertFalse(news(['DEMO.NS', 'OTHER.NS'], NOW, state, offline=path)[0]['items'])
            self.assertEqual(len(news(['DEMO.NS', 'OTHER.NS'], NOW, state, offline=path, all_items=True)[0]['items']), 1)

    def test_future_announcements_never_merge_into_a_past_story(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            raw = '<rss><channel><item><guid>a</guid><title>Result</title><link>https://example.invalid/story</link><pubDate>Mon, 19 Oct 2026 03:30:00 GMT</pubDate></item></channel></rss>'
            (path/'yahoo-DEMO.NS.xml').write_text(raw)
            write(path/'announcements.json', dict(items=[dict(symbol='OTHER.NS', title='Result', source='https://example.invalid/story', published_at='2026-10-19T12:00:00+05:30')]))
            doc, _ = news(['DEMO.NS', 'OTHER.NS'], NOW, {}, offline=path, announcements=path/'announcements.json')
            self.assertEqual(doc['items'][0]['touches'], ['DEMO.NS'])
            self.assertTrue(doc['errors'])  # missing OTHER feed remains visible


class FinancialParity(unittest.TestCase):
    def cumulative(self):
        ends = ['2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31']
        rows = [dict(period_start='2025-04-01', period_end=end, period_type='annual' if i == 3 else 'ytd',
                     revenue=value, net_profit=value/10, diluted_eps=value/100,
                     published_at=(dt.date.fromisoformat(end)+dt.timedelta(days=25)).isoformat()+'T12:00:00+05:30',
                     source='https://example.invalid/filing/'+str(i)) for i, (end, value) in enumerate(zip(ends, [100, 250, 450, 700]))]
        return dict(symbol='DEMO.NS', currency='INR', unit='INR_lakh', basis='standalone', quarters=rows,
                    eps_ttm_method='sum_comparable_quarters', eps_share_basis='unchanged synthetic diluted share denominator')

    def run_financials(self, doc, now=NOW):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'facts.json'
            write(path, doc)
            return financials(path, 'DEMO.NS', now)

    def test_ytd_q4_units_qoq_and_explicit_ttm_eps(self):
        result = self.run_financials(self.cumulative())
        self.assertEqual([q['revenue'] for q in result['quarters']], [10000000,15000000,20000000,25000000])
        self.assertEqual(result['quarters'][-1]['period_start'], '2026-01-01')
        self.assertEqual(result['ttm_revenue'], 70000000)
        self.assertEqual(result['ttm_diluted_eps'], 7)
        self.assertAlmostEqual(result['quarters'][-1]['revenue_qoq_pct'], 25)
        self.assertEqual(len(result['quarters'][-1]['derivation_sources']), 2)

    def test_eps_is_unavailable_without_documented_comparable_basis(self):
        doc = self.cumulative()
        del doc['eps_share_basis']
        result = self.run_financials(doc)
        self.assertIsNone(result['ttm_diluted_eps'])
        self.assertIsNone(result['quarters'][-1]['diluted_eps'])
        self.assertEqual(result['ttm_revenue'], 70000000)

    def test_cutoff_revisions_and_newer_result_staleness(self):
        doc = read(FIX/'workflow/financials.json')
        first = doc['quarters'][0]
        first['revision_id'] = 'first'
        revision = dict(first, revision_id='second', supersedes='first', published_at='2026-10-18T12:00:00+05:30', revenue=1234)
        doc['quarters'].append(revision)
        result = self.run_financials(doc)
        self.assertEqual(result['quarters'][0]['revision_id'], 'second')
        result = self.run_financials(doc, clock('2026-10-17T12:00:00+05:30'))
        self.assertEqual(result['quarters'][0]['revision_id'], 'first')
        doc['filings'] = [dict(source='https://example.invalid/new-results', published_at='2026-10-19T09:00:00+05:30')]
        self.assertTrue(self.run_financials(doc)['newer_results_filings'])
        bad = copy.deepcopy(doc)
        del bad['quarters'][-1]['supersedes']
        with self.assertRaises(ValueError):
            self.run_financials(bad)

    def test_directory_batch_discovery_retains_missing_company(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write(path/'financials/DEMO.NS.json', read(FIX/'workflow/financials.json'))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main('analyst', ['DEMO.NS', 'MISSING.NS', '--workspace', directory, '--now', NOW.isoformat()])
            doc = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(doc['analyzed'], 1)
            self.assertEqual(doc['errors'][0]['symbol'], 'MISSING.NS')


class OtherCompatibility(unittest.TestCase):
    def test_original_full_cli_handoff_includes_news_scenarios_risk_and_ticket(self):
        import test_workflow
        fixture = test_workflow.Workflow()
        fixture.setUp()
        bars = [b for _, rows in fixture.full_days() for b in rows]+fixture.bars
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root/'history.csv'
            with csv_path.open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['symbol','interval','time','open','high','low','close','volume'])
                for b in bars:
                    writer.writerow(['DEMO.NS','5m',b.time.isoformat(),b.open,b.high,b.low,b.close,b.volume])
            shared = ['--workspace', directory, '--now', NOW.isoformat()]
            market = ['--csv', str(csv_path)]
            ticks = ['--tick','.05','--security-master',str(FIX/'workflow/security-master.csv')]
            account = ['--capital','100000','--risk','500']
            def call(role, args):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(role, args+shared)
                self.assertEqual(code, 0, output.getvalue())
                return documents(output.getvalue())[-1]
            self.assertEqual(call('scanner', ['orb','--symbols','DEMO.NS']+market)['counts']['eligible'], 1)
            self.assertTrue(call('news', ['--tickers','DEMO.NS','--csv',str(FIX/'workflow'),'--no-moves'])['items'])
            self.assertEqual(call('analyst', ['DEMO.NS','--filing',str(FIX/'workflow/financials.json'),'--write'])['status'], 'AVAILABLE')
            self.assertTrue(call('entry', ['watch','DEMO.NS','--max-checks','1']+market+ticks)['eligible'])
            self.assertEqual(call('exit', ['plan','--write']+ticks)['stop_spec'], 'or-mid')
            scenarios = call('timemachine', ['run','--replays','32','--window','0','--write']+market+account)
            self.assertEqual(scenarios['status'], 'AVAILABLE', scenarios)
            self.assertEqual(sum(scenarios['mc']['hist']['bins']), 32)
            self.assertTrue(scenarios['verdict'])
            self.assertTrue(call('budget', ['--write']+account)['orders'])
            self.assertEqual(call('risk', market+ticks+account)['pass'], 1)
            self.assertEqual(call('broker', ['show']+market+ticks+account)['status'], 'READY_PAPER_TICKET')
            self.assertTrue(call('alerts', ['add','--from-plan'])['added'])
            self.assertFalse(read(root/'state/broker.json', {'orders':[]})['orders'])

    def test_weighted_allocation_caps_cash_and_costs(self):
        prices = dict(prices={'DEMO.NS':100, 'OTHER.NS':50}, detail={s:dict(time=NOW.isoformat()) for s in ['DEMO.NS','OTHER.NS']})
        cfg = config(budget=5000, max_position_pct=60, max_order_inr=2500)
        rows = [dict(ticker='DEMO.NS', weight=90, name=''), dict(ticker='OTHER.NS', weight=10, name='')]
        doc = allocate(rows, NOW, cfg, prices)
        self.assertLessEqual(doc['estimated_debit_inr'], 4500)
        self.assertTrue(all(r['estimated_debit_inr'] <= 2500 for r in doc['allocations']))
        self.assertTrue(all(isinstance(r['qty'], int) for r in doc['allocations']))
        self.assertGreater(doc['allocations'][1]['target_inr'], 450)
        self.assertFalse(doc['ready'])
        stale = copy.deepcopy(prices)
        stale['detail']['DEMO.NS']['time'] = '2026-10-16T15:00:00+05:30'
        with self.assertRaises(ValueError):
            allocate(rows, NOW, cfg, stale)

    def test_simulation_window_histogram_and_determinism(self):
        opening = NOW.replace(hour=9, minute=15)
        rows = [Bar(opening+dt.timedelta(minutes=5*i), 100+i*.1, 101+i*.1, 99+i*.1, 100+i*.1, 1000) for i in range(72)]
        plan = dict(entry=100, stop=95, target=110, qty=10, max_loss_estimate=50, flat_by='10:00')
        a = _simulate([rows], plan, 4, 40, 19, 'NSE', None, window=0)
        self.assertEqual(a, _simulate([rows], plan, 4, 40, 19, 'NSE', None, window=0))
        self.assertEqual(sum(a['hist']['bins']), 40)
        self.assertEqual(len(a['hist']['bins']), 24)
        self.assertEqual(a['window'], 0)
        with self.assertRaises(ValueError):
            _simulate([rows], plan, 4, 40, 19, 'NSE', None, window=11)

    def test_daily_archive_survives_intraday_scan_and_shows_bound_chart(self):
        days = []
        day = dt.date(2026,10,16)
        while len(days)<40:
            if DEFAULT_CALENDAR.trading_day(day):
                days.append(day)
            day -= dt.timedelta(days=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'daily.csv'
            with path.open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['symbol','interval','time','open','high','low','close','volume'])
                for i, day in enumerate(reversed(days)):
                    writer.writerow(['DEMO.NS','1d',dt.datetime.combine(day,dt.time(9,15),IST).isoformat(),100+i,101+i,99+i,100+i,1000])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main('scanner', ['daily', '--symbols','DEMO.NS', '--csv', str(path),'--now',NOW.isoformat(),'--workspace',directory]), 0)
                self.assertEqual(main('scanner', ['orb','--symbols','DEMO.NS','--csv',str(FIX/'synthetic.csv'),'--now',NOW.isoformat(),'--workspace',directory]), 0)
            store = OpportunityStore(root)
            self.assertEqual(store.listing()['matched'], 1)
            self.assertTrue(store.detail('DEMO.NS')['candles'])
            self.assertEqual(read(root/'watchlist.json')['mode'], 'orb')


if __name__ == '__main__':
    unittest.main()
