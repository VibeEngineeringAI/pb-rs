use pb::{
    parser::{self, FlagValue},
    spec::{Operation, command_spec},
};
use serde_json::Value;

fn args(words: &[&str]) -> Vec<String> {
    words.iter().map(|word| (*word).into()).collect()
}

#[test]
fn ingestion_parses_quoted_globs_and_repeated_exclusions() {
    let words = args(&[
        "ingest",
        "files",
        "logs/**/*.log",
        "other/*.txt",
        "--dataset",
        "app",
        "--session",
        "run-1",
        "--state-file",
        "state.json",
        "--exclude",
        "**/old/**",
        "--exclude",
        "**/test,debug.log",
        "--complete",
    ]);
    let (command, path, remaining) = parser::discover(command_spec(), &words).unwrap();
    assert_eq!(command.operation, Operation::IngestFiles);
    let parsed = parser::parse(command, &path, &remaining).unwrap();
    parser::validate_args(command, &parsed).unwrap();
    assert_eq!(parsed.positionals, ["logs/**/*.log", "other/*.txt"]);
    assert_eq!(parsed.text("format"), "text");
    assert_eq!(parsed.text("from"), "end");
    assert!(parsed.boolean("complete"));
    assert!(!parsed.boolean("follow"));
    assert_eq!(
        parsed.flags["exclude"].value,
        FlagValue::Texts(vec!["**/old/**".into(), "**/test,debug.log".into()])
    );
    let empty = parser::parse(command, &path, &[]).unwrap();
    assert!(parser::validate_args(command, &empty).is_err());
}

#[test]
fn observation_parses_receipt_ack_and_defaults_to_json() {
    let words = args(&[
        "observe",
        "app",
        "--session",
        "run-1",
        "--cursor-file",
        "cursor.json",
        "--ack",
        "receipt-token",
    ]);
    let (command, path, remaining) = parser::discover(command_spec(), &words).unwrap();
    assert_eq!(command.operation, Operation::Observe);
    let parsed = parser::parse(command, &path, &remaining).unwrap();
    parser::validate_args(command, &parsed).unwrap();
    assert_eq!(parsed.positionals, ["app"]);
    assert_eq!(parsed.text("ack"), "receipt-token");
    assert_eq!(parsed.text("output"), "json");
    assert_eq!(parsed.text("wait"), "0s");
    assert_eq!(parsed.flags["limit"].value, FlagValue::Int(100));
    assert_eq!(parsed.flags["max-bytes"].value, FlagValue::Int(262144));
    let extra = parser::parse(command, &path, &args(&["app", "other"])).unwrap();
    assert!(parser::validate_args(command, &extra).is_err());
}

#[test]
fn read_only_catalog_discloses_local_receipts_and_excludes_ingestion() {
    let result = pb::run(&args(&["agent", "-ojson"]));
    assert_eq!(result.exit_code, 0);
    let catalog: Value = serde_json::from_slice(&result.stdout).unwrap();
    let commands = catalog["commands"].as_array().unwrap();
    assert!(commands.iter().all(|c| c["mutates"] == false));
    assert!(!commands.iter().any(|c| {
        c["command"]
            .as_str()
            .unwrap()
            .starts_with("pb ingest files ")
    }));
    let observe = commands
        .iter()
        .find(|c| c["command"].as_str().unwrap().starts_with("pb observe "))
        .unwrap();
    assert_eq!(observe["mutates"], false);
    assert!(
        observe["constraints"]
            .as_array()
            .unwrap()
            .iter()
            .any(|c| c.as_str().unwrap().contains("local cursor"))
    );
}

#[test]
fn completion_lists_new_commands_and_required_local_state_flags() {
    for (words, expected) in [
        (args(&["__completeNoDesc", "ing"]), "ingest\n:4\n"),
        (args(&["__completeNoDesc", "obs"]), "observe\n:4\n"),
        (args(&["__completeNoDesc", "ingest", ""]), "files\n:4\n"),
        (
            args(&["__completeNoDesc", "ingest", "files", "--state"]),
            "--state-file\n:4\n",
        ),
        (
            args(&["__completeNoDesc", "observe", "--cursor"]),
            "--cursor-file\n:4\n",
        ),
    ] {
        let result = pb::run(&words);
        assert_eq!(result.exit_code, 0, "{words:?}");
        assert_eq!(
            String::from_utf8(result.stdout).unwrap(),
            expected,
            "{words:?}"
        );
    }
}
