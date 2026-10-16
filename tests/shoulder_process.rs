//! Exercise the durable ingestion outbox and observation receipts through the
//! real executable. Both child processes and loopback requests have deadlines.
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::{self, Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Output, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

static NEXT: AtomicUsize = AtomicUsize::new(0);
const DEADLINE: Duration = Duration::from_secs(10);

struct Sandbox(PathBuf);
impl Sandbox {
    fn new(fixture: &Fixture) -> Self {
        let path = std::env::temp_dir().join(format!(
            "pb-shoulder-process-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(path.join("config/pb")).unwrap();
        fs::write(
            path.join("config/pb/config.toml"),
            format!(
                "DefaultProfile='fixture'\n[Profiles.fixture]\nurl='{}'\napiKey='fixture-key'\n",
                fixture.url
            ),
        )
        .unwrap();
        Self(path)
    }

    fn command(&self, arguments: &[&str]) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_pb"));
        command
            .env_clear()
            .env("HOME", &self.0)
            .env("XDG_CONFIG_HOME", self.0.join("config"))
            .env("PB_ANALYTICS", "disable")
            .current_dir(&self.0)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .args(arguments);
        command
    }

    fn run(&self, arguments: &[&str]) -> Output {
        Running(Some(self.command(arguments).spawn().unwrap())).finish()
    }

    fn ingest(&self, arguments: &[&str]) -> Output {
        let mut args = vec![
            "ingest",
            "files",
            "*.log",
            "--dataset",
            "fixture",
            "--session",
            "run-1",
            "--state-file",
            "ingest.json",
            "--from",
            "beginning",
            "-o",
            "json",
        ];
        args.extend_from_slice(arguments);
        self.run(&args)
    }

    fn observe(&self, cursor: &str, arguments: &[&str]) -> Output {
        let mut args = vec![
            "observe",
            "fixture",
            "--session",
            "run-1",
            "--cursor-file",
            cursor,
        ];
        args.extend_from_slice(arguments);
        self.run(&args)
    }

    fn write(&self, name: &str, bytes: &[u8]) {
        fs::write(self.0.join(name), bytes).unwrap();
    }

    fn append(&self, name: &str, bytes: &[u8]) {
        OpenOptions::new()
            .append(true)
            .open(self.0.join(name))
            .unwrap()
            .write_all(bytes)
            .unwrap();
    }

    fn state(&self, name: &str) -> Value {
        serde_json::from_slice(&fs::read(self.0.join(name)).unwrap()).unwrap()
    }
}
impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct Running(Option<Child>);
impl Running {
    fn finish(mut self) -> Output {
        let deadline = Instant::now() + DEADLINE;
        loop {
            if self.0.as_mut().unwrap().try_wait().unwrap().is_some() {
                return self.0.take().unwrap().wait_with_output().unwrap();
            }
            assert!(Instant::now() < deadline, "pb process exceeded deadline");
            thread::sleep(Duration::from_millis(5));
        }
    }
}
impl Drop for Running {
    fn drop(&mut self) {
        if let Some(child) = &mut self.0 {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[derive(Clone, Debug)]
struct Request {
    first_line: String,
    headers: String,
    body: Vec<u8>,
}

#[derive(Default)]
struct ServerState {
    requests: Vec<Request>,
    rows: Vec<Value>,
    fail_uploads: usize,
    upload_row_limit: usize,
    fail_queries: bool,
    query_override: Option<Value>,
}

struct Fixture {
    url: String,
    state: Arc<Mutex<ServerState>>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Fixture {
    fn new() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}/", listener.local_addr().unwrap());
        let state = Arc::new(Mutex::new(ServerState::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_state = state.clone();
        let worker_stop = stop.clone();
        let worker = thread::spawn(move || {
            while !worker_stop.load(Ordering::Relaxed) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream
                            .set_read_timeout(Some(Duration::from_secs(2)))
                            .unwrap();
                        stream
                            .set_write_timeout(Some(Duration::from_secs(2)))
                            .unwrap();
                        let request = read_request(&mut stream);
                        let (status, body) = {
                            let mut state = worker_state.lock().unwrap();
                            state.requests.push(request.clone());
                            if request.first_line.contains("/ingest ") {
                                let rows: Vec<Value> =
                                    serde_json::from_slice(&request.body).unwrap();
                                if state.upload_row_limit != 0
                                    && rows.len() > state.upload_row_limit
                                {
                                    (413, b"fixture batch too large".to_vec())
                                } else if state.fail_uploads != 0 {
                                    state.fail_uploads -= 1;
                                    (503, b"retry fixture".to_vec())
                                } else {
                                    state.rows.extend(rows);
                                    (200, b"{}".to_vec())
                                }
                            } else if request.first_line.contains("/query ") {
                                if state.fail_queries {
                                    (503, b"query fixture unavailable".to_vec())
                                } else if let Some(response) = &state.query_override {
                                    (200, serde_json::to_vec(response).unwrap())
                                } else {
                                    (200, query_response(&state.rows, &request.body))
                                }
                            } else {
                                panic!("unexpected request: {}", request.first_line);
                            }
                        };
                        // A child killed by a failing assertion may close its socket.
                        let _ = write!(
                            stream,
                            "HTTP/1.1 {status} fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        );
                        let _ = stream.write_all(&body);
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(3));
                    }
                    Err(error) => panic!("fixture accept: {error}"),
                }
            }
        });
        Self {
            url,
            state,
            stop,
            worker: Some(worker),
        }
    }

    fn rows(&self) -> Vec<Value> {
        self.state.lock().unwrap().rows.clone()
    }

    fn uploads(&self) -> Vec<Request> {
        self.state
            .lock()
            .unwrap()
            .requests
            .iter()
            .filter(|request| request.first_line.contains("/ingest "))
            .cloned()
            .collect()
    }

    fn query_count(&self) -> usize {
        self.state
            .lock()
            .unwrap()
            .requests
            .iter()
            .filter(|request| request.first_line.contains("/query "))
            .count()
    }

    fn wait_for_rows(&self, count: usize) {
        let deadline = Instant::now() + DEADLINE;
        while self.rows().len() < count {
            assert!(
                Instant::now() < deadline,
                "fixture did not receive {count} rows"
            );
            assert!(
                !self.worker.as_ref().unwrap().is_finished(),
                "fixture worker exited"
            );
            thread::sleep(Duration::from_millis(5));
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        // Preserve an existing assertion failure instead of double panicking.
        if let Some(worker) = self.worker.take() {
            let result = worker.join();
            if !thread::panicking() {
                result.unwrap();
            }
        }
    }
}

fn read_request(stream: &mut TcpStream) -> Request {
    let mut request = Vec::new();
    let mut chunk = [0; 8192];
    loop {
        let n = stream
            .read(&mut chunk)
            .expect("read HTTP request before deadline");
        assert_ne!(n, 0, "request closed before complete body");
        request.extend_from_slice(&chunk[..n]);
        assert!(request.len() <= 17 * 1024 * 1024, "unbounded request");
        if let Some(end) = request.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&request[..end]).into_owned();
            let length = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
                .unwrap_or(0);
            if request.len() >= end + 4 + length {
                return Request {
                    first_line: headers.lines().next().unwrap().into(),
                    headers,
                    body: request[end + 4..end + 4 + length].to_vec(),
                };
            }
        }
    }
}

fn query_response(rows: &[Value], body: &[u8]) -> Vec<u8> {
    let body: Value = serde_json::from_slice(body).unwrap();
    let sql = body["query"].as_str().expect("query SQL");
    let compact = sql.replace('"', "").to_ascii_lowercase();
    let after = compact
        .split("pb_seq >=")
        .nth(1)
        .or_else(|| compact.split("pb_seq>=").nth(1))
        .expect("observation query must request an inclusive sequence bound");
    let from: u64 = after
        .trim_start()
        .split(|c: char| !c.is_ascii_digit())
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let limit: usize = compact
        .rsplit("limit ")
        .next()
        .unwrap()
        .trim()
        .trim_end_matches(';')
        .parse()
        .unwrap();
    let mut result = rows
        .iter()
        .filter(|row| row["pb_seq"].as_u64().unwrap() >= from)
        .cloned()
        .collect::<Vec<_>>();
    result.sort_by_key(|row| row["pb_seq"].as_u64().unwrap());
    if compact.contains("select distinct ") {
        result.dedup();
    }
    result.truncate(limit);
    // Parseable 3.2.4 materializes these numeric columns as integral floats.
    for row in &mut result {
        for field in ["pb_seq", "pb_source_seq"] {
            row[field] = json!(row[field].as_u64().unwrap() as f64);
        }
    }
    serde_json::to_vec(&result).unwrap()
}

fn success(output: &Output) -> Value {
    assert!(
        output.status.success(),
        "status {}; stderr: {}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!(
            "invalid JSON stdout: {error}; {}",
            String::from_utf8_lossy(&output.stdout)
        )
    })
}

#[test]
fn broad_glob_excludes_own_state_lock_and_abandoned_temporary_files() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("app.log", b"one\ntwo\n");
    sandbox.write(".pb-state-abandoned.tmp", b"unfinished checkpoint");
    let output = sandbox.run(&[
        "ingest",
        "files",
        "*",
        "--dataset",
        "fixture",
        "--session",
        "run-1",
        "--state-file",
        "ingest.json",
        "--from",
        "beginning",
        "--complete",
        "-o",
        "json",
    ]);
    assert_eq!(success(&output)["ingested"], 2);
    let rows = fixture.rows();
    assert_eq!(rows.len(), 3);
    assert_eq!(rows.last().unwrap()["pb_kind"], "complete");
}

#[test]
fn rejected_large_batch_splits_without_changing_event_identities() {
    let fixture = Fixture::new();
    fixture.state.lock().unwrap().upload_row_limit = 2;
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("app.log", b"one\ntwo\nthree\nfour\nfive\n");
    assert_eq!(success(&sandbox.ingest(&["--complete"]))["ingested"], 5);
    let uploads = fixture.uploads();
    assert!(uploads.len() > 3);
    let original: Vec<Value> = serde_json::from_slice(&uploads[0].body).unwrap();
    assert_eq!(original.len(), 5);
    let rows = fixture.rows();
    assert_eq!(&rows[..5], original.as_slice());
    assert_eq!(rows[5]["pb_seq"], 6);
    assert_eq!(rows[5]["pb_kind"], "complete");
    assert!(sandbox.state("ingest.json")["pending"].is_null());
}

fn failure(output: &Output) {
    assert!(
        !output.status.success(),
        "unexpected success: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(
        output.stdout.is_empty(),
        "error leaked stdout: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(!output.stderr.is_empty(), "failure lacked diagnostic");
}

fn event(sequence: u64, payload: Value) -> Value {
    json!({
        "pb_session": "run-1", "pb_collector": "collector-a", "pb_seq": sequence,
        "pb_event_id": format!("collector-a:{sequence}"), "pb_kind": "event",
        "pb_source": "/fixture/events.log", "pb_generation": "generation-a",
        "pb_source_seq": sequence, "pb_host": "fixture-host",
        "pb_collected_at": "2026-10-15T12:00:00Z", "pb_payload": payload.to_string(),
    })
}

#[test]
fn ingest_jsonl_snapshot_append_restart_has_no_duplicates() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write(
        "events.log",
        b"{\"message\":\"one\",\"pb_seq\":999}\n{\"nested\":{\"n\":2}}\n",
    );
    assert_eq!(
        success(&sandbox.ingest(&["--format", "jsonl"]))["ingested"],
        2
    );
    let initial = fixture.rows();
    assert_eq!(initial.len(), 2);
    assert_eq!(initial[0]["pb_seq"], 1);
    assert_eq!(initial[0]["pb_session"], "run-1");
    assert_eq!(
        serde_json::from_str::<Value>(initial[0]["pb_payload"].as_str().unwrap()).unwrap()["pb_seq"],
        999
    );
    assert_eq!(initial[0]["pb_source_seq"], 1);
    assert_eq!(initial[1]["pb_source_seq"], 2);
    assert_ne!(initial[0]["pb_event_id"], initial[1]["pb_event_id"]);
    let request = &fixture.uploads()[0];
    assert!(
        request
            .headers
            .to_ascii_lowercase()
            .contains("x-p-stream: fixture")
    );
    assert_eq!(
        success(&sandbox.ingest(&["--format", "jsonl"]))["ingested"],
        0
    );
    assert_eq!(fixture.uploads().len(), 1);
    sandbox.append("events.log", b"{\"message\":\"three\"}\n");
    assert_eq!(
        success(&sandbox.ingest(&["--format", "jsonl"]))["ingested"],
        1
    );
    let rows = fixture.rows();
    assert_eq!(&rows[..2], initial);
    assert_eq!(rows[2]["pb_seq"], 3);
    assert_eq!(rows[2]["pb_collector"], rows[0]["pb_collector"]);
    assert!(sandbox.state("ingest.json")["pending"].is_null());
}

#[test]
fn ingest_failed_upload_replays_exact_persisted_body_after_source_is_deleted() {
    let fixture = Fixture::new();
    fixture.state.lock().unwrap().fail_uploads = 1;
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"original one\noriginal two\n");
    failure(&sandbox.ingest(&[]));
    let first = fixture.uploads();
    assert_eq!(first.len(), 1);
    let pending = sandbox.state("ingest.json");
    assert_eq!(
        serde_json::to_vec(&pending["pending"]["rows"]).unwrap(),
        first[0].body
    );
    fs::remove_file(sandbox.0.join("events.log")).unwrap();
    assert_eq!(success(&sandbox.ingest(&[]))["ingested"], 2);
    let uploads = fixture.uploads();
    assert_eq!(uploads.len(), 2);
    assert_eq!(uploads[0].body, uploads[1].body);
    assert_eq!(fixture.rows().len(), 2);
    assert_eq!(success(&sandbox.ingest(&[]))["ingested"], 0);
    assert_eq!(fixture.uploads().len(), 2);
}

#[test]
fn ingest_failed_upload_replays_original_before_importing_rewritten_source() {
    let fixture = Fixture::new();
    fixture.state.lock().unwrap().fail_uploads = 1;
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"original\n");
    failure(&sandbox.ingest(&[]));
    sandbox.write("events.log", b"replacement with different bytes\n");
    assert_eq!(success(&sandbox.ingest(&[]))["ingested"], 2);
    let uploads = fixture.uploads();
    assert_eq!(uploads.len(), 3);
    assert_eq!(uploads[0].body, uploads[1].body);
    let rows = fixture.rows();
    let payload =
        |row: &Value| serde_json::from_str::<Value>(row["pb_payload"].as_str().unwrap()).unwrap();
    assert_eq!(payload(&rows[0])["message"], "original");
    assert_eq!(
        payload(&rows[1])["message"],
        "replacement with different bytes"
    );
    assert_eq!(rows[0]["pb_seq"], 1);
    assert_eq!(rows[1]["pb_seq"], 2);
    assert_ne!(rows[0]["pb_event_id"], rows[1]["pb_event_id"]);
}

#[test]
fn ingest_text_preserves_lines_and_jsonl_skip_is_explicit() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"first \"quoted\"\r\n\nlast\n");
    assert_eq!(success(&sandbox.ingest(&[]))["ingested"], 3);
    let payloads: Vec<Value> = fixture
        .rows()
        .iter()
        .map(|row| serde_json::from_str(row["pb_payload"].as_str().unwrap()).unwrap())
        .collect();
    assert_eq!(
        payloads,
        vec![
            json!({"message":"first \"quoted\""}),
            json!({"message":""}),
            json!({"message":"last"})
        ]
    );

    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"{\"n\":1}\nnot-json\n{\"n\":2}\n");
    failure(&sandbox.ingest(&["--format", "jsonl"]));
    assert!(fixture.uploads().is_empty());
    let output = sandbox.ingest(&["--format", "jsonl", "--on-invalid", "skip"]);
    let report = success(&output);
    assert_eq!(report["ingested"], 2);
    assert_eq!(report["skipped"], 1);
    assert!(String::from_utf8_lossy(&output.stderr).contains("Skipped 1"));
    assert_eq!(fixture.rows()[1]["pb_seq"], 2);
}

#[test]
fn ingest_complete_rejects_partial_then_sends_final_ordered_marker_once() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"first\nunfinished");
    let output = sandbox.ingest(&["--complete"]);
    failure(&output);
    assert!(String::from_utf8_lossy(&output.stderr).contains("unfinished line"));
    assert_eq!(fixture.rows().len(), 1);
    assert_eq!(fixture.rows()[0]["pb_kind"], "event");
    sandbox.append("events.log", b"\n");
    let report = success(&sandbox.ingest(&["--complete"]));
    assert_eq!(report["ingested"], 1);
    assert_eq!(report["session_complete"], true);
    let rows = fixture.rows();
    assert_eq!(rows.len(), 3);
    for (index, row) in rows.iter().enumerate() {
        assert_eq!(row["pb_seq"], (index + 1) as u64);
    }
    assert_eq!(rows[1]["pb_kind"], "event");
    assert_eq!(rows[2]["pb_kind"], "complete");
    assert_eq!(success(&sandbox.ingest(&["--complete"]))["ingested"], 0);
    assert_eq!(fixture.rows().len(), 3);
    sandbox.append("events.log", b"too late\n");
    failure(&sandbox.ingest(&[]));
    assert_eq!(fixture.rows().len(), 3);
}

#[test]
fn ingest_follow_discovers_new_glob_matches_and_respects_excludes() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("first.log", b"one\n");
    sandbox.write("ignored.log", b"excluded\n");
    let command = sandbox
        .command(&[
            "ingest",
            "files",
            "*.log",
            "--dataset",
            "fixture",
            "--session",
            "run-1",
            "--state-file",
            "ingest.json",
            "--from",
            "beginning",
            "--follow",
            "--poll-interval",
            "10ms",
            "--exclude",
            "**/ignored.log",
        ])
        .spawn()
        .unwrap();
    let child = Running(Some(command));
    fixture.wait_for_rows(1);
    sandbox.write("new.log", b"two\n");
    sandbox.append("first.log", b"three\n");
    fixture.wait_for_rows(3);
    // Receiving HTTP bytes precedes the child's durable acknowledgement save.
    let deadline = Instant::now() + DEADLINE;
    loop {
        let saved = sandbox.state("ingest.json");
        if saved["pending"].is_null() && saved["next_sequence"] == 4 {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "follower did not commit its checkpoint"
        );
        thread::sleep(Duration::from_millis(5));
    }
    drop(child);
    let rows = fixture.rows();
    assert_eq!(rows.len(), 3);
    assert!(
        rows.iter()
            .all(|row| !row["pb_payload"].as_str().unwrap().contains("excluded"))
    );
    assert_eq!(
        success(&sandbox.ingest(&["--exclude", "**/ignored.log"]))["ingested"],
        0
    );
    assert_eq!(fixture.rows().len(), 3);
}

#[test]
fn ingest_rejects_session_mismatch_without_upload_or_checkpoint_changes() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"one\n");
    success(&sandbox.ingest(&[]));
    let state = fs::read(sandbox.0.join("ingest.json")).unwrap();
    failure(&sandbox.run(&[
        "ingest",
        "files",
        "*.log",
        "--dataset",
        "fixture",
        "--session",
        "run-2",
        "--state-file",
        "ingest.json",
        "--from",
        "beginning",
    ]));
    assert_eq!(fs::read(sandbox.0.join("ingest.json")).unwrap(), state);
    assert_eq!(fixture.uploads().len(), 1);
}

#[test]
fn ingest_default_end_discards_initial_fragment_but_reads_new_files() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("existing.log", b"old line\nold fragment");
    let args = [
        "ingest",
        "files",
        "*.log",
        "--dataset",
        "fixture",
        "--session",
        "run-1",
        "--state-file",
        "ingest.json",
        "-o",
        "json",
    ];
    assert_eq!(success(&sandbox.run(&args))["ingested"], 0);
    sandbox.append("existing.log", b" remainder\nnew line\n");
    sandbox.write("new.log", b"new file\n");
    assert_eq!(success(&sandbox.run(&args))["ingested"], 2);
    let mut messages = fixture
        .rows()
        .iter()
        .map(|row| {
            let payload: Value = serde_json::from_str(row["pb_payload"].as_str().unwrap()).unwrap();
            payload["message"].as_str().unwrap().to_owned()
        })
        .collect::<Vec<_>>();
    messages.sort();
    assert_eq!(messages, ["new file", "new line"]);
}

#[test]
fn ingest_oversized_line_and_corrupt_state_fail_without_network() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"oversized\n");
    failure(&sandbox.ingest(&["--max-line-bytes", "4"]));
    assert!(fixture.uploads().is_empty());
    sandbox.write("ingest.json", b"{broken");
    failure(&sandbox.ingest(&[]));
    assert_eq!(fs::read(sandbox.0.join("ingest.json")).unwrap(), b"{broken");
    assert!(fixture.uploads().is_empty());
}

#[cfg(unix)]
#[test]
fn ingest_glob_skips_named_pipe_without_waiting_for_a_writer() {
    use std::{ffi::CString, os::unix::ffi::OsStrExt};
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("regular.log", b"regular file\n");
    let fifo = CString::new(sandbox.0.join("pipe.log").as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(fifo.as_ptr(), 0o600) }, 0);
    assert_eq!(success(&sandbox.ingest(&[]))["ingested"], 1);
    assert_eq!(fixture.rows().len(), 1);
    assert!(
        fixture.rows()[0]["pb_source"]
            .as_str()
            .unwrap()
            .ends_with("regular.log")
    );
}

fn events(receipt: &Value) -> &[Value] {
    receipt["events"].as_array().expect("receipt events array")
}

fn token(receipt: &Value) -> &str {
    let token = receipt["next_cursor"]
        .as_str()
        .expect("receipt acknowledgment token");
    assert!(!token.is_empty());
    token
}

#[test]
fn observe_replays_pending_without_network_then_acknowledges_and_reads_next() {
    let fixture = Fixture::new();
    let sandbox = Sandbox::new(&fixture);
    sandbox.write("events.log", b"first-marker\nsecond-marker\n");
    success(&sandbox.ingest(&["--complete"]));
    let first_output = sandbox.observe("observer.json", &["--limit", "1"]);
    let first = success(&first_output);
    assert_eq!(events(&first).len(), 1);
    assert_eq!(events(&first)[0]["seq"], 1);
    assert_eq!(events(&first)[0]["collection"]["source_seq"], 1);
    assert!(events(&first)[0].to_string().contains("first-marker"));
    assert_eq!(first["session_complete"], false);
    let count = fixture.query_count();
    fixture.state.lock().unwrap().fail_queries = true;
    let replay = sandbox.observe("observer.json", &["--limit", "2"]);
    success(&replay);
    assert_eq!(replay.stdout, first_output.stdout);
    assert_eq!(fixture.query_count(), count);

    let saved = fs::read(sandbox.0.join("observer.json")).unwrap();
    failure(&sandbox.observe("observer.json", &["--ack", "incorrect-token"]));
    assert_eq!(fs::read(sandbox.0.join("observer.json")).unwrap(), saved);
    success(&sandbox.observe("observer.json", &["--ack", token(&first)]));
    assert_eq!(
        fixture.query_count(),
        count,
        "acknowledgment must remain local"
    );
    fixture.state.lock().unwrap().fail_queries = false;
    let next = success(&sandbox.observe("observer.json", &["--limit", "1"]));
    assert_eq!(events(&next).len(), 1);
    assert_eq!(events(&next)[0]["seq"], 2);
    assert!(events(&next)[0].to_string().contains("second-marker"));
    assert_ne!(token(&next), token(&first));
    success(&sandbox.observe("observer.json", &["--ack", token(&next)]));
    if next["session_complete"] != true {
        let last = success(&sandbox.observe("observer.json", &["--limit", "1"]));
        assert!(events(&last).is_empty());
        assert_eq!(last["session_complete"], true);
    }
}

#[test]
fn observe_cursors_are_independent_and_reject_session_rebinding() {
    let fixture = Fixture::new();
    fixture.state.lock().unwrap().rows = vec![
        event(1, json!({"marker":"first"})),
        event(2, json!({"marker":"second"})),
    ];
    let sandbox = Sandbox::new(&fixture);
    let first = success(&sandbox.observe("one.json", &["--limit", "1"]));
    success(&sandbox.observe("one.json", &["--ack", token(&first)]));
    let second = success(&sandbox.observe("one.json", &["--limit", "1"]));
    assert!(events(&second)[0].to_string().contains("second"));
    let independent = success(&sandbox.observe("two.json", &["--limit", "1"]));
    assert_eq!(events(&independent), events(&first));

    let saved = fs::read(sandbox.0.join("one.json")).unwrap();
    let count = fixture.query_count();
    failure(&sandbox.run(&[
        "observe",
        "fixture",
        "--session",
        "run-2",
        "--cursor-file",
        "one.json",
        "--wait",
        "0s",
    ]));
    assert_eq!(fs::read(sandbox.0.join("one.json")).unwrap(), saved);
    assert_eq!(fixture.query_count(), count);
}

#[test]
fn observe_does_not_skip_a_visibility_gap_and_ignores_exact_retry_duplicates() {
    let fixture = Fixture::new();
    let first_row = event(1, json!({"marker":"first"}));
    fixture.state.lock().unwrap().rows = vec![
        first_row.clone(),
        first_row,
        event(3, json!({"marker":"third"})),
    ];
    let sandbox = Sandbox::new(&fixture);
    let first = success(&sandbox.observe("observer.json", &["--limit", "10"]));
    assert_eq!(events(&first).len(), 1);
    assert!(events(&first)[0].to_string().contains("first"));
    success(&sandbox.observe("observer.json", &["--ack", token(&first)]));
    let gap = success(&sandbox.observe("observer.json", &["--limit", "10"]));
    assert!(
        events(&gap).is_empty(),
        "a later visible sequence must not cross the gap"
    );
    assert_eq!(gap["session_complete"], false);
    // Empty polls must not prevent a newly visible missing event from arriving.
    if gap["next_cursor"]
        .as_str()
        .is_some_and(|token| !token.is_empty())
    {
        success(&sandbox.observe("observer.json", &["--ack", token(&gap)]));
    }
    fixture
        .state
        .lock()
        .unwrap()
        .rows
        .push(event(2, json!({"marker":"second"})));
    let filled = success(&sandbox.observe("observer.json", &["--limit", "10"]));
    assert_eq!(events(&filled).len(), 2);
    assert_eq!(events(&filled)[0]["seq"], 2);
    assert_eq!(events(&filled)[1]["seq"], 3);
    assert!(events(&filled)[0].to_string().contains("second"));
    assert!(events(&filled)[1].to_string().contains("third"));
}

#[test]
fn observe_byte_bound_leaves_unreturned_event_for_next_receipt() {
    let fixture = Fixture::new();
    fixture.state.lock().unwrap().rows = vec![
        event(1, json!({"marker":"first","data":"x".repeat(900)})),
        event(2, json!({"marker":"second","data":"y".repeat(900)})),
    ];
    let sandbox = Sandbox::new(&fixture);
    let receipt =
        success(&sandbox.observe("observer.json", &["--limit", "10", "--max-bytes", "2000"]));
    assert_eq!(events(&receipt).len(), 1);
    assert!(serde_json::to_vec(&receipt).unwrap().len() <= 2000);
    assert!(events(&receipt)[0].to_string().contains("first"));
    success(&sandbox.observe("observer.json", &["--ack", token(&receipt)]));
    let next =
        success(&sandbox.observe("observer.json", &["--limit", "10", "--max-bytes", "2000"]));
    assert_eq!(events(&next).len(), 1);
    assert!(events(&next)[0].to_string().contains("second"));
}

#[test]
fn observe_query_failure_oversized_event_and_fractional_sequence_emit_no_stdout() {
    let fixture = Fixture::new();
    fixture.state.lock().unwrap().fail_queries = true;
    let sandbox = Sandbox::new(&fixture);
    failure(&sandbox.observe("observer.json", &[]));
    fixture.state.lock().unwrap().fail_queries = false;
    fixture.state.lock().unwrap().rows = vec![event(1, json!({"large":"x".repeat(2000)}))];
    failure(&sandbox.observe("observer.json", &["--max-bytes", "1024"]));
    let mut fractional = event(1, json!({"marker":"fractional"}));
    fractional["pb_seq"] = json!(1.25);
    fixture.state.lock().unwrap().query_override = Some(json!([fractional]));
    failure(&sandbox.observe("observer.json", &[]));
    // No rejected response may create a pending receipt or advance the cursor.
    fixture.state.lock().unwrap().query_override = None;
    fixture.state.lock().unwrap().rows = vec![event(1, json!({"marker":"valid-first"}))];
    let valid = success(&sandbox.observe("observer.json", &[]));
    assert_eq!(events(&valid).len(), 1);
    assert!(events(&valid)[0].to_string().contains("valid-first"));
}
