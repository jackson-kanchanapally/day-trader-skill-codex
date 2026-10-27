"""Regressions for whole-snapshot recovery, official facts and ORB expiry."""
import datetime as dt
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard.intraday import overlay
from india.data import Bar, IST
from workflow.data import MarketData, aggregate_minutes, universe
from workflow.cli import main
from workflow.intraday_index import capture_daily, publish
from workflow.financials import financials
from workflow.nse_filings import collect, parse_xbrl
from workflow.scenario_chart import render
from workflow.scanning import archive_scan
from workflow.research import news


class Recovery(unittest.TestCase):
    def test_official_eq_universe_excludes_etf_and_non_eq_series(self):
        equity = ('SYMBOL,NAME OF COMPANY, SERIES, MARKET LOT\n'
                  + ''.join('STOCK%04d,Example Limited,EQ,1\n' % i for i in range(1002))
                  + 'FUND,Fund Limited,EQ,1\nTRADETO,Trade To Trade,BE,1\n')
        etfs = 'Symbol,SecurityName\nFUND,Fund Limited\n'
        class Response(io.BytesIO):
            def __init__(self, url, raw):
                super().__init__(raw.encode())
                self.url = url
        def get(request, timeout):
            url = request.full_url
            return Response(url, etfs if 'etf' in url else equity)
        with patch('workflow.data.urllib.request.urlopen', side_effect=get):
            names, source = universe('NSE')
        self.assertEqual(len(names), 1002)
        self.assertNotIn('FUND.NS', names)
        self.assertNotIn('TRADETO.NS', names)
        self.assertIn('EQUITY_L.csv', source)

    def test_minute_aggregation_never_fills_one_missing_minute(self):
        start = dt.datetime(2026, 10, 26, 9, 15, tzinfo=IST)
        rows = [Bar(start+dt.timedelta(minutes=i), 100+i, 102+i, 99+i, 101+i, 10)
                for i in range(10) if i != 7]
        result = aggregate_minutes(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0].time, result[0].open, result[0].close, result[0].volume),
                         (start, 100, 105, 50))

    def test_yahoo_uses_complete_second_host_whole_snapshot(self):
        start = dt.datetime(2026, 10, 26, 9, 15, tzinfo=IST)
        now = start+dt.timedelta(minutes=15)
        data = MarketData(now, auto_fallback=False)
        data.intraday_range = '1d'
        full = [Bar(start+dt.timedelta(minutes=i*5), 100, 103, 99, 100+i, 10) for i in range(3)]
        partial = [full[0], full[2]]
        calls = []
        def urlopen(request, timeout):
            host = request.full_url.split('/')[2]
            calls.append(host)
            return io.BytesIO(json.dumps({'host': host}).encode())
        with patch('workflow.data.urllib.request.urlopen', side_effect=urlopen), patch(
                'workflow.data.parse_yahoo', side_effect=lambda doc, *args: partial if doc['host'].startswith('query2') else full):
            result = data._yahoo('DEMO.NS', '5m')
        self.assertEqual(result, full)
        self.assertEqual(calls, ['query2.finance.yahoo.com', 'query1.finance.yahoo.com'])
        self.assertEqual(data._retrieval_attempts[0]['missing_strategy_bars'], 1)
        self.assertTrue(data._retrieval_attempts[1]['usable'])

    def test_prefetch_reuses_failed_primary_and_fetches_only_current_nse_window(self):
        start = dt.datetime(2026,10,26,9,15,tzinfo=IST)
        full = [Bar(start+dt.timedelta(minutes=i*5), 100, 103, 99, 100+i, 10) for i in range(3)]
        data = MarketData(start+dt.timedelta(minutes=15))
        with patch.object(MarketData, '_yahoo', return_value=full[:1]+full[2:]) as yahoo, patch(
                'workflow.data.NSEProvider.history', return_value=full) as nse:
            data.prefetch(['DEMO.NS'], workers=2, current_only=True)
            self.assertEqual(data.history('DEMO.NS'), full)
        self.assertEqual(yahoo.call_count, 1)
        self.assertEqual(nse.call_count, 1)
        self.assertEqual(data._nse.days, 1)
        self.assertEqual([row['provider'] for row in data.attempts[('DEMO.NS','5m')]], ['yahoo','nse'])
        self.assertTrue(data.sources[('DEMO.NS','5m')].startswith('NSE'))

    def test_orb_archive_keeps_only_captured_completed_candles_without_refetch(self):
        start = dt.datetime(2026,10,26,9,15,tzinfo=IST)
        rows = [Bar(start+dt.timedelta(minutes=5*i),100,103,99,101,10) for i in range(4)]
        class Data:
            source = 'original synthetic snapshot'
            _cache = {('DEMO.NS','5m'):rows}
            def history(self, *args):
                raise AssertionError('Archiving must not fetch another snapshot')
        doc = dict(mode='orb', as_of=(start+dt.timedelta(minutes=15)).isoformat(),
                   results=[dict(symbol='DEMO.NS')], failed=[])
        with tempfile.TemporaryDirectory() as folder:
            run = archive_scan(folder, doc, Data())
            manifest = json.loads((run/'evidence.json').read_text())
            evidence = manifest['symbols']['DEMO.NS']
            raw = (run/evidence['path']).read_bytes()
            snapshot = json.loads(gzip.decompress(raw))
            self.assertEqual(evidence['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(len(snapshot['bars']), 3)
            self.assertEqual(snapshot['bars'][-1]['time'], rows[2].time.isoformat())


class IntradayBinding(unittest.TestCase):
    def test_cli_binds_selection_before_fetch_and_retains_archive_when_daily_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            daily = dict(mode='daily', currency='INR', paper=True, execution_enabled=False,
                         as_of='2026-10-26T09:35:00+05:30', hits=[{'symbol':'DEMO.NS'}])
            path = root/'daily-watchlist.json'
            path.write_text(json.dumps(daily))
            _, original = capture_daily(path)
            def fetching(data, names, now, *args, **kwargs):
                self.assertEqual(names, ['DEMO.NS'])
                path.write_text(json.dumps(dict(daily, as_of='2026-10-26T09:45:00+05:30')))
                return dict(mode='orb', currency='INR', paper=True, execution_enabled=False,
                            as_of=now.isoformat(), universe=1, scanned=1, failed=[], skipped=[],
                            counts={'requested':1, 'scanned':1, 'eligible':0}, hits=[], eligible_hits=[],
                            results=[dict(symbol='DEMO.NS', status='waiting', eligible=False)])
            output = io.StringIO()
            with patch('workflow.cli.scan', side_effect=fetching), contextlib.redirect_stdout(output):
                code = main('scanner', ['orb','--watchlist',str(path),'--workspace',str(root/'orb'),
                                        '--now','2026-10-26T09:42:00+05:30'])
            doc = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(doc['daily_origin'], original)
            receipt = json.loads(next((root/'orb/scans').glob('*/publication.json')).read_text())
            self.assertEqual(receipt['status'], 'SOURCE_CHANGED')
            self.assertFalse((root/'intraday-index.json').exists())
            self.assertTrue(next((root/'orb/scans').glob('*/watchlist.json')).is_file())

    def test_full_scan_and_subset_refresh_expire_without_reviving_old_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            daily = dict(mode='daily', currency='INR', paper=True, execution_enabled=False,
                         as_of='2026-10-26T09:35:00+05:30', hits=[{'symbol':'DEMO.NS'}])
            (root/'daily-watchlist.json').write_text(json.dumps(daily))
            _, origin = capture_daily(root/'daily-watchlist.json')
            def save(name, clock, eligible, failed=False):
                doc = dict(mode='orb', currency='INR', paper=True, execution_enabled=False,
                           as_of=clock, daily_origin=origin, universe=1, scanned=0 if failed else 1,
                           failed=[dict(symbol='DEMO.NS', error='Missing session candles')] if failed else [], skipped=[],
                           counts={'requested':1,'eligible':int(eligible)},
                           results=[] if failed else [dict(symbol='DEMO.NS', status='triggered', eligible=eligible,
                                         data_complete=True, interval='5m', signal_confirmed_at='2026-10-26T09:40:00+05:30',
                                         no_entries_after='15:00', opening_range={'high':101,'low':99})])
                path = root/name
                path.write_text(json.dumps(doc))
                publish(root, path)
            save('full.json', '2026-10-26T09:42:00+05:30', True)
            sha = hashlib.sha256((root/'daily-watchlist.json').read_bytes()).hexdigest()
            fresh, coverage = overlay(root, sha, daily['as_of'], {'DEMO.NS'}, dt.datetime(2026,10,26,9,43,tzinfo=IST))
            self.assertEqual(fresh['DEMO.NS']['status'], 'ELIGIBLE')
            self.assertEqual(coverage['full_shortlist']['requested'], 1)
            save('subset.json', '2026-10-26T09:47:00+05:30', False)
            stale, coverage = overlay(root, sha, daily['as_of'], {'DEMO.NS'}, dt.datetime(2026,10,26,9,48,tzinfo=IST))
            self.assertEqual(stale['DEMO.NS']['status'], 'EXPIRED')
            self.assertEqual(coverage['eligible_now'], 0)
            self.assertEqual(coverage['scope'], 'full_shortlist')  # one-symbol full universe
            save('failed.json', '2026-10-26T09:50:00+05:30', False, failed=True)
            unavailable, _ = overlay(root, sha, daily['as_of'], {'DEMO.NS'}, dt.datetime(2026,10,26,9,51,tzinfo=IST))
            self.assertEqual(unavailable['DEMO.NS']['status'], 'UNAVAILABLE')
            self.assertEqual(unavailable['DEMO.NS']['previous_trigger']['status'], 'EXPIRED')
            save('failed-again.json', '2026-10-26T09:52:00+05:30', False, failed=True)
            unavailable, _ = overlay(root, sha, daily['as_of'], {'DEMO.NS'}, dt.datetime(2026,10,26,9,53,tzinfo=IST))
            self.assertEqual(unavailable['DEMO.NS']['previous_trigger']['expires_at'], '2026-10-26T09:45:00+05:30')
            (root/'subset.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash'):
                overlay(root, sha, daily['as_of'], {'DEMO.NS'}, dt.datetime(2026,10,26,9,48,tzinfo=IST))


class OfficialFacts(unittest.TestCase):
    XBRL = b'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:f="http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt">
      <xbrli:context id="OneD"><xbrli:entity><xbrli:identifier>TEST</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
      <xbrli:unit id="INRPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
      <f:Symbol contextRef="OneD">DEMO</f:Symbol><f:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</f:NatureOfReportStandaloneConsolidated>
      <f:RevenueFromOperations contextRef="OneD" unitRef="INR">12000000</f:RevenueFromOperations>
      <f:ProfitLossForPeriod contextRef="OneD" unitRef="INR">1000000</f:ProfitLossForPeriod>
      <f:DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations contextRef="OneD" unitRef="INRPerShare">2.5</f:DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations>
    </xbrli:xbrl>'''

    def test_xbrl_enforces_symbol_basis_period_and_inr_units(self):
        row = parse_xbrl(self.XBRL, 'DEMO.NS', 'https://nsearchives.nseindia.com/a.xml',
                         '2026-10-20T11:00:00+05:30')
        self.assertEqual((row['revenue'], row['diluted_eps'], row['period_end']), (12000000,2.5,'2026-09-30'))
        self.assertIsNone(parse_xbrl(self.XBRL, 'DEMO.NS', row['source'], row['published_at'], 'standalone'))
        with self.assertRaisesRegex(ValueError, 'unit|lacks'):
            parse_xbrl(self.XBRL.replace(b'unitRef="INR"', b'unitRef="USD"', 1), 'DEMO.NS', row['source'], row['published_at'])

    def test_unit_id_and_prefix_names_cannot_impersonate_inr(self):
        source, publication = 'https://nsearchives.nseindia.com/a.xml', '2026-10-20T11:00:00+05:30'
        for raw in (self.XBRL.replace(b'>iso4217:INR<', b'>iso4217:USD<', 1),
                    self.XBRL.replace(b'xmlns:iso4217="http://www.xbrl.org/2003/iso4217"', b'xmlns:iso4217="urn:wrong-currency-namespace"')):
            with self.subTest(raw=raw[:80]), self.assertRaisesRegex(ValueError, 'unit'):
                parse_xbrl(raw, 'DEMO.NS', source, publication)
        renamed = self.XBRL.replace(b'id="INR"', b'id="rupees"').replace(b'unitRef="INR"', b'unitRef="rupees"')
        self.assertEqual(parse_xbrl(renamed, 'DEMO.NS', source, publication)['revenue'], 12000000)
        invalid_eps = self.XBRL.replace(b'<xbrli:measure>xbrli:shares</xbrli:measure>', b'<xbrli:measure>xbrli:pure</xbrli:measure>')
        result = parse_xbrl(invalid_eps, 'DEMO.NS', source, publication)
        self.assertIsNone(result['diluted_eps'])
        self.assertIn('diluted_eps', result['unavailable_fields'])

    def test_duplicate_contexts_and_facts_do_not_select_an_arbitrary_value(self):
        fact = b'<f:RevenueFromOperations contextRef="OneD" unitRef="INR">12000000</f:RevenueFromOperations>'
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            parse_xbrl(self.XBRL.replace(fact, fact+fact), 'DEMO.NS',
                       'https://nsearchives.nseindia.com/a.xml', '2026-10-20T11:00:00+05:30')
        context = self.XBRL.split(b'<xbrli:context', 1)[1].split(b'</xbrli:context>', 1)[0]
        duplicate = self.XBRL.replace(b'</xbrli:xbrl>', b'<xbrli:context'+context+b'</xbrli:context></xbrli:xbrl>')
        with self.assertRaisesRegex(ValueError, 'context'):
            parse_xbrl(duplicate, 'DEMO.NS', 'https://nsearchives.nseindia.com/a.xml',
                       '2026-10-20T11:00:00+05:30')

    def test_official_feed_respects_cutoff_and_reports_provenance(self):
        feed = b'''<rss><channel><item><title>Demo Limited</title><link>https://nsearchives.nseindia.com/a.xml</link><pubDate>20-Oct-2026 11:00:00</pubDate></item></channel></rss>'''
        index = dict(symbol='DEMO', type='Integrated Filing- Financials',
                     consolidated='Consolidated', broadcast_Date='20-Oct-2026 11:00:00',
                     creation_Date='20-Oct-2026 11:00:10', qe_Date='30-SEP-2026',
                     xbrl='https://nsearchives.nseindia.com/a.xml', cmName='Demo Limited')
        def get(url, limit):
            return self.XBRL if url.endswith('/a.xml') else feed
        with patch('workflow.nse_filings._fetch', side_effect=get), patch('workflow.nse_filings._integrated_index', return_value=([index], 1)):
            early = collect('DEMO.NS', dt.datetime(2026,10,20,10,59,tzinfo=IST), feeds=['https://nsearchives.nseindia.com/feed.xml'])
            current = collect('DEMO.NS', dt.datetime(2026,10,20,11,1,tzinfo=IST), feeds=['https://nsearchives.nseindia.com/feed.xml'])
        self.assertEqual(early['quarters'], [])
        self.assertEqual(current['source_verification'], 'DIRECT_NSE_INDEX_RSS_XBRL')
        self.assertEqual(current['acquisition']['matched'], 1)
        self.assertEqual(current['acquisition']['index_total'], 1)
        self.assertEqual(current['quarters'][0]['published_at'], '2026-10-20T11:00:10+05:30')

    def test_conflicting_same_time_filings_and_newer_unreadable_results_remain_visible(self):
        def index_row(filename, day=20):
            return dict(symbol='DEMO', type='Integrated Filing- Financials', consolidated='Consolidated',
                        broadcast_Date=f'{day}-Oct-2026 11:00:00', creation_Date=f'{day}-Oct-2026 11:00:10',
                        qe_Date='30-SEP-2026', xbrl='https://nsearchives.nseindia.com/'+filename, cmName='Demo Limited')
        first, second = index_row('a.xml'), index_row('b.xml')
        def fetch(url, limit):
            if url.endswith('feed.xml'):
                return b'<rss><channel/></rss>'
            return self.XBRL if url.endswith('a.xml') else self.XBRL.replace(b'>1000000<', b'>2000000<')
        now = dt.datetime(2026,10,22,12,tzinfo=IST)
        with patch('workflow.nse_filings._fetch', side_effect=fetch), patch('workflow.nse_filings._integrated_index', return_value=([first,second],2)):
            doc = collect('DEMO.NS', now, feeds=['https://nsearchives.nseindia.com/feed.xml'])
        self.assertEqual(doc['quarters'], [])
        self.assertTrue(any('same publication time' in row['error'] for row in doc['acquisition_errors']))
        second = index_row('b.xml', 21)
        def unreadable(url, limit):
            raw = fetch(url, limit)
            return raw.replace(b'>iso4217:INR<', b'>iso4217:USD<') if url.endswith('b.xml') else raw
        with patch('workflow.nse_filings._fetch', side_effect=unreadable), patch('workflow.nse_filings._integrated_index', return_value=([first,second],2)):
            result = financials(collect('DEMO.NS', now, feeds=['https://nsearchives.nseindia.com/feed.xml']), 'DEMO.NS', now)
        self.assertTrue(result['stale'])
        self.assertEqual(len(result['newer_results_filings']), 1)
        self.assertTrue(result['newer_results_filings'][0]['source'].endswith('b.xml'))

    def test_future_revision_cannot_reenter_through_an_older_rss_timestamp(self):
        feed = b'''<rss><channel><item><title>Demo Limited</title><link>https://nsearchives.nseindia.com/a.xml</link><pubDate>20-Oct-2026 11:00:00</pubDate></item></channel></rss>'''
        row = dict(symbol='DEMO', type='Integrated Filing- Financials', consolidated='Consolidated',
                   broadcast_Date='20-Oct-2026 11:00:00', creation_Date='20-Oct-2026 11:00:10',
                   revised_Date='21-Oct-2026 12:00:00', qe_Date='30-SEP-2026',
                   xbrl='https://nsearchives.nseindia.com/a.xml')
        with patch('workflow.nse_filings._fetch', return_value=feed) as fetch, patch('workflow.nse_filings._integrated_index', return_value=([row],1)):
            result = collect('DEMO.NS', dt.datetime(2026,10,20,12,tzinfo=IST), feeds=['https://nsearchives.nseindia.com/feed.xml'])
        self.assertEqual(result['quarters'], [])
        self.assertEqual(result['acquisition']['excluded_later_revisions'], 1)
        self.assertEqual(fetch.call_count, 1)

    def test_separate_collections_refresh_shared_feed(self):
        class Response(io.BytesIO):
            url = 'https://nsearchives.nseindia.com/feed.xml'
        with patch('workflow.nse_filings._integrated_index', return_value=([],0)), patch(
                'workflow.nse_filings.urllib.request.urlopen', side_effect=lambda *args, **kw: Response(b'<rss><channel/></rss>')) as fetch:
            for _ in range(2):
                collect('DEMO.NS', dt.datetime(2026,10,20,12,tzinfo=IST), feeds=[Response.url])
        self.assertEqual(fetch.call_count, 2)


class ChartCompatibility(unittest.TestCase):
    def test_saved_scenario_bands_have_portable_text_chart(self):
        doc = dict(status='AVAILABLE', plan=dict(symbol='DEMO.NS', date='2026-10-26',
                                                entry=100, stop=99, target=102,
                                                entry_time='09:35', flat_by='15:10'),
                   mc=dict(bands=[[99, 99.5, 100, 100.5, 101],
                                  [98.5, 99.2, 100.2, 101, 102]], band_paths=2))
        chart = render(doc)
        self.assertIn('DEMO.NS', chart)
        self.assertIn('median', chart)
        self.assertIn('target', chart)
        self.assertIn('No available', render({'status':'UNAVAILABLE', 'mc':None}))


class ExchangeNewsClock(unittest.TestCase):
    def test_nse_publication_is_ist_even_when_caller_uses_utc(self):
        raw = b'''<rss><channel><item><title>Demo Limited</title><link>https://nsearchives.nseindia.com/DEMO_notice.pdf</link><description>Results announcement</description><pubDate>20-Oct-2026 12:00:00</pubDate></item></channel></rss>'''
        with patch('workflow.nse_filings._fetch', return_value=raw), patch(
                'workflow.research.urllib.request.urlopen', side_effect=lambda *args, **kwargs: io.BytesIO(b'<rss><channel/></rss>')):
            before, _ = news(['DEMO.NS'], dt.datetime(2026,10,20,6,tzinfo=dt.timezone.utc), {}, 1, names={'DEMO.NS':'Demo Limited'})
            after, _ = news(['DEMO.NS'], dt.datetime(2026,10,20,7,tzinfo=dt.timezone.utc), {}, 1, names={'DEMO.NS':'Demo Limited'})
        self.assertEqual(before['items'], [])
        self.assertEqual(len(after['items']), 1)
        self.assertEqual(after['items'][0]['published_at'], '2026-10-20T12:00:00+05:30')


if __name__ == '__main__':
    unittest.main()
