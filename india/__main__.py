import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
import time
from .data import CSVProvider, YahooProvider, IST, timestamp, symbol_name
from .analysis import report, backtest, indicators
from .upstox import UpstoxProvider
from .nse import NSEProvider
from .market import Calendar, DEFAULT_CALENDAR, costs
from .research import research
from .operations import health
from .audit import record, replay

def main(argv=None):
    p=argparse.ArgumentParser(description='NSE/BSE research only; no execution integration')
    p.add_argument('command',choices=['analyze','backtest','history','research','doctor','replay','team'])
    p.add_argument('symbol',help='NSE/BSE symbol, or stored run ID for replay')
    p.add_argument('--csv')
    p.add_argument('--provider',choices=['upstox','yahoo','nse'],default='yahoo')
    p.add_argument('--instruments',help='Local Upstox instrument master JSON or JSON.gz')
    p.add_argument('--days',type=int,default=None,help='Calendar-day lookback: team defaults to 60 (Upstox 28), other commands 7; NSE supports 1-120')
    p.add_argument('--as-of',help='Explicit historical replay timestamp with timezone')
    p.add_argument('--tick',type=float,help='Verified instrument tick size in INR (required except replay)')
    p.add_argument('--capital',type=float,default=100000)
    p.add_argument('--risk',type=float,default=500)
    p.add_argument('--rr',type=float,default=2)
    p.add_argument('--cost-config',help='JSON override of cost rates')
    p.add_argument('--calendar',help='Sourced NSE/BSE holiday calendar JSON; replaces built-in coverage')
    p.add_argument('--fallback-provider',action='append',choices=['upstox','yahoo','nse'],default=[],help='Explicit alternate provider; never mixes candles from snapshots')
    p.add_argument('--no-fallback',action='store_true',help='Disable automatic Yahoo-to-NSE fallback for .NS stocks')
    p.add_argument('--record-db','--db',dest='record_db',help='SQLite run archive, or input archive for replay')
    p.add_argument('--train-fraction',type=float,default=.7,help='Chronological research split; no parameter fitting')
    p.add_argument('--risk-fraction',type=float,default=.005,help='Research risk/equity cap, default 0.5 percent')
    p.add_argument('--json-errors',action='store_true',help='Machine-readable failures on stdout, nonzero exit')
    p.add_argument('--security-master',help='Dated security metadata with explicit INR tick units for team preparation')
    p.add_argument('--team-dir',help='New directory for immutable team evidence and independent reviews')
    p.add_argument('--portfolio',help='JSON paper portfolio snapshot for team risk checks')
    p.add_argument('--team-limits',help='JSON team portfolio risk and allocation limits')
    p.add_argument('--simulations',type=int,default=10000,help='Seeded team scenario paths; requires 20 complete prior sessions')
    p.add_argument('--seed',type=int,default=17)
    p.add_argument('--issuer-domain',action='append',default=[],help='Verified official issuer domain allowed as research evidence')
    a=p.parse_args(argv)
    if a.command != 'replay' and a.tick is None:
        p.error('--tick is required')
    started=time.monotonic()
    try:
        if a.command == 'replay':
            if not a.record_db: raise ValueError('replay requires --db PATH')
            result=replay(a.record_db,a.symbol)
            print(json.dumps(result,indent=2,allow_nan=False))
            return 0 if result['matches'] else 1
        if a.command=='team' and a.record_db:
            raise ValueError('Team sessions archive evidence in --team-dir; use --record-db with standalone analysis')
        symbol_name(a.symbol)
        if not all(math.isfinite(x) and x > 0 for x in (a.tick,a.capital,a.risk,a.rr)):
            raise ValueError('tick, capital, risk and reward/risk must be positive finite numbers')
        now=timestamp(a.as_of) if a.as_of else dt.datetime.now(IST)
        if not a.csv and now > dt.datetime.now(IST):
            raise ValueError('Cannot fetch future market data')
        calendar=Calendar.load(a.calendar) if a.calendar else DEFAULT_CALENDAR
        calendar.trading_day(now.date(),'NSE' if a.symbol.endswith('.NS') else 'BSE')
        config=None
        if a.cost_config:
            with open(a.cost_config) as f: config=json.load(f)
        if config is not None and not isinstance(config,dict): raise ValueError('Cost configuration must be a JSON object')
        costs(1,1,0,'NSE' if a.symbol.endswith('.NS') else 'BSE',config)
        days=a.days if a.days is not None else (60 if a.command=='team' and a.provider!='upstox' else 28 if a.command=='team' else 7)
        attempts=[]
        best=None
        automatic=['nse'] if a.provider=='yahoo' and a.symbol.endswith('.NS') and not a.no_fallback else []
        providers=['csv'] if a.csv else list(dict.fromkeys([a.provider]+a.fallback_provider+automatic))
        for name in providers:
            try:
                if name == 'csv': source=CSVProvider(a.csv)
                elif name == 'nse': source=NSEProvider(now,days)
                elif name == 'upstox': source=UpstoxProvider(now,days,a.instruments)
                else: source=YahooProvider(extended_history=a.command=='team')
                bars=source.history(a.symbol,'1d' if a.command=='history' else '5m')
                args=(a.symbol,a.tick,a.capital,a.risk,a.rr,config,calendar)
                if a.command in ('analyze','doctor','team'):
                    result=report(bars,now,*args)
                    usable=result['data_quality']['complete'] and (not result['strategy_open'] or not result['stale'])
                    score=(usable,-len(result['data_quality']['missing_bars']),len(bars))
                    if a.command == 'doctor':
                        result=health(result,now,calendar)
                        usable=result['status']=='PASS'
                        score=(usable,score[1],score[2])
                elif a.command=='backtest':
                    result=backtest([b for b in bars if b.time+dt.timedelta(minutes=5)<=now],*args)
                    usable=True;score=(True,0,len(bars))
                elif a.command=='research':
                    result=research(bars,now,*args,train_fraction=a.train_fraction,risk_fraction=a.risk_fraction)
                    usable=True;score=(True,0,len(bars))
                else:
                    completed_daily=[b for b in bars if b.time.date()<now.date()]
                    if not completed_daily: raise ValueError('No completed historical sessions')
                    result={'mode':'historical','bars':[dict(time=b.time.isoformat(),open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume) for b in completed_daily]}
                    usable=True;score=(True,0,len(bars))
                attempts.append({'provider':name,'usable':usable})
                if best is None or score > best[0]: best=(score,result,bars,source,name)
                if usable: break
            except (ValueError,OSError,KeyError,TypeError,IndexError) as exc:
                attempts.append({'provider':name,'usable':False,'error':str(exc)})
        if best is None:
            raise ValueError('; '.join('%s: %s' % (x['provider'],x.get('error','unavailable')) for x in attempts))
        _,result,bars,source,chosen=best
        labels={'nse':'NSE public charting website; availability and freshness not guaranteed',
                'upstox':'Upstox V3 candles','yahoo':'Yahoo unofficial historical feed; may be delayed'}
        result.update(source='CSV export' if chosen=='csv' else labels[chosen],historical_replay=bool(a.as_of))
        if len(providers)>1: result['provider_attempts']=attempts
        if isinstance(source, UpstoxProvider): result['instrument_key'] = source.instrument_key
        if isinstance(source, NSEProvider): result['provider_symbol'] = source.provider_symbol
        if a.command=='doctor': result['elapsed_ms']=round((time.monotonic()-started)*1000,2)
        if a.command=='team':
            from .team import prepare, read_json
            from .security import security_master
            settings={'symbol':a.symbol,'tick':a.tick,'capital':a.capital,'risk':a.risk,'rr':a.rr,
                      'as_of':now.isoformat(),'cost_config':config,'calendar':calendar.document}
            provenance={k:result[k] for k in ('source','historical_replay','provider_attempts','instrument_key','provider_symbol') if k in result}
            result=prepare(bars,settings,provenance,result,a.team_dir,
                           read_json(a.portfolio) if a.portfolio else None,
                           read_json(a.team_limits) if a.team_limits else None,
                           a.simulations,a.seed,a.issuer_domain,security_master(a.security_master,now))
        if a.record_db:
            settings={'command':a.command,'symbol':a.symbol,'tick':a.tick,'capital':a.capital,'risk':a.risk,
                      'rr':a.rr,'as_of':now.isoformat(),'cost_config':config,'calendar':calendar.document,
                      'train_fraction':a.train_fraction,'risk_fraction':a.risk_fraction}
            result.update(record(a.record_db,settings,result,bars))
        print(json.dumps(result,indent=2,allow_nan=False))
        return 1 if a.command=='doctor' and result['status']=='FAIL' else 0
    except (ValueError,OSError,KeyError,TypeError,IndexError,sqlite3.Error) as e:
        if a.json_errors or a.command=='doctor':
            print(json.dumps({'mode':'health' if a.command=='doctor' else 'error','status':'FAIL','error':str(e),
                              'execution_enabled':False,'elapsed_ms':round((time.monotonic()-started)*1000,2)}))
            return 1
        print('Data/analysis unavailable: '+str(e),file=sys.stderr); return 1

if __name__=='__main__': sys.exit(main())
