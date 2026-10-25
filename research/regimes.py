"""Sourced, session-open regime annotations; no inferred classification."""
import datetime as dt
import re
from typing import Optional

from india.audit import digest
from india.data import IST, timestamp

POLICY = 'published-by-session-open-v1'


def model_name(value: str) -> str:
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',value):
        raise ValueError('Regime model must be a versioned identifier of at most 64 characters')
    return value


def record(original: dict) -> dict:
    required = {'exchange','model','benchmark','method','label','effective_from',
                'effective_to','data_through','published_at','source'}
    if not isinstance(original,dict) or set(original) != required:
        raise ValueError('Regime evidence requires exchange, model, benchmark, method, label, dated validity, data_through, published_at and source')
    row = dict(original)
    model_name(row['model'])
    if row['exchange'] not in ('NSE','BSE'):
        raise ValueError('Regime exchange must be NSE or BSE')
    for key in ('benchmark','method','source'):
        if not isinstance(row[key],str) or not row[key].strip():
            raise ValueError('Regime '+key+' must be a nonempty sourced string')
    if not isinstance(row['label'],str) or not re.fullmatch(r'[A-Z][A-Z0-9_-]{0,63}',row['label']) or row['label'] == 'UNKNOWN':
        raise ValueError('Regime label must be an uppercase identifier; UNKNOWN is reserved for missing evidence')
    if any(not isinstance(row[k],str) for k in ('effective_from','effective_to','data_through','published_at')):
        raise ValueError('Regime dates and timezone-aware timestamps must be ISO strings')
    start,end = (dt.date.fromisoformat(row[k]) for k in ('effective_from','effective_to'))
    if start > end or any(row[k] != value.isoformat() for k,value in (('effective_from',start),('effective_to',end))):
        raise ValueError('Regime validity must be ordered ISO YYYY-MM-DD dates')
    for key in ('data_through','published_at'):
        row[key] = timestamp(row[key]).isoformat()
    if row['data_through'] > row['published_at']:
        raise ValueError('Regime data_through must not follow publication')
    return row


def select(records: list, day: dt.date, exchange: str, model: str) -> Optional[dict]:
    """Resolve publications before validity; never resurrect a revised window."""
    model_name(model)
    if exchange not in ('NSE','BSE'):
        raise ValueError('Regime exchange must be NSE or BSE')
    opening = dt.datetime.combine(day,dt.time(9,15),IST).isoformat()
    versions = {}
    for row in records:
        if row['exchange'] != exchange or row['model'] != model or row['published_at'] > opening:
            continue
        key = row['effective_from']
        if key not in versions or row['published_at'] > versions[key]['published_at']:
            versions[key] = row
    eligible = [row for row in versions.values() if row['effective_from'] <= day.isoformat() <= row['effective_to']]
    if not eligible:
        return None
    latest = max(row['published_at'] for row in eligible)
    selected = [row for row in eligible if row['published_at'] == latest]
    if len(selected) != 1:
        raise ValueError('Ambiguous overlapping regime evidence at the same publication time')
    return selected[0]


def snapshot(records: list, timeline: list, metadata: dict, model: Optional[str]) -> dict:
    """Only selected evidence enters the report; future/unselected rows do not."""
    if model is not None:
        model_name(model)
    sessions, sourced, unknown = {}, 0, 0
    for day in timeline:
        members = metadata[day]
        venues = sorted({'NSE' if symbol.endswith('.NS') else 'BSE' for symbol in members})
        sessions[day.isoformat()] = {}
        for exchange in venues:
            selected = select(records,day,exchange,model) if model is not None else None
            count = sum(('NSE' if symbol.endswith('.NS') else 'BSE') == exchange for symbol in members)
            sourced += count if selected else 0
            unknown += 0 if selected else count
            sessions[day.isoformat()][exchange] = dict(label=selected['label'] if selected else 'UNKNOWN',
                status='SOURCED' if selected else 'UNKNOWN', evidence=selected,
                evidence_sha256=digest(selected) if selected else None)
    return dict(model=model,decision_policy=POLICY,sessions=sessions,
                coverage=dict(eligible_symbol_sessions=sourced+unknown,sourced_symbol_sessions=sourced,
                              unknown_symbol_sessions=unknown),
                interpretation='Supplied labels known at session open; no independent source or classifier validation')
