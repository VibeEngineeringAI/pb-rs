#![cfg(unix)]
//! Real process boundaries: output failure follows commit; response consumption
//! depends on the command. Optional PB_GO_ORACLE checks the same assertions.
use std::{
    fs::{self, File},
    io::{Read, Write},
    net::TcpListener,
    path::PathBuf,
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};
static NEXT: AtomicUsize = AtomicUsize::new(0);

struct Fixture {
    root: PathBuf,
    url: String,
    calls: Arc<Mutex<Vec<String>>>,
    release: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Fixture {
    fn new(stall: bool, password: &[u8]) -> Self {
        let root = std::env::temp_dir().join(format!(
            "pb-admin-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(root.join("config/pb")).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        fs::write(
            root.join("config/pb/config.toml"),
            format!(
                "DefaultProfile='fixture'\n[Profiles.fixture]\nurl='{url}'\napiKey='fixture-key'\n"
            ),
        )
        .unwrap();
        let calls = Arc::new(Mutex::new(Vec::new()));
        let release = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let (record, gate, done) = (calls.clone(), release.clone(), stop.clone());
        let password = password.to_vec();
        let worker = thread::spawn(move || {
            while !done.load(Ordering::SeqCst) {
                let (mut stream, _) = match listener.accept() {
                    Ok(value) => value,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(e) => panic!("accept: {e}"),
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut header = Vec::new();
                let mut byte = [0];
                while !header.ends_with(b"\r\n\r\n") {
                    if stream.read_exact(&mut byte).is_err() {
                        break;
                    }
                    header.push(byte[0]);
                }
                let header = String::from_utf8(header).unwrap();
                let first = header.lines().next().unwrap_or("").to_owned();
                let length = header
                    .lines()
                    .find_map(|line| {
                        line.split_once(':')
                            .filter(|(k, _)| k.eq_ignore_ascii_case("content-length"))
                            .map(|(_, v)| v.trim().parse::<usize>().unwrap())
                    })
                    .unwrap_or(0);
                let mut body = vec![0; length];
                stream.read_exact(&mut body).unwrap();
                record.lock().unwrap().push(first.clone());
                let is_read = first.starts_with("GET ");
                let response: &[u8] = if first.starts_with("GET /api/v1/user ") {
                    b"[]"
                } else if first.starts_with("GET /api/v1/roles ") {
                    br#"{"reader":{}}"#
                } else {
                    &password
                };
                let _ = write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
                    response.len()
                );
                if stall && !is_read {
                    while !gate.load(Ordering::SeqCst) && !done.load(Ordering::SeqCst) {
                        thread::sleep(Duration::from_millis(2));
                    }
                }
                let _ = stream.write_all(response);
            }
        });
        Self {
            root,
            url,
            calls,
            release,
            stop,
            worker: Some(worker),
        }
    }
    fn command(&self, binary: &PathBuf, args: &[&str]) -> Command {
        assert!(self.url.starts_with("http://127.0.0.1:"));
        let mut command = Command::new(binary);
        command
            .env_clear()
            .env("HOME", &self.root)
            .env("XDG_CONFIG_HOME", self.root.join("config"))
            .env("TERM", "dumb")
            .env("PB_THEME", "dark")
            .env("PB_ANALYTICS", "disable")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command
    }
    fn commits(&self) -> usize {
        self.calls
            .lock()
            .unwrap()
            .iter()
            .filter(|r| !r.starts_with("GET "))
            .count()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.release.store(true, Ordering::SeqCst);
        self.worker.take().unwrap().join().unwrap();
        let _ = fs::remove_dir_all(&self.root);
    }
}
fn binaries() -> Vec<PathBuf> {
    let mut out = vec![env!("CARGO_BIN_EXE_pb").into()];
    if let Some(path) = std::env::var_os("PB_GO_ORACLE") {
        out.push(path.into());
    }
    out
}
fn wait(child: &mut std::process::Child) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("administration process stalled");
        }
        thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn status_only_mutations_finish_before_success_body() {
    for binary in binaries() {
        for args in [
            vec!["dataset", "add", "fixture", "--type", "metrics"],
            vec!["dataset", "rm", "fixture"],
            vec!["user", "rm", "fixture"],
            vec!["role", "rm", "reader"],
        ] {
            let fixture = Fixture::new(true, b"unread response");
            let mut child = fixture.command(&binary, &args).spawn().unwrap();
            wait(&mut child);
            let output = child.wait_with_output().unwrap();
            assert!(
                output.status.success(),
                "{binary:?} {args:?}: {:?}",
                output.stderr
            );
            assert_eq!(fixture.commits(), 1);
            assert!(!fixture.release.load(Ordering::SeqCst));
        }
    }
}

#[test]
fn user_creation_awaits_body_and_preserves_password_bytes() {
    for binary in binaries() {
        let fixture = Fixture::new(true, b"secret\xff\0");
        let mut child = fixture
            .command(&binary, &["user", "add", "alice", "--role", "reader"])
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while fixture.commits() == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(fixture.commits(), 1);
        thread::sleep(Duration::from_millis(100));
        assert!(
            child.try_wait().unwrap().is_none(),
            "user creation must consume its body"
        );
        fixture.release.store(true, Ordering::SeqCst);
        wait(&mut child);
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        assert_eq!(
            output.stdout,
            b"Added user: alice \nPassword is: secret\xff\0\nRole(s) assigned: reader\n"
        );
        assert_eq!(fixture.commits(), 1);
    }
}

#[test]
fn full_output_device_keeps_committed_mutations_and_fails_json_encoder() {
    for binary in binaries() {
        for (args, success, commits) in [
            (vec!["user", "add", "alice", "--role", "reader"], true, 1),
            (vec!["dataset", "add", "fixture", "--type", "logs"], true, 1),
            (vec!["user", "list"], true, 0),
            (vec!["user", "list", "-o", "json"], false, 0),
        ] {
            let fixture = Fixture::new(false, b"password");
            let config = fs::read(fixture.root.join("config/pb/config.toml")).unwrap();
            let mut command = fixture.command(&binary, &args);
            command.stdout(File::options().write(true).open("/dev/full").unwrap());
            let mut child = command.spawn().unwrap();
            wait(&mut child);
            let output = child.wait_with_output().unwrap();
            assert_eq!(
                output.status.success(),
                success,
                "{binary:?} {args:?}: {:?}",
                output.stderr
            );
            if !success {
                assert!(
                    String::from_utf8_lossy(&output.stderr)
                        .contains("failed to encode JSON output")
                );
            }
            assert_eq!(fixture.commits(), commits);
            assert_eq!(
                fs::read(fixture.root.join("config/pb/config.toml")).unwrap(),
                config
            );
        }
    }
}
