"""INR sizing, upstream risk rules and a transactional local paper ledger.

There is deliberately no brokerage client, connector dispatch or live mode.
"""
import datetime as dt
import json
import math
import re
import sqlite3

from india.execution import entry_fill, estimate_entry, size_position, validate_fill
from india.market import DEFAULT_CALENDAR, costs
from .common import config as checked_config, clock, digest, envelope, exchange_of, indian_words, number, upstream, utc
from .data import verify_tick


def one_way_cost(price, qty, side, exchange, config=None):
    c = costs(price, price, qty, exchange, config)
    parts = {k: c[k]/2 for k in ('brokerage', 'exchange', 'sebi', 'slippage')}
    if 'spread' in c:
        parts['spread'] = c['spread']/2
    parts['stt'] = c['stt'] if side == 'sell' else 0
    parts['stamp'] = c['stamp'] if side == 'buy' else 0
    parts['gst'] = c['gst']/2
    return dict(parts, total=sum(parts.values()))


def order_hash(order):
    return digest(order)


def budget(p, now, cfg):
    checked_config(**cfg)
    capital = number(cfg.get('budget'), 'budget')
    risk = number(cfg['max_loss_per_trade_inr'], 'max_loss_per_trade_inr')
    # Cash buffer and concentration/order limits constrain the cash available
    # to the same cost-aware binary-search sizing used by the Indian engine.
    available = min(capital*(1-cfg['cash_buffer_pct']/100), capital*cfg['max_position_pct']/100, cfg['max_order_inr'])
    result = size_position(p, available, risk, exchange_of(p['symbol']), cfg.get('costs'), cfg.get('entry_buffer_bps', 5.))
    if not result['qty'] or result['net_reward_risk'] < cfg['min_reward_risk']:
        return envelope('budget', now, status='BLOCKED', reason='COST_OR_RISK_LIMIT', orders=[], sizing=result)
    if result['qty']*p['entry'] < cfg['min_order_inr']:
        return envelope('budget', now, status='BLOCKED', reason='BELOW_MIN_ORDER', orders=[], sizing=result)
    order = dict(symbol=p['symbol'], side='buy', qty=str(result['qty']), type='market',
                 time_in_force='day', order_class='bracket', stop_loss_stop_price=str(p['stop']),
                 take_profit_limit_price=str(p['target']), plan=p, tick=p['tick'],
                 why='Completed-bar '+p['setup']+'; proposed local paper bracket only')
    return envelope('budget', now, status='proposed', orders=[order], sizing=result,
                    note='No broker-side bracket exists. Risk check and exact-ticket user approval precede a local paper record.')


def validate_portfolio(pf):
    if not isinstance(pf, dict) or pf.get('paper') is not True or pf.get('currency') != 'INR':
        raise ValueError('Portfolio must explicitly be paper INR state')
    number(pf['cash'], 'portfolio cash', zero=True)
    seen = set()
    for p in pf.get('positions', []):
        symbol = p.get('symbol', p.get('ticker'))
        exchange_of(symbol)
        qty = number(p.get('shares', p.get('qty')), 'position shares')
        if qty != int(qty) or symbol in seen:
            raise ValueError('Duplicate or fractional portfolio position')
        seen.add(symbol)
        if p.get('lots'):
            if sum(l['shares'] for l in p['lots']) != qty:
                raise ValueError('Portfolio lots do not reconcile to held shares')
            for lot in p['lots']:
                if int(lot['shares']) != lot['shares']:
                    raise ValueError('Fractional lot is unsupported')
                number(lot['shares'], 'lot shares')
                number(lot['cost'], 'lot cost')
    return pf


def risk(orders, now, cfg, portfolio, prices_doc, state=None, master=None, calendar=None):
    checked_config(**cfg)
    if not isinstance(orders, list) or not orders:
        raise ValueError('No proposed orders to check')
    validate_portfolio(portfolio)
    cal = calendar or DEFAULT_CALENDAR
    symbols = {o['symbol'] for o in orders}
    exchanges = {exchange_of(s) for s in symbols}
    if len(exchanges) != 1:
        raise ValueError('Check NSE and BSE batches separately against their exchange calendars')
    exchange = exchanges.pop()
    cal.require_strategy_session(now.date(), exchange)
    m = upstream('risk', cal, exchange)
    m.TICKER = re.compile(r'^[A-Z0-9&.\-]+\.(NS|BO)$')
    m.fingerprint = order_hash
    legacy_cfg = {k.replace('_inr', '_usd'): v for k, v in cfg.items() if k != 'costs'}
    legacy_cfg.update(pdt_guard=False, allow_short=False, allow_options=False, allow_margin=False, require_stop=True)
    prices, times = {}, {}
    for symbol, price in prices_doc.get('prices', {}).items():
        prices[symbol] = number(price, 'price')
        info = prices_doc.get('detail', {}).get(symbol, {})
        if info.get('time'):
            times[symbol] = utc(clock(info['time']))
    raw = m.check(orders, legacy_cfg, portfolio=portfolio, prices=prices,
                  state=state or {}, now=utc(now), price_times=times)
    result = indian_words(raw)
    used_loss = result['today']['loss_at_risk']
    cash = float(portfolio['cash'])
    for order, review in zip(orders, result['results']):
        sym, side = order['symbol'], order.get('side')
        extra = []
        if order.get('time_in_force', 'day') != 'day' or order.get('extended_hours'):
            extra.append('Indian paper orders support regular-session DAY instructions only')
        if order.get('type', 'market') not in ('market', 'limit'):
            extra.append('Only market or limit entry/exit tickets are supported; protective levels are paper plans')
        q = number(order.get('qty', 0), 'qty', zero=True)
        if not q or q != int(q) or order.get('notional') is not None:
            extra.append('Only positive whole-share orders are supported')
        check = verify_tick(sym, order.get('tick'), master or {})
        review['tick_verification'] = check
        if check['status'] != 'VERIFIED':
            extra.append('Applicable tick metadata '+check['status'])
        p = order.get('plan', {})
        if side == 'buy':
            if not p.get('signal_time'):
                extra.append('Entry requires a completed dated signal')
            else:
                step = 1 if p.get('interval') == '1m' else 5
                confirmation = clock(p['signal_time'])+dt.timedelta(minutes=step)
                if confirmation > now or now-confirmation > dt.timedelta(minutes=step) or confirmation.date() != now.date():
                    extra.append('Entry signal is forming, expired or belongs to another session')
            if clock(p.get('as_of')) > now:
                extra.append('Plan cutoff is in the future')
            if sym not in times or not 0 <= (utc(now)-times[sym]).total_seconds() <= 900:
                extra.append('A dated fresh completed price is required')
        tick = number(order.get('tick'), 'tick')
        for field in ('limit_price', 'stop_price', 'stop_loss_stop_price', 'take_profit_limit_price'):
            if field in order:
                value = number(order[field], field)
                if not math.isclose(value/tick, round(value/tick), abs_tol=1e-7):
                    extra.append(field+' does not align with tick')
        if q == int(q) and q > 0 and review['entry_ref']:
            e = review['entry_ref']
            if side == 'buy' and p.get('entry'):
                reference = dict(p, entry=max(float(e), float(p['entry'])))
                e = estimate_entry(reference, cfg.get('entry_buffer_bps', 5.))
                if order.get('type') == 'limit':
                    e = float(order['limit_price'])
                review['estimated_entry'] = e
                if q*e > cfg['max_order_inr'] or q*e > cfg['budget']*cfg['max_position_pct']/100:
                    extra.append('Estimated fill breaches order or position cap')
            fee = one_way_cost(e, int(q), side, exchange, cfg.get('costs'))['total']
            if side == 'buy' and review.get('stop') and review.get('target'):
                stop, target = review['stop'], review['target']
                loss = q*(e-stop)+costs(e, stop, int(q), exchange, cfg.get('costs'))['total']
                reward = q*(target-e)-costs(e, target, int(q), exchange, cfg.get('costs'))['total']
                review.update(max_loss=loss, net_reward=reward, net_reward_risk=reward/loss if loss > 0 else 0)
                review['info'] = [line for line in review['info'] if not line.startswith('max loss ')]
                review['info'].append('Estimated loss including costs: ₹%.2f; net reward/risk: %.3f' % (loss, reward/loss if loss > 0 else 0))
                if loss > cfg['max_loss_per_trade_inr']+1e-8:
                    extra.append('Loss including costs exceeds per-trade limit')
                if loss <= 0 or reward/loss < cfg['min_reward_risk']:
                    extra.append('Net reward/risk below minimum')
                if used_loss+loss > cfg['max_daily_loss_inr']+1e-8:
                    extra.append('Daily risk budget including costs exceeded')
            if side == 'buy' and cash-q*e-fee < cfg['budget']*cfg['cash_buffer_pct']/100-1e-8:
                extra.append('Cash buffer would be breached after entry costs')
            if review['verdict'] == 'PASS' and not extra:
                cash += q*e-fee if side == 'sell' else -q*e-fee
                if side == 'buy':
                    used_loss += review['max_loss']
        review['reasons'].extend(extra)
        if extra:
            review['verdict'] = 'BLOCK'
    result.update(envelope('risk', now), as_of=now.isoformat(), config_sha256=digest(cfg),
                   portfolio_sha256=digest(portfolio), prices_sha256=digest(prices_doc),
                   metadata_sha256=digest(master or {}),
                   **{'pass': sum(r['verdict'] == 'PASS' for r in result['results']),
                      'block': sum(r['verdict'] == 'BLOCK' for r in result['results'])})
    result['cash_after'] = cash
    return result


def ticket(order, review_doc, portfolio, now, cfg, prices_doc, master, calendar=None, state=None):
    """Recheck the exact current inputs; a previous PASS is not a reusable permit."""
    reasons = []
    if review_doc.get('config_sha256') != digest(cfg) or review_doc.get('portfolio_sha256') != digest(portfolio):
        reasons.append('Configuration or paper portfolio changed; rerun risk')
    if review_doc.get('metadata_sha256') != digest(master):
        reasons.append('Security metadata changed; rerun risk')
    age = (now-clock(review_doc.get('as_of'))).total_seconds()
    if not 0 <= age <= 300:
        reasons.append('Risk decision is expired or future-dated')
    previous = next((r for r in review_doc.get('results', []) if r['fingerprint'] == order_hash(order)), None)
    if previous is None or previous['verdict'] != 'PASS':
        reasons.append('This exact order has no risk PASS')
    if any(event.get('fingerprint') == order_hash(order) and event.get('status') in ('approved', 'declined', 'placed', 'filled') for event in (state or {}).get('orders', [])):
        reasons.append('This paper ticket already has a decision or fill in the ledger')
    current = risk([order], now, cfg, portfolio, prices_doc, state=state, master=master, calendar=calendar)
    reasons.extend(current['results'][0]['reasons'])
    return envelope('broker', now, fingerprint=order_hash(order), ready=not reasons,
                    status='READY_PAPER_TICKET' if not reasons else 'BLOCKED', reasons=reasons,
                    order=order, max_loss=current['results'][0].get('max_loss'), config_sha256=digest(cfg),
                    tick_verification=current['results'][0]['tick_verification'],
                    note='User approval permits only a local simulated fill; no external order is sent. Embedded plan metadata records creation-time inputs; top-level tick_verification is the current check.')


class Ledger:
    """SQLite transactions bind approval, fill, cash, lots and idempotency."""
    def __init__(self, path, capital):
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO state VALUES (1, ?)', (json.dumps(dict(paper=True, currency='INR', cash=number(capital, 'capital'), initial_capital=number(capital, 'capital'), positions=[], realized_pnl=0)),))
        self.db.commit()
        starting = self.portfolio().get('initial_capital')
        if starting is not None and abs(starting-float(capital)) > 1e-8:
            self.db.close()
            raise ValueError('This ledger was initialized with different capital; choose a new workspace for a different paper account')

    def close(self):
        self.db.close()

    def portfolio(self):
        return validate_portfolio(json.loads(self.db.execute('SELECT document FROM state WHERE id=1').fetchone()[0]))

    def events(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT document FROM events ORDER BY rowid')]

    def state(self):
        return {'orders': self.events()}

    def approve(self, paper_ticket, reply, now):
        if not paper_ticket.get('ready') or paper_ticket.get('execution_enabled') is not False:
            raise ValueError('A current ready paper ticket is required')
        if paper_ticket['fingerprint'] != order_hash(paper_ticket['order']):
            raise ValueError('Paper ticket contents changed')
        m = upstream('broker')
        ok, why = m.parse_yes(reply, paper_ticket['order'])
        if not ok:
            raise ValueError(indian_words(why))
        named = re.findall(r'[A-Z0-9&.\-]+\.(?:NS|BO)\b', (reply or '').upper())
        if any(symbol != paper_ticket['order']['symbol'] for symbol in named):
            raise ValueError('Reply names a different instrument; ask for this exact paper ticket')
        if not 0 <= (now-clock(paper_ticket['created_at'])).total_seconds() <= 60:
            raise ValueError('Paper ticket expired; show it again')
        key = 'approval:'+paper_ticket['fingerprint']
        event = dict(id=key, status='approved', at=now.isoformat(), date=now.date().isoformat(),
                     fingerprint=paper_ticket['fingerprint'], reply=reply, order=paper_ticket['order'],
                     max_loss=paper_ticket.get('max_loss'), portfolio_sha256=digest(self.portfolio()),
                     config_sha256=paper_ticket['config_sha256'])
        try:
            with self.db:
                self.db.execute('INSERT INTO events VALUES (?, ?)', (key, json.dumps(event)))
        except sqlite3.IntegrityError:
            raise ValueError('This exact paper ticket already has a recorded decision') from None
        return event

    def fill(self, fp, qty, price, now, cfg, event_id, *, bar=None):
        checked_config(**cfg)
        q, price = number(qty, 'qty'), number(price, 'price')
        if q != int(q) or not event_id:
            raise ValueError('Fill needs whole shares and an idempotency ID')
        q = int(q)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone():
                raise ValueError('Duplicate fill ID; nothing applied')
            row = self.db.execute('SELECT document FROM events WHERE id=?', ('approval:'+fp,)).fetchone()
            if not row:
                raise ValueError('No explicit user approval for this exact paper order')
            approval = json.loads(row[0])
            if approval['config_sha256'] != digest(cfg):
                raise ValueError('Configuration changed after approval; rerun risk')
            if not 0 <= (now-clock(approval['at'])).total_seconds() <= 300:
                raise ValueError('Paper approval expired')
            if self.db.execute('SELECT 1 FROM events WHERE id=?', ('filled:'+fp,)).fetchone():
                raise ValueError('Paper order already filled')
            order = approval['order']
            if q != int(order['qty']):
                raise ValueError('Only one full simulated fill of the approved quantity is supported')
            if not math.isclose(price/order['tick'], round(price/order['tick']), abs_tol=1e-7):
                raise ValueError('Fill price does not align with tick')
            pf = self.portfolio()
            if digest(pf) != approval['portfolio_sha256']:
                raise ValueError('Portfolio changed after approval; rerun risk')
            sym, side = order['symbol'], order['side']
            if order.get('type') == 'limit':
                limit = float(order['limit_price'])
                if (side == 'buy' and price > limit) or (side == 'sell' and price < limit):
                    raise ValueError('Simulated fill violates the approved limit price')
            fill_doc = {}
            if side == 'buy':
                if bar is None:
                    raise ValueError('Paper entry requires an observed fill candle; supply --fill-bar JSON')
                fill_doc = entry_fill(order['plan'], bar, clock(approval['at']))
                if bar.time > now:
                    raise ValueError('Fill candle open is in the future')
                if abs(price-fill_doc['entry']) > 1e-8:
                    raise ValueError('Supplied price differs from canonical candle-open fill')
                if order.get('type') == 'limit' and price > float(order['limit_price']):
                    raise ValueError('Candle open exceeds approved limit; intrabar limit fills unsupported')
            fee = one_way_cost(price, q, side, exchange_of(sym), cfg.get('costs'))['total']
            held = next((p for p in pf['positions'] if p['ticker'] == sym), None)
            pnl = 0
            if side == 'buy':
                p = order['plan']
                if not p['stop'] < price < p['target']:
                    raise ValueError('Simulated entry must lie between approved stop and target')
                available = min(pf['cash']-cfg['budget']*cfg['cash_buffer_pct']/100,
                                cfg['max_order_inr'], cfg['budget']*cfg['max_position_pct']/100-price*(held['shares'] if held else 0))
                values = validate_fill(p, price, q, available, cfg['max_loss_per_trade_inr'],
                                       exchange_of(sym), cfg.get('costs'), cfg['min_reward_risk'])
                loss = values['max_loss_estimate']
                prior_loss = sum(float(e.get('max_loss') or 0) for e in self.events()
                                 if e.get('status') == 'placed' and e.get('kind') == 'entry' and e.get('date') == now.date().isoformat())
                if prior_loss+loss > cfg['max_daily_loss_inr']:
                    raise ValueError('Actual fill breaches daily cost-inclusive risk budget')
                reward = (p['target']-price)*q-costs(price, p['target'], q, exchange_of(sym), cfg.get('costs'))['total']
                if loss > cfg['max_loss_per_trade_inr'] or reward/loss < cfg['min_reward_risk']:
                    raise ValueError('Fill price would breach approved cost/risk limits')
                if pf['cash']-price*q-fee < cfg['budget']*cfg['cash_buffer_pct']/100:
                    raise ValueError('Insufficient paper cash after buffer and costs')
                if price*q > cfg['max_order_inr'] or price*(q+(held['shares'] if held else 0)) > cfg['budget']*cfg['max_position_pct']/100:
                    raise ValueError('Fill price would breach paper order or concentration caps')
                if held is None:
                    held = dict(ticker=sym, shares=0, lots=[])
                    pf['positions'].append(held)
                held['shares'] += q
                held['lots'].append(dict(id=event_id, date=now.date().isoformat(), shares=q, cost=price*q+fee))
                pf['cash'] -= price*q+fee
            elif side == 'sell':
                if held is None or held['shares'] < q:
                    raise ValueError('Cannot sell more paper shares than held')
                left, basis = q, 0
                for lot in held['lots']:
                    take = min(left, lot['shares'])
                    part = lot['cost']*take/lot['shares']
                    basis += part
                    lot['shares'] -= take
                    lot['cost'] -= part
                    left -= take
                    if not left:
                        break
                held['lots'] = [l for l in held['lots'] if l['shares']]
                held['shares'] -= q
                if not held['shares']:
                    pf['positions'].remove(held)
                pnl = price*q-fee-basis
                pf['cash'] += price*q-fee
                pf['realized_pnl'] += pnl
            else:
                raise ValueError('Only buy or sell supported')
            validate_portfolio(pf)
            event = dict(id=event_id, status='placed', simulation=True, paper=True, execution_enabled=False,
                         fingerprint=fp, symbol=sym, side=side, qty=q, price=price, fees=fee,
                         at=now.isoformat(), date=now.date().isoformat(), kind='entry' if side == 'buy' else 'exit',
                         max_loss=loss if side == 'buy' else approval.get('max_loss'), realized_pnl=pnl,
                         **fill_doc)
            self.db.execute('UPDATE state SET document=? WHERE id=1', (json.dumps(pf),))
            self.db.execute('INSERT INTO events VALUES (?, ?)', (event_id, json.dumps(event)))
            self.db.execute('INSERT INTO events VALUES (?, ?)', ('filled:'+fp, json.dumps(dict(status='filled', fingerprint=fp))))
            self.db.commit()
            return event
        except Exception:
            self.db.rollback()
            raise
