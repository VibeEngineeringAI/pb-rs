#!/usr/bin/env python3
"""Build the three compatibility configurations from unchanged, hashed sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def sources():
    paths = [ROOT / 'Cargo.toml', ROOT / 'Cargo.lock']
    paths += sorted(p for p in (ROOT / 'src').rglob('*') if p.is_file())
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cargo', default='cargo')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    initial = sources()
    manifest = {'source_sha256': initial, 'binaries': {}, 'configurations': {},
                'complete': False}
    manifest_path = out / 'build-manifest.json'
    for name, token, url in (
            ('pb-normal', '', ''),
            ('pb-cloud', 'harness-orchestrator-token', ''),
            ('pb-login', 'harness-orchestrator-token', 'http://phase7-orchestrator.invalid')):
        if sources() != initial:
            raise RuntimeError('source changed before ' + name)
        config = {'PB_VERSION': '0.0.0-harness', 'PB_COMMIT': 'harness',
                  'PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN': token,
                  'PB_CLOUD_ORCHESTRATOR_URL': url}
        env = dict(os.environ, **config)
        # A caller's alternate target or flags must not silently change this build.
        env.pop('CARGO_BUILD_TARGET', None)
        env.pop('RUSTFLAGS', None)
        env.pop('CARGO_ENCODED_RUSTFLAGS', None)
        env['CARGO_TARGET_DIR'] = str(ROOT / 'target')
        with (out / (name + '.build.log')).open('w') as log:
            subprocess.run([args.cargo, 'build', '--release', '--locked'],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                           check=True)
        if sources() != initial:
            raise RuntimeError('source changed while building ' + name)
        shutil.copy2(ROOT / 'target/release/pb', out / name)
        manifest['binaries'][name] = digest(out / name)
        manifest['configurations'][name] = config
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        print(name + ': ' + manifest['binaries'][name], flush=True)
    manifest['complete'] = True
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
