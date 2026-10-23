"""One bounded paper health/analysis job for cron, systemd or a container.

Uses an OS file lock (Mac/Linux) to avoid overlapping jobs. Schedule externally;
no daemon, order transport or external notifications are installed.
"""
import argparse
import datetime as dt
import fcntl
import json
import logging
import math
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
import sys

from .data import IST, symbol_name
from .market import Calendar, DEFAULT_CALENDAR, trading_day


def run(config, now=None):
    allowed={'symbol','provider','tick','capital','risk','days','fallback_providers',
             'record_db','state_dir','calendar','cost_config','timeout_seconds'}
    if not isinstance(config,dict) or set(config)-allowed:
        raise ValueError('Invalid/unknown paper job configuration keys')
    symbol_name(config['symbol'])
    if 'tick' not in config or not all(isinstance(value,(int,float)) and not isinstance(value,bool)
            and math.isfinite(value) and value > 0 for value in (
                config.get('tick'),config.get('capital',100000),config.get('risk',500))):
        raise ValueError('Paper job requires positive finite tick, capital and risk')
    if not isinstance(config.get('days',7),int) or not 1 <= config.get('days',7) <= 28:
        raise ValueError('Paper job days must be 1-28')
    provider=config.get('provider','nse')
    fallbacks=config.get('fallback_providers',[])
    if provider not in ('nse','yahoo','upstox') or not isinstance(fallbacks,list) or any(x not in ('nse','yahoo','upstox') for x in fallbacks):
        raise ValueError('Invalid paper job providers')
    timeout=config.get('timeout_seconds',120)
    if not isinstance(timeout,int) or isinstance(timeout,bool) or not 1 <= timeout <= 300:
        raise ValueError('Paper job timeout must be 1-300 seconds')
    now=(now or dt.datetime.now(IST)).astimezone(IST)
    calendar=Calendar.load(config['calendar']) if config.get('calendar') else DEFAULT_CALENDAR
    state=Path(config.get('state_dir','var/state'))
    state.mkdir(parents=True,exist_ok=True)
    with (state/'paper.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'mode':'paper_job','status':'BUSY','execution_enabled':False},75
        handler=RotatingFileHandler(state/'paper.jsonl',maxBytes=2_000_000,backupCount=5)
        logger=logging.Logger('paper_job')
        logger.addHandler(handler)
        try:
            exchange='NSE' if config['symbol'].endswith('.NS') else 'BSE'
            if not trading_day(now.date(),calendar,exchange) or not dt.time(9,35) <= now.time() < dt.time(15,15):
                result={'mode':'paper_job','status':'SKIPPED','reason':'outside_strategy_check_window','execution_enabled':False}
                code=0
            else:
                args=[sys.executable,'-m','india','doctor',config['symbol'],'--provider',provider,
                      '--tick',str(config['tick']),'--record-db',str(config.get('record_db','var/runs.sqlite'))]
                for key in ('capital','risk','days','calendar','cost_config'):
                    if key in config: args.extend(['--'+key.replace('_','-'),str(config[key])])
                for name in fallbacks: args.extend(['--fallback-provider',name])
                try:
                    process=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
                    try:
                        result=json.loads(process.stdout)
                        if not isinstance(result,dict): raise ValueError('Expected JSON object')
                    except ValueError:
                        result={'mode':'paper_job','status':'FAIL','reason':'invalid_child_output','execution_enabled':False}
                    code=0 if process.returncode==0 and result.get('status')=='PASS' else 1
                except subprocess.TimeoutExpired:
                    result={'mode':'paper_job','status':'FAIL','reason':'provider_deadline_exceeded','execution_enabled':False}
                    code=1
            result.update(checked_at=now.isoformat(),symbol=config['symbol'],execution_enabled=False)
            logger.warning(json.dumps(result,separators=(',',':'),allow_nan=False))
            return result,code
        finally:
            handler.close()
            logger.removeHandler(handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    try:
        with open(args.config) as f: config=json.load(f)
        result,code=run(config)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        result={'mode':'paper_job','status':'FAIL','error':str(exc),'execution_enabled':False}
        code=1
    print(json.dumps(result,indent=2,allow_nan=False))
    return code


if __name__=='__main__':
    raise SystemExit(main())
