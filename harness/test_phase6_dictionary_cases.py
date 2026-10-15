from pathlib import Path
import base64
from collections import Counter
import contextlib
import copy
import io
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import phase6_dictionary_cases as d
from test_phase6_protocol_limits import measured


class DictionaryCorpus(unittest.TestCase):
    def corpus_selections(self):
        return [('ia1', d.CORPUS, 25, False), ('wave', d.WAVE_CORPUS, 11, False),
                ('values-wave', d.VALUES_WAVE_CORPUS, 9, True)]

    def replaced_bytes(self, originals, values):
        manifest = json.loads(originals['manifest.json'])
        name = 'list_names_same' if 'list_names_same' in manifest['cases'] else manifest['cases'][0]
        ipc = originals[name + '.arrows']
        # Same valid IPC/value replacement as the independent IA1 witness.
        replacement = ipc.replace(bytes([7, 8]), bytes([9, 8])) if name == 'list_names_same' else ipc + b'changed'
        output = name + ('.expected' if values else '.go.stdout')
        replaced_output = (originals[output].replace(b'[7,8]', b'[9,8]')
                           if name == 'list_names_same' else originals[output] + b'changed')
        result = {'manifest.json': originals['manifest.json'] + b'\n',
                  name + '.arrows': replacement,
                  output: replaced_output}
        if values:
            result.update({name + '.errors': b'changed errors', name + '.panic': b'changed panic'})
        else:
            result[name + '.go.stderr'] = b'read end: changed terminal\n'
        return result

    def test_verified_snapshot_reads_every_artifact_once_and_survives_replacement(self):
        d.p6.TMP.mkdir(parents=True, exist_ok=True)
        for selection, source, count, values in self.corpus_selections():
            for mode in ('json', 'text'):
                with self.subTest(corpus=selection, mode=mode), tempfile.TemporaryDirectory(dir=d.p6.TMP) as tmp:
                    corpus = Path(tmp) / 'corpus'
                    shutil.copytree(source, corpus)
                    expected = d.cases(corpus, count, mode, values)
                    originals = expected.verified_bytes.copy()
                    replacements = self.replaced_bytes(originals, values)
                    read = Path.read_bytes
                    templates = d.p6.corpus
                    reads = Counter()

                    def counted_read(path):
                        data = read(path)
                        if path.parent == corpus:
                            reads[path.name] += 1
                        return data

                    def replace_after_verification():
                        # Template lookup occurs after every artifact hash has passed.
                        for name, changed in replacements.items():
                            (corpus / name).write_bytes(changed)
                        return templates()

                    with patch.object(Path, 'read_bytes', counted_read), \
                         patch.object(d.p6, 'corpus', replace_after_verification):
                        actual = d.cases(corpus, count, mode, values)
                    self.assertEqual(actual, expected)
                    self.assertEqual(actual.verified_bytes, originals)
                    self.assertEqual(actual.sources, expected.sources)
                    self.assertEqual(reads, Counter({name: 1 for name in originals}))
                    # The replacement really happened, and the preverification gate still rejects it.
                    for name, changed in replacements.items():
                        self.assertEqual((corpus / name).read_bytes(), changed)
                    with self.assertRaisesRegex(ValueError, 'artifact changed'):
                        d.cases(corpus, count, mode, values)

    def run_mock_main_with_drift(self, source, selection, count, values, mode, boundary, artifact=None):
        d.p6.TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=d.p6.TMP) as tmp:
            root = Path(tmp)
            corpus = root / 'corpus'
            shutil.copytree(source, corpus)
            expected = d.cases(corpus, count, mode, values)
            by_name = {case['name']: case for case in expected}
            originals = expected.verified_bytes.copy()
            replacements = self.replaced_bytes(originals, values)
            if artifact is not None:
                replacements = {name: data for name, data in replacements.items()
                                if name.endswith(artifact)}
            self.assertTrue(replacements)
            binaries = {}
            for role in ('go', 'candidate', 'fixture'):
                binaries[role] = root / role
                binaries[role].write_bytes(('never executed: ' + role).encode())
            identities = {role: d.p6.run.digest(path.read_bytes()) for role, path in binaries.items()}
            changed = False
            sampled = []
            templates = d.p6.corpus

            def replace():
                nonlocal changed
                for name, data in replacements.items():
                    (corpus / name).write_bytes(data)
                changed = True

            def replace_after_verification():
                if boundary == 'verification' and not changed:
                    replace()
                return templates()

            def sample(binary, fixture, case, out):
                self.assertEqual(case, by_name[case['name']])
                role = 'go' if binary == binaries['go'] else 'candidate'
                oracle = case['dictionary_oracle']
                output = originals[oracle['case'] + ('.expected' if values else '.go.stdout')]
                terminal = oracle['terminal']
                message = ('rpc error: code = InvalidArgument desc = ' + d.TERMINAL
                           if terminal == 'read end: EOF' else terminal.removeprefix('read end: '))
                error = ((json.dumps({'error': dict(code='COMMAND_FAILED', message=message,
                                                    retryable=False)}, indent=2) + '\n').encode()
                         if mode == 'json' else b'Error: ' + message.encode() + b'\n' + d.USAGE)
                view = copy.deepcopy(case)
                view['protocol_limit'] = {'expected': {role: dict(stdout_b64=base64.b64encode(output).decode(),
                    stderr_b64=base64.b64encode(error).decode(), rows=case['expected']['stdout_rows'],
                    batches=case['expected']['stdout_batches'])}}
                capture = measured(view, role)
                capture.update(binary_sha256=identities[role], fixture_sha256=identities['fixture'])
                out.mkdir(parents=True, exist_ok=False)
                d.p6.run.write_json(out / 'result.json', capture)
                sampled.append(case['name'])
                if boundary == 'capture' and len(sampled) == count * 3:
                    replace()
                return capture

            argv = sum((['--' + role, str(path)] for role, path in binaries.items()), [])
            out = root / 'report'
            argv += ['--out', str(out), '--corpus', selection, '--output-mode', mode]
            with contextlib.ExitStack() as stack:
                for attribute in ('CORPUS', 'WAVE_CORPUS', 'VALUES_WAVE_CORPUS'):
                    stack.enter_context(patch.object(d, attribute, corpus))
                stack.enter_context(patch.dict(d.p6.ORACLE_HASHES, standard=identities['go']))
                stack.enter_context(patch.object(d, 'FIXTURE_SHA256', identities['fixture']))
                stack.enter_context(patch.object(d.p6, 'corpus', replace_after_verification))
                stack.enter_context(patch.object(d.p6, 'sample', sample))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                status = d.main(argv)
            report = json.loads((out / 'report.json').read_text())
            drift = boundary != 'unchanged'
            self.assertEqual(changed, drift)
            self.assertEqual(len(sampled), count * 3)
            self.assertEqual(status, int(drift))
            self.assertTrue(report['complete'])
            self.assertEqual(report['passed'], not drift)
            self.assertEqual(report['identities_unchanged'], not drift)
            self.assertEqual(report['failures'], 0)
            self.assertEqual(report['remaining_contract_passed'], count)
            self.assertEqual(report['end_identities'], identities)
            for name, data in originals.items():
                path = str(corpus / name)
                self.assertEqual(report['sources'][path], d.p6.run.digest(data))
                end = replacements.get(name, data) if drift else data
                self.assertEqual(report['end_sources'][path], d.p6.run.digest(end))
            self.assertEqual({path for path in report['sources'] if Path(path).parent == corpus},
                             {str(corpus / name) for name in originals})

    def test_main_unchanged_corpus_passes_with_complete_initial_and_end_binding(self):
        for selection, source, count, values in self.corpus_selections():
            for mode in ('json', 'text'):
                with self.subTest(corpus=selection, mode=mode):
                    self.run_mock_main_with_drift(source, selection, count, values, mode, 'unchanged')

    def test_main_replacement_after_verification_keeps_initial_identities_and_scenarios(self):
        for selection, source, count, values in self.corpus_selections():
            for mode in ('json', 'text'):
                with self.subTest(corpus=selection, mode=mode):
                    self.run_mock_main_with_drift(source, selection, count, values, mode, 'verification')

    def test_main_replacement_after_capture_checks_each_artifact_including_manifest_errors_panic(self):
        for selection, source, count, values in self.corpus_selections():
            artifacts = ['manifest.json', '.arrows', '.expected', '.errors', '.panic'] if values else [
                'manifest.json', '.arrows', '.go.stdout', '.go.stderr']
            for mode in ('json', 'text'):
                for artifact in artifacts:
                    with self.subTest(corpus=selection, mode=mode, artifact=artifact):
                        self.run_mock_main_with_drift(source, selection, count, values, mode, 'capture', artifact)

    def test_complete_inventory_and_independent_output(self):
        cases = d.cases()
        self.assertEqual(len(cases), 25)
        self.assertEqual(len({case['name'] for case in cases}), 25)
        case = next(c for c in cases if c['dictionary_oracle']['case'] == 'list_names_differ')
        self.assertEqual(case['expected']['stdout_sha256'],
                         d.p6.run.digest(b'{"a":[7,8],"b":[7,8]}\n\n'))
        self.assertEqual(case['expected']['stdout_rows'], 1)
        self.assertEqual(case['expected']['stdout_batches'], 1)

    def test_ipc_truncation_and_trailing_bytes_rejected(self):
        data = (d.CORPUS / 'list_names_same.arrows').read_bytes()
        for cut in (1, 7, 50, len(data) - 1):
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                d.flight_frames(data[:cut])
        with self.assertRaises(ValueError):
            d.flight_frames(data + b'extra')
        with self.assertRaises(ValueError):
            d.flight_frames(b'')

    def test_changed_corpus_bytes_rejected_before_execution(self):
        target = d.CORPUS / 'list_names_same.arrows'
        read = Path.read_bytes

        def changed(path):
            data = read(path)
            return data + b'x' if path == target else data

        with patch.object(Path, 'read_bytes', changed), self.assertRaisesRegex(ValueError, 'artifact changed'):
            d.cases()

    def test_protobuf_varint_known_boundaries(self):
        self.assertEqual(d.varint(0), b'\0')
        self.assertEqual(d.varint(127), b'\x7f')
        self.assertEqual(d.varint(128), b'\x80\x01')
        self.assertEqual(d.varint(8002), b'\xc2>')
        with self.assertRaises(ValueError):
            d.varint(-1)

    def test_value_wave_and_text_are_separate_explicit_inventories(self):
        for mode in ('json', 'text'):
            cases = d.cases(d.VALUES_WAVE_CORPUS, 9, mode, True)
            self.assertEqual(len(cases), 9)
            self.assertTrue(all(c['dictionary_oracle']['terminal'] == 'read end: EOF' for c in cases))
            self.assertTrue(all(c['argv'][-1] == mode for c in cases))
            first = next(c for c in cases if c['dictionary_oracle']['case'] == 'float_shortest_ties')
            self.assertEqual(first['dictionary_oracle']['stdout_bytes'], 174)
            self.assertEqual(first['expected']['stdout_rows'], 3)
        wave = d.cases(d.WAVE_CORPUS, 11, 'text')
        self.assertEqual(len(wave), 11)
        self.assertTrue(all(c['expected']['error_code'] is None for c in wave))

    def test_json_envelope_and_text_terminal_cannot_hide_wrong_stage(self):
        for mode in ('json', 'text'):
            case = d.cases(d.VALUES_WAVE_CORPUS, 9, mode, True)[0]
            output = (d.VALUES_WAVE_CORPUS / (case['dictionary_oracle']['case'] + '.expected')).read_bytes()
            message = 'rpc error: code = InvalidArgument desc = ' + d.TERMINAL
            if mode == 'json':
                error = (json.dumps({'error': dict(code='COMMAND_FAILED', message=message,
                                                   retryable=False)}, indent=2) + '\n').encode()
            else:
                error = b'Error: ' + message.encode() + b'\n' + d.USAGE
            view = copy.deepcopy(case)
            view['protocol_limit'] = {'expected': {'go': dict(stdout_b64=base64.b64encode(output).decode(),
                stderr_b64=base64.b64encode(error).decode(), rows=case['expected']['stdout_rows'],
                batches=case['expected']['stdout_batches'])}}
            capture = measured(view, 'go')
            self.assertEqual(d.assertions(case, capture), [])
            for wrong in (error + b'\n', error.replace(b'InvalidArgument', b'Internal')):
                broken = copy.deepcopy(capture)
                broken['stderr_contract_b64'] = base64.b64encode(wrong).decode()
                self.assertTrue(d.assertions(case, broken))


if __name__ == '__main__':
    unittest.main()
