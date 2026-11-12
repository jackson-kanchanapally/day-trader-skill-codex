"""Read-only client for NSE's official MCP servers (https://www.nseindia.com/nse-mcp).

Streamable HTTP JSON-RPC using only the standard library; no login or key.
The service offers end-of-day bars (about five years) and a delayed snapshot.
It has no intraday candles, so it can never feed the five-minute opening-range
engine. Prices are unadjusted: a split or bonus inside a requested window
fails closed instead of producing distorted daily setups.

NSE states that this data is for informational, non-commercial use, is not
meant for real-time trading, and grants no right to train AI systems. This
adapter is for local paper research only and does not establish permission
for any other use.
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .data import Bar, IST, symbol_name, validate

HOST = 'mcp.nseindia.in'
BHAVCOPY = 'https://mcp.nseindia.in/bhavcopy/cm/mcp'
MARKET = 'https://mcp.nseindia.in/cmmkt/mcp'
SERVERS = {'bhavcopy': BHAVCOPY, 'market': MARKET}
PROTOCOL = '2025-06-18'
LIMIT = 4_000_000
MAX_DAYS = 1830
CHUNK_MONTHS = 3
SOURCE = 'NSE official MCP end-of-day history; unadjusted prices, informational use only'
QUOTE_SOURCE = 'NSE official MCP delayed snapshot; not a completed candle'


class _Expired(Exception):
    """The server no longer recognises this session identifier."""


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('NSE MCP returned an invalid %s' % name)
    return float(value)


def _reject_constant(value):
    raise ValueError('NSE MCP returned a nonfinite number: ' + value)


def decode(raw, content_type, identifier):
    """Return the JSON-RPC reply for one request from JSON or an event stream."""
    try:
        text = raw.decode('utf-8')
        if 'text/event-stream' in content_type.lower():
            messages = []
            for block in text.replace('\r\n', '\n').replace('\r', '\n').split('\n\n'):
                data = [line[5:][1:] if line[5:6] == ' ' else line[5:]
                        for line in block.split('\n') if line.startswith('data:')]
                if data:
                    messages.append(json.loads('\n'.join(data), parse_constant=_reject_constant))
        else:
            messages = [json.loads(text, parse_constant=_reject_constant)]
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('NSE MCP returned an unreadable response') from None
    for message in messages:
        if isinstance(message, dict) and message.get('jsonrpc') == '2.0' and message.get('id') == identifier:
            return message
    raise ValueError('NSE MCP did not answer the request')


class MCPSession:
    """One JSON-RPC session with a single pinned NSE endpoint."""
    def __init__(self, url, timeout=30):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != HOST or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('NSE MCP endpoint must be an https://%s URL' % HOST)
        self.url, self.timeout = url, timeout
        self.session_id, self.server, self._serial, self._ready = None, {}, 0, False

    def _post(self, message):
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream',
                   'User-Agent': 'IndianTradingResearch/1.0'}
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id
        if self._ready:
            headers['MCP-Protocol-Version'] = PROTOCOL
        body = json.dumps(message, allow_nan=False).encode()
        for attempt in range(2):
            request = urllib.request.Request(self.url, data=body, headers=headers, method='POST')
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if urllib.parse.urlsplit(response.url).hostname != HOST:
                        raise ValueError('NSE MCP request was redirected away from NSE')
                    raw = response.read(LIMIT+1)
                    kind = response.headers.get('Content-Type') or ''
                    session = response.headers.get('Mcp-Session-Id')
                if len(raw) > LIMIT:
                    raise ValueError('NSE MCP response exceeds its size bound')
                return raw, kind, session
            except urllib.error.HTTPError as exc:
                code = exc.code
                # Python 3.9 leaves the response wrapper uninitialized without fp.
                if exc.fp is not None:
                    exc.close()
                if code == 404 and self.session_id:
                    raise _Expired() from None
                if code not in (429, 500, 502, 503, 504) or attempt:
                    raise ValueError('NSE MCP request failed: HTTP %d' % code) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt:
                    raise ValueError('NSE MCP network unavailable') from None
            time.sleep(1)

    def _request(self, method, params):
        self._serial += 1
        identifier = self._serial
        raw, kind, session = self._post(dict(jsonrpc='2.0', id=identifier, method=method, params=params))
        reply = decode(raw, kind, identifier)
        if 'error' in reply:
            error = reply['error'] if isinstance(reply['error'], dict) else {}
            detail = error.get('data') if isinstance(error.get('data'), str) else error.get('message')
            raise ValueError('NSE MCP rejected %s: %s' % (method, detail or 'unknown error'))
        if not isinstance(reply.get('result'), dict):
            raise ValueError('NSE MCP returned a malformed result')
        return reply['result'], session

    def open(self):
        self.session_id, self._ready = None, False
        result, session = self._request('initialize', dict(
            protocolVersion=PROTOCOL, capabilities={},
            clientInfo=dict(name='indian-trading-research', version='1.0')))
        if not isinstance(result.get('protocolVersion'), str):
            raise ValueError('NSE MCP initialization was not acknowledged')
        self.session_id = session
        self.server = result.get('serverInfo') if isinstance(result.get('serverInfo'), dict) else {}
        self._ready = True
        self._post(dict(jsonrpc='2.0', method='notifications/initialized'))

    def _with_session(self, method, params):
        if not self._ready:
            self.open()
        try:
            return self._request(method, params)[0]
        except _Expired:
            # The specification asks clients to start a new session after 404.
            self.open()
            try:
                return self._request(method, params)[0]
            except _Expired:
                raise ValueError('NSE MCP session could not be re-established') from None

    def tools(self):
        found, cursor = [], None
        for _ in range(20):
            result = self._with_session('tools/list', dict(cursor=cursor) if cursor else {})
            if not isinstance(result.get('tools'), list):
                raise ValueError('NSE MCP returned a malformed tool list')
            found.extend(result['tools'])
            cursor = result.get('nextCursor')
            if not cursor:
                return found
        raise ValueError('NSE MCP tool list did not terminate')

    def call(self, tool, arguments):
        """Call one read-only tool and return its decoded JSON payload."""
        result = self._with_session('tools/call', dict(name=tool, arguments=arguments))
        texts = [item.get('text') for item in result.get('content') or []
                 if isinstance(item, dict) and item.get('type') == 'text']
        if result.get('isError'):
            raise ValueError('NSE MCP tool %s failed: %s' % (tool, (texts[0] if texts else 'no detail')[:300]))
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise ValueError('NSE MCP tool %s returned unexpected content' % tool)
        try:
            payload = json.loads(texts[0], parse_constant=_reject_constant)
        except json.JSONDecodeError:
            raise ValueError('NSE MCP tool %s returned non-JSON output' % tool) from None
        # The servers report lookup failures inside a successful tool result.
        if isinstance(payload, dict) and payload.get('error'):
            raise ValueError('NSE MCP %s: %s' % (tool, str(payload['error'])[:300]))
        return payload


def parse_history(doc, name):
    """Validate one get_stock_history chunk: (from_date, next_end_date, rows)."""
    if not isinstance(doc, dict) or doc.get('symbol') != name or not isinstance(doc.get('data'), list):
        raise ValueError('Unexpected NSE MCP history response')
    try:
        first = dt.date.fromisoformat(doc['from_date'])
        following = dt.date.fromisoformat(doc['next_end_date'])
        rows = []
        for row in doc['data']:
            if not isinstance(row, dict) or row.get('symbol') != name:
                raise ValueError('NSE MCP history row belongs to another symbol')
            day = dt.date.fromisoformat(row['date'])
            values = [_number(row.get(k), k) for k in ('open', 'high', 'low', 'close', 'volume')]
            # Daily bars use the session open, like the other providers.
            rows.append((day, str(row.get('series', '')), Bar(dt.datetime.combine(day, dt.time(9, 15), IST), *values)))
    except (KeyError, TypeError):
        raise ValueError('Unexpected NSE MCP history response') from None
    return first, following, rows


class NSEMCPProvider:
    """Daily unadjusted NSE bars and delayed snapshots from the official MCP."""
    def __init__(self, as_of, days=90, check_actions=True):
        if as_of.tzinfo is None:
            raise ValueError('NSE MCP as-of timestamp requires a timezone')
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            raise ValueError('--days must be between 1 and %d for the NSE MCP provider' % MAX_DAYS)
        self.as_of, self.days, self.check_actions = as_of.astimezone(IST), days, check_actions
        self._sessions = {}
        self.series, self.corporate_actions = [], []

    def session(self, server):
        if server not in self._sessions:
            self._sessions[server] = MCPSession(SERVERS[server])
        return self._sessions[server]

    @staticmethod
    def _name(symbol):
        symbol_name(symbol)
        if not symbol.endswith('.NS'):
            raise ValueError('NSE MCP serves NSE (.NS) securities only; choose another provider for BSE')
        return symbol[:-3]

    def history(self, symbol, interval):
        name = self._name(symbol)
        if interval != '1d':
            raise ValueError('NSE MCP has end-of-day history only, no intraday candles; '
                             'choose another provider for %s data' % interval)
        end = self.as_of.date()
        start = end-dt.timedelta(days=self.days-1)
        collected, cursor = {}, end
        for _ in range(MAX_DAYS//80+2):
            try:
                doc = self.session('bhavcopy').call('get_stock_history', dict(
                    symbol=name, months=CHUNK_MONTHS, endDate=cursor.isoformat()))
            except ValueError as exc:
                # Older chunks do not exist before a listing or the service window.
                if collected and 'not found' in str(exc).lower():
                    break
                raise
            first, following, rows = parse_history(doc, name)
            for day, series, bar in rows:
                if day in collected:
                    raise ValueError('NSE MCP returned more than one bar for %s on %s' % (symbol, day))
                # Never keep a bar dated after the requested cutoff.
                if start <= day <= end:
                    collected[day] = series, bar
            if first <= start or not rows:
                break
            if following >= cursor:
                raise ValueError('NSE MCP history paging did not advance')
            cursor = following
        else:
            raise ValueError('NSE MCP history paging exceeded its bound')
        if not collected:
            raise ValueError('NSE MCP returned no daily bars for %s in the requested window' % symbol)
        days = sorted(collected)
        self.series = sorted({collected[day][0] for day in days})
        if self.check_actions:
            self.corporate_actions = self.actions(symbol, days[0], days[-1])
            blocking = [a for a in self.corporate_actions
                        if a['price_adjusting'] and days[0] < dt.date.fromisoformat(a['ex_date']) <= days[-1]]
            if blocking:
                raise ValueError('Unadjusted NSE history for %s spans %s; daily setups would be distorted. '
                                 'Use an adjusted source or a window that starts on the ex-date' % (
                                     symbol, ', '.join('%s ex %s' % (a['type'], a['ex_date']) for a in blocking)))
        return validate([collected[day][1] for day in days])

    def actions(self, symbol, start, end):
        """Corporate actions with ex-dates in [start, end]; incomplete lists fail."""
        name = self._name(symbol)
        doc = self.session('bhavcopy').call('get_corporate_actions', dict(
            symbol=name, fromDate=start.isoformat(), toDate=end.isoformat()))
        if not isinstance(doc, dict) or doc.get('symbol') != name or not isinstance(doc.get('actions'), list):
            raise ValueError('Unexpected NSE MCP corporate-action response')
        if doc.get('warning'):
            raise ValueError('NSE MCP corporate-action list is incomplete: '+str(doc['warning'])[:200])
        events = []
        for row in doc['actions']:
            try:
                ex_date = dt.date.fromisoformat(row['exDate']).isoformat()
            except (KeyError, TypeError, ValueError):
                raise ValueError('Unexpected NSE MCP corporate-action row') from None
            factor = row.get('adjustmentFactor')
            known = isinstance(factor, (int, float)) and not isinstance(factor, bool) and math.isfinite(factor)
            # An unknown factor is treated as price-changing, never as harmless.
            events.append(dict(ex_date=ex_date, type=str(row.get('actionType', 'UNKNOWN')),
                               purpose=str(row.get('purpose', '')), adjustment_factor=factor if known else None,
                               price_adjusting=not known or abs(factor-1) > 1e-12))
        return events

    def quote(self, symbol):
        """Delayed exchange snapshot. It is not a candle and cannot clear risk."""
        name = self._name(symbol)
        doc = self.session('market').call('cm_get_stock_quote', dict(symbol=name))
        stock = doc.get('stock') if isinstance(doc, dict) else None
        if not isinstance(stock, dict) or stock.get('symbol') != name:
            raise ValueError('Unexpected NSE MCP quote response')
        try:
            observed = dt.datetime.strptime(stock['latestTimestamp'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=IST)
        except (KeyError, TypeError, ValueError):
            raise ValueError('NSE MCP quote has no usable timestamp') from None
        price = _number(stock.get('lastTradedPrice'), 'last traded price')
        if price <= 0:
            raise ValueError('NSE MCP quote has no traded price')
        fields = dict(open='openPrice', high='highPrice', low='lowPrice',
                      previous_close='preClosePrice', volume='volume')
        return dict(symbol=symbol, price=price, observed_at=observed.isoformat(),
                    series=stock.get('series'), cache_updated_at=doc.get('updatedAt'),
                    kind='DELAYED_SNAPSHOT_NOT_A_CANDLE', source=QUOTE_SOURCE,
                    **{key: _number(stock.get(field), key) for key, field in fields.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read NSE official MCP data for local paper research')
    parser.add_argument('command', choices=['tools', 'call', 'quote', 'actions'])
    parser.add_argument('target', nargs='?', help='Tool name for call; SYMBOL.NS for quote/actions')
    parser.add_argument('--server', choices=sorted(SERVERS), default='bhavcopy')
    parser.add_argument('--arguments', default='{}', help='JSON object of tool arguments for call')
    parser.add_argument('--from', dest='start', help='First ex-date, yyyy-mm-dd (actions)')
    parser.add_argument('--to', dest='end', help='Last ex-date, yyyy-mm-dd (actions)')
    a = parser.parse_args(argv)
    now = dt.datetime.now(IST).replace(microsecond=0)
    try:
        provider = NSEMCPProvider(now)
        if a.command == 'tools':
            result = [dict(name=t.get('name'), description=(t.get('description') or '').split('\n')[0],
                           arguments=sorted((t.get('inputSchema') or {}).get('properties') or {}))
                      for t in provider.session(a.server).tools()]
        elif not a.target:
            raise ValueError(a.command+' requires a target')
        elif a.command == 'call':
            arguments = json.loads(a.arguments, parse_constant=_reject_constant)
            if not isinstance(arguments, dict):
                raise ValueError('--arguments must be a JSON object')
            result = provider.session(a.server).call(a.target, arguments)
        elif a.command == 'quote':
            result = provider.quote(a.target)
        else:
            end = dt.date.fromisoformat(a.end) if a.end else now.date()
            start = dt.date.fromisoformat(a.start) if a.start else end-dt.timedelta(days=365)
            if start > end:
                raise ValueError('--from must not follow --to')
            result = provider.actions(a.target, start, end)
        print(json.dumps(dict(source='NSE official MCP (%s); informational use only' % HOST,
                              retrieved_at=now.isoformat(), paper=True, execution_enabled=False,
                              result=result), indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='UNAVAILABLE', error=str(exc), paper=True, execution_enabled=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
