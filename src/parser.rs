//! Sequential parsing from owned declarations, with Cobra discovery and pflag
//! lexical compatibility. Flags stay local; explicit presence survives defaults.
//!
//! A clap adapter was tested first. Concrete oracle cases requiring first-error
//! token provenance (`--top=x --top`, `-xa -xb`, UTF-8 shorthands) and pflag's
//! assignment/syntax rules forced a parallel tokenizer. Keeping one sequential
//! parser avoids synthetic flag collisions and duplicated validation.
use crate::spec::{ArgsKind, CommandSpec, FlagKind, FlagSpec};
use std::{borrow::Cow, collections::BTreeMap};

#[derive(Clone, Debug, PartialEq)]
pub enum FlagValue {
    Text(String),
    Bool(bool),
    Int(i64),
    Texts(Vec<String>),
}
#[derive(Clone, Debug, PartialEq)]
pub struct ParsedFlag {
    pub value: FlagValue,
    pub explicit: bool,
}
#[derive(Clone, Debug)]
pub struct ParsedInvocation {
    pub command_path: String,
    pub positionals: Vec<String>,
    pub flags: BTreeMap<String, ParsedFlag>,
    pub raw: crate::argv::RawValues,
}
impl ParsedInvocation {
    pub fn positional_bytes(&self, index: usize) -> &[u8] {
        self.raw
            .positionals
            .get(index)
            .map(Vec::as_slice)
            .unwrap_or_else(|| self.positionals.get(index).map_or(b"", |s| s.as_bytes()))
    }
    pub fn text_bytes(&self, name: &str) -> &[u8] {
        self.raw
            .flags
            .get(name)
            .and_then(|values| values.last())
            .map(Vec::as_slice)
            .unwrap_or_else(|| self.text(name).as_bytes())
    }

    pub fn text(&self, name: &str) -> &str {
        match self.flags.get(name).map(|f| &f.value) {
            Some(FlagValue::Text(v)) => v,
            _ => "",
        }
    }
    pub fn boolean(&self, name: &str) -> bool {
        matches!(
            self.flags.get(name).map(|f| &f.value),
            Some(FlagValue::Bool(true))
        )
    }
    pub fn explicit(&self, name: &str) -> bool {
        self.flags.get(name).is_some_and(|f| f.explicit)
    }
}

#[derive(Debug)]
pub struct ParseError {
    pub message: String,
    pub root_find: bool,
}

fn first_non_flag(args: &[String], command: &CommandSpec) -> Option<usize> {
    let mut i = 0;
    while i < args.len() {
        let s = &args[i];
        if s == "--" {
            break;
        }
        let bool_flag = if let Some(long) = s.strip_prefix("--") {
            command
                .flags
                .iter()
                .any(|f| f.name == long && f.kind == FlagKind::Bool)
        } else if s.starts_with('-') && s.len() == 2 {
            command
                .flags
                .iter()
                .any(|f| f.shorthand == s[1..] && f.kind == FlagKind::Bool)
        } else {
            false
        };
        if !s.contains('=')
            && ((s.starts_with("--") && !bool_flag)
                || (s.starts_with('-') && s.len() == 2 && !bool_flag))
        {
            if args.len() - i - 1 <= 1 {
                break;
            }
            i += 2;
            continue;
        }
        if !s.is_empty() && !s.starts_with('-') {
            return Some(i);
        }
        i += 1;
    }
    None
}

/// Resolve aliases and remove command words, preserving all flag/value tokens.
pub fn discover<'a>(
    root: &'a CommandSpec,
    args: &[String],
) -> Result<(&'a CommandSpec, String, Vec<String>), ParseError> {
    let mut command = root;
    let mut path = root.name.clone();
    let mut remaining = args.to_vec();
    while let Some(index) = first_non_flag(&remaining, command) {
        let word = &remaining[index];
        let Some(child) = command.child(word) else {
            if path == root.name && command.args.kind == ArgsKind::Arbitrary {
                let mut message = format!("unknown command {} for {}", quote(word), quote(&path));
                let suggestions: Vec<&str> = command
                    .children
                    .iter()
                    .filter(|c| {
                        !c.hidden
                            && c.name != "help"
                            && (simple_lower(&c.name).starts_with(&simple_lower(word))
                                || distance(&c.name, word) <= 2)
                    })
                    .map(|c| c.name.as_str())
                    .collect();
                if !suggestions.is_empty() {
                    message.push_str("\n\nDid you mean this?\n");
                    for name in suggestions {
                        message.push_str(&format!("\t{name}\n"));
                    }
                }
                return Err(ParseError {
                    message,
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

/// Go strconv.Quote, used inside diagnostics (distinct from JSON escaping).
pub fn quote(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\u{7}' => out.push_str("\\a"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{b}' => out.push_str("\\v"),
            c if c < ' ' || c == '\u{7f}' => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => {
                let printable = crate::printable::is_printable(c);
                if printable {
                    out.push(c);
                } else if (c as u32) < 0x10000 {
                    out.push_str(&format!("\\u{:04x}", c as u32));
                } else {
                    out.push_str(&format!("\\U{:08x}", c as u32));
                }
            }
        }
    }
    out.push('"');
    out
}
fn simple_lower(value: &str) -> String {
    // Go strings.ToLower applies simple rune mappings, without expanding İ.
    value
        .chars()
        .map(|c| c.to_lowercase().next().unwrap_or(c))
        .collect()
}

fn distance(a: &str, b: &str) -> usize {
    // Cobra's suggestion metric counts UTF-8 bytes after Unicode lowercasing.
    let a = simple_lower(a);
    let b = simple_lower(b);
    let mut row: Vec<usize> = (0..=b.len()).collect();
    for (i, ac) in a.bytes().enumerate() {
        let mut diagonal = row[0];
        row[0] = i + 1;
        for (j, bc) in b.bytes().enumerate() {
            let old = row[j + 1];
            row[j + 1] = (row[j + 1] + 1)
                .min(row[j] + 1)
                .min(diagonal + usize::from(ac != bc));
            diagonal = old;
        }
    }
    row.last().copied().unwrap_or(0)
}

pub(crate) fn parse_value(kind: FlagKind, value: &str) -> Result<FlagValue, String> {
    match kind {
        FlagKind::String | FlagKind::Strings => Ok(FlagValue::Text(value.into())),
        FlagKind::Bool => match value {
            "1" | "t" | "T" | "true" | "TRUE" | "True" => Ok(FlagValue::Bool(true)),
            "0" | "f" | "F" | "false" | "FALSE" | "False" => Ok(FlagValue::Bool(false)),
            _ => Err(format!(
                "strconv.ParseBool: parsing {}: invalid syntax",
                quote(value)
            )),
        },
        FlagKind::Int => {
            let negative = value.starts_with('-');
            let raw = value.strip_prefix(['-', '+']).unwrap_or(value);
            let (base, digits, prefix) = if raw.starts_with("0x") || raw.starts_with("0X") {
                (16, &raw[2..], true)
            } else if raw.starts_with("0b") || raw.starts_with("0B") {
                (2, &raw[2..], true)
            } else if raw.starts_with("0o") || raw.starts_with("0O") {
                (8, &raw[2..], true)
            } else if raw.len() > 1 && raw.starts_with('0') {
                (8, &raw[1..], true)
            } else {
                (10, raw, false)
            };
            let error =
                |detail: &str| format!("strconv.ParseInt: parsing {}: {detail}", quote(value));
            let mut previous_digit = prefix;
            let mut valid_underscores = true;
            let mut saw_digit = false;
            let mut magnitude = 0_u64;
            for digit in digits.chars() {
                if digit == '_' {
                    valid_underscores &= previous_digit;
                    previous_digit = false;
                    continue;
                }
                let number = digit
                    .to_digit(base)
                    .filter(|_| digit.is_ascii())
                    .ok_or_else(|| error("invalid syntax"))?;
                magnitude = magnitude
                    .checked_mul(u64::from(base))
                    .and_then(|n| n.checked_add(u64::from(number)))
                    .ok_or_else(|| error("value out of range"))?;
                saw_digit = true;
                previous_digit = true;
            }
            // Go scans unsigned magnitude before checking underscore placement
            // or signed range. Thus uint64 overflow wins over later bad syntax.
            if !saw_digit || !previous_digit || !valid_underscores {
                return Err(error("invalid syntax"));
            }
            let signed_limit = (i64::MAX as u64) + u64::from(negative);
            if magnitude > signed_limit {
                return Err(error("value out of range"));
            }
            let signed = if negative {
                -(i128::from(magnitude))
            } else {
                i128::from(magnitude)
            };
            Ok(FlagValue::Int(signed as i64))
        }
    }
}

fn parse_error(message: String) -> ParseError {
    ParseError {
        message,
        root_find: false,
    }
}

pub(crate) fn shorthand_quote(byte: u8) -> String {
    let character = char::from(byte);
    if character == '\'' {
        return "'\\''".into();
    }
    let encoded = quote(&character.to_string());
    format!("'{}'", encoded[1..encoded.len() - 1].replace("\\\"", "\""))
}

fn assign(parsed: &mut ParsedInvocation, flag: &FlagSpec, raw: &str) -> Result<(), ParseError> {
    let value = parse_value(flag.kind, raw).map_err(|cause| {
        let label = if flag.shorthand.is_empty() {
            format!("--{}", flag.name)
        } else {
            format!("-{}, --{}", flag.shorthand, flag.name)
        };
        parse_error(format!(
            "invalid argument {} for {} flag: {cause}",
            quote(raw),
            quote(&label)
        ))
    })?;
    let current = parsed
        .flags
        .get_mut(&flag.name)
        .expect("declared flag default");
    if let (FlagValue::Texts(values), FlagValue::Text(value)) = (&mut current.value, &value) {
        values.push(value.clone());
    } else {
        current.value = value;
    }
    current.explicit = true;
    Ok(())
}

pub fn parse(
    command: &CommandSpec,
    path: &str,
    args: &[String],
) -> Result<ParsedInvocation, ParseError> {
    let flags = command.local_flags_borrowed(true);
    let defaults = flags
        .iter()
        .map(|flag| {
            let value = if flag.kind == FlagKind::Strings {
                FlagValue::Texts(Vec::new())
            } else {
                let raw = flag
                    .default
                    .as_str()
                    .map(Cow::Borrowed)
                    .unwrap_or_else(|| Cow::Owned(flag.default.to_string()));
                parse_value(flag.kind, &raw).expect("valid flag default")
            };
            (
                flag.name.clone(),
                ParsedFlag {
                    value,
                    explicit: false,
                },
            )
        })
        .collect();
    let mut parsed = ParsedInvocation {
        command_path: path.into(),
        raw: Default::default(),
        positionals: Vec::new(),
        flags: defaults,
    };
    let mut index = 0;
    while index < args.len() {
        let token = &args[index];
        if token == "--" {
            parsed.positionals.extend_from_slice(&args[index + 1..]);
            break;
        }
        if let Some(body) = token.strip_prefix("--") {
            if body.starts_with(['-', '=']) || body.is_empty() {
                return Err(parse_error(format!("bad flag syntax: {token}")));
            }
            let (name, inline) = body
                .split_once('=')
                .map_or((body, None), |(name, value)| (name, Some(value)));
            let flag = flags
                .iter()
                .find(|flag| flag.name == name)
                .ok_or_else(|| parse_error(format!("unknown flag: --{name}")))?;
            let raw = if let Some(value) = inline {
                value
            } else if flag.kind == FlagKind::Bool {
                "true"
            } else {
                index += 1;
                args.get(index)
                    .map(String::as_str)
                    .ok_or_else(|| parse_error(format!("flag needs an argument: {token}")))?
            };
            assign(&mut parsed, flag, raw)?;
        } else if token.starts_with('-') && token != "-" {
            let mut short = &token[1..];
            while !short.is_empty() {
                // pflag reserves Go's testing flag prefix at each cluster step.
                if short.starts_with("test.") {
                    break;
                }
                // Registered shorthands are ASCII. pflag diagnoses an unknown
                // first UTF-8 byte as a rune, keeping the remaining token bytes.
                let byte = short.as_bytes()[0];
                let flag = flags
                    .iter()
                    .find(|flag| flag.shorthand.as_bytes() == [byte])
                    .ok_or_else(|| {
                        parse_error(format!(
                            "unknown shorthand flag: {} in -{short}",
                            shorthand_quote(byte)
                        ))
                    })?;
                let tail = &short[1..];
                if tail.starts_with('=') && tail.len() > 1 {
                    assign(&mut parsed, flag, &tail[1..])?;
                    break;
                } else if flag.kind == FlagKind::Bool {
                    assign(&mut parsed, flag, "true")?;
                    short = tail;
                } else if !tail.is_empty() {
                    assign(&mut parsed, flag, tail)?;
                    break;
                } else {
                    index += 1;
                    let raw = args.get(index).map(String::as_str).ok_or_else(|| {
                        parse_error(format!(
                            "flag needs an argument: {} in -{short}",
                            shorthand_quote(byte)
                        ))
                    })?;
                    assign(&mut parsed, flag, raw)?;
                    break;
                }
            }
        } else {
            parsed.positionals.push(token.clone());
        }
        index += 1;
    }
    Ok(parsed)
}

pub fn validate_args(command: &CommandSpec, parsed: &ParsedInvocation) -> Result<(), String> {
    let n = parsed.positionals.len();
    let counts = &command.args.counts;
    let err = match command.args.kind {
        ArgsKind::None if n > 0 => Some(format!(
            "unknown command {} for {}",
            crate::argv::quote(parsed.positional_bytes(0)),
            quote(&parsed.command_path)
        )),
        ArgsKind::Exact if n != counts[0] => {
            Some(format!("accepts {} arg(s), received {n}", counts[0]))
        }
        ArgsKind::Max if n > counts[0] => Some(format!(
            "accepts at most {} arg(s), received {n}",
            counts[0]
        )),
        ArgsKind::Min if n < counts[0] => Some(format!(
            "requires at least {} arg(s), only received {n}",
            counts[0]
        )),
        ArgsKind::Range if n < counts[0] || n > counts[1] => Some(format!(
            "accepts between {} and {} arg(s), received {n}",
            counts[0], counts[1]
        )),
        ArgsKind::ProfileAdd if n < 2 => {
            Some(format!("requires at least 2 arg(s), only received {n}"))
        }
        ArgsKind::ProfileAdd if !parsed.text("api-key").trim().is_empty() && n != 2 => {
            Some("--api-key cannot be combined with username/password".into())
        }
        ArgsKind::ProfileAdd if n > 4 => Some(format!("accepts at most 4 arg(s), received {n}")),
        _ => None,
    };
    err.map_or(Ok(()), Err)
}

fn equal_fold_json(value: &str) -> bool {
    // For an ASCII target, Go SimpleFold has one extra equivalent relevant to
    // "json": U+017F LONG S belongs to the S/s case-folding cycle.
    let mut characters = value.chars();
    "json".chars().all(|expected| {
        characters.next().is_some_and(|actual| {
            actual.eq_ignore_ascii_case(&expected) || (expected == 's' && actual == '\u{17f}')
        })
    }) && characters.next().is_none()
}

pub fn requests_json(args: &[String]) -> bool {
    for (index, arg) in args.iter().enumerate() {
        let lower = arg.trim().to_ascii_lowercase();
        if lower == "--" {
            break;
        }
        if matches!(lower.as_str(), "--output=json" | "-o=json" | "-ojson") {
            return true;
        }
        if matches!(lower.as_str(), "--output" | "-o")
            && args
                .get(index + 1)
                .is_some_and(|s| equal_fold_json(s.trim()))
        {
            return true;
        }
    }
    false
}
