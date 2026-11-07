use crate::parser::quote;
use crate::spec::{CommandSpec, FlagKind};
use serde::Serialize;
use std::borrow::Cow;

#[derive(Serialize)]
struct Discovery<'a> {
    command: String,
    usage: String,
    #[serde(skip_serializing_if = "str::is_empty")]
    description: &'a str,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    aliases: Vec<&'a str>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    flags: Vec<DiscoveryFlag<'a>>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    subcommands: Vec<Discovery<'a>>,
}
#[derive(Serialize)]
struct DiscoveryFlag<'a> {
    name: Cow<'a, str>,
    #[serde(skip_serializing_if = "str::is_empty")]
    shorthand: Cow<'a, str>,
    usage: Cow<'a, str>,
}

fn discovery_flag(flag: Cow<'_, crate::spec::FlagSpec>) -> DiscoveryFlag<'_> {
    match flag {
        Cow::Borrowed(flag) => DiscoveryFlag {
            name: Cow::Borrowed(&flag.name),
            shorthand: Cow::Borrowed(&flag.shorthand),
            usage: Cow::Borrowed(&flag.usage),
        },
        Cow::Owned(flag) => DiscoveryFlag {
            name: Cow::Owned(flag.name),
            shorthand: Cow::Owned(flag.shorthand),
            usage: Cow::Owned(flag.usage),
        },
    }
}

// Builds the tree once and serializes it directly; struct fields serialize in
// declaration order, matching Go's output.
fn discovery<'a>(command: &'a CommandSpec, path: String, initialized: bool) -> Discovery<'a> {
    Discovery {
        usage: command.usage(&path, initialized),
        description: command.description().trim(),
        aliases: command.aliases.iter().map(String::as_str).collect(),
        flags: command
            .local_flags_borrowed(initialized)
            .into_iter()
            .map(discovery_flag)
            .collect(),
        subcommands: command
            .children
            .iter()
            .filter(|c| c.name != "help" && !c.hidden)
            .map(|c| discovery(c, format!("{path} {}", c.name), false))
            .collect(),
        command: path,
    }
}
pub fn command_json(command: &CommandSpec, path: &str, initialized: bool) -> String {
    json(&discovery(command, path.to_owned(), initialized), true)
}
pub fn json<T: Serialize>(value: &T, escape_html: bool) -> String {
    let mut bytes = Vec::new();
    let mut serializer = serde_json::Serializer::pretty(&mut bytes);
    value
        .serialize(serde_stacker::Serializer::new(&mut serializer))
        .expect("serializable output");
    bytes.push(b'\n');
    String::from_utf8(escape_json(bytes, escape_html)).expect("JSON is UTF-8")
}

/// Applies Go's encoder escapes in one pass: `&`, `<`, `>` when `escape_html`, and
/// always U+2028/U+2029. Returns the input untouched when nothing needs escaping.
fn escape_json(bytes: Vec<u8>, escape_html: bool) -> Vec<u8> {
    let escape_at = |i: usize| -> Option<(&'static [u8], usize)> {
        match bytes[i] {
            b'&' if escape_html => Some((b"\\u0026", 1)),
            b'<' if escape_html => Some((b"\\u003c", 1)),
            b'>' if escape_html => Some((b"\\u003e", 1)),
            0xE2 if bytes.get(i + 1) == Some(&0x80) => match bytes.get(i + 2) {
                Some(0xA8) => Some((b"\\u2028", 3)),
                Some(0xA9) => Some((b"\\u2029", 3)),
                _ => None,
            },
            _ => None,
        }
    };
    let Some(first) = (0..bytes.len()).find(|&i| escape_at(i).is_some()) else {
        return bytes;
    };
    let mut output = Vec::with_capacity(bytes.len() + 16);
    output.extend_from_slice(&bytes[..first]);
    let (mut start, mut i) = (first, first);
    while i < bytes.len() {
        match escape_at(i) {
            Some((escaped, len)) => {
                output.extend_from_slice(&bytes[start..i]);
                output.extend_from_slice(escaped);
                i += len;
                start = i;
            }
            None => i += 1,
        }
    }
    output.extend_from_slice(&bytes[start..]);
    output
}

pub fn flags(command: &CommandSpec, initialized: bool) -> String {
    let flags = command.local_flags_borrowed(initialized);
    let rows: Vec<(String, String)> = flags
        .into_iter()
        .map(|f| {
            let prefix = if f.shorthand.is_empty() {
                "      ".into()
            } else {
                format!("  -{}, ", f.shorthand)
            };
            let value_type = match f.kind {
                FlagKind::String => " string",
                FlagKind::Int => " int",
                FlagKind::Strings => " stringArray",
                FlagKind::Bool => "",
            };
            let label = format!("{prefix}--{}{value_type}", f.name);
            let default = match f.kind {
                FlagKind::String if f.default.as_str().is_some_and(|s| !s.is_empty()) => {
                    format!(" (default {})", quote(f.default.as_str().unwrap()))
                }
                FlagKind::Int if f.default.as_i64() != Some(0) => {
                    format!(" (default {})", f.default)
                }
                FlagKind::Bool if f.default.as_bool() == Some(true) => " (default true)".into(),
                _ => String::new(),
            };
            (label, format!("{}{default}", f.usage))
        })
        .collect();
    let width = rows
        .iter()
        .map(|(l, _)| l.chars().count())
        .max()
        .unwrap_or(0)
        + 3;
    rows.into_iter()
        .map(|(label, usage)| format!("{label:width$}{usage}\n"))
        .collect()
}

pub fn root_help(root: &CommandSpec, initialized: bool) -> String {
    let art = r" ____   _    ____  ____  _____    _    ____  _     _____
|  _ \ / \  |  _ \/ ___|| ____|  / \  | __ )| |   | ____|
| |_) / _ \ | |_) \___ \|  _|   / _ \ |  _ \| |   |  _|
|  __/ ___ \|  _ < ___) | |___ / ___ \| |_) | |___| |___
|_| /_/   \_\_| \_\____/|_____/_/   \_\____/|_____|_____|";
    use crate::tui::style::{Color, Role, Style};
    let accent = Style::new().fg(Color::active(Role::Accent)).bold(true);
    let faint = Style::new().fg(Color::active(Role::Faint));
    let body = Style::new().fg(Color::active(Role::Body));
    let command_style = body.bold(true);
    let mut output = format!(
        "{}\n{}\n\n",
        accent.render(art),
        faint.render("Parseable CLI")
    );
    for line in [
        "pb is the command line interface for Parseable.",
        "Query logs with SQL, explore metrics with PromQL, tail live events,",
        "and manage datasets, users, roles, and profiles from your terminal.",
    ] {
        output.push_str(&body.render(line));
        output.push('\n');
    }
    output.push_str(&format!(
        "\n{}\n  pb [command] [flags]\n\n{}\n",
        accent.render("Usage:"),
        accent.render("Available Commands:")
    ));
    for name in [
        "sql", "promql", "tail", "ingest", "observe", "dataset", "login", "cloud", "logout",
        "profile", "user", "role", "status", "version", "help",
    ] {
        if let Some(c) = root.child(name) {
            output.push_str(&format!(
                "  {}{}{}\n",
                command_style.render(name),
                " ".repeat(10_usize.saturating_sub(name.len()).max(1)),
                faint.render(&c.short)
            ));
        }
    }
    output.push_str(&format!("\n{}\n", accent.render("Flags:")));
    output.push_str(&flags(root, initialized));
    output.push_str(&format!(
        "\n{}\n",
        faint.render(r#"Use "pb [command] --help" for more information about a command."#)
    ));
    output
}

pub fn help(command: &CommandSpec, path: &str, initialized: bool) -> String {
    if path == "pb" {
        return root_help(command, initialized);
    }
    let promql = path.starts_with("pb promql");
    let mut output = String::new();
    let description = if promql {
        command.long.as_str()
    } else {
        command.description()
    };
    if !description.is_empty() {
        output.push_str(description);
        output.push_str("\n\n");
    }
    output.push_str(&format!(
        "Usage:\n  {}\n\n",
        command.usage(path, initialized)
    ));
    if !command.example.is_empty() {
        output.push_str(&format!("Examples:\n{}\n\n", command.example));
    }
    if promql {
        output.push_str("Available Commands:\n");
        for name in [
            "run",
            "labels",
            "label-values",
            "series",
            "cardinality",
            "tsdb",
            "active-queries",
        ] {
            if let Some(c) = command.child(name) {
                output.push_str(&format!("  {name:14} {}\n", c.short));
            }
        }
        output.push('\n');
    } else if !command.children.is_empty() {
        output.push_str("Available Commands:\n");
        for c in command.children.iter().filter(|c| !c.hidden) {
            let name = if c.aliases.is_empty() {
                c.name.clone()
            } else {
                format!("{}|{}", c.name, c.aliases.join("|"))
            };
            output.push_str(&format!("  {name:16} {}\n", c.short));
        }
        output.push('\n');
    }
    if initialized || !command.flags.is_empty() {
        output.push_str("Flags:\n");
        output.push_str(&flags(command, initialized));
        output.push('\n');
    }
    if promql || !command.children.is_empty() {
        output.push_str(&format!(
            "Use \"{path} [command] --help\" for more information about a command.\n"
        ));
    }
    output
}

/// Cobra's error usage template differs from its custom successful help template.
pub fn usage(command: &CommandSpec, path: &str) -> String {
    let initialized = true;
    let children: Vec<_> = command.children.iter().filter(|c| !c.hidden).collect();
    let mut output = if command.operation == crate::spec::Operation::CompletionGroup {
        "Usage:\n".into()
    } else {
        format!("Usage:\n  {}\n", command.usage(path, true))
    };
    if !children.is_empty() {
        output.push_str(&format!("  {path} [command]\n"));
    }
    output.push('\n');
    if !command.aliases.is_empty() {
        output.push_str(&format!(
            "Aliases:\n  {}, {}\n\n",
            command.name,
            command.aliases.join(", ")
        ));
    }
    if !command.example.is_empty() {
        output.push_str(&format!("Examples:\n{}\n\n", command.example));
    }
    if !children.is_empty() {
        output.push_str("Available Commands:\n");
        let width = children.iter().map(|c| c.name.len()).max().unwrap().max(11);
        for c in &children {
            output.push_str(&format!("  {:width$} {}\n", c.name, c.short));
        }
        output.push('\n');
    }
    if initialized || !command.flags.is_empty() {
        output.push_str("Flags:\n");
        output.push_str(&flags(command, initialized));
        output.push('\n');
    }
    if !children.is_empty() {
        output.push_str(&format!(
            "Use \"{path} [command] --help\" for more information about a command.\n\n"
        ));
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    fn former_json<T: Serialize>(value: &T, escape_html: bool) -> String {
        let mut bytes = Vec::new();
        let mut serializer = serde_json::Serializer::pretty(&mut bytes);
        value
            .serialize(serde_stacker::Serializer::new(&mut serializer))
            .unwrap();
        let mut output = String::from_utf8(bytes).unwrap();
        if escape_html {
            output = output
                .replace('&', "\\u0026")
                .replace('<', "\\u003c")
                .replace('>', "\\u003e");
        }
        output = output
            .replace('\u{2028}', "\\u2028")
            .replace('\u{2029}', "\\u2029");
        output.push('\n');
        output
    }

    #[test]
    fn json_matches_former_escaping() {
        let cases = [
            serde_json::json!({}),
            serde_json::json!([]),
            serde_json::json!(null),
            serde_json::json!("plain"),
            serde_json::json!({"z": 1, "a": [true, 2.5, "x"], "m": {"k": null}}),
            serde_json::json!({"a&b": "<tag attr=\"v\">&amp;</tag>"}),
            serde_json::json!("line\u{2028}para\u{2029}end"),
            serde_json::json!("\u{2028}\u{2029}<>&"),
            serde_json::json!("é ü 日本 🎉 \u{2027}\u{202A}\u{20AC}"),
            serde_json::json!("quote \" backslash \\ tab \t nl \n ctl \u{1}"),
            serde_json::json!(["&", "<", ">", "\u{2028}", "\u{2029}"]),
        ];
        for value in &cases {
            for escape_html in [true, false] {
                assert_eq!(
                    json(value, escape_html),
                    former_json(value, escape_html),
                    "value={value} escape_html={escape_html}"
                );
            }
        }
    }

    #[test]
    fn json_escapes_expected_bytes() {
        let value = serde_json::json!("<&>\u{2028}\u{2029}");
        assert_eq!(
            json(&value, true),
            "\"\\u003c\\u0026\\u003e\\u2028\\u2029\"\n"
        );
        assert_eq!(json(&value, false), "\"<&>\\u2028\\u2029\"\n");
    }

    #[test]
    fn escape_json_reuses_buffer_without_escapes() {
        let bytes = "{\n  \"a\": \"<\u{e9}\"\n}\n".as_bytes().to_vec();
        let ptr = bytes.as_ptr();
        let output = escape_json(bytes, false);
        assert_eq!(output.as_ptr(), ptr);
        assert_eq!(output, "{\n  \"a\": \"<\u{e9}\"\n}\n".as_bytes());
    }

    #[derive(Serialize)]
    struct FormerDiscovery<'a> {
        command: &'a str,
        usage: String,
        #[serde(skip_serializing_if = "str::is_empty")]
        description: &'a str,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        aliases: Vec<&'a str>,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        flags: Vec<DiscoveryFlag<'a>>,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        subcommands: Vec<serde_json::Value>,
    }

    fn former_discovery(command: &CommandSpec, path: &str, initialized: bool) -> serde_json::Value {
        let result = FormerDiscovery {
            command: path,
            usage: command.usage(path, initialized),
            description: command.description().trim(),
            aliases: command.aliases.iter().map(String::as_str).collect(),
            flags: command
                .local_flags(initialized)
                .into_iter()
                .map(|f| DiscoveryFlag {
                    name: Cow::Owned(f.name),
                    shorthand: Cow::Owned(f.shorthand),
                    usage: Cow::Owned(f.usage),
                })
                .collect(),
            subcommands: command
                .children
                .iter()
                .filter(|c| c.name != "help" && !c.hidden)
                .map(|c| former_discovery(c, &format!("{path} {}", c.name), false))
                .collect(),
        };
        serde_json::to_value(result).unwrap()
    }

    fn assert_discovery_parity(
        command: &CommandSpec,
        path: &str,
        checked: &mut usize,
        alias_checked: &mut usize,
    ) {
        for initialized in [true, false] {
            assert_eq!(
                command_json(command, path, initialized),
                json(&former_discovery(command, path, initialized), true),
                "path={path} initialized={initialized}"
            );
            *checked += 1;
        }
        for alias in &command.aliases {
            let alias_path = match path.rsplit_once(' ') {
                Some((parent, _)) => format!("{parent} {alias}"),
                None => alias.clone(),
            };
            for initialized in [true, false] {
                assert_eq!(
                    command_json(command, &alias_path, initialized),
                    json(&former_discovery(command, &alias_path, initialized), true),
                    "alias path={alias_path} initialized={initialized}"
                );
                *alias_checked += 1;
            }
        }
        for child in &command.children {
            assert_discovery_parity(
                child,
                &format!("{path} {}", child.name),
                checked,
                alias_checked,
            );
        }
    }

    #[test]
    fn command_json_matches_former_value_discovery() {
        let root = crate::spec::command_spec();
        let (mut checked, mut alias_checked) = (0, 0);
        assert_discovery_parity(root, &root.name, &mut checked, &mut alias_checked);
        assert!(checked > 2, "expected to walk the command tree");
        assert!(alias_checked > 0, "expected alias paths to be exercised");
    }
}
