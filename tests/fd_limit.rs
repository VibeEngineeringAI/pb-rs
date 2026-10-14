#![cfg(unix)]
//! Inventory detail reads hold one connection per entry. Like Go, the process
//! raises its soft RLIMIT_NOFILE at startup, so a low inherited soft limit does
//! not turn a wide `user list` into EMFILE failures. Optional PB_GO_ORACLE
//! checks the same assertions and output.
use std::{
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    os::unix::process::CommandExt,
    path::PathBuf,
    process::{Child, Command, Output, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

const USERS: usize = 200;
const SOFT: libc::rlim_t = 64;
static NEXT: AtomicUsize = AtomicUsize::new(0);

struct Fixture {
    root: PathBuf,
    listed: Arc<AtomicBool>,
    release: Arc<AtomicBool>,
    arrived: Arc<AtomicUsize>,
    barrier_met: Arc<AtomicBool>,
    barrier_expired: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}

/// Kills and reaps the child if a test assertion unwinds before `wait`.
struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        // kill() fails harmlessly if the child already exited.
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn respond(mut stream: TcpStream, body: &[u8]) {
    let _ = write!(
        stream,
        "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(body);
}

impl Fixture {
    /// The user listing waits for `release`; every detail read then waits
    /// until all USERS detail requests are open at once. No detail read is
    /// answered before that, so a read that reaches its deadline first records
    /// the expiry permanently and closes unanswered.
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pb-fdlimit-{}-{}",
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
        let users: Arc<[u8]> = serde_json::to_vec(
            &(0..USERS)
                .map(|i| serde_json::json!({"id": format!("u{i}"), "method": "native"}))
                .collect::<Vec<_>>(),
        )
        .unwrap()
        .into();
        let listed = Arc::new(AtomicBool::new(false));
        let release = Arc::new(AtomicBool::new(false));
        let arrived = Arc::new(AtomicUsize::new(0));
        let barrier_met = Arc::new(AtomicBool::new(false));
        let barrier_expired = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let shared = (
            listed.clone(),
            release.clone(),
            arrived.clone(),
            barrier_met.clone(),
            barrier_expired.clone(),
            stop.clone(),
        );
        let worker = thread::spawn(move || {
            let (listed, release, arrived, barrier_met, barrier_expired, stop) = shared;
            let mut handlers = Vec::new();
            while !stop.load(Ordering::SeqCst) {
                let stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(1));
                        continue;
                    }
                    Err(e) => panic!("accept: {e}"),
                };
                let (listed, release, arrived, barrier_met, barrier_expired, stop, users) = (
                    listed.clone(),
                    release.clone(),
                    arrived.clone(),
                    barrier_met.clone(),
                    barrier_expired.clone(),
                    stop.clone(),
                    users.clone(),
                );
                handlers.push(thread::spawn(move || {
                    let mut stream = stream;
                    stream.set_nonblocking(false).unwrap();
                    stream
                        .set_read_timeout(Some(Duration::from_secs(5)))
                        .unwrap();
                    let mut header = Vec::new();
                    let mut byte = [0];
                    while !header.ends_with(b"\r\n\r\n") {
                        if stream.read_exact(&mut byte).is_err() {
                            return;
                        }
                        header.push(byte[0]);
                    }
                    // Within the 20 s child deadline, generous for slow CI.
                    let deadline = Instant::now() + Duration::from_secs(15);
                    if header.starts_with(b"GET /api/v1/user ") {
                        listed.store(true, Ordering::SeqCst);
                        while !release.load(Ordering::SeqCst) && !stop.load(Ordering::SeqCst) {
                            thread::sleep(Duration::from_millis(1));
                        }
                        respond(stream, &users);
                        return;
                    }
                    if arrived.fetch_add(1, Ordering::SeqCst) + 1 == USERS {
                        barrier_met.store(true, Ordering::SeqCst);
                    }
                    while !barrier_met.load(Ordering::SeqCst)
                        && !stop.load(Ordering::SeqCst)
                        && Instant::now() < deadline
                    {
                        thread::sleep(Duration::from_millis(1));
                    }
                    // Later arrivals must not turn an expired barrier into a
                    // pass: `arrived` counts requests, not concurrent ones.
                    if !barrier_met.load(Ordering::SeqCst) {
                        barrier_expired.store(true, Ordering::SeqCst);
                        return;
                    }
                    respond(
                        stream,
                        br#"{"roles":{"reader":{"actions":[]}},"group_roles":{}}"#,
                    );
                }));
            }
            for handler in handlers {
                let _ = handler.join();
            }
        });
        Self {
            root,
            listed,
            release,
            arrived,
            barrier_met,
            barrier_expired,
            stop,
            worker: Some(worker),
        }
    }

    fn command(&self, binary: &PathBuf) -> Command {
        let mut command = Command::new(binary);
        command
            .env_clear()
            .env("HOME", &self.root)
            .env("XDG_CONFIG_HOME", self.root.join("config"))
            .env("TERM", "dumb")
            .env("PB_THEME", "dark")
            .env("PB_ANALYTICS", "disable")
            .args(["user", "list", "-o", "json"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
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

fn nofile() -> libc::rlimit {
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    assert_eq!(
        unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) },
        0
    );
    limit
}

fn drain(mut reader: impl Read) -> Vec<u8> {
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes).unwrap();
    bytes
}

/// Drains both pipes while polling, so output larger than the pipe capacity
/// (8 KiB under a per-user pipe quota) cannot block the child's exit. Also
/// reports whether the child had to be killed at the deadline.
fn wait(mut child: ChildGuard) -> (Output, bool) {
    let stdout = child.0.stdout.take().unwrap();
    let stderr = child.0.stderr.take().unwrap();
    let stdout = thread::spawn(move || drain(stdout));
    let stderr = thread::spawn(move || drain(stderr));
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut stalled = false;
    let status = loop {
        if let Some(status) = child.0.try_wait().unwrap() {
            break status;
        }
        if Instant::now() >= deadline {
            stalled = true;
            let _ = child.0.kill();
            break child.0.wait().unwrap();
        }
        thread::sleep(Duration::from_millis(5));
    };
    let stdout = stdout.join().unwrap();
    let stderr = stderr.join().unwrap();
    let output = Output {
        status,
        stdout,
        stderr,
    };
    (output, stalled)
}

/// Soft and hard RLIMIT_NOFILE of a running process, from /proc.
#[cfg(target_os = "linux")]
fn process_nofile(pid: u32) -> Option<(u64, u64)> {
    let limits = fs::read_to_string(format!("/proc/{pid}/limits")).ok()?;
    let line = limits.lines().find(|l| l.starts_with("Max open files"))?;
    let mut fields = line["Max open files".len()..].split_whitespace();
    Some((fields.next()?.parse().ok()?, fields.next()?.parse().ok()?))
}

fn run(binary: &PathBuf, soft: Option<libc::rlim_t>) -> Output {
    let fixture = Fixture::new();
    let mut command = fixture.command(binary);
    if let Some(soft) = soft {
        let hard = nofile().rlim_max;
        unsafe {
            command.pre_exec(move || {
                let limit = libc::rlimit {
                    rlim_cur: soft,
                    rlim_max: hard,
                };
                if libc::setrlimit(libc::RLIMIT_NOFILE, &limit) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    // Declared after `fixture`, so an unwinding assertion reaps the child
    // before the fixture's server stops.
    let child = ChildGuard(command.spawn().unwrap());
    let deadline = Instant::now() + Duration::from_secs(10);
    while !fixture.listed.load(Ordering::SeqCst) {
        assert!(Instant::now() < deadline, "{binary:?}: no user listing");
        thread::sleep(Duration::from_millis(1));
    }
    // Go leaves soft at hard-1 (syscall/rlimit.go); pb mirrors it. Without a
    // readable procfs the overlap and output checks below still apply.
    #[cfg(target_os = "linux")]
    if soft.is_some() {
        match process_nofile(child.0.id()) {
            Some((observed, hard)) => assert_eq!(
                observed,
                hard - 1,
                "{binary:?}: soft open-file limit not raised"
            ),
            None => eprintln!("{binary:?}: /proc limits unavailable; skipping that check"),
        }
    }
    fixture.release.store(true, Ordering::SeqCst);
    let (output, stalled) = wait(child);
    let stderr = String::from_utf8_lossy(&output.stderr);
    // Checked first: a client that never overlaps all reads may also stall.
    assert!(
        !fixture.barrier_expired.load(Ordering::SeqCst),
        "{binary:?} soft={soft:?}: detail barrier expired before all {USERS} \
         reads overlapped ({} arrived): {stderr}",
        fixture.arrived.load(Ordering::SeqCst)
    );
    assert!(
        !stalled,
        "{binary:?} soft={soft:?}: user list stalled: {stderr}"
    );
    assert!(
        output.status.success(),
        "{binary:?} soft={soft:?}: {stderr}"
    );
    assert!(
        fixture.barrier_met.load(Ordering::SeqCst),
        "{binary:?}: only {} of {USERS} detail reads overlapped",
        fixture.arrived.load(Ordering::SeqCst)
    );
    output
}

#[test]
fn wide_user_list_outgrows_a_low_inherited_soft_limit() {
    let limit = nofile();
    let needed = USERS as libc::rlim_t + 128;
    if limit.rlim_max < needed {
        eprintln!("skipping: hard RLIMIT_NOFILE {} < {needed}", limit.rlim_max);
        return;
    }
    // The fixture itself holds one socket per overlapping detail read. Ask
    // for a finite soft limit: Apple rejects RLIM_INFINITY with EINVAL.
    if limit.rlim_cur < needed {
        let raised = libc::rlimit {
            rlim_cur: needed,
            rlim_max: limit.rlim_max,
        };
        if unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &raised) } != 0 {
            eprintln!(
                "skipping: cannot raise soft RLIMIT_NOFILE to {needed}: {}",
                std::io::Error::last_os_error()
            );
            return;
        }
    }
    let reference = run(&binaries()[0], None);
    assert!(reference.stdout.starts_with(b"["), "{reference:?}");
    for binary in binaries() {
        let limited = run(&binary, Some(SOFT));
        assert_eq!(
            String::from_utf8_lossy(&limited.stdout),
            String::from_utf8_lossy(&reference.stdout),
            "{binary:?}"
        );
        assert_eq!(limited.stderr, reference.stderr, "{binary:?}");
    }
}
