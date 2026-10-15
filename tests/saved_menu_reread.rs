//! The menu rereads persisted-default configuration after successful preflight.
#![cfg(target_os = "linux")]

#[test]
fn failed_menu_reread_continues_with_empty_config_like_go() {
    let output = std::process::Command::new("python3")
        .args(["-c", FIXTURE, env!("CARGO_BIN_EXE_pb")])
        .output()
        .expect("saved-menu process fixture requires Python 3");
    assert!(
        output.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

const FIXTURE: &str = r#"
import errno, os, pathlib, signal, subprocess, sys, tempfile, threading, time
with tempfile.TemporaryDirectory(prefix='pb-saved-reread-') as directory:
    home = pathlib.Path(directory)
    config = home / 'config/pb/config.toml'
    config.parent.mkdir(parents=True)
    os.mkfifo(config, 0o600)
    env = os.environ.copy()
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home/'config'),
               XDG_CACHE_HOME=str(home/'cache'), XDG_DATA_HOME=str(home/'data'),
               XDG_STATE_HOME=str(home/'state'), TERM='xterm-256color', NO_COLOR='1')
    for key in ('PB_PROFILE','PB_URL','PB_USERNAME','PB_PASSWORD','PB_API_KEY','PB_OUTPUT'):
        env.pop(key, None)
    child = subprocess.Popen([sys.argv[1], 'sql', 'list'], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, start_new_session=True)
    errors = []
    def replace_after_open():
        fd = None
        try:
            deadline = time.monotonic() + 5
            while fd is None:
                try:
                    fd = os.open(config, os.O_WRONLY | os.O_NONBLOCK)
                except OSError as error:
                    if error.errno != errno.ENXIO or time.monotonic() >= deadline:
                        raise
                    time.sleep(.005)
            os.write(fd, b'DefaultProfile="fixture"\n[Profiles.fixture]\nurl="http://127.0.0.1:1"\napiKey="fixture-key"\n')
            # The open preflight descriptor still reads the valid FIFO. Its
            # successor sees malformed TOML; no timing-sensitive sleep is used.
            config.unlink()
            config.write_bytes(b'BROKEN = [')
        except BaseException as error:
            errors.append(repr(error))
        finally:
            if fd is not None:
                os.close(fd)
    writer = threading.Thread(target=replace_after_open, daemon=True)
    writer.start()
    try:
        stdout, stderr = child.communicate(timeout=7)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=2)
        writer.join(timeout=6)
    assert not writer.is_alive() and not errors, errors
    assert child.returncode == 1, child.returncode
    assert stdout == b'', stdout
    assert stderr == (b'Error reading Default Profile\n'
                      b'Error creating request: self-hosted profile missing credentials\n'
                      b'Error: could not open a new TTY: open /dev/tty: no such device or address\n'), stderr
    assert config.read_bytes() == b'BROKEN = [', 'menu must not rewrite config'
"#;
