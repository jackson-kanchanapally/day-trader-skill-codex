"""Alert switches, the index options desk (grading, structure, management) and its gating; no network."""
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
from india import options as O
from india import picks as P
from india.data import IST
from india.market import DEFAULT_CALENDAR

DAY = dt.date(2026, 11, 20)        # a Friday NSE session; NIFTY's 24 Nov expiry moves to 23 Nov (holiday)
OPEN = dt.datetime.combine(DAY, dt.time(9, 15), IST)


def at(h, m):
    return dt.datetime.combine(DAY, dt.time(h), IST) + dt.timedelta(minutes=m)      # minutes may run past 59


def candle(h, m, o, hi, lo, c, symbol='INDEX:NIFTY 50'):
    start = at(h, m)
    return dict(symbol=symbol, time=start.isoformat(), known_at=(start + dt.timedelta(minutes=5, seconds=40)).isoformat(),
                open=o, high=hi, low=lo, close=c, usable=True)


# Opening range 24000-24080 (0.33%): three usable candles 09:15-09:30. Breakout buffer: 12.04 points.
RANGE = [candle(9, 15, 24010, 24050, 24000, 24040), candle(9, 20, 24040, 24080, 24020, 24060),
         candle(9, 25, 24060, 24070, 24030, 24050)]
BREAKOUT = candle(9, 30, 24070, 24110, 24065, 24100)      # strong close at 24100: entry 24100, stop 24000, 1R = 100


def cfg(budget=100000, **kw):
    return dict(F.settings(kw), _budget=budget)


def run(candles, now, vix_level=13.0, rec=None, ctx=None, budget=100000, market=True, **kw):
    rec = rec if rec is not None else {}
    c = cfg(budget, **kw)
    m = O.ModelChain(c, DEFAULT_CALENDAR, vix_level) if market else None
    vix = [candle(9, 15, vix_level, vix_level, vix_level, vix_level, symbol='INDEX:INDIA VIX')]
    texts = F.process('NIFTY 50', rec, [x for x in candles if F._t(x['known_at']) <= now], vix, now, OPEN, c,
                      ctx=ctx, market=m, calendar=DEFAULT_CALENDAR)
    return rec, texts


def context(trend='up', high=24500.0, low=23700.0, close=24050.0, cpr_wide=False):
    """Yesterday's levels with chosen trend; PDH/R1 far away unless overridden."""
    ctx = F.daily_context([dict(date='2026-11-19', open=close, high=high, low=low, close=close)])
    ctx.update(trend=trend, r1=26000.0, s1=22000.0, r2=27000.0, s2=21000.0)
    if cpr_wide:
        ctx.update(cpr_pct=0.8)
    else:
        ctx.update(cpr_pct=0.1, tc=24030.0, bc=24010.0)
    return ctx


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


class Options(unittest.TestCase):
    def test_black_scholes_parity_and_implied_vol(self):
        t = 7 / 365
        call, put = O.bs_price(24000, 24000, t, 0.14, 'CE'), O.bs_price(24000, 24000, t, 0.14, 'PE')
        import math
        self.assertAlmostEqual(call - put, 24000 - 24000 * math.exp(-0.065 * t), places=6)
        self.assertAlmostEqual(O.implied_vol(call, 24000, 24000, t, 'CE'), 0.14, places=4)
        self.assertAlmostEqual(O.bs_delta(24000, 24000, t, 0.14, 'CE'), 0.5, delta=0.05)

    def test_expiries_move_off_holidays_and_skip_near_ones(self):
        nifty = F.settings()['contracts']['NIFTY 50']
        bank = F.settings()['contracts']['NIFTY BANK']
        self.assertEqual(O.scheduled_expiries(nifty, DAY, DEFAULT_CALENDAR, 2), [dt.date(2026, 11, 23), dt.date(2026, 12, 1)])
        self.assertEqual(O.scheduled_expiries(bank, DAY, DEFAULT_CALENDAR, 2), [dt.date(2026, 11, 23), dt.date(2026, 12, 29)])
        expiries = O.scheduled_expiries(nifty, DAY, DEFAULT_CALENDAR)
        self.assertEqual(O.pick_expiry(expiries, DAY, 2, DEFAULT_CALENDAR), dt.date(2026, 12, 1))
        self.assertEqual(O.pick_expiry(expiries, DAY, 0, DEFAULT_CALENDAR), dt.date(2026, 11, 23))

    def test_chain_summary_finds_pcr_max_pain_and_walls(self):
        rows = {23900.0: {'CE': {'oi': 10}, 'PE': {'oi': 900}}, 24000.0: {'CE': {'oi': 300}, 'PE': {'oi': 400}},
                24100.0: {'CE': {'oi': 800}, 'PE': {'oi': 50}}}
        s = O.chain_summary(rows, 24020)
        self.assertEqual((s['call_wall'], s['put_wall'], s['max_pain']), (24100.0, 23900.0, 24000.0))
        self.assertEqual(s['pcr'], round(1350 / 1110, 2))

    def test_upstox_chain_is_parsed_and_quoted(self):
        chain = [{'strike_price': 24100, 'underlying_spot_price': 24095.5, 'call_options': {
            'instrument_key': 'NSE_FO|1', 'market_data': {'ltp': 150.0, 'bid_price': 149.5, 'ask_price': 150.5, 'oi': 5000},
            'option_greeks': {'iv': 13.2, 'delta': 0.52}}, 'put_options': {
            'market_data': {'ltp': 140.0, 'bid_price': 139.0, 'ask_price': 141.0, 'oi': 7000}, 'option_greeks': {'iv': 14.0}}}]
        contracts = [{'expiry': '2026-11-23', 'lot_size': 65}, {'expiry': '2026-12-01', 'lot_size': 65}]
        calls = []

        def get(path, token):
            calls.append(path)
            return contracts if path.startswith('contract') else chain
        m = O.UpstoxChain(F.settings(), DEFAULT_CALENDAR, 'tok', get=get, clock=lambda: 0)
        self.assertEqual(m.expiry('NIFTY 50', DAY), (dt.date(2026, 12, 1), 65))
        q = m.quote('NIFTY 50', dt.date(2026, 12, 1), 24100, 'CE', 24100, at(9, 35))
        self.assertEqual((q['ask'], q['iv'], q['oi']), (150.5, 13.2, 5000))
        m.quote('NIFTY 50', dt.date(2026, 12, 1), 24100, 'PE', 24100, at(9, 35))
        self.assertEqual(len([c for c in calls if c.startswith('chain')]), 1)      # cached for a minute
        self.assertIn('NSE_INDEX%7CNifty%2050', calls[0])

    def test_free_model_is_the_default_even_with_a_token(self):
        c = F.settings()
        self.assertEqual(c['chain_source'], 'model')
        for token in (None, 'tok'):
            market, warning = O.make_market(c, DEFAULT_CALENDAR, 14, token)
            self.assertEqual((market.source, warning), ('model', None))
        self.assertEqual(O.make_market(dict(c, chain_source='upstox'), DEFAULT_CALENDAR, 14, 'tok')[0].source, 'upstox')
        with self.assertRaises(ValueError):
            O.make_market(dict(c, chain_source='upstox'), DEFAULT_CALENDAR, 14, None)
        with self.assertRaises(ValueError):
            F.settings({'chain_source': 'auto'})

    def test_bank_nifty_iv_follows_its_realised_vol_ratio(self):
        c = F.settings()
        contexts = {'NIFTY 50': {'rv20': 12.0}, 'NIFTY BANK': {'rv20': 18.0}}
        self.assertEqual(F.iv_scales(c, contexts), {'NIFTY 50': 1.0, 'NIFTY BANK': 1.5})
        self.assertEqual(F.iv_scales(c, {'NIFTY 50': {'rv20': 10.0}, 'NIFTY BANK': {'rv20': 40.0}})['NIFTY BANK'], 2.0)
        self.assertEqual(F.iv_scales(c, {}), {'NIFTY 50': 1.0})
        m = O.ModelChain(dict(c, _iv_scale={'NIFTY BANK': 1.5}), DEFAULT_CALENDAR, 14)
        self.assertAlmostEqual(m.sigma('NIFTY BANK'), 0.21)
        self.assertAlmostEqual(O.ModelChain(c, DEFAULT_CALENDAR, 14).sigma('NIFTY BANK'), 0.14 * 1.2)


class Rules(unittest.TestCase):
    def test_waits_for_the_range_then_arms(self):
        rec, texts = run(RANGE, at(9, 31))
        self.assertEqual((rec['status'], texts), ('WAITING', []))
        rec, texts = run(RANGE, at(9, 34))
        self.assertEqual(rec['status'], 'ARMED')
        self.assertEqual((rec['range']['high'], rec['range']['low']), (24080, 24000))

    def test_missing_range_and_bad_width_and_event_days(self):
        self.assertEqual(run(RANGE[:2], at(9, 40))[0]['status'], 'NO_RANGE')
        self.assertEqual(run(RANGE, at(9, 40), max_range_pct=0.2)[0]['status'], 'SKIPPED_RANGE')
        self.assertEqual(run(RANGE, at(9, 40), event_dates=[DAY.isoformat()])[0]['status'], 'SKIPPED_EVENT')

    def test_close_inside_the_buffer_is_not_a_breakout(self):
        rec, texts = run(RANGE + [candle(9, 30, 24070, 24095, 24065, 24090)], at(9, 36))
        self.assertEqual((rec['status'], texts), ('ARMED', []))

    def test_cheap_iv_buys_the_atm_call_sized_to_risk(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), ctx=context())
        self.assertEqual(rec['status'], 'OPEN', rec.get('note'))
        sig, opt = rec['signal'], rec['signal']['option']
        self.assertEqual((sig['side'], sig['entry'], sig['stop'], sig['grade']), ('BUY', 24100, 24000, 'A'))
        self.assertEqual((opt['kind'], opt['expiry'], opt['legs'][0]['strike'], opt['legs'][0]['type']),
                         ('LONG', '2026-12-01', 24100, 'CE'))
        self.assertTrue(opt['stop'] < opt['net'] < opt['t1'] < opt['t2'])
        self.assertGreaterEqual(opt['lots'], 1)
        self.assertLessEqual(opt['risk'], 100000)
        text = texts[0]
        self.assertIn('NIFTY 01 DEC 24100 CE · BULLISH', text)
        self.assertIn('Grade A', text)
        self.assertIn('with the daily uptrend', text)
        self.assertIn('narrow CPR', text)
        self.assertIn('Time stop 10:35', text)
        self.assertIn('🛑 Stop: NIFTY below 24,000.0 (premium ≈₹', text)
        self.assertIn('Premiums are estimates (IV 13.0%, Δ0.5', text)
        self.assertIn('per NIFTY point', text)
        self.assertNotIn('UPSTOX', text.upper())
        self.assertLess(len(text), P.TELEGRAM_LIMIT)

    def test_rich_iv_prefers_the_itm_buy_when_it_pays_better(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), vix_level=20.0, ctx=context())
        opt = rec['signal']['option']
        self.assertEqual((opt['kind'], opt['style'], opt['legs'][0]['strike']), ('LONG', 'ITM buy', 24050))
        self.assertGreaterEqual(opt['rr'], 1.5)
        self.assertIn('prefer ITM or spreads', texts[0])

    def test_debit_spread_when_only_it_fits_the_budget(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), vix_level=20.0, ctx=context(), budget=3000)
        opt = rec['signal']['option']
        self.assertEqual(opt['kind'], 'DEBIT_SPREAD')
        self.assertEqual([(l['action'], l['strike']) for l in opt['legs']], [('BUY', 24100), ('SELL', 24300)])
        self.assertLessEqual(opt['risk'], 3000)
        self.assertIn('24100/24300 CE spread', texts[0])
        self.assertIn('max profit', texts[0])

    def test_poor_reward_to_risk_structures_are_refused(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), ctx=context(), min_option_rr=5.0)
        self.assertEqual(rec['status'], 'SKIPPED_OPTION')
        self.assertIn('reward to T2 only', rec['note'])

    def test_extreme_vix_is_skipped_with_a_note(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), vix_level=27.0)
        self.assertEqual(rec['status'], 'SKIPPED_VIX')
        self.assertIn('breakout not traded', texts[0])

    def test_against_trend_on_a_wide_cpr_day_is_graded_out(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), ctx=context(trend='down', cpr_wide=True))
        self.assertEqual(rec['status'], 'SKIPPED_WEAK')
        self.assertIn('against the daily downtrend', rec['note'])
        self.assertIn('⚪', texts[0])
        self.assertEqual(run(RANGE + [BREAKOUT], at(9, 36), ctx=context(trend='down', cpr_wide=True),
                             send_skips=False)[1], [])

    def test_resistance_inside_1r_blocks_and_between_1_2r_caps_t2(self):
        rec, _ = run(RANGE + [BREAKOUT], at(9, 36), ctx=context(high=24150.0))
        self.assertEqual(rec['status'], 'SKIPPED_WEAK')
        self.assertIn('PDH 24,150.0 only 0.5R away', rec['note'])
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), ctx=context(high=24250.0))
        self.assertEqual((rec['signal']['target2'], rec['signal']['t2_level']), (24250.0, 'PDH'))
        self.assertIn('just before PDH', texts[0])

    def test_budget_too_small_for_one_lot_is_explained(self):
        rec, texts = run(RANGE + [BREAKOUT], at(9, 36), ctx=context(), budget=300)
        self.assertEqual(rec['status'], 'SKIPPED_OPTION')
        self.assertIn('raise index_fno.risk_per_trade', texts[0])

    def test_no_price_source_means_no_trade(self):
        rec, _ = run(RANGE + [BREAKOUT], at(9, 36), ctx=context(), market=False)
        self.assertEqual(rec['status'], 'SKIPPED_OPTION')

    def test_t1_books_half_then_t2_closes_with_rupee_pnl(self):
        bars = RANGE + [BREAKOUT]
        rec, _ = run(bars, at(9, 36), ctx=context())
        lots = rec['signal']['option']['lots']
        self.assertGreaterEqual(lots, 2)
        bars += [candle(9, 35, 24100, 24210, 24090, 24190)]
        rec, texts = run(bars, at(9, 41), rec=rec, ctx=context())
        self.assertIn('T1 reached', texts[0])
        self.assertIn('Book %d of %d lots' % (lots // 2, lots), texts[0])
        self.assertEqual(rec['signal']['stop'], 24100)
        bars += [candle(9, 40, 24190, 24320, 24180, 24300)]
        rec, texts = run(bars, at(9, 46), rec=rec, ctx=context())
        self.assertIn('T2 reached', texts[0])
        self.assertEqual(rec['status'], 'CLOSED')
        self.assertGreater(rec['pnl'], 0)
        self.assertGreater(rec['charges'], 0)

    def test_stop_loss_closes_at_a_loss_near_the_planned_risk(self):
        bars = RANGE + [BREAKOUT, candle(9, 35, 24100, 24105, 23990, 23995)]
        rec, texts = run(bars, at(9, 36), ctx=context())
        rec, texts = run(bars, at(9, 41), rec=rec, ctx=context())
        self.assertIn('stop-loss hit', texts[-1])
        self.assertLess(rec['pnl'], 0)
        self.assertLess(abs(rec['pnl']), rec['signal']['option']['risk'] * 1.3)

    def test_time_stop_exits_when_t1_does_not_come(self):
        bars = RANGE + [BREAKOUT]
        rec, _ = run(bars, at(9, 36), ctx=context())
        bars += [candle(h, m, 24100, 24130, 24080, 24110) for h, m in ((9, 35), (10, 0), (10, 30))]
        rec, texts = run(bars, at(10, 36), rec=rec, ctx=context())
        self.assertIn('time stop', texts[-1])
        self.assertEqual(rec['status'], 'CLOSED')

    def test_square_off_closes_what_is_left_after_t1(self):
        bars = RANGE + [BREAKOUT, candle(9, 35, 24100, 24210, 24090, 24190)]
        rec, _ = run(bars, at(9, 36), ctx=context())
        rec, _ = run(bars, at(9, 41), rec=rec, ctx=context())
        bars += [candle(15, 10, 24180, 24200, 24170, 24190)]
        rec, texts = run(bars, at(15, 16), rec=rec, ctx=context())
        self.assertIn('square-off', texts[-1])
        self.assertEqual(rec['status'], 'CLOSED')

    def test_settings_are_validated(self):
        for bad in ({'range_minutes': 7}, {'target_r': [2, 1]}, {'contracts': {'NIFTY 50': {'lot': 0}}},
                    {'entry_deadline': '15:20'}, {'unknown': 1}, {'chain_source': 'nse'}, {'indices': ['NIFTY IT']},
                    {'event_dates': ['soon']}):
            with self.assertRaises(ValueError, msg=bad):
                F.settings(bad)
        self.assertEqual(F.settings({'contracts': {'NIFTY 50': {'lot': 75}}})['contracts']['NIFTY 50']['step'], 50)
        self.assertEqual(P.load_config(None)['index_fno']['indices'], ['NIFTY 50', 'NIFTY BANK'])


class RiskGuard(unittest.TestCase):
    def doc(self, **recs):
        return dict(indices=recs)

    def fno(self, **kw):
        return dict(F.settings(kw), _budget=3000)

    def open_trade(self, risk):
        return dict(status='OPEN', signal=dict(option=dict(risk=risk)))

    def test_one_open_trade_at_a_time(self):
        guard = F.risk_guard(self.doc(**{'NIFTY 50': self.open_trade(2500)}), self.fno(), 'NIFTY BANK')
        self.assertIn('NIFTY trade already open', guard['_block'])
        self.assertEqual(F.risk_guard(self.doc(**{'NIFTY 50': self.open_trade(2500)}), self.fno(), 'NIFTY 50'), {})

    def test_room_under_the_daily_cap_sizes_the_next_trade(self):
        guard = F.risk_guard(self.doc(**{'NIFTY 50': self.open_trade(2500)}), self.fno(max_open_trades=2), 'NIFTY BANK')
        self.assertEqual(guard, {})          # cap 6000 (2 x 3000) - 2500 at risk = 3500 room >= 3000 budget: full size
        guard = F.risk_guard(self.doc(**{'NIFTY 50': self.open_trade(4000)}), self.fno(max_open_trades=2), 'NIFTY BANK')
        self.assertEqual(guard['_budget'], 2000)
        self.assertIn('daily loss cap', guard['_budget_why'])

    def test_losses_stop_new_trades(self):
        lost = dict(status='CLOSED', pnl=-3200)
        guard = F.risk_guard(self.doc(**{'NIFTY 50': lost}), self.fno(max_daily_loss=3000), 'NIFTY BANK')
        self.assertIn('daily loss cap ₹3,000 reached (lost ₹3,200', guard['_block'])
        won = dict(status='CLOSED', pnl=900)
        self.assertEqual(F.risk_guard(self.doc(**{'NIFTY 50': won}), self.fno(), 'NIFTY BANK'), {})

    def test_second_index_breaking_out_together_is_blocked(self):
        rows = RANGE + [BREAKOUT] + [candle(int(x['time'][11:13]), int(x['time'][14:16]), x['open'] * 2.5, x['high'] * 2.5,
                                            x['low'] * 2.5, x['close'] * 2.5, symbol='INDEX:NIFTY BANK')
                                     for x in RANGE + [BREAKOUT]]
        rows.append(candle(9, 15, 13.0, 13.0, 13.0, 13.0, symbol='INDEX:INDIA VIX'))
        with tempfile.TemporaryDirectory() as d:
            base = P.load_config(None)
            base['risk_per_trade'] = 20000
            base['index_fno'] = dict(base['index_fno'], state_dir=str(Path(d) / 'fno'))
            contexts = {'NIFTY 50': dict(context(), rv20=12.0),
                        'NIFTY BANK': dict(context(high=61250.0, low=59250.0, close=60125.0), rv20=12.0,
                                           tc=60075.0, bc=60025.0)}
            doc, texts = F.tick(base, at(9, 36), calendar=DEFAULT_CALENDAR,
                                candle_loader=lambda *a, **k: [r for r in rows if F._t(r['known_at']) <= at(9, 36)],
                                context_loader=lambda *a: contexts,
                                market_factory=lambda fno, cal, vix: O.make_market(fno, cal, vix))
        self.assertEqual(doc['indices']['NIFTY 50']['status'], 'OPEN')
        self.assertEqual(doc['indices']['NIFTY BANK']['status'], 'SKIPPED_RISK')
        self.assertIn('NIFTY trade already open', texts[-1])


class Backtest(unittest.TestCase):
    def test_replay_uses_the_live_engine_without_look_ahead(self):
        from india import index_fno_backtest as B
        bank = [(F._t(x['time']), x['open'] * 2.5, x['high'] * 2.5, x['low'] * 2.5, x['close'] * 2.5) for x in RANGE]
        nifty = [(F._t(x['time']), x['open'], x['high'], x['low'], x['close']) for x in RANGE + [BREAKOUT]]
        flat = [(at(9, 35) + dt.timedelta(minutes=5 * i), 24100.0, 24130.0, 24080.0, 24110.0) for i in range(70)]
        quiet = [(OPEN + dt.timedelta(minutes=5 * i), 60100.0, 60110.0, 60090.0, 60100.0) for i in range(3, 75)]
        vix = [(OPEN + dt.timedelta(minutes=5 * i), 13.0, 13.0, 13.0, 13.0) for i in range(75)]
        intraday = {'NIFTY 50': {DAY: nifty + flat}, 'NIFTY BANK': {DAY: bank + quiet}, 'INDIA VIX': {DAY: vix}}
        bars = lambda close: [dict(date='2026-%02d-%02d' % (8 + i // 28, 1 + i % 28), open=close, high=close * 1.01,
                                   low=close * 0.99, close=close) for i in range(60)]
        future = dict(date=DAY.isoformat(), open=1.0, high=1.0, low=1.0, close=1.0)    # must never be used
        daily = {'NIFTY 50': bars(24050.0) + [future], 'NIFTY BANK': bars(60100.0) + [future],
                 'INDIA VIX': bars(13.0) + [future]}
        cfg = P.load_config(None)
        cfg['risk_per_trade'] = 50000
        cfg['index_fno'] = dict(cfg['index_fno'], min_score=0)
        rep = B.run(cfg, dt.datetime.combine(DAY + dt.timedelta(days=1), dt.time(9), IST), DEFAULT_CALENDAR,
                    daily=daily, intraday=intraday)
        self.assertEqual(rep['sessions'], 1)
        self.assertEqual(F.contexts_for(daily, DAY)['NIFTY 50']['close'], 24050.0)
        live = rep['passes']['live']
        self.assertEqual(live['summary']['trades'], 1)
        trade = live['trades'][0]
        self.assertEqual((trade['index'], trade['side'], trade['exit']), ('NIFTY 50', 'BUY', 'TIME_STOP'))
        self.assertAlmostEqual(trade['spot_r'], 0.1)
        self.assertIn('LIVE RULES', B.format_report(rep))


class Offline(unittest.TestCase):
    """The laptop has no internet until 10:15."""

    def base(self, d):
        cfg = P.load_config(None)
        cfg['risk_per_trade'] = 50000
        cfg['recorder'] = dict(cfg['recorder'], state_dir=str(Path(d) / 'rec'))
        cfg['index_fno'] = dict(cfg['index_fno'], state_dir=str(Path(d) / 'fno'), indices=['NIFTY 50'])
        return cfg

    def contexts(self):
        return {'NIFTY 50': dict(context(), rv20=12.0), 'INDIA VIX': dict(close=13.0)}

    def yahoo(self, candles):
        """A backfill loader serving only candles complete by `now`."""
        return lambda name, day, now: [dict(x, known_at=now.isoformat(), source='yahoo') for x in candles
                                       if name == 'NIFTY 50' and F._t(x['time']) + dt.timedelta(minutes=5) <= now]

    def tick(self, cfg, now, contexts=None, recorder=(), yahoo=None, error=None):
        def ctx(*a):
            if error:
                raise OSError(error)
            return contexts or self.contexts()
        doc, out = F.tick(cfg, now, DEFAULT_CALENDAR, candle_loader=lambda *a, **k: list(recorder),
                          context_loader=ctx, market_factory=lambda fno, cal, vix: O.make_market(fno, cal, vix or 13.0),
                          backfill_loader=yahoo, tagged=True)
        F.save_state(cfg['index_fno']['state_dir'], doc)
        return doc, out

    def test_brief_waits_for_the_data_and_is_sent_late(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.base(d)
            doc, out = self.tick(cfg, at(9, 6), error='no route to host')
            self.assertEqual(out, [])
            self.assertIn('no route', doc['context_error'])
            yahoo = self.yahoo(RANGE + [candle(9, 30 + 5 * i, 24040, 24070, 24020, 24050) for i in range(9)])
            doc, out = self.tick(cfg, at(10, 16), yahoo=yahoo)
            briefs = [t for _, k, t in out if k == 'brief']
            self.assertEqual(len(briefs), 1)
            self.assertIn('LATE 10:16', briefs[0])
            self.assertIn('NIFTY opening range 24,000.0-24,080.0', briefs[0])
            self.assertEqual(self.tick(cfg, at(10, 21), yahoo=yahoo)[1], [])
            self.assertEqual(F.load_state(cfg['index_fno']['state_dir'], DAY)['indices']['NIFTY 50']['status'], 'ARMED')

    def test_no_brief_after_the_entry_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([k for _, k, _ in self.tick(self.base(d), at(13, 40))[1]], [])

    def test_breakout_during_the_outage_is_not_chased(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.base(d)
            later = [candle(9, 35 + 5 * i, 24100, 24130, 24090, 24110) for i in range(8)]
            doc, out = self.tick(cfg, at(10, 16), yahoo=self.yahoo(RANGE + [BREAKOUT] + later))
            rec = doc['indices']['NIFTY 50']
            self.assertEqual(rec['status'], 'MISSED')
            self.assertIn('BUY breakout at 09:35', rec['note'])
            self.assertFalse([k for _, k, _ in out if k == 'signal'])
            self.assertTrue(doc['backfilled']['NIFTY 50'])

    def test_fresh_breakout_after_reconnecting_is_traded(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.base(d)
            quiet = [candle(9, 30 + 5 * i, 24040, 24070, 24020, 24050) for i in range(9)]     # 09:30-10:10 inside
            breakout = candle(10, 15, 24070, 24110, 24065, 24100)
            yahoo = self.yahoo(RANGE + quiet + [breakout])
            doc, _ = self.tick(cfg, at(10, 16), yahoo=yahoo)
            self.assertEqual(doc['indices']['NIFTY 50']['status'], 'ARMED')
            # The 10:15 candle closes at 10:20; the recorder gets 3 minutes first, then Yahoo fills it.
            doc, out = self.tick(cfg, at(10, 24), yahoo=yahoo)
            self.assertEqual(doc['indices']['NIFTY 50']['status'], 'OPEN')
            self.assertEqual([k for _, k, _ in out], ['signal'])

    def test_recorder_candles_win_over_yahoo(self):
        doc, by = {}, {'INDEX:NIFTY 50': [dict(RANGE[0], close=1.0)]}
        F.backfill(doc, by, ['NIFTY 50'], OPEN, at(9, 34), self.yahoo(RANGE), until=at(9, 34))
        series = by['INDEX:NIFTY 50']
        self.assertEqual([r['close'] for r in series], [1.0, 24060, 24050])
        self.assertEqual(len(doc['backfilled']['NIFTY 50']), 2)


class Outbox(unittest.TestCase):
    fno = F.settings()

    def test_failed_sends_stay_queued_and_are_marked_delayed(self):
        doc = dict(indices={})
        F.enqueue(doc, [('NIFTY 50', 'signal', '🟢 entry'), ('NIFTY 50', 'update', '🎯 T1')], at(10, 0), self.fno)

        def down(text):
            raise OSError('offline')
        self.assertEqual(F.flush(doc, at(10, 0), down), 0)
        self.assertEqual(len(doc['outbox']), 2)
        sent = []
        self.assertEqual(F.flush(doc, at(10, 4), sent.append), 2)
        self.assertTrue(sent[0].startswith('⌛ <i>Delayed 4 min'))
        self.assertTrue(sent[0].endswith('🟢 entry'))
        self.assertEqual(doc['outbox'], [])

    def test_stale_signal_is_dropped_with_its_follow_ups_and_a_note(self):
        doc = dict(indices={})
        F.enqueue(doc, [('NIFTY 50', 'signal', '🟢 entry'), (None, 'brief', '🧭 brief')], at(10, 0), self.fno)
        lost = F.enqueue(doc, [('NIFTY 50', 'update', '🎯 T1')], at(10, 11), self.fno)
        self.assertEqual(len(lost), 1)
        kinds = [m['kind'] for m in doc['outbox']]
        self.assertEqual(kinds, ['brief', 'note'])
        self.assertIn('1 options signal expired undelivered', doc['outbox'][-1]['text'])
        self.assertIn('NIFTY 10:00', doc['outbox'][-1]['text'])
        F.enqueue(doc, [('NIFTY 50', 'update', '🛑 stop')], at(10, 20), self.fno)
        self.assertNotIn('update', [m['kind'] for m in doc['outbox']])

    def test_brief_expires_at_the_entry_deadline(self):
        doc = dict(indices={})
        F.enqueue(doc, [(None, 'brief', '🧭 brief')], at(9, 6), self.fno)
        F.enqueue(doc, [], at(13, 29), self.fno)
        self.assertEqual(len(doc['outbox']), 1)
        F.enqueue(doc, [], at(13, 30), self.fno)
        self.assertEqual(doc['outbox'], [])


class Desk(unittest.TestCase):
    def test_daily_context_marks_pivots_cpr_and_trend(self):
        bars = [dict(date='2026-%02d-%02d' % (9 + i // 28, 1 + i % 28), open=100 + i, high=101 + i, low=99 + i,
                     close=100 + i) for i in range(60)]
        ctx = F.daily_context(bars)
        self.assertEqual(ctx['trend'], 'up')
        self.assertAlmostEqual(ctx['pivot'], (160 + 158 + 159) / 3, places=2)
        self.assertLessEqual(ctx['bc'], ctx['tc'])
        self.assertAlmostEqual(ctx['atr'], 2.0)
        self.assertIsNotNone(ctx['rv20'])

    def test_old_cached_levels_without_realised_vol_are_refetched(self):
        with tempfile.TemporaryDirectory() as d:
            base = P.load_config(None)
            base['index_fno'] = dict(base['index_fno'], state_dir=str(Path(d) / 'fno'))
            F.save_state(base['index_fno']['state_dir'], dict(date=DAY.isoformat(), indices={}, brief_sent='x',
                                                              context={'NIFTY 50': {'close': 1.0}}))
            fetched = []
            fresh = {'NIFTY 50': dict(context(), rv20=12.0), 'NIFTY BANK': dict(context(), rv20=18.0)}
            doc, _ = F.tick(base, at(9, 6), calendar=DEFAULT_CALENDAR, candle_loader=lambda *a, **k: [],
                            context_loader=lambda *a: fetched.append(1) or fresh)
            self.assertEqual(fetched, [1])
            self.assertEqual(doc['context']['NIFTY BANK']['rv20'], 18.0)

    def test_brief_goes_out_once_before_the_open(self):
        with tempfile.TemporaryDirectory() as d:
            base = P.load_config(None)
            base['recorder'] = dict(base['recorder'], state_dir=str(Path(d) / 'rec'))
            base['index_fno'] = dict(base['index_fno'], state_dir=str(Path(d) / 'fno'))
            contexts = {'NIFTY 50': context(), 'NIFTY BANK': context(close=52000.0, high=52500.0, low=51500.0),
                        'INDIA VIX': dict(close=13.4)}
            kw = dict(calendar=DEFAULT_CALENDAR, candle_loader=lambda *a, **k: [],
                      context_loader=lambda *a: contexts,
                      market_factory=lambda fno, cal, vix: O.make_market(fno, cal, vix, None))
            doc, texts = F.tick(base, at(9, 6), **kw)
            self.assertEqual(len(texts), 1)
            self.assertIn('Index options desk', texts[0])
            self.assertIn('VIX 13.4: premiums cheap', texts[0])
            self.assertIn('Expiry used: 01 Dec', texts[0])
            self.assertIn('PDH 24,500.0', texts[0])
            F.save_state(base['index_fno']['state_dir'], doc)
            self.assertEqual(F.tick(base, at(9, 8), **kw)[1], [])


class Gating(unittest.TestCase):
    def config(self, d, **extra):
        conf = Path(d) / 'picks.json'
        conf.write_text(json.dumps(dict({'state_dir': str(Path(d) / 'picks'), 'risk_per_trade': 50000,
                                         'recorder': {'state_dir': str(Path(d) / 'rec')},
                                         'index_fno': {'state_dir': str(Path(d) / 'fno'), 'indices': ['NIFTY 50']}},
                                        **extra)))
        return conf

    def test_fno_main_is_muted_until_switched_on(self):
        with tempfile.TemporaryDirectory() as d:
            conf = self.config(d)
            bars = RANGE + [BREAKOUT]
            sent = []
            env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
            args = ['--config', str(conf), '--as-of', at(9, 36).isoformat()]
            contexts = {'NIFTY 50': context(), 'NIFTY BANK': None, 'INDIA VIX': dict(close=13.0)}
            with mock.patch.object(F, 'load_candles', lambda *a, **k: bars), \
                    mock.patch.object(F, 'fetch_context', lambda *a: contexts), \
                    mock.patch.object(F, 'yahoo_candles', lambda *a: []), \
                    mock.patch.object(P, 'send_telegram', lambda t, c, text: sent.append(text)), \
                    mock.patch('sys.stdout'), mock.patch('sys.stderr'), mock.patch.dict('os.environ', env):
                self.assertEqual(F.main(args), 0)
                self.assertEqual(sent, [])
                self.assertFalse((Path(d) / 'fno').exists())
                S.write(Path(d) / S.FILENAME, 'index_fno', True)
                self.assertEqual(F.main(args), 0)
                self.assertEqual(F.main(args), 0)      # saved state: not sent twice
            self.assertEqual(len(sent), 2)                 # the late brief, then the signal
            self.assertIn('LATE 09:36', sent[0])
            self.assertIn('NIFTY 01 DEC 24100 CE · BULLISH', sent[1])
            self.assertIn('at ≈₹', sent[1])
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


class OfflineScheduler(unittest.TestCase):
    def test_morning_alert_waits_for_the_connection_instead_of_spending_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = P.load_config(None)
            cfg['state_dir'] = d
            clock, calls, logs = [at(8, 40)], [], []

            def fake_main(args):
                if '--settle' not in args:
                    calls.append(clock[0].strftime('%H:%M'))
                    (Path(d) / ('sent-%s' % DAY.isoformat())).write_text('x')

            def step(_):
                clock[0] += dt.timedelta(minutes=5)
            P.serve([], cfg, DEFAULT_CALENDAR, now_fn=lambda: clock[0], sleep=step, main_fn=fake_main,
                    iterations=24, log=logs.append, gold_fn=lambda a: None,
                    online_fn=lambda: clock[0] >= at(10, 15))
        self.assertEqual(calls, ['10:15'])
        self.assertEqual(len([m for m in logs if 'morning alert waits for the connection' in m]), 1)

    def test_internet_check_reports_failure_as_offline(self):
        with mock.patch('india.picks.socket.create_connection', side_effect=OSError('no route')):
            self.assertFalse(P.internet_ok())


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
