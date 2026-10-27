"""User-requested capital changes wait for a flat account and retain history."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from workflow.common import clock, digest, read, write, number
from .policy import checked


def validate_request(doc):
    if not isinstance(doc,dict) or doc.get('kind') != 'PAPER_CAPITAL_CHANGE_REQUEST':
        raise ValueError('An explicit paper capital change request is required')
    body={k:v for k,v in doc.items() if k != 'id'}
    if set(body) != {'kind','requested_at','settings'} or doc.get('id') != digest(body):
        raise ValueError('Capital request integrity mismatch')
    if checked(doc['settings']) != doc['settings'] or not isinstance(doc['requested_at'],str):
        raise ValueError('Capital request requires complete validated settings and a dated request')
    clock(doc['requested_at'])
    return doc


def request_change(workspace, capital, daily_loss, now=None):
    root=Path(workspace)/'automation'
    settings=checked(read(root/'settings.json'))
    target=checked(settings)
    target['risk']['budget']=number(capital,'capital')
    target['risk']['max_daily_loss_inr']=number(daily_loss,'daily loss')
    target['risk']['max_loss_per_trade_inr']=min(target['risk']['max_loss_per_trade_inr'],target['risk']['max_daily_loss_inr'])
    target['risk']['max_order_inr']=min(target['risk']['max_order_inr'],target['risk']['budget']*target['risk']['max_position_pct']/100)
    target['enabled']=True
    target=checked(target)
    body=dict(kind='PAPER_CAPITAL_CHANGE_REQUEST',requested_at=(now or clock()).isoformat(),settings=target)
    document=dict(body,id=digest(body))
    # Existing exposure retains the policy under which it was opened.
    # Block all unfilled entries while capital cannot yet be withdrawn.
    settings['enabled']=False
    settings['risk']['max_daily_loss_inr']=target['risk']['max_daily_loss_inr']
    settings['risk']['max_loss_per_trade_inr']=min(settings['risk']['max_loss_per_trade_inr'],target['risk']['max_daily_loss_inr'])
    write(root/'settings.json',checked(settings))
    write(root/'capital-change.json',document)
    return dict(status='QUEUED_UNTIL_FLAT',capital=capital,daily_loss=daily_loss,id=document['id'])


def pending_change(workspace):
    root=Path(workspace)/'automation'
    path=root/'capital-change.json'
    if not path.is_file():return None
    request=validate_request(read(path))
    applied=read(root/'capital-change-status.json',{})
    if applied.get('id') == request['id'] and applied.get('status') == 'APPLIED':return None
    return request


def restore_applied_settings(workspace, settings_path):
    """Recover a committed withdrawal if a crash preceded the settings write."""
    root=Path(workspace)/'automation'
    path=root/'state/paper.sqlite'
    request=pending_change(workspace)
    if request is None or not path.is_file():return
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT document FROM events WHERE id=?',('capital-change:'+request['id'],)).fetchone()
    if row:
        event=json.loads(row[0])
        if event.get('request') != request:raise ValueError('Recorded capital request mismatch')
        write(settings_path,request['settings'])
        write(root/'capital-change-status.json',dict(id=request['id'],status='APPLIED',at=event['at']))


def apply_when_flat(workspace, engine, now):
    request=pending_change(workspace)
    if request is None:return None
    if engine.state['positions'] or engine.state.get('pending') or engine.ledger.portfolio()['positions']:
        return None
    target=request['settings']
    event=engine.ledger.change_capital(request,now)
    engine.save()
    root=Path(workspace)/'automation'
    write(root/'settings.json',target)
    write(root/'validated-settings.json',target)
    write(root/'capital-change-status.json',dict(id=request['id'],status='APPLIED',at=event['at']))
    engine.decision(now,'CAPITAL_CHANGED',capital=target['risk']['budget'],daily_loss=target['risk']['max_daily_loss_inr'])
    return target
