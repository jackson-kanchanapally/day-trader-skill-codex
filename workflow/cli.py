"""Command-compatible skill entrypoints for the Indian file-handoff workflow."""
import argparse
import contextlib
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import time

from india.market import Calendar, DEFAULT_CALENDAR, session
from .common import clock, config, digest, envelope, exchange_of, indian_words, read, upstream, utc, write
from .data import MarketData, security_master, universe
from .paper import Ledger, budget, order_hash, risk, ticket
from .research import disclosures, financials, news
from .strategy import entry, plan, scan, timemachine


def parser(role):
    p = argparse.ArgumentParser(description='Indian '+role+' workflow (local paper only)')
    common = set('--workspace --config --as-of --calendar --capital --risk --write --no-write --out --json'.split())
    market = set('--provider --csv --days --instruments --no-fallback'.split())
    selection = set('--symbol --symbols --tickers --watchlist --universe --limit'.split())
    role_flags = {
        'scanner': market | selection | set('--security-master --exchange --tick --min-price --min-volume --or-minutes --min-volume-x --at --date --until'.split()),
        'entry': market | selection | set('--security-master --tick --setup --level --interval --or-minutes --min-volume-x --every --max-checks --until'.split()),
        'exit': market | set('--security-master --tick --symbol --from --stop --target --entry --or-high --or-low --date --time --flat-by --setup --interval --or-minutes'.split()),
        'timemachine': market | set('--from --replays --seed'.split()),
        'budget': {'--from','--limit','--prices','--portfolio'},
        'risk': market | {'--from', '--security-master', '--exchange', '--tick'},
        'broker': market | set('--security-master --exchange --tick --symbol --from --index --reply --qty --price --fingerprint --fill-id --fill-bar'.split()),
        'alerts': market | selection | set('--exchange --from --from-plan --opening-bell --above --below --stop --target --pct --direction --at --label --id --rearm --date'.split()),
        'news': market | selection | set('--hours --all --feed-url --announcements --no-moves --prices-csv'.split()),
        'analyst': {'--filing', '--metrics'},
        'pelosi': selection | {'--csv', '--all'},
    }
    def add(*flags, **options):
        if not flags[0].startswith('-') or flags[0] in common | role_flags[role]:
            return p.add_argument(*flags, **options)
        # Keep internal defaults consistent without accepting unrelated options.
        name = options.get('dest') or next(flag for flag in flags if flag.startswith('--'))[2:].replace('-', '_')
        default = options.get('default', False if options.get('action') == 'store_true' else None)
        p.set_defaults(**{name: default})

    choices = {'scanner': ['universe', 'daily', 'orb'], 'entry': ['check', 'watch', 'watchlist'],
               'exit': ['plan', 'show'], 'timemachine': ['run', 'chart'],
               'broker': ['show', 'approve', 'decline', 'fill', 'flatten', 'log', 'portfolio'],
               'alerts': ['clock', 'add', 'check', 'quote', 'list', 'reset'],
               'pelosi': ['index', 'check']}
    if role in choices:
        add('command', choices=choices[role])
    elif role == 'analyst':
        add('symbol')
    add('items', nargs='*', help='entry/quote symbols or alert reset ID')
    add('--workspace', default='trader', help='Separate file-handoff directory; never a sealed team session')
    add('--config')
    add('--as-of', '--now', dest='as_of', help='ISO cutoff; naive original-style clocks are IST')
    add('--calendar')
    add('--provider', '--source', choices=['yahoo', 'nse', 'upstox'], default='yahoo')
    add('--csv', '--offline', dest='csv_path', help='OHLCV CSV or saved Yahoo chart JSON (directory accepted)')
    add('--days', type=int, default=60)
    add('--instruments', help='Upstox instrument resolver JSON')
    add('--no-fallback', action='store_true', help='Disable automatic Yahoo-to-NSE fallback for .NS candles')
    add('--security-master', help='Dated normalized INR security metadata')
    add('--exchange', choices=['NSE', 'BSE'], default='NSE')
    add('--tick', type=float)
    add('--capital', '--budget', dest='capital', type=float)
    add('--risk', type=float)
    add('--write', action='store_true')
    add('--no-write', action='store_true')
    add('--out')
    add('--json', action='store_true', help='JSON is also the default report format')
    add('--symbol')
    add('--symbols', help='Comma-separated scan symbols')
    add('--tickers', nargs='+')
    add('--watchlist')
    add('--universe', help='Text file, one symbol or SYMBOL|Name per line')
    if role == 'budget':
        add('--limit',dest='limit_price',nargs='?',const='plan',help='Tick-aligned INR buy limit; without a value use the planned entry')
        p.set_defaults(limit=None)
    else:
        add('--limit', type=int)
    add('--prices',help='Dated completed-price JSON for conservative pre-trade sizing')
    add('--portfolio',help='Explicit paper INR snapshot; must match the local ledger if present')
    add('--min-price', type=float, default=0)
    add('--min-volume', type=float, default=0)
    add('--setup', choices=['orb', 'level'], default='orb')
    add('--level', type=float)
    add('--interval', choices=['1m', '5m'], default='5m')
    add('--or-minutes', type=int, default=15)
    add('--min-volume-x', type=float)
    add('--every', type=float, default=30)
    add('--max-checks', type=int, default=120, help='Bound watch polling; Ctrl-C stops earlier')
    add('--from', dest='from_path')
    add('--stop', help='or-mid, or-low, atr:K, pct:X, price:X (or numeric alert stop)')
    add('--target', help='1R, 2R, price:X (or numeric alert target)')
    add('--entry', type=float)
    add('--or-high', type=float)
    add('--or-low', type=float)
    add('--date')
    add('--time')
    add('--flat-by')
    add('--replays', '--simulations', dest='replays', type=int, default=10000)
    add('--seed', type=int, default=19)
    add('--index', type=int, default=1)
    add('--reply')
    add('--qty', type=int)
    add('--price', type=float)
    add('--fingerprint')
    add('--fill-id')
    add('--fill-bar', help='Observed OHLCV JSON with time, open, high, low, close, volume for a paper entry')
    add('--from-plan', action='store_true')
    add('--opening-bell', action='store_true')
    add('--above', type=float)
    add('--below', type=float)
    add('--pct', type=float)
    add('--direction', choices=['up', 'down'])
    add('--at')
    add('--until', help='IST entry cutoff override, before flat-by')
    add('--label')
    add('--id')
    add('--rearm', action='store_true')
    add('--hours', type=float, default=24)
    add('--all', action='store_true')
    add('--feed-url', help='Authorized HTTPS RSS template with {symbol}')
    add('--announcements', help='Dated issuer/exchange announcement JSON export')
    add('--no-moves', action='store_true', help='Read headlines without completed-candle move checks')
    add('--prices-csv', help='Separate authorized OHLCV CSV for news move checks')
    add('--filing', help='Sourced normalized quarterly financial JSON')
    add('--metrics', help='Output metrics JSON')
    return p


def symbols(a, workspace):
    values = list(a.tickers or [])
    if a.symbol:
        values.append(a.symbol)
    if a.symbols:
        values.extend(a.symbols.split(','))
    values.extend(a.items)
    path = a.watchlist or a.universe
    if not values and path:
        if str(path).endswith('.json'):
            doc = read(path)
            values.extend(r.get('symbol', r.get('ticker')) for r in doc.get('hits', []))
        else:
            values.extend(line.strip().split('|')[0] for line in Path(path).read_text().splitlines() if line.strip() and not line.startswith('#'))
    if not values:
        m = upstream('news')
        values = list(m.watch_from_files(str(workspace/'portfolio.json'), str(workspace/'watchlist.json'), str(workspace/'plan.json')))
    values = list(dict.fromkeys(str(v).strip().upper() for v in values if v))
    if a.limit is not None:
        if a.limit < 1:
            raise ValueError('--limit must be positive')
        values = values[:a.limit]
    for value in values:
        exchange_of(value)
    return values


def quotes(data, names, now):
    prices, detail, errors = {}, {}, []
    for sym in names:
        try:
            bars = data.history(sym, '5m')
            row = bars[-1]
            observed = row.time+dt.timedelta(minutes=5)
            if observed.date() != now.date() or not 0 <= (now-observed).total_seconds() <= 900:
                raise ValueError('Latest completed price is missing/stale')
            prior = [b for b in bars if b.time.date() < now.date()]
            prices[sym] = row.close
            detail[sym] = dict(price=row.close, time=observed.isoformat(), source=data.source,
                               prev_close=prior[-1].close if prior else None,
                               provider_attempts=data.attempts.get((sym, '5m'), []))
        except (ValueError, OSError, KeyError) as exc:
            errors.append(dict(symbol=sym, error=str(exc)))
    used_sources = sorted({row['source'] for row in detail.values()})
    return dict(as_of=now.isoformat(), currency='INR', source=used_sources[0] if len(used_sources) == 1 else 'See per-symbol sources', prices=prices, detail=detail, errors=errors)


def market_status(now, cal, exchange):
    hours = cal.hours(now.date(),exchange)
    record = cal.document[exchange][str(now.year)]
    following,reason = None,None
    try:
        following = cal.next_open(now,exchange).isoformat()
    except ValueError as error:
        reason = str(error)
    return dict(date=now.date().isoformat(), now_new_york=now.strftime('%Y-%m-%d %H:%M'),
                open=session(now, cal, exchange), opens=hours[0].strftime('%H:%M') if hours else None,
                closes=hours[1].strftime('%H:%M') if hours else None, early_close=None, calendar_known=True,
                next_open=following,next_open_reason=reason,exchange=exchange,timezone='Asia/Kolkata',
                calendar_source=record.get('sessions',{}).get(now.date().isoformat(),record)['source'],
                session_event=exchange+':'+now.date().isoformat()+':'+str(hours))


def main(role, argv=None):
    a = parser(role).parse_args(argv)
    try:
        result, code = run(role, a)
        if result is not None:
            print(json.dumps(result, indent=2, allow_nan=False))
        return code
    except (ValueError, OSError, KeyError, TypeError, IndexError, AttributeError, sqlite3.Error) as exc:
        print(json.dumps(dict(status='UNAVAILABLE', error=str(exc), paper=True, execution_enabled=False)))
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status='STOPPED', paper=True, execution_enabled=False)))
        return 130


def run(role, a):
    now = clock(a.as_of)
    w = Path(a.workspace)
    if (w/'context.json').exists():
        raise ValueError('Use a separate workspace; sealed team session artifacts are immutable')
    config_path = a.config or (str(w/'config.json') if (w/'config.json').exists() else None)
    cfg = config(config_path, budget=a.capital, max_loss_per_trade_inr=a.risk)
    if role in ('scanner', 'entry') and a.until:
        cfg = config(config_path, budget=a.capital, max_loss_per_trade_inr=a.risk, no_entries_after=a.until)
    if role == 'scanner' and getattr(a, 'command', None) == 'orb' and a.at:
        when = dt.time.fromisoformat(a.at)
        requested = dt.datetime.combine(dt.date.fromisoformat(a.date) if a.date else now.date(), when, now.tzinfo)
        if requested > now:
            raise ValueError('--at must not exceed the as-of clock')
        now = requested
    cal = Calendar.load(a.calendar) if a.calendar else DEFAULT_CALENDAR
    cal.trading_day(now.date(), a.exchange)
    master = security_master(a.security_master, now)
    data = MarketData(now, a.provider, a.csv_path, a.days, a.instruments, not a.no_fallback, cal)
    command = getattr(a, 'command', None)
    names = [] if role == 'analyst' or (role == 'alerts' and command in ('reset', 'list', 'clock')) else symbols(a, w)

    def output(doc, name, automatic=False):
        if (a.write or automatic) and not a.no_write:
            write(a.out or w/name, doc)
        return doc, 0

    if role == 'scanner':
        if command == 'universe':
            if master:
                selected = [s for s in sorted(master) if exchange_of(s) == a.exchange]
                source = 'Supplied dated security master'
            else:
                selected, source = universe(a.exchange, a.instruments)
            doc = envelope('scanner', now, symbols=selected, count=len(selected), source=source,
                           note='Directory discovery does not verify raw tick units or guarantee candle availability')
            if not a.no_write:
                path = Path(a.out or w/'universe.txt')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('\n'.join(selected)+'\n')
            return doc, 0
        if not names and (w/'universe.txt').exists():
            a.universe = str(w/'universe.txt')
            names = symbols(a, w)
        if not names:
            raise ValueError('Supply --symbols, --watchlist or a security-master universe first')
        doc = scan(data, names, now, cfg, command, cal, a.min_price, a.min_volume,
                   or_minutes=a.or_minutes, min_volume_x=a.min_volume_x, tick=a.tick, master=master)
        result, _ = output(doc, 'watchlist.json', True)
        return result, 1 if doc['failed'] and not doc['scanned'] else 0

    if role == 'entry':
        if not names:
            raise ValueError('Supply a symbol or watchlist')
        if command != 'watchlist' and len(names) != 1:
            raise ValueError('Use watchlist for multiple symbols')
        if a.every <= 0 or a.max_checks < 1 or a.max_checks > 10000:
            raise ValueError('Watch requires positive interval and 1..10000 checks')
        if command == 'watch' and a.as_of and a.max_checks != 1:
            raise ValueError('Replay watch uses a fixed clock; supply --max-checks 1')
        for check_index in range(a.max_checks if command == 'watch' else 1):
            results = []
            for sym in names:
                try:
                    result = entry(data.history(sym, a.interval), sym, now, cfg, a.setup, a.level,
                                   a.interval, a.or_minutes, a.min_volume_x, cal, data.source, a.tick, master)
                    result['provider_attempts'] = data.attempts.get((sym, a.interval), [])
                except (ValueError, OSError) as exc:
                    result = envelope('entry', now, symbol=sym, status='no-data', reason=str(exc), eligible=False)
                results.append(result)
            doc = results[0] if command != 'watchlist' else envelope('entry', now, results=results)
            if command != 'watchlist' and a.write:
                write(a.out or w/'entry.json', doc)
            if command != 'watch' or doc['status'] in ('triggered', 'expired', 'no-data') or check_index+1 == a.max_checks:
                return doc, 1 if doc.get('status') == 'no-data' else 0
            print(json.dumps(doc, allow_nan=False), flush=True)
            time.sleep(a.every)
            now = clock()
            data = MarketData(now, a.provider, a.csv_path, a.days, a.instruments, not a.no_fallback, cal)

    if role == 'exit':
        if command == 'show':
            return read(a.from_path or w/'plan.json'), 0
        if a.symbol and a.entry is not None:
            if (a.or_high is None) != (a.or_low is None):
                raise ValueError('Supply both --or-high and --or-low')
            info = dict(symbol=a.symbol, entry=a.entry, date=a.date, time=a.time, setup=a.setup,
                        interval=a.interval, opening_range=dict(high=a.or_high, low=a.or_low, minutes=a.or_minutes) if a.or_high is not None else None)
        else:
            info = read(a.from_path or w/'entry.json')
            if info.get('status') != 'triggered':
                raise ValueError('Entry has not triggered')
        tick_size = a.tick or info.get('tick')
        if tick_size is None:
            raise ValueError('Supply verified --tick for the plan')
        bars = data.history(info['symbol'], info.get('interval', '5m')) if str(a.stop).startswith('atr:') else None
        return output(plan(info, now, tick_size, cfg, a.stop, a.target or '2R', bars, cal, master, a.flat_by), 'plan.json')

    if role == 'timemachine':
        if command == 'chart':
            doc = read(a.from_path or w/'timemachine.json')
            # Bands/sample paths are portable JSON for a chart or spreadsheet.
            return dict(status=doc['status'], mc=doc.get('mc'), execution_enabled=False), 0
        p = read(a.from_path or w/'plan.json')
        require_budget(cfg)
        return output(timemachine(data.history(p['symbol'], '5m'), p, now, cfg, cal, a.replays, a.seed), 'timemachine.json')

    if role == 'budget':
        require_budget(cfg)
        paper_state = read(a.portfolio) if a.portfolio else None
        ledger_path = w/'state/paper.sqlite'
        if ledger_path.is_file():
            with contextlib.closing(Ledger(ledger_path,cfg['budget'])) as ledger:
                authoritative = ledger.portfolio()
            if paper_state is not None and digest(paper_state) != digest(authoritative):
                raise ValueError('Supplied portfolio differs from authoritative local paper ledger')
            paper_state = authoritative
        proposal = read(a.from_path or w/'plan.json')
        limit = proposal['entry'] if a.limit_price == 'plan' else a.limit_price
        doc = budget(proposal, now, cfg,portfolio=paper_state,
                     prices_doc=read(a.prices) if a.prices else None,limit_price=limit)
        return output(doc, 'orders.json') if doc['orders'] else (doc, 1)

    if role in ('risk', 'broker'):
        require_budget(cfg)
        ledger = Ledger(w/'state/paper.sqlite', cfg['budget'])
        with contextlib.closing(ledger):
            pf = ledger.portfolio()
            # Refresh the public file as an export of the authoritative paper ledger.
            if a.write:
                write(w/'portfolio.json', pf)
            if role == 'broker' and command in ('portfolio', 'log'):
                return (pf if command == 'portfolio' else ledger.state()), 0
            if role == 'broker' and command == 'fill':
                if not a.fingerprint or a.qty is None or a.price is None or not a.fill_id:
                    raise ValueError('Fill needs --fingerprint, --qty, --price and --fill-id')
                from india.data import Bar, timestamp
                observed = read(a.fill_bar) if a.fill_bar else None
                bar = Bar(timestamp(observed['time']), *(observed[k] for k in ('open', 'high', 'low', 'close', 'volume'))) if observed else None
                event = ledger.fill(a.fingerprint, a.qty, a.price, now, cfg, a.fill_id, bar=bar)
                write(w/'portfolio.json', ledger.portfolio())
                write(w/'state/broker.json', ledger.state())
                return event, 0
            if role == 'broker' and command == 'flatten':
                if not a.symbol or a.tick is None:
                    raise ValueError('Flatten needs --symbol and --tick')
                held = next((p for p in pf['positions'] if p['ticker'] == a.symbol), None)
                if held is None:
                    raise ValueError('No paper position to flatten')
                qty = a.qty or held['shares']
                if not 0 < qty <= held['shares']:
                    raise ValueError('Flatten quantity exceeds paper holding')
                return output(envelope('broker', now, status='proposed', orders=[dict(symbol=a.symbol, side='sell', qty=str(qty), type='market', time_in_force='day', tick=a.tick, why='Flatten local paper position')]), 'orders.json', True)
            orders = read(a.from_path or w/'orders.json')['orders']
            if a.tick is not None and any(abs(float(o['tick'])-a.tick) > 1e-9 for o in orders):
                raise ValueError('Supplied tick differs from the exact proposed order; regenerate the plan')
            q = quotes(data, list(dict.fromkeys(o['symbol'] for o in orders)), now)
            if role == 'risk':
                doc = risk(orders, now, cfg, pf, q, ledger.state(), master, cal)
                write(w/'state/risk.json', doc)
                write(w/'prices.json', q)
                return doc, int(bool(doc['block']))
            if not 1 <= a.index <= len(orders):
                raise ValueError('Invalid ticket index')
            order = orders[a.index-1]
            review = read(w/'state/risk.json')
            proposal = ticket(order, review, pf, now, cfg, q, master, cal, state=ledger.state())
            if command == 'show':
                return proposal, int(not proposal['ready'])
            if command == 'approve':
                if not a.reply:
                    raise ValueError('Pass the actual user reply with --reply; never manufacture consent')
                event = ledger.approve(proposal, a.reply, now)
                write(w/'state/broker.json', ledger.state())
                return event, 0
            if command == 'decline':
                event = dict(id='decline:'+order_hash(order), status='declined', at=now.isoformat(), fingerprint=order_hash(order), reply=a.reply or 'no')
                with ledger.db:
                    ledger.db.execute('INSERT INTO events VALUES (?, ?)', (event['id'], json.dumps(event)))
                return event, 0

    if role == 'alerts':
        m = upstream('alerts', cal, a.exchange)
        status = market_status(now, cal, a.exchange)
        def ring_time(rule, status):
            at = dt.time.fromisoformat(rule['at'])
            if rule.get('before_close') and status['closes']:
                closing = dt.time.fromisoformat(status['closes'])
                delta = dt.timedelta(minutes=closing.hour*60+closing.minute-(15*60+30))
                at = (dt.datetime.combine(now.date(),at)+delta).time()
            return at
        m.ring_time = ring_time
        rules_doc = read(w/'alerts.json', {'alerts': []})
        rules = rules_doc['alerts']
        if command == 'clock':
            return indian_words(status), 0
        if command == 'list':
            return rules_doc, 0
        if command == 'add':
            if a.from_plan:
                added = m.rules_from_plan(read(a.from_path or w/'plan.json'))
            elif a.opening_bell:
                added = [dict(type='opening_bell', id=a.id or 'opening-bell')]
            else:
                options = [('above', a.above), ('below', a.below), ('stop', a.stop), ('target', a.target), ('pct_move', a.pct), ('time', a.at)]
                chosen = [(kind, value) for kind, value in options if value is not None]
                if len(chosen) != 1:
                    raise ValueError('Choose exactly one alert condition')
                kind, value = chosen[0]
                row = dict(id=a.id or ((a.symbol or 'market')+'-'+kind).lower(), type=kind, rearm=a.rearm)
                if a.symbol:
                    row['symbol'] = a.symbol
                if kind == 'time':
                    row['at'] = value
                elif kind == 'pct_move':
                    row.update(pct=float(value), direction=a.direction)
                else:
                    row['price'] = float(value)
                if a.date:
                    row['date'] = a.date
                if a.label:
                    row['label'] = a.label
                added = [row]
            for row in added:
                m.validate(row)
                if any(r['id'] == row['id'] and r != row for r in rules):
                    raise ValueError('Alert ID already exists with different conditions')
                if row not in rules:
                    rules.append(row)
            write(w/'alerts.json', {'alerts': rules})
            return envelope('alerts', now, added=added), 0
        state = read(w/'state/alerts.json', {})
        if status['open']:
            events = state.setdefault('opening_sessions',{})
            for rule in rules:
                if rule['type'] == 'opening_bell' and events.get(rule['id']) != status['session_event']:
                    state.setdefault('rung',{}).pop(rule['id'],None)
                    events[rule['id']] = status['session_event']
        if command == 'reset':
            rid = a.id or (a.items[0] if a.items else None)
            if not rid:
                raise ValueError('Supply alert ID')
            state.setdefault('rung', {}).pop(rid, None)
            write(w/'state/alerts.json', state)
            return dict(reset=rid, execution_enabled=False), 0
        if command == 'quote':
            if not names:
                raise ValueError('Supply symbols or a portfolio/watchlist')
        else:
            names = list(dict.fromkeys(r['symbol'] for r in rules if r.get('symbol') and r['type'] not in ('time', 'opening_bell') and (not r.get('date') or r['date'] == now.date().isoformat())))
        q = quotes(data, names, now)
        if a.write or command == 'check':
            write(w/'prices.json', q)
        if command == 'quote':
            return q, int(bool(q['errors']))
        rings, updated = m.evaluate(rules, q['detail'], status, state, utc(now))
        for ring in rings:
            if 'trader-broker flatten' in ring['message']:
                ring['message'] = ring['message'].split(' If ')[0]+' Inspect the paper position and use ticket.py flatten with this workspace, configured capital and the verified --tick; then rerun risk and obtain user approval.'
        write(w/'state/alerts.json', updated)
        return envelope('alerts', now, rings=indian_words(rings), errors=q['errors'],market=indian_words(status),
                        note='One persisted check; no notification delivery or background process implied'), int(bool(q['errors']))

    if role == 'news':
        if not names:
            raise ValueError('Supply tickers or build a watchlist')
        doc, updated = news(names, now, read(w/'state/news.json', {}), a.hours, a.csv_path, a.feed_url, a.announcements, a.all)
        if not a.no_moves:
            price_data = MarketData(now, a.provider, a.prices_csv or a.csv_path, a.days, a.instruments, not a.no_fallback, cal)
            q = quotes(price_data, names, now)
            doc['moves'] = [dict(symbol=symbol, price=info['price'], as_of=info['time'],
                                 previous_completed_session_candle_close=info['prev_close'],
                                 pct=100*(info['price']/info['prev_close']-1) if info['prev_close'] else None,
                                 source=info['source']) for symbol, info in q['detail'].items()]
            doc['errors'].extend(q['errors'])
            doc['move_note'] = 'Completed-candle changes, not live quotes or official closing-price reconciliation'
        if not a.no_write:
            write(w/'state/news.json', updated)
            write(a.out or w/'news.json', doc)
        return doc, int(bool(doc['errors']) and not doc['items'])

    if role == 'analyst':
        if not a.filing:
            raise ValueError('Supply --filing with sourced Indian quarterly financial exports; SEC data does not cover this workflow')
        doc = financials(a.filing, a.symbol, now)
        if a.metrics:
            write(a.metrics, doc)
        return output(doc, 'metrics.json')

    if role == 'pelosi':
        if not a.csv_path:
            return envelope('pelosi', now, status='NOT_APPLICABLE', reason='US congressional tracker has no direct Indian equivalent; --csv accepts dated public Indian insider/PIT disclosures'), 0
        doc, updated = disclosures(a.csv_path, now, read(w/'state/disclosures.json', {}), names, a.all)
        if not a.no_write:
            write(w/'state/disclosures.json', updated)
            write(a.out or w/'disclosures.json', doc)
        return doc, 0
    raise ValueError('Unsupported role command')


def require_budget(cfg):
    if not cfg.get('budget'):
        raise ValueError('Supply --capital or budget in the INR config')


def dispatch(role, argv=None):
    """Keep the existing numerical/team CLI reachable through portable run.py."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ('analyze', 'backtest', 'history', 'research', 'doctor', 'replay', 'team', 'evaluate'):
        from india.skill_runner import main as old_main
        return old_main(role, args)
    return main(role, args)
