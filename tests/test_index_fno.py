"""Dashboard alert switches, the index F&O signal rules and their gating; no network."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from india import alert_switches as S
from india import gold as G
from india import index_fno as F
from india import picks as P
from india.data import IST

DAY = dt.date(2026, 11, 20)        # a Friday NSE session
OPEN = dt.datetime.combine(DAY, dt.time(9, 15), IST)


def at(h, m):
    return dt.datetime.combine(DAY, dt.time(h, m), IST)


def candle(h, m, o, hi, lo, c, symbol='INDEX:NIFTY 50'):
    start = at(h, m)
    return dict(symbol=symbol, time=start.isoformat(), known_at=(start + dt.timedelta(minutes=5, seconds=40)).isoformat(),
                open=o, high=hi, low=lo, close=c, usable=True)


# Opening range 24000-24080 (0.33%): three usable candles 09:15-09:30.
RANGE = [candle(9, 15, 24010, 24050, 24000, 24040), candle(9, 20, 24040, 24080, 24020, 24060),
         candle(9, 25, 24060, 24070, 24030, 24050)]


def cfg(**kw):
    return F.settings(kw)


def run(candles, now, vix=(), rec=None, **kw):
    rec = rec if rec is not None else {}
    texts = F.process('NIFTY 50', rec, [c for c in candles if F._t(c['known_at']) <= now], list(vix), now, OPEN, cfg(**kw))
    return rec, texts


class Switches(unittest.TestCase):
    def test_defaults_keep_gold_on_and_fno_off(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / S.FILENAME
            self.assertEqual(S.read(path), dict(gold=True, index_fno=False, updated_at=None))
            S.write(path, 'index_fno', True)
            S.write(path, 'gold', False)
            state = S.read(path)
            self.assertTrue(state['index_fno'])
            self.assertFalse(state['gold'])
            self.assertIsNotNone(state['updated_at'])
            with self.assertRaises(ValueError):
                S.write(path, 'crypto', True)
            with self.assertRaises(ValueError):
                S.write(path, 'gold', 'yes')

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / S.FILENAME
            path.write_text('{not json')
            self.assertTrue(S.enabled(path, 'gold'))
            self.assertFalse(S.enabled(path, 'index_fno'))

    def test_path_sits_beside_the_picks_state_dir(self):
        self.assertEqual(S.path_for({'state_dir': 'var/state/picks'}), Path('var/state/alert-switches.json'))


class Rules(unittest.TestCase):
    def test_waits_for_the_range_then_arms(self):
        rec, texts = run(RANGE, at(9, 31))
        self.assertEqual((rec['status'], texts), ('WAITING', []))
        rec, texts = run(RANGE, at(9, 34))
        self.assertEqual(rec['status'], 'ARMED')
        self.assertEqual((rec['range']['high'], rec['range']['low']), (24080, 24000))

    def test_missing_range_candle_means_no_signal_today(self):
        rec, _ = run(RANGE[:2], at(9, 40))
        self.assertEqual(rec['status'], 'NO_RANGE')

    def test_range_too_wide_or_too_narrow_is_skipped(self):
        self.assertEqual(run(RANGE, at(9, 40), max_range_pct=0.2)[0]['status'], 'SKIPPED_RANGE')
        self.assertEqual(run(RANGE, at(9, 40), min_range_pct=0.5)[0]['status'], 'SKIPPED_RANGE')

    def test_buy_breakout_then_t1_then_t2(self):
        bars = RANGE + [candle(9, 30, 24050, 24075, 24040, 24070), candle(9, 35, 24070, 24110, 24065, 24100)]
        rec, texts = run(bars, at(9, 41))
        self.assertEqual(rec['status'], 'OPEN')
        sig = rec['signal']
        self.assertEqual((sig['side'], sig['entry'], sig['stop'], sig['risk']), ('BUY', 24100, 24000, 100))
        self.assertEqual((sig['target1'], sig['target2']), (24200, 24300))
        self.assertEqual(len(texts), 1)
        self.assertIn('NIFTY F&amp;O signal: BUY', texts[0])
        self.assertIn('Stop 24,000.0 (100.0 pts)', texts[0])
        # Same tick again: nothing repeats.
        self.assertEqual(run(bars, at(9, 42), rec=rec)[1], [])
        bars += [candle(9, 40, 24100, 24210, 24090, 24190)]
        rec, texts = run(bars, at(9, 46), rec=rec)
        self.assertIn('T1 reached', texts[0])
        self.assertEqual(rec['signal']['stop'], 24100)            # moved to the entry
        bars += [candle(9, 45, 24190, 24320, 24180, 24300)]
        rec, texts = run(bars, at(9, 51), rec=rec)
        self.assertIn('T2 reached', texts[0])
        self.assertEqual((rec['status'], rec['points'], rec['r']), ('CLOSED', 150.0, 1.5))

    def test_sell_breakout_stopped_out(self):
        bars = RANGE + [candle(9, 30, 24010, 24020, 23980, 23990), candle(9, 35, 23990, 24085, 23985, 24070)]
        rec, texts = run(bars, at(9, 41))
        self.assertEqual(rec['signal']['side'], 'SELL')
        self.assertIn('stop-loss hit', texts[-1])
        self.assertEqual((rec['status'], rec['r']), ('CLOSED', -1.0))

    def test_stale_breakout_is_missed_not_sent(self):
        bars = RANGE + [candle(9, 30, 24050, 24110, 24040, 24100)]
        rec, texts = run(bars, at(9, 50))
        self.assertEqual((rec['status'], texts), ('MISSED', []))

    def test_high_vix_skips_the_signal(self):
        bars = RANGE + [candle(9, 30, 24050, 24110, 24040, 24100)]
        vix = [candle(9, 25, 27, 27, 27, 27, symbol='INDEX:INDIA VIX')]
        rec, texts = run(bars, at(9, 36), vix=vix)
        self.assertEqual((rec['status'], texts), ('SKIPPED_VIX', []))
        self.assertEqual(run(bars, at(9, 36), vix=vix, max_vix=None)[0]['status'], 'OPEN')

    def test_no_breakout_by_the_deadline(self):
        bars = RANGE + [candle(h, m, 24040, 24060, 24020, 24050) for h, m in ((9, 30), (11, 0))]
        rec, _ = run(bars, at(11, 10), entry_deadline='11:00')
        self.assertEqual(rec['status'], 'NO_BREAKOUT')

    def test_square_off_closes_what_is_left(self):
        bars = RANGE + [candle(9, 30, 24050, 24110, 24040, 24100), candle(15, 10, 24120, 24150, 24110, 24140)]
        rec, _ = run(bars, at(9, 36))
        self.assertEqual(rec['status'], 'OPEN')
        rec, texts = run(bars, at(15, 16), rec=rec)
        self.assertIn('square-off', texts[-1])
        self.assertEqual((rec['status'], rec['points']), ('CLOSED', 40.0))

    def test_lot_size_adds_rupee_risk(self):
        bars = RANGE + [candle(9, 30, 24050, 24110, 24040, 24100)]
        _, texts = run(bars, at(9, 36), lot_size={'NIFTY 50': 65})
        self.assertIn('₹6,500 per lot of 65', texts[0])

    def test_settings_are_validated(self):
        for bad in ({'range_minutes': 7}, {'target_r': [2, 1]}, {'lot_size': {'NIFTY IT': 10}},
                    {'entry_deadline': '15:20'}, {'unknown': 1}, {'max_vix': 2}):
            with self.assertRaises(ValueError, msg=bad):
                F.settings(bad)
        self.assertEqual(P.load_config(None)['index_fno']['indices'], ['NIFTY 50', 'NIFTY BANK'])


class Gating(unittest.TestCase):
    def config(self, d, **extra):
        conf = Path(d) / 'picks.json'
        conf.write_text(json.dumps(dict({'state_dir': str(Path(d) / 'picks'),
                                         'recorder': {'state_dir': str(Path(d) / 'rec')},
                                         'index_fno': {'state_dir': str(Path(d) / 'fno')}}, **extra)))
        return conf

    def test_fno_main_is_muted_until_switched_on(self):
        with tempfile.TemporaryDirectory() as d:
            conf = self.config(d)
            bars = RANGE + [candle(9, 30, 24050, 24110, 24040, 24100)]
            sent = []
            env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
            args = ['--config', str(conf), '--as-of', at(9, 36).isoformat()]
            with mock.patch.object(F, 'load_candles', lambda *a, **k: bars), \
                    mock.patch.object(P, 'send_telegram', lambda t, c, text: sent.append(text)), \
                    mock.patch('sys.stdout'), mock.patch('sys.stderr'), mock.patch.dict('os.environ', env):
                self.assertEqual(F.main(args), 0)
                self.assertEqual(sent, [])
                self.assertFalse((Path(d) / 'fno').exists())
                S.write(Path(d) / S.FILENAME, 'index_fno', True)
                self.assertEqual(F.main(args), 0)
                self.assertEqual(F.main(args), 0)      # saved state: not sent twice
            self.assertEqual(len(sent), 1)
            self.assertIn('F&amp;O signal: BUY', sent[0])
            saved = json.loads((Path(d) / 'fno' / ('signals-%s.json' % DAY)).read_text())
            self.assertEqual(saved['indices']['NIFTY 50']['status'], 'OPEN')

    def test_gold_off_sends_nothing_but_lets_the_lab_run(self):
        with tempfile.TemporaryDirectory() as d:
            conf = self.config(d, gold={'enabled': True, 'state_dir': str(Path(d) / 'gold')})
            S.write(Path(d) / S.FILENAME, 'gold', False)
            sent = []
            now = dt.datetime(2026, 11, 20, 8, 30, tzinfo=IST)
            with mock.patch.object(G, 'run', side_effect=AssertionError('muted gold must not run')), \
                    mock.patch.object(P, 'send_telegram', lambda t, c, text: sent.append(text)), \
                    mock.patch('sys.stdout'), mock.patch('sys.stderr'), \
                    mock.patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}):
                self.assertEqual(G.main(['--config', str(conf), '--as-of', now.isoformat()]), 0)
                with mock.patch.object(G, 'watch', return_value=['🎯 T1']):
                    self.assertEqual(G.main(['--config', str(conf), '--watch', '--as-of', now.isoformat()]), 0)
            self.assertEqual(sent, [])
            self.assertTrue((Path(d) / 'gold' / 'ran-2026-11-20').exists())


class Scheduler(unittest.TestCase):
    def test_switch_starts_recorder_and_runs_fno_only_in_session(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = P.load_config(None)
            cfg['state_dir'] = str(Path(d) / 'picks')
            calls, made = [], []

            class FakeRecorder:
                closed = False
                def tick(self, now): return dict(feed='indices', status='WAITING')
                def stocks_tick(self, now): return dict(feed='stocks', status='DISABLED')
                def close(self): self.closed = True
            stamps = iter([at(9, 0), at(9, 20), at(9, 21), at(16, 0)])
            clock = [None]

            def now_fn():
                clock[0] = next(stamps)
                if clock[0] == at(9, 20):
                    S.write(Path(d) / S.FILENAME, 'index_fno', True)
                if clock[0] == at(16, 0):
                    S.write(Path(d) / S.FILENAME, 'index_fno', False)
                return clock[0]
            with mock.patch('india.picks.make_recorder', lambda c, *a, **k: made.append(c['recorder']) or FakeRecorder()):
                P.serve([], cfg, P.DEFAULT_CALENDAR, now_fn=now_fn, sleep=lambda s: None, main_fn=lambda a: None,
                        iterations=4, log=lambda m: None, gold_fn=lambda a: None,
                        fno_fn=lambda a: calls.append(clock[0].strftime('%H:%M')))
            self.assertEqual(calls, ['09:20', '09:21'])
            self.assertEqual(len(made), 1)               # started once, on the first tick with the switch on
            rec_cfg = made[0]
            self.assertEqual(rec_cfg['feeds'], ['indices'])
            self.assertEqual(rec_cfg['stock_source'], 'off')
            self.assertIn('INDIA VIX', rec_cfg['indices'])


class DashboardSwitch(unittest.TestCase):
    def test_get_and_post_require_origin_and_token(self):
        from dashboard.__main__ import handler
        with tempfile.TemporaryDirectory() as d:
            picks = Path(d) / 'state' / 'picks'
            try:
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler(Path(d), picks_dir=picks))
            except PermissionError:
                self.skipTest('Sandbox disallows loopback sockets')
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = 'http://127.0.0.1:%d' % server.server_port
            with urllib.request.urlopen(base + '/api/alert-switches') as r:
                view = json.load(r)
            self.assertEqual({s['name']: s['enabled'] for s in view['switches']}, {'gold': True, 'index_fno': False})

            def post(body, token=view['token'], origin=base):
                req = urllib.request.Request(base + '/api/alert-switches', data=json.dumps(body).encode(), method='POST',
                                             headers={'Content-Type': 'application/json', 'X-Trader-Token': token,
                                                      'Origin': origin})
                return urllib.request.urlopen(req)
            for kwargs in (dict(token='wrong'), dict(origin='http://evil.example')):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    post({'name': 'index_fno', 'enabled': True}, **kwargs)
                self.assertEqual(e.exception.code, 403)
                e.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as e:
                post({'name': 'index_fno', 'enabled': 'yes'})
            self.assertEqual(e.exception.code, 409)
            e.exception.close()
            with post({'name': 'index_fno', 'enabled': True}) as r:
                self.assertTrue({s['name']: s['enabled'] for s in json.load(r)['switches']}['index_fno'])
            self.assertTrue(S.read(Path(d) / 'state' / S.FILENAME)['index_fno'])


if __name__ == '__main__':
    unittest.main()
