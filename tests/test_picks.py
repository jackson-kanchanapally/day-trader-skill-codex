import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from india import picks as P
from india import review as R
from india.data import IST, Bar

ROOT = Path(__file__).resolve().parents[1]


def daily(closes, volumes=None, start=dt.date(2026, 6, 1), spread=0.01):
    """Weekday daily bars ending on the last date; open = previous close."""
    bars, day, prev = [], start, closes[0]
    for i, c in enumerate(closes):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        o = prev
        bars.append(Bar(dt.datetime.combine(day, dt.time(9, 15), IST), o, max(o, c) * (1 + spread),
                        min(o, c) * (1 - spread), c, (volumes or [1_000_000] * len(closes))[i]))
        prev, day = c, day + dt.timedelta(days=1)
    return bars


def uptrend(n=120, last_volume=7_500_000):
    """Rising, wave-shaped closes (RSI in the 60s) with a strong, high-volume last session."""
    closes = [500 * (1.004 ** i) + 25 * math.sin(i / 1.5) for i in range(n)]
    closes[-1] = closes[-2] * 1.015
    return daily(closes, [3_000_000] * (n - 1) + [last_volume])


def downtrend(n=120):
    closes = [800 * (0.996 ** i) + 15 * math.sin(i / 1.2) for i in range(n)]
    closes[-1] = closes[-2] * 0.985
    return daily(closes, [3_000_000] * (n - 1) + [7_500_000])


CFG = P.load_config(None)


class Indicators(unittest.TestCase):
    def test_ema_seed_and_recursion(self):
        e = P.ema_series([1, 2, 3, 4, 5], 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2.0)
        self.assertAlmostEqual(e[3], 3.0)
        self.assertAlmostEqual(e[4], 4.0)

    def test_rsi_bounds(self):
        self.assertEqual(P.rsi_series(list(range(1, 30)))[-1], 100.0)
        self.assertLess(P.rsi_series(list(range(30, 1, -1)))[-1], 1)

    def test_macd_signal_aligned(self):
        line, sig, hist = P.macd_series([float(x) for x in range(1, 61)])
        self.assertIsNone(sig[32])
        self.assertIsNotNone(sig[33])
        self.assertAlmostEqual(hist[-1], line[-1] - sig[-1])

    def test_atr_constant_range(self):
        bars = [Bar(dt.datetime(2026, 1, 1, tzinfo=IST) + dt.timedelta(days=i), 100, 102, 98, 100, 1) for i in range(20)]
        self.assertAlmostEqual(P.atr_series(bars)[-1], 4.0)

    def test_features_need_history(self):
        with self.assertRaises(ValueError):
            P.features(uptrend(40))


class Scoring(unittest.TestCase):
    def test_uptrend_with_volume_is_a_buy(self):
        f = P.features(uptrend())
        cand, reason = P.evaluate(f, CFG, 'bullish')
        self.assertIsNone(reason)
        self.assertEqual(cand['side'], 'BUY')
        self.assertGreaterEqual(cand['score'], CFG['min_score'])
        self.assertFalse(cand['counter_trend'])

    def test_counter_trend_penalty(self):
        f = P.features(uptrend())
        with_trend = P.evaluate(f, CFG, 'bullish')[0]['score']
        against = P.evaluate(f, CFG, 'bearish')[0]
        self.assertAlmostEqual(with_trend - against['score'], 10)
        self.assertTrue(against['counter_trend'])

    def test_quiet_volume_rejected(self):
        f = P.features(uptrend(last_volume=2_900_000))
        self.assertEqual(P.evaluate(f, CFG)[1], 'volume not expanding')

    def test_downtrend_short_respects_allow_short(self):
        bars = downtrend()
        f = P.features(bars)
        cand, reason = P.evaluate(f, CFG)
        self.assertIsNone(reason)
        self.assertEqual(cand['side'], 'SELL')
        self.assertEqual(P.evaluate(f, dict(CFG, allow_short=False))[1], 'trend, MACD and RSI not aligned')


class Plans(unittest.TestCase):
    def plan(self, bars, **cfg):
        f = P.features(bars)
        return P.build_plan('DEMO.NS', f, P.evaluate(f, dict(CFG, **cfg))[0], dict(CFG, **cfg)), f

    def test_buy_plan_levels_and_risk(self):
        p, f = self.plan(uptrend())
        self.assertGreater(p['entry'], f['high'])
        self.assertLess(p['stop'], p['entry'])
        self.assertAlmostEqual(p['target1'] - p['entry'], p['entry'] - p['stop'], delta=p['tick'])
        self.assertAlmostEqual(p['target2'] - p['entry'], 2 * (p['entry'] - p['stop']), delta=p['tick'])
        self.assertGreater(p['no_chase'], p['entry'])
        self.assertLessEqual(p['max_loss'], CFG['risk_per_trade'])
        self.assertLessEqual(p['exposure'], CFG['capital'])
        for key in ('entry', 'stop', 'target1', 'target2', 'no_chase'):
            self.assertAlmostEqual(round(p[key] / p['tick']) * p['tick'], p[key], places=6)

    def test_sell_plan_mirrors(self):
        bars = downtrend()
        p, f = self.plan(bars)
        self.assertLess(p['entry'], f['low'])
        self.assertGreater(p['stop'], p['entry'])
        self.assertLess(p['target2'], p['target1'])
        self.assertTrue(any('intraday-only' in x for x in p['flags']))

    def test_tiny_budget_yields_no_plan(self):
        p, _ = self.plan(uptrend(), risk_per_trade=1)
        self.assertIsNone(p)

    def test_tick_bands_are_multiples_of_005(self):
        for price in (40, 800, 2500, 7000, 15000, 30000):
            t = P.tick_for(price)
            self.assertAlmostEqual(round(t / 0.05) * 0.05, t)
        self.assertEqual(P.tick_for(800, 0.01), 0.01)

    def test_select_respects_score_sector_and_top_n(self):
        base = {'features': {'avg_turnover': 1e9}}
        rows = [dict(base, symbol='A.NS', score=90), dict(base, symbol='B.NS', score=85),
                dict(base, symbol='C.NS', score=80), dict(base, symbol='D.NS', score=70),
                dict(base, symbol='E.NS', score=50)]
        sectors = {'A.NS': 'IT', 'B.NS': 'IT', 'C.NS': 'Bank', 'D.NS': 'Auto', 'E.NS': 'Pharma'}
        got = [p['symbol'] for p in P.select(rows, CFG, sectors)]
        self.assertEqual(got, ['A.NS', 'C.NS', 'D.NS'])


class Messages(unittest.TestCase):
    def ctx(self, **kw):
        return dict(dict(trade_date=dt.date(2026, 11, 13), data_date='2026-11-12', bias='bullish', market=None,
                         scanned=200, universe=200, qualified=1, warnings=[]), **kw)

    def test_inr_grouping(self):
        self.assertEqual(P.inr(1234567.891), '₹12,34,567.89')
        self.assertEqual(P.inr(999), '₹999.00')
        self.assertEqual(P.inr(-1500, 0), '-₹1,500')

    def test_message_has_entry_exit_risk_and_escapes(self):
        f = P.features(uptrend())
        p = P.build_plan('M&M.NS', f, P.evaluate(f, CFG)[0], CFG)
        p['sector'] = 'Automobile & Auto <Components>'
        text = '\n'.join(P.format_messages([p], self.ctx(), CFG))
        for part in ('Entry plan', 'Exit plan', 'Risk', 'Stop-loss', 'Target 1', 'Target 2', 'Max loss', 'M&amp;M', '&lt;Components&gt;'):
            self.assertIn(part, text)
        self.assertIn('Only 1 setup(s)', text)
        self.assertNotIn('<Components>', text)

    def test_no_picks_says_stay_flat(self):
        text = P.format_messages([], self.ctx(qualified=0), CFG)[0]
        self.assertIn('No setup met the bar', text)

    def test_long_output_split_under_limit(self):
        f = P.features(uptrend())
        p = P.build_plan('DEMO.NS', f, P.evaluate(f, CFG)[0], CFG)
        p['flags'] = ['x' * 500] * 4
        msgs = P.format_messages([dict(p) for _ in range(3)], self.ctx(), dict(CFG, top_n=3))
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))


class Job(unittest.TestCase):
    def test_run_offline_with_injected_fetch(self):
        good = uptrend()
        # Shift so the last bar is the session before the as-of date.
        last = dt.date(2026, 11, 12)
        shift = last - good[-1].time.date()
        good = [Bar(b.time + shift, b.open, b.high, b.low, b.close, b.volume) for b in good]
        stale = good[:-3]
        data = {'AAA.NS': good, 'BBB.NS': stale, P.MARKET_INDEX: good}
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(CFG, symbols=['AAA.NS', 'BBB.NS'], state_dir=d, workers=1)
            now = dt.datetime(2026, 11, 13, 8, 45, tzinfo=IST)
            picks, ctx = P.run(cfg, now, fetch=lambda s: data[s])
        self.assertEqual([p['symbol'] for p in picks], ['AAA.NS'])
        self.assertIn('stale', ctx['failed']['BBB.NS'])
        self.assertEqual(ctx['trade_date'], dt.date(2026, 11, 13))
        self.assertEqual(ctx['bias'], 'bullish')
        self.assertFalse(ctx['execution_enabled'])

    def test_trade_date_after_close_is_next_session(self):
        from india.market import DEFAULT_CALENDAR as cal
        self.assertEqual(P.trade_date(dt.datetime(2026, 11, 12, 18, 0, tzinfo=IST), cal), dt.date(2026, 11, 13))
        self.assertEqual(P.trade_date(dt.datetime(2026, 11, 14, 9, 0, tzinfo=IST), cal), dt.date(2026, 11, 16))

    def test_holiday_skips_without_network(self):
        with mock.patch.object(P, 'run') as run, mock.patch('builtins.print'):
            code = P.main(['--dry-run', '--env-file', '/dev/null', '--as-of', '2026-11-24T08:45:00+05:30'])
        self.assertEqual(code, 0)
        run.assert_not_called()

    def test_mid_session_wake_skips(self):
        with mock.patch.object(P, 'run') as run, mock.patch('builtins.print'):
            code = P.main(['--dry-run', '--env-file', '/dev/null', '--as-of', '2026-11-13T12:00:00+05:30'])
        self.assertEqual(code, 0)
        run.assert_not_called()

    def test_send_requires_secrets(self):
        with mock.patch.dict('os.environ', {}, clear=True), mock.patch('builtins.print') as out:
            code = P.main(['--env-file', '/dev/null', '--as-of', '2026-11-13T08:45:00+05:30'])
        self.assertEqual(code, 1)
        self.assertIn('TELEGRAM_BOT_TOKEN', out.call_args[0][0])

    def test_sends_once_per_session(self):
        f = P.features(uptrend())
        plan = P.build_plan('DEMO.NS', f, P.evaluate(f, CFG)[0], CFG)
        ctx = dict(trade_date=dt.date(2026, 11, 13), data_date='2026-11-12', bias='bullish', market=None,
                   scanned=1, universe=1, qualified=1, warnings=[], failed={}, rejects={})
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / 'picks.json'
            cfg_path.write_text(json.dumps({'state_dir': d}))
            args = ['--config', str(cfg_path), '--env-file', '/dev/null', '--as-of', '2026-11-13T08:45:00+05:30']
            env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
            with mock.patch.dict('os.environ', env), mock.patch.object(P, 'run', return_value=([plan], ctx)), \
                    mock.patch.object(P, 'send_telegram') as send, mock.patch('builtins.print'), \
                    mock.patch('sys.stderr'):
                self.assertEqual(P.main(args), 0)
                self.assertEqual(P.main(args), 0)
            self.assertEqual(send.call_count, 1)
            self.assertTrue((Path(d) / 'sent-2026-11-13').exists())
            self.assertTrue((Path(d) / 'picks-2026-11-13.json').exists())

    def test_failure_is_reported_to_telegram(self):
        env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / 'picks.json'
            cfg_path.write_text(json.dumps({'state_dir': d}))
            with mock.patch.dict('os.environ', env), mock.patch.object(P, 'run', side_effect=ValueError('boom')), \
                    mock.patch.object(P, 'send_telegram') as send, mock.patch('builtins.print'):
                code = P.main(['--config', str(cfg_path), '--env-file', '/dev/null', '--as-of', '2026-11-13T08:45:00+05:30'])
        self.assertEqual(code, 1)
        self.assertIn('boom', send.call_args[0][2])

    def test_example_config_loads(self):
        cfg = P.load_config(ROOT / 'config/picks.example.json')
        self.assertEqual(cfg['top_n'], 3)

    def test_unknown_config_key_rejected(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            json.dump({'topn': 3}, fh)
        with self.assertRaises(ValueError):
            P.load_config(fh.name)
        Path(fh.name).unlink()

    def test_launchd_plist_weekdays(self):
        xml = P.launchd_plist('x', '/usr/bin/python3', '/tmp/a&b', '/tmp/c.json', 8, 45)
        self.assertEqual(xml.count('<key>Weekday</key>'), 5)
        self.assertIn('/tmp/a&amp;b', xml)



def plan(side='BUY', **kw):
    p = dict(symbol='DEMO.NS', side=side, score=70, qty=10, max_loss=100.0,
             entry_after='09:30', entry_deadline='11:30', square_off='15:10')
    p.update(dict(entry=100.0, stop=99.0, target1=101.0, target2=102.0, no_chase=100.5) if side == 'BUY' else
             dict(entry=100.0, stop=101.0, target1=99.0, target2=98.0, no_chase=99.5))
    p.update(kw)
    return p


def session(rows, day=dt.date(2026, 11, 13)):
    """rows: list of (HH:MM, o, h, l, c) 5-minute candles."""
    return [(dt.datetime.combine(day, dt.time.fromisoformat(t), IST), o, h, l, c) for t, o, h, l, c in rows]


def flat(start, end, price, day=dt.date(2026, 11, 13)):
    t = dt.datetime.combine(day, dt.time.fromisoformat(start), IST)
    stop = dt.datetime.combine(day, dt.time.fromisoformat(end), IST)
    out = []
    while t < stop:
        out.append((t.strftime('%H:%M'), price, price + .05, price - .05, price))
        t += dt.timedelta(minutes=5)
    return out


AFTER = dt.datetime(2026, 11, 13, 16, 0, tzinfo=IST)


class PaperTrades(unittest.TestCase):
    def test_trigger_t1_t2(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2)]
                       + [('09:30', 100.2, 101.1, 100.1, 101.0), ('09:35', 101, 102.1, 100.9, 102.0)] + flat('09:40', '15:30', 102))
        r = P.simulate(plan(), bars, AFTER)
        self.assertEqual(r['status'], 'CLOSED')
        self.assertEqual(r['entry_price'], 100.2)
        self.assertEqual(r['entry_time'][11:16], '09:30')
        self.assertEqual([(e['qty'], e['price']) for e in r['events'][1:]], [(5, 101.0), (5, 102.0)])
        self.assertEqual(r['exit_reason'], 'Target 1 + Target 2')
        self.assertAlmostEqual(r['gross_pnl'], 5 * .8 + 5 * 1.8)
        self.assertAlmostEqual(r['net_pnl'], r['gross_pnl'] - r['costs'])

    def test_trigger_before_0930_waits(self):
        bars = session([('09:15', 100, 100.4, 99.9, 100.3), ('09:20', 100.3, 100.4, 100.2, 100.3)])
        now = dt.datetime(2026, 11, 13, 9, 26, tzinfo=IST)
        self.assertEqual(P.simulate(plan(), bars, now)['status'], 'WAITING')

    def test_stop_first_when_one_candle_hits_both(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2),
                       ('09:30', 100.2, 101.5, 98.5, 100)] + flat('09:35', '15:30', 100))
        r = P.simulate(plan(), bars, AFTER)
        self.assertEqual(r['exit_reason'], 'Stop-loss')
        self.assertEqual(r['events'][-1]['price'], 99.0)
        self.assertLess(r['net_pnl'], 0)

    def test_gap_through_stop_fills_at_open(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2),
                       ('09:30', 98.0, 98.2, 97.5, 98.0)] + flat('09:35', '15:30', 98))
        self.assertEqual(P.simulate(plan(), bars, AFTER)['events'][-1]['price'], 98.0)

    def test_t1_then_breakeven(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2),
                       ('09:30', 100.2, 101.2, 100.3, 101.0), ('09:35', 101, 101, 100.1, 100.15)] + flat('09:40', '15:30', 100))
        r = P.simulate(plan(), bars, AFTER)
        self.assertEqual(r['exit_reason'], 'Target 1 then breakeven stop')
        self.assertEqual([(e['qty'], e['price']) for e in r['events'][1:]], [(5, 101.0), (5, 100.2)])

    def test_square_off_at_1510_open(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2)] + flat('09:30', '15:10', 100.4)
                       + [('15:10', 100.6, 100.7, 100.5, 100.6)] + flat('15:15', '15:30', 100.6))
        r = P.simulate(plan(), bars, AFTER)
        self.assertEqual(r['exit_reason'], 'Square-off')
        self.assertEqual(r['events'][-1]['price'], 100.6)
        self.assertEqual(r['events'][-1]['time'][11:16], '15:10')

    def test_not_triggered_gap_and_chase(self):
        self.assertEqual(P.simulate(plan(), session(flat('09:15', '15:30', 99.5)), AFTER)['status'], 'NOT_TRIGGERED')
        gap = session([('09:15', 100.8, 101, 100.7, 100.9)] + flat('09:20', '15:30', 100.9))
        self.assertIn('Gapped', P.simulate(plan(), gap, AFTER)['note'])
        chase = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.8, 99.7, 100.7)] + flat('09:30', '15:30', 100.7))
        self.assertIn('no-chase', P.simulate(plan(), chase, AFTER)['note'])
        late = session(flat('09:15', '11:30', 99.8) + [('11:30', 99.8, 100.3, 99.7, 100.2)] + flat('11:35', '15:30', 100.2))
        self.assertEqual(P.simulate(plan(), late, AFTER)['status'], 'NOT_TRIGGERED')

    def test_short_mirrors(self):
        bars = session(flat('09:15', '09:25', 100.2) + [('09:25', 100.2, 100.3, 99.7, 99.8),
                       ('09:30', 99.8, 99.9, 97.9, 98.0)] + flat('09:35', '15:30', 98))
        r = P.simulate(plan('SELL'), bars, AFTER)
        self.assertEqual(r['events'][0]['action'], 'SELL SHORT')
        self.assertEqual(r['exit_reason'], 'Target 1 + Target 2')
        self.assertAlmostEqual(r['gross_pnl'], 5 * .8 + 5 * 1.8)
        stop = session(flat('09:15', '09:25', 100.2) + [('09:25', 100.2, 100.3, 99.7, 99.8),
                       ('09:30', 99.8, 101.2, 99.7, 101.0)] + flat('09:35', '15:30', 101))
        r = P.simulate(plan('SELL'), stop, AFTER)
        self.assertEqual((r['exit_reason'], r['events'][-1]['price']), ('Stop-loss', 101.0))

    def test_open_position_marked_intraday(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2)] + flat('09:30', '10:00', 100.5))
        r = P.simulate(plan(), bars, dt.datetime(2026, 11, 13, 10, 1, tzinfo=IST))
        self.assertEqual((r['status'], r['open_qty'], r['mark']), ('OPEN', 10, 100.5))

    def test_single_share_still_reaches_t2(self):
        bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2),
                       ('09:30', 100.2, 101.1, 100.1, 101.0), ('09:35', 101, 102.1, 100.9, 102.0)] + flat('09:40', '15:30', 102))
        r = P.simulate(plan(qty=1), bars, AFTER)
        self.assertEqual([(e['qty'], e['price']) for e in r['events'][1:]], [(1, 102.0)])

    def test_settle_ledger_and_scorecard(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'picks-2026-11-13.json').write_text(json.dumps(
                {'trade_date': '2026-11-13', 'picks': [plan(), plan(symbol='OTHER.NS')]}))
            bars = session(flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2),
                           ('09:30', 100.2, 100.3, 98.8, 99.0)] + flat('09:35', '15:30', 99))
            fetch = lambda s, day, now: bars if s == 'DEMO.NS' else session(flat('09:15', '15:30', 99.5))
            doc = P.settle(dict(CFG, state_dir=d), dt.date(2026, 11, 13), AFTER, fetch=fetch)
            self.assertTrue(doc['final'])
            book = P.ledger(d)
            self.assertEqual((book['summary']['closed'], book['summary']['not_triggered']), (1, 1))
            self.assertEqual(book['curve'][0]['cumulative'], doc['trades'][0]['net_pnl'])
            text = P.format_scorecard(doc, book)
            self.assertIn('Stop-loss', text)
            self.assertIn('not reached', text)

    def test_settle_keeps_last_good_result_through_an_outage(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(CFG, state_dir=d)
            day = dt.date(2026, 11, 13)
            (Path(d) / 'picks-2026-11-13.json').write_text(json.dumps({'trade_date': '2026-11-13', 'picks': [plan()]}))
            good = lambda s, day, now: session(flat('09:15', '15:30', 99.5))
            def down(s, day, now):
                raise OSError('No address associated with hostname')
            midday = dt.datetime(2026, 11, 13, 13, 0, tzinfo=P.IST)
            before = P.settle(cfg, day, midday, fetch=good)['trades'][0]
            kept = P.settle(cfg, day, midday + dt.timedelta(minutes=10), fetch=down)['trades'][0]
            self.assertEqual((kept['status'], kept['stale']), (before['status'], True))
            self.assertIn('last result kept', kept['note'])
            # A second outage still keeps it, and no scorecard goes out until data returns.
            late = P.settle(cfg, day, AFTER, fetch=down)
            self.assertEqual(late['trades'][0]['status'], before['status'])
            self.assertFalse(late['final'])
            self.assertTrue(P.settle(cfg, day, AFTER, fetch=good)['final'])
            # After GIVE_UP_AFTER the session is finalised with whatever exists.
            evening = dt.datetime(2026, 11, 13, 18, 5, tzinfo=P.IST)
            self.assertTrue(P.settle(cfg, day, evening, fetch=down)['final'])

    def test_settle_without_alert_and_mismatched_record(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(P.settle(dict(CFG, state_dir=d), dt.date(2026, 11, 13), AFTER))
            (Path(d) / 'picks-2026-11-13.json').write_text(json.dumps({'trade_date': '2026-11-16', 'picks': []}))
            with self.assertRaises(ValueError):
                P.settle(dict(CFG, state_dir=d), dt.date(2026, 11, 13), AFTER)

    def test_settle_cli_sends_scorecard_once_and_never_pages_failures(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / 'picks.json'
            cfg_path.write_text(json.dumps({'state_dir': d}))
            (Path(d) / 'picks-2026-11-13.json').write_text(json.dumps({'trade_date': '2026-11-13', 'picks': [plan()]}))
            args = ['--config', str(cfg_path), '--env-file', '/dev/null', '--settle', '--as-of', '2026-11-13T15:45:00+05:30']
            env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
            with mock.patch.dict('os.environ', env), mock.patch.object(P, 'fetch_yahoo_5m', return_value=session(flat('09:15', '15:30', 99.5))), \
                    mock.patch.object(P, 'send_telegram') as send, mock.patch('builtins.print'), mock.patch('sys.stderr'):
                self.assertEqual(P.main(args), 0)
                self.assertEqual(P.main(args), 0)
                self.assertTrue((Path(d) / 'results-2026-11-13.json').exists())
                with mock.patch.object(P, 'run_settle', side_effect=ValueError('feed down')):
                    self.assertEqual(P.main(args), 1)
            self.assertEqual(send.call_count, 1)

    def test_dry_run_does_not_overwrite_sent_record(self):
        f = P.features(uptrend())
        p = P.build_plan('DEMO.NS', f, P.evaluate(f, CFG)[0], CFG)
        ctx = dict(trade_date=dt.date(2026, 11, 13), data_date='2026-11-12', bias='bullish', market=None,
                   scanned=1, universe=1, qualified=1, warnings=[], failed={}, rejects={})
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / 'picks.json'
            cfg_path.write_text(json.dumps({'state_dir': d}))
            with mock.patch.object(P, 'run', return_value=([p], ctx)), mock.patch('builtins.print'), mock.patch('sys.stderr'):
                P.main(['--config', str(cfg_path), '--env-file', '/dev/null', '--dry-run', '--as-of', '2026-11-13T08:45:00+05:30'])
            self.assertFalse((Path(d) / 'picks-2026-11-13.json').exists())


    def test_ledger_lists_upcoming_alerts_and_dashboard_serves_them(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from dashboard.__main__ import handler
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'picks-2026-11-16.json').write_text(json.dumps(
                {'trade_date': '2026-11-16', 'picks': [plan(risk_rating='Medium')]}))
            (Path(d) / 'picks-2026-11-12.json').write_text(json.dumps({'trade_date': '2026-11-13', 'picks': []}))
            book = P.ledger(d)
            self.assertEqual([u['session'] for u in book['upcoming']], ['2026-11-16'])
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(Path(d), Path(d), picks_dir=Path(d)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/api/alert-trades' % server.server_port) as response:
                    served = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(served['upcoming'][0]['picks'][0]['symbol'], 'DEMO.NS')
            self.assertFalse(served['execution_enabled'])


FEAT = {'close': 100.0, 'atr': 2.0, 'ema20': 99.0, 'rsi': 60.0, 'change_pct': 1.0, 'rel_volume': 1.5,
        'atr_pct': 2.0, 'macd_cross': 'bullish'}


def rplan(**kw):
    return plan(features=dict(FEAT, **kw.pop('features', {})), risk_per_share=1.0, **kw)


TRIGGER = flat('09:15', '09:25', 99.8) + [('09:25', 99.8, 100.3, 99.7, 100.2)]   # fills 100.2, entry ends 09:30


def review_of(p, rows, market=None, now=AFTER):
    bars = session(rows)
    trade = P.simulate(p, bars, now)
    return trade, R.diagnose(p, trade, bars, session(market) if market else None)


def results(d, day, trades, final=True):
    (Path(d) / ('results-%s.json' % day)).write_text(json.dumps(
        {'session': day, 'final': final, 'trades': trades}))


def closed(r, traits, tags=()):
    return {'symbol': 'X.NS', 'side': 'BUY', 'status': 'CLOSED', 'r_multiple': r, 'net_pnl': r * 1000,
            'costs': 50.0, 'traits': list(traits), 'diagnosis': {'tags': list(tags)}}


class Review(unittest.TestCase):
    def test_failed_breakout(self):
        trade, d = review_of(rplan(), TRIGGER + [('09:30', 100.2, 100.3, 98.8, 99.0)] + flat('09:35', '15:30', 99))
        self.assertEqual((trade['exit_reason'], d['outcome']), ('Stop-loss', 'loss'))
        self.assertEqual(d['tags'], ['FAILED_BREAKOUT'])
        self.assertIn('breakout failed', d['why'])
        self.assertLess(d['metrics']['mfe_r'], 0.5)

    def test_stop_then_target_means_stop_too_tight(self):
        rows = (TRIGGER + flat('09:30', '10:30', 100.3) + [('10:30', 100.3, 100.4, 98.9, 99.0)]
                + flat('10:35', '13:00', 99.5) + [('13:00', 99.5, 101.2, 99.4, 101.0)] + flat('13:05', '15:30', 101))
        _, d = review_of(rplan(), rows)
        self.assertEqual(d['tags'], ['STOPPED', 'STOP_TOO_TIGHT'])
        self.assertIn('reach T1 at 13:05', d['why'])

    def test_follow_through_with_market_tailwind(self):
        rows = TRIGGER + [('09:30', 100.2, 101.2, 100.1, 101.1), ('09:35', 101.1, 102.2, 101.0, 102.1)] + flat('09:40', '15:30', 102)
        market = flat('09:15', '09:30', 20000) + [('09:30', 20000, 20100, 20000, 20080)] + flat('09:35', '15:30', 20080)
        _, d = review_of(rplan(), rows, market)
        self.assertEqual((d['outcome'], d['tags']), ('win', ['FOLLOW_THROUGH', 'MARKET_TAILWIND']))
        self.assertEqual(d['metrics']['nifty_pct'], 0.4)

    def test_short_side_and_market_against(self):
        p = rplan(side='SELL', features={'close': 100.0, 'ema20': 101.0, 'rsi': 40.0, 'macd_cross': None})
        rows = (flat('09:15', '09:25', 100.2) + [('09:25', 100.2, 100.3, 99.7, 99.8)]
                + flat('09:30', '10:00', 99.9) + [('10:00', 99.9, 101.2, 99.8, 101.1)] + flat('10:05', '15:30', 101))
        market = flat('09:15', '09:55', 20000) + [('09:55', 20000, 20120, 20000, 20100)] + flat('10:00', '15:30', 20100)
        trade, d = review_of(p, rows, market)
        self.assertEqual(trade['exit_reason'], 'Stop-loss')
        self.assertIn('MARKET_AGAINST', d['tags'])
        self.assertIn('NIFTY rose 0.5%', d['why'])
        self.assertEqual(R.traits(p), ['side:SELL', 'stale_macd'])

    def test_not_triggered_gap_against_and_gap_skip(self):
        _, d = review_of(rplan(features={'close': 100.5}), flat('09:15', '15:30', 99.5))
        self.assertEqual(d['tags'], ['NO_BREAKOUT', 'GAP_AGAINST'])
        self.assertEqual(d['outcome'], 'no_trade')
        self.assertEqual(d['metrics']['missed_by_pct'], 0.45)
        trade, d = review_of(rplan(), [('09:15', 101.0, 101.2, 100.8, 101.0)] + flat('09:20', '15:30', 101))
        self.assertEqual((trade['status'], d['tags']), ('SKIPPED', ['GAP_PAST_NO_CHASE']))

    def test_traits_at_alert_time(self):
        p = rplan(features={'rsi': 70.0, 'change_pct': 4.0, 'rel_volume': 3.0, 'atr_pct': 3.5, 'close': 103.0},
                  counter_trend=True, market_bias='neutral', base_score=62)
        self.assertEqual(R.traits(p), ['side:BUY', 'rsi_stretched', 'big_prior_move', 'volume_spike',
                                       'high_volatility', 'extended', 'counter_trend', 'neutral_market',
                                       'marginal_score'])

    def test_lessons_need_enough_trades_and_a_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            results(d, '2026-11-10', [closed(-0.8, ['side:BUY', 'rsi_stretched'], ['STOP_TOO_TIGHT'])] * 7 +
                    [closed(0.3, ['side:BUY'])] * 10)
            book = R.lessons(d)
            row = next(r for r in book['traits'] if r['trait'] == 'rsi_stretched')
            self.assertEqual((row['status'], book['active']), ('collecting', []))
            results(d, '2026-11-11', [closed(-0.8, ['side:BUY', 'rsi_stretched'], ['STOP_TOO_TIGHT'])])
            results(d, '2026-11-12', [closed(5, ['side:BUY', 'rsi_stretched'])], final=False)   # provisional: ignored
            book = R.lessons(d)
            self.assertEqual([r['trait'] for r in book['active']], ['rsi_stretched'])
            self.assertIn('averaged -0.80R over 8', book['active'][0]['evidence'])
            # Every trade is a buy: no comparison group, so 'side:BUY' never becomes a lesson.
            self.assertEqual(next(r for r in book['traits'] if r['trait'] == 'side:BUY')['status'], 'ok')
            self.assertEqual(book['patterns'][0]['tag'], 'STOP_TOO_TIGHT')
            self.assertTrue(book['patterns'][0]['notable'])

    def test_apply_caps_penalty(self):
        learned = {'active': [dict(trait=t, label=t, penalty=10, evidence=t) for t in ('a', 'b', 'c')]}
        p = R.apply({'score': 80, 'traits': ['a', 'b', 'c', 'z']}, learned, max_penalty=20)
        self.assertEqual((p['score'], p['lesson_penalty'], len(p['lessons'])), (60, 20, 3))
        self.assertEqual(R.apply({'score': 80, 'traits': ['z']}, learned)['score'], 80)

    def test_run_applies_lessons_and_alert_says_so(self):
        good = uptrend()
        shift = dt.date(2026, 11, 12) - good[-1].time.date()
        good = [Bar(b.time + shift, b.open, b.high, b.low, b.close, b.volume) for b in good]
        with tempfile.TemporaryDirectory() as d:
            results(d, '2026-11-10', [closed(-1.0, ['side:BUY'])] * 8 + [dict(closed(0.5, ['side:SELL']), side='SELL')] * 8)
            cfg = dict(CFG, symbols=['AAA.NS'], state_dir=d, workers=1, min_score=0)
            now = dt.datetime(2026, 11, 13, 8, 45, tzinfo=IST)
            picks, ctx = P.run(cfg, now, fetch=lambda s: good)
            self.assertEqual(picks[0]['base_score'] - picks[0]['score'], 10)
            self.assertEqual(picks[0]['lessons'][0]['trait'], 'side:BUY')
            text = '\n'.join(P.format_messages(picks, ctx, cfg))
            self.assertIn('📚 Learning from 16 paper trades · 1 lesson(s) active', text)
            self.assertIn('📚 −10: Buy setups averaged -1.00R over 8', text)
            picks, _ = P.run(dict(cfg, learning=False), now, fetch=lambda s: good)
            self.assertEqual(picks[0]['score'], picks[0]['base_score'])

    def test_settle_records_diagnosis_and_scorecard_explains(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'picks-2026-11-13.json').write_text(json.dumps({'trade_date': '2026-11-13', 'picks': [rplan()]}))
            stop = session(TRIGGER + [('09:30', 100.2, 100.3, 98.8, 99.0)] + flat('09:35', '15:30', 99))
            fetch = lambda s, day, now: session(flat('09:15', '15:30', 20000)) if s == P.MARKET_INDEX else stop
            doc = P.settle(dict(CFG, state_dir=d), dt.date(2026, 11, 13), AFTER, fetch=fetch)
            t = doc['trades'][0]
            self.assertEqual((t['traits'], t['diagnosis']['tags']), (['side:BUY'], ['FAILED_BREAKOUT']))
            book = P.ledger(d)
            self.assertEqual(book['lessons']['trades'], 1)
            text = P.format_scorecard(doc, book)
            self.assertIn('Why: Hit the stop 0 min after entry', text)
            self.assertIn('needs 8 before it can become a lesson', text)
            # A broken market feed only drops the NIFTY part of the review.
            def no_market(s, day, now):
                if s == P.MARKET_INDEX:
                    raise ValueError('down')
                return stop
            t = P.settle(dict(CFG, state_dir=d), dt.date(2026, 11, 13), AFTER, fetch=no_market)['trades'][0]
            self.assertEqual(t['status'], 'CLOSED')
            self.assertNotIn('nifty_pct', t['diagnosis']['metrics'])

    def test_learning_config_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'c.json'
            for bad in ({'learning': 'yes'}, {'learn_min_trades': 1}, {'learn_penalty': 2.5}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    P.load_config(str(path))


class Scheduler(unittest.TestCase):
    def drive(self, start, minutes, sent_after_first=True, step=1):
        from india.market import DEFAULT_CALENDAR
        calls, clock = [], [start]
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(CFG, state_dir=d)

            def fake_main(args):
                calls.append((clock[0].strftime('%H:%M'), 'settle' if '--settle' in args else 'alert'))
                if '--settle' not in args and sent_after_first:
                    (Path(d) / ('sent-%s' % clock[0].date().isoformat())).write_text('x')

            def tick(_):
                clock[0] += dt.timedelta(minutes=step)
            P.serve(['--config', 'x'], cfg, DEFAULT_CALENDAR, now_fn=lambda: clock[0], sleep=tick,
                    main_fn=fake_main, iterations=minutes, log=lambda m: None)
            self.assertTrue((Path(d) / 'heartbeat').exists())
        return calls

    def test_alert_once_at_0845_and_settles_every_10_minutes(self):
        calls = self.drive(dt.datetime(2026, 11, 13, 8, 40, tzinfo=IST), 30)
        self.assertEqual([c for c in calls if c[1] == 'alert'], [('08:45', 'alert')])
        self.assertEqual([c[0] for c in calls if c[1] == 'settle'], ['08:40', '08:50', '09:00'])

    def test_failed_alert_retried_three_times_twenty_minutes_apart(self):
        calls = self.drive(dt.datetime(2026, 11, 13, 8, 45, tzinfo=IST), 120, sent_after_first=False)
        self.assertEqual([c[0] for c in calls if c[1] == 'alert'], ['08:45', '09:05', '09:25'])

    def test_no_alert_on_holiday_or_after_deadline(self):
        holiday = self.drive(dt.datetime(2026, 11, 24, 8, 40, tzinfo=IST), 30)
        late = self.drive(dt.datetime(2026, 11, 13, 11, 30, tzinfo=IST), 30)
        self.assertFalse([c for c in holiday + late if c[1] == 'alert'])

    def test_crashing_run_does_not_stop_the_loop(self):
        from india.market import DEFAULT_CALENDAR
        with tempfile.TemporaryDirectory() as d:
            calls = []

            def boom(args):
                calls.append(args)
                raise RuntimeError('x')
            P.serve([], dict(CFG, state_dir=d), DEFAULT_CALENDAR,
                    now_fn=lambda: dt.datetime(2026, 11, 13, 8, 50, tzinfo=IST), sleep=lambda _: None,
                    main_fn=boom, iterations=3, log=lambda m: None)
        self.assertEqual(len(calls), 2)   # one alert attempt + one settle; retries wait 20 minutes

if __name__ == '__main__':
    unittest.main()
