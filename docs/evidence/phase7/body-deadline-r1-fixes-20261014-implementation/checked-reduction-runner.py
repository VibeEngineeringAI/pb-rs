"""Process-free, fail-closed reduction of the UNPROMOTED exact body rule.

Use an off-tree phase3.py containing exactly the single approved-source entry.
No executable or fixture process is run. Output directories must be new.
"""
import argparse
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

CASE = 'p3-deadline-body-stall-text'
BARE = b'Error: failed to stream response body: context deadline exceeded\n'
SUFFIXED = BARE[:-1] + b' (Client.Timeout or context cancellation while reading body)\n'
CANCELED = SUFFIXED.replace(b'context deadline exceeded', b'net/http: request canceled')
CANONICAL = SUFFIXED.replace(b'context deadline exceeded', b'<CLIENT-TIMEOUT>')


def require(condition, message):
    # Intentionally not assert: these checks must survive python -O.
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def encoded(value):
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    if isinstance(value, dict):
        return {k: encoded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encoded(v) for v in value]
    return value


def controls(module, case, result):
    records = []
    request = copy.deepcopy(result['trace'][0])
    independent = [
        ('exit_zero', 'termination', {'exit': 0}, 'termination '),
        ('empty_trace', 'trace', [], 'requests '),
        ('extra_request', 'trace', [request, copy.deepcopy(request)], 'requests '),
        ('unexpected_request', 'trace', [{**request, 'unexpected': True}], 'unexpected fixture request'),
        ('empty_stdout', 'stdout_normalized', b'', 'stdout '),
        ('altered_stdout', 'stdout_normalized', result['stdout_normalized'][:-1] + b'!', 'stdout '),
        ('early_deadline', 'wall_ms', 58000, 'wall '),
        ('late_deadline', 'wall_ms', 71000, 'wall '),
        ('prefix_phase_error', 'stderr_normalized', BARE.replace(b'stream', b'read'), 'stderr '),
    ]
    for name, field, value, needle in independent:
        changed = copy.deepcopy(result)
        changed[field] = value
        if field == 'stdout_normalized':
            changed.update(stdout=value, stdout_bytes=len(value), stdout_sha256=module.run.digest(value),
                           stdout_normalized_sha256=module.run.digest(value))
        if field == 'stderr_normalized':
            changed['stderr'] = value
        issues = module.expectation_issues(case, changed)
        rejected = any(needle in issue for issue in issues)
        records.append({'name': name, 'gate': 'expectation_issues', 'field': field,
                        'value': encoded(value), 'issues': issues, 'rejected_by_expected_gate': rejected})
        require(rejected, f'{name} not rejected by {needle!r}: {issues}')
    for name in ('request_body', 'contract_header', 'config_before', 'config_after'):
        changed = copy.deepcopy(result)
        if name == 'request_body':
            value = base64.b64encode(b'{"query":"SELECT 2"}').decode()
            changed['requests'][0]['body_b64'] = value
            changed['trace'][0]['body_b64'] = value
            needle = 'requests differ'
        elif name == 'contract_header':
            value = 'text/plain'
            changed['requests'][0]['headers']['content-type'] = value
            changed['trace'][0]['headers']['content-type'] = value
            needle = 'requests differ'
        else:
            value = 'mutated-profile'
            changed[name]['config/pb/config.toml']['toml']['DefaultProfile'] = value
            needle = name.replace('_', ' ') + ' differs semantically'
        issues = module.differences(case, result, changed)
        rejected = any(needle in issue for issue in issues)
        records.append({'name': name, 'gate': 'differences', 'value': value,
                        'issues': issues, 'rejected_by_expected_gate': rejected})
        require(rejected, f'{name} not rejected by {needle!r}: {issues}')
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-root', type=Path, action='append', required=True)
    parser.add_argument('--staged-runner', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    source_path = root / 'harness/phase3.py'
    test_path = root / 'harness/test_phase3.py'
    staged_path = args.staged_runner.resolve()
    require(staged_path != source_path.resolve(), 'runner must be off-tree')
    require(root not in staged_path.parents, 'runner must be outside the worktree')
    require(not args.out.exists(), 'output already exists; preserve previous results')
    sys.path.insert(0, str(root / 'harness'))
    original_source = source_path.read_text()
    anchor = '        "p3-deadline-not-reset-per-chunk": b"failed to stream response body",\n'
    addition = '        "p3-deadline-body-stall-text": b"failed to stream response body",\n'
    require(original_source.count(anchor) == 1 and addition not in original_source, 'shared source is not frozen prepatch source')
    require(staged_path.read_text() == original_source.replace(anchor, anchor + addition), 'staged runner is not exactly the body-only entry patch')
    frozen_hashes = {str(p): sha(p) for p in (source_path, test_path, staged_path, Path(__file__))}
    original = load_module('phase3_body_deadline_original', source_path)
    module = load_module('phase3_body_deadline_staged', staged_path)
    imported_path = Path(module.__file__).resolve()
    require(imported_path == staged_path, 'imported runner path mismatch')
    original_case = next(c for c in original.corpus() if c['name'] == CASE)
    case = next(c for c in module.corpus() if c['name'] == CASE)
    corpus_unchanged = module.corpus() == original.corpus()
    require(corpus_unchanged, 'corpus changed')
    positives = []
    for value in (BARE, SUFFIXED, CANCELED):
        actual = module.compare_stderr(case, value)
        # The real manifest_entry digest is recorded per capture below.
        positives.append({'input_hex': value.hex(), 'output_hex': actual.hex(), 'canonical': actual == CANONICAL})
        require(actual == CANONICAL, f'positive not canonical: {value!r}')
    negative_inputs = [
        ('bare_canceled', BARE.replace(b'context deadline exceeded', b'net/http: request canceled')),
        ('no_newline', BARE[:-1]), ('crlf', BARE[:-1] + b'\r\n'),
        ('trailing_line', BARE + b'extra\n'), ('preceding_line', b'extra\n' + BARE),
        ('prefix_phase', BARE.replace(b'stream', b'read')),
        ('error_response_phase', BARE.replace(b'failed to stream response body', b'failed to read error response body')),
        ('header_branch', b'Error: request execution failed: Post "http://<FIXTURE>/api/v1/query": context deadline exceeded\n'),
        ('wrong_suffix', BARE[:-1] + b' (Client.Timeout exceeded while awaiting headers)\n'),
        ('json_wrapper', json.dumps({'error': BARE.decode()}).encode()),
    ]
    negatives = []
    for name, value in negative_inputs:
        actual = module.compare_stderr(case, value)
        unchanged = actual == original.compare_stderr(original_case, value)
        noncanonical = actual != CANONICAL
        negatives.append({'name': name, 'input_hex': value.hex(), 'output_hex': actual.hex(),
                          'unchanged_from_original': unchanged, 'noncanonical': noncanonical})
        require(unchanged and noncanonical, f'negative changed or canonical: {name}')
    for name in ('p3-deadline-body-stall-json', 'p3-text-empty', 'p3-deadline-body-stall-text-extra',
                 'p3-deadline-text-empty-array-lookahead', 'p3-deadline-preview-needs-one-more-byte',
                 'p3-deadline-header-stall-text', 'p3-deadline-spans-redirects'):
        altered = copy.deepcopy(case)
        altered['name'] = name
        actual = module.compare_stderr(altered, BARE)
        negatives.append({'name': 'scope:' + name, 'output_hex': actual.hex(), 'unchanged': actual == BARE,
                          'noncanonical': actual != CANONICAL})
        require(actual == BARE and actual != CANONICAL, f'scope negative failed: {name}')
    for options in ({}, {'timeout_cause': False}):
        altered = copy.deepcopy(case)
        altered['compare'] = options
        actual = module.compare_stderr(altered, BARE)
        negatives.append({'name': 'opt_in', 'compare': options, 'output_hex': actual.hex(),
                          'unchanged': actual == BARE, 'noncanonical': actual != CANONICAL})
        require(actual == BARE and actual != CANONICAL, f'opt-in negative failed: {options}')

    evidence = root / 'docs/evidence/phase7'
    original_record_path = evidence / 'p3-body-deadline-original.json'
    prior_record_path = evidence / 'p3-body-deadline-proposed-reduction.json'
    original_record = json.loads(original_record_path.read_text())
    prior_record = json.loads(prior_record_path.read_text())
    expected_hashes = {**prior_record['artifact_hashes'], **prior_record['original_reports'],
                       str(original_record_path): sha(original_record_path), str(prior_record_path): sha(prior_record_path)}
    require(all(sha(Path(p)) == h for p, h in expected_hashes.items()), 'retained evidence hash mismatch before reduction')
    require(all(sha(root / p) == h for p, h in original_record['source_sha256'].items()), 'shared source differs from captured source identity')
    requested_reports = {str(c.resolve() / 'report.json') for c in args.capture_root}
    require(len(args.capture_root) == 2 and requested_reports == set(prior_record['original_reports']), 'both exact retained campaigns are required once each')
    reports, captures, results = [], [], []
    for campaign in args.capture_root:
        campaign = campaign.resolve()
        report_path = campaign / 'report.json'
        report = json.loads(report_path.read_text())
        entry = next(c for c in report['cases'] if c['name'] == CASE)
        runner_matches = report['runner_sha256'] == sha(source_path)
        require(runner_matches, f'report runner differs from frozen source: {report_path}')
        reports.append({'path': str(report_path), 'sha256': sha(report_path),
                        'runner_sha256': report['runner_sha256'], 'runner_matches_frozen_source': runner_matches,
                        'mode': report['mode'], 'partial': report['partial'], 'reference_runs': report['reference_runs'],
                        'binaries': report['binaries'], 'original_case_entry': entry})
        roles = ('reference', 'reference-repeat-1', 'candidate') if report_path == Path(original_record['original_report_path']) else ('reference', 'reference-repeat-1')
        for role in roles:
            folder = campaign / 'cases' / CASE / role
            result = module.load_stored(folder, case)
            require(result is not None, f'missing capture: {folder}')
            raw_ok = sha(folder / 'stderr.bin') == result['stderr_raw_sha256']
            stdout_ok = sha(folder / 'stdout.bin') == result['stdout_sha256']
            require(raw_ok and stdout_ok, f'raw digest mismatch: {folder}')
            issues = module.expectation_issues(case, result)
            require(not issues, f'capture expectations fail: {folder}: {issues}')
            digest = module.manifest_entry(case, result)['stderr_sha256']
            captures.append({'path': str(folder), 'role': role, 'original_issues': original.expectation_issues(original_case, result),
                             'proposed_issues': issues, 'manifest_stderr_sha256': digest,
                             'raw_stderr_hex': result['stderr'].hex(), 'normalized_stderr_hex': result['stderr_normalized'].hex(),
                             'raw_stderr_digest_verified': raw_ok, 'raw_stdout_digest_verified': stdout_ok,
                             'wall_ms': result['wall_ms'], 'rejection_controls': controls(module, case, result)})
            results.append(result)
    require(len(captures) == 5, 'need all five actual stored captures')
    manifest_equal = len({c['manifest_stderr_sha256'] for c in captures}) == 1
    require(manifest_equal, 'capture manifest stderr digests differ')
    pairs = []
    for i, reference in enumerate(results):
        for j, candidate in enumerate(results):
            if i == j:
                continue  # no vacuous self-pairs; includes real A/A and Go/candidate pairs in both directions
            issues = module.differences(case, reference, candidate)
            pairs.append({'reference': captures[i]['path'], 'candidate': captures[j]['path'], 'issues': issues})
            require(not issues, f'capture pair fails: {i}/{j}: {issues}')
    final_hashes = {p: sha(Path(p)) for p in {**expected_hashes, **frozen_hashes}}
    unchanged = final_hashes == {**expected_hashes, **frozen_hashes}
    require(unchanged, 'source or retained evidence changed during reduction')
    record = {'status': 'off-tree process-free reduction only; not promoted or independently reviewed; no CLI execution',
              'interpreter': {'executable': sys.executable, 'version': sys.version, 'optimize': sys.flags.optimize, 'argv': sys.argv},
              'imported_runner_path': str(imported_path), 'source_and_script_sha256': frozen_hashes,
              'corpus_unchanged': corpus_unchanged, 'corpus_sha256': module.run.digest(module.run.canonical(module.corpus())),
              'positive_checks': positives, 'negative_checks': negatives, 'captures': captures, 'comparison_pairs': pairs,
              'manifest_stderr_digests_equal': manifest_equal, 'reports': reports,
              'preserved_evidence_sha256': expected_hashes, 'all_bound_inputs_unchanged': unchanged}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'report.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'{len(captures)} captures; {len(pairs)} nonself directed pairs; {len(negatives)} negatives; '
          f'{sum(len(c["rejection_controls"]) for c in captures)} mutation controls pass; optimize={sys.flags.optimize}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
