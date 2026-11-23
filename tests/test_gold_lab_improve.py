import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from india import gold_lab as L
from india import gold_lab_improve as I

ROOT = Path(__file__).resolve().parents[1]
TODAY = '2026-12-01'

GOOD_PLUGIN = '''
def strategies(lab):
    def above(m, n):
        return [None if i < n else (1.0 if m.c[i] > m.c[i - n] else 0.0) for i in range(len(m))]
    return [lab.Strategy('test_above_40', 'test', above, {'n': 40}, posthoc=True, added='%s', note='test')]
''' % TODAY

PEEKING_PLUGIN = '''
def strategies(lab):
    def peek(m):
        return [1.0 if i + 1 < len(m) and m.c[i + 1] > m.c[i] else 0.0 for i in range(len(m))]
    return [lab.Strategy('test_peek', 'test', peek, posthoc=True, added='%s', note='looks ahead')]
''' % TODAY


def snap(names, errors=None, **over):
    s = {n: {'family': 'f', 'posthoc': True, 'added': TODAY, 'version': 1, 'note': '', 'source': 'c_x.py'} for n in names}
    for n, v in over.items():
        s[n].update(v)
    return {'strategies': s, 'errors': errors or {}}


class PolicyTests(unittest.TestCase):
    def test_diff_policy(self):
        ok = [('A', 'india/gold_lab_challengers/c_2026_12_carry.py'), ('M', I.JOURNAL)]
        self.assertEqual(I.check_diff(ok, 'old\n', 'old\nnew\n'), [])
        cases = [
            ([('M', 'india/gold_lab.py')], 'old', 'old', 'india/gold_lab.py'),
            ([('M', 'india/gold_lab_challengers/c_2026_11_blends.py')], 'old', 'old', 'c_2026_11_blends'),
            ([('D', 'india/gold_lab_challengers/c_2026_11_blends.py')], 'old', 'old', 'deletes'),
            ([('A', 'india/gold_lab_challengers/helper.py')], 'old', 'old', 'helper.py'),
            ([('A', 'tests/test_x.py')], 'old', 'old', 'tests/test_x.py'),
            ([('M', I.JOURNAL)], 'old\n', 'rewritten\n', 'rewritten'),
        ]
        for rows, before, after, text in cases:
            bad = I.check_diff(rows, before, after)
            self.assertTrue(bad and text in bad[0], (rows, bad))
        big = I.check_diff([('A', 'india/gold_lab_challengers/c_big.py')], '', '', read=lambda p: 'x' * (I.MAX_FILE_BYTES + 1))
        self.assertIn('larger', big[0])

    def test_registry_policy(self):
        before = snap(['a', 'b'])
        new, bad = I.check_registry(before, snap(['a', 'b', 'c']), TODAY)
        self.assertEqual((new, bad), (['c'], []))
        checks = [
            (snap(['a']), 'removed'),
            (snap(['a', 'b', 'c', 'd', 'e', 'f']), 'at most'),
            (snap(['a', 'b', 'c'], c={'posthoc': False}), 'posthoc'),
            (snap(['a', 'b', 'c'], c={'added': '2026-01-01'}), 'added='),
            (snap(['a', 'b'], errors={'c_bad.py': 'SyntaxError'}), 'does not load'),
            (snap(['a', 'b', 'c', 'd', 'e'], c={'source': '1.py'}, d={'source': '2.py'}, e={'source': '3.py'}), 'files'),
        ]
        for after, text in checks:
            _, bad = I.check_registry(before, after, TODAY)
            self.assertTrue(any(text in b for b in bad), (text, bad))

    def test_research_gate_keeps_files_whose_strategies_all_reach_the_median(self):
        rows = [{'name': n, 'test': {'sharpe': s}, 'source': src} for n, s, src in (
            ('old1', 0.1, None), ('old2', 0.3, None), ('old3', 0.5, None),
            ('new_good', 0.4, 'c_good.py'), ('new_mixed_a', 0.6, 'c_mixed.py'), ('new_mixed_b', 0.2, 'c_mixed.py'))]
        kept, verdict = I.research_gate(rows, {'new_good', 'new_mixed_a', 'new_mixed_b'})
        self.assertEqual(kept, {'c_good.py'})
        self.assertEqual(verdict['new_good']['median'], 0.3)
        self.assertFalse(verdict['new_mixed_b']['kept'])

    def test_parse_reply(self):
        doc = {'files': [{'path': 'india/gold_lab_challengers/c_a.py', 'content': 'x'}], 'journal': 'why'}
        for text in (json.dumps(doc), 'Sure!\n```json\n%s\n```\nGood luck' % json.dumps(doc), 'Here: %s done' % json.dumps(doc)):
            files, journal = I.parse_reply(text)
            self.assertEqual((files[0]['path'], journal), ('india/gold_lab_challengers/c_a.py', 'why'))
        for text in ('no json here', '{"files": "nope"}', '{"files": [{"path": 1}]}'):
            with self.assertRaises(ValueError):
                I.parse_reply(text)

    def test_cli_commands(self):
        fill = dict(prompt='P', prompt_file='/w/p.md', brief_file='/w/b.md', workdir='/w')
        self.assertEqual(I.build_cli('codex', 'gpt-x', **fill), ['codex', 'exec', '-m', 'gpt-x', '--full-auto', 'P'])
        self.assertEqual(I.build_cli('pi', 'openai/gpt-6-sol', **fill),
                         ['pi', '-p', '--no-session', '--no-approve', '--model', 'openai/gpt-6-sol', 'P'])
        self.assertEqual(I.build_cli('pi', **fill), ['pi', '-p', '--no-session', '--no-approve', 'P'])
        self.assertEqual(I.build_cli('cmd', 'm1', custom='x --model {model_name} {prompt}', **fill), ['x', '--model', 'm1', 'P'])
        self.assertEqual(I.build_cli('claude', **fill)[:3], ['claude', '-p', 'P'])
        self.assertIn('/w/p.md', I.build_cli('aider', 'deepseek/deepseek-chat', **fill))
        self.assertEqual(I.build_cli('cmd', custom='my-ai --in "{prompt_file}" --cwd {workdir}', **fill),
                         ['my-ai', '--in', '/w/p.md', '--cwd', '/w'])
        with self.assertRaises(ValueError):
            I.build_cli('cmd', **fill)


class LocalModelRefusalTests(unittest.TestCase):
    """Local LLMs are refused outright: a 14B model restarted the 16 GB laptop on 2026-11-19."""

    def test_local_endpoints_providers_and_models_are_refused(self):
        for kw in ({'provider': 'ollama'}, {'provider': 'lmstudio'},
                   {'base_url': 'http://localhost:11434/v1'}, {'base_url': 'http://127.0.0.1:1234/v1'},
                   {'base_url': 'http://192.168.1.20:8000/v1'}, {'base_url': 'http://172.20.0.5/v1'},
                   {'base_url': 'http://gpu-box.local/v1'}, {'model': 'ollama/qwen2.5-coder:14b'},
                   {'model': 'lmstudio/llama'}):
            self.assertIsNotNone(I.local_reason(**kw), kw)

    def test_server_models_pass(self):
        for kw in ({'provider': 'deepseek'}, {'base_url': 'https://api.deepseek.com'},
                   {'base_url': 'https://velona.in/v1', 'model': 'deepseek/deepseek-v4.1-flash'},
                   {'model': 'openai/gpt-6-sol'}, {'base_url': 'https://172.67.1.1/v1'}):
            self.assertIsNone(I.local_reason(**kw), kw)

    def test_the_runner_refuses_before_doing_anything(self):
        with tempfile.TemporaryDirectory() as d:
            repo = mini_repo(d)
            called = []
            for kw in ({'agent': 'api', 'provider': 'ollama'}, {'agent': 'api', 'provider': 'custom', 'base_url': 'http://localhost:8080/v1'},
                       {'agent': 'pi', 'model': 'ollama/qwen3:8b'}, {'agent': 'aider', 'model': 'ollama_chat/llama3'}):
                agent = kw.pop('agent')
                rep = I.improve(repo, agent, TODAY, call=lambda *a: called.append(a),
                                review=lambda r, c: called.append('review'), log=lambda m: None, **kw)
                self.assertEqual(rep['status'], 'FAILED')
                self.assertIn('refused', rep['error'])
            self.assertEqual(called, [])
            branches = subprocess.run(['git', 'branch'], cwd=repo, capture_output=True, text=True).stdout
            self.assertNotIn('gold-lab', branches)


class PluginTests(unittest.TestCase):
    def load(self, files):
        with tempfile.TemporaryDirectory() as d:
            for name, text in files.items():
                (Path(d) / name).write_text(text)
            reg, errors = dict(L.REGISTRY), {}
            added = L.load_plugins(reg, d, errors)
            return added, errors

    def test_plugins_load_and_bad_ones_are_isolated(self):
        added, errors = self.load({'c_good.py': GOOD_PLUGIN, 'c_broken.py': 'def strategies(lab): return [1/0]',
                                   'c_dupe.py': GOOD_PLUGIN.replace('test_above_40', 'swing_v1'),
                                   'c_nonposthoc.py': GOOD_PLUGIN.replace('posthoc=True', 'posthoc=False'),
                                   'helper.py': 'raise SystemExit'})
        self.assertEqual(list(added), ['test_above_40'])
        self.assertEqual(added['test_above_40'].source, 'c_good.py')
        self.assertEqual(set(errors), {'c_broken.py', 'c_dupe.py', 'c_nonposthoc.py'})

    def test_shipped_plugins_load_cleanly(self):
        self.assertEqual(L.PLUGIN_ERRORS, {})
        self.assertIn('blend_swing_dip', L.REGISTRY)


def mini_repo(d):
    """A throwaway git repo with just enough of this project for the runner."""
    d = Path(d)
    for rel in ('india', 'automation'):
        shutil.copytree(ROOT / rel, d / rel, ignore=shutil.ignore_patterns('__pycache__'))
    (d / 'tests').mkdir()
    shutil.copy(ROOT / 'tests' / 'test_gold_lab.py', d / 'tests')
    (d / 'docs').mkdir()
    shutil.copy(ROOT / I.JOURNAL, d / I.JOURNAL)
    (d / 'config').mkdir()
    shutil.copy(ROOT / 'config' / 'gold_lab.example.json', d / 'config')
    (d / '.gitignore').write_text('var/\n__pycache__/\n')
    for cmd in (['init', '-q', '-b', 'main'], ['add', '-A'],
                ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'base']):
        subprocess.run(['git'] + cmd, cwd=d, check=True, capture_output=True)
    return d


class RunnerTests(unittest.TestCase):
    """End to end with a fake chat model: no network, no real AI."""

    def run_improve(self, replies, sharpe=0.9):
        calls = []

        def fake_call(base, key, model, messages):
            calls.append(messages[-1]['content'])
            return replies[min(len(calls), len(replies)) - 1]

        def fake_research(worktree, cfg_args):
            names = json.loads(I.python(worktree, '-c', I.SNAPSHOT).stdout)['strategies']
            return {'strategies': [{'name': n, 'test': {'sharpe': sharpe if n.startswith('test_') else 0.3,
                                                        'cagr_pct': 1.0, 'max_dd_pct': 5.0}} for n in names]}

        with tempfile.TemporaryDirectory() as d:
            repo = mini_repo(d)
            # The fake suite is just the lab tests: keep the run quick.
            orig = I.python
            I.python = lambda cwd, *a, **k: orig(cwd, *(('-m', 'unittest', 'tests.test_gold_lab')
                                                        if a[:3] == ('-m', 'unittest', 'discover') else a), **k)
            try:
                rep = I.improve(repo, 'api', TODAY, model='fake', provider='custom', base_url='https://llm.example/v1',
                                api_key='test-key', call=fake_call,
                                review=lambda r, c: '(fake review)', research=fake_research, log=lambda m: None)
            finally:
                I.python = orig
            journal = ''
            if rep['status'] == 'COMMITTED':
                journal = subprocess.run(['git', 'show', '%s:%s' % (rep['branch'], I.JOURNAL)], cwd=repo,
                                         capture_output=True, text=True).stdout
                files = subprocess.run(['git', 'show', '--name-only', '--format=', rep['branch']], cwd=repo,
                                       capture_output=True, text=True).stdout.split()
                rep['files'] = files
            main_clean = subprocess.run(['git', 'status', '--porcelain'], cwd=repo, capture_output=True, text=True).stdout
            return rep, calls, journal, main_clean

    def reply(self, plugin, journal='Hypothesis: test.'):
        return json.dumps({'files': [{'path': 'india/gold_lab_challengers/c_2026_12_test.py', 'content': plugin}],
                           'journal': journal})

    def test_a_good_proposal_is_tested_and_committed_on_a_branch(self):
        rep, calls, journal, main_clean = self.run_improve([self.reply(GOOD_PLUGIN)])
        self.assertEqual(rep['status'], 'COMMITTED', rep)
        self.assertEqual(rep['kept'], ['test_above_40'])
        self.assertIn('india/gold_lab_challengers/c_2026_12_test.py', rep['files'])
        self.assertIn('Hypothesis: test.', journal)
        self.assertIn('kept as a challenger', journal)
        self.assertEqual(main_clean, '')            # the user's checkout is untouched
        self.assertEqual(len(calls), 1)

    def test_a_look_ahead_bug_is_sent_back_then_fails_the_run(self):
        rep, calls, _, _ = self.run_improve([self.reply(PEEKING_PLUGIN)])
        self.assertEqual(len(calls), 3)             # default rounds: the error went back to the model twice
        self.assertIn('tests failed', calls[1])
        self.assertEqual(rep['status'], 'FAILED')   # the full suite rejects it again

    def test_the_retry_can_fix_it(self):
        rep, calls, _, _ = self.run_improve([self.reply(PEEKING_PLUGIN), self.reply(GOOD_PLUGIN)])
        self.assertEqual(rep['status'], 'COMMITTED', rep)
        self.assertEqual(len(calls), 2)

    def test_a_weak_strategy_is_dropped_but_recorded(self):
        rep, _, journal, _ = self.run_improve([self.reply(GOOD_PLUGIN)], sharpe=0.1)
        self.assertEqual(rep['status'], 'COMMITTED')
        self.assertEqual(rep['kept'], [])
        self.assertNotIn('india/gold_lab_challengers/c_2026_12_test.py', rep['files'])
        self.assertIn('dropped', journal)

    def test_paths_outside_the_plugin_folder_are_refused(self):
        evil = json.dumps({'files': [{'path': 'india/gold_lab.py', 'content': 'PROMOTION = {}'}], 'journal': 'x'})
        rep, calls, _, _ = self.run_improve([evil, evil])
        self.assertIn('refused path india/gold_lab.py', calls[1])
        self.assertEqual(rep['new'], [])


if __name__ == '__main__':
    unittest.main()
