use crate::parser::quote;
use crate::spec::{CommandSpec, FlagKind};
use serde::Serialize;

#[derive(Serialize)]
struct Discovery<'a> {
    command: &'a str,
    usage: String,
    #[serde(skip_serializing_if = "str::is_empty")]
    description: &'a str,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    aliases: Vec<&'a str>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    flags: Vec<DiscoveryFlag>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    subcommands: Vec<serde_json::Value>,
}
#[derive(Serialize)]
struct DiscoveryFlag {
    name: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    shorthand: String,
    usage: String,
}

fn discovery(command: &CommandSpec, path: &str, initialized: bool) -> serde_json::Value {
    let result = Discovery {
        command: path,
        usage: command.usage(path, initialized),
        description: command.description().trim(),
        aliases: command.aliases.iter().map(String::as_str).collect(),
        flags: command
            .local_flags(initialized)
            .into_iter()
            .map(|f| DiscoveryFlag {
                name: f.name,
                shorthand: f.shorthand,
                usage: f.usage,
            })
            .collect(),
        subcommands: command
            .children
            .iter()
            .filter(|c| c.name != "help" && !c.hidden)
            .map(|c| discovery(c, &format!("{path} {}", c.name), false))
            .collect(),
    };
    // Value is used only for recursion; preserve field order with serde_json's
    // preserve_order feature, as Go emits declaration order for structs.
    serde_json::to_value(result).expect("serializable specification")
}
pub fn command_json(command: &CommandSpec, path: &str, initialized: bool) -> String {
    json(&discovery(command, path, initialized), true)
}
pub fn json<T: Serialize>(value: &T, escape_html: bool) -> String {
    let mut bytes = Vec::new();
    let mut serializer = serde_json::Serializer::pretty(&mut bytes);
    value
        .serialize(serde_stacker::Serializer::new(&mut serializer))
        .expect("serializable output");
    let mut output = String::from_utf8(bytes).expect("JSON is UTF-8");
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

pub fn flags(command: &CommandSpec, initialized: bool) -> String {
    let flags = command.local_flags(initialized);
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
        "sql", "promql", "tail", "dataset", "login", "cloud", "logout", "profile", "user", "role",
        "status", "version", "help",
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
