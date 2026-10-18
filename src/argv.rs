//! Byte-preserving OS arguments. Command identifiers are ASCII; values retain
//! their original bytes until a consumer chooses text, JSON, URL or filesystem
//! semantics. The UTF-8 entry point remains the common, allocation-light path.
use crate::{
    parser::{self, FlagValue, ParsedInvocation},
    spec::{ArgsKind, CommandSpec, FlagKind, FlagSpec},
};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default)]
pub struct RawValues {
    pub positionals: Vec<Vec<u8>>,
    pub flags: BTreeMap<String, Vec<Vec<u8>>>,
}

#[derive(Debug)]
pub(crate) struct Error {
    pub message: Vec<u8>,
    pub root_find: bool,
}
fn error(message: impl Into<Vec<u8>>) -> Error {
    Error {
        message: message.into(),
        root_find: false,
    }
}
fn join(parts: &[&[u8]]) -> Vec<u8> {
    parts.concat()
}

/// strconv.Quote retains valid UTF-8 and escapes each malformed byte as \xNN.
pub fn quote(bytes: &[u8]) -> String {
    let mut result = String::from("\"");
    let mut rest = bytes;
    while !rest.is_empty() {
        match std::str::from_utf8(rest) {
            Ok(text) => {
                let quoted = parser::quote(text);
                result.push_str(&quoted[1..quoted.len() - 1]);
                break;
            }
            Err(error) => {
                let count = error.valid_up_to();
                if count > 0 {
                    let quoted = parser::quote(std::str::from_utf8(&rest[..count]).unwrap());
                    result.push_str(&quoted[1..quoted.len() - 1]);
                }
                result.push_str(&format!("\\x{:02x}", rest[count]));
                rest = &rest[count + 1..];
            }
        }
    }
    result.push('"');
    result
}

pub(crate) fn discover<'a>(
    root: &'a CommandSpec,
    args: &[Vec<u8>],
) -> Result<(&'a CommandSpec, String, Vec<Vec<u8>>), Error> {
    discover_with_root_child(root, args, None)
}

/// Resolve against the ordinary tree with an optional hidden synthetic root child.
/// Existing children retain precedence; the child is never added to nested
/// commands. Completion uses this instead of cloning the entire owned tree.
pub(crate) fn discover_with_root_child<'a>(
    root: &'a CommandSpec,
    args: &[Vec<u8>],
    extra: Option<&'a CommandSpec>,
) -> Result<(&'a CommandSpec, String, Vec<Vec<u8>>), Error> {
    let mut command = root;
    let mut path = root.name.clone();
    let mut remaining = args.to_vec();
    loop {
        let mut first = None;
        let mut i = 0;
        while i < remaining.len() {
            let word = remaining[i].as_slice();
            if word == b"--" {
                break;
            }
            let is_bool = if let Some(long) = word.strip_prefix(b"--") {
                command
                    .flags
                    .iter()
                    .any(|f| f.name.as_bytes() == long && f.kind == FlagKind::Bool)
            } else if word.len() == 2 && word[0] == b'-' {
                command
                    .flags
                    .iter()
                    .any(|f| f.shorthand.as_bytes() == &word[1..] && f.kind == FlagKind::Bool)
            } else {
                false
            };
            if !word.contains(&b'=')
                && (word.starts_with(b"--") && !is_bool
                    || word.starts_with(b"-") && word.len() == 2 && !is_bool)
            {
                if remaining.len() - i - 1 <= 1 {
                    break;
                }
                i += 2;
                continue;
            }
            if !word.is_empty() && !word.starts_with(b"-") {
                first = Some(i);
                break;
            }
            i += 1;
        }
        let Some(index) = first else {
            break;
        };
        let word = &remaining[index];
        let child = command
            .children
            .iter()
            .chain(extra.filter(|_| std::ptr::eq(command, root)))
            .find(|c| c.name.as_bytes() == word || c.aliases.iter().any(|a| a.as_bytes() == word));
        let Some(child) = child else {
            if path == root.name && command.args.kind == ArgsKind::Arbitrary {
                // Reuse the Unicode suggestion rules when the token is valid;
                // malformed bytes cannot be equal to command identifiers.
                if let Ok(text) = std::str::from_utf8(word)
                    && let Err(error) = parser::discover(root, &[text.into()])
                {
                    return Err(Error {
                        message: error.message.into_bytes(),
                        root_find: true,
                    });
                }
                return Err(Error {
                    message: format!(
                        "unknown command {} for {}",
                        quote(word),
                        parser::quote(&path)
                    )
                    .into_bytes(),
                    root_find: true,
                });
            }
            break;
        };
        remaining.remove(index);
        path.push(' ');
        path.push_str(&child.name);
        command = child;
    }
    Ok((command, path, remaining))
}

fn assign(parsed: &mut ParsedInvocation, flag: &FlagSpec, raw: &[u8]) -> Result<(), Error> {
    let display = crate::error::go_string(raw);
    let value = if matches!(flag.kind, FlagKind::String | FlagKind::Strings) {
        FlagValue::Text(display)
    } else {
        parser::parse_value(flag.kind, &display).map_err(|cause| {
            let cause = if std::str::from_utf8(raw).is_err() {
                format!(
                    "strconv.Parse{}: parsing {}: {}",
                    if flag.kind == FlagKind::Bool {
                        "Bool"
                    } else {
                        "Int"
                    },
                    quote(raw),
                    if cause.ends_with("value out of range") {
                        "value out of range"
                    } else {
                        "invalid syntax"
                    }
                )
            } else {
                cause
            };
            let label = if flag.shorthand.is_empty() {
                format!("--{}", flag.name)
            } else {
                format!("-{}, --{}", flag.shorthand, flag.name)
            };
            error(format!(
                "invalid argument {} for {} flag: {cause}",
                quote(raw),
                parser::quote(&label)
            ))
        })?
    };
    let current = parsed.flags.get_mut(&flag.name).expect("declared flag");
    if let (FlagValue::Texts(values), FlagValue::Text(value)) = (&mut current.value, &value) {
        values.push(value.clone());
    } else {
        current.value = value;
    }
    current.explicit = true;
    let values = parsed.raw.flags.entry(flag.name.clone()).or_default();
    if flag.kind != FlagKind::Strings {
        values.clear();
    }
    values.push(raw.to_vec());
    Ok(())
}

pub(crate) fn parse(
    command: &CommandSpec,
    path: &str,
    args: &[Vec<u8>],
) -> Result<ParsedInvocation, Error> {
    let mut parsed = parser::parse(command, path, &[]).expect("valid command defaults");
    let flags = command.local_flags_borrowed(true);
    let mut index = 0;
    while index < args.len() {
        let token = args[index].as_slice();
        if token == b"--" {
            for value in &args[index + 1..] {
                parsed.positionals.push(crate::error::go_string(value));
                parsed.raw.positionals.push(value.clone());
            }
            break;
        }
        if let Some(body) = token.strip_prefix(b"--") {
            if body.is_empty() || matches!(body[0], b'-' | b'=') {
                return Err(error(join(&[b"bad flag syntax: ", token])));
            }
            let (name, inline) = match body.iter().position(|&b| b == b'=') {
                Some(at) => (&body[..at], Some(&body[at + 1..])),
                None => (body, None),
            };
            let flag = flags
                .iter()
                .find(|f| f.name.as_bytes() == name)
                .ok_or_else(|| error(join(&[b"unknown flag: --", name])))?;
            let raw = if let Some(value) = inline {
                value
            } else if flag.kind == FlagKind::Bool {
                b"true"
            } else {
                index += 1;
                args.get(index)
                    .map(Vec::as_slice)
                    .ok_or_else(|| error(join(&[b"flag needs an argument: ", token])))?
            };
            assign(&mut parsed, flag, raw)?;
        } else if token.starts_with(b"-") && token != b"-" {
            let mut short = &token[1..];
            while !short.is_empty() {
                if short.starts_with(b"test.") {
                    break;
                }
                let byte = short[0];
                let quoted = parser::shorthand_quote(byte);
                let flag = flags
                    .iter()
                    .find(|f| f.shorthand.as_bytes() == [byte])
                    .ok_or_else(|| {
                        error(join(&[
                            b"unknown shorthand flag: ",
                            quoted.as_bytes(),
                            b" in -",
                            short,
                        ]))
                    })?;
                let tail = &short[1..];
                if tail.starts_with(b"=") && tail.len() > 1 {
                    assign(&mut parsed, flag, &tail[1..])?;
                    break;
                }
                if flag.kind == FlagKind::Bool {
                    assign(&mut parsed, flag, b"true")?;
                    short = tail;
                } else if !tail.is_empty() {
                    assign(&mut parsed, flag, tail)?;
                    break;
                } else {
                    index += 1;
                    let raw = args.get(index).ok_or_else(|| {
                        error(join(&[
                            b"flag needs an argument: ",
                            quoted.as_bytes(),
                            b" in -",
                            short,
                        ]))
                    })?;
                    assign(&mut parsed, flag, raw)?;
                    break;
                }
            }
        } else {
            parsed.positionals.push(crate::error::go_string(token));
            parsed.raw.positionals.push(token.to_vec());
        }
        index += 1;
    }
    Ok(parsed)
}

pub(crate) fn render_error(
    error: Error,
    command: &CommandSpec,
    path: &str,
    structured: bool,
) -> crate::Outcome {
    if error.root_find && !structured {
        return crate::Outcome {
            stderr: [
                b"Error: ".as_slice(),
                &error.message,
                b"\nRun 'pb --help' for usage.\n",
            ]
            .concat(),
            exit_code: 1,
            ..crate::Outcome::default()
        };
    }
    let display = crate::error::go_string(&error.message);
    crate::error::CliError::new(crate::input_code(&display), display)
        .with_raw_message(error.message)
        .render(command, path, structured)
}

#[cfg(test)]
pub(crate) mod discovery_tests {
    use super::*;

    // Snapshot of discovery before the first-index/overlay optimization. Keep
    // the full nonflag scan as an independent oracle for token skip rules.
    pub(crate) fn legacy_discover<'a>(
        root: &'a CommandSpec,
        args: &[Vec<u8>],
    ) -> Result<(&'a CommandSpec, String, Vec<Vec<u8>>), Error> {
        let mut command = root;
        let mut path = root.name.clone();
        let mut remaining = args.to_vec();
        loop {
            let mut words = Vec::new();
            let mut i = 0;
            while i < remaining.len() {
                let word = remaining[i].as_slice();
                if word == b"--" {
                    break;
                }
                let is_bool = if let Some(long) = word.strip_prefix(b"--") {
                    command
                        .flags
                        .iter()
                        .any(|f| f.name.as_bytes() == long && f.kind == FlagKind::Bool)
                } else if word.len() == 2 && word[0] == b'-' {
                    command
                        .flags
                        .iter()
                        .any(|f| f.shorthand.as_bytes() == &word[1..] && f.kind == FlagKind::Bool)
                } else {
                    false
                };
                if !word.contains(&b'=')
                    && (word.starts_with(b"--") && !is_bool
                        || word.starts_with(b"-") && word.len() == 2 && !is_bool)
                {
                    if remaining.len() - i - 1 <= 1 {
                        break;
                    }
                    i += 2;
                    continue;
                }
                if !word.is_empty() && !word.starts_with(b"-") {
                    words.push(i);
                }
                i += 1;
            }
            let Some(&index) = words.first() else {
                break;
            };
            let word = &remaining[index];
            let child = command.children.iter().find(|c| {
                c.name.as_bytes() == word || c.aliases.iter().any(|a| a.as_bytes() == word)
            });
            let Some(child) = child else {
                if path == root.name && command.args.kind == ArgsKind::Arbitrary {
                    // Reuse the Unicode suggestion rules when the token is valid;
                    // malformed bytes cannot be equal to command identifiers.
                    if let Ok(text) = std::str::from_utf8(word)
                        && let Err(error) = parser::discover(root, &[text.into()])
                    {
                        return Err(Error {
                            message: error.message.into_bytes(),
                            root_find: true,
                        });
                    }
                    return Err(Error {
                        message: format!(
                            "unknown command {} for {}",
                            quote(word),
                            parser::quote(&path)
                        )
                        .into_bytes(),
                        root_find: true,
                    });
                }
                break;
            };
            remaining.remove(index);
            path.push(' ');
            path.push_str(&child.name);
            command = child;
        }
        Ok((command, path, remaining))
    }

    pub(crate) fn cases(root: &CommandSpec) -> Vec<Vec<Vec<u8>>> {
        let tokens: &[&[u8]] = &[
            b"sql",
            b"run",
            b"help",
            b"version",
            b"vers",
            b"profil",
            b"completion",
            b"bash",
            b"__complete",
            b"__completeNoDesc",
            b"--",
            b"",
            b"--help",
            b"-h",
            b"--version",
            b"--output",
            b"-o",
            b"json",
            b"--output=json",
            b"--unknown",
            b"-x",
            b"--=",
            b"--help=false",
            b"-",
            b"-h=false",
            b"--top=x",
            b"--top",
            b"-test.foo",
            b"\xff",
            b"--\xff",
            b"-\xc3\xa9",
            "jſon".as_bytes(),
        ];
        let mut result = vec![Vec::new()];
        for a in tokens {
            result.push(vec![a.to_vec()]);
            for b in tokens {
                result.push(vec![a.to_vec(), b.to_vec()]);
                for c in tokens {
                    result.push(vec![a.to_vec(), b.to_vec(), c.to_vec()]);
                }
            }
        }
        fn walk(command: &CommandSpec, path: &[Vec<u8>], result: &mut Vec<Vec<Vec<u8>>>) {
            for tail in [
                vec![],
                vec![b"--help".to_vec()],
                vec![b"--".to_vec(), b"run".to_vec()],
                vec![b"--unknown".to_vec(), b"value".to_vec(), b"run".to_vec()],
            ] {
                let mut case = path.to_vec();
                case.extend(tail);
                result.push(case);
            }
            for flag in &command.flags {
                let mut case = path.to_vec();
                case.extend([
                    format!("--{}", flag.name).into_bytes(),
                    b"value".to_vec(),
                    b"run".to_vec(),
                ]);
                result.push(case);
            }
            for child in &command.children {
                for name in std::iter::once(&child.name).chain(&child.aliases) {
                    let mut path = path.to_vec();
                    path.push(name.as_bytes().to_vec());
                    walk(child, &path, result);
                }
            }
        }
        walk(root, &[], &mut result);
        result
    }

    #[test]
    fn first_nonflag_discovery_matches_legacy_for_text_bytes_aliases_and_flags() {
        let root = crate::spec::command_spec();
        for args in cases(root) {
            let expected = legacy_discover(root, &args).map_err(|e| (e.message, e.root_find));
            let actual = discover(root, &args).map_err(|e| (e.message, e.root_find));
            assert_eq!(actual, expected, "byte args={args:?}");
            if let Ok(text) = args
                .iter()
                .map(|s| String::from_utf8(s.clone()))
                .collect::<Result<Vec<_>, _>>()
            {
                let textual = parser::discover(root, &text)
                    .map(|(c, p, a)| (c, p, a.into_iter().map(String::into_bytes).collect()))
                    .map_err(|e| (e.message.into_bytes(), e.root_find));
                assert_eq!(textual, expected, "text args={args:?}");
            }
        }
    }
}
