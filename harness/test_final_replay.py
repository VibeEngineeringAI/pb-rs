import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import final_replay


class FrozenReplayTests(unittest.TestCase):
    def run_replay(self, *, gate=None, failure=False, mutate=False, added_source=False, matrix=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reference, candidate = root / 'reference', root / 'candidate'
            reference.mkdir()
            candidate.mkdir()
            for directory, names in ((reference, ('pb-go', 'pb-go-cloud', 'pb-go-login')),
                                     (candidate, ('pb-normal', 'pb-cloud', 'pb-login'))):
                for name in names:
                    (directory / name).write_bytes(name.encode())
            (root / 'fixture').write_bytes(b'fixture')
            manifest = {'complete': True, 'source_sha256': final_replay.build_sources(),
                        'binaries': {p.name: final_replay.digest(p) for p in candidate.iterdir()}}
            (candidate / 'build-manifest.json').write_text(json.dumps(manifest))
            argv = ['replay', '--reference-dir', str(reference), '--candidate-dir', str(candidate),
                    '--out', str(root / 'result'), '--jobs', '1']
            for option in ('fixture', 'flight-fixture', 'decoder-fixture', 'meter', 'effects-test',
                           'trailer-followup-reference', 'review3-followup-reference'):
                argv += ['--' + option, str(root / 'fixture')]
            if gate:
                argv += ['--gate', gate]

            current_sources = dict(manifest['source_sha256'])

            def child(command, **kwargs):
                if mutate:
                    (candidate / 'pb-normal').write_bytes(b'changed')
                if added_source:
                    current_sources['src/new.rs'] = 'new source digest'
                return subprocess.CompletedProcess(command, 7 if failure else 0)

            with patch.object(sys, 'argv', argv), \
                    patch.object(final_replay, 'commands', return_value=matrix or [('a', ['mock']), ('b', ['mock'])]), \
                    patch.object(final_replay, 'build_sources', side_effect=lambda: dict(current_sources)), \
                    patch.object(final_replay.subprocess, 'run', side_effect=child):
                code = final_replay.main()
            return code, json.loads((root / 'result/manifest.json').read_text())

    def test_flight_output_root_is_rejected_before_build_reads_or_children(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / 'outside'
            argv = ['replay', '--reference-dir', str(root / 'reference'),
                    '--candidate-dir', str(root / 'candidate'), '--out', str(out)]
            for option in ('fixture', 'flight-fixture', 'decoder-fixture', 'meter', 'effects-test',
                           'trailer-followup-reference', 'review3-followup-reference'):
                argv += ['--' + option, str(root / 'fixture')]
            errors = io.StringIO()
            with patch.object(sys, 'argv', argv), \
                    patch.object(final_replay, 'commands', return_value=[('flight-main', ['mock'])]), \
                    patch.object(final_replay.importlib, 'import_module',
                                 return_value=SimpleNamespace(TMP=root / 'owned')) as load, \
                    patch.object(final_replay.subprocess, 'run') as child, \
                    contextlib.redirect_stderr(errors):
                with self.assertRaises(SystemExit) as rejected:
                    final_replay.main()
            self.assertEqual(rejected.exception.code, 2)
            self.assertIn(f'--out must be at or under {root / "owned"}', errors.getvalue())
            load.assert_called_once_with('phase6')
            child.assert_not_called()
            self.assertFalse(out.exists())

    def test_nonflight_subset_does_not_inherit_unselected_flight_root_policy(self):
        with patch.object(final_replay.importlib, 'import_module',
                          side_effect=AssertionError('unselected Flight gate must not load phase6')):
            code, result = self.run_replay(gate='a', matrix=[('a', ['mock']), ('flight-main', ['mock'])])
        self.assertEqual(code, 1)  # Subsets still cannot claim full coverage.
        self.assertEqual([row['gate'] for row in result['checks']], ['a'])
        self.assertFalse(result['complete_coverage'])

    def test_flight_output_root_accepts_owned_root_and_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            owned = Path(folder).resolve() / 'owned'
            for gate in final_replay.FLIGHT_ROOT_GATES:
                with self.subTest(gate=gate), \
                        patch.object(final_replay.importlib, 'import_module',
                                     return_value=SimpleNamespace(TMP=owned)):
                    final_replay.preflight_output_root(owned, [gate])
                    final_replay.preflight_output_root(owned / 'nested', [gate])


    def test_complete_unchanged_inputs_pass(self):
        code, result = self.run_replay()
        self.assertEqual(code, 0)
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['checks']), 2)

    def test_subset_never_claims_complete(self):
        code, result = self.run_replay(gate='a')
        self.assertEqual(code, 1)
        self.assertFalse(result['complete_coverage'])
        self.assertFalse(result['passed'])

    def test_child_failure_retains_remaining_results(self):
        code, result = self.run_replay(failure=True)
        self.assertEqual(code, 1)
        self.assertEqual([row['exit'] for row in result['checks']], [7, 7])

    def test_changed_binary_invalidates_run(self):
        code, result = self.run_replay(mutate=True)
        self.assertEqual(code, 1)
        self.assertFalse(result['passed'])
        self.assertTrue(all(not row['identities_unchanged'] for row in result['checks']))

    def test_new_source_file_invalidates_run(self):
        code, result = self.run_replay(added_source=True)
        self.assertEqual(code, 1)
        self.assertFalse(result['passed'])
        self.assertTrue(all(not row['identities_unchanged'] for row in result['checks']))


if __name__ == '__main__':
    unittest.main()
