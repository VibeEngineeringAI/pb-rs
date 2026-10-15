"""Source-stamped off-tree suite driver; prohibits executable launches."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--runner-dir', type=Path, required=True)
parser.add_argument('--test', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--f1-only', action='store_true')
parser.add_argument('--legacy-only', action='store_true')
args = parser.parse_args()
repo = Path('/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive')
stage = Path('/home/ajs/.cache/pb-phase7/body-deadline-r1-fixes')
sys.path.insert(0, str(repo / 'harness'))
sys.path.insert(0, str(args.runner_dir.resolve()))
import phase3

if Path(phase3.__file__).resolve() != (args.runner_dir / 'phase3.py').resolve():
    raise RuntimeError('runner import escaped requested off-tree directory')

def forbid_process(event, values):
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.exec'):
        raise RuntimeError('pure suite forbids executable launches: ' + event)

sys.addaudithook(forbid_process)
spec = importlib.util.spec_from_file_location('test_phase3_offtree', args.test)
tests = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tests
spec.loader.exec_module(tests)

def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item

all_tests = list(flatten(unittest.defaultTestLoader.loadTestsFromModule(tests)))
selected, excluded = [], []
for test in all_tests:
    name = test.id()
    if args.f1_only:
        if name.endswith('test_bare_deadline_cause_is_limited_to_two_exact_text_messages'):
            selected.append(test)
    elif args.legacy_only:
        if name.endswith('test_legacy_bare_deadline_contexts_remain_case_specific'):
            selected.append(test)
    elif '.RunnerTests.' in name or '.GoOracleTests.' in name or name.endswith('test_go_fixture_generate_parity'):
        excluded.append({'id': name, 'reason': 'launches shell, Go fixture or pb CLI processes; outside pure authorization'})
    else:
        selected.append(test)
if not selected:
    raise RuntimeError('no tests selected')

class Result(unittest.TextTestResult):
    def __init__(self, *values):
        super().__init__(*values)
        self.outcomes = []
    def addSuccess(self, test):
        self.outcomes.append({'id': test.id(), 'status': 'pass'})
        super().addSuccess(test)

stamp = {'argv': sys.argv, 'executable': sys.executable, 'version': sys.version, 'optimize': sys.flags.optimize,
         'runner_path': phase3.__file__, 'test_path': tests.__file__,
         'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                           (Path(phase3.__file__), args.test, stage / 'body-deadline-r1-fixes.patch')},
         'dependency_paths': {name: sys.modules[name].__file__ for name in ('phase1', 'phase2', 'run')},
         'excluded_tests': excluded, 'selected_tests': [t.id() for t in selected],
         'executable_launches_forbidden_by_audit_hook': True}
with args.out.with_suffix('.log').open('x') as log:
    log.write(json.dumps(stamp, indent=2) + '\n')
    log.flush()
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Result).run(unittest.TestSuite(selected))
stamp.update(tests_run=result.testsRun, failures=[{'id': t.id(), 'trace': trace} for t, trace in result.failures],
             errors=[{'id': t.id(), 'trace': trace} for t, trace in result.errors], skipped=result.skipped,
             outcomes=result.outcomes, successful=result.wasSuccessful())
with args.out.with_suffix('.json').open('x') as output:
    output.write(json.dumps(stamp, indent=2) + '\n')
print(f'{args.out.name}: {result.testsRun} tests; failures={len(result.failures)}; errors={len(result.errors)}; excluded={len(excluded)}')
raise SystemExit(0 if result.wasSuccessful() else 1)
