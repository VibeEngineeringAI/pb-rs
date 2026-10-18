//! Cobra 1.8.1 shell protocol and generators. Shell templates are adapted from
//! github.com/spf13/cobra (Apache-2.0); see completion/LICENSE-Cobra.txt.
//! Templates are executable source, with parameters rendered here, not captured
//! CLI output. Choices always come from the owned command declarations.
use crate::{
    Outcome, parser,
    spec::{ArgsKind, ArgsRule, CommandSpec, FlagKind, Operation, command_spec},
};

fn protocol_spec() -> CommandSpec {
    // Built field by field from the root so the 55-command tree is never
    // deep-cloned only to discard flags and children.
    let root = command_spec();
    CommandSpec {
        name: "__complete".into(),
        operation: Operation::Deferred,
        disable_flags_in_use_line: true,
        r#use: "__complete [command-line]".into(),
        short: "Request shell completion choices for the specified command-line".into(),
        long: "__complete is a special command that is used by the shell completion logic\nto request completion choices for the specified command-line.".into(),
        example: root.example.clone(),
        aliases: vec!["__completeNoDesc".into()],
        flags: Vec::new(),
        children: Vec::new(),
        args: ArgsRule {
            kind: ArgsKind::Min,
            counts: vec![1],
        },
        silence_usage: false,
        requires_profile: root.requires_profile,
        wrapped_preflight: root.wrapped_preflight,
        hidden: true,
    }
}

/// The protocol command disables flag parsing: every trailing token, including
/// --help and --output, describes the command line being completed.
pub(crate) fn request(args: &[String], raw: Option<&[Vec<u8>]>) -> Option<Outcome> {
    if !args
        .iter()
        .any(|a| a == "__complete" || a == "__completeNoDesc")
    {
        return None;
    }
    let root = command_spec();
    let protocol = protocol_spec();
    let owned;
    let bytes = if let Some(raw) = raw {
        raw
    } else {
        owned = args
            .iter()
            .map(|s| s.as_bytes().to_vec())
            .collect::<Vec<_>>();
        &owned
    };
    let (command, _, remaining) =
        crate::argv::discover_with_root_child(root, bytes, Some(&protocol)).ok()?;
    if command.name != "__complete" {
        return None;
    }
    if remaining.is_empty() {
        return Some(crate::failure(
            command,
            "pb __complete",
            "requires at least 1 arg(s), only received 0",
            crate::ErrorCode::InvalidInput,
            parser::requests_json(args),
            false,
        ));
    }
    let called_index = bytes
        .iter()
        .zip(&remaining)
        .position(|(arg, remaining)| arg != remaining)
        .unwrap_or(args.len() - 1);
    let no_desc = args[called_index] == "__completeNoDesc" || descriptions_disabled();
    let (choices, directive, error) = choices(root, &protocol, &remaining);
    let mut stdout = String::new();
    for choice in choices {
        let choice = if no_desc {
            choice.split('\t').next().unwrap_or("")
        } else {
            &choice
        };
        stdout.push_str(choice.split('\n').next().unwrap_or("").trim());
        stdout.push('\n');
    }
    stdout.push_str(&format!(":{directive}\n"));
    let mut stderr = Vec::new();
    if let Some(error) = error {
        stderr.extend_from_slice(b"[Debug] [Error] ");
        stderr.extend(error);
        stderr.push(b'\n');
    }
    stderr.extend_from_slice(
        format!(
            "Completion ended with directive: {}\n",
            if directive == 4 {
                "ShellCompDirectiveNoFileComp"
            } else {
                "ShellCompDirectiveDefault"
            }
        )
        .as_bytes(),
    );
    Some(Outcome {
        stdout: stdout.into_bytes(),
        stderr,
        exit_code: 0,
    })
}

fn descriptions_disabled() -> bool {
    let value = std::env::var("PB_COMPLETION_DESCRIPTIONS")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("COBRA_COMPLETION_DESCRIPTIONS").ok());
    matches!(
        value.as_deref(),
        Some("0" | "f" | "F" | "false" | "FALSE" | "False")
    )
}
fn go_args(args: &[Vec<u8>]) -> Vec<u8> {
    let mut result = b"[".to_vec();
    for (i, arg) in args.iter().enumerate() {
        if i > 0 {
            result.push(b' ');
        }
        result.extend(arg);
    }
    result.push(b']');
    result
}
fn choices(
    root: &CommandSpec,
    protocol: &CommandSpec,
    args: &[Vec<u8>],
) -> (Vec<String>, u8, Option<Vec<u8>>) {
    let (last, before) = args.split_last().expect("at least one argument");
    let (command, path, mut remaining) =
        match crate::argv::discover_with_root_child(root, before, Some(protocol)) {
            Ok(found) => found,
            Err(_) => {
                return (
                    vec![],
                    0,
                    Some(
                        [
                            b"unable to find a command for arguments: ".as_slice(),
                            &go_args(before),
                        ]
                        .concat(),
                    ),
                );
            }
        };
    if command.name == "__complete" {
        return (vec![], 0, None);
    }
    let flags = command.local_flags_borrowed(true);
    let mut value_flag = false;
    let mut flag_error = None;
    let mut prefix = last.as_slice();
    if !last.starts_with(b"-") || last.contains(&b'=') {
        let mut name: Option<&[u8]> = None;
        let mut equals = false;
        if last.starts_with(b"-")
            && let Some(at) = last.iter().position(|&b| b == b'=')
        {
            let flag = &last[..at];
            name = Some(
                flag.strip_prefix(b"--")
                    .unwrap_or_else(|| &flag[flag.len() - 1..]),
            );
            prefix = &last[at + 1..];
            equals = true;
        }
        if name.is_none()
            && let Some(previous) = remaining.last().filter(|s| {
                s.starts_with(b"-")
                    && s.as_slice() != b"-"
                    && s.as_slice() != b"--"
                    && !s.contains(&b'=')
            })
        {
            name = Some(
                previous
                    .strip_prefix(b"--")
                    .unwrap_or_else(|| &previous[previous.len() - 1..]),
            );
        }
        if let Some(name) = name {
            match flags.iter().find(|f| {
                f.name.as_bytes() == name
                    || !f.shorthand.is_empty() && f.shorthand.as_bytes() == name
            }) {
                Some(flag) if equals || flag.kind != FlagKind::Bool => {
                    value_flag = true;
                    if !equals {
                        remaining.pop();
                    }
                }
                Some(_) => {}
                None => {
                    flag_error = Some(
                        [
                            format!("Subcommand '{}' does not support flag '", command.name)
                                .as_bytes(),
                            name,
                            b"'",
                        ]
                        .concat(),
                    )
                }
            }
        }
    }
    let parsed = match crate::argv::parse(command, &path, &remaining) {
        Ok(parsed) => parsed,
        Err(error) => {
            return (
                vec![],
                0,
                Some(
                    [
                        b"Error while parsing flags from args ".as_slice(),
                        &go_args(&remaining),
                        b": ",
                        &error.message,
                    ]
                    .concat(),
                ),
            );
        }
    };
    let mut with_dash = remaining.clone();
    with_dash.push(b"--".to_vec());
    let after_dash = crate::argv::parse(command, &path, &with_dash)
        .is_ok_and(|extra| extra.positionals.len() > parsed.positionals.len());
    if !after_dash && let Some(error) = flag_error {
        return (vec![], 0, Some(error));
    }
    if parsed.explicit("help") {
        return (vec![], 4, None);
    }
    if value_flag {
        return (vec![], 0, None);
    }
    if prefix.starts_with(b"-") && !prefix.contains(&b'=') && !after_dash {
        let mut choices = Vec::new();
        for flag in flags {
            if parsed.explicit(&flag.name) && flag.kind != FlagKind::Strings {
                continue;
            }
            for name in [
                format!("--{}", flag.name),
                if flag.shorthand.is_empty() {
                    String::new()
                } else {
                    format!("-{}", flag.shorthand)
                },
            ] {
                if !name.is_empty() && name.as_bytes().starts_with(prefix) {
                    choices.push(format!("{name}\t{}", flag.usage));
                }
            }
        }
        return (choices, 4, None);
    }
    let mut choices = Vec::new();
    let mut directive = 0;
    if parsed.positionals.is_empty() && !parsed.flags.values().any(|f| f.explicit) {
        for child in &command.children {
            if child.hidden {
                continue;
            }
            directive = 4;
            if child.name.as_bytes().starts_with(prefix) {
                choices.push(format!("{}\t{}", child.name, child.short));
            }
        }
    }
    if path == "pb completion" || path.starts_with("pb completion ") {
        directive = 4;
    }
    (choices, directive, None)
}

pub(crate) fn generate(path: &str, parsed: &parser::ParsedInvocation) -> Option<Outcome> {
    let shell = path.strip_prefix("pb completion ")?;
    let comp = if parsed.boolean("no-descriptions") {
        "__completeNoDesc"
    } else {
        "__complete"
    };
    let template = match shell {
        "bash" => include_str!("completion/bash.tmpl"),
        "zsh" => include_str!("completion/zsh.tmpl"),
        "fish" => include_str!("completion/fish.tmpl"),
        "powershell" => include_str!("completion/powershell.tmpl"),
        _ => return None,
    };
    let parameters = if shell == "fish" || shell == "powershell" {
        vec![
            "pb",
            "pb",
            comp,
            "1",
            "2",
            "4",
            "8",
            "16",
            "32",
            "PB_ACTIVE_HELP",
        ]
    } else {
        vec!["pb", comp, "1", "2", "4", "8", "16", "32", "_activeHelp_ "]
    };
    let mut script = render_template(template, &parameters);
    if shell == "fish" {
        script.insert_str(
            0,
            &format!("# fish completion for {:<36} -*- shell-script -*-\n", "pb"),
        );
    }
    Some(Outcome::success(script))
}

// Go's source templates only use explicitly indexed string/integer parameters,
// one padded name, and %% escapes. Preserve every other shell byte verbatim.
fn render_template(template: &str, parameters: &[&str]) -> String {
    let mut output = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(index) = rest.find('%') {
        output.push_str(&rest[..index]);
        rest = &rest[index + 1..];
        if let Some(tail) = rest.strip_prefix('%') {
            output.push('%');
            rest = tail;
            continue;
        }
        let padded = rest.starts_with("-36");
        if padded {
            rest = &rest[3..];
        }
        assert!(rest.starts_with('['), "unknown Cobra template parameter");
        let end = rest.find(']').expect("indexed template parameter");
        let number: usize = rest[1..end].parse().expect("numeric parameter index");
        let value = parameters[number - 1];
        if padded {
            output.push_str(&format!("{value:<36}"));
        } else {
            output.push_str(value);
        }
        rest = &rest[end + 2..];
    }
    output.push_str(rest);
    output
}

#[cfg(test)]
mod protocol_spec_tests {
    use super::*;

    #[test]
    fn protocol_spec_matches_root_clone_with_overrides() {
        let mut expected = command_spec().clone();
        expected.name = "__complete".into();
        expected.r#use = "__complete [command-line]".into();
        expected.short = "Request shell completion choices for the specified command-line".into();
        expected.long = "__complete is a special command that is used by the shell completion logic\nto request completion choices for the specified command-line.".into();
        expected.aliases = vec!["__completeNoDesc".into()];
        expected.flags.clear();
        expected.children.clear();
        expected.args = ArgsRule {
            kind: ArgsKind::Min,
            counts: vec![1],
        };
        expected.disable_flags_in_use_line = true;
        expected.silence_usage = false;
        expected.hidden = true;
        expected.operation = Operation::Deferred;
        assert_eq!(protocol_spec(), expected);
    }

    #[test]
    fn root_child_overlay_matches_cloned_tree_discovery() {
        let root = command_spec();
        let protocol = protocol_spec();
        let mut cloned = root.clone();
        cloned.children.push(protocol_spec());
        for args in crate::argv::discovery_tests::cases(&cloned) {
            let expected = crate::argv::discovery_tests::legacy_discover(&cloned, &args)
                .map(|(command, path, remaining)| (command.name.as_str(), path, remaining))
                .map_err(|e| (e.message, e.root_find));
            let actual = crate::argv::discover_with_root_child(root, &args, Some(&protocol))
                .map(|(command, path, remaining)| (command.name.as_str(), path, remaining))
                .map_err(|e| (e.message, e.root_find));
            assert_eq!(actual, expected, "args={args:?}");
        }
    }

    #[test]
    fn root_child_overlay_preserves_existing_name_and_alias_precedence() {
        let root = command_spec();
        let mut protocol = protocol_spec();
        protocol.name = "sql".into();
        protocol.aliases.push("version".into());
        for args in [
            vec![b"sql".to_vec(), b"run".to_vec()],
            vec![b"version".to_vec()],
        ] {
            let actual =
                crate::argv::discover_with_root_child(root, &args, Some(&protocol)).unwrap();
            let expected = crate::argv::discover(root, &args).unwrap();
            assert_eq!(actual, expected);
        }
    }
}
