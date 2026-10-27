"""Read-only worker status, reconciled paper results and audit decisions."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from dashboard.journal import paper_journal
from dashboard.research import inside
from workflow.common import clock, digest, read
from .policy import checked
from .capital import pending_change


def snapshot(workspace, now=None):
    now = now or clock()
    root = Path(workspace).resolve()
    auto = inside(root,root/'automation')
    absent = dict(configured=False,phase='NOT_CONFIGURED',worker_running=False,enabled=False,
                  paper=True,execution_enabled=False,decisions=[],equity_curve=[],ranked=[],positions=[])
    if not (auto/'settings.json').is_file():
        return absent
    status = read(inside(root,auto/'status.json'),{})
    configuration_error = None
    try:
        settings = checked(read(inside(root,auto/'settings.json')))
    except (ValueError,OSError,TypeError) as exc:
        configuration_error = str(exc)
        settings = checked(dict(enabled=False,risk=status.get('risk',{}),selection=status.get('selection',{})))
    scanner = read(inside(root,auto/'scanner.json'),{})
    candidates = read(inside(root,auto/'candidates.json'),{})
    journal = paper_journal(auto)
    fresh = bool(status.get('heartbeat_at') and 0 <= (now-clock(status['heartbeat_at'])).total_seconds() <= max(60,settings['poll_seconds']*3))
    running = fresh and status.get('worker_running') is True
    decisions, curve, pf = [],[],None
    path = inside(root,auto/'state/paper.sqlite')
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            pf = json.loads(db.execute('SELECT document FROM state WHERE id=1').fetchone()[0])
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'automation_decisions' in tables:
                decisions = [json.loads(row[0]) for row in db.execute('SELECT document FROM automation_decisions ORDER BY at DESC,rowid DESC LIMIT 60')]
            if 'automation_equity' in tables:
                columns={r[1] for r in db.execute('PRAGMA table_info(automation_equity)')}
                flow='capital_flow' if 'capital_flow' in columns else '0'
                curve = [dict(at=row[0],equity=row[1],realized_pnl=row[2],capital_flow=row[3],performance_equity=row[1]-row[3])
                         for row in db.execute('SELECT at,equity,realized,'+flow+' FROM automation_equity ORDER BY at')]
    marks = status.get('marks',{})
    marks_valid = bool(pf and status.get('portfolio_sha256') == digest(pf))
    positions = []
    for row in journal.get('positions',[]):
        symbol = row.get('ticker',row.get('symbol'))
        stored = status.get('positions',{}).get(symbol,{})
        mark = marks.get(symbol)
        if not (marks_valid and mark and 0 <= (now-clock(mark['at'])).total_seconds() <= 300): mark = None
        positions.append(dict(symbol=symbol,qty=row['shares'],stop=stored.get('plan',{}).get('stop'),
                              target=stored.get('plan',{}).get('target'),mark=mark))
    unrealized = sum(row['mark']['unrealized_pnl'] for row in positions) if all(row['mark'] for row in positions) else None
    capital = journal.get('initial_capital')
    funded = journal.get('funded_capital',capital)
    equity = funded+journal['realized_pnl']+unrealized if funded is not None and unrealized is not None and journal['status']=='RECORDED' else None
    peak, drawdown = capital or 0,0
    for row in curve:
        adjusted=row['performance_equity']
        peak=max(peak,adjusted)
        drawdown=max(drawdown,(peak-adjusted)/peak if peak else 0)
    ranked = []
    for row in candidates.get('ranked',[])[:20]:
        entry = row.get('entry',{})
        confirmed = clock(entry['signal_confirmed_at']) if entry.get('signal_confirmed_at') else None
        eligible = bool(entry.get('eligible') and confirmed and confirmed.date()==now.date() and 0 <= (now-confirmed).total_seconds() <= 300)
        ranked.append(dict(symbol=row['symbol'],score=row['score'],setups=row['daily_setups'],
                           eligible=eligible,checked_at=candidates.get('as_of'),confirmed_at=entry.get('signal_confirmed_at')))
    daily = scanner.get('daily',{})
    change=pending_change(root)
    return dict(configured=True,paper=True,execution_enabled=False,enabled=settings['enabled'],
                phase='CONFIGURATION_ERROR' if configuration_error else status.get('phase','STARTING') if running else 'WORKER_OFFLINE',worker_running=running,
                heartbeat_at=status.get('heartbeat_at'),next_open=status.get('next_open'),risk=settings['risk'],
                pending_capital_change=dict(capital=change['settings']['risk']['budget'],daily_loss=change['settings']['risk']['max_daily_loss_inr'],requested_at=change['requested_at']) if change else None,
                selection=settings['selection'],errors=status.get('errors',[])+([configuration_error] if configuration_error else []),
                scanner=dict(phase=scanner.get('phase','PENDING'),stage=scanner.get('stage'),error=scanner.get('error'),
                             started_at=scanner.get('started_at'),completed_at=scanner.get('completed_at'),
                             shortlisted=scanner.get('shortlisted'),orb=scanner.get('orb'),
                             daily={k:daily.get(k) for k in ('session','daily_cutoff','requested','scanned','failed','unavailable','stale','skipped','matches','retrieval_coverage','fresh_coverage')}),
                pending=dict(symbol=status['pending']['order']['symbol'],qty=status['pending']['order']['qty'],
                             submitted_at=status['pending']['submitted_at']) if status.get('pending') else None,
                positions=positions,ranked=ranked,decisions=decisions,equity_curve=curve[-500:],journal=journal,
                performance=dict(equity=equity,unrealized_pnl=unrealized,
                                 return_pct=((equity-journal.get('net_capital_flows',0))/capital-1)*100 if equity is not None and capital else None,
                                 max_drawdown_pct=drawdown*100),
                note='Scores rank rule-based setups; returns are measured after simulated costs and are not guaranteed. Returns and drawdown exclude capital deposits/withdrawals; return uses original starting capital.')


def set_entries(workspace, enabled):
    if type(enabled) is not bool:
        raise ValueError('enabled must be a boolean')
    root=Path(workspace).resolve()
    path=inside(root,root/'automation/settings.json')
    settings=checked(read(path))
    settings['enabled']=enabled
    from workflow.common import write
    write(path,settings)
    return dict(enabled=enabled,paper=True,execution_enabled=False,
                note='New entries paused; protective exits continue.' if not enabled else 'New simulated entries enabled under the saved paper policy.')
