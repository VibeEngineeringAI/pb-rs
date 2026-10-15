#!/usr/bin/env python3
"""Generate/check the saved-only pinned Unicode table; no Rust build/tests.

Default checks byte reproducibility. --write generates the owned helper.
Uses offline Go 1.25.4, immutable source guards, and bounded exhaustive scalar
comparison against actual Go unicode calls. All scratch files use owned TMP.
"""
import argparse
import bisect
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[3]
CACHE = Path('/home/ajs/.cache/pb-phase7/opus-r3-low')
TMP = Path('/home/ajs/.cache/pb-phase7/tmp')
GOROOT = Path('/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64')
HELPER = REPO / 'src/tui/saved_fuzzy_unicode.rs'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    before = json.loads((REPO / 'docs/evidence/phase7/opus-r3-low-before.json').read_text())
    guarded = ['go.mod', 'go.sum', str(GOROOT / 'src/unicode/tables.go'),
               str(GOROOT / 'src/unicode/letter.go'),
               '/home/ajs/go/pkg/mod/github.com/sahilm/fuzzy@v0.1.1/fuzzy.go',
               '/home/ajs/go/pkg/mod/github.com/charmbracelet/bubbles@v0.20.0/list/list.go']
    hashes = {}
    for name in guarded:
        value = sha((REPO / name).read_bytes())
        assert value == before['before_sha256'][name], ('oracle source changed', name)
        hashes[name] = value
    CACHE.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off', GOCACHE=str(CACHE / 'go-cache'),
               TMPDIR=str(TMP), GOTMPDIR=str(TMP), GOROOT=str(GOROOT))
    source = Path(__file__).with_name('opus-r3-low-unicode-oracle.go.txt').read_bytes()
    with tempfile.TemporaryDirectory(prefix='opus-r3-low-go-', dir=TMP) as directory:
        main_go = Path(directory) / 'main.go'
        main_go.write_bytes(source)
        command = [str(GOROOT / 'bin/go'), 'run', '-mod=readonly', str(main_go)]
        generated = subprocess.check_output(command + ['--tables'], cwd=REPO, env=env)
        oracle_bytes = subprocess.check_output(command, cwd=REPO, env=env)
    # rustfmt is formatting only; no compilation or Rust test execution.
    rustfmt = os.environ.get('RUSTFMT', '/home/ajs/.rustup/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin/rustfmt')
    generated = subprocess.check_output([rustfmt, '--edition', '2024', '--emit', 'stdout'], input=generated, env=env)
    oracle = json.loads(oracle_bytes)
    parsed = {}
    for name in ('FOLD', 'LOWER', 'UPPER'):
        body = generated.decode().split('const ' + name + ':', 1)[1].split('&[', 2)[2].split('];', 1)[0]
        parsed[name] = [tuple(int(value.strip(), 0) for value in row.split(','))
                        for row in re.findall(r'\(([^()]+)\)', body)]
    folds = dict(parsed['FOLD'])
    keys = [source for source, _ in parsed['FOLD']]
    assert keys == sorted(set(keys))
    def fold(rune):
        return rune - 32 if 97 <= rune <= 122 else folds.get(rune, rune)
    def category(rune, name):
        ranges = parsed[name]
        index = bisect.bisect_right(starts[name], rune) - 1
        if index < 0:
            return False
        lo, hi, stride = ranges[index]
        return rune <= hi and (rune - lo) % stride == 0
    starts = {name: [lo for lo, _, _ in parsed[name]] for name in ('LOWER', 'UPPER')}
    for name in starts:
        assert all(a[1] < b[0] for a, b in zip(parsed[name], parsed[name][1:]))
    truth_folds = dict(oracle['noncanonical_folds'])
    truth_lower = set(oracle['lower_scalars'])
    truth_upper = set(oracle['upper_scalars'])
    count = 0
    for rune in range(0x110000):
        if 0xD800 <= rune <= 0xDFFF:
            continue
        assert fold(rune) == truth_folds.get(rune, rune), ('fold', rune)
        assert fold(fold(rune)) == fold(rune), ('idempotence', rune)
        assert category(rune, 'LOWER') == (rune in truth_lower), ('Ll', rune)
        assert category(rune, 'UPPER') == (rune in truth_upper), ('Lu', rune)
        count += 1
    if args.write:
        HELPER.write_bytes(generated)
    else:
        assert HELPER.read_bytes() == generated, 'helper differs from pinned regeneration'
    # Retain the oracle controls separately from the complete scalar truth.
    (CACHE / 'unicode-oracle-full.json').write_bytes(oracle_bytes)
    controls = {key: value for key, value in oracle.items()
                if key not in ('noncanonical_folds', 'lower_scalars', 'upper_scalars')}
    (REPO / 'docs/evidence/phase7/opus-r3-low-unicode-controls.json').write_text(json.dumps(controls, indent=2) + '\n')
    record = dict(go_version=oracle['go_version'], unicode_version=oracle['unicode_version'],
                  generator_sha256=sha(Path(__file__).read_bytes()), oracle_source_sha256=sha(source),
                  helper_sha256=sha(generated), source_hashes=hashes,
                  scalar_count=count, fold_entries=len(folds), lower_ranges=len(parsed['LOWER']),
                  upper_ranges=len(parsed['UPPER']), exhaustive_go_comparison_passed=True,
                  idempotence_passed=True, regeneration_equal=True, rust_executed=False,
                  full_oracle_path=str(CACHE / 'unicode-oracle-full.json'), full_oracle_sha256=sha(oracle_bytes))
    (REPO / 'docs/evidence/phase7/opus-r3-low-unicode-generation.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
