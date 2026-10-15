"""Inject erroneous computed results and require rejection even under -O."""
import importlib.util
import json
from pathlib import Path
import sys

stage = Path('/home/ajs/.cache/pb-phase7/body-deadline-r1-fixes')
script = Path('/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive/docs/evidence/phase7/p3-body-deadline-reduction.py')
spec = importlib.util.spec_from_file_location('body_reducer_checked', script)
reducer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reducer)
loader = reducer.load_module
records = []
for fault in ('overbroad_normalization', 'expectations_silently_pass', 'differential_silently_pass', 'missing_campaign'):
    def faulty_loader(name, path):
        module = loader(name, path)
        if name.endswith('_staged'):
            if fault == 'overbroad_normalization':
                module.compare_stderr = lambda case, value: reducer.CANONICAL
            elif fault == 'expectations_silently_pass':
                module.expectation_issues = lambda case, result: []
            elif fault == 'differential_silently_pass':
                module.differences = lambda case, reference, candidate: []
        return module
    reducer.load_module = faulty_loader
    output = stage / ('failclosed-' + fault)
    argv = ['--capture-root', '/home/ajs/.cache/pb-phase7/retained-release4-phase3',
            '--capture-root', '/home/ajs/.cache/pb-phase7/oracle-p3-body-deadline-1',
            '--staged-runner', str(stage / 'staged/harness/phase3.py'), '--out', str(output)]
    if fault == 'missing_campaign':
        argv[3] = str(stage / 'absent-campaign')
    expected = {'overbroad_normalization': 'negative changed or canonical: bare_canceled',
                'expectations_silently_pass': 'exit_zero not rejected',
                'differential_silently_pass': 'request_body not rejected',
                'missing_campaign': 'both exact retained campaigns are required'}[fault]
    try:
        reducer.main(argv)
    except RuntimeError as error:
        matched = expected in str(error)
        no_success_record = not (output / 'report.json').exists()
        records.append({'injected_fault': fault, 'error': str(error), 'expected_rejection': matched,
                        'no_success_record': no_success_record})
        if not matched or not no_success_record:
            raise RuntimeError('incorrect fail-closed outcome') from error
    else:
        raise RuntimeError('fault incorrectly accepted: ' + fault)
record = {'optimize': sys.flags.optimize, 'version': sys.version, 'checks': records}
with (stage / 'failclosed-optimized.json').open('x') as output:
    output.write(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
