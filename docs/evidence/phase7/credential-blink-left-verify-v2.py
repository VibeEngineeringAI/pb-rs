#!/usr/bin/env python3
"""Read-only E6-1 successor seal/artifact verification; no CLI or file writes."""
import difflib
import hashlib
import json
from pathlib import Path
import tomllib

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_bytes())


def identity(path):
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify():
    verified, checks = {}, []
    def check_refs(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                actual = identity(value['path'])
                require(actual['sha256'] == value['sha256'], 'artifact hash drift: ' + value['path'])
                if 'bytes' in value:
                    require(actual['bytes'] == value['bytes'], 'artifact size drift: ' + value['path'])
                verified[actual['path']] = actual
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    seal = read(HERE / 'credential-blink-left-sealed-v2.json')
    check_refs(seal)
    evidence = read(seal['evidence']['path'])
    # The pre-Left live runner path was intentionally replaced during that
    # historical task. Its bytes are bound to the separately preserved archive.
    current_refs = dict(evidence)
    historical_runner = current_refs.pop('original_runner')
    archive = identity(evidence['archived_runner']['path'])
    require(archive['sha256'] == historical_runner['sha256'] and
            archive['bytes'] == historical_runner['bytes'], 'historical runner archive binding')
    # Captured before/after manifests include root-owned design docs that may
    # change after the recorded freeze. Verify that historical map through its
    # sealed original evidence, not against subsequently edited live docs.
    historical_identities = current_refs.pop('binary_helper_manifest_identities')
    check_refs(current_refs)
    erratum = evidence['E6_1_erratum']
    originals = erratum['original_immutable_artifacts']
    original_seal = read(originals['sealed.json']['path'])
    check_refs(original_seal)
    require(original_seal['evidence'] == originals['evidence.json'], 'original evidence seal binding')
    require(original_seal['results'] == originals['results.md'], 'original results seal binding')
    original_evidence = read(originals['evidence.json']['path'])
    require(historical_identities == original_evidence['binary_helper_manifest_identities'],
            'historical captured identities changed')
    require(original_evidence['raw_saved_config_bytes_equal'] is False, 'original JSON raw-byte flag')
    require(evidence['full_strict_differential'] == original_evidence['full_strict_differential'],
            'strict outcome must remain unchanged')
    require(evidence['raw_capture_files'] == original_evidence['raw_capture_files'], 'raw capture references changed')
    require(evidence['source_identity_during_capture'] == original_evidence['source_identity_during_capture'],
            'historical source identities changed')
    checks.append('Original seals, raw captures, source identities and full strict outcome preserved')

    parent = read(erratum['root_parent_verification_preserved']['path'])
    for path, expected in parent['artifacts'].items():
        actual = identity(path)
        require(actual['sha256'] == expected, 'parent-verified artifact drift: ' + path)
        verified[path] = actual
    require(parent['status'] == 'passed' and parent['independent_root_pure_tests'] == 8 and
            parent['verified_artifact_count'] == 22, 'parent verification record')
    checks.append('Root independent eight tests / 22 artifact checks preserved; not rerun')

    config = evidence['saved_config_serialization']
    data = {role: Path(file['path']).read_bytes() for role, file in config['files'].items()}
    parsed = {role: tomllib.loads(raw.decode()) for role, raw in data.items()}
    require(len(data['go']) == 91 and len(data['release5']) == 80, 'exact config byte lengths')
    require(data['go'] != data['release5'] and parsed['go'] == parsed['release5'], 'raw false / parsed true')
    require(config['parsed_TOML'] == parsed, 'recorded parsed values')
    require(config['raw_hex'] == {role: raw.hex() for role, raw in data.items()}, 'raw hex retention')
    require(config['raw_utf8'] == {role: raw.decode() for role, raw in data.items()}, 'raw text retention')
    require(evidence['raw_saved_config_bytes_equal'] is False and
            evidence['parsed_saved_config_TOML_equal'] is True and config['raw_bytes_equal'] is False and
            config['parsed_TOML_equal'] is True, 'successor config equality flags')
    exact_diff = ''.join(difflib.unified_diff(data['go'].decode().splitlines(keepends=True),
        data['release5'].decode().splitlines(keepends=True), fromfile='Go config-after.toml',
        tofile='release5 config-after.toml'))
    require(config['exact_unified_diff'] == exact_diff, 'exact unified byte/text diff')
    spans = [{'kind': kind, 'go_span_zero_based_half_open': [a, b],
              'release5_span_zero_based_half_open': [x, y], 'go_hex': data['go'][a:b].hex(),
              'release5_hex': data['release5'][x:y].hex()}
             for kind, a, b, x, y in difflib.SequenceMatcher(
                 a=data['go'], b=data['release5'], autojunk=False).get_opcodes() if kind != 'equal']
    require(config['exact_byte_edit_spans'] == spans, 'exact byte edit spans')
    checks.append('Raw bytes false, direct parsed TOML true; exact text/hex/diff verified without normalization')

    for role, row in config['prior_release4_A_controls'].items():
        report, old_seal = read(row['report']['path']), read(row['seal']['path'])
        check_refs(old_seal)
        require(old_seal['report'] == row['report'], 'release4 report seal binding')
        require(report['binary_before'] == row['binary_identity'] == report['binary_after'],
                'release4 binary capture identity')
        session_row = next(s for s in report['sessions'] if s['case'] == 'A')
        require(row['config'] in session_row['capture_files'], 'release4 A raw config binding')
        session = read(row['session']['path'])
        require(session['binary'] == row['binary_identity'], 'release4 A session binary binding')
        raw = Path(row['config']['path']).read_bytes()
        require(raw == data['go' if role == 'go' else 'release5'], 'respective release4 bytes changed')
        require(tomllib.loads(raw.decode()) == row['parsed_TOML'] == parsed['go'], 'release4 parsed equality')
    checks.append('Immutable release4 A provenance proves the same respective serialization bytes')

    sessions = {}
    for role, refs in evidence['reports'].items():
        report, captured_seal = read(refs['report']['path']), read(refs['sealed']['path'])
        check_refs(captured_seal)
        require(captured_seal['report'] == refs['report'], 'release5 pair report seal binding')
        row = next(s for s in report['sessions'] if s['case'] == 'A')
        path = next(f['path'] for f in row['capture_files'] if Path(f['path']).name == 'session.json')
        sessions[role] = read(path)
        require(report['identity_unchanged'], 'original capture identity freeze')
    go, candidate = sessions['go-A'], sessions['release5-A']
    require({f['name']: f['frame'] for f in go['frames']} ==
            {f['name']: f['frame'] for f in candidate['frames']}, 'exact styled checkpoint frames')
    require(go['final_frame'] == candidate['final_frame'], 'exact final frame')
    require(go['config_before'] == candidate['config_before'] and go['config_after'] == candidate['config_after'],
            'existing semantic configuration comparison')
    for role, session in (('go', go), ('release5', candidate)):
        require(session['config_after']['config/pb/config.toml']['toml'] == parsed[role],
                'direct parse must equal retained semantic snapshot')
    checks.append('Unchanged exact styled checkpoints/final frame and existing semantic config contract verified')

    freeze = evidence['documentary_source_freeze']
    require(freeze['before'] == freeze['after'] and freeze['status'] == 'COMPLETE', 'documentary source freeze')
    require(erratum['new_CLI_sessions'] == 0 and not erratum['production_edits'] and
            not erratum['harness_edits'] and not erratum['normalization'] and not erratum['whole_phase_approval'],
            'narrow documentary scope')
    results = Path(seal['results']['path']).read_text()
    require('That claim was wrong' in results and '91 bytes' in results and '80 bytes' in results,
            'user-visible prior-wording correction')
    require('raw saved-config bytes compare equal' not in evidence['limits'][2], 'contradictory successor limit')
    checks.append('User-visible wording corrected; documentary scope, no normalization or phase approval')
    return {'status': 'passed', 'new_CLI_sessions': 0, 'artifact_writes_by_verifier': 0,
            'successor_seal': identity(HERE / 'credential-blink-left-sealed-v2.json'),
            'checks': checks, 'verified_unique_artifacts': len(verified), 'artifacts': verified}


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
