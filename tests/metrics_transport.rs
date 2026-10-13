//! Real TCP resets distinguish PromQL's header error rewrite from body errors.
#![cfg(unix)]

use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    os::fd::AsRawFd,
    process::{Command, Output},
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Copy)]
enum ResetPhase {
    Headers,
    Body,
}

fn reset_peer(socket: TcpStream) {
    let linger = libc::linger {
        l_onoff: 1,
        l_linger: 0,
    };
    // An abortive close sends RST instead of a graceful FIN.
    assert_eq!(
        unsafe {
            libc::setsockopt(
                socket.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_LINGER,
                std::ptr::from_ref(&linger).cast(),
                std::mem::size_of_val(&linger) as libc::socklen_t,
            )
        },
        0
    );
    drop(socket);
}

fn invoke_reset(name: &str, arguments: &[&str], phase: ResetPhase) -> Output {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    listener.set_nonblocking(true).unwrap();
    let root = std::env::temp_dir().join(format!("pb-metrics-reset-{}-{name}", std::process::id()));
    std::fs::create_dir_all(root.join("pb")).unwrap();
    std::fs::write(
        root.join("pb/config.toml"),
        format!(
            "DefaultProfile = \"fixture\"\n[Profiles.fixture]\nurl = \"http://127.0.0.1:{port}\"\nusername = \"u\"\npassword = \"p\"\ncloud = false\n"
        ),
    )
    .unwrap();
    let server = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut socket = loop {
            match listener.accept() {
                Ok((socket, _)) => break socket,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(Instant::now() < deadline, "CLI did not connect");
                    thread::sleep(Duration::from_millis(5));
                }
                Err(error) => panic!("accept: {error}"),
            }
        };
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut headers = Vec::new();
        while !headers.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            socket.read_exact(&mut byte).unwrap();
            headers.push(byte[0]);
        }
        assert!(headers.starts_with(b"GET /prometheus/api/v1/"));
        if matches!(phase, ResetPhase::Body) {
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\nConnection: close\r\n\r\n{\"status\":\"success\",")
                .unwrap();
        }
        // Give the client time to enter its header/body receive phase.
        thread::sleep(Duration::from_millis(50));
        reset_peer(socket);
    });
    let output = Command::new(env!("CARGO_BIN_EXE_pb"))
        .args(arguments)
        .env_clear()
        .env("HOME", &root)
        .env("XDG_CONFIG_HOME", &root)
        .env("NO_COLOR", "1")
        .env("TERM", "dumb")
        .env("PB_THEME", "dark")
        .output()
        .unwrap();
    server.join().unwrap();
    std::fs::remove_dir_all(root).unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty(), "unexpected partial stdout");
    output
}

fn assert_header_reset_json(name: &str, arguments: &[&str], message: &str) {
    let output = invoke_reset(name, arguments, ResetPhase::Headers);
    let error: serde_json::Value = serde_json::from_slice(&output.stderr).unwrap();
    assert_eq!(
        error,
        serde_json::json!({"error": {
            "code": "COMMAND_FAILED",
            "message": message,
            "retryable": false
        }})
    );
}

#[test]
fn labels_header_reset_uses_command_error_json() {
    assert_header_reset_json(
        "labels-json",
        &["promql", "labels", "-o", "json"],
        "server reset the connection — query timed out",
    );
}

#[test]
fn instant_query_header_reset_uses_command_error_json() {
    assert_header_reset_json(
        "run-json",
        &["promql", "run", "up", "--instant", "-o", "json"],
        "request failed: server reset the connection — query timed out",
    );
}

#[test]
fn labels_header_reset_explains_query_timeout_text() {
    let output = invoke_reset("labels-text", &["promql", "labels"], ResetPhase::Headers);
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(
        stderr.contains("server reset the connection — query timed out"),
        "{stderr}"
    );
}

#[test]
fn labels_body_reset_retains_retryable_network_error() {
    let output = invoke_reset(
        "labels-body",
        &["promql", "labels", "-o", "json"],
        ResetPhase::Body,
    );
    let error: serde_json::Value = serde_json::from_slice(&output.stderr).unwrap();
    assert_eq!(error["error"]["code"], "CONNECTION_FAILED");
    assert_eq!(error["error"]["retryable"], true);
    assert_ne!(
        error["error"]["message"],
        "server reset the connection — query timed out"
    );
}
