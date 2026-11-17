"""Results, board meetings and ex-dates that make a daily setup unsafe to trade.

A setup is built on the previous session's candle. If the company announces
results (or another price-moving decision) on the signal day or before the
trade-day close, the next session can gap far past an ATR-sized stop, and the
plan's prices no longer mean anything. Ex-dates of splits, bonuses and similar
actions change the price scale overnight.

Sources (public NSE JSON, no login, read-only):

    event-calendar          board meetings with their purpose, by date range
    corporates-corporateActions   ex-dates with their subject, by date range

check() classifies each event for one alert window:

    block   results / fund raising / buyback / split / bonus / merger ...
            board meeting from the signal day through the trade day, or a
            split / bonus / rights / demerger ex-date on the trade day
    flag    any other board meeting in the window (e.g. "Other business
            matters"), or a dividend / other ex-date on the trade day

When NSE is unreachable the last fetched calendar is used if it covers the
window; otherwise the result says so and the alert carries a warning. The
check never invents an event.
"""
import datetime as dt
import json
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://www.nseindia.com/api/'
CALENDAR_PATH = 'event-calendar?index=equities&from_date=%s&to_date=%s'
ACTIONS_PATH = 'corporates-corporateActions?index=equities&from_date=%s&to_date=%s'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-event-calendar',
}
# Board-meeting purposes that can move the price far beyond a day's ATR.
MATERIAL = ('result', 'fund rais', 'raising', 'buyback', 'buy back', 'split', 'bonus', 'merger',
            'amalgamation', 'demerger', 'scheme', 'rights', 'delist', 'preferential', 'qip',
            'capital reduction', 'consolidation', 'acquisition', 'divest')
# Ex-date subjects that change the price scale overnight.
RESCALING = ('split', 'sub-division', 'subdivision', 'bonus', 'rights', 'demerger', 'consolidation',
             'capital reduction', 'spin off', 'spin-off')


def _query(d):
    return d.strftime('%d-%m-%Y')


def _date(text):
    return dt.datetime.strptime(text.strip(), '%d-%b-%Y').date()


def http_json(url, timeout=20):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(10_000_001))


def parse_calendar(doc):
    if not isinstance(doc, list):
        raise ValueError('unexpected NSE event-calendar response')
    out = []
    for row in doc:
        try:
            out.append({'symbol': str(row['symbol']).strip().upper(), 'date': _date(row['date']).isoformat(),
                        'kind': 'board', 'purpose': str(row.get('purpose') or '').strip(),
                        'detail': str(row.get('bm_desc') or '').strip()[:300]})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue    # one malformed row must not hide the others
    return out


def parse_actions(doc):
    if not isinstance(doc, list):
        raise ValueError('unexpected NSE corporate-actions response')
    out = []
    for row in doc:
        try:
            if (row.get('series') or 'EQ').strip() != 'EQ':
                continue
            out.append({'symbol': str(row['symbol']).strip().upper(), 'date': _date(row['exDate']).isoformat(),
                        'kind': 'ex', 'purpose': str(row.get('subject') or '').strip(), 'detail': ''})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def fetch(start, end, get=http_json):
    """All board meetings and EQ ex-dates dated start..end (inclusive)."""
    window = (_query(start), _query(end))
    return parse_calendar(get(BASE + CALENDAR_PATH % window)) + parse_actions(get(BASE + ACTIONS_PATH % window))


def classify(event):
    text = event['purpose'].lower()
    if event['kind'] == 'board':
        return 'block' if any(k in text for k in MATERIAL) else 'flag'
    return 'block' if any(k in text for k in RESCALING) else 'flag'


def describe(event):
    when = dt.date.fromisoformat(event['date']).strftime('%d %b')
    if event['kind'] == 'board':
        return 'Board meeting %s: %s' % (when, event['purpose'] or 'purpose not stated')
    return 'Ex-date %s: %s' % (when, event['purpose'] or 'corporate action')


def load(state_dir, start, end, fetch_fn=None, now=None):
    """(events, source, warning) for start..end, using a cache when NSE is unreachable."""
    fetch_fn = fetch_fn or fetch
    cache = Path(state_dir) / 'events-cache.json'
    try:
        events = fetch_fn(start, end)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({'start': start.isoformat(), 'end': end.isoformat(),
                                         'fetched_at': (now or dt.datetime.now()).isoformat(),
                                         'events': events}))
        except OSError:
            pass
        return events, 'NSE event calendar and corporate actions', None
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) or type(exc).__name__
    try:
        doc = json.loads(cache.read_text())
        if doc['start'] <= start.isoformat() and doc['end'] >= end.isoformat():
            return doc['events'], 'cached NSE events (fetched %s)' % doc['fetched_at'][:16], \
                'NSE events unreachable (%s); using the calendar fetched %s' % (reason, doc['fetched_at'][:16])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None, None, ('Results/events calendar unavailable (%s); check each stock for results, '
                        'board meetings and ex-dates before entry' % reason)


def check(symbols, signal_day, trade_day, events):
    """{symbol: {'block': [...], 'flag': [...]}} for the symbols with an event in the window.

    Board meetings from the signal day through the trade day count (results
    announced after the signal close move the next open). Ex-dates count only on
    the trade day: an earlier one is already in the signal candle.
    """
    names = {s.rsplit('.', 1)[0].upper(): s for s in symbols}
    lo, hi = signal_day.isoformat(), trade_day.isoformat()
    found = {}
    for e in events or []:
        sym = names.get(e['symbol'])
        if not sym:
            continue
        relevant = lo <= e['date'] <= hi if e['kind'] == 'board' else e['date'] == hi
        if relevant:
            slot = found.setdefault(sym, {'block': [], 'flag': []})
            text = describe(e)
            if text not in slot['block'] + slot['flag']:
                slot[classify(e)].append(text)
    return found
