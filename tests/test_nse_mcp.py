"""NSE official MCP adapter: transport, causal daily history and guards.

Every payload is generated here from arithmetic sequences for the fictional
DEMO symbol. No request leaves the test process and no NSE data is reproduced.
"""
import contextlib
import datetime as dt
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from india.__main__ import main as india_main
from india.data import IST, timestamp
from india.market import DEFAULT_CALENDAR
from india.nse_mcp import (BHAVCOPY, LIMIT, MARKET, MCPSession, NSEMCPProvider,
                           decode, main as mcp_main)
from workflow.cli import parser
from workflow.common import config
from workflow.data import MarketData
from workflow.strategy import scan

NOW = timestamp('2026-10-23T20:30:00+05:30')
FIRST = dt.date(2026, 5, 4)


def sessions(first, last):
    day, found = first, []
    while day <= last:
        if DEFAULT_CALENDAR.trading_day(day):
            found.append(day)
        day += dt.timedelta(days=1)
    return found


class Response:
    def __init__(self, body, kind='application/json', session=None, url=BHAVCOPY):
        self.body, self.url = body, url
        self.headers = {'Content-Type': kind}
        if session:
            self.headers['Mcp-Session-Id'] = session

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeNSE:
    """Synthetic stand-in: JSON for initialize, an event stream for tools."""
    def __init__(self, actions=(), listed=FIRST, extra_rows=()):
        self.actions, self.listed, self.extra_rows = list(actions), listed, list(extra_rows)
        self.calls, self.headers, self.urls, self.opened = [], [], [], 0
        self.expire_next = False
        self.override = {}

    def rows(self, end):
        first = end-dt.timedelta(days=91)
        rows = []
        for index, day in enumerate(sessions(self.listed, dt.date(2026, 12, 31))):
            if first <= day <= end:
                close = 100+index/2
                rows.append(dict(symbol='DEMO', series='EQ', open=close-.2, high=close+1, low=close-1,
                                 close=close, prevClose=close-.5, volume=2000+index, deliveryQty=0,
                                 date=day.isoformat()))
        return first, rows

    def tool(self, name, arguments):
        if name in self.override:
            return self.override[name]
        if arguments.get('symbol') != 'DEMO':
            return dict(error='Symbol not found: '+str(arguments.get('symbol')))
        if name == 'get_stock_history':
            end = dt.date.fromisoformat(arguments['endDate'])
            first, rows = self.rows(end)
            if end < self.listed:
                return dict(error='Symbol not found: DEMO')
            return dict(symbol='DEMO', from_date=first.isoformat(), to_date=end.isoformat(),
                        data=rows+self.extra_rows, next_end_date=(first-dt.timedelta(days=1)).isoformat())
        if name == 'get_corporate_actions':
            chosen = [a for a in self.actions if arguments['fromDate'] <= a['exDate'] <= arguments['toDate']]
            return dict(symbol='DEMO', count=len(chosen), actions=chosen)
        if name == 'cm_get_stock_quote':
            return dict(updatedAt='2026-10-23T06:45:28Z', stock=dict(
                symbol='DEMO', series='EQ', openPrice=100., highPrice=103., lowPrice=99.,
                preClosePrice=100.5, lastTradedPrice=102.5, volume=4321,
                latestTimestamp='2026-10-23 12:15:28'))
        raise AssertionError('Unexpected tool '+name)

    def __call__(self, request, timeout=None):
        message = json.loads(request.data)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.headers.append(headers)
        url = request.full_url
        self.urls.append(url)
        if message['method'] == 'initialize':
            self.opened += 1
            return Response(json.dumps(dict(jsonrpc='2.0', id=message['id'], result=dict(
                protocolVersion='2025-06-18', serverInfo=dict(name='synthetic')))).encode(),
                session='session-%d' % self.opened, url=url)
        if headers.get('mcp-session-id') != 'session-%d' % self.opened or self.expire_next:
            self.expire_next = False
            raise urllib.error.HTTPError(url, 404, 'expired', {}, None)
        if message['method'] == 'notifications/initialized':
            return Response(b'', url=url)
        if message['method'] == 'tools/list':
            result = dict(tools=[dict(name='get_stock_history', description='Daily bars\nmore',
                                      inputSchema=dict(properties=dict(symbol={}, months={}, endDate={})))])
        else:
            self.calls.append((message['params']['name'], message['params']['arguments']))
            payload = self.tool(message['params']['name'], message['params']['arguments'])
            result = payload if 'content' in payload else dict(
                content=[dict(type='text', text=json.dumps(payload))], isError=False)
        body = 'event:message\ndata:'+json.dumps(dict(jsonrpc='2.0', id=message['id'], result=result))+'\n\n'
        return Response(body.encode(), 'text/event-stream;charset=UTF-8', url=url)


def bonus(day):
    return dict(exDate=day, actionType='BONUS', purpose='Bonus 1:1', adjustmentFactor=.5)


class Base(unittest.TestCase):
    def serve(self, server):
        for target in ('india.nse_mcp.urllib.request.urlopen', 'india.nse_mcp.time.sleep'):
            patcher = patch(target, server if target.endswith('urlopen') else lambda seconds: None)
            patcher.start()
            self.addCleanup(patcher.stop)
        return server


class Transport(Base):
    def test_decode_json_and_event_streams(self):
        reply = dict(jsonrpc='2.0', id=7, result=dict(ok=True))
        self.assertEqual(decode(json.dumps(reply).encode(), 'application/json', 7), reply)
        stream = ': keepalive\r\n\r\nevent: message\r\ndata: {"jsonrpc":"2.0","method":"note"}\r\n\r\n' \
                 'data: {"jsonrpc":"2.0",\r\ndata: "id":7,"result":{"ok":true}}\r\n\r\n'
        self.assertEqual(decode(stream.encode(), 'text/event-stream', 7), reply)
        for raw, kind in ((b'<html>', 'text/html'), (json.dumps(dict(reply, id=8)).encode(), 'application/json'),
                          (b'{"jsonrpc":"2.0","id":7,"result":NaN}', 'application/json')):
            with self.assertRaises(ValueError):
                decode(raw, kind, 7)

    def test_session_headers_and_single_initialization(self):
        server = self.serve(FakeNSE())
        session = MCPSession(BHAVCOPY)
        session.call('get_corporate_actions', dict(symbol='DEMO', fromDate='2026-01-01', toDate='2026-10-23'))
        session.call('get_corporate_actions', dict(symbol='DEMO', fromDate='2026-01-01', toDate='2026-10-23'))
        self.assertEqual(server.opened, 1)
        self.assertNotIn('mcp-session-id', server.headers[0])
        self.assertEqual([h.get('mcp-session-id') for h in server.headers[1:]], ['session-1']*3)
        self.assertEqual(server.headers[-1]['mcp-protocol-version'], '2025-06-18')
        self.assertIn('text/event-stream', server.headers[0]['accept'])
        self.assertEqual([t['name'] for t in session.tools()], ['get_stock_history'])

    def test_only_the_official_https_host(self):
        for url in ('http://mcp.nseindia.in/cmmkt/mcp', 'https://mcp.nseindia.in.example.com/mcp',
                    'https://user@mcp.nseindia.in/mcp', 'https://mcp.nseindia.in:8443/mcp'):
            with self.assertRaisesRegex(ValueError, 'must be an https'):
                MCPSession(url)
        self.serve(lambda request, timeout=None: Response(b'{}', url='https://example.com/mcp'))
        with self.assertRaisesRegex(ValueError, 'redirected away'):
            MCPSession(MARKET).open()

    def test_size_bound_and_failures(self):
        self.serve(lambda request, timeout=None: Response(b' '*(LIMIT+1)))
        with self.assertRaisesRegex(ValueError, 'size bound'):
            MCPSession(BHAVCOPY).open()
        attempts = []
        def unavailable(request, timeout=None):
            attempts.append(1)
            raise urllib.error.HTTPError(request.full_url, 503, 'busy', {}, None)
        self.serve(unavailable)
        with self.assertRaisesRegex(ValueError, 'HTTP 503'):
            MCPSession(BHAVCOPY).open()
        self.assertEqual(len(attempts), 2)  # one bounded retry
        self.serve(lambda request, timeout=None: (_ for _ in ()).throw(urllib.error.URLError('down')))
        with self.assertRaisesRegex(ValueError, 'network unavailable'):
            MCPSession(BHAVCOPY).open()

    def test_expired_session_restarts_once(self):
        server = self.serve(FakeNSE())
        session = MCPSession(BHAVCOPY)
        arguments = dict(symbol='DEMO', fromDate='2026-01-01', toDate='2026-10-23')
        session.call('get_corporate_actions', arguments)
        server.expire_next = True
        self.assertEqual(session.call('get_corporate_actions', arguments)['count'], 0)
        self.assertEqual((server.opened, session.session_id), (2, 'session-2'))

    def test_tool_and_protocol_errors_are_explicit(self):
        server = self.serve(FakeNSE())
        session = MCPSession(BHAVCOPY)
        with self.assertRaisesRegex(ValueError, 'Symbol not found: NOPE'):
            session.call('get_stock_history', dict(symbol='NOPE', months=3, endDate='2026-10-23'))
        server.override['x'] = dict(content=[dict(type='text', text='boom')], isError=True)
        with self.assertRaisesRegex(ValueError, 'failed: boom'):
            session.call('x', {})
        server.override['x'] = dict(content=[dict(type='text', text='not json')], isError=False)
        with self.assertRaisesRegex(ValueError, 'non-JSON'):
            session.call('x', {})
        server.override['x'] = dict(content=[], isError=False)
        with self.assertRaisesRegex(ValueError, 'unexpected content'):
            session.call('x', {})
        self.serve(lambda request, timeout=None: Response(json.dumps(dict(
            jsonrpc='2.0', id=1, error=dict(code=-32602, message='Unknown tool', data='Tool not found: nope'))).encode()))
        with self.assertRaisesRegex(ValueError, 'Tool not found: nope'):
            MCPSession(BHAVCOPY).open()


class Provider(Base):
    def test_paged_daily_history_is_causal_and_ordered(self):
        server = self.serve(FakeNSE())
        provider = NSEMCPProvider(NOW, 150)
        bars = provider.history('DEMO.NS', '1d')
        expected = sessions(NOW.date()-dt.timedelta(days=149), NOW.date())
        self.assertEqual([b.time.date() for b in bars], expected)
        self.assertTrue(all(b.time.time() == dt.time(9, 15) and b.time.utcoffset() == IST.utcoffset(None) for b in bars))
        history = [c for c in server.calls if c[0] == 'get_stock_history']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0][1], dict(symbol='DEMO', months=3, endDate='2026-10-23'))
        self.assertLess(history[1][1]['endDate'], history[0][1]['endDate'])
        self.assertEqual((bars[-1].volume, provider.series), (2000+len(sessions(FIRST, NOW.date()))-1, ['EQ']))
        # A replay cutoff never receives a later session.
        earlier = NSEMCPProvider(timestamp('2026-09-10T10:00:00+05:30'), 30).history('DEMO.NS', '1d')
        self.assertEqual(earlier[-1].time.date(), dt.date(2026, 9, 10))
        self.assertTrue(all(b.time.date() >= dt.date(2026, 8, 12) for b in earlier))

    def test_future_rows_from_the_server_are_dropped(self):
        late = dict(symbol='DEMO', series='EQ', open=1., high=2., low=1., close=2., volume=5, date='2026-10-26')
        self.serve(FakeNSE(extra_rows=[late]))
        bars = NSEMCPProvider(NOW, 30).history('DEMO.NS', '1d')
        self.assertEqual(bars[-1].time.date(), dt.date(2026, 10, 23))

    def test_no_intraday_candles_and_no_bse(self):
        server = self.serve(FakeNSE())
        provider = NSEMCPProvider(NOW)
        for interval in ('5m', '1m'):
            with self.assertRaisesRegex(ValueError, 'no intraday candles'):
                provider.history('DEMO.NS', interval)
        with self.assertRaisesRegex(ValueError, 'NSE \\(.NS\\) securities only'):
            provider.history('500325.BO', '1d')
        self.assertEqual(server.calls, [])
        for days in (0, 1831, True, 2.5):
            with self.assertRaises(ValueError):
                NSEMCPProvider(NOW, days)
        with self.assertRaises(ValueError):
            NSEMCPProvider(dt.datetime(2026, 10, 23, 20, 30))

    def test_listing_boundary_and_unknown_symbols(self):
        self.serve(FakeNSE(listed=dt.date(2026, 9, 1)))
        bars = NSEMCPProvider(NOW, 400).history('DEMO.NS', '1d')
        self.assertEqual(bars[0].time.date(), dt.date(2026, 9, 1))
        with self.assertRaisesRegex(ValueError, 'Symbol not found: MISSING'):
            NSEMCPProvider(NOW).history('MISSING.NS', '1d')
        self.serve(FakeNSE(listed=dt.date(2026, 11, 2)))
        with self.assertRaisesRegex(ValueError, 'Symbol not found'):
            NSEMCPProvider(NOW).history('DEMO.NS', '1d')

    def test_malformed_history_fails_closed(self):
        row = dict(symbol='DEMO', series='EQ', open=1., high=2., low=1., close=2., volume=5, date='2026-10-22')
        cases = [([row, dict(row, series='BE')], 'more than one bar'),
                 ([dict(row, symbol='OTHER')], 'another symbol'),
                 ([dict(row, close=None)], 'invalid close'),
                 ([dict(row, volume=True)], 'invalid volume'),
                 ([dict(row, high=.5)], 'Invalid OHLC range')]
        for rows, message in cases:
            server = self.serve(FakeNSE(listed=dt.date(2026, 12, 31), extra_rows=rows))
            server.listed = dt.date(2026, 12, 31)
            server.override['get_stock_history'] = dict(symbol='DEMO', from_date='2026-07-24', to_date='2026-10-23',
                                                        data=rows, next_end_date='2026-07-23')
            with self.assertRaisesRegex(ValueError, message):
                NSEMCPProvider(NOW, 30).history('DEMO.NS', '1d')
        server.override['get_stock_history'] = dict(symbol='DEMO', from_date='2026-10-20', to_date='2026-10-23',
                                                    data=[row], next_end_date='2026-10-23')
        with self.assertRaisesRegex(ValueError, 'did not advance'):
            NSEMCPProvider(NOW, 30).history('DEMO.NS', '1d')
        server.override['get_stock_history'] = dict(symbol='DEMO', data=[row])
        with self.assertRaisesRegex(ValueError, 'Unexpected NSE MCP history'):
            NSEMCPProvider(NOW, 30).history('DEMO.NS', '1d')

    def test_split_or_bonus_inside_the_window_blocks_unadjusted_history(self):
        self.serve(FakeNSE(actions=[bonus('2026-09-15')]))
        with self.assertRaisesRegex(ValueError, 'spans BONUS ex 2026-09-15'):
            NSEMCPProvider(NOW, 90).history('DEMO.NS', '1d')
        # All bars on or after the ex-date share one price basis.
        provider = NSEMCPProvider(NOW, 39)
        bars = provider.history('DEMO.NS', '1d')
        self.assertEqual(bars[0].time.date(), dt.date(2026, 9, 15))
        self.assertTrue(provider.corporate_actions[0]['price_adjusting'])
        unchecked = NSEMCPProvider(NOW, 90, check_actions=False).history('DEMO.NS', '1d')
        self.assertGreater(len(unchecked), len(bars))

    def test_dividends_pass_and_uncertain_actions_block(self):
        dividend = dict(exDate='2026-09-15', actionType='DIVIDEND', purpose='Dividend', adjustmentFactor=1.0)
        self.serve(FakeNSE(actions=[dividend]))
        provider = NSEMCPProvider(NOW, 90)
        self.assertTrue(provider.history('DEMO.NS', '1d'))
        self.assertEqual(provider.corporate_actions, [dict(ex_date='2026-09-15', type='DIVIDEND', purpose='Dividend',
                                                           adjustment_factor=1.0, price_adjusting=False)])
        self.serve(FakeNSE(actions=[dict(exDate='2026-09-15', actionType='DEMERGER', purpose='Scheme')]))
        with self.assertRaisesRegex(ValueError, 'spans DEMERGER'):
            NSEMCPProvider(NOW, 90).history('DEMO.NS', '1d')
        server = self.serve(FakeNSE())
        server.override['get_corporate_actions'] = dict(symbol='DEMO', actions=[], warning='partial list')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            NSEMCPProvider(NOW, 90).history('DEMO.NS', '1d')
        server.override['get_corporate_actions'] = dict(error='upstream timeout')
        with self.assertRaisesRegex(ValueError, 'upstream timeout'):
            NSEMCPProvider(NOW, 90).history('DEMO.NS', '1d')
        server.override['get_corporate_actions'] = dict(symbol='DEMO', actions=[dict(actionType='BONUS')])
        with self.assertRaisesRegex(ValueError, 'corporate-action row'):
            NSEMCPProvider(NOW, 90).history('DEMO.NS', '1d')

    def test_delayed_quote_is_labelled_and_validated(self):
        server = self.serve(FakeNSE())
        quote = NSEMCPProvider(NOW).quote('DEMO.NS')
        self.assertEqual((quote['price'], quote['observed_at'], quote['previous_close']),
                         (102.5, '2026-10-23T12:15:28+05:30', 100.5))
        self.assertEqual(quote['kind'], 'DELAYED_SNAPSHOT_NOT_A_CANDLE')
        self.assertEqual(set(server.urls), {MARKET})  # quotes use the market endpoint only
        for stock in (dict(symbol='OTHER'), dict(symbol='DEMO', lastTradedPrice=1., latestTimestamp='soon'),
                      dict(symbol='DEMO', lastTradedPrice=0., latestTimestamp='2026-10-23 12:15:28')):
            server.override['cm_get_stock_quote'] = dict(stock=stock)
            with self.assertRaises(ValueError):
                NSEMCPProvider(NOW).quote('DEMO.NS')


class Wiring(Base):
    def test_market_data_daily_snapshot_and_explicit_intraday_refusal(self):
        self.serve(FakeNSE())
        data = MarketData(NOW, 'nse-mcp')
        bars = data.history('DEMO.NS', '1d')
        self.assertEqual(bars[-1].time.date(), dt.date(2026, 10, 23))
        self.assertGreaterEqual(len(bars), 60)  # at least the three months Yahoo supplies
        self.assertIn('NSE official MCP', data.source)
        self.assertEqual(data.attempts[('DEMO.NS', '1d')][0]['provider'], 'nse-mcp')
        with self.assertRaisesRegex(ValueError, 'no intraday candles'):
            data.history('DEMO.NS', '5m')
        # The same-day bar is excluded until the session has closed.
        open_market = MarketData(timestamp('2026-10-23T11:00:00+05:30'), 'nse-mcp').history('DEMO.NS', '1d')
        self.assertEqual(open_market[-1].time.date(), dt.date(2026, 10, 22))
        with self.assertRaisesRegex(ValueError, 'Unsupported candle provider'):
            MarketData(NOW, 'unknown')

    def test_daily_scan_reports_the_official_source(self):
        self.serve(FakeNSE(actions=[bonus('2026-09-15')]))
        blocked = scan(MarketData(NOW, 'nse-mcp'), ['DEMO.NS'], NOW, config(), mode='daily')
        self.assertEqual((blocked['scanned'], len(blocked['failed'])), (0, 1))
        self.assertIn('spans BONUS', blocked['failed'][0]['error'])
        self.serve(FakeNSE())
        result = scan(MarketData(NOW, 'nse-mcp'), ['DEMO.NS', 'OTHER.NS'], NOW, config(), mode='daily')
        self.assertEqual(result['scanned'], 1)
        row = next(r for r in result['results'] if r['symbol'] == 'DEMO.NS')
        self.assertEqual((row['status'], row['as_of'][:10], row['expected_session']), ('MATCH', '2026-10-23', '2026-10-23'))
        self.assertIn('breakout_20d', row['setups'])
        self.assertIn('NSE official MCP', row['source'])
        self.assertIn('Symbol not found', result['failed'][0]['error'])
        self.assertFalse(result['execution_enabled'])

    def test_command_lines_accept_the_provider(self):
        self.serve(FakeNSE())
        self.assertEqual(parser('scanner').parse_args(['daily', '--provider', 'nse-mcp']).provider, 'nse-mcp')
        common = ['DEMO.NS', '--provider', 'nse-mcp', '--tick', '0.05', '--as-of', NOW.isoformat()]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(india_main(['history']+common+['--days', '30']), 0)
        doc = json.loads(out.getvalue())
        self.assertEqual((doc['mode'], doc['bars'][-1]['time'][:10], doc['corporate_actions']),
                         ('historical', '2026-10-22', []))  # replay: sessions before the cutoff date
        self.assertIn('NSE official MCP', doc['source'])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(india_main(['analyze']+common+['--no-fallback']), 1)
        self.assertIn('no intraday candles', err.getvalue())

    def test_module_command_line(self):
        self.serve(FakeNSE(actions=[bonus('2026-09-15')]))
        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = mcp_main(list(argv))
            return code, json.loads(out.getvalue())
        code, doc = run('quote', 'DEMO.NS')
        self.assertEqual((code, doc['result']['price'], doc['execution_enabled']), (0, 102.5, False))
        code, doc = run('actions', 'DEMO.NS', '--from', '2026-09-01', '--to', '2026-10-23')
        self.assertEqual((code, doc['result'][0]['type']), (0, 'BONUS'))
        code, doc = run('tools')
        self.assertEqual(doc['result'], [dict(name='get_stock_history', description='Daily bars',
                                              arguments=['endDate', 'months', 'symbol'])])
        code, doc = run('call', 'get_corporate_actions', '--arguments',
                        '{"symbol":"DEMO","fromDate":"2026-01-01","toDate":"2026-10-23"}')
        self.assertEqual((code, doc['result']['count']), (0, 1))
        for argv in (('quote',), ('call', 'x', '--arguments', '[]'), ('quote', 'DEMO.BO'),
                     ('actions', 'DEMO.NS', '--from', '2026-10-23', '--to', '2026-01-01')):
            code, doc = run(*argv)
            self.assertEqual((code, doc['status'], doc['execution_enabled']), (1, 'UNAVAILABLE', False))


if __name__ == '__main__':
    unittest.main()
