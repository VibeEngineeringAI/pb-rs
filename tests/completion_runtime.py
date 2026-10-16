"""Execute generated Cobra adapters in real shells; invoked by completion.rs.

Only Zsh needs a PTY. Fish uses complete -C and PowerShell uses TabExpansion2.
The native executable is placed on PATH in a directory containing shell-sensitive
characters. All child processes and PTY sessions have a 15-second deadline.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import tempfile

TIMEOUT = 15
FILENAME = "report Ω ' quote.txt"


def kill_tree(child):
    if os.name == 'posix':
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(child.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        child.kill()
    child.wait(timeout=5)


def run(argv, env, cwd, source=None):
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, cwd=cwd,
                             start_new_session=os.name == 'posix')
    try:
        stdout, stderr = child.communicate(source, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as error:
        kill_tree(child)
        raise AssertionError(('shell deadline', argv, error.stdout, error.stderr)) from error
    assert child.returncode == 0, (argv, child.returncode, stdout, stderr)
    return stdout


def quoted(value):
    # Fish permits escaped backslashes and apostrophes inside single quotes.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def check_requests(path, expected):
    actual = path.read_bytes().split(b'\0')[:-1]
    expected = [word.encode() for word in expected]
    assert actual and len(actual) % len(expected) == 0, (actual, expected)
    assert all(actual[i:i + len(expected)] == expected
               for i in range(0, len(actual), len(expected))), (actual, expected)


def fish(runtime, script, env, cwd, no_desc):
    setup = '''
source "$PB_SCRIPT"
function pb
    printf '%s\\0' $argv >> "$PB_CAPTURE"
    command "$PB_BINARY" $argv
end
'''
    cases = [
        ('pb sql run --o', ['sql', 'run', '--o'], '--output', 'Output format (text|json)'),
        ('pb dat', ['dat'], 'dataset', 'Manage datasets'),
        ("pb sql run --output 'space Ω and \"quote\"' --h",
         # Cobra 1.8.1 Fish re-evaluates prior unescaped arguments. Keep this
         # inherited split/quote loss explicit and replay it against Go.
         ['sql', 'run', '--output', 'space', 'Ω', 'and', 'quote', '--h'], '--help', 'help for run'),
        ('pb sql run --output=Ω --h', ['sql', 'run', '--output=Ω', '--h'], '--help', 'help for run'),
    ]
    observations = []
    for line, argv, choice, description in cases:
        Path(env['PB_CAPTURE']).unlink(missing_ok=True)
        source = setup + '\ncomplete --do-complete ' + quoted(line) + '\n'
        output = run([runtime, '--no-config', '-c', source], env, cwd).decode()
        expected = choice + ('' if no_desc else '\t' + description) + '\n'
        assert output == expected, (line, output, expected)
        check_requests(Path(env['PB_CAPTURE']), ['__completeNoDesc' if no_desc else '__complete'] + argv)
        observations.append(output)
    # Use Fish's escaping mode and decode its emitted insertion through Fish.
    # A filename containing spaces, Unicode and an apostrophe must round-trip.
    Path(env['PB_CAPTURE']).unlink(missing_ok=True)
    source = setup + r'''
set -l result (complete --escape --do-complete 'pb sql run report')
if test (count $result) -ne 1
    exit 1
end
string unescape -- $result
'''
    output = run([runtime, '--no-config', '-c', source], env, cwd).decode()
    assert output == FILENAME + '\n', output
    check_requests(Path(env['PB_CAPTURE']), ['__completeNoDesc' if no_desc else '__complete', 'sql', 'run', 'report'])
    observations.append(output)
    Path(env['PB_CAPTURE']).unlink(missing_ok=True)
    output = run([runtime, '--no-config', '-c', setup + "\ncomplete --do-complete 'pb sql run --help '\n"], env, cwd)
    assert output == b'', output
    check_requests(Path(env['PB_CAPTURE']), ['__completeNoDesc' if no_desc else '__complete', 'sql', 'run', '--help', ''])
    observations.append(output)
    return observations


def zsh(runtime, script, env, cwd, no_desc):
    import errno
    import pty
    import select
    import time

    setup = r'''
if [[ -n $PB_ZSH_LOCAL_ROOT ]]; then
    module_path=("$PB_ZSH_LOCAL_ROOT/usr/lib/zsh/"* $module_path)
    fpath=("$PB_ZSH_LOCAL_ROOT/usr/share/zsh/functions/"**/*(/) $fpath)
fi
zmodload zsh/zle
zmodload zsh/complist
autoload -Uz compinit
compinit -D -i
source "$PB_SCRIPT"
function pb() {
    printf '%s\0' "$@" >> "$PB_CAPTURE"
    "$PB_BINARY" "$@"
}
autoload +X _describe
functions[_pb_runtime_describe]=$functions[_describe]
function _describe() {
    printf '%s\0' "${(@P)2}" >> "$PB_CHOICES"
    _pb_runtime_describe "$@"
}
function capture_buffer() {
    print -rn -- "$BUFFER" > "$PB_BUFFER"
    BUFFER='exit'
    zle accept-line
}
zle -N capture_buffer
bindkey '^I' expand-or-complete
bindkey '^X' capture_buffer
PS1=''
print -r -- PB_READY
'''
    driver = cwd / 'driver.zsh'
    driver.write_text(setup)
    cases = [
        ('pb sql run --o', ['sql', 'run', '--o'], 'pb sql run --output ', '--output', 'Output format (text|json)'),
        ('pb dat', ['dat'], 'pb dataset ', 'dataset', 'Manage datasets'),
        ("pb sql run --output 'space Ω and \"quote\"' --h",
         ['sql', 'run', '--output', 'space Ω and "quote"', '--h'],
         "pb sql run --output 'space Ω and \"quote\"' --help ", '--help', 'help for run'),
        ('pb sql run --output=Ω --h', ['sql', 'run', '--output=Ω', '--h'],
         'pb sql run --output=Ω --help ', '--help', 'help for run'),
        ('pb sql run report', ['sql', 'run', 'report'], None, '', ''),
        ('pb sql run --help ', ['sql', 'run', '--help', ''], 'pb sql run --help ', '', ''),
    ]
    observations = []
    for line, argv, insertion, choice, description in cases:
        for name in ['PB_CAPTURE', 'PB_CHOICES', 'PB_BUFFER']:
            Path(env[name]).unlink(missing_ok=True)
        master, slave = pty.openpty()
        child = subprocess.Popen([runtime, '-f', '-i'], stdin=slave,
                                 stdout=slave, stderr=slave, env=env, cwd=cwd,
                                 start_new_session=True)
        os.close(slave)
        os.write(master, ('. ' + shlex.quote(str(driver)) + '\n').encode())
        transcript = b''
        deadline = time.monotonic() + TIMEOUT
        line_sent = False
        capture_sent = False
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    transcript += chunk
                if b'PB_READY\r\n' in transcript and not line_sent:
                    os.write(master, line.encode() + b'\t')
                    line_sent = True
                if Path(env['PB_CHOICES']).exists() and line_sent and not capture_sent:
                    # Completion has produced its choices. Queue a widget after
                    # completion; ZLE processes it only once insertion finishes.
                    os.write(master, b'\x18')
                    capture_sent = True
                if child.poll() is not None:
                    break
            else:
                raise AssertionError(('Zsh PTY deadline', transcript))
            child.wait(timeout=2)
            assert child.returncode == 0, transcript
        finally:
            if child.poll() is None:
                kill_tree(child)
            os.close(master)
        actual = Path(env['PB_BUFFER']).read_text()
        if insertion is None:
            assert shlex.split(actual) == ['pb', 'sql', 'run', FILENAME], (actual, transcript)
        else:
            assert actual == insertion, (line, actual, insertion, transcript)
        choices = Path(env['PB_CHOICES']).read_bytes().split(b'\0')[:-1]
        expected = choice + (':' + description if description and not no_desc else '')
        assert choices == [expected.encode()], (choices, expected, transcript)
        check_requests(Path(env['PB_CAPTURE']), ['__completeNoDesc' if no_desc else '__complete'] + argv)
        observations.append((actual, choices))
    return observations


def powershell(runtime, script, env, cwd, no_desc):
    # Invoke the registered native completer through the public completion API.
    # Then invoke the same registered scriptblock with a recording function to
    # check the native argv, including the empty argument after a trailing space.
    source = r'''
$ErrorActionPreference = 'Stop'
Import-Module PSReadLine
Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete
. $env:PB_SCRIPT
function Test-NullCompletionError($record, $parameter) {
    $exception = $record.Exception
    while ($null -ne $exception) {
        if ($exception.GetType().FullName -ceq 'System.Management.Automation.PSArgumentNullException' -and
            $exception.ParamName -ceq $parameter) {
            return $true
        }
        $exception = $exception.InnerException
    }
    return $false
}
$cases = @(
    @{ Line = 'pb sql run --o'; Args = @('sql', 'run', '--o'); Choice = '--output'; Description = 'Output format (text|json)' },
    @{ Line = 'pb dat'; Args = @('dat'); Choice = 'dataset'; Description = 'Manage datasets' },
    @{ Line = 'pb sql run --output ''space Ω and "quote"'' --h'; Args = @('sql', 'run', '--output', 'space Ω and "quote"', '--h'); Choice = '--help'; Description = 'help for run' },
    @{ Line = 'pb sql run --output=Ω --h'; Args = @('sql', 'run', '--output=Ω', '--h'); Choice = '--help'; Description = 'help for run' },
    @{ Line = 'pb sql run report'; Args = @('sql', 'run', 'report'); File = $true },
    @{ Line = 'pb sql run --help '; Args = @('sql', 'run', '--help', ''); Empty = $true }
)
$observations = @()
foreach ($case in $cases) {
    $line = $case.Line
    $knownEmptyError = $false
    $knownFileError = $false
    try {
        $results = @( (TabExpansion2 $line $line.Length).CompletionMatches )
    } catch {
        # Some PowerShell versions reject Cobra's empty-string NoFileComp
        # sentinel rather than treating it as an empty completion set.
        if (-not $case.Empty -or -not (Test-NullCompletionError $_ 'completionText')) { throw }
        $results = @()
        $knownEmptyError = $true
    }
    if ($case.Empty) {
        if ($results.Count -ne 0) { throw "Expected no completion: $line : $results" }
    } elseif ($results.Count -ne 1) { throw "Expected one completion: $line : $results" }
    $result = if ($results.Count) { $results[0] } else { $null }
    if ($case.File) {
        $fileAst = [System.Management.Automation.Language.Parser]::ParseInput(('pb ' + $result.CompletionText), [ref]$null, [ref]$null)
        $fileCommand = $fileAst.Find({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true)
        $filename = [System.IO.Path]::GetFileName($fileCommand.CommandElements[1].Value)
        if ($filename -cne "report Ω ' quote.txt") { throw "Filename quoting lost: $result" }
    } elseif (-not $case.Empty) {
        $description = if ($env:PB_NO_DESC -eq '1') { ' ' } else { $case.Description }
        if ($result.CompletionText -cne ($case.Choice + ' ') -or
            $result.ListItemText -cne $case.Choice -or $result.ToolTip -cne $description) {
            throw "Unexpected completion for $line : $($result | ConvertTo-Json -Compress)"
        }
    }
    function global:pb {
        $global:seenArgs = @($args)
        & $env:PB_BINARY @args
    }
    try {
        $ast = [System.Management.Automation.Language.Parser]::ParseInput($line, [ref]$null, [ref]$null)
        $command = $ast.Find({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true)
        $word = $case.Args[-1]
        try {
            $null = & $__pbCompleterBlock $word $command $line.Length
        } catch {
            # Cobra 1.8.1 passes a null item to CompletionResult when there
            # are no candidates with the default directive. TabExpansion2
            # swallows that error and performs native filename completion.
            if (-not $case.File -or -not (Test-NullCompletionError $_ 'listItemText')) { throw }
            $knownFileError = $true
        }
        $protocol = if ($env:PB_NO_DESC -eq '1') { '__completeNoDesc' } else { '__complete' }
        $expected = @($protocol) + $case.Args
        if (($global:seenArgs | ConvertTo-Json -Compress) -cne ($expected | ConvertTo-Json -Compress)) {
            throw "Unexpected argv: $($global:seenArgs | ConvertTo-Json -Compress)"
        }
    } finally { Remove-Item Function:pb }
    if ($case.File) {
        $observations += @{ file = $filename; null_item_error = $knownFileError }
    } elseif ($case.Empty) {
        $observations += @{ empty = $true; null_result_error = $knownEmptyError }
    } else {
        $observations += @{ completion = $result.CompletionText; description = $result.ToolTip }
    }
}
$observations | ConvertTo-Json -Compress
'''
    driver = cwd / 'driver.ps1'
    driver.write_text(source, encoding='utf-8-sig')
    return json.loads(run([runtime, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(driver)], env, cwd))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('shell', choices=['zsh', 'fish', 'powershell'])
    parser.add_argument('runtime')
    parser.add_argument('binary')
    options = parser.parse_args()
    runner = globals()[options.shell]
    with tempfile.TemporaryDirectory(prefix="pb completion Ω ' ") as temporary:
        cwd = Path(temporary)
        (cwd / FILENAME).touch()
        # These would match if a NoFileComp directive were ignored.
        (cwd / '--output-localfile').touch()
        (cwd / 'dataset-localfile').touch()
        env = os.environ.copy()
        for name in ['PB_COMPLETION_DESCRIPTIONS', 'COBRA_COMPLETION_DESCRIPTIONS', 'BASH_COMP_DEBUG_FILE']:
            env.pop(name, None)
        env.update(PATH=str(cwd) + os.pathsep + env.get('PATH', ''), LANG='C.UTF-8', LC_ALL='C.UTF-8',
                   POWERSHELL_TELEMETRY_OPTOUT='1', TERM='xterm', PB_CAPTURE=str(cwd / 'argv'), PB_CHOICES=str(cwd / 'choices'),
                   PB_BUFFER=str(cwd / 'buffer'), HOME=str(cwd), XDG_CONFIG_HOME=str(cwd / 'config'))
        version = run([options.runtime, '--version'], env, cwd).decode().strip()
        print(version, flush=True)
        executables = [options.binary]
        if os.environ.get('PB_GO_ORACLE'):
            executables.append(os.environ['PB_GO_ORACLE'])
        baseline = None
        for executable in executables:
            executable = str(Path(executable).resolve())
            link = cwd / ('pb.exe' if os.name == 'nt' else 'pb')
            link.unlink(missing_ok=True)
            shutil.copy2(executable, link)
            env['PB_BINARY'] = str(link)
            observed = []
            for no_desc in [False, True]:
                script = cwd / ('completion.' + ('ps1' if options.shell == 'powershell' else options.shell))
                command = [str(link), 'completion', options.shell]
                if no_desc:
                    command.append('--no-descriptions')
                script.write_bytes(run(command, env, cwd))
                env.update(PB_SCRIPT=str(script), PB_NO_DESC='1' if no_desc else '0')
                observed.append(runner(options.runtime, script, env, cwd, no_desc))
            if baseline is None:
                baseline = observed
            else:
                assert observed == baseline, ('runtime Go parity', options.shell, observed, baseline)
    print(options.shell + ': 6 cases × 2 description modes × ' + str(len(executables)) + ' executable(s) passed')
    if options.shell == 'powershell':
        print('Known Cobra 1.8.1 PowerShell limitation: empty results can raise null completionText/listItemText errors; filename fallback and exception paths checked' + (' and reproduced with Go' if len(executables) > 1 else ''))
    if options.shell == 'fish':
        print('Known Cobra 1.8.1 Fish limitation: prior quoted values lose grouping/quotes; asserted explicitly' + (' and reproduced with Go' if len(executables) > 1 else ''))


if __name__ == '__main__':
    main()
