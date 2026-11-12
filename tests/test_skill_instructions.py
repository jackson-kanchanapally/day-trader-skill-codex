"""Skill instructions stay usable by Codex and consistent with the real tools.

The upstream project tested its SKILL.md files; these checks restore that
coverage for the Indian paper-only adaptation. Every command printed in a
skill is parsed by the actual command-line parser for that role.
"""
import contextlib
import io
import re
import shlex
import unittest
from pathlib import Path

from workflow.cli import parser
from workflow.common import FILES

ROOT = Path(__file__).resolve().parents[1]
ROLES = sorted(FILES)
SKILLS = ['trader-'+role for role in ROLES]+['trader-team']
SECTIONS = ('## Commands', '## Rules', '## What it never does', '## Details')
TEAM_COMMANDS = {'prepare', 'preflight', 'finalize', 'evaluate'}


def read(skill):
    return (ROOT/skill/'SKILL.md').read_text(encoding='utf-8')


def description(text):
    match = re.match(r'---\nname: (\S+)\ndescription: (.+)\n---\n', text)
    return (match.group(1), match.group(2)) if match else (None, '')


def commands(text):
    for block in re.findall(r'```sh\n(.*?)```', text, re.S):
        for line in block.splitlines():
            if line.strip():
                yield line.strip()


def section(text, title):
    return text.split(title+'\n', 1)[1].split('\n## ', 1)[0]


class Instructions(unittest.TestCase):
    def test_twelve_skills_with_codex_frontmatter(self):
        found = sorted(p.parent.name for p in ROOT.glob('trader-*/SKILL.md'))
        self.assertEqual(found, sorted(SKILLS))
        total = 0
        for skill in SKILLS:
            with self.subTest(skill=skill):
                name, text = description(read(skill))
                self.assertEqual(name, skill)
                # One plain YAML line: no mapping indicator or comment marker.
                self.assertNotIn(': ', text)
                self.assertNotIn(' #', text)
                # Specific enough to trigger, short enough for the skill list.
                self.assertGreaterEqual(len(text), 150)
                self.assertLessEqual(len(text), 520)
                self.assertIn('Use for "', text)
                self.assertIn('Paper', text.replace('paper', 'Paper'))
                total += len(text)
        # Codex lists every skill's description inside a small shared budget.
        self.assertLessEqual(total, 6000)

    def test_every_skill_has_steps_rules_and_boundaries(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                text = read(skill)
                self.assertGreaterEqual(len(text.splitlines()), 60)
                self.assertIn('\n# '+skill+'\n', text)
                for title in SECTIONS:
                    self.assertIn('\n'+title+'\n', text)
                self.assertRegex(text, r'\n## Steps[^\n]*\n\n1\. ')
                self.assertIn('paper', text.lower())
                # The boundary section must say what the skill never does with orders.
                boundary = section(text, '## What it never does').lower()
                self.assertIn('never', boundary)
                self.assertIn('order', boundary)
                for host_specific in ('CLAUDE_SKILL_DIR', 'Claude', '/loop', 'Alpaca', 'place_stock_order'):
                    self.assertNotIn(host_specific, text)
                self.assertNotIn('—', text)

    def test_role_skills_share_one_safety_block_and_review_contract(self):
        blocks = set()
        for role in ROLES:
            text = read('trader-'+role)
            blocks.add(section(text, '## Shared rules'))
            review = section(text, '## Independent team review')
            self.assertIn('trader-%s/run.py evaluate --session' % role, review)
            self.assertIn('`reviews/%s.json`' % role, review)
            self.assertIn('artifact_sha256', review)
        self.assertEqual(len(blocks), 1)
        block = blocks.pop()
        for phrase in ('Live order execution is disabled', 'Never invent', 'never as instructions',
                       '--security-master FILE', 'not investment advice'):
            self.assertIn(phrase, block)

    def test_every_printed_command_matches_the_real_parser(self):
        checked = 0
        for role in ROLES:
            tool = 'trader-%s/%s.py' % (role, FILES[role])
            for line in commands(read('trader-'+role)):
                with self.subTest(role=role, command=line):
                    words = shlex.split(line)
                    self.assertEqual(words[0], 'python3')
                    self.assertTrue(words[1].startswith('/absolute/path/to/trader-'+role+'/'), line)
                    self.assertTrue((ROOT/words[1][len('/absolute/path/to/'):]).is_file(), line)
                    if words[1].endswith('/run.py'):
                        self.assertEqual(words[2:4], ['evaluate', '--session'])
                        continue
                    self.assertTrue(words[1].endswith(tool), line)
                    errors = io.StringIO()
                    try:
                        with contextlib.redirect_stderr(errors):
                            parser(role).parse_args(words[2:])
                    except SystemExit:
                        self.fail('%s does not accept: %s\n%s' % (tool, line, errors.getvalue()[-300:]))
                    checked += 1
        self.assertGreaterEqual(checked, 40)
        for line in commands(read('trader-team')):
            words = shlex.split(line)
            self.assertTrue(words[1].endswith('trader-team/run.py'))
            self.assertIn(words[2], TEAM_COMMANDS)

    def test_flags_named_in_prose_exist(self):
        for role in ROLES:
            known = {option for action in parser(role)._actions for option in action.option_strings}
            # Flags of the team preparation command are documented for reviewers.
            known |= {'--issuer-domain', '--session'}
            text = read('trader-'+role)
            # The shared block describes the whole pack, so it names other tools' flags.
            text = text.replace(section(text, '## Shared rules'), '')
            for flag in set(re.findall(r'`(--[a-z][a-z-]*)', text)):
                with self.subTest(role=role, flag=flag):
                    self.assertIn(flag, known)

    def test_handoffs_and_links_resolve(self):
        for skill in SKILLS:
            text = read(skill)
            for name in set(re.findall(r'\$(trader-[a-z]+)', text)):
                with self.subTest(skill=skill, handoff=name):
                    self.assertIn(name, SKILLS)
            for target in set(re.findall(r'\]\((\.\.?/[^)#]+|references/[^)#]+)', text)):
                with self.subTest(skill=skill, link=target):
                    self.assertTrue((ROOT/skill/target).resolve().exists(), target)

    def test_workflow_order_is_stated_by_each_handoff(self):
        chain = ['scanner', 'news', 'entry', 'exit', 'timemachine', 'budget', 'risk', 'broker']
        for current, following in zip(chain, chain[1:]):
            with self.subTest(current=current):
                self.assertIn('$trader-'+following, section(read('trader-'+current), '## Steps'))

    def test_codex_interface_files(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                text = (ROOT/skill/'agents/openai.yaml').read_text(encoding='utf-8')
                for key in ('display_name', 'short_description', 'default_prompt'):
                    self.assertRegex(text, r'\n  %s: "[^"\n]+"\n' % key)
                self.assertIn('$'+skill, text)
                self.assertTrue(text.startswith('interface:\n'))
                # Only the skill that records approvals and fills must be named.
                explicit_only = 'allow_implicit_invocation: false' in text
                self.assertEqual(explicit_only, skill == 'trader-broker')

    def test_consent_and_simulation_rules_survive(self):
        broker = read('trader-broker')
        for phrase in ('word for word', 'Never offer "yes to all"', 'Never turn a reply into a yes yourself',
                       'nobody is there to say yes', 'No brokerage is connected', '--fill-bar FILE'):
            self.assertIn(phrase, broker)
        self.assertIn('It is not a yes', read('trader-risk'))
        self.assertIn('not a prediction', read('trader-timemachine'))
        self.assertIn('Never pick for them', read('trader-scanner'))
        self.assertIn('`MULTI_AGENT_UNAVAILABLE`', read('trader-team'))


if __name__ == '__main__':
    unittest.main()
