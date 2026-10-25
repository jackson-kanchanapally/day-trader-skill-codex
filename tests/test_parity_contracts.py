"""Phase-one active CLI/instruction/export contracts, distinct from legacy tests."""
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.build_release import source_files

ROOT=Path(__file__).resolve().parents[1]


class ActiveContracts(unittest.TestCase):
    def test_budget_and_broker_required_options_are_in_help_and_skill_instructions(self):
        for role,tool,flags in (('budget','size',('--limit','--prices','--portfolio')),
                                ('broker','ticket',('--fill-bar',))):
            with self.subTest(role=role):
                result=subprocess.run([sys.executable,str(ROOT/('trader-'+role)/(tool+'.py')),'--help'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                instructions=(ROOT/('trader-'+role)/'SKILL.md').read_text()
                for flag in flags:
                    self.assertIn(flag,result.stdout)
                    self.assertIn(flag,instructions)

    def test_public_profile_includes_implementation_report_audit_and_active_regressions(self):
        selected,_=source_files(ROOT)
        selected={str(p) for p in selected}
        for name in ('docs/PARITY_IMPLEMENTATION_RESULTS.md','docs/UPSTREAM_PARITY_AUDIT.md',
                     'tests/test_parity_forecast.py','tests/test_parity_sessions.py','tests/test_parity_scanner.py',
                     'tests/test_parity_budget.py','tests/test_parity_contracts.py'):
            self.assertIn(name,selected)
