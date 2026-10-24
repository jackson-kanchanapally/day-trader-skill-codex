"""Public export boundaries, synthetic provenance and reproducible packaging."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts/build_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PublicRelease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / 'source'
        self.source.mkdir()
        self.manifest = dict(schema_version=1, include=['README.md', 'module.py'],
                             tests=['tests/test_example.py'],
                             synthetic_fixtures=['tests/fixtures/synthetic.csv'])
        for name, content in {'README.md': '[Module](module.py)\n', 'module.py': 'VALUE = 1\n',
                              'tests/test_example.py': '# original synthetic test\n',
                              'tests/fixtures/synthetic.csv': 'fixture,value\ntrue,100\n'}.items():
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.save_manifest()

    def save_manifest(self):
        (self.source / release.MANIFEST).write_text(json.dumps(self.manifest))

    def test_actual_public_profile_contains_runtime_and_only_declared_fixtures(self):
        files, manifest = release.source_files(ROOT)
        release.check_links(ROOT, files)
        names = {name.as_posix() for name in files}
        self.assertIn('LICENSE', names)
        self.assertIn('india/team.py', names)
        self.assertIn('legacy/trader-scanner/scan.py', names)
        self.assertIn('dashboard/src/main.jsx', names)
        self.assertIn('dashboard/__main__.py', names)
        self.assertIn('dashboard/__init__.py', names)
        self.assertIn('tests/_provider_fixtures.py', names)
        fixtures = {name for name in names if name.startswith('tests/fixtures/')}
        self.assertEqual(fixtures, set(manifest['synthetic_fixtures']) | {'tests/fixtures/README.md'})
        self.assertNotIn('tests/test_scanner.py', names)
        self.assertNotIn('docs/TCS-20261024-DIAGNOSIS.md', names)
        self.assertFalse(any(set(Path(name).parts) & release.RESERVED for name in names))

    def test_export_checksums_and_archives_are_reproducible(self):
        first = self.directory / 'a' / 'public'
        second = self.directory / 'b' / 'public'
        release.build(self.source, first, archive=True)
        release.build(self.source, second, archive=True)
        self.assertEqual(first.with_suffix('.tar.gz').read_bytes(), second.with_suffix('.tar.gz').read_bytes())
        for line in (first / 'SHA256SUMS').read_text().splitlines():
            checksum, name = line.split('  ', 1)
            self.assertEqual(hashlib.sha256((first / name).read_bytes()).hexdigest(), checksum)
        with tarfile.open(first.with_suffix('.tar.gz')) as bundle:
            self.assertTrue(all(member.isfile() and member.name.startswith('public/') for member in bundle))
        self.assertFalse((first / '.git').exists())

    def test_existing_destination_is_never_overwritten(self):
        target = self.directory / 'existing'
        target.mkdir()
        (target / 'keep.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            release.build(self.source, target)
        self.assertEqual((target / 'keep.txt').read_text(), 'keep')

    def test_missing_input_and_broken_public_link_fail(self):
        self.manifest['include'].append('missing.py')
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'input missing'):
            release.source_files(self.source)
        self.manifest['include'].pop()
        self.save_manifest()
        (self.source / 'README.md').write_text('[Private capture](capture.json)')
        files, _ = release.source_files(self.source)
        with self.assertRaisesRegex(ValueError, 'Link outside public profile'):
            release.check_links(self.source, files)

    def test_secret_scan_reports_location_without_printing_the_value(self):
        token = 'ghp_' + 'A' * 36
        (self.source / 'module.py').write_text('TOKEN = ' + repr(token))
        with self.assertRaisesRegex(ValueError, 'GitHub token in module.py') as caught:
            release.source_files(self.source)
        self.assertNotIn(token, str(caught.exception))

    def test_traversal_private_paths_and_undeclared_fixtures_fail(self):
        for pattern, message in (('../outside.py', 'Unsafe release pattern'),
                                 ('.env', 'Environment file'),
                                 ('tests/fixtures/captured.json', 'synthetic provenance')):
            with self.subTest(pattern=pattern):
                if not pattern.startswith('..'):
                    path = self.source / pattern
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('{}')
                self.manifest['include'].append(pattern)
                self.save_manifest()
                with self.assertRaisesRegex(ValueError, message):
                    release.source_files(self.source)
                self.manifest['include'].pop()

    def test_symlink_file_and_parent_are_rejected(self):
        outside = self.directory / 'outside.py'
        outside.write_text('VALUE = 2\n')
        (self.source / 'module.py').unlink()
        (self.source / 'module.py').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink release input'):
            release.source_files(self.source)
        (self.source / 'module.py').unlink()
        (self.source / 'module.py').write_text('VALUE = 1\n')
        (self.source / 'linked').symlink_to(self.directory, target_is_directory=True)
        self.manifest['include'].append('linked/outside.py')
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'Symlink release input'):
            release.source_files(self.source)


if __name__ == '__main__':
    unittest.main()
