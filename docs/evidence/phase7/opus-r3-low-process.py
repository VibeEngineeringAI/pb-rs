#!/usr/bin/env python3
"""Bounded O3-1/O3-2 process controls, one explicitly hashed binary per run.

Loads the stable embedded PROCESS_FIXTURE without modifying tests or building
Rust. Uses pyte from harness-venv only to capture causal terminal frames; this
does not claim full styled parity. Mismatches retain actual effects and frames.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import types

import pyte

REPO = Path(__file__).resolve().parents[3]
TMP = Path('/home/ajs/.cache/pb-phase7/tmp')
GO_SHA = 'fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314'
RELEASE3_SHA = 'aceb58bd412e8fbf34f603d247c68e87e69897356cefd5aad19ab6a92484db28'
CREDENTIALS = ('credential_valid_submit', 'credential_valid_unfocused',
               'credential_invalid_latch', 'credential_current_invalid',
               'credential_plain_enter_current_invalid', 'credential_partial_cancel',
               'credential_valid_cancel')
# Expected index is based on actual Go list.DefaultFilter controls, not on the
# proposed native implementation. Different SQL per title exposes application.
SAVED = {
    'saved_fold_long_s_dot': ('ṡ', ['other', 'ẛ'], 1),
    'saved_fold_cyrillic': ('в', ['other', 'ᲀ'], 1),
    'saved_lower_category': ('A', ['ªA', 'QA'], 1),
    'saved_upper_category_circled': ('Ⓐ', ['xⒶ', 'Ⓐ'], 1),
    'saved_upper_category_roman': ('Ⅰ', ['xⅠ', 'Ⅰ'], 1),
    'saved_ascii_rank': ('lg', ['Log Server', 'log'], 1),
    'saved_ascii_ties': ('L', ['Logs', 'Logs'], 0),
    'saved_sigma_orbit': ('Σ', ['other', 'ς'], 1),
    'saved_long_s_orbit': ('S', ['other', 'ſ'], 1),
    'saved_dotted_i_distinct': ('i', ['İ', 'i'], 1),
    'saved_filter_apply_a': ('L', ['Logs', 'Metrics'], 0),
    'saved_filter_quit_b': ('L', ['Logs', 'Metrics'], None),
    'saved_filter_clear_esc': ('L', ['Metrics', 'Logs'], 0),
    'saved_filter_accept_down': ('L', ['Metrics', 'Logs'], 1),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_fixture():
    path = REPO / 'tests/interactive_effects.rs'
    source = path.read_bytes()
    marker = 'const PROCESS_FIXTURE: &str = r####"'
    text = source.decode()
    assert text.count(marker) == 1, 'embedded fixture declaration changed'
    fixture_source = text.split(marker, 1)[1].split('"####;', 1)[0]
    before = json.loads((REPO / 'docs/evidence/phase7/opus-r3-low-before.json').read_text())
    assert sha(fixture_source.encode()) == before['embedded_fixture_sha256'], 'coordinate changed PROCESS_FIXTURE before replay'
    module = types.ModuleType('opus_r3_low_process_fixture')
    exec(compile(fixture_source, 'interactive_effects.rs:PROCESS_FIXTURE', 'exec'), module.__dict__)
    module.TIMEOUT = 4
    module.ROOT = str(TMP)
    return module, dict(fixture_path='tests/interactive_effects.rs:PROCESS_FIXTURE',
                        embedded_fixture_sha256=sha(fixture_source.encode()),
                        fixture_file_sha256=sha(source))


def frame(session):
    screen = pyte.Screen(120, 35)
    pyte.Stream(screen).feed(bytes(session.output).decode(errors='replace'))
    rows = [row.rstrip() for row in screen.display]
    result = {'output_offset': len(session.output), 'rows': rows,
              'cursor': [screen.cursor.x, screen.cursor.y]}
    # Color and coordinates establish the focused widget, rather than inferring
    # focus merely from a fixed delay or from credentials having been entered.
    for label, needle in [('user', 'user:'), ('pass', 'pass:'), ('button', '[ ' )]:
        for y, row in enumerate(rows):
            x = row.find(needle)
            if x >= 0:
                colored_x = x + (2 if label == 'button' else 0)
                cell = screen.buffer[y][colored_x]
                result[label] = {'row': row, 'position': [x, y], 'fg': cell.fg}
                break
    return result


def observe(session, seconds=.35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and session.child.poll() is None:
        session.pump(.03)


def credential(effects, binary, fixture, record, case):
    session = effects.Session(str(binary), ['profile', 'add', 'new', fixture.url], fixture)
    record['_session'] = session
    session.until('user:')
    initial = frame(session)
    record['initial_frame'] = initial
    accent = initial['user']['fg']
    if case == 'credential_invalid_latch':
        session.send(b'\t')
        session.send(b'\t')
    else:
        session.send(b'probe-user')
        if case != 'credential_partial_cancel':
            session.send(b'\t')
            session.send(b'pw')
            if case in ('credential_current_invalid', 'credential_plain_enter_current_invalid'):
                session.send(b'\x7f')
                session.send(b'\x7f')
            if case not in ('credential_valid_unfocused', 'credential_valid_cancel'):
                session.send(b'\t')
    before = frame(session)
    record['pre_key_frame'] = before
    focused = case not in ('credential_valid_unfocused', 'credential_partial_cancel', 'credential_valid_cancel')
    label = 'X' if case in ('credential_invalid_latch', 'credential_partial_cancel') else 'Submit'
    assert '[ ' + label + ' ]' in before['button']['row'], before
    assert (before['button']['fg'] == accent) == focused, before
    if case == 'credential_valid_unfocused':
        assert before['pass']['fg'] == accent, before
    currently_valid = case in ('credential_valid_submit', 'credential_valid_unfocused', 'credential_valid_cancel')
    assert ('••' in before['pass']['row']) == currently_valid, before
    record['causal_state'] = dict(button_focused=focused, button_invalid_latch=label == 'X',
                                  currently_valid=currently_valid, focus_accent=accent)
    key = b'\x03' if case.endswith('_cancel') else (b'\r' if case == 'credential_plain_enter_current_invalid' else b'\x1b\r')
    record['key_hex'] = key.hex()
    session.send(key)
    expected_exit = case in ('credential_valid_submit', 'credential_plain_enter_current_invalid',
                             'credential_partial_cancel', 'credential_valid_cancel')
    observe(session, 1.2 if expected_exit else .35)
    record['post_key_frame'] = frame(session)
    record['exited_on_key'] = session.child.poll() is not None
    if not expected_exit:
        assert session.child.poll() is None, 'unfocused/invalid Alt+Enter exited'
        assert session.config.read_bytes() == session.before, 'saved before quit'
        # A subsequent navigation frame proves the session still processes
        # input, and distinguishes ignored Alt+Enter from focus cycling.
        session.send(b'\t')
        record['continuation_frame'] = frame(session)
        if focused:
            assert record['continuation_frame']['user']['fg'] == accent, record['continuation_frame']
        else:
            assert record['continuation_frame']['button']['fg'] == accent, record['continuation_frame']
        session.send(b'\x03')
    elif session.child.poll() is None:
        # Retain a cleanup cancel/config outcome for the failing immutable
        # release3, without labeling that later cancellation a successful submit.
        record['submit_failure'] = 'valid focused Alt+Enter did not exit'
        session.send(b'\x03')
    session.finish()
    record['terminal_restored'] = True
    data = session.data()
    profile = data['Profiles']['new']
    actual = (profile.get('username', ''), profile.get('password', ''))
    expected = ('probe-user', 'pw') if currently_valid else ('', '')
    record['saved_values'] = list(actual)
    assert actual == expected, data
    assert data['DefaultProfile'] == 'new' and 'original' in data['Profiles'], data
    assert profile['url'] == fixture.url, profile
    assert session.config.stat().st_mode & 0o777 == 0o600
    assert b'pw' not in session.output, 'password leaked'
    effects.assert_requests(fixture, [])
    assert record['exited_on_key'] == expected_exit, record.get('submit_failure')


def saved(effects, binary, fixture, record, case):
    pattern, titles, expected_index = SAVED[case]
    sql = [f'SELECT event.code FROM logs_{index}' for index in range(len(titles))]
    fixture.routes[('GET', '/api/v1/filters')] = (200, [
        {'filter_id': 'id-' + str(index), 'filter_name': title, 'stream_name': 'deliberately-unused',
         'query': {'filter_type': 'sql', 'filter_query': sql[index]}}
        for index, title in enumerate(titles)])
    fixture.routes[('POST', '/api/v1/query')] = (200, b'[]')
    session = effects.Session(str(binary), ['sql', 'list'], fixture)
    record['_session'] = session
    session.until('event.code')
    record['initial_frame'] = frame(session)
    session.send(b'/')
    mark = len(session.output)
    # One rune per event preserves the existing coalesced-key contract.
    for rune in pattern:
        session.send(rune.encode())
    # Go runs the matcher asynchronously. Wait for a post-input redraw and
    # settle this bounded human-input control; no scheduler universality claim.
    session.until('Filter:', after=mark)
    observe(session, .35)
    record['filtered_frame'] = frame(session)
    record.update(pattern=pattern, titles=titles, expected_selected_index=expected_index)
    if case == 'saved_filter_clear_esc':
        session.send(b'\x1b')
        record['cleared_frame'] = frame(session)
    if case == 'saved_filter_accept_down':
        session.send(b'\x1b[B')
        record['accepted_frame'] = frame(session)
    session.send(b'b' if case == 'saved_filter_quit_b' else b'a' if case == 'saved_filter_apply_a' else b'\r')
    session.finish()
    record['terminal_restored'] = True
    session.unchanged()
    record['config_unchanged'] = True
    posts = [request for request in fixture.requests if request['method'] == 'POST']
    record['actual_wire_query'] = json.loads(posts[0]['body'])['query'] if posts else None
    if expected_index is None:
        effects.assert_requests(fixture, [('GET', '/api/v1/filters')])
    else:
        expected_printed = f'SELECT "event.code" FROM logs_{expected_index}'
        record['expected_wire_query'] = expected_printed + ' LIMIT 500'
        effects.assert_requests(fixture, [('GET', '/api/v1/filters'), ('POST', '/api/v1/query')])
        wire = json.loads(posts[0]['body'])
        assert wire['query'] == record['expected_wire_query'], wire
        assert (wire['startTime'], wire['endTime']) == ('1h', 'now'), wire
        assert 'Query: ' + expected_printed in session.text(), session.text()
        assert session.output.rfind(b'\x1b[?1049l') < session.output.find(b'Query: ')


def run(binary, role, case, out, expected_sha):
    effects, provenance = load_fixture()
    assert sha(binary.read_bytes()) == expected_sha, 'immutable binary hash mismatch'
    fixture = effects.Fixture()
    prefix = out / (case + '-' + str(time.time_ns()))
    record = dict(case=case, role=role, binary=str(binary), binary_sha256=expected_sha,
                  source_pin='07af390' if role == 'go' else None,
                  provenance=provenance, dimensions=[120,35], passed=False,
                  crate_builds_executed=False, rust_tests_executed=False,
                  scope='focus/validity, saved membership/ranking and application effects; no full frame or race parity')
    try:
        if case in CREDENTIALS:
            credential(effects, binary, fixture, record, case)
        else:
            saved(effects, binary, fixture, record, case)
        session = record['_session']
        assert not session.error, session.error
        record['passed'] = True
    except Exception as error:
        record['failure'] = repr(error)
    finally:
        session = record.pop('_session', None)
        if session is not None:
            record.update(returncode=session.child.poll(), stdout_sha256=sha(session.output),
                          stderr_sha256=sha(session.error), raw_stdout_path=str(prefix.with_suffix('.stdout')),
                          raw_stderr_path=str(prefix.with_suffix('.stderr')),
                          final_config_sha256=sha(session.config.read_bytes()),
                          initial_config_sha256=sha(session.before))
            prefix.with_suffix('.stdout').write_bytes(session.output)
            prefix.with_suffix('.stderr').write_bytes(session.error)
            session.close()
            record['returncode_after_cleanup'] = session.child.returncode
        fixture.close()
        record['requests'] = fixture.requests
        record['binary_unchanged'] = sha(binary.read_bytes()) == expected_sha
        assert record['binary_unchanged']
        prefix.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
    return record, prefix.with_suffix('.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--role', required=True, choices=('go', 'release3', 'candidate'))
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--expect-sha256', required=True)
    parser.add_argument('--case', action='append', choices=(*CREDENTIALS, *SAVED))
    args = parser.parse_args()
    if args.role in ('go', 'release3'):
        assert args.expect_sha256 == (GO_SHA if args.role == 'go' else RELEASE3_SHA)
    TMP.mkdir(parents=True, exist_ok=True)
    os.environ.update(TMPDIR=str(TMP), GOTMPDIR=str(TMP), CLAUDE_CODE_TMPDIR=str(TMP),
                      COLORTERM='truecolor', LANG='C.UTF-8', LC_ALL='C.UTF-8')
    os.environ.pop('NO_COLOR', None)
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for case in args.case or (*CREDENTIALS, *SAVED):
        record, path = run(args.binary.resolve(), args.role, case, args.out.resolve(), args.expect_sha256)
        result = dict(case=case, passed=record['passed'], record=str(path), failure=record.get('failure'))
        results.append(result)
        print(json.dumps(result), flush=True)
    summary = dict(role=args.role, binary_sha256=args.expect_sha256, results=results,
                   passed=sum(r['passed'] for r in results), total=len(results))
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if summary['passed'] == summary['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
