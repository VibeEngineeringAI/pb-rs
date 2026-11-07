//! The Go oracle predates exactly these Rust extension commands. Their own
//! declarations/protocol tests validate them independently of legacy parity.
pub fn extension(name: &str) -> bool {
    matches!(name, "ingest" | "observe")
}

pub fn project_rows(bytes: &[u8], root_completion: bool) -> Vec<u8> {
    let root_help = bytes
        .windows(b"  pb [command] [flags]\n".len())
        .any(|window| window == b"  pb [command] [flags]\n");
    let root_usage = bytes
        .windows(b"Usage:\n  pb [flags]\n  pb [command]\n".len())
        .any(|window| window == b"Usage:\n  pb [flags]\n  pb [command]\n");
    let width = if root_help { 10 } else { 12 };
    let completion = root_completion && bytes.ends_with(b":4\n");
    bytes
        .split_inclusive(|byte| *byte == b'\n')
        .filter(|line| {
            ![
                ("ingest", "Ingest events into Parseable"),
                (
                    "observe",
                    "Read session events with an explicit cursor receipt",
                ),
            ]
            .iter()
            .any(|(name, description)| {
                ((root_help || root_usage)
                    && *line
                        == format!("  {name}{}{description}\n", " ".repeat(width - name.len()))
                            .as_bytes())
                    || (completion
                        && (*line == format!("{name}\n").as_bytes()
                            || *line == format!("{name}\t{description}\n").as_bytes()))
            })
        })
        .flatten()
        .copied()
        .collect()
}
