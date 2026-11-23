"""AI-agnostic improvement runner for the gold strategy lab.

Any server-hosted AI can propose new challenger strategies: an agent CLI that
edits files (pi, Codex, Claude Code, Gemini CLI, Aider, or any command you name)
or a chat model behind an OpenAI-compatible cloud API (DeepSeek, OpenAI,
OpenRouter, Groq, Anthropic). Local models (Ollama, LM Studio, localhost
endpoints) are refused: a 14B model restarted the 16 GB laptop this runs on.
The runner, not the AI, enforces the rules:

1. Work happens in an isolated git worktree on branch gold-lab/improve-YYYY-MM.
   Your checkout, var/state and the running containers are not touched.
2. The AI gets a brief: the protocol, the latest review, the strategy table,
   the plugin interface and the journal.
3. Diff policy: the only allowed changes are new
   india/gold_lab_challengers/c_*.py files and an append to
   docs/GOLD_LAB_JOURNAL.md. Anything else fails the run.
4. Registry check: at most MAX_NEW new strategies in MAX_FILES files, each
   post-hoc and dated today, no plugin load errors, nothing removed.
5. Tests: the look-ahead test (every strategy) and the whole suite.
6. Research gate: a new strategy's test-year Sharpe must reach the median of
   the existing ones. A file with any failing strategy is dropped.
7. The runner appends its own verdict, with every new strategy's research
   row, to the journal and commits on the branch. It never merges or pushes.

Running AI-written code is inherent to this job: the worktree isolates git
state, not the machine. Review the branch before merging.
"""
import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_NEW = 3
MAX_FILES = 2
MAX_FILE_BYTES = 40000
PLUGIN_RE = re.compile(r'^india/gold_lab_challengers/c_[a-z0-9_]+\.py$')
JOURNAL = 'docs/GOLD_LAB_JOURNAL.md'
PROTOCOL = 'automation/gold-lab-improve.md'
BRIEF = 'var/gold_lab_brief.md'          # var/ is gitignored, so the brief never shows up in the diff

# Agent CLIs that edit files themselves, all driving server-hosted models. {prompt},
# {prompt_file}, {brief_file} and {workdir} are filled in; {model} becomes the
# model flag and value when --model is given, and disappears otherwise.
CLI_AGENTS = {
    'pi': (['pi', '-p', '--no-session', '--no-approve', '{model}', '{prompt}'], ['--model']),
    'codex': (['codex', 'exec', '{model}', '--full-auto', '{prompt}'], ['-m']),
    'claude': (['claude', '{model}', '-p', '{prompt}', '--allowedTools', 'Read', 'Edit', 'Write', 'Grep', 'Glob',
                'Bash(python3 -m india.gold_lab:*)', 'Bash(python3 -m unittest:*)'], ['--model']),
    'gemini': (['gemini', '{model}', '--yolo', '-p', '{prompt}'], ['-m']),
    'aider': (['aider', '{model}', '--yes-always', '--no-auto-commits', '--message-file', '{prompt_file}',
               '--file', JOURNAL, '--read', 'india/gold_lab.py', '--read', '{brief_file}'], ['--model']),
}
# OpenAI-compatible chat endpoints: base URL, API-key variable, default model (None = pass --model).
PROVIDERS = {
    'deepseek': ('https://api.deepseek.com', 'DEEPSEEK_API_KEY', 'deepseek-chat'),
    'openai': ('https://api.openai.com/v1', 'OPENAI_API_KEY', None),
    'openrouter': ('https://openrouter.ai/api/v1', 'OPENROUTER_API_KEY', None),
    'groq': ('https://api.groq.com/openai/v1', 'GROQ_API_KEY', None),
    'anthropic': ('https://api.anthropic.com/v1', 'ANTHROPIC_API_KEY', None),
    'custom': (None, 'GOLD_LAB_API_KEY', None),
}

API_FORMAT = '''
## Output format (chat API)

You cannot run code or edit files. Reply with ONE JSON object and nothing else:

{"files": [{"path": "india/gold_lab_challengers/c_%(stamp)s_<topic>.py", "content": "<the complete Python file>"}],
 "journal": "<markdown: your hypothesis, the economic reason, and what result would make you drop the idea>"}

Every file has exactly this shape. Do not import anything from this repository;
everything comes in through the `lab` argument:

```python
def strategies(lab):
    def my_rule(m, n):                       # m: lab.Market, n: a parameter
        out = []
        for i in range(len(m)):
            if i < n:
                out.append(None)             # warm-up
            else:                            # use m.c[:i + 1] etc. only, never i + 1 or later
                out.append(1.0 if m.c[i] > m.ema(n)[i] else 0.0)
        return out
    return [lab.Strategy('my_rule_50', 'my_family', my_rule, {'n': 50},
                         posthoc=True, added='%(today)s', note='one-line economic reason')]
```

Each file must define `strategies(lab)` exactly as above, use only the
Python standard library and the `lab` / `m` objects described in the brief, set
`posthoc=True` and `added='%(today)s'`, and use strategy names that are not in
the strategy table. At most %(max_new)d strategies in at most %(max_files)d files.
The runner tests your code. If it fails, you get the error and one more try.
'''


# --------------------------------------------------------------------------- git and subprocess helpers

def run(cmd, cwd, timeout=600, check=True, env=None):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)
    if check and p.returncode:
        raise RuntimeError('%s failed (%d): %s' % (' '.join(cmd[:4]), p.returncode, (p.stderr or p.stdout)[-2000:]))
    return p


def git(cwd, *args, check=True):
    return run(['git'] + list(args), cwd, check=check).stdout.strip()


def make_worktree(repo, month, root=None):
    """(path, branch, base sha): a fresh worktree on a new branch from HEAD."""
    if run(['git', 'cat-file', '-e', 'HEAD:india/gold_lab.py'], repo, check=False).returncode:
        raise RuntimeError('HEAD has no india/gold_lab.py: commit the gold lab before running an improvement')
    base = git(repo, 'rev-parse', 'HEAD')
    root = Path(root or Path(repo) / 'var' / 'gold_lab_worktrees')
    root.mkdir(parents=True, exist_ok=True)
    for k in range(1, 50):
        branch = 'gold-lab/improve-%s' % month + ('' if k == 1 else '-%d' % k)
        path = root / branch.split('/')[-1]
        if path.exists() or git(repo, 'branch', '--list', branch):
            continue
        git(repo, 'worktree', 'add', '-q', '-b', branch, str(path), base)
        return path, branch, base
    raise RuntimeError('too many improvement branches for %s' % month)


def python(cwd, *args, timeout=900):
    return run([sys.executable] + list(args), cwd, timeout=timeout, check=False)


# --------------------------------------------------------------------------- policy checks (pure, unit-tested)

def changed_files(worktree, base):
    """[(status, path)] for everything that differs from ``base``, including agent commits and untracked files."""
    git(worktree, 'add', '-A')
    out = git(worktree, 'diff', '--cached', '--name-status', '--no-renames', base)
    rows = []
    for line in out.splitlines():
        status, _, path = line.partition('\t')
        rows.append((status[:1], path))
    return rows


def check_diff(rows, journal_before, journal_after, read=None):
    """Violations of the diff policy (an empty list means the change is allowed)."""
    bad = []
    for status, path in rows:
        if status == 'A' and PLUGIN_RE.match(path):
            if read and len(read(path).encode()) > MAX_FILE_BYTES:
                bad.append('%s is larger than %d bytes' % (path, MAX_FILE_BYTES))
            continue
        if status == 'M' and path == JOURNAL:
            if not journal_after.startswith(journal_before):
                bad.append('%s was rewritten; only appending is allowed' % JOURNAL)
            continue
        bad.append('%s %s is outside the allowed changes (new %s files, appends to %s)' % (
            {'A': 'adds', 'M': 'modifies', 'D': 'deletes'}.get(status, status), path,
            'india/gold_lab_challengers/c_*.py', JOURNAL))
    return bad


def check_registry(before, after, today):
    """(new strategy names, violations) from two registry snapshots."""
    bad = []
    if after.get('errors'):
        bad += ['plugin %s does not load: %s' % kv for kv in sorted(after['errors'].items())]
    new = sorted(set(after['strategies']) - set(before['strategies']))
    gone = sorted(set(before['strategies']) - set(after['strategies']))
    if gone:
        bad.append('strategies removed: %s' % ', '.join(gone))
    if len(new) > MAX_NEW:
        bad.append('%d new strategies; at most %d are allowed' % (len(new), MAX_NEW))
    files = {after['strategies'][n]['source'] for n in new}
    if len(files) > MAX_FILES:
        bad.append('new strategies in %d files; at most %d are allowed' % (len(files), MAX_FILES))
    for n in new:
        s = after['strategies'][n]
        if not s['posthoc']:
            bad.append('%s is not marked posthoc' % n)
        if s['added'] != today:
            bad.append('%s has added=%r, expected %r' % (n, s['added'], today))
    return new, bad


def research_gate(rows, new):
    """(kept files, verdict per new strategy). A new strategy needs the median test Sharpe of the
    existing ones; a file is kept only if all its new strategies pass."""
    old = sorted(r['test'].get('sharpe', -9) for r in rows if r['name'] not in new)
    if not old:
        return set(), {}
    k = len(old)
    median = old[k // 2] if k % 2 else (old[k // 2 - 1] + old[k // 2]) / 2
    verdict, by_file = {}, {}
    for r in rows:
        if r['name'] in new:
            ok = r['test'].get('sharpe', -9) >= median
            verdict[r['name']] = {'sharpe': r['test'].get('sharpe'), 'median': round(median, 3), 'kept': ok,
                                  'row': r['test'], 'source': r.get('source')}
            by_file.setdefault(r.get('source'), []).append(ok)
    return {f for f, oks in by_file.items() if all(oks)}, verdict


def parse_reply(text):
    """The {"files": [...], "journal": "..."} object from a chat reply, tolerating code fences and chatter."""
    s = text.strip()
    fence = re.search(r'```(?:json)?\s*(\{.*\})\s*```', s, re.S)
    if fence:
        s = fence.group(1)
    else:
        a, b = s.find('{'), s.rfind('}')
        if a < 0 or b < a:
            raise ValueError('no JSON object in the reply')
        s = s[a:b + 1]
    doc = json.loads(s)
    files = doc.get('files')
    if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get('path'), str)
                                              and isinstance(f.get('content'), str) for f in files):
        raise ValueError('"files" must be a list of {"path", "content"} objects')
    if not isinstance(doc.get('journal', ''), str):
        raise ValueError('"journal" must be a string')
    return files, doc.get('journal', '')


def build_cli(agent, model=None, custom=None, **fill):
    """argv for an agent CLI preset or a custom command template."""
    if agent == 'cmd':
        if not custom:
            raise ValueError('--agent cmd needs --agent-cmd or GOLD_LAB_AGENT_CMD, e.g. "mytool --prompt-file {prompt_file}"')
        template, model_flag = shlex.split(custom), None
    elif agent in CLI_AGENTS:
        template, model_flag = CLI_AGENTS[agent]
    else:
        raise ValueError('unknown agent %r' % agent)
    fill = dict(fill, model_name=model or '')
    argv = []
    for part in template:
        if part == '{model}':
            argv += (model_flag + [model]) if model and model_flag else []
        else:
            argv.append(part.format(**fill))
    return argv


# --------------------------------------------------------------------------- snapshots, brief, agents

SNAPSHOT = ('import json; from india import gold_lab as L; print(json.dumps({"strategies": {n: {"family": s.family, '
            '"posthoc": s.posthoc, "added": s.added, "version": s.version, "note": s.note, "source": s.source} '
            'for n, s in L.REGISTRY.items()}, "errors": L.PLUGIN_ERRORS}))')


def snapshot(worktree):
    p = python(worktree, '-c', SNAPSHOT, timeout=300)
    if p.returncode:
        return {'strategies': {}, 'errors': {'india/gold_lab.py': (p.stderr or p.stdout)[-1500:]}}
    return json.loads(p.stdout.strip().splitlines()[-1])


def brief_text(repo, worktree, snap, today, review_md):
    lab = (Path(worktree) / 'india' / 'gold_lab.py').read_text()
    market = lab[lab.index('class Market'):lab.index('def load_market')]
    funcs = lab[lab.index('def s_buy_hold'):lab.index('class Strategy')]
    template = (Path(worktree) / 'india' / 'gold_lab_challengers' / '__init__.py').read_text()
    example = (Path(worktree) / 'india' / 'gold_lab_challengers' / 'c_2026_11_blends.py').read_text()
    journal = (Path(worktree) / JOURNAL).read_text()
    table = '\n'.join('| %s | %s | %s | %s | %s |' % (n, s['family'], 'yes' if s['posthoc'] else 'no', s['added'], s['note'])
                      for n, s in sorted(snap['strategies'].items()))
    return '\n\n'.join([
        '# Gold lab improvement brief, %s' % today,
        'Today is %s. New files: india/gold_lab_challengers/c_%s_<topic>.py, at most %d strategies in at most %d files.' % (
            today, today[:7].replace('-', '_'), MAX_NEW, MAX_FILES),
        '## Protocol\n\n' + (Path(worktree) / PROTOCOL).read_text(),
        '## Latest review (forward paper results, research, promotion status)\n\n' + (review_md or '(no review yet)'),
        '## Registered strategies\n\n| Name | Family | Post-hoc | Added | Note |\n|---|---|---|---|---|\n' + table,
        '## Plugin interface\n\n```python\n%s```\n\nReference plugin:\n\n```python\n%s```' % (template, example),
        '## Market object (india/gold_lab.py)\n\n```python\n%s```' % market,
        '## Existing strategy functions you can call as lab.<name> or blend with lab.s_blend\n\n```python\n%s```' % funcs,
        '## Journal (newest last)\n\n' + journal[-8000:],
    ]) + '\n'


def call_api(base_url, key, model, messages, timeout=900):
    body = json.dumps({'model': model, 'messages': messages, 'temperature': 0.2}).encode()
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(base_url.rstrip('/') + '/chat/completions', body, headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError('chat API HTTP %d: %s' % (exc.code, exc.read().decode(errors='replace')[:500]))
    return doc['choices'][0]['message']['content']


LOCAL_HOSTS = ('localhost', '127.', '0.0.0.0', '[::1]', '::1', '10.', '192.168.', '.local')
LOCAL_MODEL_PREFIXES = ('ollama', 'lmstudio', 'lm-studio', 'llamacpp', 'llama.cpp', 'local/')


def local_reason(base_url=None, model=None, provider=None):
    """Why a request would run an LLM on this machine (None if it is server-hosted).

    Local models are refused outright: on 2026-11-19 a 14B model restarted the
    16 GB laptop this runs on. Use a server-hosted model through an agent CLI
    (pi, codex, claude, gemini) or a cloud API."""
    from urllib.parse import urlparse
    if provider and provider.lower() in ('ollama', 'lmstudio', 'local'):
        return 'provider %s runs the model on this machine' % provider
    if base_url:
        host = (urlparse(base_url).hostname or '').lower()
        if host.startswith(LOCAL_HOSTS[:-1]) or host.endswith('.local') or host in ('localhost', '::1') \
                or re.match(r'^172\.(1[6-9]|2\d|3[01])\.', host):
            return '%s is a local address' % base_url
    if model and model.lower().startswith(LOCAL_MODEL_PREFIXES):
        return 'model %s is served locally' % model
    return None


def write_files(worktree, files):
    """Write proposed plugin files; paths outside the plugin folder are refused here as well."""
    written, refused = [], []
    for f in files:
        path = f['path'].strip().lstrip('./')
        if not PLUGIN_RE.match(path) or (Path(worktree) / path).exists() and path not in written:
            refused.append(path)
            continue
        (Path(worktree) / path).write_text(f['content'])
        written.append(path)
    return written, refused


def run_api_agent(worktree, brief, today, provider, model, base_url, key, rounds, call=call_api, log=print):
    rules = API_FORMAT % {'stamp': today[:7].replace('-', '_'), 'today': today, 'max_new': MAX_NEW, 'max_files': MAX_FILES}
    messages = [{'role': 'system', 'content': 'You are a careful quantitative researcher. Follow the protocol in the brief.\n' + rules},
                {'role': 'user', 'content': brief}]
    journal, written = '', []
    for attempt in range(1, rounds + 1):
        log('improve: asking %s (%s), round %d' % (provider, model, attempt))
        reply = call(base_url, key, model, messages)
        messages.append({'role': 'assistant', 'content': reply})
        try:
            files, journal = parse_reply(reply)
        except (ValueError, json.JSONDecodeError) as exc:
            messages.append({'role': 'user', 'content': 'That reply could not be parsed (%s). Reply with the JSON object only.' % exc})
            continue
        for path in written:                      # a retry replaces the previous round's files
            (Path(worktree) / path).unlink(missing_ok=True)
        written, refused = write_files(worktree, files)
        problems = ['refused path %s (only new india/gold_lab_challengers/c_*.py files)' % p for p in refused]
        snap = snapshot(worktree)
        problems += ['plugin %s does not load: %s' % kv for kv in snap['errors'].items()]
        if not problems:
            t = python(worktree, '-m', 'unittest', 'tests.test_gold_lab')
            if t.returncode:
                problems.append('tests failed:\n' + (t.stderr or t.stdout)[-3000:])
        if not problems:
            return journal
        messages.append({'role': 'user', 'content': 'The runner found problems:\n\n' + '\n'.join(problems) +
                         '\n\nFix them and reply with the complete JSON object again. Remember: each file only defines '
                         '`def strategies(lab):` returning a list of `lab.Strategy(...)`, imports nothing from this '
                         'repository, and every exposure i uses sessions up to i only.'})
    return journal


# --------------------------------------------------------------------------- the run

def default_review(repo, cfg_args):
    rev = python(repo, '-m', 'india.gold_lab', *cfg_args, '--review', '--dry-run')
    return rev.stdout if rev.returncode == 0 else '(review failed: %s)' % (rev.stderr or rev.stdout)[-500:]


def default_research(worktree, cfg_args):
    r = python(worktree, '-m', 'india.gold_lab', *cfg_args, '--research', '--json', timeout=1800)
    if r.returncode:
        raise RuntimeError('research failed: ' + (r.stderr or r.stdout)[-800:])
    return json.loads(r.stdout)


def improve(repo, agent, today, model=None, provider=None, base_url=None, api_key=None, agent_cmd=None,
            config=None, lab_config=None, rounds=3, timeout=3600, keep=False, log=print, call=call_api,
            review=None, research=None):
    """One improvement run. ``review`` and ``research`` replace the network steps in tests."""
    repo = Path(repo).resolve()
    month = today[:7]
    config = str(Path(config).resolve()) if config else None
    lab_config = str(Path(lab_config or repo / 'config' / 'gold_lab.json').resolve())
    cfg_args = (['--config', config] if config else []) + ['--lab-config', lab_config]
    report = {'date': today, 'agent': agent, 'model': model, 'provider': provider, 'status': 'FAILED', 'violations': []}
    if agent == 'api':
        base_default = PROVIDERS.get(provider or 'custom', (None,))[0]
        problem = local_reason(base_url or base_default, model, provider)
    else:
        problem = local_reason(None, model, provider) or local_reason(None, None, os.environ.get('GOLD_LAB_PROVIDER'))
    if agent == 'api' and not problem and (provider or 'custom') not in PROVIDERS:
        report['error'] = 'unknown provider %r; use one of %s' % (provider, ', '.join(sorted(PROVIDERS)))
        return report
    if problem:
        report['error'] = 'refused: %s. Only server-hosted LLMs are allowed (pi, codex, claude, gemini or a cloud API).' % problem
        return report

    # A fresh review (not promoting) in your checkout, where the ledger lives.
    review = review or default_review
    research = research or default_research
    review_md = review(repo, cfg_args)

    worktree, branch, base = make_worktree(repo, month)
    report.update(branch=branch, worktree=str(worktree), base=base)
    log('improve: worktree %s on %s' % (worktree, branch))
    try:
        before = snapshot(worktree)
        journal_before = (worktree / JOURNAL).read_text()
        brief = brief_text(repo, worktree, before, today, review_md)
        (worktree / 'var').mkdir(exist_ok=True)
        (worktree / BRIEF).write_text(brief)
        agent_notes = ''
        if agent == 'api':
            base_default, key_var, model_default = PROVIDERS[provider or 'custom']
            base_url = base_url or base_default
            model = model or model_default
            if not base_url or not model:
                raise ValueError('--agent api needs --base-url and --model for provider %s' % (provider or 'custom'))
            key = api_key or (os.environ.get(key_var) if key_var else None) or os.environ.get('GOLD_LAB_API_KEY')
            if key_var and not key:
                raise ValueError('set %s (or GOLD_LAB_API_KEY) for provider %s' % (key_var, provider))
            report['model'] = model
            agent_notes = run_api_agent(worktree, brief, today, provider or 'custom', model, base_url, key, rounds, call, log)
        else:
            prompt = ('Follow the protocol in %s. Your brief, with today\'s review, the strategy table and the plugin '
                      'interface, is in %s. Do not commit; the runner checks and commits your work.' % (PROTOCOL, BRIEF))
            (worktree / 'var' / 'gold_lab_prompt.md').write_text(prompt + '\n\n' + brief)
            argv = build_cli(agent, model, agent_cmd, prompt=prompt, prompt_file=str(worktree / 'var' / 'gold_lab_prompt.md'),
                             brief_file=str(worktree / BRIEF), workdir=str(worktree))
            if not shutil.which(argv[0]):
                raise ValueError('%s not found on PATH' % argv[0])
            log('improve: running %s' % ' '.join(shlex.quote(a) for a in argv[:3]))
            p = run(argv, worktree, timeout=timeout, check=False)
            (worktree / 'var' / 'gold_lab_agent.log').write_text(p.stdout + '\n' + p.stderr)
            report['agent_exit'] = p.returncode

        # Everything below is enforced by the runner, whatever the agent did.
        if agent_notes:
            with open(worktree / JOURNAL, 'a') as fh:
                fh.write('\n## %s: proposal from %s (%s)\n\n%s\n' % (today, agent, report['model'] or provider, agent_notes.strip()))
        rows = changed_files(worktree, base)
        report['violations'] = check_diff(rows, journal_before, (worktree / JOURNAL).read_text(),
                                          read=lambda p: (worktree / p).read_text())
        after = snapshot(worktree)
        new, bad = check_registry(before, after, today)
        report['violations'] += bad
        report['new'] = new
        if report['violations']:
            raise RuntimeError('policy violations')
        if new:
            t = python(worktree, '-m', 'unittest', 'discover', '-s', 'tests', timeout=1800)
            if t.returncode:
                report['violations'].append('test suite failed: ' + (t.stderr or t.stdout)[-1500:])
                raise RuntimeError('tests failed')
            rows_by = research(worktree, cfg_args)['strategies']
            for row in rows_by:
                row['source'] = after['strategies'].get(row['name'], {}).get('source')
            kept_files, verdict = research_gate(rows_by, set(new))
            report['verdict'] = verdict
            for src in {v['source'] for v in verdict.values()} - kept_files:
                (worktree / 'india' / 'gold_lab_challengers' / src).unlink()
            final = snapshot(worktree)
            if final['errors']:                    # e.g. a kept blend that used a dropped strategy
                for src in kept_files:
                    (worktree / 'india' / 'gold_lab_challengers' / src).unlink()
                kept_files = set()
            report['kept'] = sorted(n for n, v in verdict.items() if v['source'] in kept_files)
        else:
            report['verdict'], report['kept'] = {}, []
        with open(worktree / JOURNAL, 'a') as fh:
            fh.write(verdict_text(report, today))
        git(worktree, 'add', '-A')
        git(worktree, '-c', 'user.name=gold-lab-runner', '-c', 'user.email=gold-lab-runner@localhost',
            'commit', '-q', '-m', 'gold-lab: monthly improvement %s (%s)\n\nKept: %s\nRejected: %s' % (
                month, agent if agent != 'api' else '%s/%s' % (provider, report['model']),
                ', '.join(report['kept']) or 'none',
                ', '.join(n for n in report['new'] if n not in report['kept']) or 'none'))
        report['status'] = 'COMMITTED'
        report['commit'] = git(worktree, 'rev-parse', 'HEAD')
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as exc:
        report['error'] = str(exc)
    finally:
        if report['status'] == 'COMMITTED' and not keep:
            git(repo, 'worktree', 'remove', '--force', str(worktree), check=False)
            report['worktree'] = None
        out = repo / 'var' / 'state' / 'gold_lab' / 'improve'
        out.mkdir(parents=True, exist_ok=True)
        (out / ('%s-%s.json' % (today, branch.split('/')[-1] if 'branch' in report else 'run'))).write_text(
            json.dumps(report, indent=2, default=str))
    return report


def verdict_text(report, today):
    lines = ['', '### %s: runner verdict (%s)' % (today, report['agent'] if report['agent'] != 'api' else
                                                  '%s/%s' % (report['provider'], report['model'])), '']
    if not report['new']:
        lines.append('No new strategies were proposed.')
    for name, v in sorted(report['verdict'].items()):
        t = v['row']
        lines.append('- `%s` (%s): test-year Sharpe %.2f vs median %.2f, CAGR %.1f%%, max DD %.1f%% → %s' % (
            name, v['source'], t.get('sharpe', 0), v['median'], t.get('cagr_pct', 0), t.get('max_dd_pct', 0),
            'kept as a challenger' if name in report['kept'] else 'dropped'))
    return '\n'.join(lines) + '\n'


def summary(report):
    if report['status'] != 'COMMITTED':
        out = ['Gold lab improvement FAILED: %s' % report.get('error')]
        out += ['  - ' + v for v in report['violations']]
        if report.get('worktree'):
            out.append('Nothing was committed. The worktree is kept for inspection: %s' % report['worktree'])
        return '\n'.join(out)
    return '\n'.join([
        'Gold lab improvement committed on %s (%s).' % (report['branch'], report['commit'][:10]),
        'Kept: %s · Dropped: %s' % (', '.join(report['kept']) or 'none',
                                    ', '.join(n for n in report['new'] if n not in report['kept']) or 'none'),
        'Review:  git log -p main..%s' % report['branch'],
        'Adopt:   git merge %s && docker compose up -d --build' % report['branch'],
        'New challengers start their forward paper record on the first run after the merge.'])


def main(argv=None):
    ap = argparse.ArgumentParser(description='Let any AI propose gold lab challengers; the runner enforces the rules.')
    ap.add_argument('--agent', required=True, choices=sorted(CLI_AGENTS) + ['api', 'cmd'],
                    help='an agent CLI preset, "api" for any OpenAI-compatible chat model, or "cmd" for your own command')
    ap.add_argument('--model', help='model name passed to the agent or API')
    ap.add_argument('--provider', help='with --agent api: endpoint preset, one of %s (default custom)' % ', '.join(sorted(PROVIDERS)))
    ap.add_argument('--base-url', help='with --agent api: OpenAI-compatible base URL (overrides the preset)')
    ap.add_argument('--agent-cmd', default=os.environ.get('GOLD_LAB_AGENT_CMD'),
                    help='with --agent cmd: command template with {prompt}, {prompt_file}, {brief_file}, {workdir}, {model_name}')
    ap.add_argument('--config', help='picks JSON with the "gold" object (default: built-in gold defaults)')
    ap.add_argument('--lab-config', help='lab settings (default config/gold_lab.json if present)')
    ap.add_argument('--rounds', type=int, default=3, help='with --agent api: attempts when the runner finds errors')
    ap.add_argument('--timeout', type=int, default=3600, help='seconds an agent CLI may run')
    ap.add_argument('--keep-worktree', action='store_true')
    ap.add_argument('--notify', action='store_true', help='send the summary to the Telegram alert chat')
    ap.add_argument('--env-file', default='var/telegram.env')
    a = ap.parse_args(argv)
    repo = git(Path.cwd(), 'rev-parse', '--show-toplevel')
    today = dt.date.today().isoformat()
    report = improve(repo, a.agent, today, a.model, a.provider, a.base_url, None, a.agent_cmd, a.config, a.lab_config,
                     a.rounds, a.timeout, a.keep_worktree, log=lambda m: print(m, file=sys.stderr))
    text = summary(report)
    print(text)
    if a.notify:
        from . import picks as P
        env = dict(P.read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith('TELEGRAM_')})
        if env.get('TELEGRAM_BOT_TOKEN') and env.get('TELEGRAM_CHAT_ID'):
            import html
            P.send_telegram(env['TELEGRAM_BOT_TOKEN'], env['TELEGRAM_CHAT_ID'], '🧪 ' + html.escape(text))
    return 0 if report['status'] == 'COMMITTED' else 1


if __name__ == '__main__':
    sys.exit(main())
