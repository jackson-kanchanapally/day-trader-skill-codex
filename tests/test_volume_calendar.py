"""Synthetic time-of-day history and sourced-session schema regressions."""
import datetime as dt
from dataclasses import replace
from pathlib import Path
import unittest
import warnings

from india.analysis import indicators
from india.data import CSVProvider, timestamp
from india.market import Calendar, CalendarCoverageWarning, session
from india.volume import relative_volume, volume_profile
from workflow.common import config
from workflow.strategy import entry

NOW = timestamp('2026-10-19T09:35:00+05:30')


class RelativeVolume(unittest.TestCase):
    def setUp(self):
        self.today = CSVProvider(Path(__file__).parent/'fixtures/india/synthetic.csv').history('DEMO.NS', '5m')[:5]
        self.prior = [replace(b, time=b.time.replace(day=day), volume=volume)
                      for day in (12, 13, 14, 15, 16)
                      for b, volume in zip(self.today, (100, 200, 300, 500, 400))]
        self.bars = self.prior+self.today[:4]

    def test_same_time_opening_and_cumulative_are_distinct(self):
        profile = volume_profile(self.bars, as_of=NOW)
        self.assertEqual(profile['same_time']['value'], 5)
        self.assertEqual(profile['opening_5m']['value'], 10)
        self.assertEqual(profile['opening_15m']['value'], 5)
        self.assertEqual(profile['cumulative']['value'], 5)
        self.assertEqual(profile['same_time']['samples'], 5)
        self.assertEqual(indicators(self.bars)['volume_spike'], 5)

    def test_future_and_same_session_candles_cannot_leak(self):
        future = [replace(b, time=b.time.replace(day=21), volume=999999999) for b in self.today]
        self.assertEqual(relative_volume(self.bars, as_of=NOW), relative_volume(self.bars+self.today[4:]+future, as_of=NOW))
        changed = [replace(b, volume=999999) if b.time.date() == NOW.date() and b.time < self.today[3].time else b for b in self.bars]
        self.assertEqual(relative_volume(changed)['value'], 5)

    def test_configurable_lookback_and_missing_sessions(self):
        r = relative_volume(self.bars, lookback=3, min_samples=3)
        self.assertEqual(r['samples'], 3)
        missing = [b for b in self.bars if b.time.day != 16]
        r = relative_volume(missing, lookback=5, min_samples=5)
        self.assertIsNone(r['value'])
        self.assertEqual(r['samples'], 4)
        self.assertIn('2026-10-16', r['excluded_sessions'])
        self.assertEqual(r['reason'], 'INSUFFICIENT_SAMPLES')
        with self.assertRaises(ValueError):
            relative_volume(self.bars, lookback=3, min_samples=4)

    def test_incomplete_windows_zero_volume_and_minimum_samples(self):
        missing = [b for b in self.bars if b.time.day != 16 or b.time.minute != 20]
        self.assertEqual(relative_volume(missing, mode='opening_15m')['samples'], 4)
        self.assertEqual(relative_volume(self.today[:1], mode='opening_15m')['reason'], 'INCOMPLETE_CURRENT_WINDOW')
        zero_history = [replace(b, volume=0) for b in self.prior]+self.today[:4]
        self.assertEqual(relative_volume(zero_history)['reason'], 'ZERO_BASELINE')
        self.assertIsNone(relative_volume(zero_history)['value'])
        zero_current = self.prior+[replace(b, volume=0) for b in self.today[:4]]
        self.assertEqual(relative_volume(zero_current)['value'], 0)
        self.assertIsNone(indicators(self.today[:4])['volume_spike'])

    def test_one_minute_windows(self):
        rows = [replace(self.today[0], time=NOW.replace(day=day, hour=9, minute=15)+dt.timedelta(minutes=i), volume=100 if day == 19 else 50)
                for day in (12, 13, 14, 15, 16, 19) for i in range(15)]
        for mode in ('same_time', 'opening_5m', 'opening_15m', 'cumulative'):
            self.assertEqual(relative_volume(rows, minutes=1, mode=mode)['value'], 2)

    def test_entry_filter_requires_prior_same_time_samples(self):
        cfg = config(budget=100000)
        self.assertEqual(entry(self.bars, 'DEMO.NS', NOW, cfg, min_volume_x=4)['status'], 'triggered')
        for bars, threshold in ((self.bars, 6), (self.today[:4], 1)):
            doc = entry(bars, 'DEMO.NS', NOW, cfg, min_volume_x=threshold)
            self.assertFalse(doc['eligible'])
            self.assertFalse(doc.get('trigger'))
            self.assertTrue(doc['volume_rejections'])

    def test_rejected_first_breakout_can_be_followed_by_qualified_breakout(self):
        rows = self.prior+[replace(self.today[3], volume=100) if i == 3 else b for i, b in enumerate(self.today)]
        doc = entry(rows, 'DEMO.NS', NOW+dt.timedelta(minutes=5), config(budget=100000), min_volume_x=2)
        self.assertEqual(doc['signal_time'][11:16], '09:35')
        self.assertEqual(len(doc['volume_rejections']), 1)


class ExceptionalCalendar(unittest.TestCase):
    def calendar(self, **fields):
        record = dict(source='synthetic schema test; not a published market schedule', verified_on='2026-10-01', holidays=['2026-10-18'])
        record.update(fields)
        return Calendar({'NSE': {'2026': record}})

    def test_weekend_muhurat_window_overrides_holiday_without_invented_default(self):
        cal = self.calendar(sessions={'2026-10-18': dict(open='18:00', close='19:00', kind='muhurat', source='synthetic session only')})
        self.assertTrue(cal.trading_day(dt.date(2026, 10, 18)))
        for clock, expected in (('17:59', False), ('18:00', True), ('18:59', True), ('19:00', False)):
            self.assertEqual(session(timestamp('2026-10-18T'+clock+':00+05:30'), cal), expected)
        with self.assertRaisesRegex(ValueError, 'Exceptional session'):
            cal.require_strategy_session(dt.date(2026, 10, 18))
        self.assertFalse(self.calendar().trading_day(dt.date(2026, 10, 18)))

    def test_missing_open_has_actionable_validation_error(self):
        with self.assertRaisesRegex(ValueError, 'open and close'):
            self.calendar(sessions={'2026-10-18': {'close': '19:00', 'source': 'synthetic'}})

    def test_shortened_session_cannot_use_full_day_orb_assumptions(self):
        cal = self.calendar(sessions={'2026-10-19': dict(open='09:15', close='12:00', source='synthetic session only')})
        with self.assertRaisesRegex(ValueError, 'Exceptional session'):
            entry([], 'DEMO.NS', NOW, config(budget=100000), calendar=cal)

    def test_coverage_expiry_warns_and_fails_closed(self):
        cal = self.calendar(valid_until='2026-10-31')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            self.assertTrue(cal.trading_day(NOW.date()))
            self.assertTrue(any(issubclass(w.category, CalendarCoverageWarning) for w in caught))
        self.assertIn('expires', cal.coverage_warnings(NOW.date())[0])
        with self.assertWarns(CalendarCoverageWarning), self.assertRaisesRegex(ValueError, 'expired'):
            cal.trading_day(dt.date(2026, 11, 2))
        with self.assertWarns(CalendarCoverageWarning), self.assertRaisesRegex(ValueError, 'coverage'):
            cal.trading_day(dt.date(2027, 1, 4))

    def test_invalid_unattributed_or_wrong_year_windows_fail(self):
        for sessions in ({'2026-10-18': {'open': '18:00', 'close': '19:00'}},
                         {'2027-10-18': {'open': '18:00', 'close': '19:00', 'source': 'test'}},
                         {'2026-10-18': {'open': '19:00', 'close': '18:00', 'source': 'test'}}):
            with self.assertRaises(ValueError):
                self.calendar(sessions=sessions)
