#!/usr/bin/env python3
"""Functional PTY controls for sidebar, builder and saved byte rendering.

Requires the phase7 Python environment (pyte 0.8.2, wcwidth 0.2.13).
Loads the owned embedded HTTP/PTY fixture and only pure Terminal declarations;
never imports/calls phase7.execute or the native helper. No builds or timings.
"""
import argparse
import ast
import codecs
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import types

REPO = Path(__file__).resolve().parents[3]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_tools():
    rust = (REPO / 'tests/interactive_effects.rs').read_text()
    fixture_source = rust.split('const PROCESS_FIXTURE: &str = r####"', 1)[1].rsplit('"####;', 1)[0]
    fixture = types.ModuleType('owned_effect_fixture')
    exec(compile(fixture_source, 'interactive_effects.rs:PROCESS_FIXTURE', 'exec'), fixture.__dict__)
    observer_source = (REPO / 'harness/phase7.py').read_text()
    parsed = ast.parse(observer_source)
    nodes = [node for node in parsed.body if (
        isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in ('dependencies', 'Terminal')) or (
        isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'CELL_FIELDS'
                                            for target in node.targets))]
    assert len(nodes) == 3, 'pure observer declarations changed; review before using'
    observer = types.ModuleType('pure_terminal_observer')
    observer.__dict__.update(codecs=codecs, time=time)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'phase7.py:pure-Terminal', 'exec'), observer.__dict__)
    return fixture, observer, {'fixture_sha256': sha(fixture_source.encode()),
                              'observer_source_sha256': sha(observer_source.encode()),
                              'observer_definitions': ['CELL_FIELDS', 'dependencies', 'Terminal']}


def endpoint_frame(frame, url):
    """Normalize only digits of this exact ephemeral URL, retaining styles."""
    result = copy.deepcopy(frame)
    for row in result['cells']:
        text = ''.join(cell[0] for cell in row)
        offset = text.find(url)
        if offset >= 0:
            assert all(len(cell[0]) == 1 for cell in row[:offset + len(url)])
            for index, char in enumerate(url):
                if char.isdigit():
                    row[offset + index][0] = '0'
    return result


def clip_spec(case):
    if case == 'builder':
        return {'dimensions': (80, 30), 'text': 'abcdefghijkl测试_tail',
                'raw_clip': b'abcdefghijkl\xe6...', 'row': 11, 'column': 8,
                'visible_clip': 'abcdefghijkl�...',
                'visible_row': '│  │   abcdefghijkl�...   ││ (empty)              ││ (empty)              │   │ '}
    return {'dimensions': (40, 20), 'text': 'saved_' + 'a' * 22 + '测试_tail',
            'raw_clip': b'saved_' + b'a' * 22 + b'\xe6\xe2\x80\xa6',
            'row': 5, 'column': 8, 'visible_clip': 'saved_' + 'a' * 22 + '�…',
            'visible_row': '│      saved_' + 'a' * 22 + '�…  │'}


def run(binary, role, case, out, fixture_module, observer, provenance):
    class ObservedSession(fixture_module.Session):
        def __init__(self, *args, dimensions):
            super().__init__(*args, dimensions=dimensions)
            self.terminal = observer.Terminal(*dimensions, 'dark')
            self.chunks = []

        def pump(self, duration=.05):
            start = len(self.output)
            super().pump(duration)
            data = bytes(self.output[start:])
            if data:
                self.chunks.append([start, len(self.output)])
                self.terminal.feed(data)

    fixture = fixture_module.Fixture()
    session, frames, failure = None, {}, None
    before = sha(Path(binary).read_bytes())
    prefix = out / (case + '-' + role + '-' + str(time.time_ns()))
    dimensions = (120, 35) if case == 'sidebar' else clip_spec(case)['dimensions']
    record = dict(case=case, role=role, binary=binary, binary_sha256=before,
                  dimensions=dimensions, fixture_url=fixture.url, provenance=provenance,
                  cell_fields=observer.CELL_FIELDS, observer_chunk_scope='recorded reads and separate one-chunk replay')
    try:
        if case == 'sidebar':
            fixture_module.setup_sql(fixture)
            schema = '/api/v1/logstream/%B5%8B%E8%AF%95/schema'
            fixture.routes[('GET', schema)] = (200, {'fields': [{'name': 'RAW_BYTE_SCHEMA'}]})
            session = ObservedSession(binary, ['sql', 'run', '-i', '--from', '2024-01-01T00:00:00Z',
                                              '--to', '2024-01-01T01:00:00Z'], fixture, dimensions=dimensions)
            session.until('QUERY SQL')
            session.send(b'\x1b[200~' + "select 'Ⱥ' from 测试".encode() + b'\x1b[201~')
            session.until("select 'Ⱥ' from 测试")
            session.send(b'\x0c')
            session.send(b'\x0c')
            fixture.wait(schema)
            session.until('RAW_BYTE_SCHEMA')
            session.send(b'\x03')
            session.finish()
            frames['actual_reads'] = [item['frame'] for item in session.terminal.transitions
                                      if item['kind'] == 'before-alt-exit'][-1]
            checkpoint = len(session.output)
            replay = observer.Terminal(*dimensions, 'dark')
            replay.feed(bytes(session.output), final=True)
            frames['one_chunk'] = [item['frame'] for item in replay.transitions
                                   if item['kind'] == 'before-alt-exit'][-1]
            fixture_module.assert_requests(fixture, [('GET', '/api/prism/v1/home'), ('GET', schema)])
            raw_value = b'\xb5\xc2\x8b\xe8\xaf\x95'
            record.update(raw_value_hex=raw_value.hex(), raw_value_occurrences=session.output.count(raw_value),
                          replacement_occurrences=session.output.count('��试'.encode()))
            assert record['raw_value_occurrences'] >= 1, record
            assert record['replacement_occurrences'] == 0, record
        else:
            spec = clip_spec(case)
            record.update(input_text=spec['text'], raw_value_hex=spec['raw_clip'].hex())
            if case == 'builder':
                fixture_module.setup_promql(fixture, [spec['text']])
                session = ObservedSession(binary, fixture_module.query_args(''), fixture, dimensions=dimensions)
                fixture_module.open_builder(session)
                session.until('built  ' + spec['text'])
                session.until('filter')
            else:
                fixture.routes[('GET', '/api/v1/filters')] = (200, [{
                    'filter_id': 'saved-id', 'filter_name': spec['text'], 'stream_name': 'logs',
                    'query': {'filter_type': 'sql', 'filter_query': 'SELECT 1'}}])
                session = ObservedSession(binary, ['sql', 'list'], fixture, dimensions=dimensions)
                session.until('SAVED QUERIES')
                session.until('SELECT 1')
                session.until('quit')
            checkpoint = len(session.output)
            frames['actual_reads'] = session.terminal.frame()
            replay = observer.Terminal(*dimensions, 'dark')
            replay.feed(bytes(session.output[:checkpoint]), final=True)
            frames['one_chunk'] = replay.frame()
            # Preserve the captured failure even when byte/row/style checks fail.
            raw_present = spec['raw_clip'] in session.output
            row = frames['actual_reads']['cells'][spec['row'] - 1]
            record.update(raw_value_present=raw_present, expected_row=spec['row'], expected_column=spec['column'])
            if case == 'builder':
                session.send(b'\x1b')
                session.send(b'\x03')
            else:
                session.send(b'b')
            session.finish()
            if case == 'builder':
                count = sum(item['path'] == '/prometheus/api/v1/label/__name__/values' for item in fixture.requests)
                assert 1 <= count <= 2, fixture.requests
                fixture_module.assert_requests(fixture, [('GET', '/api/prism/v1/home')] +
                                               [('GET', '/prometheus/api/v1/label/__name__/values')] * count,
                                               independent=True)
            else:
                fixture_module.assert_requests(fixture, [('GET', '/api/v1/filters')])
            assert raw_present, record
            assert ''.join(cell[0] for cell in row) == spec['visible_row'], row
            for offset, char in enumerate(spec['visible_clip']):
                expected = [char, '38bdf8', 'default', True, False, False, False, False, False, False, False, False]
                assert row[spec['column'] - 1 + offset] == expected, row
            assert frames['actual_reads']['cursor'] == {
                'x': 0, 'y': dimensions[1] - 1, 'visible': False, 'shape': 0}, frames['actual_reads']['cursor']
        session.unchanged()
        assert not session.error, session.error
        assert not session.terminal.unsupported and not replay.unsupported, (session.terminal.unsupported, replay.unsupported)
        assert before == sha(Path(binary).read_bytes()), 'binary changed during control'
        record['checkpoint_byte_offset'] = checkpoint
        record['actual_vs_one_chunk_equal'] = frames['actual_reads'] == frames['one_chunk']
        if case != 'sidebar':
            assert record['actual_vs_one_chunk_equal'], 'clip checkpoint depends on read split'
    except BaseException as error:
        failure = repr(error)
    finally:
        record.update(passed=failure is None, failure=failure, requests=fixture.requests)
        if session:
            record.update(returncode=session.child.poll(), actual_read_chunks=session.chunks,
                          stdout_sha256=sha(session.output), stdout_path=str(prefix.with_suffix('.stdout')),
                          observer_unsupported=session.terminal.unsupported)
            prefix.with_suffix('.stdout').write_bytes(session.output)
            prefix.with_suffix('.stderr').write_bytes(session.error)
            session.close()
        prefix.with_suffix('.frames.json').write_text(json.dumps(frames, separators=(',', ':')) + '\n')
        prefix.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
        fixture.close()
    return record, frames, prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['sidebar', 'builder', 'saved'], required=True)
    parser.add_argument('--oracle', required=True, help='preserved pinned Go executable')
    parser.add_argument('--candidate', help='immutable prebuilt candidate; no build occurs')
    parser.add_argument('--out', type=Path, required=True, help='retain all raw/cell/failure records here')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ.pop('NO_COLOR', None)
    os.environ.update(COLORTERM='truecolor', LANG='C.UTF-8', LC_ALL='C.UTF-8')
    fixture, observer, provenance = load_tools()
    oracle, go_frames, go_prefix = run(args.oracle, 'go', args.case, args.out, fixture, observer, provenance)
    report = {'case': args.case, 'oracle_record': str(go_prefix), 'oracle_passed': oracle['passed'],
              'normalization': 'only digits in the exact ephemeral fixture URL, preserving styles/layout',
              'chunk_limit': 'sidebar C1 can split pyte draw calls; actual-read-only differences require scoped review'}
    passed = oracle['passed']
    if args.candidate:
        candidate, candidate_frames, candidate_prefix = run(args.candidate, 'candidate', args.case,
                                                           args.out, fixture, observer, provenance)
        report.update(candidate_record=str(candidate_prefix), candidate_passed=candidate['passed'])
        comparisons = []
        for mode in ('actual_reads', 'one_chunk'):
            if mode not in go_frames or mode not in candidate_frames:
                continue
            a = endpoint_frame(go_frames[mode], oracle['fixture_url'])
            b = endpoint_frame(candidate_frames[mode], candidate['fixture_url'])
            changes = [{'row_1based': y + 1, 'column_1based': x + 1, 'go': cell, 'candidate': b['cells'][y][x]}
                       for y, row in enumerate(a['cells']) for x, cell in enumerate(row) if cell != b['cells'][y][x]]
            comparisons.append({'mode': mode, 'equal': a == b, 'cell_difference_count': len(changes),
                                'cell_differences': changes, 'go_cursor': a['cursor'], 'candidate_cursor': b['cursor']})
        report['comparisons'] = comparisons
        passed = passed and candidate['passed'] and len(comparisons) == 2 and all(item['equal'] for item in comparisons)
    report['passed'] = passed
    report_path = args.out / (args.case + '-comparison-' + str(time.time_ns()) + '.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': passed, 'report': str(report_path),
                      'oracle_record': str(go_prefix), 'candidate_record': report.get('candidate_record')}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
