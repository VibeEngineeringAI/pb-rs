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
    process::{Command, Output, Stdio},
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
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
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
    /// until all USERS detail requests are open at once (or a deadline).
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
        let stop = Arc::new(AtomicBool::new(false));
        let shared = (
            listed.clone(),
            release.clone(),
            arrived.clone(),
            barrier_met.clone(),
            stop.clone(),
        );
        let worker = thread::spawn(move || {
            let (listed, release, arrived, barrier_met, stop) = shared;
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
                let (listed, release, arrived, barrier_met, stop, users) = (
                    listed.clone(),
                    release.clone(),
                    arrived.clone(),
                    barrier_met.clone(),
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
                    let deadline = Instant::now() + Duration::from_secs(5);
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

fn wait(mut child: std::process::Child) -> Output {
    let deadline = Instant::now() + Duration::from_secs(20);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("user list stalled");
        }
        thread::sleep(Duration::from_millis(5));
    }
    child.wait_with_output().unwrap()
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
    let child = command.spawn().unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while !fixture.listed.load(Ordering::SeqCst) {
        assert!(Instant::now() < deadline, "{binary:?}: no user listing");
        thread::sleep(Duration::from_millis(1));
    }
    // Go leaves soft at hard-1 (syscall/rlimit.go); pb mirrors it.
    #[cfg(target_os = "linux")]
    if soft.is_some() {
        let (soft, hard) = process_nofile(child.id()).expect("readable /proc limits");
        assert_eq!(
            soft,
            hard - 1,
            "{binary:?}: soft open-file limit not raised"
        );
    }
    fixture.release.store(true, Ordering::SeqCst);
    let output = wait(child);
    assert!(
        output.status.success(),
        "{binary:?} soft={soft:?}: {}",
        String::from_utf8_lossy(&output.stderr)
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
    // The fixture itself holds one socket per overlapping detail read.
    if limit.rlim_cur < needed {
        let raised = libc::rlimit {
            rlim_cur: limit.rlim_max,
            rlim_max: limit.rlim_max,
        };
        assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &raised) }, 0);
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
