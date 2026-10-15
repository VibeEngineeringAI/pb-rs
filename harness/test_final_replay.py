import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import final_replay


class FrozenReplayTests(unittest.TestCase):
    def run_replay(self, *, gate=None, failure=False, mutate=False, added_source=False):
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
            for option in ('fixture', 'flight-fixture', 'decoder-fixture', 'meter',
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
                    patch.object(final_replay, 'commands', return_value=[('a', ['mock']), ('b', ['mock'])]), \
                    patch.object(final_replay, 'build_sources', side_effect=lambda: dict(current_sources)), \
                    patch.object(final_replay.subprocess, 'run', side_effect=child):
                code = final_replay.main()
            return code, json.loads((root / 'result/manifest.json').read_text())

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
