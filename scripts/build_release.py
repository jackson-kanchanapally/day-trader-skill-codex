"""Build an allowlisted public source tree, without Git history or runtime data."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'release-manifest.json'
RESERVED = {'.git', '.agents', '.codex', '.aws', 'node_modules', '__pycache__',
            'trader', 'var', '.venv', 'venv', 'dist'}
PATTERNS = {
    'private key': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'AWS access key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'GitHub token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b',
    'OpenAI token': rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b',
    'credential URL': rb'https?://[^\s/<>:@]+:[^\s/<>@]+@',
    'personal home path': rb'/(?:Users|home)/[A-Za-z0-9_.-]+/',
}


def source_files(root):
    """Fail closed for missing inputs, traversal, symlinks and common secrets."""
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported release manifest schema')
    selected = set()
    for key in ('include', 'tests', 'synthetic_fixtures'):
        entries = manifest[key]
        if not isinstance(entries, list) or not entries:
            raise ValueError('Release manifest requires a nonempty ' + key + ' list')
        for pattern in entries:
            path = PurePosixPath(pattern)
            if path.is_absolute() or '..' in path.parts or '\\' in pattern:
                raise ValueError('Unsafe release pattern: ' + pattern)
            matches = list(root.glob(pattern))
            if not matches:
                raise ValueError('Release input missing: ' + pattern)
            for source in matches:
                relative = source.relative_to(root)
                if any(part in RESERVED for part in relative.parts):
                    raise ValueError('Private/generated release input: ' + relative.as_posix())
                if relative.name.startswith('.env') and relative.name != '.env.example':
                    raise ValueError('Environment file in release: ' + relative.as_posix())
                if any(parent.is_symlink() for parent in (source, *source.parents) if parent != root.parent):
                    raise ValueError('Symlink release input: ' + relative.as_posix())
                if not source.is_file():
                    raise ValueError('Release input is not a file: ' + relative.as_posix())
                # Fixture content must be explicitly listed, never picked up by
                # an expanded source glob during a future manifest edit.
                if relative.parts[:2] == ('tests', 'fixtures'):
                    allowed = set(manifest['synthetic_fixtures']) | {'tests/fixtures/README.md'}
                    if relative.as_posix() not in allowed:
                        raise ValueError('Fixture lacks synthetic provenance: ' + relative.as_posix())
                payload = source.read_bytes()
                for label, regex in PATTERNS.items():
                    if re.search(regex, payload):
                        raise ValueError('Possible ' + label + ' in ' + relative.as_posix())
                selected.add(relative)
    return sorted(selected), manifest


def check_links(root, files):
    """Check relative Markdown file targets against the actual public profile."""
    root = Path(root)
    allowed = {str((root / name).resolve()) for name in files}
    for name in files:
        if name.suffix != '.md':
            continue
        for match in re.finditer(r'!?\[[^\]]*\]\(([^)]+)\)', (root / name).read_text()):
            target = match.group(1).split(' "', 1)[0].strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (root / name.parent / unquote(parsed.path)).resolve()
            if str(resolved) not in allowed:
                raise ValueError('Link outside public profile in ' + str(name) + ': ' + target)


def archive_tree(directory, archive):
    """Write sorted files with fixed metadata so identical inputs reproduce."""
    with Path(archive).open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as bundle:
                for path in sorted(Path(directory).rglob('*')):
                    if not path.is_file():
                        continue
                    payload = path.read_bytes()
                    info = tarfile.TarInfo(Path(directory).name + '/' + path.relative_to(directory).as_posix())
                    info.size, info.mode, info.mtime = len(payload), 0o644, 0
                    bundle.addfile(info, io.BytesIO(payload))


def build(root, destination, archive=False):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    # Never overwrite an existing tree, follow an output symlink, or create a
    # release within one of the source directories it is copying.
    if destination.exists() or destination.is_symlink():
        raise ValueError('Release destination already exists: ' + str(destination))
    resolved = destination.resolve()
    if resolved == root or resolved in root.parents:
        raise ValueError('Release destination must not contain the source checkout')
    if root in resolved.parents and resolved.relative_to(root).parts[0] != 'dist':
        raise ValueError('Use dist/ for release output within the source checkout')
    archive_path = destination.with_name(destination.name + '.tar.gz')
    if archive and (archive_path.exists() or archive_path.is_symlink()):
        raise ValueError('Release archive already exists: ' + str(archive_path))
    files, manifest = source_files(root)
    check_links(root, files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.public-release-', dir=destination.parent))
    try:
        for name in files:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / name).read_bytes())
        metadata = dict(schema_version=1, profile='synthetic-public-source',
                        source_files=len(files), test_modules=manifest['tests'],
                        synthetic_fixtures=manifest['synthetic_fixtures'],
                        exclusions=['Git history', 'captured provider/disclosure fixtures',
                                    'capture-dependent upstream tests', 'private session diagnostics',
                                    'runtime reports and credentials', 'dependencies and generated builds'])
        (staging / 'RELEASE.json').write_text(json.dumps(metadata, indent=2) + '\n')
        checksums = []
        for name in sorted([*files, Path('RELEASE.json')]):
            checksums.append(hashlib.sha256((staging / name).read_bytes()).hexdigest() + '  ' + name.as_posix())
        (staging / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if archive:
        archive_tree(destination, archive_path)
    return dict(directory=str(destination), archive=str(archive_path) if archive else None,
                source_files=len(files), test_modules=len(manifest['tests']),
                synthetic_fixtures=len(manifest['synthetic_fixtures']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'dist/day-trader-skill-public')
    parser.add_argument('--archive', action='store_true', help='Also write a reproducible .tar.gz')
    parser.add_argument('--check', action='store_true', help='Audit source selection and links without writing')
    args = parser.parse_args(argv)
    try:
        if args.check:
            files, manifest = source_files(ROOT)
            check_links(ROOT, files)
            result = dict(status='PASS', source_files=len(files), test_modules=len(manifest['tests']))
        else:
            result = build(ROOT, args.out, args.archive)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
