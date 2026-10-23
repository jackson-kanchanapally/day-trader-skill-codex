"""Atomic SQLite run records and deterministic offline numerical replay."""
import datetime as dt
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .analysis import report, backtest
from .data import Bar, timestamp, validate, IST
from .market import Calendar
from .research import research
from .operations import health


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def code_hash():
    root=Path(__file__).resolve().parent
    files=sorted(root.glob('*.py'))+[root.parent/'legacy/trader-scanner/scan.py']
    h=hashlib.sha256()
    for path in files:
        h.update(path.name.encode());h.update(path.read_bytes())
    return h.hexdigest()


def encode_bars(bars):
    validate(bars)
    return [dict(time=b.time.isoformat(),open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume) for b in bars]


def record(path, settings, result, bars):
    snapshot=encode_bars(bars)
    payload={'settings':settings,'bars':snapshot,'result':result}
    run_id=uuid.uuid4().hex
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(str(target),timeout=10)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, schema_version INTEGER NOT NULL, code_hash TEXT NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',
                   (run_id,dt.datetime.now(IST).isoformat(),1,code_hash(),digest(payload),canonical(payload)))
    return {'run_id':run_id,'snapshot_sha256':digest(snapshot)}


def load(path, run_id):
    # Opening a missing audit file must not create a new empty database.
    target=Path(path).resolve()
    if not target.is_file():
        raise ValueError('Audit database does not exist')
    with closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        row=db.execute('SELECT schema_version,code_hash,payload_hash,payload FROM runs WHERE id=?',(run_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown audit run ID')
    schema,code,expected,raw=row
    if schema != 1:
        raise ValueError('Unsupported audit schema version')
    payload=json.loads(raw)
    if digest(payload) != expected:
        raise ValueError('Audit payload integrity check failed')
    return payload,code


def replay(path, run_id):
    payload,original_code=load(path,run_id)
    s=payload['settings']
    bars=validate([Bar(timestamp(r['time']),*(r[k] for k in ('open','high','low','close','volume')))
                   for r in payload['bars']])
    now=timestamp(s['as_of'])
    calendar=Calendar(s['calendar'])
    args=(s['symbol'],s['tick'],s['capital'],s['risk'],s['rr'],s['cost_config'],calendar)
    if s['command'] in ('analyze','doctor'):
        result=report(bars,now,*args)
        if s['command']=='doctor': result=health(result,now,calendar)
    elif s['command']=='backtest':
        result=backtest([b for b in bars if b.time+dt.timedelta(minutes=5)<=now],*args)
    elif s['command']=='research':
        result=research(bars,now,*args,train_fraction=s['train_fraction'],risk_fraction=s['risk_fraction'])
    elif s['command']=='history':
        result={'mode':'historical','bars':encode_bars([b for b in bars if b.time.date()<now.date()])}
    else:
        raise ValueError('This run type does not support numerical replay')
    for key in ('source','historical_replay','instrument_key','provider_symbol','provider_attempts','elapsed_ms'):
        if key in payload['result']: result[key]=payload['result'][key]
    matches=canonical(result)==canonical(payload['result'])
    return {'mode':'audit_replay','run_id':run_id,'matches':matches,
            'same_code':original_code==code_hash(),'snapshot_sha256':digest(payload['bars']),
            'result':result,'execution_enabled':False}
