#![cfg(unix)]
use std::{
    ffi::OsString,
    os::unix::ffi::OsStringExt,
    path::PathBuf,
    process::Command,
    sync::atomic::{AtomicUsize, Ordering},
};
static NEXT: AtomicUsize = AtomicUsize::new(0);
struct Sandbox(PathBuf);
impl Sandbox {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pb-raw-argv-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&root).unwrap();
        Self(root)
    }
    fn run(&self, binary: &std::ffi::OsStr, args: &[Vec<u8>]) -> std::process::Output {
        Command::new(binary)
            .args(args.iter().cloned().map(OsString::from_vec))
            .env("HOME", &self.0)
            .env("XDG_CONFIG_HOME", &self.0)
            .env("PB_THEME", "dark")
            .env("TERM", "dumb")
            .env_remove("PB_COMPLETION_DESCRIPTIONS")
            .env_remove("COBRA_COMPLETION_DESCRIPTIONS")
            .output()
            .unwrap()
    }
    fn config(&self) -> Option<Vec<u8>> {
        std::fs::read(self.0.join("pb/config.toml")).ok()
    }
}
impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn args(words: &[&[u8]]) -> Vec<Vec<u8>> {
    words.iter().map(|w| w.to_vec()).collect()
}
#[test]
fn raw_tokens_keep_quote_and_unquoted_error_semantics() {
    let sandbox = Sandbox::new();
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let output = sandbox.run(binary, &args(&[b"--\xff"]));
    assert!(output.stderr.starts_with(b"Error: unknown flag: --\xff\n"));
    let output = sandbox.run(binary, &args(&[b"version", b"--output=\xe2\x82"]));
    assert!(
        output
            .stderr
            .starts_with(b"Error: unsupported output format \"\\xe2\\x82\"")
    );
    let output = sandbox.run(binary, &args(&[b"__complete", b"sql", b"run", b"--\xff="]));
    assert_eq!(output.stdout, b":0\n");
    assert!(
        output
            .stderr
            .starts_with(b"[Debug] [Error] Subcommand 'run' does not support flag '\xff'\n")
    );
}
#[test]
fn raw_profile_values_survive_storage_and_success_output() {
    let sandbox = Sandbox::new();
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let output = sandbox.run(
        binary,
        &args(&[
            b"profile",
            b"add",
            b"raw\xff",
            b"http://example.test/\xff",
            b"u\xff",
            b"p\xe2\x82",
        ]),
    );
    assert!(output.status.success(), "{:?}", output.stderr);
    assert_eq!(output.stdout, b"Profile raw\xff added successfully\n");
    assert_eq!(sandbox.config().unwrap(),b"DefaultProfile = 'raw\xff'\n\n[Profiles]\n[Profiles.'raw\xff']\nurl = 'http://example.test/%FF'\nusername = 'u\xff'\npassword = 'p\xe2\x82'\ncloud = false\n");
    let output = sandbox.run(binary, &args(&[b"profile", b"list"]));
    assert!(
        output
            .stderr
            .starts_with(b"Error: toml: invalid character\n")
    );
}
#[test]
fn raw_argv_matches_go_oracle() {
    let Some(oracle) = std::env::var_os("PB_GO_ORACLE") else {
        eprintln!("PB_GO_ORACLE is not set; skipping Go raw argv parity replay");
        return;
    };
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let sandbox = Sandbox::new();
    let mut cases = vec![
        args(&[b"\xff"]),
        args(&[b"\xff", b"-ojson"]),
        args(&[b"--\xff"]),
        args(&[b"-\xff"]),
        args(&[b"help", b"\xff"]),
        args(&[b"help", b"\xff", b"-ojson"]),
        args(&[b"version", b"--output=\xff"]),
        args(&[b"__complete", b"sql", b"run", b"--\xff="]),
    ];
    fn walk(node: &pb::spec::CommandSpec, path: &[Vec<u8>], cases: &mut Vec<Vec<Vec<u8>>>) {
        for tail in [
            args(&[b"--\xff"]),
            args(&[b"-\xff"]),
            args(&[b"--help=\xe2\x82"]),
        ] {
            let mut case = path.to_vec();
            case.extend(tail);
            cases.push(case);
        }
        for flag in &node.flags {
            let mut case = path.to_vec();
            case.push([b"--".as_slice(), flag.name.as_bytes(), b"=\xff"].concat());
            case.push(b"--help".to_vec());
            cases.push(case);
        }
        for child in &node.children {
            let mut path = path.to_vec();
            path.push(child.name.as_bytes().to_vec());
            walk(child, &path, cases);
        }
    }
    walk(pb::spec::command_spec(), &[], &mut cases);
    for case in cases {
        let expected = sandbox.run(&oracle, &case);
        let actual = sandbox.run(binary, &case);
        assert_eq!(
            (actual.status.code(), actual.stdout, actual.stderr),
            (expected.status.code(), expected.stdout, expected.stderr),
            "{case:?}"
        );
    }
    for url in [
        b"http://example.test/\xff".as_slice(),
        b"http://example.test/\xff?q=\xe2\x82#\xff",
        b"http://example.test/%\xff",
        b"http://\xff@example.test/",
        b"scheme:raw\xff",
    ] {
        let case = args(&[
            b"profile",
            b"add",
            b"plain",
            url,
            b"u\xff",
            b"p\xe2\x82",
            b"-ojson",
        ]);
        let _ = std::fs::remove_dir_all(sandbox.0.join("pb"));
        let expected = sandbox.run(&oracle, &case);
        let expected_config = sandbox.config();
        let _ = std::fs::remove_dir_all(sandbox.0.join("pb"));
        let actual = sandbox.run(binary, &case);
        assert_eq!(
            (
                actual.status.code(),
                actual.stdout,
                actual.stderr,
                sandbox.config()
            ),
            (
                expected.status.code(),
                expected.stdout,
                expected.stderr,
                expected_config
            ),
            "{case:?}"
        );
    }
    for name in [
        b"raw\xff".as_slice(),
        b"raw\xe2\x82",
        b"raw\xef\xbf\xbd\xff",
    ] {
        let case = args(&[
            b"profile",
            b"add",
            name,
            b"http://example.test/\xff",
            b"u\xff",
            b"p\xe2\x82",
        ]);
        let _ = std::fs::remove_dir_all(sandbox.0.join("pb"));
        let expected = sandbox.run(&oracle, &case);
        let expected_config = sandbox.config();
        let _ = std::fs::remove_dir_all(sandbox.0.join("pb"));
        let actual = sandbox.run(binary, &case);
        assert_eq!(
            (
                actual.status.code(),
                actual.stdout,
                actual.stderr,
                sandbox.config()
            ),
            (
                expected.status.code(),
                expected.stdout,
                expected.stderr,
                expected_config
            )
        );
    }
}

#[test]
fn raw_network_paths_query_values_json_and_styled_names_match_go() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::{Arc, Mutex, atomic::AtomicBool},
        thread,
        time::Duration,
    };
    let Some(oracle) = std::env::var_os("PB_GO_ORACLE") else {
        eprintln!("PB_GO_ORACLE is not set; skipping Go raw argv parity replay");
        return;
    };
    let sandbox = Sandbox::new();
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    std::fs::create_dir_all(sandbox.0.join("pb")).unwrap();
    std::fs::write(
        sandbox.0.join("pb/config.toml"),
        format!("DefaultProfile='test'\n[Profiles.test]\nurl='{url}'\napiKey='test'\n"),
    )
    .unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let calls = Arc::new(Mutex::new(Vec::new()));
    let (done, record) = (stop.clone(), calls.clone());
    let server = thread::spawn(move || {
        while !done.load(Ordering::SeqCst) {
            let (mut socket, _) = match listener.accept() {
                Ok(stream) => stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(1));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            socket
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                head.push(byte[0]);
            }
            let head = String::from_utf8_lossy(&head);
            let first = head.lines().next().unwrap().to_owned();
            let length = head
                .lines()
                .find_map(|line| {
                    line.split_once(':')
                        .filter(|(key, _)| key.eq_ignore_ascii_case("content-length"))
                        .map(|(_, value)| value.trim().parse::<usize>().unwrap())
                })
                .unwrap_or(0);
            let mut body = vec![0; length];
            socket.read_exact(&mut body).unwrap();
            record.lock().unwrap().push((first.clone(), body));
            let response: &[u8] = if first.starts_with("GET /api/v1/user ") {
                b"[]"
            } else if first.starts_with("GET /api/v1/roles ") {
                br#"{"reader":{}}"#
            } else if first.contains("/schema ") {
                br#"{"fields":[]}"#
            } else if first.contains("/prometheus/api/v1/query?") {
                br#"{"status":"success","data":{"resultType":"vector","result":[]}}"#
            } else if first.contains("/status/tsdb?") {
                br#"{"status":"success","data":{"seriesCountByFocusLabelValue":[{"name":"value","value":1}]}}"#
            } else if first.contains("/prometheus/") {
                br#"{"status":"success","data":["value"]}"#
            } else if first.starts_with("POST /api/v1/query ") {
                b"[]"
            } else {
                b"pw"
            };
            write!(
                socket,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                response.len()
            )
            .unwrap();
            socket.write_all(response).unwrap();
        }
    });
    let cases = [
        args(&[b"dataset", b"remove", b"raw\xff"]),
        args(&[b"dataset", b"add", b"raw\xff", b"--type=logs"]),
        args(&[b"dataset", b"schema", b"raw\xff", b"-ojson"]),
        args(&[b"user", b"remove", b"raw\xff"]),
        args(&[b"user", b"add", b"raw\xff", b"--role=reader"]),
        args(&[b"user", b"add", b"raw\xff", b"--role=bad\xe2\x82"]),
        args(&[b"user", b"set-role", b"raw\xff", b"reader"]),
        args(&[b"role", b"remove", b"raw\xff"]),
        args(&[
            b"promql",
            b"run",
            b"up{raw=\"\xff\"}",
            b"--dataset=raw\xff",
            b"--instant",
            b"--to=2020-01-01T00:00:00Z",
            b"-ojson",
        ]),
        args(&[
            b"promql",
            b"label-values",
            b"label\xff",
            b"raw\xff",
            b"-ojson",
        ]),
        args(&[
            b"promql",
            b"series",
            b"raw\xff",
            b"--match=up{raw=\"\xff\"}",
            b"--match=up{raw=\"\xe2\x82\"}",
            b"-ojson",
        ]),
        args(&[b"promql", b"run", b"up", b"--to=bad\xff"]),
        args(&[
            b"sql",
            b"save",
            b"select * from raw\xff",
            b"--from=2020-01-01T00:00:00Z",
            b"--to=2020-01-01T00:00:01Z",
        ]),
        args(&[
            b"sql",
            b"save",
            b"select '\xe2\x82' from raw",
            b"--name=raw\xff",
            b"--from=2020-01-01T00:00:00Z",
            b"--to=2020-01-01T00:00:01Z",
        ]),
        args(&[
            b"sql",
            b"run",
            b"select 1",
            b"--save-as=n",
            b"--from=1\xffm",
            b"-ojson",
        ]),
        args(&[b"promql", b"label-values", b"job\xff", b"ds"]),
        args(&[
            b"sql",
            b"save",
            b"select * from x",
            b"--from=bogus",
            b"--to=\xff",
        ]),
        args(&[
            b"sql",
            b"run",
            b"select 1",
            b"--save-as=n",
            b"--from=bogus",
            b"--to=\xff",
            b"-ojson",
        ]),
        args(&[b"promql", b"tsdb", b"ds", b"--focus-label=job\xff"]),
    ];
    let mut mismatch = None;
    for case in cases {
        let expected = sandbox.run(&oracle, &case);
        let expected_calls = std::mem::take(&mut *calls.lock().unwrap());
        let actual = sandbox.run(binary, &case);
        let actual_calls = std::mem::take(&mut *calls.lock().unwrap());
        let actual = (
            actual.status.code(),
            actual.stdout,
            actual.stderr,
            actual_calls,
        );
        let expected = (
            expected.status.code(),
            expected.stdout,
            expected.stderr,
            expected_calls,
        );
        if actual != expected {
            mismatch = Some((case, actual, expected));
            break;
        }
    }
    stop.store(true, Ordering::SeqCst);
    server.join().unwrap();
    assert!(mismatch.is_none(), "{mismatch:?}");
}
