use std::io::Write;
use std::process::{Command, Output, Stdio};

#[path = "support/legacy_surface.rs"]
mod legacy_surface;

fn process(binary: &std::ffi::OsStr, args: &[String]) -> Output {
    Command::new(binary)
        .args(args)
        .env_remove("PB_COMPLETION_DESCRIPTIONS")
        .env_remove("COBRA_COMPLETION_DESCRIPTIONS")
        .output()
        .expect("run CLI")
}
fn args(words: &[&str]) -> Vec<String> {
    words.iter().map(|s| (*s).into()).collect()
}

#[test]
fn protocol_uses_owned_command_tree_and_keeps_completion_offline() {
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let result = process(binary, &args(&["__complete", ""]));
    assert!(result.status.success());
    let text = String::from_utf8(result.stdout).unwrap();
    assert!(text.contains("dataset\tManage datasets\n"));
    assert!(!text.contains("completion\t"));
    assert!(text.ends_with(":4\n"));
    let result = process(binary, &args(&["__completeNoDesc", "sql", "run", "--o"]));
    assert_eq!(result.stdout, b"--output\n:4\n");
    assert_eq!(
        result.stderr,
        b"Completion ended with directive: ShellCompDirectiveNoFileComp\n"
    );
    for invocation in [
        args(&["__complete", "sql", "--", "--help=false"]),
        args(&["__complete", "--", "--output="]),
    ] {
        assert_eq!(process(binary, &invocation).stdout, b":0\n");
    }
    let result = process(binary, &args(&["__complete", "sql", "run", "--output", ""]));
    assert_eq!(result.stdout, b":0\n");
    assert!(result.status.success());
}

#[test]
fn generated_bash_is_valid_and_drives_the_native_completion_protocol() {
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let generated = process(binary, &args(&["completion", "bash"]));
    assert!(generated.status.success());
    let mut shell = Command::new("bash")
        .args(["-n"])
        .stdin(Stdio::piped())
        .spawn()
        .expect("bash");
    shell
        .stdin
        .take()
        .unwrap()
        .write_all(&generated.stdout)
        .unwrap();
    assert!(shell.wait().unwrap().success());
    // Exercise Cobra's generated script as a real shell, bypassing only the
    // bash-completion package's command-line token reader.
    let mut script = generated.stdout;
    script.extend_from_slice(b"\nwords=(\"$1\" sql run --o); cword=3; cur=--o; prev=run; __pb_get_completion_results; printf '%s\\n' \"$out\" \"$directive\"\n");
    let mut shell = Command::new("bash")
        .args(["-s", "--", env!("CARGO_BIN_EXE_pb")])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    shell.stdin.take().unwrap().write_all(&script).unwrap();
    let output = shell.wait_with_output().unwrap();
    assert!(output.status.success(), "{:?}", output.stderr);
    assert_eq!(output.stdout, b"--output\tOutput format (text|json)\n\n4\n");
}

/// Set PB_GO_ORACLE to replay against the pinned Go executable. Every legacy
/// command path is exercised, including hidden completion generation and
/// flag errors. Rust-only extensions have independent protocol tests.
#[test]
fn completion_matches_go_oracle() {
    let Some(oracle) = std::env::var_os("PB_GO_ORACLE") else {
        eprintln!("PB_GO_ORACLE is not set; skipping Go completion parity replay");
        return;
    };
    let binary = std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb"));
    let mut cases = vec![
        args(&["__complete"]),
        args(&["__completeNoDesc"]),
        args(&["__complete", "unknown-command", ""]),
    ];
    fn walk(node: &pb::spec::CommandSpec, path: &[String], cases: &mut Vec<Vec<String>>) {
        for protocol in ["__complete", "__completeNoDesc"] {
            for tail in [
                vec![""],
                vec!["-"],
                vec!["--h"],
                vec!["--help=false", ""],
                vec!["x", ""],
                vec!["--", "-"],
                vec!["--unknown="],
                vec!["--unknown", ""],
            ] {
                let mut case = vec![protocol.into()];
                case.extend_from_slice(path);
                case.extend(tail.into_iter().map(str::to_owned));
                cases.push(case);
            }
            for flag in &node.flags {
                for tail in [
                    vec![format!("--{}", flag.name)],
                    vec![format!("--{}", flag.name), String::new()],
                    vec![format!("--{}=", flag.name)],
                ] {
                    let mut case = vec![protocol.into()];
                    case.extend_from_slice(path);
                    case.extend(tail);
                    cases.push(case);
                }
            }
        }
        for child in &node.children {
            if path.is_empty() && legacy_surface::extension(&child.name) {
                continue;
            }
            let mut path = path.to_vec();
            path.push(child.name.clone());
            walk(child, &path, cases);
        }
    }
    walk(pb::spec::command_spec(), &[], &mut cases);
    for shell in ["bash", "zsh", "fish", "powershell"] {
        for tail in [
            vec![],
            vec!["--no-descriptions"],
            vec!["--no-descriptions=false"],
        ] {
            let mut case = args(&["completion", shell]);
            case.extend(tail.into_iter().map(str::to_owned));
            cases.push(case);
        }
    }
    for case in cases {
        let expected = process(&oracle, &case);
        let actual = process(binary, &case);
        let root_completion =
            case.len() == 2 && matches!(case[0].as_str(), "__complete" | "__completeNoDesc");
        assert_eq!(
            (
                actual.status.code(),
                legacy_surface::project_rows(&actual.stdout, root_completion),
                actual.stderr
            ),
            (
                expected.status.code(),
                legacy_surface::project_rows(&expected.stdout, root_completion),
                expected.stderr
            ),
            "{case:?}"
        );
    }
}

fn completion_probe(command: &mut Command) -> std::io::Result<std::process::ExitStatus> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status),
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            result => {
                let _ = child.kill();
                let _ = child.wait();
                return match result {
                    Err(error) => Err(error),
                    _ => Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "completion interpreter probe exceeded 15 seconds",
                    )),
                };
            }
        }
    }
}

/// Missing optional interpreters skip locally. CI sets a comma-separated list
/// (or `1`/`all`) to make missing runtimes a hard failure on the relevant OS.
fn completion_runtime(shell: &str, default_runtime: &str) {
    let required = std::env::var("PB_REQUIRE_COMPLETION_SHELLS").unwrap_or_default();
    let required = required == "1"
        || required == "all"
        || required.split(',').any(|name| name.trim() == shell);
    let variable = format!("PB_COMPLETION_{}", shell.to_uppercase());
    let runtime = std::env::var_os(&variable).unwrap_or_else(|| default_runtime.into());
    match completion_probe(Command::new(&runtime).arg("--version")) {
        Ok(status) => assert!(status.success(), "{shell} runtime failed: {status:?}"),
        Err(error)
            if error.kind() == std::io::ErrorKind::NotFound
                && !required
                && std::env::var_os(&variable).is_none() =>
        {
            eprintln!(
                "{shell} runtime missing; set {variable} or PB_REQUIRE_COMPLETION_SHELLS={shell}"
            );
            return;
        }
        Err(error) => panic!("required {shell} runtime {runtime:?}: {error}"),
    }
    let python = ["python3", "python"]
        .into_iter()
        .find(|python| {
            completion_probe(
                Command::new(python).args(["-c", "import sys; sys.exit(sys.version_info[0] != 3)"]),
            )
            .is_ok_and(|status| status.success())
        })
        .expect("Python 3 is required for real-shell completion tests");
    let output = Command::new(python)
        .arg(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/completion_runtime.py"
        ))
        .args([
            std::ffi::OsStr::new(shell),
            &runtime,
            std::ffi::OsStr::new(env!("CARGO_BIN_EXE_pb")),
        ])
        .output()
        .expect("run bounded shell completion harness");
    assert!(
        output.status.success(),
        "{shell}:\n{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    eprintln!("{}", String::from_utf8_lossy(&output.stdout));
}

#[cfg(unix)]
#[test]
fn zsh_runtime_completes_with_native_zle() {
    completion_runtime("zsh", "zsh");
}

#[test]
fn fish_runtime_completes_with_native_engine() {
    completion_runtime("fish", "fish");
}

#[test]
fn powershell_runtime_completes_with_native_engine() {
    completion_runtime("powershell", "pwsh");
}
