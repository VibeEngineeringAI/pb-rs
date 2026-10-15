#!/usr/bin/env python3
"""Actual pinned fuzzy oracle with a scratch-only observational module copy.

Checks instrumented scores/ranks/panics against the unmodified module, retaining
per-candidate and per-apply int64 traces. Does not build or execute Rust.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[3]
CACHE = Path('/home/ajs/.cache/pb-phase7/opus-r4-saved')
TMP = Path('/home/ajs/.cache/pb-phase7/tmp')
GOROOT = Path('/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64')
FUZZY = Path('/home/ajs/go/pkg/mod/github.com/sahilm/fuzzy@v0.1.1/fuzzy.go')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    before = json.loads(Path(__file__).with_name('opus-r4-saved-before.json').read_text())
    for name in ('go.mod','go.sum',str(FUZZY),
                 '/home/ajs/go/pkg/mod/github.com/charmbracelet/bubbles@v0.20.0/list/list.go'):
        assert sha((REPO/name).read_bytes()) == before['before_sha256'][name], ('source changed',name)
    source_path = Path(__file__).with_name('opus-r4-saved-oracle.go.txt')
    source = source_path.read_text()
    original = FUZZY.read_text()
    instrumented = original
    injections = {
        '\t\t\t\tif score > bestScore {':
            '\t\t\t\tProbeTrace = append(ProbeTrace, ProbeStep{"candidate", j, patternIndex, score, bestScore, matchedIndex, currAdjacentMatchBonus, score > bestScore})\n',
        '\t\t\t\t\tmatch.Score += bestScore':
            '\t\t\t\t\tProbeTrace = append(ProbeTrace, ProbeStep{"apply", j, patternIndex, bestScore, bestScore, matchedIndex, currAdjacentMatchBonus, true})\n',
    }
    for marker, injection in injections.items():
        assert instrumented.count(marker)==1
        instrumented = instrumented.replace(marker,injection+marker)
    instrumented += '\n// Scratch-only observational hooks; guarded against the unmodified oracle.\n' \
        'type ProbeStep struct { Kind string; Byte, PatternIndex, Score, BestBefore, MatchedBefore, AccumulatedBonus int; Accepted bool }\n' \
        'var ProbeTrace []ProbeStep\n'
    traced_source = source.replace('// PROBE_RESET','fuzzy.ProbeTrace = nil') \
        .replace('// PROBE_CAPTURE_ON_PANIC','if out.Trace == nil { out.Trace = append([]fuzzy.ProbeStep(nil), fuzzy.ProbeTrace...) }') \
        .replace('// PROBE_CAPTURE','out.Trace = append([]fuzzy.ProbeStep(nil), fuzzy.ProbeTrace...)')
    CACHE.mkdir(parents=True,exist_ok=True)
    TMP.mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    env.update(GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off', GOROOT=str(GOROOT),
               GOCACHE=str(CACHE/'go-cache'), TMPDIR=str(TMP), GOTMPDIR=str(TMP))
    with tempfile.TemporaryDirectory(prefix='opus-r4-saved-oracle-',dir=TMP) as directory:
        folder=Path(directory)
        main_go=folder/'main.go'; main_go.write_text(source)
        command=[str(GOROOT/'bin/go'),'run','-mod=readonly']
        plain=json.loads(subprocess.check_output(command+[str(main_go)],cwd=REPO,env=env))
        replacement=folder/'fuzzy'; replacement.mkdir()
        (replacement/'fuzzy.go').write_text(instrumented)
        (replacement/'go.mod').write_text('module github.com/sahilm/fuzzy\n\ngo 1.25.0\n')
        (folder/'go.mod').write_text((REPO/'go.mod').read_text()+'\nreplace github.com/sahilm/fuzzy => ./fuzzy\n')
        (folder/'go.sum').write_bytes((REPO/'go.sum').read_bytes())
        main_go.write_text(traced_source)
        traced=json.loads(subprocess.check_output([str(GOROOT/'bin/go'),'run','-mod=mod',str(main_go)],cwd=folder,env=env))
    assert plain['go_version']==traced['go_version']=='go1.25.4' and plain['int_bits']==64
    for expected,actual in zip(plain['results'],traced['results'],strict=True):
        assert expected=={key:value for key,value in actual.items() if key!='Trace'}, expected['Name']
    full_trace_path=CACHE/'go-oracle-traced-full.json'
    full_trace_bytes=(json.dumps(traced,indent=2)+'\n').encode()
    full_trace_path.write_bytes(full_trace_bytes)
    compact_results=[{key:value for key,value in result.items()
                     if key!='Trace' or result['Name'].startswith('nul_') or result['Name']=='contiguous_64'}
                    for result in traced['results']]
    record=dict(go_version=plain['go_version'],int_bits=plain['int_bits'],
                unmodified_and_instrumented_equal=True,results=compact_results,
                full_trace_path=str(full_trace_path),full_trace_sha256=sha(full_trace_bytes),
                provenance={'module_sha256':sha(original.encode()),'oracle_source_sha256':sha(source.encode()),
                            'instrumented_source_sha256':sha(instrumented.encode()),'runner_sha256':sha(Path(__file__).read_bytes())},
                rust_executed=False,scope='bounded actual imported fuzzy/list plus observational scratch module copy')
    output=Path(__file__).with_name('opus-r4-saved-go-oracle.json')
    output.write_text(json.dumps(record,indent=2)+'\n')
    (CACHE/'go-oracle-unmodified.json').write_text(json.dumps(plain,indent=2)+'\n')
    rejected=[]
    for result in traced['results']:
        if result['Name'].startswith('contiguous_'):
            bad=[step for step in result.get('Trace',[]) if step['Kind']=='candidate' and not step['Accepted']]
            if bad:
                rejected.append({'count':len(result['Pattern']),'first_rejected':bad[0],
                                 'score':result['Matches'][0]['Score'],'indexes':result['Matches'][0]['MatchedIndexes']})
    print(json.dumps({'results':len(traced['results']),'oracle':str(output),
                      'first_rejected_contiguous':rejected[:2]},indent=2))


if __name__=='__main__':
    main()
