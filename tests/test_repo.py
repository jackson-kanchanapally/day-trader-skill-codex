"""Codex installation and skill-runtime regression coverage."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer',ROOT/'install_codex.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class Codex(unittest.TestCase):
    def test_modular_skills(self):
        skills=list(ROOT.glob('trader-*/SKILL.md'))
        self.assertEqual(len(skills),12)
        for p in skills:
            text=p.read_text()
            self.assertTrue(text.startswith('---\nname: '+p.parent.name+'\n'))
            self.assertIn('paper',text.lower())
            self.assertNotIn('CLAUDE_SKILL_DIR',text)
            self.assertTrue((p.parent/'run.py').is_file())
    def test_install_idempotent_and_conflict_preflight(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'skills'
            self.assertEqual(len(installer.install(target,True)),12)
            self.assertFalse(target.exists())
            target.mkdir(); (target/'trader-risk').mkdir()
            with self.assertRaises(ValueError):installer.install(target)
            self.assertEqual(len(list(target.iterdir())),1)
            (target/'trader-risk').rmdir()
            self.assertEqual(len(installer.install(target)),12)
            self.assertEqual(installer.install(target),[])
    def test_installed_runner_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as d:
            installer.install(Path(d)/'skills')
            args=[sys.executable,str(Path(d)/'skills/trader-scanner/run.py'),'analyze','DEMO.NS','--csv',str(ROOT/'tests/fixtures/india/synthetic.csv'),'--as-of','2026-10-19T09:35:00+05:30','--tick','.05']
            result=subprocess.run(args,cwd=d,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            doc=json.loads(result.stdout)
            self.assertEqual(doc['status'],'PAPER_SETUP')
            self.assertFalse(doc['execution_enabled'])
    def test_execution_command_unavailable(self):
        r=subprocess.run([sys.executable,'-m','india','execute','DEMO.NS','--tick','.05'],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0)
