"""Restart-safe, causal, risk-checked automatic paper entries and bracket exits."""
import datetime as dt
import json
import math
from pathlib import Path

from india.data import IST
from india.execution import entry_fill, exit_fill, tick_price
from india.market import DEFAULT_CALENDAR
from india.security import security_records
from workflow.common import clock, config, digest, write
from workflow.paper import Ledger, budget, risk, ticket, one_way_cost
from workflow.strategy import entry, plan
from .policy import checked, mandate


def quote(symbol, bar, source):
    return dict(currency='INR', paper=True, execution_enabled=False, prices={symbol:bar.close},
                detail={symbol:dict(time=(bar.time+dt.timedelta(minutes=5)).isoformat(),source=source)})


class PaperEngine:
    def __init__(self, workspace, settings, calendar=None):
        self.root = Path(workspace)/'automation'
        self.settings, self.cal = checked(settings), calendar or DEFAULT_CALENDAR
        self.cfg = config(**self.settings['risk'])
        self.policy = mandate(dict(self.settings,enabled=True))
        self.ledger = Ledger(self.root/'state/paper.sqlite',self.cfg['budget'])
        self.ledger.db.execute('CREATE TABLE IF NOT EXISTS automation_state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
        self.ledger.db.execute('CREATE TABLE IF NOT EXISTS automation_decisions (id TEXT PRIMARY KEY, at TEXT NOT NULL, document TEXT NOT NULL)')
        self.ledger.db.execute('CREATE TABLE IF NOT EXISTS automation_equity (at TEXT PRIMARY KEY, equity REAL NOT NULL, realized REAL NOT NULL)')
        if 'capital_flow' not in {row[1] for row in self.ledger.db.execute('PRAGMA table_info(automation_equity)')}:
            self.ledger.db.execute('ALTER TABLE automation_equity ADD COLUMN capital_flow REAL NOT NULL DEFAULT 0')
        self.ledger.db.commit()
        row = self.ledger.db.execute('SELECT document FROM automation_state WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else dict(pending=None,positions={},attempted_signals=[])
        self.recover()

    def close(self):
        self.ledger.close()

    def save(self):
        with self.ledger.db:
            self.ledger.db.execute('INSERT OR REPLACE INTO automation_state VALUES (1,?)',(json.dumps(self.state),))
        write(self.root/'portfolio.json',self.ledger.portfolio())

    def decision(self, now, status, symbol=None, **fields):
        doc = dict(at=now.isoformat(),status=status,symbol=symbol,paper=True,execution_enabled=False,**fields)
        with self.ledger.db:
            self.ledger.db.execute('INSERT OR IGNORE INTO automation_decisions VALUES (?,?,?)',
                                   (digest(doc),now.isoformat(),json.dumps(doc)))
        return doc

    def recover(self):
        """Recover committed fills/approvals even if a crash preceded state save."""
        events = self.ledger.events()
        approvals = {row['fingerprint']:row for row in events if row.get('status') == 'approved'}
        filled = {row['fingerprint']:row for row in events if row.get('status') == 'placed'}
        cancelled = {row['fingerprint'] for row in events if row.get('status') == 'automation_cancelled'}
        policies = {row['policy_sha256']:row['policy'] for row in events if row.get('status') == 'paper_policy'}
        actual = {row['ticker']:row for row in self.ledger.portfolio()['positions']}
        self.state['positions'] = {s:p for s,p in self.state.get('positions',{}).items() if s in actual}
        for fp, approval in approvals.items():
            if approval.get('authorization',{}).get('kind') != 'STANDING_PAPER_AUTOMATION_POLICY':
                continue
            order = approval['order']
            if order['side'] != 'buy': continue
            signal_id = order['symbol']+':'+order['plan']['signal_time']
            if signal_id not in self.state['attempted_signals']:
                self.state['attempted_signals'].append(signal_id)
            policy = policies[approval['authorization']['policy_sha256']]
            context = dict(order=order,plan=order['plan'],fingerprint=fp,
                           submitted_at=approval['at'],policy=policy,cfg=config(**policy['settings']['risk']))
            active_lots = {lot['id'] for lot in actual.get(order['symbol'],{}).get('lots',[])}
            if fp in filled and filled[fp]['id'] in active_lots and order['symbol'] not in self.state['positions']:
                fill = filled[fp]
                context['last_bar'] = (clock(fill['entry_time'])-dt.timedelta(minutes=5)).isoformat()
                context['entry_time'] = fill['entry_time']
                self.state['positions'][order['symbol']] = context
            elif fp not in filled and fp not in cancelled and self.state.get('pending') is None:
                self.state['pending'] = context
        if self.state.get('pending') and self.state['pending']['fingerprint'] in filled:
            self.state['pending'] = None
        unknown = set(actual)-set(self.state['positions'])
        if unknown:
            raise ValueError('Automated account has positions without recoverable entry plans: '+', '.join(sorted(unknown)))
        self.state['marks'] = {s:m for s,m in self.state.get('marks',{}).items() if s in actual}
        self.state['unresolved_positions'] = [s for s in self.state.get('unresolved_positions',[]) if s in actual]
        self.save()

    def cancel(self, now, reason):
        pending = self.state.get('pending')
        if not pending: return
        fp = pending['fingerprint']
        with self.ledger.db:
            self.ledger.db.execute('INSERT OR IGNORE INTO events VALUES (?,?)',
                ('automation-cancel:'+fp,json.dumps(dict(status='automation_cancelled',fingerprint=fp,at=now.isoformat(),reason=reason))))
        self.decision(now,'CANCELLED',pending['order']['symbol'],reason=reason)
        self.state['pending'] = None
        self.save()

    def required_symbols(self):
        names = set(self.state['positions'])
        if self.state.get('pending'): names.add(self.state['pending']['order']['symbol'])
        return sorted(names)

    def record_equity(self, now):
        pf = self.ledger.portfolio()
        marks = self.state.get('marks',{})
        if any(row['ticker'] not in marks for row in pf['positions']):
            return
        equity = pf.get('funded_capital',pf['initial_capital'])+pf['realized_pnl']+sum(row['unrealized_pnl'] for row in marks.values())
        previous = self.ledger.db.execute('SELECT at,equity FROM automation_equity ORDER BY at DESC LIMIT 1').fetchone()
        if previous and previous[0][:10] == now.date().isoformat() and abs(previous[1]-equity) < 1e-8:
            return
        with self.ledger.db:
            self.ledger.db.execute('INSERT OR IGNORE INTO automation_equity (at,equity,realized,capital_flow) VALUES (?,?,?,?)',
                                   (now.isoformat(),equity,pf['realized_pnl'],pf.get('net_capital_flows',0)))

    def consider(self, candidates, data, master, now):
        if not self.settings['enabled'] or self.state.get('pending') or self.state.get('unresolved_positions'):
            return None
        if len(self.state['positions']) >= self.settings['selection']['max_open_positions']:
            return self.decision(now,'POSITION_LIMIT')
        if not self.cal.trading_day(now.date(),'NSE') or not self.cfg['no_entries_before'] <= now.strftime('%H:%M') <= self.cfg['no_entries_after']:
            return None
        today = [e for e in self.ledger.events() if e.get('status') == 'placed' and e.get('date') == now.date().isoformat()]
        if sum(e.get('side') == 'buy' for e in today) >= self.cfg['max_trades_per_day']:
            return self.decision(now,'DAILY_TRADE_LIMIT')
        pnl = sum(e.get('realized_pnl',0) for e in today if e.get('side') == 'sell')
        open_pnl = sum(min(0,row.get('unrealized_pnl',0)) for row in self.state.get('marks',{}).values())
        if pnl+open_pnl <= -self.cfg['max_daily_loss_inr']:
            return self.decision(now,'DAILY_LOSS_LIMIT',realized_pnl=pnl)
        applicable = security_records(list(master.values()),now)
        rules = self.settings['selection']
        for candidate in candidates:
            symbol = candidate['symbol']
            if symbol in self.state['positions']: continue
            try:
                bars = data.history(symbol,'5m')
                latest = bars[-1]
                if latest.time.date() != now.date() or now-latest.time-dt.timedelta(minutes=5) > dt.timedelta(seconds=self.settings['max_data_delay_seconds']):
                    raise ValueError('STALE_ENTRY_CANDLES')
                if symbol not in applicable: raise ValueError('NO_DATED_TICK_METADATA')
                tick = applicable[symbol]['tick_size']
                signal = entry(bars,symbol,now,self.cfg,calendar=self.cal,source=data.source,
                               tick=tick,master=applicable,volume_basis='opening-range',min_volume_x=rules['min_volume_x'] or None)
                if not signal['eligible']: raise ValueError('NO_FRESH_ENTRY')
                signal_id = symbol+':'+signal['signal_time']
                if signal_id in self.state['attempted_signals']: continue
                turnover = sum(b.close*b.volume for b in bars if b.time.date() == now.date())
                if turnover < rules['min_session_turnover_inr']: raise ValueError('SESSION_LIQUIDITY_FILTER')
                p = plan(signal,now,tick,self.cfg,self.settings['exits']['stop'],self.settings['exits']['target'],calendar=self.cal,master=applicable)
                prices = quote(symbol,latest,data.source)
                sized = budget(p,now,self.cfg,portfolio=self.ledger.portfolio(),prices_doc=prices)
                if not sized['orders']: raise ValueError(sized['reason'])
                order = sized['orders'][0]
                if int(order['qty']) > math.floor(latest.volume*rules['max_participation']):
                    raise ValueError('LAGGED_VOLUME_CAPACITY')
                review = risk([order],now,self.cfg,self.ledger.portfolio(),prices,self.ledger.state(),applicable,self.cal)
                proposed = ticket(order,review,self.ledger.portfolio(),now,self.cfg,prices,applicable,self.cal,self.ledger.state())
                if not proposed['ready']: raise ValueError('; '.join(proposed['reasons']))
                self.ledger.authorize_automation(proposed,self.policy,now)
                self.state['pending'] = dict(order=order,plan=p,fingerprint=proposed['fingerprint'],
                                             submitted_at=now.isoformat(),cfg=self.cfg,policy=self.policy,
                                             selection=candidate)
                self.state['attempted_signals'].append(signal_id)
                self.save()
                return self.decision(now,'PENDING_ENTRY',symbol,score=candidate['score'],qty=int(order['qty']),
                                     planned_entry=p['entry'],stop=p['stop'],target=p['target'],risk_sha256=digest(review))
            except (ValueError,OSError,KeyError,IndexError,TypeError) as exc:
                self.decision(now,'REJECTED',symbol,reason=str(exc),score=candidate.get('score'))
        return None

    def manage(self, data, master, now):
        self.state['unresolved_positions'] = []
        self.state['marks'] = {}
        # Resolve already-held exposures before accepting a pending new fill.
        # A protective exit changes the portfolio hash and cancels that pending
        # entry rather than applying it against an obsolete risk snapshot.
        self._manage_positions(data,master,now)
        filled = self._manage_pending(data,master,now)
        if filled:
            self._manage_positions(data,master,now,only=filled)
        self.save()

    def _manage_pending(self, data, master, now):
        pending = self.state.get('pending')
        if pending:
            symbol = pending['order']['symbol']
            try:
                if not self.settings['enabled'] or digest(pending['cfg']) != digest(self.cfg):
                    self.cancel(now,'ENTRY_PAUSED_OR_POLICY_CHANGED')
                else:
                    submitted = clock(pending['submitted_at'])
                    opening = submitted.replace(hour=9,minute=15,second=0,microsecond=0)
                    expected = opening+dt.timedelta(minutes=5*math.ceil((submitted-opening).total_seconds()/300))
                    if now >= expected+dt.timedelta(minutes=5):
                        bars = data.history(symbol,'5m')
                        bar = next((b for b in bars if b.time == expected),None)
                        previous = next((b for b in bars if b.time+dt.timedelta(minutes=5) == expected),None)
                        if bar is None:
                            if now > expected+dt.timedelta(minutes=5,seconds=self.settings['max_data_delay_seconds']):
                                self.cancel(now,'FILL_CANDLE_MISSING')
                        else:
                            qty = int(pending['order']['qty'])
                            if previous is None or qty > math.floor(previous.volume*self.settings['selection']['max_participation']):
                                raise ValueError('FILL_LAGGED_VOLUME_CAPACITY')
                            event = self.ledger.fill(pending['fingerprint'],qty,
                                tick_price(bar.open,pending['order']['tick'],True),bar.time,pending['cfg'],
                                'auto-entry:'+pending['fingerprint'],bar=bar,observed_at=now)
                            position = dict(pending,entry_time=event['entry_time'],last_bar=(bar.time-dt.timedelta(minutes=5)).isoformat())
                            self.state['positions'][symbol] = position
                            self.state['pending'] = None
                            self.save()
                            self.decision(now,'ENTRY_FILLED',symbol,qty=qty,price=event['price'],fees=event['fees'])
                            return symbol
            except (ValueError,OSError,KeyError,IndexError,TypeError) as exc:
                self.cancel(now,str(exc))

    def _manage_positions(self, data, master, now, only=None):
        for symbol, position in list(self.state['positions'].items()):
            if only is not None and symbol != only:
                continue
            try:
                bars = data.history(symbol,'5m')
                latest = bars[-1]
                held = next(row for row in self.ledger.portfolio()['positions'] if row['ticker'] == symbol)
                fresh = latest.time.date() == now.date() and now-latest.time-dt.timedelta(minutes=5) <= dt.timedelta(seconds=self.settings['max_data_delay_seconds'])
                if fresh:
                    liquidation = one_way_cost(latest.close,held['shares'],'sell','NSE',position['cfg'].get('costs'))['total']
                    self.state['marks'][symbol] = dict(price=latest.close,at=(latest.time+dt.timedelta(minutes=5)).isoformat(),
                        source=data.source,unrealized_pnl=held['shares']*latest.close-liquidation-sum(l['cost'] for l in held['lots']))
                else:
                    self.state['unresolved_positions'].append(symbol)
                rows = [b for b in bars if b.time > clock(position['last_bar']) and b.time >= clock(position['entry_time']) and b.time+dt.timedelta(minutes=5) <= now]
                if not rows:
                    self.decision(now,'AWAITING_POSITION_CANDLES',symbol)
                    continue
                for bar in rows:
                    p = position['plan']
                    expected = clock(position['last_bar'])+dt.timedelta(minutes=5)
                    missing = []
                    if bar.time.date() > clock(position['entry_time']).date():
                        outcome, price = 'late_flatten', bar.open
                    elif bar.time > expected:
                        missing = [(expected+dt.timedelta(minutes=5*i)).isoformat() for i in range(int((bar.time-expected).total_seconds()//300))]
                        outcome, price = 'data_gap_exit', bar.open
                    else:
                        event = exit_fill(p,bar)
                        flat = clock(p['date']+'T'+p['flat_by']+':00+05:30')
                        if event:
                            outcome, price = event['outcome'],event['exit']
                        elif bar.time+dt.timedelta(minutes=5) > flat:
                            outcome, price = 'late_flatten',bar.open
                        else:
                            position['last_bar'] = bar.time.isoformat()
                            self.save()
                            continue
                    qty = next(row['shares'] for row in self.ledger.portfolio()['positions'] if row['ticker'] == symbol)
                    at = bar.time+dt.timedelta(minutes=5)
                    cfg, policy = position['cfg'],position['policy']
                    applicable = security_records(list(master.values()),at)
                    order = dict(symbol=symbol,side='sell',qty=str(qty),type='market',time_in_force='day',tick=p['tick'],
                                 why='Automatic paper protective '+outcome,automation_exit=dict(plan=p,outcome=outcome,
                                 entry_fingerprint=position['fingerprint'],missing_bars=missing))
                    prices = quote(symbol,bar,data.source)
                    review = risk([order],at,cfg,self.ledger.portfolio(),prices,self.ledger.state(),applicable,self.cal)
                    proposal = ticket(order,review,self.ledger.portfolio(),at,cfg,prices,applicable,self.cal,self.ledger.state())
                    if not proposal['ready']: raise ValueError('; '.join(proposal['reasons']))
                    self.ledger.authorize_automation(proposal,policy,at,observed_at=now)
                    filled = self.ledger.fill(proposal['fingerprint'],qty,tick_price(price,p['tick']),at,cfg,
                                              'auto-exit:'+proposal['fingerprint'],bar=bar,observed_at=now)
                    self.state['positions'].pop(symbol,None)
                    self.save()
                    self.decision(now,'EXIT_FILLED',symbol,outcome=outcome,price=filled['price'],net_pnl=filled['realized_pnl'])
                    break
            except (ValueError,OSError,KeyError,IndexError,TypeError,StopIteration) as exc:
                if symbol not in self.state['unresolved_positions']:
                    self.state['unresolved_positions'].append(symbol)
                self.decision(now,'POSITION_UNRESOLVED',symbol,reason=str(exc))
        self.state['marks'] = {s:m for s,m in self.state['marks'].items() if s in self.state['positions']}
        self.state['unresolved_positions'] = [s for s in self.state['unresolved_positions'] if s in self.state['positions']]
        self.save()
