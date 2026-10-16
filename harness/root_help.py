#!/usr/bin/env python3
import argparse, errno, json, os, pty, select, subprocess, tempfile, time, tty
from pathlib import Path
import compatibility

def capture(binary, terminal, variables, args):
    with tempfile.TemporaryDirectory() as home:
        env = dict(HOME=home, XDG_CONFIG_HOME=home, PB_ANALYTICS='disable', **variables)
        if not terminal:
            p = subprocess.run([binary, *args], env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=10)
            assert p.returncode == 0 and not p.stderr, (p.returncode, p.stderr)
            return p.stdout
        master, slave = pty.openpty()
        tty.setraw(slave)
        p = subprocess.Popen([binary, *args], env=env, stdin=subprocess.DEVNULL,
                             stdout=slave, stderr=subprocess.PIPE)
        os.close(slave)
        data = bytearray()
        deadline = time.monotonic() + 10
        try:
            while True:
                assert time.monotonic() < deadline, 'help timed out'
                if not select.select([master], [], [], .1)[0]:
                    continue
                try:
                    chunk = os.read(master, 65536)
                except OSError as e:
                    if e.errno == errno.EIO: break
                    raise
                if not chunk: break
                data.extend(chunk)
            _, error = p.communicate(timeout=2)
            assert p.returncode == 0 and not error, (p.returncode, error)
            return bytes(data)
        finally:
            if p.poll() is None: p.kill(); p.wait()
            os.close(master)

parser = argparse.ArgumentParser(description='Exact root-help terminal and redirection checks')
parser.add_argument('--candidate', required=True)
parser.add_argument('--reference', default=os.environ.get('PB_GO_ORACLE'))
parser.add_argument('--out', type=Path)
args = parser.parse_args()
candidate, oracle, output = args.candidate, args.reference, args.out
count = 0
for args in ([], ['--help'], ['help']):
    for theme in ('dark', 'light'):
        for terminal, extra, colored in (
            (False, {}, False),
            (True, {}, True),
            (True, {'COLORTERM':'truecolor'}, True),
            (True, {'TERM':'xterm'}, False),
            (True, {'NO_COLOR':'1'}, False),
            (True, {'TERM':'dumb'}, False),
            (True, {'CI':'1'}, False),
            (False, {'CLICOLOR_FORCE':'1'}, True),
        ):
            env = dict(TERM='xterm-256color', COLORFGBG='0;15', PB_THEME=theme)
            env.update(extra)
            actual = capture(candidate, terminal, env, args)
            assert (b'\x1b[' in actual) == colored, (args, env, actual[:150])
            assert b'Parseable CLI' in actual and b'Available Commands:' in actual
            plain = compatibility.ANSI.sub(b'', actual)
            for name, description in compatibility.COMMANDS.items():
                row = f"  {name}{' ' * (10 - len(name))}{description}\n".encode()
                assert plain.count(row) == 1, (args, env, name, plain)
            if oracle:
                expected = capture(oracle, terminal, env, args)
                assert compatibility.project(actual) == compatibility.project(expected), (args, terminal, env, actual[:350], expected[:350])
            count += 1
print(f'{count} root-help cases passed; Go differential={bool(oracle)}')
if output:
    output.mkdir(parents=True, exist_ok=False)
    (output / 'report.json').write_text(json.dumps({'passed': count, 'go_differential': bool(oracle),
        'compatibility_projection': compatibility.PROJECTION}) + '\n')
