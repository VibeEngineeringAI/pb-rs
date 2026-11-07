#![cfg(unix)]

use std::ffi::OsString;
use std::fs;
use std::io::Read;
use std::os::fd::{FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::symlink;
use std::os::unix::process::{CommandExt, ExitStatusExt};
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

fn close_child_output(command: &mut Command, descriptor: libc::c_int) {
    assert!(matches!(
        descriptor,
        libc::STDOUT_FILENO | libc::STDERR_FILENO
    ));
    // SAFETY: this callback runs after fork, in the single-threaded child.
    // Only async-signal-safe syscalls create, duplicate and close descriptors;
    // no parent or sibling can inherit the newly created reader. Command's
    // output setup has already opened descriptors 0, 1 and 2 before this runs.
    unsafe {
        command.pre_exec(move || {
            let mut descriptors = [-1; 2];
            if libc::pipe(descriptors.as_mut_ptr()) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            let duplicate = libc::dup2(descriptors[1], descriptor);
            let error = (duplicate < 0).then(std::io::Error::last_os_error);
            libc::close(descriptors[0]);
            libc::close(descriptors[1]);
            match error {
                Some(error) => Err(error),
                None => Ok(()),
            }
        });
    }
}

#[test]
fn sibling_inherited_reader_does_not_change_closed_output() {
    let sandbox = Sandbox::new();
    let mut descriptors = [-1; 2];
    // Intentionally inheritable, simulating the old fixture's reader leaking
    // into a sibling. Both descriptors become owned exactly once on success.
    let (reader, writer) = unsafe {
        assert_eq!(libc::pipe(descriptors.as_mut_ptr()), 0);
        (
            OwnedFd::from_raw_fd(descriptors[0]),
            OwnedFd::from_raw_fd(descriptors[1]),
        )
    };
    let mut keeper = Command::new("/bin/sh")
        .args(["-c", "printf ready; /bin/cat >/dev/null"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut ready = [0; 5];
    let readiness = keeper.stdout.as_mut().unwrap().read_exact(&mut ready);
    drop(reader);
    let mut command = sandbox.command();
    command.arg("version").stdout(Stdio::from(writer));
    close_child_output(&mut command, libc::STDOUT_FILENO);
    let output = command.output();
    // Release and reap the child before asserting, including on the failure
    // path, so a regression cannot leave its blocking cat process behind.
    drop(keeper.stdin.take());
    let status = keeper.wait().unwrap();
    readiness.unwrap();
    assert_eq!(&ready, b"ready");
    assert!(status.success());
    let output = output.unwrap();
    assert_eq!(output.status.signal(), Some(libc::SIGPIPE));
    assert!(output.stderr.is_empty());
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
    let mut command = sandbox.command();
    command.arg("--help");
    close_child_output(&mut command, libc::STDOUT_FILENO);
    let output = command.output().unwrap();
    assert_eq!(output.status.signal(), Some(libc::SIGPIPE));
    assert!(output.stderr.is_empty());
}

#[test]
fn closed_stderr_terminates_with_sigpipe() {
    let sandbox = Sandbox::new();
    let mut command = sandbox.command();
    command.arg("nosuch");
    close_child_output(&mut command, libc::STDERR_FILENO);
    let output = command.output().unwrap();
    assert_eq!(output.status.signal(), Some(libc::SIGPIPE));
    assert!(output.stdout.is_empty());
}

#[test]
fn invalid_utf8_command_is_byte_quoted_without_panicking() {
    let sandbox = Sandbox::new();
    let invalid = OsString::from_vec(vec![0xff]);
    let output = sandbox.command().arg(invalid).output().unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(
        output.stderr,
        b"Error: unknown command \"\\xff\" for \"pb\"\nRun 'pb --help' for usage.\n"
    );
}

#[test]
fn invalid_utf8_command_error_uses_raw_output_request_scan() {
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
            "code": "INVALID_INPUT", "message": "unknown command \"\\xff\" for \"pb\"", "retryable": false
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
    assert_eq!(
        output.stderr,
        b"Error: unknown command \"\\xff\" for \"pb\"\nRun 'pb --help' for usage.\n"
    );
}

#[test]
fn invalid_utf8_output_value_is_byte_quoted_without_panicking() {
    let sandbox = Sandbox::new();
    let output = sandbox
        .command()
        .arg("--output")
        .arg(OsString::from_vec(b"bad\xffvalue".to_vec()))
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert!(output.stderr.starts_with(
        b"Error: unsupported output format \"bad\\xffvalue\" (expected text or json)\n"
    ));
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
