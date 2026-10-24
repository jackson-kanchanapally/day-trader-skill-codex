"""Index saved assessments and real team sessions without inventing agent reviews."""
import hashlib
import json
from pathlib import Path

from india.data import symbol_name, timestamp
from india.security import security_master, verify_tick
from .decision import https

ASSESSMENT = 'COORDINATOR_PREREQUISITE_ASSESSMENT_NOT_INDEPENDENT_AGENT_REVIEWS'


def inside(root, candidate):
    root, candidate = Path(root).resolve(), Path(candidate)
    if root not in candidate.resolve().parents or any(p.is_symlink() for p in (candidate, *candidate.parents) if p != root):
        raise ValueError('Research path is outside the selected workspace')
    return candidate


class ResearchStore:
    def __init__(self, workspace, team_root, list_team, load_team):
        self.root, self.team_root = Path(workspace).resolve(), Path(team_root).resolve()
        self.list_team, self.load_team = list_team, load_team

    def _names(self):
        try:
            names = json.loads(inside(self.root,self.root/'instrument-names.json').read_text()).get('names',{})
            return {k:v.strip()[:180] for k,v in names.items() if isinstance(v,str) and v.strip()}
        except (ValueError, OSError, TypeError, AttributeError):
            return {}

    def _assessment(self, name):
        if not isinstance(name, str) or Path(name).name != name or name in ('', '.', '..'):
            raise ValueError('Unknown assessment')
        directory = inside(self.root, self.root/'reviews'/name)
        path = inside(self.root, directory/'readiness.json')
        doc = json.loads(path.read_text())
        if (doc.get('kind') != ASSESSMENT or doc.get('execution_enabled') is not False
                or doc.get('independent_team_dispatched') is not False or doc.get('independent_risk_clearance') is not False
                or any(doc.get(k) is not None for k in ('order', 'quantity', 'stop', 'target', 'cost_estimate'))):
            raise ValueError('Assessment is not an unapproved coordinator finding')
        symbol_name(doc['symbol'])
        timestamp(doc['as_of'])
        findings = doc.get('coordinator_role_findings', [])
        if (not isinstance(findings, list) or any(not isinstance(r, dict) or
                any(not isinstance(r.get(k), str) or not r[k].strip() for k in ('role', 'status', 'decision')) for r in findings)):
            raise ValueError('Invalid coordinator findings')
        if doc.get('evidence_status') == 'UNAVAILABLE':
            pending = {'UNAVAILABLE','REQUIRED','PENDING','NOT_READY','NO_CURRENT_PLAN','NO_INDEPENDENT_CLEARANCE',
                       'USER_LIMITS_REQUIRED','NO_TICKET','NO_ORDER_BOUND_ALERTS','INELIGIBLE_AT_CUTOFF'}
            if (any(doc.get(k) is not None for k in ('original_scan_source','original_scan_sha256','original_daily_setup'))
                    or any(r['status'] not in pending for r in findings)):
                raise ValueError('An unbound prerequisite note cannot claim completed setup evidence')
            return directory, doc
        reference = Path(doc['original_scan_source'])
        if reference.is_absolute():
            source = inside(self.root, reference)
        else:
            if reference.parts and reference.parts[0] == self.root.name:
                reference = Path(*reference.parts[1:])
            source = inside(self.root, self.root/reference)
        payload = source.read_bytes()
        scan = json.loads(payload)
        origin = doc.get('original_scan_as_of', doc['as_of'])
        if (scan.get('mode') != 'daily' or scan.get('currency') != 'INR' or scan.get('paper') is not True
                or scan.get('execution_enabled') is not False
                or hashlib.sha256(payload).hexdigest() != doc['original_scan_sha256'] or scan['as_of'] != origin
                or timestamp(origin) > timestamp(doc['as_of'])
                or doc['original_daily_setup'] != next((h for h in scan['hits'] if h['symbol'] == doc['symbol']), None)):
            raise ValueError('Assessment does not match its original saved daily evidence')
        return directory, doc

    def listing(self):
        items, rejected = [], 0
        for s in self.list_team(self.team_root):
            items.append(dict(id='team:'+s['id'], kind='TEAM', symbol=s['symbol'], as_of=s['as_of'],
                              status='SAVED_SESSION', label='Independent team session', session_id=s['id']))
        directory = self.root/'reviews'
        if directory.is_dir():
            for path in directory.iterdir():
                if not path.is_dir() or not (path/'readiness.json').exists():
                    continue
                try:
                    _, doc = self._assessment(path.name)
                    items.append(dict(id='assessment:'+path.name, kind='ASSESSMENT', symbol=doc['symbol'],
                                      as_of=doc['as_of'], status='PREREQUISITES_PENDING', label='Coordinator assessment',
                                      source_scan_cutoff=None if doc.get('evidence_status')=='UNAVAILABLE' else doc.get('original_scan_as_of',doc['as_of']),
                                      summary='Saved prerequisite findings. Independent risk clearance remains pending.'))
                except (ValueError, KeyError, TypeError, OSError, StopIteration):
                    rejected += 1
        items.sort(key=lambda r: timestamp(r['as_of']), reverse=True)
        names = self._names()
        for item in items:
            item['name'] = names.get(item['symbol'],item['symbol'])
        return dict(items=items, rejected=rejected, execution_enabled=False)

    def detail(self, identifier):
        kind, separator, name = identifier.partition(':')
        if not separator:
            raise ValueError('Unknown research record')
        if kind == 'team':
            doc = self.load_team(self.team_root, name)
            budget = next((r['artifact'].get('data', {}) for r in doc['roles']
                           if r['role'] == 'budget' and isinstance(r.get('artifact'),dict)), {})
            if not isinstance(budget,dict):
                budget = {}
            return dict(id=identifier, kind='TEAM', label='Independent team session', symbol=doc['context']['settings']['symbol'],
                        name=self._names().get(doc['context']['settings']['symbol'],doc['context']['settings']['symbol']),
                        as_of=doc['context']['settings']['as_of'], status=doc['status'], session_id=name,
                        findings=[dict(role=r['role'], status=r['review'].get('verdict') if r['review'] and not r['errors'] else r['phase'],
                                       decision=r['review'].get('summary') if r['review'] and not r['errors'] else 'Independent review pending or invalid.')
                                  for r in doc['roles']], integrity_errors=doc['integrity_errors'],
                        proposal=budget.get('sizing') if not doc['integrity_errors'] else None,
                        sources=[], execution_enabled=False)
        if kind != 'assessment':
            raise ValueError('Unknown research record')
        directory, doc = self._assessment(name)
        tick = dict(status='UNVERIFIED')
        try:
            meta = inside(self.root, directory/'security-master.json')
            tick = verify_tick(doc['symbol'], doc['tick_verification']['supplied'], security_master(meta, timestamp(doc['as_of'])))
        except (ValueError, KeyError, TypeError, OSError):
            pass
        source = doc.get('issuer_source', {})
        if not isinstance(source,dict):
            source = {}
        sources = [source] if https(source.get('url')) else []
        findings = doc.get('coordinator_role_findings', [])
        return dict(id=identifier, kind='ASSESSMENT', label='Coordinator assessment', symbol=doc['symbol'],
                    name=self._names().get(doc['symbol'],doc['symbol']),
                    as_of=doc['as_of'], status='PREREQUISITES_PENDING', findings=findings,
                    source_scan_cutoff=None if doc.get('evidence_status')=='UNAVAILABLE' else doc.get('original_scan_as_of',doc['as_of']),
                    evidence_status=doc.get('evidence_status','BOUND_DAILY'),
                    daily_setup=doc.get('original_daily_setup'), tick=tick, sources=sources,
                    news=doc.get('news_findings'), inputs=doc.get('user_inputs', {}),
                    history=doc.get('history_coverage'), independent_risk_clearance=False,
                    integrity_errors=[], execution_enabled=False)
