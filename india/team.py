"""Sealed specialist handoffs and independent Codex review aggregation.

Python prepares/verifies numerical evidence; actual LLM agents are spawned by
the trader-team Codex skill. This module never impersonates an LLM or submits
orders. Native thread IDs are recorded provenance, not cryptographic attestation.
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid

from .analysis import report, size
from .audit import code_hash, digest, encode_bars
from .data import Bar, IST, timestamp, symbol_name, validate
from .forecast import forecast
from .market import Calendar, costs
from .security import security_records, verify_tick

ROLES = ('scanner','news','entry','exit','timemachine','budget','risk','broker','alerts','analyst','pelosi')
REQUIRED = tuple(r for r in ROLES if r != 'pelosi')
ARTIFACTS = dict(zip(ROLES,('watchlist.json','news.json','entry.json','plan.json',
    'timemachine.json','orders.json','state/risk.json','state/broker.json',
    'alerts.json','metrics.json','disclosures.json')))
VERDICTS = ('PASS','WARN','BLOCK','UNAVAILABLE','NOT_APPLICABLE')


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
            temporary=f.name
            json.dump(value,f,indent=2,allow_nan=False)
            f.write('\n')
        os.replace(temporary,path)
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)


def read_json(path):
    with Path(path).open(encoding='utf-8') as f: return json.load(f)


def positive(value, zero=False):
    return (isinstance(value,(int,float)) and not isinstance(value,bool)
            and math.isfinite(value) and (value >= 0 if zero else value > 0))


def portfolio_input(value, settings):
    now=timestamp(settings['as_of'])
    if value is None:
        return {'as_of':now.isoformat(),'cash':settings['capital'],'equity':settings['capital'],
                'positions':[],'realized_pnl_today':0.,'trades_today':0,'orders_today':0,
                'basis':'cash-only paper assumption from supplied capital; not a broker balance'}
    value=json.loads(json.dumps(value))
    required={'as_of','cash','equity','positions','realized_pnl_today','trades_today','orders_today'}
    if not isinstance(value,dict) or not required <= set(value):
        raise ValueError('Portfolio needs as_of, cash, equity, positions, realized_pnl_today, trades_today and orders_today')
    if not positive(value['cash'],True) or not positive(value['equity']) or value['cash'] > value['equity']:
        raise ValueError('Invalid cash-funded portfolio balances')
    age=(now-timestamp(value['as_of'])).total_seconds()
    if not 0 <= age <= 900:
        raise ValueError('Portfolio snapshot must be no later than as-of and at most 15 minutes old')
    if not isinstance(value['positions'],list) or not isinstance(value['realized_pnl_today'],(int,float)) or not math.isfinite(value['realized_pnl_today']):
        raise ValueError('Invalid portfolio positions or realized P&L')
    for key in ('trades_today','orders_today'):
        if isinstance(value[key],bool) or not isinstance(value[key],int) or value[key]<0:
            raise ValueError('Portfolio daily counts must be nonnegative integers')
    seen=set()
    for position in value['positions']:
        if not isinstance(position,dict) or not {'symbol','qty','mark','stop'} <= set(position):
            raise ValueError('Positions need symbol, qty, mark and stop')
        symbol_name(position['symbol'])
        if position['symbol'] in seen:
            raise ValueError('Consolidate duplicate portfolio symbols before review')
        seen.add(position['symbol'])
        if (isinstance(position['qty'],bool) or not isinstance(position['qty'],int) or position['qty']<=0
                or not positive(position['mark']) or not positive(position['stop'])
                or position['stop']>=position['mark']):
            raise ValueError('Positions must be long whole shares with an untriggered stop below mark')
    value['basis']='user-supplied paper portfolio snapshot; not independently broker-reconciled'
    return value


def limit_input(value, settings):
    limits={'cash_buffer_fraction':.1,'max_position_fraction':.75,'max_order_value':settings['capital'],
            'max_total_risk':3*settings['risk'],'max_daily_loss':3*settings['risk'],
            'max_trades_per_day':3,'max_orders_per_day':10,'min_net_rr':1.}
    if value is not None:
        if not isinstance(value,dict) or set(value)-set(limits):
            raise ValueError('Unknown team limit keys')
        limits.update(value)
    if not 0 <= limits['cash_buffer_fraction'] < 1 or not 0 < limits['max_position_fraction'] <= 1:
        raise ValueError('Invalid cash buffer or position fraction')
    if not all(positive(x,True) for x in limits.values()): raise ValueError('Team limits must be finite and nonnegative')
    for key in ('max_trades_per_day','max_orders_per_day'):
        if isinstance(limits[key],bool) or not isinstance(limits[key],int):
            raise ValueError('Trade/order caps must be whole counts')
    return limits


def _payload(context, bars):
    payload={k:context[k] for k in ('schema_version','settings','provenance','portfolio','limits','code_sha256')}
    payload['bars']=bars
    return payload


def load_session(directory, verify_artifacts=True, verify_engine=True):
    directory=Path(directory).resolve()
    context=read_json(directory/'context.json')
    snapshot=read_json(directory/'snapshot.json')
    if (context.get('schema_version')!=1 or snapshot.get('snapshot_id')!=context.get('snapshot_id')
            or digest(_payload(context,snapshot['bars']))!=context['snapshot_id']):
        raise ValueError('Team snapshot integrity mismatch; prepare a new session')
    if verify_engine and context['code_sha256']!=code_hash():
        raise ValueError('Engine changed since snapshot preparation; prepare a new session')
    if verify_artifacts:
        for role,path in ARTIFACTS.items():
            if digest(read_json(directory/path))!=context['artifact_hashes'].get(role):
                raise ValueError('Team artifact changed after preparation: '+role)
    bars=validate([Bar(timestamp(r['time']),*(r[k] for k in ('open','high','low','close','volume')))
                   for r in snapshot['bars']])
    return context,bars


def budget_plan(settings, baseline, portfolio, limits):
    reasons=[]
    plan=baseline.get('plan')
    config=settings['cost_config']
    symbol=settings['symbol']
    exchange='NSE' if symbol.endswith('.NS') else 'BSE'
    open_risk=0.
    existing_value=0.
    for p in portfolio['positions']:
        px='NSE' if p['symbol'].endswith('.NS') else 'BSE'
        open_risk += p['qty']*(p['mark']-p['stop'])+costs(p['mark'],p['stop'],p['qty'],px,config)['total']
        if p['symbol']==symbol: existing_value += p['qty']*p['mark']
    cash=max(0,min(portfolio['cash'],settings['capital'])-settings['capital']*limits['cash_buffer_fraction'])
    notional=max(0,min(limits['max_order_value'],portfolio['equity']*limits['max_position_fraction']-existing_value))
    daily_room=max(0,limits['max_daily_loss']+min(0,portfolio['realized_pnl_today']))
    risk_room=max(0,min(settings['risk'],limits['max_total_risk']-open_risk,daily_room))
    if portfolio['trades_today']>=limits['max_trades_per_day']: reasons.append('daily_trade_cap')
    if portfolio['orders_today']>=limits['max_orders_per_day']: reasons.append('daily_order_cap')
    if cash<=0: reasons.append('cash_buffer_or_cash_limit')
    if notional<=0: reasons.append('position_or_order_limit')
    if risk_room<=0: reasons.append('daily_loss_or_total_open_risk_limit')
    if baseline['status']!='PAPER_SETUP' or not plan: reasons.append('no_eligible_entry')
    metadata=settings.get('security_metadata')
    tick_check=verify_tick(symbol,settings['tick'],security_records([metadata],timestamp(settings['as_of'])) if metadata else {})
    if tick_check['status']!='VERIFIED': reasons.append('tick_'+tick_check['status'].lower())
    sized=None
    if not reasons:
        sized=size(plan,min(cash,notional),risk_room,exchange,config)
        if sized['qty']<=0: reasons.append('zero_affordable_quantity')
        if sized['net_reward_risk']<limits['min_net_rr']: reasons.append('net_reward_risk_below_floor')
    order=None
    if sized and not reasons:
        order={'symbol':symbol,'currency':'INR','side':'BUY','mode':'paper','execution_enabled':False,
               'qty':sized['qty'],'entry':sized['entry'],'stop':sized['stop'],'target':sized['target'],
               'signal_time':sized['signal_time'],'as_of':settings['as_of']}
    return {'status':'PASS' if order else 'BLOCK','reasons':reasons,'order':order,
            'sizing':sized,'available_cash_after_buffer':cash,'remaining_risk_budget':risk_room,
            'existing_open_risk_estimate':open_risk,'existing_symbol_value':existing_value,
            'position_value_room':notional,'remaining_daily_loss_budget':daily_room,
            'portfolio_basis':portfolio['basis']}


def _role(role, context, verdict, summary, data):
    return {'schema_version':1,'role':role,'snapshot_id':context['snapshot_id'],
            'as_of':context['settings']['as_of'],'symbol':context['settings']['symbol'],
            'verdict':verdict,'summary':summary,'execution_enabled':False,'data':data}


def _artifacts(context, bars, baseline, simulations, seed):
    s=context['settings'];now=timestamp(s['as_of']);cal=Calendar(s['calendar'])
    args=(s['symbol'],s['tick'],s['capital'],s['risk'],s['rr'],s['cost_config'],cal)
    tick_check=verify_tick(s['symbol'],s['tick'],{s['symbol']:s['security_metadata']} if s.get('security_metadata') else {})
    plan=baseline.get('plan') if tick_check['status']=='VERIFIED' else None
    valid=baseline['status']=='PAPER_SETUP' and plan is not None
    decisions={}
    decisions['scanner']=_role('scanner',context,'PASS' if baseline['data_quality']['complete'] else 'BLOCK',
        'Supplied-symbol opening-range scan; no automatic exchange-wide universe claim.',
        {'watchlist':[s['symbol']],'candidates':[s['symbol']] if valid else [],'analysis':baseline,'tick_verification':tick_check})
    for role in ('news','analyst'):
        decisions[role]=_role(role,context,'UNAVAILABLE','Requires independently read exchange/issuer evidence available by as-of.',
                              {'items':[],'sources':[],'automated_filings_adapter':False})
    decisions['entry']=_role('entry',context,'PASS' if valid else 'BLOCK',
        'Completed-bar trigger only; no intrabar anticipation.',
        {'status':'TRIGGERED' if valid else ('EXPIRED' if not baseline['strategy_open'] else 'WAITING'),
         'signal_time':plan['signal_time'] if plan else None,'confirmation_time':
         (timestamp(plan['signal_time'])+dt.timedelta(minutes=5)).isoformat() if plan else None,
         'data_complete':baseline['data_quality']['complete'],'stale':baseline['stale']})
    decisions['exit']=_role('exit',context,'PASS' if valid else 'UNAVAILABLE',
        'Opening-range-low stop and tick-rounded reward/risk target; proposed paper levels.',
        {'plan':plan,'stop_method':'opening_range_low','target_multiple':s['rr']})
    machine=forecast(bars,now,plan,*args,sims=simulations,seed=seed)
    decisions['timemachine']=_role('timemachine',context,'PASS' if machine['status']=='AVAILABLE' else 'UNAVAILABLE',
        'Seeded whole-candle resampling and prior-session next-bar replay; scenarios are not predictions.',machine)
    budget=budget_plan(s,baseline,context['portfolio'],context['limits'])
    order=budget['order']
    fingerprint=digest({'snapshot_id':context['snapshot_id'],'order':order}) if order else None
    decisions['budget']=_role('budget',context,budget['status'],'Cash, position, order, daily-loss and combined open-risk limits.',
        dict(budget,order_fingerprint=fingerprint))
    risk_reasons=list(budget['reasons'])
    if order:
        checked=budget['sizing']
        fee=costs(order['entry'],order['stop'],order['qty'],'NSE' if s['symbol'].endswith('.NS') else 'BSE',s['cost_config'])['total']
        if order['qty']*order['entry']+fee>budget['available_cash_after_buffer']+1e-8: risk_reasons.append('cash_veto')
        if checked['max_loss_estimate']>budget['remaining_risk_budget']+1e-8: risk_reasons.append('risk_veto')
        if not order['stop']<order['entry']<order['target']: risk_reasons.append('invalid_levels')
    decisions['risk']=_role('risk',context,'BLOCK' if risk_reasons else 'PASS',
        'Independent numerical veto bound to the exact order and snapshot; cannot authorize execution.',
        {'status':'BLOCK' if risk_reasons else 'PASS','reasons':risk_reasons,
         'order_fingerprint':fingerprint,'order':order,'checks_as_of':s['as_of']})
    decisions['broker']=_role('broker',context,'WARN' if order else 'BLOCK',
        'Draft paper ticket only. Pending independent reviews; no transport, fills or portfolio mutation.',
        {'status':'DRAFT_PAPER_TICKET' if order else 'NO_TICKET','order':order,'order_fingerprint':fingerprint,
         'submitted':False,'filled':False,'requires_fresh_review_after_edit':True})
    alerts=[]
    if order:
        for kind,level in (('stop',order['stop']),('target',order['target']),('flat_by','15:15')):
            alerts.append({'id':digest({'order':fingerprint,'kind':kind})[:20],'kind':kind,'level':level,
                           'symbol':s['symbol'],'order_fingerprint':fingerprint})
    decisions['alerts']=_role('alerts',context,'PASS' if alerts else 'UNAVAILABLE',
        'Alert definitions only; creating these does not start a scheduler or send a message.',
        {'alerts':alerts,'monitoring_started':False,'deduplication_scope':'order fingerprint'})
    decisions['pelosi']=_role('pelosi',context,'NOT_APPLICABLE','US congressional disclosures are outside this NSE/BSE workflow.',
                             {'sources':[],'automatic_us_lookup':False})
    return decisions


def prepare(bars, settings, provenance, analysis, directory=None, portfolio=None, limits=None,
            simulations=10000, seed=17, issuer_domains=None, security_metadata=None):
    now=timestamp(settings['as_of'])
    settings=json.loads(json.dumps(settings))
    settings.update(simulations=simulations,seed=seed)
    master=security_records(list((security_metadata or {}).values()),now)
    settings['security_metadata']=master.get(settings['symbol'])
    domains=['nseindia.com','bseindia.com']
    for domain in issuer_domains or []:
        domain=domain.lower().strip()
        if not domain or '/' in domain or ':' in domain or '.' not in domain:
            raise ValueError('Issuer domains must be bare official DNS names')
        domains.append(domain)
    settings['trusted_source_domains']=sorted(set(domains))
    # Expose no future observations to specialist agents, even when a CSV has
    # the rest of the day. Forming candles are excluded as well.
    bars=validate([b for b in bars if b.time+dt.timedelta(minutes=5)<=now])
    analysis=report(bars,now,settings['symbol'],settings['tick'],settings['capital'],settings['risk'],
                    settings['rr'],settings['cost_config'],Calendar(settings['calendar']))
    context={'schema_version':1,'settings':settings,'provenance':provenance,
             'portfolio':portfolio_input(portfolio,settings),'limits':limit_input(limits,settings),
             'code_sha256':code_hash()}
    serialized=encode_bars(bars)
    context['snapshot_id']=digest(_payload(context,serialized))
    if directory is None: directory=Path('trader/team')/(settings['symbol']+'-'+uuid.uuid4().hex[:12])
    directory=Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Team directory is not empty; use a new directory for each snapshot')
    decisions=_artifacts(context,bars,analysis,simulations,seed)
    context['artifact_hashes']={r:digest(doc) for r,doc in decisions.items()}
    for role,doc in decisions.items(): write_json(directory/ARTIFACTS[role],doc)
    write_json(directory/'snapshot.json',{'schema_version':1,'snapshot_id':context['snapshot_id'],'bars':serialized})
    write_json(directory/'context.json',context)
    write_json(directory/'delegations.json',{'schema_version':1,'snapshot_id':context['snapshot_id'],'agents':{}})
    readiness=preflight(directory)
    write_json(directory/'preflight.json',readiness)
    return {'preflight':readiness,'mode':'team_prepared','status':'PENDING_AGENT_REVIEWS','session_dir':str(directory),
            'snapshot_id':context['snapshot_id'],'symbol':settings['symbol'],'as_of':settings['as_of'],
            'required_roles':list(REQUIRED),'artifacts':ARTIFACTS,
            'native_agents_started':False,'execution_enabled':False}


def evaluate(directory, role):
    if role not in ROLES: raise ValueError('Unknown specialist role')
    context,_=load_session(directory)
    artifact=read_json(Path(directory)/ARTIFACTS[role])
    return dict(artifact,artifact_sha256=context['artifact_hashes'][role])


def _publication_upper_bound(source):
    """Latest possible publication, with explicit precision and timezone.

    A date-only source is usable only after that entire local date has ended.
    This bound is not presented as an observed publication clock time.
    """
    if 'published_at' in source:
        if 'published_date' in source or 'publication_timezone' in source:
            raise ValueError('Use either an exact timestamp or a date and timezone')
        return timestamp(source['published_at'])
    day=dt.date.fromisoformat(source['published_date'])
    zone=ZoneInfo(source['publication_timezone'])
    return dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),zone).astimezone(IST)


def _source_check(sources, context):
    if not isinstance(sources,list) or not sources: return False
    now=timestamp(context['settings']['as_of'])
    domains=context['settings']['trusted_source_domains']
    for source in sources:
        if not isinstance(source,dict): return False
        try:
            parsed=urlparse(source.get('url',''))
            host=(parsed.hostname or '').lower()
            if parsed.scheme!='https' or parsed.username or not any(host==d or host.endswith('.'+d) for d in domains): return False
            upper=_publication_upper_bound(source);accessed=timestamp(source['accessed_at'])
        except (ValueError,TypeError,KeyError,ZoneInfoNotFoundError): return False
        if upper>now or accessed<upper: return False
    return True


def preflight(directory):
    """Read-only readiness diagnosis, including archives from older engines.

    Verify sealed data/artifact hashes but never refresh or clear an old review.
    Current evaluate/finalize still require an exact engine match.
    """
    directory=Path(directory).resolve()
    context,_=load_session(directory,verify_engine=False)
    s=context['settings'];now=timestamp(s['as_of']);cal=Calendar(s['calendar'])
    baseline=read_json(directory/ARTIFACTS['scanner'])['data']['analysis']
    machine=read_json(directory/ARTIFACTS['timemachine'])['data']
    metadata=s.get('security_metadata')
    tick=verify_tick(s['symbol'],s['tick'],security_records([metadata],now) if metadata else {})
    complete=machine['data']['prior_complete_sessions']
    minimum=machine['data']['minimum_sessions']
    issues=[]
    if not baseline['strategy_open']:
        issues.append({'code':'OUTSIDE_STRATEGY_WINDOW','action':'Wait for an eligible session; use an explicit historical cutoff only for a labelled replay.'})
    if not baseline['data_quality']['complete']:
        issues.append({'code':'INCOMPLETE_CANDLES','action':'Obtain the missing completed bars from one consistent source.'})
    if baseline['stale'] and baseline['strategy_open']:
        issues.append({'code':'STALE_CANDLES','action':'Obtain fresh completed candles before preparing another session.'})
    if tick['status']!='VERIFIED':
        issues.append({'code':'TICK_'+tick['status'],'action':'Supply --security-master with the applicable dated INR tick; do not infer raw units.'})
    if complete<minimum:
        issues.append({'code':'INSUFFICIENT_HISTORY','action':'Request --days 60 with NSE, or supply complete authorized CSV history; keep the 20-session minimum.'})
    if baseline['strategy_open'] and not baseline['stale'] and baseline['status']=='NO_SETUP':
        issues.append({'code':'NO_SETUP','action':'Wait for a completed-bar trigger; a working scan need not find a trade.'})
    next_window=None
    exchange='NSE' if s['symbol'].endswith('.NS') else 'BSE'
    for offset in range(15):
        day=now.date()+dt.timedelta(days=offset)
        opening=dt.datetime.combine(day,dt.time(9,35),IST)
        if opening<=now: continue
        try:
            if cal.trading_day(day,exchange):
                next_window=opening.isoformat();break
        except ValueError: break
    same_code=context['code_sha256']==code_hash()
    return {'mode':'team_preflight','snapshot_id':context['snapshot_id'],'as_of':s['as_of'],
            'same_engine':same_code,'status':'PREREQUISITES_READY' if not issues and same_code else 'NOT_READY',
            'should_dispatch_specialists':not issues and same_code,
            'analysis_status':baseline['status'],'market_open':baseline['market_open'],
            'strategy_open':baseline['strategy_open'],'data_quality':baseline['data_quality'],
            'tick_verification':tick,'history':{'complete_prior_sessions':complete,'minimum_sessions':minimum,
                                              'shortfall':max(0,minimum-complete)},
            'issues':issues,'next_potential_signal_cutoff':next_window,
            'research':'Independent source review still required; readiness is not trade clearance.',
            'dependent_roles_without_entry':['exit','budget','risk','broker','alerts'] if not baseline.get('plan') or tick['status']!='VERIFIED' else [],
            'execution_enabled':False}


def finalize(directory):
    directory=Path(directory).resolve()
    context,bars=load_session(directory)
    s=context['settings'];now=timestamp(s['as_of'])
    # Recompute the numerical risk boundary, rather than trusting agent votes
    # or a stale PASS copied from another order.
    baseline=report(bars,now,s['symbol'],s['tick'],s['capital'],s['risk'],s['rr'],s['cost_config'],Calendar(s['calendar']))
    budget=budget_plan(s,baseline,context['portfolio'],context['limits'])
    actual_order=read_json(directory/ARTIFACTS['budget'])['data']['order']
    if digest(actual_order)!=digest(budget['order']): raise ValueError('Order no longer matches independent budget calculation')
    risk=read_json(directory/ARTIFACTS['risk'])['data']
    expected_fingerprint=digest({'snapshot_id':context['snapshot_id'],'order':actual_order}) if actual_order else None
    if risk['order_fingerprint']!=expected_fingerprint: raise ValueError('Risk verdict does not match exact order')
    manifest=read_json(directory/'delegations.json')
    if manifest.get('schema_version')!=1 or manifest.get('snapshot_id')!=context['snapshot_id'] or not isinstance(manifest.get('agents'),dict):
        raise ValueError('Invalid native agent delegation manifest')
    reviews={};missing=[];invalid=[];ids=[]
    for role in REQUIRED:
        path=directory/'reviews'/(role+'.json')
        if not path.is_file(): missing.append(role);continue
        doc=read_json(path)
        if (not isinstance(doc,dict) or doc.get('schema_version')!=1 or doc.get('role')!=role
                or doc.get('snapshot_id')!=context['snapshot_id'] or doc.get('as_of')!=s['as_of']
                or doc.get('artifact_sha256')!=context['artifact_hashes'][role]
                or doc.get('verdict') not in VERDICTS or not isinstance(doc.get('summary'),str) or not doc['summary'].strip()
                or not isinstance(doc.get('evidence'),list) or not doc['evidence']
                or not isinstance(doc.get('agent_id'),str) or not doc['agent_id'].strip()
                or doc['agent_id']!=manifest['agents'].get(role)):
            invalid.append(role);continue
        if doc['verdict']=='NOT_APPLICABLE': invalid.append(role);continue
        if role in ('news','analyst') and doc['verdict'] in ('PASS','WARN') and not _source_check(doc.get('sources'),context):
            invalid.append(role);continue
        ids.append(doc['agent_id']);reviews[role]=doc
    if len(ids)!=len(set(ids)): invalid.append('agent_identity_reused_across_roles')
    vetoes=[r for r,d in reviews.items() if d['verdict']=='BLOCK']
    unavailable=[r for r,d in reviews.items() if d['verdict']=='UNAVAILABLE']
    # A positive model opinion cannot conjure missing numerical history or
    # override a deterministic veto. Filings roles can add genuine sources.
    engine_unavailable=[]
    engine_vetoes=[]
    for role,path in ARTIFACTS.items():
        if role in ('news','analyst','pelosi'): continue
        verdict=read_json(directory/path)['verdict']
        if verdict=='UNAVAILABLE': engine_unavailable.append(role)
        elif verdict=='BLOCK': engine_vetoes.append(role)
    if budget['status']=='BLOCK' or risk['status']=='BLOCK' or vetoes or engine_vetoes:
        status='BLOCKED'
    elif missing or invalid:
        status='PENDING_AGENT_REVIEWS'
    elif unavailable or engine_unavailable or reviews['risk']['verdict']!='PASS':
        status='REVIEW_INCOMPLETE'
    else:
        status='READY_PAPER_TICKET'
    result={'schema_version':1,'mode':'codex_team_review','symbol':s['symbol'],'as_of':s['as_of'],
            'snapshot_id':context['snapshot_id'],'status':status,'execution_enabled':False,
            'orchestration':{'kind':'native_codex_subagents' if not missing and not invalid else 'unverified_or_incomplete',
                             'required_agents':len(REQUIRED),'received_reviews':len(reviews),
                             'distinct_recorded_agent_ids':len(set(ids)),
                             'provenance_note':'Thread IDs are recorded host evidence, not signed runtime attestation.'},
            'missing_reviews':missing,'invalid_reviews':invalid,'unavailable_reviews':unavailable,
            'engine_unavailable':engine_unavailable,'engine_vetoes':engine_vetoes,
            'vetoes':vetoes,'numerical_risk':risk,'budget':budget,
            'paper_ticket':actual_order if status=='READY_PAPER_TICKET' else None,
            'reviews':reviews,'not_applicable':['pelosi'],
            'source':context['provenance'],'live_orders_submitted':0}
    write_json(directory/'report.json',result)
    return result


def main(argv=None):
    import sys
    args=list(sys.argv[1:] if argv is None else argv)
    if args and args[0]=='prepare':
        from .__main__ import main as india_main
        return india_main(['team']+args[1:])
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    evaluate_parser=sub.add_parser('evaluate')
    evaluate_parser.add_argument('--session',required=True)
    evaluate_parser.add_argument('--role',choices=ROLES,required=True)
    check=sub.add_parser('preflight');check.add_argument('--session',required=True)
    final=sub.add_parser('finalize');final.add_argument('--session',required=True)
    a=parser.parse_args(args)
    try:
        result=evaluate(a.session,a.role) if a.command=='evaluate' else preflight(a.session) if a.command=='preflight' else finalize(a.session)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 0
    except (ValueError,OSError,TypeError,KeyError,IndexError) as exc:
        print('Team workflow unavailable: '+str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
