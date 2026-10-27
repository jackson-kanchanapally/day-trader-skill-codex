"""Read the authoritative paper ledger without creating accounts or recording fills."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from india.data import symbol_name, timestamp
from workflow.paper import order_hash, validate_portfolio
from workflow.common import digest
from .decision import finite
from .research import inside


def paper_journal(workspace):
    root = Path(workspace).resolve()
    path = inside(root, root/'state'/'paper.sqlite')
    empty = dict(status='NO_ACCOUNT', entries=[], exits=[], positions=[], groups=[], equity_curve=[],
                 cash=None, realized_pnl=None, total_costs=None, win_rate=None, profit_factor=None,
                 execution_enabled=False, note='No paper account exists in this workspace. No account or sample fills were created.')
    if not path.is_file():
        return empty
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            pf = validate_portfolio(json.loads(db.execute('SELECT document FROM state WHERE id=1').fetchone()[0]))
            events = [json.loads(r[0]) for r in db.execute('SELECT document FROM events ORDER BY rowid')]
        approvals = {e['fingerprint']: e for e in events if e.get('status') == 'approved'}
        policies = {e['policy_sha256']:e['policy'] for e in events if e.get('status') == 'paper_policy'}
        lots, entries, exits, groups, curve = {}, [], [], {}, []
        net, total_costs, gains, losses, wins = 0., 0., 0., 0., 0
        cash = pf.get('initial_capital')
        capital_flows, adjustments = 0., []
        if not finite(cash):
            raise ValueError('Missing initial paper capital')
        for fill in events:
            if fill.get('status') == 'capital_adjusted':
                from automation.capital import validate_request
                request=validate_request(fill['request'])
                before=pf['initial_capital']+capital_flows
                target=request['settings']['risk']['budget']
                if (fill.get('paper') is not True or fill.get('execution_enabled') is not False
                        or fill.get('id') != 'capital-change:'+request['id']
                        or timestamp(fill['at']) < timestamp(request['requested_at'])
                        or any(l['qty'] for queue in lots.values() for l in queue)
                        or not all(finite(fill.get(k)) for k in ('from_capital','to_capital','amount'))
                        or abs(fill['from_capital']-before) > .01 or abs(fill['to_capital']-target) > .01
                        or abs(fill['amount']-(target-before)) > .01 or cash+fill['amount'] < 0):
                    raise ValueError('Invalid or unreconciled paper capital adjustment')
                cash+=fill['amount'];capital_flows+=fill['amount'];adjustments.append(fill)
                continue
            if fill.get('status') != 'placed':continue
            if (fill.get('simulation') is not True or fill.get('paper') is not True or fill.get('execution_enabled') is not False
                    or fill.get('side') not in ('buy', 'sell') or not isinstance(fill.get('qty'), int)
                    or isinstance(fill['qty'], bool) or fill['qty'] <= 0
                    or not finite(fill.get('price')) or fill['price'] <= 0
                    or not finite(fill.get('fees')) or fill['fees'] < 0):
                raise ValueError('Invalid paper fill')
            symbol_name(fill['symbol'])
            timestamp(fill['at'])
            approval = approvals.get(fill['fingerprint'])
            if (not approval or approval['order']['symbol'] != fill['symbol'] or approval['order']['side'] != fill['side']
                    or order_hash(approval['order']) != fill['fingerprint'] or int(approval['order']['qty']) != fill['qty']):
                raise ValueError('Fill is missing its exact paper approval')
            authorization = approval.get('authorization',{})
            if authorization:
                from automation.policy import validate_mandate
                policy_sha = authorization.get('policy_sha256')
                policy = policies.get(policy_sha)
                if authorization.get('kind') != 'STANDING_PAPER_AUTOMATION_POLICY' or not policy or digest(policy) != policy_sha:
                    raise ValueError('Automatic fill is missing its recorded standing paper policy')
                validate_mandate(policy)
                if fill.get('authorization') != authorization or timestamp(fill['observed_at']) < timestamp(fill['at']):
                    raise ValueError('Automatic fill observation/authorization differs from its policy')
            event = {k: fill[k] for k in ('id', 'symbol', 'side', 'qty', 'price', 'fees', 'at', 'fingerprint')}
            event.update(authorization_kind=authorization.get('kind','EXACT_USER_TICKET'),outcome=fill.get('outcome'),observed_at=fill.get('observed_at'))
            plan = approval['order'].get('plan', {})
            total_costs += fill['fees']
            cash += fill['qty']*fill['price']-fill['fees'] if fill['side'] == 'sell' else -fill['qty']*fill['price']-fill['fees']
            if fill['side'] == 'buy':
                setup = str(plan.get('setup') or 'Unclassified')
                event.update(setup=setup, planned_entry=plan.get('entry'), stop=plan.get('stop'), target=plan.get('target'))
                entries.append(event)
                lots.setdefault(fill['symbol'], []).append(dict(qty=fill['qty'], cost=fill['qty']*fill['price']+fill['fees'], setup=setup))
            else:
                left, pnl = fill['qty'], 0.
                allocations = []
                for lot in lots.get(fill['symbol'], []):
                    if not left or not lot['qty']:
                        continue
                    take = min(left, lot['qty'])
                    basis = lot['cost']*take/lot['qty']
                    result = take*fill['price']-fill['fees']*take/fill['qty']-basis
                    lot['cost'] -= basis
                    lot['qty'] -= take
                    left -= take
                    pnl += result
                    allocations.append(dict(setup=lot['setup'], qty=take, net_pnl=result))
                    group = groups.setdefault(lot['setup'], dict(setup=lot['setup'], exit_allocations=0, net_pnl=0.))
                    group['exit_allocations'] += 1
                    group['net_pnl'] += result
                if left or not finite(fill.get('realized_pnl')) or abs(pnl-fill['realized_pnl']) > .01:
                    raise ValueError('FIFO outcome does not reconcile to recorded paper P&L')
                net += pnl
                wins += pnl > 0
                gains += max(0, pnl)
                losses += max(0, -pnl)
                event.update(net_pnl=pnl, allocations=allocations)
                exits.append(event)
                curve.append(dict(at=fill['at'], realized_pnl=net))
        if not finite(pf.get('realized_pnl')) or abs(net-pf['realized_pnl']) > .01 or abs(cash-pf['cash']) > .01:
            raise ValueError('Recorded outcomes do not reconcile to account P&L')
        if (abs(capital_flows-pf.get('net_capital_flows',0)) > .01
                or abs(pf.get('funded_capital',pf['initial_capital'])-pf['initial_capital']-capital_flows) > .01):
            raise ValueError('Capital flows do not reconcile to funded capital')
        actual = {p.get('ticker', p.get('symbol')): p for p in pf.get('positions', [])}
        expected = {symbol: sum(l['qty'] for l in queue) for symbol, queue in lots.items() if any(l['qty'] for l in queue)}
        if {s: p.get('shares', p.get('qty')) for s, p in actual.items()} != expected:
            raise ValueError('Open shares do not reconcile to recorded fills')
        return dict(status='RECORDED', entries=list(reversed(entries)), exits=list(reversed(exits)),
                    positions=pf.get('positions', []), cash=pf['cash'], initial_capital=pf.get('initial_capital'),
                    funded_capital=pf.get('funded_capital',pf['initial_capital']),net_capital_flows=capital_flows,capital_adjustments=adjustments,
                    realized_pnl=net, total_costs=total_costs, closed_exits=len(exits),
                    win_rate=wins/len(exits)*100 if exits else None,
                    profit_factor=gains/losses if losses else None, average_exit_pnl=net/len(exits) if exits else None,
                    groups=sorted(groups.values(), key=lambda g: -g['net_pnl']), equity_curve=curve,
                    execution_enabled=False, note='Local simulated fills only. P&L includes recorded entry and exit model cost estimates. Win rate is per sell fill; setup groups use FIFO exit allocations. No mark-to-market or market-regime classification is inferred.')
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return dict(empty, status='UNAVAILABLE', note='Paper ledger is unavailable or failed reconciliation. No performance figures are shown.')
