#!/usr/bin/env python3
"""Generate a read-only Linux measurement adapter; never edit the shared harness."""
import argparse
import hashlib
import json
from pathlib import Path
import re

BASE_SHA256 = 'b280d1295a4b351e6f2d838c56acaac58b2247bb77fc9881587bb7bbe849a076'
CASES_SHA256 = 'b892e1b592111d8115ceddac61ca732942dd6b9b7ef900ce4a023cff8420ebfa'
PATCH_SHA256 = '62e1b8c90eb9cb89d4b8ff844bb6079ef8c7af2f3dfad7845756fd4909fdef4b'
RUNNER_SHA256 = 'd65cf16df8ba26f0b3b99efe8f0ed408d1c7bfaee1dd1a16d19fd6c4723a2f5e'
CACHE = Path('/home/ajs/.cache/pb-phase7')


def checked(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'input identity mismatch: {path}')
    if b'\r' in data or not data.endswith(b'\n'):
        raise ValueError(f'expected LF source: {path}')
    return data


def apply_exact(before, patch):
    """Apply only exact LF unified hunks with fixed headers, no subprocess/fuzz."""
    original, diff = before.decode().splitlines(True), patch.decode().splitlines(True)
    if diff[:2] != ['--- a/harness/run.py\n', '+++ b/harness/run.py\n']:
        raise ValueError('unexpected patch paths')
    output, cursor, i = [], 0, 2
    while i < len(diff):
        match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n', diff[i])
        if match is None:
            raise ValueError('expected unified hunk')
        oldstart, oldcount, newstart, newcount = (int(value) if value is not None else 1 for value in match.groups())
        if oldstart - 1 < cursor:
            raise ValueError('overlapping patch hunk')
        output.extend(original[cursor:oldstart-1])
        cursor = oldstart - 1
        if len(output) != newstart - 1:
            raise ValueError('new hunk position mismatch')
        consumed = produced = 0
        i += 1
        while i < len(diff) and not diff[i].startswith('@@ '):
            line = diff[i]
            if line[0] not in ' +-':
                raise ValueError('unsupported patch record')
            if line[0] in ' -':
                if cursor >= len(original) or original[cursor] != line[1:]:
                    raise ValueError('patch context mismatch')
                cursor += 1
                consumed += 1
            if line[0] in ' +':
                output.append(line[1:])
                produced += 1
            i += 1
        if (consumed, produced) != (oldcount, newcount):
            raise ValueError('patch hunk count mismatch')
    output.extend(original[cursor:])
    return ''.join(output).encode()


def generate(base_run, cases, patch, out):
    before = checked(base_run, BASE_SHA256)
    dependency = checked(cases, CASES_SHA256)
    patch_bytes = checked(patch, PATCH_SHA256)
    generated = apply_exact(before, patch_bytes)
    if hashlib.sha256(generated).hexdigest() != RUNNER_SHA256:
        raise ValueError('generated runner identity mismatch')
    destination = out.absolute()
    # Keep every generated runner outside the live worktree and within this lane's cache.
    if not destination.resolve().is_relative_to(CACHE.resolve()):
        raise ValueError('generated output must be inside /home/ajs/.cache/pb-phase7')
    destination.mkdir(parents=False, exist_ok=False)
    sources = {'base_run': str(base_run.resolve()), 'cases': str(cases.resolve()), 'patch': str(patch.resolve())}
    manifest = dict(schema_version=1, platform='linux', python='3.11+',
        base_sha256=BASE_SHA256, patch_sha256=PATCH_SHA256,
        runner_sha256=RUNNER_SHA256, cases_sha256=CASES_SHA256, sources=sources,
        required_runtime_options=['--suite', '--fixture', '--meter'],
        scope='generated measurement adapter only; live shared runner unchanged')
    for name, data in (('run.py', generated), ('cases.py', dependency),
                       ('generation.json', (json.dumps(manifest, indent=2)+'\n').encode())):
        path = destination / name
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(0o444)
    destination.chmod(0o555)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-run', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--patch', type=Path, default=Path(__file__).with_name('shared-runner-linux-ownership.patch'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.base_run, args.cases, args.patch, args.out), indent=2))


if __name__ == '__main__':
    main()
