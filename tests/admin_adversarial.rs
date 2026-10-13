#![cfg(unix)]
//! Adversarial administration inventories beyond the rejected 64-request cap experiment:
//! indexed output, aggregate order, exact request membership, raw error bytes
//! and Go URL joining. Optional PB_GO_ORACLE runs the same assertions on Go.
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::{Command, Output, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::Duration,
};

static NEXT: AtomicUsize = AtomicUsize::new(0);

#[derive(Clone)]
struct Reply {
    status: &'static str,
    body: Vec<u8>,
    delay_ms: u64,
}
fn ok(body: impl Into<Vec<u8>>) -> Reply {
    Reply {
        status: "200 OK",
        body: body.into(),
        delay_ms: 0,
    }
}

type Router = Arc<dyn Fn(&str, &str) -> Reply + Send + Sync>;

struct Fixture {
    root: PathBuf,
    calls: Arc<Mutex<Vec<String>>>,
    peak: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Fixture {
    fn new(router: Router) -> Self {
        let root = std::env::temp_dir().join(format!(
            "pb-admin-adversarial-{}-{}",
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
        let peak = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));
        let (record, high, done) = (calls.clone(), peak.clone(), stop.clone());
        let active = Arc::new(AtomicUsize::new(0));
        let worker = thread::spawn(move || {
            let mut handlers = Vec::new();
            while !done.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        let (router, record, high, active) =
                            (router.clone(), record.clone(), high.clone(), active.clone());
                        handlers.push(thread::spawn(move || {
                            serve(stream, &*router, &record, &high, &active)
                        }));
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(1));
                    }
                    Err(e) => panic!("accept: {e}"),
                }
            }
            for handler in handlers {
                let _ = handler.join();
            }
        });
        Self {
            root,
            calls,
            peak,
            stop,
            worker: Some(worker),
        }
    }
    fn run(&self, binary: &PathBuf, args: &[&str]) -> Output {
        Command::new(binary)
            .env_clear()
            .env("HOME", &self.root)
            .env("XDG_CONFIG_HOME", self.root.join("config"))
            .env("TERM", "dumb")
            .env("PB_ANALYTICS", "disable")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .unwrap()
    }
    fn take_calls(&self) -> Vec<String> {
        std::mem::take(&mut *self.calls.lock().unwrap())
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.worker.take().unwrap().join().unwrap();
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn serve(
    mut stream: TcpStream,
    router: &(dyn Fn(&str, &str) -> Reply + Send + Sync),
    record: &Mutex<Vec<String>>,
    peak: &AtomicUsize,
    active: &AtomicUsize,
) {
    stream.set_nonblocking(false).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .unwrap();
    let mut head = Vec::new();
    let mut byte = [0];
    while !head.ends_with(b"\r\n\r\n") {
        if stream.read_exact(&mut byte).is_err() {
            return;
        }
        head.push(byte[0]);
    }
    let head = String::from_utf8(head).unwrap();
    let length = head
        .lines()
        .find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().unwrap())
        })
        .unwrap_or(0);
    let mut body = vec![0; length];
    stream.read_exact(&mut body).unwrap();
    let mut first = head.lines().next().unwrap_or("").split(' ');
    let (method, path) = (first.next().unwrap_or(""), first.next().unwrap_or(""));
    record.lock().unwrap().push(format!("{method} {path}"));
    let now = active.fetch_add(1, Ordering::SeqCst) + 1;
    peak.fetch_max(now, Ordering::SeqCst);
    let reply = router(method, path);
    thread::sleep(Duration::from_millis(reply.delay_ms));
    let mut response = format!(
        "HTTP/1.1 {}\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
        reply.status,
        reply.body.len()
    )
    .into_bytes();
    response.extend_from_slice(&reply.body);
    active.fetch_sub(1, Ordering::SeqCst);
    let _ = stream.write_all(&response);
}

fn binaries() -> Vec<PathBuf> {
    let mut out = vec![env!("CARGO_BIN_EXE_pb").into()];
    if let Some(path) = std::env::var_os("PB_GO_ORACLE") {
        out.push(path.into());
    }
    out
}

/// Well beyond the provisional Rust cap (64) and not a multiple of it.
const USERS: usize = 300;
fn user(index: usize) -> String {
    format!("u{index:03}")
}
fn failing(index: usize) -> bool {
    index % 37 == 5
}

fn user_router(fail: bool) -> Router {
    let listing = format!(
        "[{}]",
        (0..USERS)
            .map(|i| format!(r#"{{"id":"{}","method":"native"}}"#, user(i)))
            .collect::<Vec<_>>()
            .join(",")
    );
    Arc::new(move |method, path| {
        assert_eq!(method, "GET", "inventories never mutate");
        if path == "/api/v1/user" {
            return ok(listing.clone());
        }
        let index: usize = path
            .strip_prefix("/api/v1/user/u")
            .and_then(|rest| rest.strip_suffix("/role"))
            .and_then(|digits| digits.parse().ok())
            .unwrap_or_else(|| panic!("unexpected request {method} {path}"));
        // Later entries answer first, so completion order inverts index order.
        let delay_ms = ((USERS - index) % 9) as u64 * 3;
        if fail && failing(index) {
            return Reply {
                status: "500 Internal Server Error",
                body: [b" fail\xff".as_slice(), format!("{index} \n").as_bytes()].concat(),
                delay_ms,
            };
        }
        Reply {
            delay_ms,
            ..ok(format!(
                r#"{{"roles":{{"r{index}":{{}}}},"group_roles":{{}}}}"#
            ))
        }
    })
}

fn expected_requests() -> Vec<String> {
    let mut expected: Vec<String> = (0..USERS)
        .map(|i| format!("GET /api/v1/user/{}/role", user(i)))
        .collect();
    expected.sort();
    expected
}

fn assert_requests(calls: Vec<String>) {
    // The listing precedes every detail read; details are an exact multiset.
    assert_eq!(calls[0], "GET /api/v1/user");
    let mut details = calls[1..].to_vec();
    details.sort();
    assert_eq!(details, expected_requests());
}

#[test]
fn large_user_list_keeps_index_order_and_exact_membership() {
    for binary in binaries() {
        let fixture = Fixture::new(user_router(false));
        let output = fixture.run(&binary, &["user", "list", "-o", "json"]);
        assert!(output.status.success(), "{binary:?}: {:?}", output.stderr);
        let rows: Vec<BTreeMap<String, serde_json::Value>> =
            serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(rows.len(), USERS);
        for (index, row) in rows.iter().enumerate() {
            assert_eq!(row["id"], user(index), "{binary:?}");
            assert_eq!(row["roles"], serde_json::json!([format!("r{index}")]));
        }
        assert_requests(fixture.take_calls());
        assert!(
            fixture.peak.load(Ordering::SeqCst) > 1,
            "{binary:?}: detail reads must overlap"
        );
    }
}

#[test]
fn large_user_list_aggregates_failures_in_index_order_with_raw_bytes() {
    let failures: Vec<usize> = (0..USERS).filter(|&i| failing(i)).collect();
    let mut aggregate = format!(
        "Error: failed to fetch roles for {} user(s): ",
        failures.len()
    )
    .into_bytes();
    for (n, &index) in failures.iter().enumerate() {
        if n > 0 {
            aggregate.extend_from_slice(b"; ");
        }
        aggregate.extend_from_slice(
            format!(
                "Error fetching roles for {}: fetch user roles failed: 500 Internal Server Error: fail",
                user(index)
            )
            .as_bytes(),
        );
        aggregate.push(0xff);
        aggregate.extend_from_slice(index.to_string().as_bytes());
    }
    aggregate.push(b'\n');
    for binary in binaries() {
        let fixture = Fixture::new(user_router(true));
        // Text renders every row, failures included, then fails.
        let output = fixture.run(&binary, &["user", "list"]);
        assert_eq!(output.status.code(), Some(1), "{binary:?}");
        assert_eq!(output.stderr, aggregate, "{binary:?}");
        let text = output.stdout;
        let lines: Vec<&[u8]> = text.split(|b| *b == b'\n').collect();
        // Blank line, header, rule, one row per user, blank line, final newline.
        assert_eq!(lines.len(), USERS + 5, "{binary:?}");
        for (index, line) in lines[3..3 + USERS].iter().enumerate() {
            assert!(
                line.starts_with(user(index).as_bytes()),
                "{binary:?} row {index}"
            );
            let tail: &[u8] = if failing(index) {
                &[b'l', 0xff]
            } else {
                b". r"
            };
            assert!(
                line.windows(tail.len()).any(|w| w == tail),
                "{binary:?} row {index}: {:?}",
                String::from_utf8_lossy(line)
            );
        }
        assert_requests(fixture.take_calls());

        // JSON publishes nothing on any failure; the public message is the
        // aggregate with each invalid byte replaced.
        let output = fixture.run(&binary, &["user", "list", "-o", "json"]);
        assert_eq!(output.status.code(), Some(1), "{binary:?}");
        assert!(output.stdout.is_empty(), "{binary:?}");
        let envelope: serde_json::Value = serde_json::from_slice(&output.stderr).unwrap();
        assert_eq!(envelope["error"]["code"], "COMMAND_FAILED");
        let message = envelope["error"]["message"].as_str().unwrap();
        assert_eq!(
            message.as_bytes(),
            String::from_utf8_lossy(&aggregate[b"Error: ".len()..aggregate.len() - 1]).as_bytes()
        );
        assert_requests(fixture.take_calls());
    }
}

#[test]
fn large_role_list_validates_output_before_details_and_sorts_json() {
    const ROLES: usize = 150;
    // Listing order is reverse-sorted; Go map output sorts keys.
    let names: Vec<String> = (0..ROLES).rev().map(|i| format!("role{i:03}")).collect();
    let listing = format!(
        "{{{}}}",
        names
            .iter()
            .map(|n| format!(r#""{n}":{{}}"#))
            .collect::<Vec<_>>()
            .join(",")
    );
    let router: Router = Arc::new(move |method, path| {
        assert_eq!(method, "GET");
        if path == "/api/v1/roles" {
            return ok(listing.clone());
        }
        let index: usize = path
            .strip_prefix("/api/v1/role/role")
            .and_then(|digits| digits.parse().ok())
            .unwrap_or_else(|| panic!("unexpected request {path}"));
        let body = match index % 3 {
            0 => r#"{"actions":null}"#.to_owned(),
            1 => r#"{"actions":[]}"#.to_owned(),
            _ => format!(
                r#"{{"actions":[{{"privilege":"reader","resource":{{"stream":"s{index}","dataset":""}}}}]}}"#
            ),
        };
        Reply {
            delay_ms: (index % 5) as u64 * 2,
            ..ok(body)
        }
    });
    for binary in binaries() {
        let fixture = Fixture::new(router.clone());
        let output = fixture.run(&binary, &["role", "list", "-o", "yaml"]);
        assert_eq!(output.status.code(), Some(1), "{binary:?}");
        assert_eq!(fixture.take_calls(), ["GET /api/v1/roles"], "{binary:?}");

        let output = fixture.run(&binary, &["role", "list", "-o", "json"]);
        assert!(output.status.success(), "{binary:?}: {:?}", output.stderr);
        let text = String::from_utf8(output.stdout).unwrap();
        let document: serde_json::Map<String, serde_json::Value> =
            serde_json::from_str(&text).unwrap();
        let keys: Vec<&String> = document.keys().collect();
        let mut sorted = names.clone();
        sorted.sort();
        assert_eq!(keys, sorted.iter().collect::<Vec<_>>(), "{binary:?}");
        // serde_json::Map preserves document order only with preserve_order,
        // so also check the byte order directly.
        let positions: Vec<usize> = sorted
            .iter()
            .map(|n| text.find(&format!("\"{n}\":")).unwrap())
            .collect();
        assert!(positions.windows(2).all(|w| w[0] < w[1]), "{binary:?}");
        for i in 0..ROLES {
            let value = &document[&format!("role{i:03}")];
            match i % 3 {
                0 => assert!(value.is_null(), "{binary:?} role{i:03}"),
                1 => assert_eq!(value, &serde_json::json!([])),
                _ => assert_eq!(
                    value,
                    &serde_json::json!([{"privilege": "reader", "resource": {"stream": format!("s{i}")}}])
                ),
            }
        }
        let mut calls = fixture.take_calls();
        assert_eq!(calls.remove(0), "GET /api/v1/roles");
        calls.sort();
        let mut expected: Vec<String> = sorted
            .iter()
            .map(|n| format!("GET /api/v1/role/{n}"))
            .collect();
        expected.sort();
        assert_eq!(calls, expected, "{binary:?}");
    }
}

#[test]
fn identifiers_use_go_url_joining_without_new_validation() {
    let router: Router = Arc::new(|_, _| ok(""));
    for binary in binaries() {
        let fixture = Fixture::new(router.clone());
        for (name, request) in [
            ("a b", "DELETE /api/v1/user/a%20b"),
            ("x/../y", "DELETE /api/v1/user/y"),
            ("..", "DELETE /api/v1"),
            ("ü", "DELETE /api/v1/user/%C3%BC"),
        ] {
            let output = fixture.run(&binary, &["user", "rm", name]);
            assert!(
                output.status.success(),
                "{binary:?} {name:?}: {:?}",
                output.stderr
            );
            assert_eq!(
                output.stdout,
                format!("Removed user {name}\n").into_bytes(),
                "{binary:?}"
            );
            assert_eq!(fixture.take_calls(), [request], "{binary:?} {name:?}");
        }
    }
}
