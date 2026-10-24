"""Research cautions and current-session gates; never an order clearance engine."""
import datetime as dt
import math
from urllib.parse import urlsplit

from india.data import IST, timestamp
from india.market import DEFAULT_CALENDAR


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def https(value):
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
    except (TypeError, ValueError, AttributeError):
        return False


def completed_session(now, exchange='NSE'):
    now = now.astimezone(IST)
    day = now.date()
    if now.time() < dt.time(15, 30):
        day -= dt.timedelta(days=1)
    for _ in range(40):
        if DEFAULT_CALENDAR.trading_day(day, exchange):
            return day.isoformat()
        day -= dt.timedelta(days=1)
    raise ValueError('No covered completed session')


def market_clock(now, exchange='NSE'):
    now = now.astimezone(IST)
    try:
        trading = DEFAULT_CALENDAR.trading_day(now.date(), exchange)
        expected = completed_session(now, exchange)
    except ValueError:
        return dict(status='CALENDAR_UNAVAILABLE', label='Calendar coverage unavailable', expected_session=None,
                    entry_window_open=False, observed_at=now.isoformat())
    entry = trading and dt.time(9, 30) <= now.time() < dt.time(15)
    opened = trading and dt.time(9, 15) <= now.time() < dt.time(15, 30)
    status = 'ENTRY_WINDOW' if entry else ('OUTSIDE_ENTRY_WINDOW' if opened else 'MARKET_CLOSED')
    return dict(status=status, label={'ENTRY_WINDOW': 'Default entry window open',
                'OUTSIDE_ENTRY_WINDOW': 'Outside default entry window', 'MARKET_CLOSED': 'Market closed'}[status],
                expected_session=expected, entry_window_open=entry, observed_at=now.isoformat(),
                note='Configured regular-session calendar; default entries 09:30–15:00 IST. Special sessions unsupported.')


def data_health(cutoff, candle_date, now, exchange='NSE'):
    market = market_clock(now, exchange)
    age = (now-timestamp(cutoff)).total_seconds()
    status = ('FUTURE' if age < 0 else 'UNAVAILABLE' if market['expected_session'] is None else
              'CURRENT_DAILY' if candle_date == market['expected_session'] else 'STALE')
    return dict(status=status, candle_session=candle_date, expected_session=market['expected_session'],
                scan_age_seconds=max(0, age), scan_cutoff=cutoff, observed_at=now.isoformat(),
                intraday_entry_evidence=False, live_feed=False)


def caution_flags(row, metrics=None):
    """Explicit review thresholds, not exchange rules or calibrated predictions."""
    flags = []
    def add(code, label, evidence):
        flags.append(dict(code=code, label=label, evidence=evidence, severity='CAUTION'))
    if finite(row.get('ma20')) and row['ma20'] > 0:
        extension = (row['price']/row['ma20']-1)*100
        if extension >= 10:
            add('EXTENDED', 'Extended above MA20', '%.2f%% above MA20; review threshold ≥10%%.' % extension)
    if finite(row.get('rsi')) and row['rsi'] >= 70:
        add('HIGH_RSI', 'High RSI', 'RSI14 %.2f; review threshold ≥70.' % row['rsi'])
    if finite(row.get('range5_pct')) and row['range5_pct'] >= 10:
        add('WIDE_RANGE', 'Wide five-close range', '%.2f%%; review threshold ≥10%%.' % row['range5_pct'])
    if finite(row.get('volume_x')) and row['volume_x'] >= 3:
        add('VOLUME_SURGE', 'Exceptional volume', '%.2f× recent average; review threshold ≥3×.' % row['volume_x'])
    turnover = row['price']*row['volume']
    if turnover < 1e7:
        add('LOW_TURNOVER', 'Lower daily participation', 'Approximate daily turnover below ₹1 crore; this does not measure spread or book depth.')
    change = (metrics or {}).get('change_pct')
    if finite(change) and abs(change) >= 8:
        add('LARGE_MOVE', 'Large daily move', '%.2f%% close-to-close; review threshold |move| ≥8%%. Price-band status requires separate evidence.' % change)
    return flags


def candle_metrics(candles):
    if len(candles) < 2:
        return {}
    latest, previous = candles[-1], candles[-2]
    trs = [max(c['high']-c['low'], abs(c['high']-p['close']), abs(c['low']-p['close']))
           for p, c in zip(candles, candles[1:])]
    atr = sum(trs[:14])/14 if len(trs) >= 14 else None
    if atr is not None:
        for value in trs[14:]:
            atr = (atr*13+value)/14
    return dict(previous_close=previous['close'], change_pct=(latest['close']/previous['close']-1)*100,
                gap_pct=(latest['open']/previous['close']-1)*100,
                day_range_pct=(latest['high']-latest['low'])/previous['close']*100,
                atr14=atr, atr_pct=atr/latest['close']*100 if atr is not None else None,
                basis='Same bound daily snapshot; Wilder ATR14 seeded with the first 14 true ranges.')


def execution_quote(document, symbol, now):
    absent = dict(status='UNAVAILABLE', reason='No valid dated bid/ask, price-band and halt evidence supplied.',
                  bid=None, ask=None, spread_bps=None, source=None)
    try:
        if (document.get('currency') != 'INR' or document.get('paper') is not True
                or document.get('execution_enabled') is not False):
            return absent
        matches = [q for q in document['quotes'] if q.get('symbol') == symbol]
        if len(matches) != 1:
            return absent
        q = matches[0]
        observed = timestamp(q['observed_at'])
        age = (now-observed).total_seconds()
        if (not https(q.get('source')) or q.get('fixture') and not symbol.startswith('DEMO.')
                or not all(finite(q.get(k)) and q[k] > 0 for k in ('bid', 'ask', 'lower_price_band', 'upper_price_band'))
                or not q['lower_price_band'] <= q['bid'] <= q['ask'] <= q['upper_price_band']
                or not isinstance(q.get('halted'), bool)):
            return absent
        mid = (q['ask']+q['bid'])/2
        return dict(status='CAPTURED' if 0 <= age <= 60 and observed.date() == now.astimezone(IST).date() else 'STALE',
                    reason='Supplied snapshot, not a streaming feed. Maximum quote age: 60 seconds.',
                    bid=q['bid'], ask=q['ask'], spread_bps=(q['ask']-q['bid'])/mid*10000,
                    lower_price_band=q['lower_price_band'], upper_price_band=q['upper_price_band'],
                    halted=q['halted'], observed_at=q['observed_at'], source=q['source'],
                    instrument_type=q.get('instrument_type', 'UNKNOWN'))
    except (KeyError, ValueError, TypeError, AttributeError):
        return absent


def decision_state(row, cutoff, now, quote=None, research=None):
    exchange = 'NSE' if row['symbol'].endswith('.NS') else 'BSE'
    market = market_clock(now, exchange)
    health = data_health(cutoff, row['as_of'][:10], now, exchange)
    quote = quote or execution_quote({}, row['symbol'], now)
    checks = [dict(label='Daily data', status=health['status'],
                   reason='Daily candles support research. A separate completed intraday entry is required.'),
              dict(label='Entry window now', status='OPEN' if market['entry_window_open'] else 'CLOSED', reason=market['label']),
              dict(label='Completed-bar entry', status='REQUIRED', reason='The daily match does not include a current entry signal.'),
              dict(label='Spread and price bands', status=quote['status'], reason=quote['reason']),
              dict(label='Tick metadata', status='REQUIRED', reason='Verify dated INR metadata for the chosen entry session.'),
              dict(label='Capital and costs', status='REQUIRED', reason='Explicit account limits and a cost-inclusive exact plan are required.'),
              dict(label='Independent risk', status='NOT_CLEARED', reason='A historical finding cannot clear a new order.')]
    blockers = []
    if health['status'] != 'CURRENT_DAILY':
        blockers.append('Daily evidence is stale, future-dated or outside calendar coverage.')
    if not market['entry_window_open']:
        blockers.append(market['label']+'.')
    if quote['status'] != 'CAPTURED':
        blockers.append('Fresh spread, price-band and halt evidence is unavailable.')
    elif quote['halted']:
        blockers.append('Supplied snapshot reports a trading halt.')
    elif quote['spread_bps'] > 20:
        blockers.append('Spread exceeds the 20 bps research caution threshold.')
    if quote['status'] == 'CAPTURED' and abs(quote['ask']-quote['upper_price_band']) < 1e-9:
        blockers.append('Ask is at the supplied upper price band. Executable volume and queue position remain unverified.')
    blockers.append('Current completed-bar entry, exact plan and independent risk clearance are required.')
    return dict(setup=dict(status='MATCHED', score=row['score']),
                tradeability=dict(status='BLOCKED' if health['status'] != 'CURRENT_DAILY' or not market['entry_window_open']
                                  or quote['status'] == 'CAPTURED' and quote.get('halted') else 'INCOMPLETE', reasons=blockers),
                risk=dict(status='NOT_CLEARED'), checks=checks, market=market, data=health,
                quote=quote, research=research or [], can_prepare_order=False, execution_enabled=False)
