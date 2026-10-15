#!/usr/bin/env python3
"""Bounded actual-process O4-1 controls; explicit immutable binary per run.

Controlled Ctrl+V subprocess gates make a/ab/long filters reachable. Typed a
interception is a separate control. Existing Rust PROCESS_FIXTURE is read only.
No Rust build/tests, performance measurements or shared harness edits.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import types

import pyte

REPO=Path(__file__).resolve().parents[3]
TMP=Path('/home/ajs/.cache/pb-phase7/tmp')
GO_SHA='fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314'
RELEASE4_SHA='fd1f91b608bbf76832fab183f02d9c63e658f7bc12ca4aabf57a811559a871c1'
CASES={
    'paste_nul_a': dict(pattern='a',titles=['a\0b'],fault_len=1),
    'paste_nul_trailing_a': dict(pattern='a',titles=['a\0'],fault_len=1),
    'paste_nul_ab': dict(pattern='ab',titles=['ab\0x'],fault_len=2),
    'paste_stale_ab': dict(pattern='ab',titles=['other','a\0x'],selected=1),
    'paste_stale_abc': dict(pattern='abc',titles=['other','a\0x\0y'],selected=1),
    'typed_a_intercepts': dict(pattern='a',titles=['a\0b'],selected=0,typed=True),
    'typed_A_nul': dict(pattern='A',titles=['a\0b'],fault_len=1,typed=True),
    'paste_no_nul_ab': dict(pattern='ab',titles=['ax'],selected=None),
    'paste_nul_before_first_a': dict(pattern='a',titles=['x\0a'],selected=0),
}
for count,slot in ((40,0),(42,0),(43,0),(43,1),(44,0),(45,1),(64,0)):
    pattern='a'*count
    # Rankings from the unmodified actual Go oracle, with distinct SQL IDs.
    order=([1,2,0,3] if count>=43 else [1,2,0,3] if count==40 else [2,0,3,1])
    CASES[f'overflow_{count}_slot_{slot}']=dict(pattern=pattern,titles=[
        pattern,'a'*(count-1)+'_a','_'+pattern,pattern+'a'],selected=order[slot],slot=slot)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_fixture():
    source=(REPO/'tests/interactive_effects.rs').read_bytes()
    marker='const PROCESS_FIXTURE: &str = r####"'
    assert source.decode().count(marker)==1
    code=source.decode().split(marker,1)[1].split('"####;',1)[0]
    before=json.loads(Path(__file__).with_name('opus-r4-saved-before.json').read_text())
    assert sha(code.encode())==before['embedded_fixture_sha256'], 'coordinate changed embedded fixture before replay'
    module=types.ModuleType('opus_r4_saved_fixture')
    exec(compile(code,'interactive_effects.rs:PROCESS_FIXTURE','exec'),module.__dict__)
    module.ROOT=str(TMP); module.TIMEOUT=4
    return module,dict(embedded_fixture_sha256=sha(code.encode()),fixture_file_sha256=sha(source))


def frame(session):
    screen=pyte.Screen(120,35)
    pyte.Stream(screen).feed(bytes(session.output).decode(errors='replace'))
    return dict(output_offset=len(session.output),rows=[row.rstrip() for row in screen.display],
                cursor=[screen.cursor.x,screen.cursor.y])


def wait_marker(session,path):
    deadline=time.monotonic()+3
    while not path.exists():
        session.pump(.03)
        assert session.child.poll() is None, ('exited before clipboard marker',session.error)
        assert time.monotonic()<deadline, ('clipboard marker missing',str(path))


def observe(session,seconds):
    deadline=time.monotonic()+seconds
    while session.child.poll() is None and time.monotonic()<deadline:
        session.pump(.03)


def run(binary,role,case,out,expected_sha):
    effects,provenance=load_fixture()
    assert sha(binary.read_bytes())==expected_sha
    spec=CASES[case]
    fixture=effects.Fixture()
    fixture.routes[('GET','/api/v1/filters')]=(200,[
        dict(filter_id=f'id-{index}',filter_name=title,stream_name='unused',
             query=dict(filter_type='sql',filter_query=f'SELECT event.code FROM logs_{index}'))
        for index,title in enumerate(spec['titles'])])
    fixture.routes[('POST','/api/v1/query')]=(200,b'[]')
    prefix=out/(case+'-'+str(time.time_ns()))
    record=dict(case=case,role=role,binary=str(binary),binary_sha256=expected_sha,
                spec=spec,provenance=provenance,passed=False,dimensions=[120,35],
                crate_builds_executed=False,rust_tests_executed=False,
                scope='bounded clipboard/filter, crash and SQL/config/terminal effects; no full styled/race parity')
    session=None
    old_path=os.environ.get('PATH'); old_wayland=os.environ.get('WAYLAND_DISPLAY')
    try:
        with tempfile.TemporaryDirectory(prefix='opus-r4-saved-clipboard-',dir=TMP) as directory:
            folder=Path(directory)
            # PATH contains only this controlled utility. No desktop clipboard
            # is read or changed. Absolute utility paths avoid inherited helpers.
            helper=folder/'xclip'
            helper.write_text('#!/bin/sh\n'+'base=${0%/*}\n'+
                'printf started > "$base/started"\n'+
                'while [ ! -f "$base/release" ]; do /usr/bin/sleep 0.01; done\n'+
                '/usr/bin/cat "$base/payload"\n'+
                'printf completed > "$base/completed"\n')
            helper.chmod(0o700)
            (folder/'payload').write_bytes(spec['pattern'].encode())
            os.environ.update(PATH=str(folder),WAYLAND_DISPLAY='')
            session=effects.Session(str(binary),['sql','list'],fixture)
            session.until('event.code')
            record['initial_frame']=frame(session)
            session.send(b'/')
            session.until('Filter:')
            record['editing_frame']=frame(session)
            if spec.get('typed'):
                session.send(spec['pattern'].encode())
            else:
                session.send(b'\x16')
                wait_marker(session,folder/'started')
                record['clipboard_held_frame']=frame(session)
                record['clipboard_started']=True
                assert session.config.read_bytes()==session.before
                effects.assert_requests(fixture,[('GET','/api/v1/filters')])
                (folder/'release').write_text('release')
                record['clipboard_released']=True
                wait_marker(session,folder/'completed')
                record['clipboard_completed']=True
            observe(session,.65)
            record['after_filter_frame']=frame(session)
            record['exited_during_filter']=session.child.poll() is not None
            record['clipboard_invoked']=(folder/'started').exists()
            if spec.get('typed'):
                assert not record['clipboard_invoked']
            if session.child.poll() is None:
                assert 'Filter: '+spec['pattern'] in session.text(), 'paste/key filter value not observed'
                # First Down accepts a filter; later Downs navigate. Capture
                # selection/application after the match command has settled.
                if 'slot' in spec:
                    session.send(b'\x1b[B')
                    record['accepted_frame']=frame(session)
                    for _ in range(spec['slot']):
                        session.send(b'\x1b[B')
                    record['selection_frame']=frame(session)
                session.send(b'\r')
                session.finish()
            else:
                session.finish(code=session.child.returncode)
            record.update(returncode=session.child.returncode,terminal_restored=True)
            session.unchanged(); record['config_unchanged']=True
            posts=[r for r in fixture.requests if r['method']=='POST']
            record['actual_wire_query']=json.loads(posts[0]['body'])['query'] if posts else None
            if 'fault_len' in spec:
                n=spec['fault_len']
                diagnostic=f'Caught panic:\n\nruntime error: index out of range [{n}] with length {n}\n\nRestoring terminal...\n\n'
                record['expected_panic_stdout']=diagnostic
                # Terminal restoration re-enables OPOST: the diagnostic's LF
                # bytes appear as CRLF on this real PTY, unlike piped stderr.
                terminal_diagnostic=diagnostic.replace('\n','\r\n').encode()
                record['panic_stdout_exact_suffix']=session.output.endswith(terminal_diagnostic)
                record['expected_panic_pty_hex']=terminal_diagnostic.hex()
                record['killed_error_present']=b'Error: program was killed: context canceled\n' in session.error
                effects.assert_requests(fixture,[('GET','/api/v1/filters')])
                assert session.child.returncode==1, 'expected command-panic exit 1'
                assert record['panic_stdout_exact_suffix'], 'panic stdout PTY suffix differs'
                assert record['killed_error_present'], session.error
            else:
                assert session.child.returncode==0 and not session.error, session.error
                selected=spec['selected']
                if selected is None:
                    effects.assert_requests(fixture,[('GET','/api/v1/filters')])
                else:
                    record['expected_wire_query']=f'SELECT "event.code" FROM logs_{selected} LIMIT 500'
                    effects.assert_requests(fixture,[('GET','/api/v1/filters'),('POST','/api/v1/query')])
                    wire=json.loads(posts[0]['body'])
                    assert wire['query']==record['expected_wire_query'],wire
                    assert (wire['startTime'],wire['endTime'])==('1h','now'),wire
                    assert f'Query: SELECT "event.code" FROM logs_{selected}' in session.text()
                    assert session.output.rfind(b'\x1b[?1049l')<session.output.find(b'Query: ')
            record['passed']=True
    except Exception as error:
        record['failure']=repr(error)
    finally:
        if old_path is None: os.environ.pop('PATH',None)
        else: os.environ['PATH']=old_path
        if old_wayland is None: os.environ.pop('WAYLAND_DISPLAY',None)
        else: os.environ['WAYLAND_DISPLAY']=old_wayland
        if session is not None:
            record.update(returncode=session.child.poll(),stdout_sha256=sha(session.output),stderr_sha256=sha(session.error),
                          raw_stdout_path=str(prefix.with_suffix('.stdout')),raw_stderr_path=str(prefix.with_suffix('.stderr')))
            prefix.with_suffix('.stdout').write_bytes(session.output)
            prefix.with_suffix('.stderr').write_bytes(session.error)
            session.close()
            record['returncode_after_cleanup']=session.child.returncode
        fixture.close()
        record['requests']=fixture.requests
        record['binary_unchanged']=sha(binary.read_bytes())==expected_sha
        assert record['binary_unchanged']
        prefix.with_suffix('.json').write_text(json.dumps(record,indent=2)+'\n')
    return record,prefix.with_suffix('.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--role',required=True,choices=('go','release4','candidate'))
    parser.add_argument('--expect-sha256',required=True)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--case',action='append',choices=CASES)
    args=parser.parse_args()
    if args.role in ('go','release4'):
        assert args.expect_sha256==(GO_SHA if args.role=='go' else RELEASE4_SHA)
    TMP.mkdir(parents=True,exist_ok=True)
    os.environ.update(TMPDIR=str(TMP),GOTMPDIR=str(TMP),CLAUDE_CODE_TMPDIR=str(TMP),
                      COLORTERM='truecolor',LANG='C.UTF-8',LC_ALL='C.UTF-8')
    os.environ.pop('NO_COLOR',None)
    args.out.mkdir(parents=True,exist_ok=True)
    results=[]
    for case in args.case or CASES:
        record,path=run(args.binary.resolve(),args.role,case,args.out.resolve(),args.expect_sha256)
        result=dict(case=case,passed=record['passed'],record=str(path),failure=record.get('failure'))
        results.append(result); print(json.dumps(result),flush=True)
    summary=dict(role=args.role,binary_sha256=args.expect_sha256,results=results,
                 passed=sum(result['passed'] for result in results),total=len(results))
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return 0 if summary['passed']==summary['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
