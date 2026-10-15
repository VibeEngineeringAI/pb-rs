#!/usr/bin/env python3
"""Run held-schema clock regression/control against an explicitly pinned binary.

Loads PROCESS_FIXTURE from tests/interactive_effects.rs (or an explicitly supplied
prepared copy). No cache-only module, Rust build/test or shared harness import.
Every invocation preserves failures, requests, raw stdout/stderr and binary hashes.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import time
import types

REPO = Path(__file__).resolve().parents[3]
TMP = '/home/ajs/.cache/pb-phase7/tmp'
PINNED_GO_SHA = 'fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314'
CASES = ('sql_schema_fallback_clock', 'sql_schema_primary_no_fallback')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_fixture(path):
    source = path.read_bytes()
    marker = 'const PROCESS_FIXTURE: &str = r####"'
    text = source.decode()
    assert text.count(marker) == 1, 'embedded fixture declaration changed'
    fixture_source = text.split(marker, 1)[1].split('"####;', 1)[0]
    module = types.ModuleType('schema_fallback_clock_owned_fixture')
    exec(compile(fixture_source, str(path) + ':PROCESS_FIXTURE', 'exec'), module.__dict__)
    return module, {'fixture_path': str(path), 'fixture_sha256': sha(fixture_source.encode()),
                    'fixture_file_sha256': sha(source)}


def run(binary, role, out, expected_sha, fixture_file, cases):
    before = sha(binary.read_bytes())
    assert before == expected_sha, 'binary does not match expected immutable hash'
    if role == 'go':
        assert before == PINNED_GO_SHA, 'Go binary is not the pinned oracle'
    fixture_before = sha(fixture_file.read_bytes())
    records = []
    for case in cases:
        effects, provenance = load_fixture(fixture_file)
        capture = out / (case + '-' + str(time.time_ns()))
        capture.mkdir(parents=True)
        os.environ['PB_EFFECTS_CAPTURE_DIR'] = str(capture)
        stdout, stderr = io.StringIO(), io.StringIO()
        record = {'kind': 'bounded actual-process SQL fallback clock regression/control',
                  'case': case, 'role': role, 'binary': str(binary), 'binary_sha256': before,
                  'source_pin': '07af390ab09bda8ac9e427ffc6310077bace0125' if role == 'go' else None,
                  'provenance': provenance, 'runner_sha256': sha(Path(__file__).read_bytes()),
                  'rust_tests_executed': False, 'crate_builds_executed': False,
                  'scope': 'held GET clock position and exact effects; no performance or styled-frame claim',
                  'passed': False}
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                effects.run_case(str(binary), case)
            record['passed'] = True
        except Exception as error:
            record['failure'] = repr(error)
        finally:
            (capture / 'fixture.stdout.log').write_text(stdout.getvalue())
            (capture / 'fixture.stderr.log').write_text(stderr.getvalue())
            record['fixture_stdout_path'] = str(capture / 'fixture.stdout.log')
            record['fixture_stderr_path'] = str(capture / 'fixture.stderr.log')
            for line in stdout.getvalue().splitlines():
                if line.startswith('SCHEMA_CLOCK='):
                    record['clock'] = json.loads(line.split('=', 1)[1])
            raw = list(capture.glob(case + '-*.json'))
            assert len(raw) == 1, ('missing/extra process captures', raw)
            raw_record = json.loads(raw[0].read_text())
            record.update(process_record_path=str(raw[0]), returncode=raw_record['returncode'],
                          requests=raw_record['requests'])
            for suffix in ('stdout', 'stderr'):
                path = raw[0].with_suffix('.' + suffix)
                record['raw_' + suffix + '_path'] = str(path)
                record[suffix + '_sha256'] = sha(path.read_bytes())
            record['binary_unchanged'] = sha(binary.read_bytes()) == before
            record['fixture_unchanged'] = sha(fixture_file.read_bytes()) == fixture_before
            assert record['binary_unchanged'] and record['fixture_unchanged'], 'input changed during process'
            path = capture / 'result.json'
            record['result_path'] = str(path)
            path.write_text(json.dumps(record, indent=2) + '\n')
            records.append(record)
    summary = {'role': role, 'binary_sha256': before, 'passed': all(r['passed'] for r in records),
               'results': [{'case': r['case'], 'passed': r['passed'], 'result_path': r['result_path'],
                            'clock': r.get('clock'), 'failure': r.get('failure')} for r in records]}
    path = out / ('schema-fallback-clock-' + role + '-' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(dict(summary, summary_path=str(path)), indent=2))
    return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--role', required=True, choices=('go', 'candidate'))
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--expect-sha256', required=True)
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--fixture-file', type=Path, default=REPO / 'tests/interactive_effects.rs',
                        help='Explicit prepared fixture source for pre-application characterization')
    args = parser.parse_args()
    for name in ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR'):
        os.environ[name] = TMP
    Path(TMP).mkdir(parents=True, exist_ok=True)
    os.environ.pop('NO_COLOR', None)
    os.environ.update(COLORTERM='truecolor', LANG='C.UTF-8', LC_ALL='C.UTF-8')
    args.out.mkdir(parents=True, exist_ok=True)
    return run(args.binary.resolve(), args.role, args.out.resolve(), args.expect_sha256,
               args.fixture_file.resolve(), (args.case,) if args.case else CASES)


if __name__ == '__main__':
    raise SystemExit(main())
