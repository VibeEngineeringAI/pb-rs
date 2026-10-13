use pb::{
    parser::{self, FlagValue},
    spec::{CommandSpec, FlagKind, command_spec},
};
use serde_json::Value;
use std::collections::BTreeSet;

fn args(values: &[&str]) -> Vec<String> {
    values.iter().map(|s| (*s).into()).collect()
}
fn collect<'a>(spec: &'a CommandSpec, path: String, out: &mut Vec<(&'a CommandSpec, String)>) {
    out.push((spec, path.clone()));
    for child in &spec.children {
        collect(child, format!("{path} {}", child.name), out);
    }
}

#[test]
fn specification_is_consistent_and_all_help_paths_execute() {
    let mut nodes = Vec::new();
    collect(command_spec(), "pb".into(), &mut nodes);
    assert_eq!(nodes.len(), 52);
    for (node, path) in nodes {
        let mut long = BTreeSet::new();
        let mut short = BTreeSet::new();
        for flag in &node.flags {
            assert!(long.insert(&flag.name), "duplicate flag on {path}");
            if !flag.shorthand.is_empty() {
                assert!(short.insert(&flag.shorthand));
            }
        }
        let mut invocation: Vec<String> = path.split(' ').skip(1).map(str::to_string).collect();
        invocation.push("--help".into());
        let result = pb::run(&invocation);
        assert_eq!(result.exit_code, 0, "{path}: {:?}", result.stderr);
        assert!(result.stdout.windows(7).any(|w| w == b"Usage:\n"));
        assert!(result.stderr.is_empty());
    }
}

#[test]
fn flags_are_local_and_explicit_presence_survives_default_values() {
    let (command, path, remaining) =
        parser::discover(command_spec(), &args(&["sql", "run"])).unwrap();
    let absent = parser::parse(command, &path, &remaining).unwrap();
    let explicit = parser::parse(command, &path, &args(&["--from=1m", "-i=false"])).unwrap();
    assert_eq!(absent.text("from"), explicit.text("from"));
    assert!(!absent.explicit("from"));
    assert!(explicit.explicit("from"));
    assert!(explicit.explicit("interactive"));
    assert!(!explicit.boolean("interactive"));
    assert!(parser::parse(command, &path, &args(&["--version"])).is_err());
    let add = command_spec()
        .child("dataset")
        .unwrap()
        .child("add")
        .unwrap();
    assert!(!add.flags.iter().any(|f| f.name == "output"));
    assert!(parser::parse(add, "pb dataset add", &args(&["-ojson"])).is_err());
}

#[test]
fn arrays_are_repeatable_without_comma_splitting() {
    let command = command_spec()
        .child("promql")
        .unwrap()
        .child("series")
        .unwrap();
    let parsed = parser::parse(
        command,
        "pb promql series",
        &args(&[
            "--match",
            "{job=\"api\",env=\"test\"}",
            "-msecond,third",
            "--match=",
        ]),
    )
    .unwrap();
    assert_eq!(
        parsed.flags["match"].value,
        FlagValue::Texts(vec![
            "{job=\"api\",env=\"test\"}".into(),
            "second,third".into(),
            "".into()
        ])
    );
}

#[test]
fn raw_json_error_mode_obeys_terminator_and_any_explicit_occurrence() {
    assert!(parser::requests_json(&args(&[
        "--OUTPUT=JSON",
        "--output=text"
    ])));
    assert!(parser::requests_json(&args(&[" --output ", " JSON "])));
    assert!(!parser::requests_json(&args(&["--", "-ojson"])));
    let result = pb::run(&args(&["version", "--bogus", "-ojson", "-otext"]));
    assert!(result.stdout.is_empty());
    assert_eq!(result.exit_code, 1);
    let value: Value = serde_json::from_slice(&result.stderr).unwrap();
    assert_eq!(value["error"]["code"], "INVALID_INPUT");
    assert_eq!(value["error"]["message"], "unknown flag: --bogus");
    assert_eq!(value["error"]["retryable"], false);
}

#[test]
fn help_and_discovery_only_include_initialized_help_flags() {
    let root: Value = serde_json::from_slice(&pb::run(&args(&["-ojson"])).stdout).unwrap();
    let via_help: Value =
        serde_json::from_slice(&pb::run(&args(&["help", "-ojson"])).stdout).unwrap();
    assert_eq!(root["flags"][0]["name"], "help");
    assert_eq!(via_help["flags"][0]["name"], "output");
    assert!(
        !root["subcommands"]
            .as_array()
            .unwrap()
            .iter()
            .any(|c| matches!(c["command"].as_str(), Some("pb help" | "pb completion")))
    );
    let completion: Value =
        serde_json::from_slice(&pb::run(&args(&["help", "completion", "-ojson"])).stdout).unwrap();
    assert_eq!(completion["subcommands"].as_array().unwrap().len(), 4);
}

#[test]
fn error_quotes_use_go_string_not_json_string_rules() {
    assert_eq!(
        parser::quote("bell\u{7} esc\u{1b} vt\u{b} line\u{2028}"),
        "\"bell\\a esc\\x1b vt\\v line\\u2028\""
    );
    assert_eq!(parser::quote("café <select>"), "\"café <select>\"");
    assert_eq!(parser::quote("\u{1fae8}"), "\"\u{1fae8}\""); // Unicode 15 assignment
    assert_eq!(
        parser::quote("\u{ad}\u{a0}\u{378}\u{200b}"),
        "\"\\u00ad\\u00a0\\u0378\\u200b\""
    );
    let result = pb::run(&args(&["bad\u{7}", "-ojson"]));
    let value: Value = serde_json::from_slice(&result.stderr).unwrap();
    assert_eq!(
        value["error"]["message"],
        "unknown command \"bad\\a\" for \"pb\""
    );
}

#[test]
fn typed_integer_flags_accept_go_radix_and_reject_overflow() {
    let command = command_spec()
        .child("promql")
        .unwrap()
        .child("tsdb")
        .unwrap();
    for (value, expected) in [
        ("0x10", 16),
        ("010", 8),
        ("-1", -1),
        ("1_0", 10),
        ("0_7", 7),
        ("0x_ff", 255),
    ] {
        let parsed = parser::parse(command, "pb promql tsdb", &args(&["--top", value])).unwrap();
        assert_eq!(parsed.flags["top"].value, FlagValue::Int(expected));
    }
    let err = parser::parse(
        command,
        "pb promql tsdb",
        &args(&["--top=9223372036854775808"]),
    )
    .unwrap_err();
    assert!(err.message.ends_with("value out of range"));
    for invalid in ["++1", "--1", "1__0", "1_"] {
        let err = parser::parse(command, "pb promql tsdb", &args(&["--top", invalid])).unwrap_err();
        assert!(err.message.ends_with("invalid syntax"), "{invalid}");
    }
}

#[test]
fn custom_profile_add_validation_precedes_operation_dispatch() {
    let result = pb::run(&args(&[
        "profile",
        "add",
        "name",
        "http://127.0.0.1:1",
        "user",
        "--api-key=x",
        "-ojson",
    ]));
    let value: Value = serde_json::from_slice(&result.stderr).unwrap();
    assert_eq!(value["error"]["code"], "INVALID_INPUT");
    assert_eq!(
        value["error"]["message"],
        "--api-key cannot be combined with username/password"
    );
}

#[test]
fn version_and_agent_outputs_follow_owned_contract() {
    let version: Value = serde_json::from_slice(&pb::run(&args(&["-vojson"])).stdout).unwrap();
    assert_eq!(version["version"], pb::version());
    let catalog: Value =
        serde_json::from_slice(&pb::run(&args(&["agent", "-ojson"])).stdout).unwrap();
    assert_eq!(catalog["schema_version"], "1");
    assert!(
        catalog["commands"]
            .as_array()
            .unwrap()
            .iter()
            .all(|c| c["mutates"] == false)
    );
    assert!(
        catalog["commands"]
            .as_array()
            .unwrap()
            .iter()
            .any(|c| c["streaming"] == true)
    );
    assert!(
        command_spec()
            .flags
            .iter()
            .any(|f| f.name == "version" && f.kind == FlagKind::Bool)
    );
}

#[test]
fn sequential_parser_reports_the_first_original_token() {
    let tsdb = command_spec()
        .child("promql")
        .unwrap()
        .child("tsdb")
        .unwrap();
    let err = parser::parse(tsdb, "pb promql tsdb", &args(&["--top=x", "--top"])).unwrap_err();
    assert_eq!(
        err.message,
        "invalid argument \"x\" for \"--top\" flag: strconv.ParseInt: parsing \"x\": invalid syntax"
    );
    let version = command_spec().child("version").unwrap();
    let err = parser::parse(version, "pb version", &args(&["-xa", "-xb"])).unwrap_err();
    assert_eq!(err.message, "unknown shorthand flag: 'x' in -xa");
    let err = parser::parse(
        version,
        "pb version",
        &args(&["--pb-unknown-short-x", "-xab"]),
    )
    .unwrap_err();
    assert_eq!(err.message, "unknown flag: --pb-unknown-short-x");
    for malformed in ["---", "---foo", "--=x"] {
        assert_eq!(
            parser::parse(version, "pb version", &args(&[malformed]))
                .unwrap_err()
                .message,
            format!("bad flag syntax: {malformed}")
        );
    }
    assert_eq!(
        parser::parse(version, "pb version", &args(&["-é"]))
            .unwrap_err()
            .message,
        "unknown shorthand flag: 'Ã' in -é"
    );
    assert_eq!(
        parser::parse(version, "pb version", &args(&["-\n"]))
            .unwrap_err()
            .message,
        "unknown shorthand flag: '\\n' in -\n"
    );
}

#[test]
fn inherited_profile_preflight_keeps_text_prefix_and_json_public_message() {
    use std::{
        fs,
        process::Command,
        time::{SystemTime, UNIX_EPOCH},
    };
    let root = std::env::temp_dir().join(format!(
        "pb-preflight-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir(&root).unwrap();
    let invoke = |argv: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_pb"))
            .args(argv)
            .env("HOME", &root)
            .env("XDG_CONFIG_HOME", root.join("config"))
            .env("PB_ANALYTICS", "disable")
            .env("TERM", "dumb")
            .env("PB_THEME", "dark")
            .output()
            .unwrap()
    };
    for argv in [
        vec!["sql", "run", "SELECT 1"],
        vec!["promql", "labels"],
        vec!["promql", "series"],
        vec!["promql", "tsdb"],
    ] {
        let result = invoke(&argv);
        assert_eq!(result.status.code(), Some(1));
        assert!(result.stdout.is_empty());
        assert!(String::from_utf8(result.stderr).unwrap().starts_with(
            "Error: error initializing default profile: no profile configured. run: pb login\n"
        ));
        let mut json_args = argv;
        json_args.extend(["-o", "json"]);
        let result = invoke(&json_args);
        let error: Value = serde_json::from_slice(&result.stderr).unwrap();
        assert_eq!(error["error"]["code"], "NOT_FOUND");
        assert_eq!(
            error["error"]["message"],
            "no profile configured. run: pb login"
        );
    }
    let tail = invoke(&["tail", "dataset"]);
    assert!(
        String::from_utf8(tail.stderr)
            .unwrap()
            .starts_with("Error: no profile configured. run: pb login\n")
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn completion_group_renders_help_with_unrecognized_extra_words() {
    let result = pb::run(&args(&["completion", "foo"]));
    assert_eq!(result.exit_code, 0);
    assert!(result.stderr.is_empty());
    assert!(String::from_utf8_lossy(&result.stdout).contains("pb completion [flags]"));
    assert!(
        String::from_utf8_lossy(&result.stdout)
            .contains("Generate the autocompletion script for bash")
    );
}

#[test]
fn integer_unsigned_overflow_precedes_later_syntax_validation() {
    let command = command_spec()
        .child("promql")
        .unwrap()
        .child("tsdb")
        .unwrap();
    for raw in [
        "99999999999999999999x",
        "99999999999999999999_",
        "_99999999999999999999",
    ] {
        let err = parser::parse(command, "pb promql tsdb", &args(&["--top", raw])).unwrap_err();
        assert!(
            err.message.ends_with("value out of range"),
            "{raw}: {}",
            err.message
        );
    }
    // Signed overflow is checked after syntax when the magnitude fits uint64.
    for raw in ["9223372036854775808x", "9223372036854775808_"] {
        let err = parser::parse(command, "pb promql tsdb", &args(&["--top", raw])).unwrap_err();
        assert!(
            err.message.ends_with("invalid syntax"),
            "{raw}: {}",
            err.message
        );
    }
    let min = parser::parse(
        command,
        "pb promql tsdb",
        &args(&["--top=-9223372036854775808"]),
    )
    .unwrap();
    assert_eq!(min.flags["top"].value, FlagValue::Int(i64::MIN));
}

#[test]
fn separated_json_error_request_uses_unicode_simple_case_folding() {
    assert!(parser::requests_json(&args(&["-o", "jſon"])));
    assert!(parser::requests_json(&args(&["--output", "JſON"])));
    assert!(!parser::requests_json(&args(&["--output=jſon"])));
    let result = pb::run(&args(&["version", "-o", "jſon"]));
    let error: Value = serde_json::from_slice(&result.stderr).unwrap();
    assert_eq!(error["error"]["code"], "INVALID_INPUT");
    assert_eq!(
        error["error"]["message"],
        "unsupported output format \"jſon\" (expected text or json)"
    );
}
