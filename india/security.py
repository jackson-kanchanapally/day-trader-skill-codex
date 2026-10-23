"""Dated, explicitly INR-denominated security metadata supplied by the user."""
import csv
import datetime as dt
import json
import math
from .data import timestamp, symbol_name


def number(value, name):
    if isinstance(value, bool):
        raise ValueError(name+' must be positive and finite')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name+' must be positive and finite')
    return value


def read(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def security_master(path, as_of):
    if not path:
        return {}
    if str(path).endswith('.csv'):
        with open(path, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
    else:
        doc = read(path)
        rows = doc.get('securities', []) if isinstance(doc, dict) else doc
    return security_records(rows, as_of)


def security_records(rows, as_of):
    result = {}
    for row in rows:
        symbol = symbol_name(row['symbol'])
        if symbol in result:
            raise ValueError('Duplicate security metadata: '+symbol)
        tick = number(row['tick_size'], 'tick_size')
        if row.get('currency') != 'INR' or row.get('tick_unit') != 'INR':
            raise ValueError('Security master must explicitly use INR prices and INR tick units')
        effective = dt.date.fromisoformat(row['effective_from'])
        until = dt.date.fromisoformat(row['effective_to'])
        published = timestamp(row['published_at'])
        if effective > until or published > as_of:
            raise ValueError('Invalid/future security metadata')
        if not row.get('source'):
            raise ValueError('Security metadata requires source provenance')
        if effective <= as_of.date() <= until:
            result[symbol] = dict(row, tick_size=tick, fixture=str(row.get('fixture', False)).lower() == 'true')
    return result


def verify_tick(symbol, tick, master):
    number(tick, 'tick')
    row = master.get(symbol)
    if not row:
        return {'status': 'UNVERIFIED', 'supplied': tick, 'reason': 'No dated INR security metadata supplied'}
    if row.get('fixture') and not symbol.startswith('DEMO.'):
        return {'status': 'UNVERIFIED', 'supplied': tick, 'reason': 'Synthetic metadata cannot verify a real instrument'}
    if abs(row['tick_size']-tick) > 1e-9:
        return {'status': 'MISMATCH', 'supplied': tick, 'metadata_tick': row['tick_size'], 'source': row['source']}
    return {'status': 'VERIFIED', 'supplied': tick, 'source': row['source'],
            'published_at': row['published_at'], 'fixture': row.get('fixture', False)}

