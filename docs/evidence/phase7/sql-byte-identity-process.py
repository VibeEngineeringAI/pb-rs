#!/usr/bin/env python3
"""Bounded SQL dataset identity regression; standard-library HTTP/PTY fixture.

Loads PROCESS_FIXTURE from tests/interactive_effects.rs without building or
running Rust tests. Run once per explicitly supplied immutable binary. Captures
failed runs as well as successful ones; no styled-frame or timing claims.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import types

REPO = Path(__file__).resolve().parents[3]
TMP = '/home/ajs/.cache/pb-phase7/tmp'
PINNED_GO_SHA = 'fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314'
REPLACEMENT = '\ufffd\ufffd试'
RAW_PATH = '/api/v1/logstream/%B5%8B%E8%AF%95/schema'
LOGS_PATH = '/api/v1/logstream/logs/schema'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_fixture():
    source = (REPO / 'tests/interactive_effects.rs').read_text()
    marker = 'const PROCESS_FIXTURE: &str = r####"'
    assert source.count(marker) == 1, 'embedded fixture declaration changed'
    fixture_source = source.split(marker, 1)[1].split('"####;', 1)[0]
    module = types.ModuleType('sql_identity_owned_fixture')
    exec(compile(fixture_source, 'interactive_effects.rs:PROCESS_FIXTURE', 'exec'), module.__dict__)
    module.TIMEOUT = 6
    return module, {'fixture_path': 'tests/interactive_effects.rs:PROCESS_FIXTURE',
                    'fixture_sha256': sha(fixture_source.encode()),
                    'fixture_file_sha256': sha(source.encode())}


def run(binary, role, out, expected_sha):
    effects, provenance = load_fixture()
    before = sha(binary.read_bytes())
    if expected_sha is not None:
        assert before == expected_sha, 'binary does not match expected immutable hash'
    prefix = out / ('sql-byte-identity-' + role + '-' + str(time.time_ns()))
    fixture = effects.Fixture()
    session = None
    gate = threading.Event()
    fixture.releases.append(gate)
    fixture.routes[('GET', '/api/prism/v1/home')] = (200, {'datasets': [
        {'title': name, 'datasetType': 'logs'} for name in ('logs', REPLACEMENT)]}, gate)
    fixture.routes[('GET', RAW_PATH)] = (200, {'fields': []})
    fixture.routes[('POST', '/api/v1/query')] = (200, {'fields': [], 'records': []})
    fixture.routes[('GET', LOGS_PATH)] = (
        200, {'fields': [{'name': 'LOGS_SELECTED_SCHEMA'}]})
    record = {'kind': 'bounded actual-process SQL byte-identity regression',
              'case': 'late_dataset_list_after_empty_raw_schema', 'role': role,
              'binary': str(binary), 'binary_sha256': before,
              'source_pin': '07af390' if role == 'go' and before == PINNED_GO_SHA else None,
              'dimensions': [120, 35], 'raw_dataset_hex': 'b58be8af95',
              'replacement_title': REPLACEMENT, 'provenance': provenance,
              'candidate_executed': role == 'candidate', 'rust_tests_executed': False,
              'crate_builds_executed': False,
              'scope': 'request identity and causal completion; no full styled-frame parity or timings',
              'passed': False}
    try:
        session = effects.Session(str(binary), ['sql', 'run', '-i', '--from',
            '2024-01-01T00:00:00Z', '--to', '2024-01-01T01:00:00Z'], fixture)
        fixture.wait('/api/prism/v1/home')
        session.until('QUERY SQL')
        query = "select 'Ⱥ' from 测试"
        session.send(b'\x1b[200~' + query.encode() + b'\x1b[201~')
        session.until(query)
        session.send(b'\x0c')
        mark = len(session.output)
        session.send(b'\x0c')
        fixture.wait(RAW_PATH)
        fallback = fixture.wait('/api/v1/query')[0]
        assert json.loads(fallback['body'])['query'] == "SELECT * FROM '" + REPLACEMENT + "' LIMIT 1"
        # A post-request frame establishes idle schema state before releasing
        # the dataset list. No guessed completion sleep is used.
        session.until('no columns', after=mark)
        record['raw_schema_idle_frame_offset'] = len(session.output)
        gate.set()
        request = fixture.wait(LOGS_PATH)[0]
        session.until('LOGS_SELECTED_SCHEMA')
        record['logs_schema_frame_offset'] = len(session.output)
        session.send(b'\x03')
        session.finish()
        session.unchanged()
        assert not session.error, session.error
        effects.assert_requests(fixture, [('GET', '/api/prism/v1/home'),
            ('GET', RAW_PATH), ('POST', '/api/v1/query'), ('GET', LOGS_PATH)])
        assert request['body'] == '' and request['params'] == {}, request
        assert sha(binary.read_bytes()) == before, 'binary changed during process'
        record.update(passed=True, terminal_restored=True, config_unchanged=True,
                      byte_identity_selected='logs')
    except Exception as error:
        record['failure'] = repr(error)
    finally:
        if session is not None:
            record.update(returncode=session.child.poll(),
                          raw_stdout_path=str(prefix.with_suffix('.stdout')),
                          raw_stderr_path=str(prefix.with_suffix('.stderr')),
                          stdout_sha256=sha(session.output), stderr_sha256=sha(session.error))
            prefix.with_suffix('.stdout').write_bytes(session.output)
            prefix.with_suffix('.stderr').write_bytes(session.error)
            session.close()
            record['returncode_after_cleanup'] = session.child.returncode
        fixture.close()
        record['requests'] = fixture.requests
        record['binary_unchanged'] = sha(binary.read_bytes()) == before
        prefix.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
    return record, prefix.with_suffix('.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--role', required=True, choices=('go', 'candidate'))
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--expect-sha256')
    args = parser.parse_args()
    for name in ('TMPDIR', 'GOTMPDIR', 'CLAUDE_CODE_TMPDIR'):
        os.environ[name] = TMP
    Path(TMP).mkdir(parents=True, exist_ok=True)
    os.environ.pop('NO_COLOR', None)
    os.environ.update(COLORTERM='truecolor', LANG='C.UTF-8', LC_ALL='C.UTF-8')
    args.out.mkdir(parents=True, exist_ok=True)
    record, path = run(args.binary.resolve(), args.role, args.out.resolve(), args.expect_sha256)
    print(json.dumps({'passed': record['passed'], 'role': args.role, 'record': str(path)}, indent=2))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
