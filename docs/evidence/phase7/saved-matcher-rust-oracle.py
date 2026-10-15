"""Compile the actual frozen Rust matcher against retained actual-Go controls.

Checks faults, membership, scores and stable rankings; does not expose or check
internal matched indexes, run the CLI, or make a performance claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
args.out.mkdir(parents=True, exist_ok=False)
source_path = root / 'src/tui/saved.rs'
unicode_path = root / 'src/tui/saved_fuzzy_unicode.rs'
oracle_path = Path(__file__).with_name('opus-r4-saved-go-oracle.json')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
before = {str(p): sha(p) for p in (source_path, unicode_path, oracle_path)}
source = source_path.read_text()
start = source.index('#[derive(Clone, Copy, Debug, PartialEq, Eq)]\npub(crate) struct MatchFault')
end = source.index('\n#[cfg(test)]\nmod tests', start)
extracted = source[start:end]
cases = json.loads(oracle_path.read_text())['results']

def string(value):
    return 'std::str::from_utf8(&[' + ','.join(map(str, value.encode())) + ']).unwrap()'

program = ['#[path=' + json.dumps(str(unicode_path)) + '] mod fuzzy_unicode;', extracted,
           'fn main() {']
for case in cases:
    program.append('{ let pattern = ' + string(case['Pattern']) + ';')
    program.append('let titles = [' + ','.join(string(t) for t in case['Titles']) + '];')
    program.append('let actual: Result<Vec<(usize,i64)>, String> = (|| { let mut rows=Vec::new(); '
                   'for (i,title) in titles.iter().enumerate() { '
                   'if let Some(score)=match_score(pattern,title).map_err(|e|e.to_string())? '
                   '{ rows.push((i,score)); } } Ok(rows) })();')
    label = json.dumps(case['Name'])
    if case.get('Panic'):
        program.append('assert_eq!(actual, Err(' + string(case['Panic']) + '.to_owned()), ' + label + ');')
    else:
        matches = case['Matches'] or []
        expected = ','.join('(%dusize,%di64)' % (m['Index'], m['Score']) for m in matches)
        program.append('assert_eq!(actual, Ok(vec![' + expected + ']), ' + label + ');')
        ranks = ','.join(str(m['Index']) + 'usize' for m in case['Ranks'])
        program.append('let mut rows=actual.unwrap(); rows.sort_by_key(|&(_,s)|std::cmp::Reverse(s)); '
                       'assert_eq!(rows.iter().map(|&(i,_)|i).collect::<Vec<_>>(), vec![' + ranks + '], ' + label + ');')
    program.append('}')
program.append('println!("' + str(len(cases)) + ' actual-Go controls matched"); }')
probe = args.out / 'main.rs'
probe.write_text('\n'.join(program) + '\n')
binary = args.out / 'matcher-probe'
compiler = subprocess.check_output(['rustc', '--version', '--verbose'], text=True)
command = ['rustc', '--edition=2024', '-O', '-C', 'overflow-checks=yes', str(probe), '-o', str(binary)]
with (args.out / 'compile.log').open('wb') as log:
    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
with (args.out / 'run.log').open('wb') as log:
    subprocess.run([str(binary)], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
assert before == {str(p): sha(p) for p in (source_path, unicode_path, oracle_path)}
record = {'kind': 'actual Rust matcher execution against retained actual Go controls',
          'passed': len(cases), 'source_unchanged': before, 'compiler': compiler,
          'compile_argv': command, 'extracted_source_sha256': hashlib.sha256(extracted.encode()).hexdigest(),
          'artifacts': {str(p): sha(p) for p in (probe, binary, args.out/'compile.log', args.out/'run.log')},
          'limits': 'Fault values, membership, exact scores and stable ranking only; matched indexes, CLI effects and performance not checked.'}
(args.out / 'report.json').write_text(json.dumps(record, indent=2) + '\n')
print(record['passed'], 'actual-Go controls matched by compiled Rust')
