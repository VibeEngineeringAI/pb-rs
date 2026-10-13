#![cfg(all(unix, feature = "diagnostics"))]

//! These captures validate attribution contracts and CLI byte parity, not speed.
use serde_json::Value;
use std::{
    fs,
    io::{Read, Write},
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
    thread,
    time::Duration,
};

static NEXT: AtomicUsize = AtomicUsize::new(0);
const PRIVATE: &str = "private-payload-query-key-marker";

struct Sandbox(PathBuf);
impl Sandbox {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "pb-diagnostic-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(path.join("config/pb")).unwrap();
        Self(path)
    }
    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_pb"));
        command
            .env_clear()
            .env("HOME", &self.0)
            .env("XDG_CONFIG_HOME", self.0.join("config"));
        command
    }
}
impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn sidecar(directory: &Path) -> Value {
    let paths = fs::read_dir(directory)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect::<Vec<_>>();
    assert_eq!(paths.len(), 1);
    let bytes = fs::read(&paths[0]).unwrap();
    assert!(!String::from_utf8_lossy(&bytes).contains(PRIVATE));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&paths[0]).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    serde_json::from_slice(&bytes).unwrap()
}
fn renderer(report: &Value) -> &Value {
    report["threads"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["role"] == "sql_renderer")
        .unwrap()
}
fn stage<'a>(thread: &'a Value, name: &str) -> &'a Value {
    thread["stages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["name"] == name)
        .unwrap()
}

fn sql_capture(
    sandbox: &Sandbox,
    directory: Option<&Path>,
    allocation: bool,
    sample: u64,
) -> (Output, usize) {
    let body = format!(r#"[{{"{PRIVATE}":"{PRIVATE}","n":1}},{{"n":2}},{{"n":3}}]"#);
    let body_length = body.len();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/", listener.local_addr().unwrap());
    fs::write(
        sandbox.0.join("config/pb/config.toml"),
        format!("DefaultProfile='fixture'\n[Profiles.fixture]\nurl='{url}'\napiKey='{PRIVATE}'\n"),
    )
    .unwrap();
    let worker = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut request = Vec::new();
        let mut chunk = [0; 1024];
        loop {
            let length = stream.read(&mut chunk).unwrap();
            assert_ne!(length, 0);
            request.extend_from_slice(&chunk[..length]);
            if let Some(end) = request.windows(4).position(|w| w == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&request[..end]);
                let length = headers
                    .lines()
                    .find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().unwrap())
                    })
                    .unwrap_or(0);
                if request.len() >= end + 4 + length {
                    break;
                }
            }
        }
        let response = format!("{body}unread-private-tail");
        write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\nContent-Type: application/json\r\n\r\n{response}", response.len()).unwrap();
        stream.flush().unwrap();
    });
    let mut command = sandbox.command();
    command
        .args([
            "sql",
            "run",
            &format!("select {PRIVATE} from fixture limit 3"),
            "-ojson",
        ])
        .env("PB_DIAGNOSTICS_SAMPLE_EVERY", sample.to_string());
    if let Some(directory) = directory {
        command.env("PB_DIAGNOSTICS_DIR", directory);
    }
    if allocation {
        command.env("PB_DIAGNOSTICS_ALLOCATIONS", "1");
    }
    let output = command.output().unwrap();
    worker.join().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    (output, body_length)
}

#[test]
fn diagnostic_sidecar_matches_output_counts_sampling_and_allocation_semantics() {
    let sandbox = Sandbox::new();
    let (plain, body_length) = sql_capture(&sandbox, None, false, 1);
    assert!(!sandbox.0.join("none").exists());
    for (name, allocation, sample, timed) in [
        ("timing", false, 1, 3),
        ("sampled", false, 2, 2),
        ("allocations", true, 64, 3),
    ] {
        let directory = sandbox.0.join(name);
        let (output, _) = sql_capture(&sandbox, Some(&directory), allocation, sample);
        assert_eq!(output.stdout, plain.stdout);
        assert_eq!(output.stderr, plain.stderr);
        assert_eq!(output.status, plain.status);
        let report = sidecar(&directory);
        assert_eq!(report["schema_version"], 1);
        assert_eq!(report["complete"], true);
        assert_eq!(
            report["mode"],
            if allocation {
                "timing_allocations"
            } else {
                "timing"
            }
        );
        assert_eq!(
            report["measurement"]["sample_every"],
            if allocation { 1 } else { sample }
        );
        assert_eq!(
            report["measurement"]["thread_cpu_clock"],
            "CLOCK_THREAD_CPUTIME_ID"
        );
        let renderer = renderer(&report);
        assert_eq!(renderer["counters"]["elements"], 3);
        assert_eq!(renderer["counters"]["input_consumed_bytes"], body_length);
        assert_eq!(
            renderer["counters"]["write_completed_bytes"],
            output.stdout.len()
        );
        for name in [
            "ordered_decode",
            "value_conversion",
            "serialization",
            "indentation_copy",
        ] {
            let stage = stage(renderer, name);
            assert_eq!(stage["calls"], 3, "{name}");
            assert_eq!(stage["timed_calls"], timed, "{name}");
            assert!(stage["wall_ns"].as_u64().unwrap() > 0);
            if allocation {
                assert!(
                    stage["allocations"]["alloc_calls"].as_u64().unwrap() > 0,
                    "{name}"
                );
                assert!(
                    stage["allocations"]["alloc_requested_bytes"]
                        .as_u64()
                        .unwrap()
                        > 0,
                    "{name}"
                );
            } else {
                assert!(stage.get("allocations").is_none());
            }
        }
        assert_eq!(
            stage(renderer, "output_write")["calls"],
            renderer["counters"]["write_calls"]
        );
        assert_eq!(
            stage(renderer, "output_flush")["calls"],
            renderer["counters"]["flush_calls"]
        );
        let sum_stage_wall: u64 = renderer["stages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s["wall_ns"].as_u64().unwrap())
            .sum();
        assert!(sum_stage_wall <= renderer["span_wall_ns"].as_u64().unwrap());
        if allocation {
            for counter in [
                "alloc_calls",
                "alloc_requested_bytes",
                "realloc_calls",
                "realloc_requested_bytes",
                "dealloc_calls",
                "dealloc_layout_bytes",
            ] {
                let stage_total: u64 = renderer["stages"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter_map(|s| s["allocations"][counter].as_u64())
                    .sum();
                assert!(
                    stage_total <= renderer["allocations"][counter].as_u64().unwrap(),
                    "{counter}"
                );
            }
        }
    }
}

#[test]
fn diagnostic_failed_sidecar_and_invalid_sampling_leave_offline_contract_unchanged() {
    let sandbox = Sandbox::new();
    let plain = sandbox.command().arg("--version").output().unwrap();
    let directory = sandbox.0.join("not-a-directory");
    fs::write(&directory, b"sentinel").unwrap();
    let failed = sandbox
        .command()
        .arg("--version")
        .env("PB_DIAGNOSTICS_DIR", &directory)
        .output()
        .unwrap();
    assert_eq!(plain.status, failed.status);
    assert_eq!(plain.stdout, failed.stdout);
    assert_eq!(plain.stderr, failed.stderr);
    assert_eq!(fs::read(directory).unwrap(), b"sentinel");
    let directory = sandbox.0.join("invalid-sampling");
    let output = sandbox
        .command()
        .args(["version", &format!("--{PRIVATE}")])
        .env("PB_DIAGNOSTICS_DIR", &directory)
        .env("PB_DIAGNOSTICS_SAMPLE_EVERY", "0")
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert_eq!(sidecar(&directory)["measurement"]["sample_every"], 1);
}
