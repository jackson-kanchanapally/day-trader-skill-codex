"""Serve the local dashboard; the browser cannot spawn agents or submit orders."""
import argparse
import datetime as dt
import errno
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from india.team import (ARTIFACTS, REQUIRED, VERDICTS, _source_check, load_session,
                        read_json, write_json)
from .opportunities import OpportunityStore
from .research import ResearchStore
from .journal import paper_journal

STATIC=Path(__file__).resolve().parent/'dist'
ORDER=('scanner','news','analyst','entry','exit','timemachine','budget','risk','broker','alerts')


def session_path(root, name):
    root=Path(root).resolve()
    candidate=(root/name).resolve()
    if candidate.parent!=root or not (candidate/'context.json').is_file():
        raise ValueError('Unknown session')
    return candidate


def optional(path, default=None):
    if not path.is_file(): return default
    return read_json(path)


def sessions(root):
    root=Path(root).resolve()
    if not root.is_dir(): return []
    result=[]
    for path in root.iterdir():
        try:
            path=session_path(root,path.name)
            context=read_json(path/'context.json');settings=context['settings']
            result.append(dict(id=path.name,symbol=settings['symbol'],as_of=settings['as_of'],
                               modified=(path/'context.json').stat().st_mtime))
        except (ValueError,OSError,KeyError,TypeError): continue
    return sorted(result,key=lambda s:s['modified'],reverse=True)


def review_errors(doc, role, context, agents):
    if not isinstance(doc,dict): return ['Review is not an object']
    errors=[]
    expected=dict(schema_version=1,role=role,snapshot_id=context['snapshot_id'],
                  as_of=context['settings']['as_of'],artifact_sha256=context['artifact_hashes'][role])
    if any(doc.get(k)!=v for k,v in expected.items()): errors.append('Snapshot, cutoff or evidence hash mismatch')
    agent=doc.get('agent_id')
    if not isinstance(agent,str) or not agent.strip() or agent!=agents.get(role): errors.append('Native identity mismatch')
    elif list(agents.values()).count(agent)>1: errors.append('Identity reused across roles')
    if doc.get('verdict') not in VERDICTS or doc.get('verdict')=='NOT_APPLICABLE': errors.append('Invalid verdict')
    if not isinstance(doc.get('summary'),str) or not doc['summary'].strip(): errors.append('Missing summary')
    if not isinstance(doc.get('evidence'),list) or not doc['evidence']: errors.append('Missing evidence')
    if role in ('news','analyst') and doc.get('verdict') in ('PASS','WARN'):
        try: supported=_source_check(doc.get('sources'),context)
        except (ValueError,TypeError,AttributeError): supported=False
        if not supported: errors.append('Missing or inadmissible source metadata')
    return errors


def session_view(root, name):
    path=session_path(root,name)
    context=read_json(path/'context.json')
    integrity=[]
    try: load_session(path)
    except (ValueError,OSError,KeyError,TypeError):
        integrity.append('Snapshot, engine or artifact integrity check failed. Archived data is shown without clearance.')
    manifest=optional(path/'delegations.json',{})
    agents=manifest.get('agents',{})
    if (manifest.get('snapshot_id')!=context['snapshot_id'] or manifest.get('schema_version')!=1
            or not isinstance(agents,dict)):
        integrity.append('Delegation manifest mismatch');agents={}
    activity=optional(path/'activity.json',{})
    events=activity.get('events',[]) if activity.get('snapshot_id')==context['snapshot_id'] else []
    if not isinstance(events,list): events=[]
    roles=[];reviews={}
    for role in ORDER:
        artifact=optional(path/ARTIFACTS[role],{})
        review=optional(path/'reviews'/(role+'.json'))
        errors=review_errors(review,role,context,agents) if review is not None else []
        if review is not None: reviews[role]=review
        recorded=[e for e in events if e.get('role')==role and e.get('agent_id')==agents.get(role)]
        latest=recorded[-1] if recorded else {}
        phase='queued'
        if agents.get(role): phase='dispatched'
        if latest.get('state') in ('running','failed'): phase=latest['state']
        if review is not None: phase='invalid' if errors else 'completed'
        roles.append(dict(role=role,phase=phase,agent_id=agents.get(role),review=review,
                          errors=errors,artifact=artifact,activity=recorded,
                          saved_at=dt.datetime.fromtimestamp((path/'reviews'/(role+'.json')).stat().st_mtime,
                              dt.timezone.utc).isoformat() if review is not None else None))
    report=optional(path/'report.json')
    usable_report=(isinstance(report,dict) and report.get('snapshot_id')==context['snapshot_id']
                   and report.get('as_of')==context['settings']['as_of']
                   and report.get('reviews')==reviews and len(reviews)==len(REQUIRED)
                   and not integrity and not any(r['errors'] for r in roles))
    # Never keep showing a prior clearance after a reviewer edits its decision.
    status=report['status'] if usable_report else ('INTEGRITY_ERROR' if integrity else 'AWAITING_FINALIZATION')
    scanner=optional(path/'watchlist.json',{}).get('data',{}).get('analysis',{})
    snapshot=read_json(path/'snapshot.json')
    last_day=scanner.get('last_bar','')[:10]
    candles=[b for b in snapshot['bars'] if b['time'][:10]==last_day and '09:15'<=b['time'][11:16]<'15:15']
    return dict(id=name,context=context,roles=roles,analysis=scanner,candles=candles,
                report=report if usable_report else None,status=status,integrity_errors=integrity,
                report_outdated=report is not None and not usable_report,activity=events,
                execution_enabled=False,observed_at=dt.datetime.now(dt.timezone.utc).isoformat())


def record_activity(directory, role, agent_id, state):
    if role not in ORDER or state not in ('dispatched','running','completed','failed'):
        raise ValueError('Invalid role or lifecycle state')
    path=Path(directory).resolve();context,_=load_session(path)
    manifest=read_json(path/'delegations.json');agents=manifest['agents']
    if (manifest.get('snapshot_id')!=context['snapshot_id'] or not isinstance(agent_id,str) or not agent_id.strip()
            or agents.get(role)!=agent_id or list(agents.values()).count(agent_id)!=1):
        raise ValueError('Activity needs the actual unique identity recorded in delegations.json')
    if state=='completed':
        errors=review_errors(optional(path/'reviews'/(role+'.json')),role,context,agents)
        if errors: raise ValueError('Completion needs a valid independent review: '+', '.join(errors))
    data=optional(path/'activity.json',dict(schema_version=1,snapshot_id=context['snapshot_id'],events=[]))
    if data.get('snapshot_id')!=context['snapshot_id']: raise ValueError('Activity belongs to another snapshot')
    data['events'].append(dict(role=role,agent_id=agent_id,state=state,
                              time=dt.datetime.now(dt.timezone.utc).isoformat()))
    data['events']=data['events'][-500:]
    write_json(path/'activity.json',data)


def handler(root, workspace=None):
    workspace=Path(workspace if workspace is not None else Path(root)/'_scanner').resolve()
    research_store=ResearchStore(workspace,root,sessions,session_view)
    opportunity_store=OpportunityStore(workspace,research=research_store)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Loopback only, no cross-origin browser access or DNS rebinding.
            if self.headers.get('Host','') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):
                self.respond(403,dict(error='Local dashboard only'));return
            url=urlsplit(self.path).path
            try:
                if url=='/api/opportunities':
                    self.respond(200,opportunity_store.listing());return
                if url.startswith('/api/opportunities/'):
                    query=parse_qs(urlsplit(self.path).query)
                    self.respond(200,opportunity_store.detail(unquote(url[len('/api/opportunities/'):]),query.get('scan',[None])[0]));return
                if url=='/api/research':
                    self.respond(200,research_store.listing());return
                if url.startswith('/api/research/'):
                    self.respond(200,research_store.detail(unquote(url[len('/api/research/'):])));return
                if url=='/api/journal':
                    self.respond(200,paper_journal(workspace));return
                if url=='/api/sessions': self.respond(200,dict(sessions=sessions(root)));return
                if url.startswith('/api/sessions/'):
                    self.respond(200,session_view(root,unquote(url[len('/api/sessions/'):])));return
                asset=(STATIC/('index.html' if url=='/' else unquote(url).lstrip('/'))).resolve()
                mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}.get(asset.suffix)
                if not mime or (url!='/' and not url.startswith('/assets/')) or STATIC.resolve() not in asset.parents or not asset.is_file():
                    self.respond(404,dict(error='Not found. Build the React frontend with npm run build --prefix dashboard.'));return
                self.respond(200,asset.read_bytes(),mime)
            except (ValueError,OSError,TypeError,KeyError,IndexError):
                message=('Saved scan is unavailable or changed. Refresh the opportunities list.' if url.startswith('/api/opportunities') else
                         'Saved research is unavailable, changed or fails evidence binding.' if url.startswith('/api/research') else
                         'Paper ledger is unavailable or failed reconciliation.' if url=='/api/journal' else
                         'Session is unavailable, incomplete or changed. Retrying on the next refresh.')
                self.respond(409,dict(error=message))

        def respond(self, code, value, mime='application/json'):
            body=json.dumps(value,allow_nan=False).encode() if mime=='application/json' else value
            self.send_response(code)
            self.send_header('Content-Type',mime+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(body)

        def log_message(self,*args): pass
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions',default='trader/team',help='Directory containing team session folders')
    parser.add_argument('--workspace',default='trader',help='Scanner workspace containing watchlist.json and optional scan snapshots')
    parser.add_argument('--port',type=int,default=8765)
    sub=parser.add_subparsers(dest='command')
    activity=sub.add_parser('activity',help='Coordinator records actual native lifecycle events; never fabricates progress')
    activity.add_argument('--session',required=True)
    activity.add_argument('--role',required=True,choices=ORDER)
    activity.add_argument('--agent-id',required=True)
    activity.add_argument('--state',required=True,choices=['dispatched','running','completed','failed'])
    args=parser.parse_args()
    if args.command=='activity':
        try: record_activity(args.session,args.role,args.agent_id,args.state)
        except (ValueError,OSError,KeyError,TypeError) as exc: parser.exit(1,str(exc)+'\n')
        return
    try:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(Path(args.sessions).resolve(),Path(args.workspace).resolve()))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            alternate = args.port+1 if args.port < 65535 else 8765
            parser.exit(1, 'Port %s is already in use.\n'
                        'If the dashboard is already running, open http://127.0.0.1:%s\n'
                        'For another copy, run: python3 -m dashboard --port %s\n' % (args.port,args.port,alternate))
        raise
    print('Agent dashboard: http://127.0.0.1:%s' % server.server_port,flush=True)
    print('Watching '+str(Path(args.sessions).resolve()),flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
