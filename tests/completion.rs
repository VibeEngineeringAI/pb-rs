use std::io::Write;
use std::process::{Command, Output, Stdio};

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

/// Set PB_GO_ORACLE to replay against the pinned Go executable. Every command
/// path is exercised, including hidden completion generation and flag errors.
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
        assert_eq!(
            (actual.status.code(), actual.stdout, actual.stderr),
            (expected.status.code(), expected.stdout, expected.stderr),
            "{case:?}"
        );
    }
}
