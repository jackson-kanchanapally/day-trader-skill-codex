"""Offline CLI: imports, frozen-vintage walk-forward reports and gate checks."""
import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from india.data import CSVProvider
from india.market import Calendar
from workflow.common import read
from .lab import experiment
from .promotion import STATES, assess
from .store import CandleStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Offline NSE/BSE strategy research; no orders or background network jobs')
    commands = parser.add_subparsers(dest='command',required=True)
    ingest = commands.add_parser('ingest',help='Idempotent incremental import of a single-symbol 5m CSV')
    ingest.add_argument('symbol')
    ingest.add_argument('--csv',required=True)
    ingest.add_argument('--source',required=True,help='JSON with provider, reference, license_note')
    ingest.add_argument('--observed-at',required=True,help='Explicit source vintage timestamp; not independently authenticated')
    evidence = commands.add_parser('evidence',help='Import dated instrument/action/regime records from a JSON array')
    evidence.add_argument('kind',choices=['instrument','action','regime'])
    evidence.add_argument('--file',required=True)
    evidence.add_argument('--observed-at',required=True)
    watermark = commands.add_parser('watermark',help='Last stored candle at an explicit vintage')
    watermark.add_argument('symbol')
    watermark.add_argument('--vintage',required=True)
    run = commands.add_parser('walk-forward',help='Declared ORB experiments, independent symbol/fold accounts')
    run.add_argument('--start',type=dt.date.fromisoformat,required=True)
    run.add_argument('--end',type=dt.date.fromisoformat,required=True)
    run.add_argument('--as-of',required=True)
    run.add_argument('--vintage',required=True)
    run.add_argument('--train-sessions',type=int,default=20)
    run.add_argument('--test-sessions',type=int,default=5)
    run.add_argument('--embargo-sessions',type=int,default=0)
    run.add_argument('--capital',type=float,default=100000)
    run.add_argument('--risk',type=float,default=500)
    run.add_argument('--risk-fraction',type=float,default=.005)
    run.add_argument('--rr',type=float,nargs='+',default=[2.])
    run.add_argument('--strategies',help='JSON array of declared ORB configurations; unchanged baseline is always included')
    run.add_argument('--cost-config')
    run.add_argument('--liquidity-config',help='JSON with max_participation of the previous completed candle volume; a capacity proxy')
    run.add_argument('--regime-model',help='Explicit versioned regime model from imported evidence; labels freeze at session open')
    run.add_argument('--calendar')
    run.add_argument('--bootstrap-samples',type=int,default=1000)
    run.add_argument('--block-days',type=int,default=5)
    run.add_argument('--seed',type=int,default=17)
    run.add_argument('--out',help='Write a new report file; existing files are never overwritten')
    gate = commands.add_parser('promotion-check',help='Read-only deterministic checklist; never changes state or permissions')
    gate.add_argument('--report',required=True)
    gate.add_argument('--policy',required=True)
    gate.add_argument('--state',choices=STATES,default='RESEARCH')
    for command in (ingest,evidence,watermark,run):
        command.add_argument('--db',required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'promotion-check':
            result = assess(read(args.report),args.state,read(args.policy))
        else:
            # Read commands never create a missing historical database.
            if args.command == 'ingest':
                raw = Path(args.csv).read_bytes()
                bars = CSVProvider(args.csv).history(args.symbol,'5m')
                source = read(args.source)
            with closing(CandleStore(args.db,readonly=args.command in ('walk-forward','watermark'))) as store:
                if args.command == 'ingest':
                    result = store.ingest(args.symbol,bars,source=source,observed_at=args.observed_at,input_sha256=hashlib.sha256(raw).hexdigest())
                    result['watermark'] = store.watermark(args.symbol,vintage=args.observed_at)
                elif args.command == 'evidence':
                    result = dict(written=store.evidence(args.kind,read(args.file),observed_at=args.observed_at))
                elif args.command == 'watermark':
                    result = dict(watermark=store.watermark(args.symbol,vintage=args.vintage))
                else:
                    result = experiment(store,start=args.start,end=args.end,as_of=args.as_of,vintage=args.vintage,
                                        train_sessions=args.train_sessions,test_sessions=args.test_sessions,
                                        embargo_sessions=args.embargo_sessions,capital=args.capital,risk=args.risk,
                                        risk_fraction=args.risk_fraction,rr_values=tuple(args.rr),
                                        cost_config=read(args.cost_config) if args.cost_config else None,
                                        calendar=Calendar.load(args.calendar) if args.calendar else None,
                                        bootstrap_samples=args.bootstrap_samples,block_days=args.block_days,seed=args.seed,
                                        strategy_configs=read(args.strategies) if args.strategies else None,
                                        liquidity_config=read(args.liquidity_config) if args.liquidity_config else None,
                                        regime_model=args.regime_model)
                    if args.out:
                        serialized = json.dumps(result,indent=2,allow_nan=False)+'\n'
                        path = Path(args.out)
                        path.parent.mkdir(parents=True,exist_ok=True)
                        with path.open('x',encoding='utf-8') as output:
                            output.write(serialized)
        print(json.dumps(dict(result,execution_enabled=False),indent=2,allow_nan=False))
        return 1 if args.command == 'promotion-check' and not result['eligible'] else 0
    except (ValueError,OSError,KeyError,TypeError,IndexError,sqlite3.Error) as error:
        print(json.dumps(dict(status='UNAVAILABLE',error=str(error),execution_enabled=False)))
        return 1


if __name__ == '__main__':
    sys.exit(main())
