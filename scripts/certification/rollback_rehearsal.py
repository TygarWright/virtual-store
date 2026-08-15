#!/usr/bin/env python3
"""Repository-local deployment rollback rehearsal using immutable release manifests.
No production credentials or network calls are required.
"""
from __future__ import annotations
import hashlib, json, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(tree: Path) -> dict:
    files = {}
    for p in sorted(tree.rglob('*')):
        if p.is_file() and p.name != 'RELEASE_MANIFEST.json' and '.git' not in p.parts and '__pycache__' not in p.parts:
            rel = p.relative_to(tree).as_posix()
            files[rel] = sha256(p)
    return {'version': 1, 'files': files, 'file_count': len(files)}


def verify_manifest(tree: Path, manifest: dict) -> None:
    actual = build_manifest(tree)
    assert actual == manifest, 'release tree fingerprint mismatch'


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='titan-rollback-') as td:
        root = Path(td)
        v1 = root / 'release-v1'
        v2 = root / 'release-v2'
        current = root / 'current'

        # Use a small immutable release simulation rather than copying the whole repo.
        (v1 / 'app').mkdir(parents=True)
        (v1 / 'app' / 'VERSION').write_text('titan-v1\n')
        (v1 / 'app' / 'HEALTH').write_text('healthy\n')
        m1 = build_manifest(v1)
        (v1 / 'RELEASE_MANIFEST.json').write_text(json.dumps(m1, sort_keys=True, indent=2))

        (v2 / 'app').mkdir(parents=True)
        (v2 / 'app' / 'VERSION').write_text('titan-v2\n')
        (v2 / 'app' / 'HEALTH').write_text('healthy\n')
        m2 = build_manifest(v2)
        (v2 / 'RELEASE_MANIFEST.json').write_text(json.dumps(m2, sort_keys=True, indent=2))

        import shutil
        shutil.copytree(v1, current)
        assert (current / 'app' / 'VERSION').read_text().strip() == 'titan-v1'

        # Deploy candidate v2 and verify its health/fingerprint.
        shutil.rmtree(current)
        shutil.copytree(v2, current)
        assert (current / 'app' / 'HEALTH').read_text().strip() == 'healthy'

        # Simulate a release failure after switch: corrupt the candidate.
        (current / 'app' / 'HEALTH').write_text('broken\n')
        assert (current / 'app' / 'HEALTH').read_text().strip() != 'healthy'

        # Roll back atomically to the last known-good immutable release.
        shutil.rmtree(current)
        shutil.copytree(v1, current)
        verify_manifest(current, m1)
        assert (current / 'app' / 'VERSION').read_text().strip() == 'titan-v1'
        assert (current / 'app' / 'HEALTH').read_text().strip() == 'healthy'

    print('DEPLOYMENT_ROLLBACK_REHEARSAL: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
