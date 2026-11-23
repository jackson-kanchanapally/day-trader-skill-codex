"""NIFTY / BANK NIFTY opening-range futures signals to Telegram (paper research only).

Rules, per index, once per NSE session:

  1. Opening range: high and low of the first `range_minutes` (default 15,
     09:15-09:30) from the recorder's sampled five-minute index candles. Every
     candle of the range must be usable, else no signal that day.
  2. Skip the day when the range is narrower than `min_range_pct` (chop) or
     wider than `max_range_pct` (a gap or news day), or when India VIX is above
     `max_vix` at the breakout.
  3. Signal on the first five-minute CLOSE beyond the range before
     `entry_deadline`: above it is BUY (long futures), below it is SELL.
     A breakout seen more than `fresh_minutes` late (the switch was off, or the
     data was late) is recorded as missed, not sent.
  4. Entry is that close. Stop is the other side of the range. Half is booked
     at T1 (1R) and its stop moves to the entry; the rest exits at T2 (2R),
     the stop, or `square_off`.

The candles are sampled from NSE's delayed snapshot (one to three minutes
behind), not exchange candles, and the levels are on the spot index: futures
trade at a premium, so shift every level by the current basis. A stop and a
target inside the same candle count as the stop. Nothing here places orders.

    python3 -m india.index_fno --config config/picks.json --dry-run
"""
import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import sys
import tempfile

from . import alert_switches as S
from . import picks as P
from .data import IST, timestamp
from .market import DEFAULT_CALENDAR, Calendar
from .recorder import load_candles

DEFAULTS = dict(
    state_dir='var/state/index_fno',
    indices=['NIFTY 50', 'NIFTY BANK'],
    vix_index='INDIA VIX',
    range_minutes=15,
    entry_deadline='13:30',
    square_off='15:15',
    min_range_pct=0.15,
    max_range_pct=1.2,
    max_vix=25.0,            # null disables the VIX filter
    target_r=[1.0, 2.0],
    fresh_minutes=10,
    lot_size={},             # e.g. {"NIFTY 50": 65}; adds rupee risk per lot to the message
)
FINAL = frozenset(('NO_RANGE', 'SKIPPED_RANGE', 'SKIPPED_VIX', 'NO_BREAKOUT', 'MISSED', 'CLOSED'))
SHORT = {'NIFTY 50': 'NIFTY', 'NIFTY BANK': 'BANKNIFTY', 'NIFTY FIN SERVICE': 'FINNIFTY'}
SETTLE = dt.timedelta(minutes=3)      # wait this long after a window for the recorder to finalize it


def settings(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError('index_fno settings must be an object')
    supplied = value or {}
    unknown = set(supplied) - set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown index_fno settings: ' + ', '.join(sorted(unknown)))
    cfg = dict(DEFAULTS, **supplied)
    if not isinstance(cfg['state_dir'], str) or not cfg['state_dir'].strip():
        raise ValueError('index_fno.state_dir must be a nonempty path')
    names = cfg['indices']
    if (not isinstance(names, list) or not names or len(names) > 5 or len(set(names)) != len(names)
            or any(not isinstance(n, str) or not n.strip() for n in names)):
        raise ValueError('index_fno.indices must list 1-5 unique NSE index names')
    if not isinstance(cfg['vix_index'], str) or not cfg['vix_index'].strip():
        raise ValueError('index_fno.vix_index must be an NSE index name')
    if cfg['range_minutes'] not in (5, 10, 15, 30):
        raise ValueError('index_fno.range_minutes must be 5, 10, 15 or 30')
    for key in ('entry_deadline', 'square_off'):
        P.hhmm(cfg[key])
    if not P.hhmm(cfg['entry_deadline']) < P.hhmm(cfg['square_off']) <= dt.time(15, 25):
        raise ValueError('index_fno needs entry_deadline < square_off <= 15:25')
    for key in ('min_range_pct', 'max_range_pct'):
        if not isinstance(cfg[key], (int, float)) or isinstance(cfg[key], bool) or not 0 < cfg[key] <= 5:
            raise ValueError('index_fno.%s must be a number above 0 and at most 5' % key)
    if cfg['min_range_pct'] >= cfg['max_range_pct']:
        raise ValueError('index_fno.min_range_pct must be below max_range_pct')
    if cfg['max_vix'] is not None and (not isinstance(cfg['max_vix'], (int, float))
                                       or isinstance(cfg['max_vix'], bool) or not 5 <= cfg['max_vix'] <= 100):
        raise ValueError('index_fno.max_vix must be null or a number from 5 to 100')
    t = cfg['target_r']
    if (not isinstance(t, list) or len(t) != 2 or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in t)
            or not 0 < t[0] < t[1] <= 5):
        raise ValueError('index_fno.target_r must be [T1, T2] with 0 < T1 < T2 <= 5')
    if not isinstance(cfg['fresh_minutes'], int) or isinstance(cfg['fresh_minutes'], bool) \
            or not 5 <= cfg['fresh_minutes'] <= 30:
        raise ValueError('index_fno.fresh_minutes must be a whole number from 5 to 30')
    lots = cfg['lot_size']
    if not isinstance(lots, dict) or any(k not in names or not isinstance(v, int) or isinstance(v, bool) or v <= 0
                                         for k, v in lots.items()):
        raise ValueError('index_fno.lot_size maps configured index names to positive whole lot sizes')
    cfg['indices'], cfg['target_r'], cfg['lot_size'] = list(names), [float(x) for x in t], dict(lots)
    return cfg


# --------------------------------------------------------------------------- rules

def _num(x, places=1):
    return '{:,.{p}f}'.format(x, p=places)


def _t(iso):
    return timestamp(iso).astimezone(IST)


def _price(c, key):
    return c[key] if c.get(key) is not None else c['close']


def _result(sig):
    sign = 1 if sig['side'] == 'BUY' else -1
    points = sum(f * sign * (px - sig['entry']) for f, px in sig['legs'])
    return round(points, 2), round(points / sig['risk'], 2)


def process(name, rec, candles, vix, now, opening, cfg):
    """Advance one index's state with candles known at `now`. Returns Telegram texts.

    `candles` are this index's usable sampled candles for the day, oldest first;
    `vix` the India VIX ones. `rec` is updated in place."""
    texts = []
    if rec.get('status') in FINAL:
        return texts
    day = opening.date()
    range_end = opening + dt.timedelta(minutes=cfg['range_minutes'])
    label = SHORT.get(name, name)
    if rec.get('status', 'WAITING') == 'WAITING':
        if now < range_end + SETTLE:
            rec['status'] = 'WAITING'
            return texts
        inside = [c for c in candles if opening <= _t(c['time']) < range_end]
        if len(inside) < cfg['range_minutes'] // 5:
            rec.update(status='NO_RANGE', note='%d of %d opening-range candles usable' % (
                len(inside), cfg['range_minutes'] // 5))
            return texts
        high, low = max(_price(c, 'high') for c in inside), min(_price(c, 'low') for c in inside)
        width = (high - low) / ((high + low) / 2) * 100
        rec.update(status='ARMED', range=dict(high=high, low=low, width_pct=round(width, 3),
                                              start=opening.isoformat(), end=range_end.isoformat()))
        if not cfg['min_range_pct'] <= width <= cfg['max_range_pct']:
            rec.update(status='SKIPPED_RANGE', note='opening range %.2f%% is outside %.2f-%.2f%%' % (
                width, cfg['min_range_pct'], cfg['max_range_pct']))
            return texts
    after = lambda c: rec.get('checked_until') is None or c['time'] > rec['checked_until']
    if rec['status'] == 'ARMED':
        deadline = dt.datetime.combine(day, P.hhmm(cfg['entry_deadline']), IST)
        for c in candles:
            start = _t(c['time'])
            if start < range_end or not after(c):
                continue
            end = start + dt.timedelta(minutes=5)
            if end > deadline:
                rec['status'] = 'NO_BREAKOUT'
                break
            rec['checked_until'] = c['time']
            rng = rec['range']
            side = 'BUY' if c['close'] > rng['high'] else 'SELL' if c['close'] < rng['low'] else None
            if not side:
                continue
            known = [v for v in vix if _t(v['time']) <= start]
            level = known[-1]['close'] if known else None
            if cfg['max_vix'] is not None and level is not None and level > cfg['max_vix']:
                rec.update(status='SKIPPED_VIX', note='India VIX %.2f above %.2f at the breakout' % (level, cfg['max_vix']))
                break
            if now - end > dt.timedelta(minutes=cfg['fresh_minutes']):
                rec.update(status='MISSED', note='%s breakout at %s seen at %s' % (
                    side, end.strftime('%H:%M'), now.strftime('%H:%M')))
                break
            entry = c['close']
            stop = rng['low'] if side == 'BUY' else rng['high']
            risk = abs(entry - stop)
            sign = 1 if side == 'BUY' else -1
            t1, t2 = (entry + sign * r * risk for r in cfg['target_r'])
            rec.update(status='OPEN', signal=dict(
                side=side, time=end.isoformat(), entry=entry, stop=stop, initial_stop=stop, risk=round(risk, 2),
                target1=round(t1, 2), target2=round(t2, 2), vix=level, t1_hit=False, legs=[], events=[]))
            lot = cfg['lot_size'].get(name)
            texts.append(
                '%s <b>%s F&amp;O signal: %s</b> · %s IST\n'
                'Five-minute close %s the %s-%s range (%s-%s, %.2f%%).\n'
                'Entry ~%s · Stop %s (%s pts%s)\n'
                'T1 %s (%gR: book half, stop to entry) · T2 %s (%gR)\n'
                'Exit by %s.%s\n'
                '<i>Levels are on the spot index from NSE\'s delayed snapshot (1-3 min behind). Futures trade at a '
                'premium: shift each level by the current basis. Paper research, not advice.</i>' % (
                    '📈' if side == 'BUY' else '📉', html.escape(label), side, end.strftime('%H:%M'),
                    'above' if side == 'BUY' else 'below', opening.strftime('%H:%M'), range_end.strftime('%H:%M'),
                    _num(rng['low']), _num(rng['high']), rng['width_pct'],
                    _num(entry), _num(stop), _num(risk), (', ₹%s per lot of %d' % (P.inr(risk * lot, 0), lot)) if lot else '',
                    _num(t1), cfg['target_r'][0], _num(t2), cfg['target_r'][1], cfg['square_off'],
                    (' India VIX %.2f.' % level) if level is not None else ' India VIX unavailable.'))
            break
        if rec['status'] == 'ARMED' and now >= deadline + SETTLE:
            rec['status'] = 'NO_BREAKOUT'
    if rec['status'] == 'OPEN':
        sig = rec['signal']
        sign = 1 if sig['side'] == 'BUY' else -1
        square = dt.datetime.combine(day, P.hhmm(cfg['square_off']), IST)
        head = '%s %s' % (html.escape(label), sig['side'])

        def close(kind, price, at, text):
            remaining = 1 - sum(f for f, _ in sig['legs'])
            sig['legs'].append((remaining, price))
            points, r = _result(sig)
            sig['events'].append(dict(kind=kind, price=price, time=at.isoformat()))
            rec.update(status='CLOSED', points=points, r=r)
            texts.append('%s <b>%s: %s</b> · %s IST\n%s\nResult %+.1f pts (%+.2fR) on the index, before costs.' % (
                {'STOP': '🛑', 'BREAKEVEN': '⚖️', 'TARGET2': '🏁', 'SQUARE_OFF': '⏰'}[kind], head,
                {'STOP': 'stop-loss hit', 'BREAKEVEN': 'breakeven stop hit', 'TARGET2': 'T2 reached',
                 'SQUARE_OFF': 'square-off'}[kind], at.strftime('%H:%M'), text, points, r))

        for c in candles:
            start = _t(c['time'])
            if start < _t(sig['time']) or not after(c):
                continue
            rec['checked_until'] = c['time']
            end = start + dt.timedelta(minutes=5)
            fav = _price(c, 'high') if sign > 0 else _price(c, 'low')
            adv = _price(c, 'low') if sign > 0 else _price(c, 'high')
            if sign * (adv - sig['stop']) <= 0:
                kind = 'BREAKEVEN' if sig['t1_hit'] else 'STOP'
                close(kind, sig['stop'], end, 'Index reached %s through the stop %s. Exit the %s.' % (
                    _num(adv), _num(sig['stop']), 'rest' if sig['t1_hit'] else 'position'))
                break
            if not sig['t1_hit'] and sign * (fav - sig['target1']) >= 0:
                sig['t1_hit'] = True
                sig['legs'].append((0.5, sig['target1']))
                sig['events'].append(dict(kind='TARGET1', price=sig['target1'], time=end.isoformat()))
                sig['stop'] = sig['entry']
                texts.append('🎯 <b>%s: T1 reached</b> · %s IST\nIndex %s through T1 %s. Book half and move the stop '
                             'to the entry %s; the rest aims for T2 %s.' % (
                                 head, end.strftime('%H:%M'), _num(fav), _num(sig['target1']),
                                 _num(sig['entry']), _num(sig['target2'])))
            if sig['t1_hit'] and sign * (fav - sig['target2']) >= 0:
                close('TARGET2', sig['target2'], end, 'Index %s through T2 %s. Exit the rest.' % (
                    _num(fav), _num(sig['target2'])))
                break
            if end >= square:
                close('SQUARE_OFF', c['close'], end, 'Exit what is left at about %s.' % _num(c['close']))
                break
        if rec['status'] == 'OPEN' and now >= square + dt.timedelta(minutes=15):
            last = candles[-1]['close'] if candles else sig['entry']
            close('SQUARE_OFF', last, square, 'No usable candle at %s; marked at the last close %s.' % (
                cfg['square_off'], _num(last)))
    return texts


# --------------------------------------------------------------------------- state and entry point

def load_state(state_dir, day):
    path = Path(state_dir) / ('signals-%s.json' % day.isoformat())
    if path.is_file():
        return json.loads(path.read_text())
    return dict(date=day.isoformat(), indices={})


def save_state(state_dir, doc):
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(root), prefix='.signals-')
    with os.fdopen(fd, 'w') as fh:
        json.dump(doc, fh, indent=2, default=str)
    os.replace(temporary, root / ('signals-%s.json' % doc['date']))


def tick(cfg, now, calendar=None, candle_loader=None):
    """One pass over every configured index. Returns (state, texts); saves nothing."""
    fno = cfg['index_fno']
    calendar = calendar or DEFAULT_CALENDAR
    hours = calendar.hours(now.date(), 'NSE')
    if not hours:
        return None, []
    opening = dt.datetime.combine(now.date(), hours[0], IST)
    rows = (candle_loader or load_candles)(cfg['recorder']['state_dir'], now.date(), as_of=now.isoformat(), usable_only=True)
    by = {}
    for row in rows:
        by.setdefault(row['symbol'], []).append(row)
    for series in by.values():
        series.sort(key=lambda r: r['time'])
    doc = load_state(fno['state_dir'], now.date())
    texts = []
    for name in fno['indices']:
        rec = doc['indices'].setdefault(name, dict(status='WAITING'))
        texts += process(name, rec, by.get('INDEX:' + name, []), by.get('INDEX:' + fno['vix_index'], []),
                         now, opening, fno)
    doc['checked_at'] = now.isoformat()
    return doc, texts


def needed_indices(cfg):
    fno = cfg['index_fno']
    return list(dict.fromkeys(fno['indices'] + [fno['vix_index']]))


def main(argv=None):
    ap = argparse.ArgumentParser(description='NIFTY / BANK NIFTY opening-range futures signals (paper research only).')
    ap.add_argument('--config', help='picks JSON with optional "index_fno" and "recorder" objects')
    ap.add_argument('--env-file', default='var/telegram.env')
    ap.add_argument('--dry-run', action='store_true', help='print instead of sending; ignores the switch; saves nothing')
    ap.add_argument('--as-of', help='pretend it is this time (ISO 8601 with offset)')
    a = ap.parse_args(argv)
    log = lambda msg: print(msg, file=sys.stderr)
    try:
        cfg = P.load_config(a.config)
        if not a.dry_run and not S.enabled(S.path_for(cfg), 'index_fno'):
            print(json.dumps({'status': 'MUTED', 'reason': 'index F&O alerts are switched off in the dashboard'}))
            return 0
        now = dt.datetime.fromisoformat(a.as_of).astimezone(IST) if a.as_of else dt.datetime.now(IST)
        calendar = Calendar.load(cfg['calendar']) if cfg['calendar'] else DEFAULT_CALENDAR
        doc, texts = tick(cfg, now, calendar)
        if doc is None:
            print(json.dumps({'status': 'SKIPPED', 'reason': 'not an NSE trading day'}))
            return 0
        if a.dry_run:
            print('\n\n'.join(texts) or json.dumps({k: v.get('status') for k, v in doc['indices'].items()}))
            return 0
        env = dict(P.read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith('TELEGRAM_')})
        token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        if texts and not (token and chat):
            raise ValueError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (environment or %s)' % a.env_file)
        # Save first: a failed send is logged once instead of repeating every tick.
        save_state(cfg['index_fno']['state_dir'], doc)
        for text in texts:
            try:
                P.send_telegram(token, chat, text)
            except (ValueError, OSError) as exc:
                log('index F&O send failed: %s' % exc)
        if texts:
            log('index F&O: sent %d message(s)' % len(texts))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
