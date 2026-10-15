"""Executable provenance at the Phase6 child-process boundaries."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import phase6 as p6


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        p6.TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=p6.TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'binary'
        self.binary.write_bytes(b'unpinned executable')
        self.fixture = self.root / 'fixture'
        self.fixture.write_bytes(b'fixture executable')
        self.case = dict(name='identity', build='standard', fixture={}, auth='basic',
                         argv=[], unconfigured=True)

    def sample(self, mutation=None):
        fixture = mock.Mock(url='http://127.0.0.1:1234', info={'grpc_port': 5678})
        fixture.trace.return_value = []
        fixture.events.return_value = []
        fixture.summary.return_value = {}

        def execute(command, env, cwd, case, fx, out):
            # Run a replacement exactly where the real collector would run the
            # already-started child. The old post-only hash mislabeled this run.
            if mutation:
                mutation()
            (out / 'stderr.bin').write_bytes(b'')
            return {'failure': None}

        with mock.patch.object(p6, 'Fixture', return_value=fixture), \
                mock.patch.object(p6, 'execute', side_effect=execute):
            return p6.sample(self.binary, self.fixture, self.case, self.root / 'sample')

    def test_stable_sample_records_both_input_hashes(self):
        result = self.sample()
        self.assertEqual(result['binary_sha256'], p6.run.digest(self.binary.read_bytes()))
        self.assertEqual(result['fixture_sha256'], p6.run.digest(self.fixture.read_bytes()))
        self.assertEqual(json.loads((self.root / 'sample/result.json').read_text()), result)

    def test_replacement_with_pinned_bytes_cannot_relabel_a_running_sample(self):
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'pinned executable')
        with self.assertRaisesRegex(ValueError, '^executable changed during sample:'):
            self.sample(lambda: replacement.replace(self.binary))
        self.assertFalse((self.root / 'sample/result.json').exists())

    def test_fixture_replacement_cannot_relabel_a_running_sample(self):
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'replacement fixture')
        with self.assertRaisesRegex(ValueError, '^fixture executable changed during sample:'):
            self.sample(lambda: replacement.replace(self.fixture))

    def test_all_modes_reject_unpinned_oracle_before_sampling(self):
        reference = self.root / 'reference.json'
        reference.write_text('{}')
        for command in ('freeze', 'check-reference', 'compare'):
            with self.subTest(command=command):
                args = [command, '--go', str(self.binary), '--fixture', str(self.fixture),
                        '--out', str(self.root / command), '--reference', str(reference)]
                if command == 'compare':
                    args += ['--candidate', str(self.binary)]
                stderr = io.StringIO()
                with mock.patch.object(p6, 'corpus', return_value=[self.case]), \
                        mock.patch.object(p6, 'validate_frozen'), \
                        mock.patch.object(p6, 'sample') as sample, \
                        contextlib.redirect_stderr(stderr):
                    self.assertEqual(p6.main(args), 2)
                    sample.assert_not_called()
                self.assertIn('Go executable differs from pinned baseline hash', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
