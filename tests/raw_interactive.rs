//! Malformed Unix argv at interactive session boundaries. Editors remain rune
//! based; pre-editor diagnostics and dataset requests retain the original bytes.
#![cfg(unix)]
use std::{ffi::OsString, os::unix::ffi::OsStringExt, process::Command};

#[test]
fn sql_raw_time_and_pre_editor_panic_keep_go_bytes() {
    let root = std::env::temp_dir().join(format!("pb-raw-interactive-{}", std::process::id()));
    std::fs::create_dir_all(root.join("pb")).unwrap();
    std::fs::write(root.join("pb/config.toml"), "DefaultProfile = \"fixture\"\n[Profiles.fixture]\nurl = \"http://127.0.0.1:1\"\nusername = \"u\"\npassword = \"p\"\ncloud = false\n").unwrap();
    let mut binaries = vec![OsString::from(env!("CARGO_BIN_EXE_pb"))];
    if let Some(oracle) = std::env::var_os("PB_GO_ORACLE") {
        binaries.push(oracle);
    }
    for binary in binaries {
        for time_flag in [b"--from=".as_slice(), b"--to=".as_slice()] {
            let output = Command::new(&binary)
                .args([
                    OsString::from("sql"),
                    "run".into(),
                    "-i".into(),
                    OsString::from_vec([time_flag, b"\xff"].concat()),
                ])
                .env("XDG_CONFIG_HOME", &root)
                .env("HOME", &root)
                .env("PB_THEME", "dark")
                .output()
                .unwrap();
            assert_eq!(output.status.code(), Some(1), "{binary:?}");
            let stderr = String::from_utf8_lossy(&output.stderr);
            assert!(
                stderr.contains("unrecognized time format \"\\xff\""),
                "{binary:?}: {stderr}"
            );
        }
        let query = [vec![0xff; 8], b" select * from x".to_vec()].concat();
        let output = Command::new(&binary)
            .args([
                OsString::from("sql"),
                "run".into(),
                "-i".into(),
                OsString::from_vec(query),
            ])
            .env("XDG_CONFIG_HOME", &root)
            .env("HOME", &root)
            .env("PB_THEME", "dark")
            .output()
            .unwrap();
        assert_eq!(
            output.status.code(),
            Some(2),
            "{binary:?}: {:?}",
            output.stderr
        );
        assert!(
            output
                .stderr
                .starts_with(b"panic: runtime error: slice bounds out of range [39:34]\n"),
            "{binary:?}: {:?}",
            output.stderr
        );
    }
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn promql_raw_initial_dataset_reaches_result_and_cache_requests() {
    let mut binaries = vec![OsString::from(env!("CARGO_BIN_EXE_pb"))];
    if let Some(oracle) = std::env::var_os("PB_GO_ORACLE") {
        binaries.push(oracle);
    }
    for binary in binaries {
        let result = Command::new("python3")
            .args(["-c", PROMQL_PTY])
            .arg(&binary)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{binary:?}\n{}\n{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
    }
}

const PROMQL_PTY: &str = r#"
import fcntl, http.server, json, os, pty, select, struct, subprocess, sys, tempfile, termios, threading, time
requests = []
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        if '/query_range?' in self.path:
            body = {'status': 'success', 'data': {'resultType': 'matrix', 'result': []}}
        elif '/label/__name__/values?' in self.path:
            body = {'status': 'success', 'data': ['up']}
        else:
            body = {'datasets': []}
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
    def log_message(self, *args): pass
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory(prefix='pb-raw-promql-') as root:
    os.makedirs(root + '/pb')
    with open(root + '/pb/config.toml', 'w') as f:
        f.write('DefaultProfile = "fixture"\n[Profiles.fixture]\nurl = "http://127.0.0.1:%d"\nusername = "u"\npassword = "p"\ncloud = false\n' % server.server_port)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
    env = dict(os.environ, HOME=root, XDG_CONFIG_HOME=root, PB_THEME='dark', TERM='xterm-256color')
    env.pop('CI', None)
    process = subprocess.Popen([os.fsencode(sys.argv[1]), b'promql', b'run', b'-i', b'up', b'--dataset=m\xff', b'--from=2026-01-01T00:00:00Z', b'--to=2026-01-01T00:10:00Z'], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    output = bytearray()
    def drain():
        if select.select([master], [], [], .02)[0]:
            try: output.extend(os.read(master, 65536))
            except OSError: pass
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            drain()
            result = [p for p in requests if '/query_range?' in p]
            cache = [p for p in requests if '/label/__name__/values?' in p]
            if result and cache: break
            assert process.poll() is None, (process.returncode, output)
        assert result and cache, (requests, output)
        for path in result + cache:
            assert 'stream=m%FF' in path, path
            assert '%EF%BF%BD' not in path, path
        os.write(master, b'\x03')
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline: drain()
        assert process.poll() == 0, (process.poll(), output)
    finally:
        if process.poll() is None: process.kill()
        process.wait()
        os.close(master)
server.shutdown()
"#;
