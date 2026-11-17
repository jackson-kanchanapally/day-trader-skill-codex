"""Arithmetic-only fictional snapshots. No captured exchange payloads or network."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest import mock

from india.data import IST, timestamp
from india.market import DEFAULT_CALENDAR
from india.nse_mcp import MARKET, MCPSession
from india import picks
from india.recorder import (KIND, YAHOO_KIND, YAHOO_PAUSE, SnapshotRecorder, load_candles,
                            load_stock_closes, normalize, parse_spark, sampled_candle, settings)


def at(clock):
    return timestamp('2026-11-17T' + clock + '+05:30')


def equity(clock='09:19:10', symbol='DEMO', **values):
    row = dict(symbol=symbol, series='EQ', openPrice=100, highPrice=110,
               lowPrice=90, lastTradedPrice=102, preClosePrice=99,
               volume=1000, value=.0102,
               latestTimestamp='2026-11-17 ' + clock)
    row.update(values)
    return row


def stocks(*rows):
    return dict(stocks=list(rows), updatedAt='2026-11-17T03:49:20Z')


def index_doc(clock='09:19', **values):
    row = dict(indexName='NIFTY 50', last=102, open=100, high=110, low=90,
               previousClose=99, timeVal='17-Nov-2026 ' + clock)
    row.update(values)
    return dict(indices=dict(derivatives=[row], broad=[]), nseTimestamp='17-Nov-2026 09:19:59')


def spark(symbols, closes, start='09:15'):
    """Fictional Yahoo spark document: the same arithmetic closes for every symbol."""
    first = at(start + ':00')
    times = [int((first + dt.timedelta(minutes=5 * i)).timestamp()) for i in range(len(closes))]
    return {s: dict(symbol=s, dataGranularity=300, timestamp=list(times), close=list(closes)) for s in symbols}


def parsed(doc, feed='equity', received='09:19:30'):
    return normalize(doc, feed, ['DEMO.NS'], ['NIFTY 50'], at(received).isoformat(),
                     at(received).isoformat(), 120, DEFAULT_CALENDAR)


def observations(start='09:19:10', count=6):
    first = at(start)
    result = []
    for i in range(count):
        when = first + dt.timedelta(minutes=i)
        result.append(dict(symbol='DEMO.NS', feed='equity', observed_at=when.isoformat(),
                           received_at=(when + dt.timedelta(seconds=10)).isoformat(),
                           price=100 + i, session_open=100, session_high=110,
                           session_low=90, cumulative_volume=1000 + i * 100,
                           session_vwap=101))
    return result


class Parsing(unittest.TestCase):
    def test_value_is_crore_and_vwap_is_session_value_over_volume(self):
        q = parsed(stocks(equity()))['quotes'][0]
        self.assertAlmostEqual(q['cumulative_value_inr'], 102000)
        self.assertAlmostEqual(q['session_vwap'], 102)
        self.assertEqual(q['timestamp_precision'], 'second')

    def test_missing_value_and_zero_volume_do_not_invent_vwap(self):
        for changes in (dict(value=None), dict(volume=0, value=0)):
            self.assertIsNone(parsed(stocks(equity(**changes)))['quotes'][0]['session_vwap'])

    def test_missing_and_bad_instruments_are_reported_separately(self):
        result = parsed(stocks(equity(volume=None), equity(symbol='OTHER')))
        self.assertEqual(result['covered'], 0)
        self.assertEqual(result['missing'], ['DEMO.NS'])
        self.assertEqual(result['rejected'][0]['symbol'], 'DEMO.NS')

    def test_wrong_session_stale_future_nonfinite_and_fractional_volume(self):
        for changes in (dict(latestTimestamp='2026-11-16 09:19:10'),
                        dict(latestTimestamp='2026-11-17 09:16:00'),
                        dict(latestTimestamp='2026-11-17 09:20:00'),
                        dict(lastTradedPrice=float('nan')), dict(value=1e308), dict(volume=1.5),
                        dict(lowPrice=103)):
            with self.subTest(changes=changes):
                self.assertEqual(parsed(stocks(equity(**changes)))['covered'], 0)

    def test_duplicate_eq_is_rejected_but_other_series_does_not_replace_eq(self):
        self.assertEqual(parsed(stocks(equity(), equity()))['covered'], 0)
        self.assertEqual(parsed(stocks(equity(), equity(series='BE')))['covered'], 1)

    def test_index_uses_its_own_minute_timestamp_and_has_no_fake_volume(self):
        q = parsed(index_doc(), 'indices')['quotes'][0]
        self.assertEqual(q['observed_at'], at('09:19:00').isoformat())
        self.assertIsNone(q['cumulative_volume'])
        self.assertIsNone(q['session_vwap'])
        self.assertEqual(parsed(index_doc('09:16'), 'indices')['covered'], 0)

    def test_wrong_schema_and_oversize_fail_closed(self):
        for doc, feed in ((dict(stocks={}), 'equity'), (dict(indices=[]), 'indices'),
                          (stocks(*[equity()] * 501), 'equity')):
            with self.assertRaises(ValueError):
                parsed(doc, feed)


class Candles(unittest.TestCase):
    def test_cumulative_volume_window_and_sampled_ohlc(self):
        q = observations()
        row = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())
        self.assertTrue(row['complete'])
        self.assertTrue(row['ohlcv_complete'])
        self.assertEqual((row['open'], row['high'], row['low'], row['close']), (101, 105, 101, 105))
        self.assertEqual(row['volume'], 500)
        self.assertEqual(row['volume_window_start'], at('09:19:10').isoformat())
        self.assertFalse(row['volume_exchange_aligned'])
        self.assertEqual(row['kind'], KIND)
        self.assertFalse(row['exchange_candle'])
        self.assertFalse(row['execution_enabled'])

    def test_missing_poll_is_incomplete_without_interpolation(self):
        q = observations()
        del q[3]
        row = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())
        self.assertFalse(row['complete'])
        self.assertEqual(row['sample_minutes'], 4)
        self.assertEqual(row['missing_minutes'], [at('09:22:00').isoformat()])

    def test_stale_repeated_sample_does_not_fill_a_minute(self):
        q = observations()
        q[3] = dict(q[2], received_at=q[3]['received_at'])
        self.assertFalse(sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())['complete'])

    def test_missing_baseline_and_counter_reset_do_not_create_volume(self):
        for q in (observations()[1:], observations()):
            if len(q) == 6:
                q[3]['cumulative_volume'] = 5
            row = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())
            self.assertFalse(row['complete'])
            self.assertIsNone(row['volume'])

    def test_opening_counter_only_applies_at_sourced_session_open(self):
        q = observations('09:15:10', 5)
        row = sampled_candle('DEMO.NS', at('09:15:00'), q, at('09:20:00').isoformat(), at('09:15:00'))
        self.assertTrue(row['complete'])
        self.assertEqual(row['volume'], 1400)
        q = observations('09:20:10', 5)
        self.assertIsNone(sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat(), at('09:15:00'))['volume'])

    def test_new_session_extremes_recovered_only_within_window(self):
        q = observations()
        for row in q[3:]:
            row['session_high'], row['session_low'] = 120, 80
        row = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())
        self.assertEqual((row['high'], row['low'], row['recovered_extremes']), (120, 80, 2))
        # An extreme that changed across 09:20 cannot be assigned to either bar.
        for row in q[1:]:
            row['session_high'], row['session_low'] = 120, 80
        row = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())
        self.assertEqual((row['high'], row['low']), (105, 101))
        self.assertIn('BOUNDARY_EXTREME_UNASSIGNED', row['quality_warnings'])

    def test_later_extreme_and_later_receipt_are_never_used(self):
        q = observations()
        future = dict(q[-1], observed_at=at('09:25:10').isoformat(),
                      received_at=at('09:25:20').isoformat(), session_high=999)
        row = sampled_candle('DEMO.NS', at('09:20:00'), q + [future], at('09:25:00').isoformat())
        self.assertEqual(row['high'], 105)
        q[-1]['received_at'] = at('09:25:30').isoformat()
        self.assertFalse(sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())['complete'])
        with self.assertRaises(ValueError):
            sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:24:59').isoformat())

    def test_conflicting_source_time_and_extrema_reset_are_explicit(self):
        q = observations()
        q.append(dict(q[2], price=102.5))
        q[4]['session_high'] = 109
        reasons = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())['reasons']
        self.assertIn('CONFLICTING_SOURCE_TIMESTAMP', reasons)
        self.assertIn('SESSION_EXTREMA_RESET', reasons)

    def test_index_candles_keep_volume_missing(self):
        q = [dict(row, cumulative_volume=None) for row in observations()]
        row = sampled_candle('INDEX:NIFTY 50', at('09:20:00'), q, at('09:25:00').isoformat())
        self.assertTrue(row['complete'])
        self.assertFalse(row['ohlcv_complete'])
        self.assertIsNone(row['volume'])

    def test_index_samples_in_one_minute_are_normal_and_the_later_one_closes(self):
        q = [dict(row, cumulative_volume=None,
                  observed_at=timestamp(row['observed_at']).replace(second=0).isoformat()) for row in observations()]
        q.append(dict(q[-1], price=107.5, received_at=at('09:24:45').isoformat()))
        row = sampled_candle('INDEX:NIFTY 50', at('09:20:00'), q, at('09:25:00').isoformat())
        self.assertEqual((row['reasons'], row['complete'], row['usable']), ([], True, True))
        self.assertEqual((row['open'], row['high'], row['close']), (101, 107.5, 107.5))
        # The later sample only counts once it has actually been received.
        late = q[:-1] + [dict(q[-1], received_at=at('09:25:30').isoformat())]
        self.assertEqual(sampled_candle('INDEX:NIFTY 50', at('09:20:00'), late, at('09:25:00').isoformat())['close'], 105)

    def test_usable_needs_the_final_minute_and_at_most_one_gap(self):
        def flags(q):
            row = sampled_candle('DEMO.NS', at('09:20:00'), q, at('09:25:00').isoformat())
            return row['complete'], row['usable']
        q = observations()
        self.assertEqual(flags(q), (True, True))
        self.assertEqual(flags(q[:3] + q[4:]), (False, True))         # 09:22 missing
        self.assertEqual(flags(q[:5]), (False, False))                # final minute missing
        self.assertEqual(flags(q[:2] + q[4:]), (False, False))        # two minutes missing
        self.assertEqual(flags(q[1:]), (False, True))                 # no volume baseline: prices still usable
        self.assertEqual(flags(q + [dict(q[2], price=102.5)]), (False, False))   # conflicting stock sample
        row = sampled_candle('DEMO.NS', at('09:20:00'), q[:3] + q[4:], at('09:25:00').isoformat())
        self.assertEqual(row['missing_minutes'], [at('09:22:00').isoformat()])
        self.assertFalse(sampled_candle('DEMO.NS', at('09:20:00'), [], at('09:25:00').isoformat())['usable'])

    def test_cutoffs_are_normalized_to_ist_and_naive_times_rejected(self):
        start = at('09:20:00').astimezone(dt.timezone.utc)
        row = sampled_candle('DEMO.NS', start, observations(), at('09:25:00').isoformat())
        self.assertEqual(row['time'], at('09:20:00').isoformat())
        with self.assertRaises(ValueError):
            sampled_candle('DEMO.NS', start.replace(tzinfo=None), observations(), at('09:25:00').isoformat())


class Recording(unittest.TestCase):
    def setup_recorder(self, directory, clock, session=None, **overrides):
        cfg = dict(enabled=True, state_dir=directory, symbols=['DEMO.NS'], indices=['NIFTY 50'],
                   feeds=['equity', 'indices'], stock_source='off')
        cfg.update(overrides)
        session = session or mock.Mock()
        recorder = SnapshotRecorder(cfg, session=session, now_fn=lambda: clock[0])
        self.addCleanup(recorder.close)
        return recorder, session

    def test_staggered_calls_one_per_tick_and_persisted_restart_spacing(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:20:00')]
            recorder, session = self.setup_recorder(directory, clock)
            session.call.return_value = stocks(equity('09:19:10'))
            self.assertEqual(recorder.tick()['status'], 'RECORDED')
            self.assertEqual(recorder.tick()['status'], 'WAITING')
            other, _ = self.setup_recorder(directory, clock)
            self.assertEqual(other.tick()['status'], 'WAITING')
            clock[0] += dt.timedelta(seconds=30)
            session.call.return_value = index_doc('09:20')
            self.assertEqual(recorder.tick()['feed'], 'indices')
            self.assertEqual([c.args[0] for c in session.call.call_args_list],
                             ['cm_get_equity_stocks', 'cm_get_live_indices'])
            self.assertTrue((Path(directory) / 'status.json').is_file())

    def test_default_is_index_only_every_poll_and_never_loads_the_stock_universe(self):
        with tempfile.TemporaryDirectory() as directory:
            clock, session = [at('09:20:00')], mock.Mock()
            recorder = SnapshotRecorder(dict(enabled=True, state_dir=directory, indices=['NIFTY 50']), session=session,
                                        now_fn=lambda: clock[0],
                                        universe_loader=mock.Mock(side_effect=AssertionError('universe loaded')))
            self.addCleanup(recorder.close)
            session.call.return_value = index_doc('09:19')
            self.assertEqual(recorder.tick()['feed'], 'indices')
            clock[0] = at('09:20:29')
            self.assertEqual(recorder.tick()['status'], 'WAITING')
            clock[0] = at('09:20:30')
            session.call.return_value = index_doc('09:20')
            self.assertEqual(recorder.tick()['status'], 'RECORDED')
            self.assertEqual([c.args[0] for c in session.call.call_args_list], ['cm_get_live_indices'] * 2)

    def test_each_feed_backs_off_alone_across_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:20:00')]
            recorder, session = self.setup_recorder(directory, clock)
            session.call.side_effect = ValueError('synthetic unavailable')
            self.assertEqual(recorder.tick()['next_poll_at'], at('09:21:00').isoformat())     # stocks: 60 s
            clock[0] = at('09:20:30')
            session.call.side_effect = None
            session.call.return_value = index_doc('09:20')
            good = recorder.tick()                                # the index feed is not paused
            self.assertEqual((good['feed'], good['status']), ('indices', 'RECORDED'))
            clock[0] = at('09:21:00')
            session.call.side_effect = ValueError('synthetic unavailable')
            other, _ = self.setup_recorder(directory, clock, session=session)
            bad = other.tick()                                    # a restart keeps the count
            self.assertEqual((bad['feed'], bad['next_poll_at']), ('equity', at('09:23:00').isoformat()))
            clock[0] = at('09:21:30')
            session.call.side_effect = None
            self.assertEqual(recorder.tick()['feed'], 'indices')
            clock[0] = at('09:22:00')
            self.assertEqual(recorder.tick()['feed'], 'indices')      # the stock snapshot is still backing off
            clock[0] = at('09:23:00')
            session.call.return_value = stocks(equity('09:22:10'))
            self.assertEqual(recorder.tick()['feed'], 'equity')
            self.assertEqual(session.call.call_count, 6)

    def test_window_freezes_after_the_source_moves_on_and_keeps_the_last_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:15:20')]
            recorder, session = self.setup_recorder(directory, clock, feeds=['indices'])
            for minute in range(15, 20):
                clock[0] = at('09:%02d:20' % minute)
                session.call.return_value = index_doc('09:%02d' % minute, last=100 + minute - 15)
                recorder.tick()
            clock[0] = at('09:20:10')            # past the window on the clock, but the source still shows 09:19
            session.call.return_value = index_doc('09:19', last=104.5)
            recorder.tick()
            self.assertEqual(load_candles(directory, '2026-11-17'), [])
            clock[0] = at('09:20:40')
            session.call.return_value = index_doc('09:20', last=106)
            recorder.tick()
            row, = load_candles(directory, '2026-11-17', usable_only=True)
            self.assertEqual((row['close'], row['complete'], row['known_at']), (104.5, True, at('09:20:40').isoformat()))

    def test_inflight_lease_prevents_another_process_overlapping_a_slow_call(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:20:00')]
            recorder, session = self.setup_recorder(directory, clock)
            other, other_session = self.setup_recorder(directory, clock)

            def slow_call(*args):
                clock[0] = at('09:20:30')
                self.assertEqual(other.tick()['status'], 'WAITING')
                return stocks(equity('09:20:10'))

            session.call.side_effect = slow_call
            self.assertEqual(recorder.tick()['status'], 'RECORDED')
            other_session.call.assert_not_called()

    def test_no_calls_on_holidays_outside_hours_and_uncovered_year(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:00:00')]
            recorder, session = self.setup_recorder(directory, clock)
            self.assertEqual(recorder.tick()['status'], 'OUTSIDE_SESSION')
            clock[0] = timestamp('2026-11-24T10:00:00+05:30')
            self.assertEqual(recorder.tick()['status'], 'OUTSIDE_SESSION')
            clock[0] = at('15:30:00')
            self.assertEqual(recorder.tick()['status'], 'OUTSIDE_SESSION')
            clock[0] = timestamp('2027-01-04T10:00:00+05:30')
            with self.assertRaises(ValueError):
                recorder.tick()
            session.call.assert_not_called()

    def test_completed_candles_are_frozen_and_cutoff_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:15:20')]
            recorder, session = self.setup_recorder(directory, clock)
            # Explicit feeds are useful for a deterministic standalone probe;
            # production ticks alternate them automatically.
            for minute in range(15, 20):
                clock[0] = at('09:%02d:20' % minute)
                session.call.return_value = stocks(equity('09:%02d:10' % minute, volume=100 * (minute - 14)))
                recorder.tick(feed='equity')
            clock[0] = at('09:20:20')
            session.call.return_value = stocks(equity('09:20:10', volume=600))
            recorder.tick(feed='equity')
            rows = load_candles(directory, '2026-11-17', complete_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['volume'], 500)
            self.assertEqual(load_candles(directory, '2026-11-17', as_of=at('09:20:00').isoformat()), [])
            saved = rows[0]
            clock[0] = at('09:21:20')
            session.call.return_value = stocks(equity('09:21:10', volume=700, highPrice=999))
            recorder.tick(feed='equity')
            self.assertEqual(load_candles(directory, '2026-11-17', complete_only=True)[0], saved)

    def test_missed_poll_and_absent_symbol_create_incomplete_candles(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [at('09:15:20')]
            recorder, session = self.setup_recorder(directory, clock)
            session.call.return_value = stocks(equity('09:15:10'))
            recorder.tick()
            clock[0] = at('09:22:20')
            session.call.return_value = stocks()
            recorder.tick(feed='equity')
            self.assertEqual(load_candles(directory, '2026-11-17'), [])      # still inside the grace
            clock[0] = at('09:23:20')
            recorder.tick(feed='equity')
            rows = load_candles(directory, '2026-11-17')
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(not row['complete'] and not row['usable'] for row in rows))
            self.assertIsNone(next(r for r in rows if r['symbol'].startswith('INDEX:'))['close'])

    def test_dry_run_has_no_files_and_no_telegram_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'not-created'
            recorder = SnapshotRecorder(dict(enabled=True, state_dir=str(root), symbols=['DEMO.NS'],
                                             indices=['NIFTY 50']), dry_run=True, session=mock.Mock(),
                                        now_fn=lambda: at('09:20:30'), sleep=lambda _: None,
                                        http_get=lambda url: spark(['DEMO.NS'], [100, 101]))
            self.addCleanup(recorder.close)
            recorder.session.call.return_value = index_doc('09:20')
            self.assertEqual(recorder.tick()['status'], 'RECORDED')
            self.assertEqual(recorder.stocks_tick()['new_candles'], 1)
            self.assertFalse(root.exists())


class StockCloses(unittest.TestCase):
    def setup_recorder(self, directory, clock, docs, symbols=('DEMO.NS',), **overrides):
        calls, pauses = [], []

        def get(url):
            calls.append(url)
            if isinstance(docs[0], Exception):
                raise docs[0]
            return docs[0]
        cfg = dict(enabled=True, state_dir=directory, symbols=list(symbols), indices=['NIFTY 50'])
        cfg.update(overrides)
        recorder = SnapshotRecorder(cfg, session=mock.Mock(), now_fn=lambda: clock[0], http_get=get, sleep=pauses.append)
        self.addCleanup(recorder.close)
        return recorder, calls, pauses

    def test_parse_keeps_only_completed_session_windows_and_never_fills_a_gap(self):
        doc = spark(['DEMO.NS', 'OTHER.NS', 'SLOW.NS'], [99, 100, None, 102, 103], start='09:10')
        doc['DEMO.NS']['timestamp'].append(int(at('09:30:15').timestamp()))     # the live tick Yahoo appends
        doc['DEMO.NS']['close'].append(103.5)
        doc['OTHER.NS']['symbol'] = 'ELSE.NS'
        doc['SLOW.NS']['dataGranularity'] = 60
        bars, rejected = parse_spark(doc, ['DEMO.NS', 'OTHER.NS', 'SLOW.NS', 'GONE.NS'], at('09:30:19').isoformat(),
                                     DEFAULT_CALENDAR)
        # 09:10 is before the open, 09:20 has no close, 09:25 ended under 20 seconds ago, 09:30 is in progress.
        self.assertEqual(bars, {'DEMO.NS': [(at('09:15:00'), 100.0)]})
        self.assertEqual(parse_spark(doc, ['DEMO.NS'], at('09:30:20').isoformat(), DEFAULT_CALENDAR)[0]['DEMO.NS'],
                         [(at('09:15:00'), 100.0), (at('09:25:00'), 102.0)])
        self.assertEqual([r['symbol'] for r in rejected], ['OTHER.NS', 'SLOW.NS', 'GONE.NS'])
        self.assertEqual(parse_spark(doc, ['DEMO.NS'], '2026-11-18T09:40:00+05:30', DEFAULT_CALENDAR)[0]['DEMO.NS'], [])
        with self.assertRaises(ValueError):
            parse_spark([], ['DEMO.NS'], at('09:30:20').isoformat(), DEFAULT_CALENDAR)

    def test_one_refresh_per_completed_window_and_first_close_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            clock, docs = [at('09:20:10')], [spark(['DEMO.NS'], [100, 101])]
            recorder, calls, _ = self.setup_recorder(directory, clock, docs)
            self.assertEqual(recorder.stocks_tick()['status'], 'WAITING')     # the 09:15 window has not settled
            clock[0] = at('09:20:30')
            first = recorder.stocks_tick()
            self.assertEqual((first['status'], first['window'], first['covered'], first['new_candles']),
                             ('RECORDED', at('09:15:00').isoformat(), 1, 1))
            clock[0] = at('09:21:00')
            self.assertEqual(recorder.stocks_tick()['status'], 'WAITING')
            self.assertEqual(len(calls), 1)
            clock[0], docs[0] = at('09:25:30'), spark(['DEMO.NS'], [100.5, 101, 102])
            second = recorder.stocks_tick()
            self.assertEqual((second['new_candles'], second['revised_closes']), (1, 1))
            rows = load_stock_closes(directory, '2026-11-17')
            self.assertEqual([(r['time'], r['close'], r['known_at']) for r in rows],
                             [(at('09:15:00').isoformat(), 100.0, at('09:20:30').isoformat()),
                              (at('09:20:00').isoformat(), 101.0, at('09:25:30').isoformat())])
            self.assertEqual(len(load_stock_closes(directory, '2026-11-17', as_of=at('09:21:00').isoformat())), 1)
            for row in rows:
                self.assertEqual((row['kind'], row['close_only'], row['exchange_candle'], row['execution_enabled']),
                                 (YAHOO_KIND, True, False, False))
                self.assertIn('Yahoo', row['source'])
            self.assertEqual(json.loads((Path(directory) / 'status.json').read_text())['stocks']['covered'], 1)

    def test_calls_are_sequential_batches_of_twenty_with_a_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            symbols = ['DEMO%02d.NS' % i for i in range(45)]
            recorder, calls, pauses = self.setup_recorder(directory, [at('09:20:30')], [spark(symbols, [100, 101])], symbols)
            result = recorder.stocks_tick()
            self.assertEqual((result['calls'], result['covered'], result['missing']), (3, 45, []))
            self.assertEqual(pauses, [YAHOO_PAUSE] * 2)
            self.assertEqual([url.count('.NS') for url in calls], [20, 20, 5])

    def test_failure_stops_the_refresh_backs_off_and_a_later_refresh_fills_in(self):
        with tempfile.TemporaryDirectory() as directory:
            symbols = ['DEMO%02d.NS' % i for i in range(45)]
            clock, docs = [at('09:20:30')], [urllib.error.HTTPError('u', 429, 'Too Many Requests', None, None)]
            recorder, calls, _ = self.setup_recorder(directory, clock, docs, symbols)
            failed = recorder.stocks_tick()
            self.assertEqual((failed['status'], failed['error'], failed['calls'], failed['next_poll_at']),
                             ('UNAVAILABLE', 'HTTP 429', 1, at('09:25:30').isoformat()))
            clock[0] = at('09:25:20')
            self.assertEqual(recorder.stocks_tick()['status'], 'WAITING')       # still backing off
            clock[0] = at('09:25:30')
            again = recorder.stocks_tick()
            self.assertEqual((again['calls'], again['next_poll_at']), (1, at('09:35:30').isoformat()))   # doubled
            clock[0], docs[0] = at('09:35:30'), spark(symbols, [100, 101, 102, 103, 104])
            healed = recorder.stocks_tick()
            self.assertEqual((healed['status'], healed['new_candles'], healed['next_poll_at']), ('RECORDED', 45 * 4, None))
            rows = load_stock_closes(directory, '2026-11-17')
            self.assertTrue(all(r['known_at'] == at('09:35:30').isoformat() for r in rows))   # late, and says so

    def test_no_calls_when_off_outside_the_session_or_on_a_holiday(self):
        with tempfile.TemporaryDirectory() as directory:
            clock, docs = [at('09:14:00')], [spark(['DEMO.NS'], [100] * 75)]
            recorder, calls, _ = self.setup_recorder(directory, clock, docs)
            for when in (at('09:14:00'), at('15:35:00'), timestamp('2026-11-24T10:00:00+05:30')):
                clock[0] = when
                self.assertEqual(recorder.stocks_tick()['status'], 'OUTSIDE_SESSION')
            off, off_calls, _ = self.setup_recorder(directory + '/off', [at('10:00:00')], docs, stock_source='off')
            self.assertEqual(off.stocks_tick()['status'], 'DISABLED')
            self.assertEqual(calls + off_calls, [])
            clock[0] = at('15:30:30')                                   # the last window is read after the close
            self.assertEqual(recorder.stocks_tick()['window'], at('15:25:00').isoformat())


class Integration(unittest.TestCase):
    def test_new_settings_merge_without_changing_older_configs(self):
        self.assertFalse(picks.load_config(None)['recorder']['enabled'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            for changes in (dict(poll_seconds=29), dict(snapshot_limit=501), dict(enabled=1),
                            dict(indices=[]), dict(symbols=['DEMO.BO']), dict(unknown=True),
                            dict(feeds=[]), dict(feeds=['stocks']), dict(feeds=['indices', 'indices']),
                            dict(stock_source='broker')):
                path.write_text(json.dumps(dict(recorder=changes)))
                with self.assertRaises(ValueError):
                    picks.load_config(path)
        self.assertTrue(settings(dict(enabled=True))['enabled'])
        example = picks.load_config('config/picks.example.json')['recorder']
        self.assertEqual((example['enabled'], example['feeds'], example['stock_source']), (False, ['indices'], 'yahoo'))
        self.assertEqual(example, settings())

    def test_record_once_bypasses_secrets_and_telegram(self):
        recorder = mock.Mock()
        recorder.tick.return_value = dict(status='RECORDED')
        with mock.patch.object(picks, 'make_recorder', return_value=recorder), \
                mock.patch.object(picks, 'read_env_file', side_effect=AssertionError('read secrets')), \
                mock.patch.object(picks, 'send_telegram', side_effect=AssertionError('sent message')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(picks.main(['--record-once', '--dry-run', '--record-feed', 'indices']), 0)
        recorder.tick.assert_called_once_with(feed='indices')
        recorder.close.assert_called_once()
        recorder.stocks_tick.return_value = dict(status='PARTIAL')
        with mock.patch.object(picks, 'make_recorder', return_value=recorder), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(picks.main(['--record-once', '--dry-run', '--record-feed', 'stocks']), 1)
        recorder.stocks_tick.assert_called_once_with(force=True)

    def test_scheduler_runs_recorder_in_existing_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = picks.load_config(None)
            cfg['state_dir'] = directory
            recorder = mock.Mock()
            recorder.tick.return_value = dict(status='WAITING')
            recorder.stocks_tick.side_effect = OSError('synthetic')       # one source failing never stops the loop
            picks.serve([], cfg, DEFAULT_CALENDAR, now_fn=lambda: at('12:00:00'), sleep=lambda _: None,
                        main_fn=lambda _: None, iterations=3, log=lambda _: None, recorder=recorder)
            self.assertEqual((recorder.tick.call_count, recorder.stocks_tick.call_count), (3, 3))

    def test_serve_dry_run_stays_dry_in_recursive_jobs(self):
        with mock.patch.object(picks, 'serve') as serve, \
                mock.patch.object(picks, 'read_env_file', return_value={}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(picks.main(['--serve', '--dry-run']), 0)
        self.assertIn('--dry-run', serve.call_args.args[0])

    def test_polling_transport_never_retries_immediately(self):
        session = MCPSession(MARKET, attempts=1, recover_expired=False)
        with mock.patch('india.nse_mcp.urllib.request.urlopen', side_effect=urllib.error.URLError('offline')) as request, \
                mock.patch('india.nse_mcp.time.sleep') as sleep:
            with self.assertRaises(ValueError):
                session.call('cm_get_equity_stocks', dict(limit=500, symbolFilter=''))
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
