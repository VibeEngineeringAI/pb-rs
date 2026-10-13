#![cfg(unix)]

use std::ffi::OsString;
use std::fs;
use std::os::fd::{FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::symlink;
use std::os::unix::process::ExitStatusExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT_DIRECTORY: AtomicUsize = AtomicUsize::new(0);

struct Sandbox(PathBuf);

impl Sandbox {
    fn new() -> Self {
        let serial = NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("pb-process-{}-{serial}", std::process::id()));
        fs::create_dir(&path).expect("create isolated process environment");
        Self(path)
    }

    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_pb"));
        self.configure(&mut command);
        command
    }

    fn configure(&self, command: &mut Command) {
        command
            .env_clear()
            .env("HOME", &self.0)
            .env("XDG_CONFIG_HOME", self.0.join("config"))
            .env("TERM", "dumb")
            .env("PB_THEME", "dark")
            .env("PB_ANALYTICS", "disable")
            .stdin(Stdio::null());
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn closed_pipe() -> Stdio {
    let mut descriptors = [-1; 2];
    // SAFETY: pipe initializes two owned descriptors on success. The reader is
    // closed, and ownership of the writer is transferred exactly once.
    unsafe {
        assert_eq!(libc::pipe(descriptors.as_mut_ptr()), 0, "create pipe");
        drop(OwnedFd::from_raw_fd(descriptors[0]));
        Stdio::from(OwnedFd::from_raw_fd(descriptors[1]))
    }
}

#[test]
fn missing_config_environment_keeps_command_error_policy() {
    let sandbox = Sandbox::new();
    for empty in [false, true] {
        for (argv, message, on_stdout) in [
            (
                vec!["status", "-o", "json"],
                "no profile configured. run: pb login",
                true,
            ),
            (
                vec!["logout", "--yes", "-o", "json"],
                "no config found — nothing to logout from",
                false,
            ),
            (
                vec!["logout", "-o", "json"],
                "use --yes with -o json to avoid interactive prompts",
                false,
            ),
        ] {
            let mut command = sandbox.command();
            command
                .args(&argv)
                .env_remove("HOME")
                .env_remove("XDG_CONFIG_HOME");
            if empty {
                command.env("HOME", "").env("XDG_CONFIG_HOME", "");
            }
            let output = command.output().unwrap();
            assert_eq!(output.status.code(), Some(1));
            let stream = if on_stdout {
                assert!(output.stderr.is_empty());
                &output.stdout
            } else {
                assert!(output.stdout.is_empty());
                &output.stderr
            };
            let value: serde_json::Value = serde_json::from_slice(stream).unwrap();
            if on_stdout {
                assert_eq!(value["healthy"], false);
                assert_eq!(value["error"], message);
            } else {
                assert_eq!(value["error"]["message"], message);
            }
            if let Some(oracle) = std::env::var_os("PB_GO_ORACLE") {
                let mut reference = Command::new(oracle);
                sandbox.configure(&mut reference);
                reference
                    .args(&argv)
                    .env_remove("HOME")
                    .env_remove("XDG_CONFIG_HOME");
                if empty {
                    reference.env("HOME", "").env("XDG_CONFIG_HOME", "");
                }
                let reference = reference.output().unwrap();
                assert_eq!(output.stdout, reference.stdout);
                assert_eq!(output.stderr, reference.stderr);
                assert_eq!(output.status.code(), reference.status.code());
            }
        }
    }
}

#[test]
fn closed_stdout_terminates_with_sigpipe() {
    let sandbox = Sandbox::new();
    let output = sandbox
        .command()
        .arg("--help")
        .stdout(closed_pipe())
        .output()
        .unwrap();
    assert_eq!(output.status.signal(), Some(libc::SIGPIPE));
    assert!(output.stderr.is_empty());
}

#[test]
fn closed_stderr_terminates_with_sigpipe() {
    let sandbox = Sandbox::new();
    let output = sandbox
        .command()
        .arg("nosuch")
        .stderr(closed_pipe())
        .output()
        .unwrap();
    assert_eq!(output.status.signal(), Some(libc::SIGPIPE));
    assert!(output.stdout.is_empty());
}

#[test]
fn invalid_utf8_is_an_explicit_error_without_panicking() {
    let sandbox = Sandbox::new();
    let invalid = OsString::from_vec(vec![0xff]);
    let output = sandbox.command().arg(invalid).output().unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(output.stderr, b"Error: argument 1 is not valid UTF-8\n");
}

#[test]
fn invalid_utf8_json_rejection_uses_raw_output_request_scan() {
    let sandbox = Sandbox::new();
    let output = sandbox
        .command()
        .arg(OsString::from_vec(vec![0xff]))
        .args(["-ojson", "-otext"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    let error: serde_json::Value = serde_json::from_slice(&output.stderr).unwrap();
    assert_eq!(
        error,
        serde_json::json!({"error": {
            "code": "INVALID_INPUT", "message": "argument 1 is not valid UTF-8", "retryable": false
        }})
    );
}

#[test]
fn invalid_utf8_after_terminator_does_not_scan_json_as_a_flag() {
    let sandbox = Sandbox::new();
    let output = sandbox
        .command()
        .arg(OsString::from_vec(vec![0xff]))
        .args(["--", "-ojson"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(output.stderr, b"Error: argument 1 is not valid UTF-8\n");
}

#[test]
fn invalid_utf8_flag_value_is_rejected_without_panicking() {
    let sandbox = Sandbox::new();
    let output = sandbox
        .command()
        .arg("--output")
        .arg(OsString::from_vec(b"bad\xffvalue".to_vec()))
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(output.stderr, b"Error: argument 2 is not valid UTF-8\n");
}

#[test]
fn valid_unicode_arguments_reach_the_cli() {
    let sandbox = Sandbox::new();
    let output = sandbox.command().args(["café", "-ojson"]).output().unwrap();
    assert_eq!(output.status.code(), Some(1));
    let error: serde_json::Value = serde_json::from_slice(&output.stderr).unwrap();
    assert_eq!(error["error"]["code"], "INVALID_INPUT");
    assert_eq!(
        error["error"]["message"],
        "unknown command \"café\" for \"pb\""
    );

    let accepted = sandbox
        .command()
        .args(["promql", "series", "--match", "{job=\"café\"}", "--help"])
        .output()
        .unwrap();
    assert!(accepted.status.success());
    assert!(accepted.stderr.is_empty());
    assert!(
        String::from_utf8(accepted.stdout)
            .unwrap()
            .contains("pb promql series")
    );
}

#[test]
fn renamed_executable_keeps_the_public_pb_name() {
    let sandbox = Sandbox::new();
    let renamed = sandbox.0.join("renamed-client");
    symlink(env!("CARGO_BIN_EXE_pb"), &renamed).unwrap();
    let expected = sandbox.command().arg("--help").output().unwrap();
    let mut command = Command::new(renamed);
    sandbox.configure(&mut command);
    let actual = command.arg("--help").output().unwrap();
    assert!(actual.status.success());
    assert_eq!(actual.stdout, expected.stdout);
    assert_eq!(actual.stderr, expected.stderr);
    assert!(
        String::from_utf8(actual.stdout)
            .unwrap()
            .contains("pb [command] [flags]")
    );
}
