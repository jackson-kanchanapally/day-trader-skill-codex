"""Transport and request-control tests; fake RPC is never specialist evidence."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from dashboard.__main__ import handler
from dashboard.runner import CodexRunner, RunnerError


class FakeClient:
    def __init__(self, executable, cwd, on_event, on_exit):
        self.event, self.on_exit = on_event, on_exit
        self.closed = False
        self.calls, self.responses = [], []
        self.started = threading.Event()
        self.count = 0

    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/start':
            return {'thread': {'id': 'transport-test-thread'}}
        if method == 'turn/start':
            self.count += 1
            self.turn = 'transport-test-turn-' + str(self.count)
            self.event(dict(method='turn/started', params=dict(threadId='transport-test-thread', turn=dict(id=self.turn))))
            self.started.set()
            return {'turn': {'id': self.turn}}
        if method == 'turn/interrupt':
            self.emit('turn/completed', turn=dict(id=self.turn, status='interrupted'))
        return {}

    def notify(self, method, params):
        self.calls.append((method, params))

    def respond(self, identifier, result=None, error=None):
        self.responses.append(dict(id=identifier, result=result, error=error))

    def emit(self, method, **params):
        self.event(dict(method=method, params=dict(threadId='transport-test-thread', turnId=self.turn, **params)))

    def close(self):
        self.closed = True
        self.on_exit()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.factory_calls = []
        def factory(*args):
            self.fake = FakeClient(*args)
            self.factory_calls.append(args)
            return self.fake
        self.patch = patch('dashboard.runner.shutil.which', return_value='/test/codex')
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.runner = CodexRunner(self.root, self.root/'trader', self.root/'trader/team', client_factory=factory)
        self.addCleanup(self.runner.close)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.005)
        self.fail('Timed out waiting for test worker')

    def start(self):
        run = self.runner.start('Research $trader-team SSWL.NS at 2026-10-24T19:05:49.207303+05:30; capital INR 5000; risk INR 500')
        self.wait_for(lambda: self.runner.detail(run['id'])['status'] == 'running')
        return run['id']

    def test_reading_status_never_starts_codex(self):
        self.assertTrue(self.runner.state()['available'])
        self.assertEqual(self.factory_calls, [])
        self.assertFalse((self.root/'trader').exists())

    def test_cutoff_prompt_skill_and_sandbox_preserved_without_shell(self):
        skill = self.root/'trader-team/SKILL.md'
        skill.parent.mkdir(); skill.write_text('Test skill text')
        identifier = self.start()
        calls = dict(self.fake.calls)
        self.assertEqual(calls['thread/start']['sandbox'], 'workspace-write')
        self.assertEqual(calls['thread/start']['approvalPolicy'], 'on-request')
        turn = calls['turn/start']
        self.assertEqual(turn['input'][0]['text'], self.runner.detail(identifier)['prompt'])
        self.assertEqual(turn['input'][1], dict(type='skill', name='trader-team', path=str(skill.resolve())))
        self.assertFalse(turn['sandboxPolicy']['networkAccess'])
        self.assertFalse(self.runner.detail(identifier)['execution_enabled'])

    def test_one_active_request_and_unknown_actions_fail(self):
        identifier = self.start()
        with self.assertRaises(RunnerError): self.runner.start('Duplicate')
        with self.assertRaises(RunnerError): self.runner.reply(identifier, 'Reply while running')
        with self.assertRaises(RunnerError): self.runner.detail('../outside')
        for prompt in ('', None, 'x'*32001):
            with self.assertRaises(RunnerError): self.runner.start(prompt)

    def test_stream_completion_and_follow_up_use_original_thread(self):
        identifier = self.start()
        self.fake.emit('item/agentMessage/delta', itemId='message1', delta='Need ')
        self.fake.emit('item/agentMessage/delta', itemId='message1', delta='account details')
        self.fake.emit('item/completed', item=dict(id='message1', type='agentMessage', text='Final authoritative response'))
        self.fake.emit('turn/completed', turn=dict(id=self.fake.turn, status='completed'))
        run = self.runner.detail(identifier)
        self.assertEqual(run['messages'][-1]['text'], 'Final authoritative response')
        self.assertIsNone(self.runner.state()['active_id'])
        self.runner.reply(identifier, 'Cash is INR 5000')
        self.wait_for(lambda: self.fake.count == 2)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'thread/start']), 1)
        self.assertIn(('thread/resume', {'threadId': run['thread_id']}), self.fake.calls)

    def pending(self, identifier, method='item/tool/requestUserInput', **overrides):
        params = dict(threadId='transport-test-thread', turnId=self.fake.turn, questions=[dict(id='cash', question='Available cash?')])
        params.update(overrides)
        self.runner._event(dict(id=71, method=method, params=params))

    def test_exact_questions_require_real_answers_and_cannot_be_replayed(self):
        identifier = self.start(); self.pending(identifier)
        self.assertEqual(self.runner.detail(identifier)['status'], 'waiting')
        self.assertFalse(self.fake.responses)
        with self.assertRaises(RunnerError): self.runner.answer(identifier, 71, {})
        self.runner.answer(identifier, 71, {'cash': 'INR 5000'})
        self.assertEqual(self.fake.responses[-1]['result'], {'answers': {'cash': {'answers': ['INR 5000']}}})
        with self.assertRaises(RunnerError): self.runner.answer(identifier, 71, {'cash': 'INR 5000'})

    def test_approvals_are_one_time_and_validate_native_choices(self):
        identifier = self.start()
        self.pending(identifier, 'item/commandExecution/requestApproval', availableDecisions=['decline', 'cancel'], command='test command')
        with self.assertRaises(RunnerError): self.runner.answer(identifier, 71, 'accept')
        with self.assertRaises(RunnerError): self.runner.answer(identifier, 71, 'acceptForSession')
        self.runner.answer(identifier, 71, 'decline')
        self.assertEqual(self.fake.responses[-1]['result'], {'decision': 'decline'})

    def test_foreign_and_stale_requests_never_gain_permission(self):
        identifier = self.start()
        self.pending(identifier, threadId='unowned-thread')
        self.assertEqual(self.runner.detail(identifier)['pending'], [])
        self.assertIsNotNone(self.fake.responses[-1]['error'])
        self.pending(identifier, turnId='old-turn')
        self.assertEqual(self.runner.detail(identifier)['pending'], [])
        self.pending(identifier, method='arbitrary/method')
        self.assertIsNotNone(self.fake.responses[-1]['error'])

    def test_native_child_questions_and_diff_previews_remain_owned(self):
        identifier = self.start()
        self.runner._event(dict(method='thread/started', params=dict(thread=dict(id='test-child', parentThreadId='transport-test-thread'))))
        self.runner._event(dict(method='turn/started', params=dict(threadId='test-child', turn=dict(id='child-turn'))))
        item = dict(id='diff1', type='fileChange', changes=[dict(path='research.json', diff='+source evidence')])
        self.runner._event(dict(method='item/started', params=dict(threadId='test-child', turnId='child-turn', item=item)))
        self.pending(identifier, method='item/fileChange/requestApproval', threadId='test-child', turnId='child-turn', itemId='diff1')
        self.assertEqual(self.runner.detail(identifier)['pending'][0]['item'], item)
        self.runner.answer(identifier, 71, 'decline')
        self.assertEqual(self.fake.responses[-1]['result'], {'decision':'decline'})
        # A coordinator's turn completion cannot imply its child has finished.
        self.fake.emit('turn/completed', turn=dict(id=self.fake.turn, status='completed'))
        self.assertEqual(self.runner.state()['active_id'], identifier)
        self.runner._event(dict(method='turn/completed', params=dict(threadId='test-child', turn=dict(id='child-turn', status='completed'))))
        self.assertEqual(self.runner.detail(identifier)['status'], 'completed')

    def test_resolved_question_is_removed_and_stop_requires_confirmation(self):
        identifier = self.start(); self.pending(identifier)
        self.fake.emit('serverRequest/resolved', requestId=71)
        self.assertEqual(self.runner.detail(identifier)['pending'], [])
        self.runner.stop(identifier)
        self.wait_for(lambda: self.runner.detail(identifier)['status'] == 'interrupted')
        self.assertIsNone(self.runner.state()['active_id'])
        with self.assertRaises(RunnerError): self.runner.stop(identifier)

    def test_server_restart_retains_history_without_fake_running_state(self):
        identifier = self.start()
        restored = CodexRunner(self.root, self.root/'trader', self.root/'trader/team')
        self.assertEqual(restored.detail(identifier)['status'], 'interrupted')
        self.assertEqual(restored.detail(identifier)['pending'], [])
        self.assertIsNone(restored.state()['active_id'])
        self.assertFalse((self.root/'trader/team').exists())
        self.assertFalse((self.root/'trader/state/paper.sqlite').exists())

    def test_disconnection_is_failed_not_independent_completion(self):
        identifier = self.start(); self.fake.close()
        self.assertEqual(self.runner.detail(identifier)['status'], 'failed')
        self.assertIsNone(self.runner.state()['active_id'])

    def test_http_requires_same_origin_and_nonce_and_accepts_only_run_actions(self):
        try: server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.root/'trader/team', self.root/'trader', self.runner))
        except PermissionError: self.skipTest('Sandbox disallows loopback sockets')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_port)
        headers = {'Content-Type': 'application/json', 'Origin': base, 'X-Trader-Token': self.runner.token}
        def post(path, doc, override=None):
            return urllib.request.urlopen(urllib.request.Request(base+path, data=json.dumps(doc).encode(), headers={**headers, **(override or {})}, method='POST'))
        with urllib.request.urlopen(base+'/api/runner') as response:
            self.assertTrue(json.load(response)['available'])
        self.assertFalse(self.factory_calls)
        for override in ({'Origin':'https://evil.example'}, {'X-Trader-Token':'wrong'}, {'Host':'evil.example'}):
            with self.assertRaises(urllib.error.HTTPError) as exc: post('/api/runs', {'prompt':'Test'}, override)
            self.assertEqual(exc.exception.code, 403); exc.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as exc: post('/api/orders', {})
        self.assertEqual(exc.exception.code, 404); exc.exception.close()
        with post('/api/runs', {'prompt':'Research only'}) as response:
            self.assertEqual(response.status, 202); identifier = json.load(response)['id']
        with urllib.request.urlopen(base+'/api/runs/'+identifier) as response:
            self.assertFalse(json.load(response)['execution_enabled'])


if __name__ == '__main__': unittest.main()
