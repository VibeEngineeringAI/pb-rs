//! Root help uses the same terminal palette and color negotiation as Go.
#![cfg(unix)]

#[test]
fn terminal_and_redirected_root_help() {
    let output = std::process::Command::new("python3")
        .args([
            concat!(env!("CARGO_MANIFEST_DIR"), "/harness/root_help.py"),
            "--candidate",
            env!("CARGO_BIN_EXE_pb"),
        ])
        .output()
        .expect("python3 is required for PTY validation");
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}
