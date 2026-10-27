"""Local Codex app-server client. Only explicit dashboard actions start turns.

Protocol: https://learn.chatgpt.com/docs/app-server
Run history is distinct from specialist reviews and paper-account records.
"""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import subprocess
import threading


TERMINAL = {'completed', 'failed', 'interrupted'}
APPROVALS = {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval'}
QUESTIONS = {'item/tool/requestUserInput', 'tool/requestUserInput'}


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RunnerError(ValueError):
    pass


class AppServer:
    """Multiplex newline-delimited RPC without blocking the notification reader."""
    def __init__(self, executable, cwd, on_event, on_exit):
        self.on_event, self.on_exit = on_event, on_exit
        self.lock = threading.Lock()
        self.pending = {}
        self.serial = 0
        self.closed = False
        self.process = subprocess.Popen(
            [executable, 'app-server', '--listen', 'stdio://'], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, message):
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise RunnerError('Codex connection closed. Start a new request.')
            self.process.stdin.write(json.dumps(message) + '\n')
            self.process.stdin.flush()

    def request(self, method, params, timeout=45):
        result = queue.Queue(maxsize=1)
        with self.lock:
            self.serial += 1
            identifier = 'dashboard-' + str(self.serial)
            self.pending[identifier] = result
        try:
            self._send(dict(id=identifier, method=method, params=params))
            try:
                message = result.get(timeout=timeout)
            except queue.Empty:
                # A timed-out mutation may still run. Tear down the connection;
                # never retry turn/start and accidentally duplicate a request.
                self.close()
                raise RunnerError('Codex did not acknowledge the request. Connection stopped; inspect saved results before retrying.')
            if 'error' in message:
                raise RunnerError(message['error'].get('message', 'Codex request failed'))
            return message.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def notify(self, method, params):
        self._send(dict(method=method, params=params))

    def respond(self, identifier, result=None, error=None):
        self._send(dict(id=identifier, **({'error': error} if error else {'result': result})))

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if 'method' not in message and 'id' in message:
                    with self.lock:
                        target = self.pending.get(message['id'])
                    if target:
                        target.put_nowait(message)
                elif 'method' in message:
                    self.on_event(message)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.closed = True
                waiting = list(self.pending.values())
            for target in waiting:
                try:
                    target.put_nowait({'error': {'message': 'Codex app-server disconnected. Check your local Codex installation/sign-in.'}})
                except queue.Full:
                    pass
            self.on_exit()

    def close(self):
        with self.lock:
            self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


class CodexRunner:
    def __init__(self, cwd, workspace, sessions, executable=None, client_factory=AppServer):
        self.cwd = Path(cwd).resolve()
        self.workspace, self.sessions = Path(workspace).resolve(), Path(sessions).resolve()
        self.directory = self.workspace / 'dashboard-runs'
        self.executable = shutil.which(str(executable or 'codex'))
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.client_lock = threading.Lock()
        self.client = None
        self.runs = {}
        self.active = None
        self.token = secrets.token_urlsafe(32)
        self.closing = False
        if self.directory.exists():
            if self.directory.is_symlink() or self.directory.resolve().parent != self.workspace:
                raise RunnerError('Dashboard run directory must be inside the selected workspace')
            for path in self.directory.glob('*.json'):
                try:
                    if path.is_symlink():
                        continue
                    run = json.loads(path.read_text())
                    if not re.fullmatch(r'[0-9a-f]{32}', run['id']) or path.stem != run['id']:
                        continue
                    if run['status'] not in TERMINAL:
                        run.update(status='interrupted', pending=[], error='Dashboard server restarted. This saved run is no longer connected.')
                        self._save(run)
                    self.runs[run['id']] = run
                except (ValueError, OSError, KeyError, TypeError):
                    continue

    def _save(self, run):
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RunnerError('Unsafe dashboard run directory')
        path = self.directory / (run['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        # Exclusive creation rejects pre-existing files/symlinks at the temp path.
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(run, stream, ensure_ascii=False)
            stream.write('\n')
        temporary.replace(path)

    def _get(self, identifier):
        if identifier not in self.runs:
            raise RunnerError('Unknown dashboard run')
        return self.runs[identifier]

    def state(self):
        with self.lock:
            runs = sorted(self.runs.values(), key=lambda r: r['created_at'], reverse=True)
            return dict(available=bool(self.executable), token=self.token, active_id=self.active,
                        runs=[{k: r.get(k) for k in ('id', 'status', 'created_at', 'title')} for r in runs],
                        error=None if self.executable else 'Codex CLI was not found. Put codex on PATH or start the dashboard with --codex-bin /absolute/path/to/codex.',
                        execution_enabled=False)

    def detail(self, identifier):
        with self.lock:
            return copy.deepcopy(self._get(identifier))

    def _client(self):
        with self.client_lock:
            if self.closing:
                raise RunnerError('Dashboard is shutting down')
            if self.client and not self.client.closed:
                return self.client
            if not self.executable:
                raise RunnerError('Codex CLI not found. Copy the request into Codex or configure --codex-bin.')
            self.client = self.client_factory(str(self.executable), str(self.cwd), self._event, self._disconnected)
            self.client.request('initialize', dict(
                clientInfo=dict(name='trader_dashboard', title='Trade Desk', version='1.0.0'),
                capabilities=dict(experimentalApi=True),
            ))
            self.client.notify('initialized', {})
            return self.client

    def start(self, prompt):
        prompt = self._text(prompt)
        with self.lock:
            if self.closing:
                raise RunnerError('Dashboard is shutting down')
            if not self.executable:
                raise RunnerError('Codex CLI not found. Configure --codex-bin or use Copy request.')
            if self.active:
                raise RunnerError('Another request is active. Finish it or stop it before starting a new one.')
            run = dict(id=secrets.token_hex(16), title=prompt.splitlines()[0][:140], prompt=prompt,
                       status='starting', created_at=timestamp(), thread_id=None, turn_id=None,
                       messages=[dict(id='user-1', role='user', text=prompt)], activity=[], pending=[],
                       error=None, cancel_requested=False, child_threads={}, parent_result=None, tool_items={}, execution_enabled=False)
            self._save(run)
            self.runs[run['id']] = run
            self.active = run['id']
            threading.Thread(target=self._turn, args=(run['id'], prompt), daemon=True).start()
            return copy.deepcopy(run)

    @staticmethod
    def _text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 32000:
            raise RunnerError('Enter a request between 1 and 32,000 characters')
        return value.strip()

    def reply(self, identifier, text):
        text = self._text(text)
        with self.lock:
            run = self._get(identifier)
            if self.active or run['status'] not in TERMINAL or not run.get('thread_id'):
                raise RunnerError('Wait for this turn to finish, or answer its pending question first')
            run.update(status='starting', turn_id=None, error=None, cancel_requested=False, child_threads={}, parent_result=None, tool_items={})
            run['messages'].append(dict(id=secrets.token_hex(8), role='user', text=text))
            self.active = identifier
            self._save(run)
            threading.Thread(target=self._turn, args=(identifier, text), daemon=True).start()
            return copy.deepcopy(run)

    def _turn(self, identifier, text):
        try:
            client = self._client()
            with self.lock:
                run = self._get(identifier)
                if run['cancel_requested']:
                    self._finish(run, 'interrupted')
                    return
                thread_id = run['thread_id']
            if thread_id:
                client.request('thread/resume', dict(threadId=thread_id))
            else:
                instructions = (
                    'You are serving a local Indian equity research dashboard. '
                    'Follow the explicitly invoked trader skill and its prerequisites. '
                    f'Use the mutable workspace {self.workspace} and independent session root {self.sessions}. '
                    'Preserve requested cutoffs and source hashes. Use the original modular skill workflow by default; independent review is opt-in. Use saved configuration and explicit overrides, obtain missing capital/risk choices, and label provisional capital-based sizing when cash is unverified. '
                    'Native specialist contexts are required for independent review; deterministic CLI output is not an agent. '
                    'Record lifecycle events only after real native events. Save unavailable research visibly. '
                    'Dashboard requests authorize research and proposals only. Never invoke paper-ticket approve/fill '
                    'or submit live orders from this interface. Tool/file permissions are not paper-trade consent. '
                    'This thread does not inherit another chat: obtain missing context from saved evidence or the user.'
                )
                response = client.request('thread/start', dict(
                    cwd=str(self.cwd), approvalPolicy='on-request', sandbox='workspace-write',
                    developerInstructions=instructions,
                ))
                thread_id = response['thread']['id']
                with self.lock:
                    run['thread_id'] = thread_id
                    self._save(run)
            with self.lock:
                if run['cancel_requested']:
                    self._finish(run, 'interrupted')
                    return
            inputs = [dict(type='text', text=text)]
            for skill in ('trader-team', 'trader-scanner'):
                path = self.cwd / skill / 'SKILL.md'
                if '$' + skill in text and path.is_file():
                    inputs.append(dict(type='skill', name=skill, path=str(path.resolve())))
            response = client.request('turn/start', dict(
                threadId=thread_id, input=inputs,
                approvalPolicy='on-request',
                sandboxPolicy=dict(type='workspaceWrite', writableRoots=[str(self.cwd), str(self.workspace), str(self.sessions)], networkAccess=False),
            ))
            with self.lock:
                if run['status'] not in TERMINAL:
                    run['turn_id'] = response['turn']['id']
                    run['status'] = 'stopping' if run['cancel_requested'] else ('waiting' if run['pending'] else 'running')
                    self._save(run)
                cancel = run['cancel_requested'] and run['status'] not in TERMINAL
            if cancel:
                self._interrupt(identifier)
        except (RunnerError, OSError, KeyError, TypeError) as exc:
            with self.lock:
                run = self._get(identifier)
                if run['status'] not in TERMINAL:
                    self._finish(run, 'failed', str(exc))

    def _finish(self, run, status, error=None):
        run.update(status=status, pending=[], error=error)
        if self.active == run['id']:
            self.active = None
        self._save(run)

    def _event(self, message):
        method, params = message['method'], message.get('params', {})
        with self.lock:
            run = self.runs.get(self.active)
            # Only native parent/child relationships grant request ownership.
            # These transport records never become specialist review artifacts.
            children = run.setdefault('child_threads', {}) if run else {}
            if run and method == 'thread/started':
                thread = params.get('thread', {})
                if thread.get('parentThreadId') in {run.get('thread_id'), *children} and thread.get('parentThreadId'):
                    children[thread['id']] = None
                    self._save(run)
                return
            owner = params.get('threadId')
            if not run or owner not in {run.get('thread_id'), *children}:
                if 'id' in message and self.client:
                    self.client.respond(message['id'], error=dict(code=-32601, message='No active dashboard thread owns this request'))
                return
            if 'id' in message:
                if method not in APPROVALS | QUESTIONS:
                    self.client.respond(message['id'], error=dict(code=-32601, message='This request type is not supported by Trade Desk'))
                    return
                expected_turn = run['turn_id'] if owner == run['thread_id'] else children[owner]
                if params.get('turnId') != expected_turn or not expected_turn:
                    self.client.respond(message['id'], error=dict(code=-32602, message='Stale or unowned turn'))
                    return
                item_key = owner + ':' + str(params.get('itemId'))
                run['pending'].append(dict(id=message['id'], method=method, params=params,
                                           item=run.get('tool_items', {}).get(item_key)))
                run['status'] = 'waiting'
            elif method == 'serverRequest/resolved':
                run['pending'] = [p for p in run['pending'] if p['id'] != params.get('requestId')]
                if not run['pending'] and run['status'] == 'waiting':
                    run['status'] = 'running'
            elif method == 'turn/started':
                if owner == run['thread_id']:
                    run['turn_id'] = params['turn']['id']
                else:
                    children[owner] = params['turn']['id']
                run['status'] = 'stopping' if run['cancel_requested'] else ('waiting' if run['pending'] else 'running')
            elif method == 'turn/completed':
                turn = params['turn']
                expected_turn = run['turn_id'] if owner == run['thread_id'] else children[owner]
                if turn['id'] != expected_turn:
                    return
                if owner == run['thread_id']:
                    status = turn.get('status')
                    run['parent_result'] = dict(status=status if status in TERMINAL else 'failed', error=(turn.get('error') or {}).get('message'))
                else:
                    children[owner] = None
                run['pending'] = [p for p in run['pending'] if p['params'].get('threadId') != owner]
                if run.get('parent_result') and not any(children.values()):
                    self._finish(run, **run['parent_result'])
                else:
                    self._save(run)
                return
            elif method == 'item/started' and params.get('item', {}).get('type') in ('fileChange', 'commandExecution'):
                item = params['item']
                key = owner + ':' + item['id']
                run.setdefault('tool_items', {})[key] = {k: item[k] for k in ('id', 'type', 'command', 'cwd', 'changes') if k in item}
                run['activity'].append(dict(time=timestamp(), label=str(item.get('command', item['type']))[:500]))
                run['activity'] = run['activity'][-80:]
            elif owner != run['thread_id']:
                # Child output belongs in real specialist files, not the
                # coordinator conversation. Only its questions are surfaced here.
                return
            elif params.get('turnId') != run.get('turn_id'):
                return
            elif method in ('item/started', 'item/completed'):
                item = params.get('item', {})
                if item.get('type') == 'agentMessage':
                    self._message(run, item['id'], item.get('text', ''), replace=True)
                elif method == 'item/started':
                    label = item.get('command') if item.get('type') == 'commandExecution' else item.get('type', 'Working')
                    run['activity'].append(dict(time=timestamp(), label=str(label)[:500]))
                    run['activity'] = run['activity'][-80:]
            elif method == 'item/agentMessage/delta':
                self._message(run, params['itemId'], params.get('delta', ''))
            elif method == 'error':
                run['error'] = params.get('error', {}).get('message', 'Codex reported an error')
            else:
                return
            self._save(run)

    @staticmethod
    def _message(run, identifier, text, replace=False):
        message = next((m for m in run['messages'] if m['id'] == identifier), None)
        if message is None:
            message = dict(id=identifier, role='assistant', text='')
            run['messages'].append(message)
        message['text'] = (text if replace else message['text'] + text)[-100000:]

    def answer(self, identifier, request_id, answer):
        with self.lock:
            run = self._get(identifier)
            pending = next((p for p in run['pending'] if p['id'] == request_id), None)
            if self.active != identifier or not pending or not self.client or self.client.closed:
                raise RunnerError('This question or approval is no longer pending')
            owner = pending['params'].get('threadId')
            expected_turn = run['turn_id'] if owner == run['thread_id'] else run.get('child_threads', {}).get(owner)
            if pending['params'].get('turnId') != expected_turn or not expected_turn:
                raise RunnerError('This question belongs to an older turn')
            if pending['method'] in APPROVALS:
                permitted = pending['params'].get('availableDecisions') or ['accept', 'decline', 'cancel']
                if not isinstance(answer, str) or answer not in ('accept', 'decline', 'cancel') or answer not in permitted:
                    raise RunnerError('Choose a supported one-time approval decision')
                result = dict(decision=answer)
            else:
                questions = pending['params']['questions']
                if not isinstance(answer, dict) or set(answer) != {q['id'] for q in questions}:
                    raise RunnerError('Answer every pending question')
                result = dict(answers={q['id']: dict(answers=[self._text(answer[q['id']])]) for q in questions})
            # Respond to this exact native request once; browser cannot send RPC.
            self.client.respond(pending['id'], result=result)
            run['pending'].remove(pending)
            if not run['pending']:
                run['status'] = 'running'
            self._save(run)
            return copy.deepcopy(run)

    def stop(self, identifier):
        with self.lock:
            run = self._get(identifier)
            if self.active != identifier:
                raise RunnerError('This request is no longer active')
            run['cancel_requested'] = True
            run['status'] = 'stopping'
            self._save(run)
            if run['turn_id']:
                threading.Thread(target=self._interrupt, args=(identifier,), daemon=True).start()
            return copy.deepcopy(run)

    def _interrupt(self, identifier):
        try:
            with self.lock:
                run = self._get(identifier)
                turns = {**run.get('child_threads', {}), run['thread_id']: run['turn_id']}
            for thread_id, turn_id in turns.items():
                if turn_id:
                    self.client.request('turn/interrupt', dict(threadId=thread_id, turnId=turn_id))
        except (RunnerError, OSError) as exc:
            with self.lock:
                if self.active == identifier:
                    self.runs[identifier]['error'] = 'Stop was not confirmed: ' + str(exc)
                    self._save(self.runs[identifier])

    def _disconnected(self):
        with self.lock:
            if self.active:
                self._finish(self.runs[self.active], 'interrupted' if self.closing else 'failed', 'Codex connection closed; inspect saved results before retrying.')

    def close(self):
        self.closing = True
        if self.client:
            self.client.close()
