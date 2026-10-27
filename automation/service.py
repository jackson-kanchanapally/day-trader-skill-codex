"""Independent scanner thread plus priority position management and persistence."""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import threading
import time

from india.market import DEFAULT_CALENDAR, Calendar
from india.security import security_master
from workflow.common import clock, config, digest, read, write
from workflow.intraday_index import capture_daily
from workflow.scanning import archive_scan
from workflow.strategy import scan
from .engine import PaperEngine
from .policy import checked, mandate
from .selection import shortlist, rank_entries
from .sources import OnlineSources
from .capital import pending_change, apply_when_flat, restore_applied_settings


class Scanner:
    def __init__(self, workspace, settings, calendar, now, messages, sources):
        self.root, self.settings, self.cal, self.now = Path(workspace),settings,calendar,now
        self.messages, self.sources = messages,sources

    def run(self):
        started = self.now()
        status = dict(phase='RUNNING',started_at=started.isoformat(),bucket=int(started.timestamp())//300)
        path = self.root/'automation/scanner.json'
        try:
            daily_path = self.root/'daily-watchlist.json'
            saved = read(self.root/'automation/shortlist.json', {})
            master = security_master(self.root/'automation/metadata.json',started) if (self.root/'automation/metadata.json').is_file() else {}
            fresh_daily = (saved.get('session') == started.date().isoformat() and daily_path.is_file()
                           and saved.get('selection_sha256') == digest(self.settings['selection'])
                           and saved.get('daily_sha256') == hashlib.sha256(daily_path.read_bytes()).hexdigest() and master)
            if not fresh_daily:
                status['stage'] = 'FULL_UNIVERSE_DAILY_SCAN'
                write(path,status)
                names, source = self.sources.universe()
                self.root.mkdir(parents=True,exist_ok=True)
                temporary = self.root/'universe.txt.automation-new'
                temporary.write_text('\n'.join(names)+'\n')
                os.replace(temporary,self.root/'universe.txt')
                master = self.sources.metadata(names)
                if not master:
                    raise ValueError('No eligible dated tick metadata was available from the instrument source')
                # Protective management must receive today's ticks before the
                # slower full-universe scan completes or fails its coverage gate.
                self.messages.put(dict(kind='MASTER',master=master))
                cutoff = self.now()
                data = self.sources.market(cutoff)
                daily = scan(data,names,cutoff,config(**self.settings['risk']),mode='daily',calendar=self.cal,
                             workers=self.settings['workers'],batch_size=self.settings['batch_size'])
                archive_scan(self.root,daily,data)
                write(self.root/'watchlist.json',daily)
                # This is a retrieval-outage gate. Stale histories and recent
                # listings still have explicit outcomes, and cannot supply hits.
                # A missing candle in one stock must not invalidate another
                # stock's independently verified complete daily setup.
                coverage = (len(names)-len(daily['failed']))/len(names) if names else 0
                accepted,rejected = shortlist(daily,self.settings,master)
                saved = dict(session=cutoff.date().isoformat(),daily_cutoff=daily['as_of'],
                             daily_sha256=hashlib.sha256(daily_path.read_bytes()).hexdigest(),
                             selection_sha256=digest(self.settings['selection']),requested=len(names),
                             scanned=daily['scanned'],failed=len(daily['failed']),unavailable=len(daily['unavailable']),
                             stale=len(daily['stale']),skipped=len(daily['skipped']),matches=len(daily['hits']),
                             retrieval_coverage=coverage, fresh_coverage=daily['scanned']/len(names) if names else 0,
                             accepted=accepted,rejected=rejected,universe_source=source)
                status['daily'] = saved
                if coverage < self.settings['minimum_daily_coverage']:
                    raise ValueError('Daily retrieval coverage %.1f%% is below the configured minimum' % (coverage*100))
                write(self.root/'automation/shortlist.json',saved)
            now = self.now()
            self.messages.put(dict(kind='MASTER',master=master))
            status.update(stage='MONITORING_SHORTLIST',daily=saved,
                          shortlisted=len(saved['accepted']),started_at=started.isoformat())
            write(path,status)
            candidates = []
            if saved['accepted'] and self.settings['enabled'] and now.strftime('%H:%M') <= self.settings['risk']['no_entries_after']:
                daily, origin = capture_daily(daily_path)
                if origin['sha256'] != saved['daily_sha256']:
                    raise ValueError('Daily evidence changed before the ORB scan')
                names = [row['symbol'] for row in saved['accepted']]
                data = self.sources.market(now)
                result = scan(data,names,now,config(**self.settings['risk']),calendar=self.cal,
                              workers=self.settings['workers'],batch_size=self.settings['batch_size'],
                              eligible_only=True,min_volume_x=self.settings['selection']['min_volume_x'] or None,volume_basis='opening-range')
                result['daily_origin'] = origin
                run = self.root/'scan-runs'/('automation-'+now.date().isoformat())/'orb'
                archive_scan(run,result,data)
                write(run/'watchlist.json',result)
                candidates = rank_entries(result['results'],saved['accepted'])
                status.update(orb=dict(as_of=result['as_of'],requested=result['counts']['requested'],scanned=result['scanned'],
                              failed=len(result['failed']),skipped=len(result['skipped']),eligible=len(candidates)))
                write(self.root/'automation/candidates.json',dict(as_of=result['as_of'],ranked=candidates,
                      ranking='daily setup strength and participation + completed breakout volume; not profit probabilities'))
            self.messages.put(dict(kind='CANDIDATES',candidates=candidates,master=master,
                                   daily_sha256=saved['daily_sha256'],settings_sha256=digest(self.settings)))
            status.update(phase='COMPLETE',completed_at=self.now().isoformat(),elapsed_seconds=(self.now()-started).total_seconds())
        except Exception as exc:
            status.update(phase='ERROR',error=str(exc),completed_at=self.now().isoformat())
            self.messages.put(dict(kind='SCAN_ERROR',error=str(exc)))
        write(path,status)


def run(workspace='trader', settings_path=None, once=False, now=clock, source_factory=OnlineSources):
    root = Path(workspace).resolve()
    auto = root/'automation'
    auto.mkdir(parents=True,exist_ok=True)
    settings_path = Path(settings_path or auto/'settings.json')
    lock = (auto/'worker.lock').open('a+')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError('Automatic paper worker is already running for this workspace') from None
    cal = DEFAULT_CALENDAR
    validated_path = auto/'validated-settings.json'
    try:
        try:
            restore_applied_settings(root,settings_path)
            settings = checked(read(settings_path))
            engine = PaperEngine(root,settings,cal)
            write(validated_path,settings)
        except (ValueError,OSError,KeyError,TypeError):
            # Retain protective management after a restart with damaged or
            # incompatible new settings. Never reset the existing account.
            settings = dict(checked(read(validated_path)),enabled=False)
            engine = PaperEngine(root,settings,cal)
    except Exception:
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
        raise
    validated_sha = digest(settings)
    write(auto/'worker.json',dict(pid=os.getpid(),started_at=now().isoformat(),paper=True,execution_enabled=False))
    stop, messages = threading.Event(),queue.Queue()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous[sig] = signal.signal(sig,lambda *args:stop.set())
    worker, last_bucket, retry_after, last_candidates = None,None,None,[]
    master = security_master(auto/'metadata.json',now()) if (auto/'metadata.json').is_file() else {}
    status = {}
    try:
        while not stop.is_set():
            current = now()
            errors = []
            try:
                updated = checked(read(settings_path))
                if updated['risk']['budget'] != settings['risk']['budget']:
                    raise ValueError('Changing initial paper capital requires a new workspace')
                settings = updated
                if digest(updated) != validated_sha:
                    write(validated_path,updated)
                    validated_sha = digest(updated)
                engine.settings, engine.cfg = settings,config(**settings['risk'])
                engine.policy = mandate(dict(settings,enabled=True))
            except (ValueError,OSError,KeyError) as exc:
                errors.append(str(exc))
                engine.settings = dict(settings,enabled=False)
            change=None
            try:
                change=pending_change(root)
                if change:engine.settings=dict(engine.settings,enabled=False)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                errors.append(str(exc));engine.settings=dict(engine.settings,enabled=False)
            sources = source_factory(root,settings,cal,now)
            needed = engine.required_symbols()
            if needed:
                data = sources.market(current)
                data.prefetch(needed,workers=min(2,settings['workers']),current_only=True)
                engine.manage(data,master,now())
            if change:
                try:
                    applied=apply_when_flat(root,engine,now())
                    if applied:
                        settings=applied
                        validated_sha=digest(settings)
                        engine.settings,engine.cfg=settings,config(**settings['risk'])
                        engine.policy=mandate(dict(settings,enabled=True))
                        last_candidates=[];last_bucket=None;change=None
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    errors.append(str(exc))
            while not messages.empty():
                message = messages.get_nowait()
                if message['kind'] in ('MASTER','CANDIDATES'): master = message['master']
                if message['kind'] == 'CANDIDATES':
                    if (message['settings_sha256'] == digest(settings) and (root/'daily-watchlist.json').is_file()
                            and hashlib.sha256((root/'daily-watchlist.json').read_bytes()).hexdigest() == message['daily_sha256']):
                        last_candidates = message['candidates']
                    else:
                        errors.append('Daily source or policy changed after candidate scan; candidates discarded')
                if message['kind'] == 'SCAN_ERROR':
                    errors.append(message['error'])
                    retry_after = current+dt.timedelta(minutes=15)
                    last_candidates = []
            if last_candidates and engine.settings['enabled']:
                data = sources.market(now())
                data.prefetch([r['symbol'] for r in last_candidates],workers=settings['workers'],current_only=True)
                engine.consider(last_candidates,data,master,now())
                last_candidates = []
            phase, next_open = 'WAITING_SESSION',None
            try:
                trading = cal.trading_day(current.date(),'NSE')
                eligible_window = trading and dt.time(8,45) <= current.time() <= dt.time.fromisoformat(engine.cfg['no_entries_after'])
                next_open = cal.next_open(current,'NSE').isoformat()
                if engine.state['positions']: phase='MANAGING_POSITIONS'
                elif engine.state.get('pending'): phase='PENDING_ENTRY'
                elif not settings['enabled']: phase='ENTRIES_PAUSED'
                elif eligible_window: phase='SCANNING' if worker and worker.is_alive() else 'MONITORING'
                if change:phase='CAPITAL_CHANGE_PENDING'
                bucket = int(current.timestamp())//300
                if eligible_window and settings['enabled'] and not change and not errors and (worker is None or not worker.is_alive()) and bucket != last_bucket and (retry_after is None or current >= retry_after):
                    job = Scanner(root,settings,cal,now,messages,sources)
                    last_bucket = bucket
                    if once:
                        job.run()
                    else:
                        worker = threading.Thread(target=job.run,name='paper-market-scanner',daemon=True)
                        worker.start()
            except ValueError as exc:
                errors.append(str(exc));phase='CALENDAR_UNAVAILABLE'
            pf = engine.ledger.portfolio()
            engine.record_equity(now())
            status = dict(schema_version=1,paper=True,execution_enabled=False,phase=phase,enabled=engine.settings['enabled'],
                          heartbeat_at=now().isoformat(),pid=os.getpid(),next_open=next_open,
                          risk=settings['risk'],selection=settings['selection'],pending=engine.state.get('pending'),
                          positions=engine.state['positions'],marks=engine.state.get('marks',{}),
                          unresolved_positions=engine.state.get('unresolved_positions',[]),portfolio_sha256=digest(pf),
                          errors=errors,worker_running=True,
                          note='Automatic local simulated fills only; missing prices are never fabricated and returns are not guaranteed.')
            write(auto/'status.json',status)
            if once:
                if not messages.empty(): continue
                break
            stop.wait(settings['poll_seconds'])
    finally:
        status.update(phase='STOPPED',heartbeat_at=now().isoformat(),worker_running=False)
        write(auto/'status.json',status)
        engine.close()
        for sig, handler in previous.items(): signal.signal(sig,handler)
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
