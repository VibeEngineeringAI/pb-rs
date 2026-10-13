#![cfg(unix)]

//! Process-level tests use gated loopback responses: the fixture cannot finish
//! until the test has observed output or verified early command completion.
use std::{
    fs::{self, File},
    io::{self, Read, Write},
    net::{TcpListener, TcpStream},
    os::{
        fd::{FromRawFd, OwnedFd},
        unix::process::ExitStatusExt,
    },
    path::PathBuf,
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

static NEXT: AtomicUsize = AtomicUsize::new(0);
const WAIT: Duration = Duration::from_secs(5);

fn binaries() -> Vec<PathBuf> {
    let mut paths = vec![PathBuf::from(env!("CARGO_BIN_EXE_pb"))];
    if let Some(oracle) = std::env::var_os("PB_GO_ORACLE") {
        paths.push(oracle.into());
    }
    paths
}

struct Sandbox(PathBuf);
impl Sandbox {
    fn new(url: &str) -> Self {
        let path = std::env::temp_dir().join(format!(
            "pb-sql-process-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(path.join("config/pb")).unwrap();
        fs::write(
            path.join("config/pb/config.toml"),
            format!(
                "DefaultProfile='fixture'\n[Profiles.fixture]\nurl='{url}'\napiKey='fixture-key'\n"
            ),
        )
        .unwrap();
        Self(path)
    }
    fn command(&self, binary: &PathBuf, mode: &str) -> Command {
        let mut command = Command::new(binary);
        command
            .env_clear()
            .env("HOME", &self.0)
            .env("XDG_CONFIG_HOME", self.0.join("config"))
            .env("TERM", "xterm")
            .env("PB_ANALYTICS", "disable")
            .env("PB_THEME", "dark")
            .args([
                "sql",
                "run",
                "select * from fixture limit 2",
                "--from=2020-01-01T00:00:00Z",
                "--to=2020-01-01T00:01:00Z",
                "-o",
                mode,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command
    }
}
impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct Fixture {
    url: String,
    ready: mpsc::Receiver<()>,
    release: mpsc::Sender<()>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Fixture {
    fn new(first: Vec<u8>, last: Vec<u8>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let (ready_tx, ready) = mpsc::channel();
        let (release, release_rx) = mpsc::channel();
        let worker = thread::spawn(move || {
            let deadline = Instant::now() + WAIT;
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error)
                        if error.kind() == io::ErrorKind::WouldBlock
                            && Instant::now() < deadline =>
                    {
                        thread::sleep(Duration::from_millis(5))
                    }
                    other => panic!("fixture accept: {other:?}"),
                }
            };
            stream.set_read_timeout(Some(WAIT)).unwrap();
            request(&mut stream);
            stream.write_all(&first).unwrap();
            stream.flush().unwrap();
            ready_tx.send(()).unwrap();
            let _ = release_rx.recv_timeout(WAIT * 3);
            // Early renderer completion may have closed this connection.
            let _ = stream.write_all(&last);
            let _ = stream.flush();
        });
        Self {
            url,
            ready,
            release,
            worker: Some(worker),
        }
    }
    fn ready(&self) {
        self.ready
            .recv_timeout(WAIT)
            .expect("fixture reached response gate");
    }
    fn release(&self) {
        let _ = self.release.send(());
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.release();
        if let Some(worker) = self.worker.take()
            && !thread::panicking()
        {
            worker.join().expect("fixture worker");
        }
    }
}

fn request(stream: &mut TcpStream) {
    let mut request = Vec::new();
    let mut buffer = [0; 1024];
    loop {
        let count = stream.read(&mut buffer).expect("read fixture request");
        assert_ne!(count, 0, "request ended before headers");
        request.extend_from_slice(&buffer[..count]);
        if let Some(end) = request.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
            let header = String::from_utf8_lossy(&request[..end]);
            assert!(
                header.starts_with("POST /api/v1/query HTTP/1.1\r\n"),
                "{header}"
            );
            let length = header
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
                .unwrap_or(0);
            if request.len() >= end + 4 + length {
                return;
            }
        }
    }
}

fn headers(status: &str) -> Vec<u8> {
    format!("HTTP/1.1 {status}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\nContent-Type: application/json\r\n\r\n").into_bytes()
}
fn chunk(bytes: &[u8]) -> Vec<u8> {
    let mut output = format!("{:x}\r\n", bytes.len()).into_bytes();
    output.extend_from_slice(bytes);
    output.extend_from_slice(b"\r\n");
    output
}
fn first_chunk(status: &str, bytes: &[u8]) -> Vec<u8> {
    let mut output = headers(status);
    output.extend(chunk(bytes));
    output
}

struct Capture {
    receiver: mpsc::Receiver<Vec<u8>>,
    bytes: Vec<u8>,
}
impl Capture {
    fn new(mut reader: impl Read + Send + 'static) -> Self {
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let mut bytes = [0; 4096];
            loop {
                match reader.read(&mut bytes) {
                    Ok(0) => break,
                    Ok(count) => {
                        if sender.send(bytes[..count].to_vec()).is_err() {
                            break;
                        }
                    }
                    // A Linux PTY reports EIO after the slave closes.
                    Err(error) if error.raw_os_error() == Some(libc::EIO) => break,
                    Err(error) => panic!("capture read: {error}"),
                }
            }
        });
        Self {
            receiver,
            bytes: Vec::new(),
        }
    }
    fn until(&mut self, fragment: &[u8]) {
        let deadline = Instant::now() + WAIT;
        while !self
            .bytes
            .windows(fragment.len())
            .any(|bytes| bytes == fragment)
        {
            let bytes = self
                .receiver
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .unwrap_or_else(|error| {
                    panic!(
                        "output fragment {:?} before fixture release: {error}; observed {:?}",
                        String::from_utf8_lossy(fragment),
                        self.bytes
                    )
                });
            self.bytes.extend(bytes);
        }
    }
    fn finish(&mut self) -> Vec<u8> {
        loop {
            match self.receiver.recv_timeout(WAIT) {
                Ok(bytes) => self.bytes.extend(bytes),
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    return std::mem::take(&mut self.bytes);
                }
                Err(error) => panic!("capture still open after child exit: {error}"),
            }
        }
    }
}

struct Running {
    child: Child,
    stdout: Option<Capture>,
    stderr: Option<Capture>,
}
impl Running {
    fn spawn(command: &mut Command) -> Self {
        let mut child = command.spawn().unwrap();
        let stdout = child.stdout.take().map(Capture::new);
        let stderr = child.stderr.take().map(Capture::new);
        Self {
            child,
            stdout,
            stderr,
        }
    }
    fn wait(&mut self) -> ExitStatus {
        let deadline = Instant::now() + WAIT;
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status;
            }
            assert!(
                Instant::now() < deadline,
                "SQL process did not exit before fixture release"
            );
            thread::sleep(Duration::from_millis(5));
        }
    }
    fn output(&mut self) -> (ExitStatus, Vec<u8>, Vec<u8>) {
        let status = self.wait();
        let stdout = self
            .stdout
            .as_mut()
            .map(Capture::finish)
            .unwrap_or_default();
        let stderr = self
            .stderr
            .as_mut()
            .map(Capture::finish)
            .unwrap_or_default();
        (status, stdout, stderr)
    }
}
impl Drop for Running {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[test]
fn emits_complete_element_before_fixture_sends_final_chunks() {
    for binary in binaries() {
        for mode in ["text", "json"] {
            let fixture = Fixture::new(first_chunk("200 OK", br#"[{"x":1},"#), {
                let mut bytes = chunk(br#"{"x":2}]"#);
                bytes.extend_from_slice(b"0\r\n\r\n");
                bytes
            });
            let sandbox = Sandbox::new(&fixture.url);
            let mut process = Running::spawn(&mut sandbox.command(&binary, mode));
            fixture.ready();
            process.stdout.as_mut().unwrap().until(if mode == "json" {
                b"\"x\": 1\n  }"
            } else {
                b"\"x\":1}"
            });
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert!(status.success(), "{binary:?}: {stderr:?}");
            assert!(stderr.is_empty());
            assert_eq!(
                stdout,
                if mode == "json" {
                    b"[\n  {\n    \"x\": 1\n  },\n  {\n    \"x\": 2\n  }\n]\n".as_slice()
                } else {
                    b"[{\"x\":1},{\"x\":2}]\n"
                }
            );
        }
    }
}

#[test]
fn inherited_nonblocking_stdout_waits_for_reader_without_losing_bytes() {
    use std::os::unix::process::CommandExt;
    for binary in binaries() {
        for mode in ["text", "json"] {
            let value = "x".repeat(256 * 1024);
            let body = format!("[{{\"message\":\"{value}\"}}]");
            let fixture = Fixture::new(
                first_chunk("200 OK", body.as_bytes()),
                b"0\r\n\r\n".to_vec(),
            );
            let sandbox = Sandbox::new(&fixture.url);
            let mut command = sandbox.command(&binary, mode);
            unsafe {
                command.pre_exec(|| {
                    let flags = libc::fcntl(libc::STDOUT_FILENO, libc::F_GETFL);
                    if flags < 0
                        || libc::fcntl(libc::STDOUT_FILENO, libc::F_SETFL, flags | libc::O_NONBLOCK)
                            < 0
                    {
                        return Err(io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
            let mut child = command.spawn().unwrap();
            fixture.ready();
            fixture.release();
            thread::sleep(Duration::from_millis(100));
            let stdout = child.stdout.take().map(Capture::new);
            let stderr = child.stderr.take().map(Capture::new);
            let mut process = Running {
                child,
                stdout,
                stderr,
            };
            let (status, stdout, stderr) = process.output();
            assert!(
                status.success(),
                "{binary:?} {mode}: {}",
                String::from_utf8_lossy(&stderr)
            );
            assert!(stderr.is_empty());
            let expected = if mode == "json" {
                format!("[\n  {{\n    \"message\": \"{value}\"\n  }}\n]\n")
            } else {
                format!("{body}\n")
            };
            assert_eq!(stdout.len(), expected.len(), "{binary:?} {mode}");
            assert_eq!(stdout, expected.as_bytes());
        }
    }
}

#[test]
fn json_finishes_with_unread_http_tail_still_gated() {
    for binary in binaries() {
        let fixture = Fixture::new(first_chunk("200 OK", b"[1]"), b"0\r\n\r\n".to_vec());
        let sandbox = Sandbox::new(&fixture.url);
        let mut process = Running::spawn(&mut sandbox.command(&binary, "json"));
        fixture.ready();
        let (status, stdout, stderr) = process.output();
        assert!(status.success(), "{binary:?}: {stderr:?}");
        assert_eq!(stdout, b"[\n  1\n]\n");
        assert!(stderr.is_empty());
        fixture.release();
    }
}

#[test]
fn text_empty_array_waits_for_http_eof() {
    for binary in binaries() {
        let fixture = Fixture::new(first_chunk("200 OK", b"[]"), b"0\r\n\r\n".to_vec());
        let sandbox = Sandbox::new(&fixture.url);
        let mut process = Running::spawn(&mut sandbox.command(&binary, "text"));
        fixture.ready();
        assert!(matches!(
            process
                .stdout
                .as_mut()
                .unwrap()
                .receiver
                .recv_timeout(Duration::from_millis(200)),
            Err(mpsc::RecvTimeoutError::Timeout)
        ));
        assert!(
            process.child.try_wait().unwrap().is_none(),
            "text empty array exited before EOF"
        );
        fixture.release();
        let (status, stdout, stderr) = process.output();
        assert!(status.success(), "{binary:?}: {stderr:?}");
        assert_eq!(
            stdout,
            b"Query succeeded: no rows returned (status: 200 OK).\n"
        );
        assert!(stderr.is_empty());
    }
}

#[test]
fn preserves_invalid_utf8_in_stdout_and_error_preview() {
    for binary in binaries() {
        for (status, body, expected_stdout, expected_stderr) in [
            (
                "200 OK",
                b"a\xff\xfe".as_slice(),
                b"a\xff\xfe\n".as_slice(),
                b"".as_slice(),
            ),
            (
                "500 Broken",
                b" \xff\xc3\n",
                b"",
                b"Error: query failed: server returned 500 Broken: \xff\xc3\n",
            ),
        ] {
            let fixture = Fixture::new(first_chunk(status, body), b"0\r\n\r\n".to_vec());
            let sandbox = Sandbox::new(&fixture.url);
            let mut process = Running::spawn(&mut sandbox.command(&binary, "text"));
            fixture.ready();
            fixture.release();
            let (exit, stdout, stderr) = process.output();
            assert_eq!(
                exit.code(),
                Some(if status.starts_with("200") { 0 } else { 1 }),
                "{binary:?}"
            );
            assert_eq!(stdout, expected_stdout, "{binary:?}");
            assert_eq!(stderr, expected_stderr, "{binary:?}");
        }
    }
}

#[test]
fn error_preview_finishes_at_cap_before_http_eof() {
    for binary in binaries() {
        let fixture = Fixture::new(
            first_chunk("500 Broken", &vec![b'x'; 65_537]),
            b"0\r\n\r\n".to_vec(),
        );
        let sandbox = Sandbox::new(&fixture.url);
        let mut process = Running::spawn(&mut sandbox.command(&binary, "text"));
        fixture.ready();
        let (status, stdout, stderr) = process.output();
        assert_eq!(status.code(), Some(1), "{binary:?}");
        assert!(stdout.is_empty());
        let mut expected = b"Error: query failed: server returned 500 Broken: ".to_vec();
        expected.extend(vec![b'x'; 65_536]);
        expected.extend_from_slice(b"...\n");
        assert_eq!(stderr, expected, "{binary:?}");
        fixture.release();
    }
}

fn closed_pipe() -> Stdio {
    let mut descriptors = [-1; 2];
    // SAFETY: both descriptors become owned exactly once; the reader is closed.
    unsafe {
        assert_eq!(libc::pipe(descriptors.as_mut_ptr()), 0);
        drop(OwnedFd::from_raw_fd(descriptors[0]));
        Stdio::from(OwnedFd::from_raw_fd(descriptors[1]))
    }
}

#[test]
fn sql_closed_stdout_terminates_with_real_sigpipe() {
    for binary in binaries() {
        for mode in ["text", "json"] {
            let fixture = Fixture::new(first_chunk("200 OK", b"[1]"), b"0\r\n\r\n".to_vec());
            let sandbox = Sandbox::new(&fixture.url);
            let mut command = sandbox.command(&binary, mode);
            command.stdout(closed_pipe());
            let mut process = Running::spawn(&mut command);
            fixture.ready();
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert_eq!(
                status.signal(),
                Some(libc::SIGPIPE),
                "{binary:?}/{mode}: {stderr:?}"
            );
            assert!(stdout.is_empty());
            assert!(stderr.is_empty());
        }
    }
}

#[cfg(target_os = "linux")]
#[test]
fn dev_full_errors_follow_text_and_json_writer_policies() {
    let mut paths = binaries();
    // Check the optional reference first so a candidate mismatch is preceded
    // by verification of the exact expected bytes against Go.
    paths.reverse();
    for binary in paths {
        for (mode, structured) in [("text", false), ("text", true), ("json", false)] {
            let fixture = Fixture::new(first_chunk("200 OK", b"[1]"), b"0\r\n\r\n".to_vec());
            let sandbox = Sandbox::new(&fixture.url);
            let mut command = sandbox.command(&binary, mode);
            if structured {
                // The last format controls the renderer; the raw JSON request
                // scan independently controls the command error envelope.
                command.args(["-ojson", "-otext"]);
            }
            command.stdout(File::options().write(true).open("/dev/full").unwrap());
            let mut process = Running::spawn(&mut command);
            fixture.ready();
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert!(stdout.is_empty());
            if mode == "json" {
                assert!(status.success(), "{binary:?}: {stderr:?}");
                assert!(stderr.is_empty());
            } else {
                assert_eq!(status.code(), Some(1), "{binary:?}");
                let message =
                    "failed to stream response body: write /dev/stdout: no space left on device";
                let expected = if structured {
                    format!(
                        "{{\n  \"error\": {{\n    \"code\": \"CONNECTION_FAILED\",\n    \"message\": \"{message}\",\n    \"retryable\": true\n  }}\n}}\n"
                    )
                } else {
                    format!("Error: {message}\n")
                };
                assert_eq!(
                    String::from_utf8(stderr).unwrap(),
                    expected,
                    "{binary:?}/structured={structured}"
                );
            }
        }
    }
}

#[test]
fn read_only_stdout_follows_text_and_json_writer_policies() {
    let mut paths = binaries();
    paths.reverse(); // Validate the expected contract against Go first when present.
    for mode in ["text", "json"] {
        let mut reference = None;
        for binary in &paths {
            let fixture = Fixture::new(first_chunk("200 OK", b"[1]"), b"0\r\n\r\n".to_vec());
            let sandbox = Sandbox::new(&fixture.url);
            let mut command = sandbox.command(binary, mode);
            command.stdout(File::open("/dev/null").unwrap());
            let mut process = Running::spawn(&mut command);
            fixture.ready();
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert!(stdout.is_empty());
            assert_eq!(status.signal(), None, "{binary:?}/{mode}");
            if mode == "json" {
                assert_eq!(status.code(), Some(0), "{binary:?}: {stderr:?}");
                assert!(stderr.is_empty(), "{binary:?}: {stderr:?}");
            } else {
                assert_eq!(status.code(), Some(1), "{binary:?}");
                assert_eq!(
                    stderr,
                    b"Error: failed to stream response body: write /dev/stdout: bad file descriptor\n",
                    "{binary:?}"
                );
            }
            let observed = (status.code(), status.signal(), stderr);
            if let Some(expected) = &reference {
                assert_eq!(&observed, expected, "{binary:?}/{mode}: Go comparison");
            } else {
                reference = Some(observed);
            }
        }
    }
}

#[test]
fn shared_stdout_stderr_pipe_keeps_completed_json_before_decode_error() {
    let mut paths = binaries();
    paths.reverse();
    let mut reference = None;
    let prefix = b"[\n  {\n    \"x\": 1\n  }";
    let mut expected = prefix.to_vec();
    expected.extend_from_slice(
        b"{\n  \"error\": {\n    \"code\": \"COMMAND_FAILED\",\n    \"message\": \"error decoding JSON response: invalid character 'o' looking for beginning of value\",\n    \"retryable\": false\n  }\n}\n",
    );
    for binary in paths {
        let mut tail = chunk(b"oops]");
        tail.extend_from_slice(b"0\r\n\r\n");
        let fixture = Fixture::new(first_chunk("200 OK", br#"[{"x":1},"#), tail);
        let sandbox = Sandbox::new(&fixture.url);
        let mut descriptors = [-1; 2];
        // SAFETY: successful pipe descriptors are owned once, and close-on-exec
        // leaves only the explicitly configured stdout/stderr copies in the child.
        let (reader, writer) = unsafe {
            assert_eq!(libc::pipe(descriptors.as_mut_ptr()), 0);
            let reader = File::from_raw_fd(descriptors[0]);
            let writer = File::from_raw_fd(descriptors[1]);
            for descriptor in descriptors {
                assert_eq!(libc::fcntl(descriptor, libc::F_SETFD, libc::FD_CLOEXEC), 0);
            }
            (reader, writer)
        };
        let mut combined = Capture::new(reader);
        let mut command = sandbox.command(&binary, "json");
        command.stdout(writer.try_clone().unwrap()).stderr(writer);
        let mut process = Running::spawn(&mut command);
        drop(command); // Close both parent writer copies so capture can reach EOF.
        fixture.ready();
        combined.until(prefix);
        fixture.release();
        let (status, stdout, stderr) = process.output();
        assert!(stdout.is_empty());
        assert!(stderr.is_empty());
        assert_eq!(status.code(), Some(1), "{binary:?}");
        assert_eq!(status.signal(), None, "{binary:?}");
        let bytes = combined.finish();
        assert_eq!(bytes, expected, "{binary:?}");
        let observed = (status.code(), status.signal(), bytes);
        if let Some(expected) = &reference {
            assert_eq!(&observed, expected, "{binary:?}: Go comparison");
        } else {
            reference = Some(observed);
        }
    }
}

#[test]
fn closed_stdout_special_messages_still_raise_sigpipe() {
    for binary in binaries() {
        for body in [b" \t\r\n".as_slice(), b"[]"] {
            let fixture = Fixture::new(first_chunk("200 OK", body), b"0\r\n\r\n".to_vec());
            let sandbox = Sandbox::new(&fixture.url);
            let mut command = sandbox.command(&binary, "text");
            command.stdout(closed_pipe());
            let mut process = Running::spawn(&mut command);
            fixture.ready();
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert_eq!(
                status.signal(),
                Some(libc::SIGPIPE),
                "{binary:?}/{body:?}: {stderr:?}"
            );
            assert!(stdout.is_empty());
            assert!(stderr.is_empty());
        }
    }
}

#[test]
fn external_signals_terminate_header_and_body_waits_without_error_envelopes() {
    for binary in binaries() {
        for mode in ["text", "json"] {
            for waiting_for_body in [false, true] {
                for signal in [libc::SIGINT, libc::SIGTERM] {
                    let first = if waiting_for_body {
                        first_chunk("200 OK", br#"[{"x":"#)
                    } else {
                        Vec::new()
                    };
                    let fixture = Fixture::new(first, Vec::new());
                    let sandbox = Sandbox::new(&fixture.url);
                    let mut process = Running::spawn(&mut sandbox.command(&binary, mode));
                    fixture.ready();
                    if waiting_for_body {
                        // This output proves headers were received and the
                        // renderer entered its wait for the unfinished body.
                        process.stdout.as_mut().unwrap().until(if mode == "json" {
                            b"["
                        } else {
                            br#"[{"x":"#
                        });
                    }
                    // SAFETY: the guarded child is still owned by this test;
                    // kill targets its exact PID and installs no handler.
                    unsafe {
                        assert_eq!(libc::kill(process.child.id() as libc::pid_t, signal), 0);
                    }
                    let (status, stdout, stderr) = process.output();
                    assert_eq!(
                        status.signal(),
                        Some(signal),
                        "{binary:?}/{mode}/body={waiting_for_body}: {status:?}"
                    );
                    assert!(
                        stderr.is_empty(),
                        "{binary:?}/{mode}/body={waiting_for_body}: {stderr:?}"
                    );
                    if waiting_for_body {
                        assert_eq!(
                            stdout,
                            if mode == "json" {
                                b"[".as_slice()
                            } else {
                                br#"[{"x":"#
                            },
                            "{binary:?}"
                        );
                    } else {
                        assert!(stdout.is_empty(), "{binary:?}: {stdout:?}");
                    }
                    fixture.release();
                }
            }
        }
    }
}

#[cfg(target_os = "linux")]
#[test]
fn dev_full_special_text_messages_ignore_write_errors() {
    for binary in binaries() {
        for body in [b" \t\r\n".as_slice(), b"[]"] {
            let fixture = Fixture::new(first_chunk("200 OK", body), b"0\r\n\r\n".to_vec());
            let sandbox = Sandbox::new(&fixture.url);
            let mut command = sandbox.command(&binary, "text");
            command.stdout(File::options().write(true).open("/dev/full").unwrap());
            let mut process = Running::spawn(&mut command);
            fixture.ready();
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert!(status.success(), "{binary:?}/{body:?}: {stderr:?}");
            assert!(stdout.is_empty());
            assert!(stderr.is_empty());
        }
    }
}

#[cfg(target_os = "linux")]
#[test]
fn terminal_spinner_starts_before_headers_and_clears_on_completion() {
    for binary in binaries() {
        let mut final_response = first_chunk("200 OK", b"[]");
        final_response.extend_from_slice(b"0\r\n\r\n");
        let fixture = Fixture::new(Vec::new(), final_response);
        let sandbox = Sandbox::new(&fixture.url);
        let (mut master, mut slave) = (-1, -1);
        // SAFETY: null termios/winsize select defaults. On success both file
        // descriptors are transferred into File exactly once.
        let (master, slave) = unsafe {
            assert_eq!(
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    std::ptr::null()
                ),
                0
            );
            (File::from_raw_fd(master), File::from_raw_fd(slave))
        };
        let mut terminal = Capture::new(master);
        let mut command = sandbox.command(&binary, "json");
        command.stderr(slave);
        let mut process = Running::spawn(&mut command);
        drop(command); // Release the parent's copy of the PTY slave.
        fixture.ready();
        terminal.until(b" fetching...");
        fixture.release();
        let (status, stdout, stderr) = process.output();
        assert!(status.success(), "{binary:?}: {stderr:?}");
        assert_eq!(stdout, b"[]\n");
        let terminal = terminal.finish();
        assert!(terminal.ends_with(b"\r\x1b[K"), "{binary:?}: {terminal:?}");
    }
}

#[test]
fn conversion_errors_cleanup_near_limit_depth_without_recursive_drop() {
    // Cover both unconverted AST and converted values near Go's depth limit.
    let deep = format!("{}0{}", "[".repeat(9_990), "]".repeat(9_990));
    for body in [format!("[[1e1000,{deep}]]"), format!("[[{deep},1e1000]]")] {
        let mut reference = None;
        for binary in binaries() {
            let fixture = Fixture::new(
                first_chunk("200 OK", body.as_bytes()),
                b"0\r\n\r\n".to_vec(),
            );
            let sandbox = Sandbox::new(&fixture.url);
            let mut process = Running::spawn(&mut sandbox.command(&binary, "json"));
            fixture.ready();
            fixture.release();
            let (status, stdout, stderr) = process.output();
            assert_eq!(status.code(), Some(1), "{binary:?}: {status:?}");
            assert_eq!(stdout, b"[");
            let error: serde_json::Value = serde_json::from_slice(&stderr).unwrap();
            assert_eq!(error["error"]["code"], "COMMAND_FAILED");
            assert!(
                error["error"]["message"]
                    .as_str()
                    .unwrap()
                    .contains("cannot unmarshal number 1e1000")
            );
            if let Some(expected) = &reference {
                assert_eq!(&stderr, expected, "{binary:?}");
            } else {
                reference = Some(stderr);
            }
        }
    }
}
