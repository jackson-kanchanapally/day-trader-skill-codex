import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from india import gold as G
from india import picks as P
from india.data import IST, Bar

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def day_bars(rows, start=dt.date(2025, 1, 6)):
    """Weekday bars from (open, high, low, close) rows, labelled at midnight UTC."""
    out, day = [], start
    for o, h, l, c in rows:
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        out.append(Bar(dt.datetime.combine(day, dt.time(), UTC), o, h, l, c, 0.0))
        day += dt.timedelta(days=1)
    return out


def trend(n, start=2000.0, drift=0.003, wave=0.01, start_day=dt.date(2024, 1, 1)):
    """Rising closes with a gentle wave; open = previous close."""
    rows, prev = [], start
    for i in range(n):
        c = start * (1 + drift) ** i * (1 + wave * math.sin(i / 3))
        rows.append((prev, max(prev, c) * 1.004, min(prev, c) * 0.996, c))
        prev = c
    return day_bars(rows, start_day)


def cfg(**over):
    # news is off here so no test reaches the network; tests/test_gold_news.py covers it.
    return G.settings(dict({'risk_per_trade': 5000, 'news': 'off'}, **over))


def feat(**over):
    """A features dict for a clean pullback buy in an uptrend."""
    f = {'date': '2026-11-16', 'open': 12000.0, 'high': 12150.0, 'low': 11980.0, 'close': 12140.0,
         'prev_close': 12000.0, 'prev_high': 12100.0, 'prev_low': 11950.0, 'change_pct': 1.17,
         'ema20': 12050.0, 'ema50': 11800.0, 'ema200': 11000.0, 'ema50_prev5': 11750.0, 'ema200_prev10': 10950.0,
         'rsi': 56.0, 'rsi_min5': 47.0, 'rsi_max5': 58.0, 'macd': 40.0, 'macd_signal': 30.0,
         'macd_hist': 10.0, 'macd_hist_prev': 6.0, 'atr': 150.0, 'atr_pct': 1.24,
         'adx': 26.0, 'plus_di': 28.0, 'minus_di': 15.0, 'high20': 12400.0, 'low20': 11700.0,
         'high3': 12150.0, 'low3': 11990.0, 'swing_high5': 12200.0, 'swing_low5': 11950.0,
         'close_loc': 0.94, 'true_range': 170.0, 'weekly': 1, 'weekly_ema': 11900.0,
         'dxy': 100.0, 'dxy_change_pct': -1.0, 'us10y': 4.0, 'us10y_change': -0.2,
         'comex': 3900.0, 'usdinr': 93.0}
    f.update(over)
    return f


def plan(**over):
    p = {'side': 'BUY', 'entry': 1000.0, 'stop': 950.0, 'target1': 1050.0, 'target2': None, 'no_chase': 1020.0,
         'lots': 4, 'max_loss': 200.0, 'trail_atr': 2.0, 'features': {'atr': 20.0},
         'entry_valid_sessions': 3, 'signal_date': '2025-01-03', 'basis': 1.06}
    p.update(over)
    return p


def sim_cfg(**over):
    c = G.settings({'costs': {k: 0 for k in G.COST_DEFAULTS}})
    c.update(over)
    return c


class DataTests(unittest.TestCase):
    def test_parse_daily_uses_exchange_date_and_skips_blank_rows(self):
        base = int(dt.datetime(2026, 11, 13, 5, tzinfo=UTC).timestamp())   # midnight New York
        doc = {'chart': {'result': [{'meta': {'symbol': 'GC=F', 'gmtoffset': -18000},
                                     'timestamp': [base, base + 86400 * 3, base + 86400 * 4],
                                     'indicators': {'quote': [{'open': [1, None, 3], 'high': [2, 2, 4],
                                                               'low': [0.5, 1, 2.5], 'close': [1.5, 1, 3.5]}]}}]}}
        bars = G.parse_daily(doc, 'GC=F')
        self.assertEqual([b.time.date() for b in bars], [dt.date(2026, 11, 13), dt.date(2026, 11, 17)])
        with self.assertRaises(ValueError):
            G.parse_daily(doc, 'SI=F')

    def test_completed_waits_for_the_comex_day_to_end(self):
        bars = day_bars([(1, 2, 0.5, 1.5)] * 2, dt.date(2026, 11, 16))
        before = dt.datetime(2026, 11, 17, 21, 59, tzinfo=UTC)
        self.assertEqual(len(G.completed(bars, before)), 1)
        self.assertEqual(len(G.completed(bars, before + dt.timedelta(minutes=1))), 2)
        # 08:30 IST next morning sees both
        self.assertEqual(len(G.completed(bars, dt.datetime(2026, 11, 18, 8, 30, tzinfo=IST))), 2)

    def test_proxy_converts_to_rupees_per_gram_and_drops_bars_without_fx(self):
        gold = day_bars([(3110.34768, 3110.34768, 3110.34768, 3110.34768)] * 3, dt.date(2026, 1, 5))
        fx = day_bars([(90, 90, 90, 90)], dt.date(2026, 1, 5))
        bars, refs = G.proxy(gold, fx, 1.05)
        self.assertAlmostEqual(bars[0].close, 100 * 90 * 1.05, places=6)
        self.assertEqual(refs[0], (3110.34768, 90))
        far = day_bars([(90, 90, 90, 90)], dt.date(2025, 12, 1))
        self.assertEqual(G.proxy(gold, far, 1.0)[0], [])

    def test_lookup_is_causal(self):
        bars = day_bars([(c, c, c, c) for c in range(100, 130)], dt.date(2026, 1, 5))
        look = G.Lookup(bars, back=20)
        self.assertIsNone(look.at(bars[10].time.date()))
        hit = look.at(bars[25].time.date())
        self.assertEqual(hit['close'], 125)
        self.assertEqual(hit['change'], 20)


class IndicatorTests(unittest.TestCase):
    def test_adx_and_weekly_trend_on_a_steady_uptrend(self):
        bars = trend(260, wave=0.002)
        adx, pdi, mdi = G.adx_series(bars)
        self.assertIsNone(adx[20])
        self.assertGreater(adx[-1], 25)
        self.assertGreater(pdi[-1], mdi[-1])
        self.assertEqual(G.weekly_series(bars)[-1][0], 1)

    def test_features_need_enough_history(self):
        bars = trend(150)
        with self.assertRaises(ValueError):
            G.features(bars, G.series(bars), len(bars) - 1)
        bars = trend(260)
        f = G.features(bars, G.series(bars), len(bars) - 1)
        self.assertEqual(f['date'], bars[-1].time.date().isoformat())
        self.assertGreater(f['high20'], 0)


class SignalTests(unittest.TestCase):
    def test_pullback_buy(self):
        cand, reason = G.evaluate(feat(), cfg())
        self.assertIsNone(reason)
        self.assertEqual((cand['side'], cand['setup']), ('BUY', 'PULLBACK'))
        self.assertGreaterEqual(cand['score'], 80)
        self.assertIn('pulled back to EMA20 and turned up', cand['reasons'])

    def test_breakout_buy_needs_adx_and_a_strong_close(self):
        f = feat(close=12450.0, high=12460.0, close_loc=0.95, low3=12100.0)
        cand, _ = G.evaluate(f, cfg())
        self.assertEqual(cand['setup'], 'BREAKOUT')
        self.assertIsNone(G.evaluate(dict(f, adx=15.0), cfg())[0])
        self.assertIsNone(G.evaluate(dict(f, close_loc=0.4), cfg())[0])

    def test_weekly_trend_must_agree_for_pullbacks(self):
        cand, reason = G.evaluate(feat(weekly=0), cfg())
        self.assertIsNone(cand)
        self.assertIn('weekly trend does not agree', reason)

    def test_shorts_off_by_default_and_mirrored_when_on(self):
        down = feat(open=12100.0, high=12120.0, low=11950.0, close=11960.0, ema20=12050.0, ema50=12300.0,
                    ema200=13000.0, ema50_prev5=12350.0, ema200_prev10=13050.0, rsi=44.0, rsi_max5=53.0,
                    macd=-40.0, macd_signal=-30.0, macd_hist=-10.0, macd_hist_prev=-6.0, high3=12110.0,
                    close_loc=0.06, weekly=-1, plus_di=15.0, minus_di=28.0, prev_low=11990.0,
                    dxy_change_pct=1.0, us10y_change=0.2)
        self.assertIsNone(G.evaluate(down, cfg())[0])
        cand, _ = G.evaluate(down, cfg(allow_short=True))
        self.assertEqual((cand['side'], cand['setup']), ('SELL', 'PULLBACK'))

    def test_macro_against_lowers_the_score(self):
        good = G.evaluate(feat(), cfg())[0]['score']
        bad = G.evaluate(feat(dxy_change_pct=2.0, us10y_change=0.4), cfg())[0]['score']
        missing = G.evaluate(feat(dxy_change_pct=None, us10y_change=None), cfg())[0]['score']
        self.assertEqual(good - bad, 15)
        self.assertTrue(bad < missing < good)


class PlanTests(unittest.TestCase):
    def test_geometry_and_sizing_stay_within_risk(self):
        c = cfg()
        p = G.build_plan(feat(), G.evaluate(feat(), c)[0], c, 1.06)
        self.assertEqual(p['entry'], 12165)              # high + 0.1 ATR, whole rupees
        self.assertLess(p['stop'], p['entry'])
        risk = p['entry'] - p['stop']
        self.assertTrue(150 <= risk <= 375)              # 1 to 2.5 ATR
        self.assertEqual(p['target1'], p['entry'] + risk)
        self.assertIsNone(p['target2'])                  # trailing by default
        self.assertLessEqual(p['max_loss'], c['risk_per_trade'])
        self.assertGreater(p['lots'], 1)
        self.assertEqual(p['margin'], round(p['notional'] * 0.10, 2))
        self.assertIn('FOMC', ' '.join(G.build_plan(feat(date='2026-12-01'), G.evaluate(feat(), c)[0], c, 1.06)['flags']))

    def test_fixed_target_mode_and_too_small_budget(self):
        c = cfg(trail_atr=0)
        p = G.build_plan(feat(), G.evaluate(feat(), c)[0], c, 1.06)
        self.assertEqual(p['target2'] - p['entry'], 3 * (p['entry'] - p['stop']))
        tiny = cfg(risk_per_trade=50)
        self.assertIsNone(G.build_plan(feat(), G.evaluate(feat(), tiny)[0], tiny, 1.06))

    def test_costs_are_positive_and_scale_with_lots(self):
        c = cfg()
        one, two = G.trade_costs(12000, 12100, 1, c), G.trade_costs(12000, 12100, 2, c)
        self.assertGreater(one, 40)                      # two orders of brokerage plus GST
        self.assertGreater(two, one)
        self.assertEqual(G.trade_costs(12000, 12100, 0, c), 0)


class SimulateTests(unittest.TestCase):
    def test_trigger_t1_then_trailing_stop(self):
        bars = day_bars([(995, 1005, 990, 1002),     # trigger at 1000
                         (1002, 1052, 1000, 1045),   # T1, half booked, stop to 1000
                         (1045, 1090, 1040, 1085),   # best 1090 -> trail 1050
                         (1080, 1082, 1049, 1060)])  # trail hit
        r = G.simulate(plan(), bars, sim_cfg())
        self.assertEqual(r['status'], 'CLOSED')
        self.assertEqual(r['exit_reason'], 'TRAIL')
        self.assertEqual([e['kind'] for e in r['events']], ['TRIGGER', 'TARGET1', 'TRAIL'])
        self.assertEqual(r['net'], 2 * 50 + 2 * 50)
        self.assertEqual(r['r'], 1.0)

    def test_gap_above_no_chase_is_skipped_and_untriggered_expires(self):
        self.assertEqual(G.simulate(plan(), day_bars([(1030, 1040, 1025, 1035)]), sim_cfg())['status'], 'SKIPPED')
        quiet = day_bars([(990, 995, 980, 990)] * 4)
        r = G.simulate(plan(), quiet, sim_cfg())
        self.assertEqual((r['status'], r['events'][-1]['kind']), ('EXPIRED', 'EXPIRED'))
        self.assertEqual(G.simulate(plan(), quiet[:2], sim_cfg())['status'], 'PENDING')

    def test_stop_first_on_an_ambiguous_bar_and_gap_through_stop(self):
        both = day_bars([(995, 1060, 940, 1000)])
        r = G.simulate(plan(), both, sim_cfg())
        self.assertEqual((r['exit_reason'], r['r']), ('STOP', -1.0))
        gap = day_bars([(995, 1005, 990, 1002), (930, 935, 920, 925)])
        r = G.simulate(plan(), gap, sim_cfg())
        self.assertEqual(r['events'][-1]['price'], 930)
        self.assertEqual(r['net'], -70 * 4)

    def test_open_trade_is_marked_to_market_and_time_stop_closes_it(self):
        bars = day_bars([(995, 1005, 990, 1002)] + [(1002, 1010, 995, 1008)] * 3)
        r = G.simulate(plan(), bars, sim_cfg())
        self.assertEqual(r['status'], 'OPEN')
        self.assertEqual(r['unrealised'], 8 * 4)
        self.assertEqual(r['open_r'], 0.16)
        r = G.simulate(plan(), bars, sim_cfg(max_hold_sessions=3))
        self.assertEqual((r['status'], r['exit_reason']), ('CLOSED', 'TIME'))

    def test_fixed_t2_and_short_side(self):
        bars = day_bars([(995, 1005, 990, 1002), (1002, 1110, 1001, 1100)])
        r = G.simulate(plan(target2=1100.0, trail_atr=0), bars, sim_cfg())
        self.assertEqual([e['kind'] for e in r['events']], ['TRIGGER', 'TARGET1', 'TARGET2'])
        short = plan(side='SELL', entry=1000.0, stop=1050.0, target1=950.0, no_chase=980.0, target2=900.0, trail_atr=0)
        bars = day_bars([(1005, 1010, 995, 998), (998, 1000, 945, 950), (950, 955, 890, 900)])
        r = G.simulate(short, bars, sim_cfg())
        self.assertEqual(r['exit_reason'], 'TARGET2')
        self.assertEqual(r['net'], 2 * 50 + 2 * 100)


class ConfigTests(unittest.TestCase):
    def test_example_config_loads_with_gold_disabled(self):
        g = P.load_config(ROOT / 'config/picks.example.json')['gold']
        self.assertFalse(g['enabled'])
        self.assertFalse(g['allow_short'])
        self.assertEqual(g['contract'], 'GOLDPETAL')
        self.assertEqual(g['costs'], G.COST_DEFAULTS)
        self.assertFalse(P.load_config(None)['gold']['enabled'])

    def test_bad_settings_are_rejected(self):
        for bad in ({'nope': 1}, {'enabled': 'yes'}, {'basis': 2}, {'target1_r': 3, 'target2_r': 2},
                    {'max_hold_sessions': 1}, {'event_dates': [{'date': 'soon'}]}, {'costs': {'x': 1}},
                    {'alert_time': '25:00'}):
            with self.assertRaises(ValueError, msg=bad):
                G.settings(bad)


class CalibrationTests(unittest.TestCase):
    def test_calibrate_stores_basis_and_rejects_nonsense(self):
        quotes = {'GC=F': {'price': 3110.34768, 'time': 't'}, 'USDINR=X': {'price': 100.0, 'time': 't'}}
        with tempfile.TemporaryDirectory() as d:
            c = cfg(state_dir=d)
            now = dt.datetime(2026, 11, 17, 10, tzinfo=IST)
            doc = G.calibrate(c, 10600, now, quotes.get)
            self.assertAlmostEqual(doc['basis'], 1.06)
            self.assertEqual(G.load_calibration(d)['basis'], doc['basis'])
            with self.assertRaises(ValueError):
                G.calibrate(c, 106000 / 10 * 2, now, quotes.get)   # 10 g price typed by mistake
            with self.assertRaises(ValueError):
                G.calibrate(c, 50, now, quotes.get)


def NO_QUOTE(symbol):
    raise ValueError('offline')


def quotes(gc, fx=90.0):
    return {G.GOLD: {'price': gc, 'time': '2026-11-18T05:00:00+00:00'},
            G.FX: {'price': fx, 'time': '2026-11-18T05:00:00+00:00'}}.get


def fake_fetch(series):
    def fetch(symbol, start, now=None):
        return series[symbol]
    return fetch


def market(n=320):
    gold = trend(n, start=2000.0, drift=0.002, start_day=dt.date(2025, 1, 1))
    flat = lambda v: day_bars([(v, v, v, v)] * (n + 30), dt.date(2024, 12, 1))
    return {G.GOLD: gold, G.FX: flat(90.0), G.DXY: flat(100.0), G.US10Y: flat(4.0)}


class JobTests(unittest.TestCase):
    def test_messages_render_for_setup_and_no_setup(self):
        c = cfg()
        f = feat()
        p = G.build_plan(f, G.evaluate(f, c)[0], c, 1.06)
        base = {'data_date': '2026-11-16', 'generated_at': '2026-11-17T08:30:00+05:30', 'features': f,
                'basis': 1.06, 'calibration': None, 'calibration_age_days': None, 'reason': 'x <y>',
                'trades': [], 'news': [], 'watch': G.watch_levels(f), 'warnings': ['a & b']}
        text = '\n'.join(G.format_messages(dict(base, plan=p), c))
        self.assertIn('🟢 <b>BUY setup</b> (pullback) · score 96', text)
        self.assertIn('▶ Entry: SL-buy above ₹12,165, limit ₹12,240 · valid 3 sessions', text)
        self.assertIn('then trail ₹300 behind the best price', text)
        self.assertIn('Basis 1.060 · rule-based', text)
        self.assertIn('a &amp; b', text)
        self.assertLess(len(text.splitlines()), 25)
        text = '\n'.join(G.format_messages(dict(base, plan=None), c))
        self.assertIn('⛔ <b>NO TRADE, stay flat</b>', text)
        self.assertIn('👀 Watch: dip to ₹12,050 (EMA20) · close above ₹12,400 (20-day high)', text)
        self.assertTrue(all(len(m) <= P.TELEGRAM_LIMIT for m in G.format_messages(dict(base, plan=p), c)))

    def test_send_records_trade_once_and_reports_milestones(self):
        data = market()
        last = data[G.GOLD][-1].time.date()
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / 'picks.json'
            conf.write_text(json.dumps({'state_dir': str(Path(d) / 'picks'),
                                        'gold': {'state_dir': d, 'risk_per_trade': 5000}}))
            now = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(8, 30), IST)
            sent = []
            forced = {'side': 'BUY', 'setup': 'PULLBACK', 'score': 75.0, 'components': {}, 'reasons': ['test']}
            env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
            args = ['--config', str(conf), '--as-of', now.isoformat()]
            with mock.patch.object(G, 'fetch_daily', fake_fetch(data)), mock.patch.object(G, 'latest_quote', NO_QUOTE), \
                    mock.patch.object(G, 'evaluate', return_value=(forced, None)), \
                    mock.patch.object(P, 'send_telegram', lambda t, c, text: sent.append(text)), \
                    mock.patch('sys.stdout'), mock.patch('sys.stderr'), mock.patch.dict('os.environ', env):
                self.assertEqual(G.main(args), 0)
                self.assertEqual(G.main(args), 0)          # same COMEX session: skipped
            self.assertEqual(len(sent), 1)
            self.assertIn('<b>GOLDPETAL</b>', sent[0])
            self.assertIn('BUY setup', sent[0])
            trades = G.load_trades(d)
            self.assertEqual(len(trades), 1)
            self.assertTrue((Path(d) / ('sent-%s' % last)).exists())
            self.assertTrue((Path(d) / ('ran-%s' % now.date())).exists())

            # Next session trades through the entry: the update is reported once.
            p = trades[0]['plan']
            nxt = last + dt.timedelta(days=3 if last.weekday() == 4 else 1)
            usd = p['entry'] / (90 / G.TROY_OUNCE_G * p['basis'])      # entry level in COMEX dollars
            data[G.GOLD] = data[G.GOLD] + [Bar(dt.datetime.combine(nxt, dt.time(), UTC), usd - 1, usd + 1, usd - 2, usd + 0.5, 0.0)]
            later = dt.datetime.combine(nxt + dt.timedelta(days=1), dt.time(8, 30), IST)
            sent.clear()
            with mock.patch.object(G, 'fetch_daily', fake_fetch(data)), mock.patch.object(G, 'latest_quote', NO_QUOTE), \
                    mock.patch.object(G, 'evaluate', return_value=(None, 'quiet')), \
                    mock.patch.object(P, 'send_telegram', lambda t, c, text: sent.append(text)), \
                    mock.patch('sys.stdout'), mock.patch('sys.stderr'), mock.patch.dict('os.environ', env):
                self.assertEqual(G.main(['--config', str(conf), '--as-of', later.isoformat()]), 0)
            self.assertIn('📒 🟢 BUY', sent[0])
            self.assertIn('open @', sent[0])
            self.assertEqual(G.load_trades(d)[0]['result']['status'], 'OPEN')
            self.assertEqual(G.load_trades(d)[0]['reported'], ['TRIGGER:%s' % nxt])

    def test_backtest_report_structure(self):
        rep = G.backtest(cfg(), dt.date(2025, 1, 1), now=dt.datetime(2026, 6, 1, tzinfo=UTC), fetch=fake_fetch(market()))
        self.assertIn('alerts', rep)
        self.assertEqual(rep['settings']['trail_atr'], 2.0)
        self.assertIsInstance(G.format_backtest(rep), str)


class ServeTests(unittest.TestCase):
    def run_serve(self, gold, stamps, ran_marker=None):
        with tempfile.TemporaryDirectory() as d:
            base = P.load_config(None)
            base['state_dir'] = d
            (Path(d) / 'gold').mkdir()
            base['gold'] = G.settings(dict(gold, state_dir=str(Path(d) / 'gold')))
            calls = []

            def gold_fn(args):
                calls.append(args[2:])
                if not args[2:] and ran_marker:
                    (Path(d) / 'gold' / ran_marker).write_text('x')

            def lab_fn(args):
                calls.append(['LAB'])
                (Path(d) / 'gold_lab').mkdir(exist_ok=True)
                (Path(d) / 'gold_lab' / ('ran-%s' % stamps[0].date().isoformat())).write_text('x')

            times = iter(stamps)
            P.serve(['--config', 'x'], base, P.DEFAULT_CALENDAR, now_fn=lambda: next(times), sleep=lambda s: None,
                    main_fn=lambda a: None, iterations=len(stamps), log=lambda m: None, gold_fn=gold_fn, lab_fn=lab_fn)
            return calls

    def test_morning_alert_runs_once_and_commands_poll_every_tick(self):
        at = lambda h, m: dt.datetime(2026, 11, 17, h, m, tzinfo=IST)
        calls = self.run_serve({'enabled': True, 'live_watch': False}, [at(8, 0), at(8, 31), at(8, 32)], 'ran-2026-11-17')
        # The lab runs once, right after the gold alert has written its ran- marker.
        self.assertEqual(calls, [['--commands'], [], ['LAB'], ['--commands'], ['--commands']])

    def test_live_watch_runs_inside_mcx_hours_at_its_cadence(self):
        at = lambda h, m: dt.datetime(2026, 11, 17, h, m, tzinfo=IST)
        calls = self.run_serve({'enabled': True, 'commands': False},
                               [at(8, 59), at(9, 0), at(9, 10), at(9, 16), at(23, 31)], 'ran-2026-11-17')
        self.assertEqual([c for c in calls if c and c != ['LAB']], [['--watch'], ['--watch']])

    def test_nothing_runs_when_disabled(self):
        self.assertEqual(self.run_serve({'enabled': False}, [dt.datetime(2026, 11, 18, 9, 0, tzinfo=IST)]), [])


def live_trade(d, status='PENDING', **result):
    """One saved paper trade: buy trigger 1000, stop 950, T1 1050 (proxy basis 1.0, FX 31.1035 => COMEX $ = INR)."""
    p = plan(signal_date='2026-11-16', basis=1.0, contract='GOLDPETAL', setup='PULLBACK')
    r = {'status': status, 'events': [], 'sessions_seen': 1, 'stop': 950.0}
    if status == 'OPEN':
        r.update(fill=1000.0, fill_date='2026-11-17', last_close=1010.0, unrealised=40.0, net=0.0, open_r=0.2, t1_hit=False)
    r.update(result)
    G.save_trades(d, [{'plan': p, 'result': r, 'reported': []}])


def bars15(prices, start=dt.datetime(2026, 11, 17, 4, 0, tzinfo=UTC)):
    """15-minute bars from (high, low) pairs."""
    return [Bar(start + dt.timedelta(minutes=15 * i), (h + l) / 2, h, l, (h + l) / 2, 0.0) for i, (h, l) in enumerate(prices)]


class LiveTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

    def run_watch(self, d, prices, now=None):
        fx = {'price': G.TROY_OUNCE_G, 'time': 't'}
        return G.watch(cfg(state_dir=d), now or self.NOW, fetch_bars=lambda s, n: bars15(prices),
                       fetch_quote=lambda s: fx)

    def test_parse_intraday_drops_the_live_tick_and_the_forming_bar(self):
        base = 1794981600          # 2026-11-18 05:00 UTC, on the 15-minute grid
        stamps = [base, base + 900, base + 900 + 180]
        doc = {'chart': {'result': [{'meta': {'symbol': 'GC=F'}, 'timestamp': stamps,
                                     'indicators': {'quote': [{k: [1.0, 2.0, 3.0] for k in ('open', 'high', 'low', 'close')}]}}]}}
        now = dt.datetime.fromtimestamp(base + 900 + 200, UTC)
        self.assertEqual([b.time.timestamp() for b in G.parse_intraday(doc, 'GC=F', now)], [base])

    def test_near_then_trigger_then_t1_each_sent_once(self):
        with tempfile.TemporaryDirectory() as d:
            live_trade(d)
            texts = self.run_watch(d, [(990, 980), (996, 985), (1003, 994), (1052, 1001)])
            self.assertEqual([t.split('</b>')[0] for t in texts],
                             ['⏳ <b>GOLDPETAL BUY 16 Nov: near the trigger', '🔔 <b>GOLDPETAL BUY 16 Nov: trigger crossed',
                              '🎯 <b>GOLDPETAL BUY 16 Nov: T1 reached'])
            saved = G.load_trades(d)[0]
            self.assertEqual(saved['reported'], ['TARGET1:2026-11-17', 'TRIGGER:2026-11-17'])
            self.assertEqual(self.run_watch(d, [(990, 980), (996, 985), (1003, 994), (1052, 1001), (1060, 1040)]), [])

    def test_stop_on_an_open_trade_and_nothing_for_closed_ones(self):
        with tempfile.TemporaryDirectory() as d:
            live_trade(d, 'OPEN', fill=1000.0)
            texts = self.run_watch(d, [(1010, 990), (995, 945)])
            self.assertEqual(len(texts), 1)
            self.assertIn('stop-loss reached', texts[0])
            self.assertEqual(G.load_trades(d)[0]['reported'], [])     # the morning alert confirms the close
            live_trade(d, 'CLOSED')
            self.assertEqual(self.run_watch(d, [(995, 900)]), [])

    def test_bars_before_the_signal_close_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            live_trade(d)
            early = [Bar(dt.datetime(2026, 11, 16, 20, 0, tzinfo=UTC), 1000, 1010, 990, 1005, 0.0)]
            texts = G.watch(cfg(state_dir=d), self.NOW, fetch_bars=lambda s, n: early,
                            fetch_quote=lambda s: {'price': G.TROY_OUNCE_G, 'time': 't'})
            self.assertEqual(texts, [])

    def test_morning_alert_flags_an_overnight_gap_past_no_chase(self):
        data = market()
        forced = {'side': 'BUY', 'setup': 'PULLBACK', 'score': 75.0, 'components': {}, 'reasons': ['x']}
        last = data[G.GOLD][-1].time.date()
        now = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(8, 30), IST)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(G, 'evaluate', return_value=(forced, None)):
            ctx = G.run(cfg(state_dir=d), now, fetch=fake_fetch(data), fetch_quote=quotes(data[G.GOLD][-1].close * 1.2))
            self.assertIn('beyond the no-chase level', ctx['plan']['flags'][0])
            self.assertIn('/g now (+20.0% vs close', G.format_messages(ctx, cfg())[0])
            ctx = G.run(cfg(state_dir=d), now, fetch=fake_fetch(data), fetch_quote=NO_QUOTE)
            self.assertIn('Live COMEX quote unavailable (offline)', ctx['warnings'])


class CommandTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 18, 10, 0, tzinfo=IST)

    def update(self, uid, text, chat=42, age=5):
        return {'update_id': uid, 'message': {'chat': {'id': chat}, 'text': text, 'date': int(self.NOW.timestamp()) - age}}

    def test_only_fresh_commands_from_the_alert_chat_are_answered(self):
        with tempfile.TemporaryDirectory() as d:
            sent = []
            updates = [self.update(5, '/help'), self.update(6, '/help', chat=99), self.update(7, 'hello'),
                       self.update(8, '/trades', age=3600), self.update(9, '/trades@GoldBot')]
            n = G.poll_commands(cfg(state_dir=d), self.NOW, 'tok', '42', send=lambda t, c, x: sent.append((c, x)),
                                fetch_updates=lambda t, off: updates if off == 0 else [])
            self.assertEqual(n, 2)
            self.assertIn('/calibrate 12450', sent[0][1])
            self.assertIn('No gold paper trades yet', sent[1][1])
            self.assertEqual(json.loads((Path(d) / 'telegram-offset.json').read_text()), {'offset': 10})
            seen = []
            G.poll_commands(cfg(state_dir=d), self.NOW, 'tok', '42', send=lambda *a: None,
                            fetch_updates=lambda t, off: seen.append(off) or [])
            self.assertEqual(seen, [10])

    def test_calibrate_price_and_trades_commands(self):
        with tempfile.TemporaryDirectory() as d:
            c = cfg(state_dir=d)
            q = quotes(3110.34768, 100.0)
            self.assertIn('Send the GOLDPETAL price', G.handle_command('/calibrate soon', c, self.NOW, q)[0])
            self.assertIn('basis 1.0600', G.handle_command('/calibrate ₹10,600', c, self.NOW, q)[0])
            self.assertIn('failed', G.handle_command('/calibrate 106000', c, self.NOW, q)[0])
            live_trade(d, 'OPEN', fill=1000.0)
            text = G.handle_command('/price', c, self.NOW, q)[0]
            self.assertIn('proxy ₹10,600/g', text)
            self.assertIn('Open BUY from ₹1,000', text)
            self.assertIn('BUY 16 Nov', G.handle_command('/trades', c, self.NOW, q)[0])
            self.assertIn('Gold bot commands', G.handle_command('/whatever', c, self.NOW, q)[0])

    def test_gold_command_previews_without_recording(self):
        data = market()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(G, 'fetch_daily', fake_fetch(data)):
            out = G.handle_command('/gold', cfg(state_dir=d), self.NOW, NO_QUOTE)
            self.assertIn('On-demand view', out[0])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
