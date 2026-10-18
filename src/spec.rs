//! Owned command declarations. Metadata mirrors command registrations in main.go
//! and cmd/*.go; it is input to parsing and rendering, never captured CLI output.
use serde::Deserialize;
use serde_json::Value;
use std::{borrow::Cow, sync::OnceLock};

#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct CommandSpec {
    pub name: String,
    pub operation: Operation,
    #[serde(default)]
    pub disable_flags_in_use_line: bool,
    pub r#use: String,
    pub short: String,
    pub long: String,
    pub example: String,
    pub aliases: Vec<String>,
    pub flags: Vec<FlagSpec>,
    pub children: Vec<CommandSpec>,
    pub args: ArgsRule,
    pub silence_usage: bool,
    pub requires_profile: bool,
    pub wrapped_preflight: bool,
    #[serde(default)]
    pub hidden: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Root,
    Help,
    Version,
    Agent,
    Status,
    Metadata,
    IngestFiles,
    Observe,
    CompletionGroup,
    Administration,
    Deferred,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct ArgsRule {
    pub kind: ArgsKind,
    #[serde(default)]
    pub counts: Vec<usize>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ArgsKind {
    Arbitrary,
    Exact,
    Max,
    Min,
    Range,
    None,
    ProfileAdd,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct FlagSpec {
    pub name: String,
    pub shorthand: String,
    pub kind: FlagKind,
    pub default: Value,
    pub usage: String,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum FlagKind {
    String,
    Bool,
    Int,
    Strings,
}

impl CommandSpec {
    pub fn description(&self) -> &str {
        if self.long.is_empty() {
            &self.short
        } else {
            &self.long
        }
    }
    pub fn child(&self, token: &str) -> Option<&Self> {
        self.children
            .iter()
            .find(|c| c.name == token || c.aliases.iter().any(|a| a == token))
    }
    pub fn local_flags(&self, help: bool) -> Vec<FlagSpec> {
        let mut flags = self.flags.clone();
        if help {
            flags.push(FlagSpec {
                name: "help".into(),
                shorthand: "h".into(),
                kind: FlagKind::Bool,
                default: Value::Bool(false),
                usage: format!("help for {}", self.name),
            });
        }
        flags.sort_by(|a, b| a.name.cmp(&b.name));
        flags
    }
    /// Internal consumers borrow declarations; only the synthesized help flag
    /// needs ownership. Keep the public owned adapter and its ordering intact.
    pub(crate) fn local_flags_borrowed(&self, help: bool) -> Vec<Cow<'_, FlagSpec>> {
        let mut flags: Vec<_> = self.flags.iter().map(Cow::Borrowed).collect();
        if help {
            flags.push(Cow::Owned(FlagSpec {
                name: "help".into(),
                shorthand: "h".into(),
                kind: FlagKind::Bool,
                default: Value::Bool(false),
                usage: format!("help for {}", self.name),
            }));
        }
        flags.sort_by(|a, b| a.name.cmp(&b.name));
        flags
    }
    pub fn usage(&self, path: &str, help: bool) -> String {
        let suffix = self.r#use.strip_prefix(&self.name).unwrap_or("");
        let mut usage = format!("{path}{suffix}");
        if !self.disable_flags_in_use_line
            && (help || !self.flags.is_empty())
            && !usage.contains("[flags]")
        {
            usage.push_str(" [flags]");
        }
        usage
    }
}

// Defines `generated_command_spec()`, built by build.rs from spec.json so the
// specification is not parsed from JSON on every invocation.
include!(concat!(env!("OUT_DIR"), "/command_spec.rs"));

pub fn command_spec() -> &'static CommandSpec {
    static ROOT: OnceLock<CommandSpec> = OnceLock::new();
    ROOT.get_or_init(generated_command_spec)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parsed() -> CommandSpec {
        serde_json::from_str(include_str!("spec.json")).expect("valid owned command specification")
    }

    fn count(spec: &CommandSpec) -> (usize, usize) {
        spec.children
            .iter()
            .map(count)
            .fold((1, spec.flags.len()), |(c, f), (cc, cf)| (c + cc, f + cf))
    }

    #[test]
    fn generated_spec_matches_runtime_json_parse() {
        let parsed = parsed();
        let generated = generated_command_spec();
        let (commands, flags) = count(&parsed);
        assert!(commands > 1 && flags > 0, "spec.json unexpectedly empty");
        assert_eq!(count(&generated), (commands, flags));
        assert_eq!(generated, parsed);
        assert_eq!(command_spec(), &parsed);
    }

    #[test]
    fn generated_defaults_render_like_parsed_defaults() {
        // Value equality under arbitrary_precision compares number text; also
        // check the Display form that parsing and help rendering consume.
        fn walk(a: &CommandSpec, b: &CommandSpec) {
            for (x, y) in a.flags.iter().zip(&b.flags) {
                assert_eq!(x.default.to_string(), y.default.to_string(), "{}", x.name);
                assert_eq!(x.default.as_i64(), y.default.as_i64(), "{}", x.name);
            }
            a.children
                .iter()
                .zip(&b.children)
                .for_each(|(x, y)| walk(x, y));
        }
        walk(&generated_command_spec(), &parsed());
    }

    #[test]
    fn borrowed_local_flags_match_owned_fields_order_and_help() {
        fn check(command: &CommandSpec) {
            for help in [false, true] {
                let owned = command.local_flags(help);
                let borrowed = command.local_flags_borrowed(help);
                assert_eq!(
                    borrowed.iter().map(Cow::as_ref).collect::<Vec<_>>(),
                    owned.iter().collect::<Vec<_>>()
                );
                let mut synthesized = 0;
                for flag in &borrowed {
                    match flag {
                        Cow::Borrowed(flag) => assert!(
                            command
                                .flags
                                .iter()
                                .any(|source| std::ptr::eq(source, *flag)),
                            "declared flags must retain their source allocation"
                        ),
                        Cow::Owned(flag) => {
                            synthesized += 1;
                            assert_eq!(flag.name, "help");
                            assert_eq!(flag.usage, format!("help for {}", command.name));
                        }
                    }
                }
                assert_eq!(synthesized, usize::from(help));
            }
            command.children.iter().for_each(check);
        }
        check(command_spec());

        // Public declarations need not be sorted or have unique flag names.
        // Retain the stable ordering of duplicates, including a declared help.
        let mut custom = command_spec().clone();
        custom.children.clear();
        custom.flags.reverse();
        custom.flags.extend([
            FlagSpec {
                name: "help".into(),
                shorthand: "custom".into(),
                kind: FlagKind::String,
                default: Value::String("custom default".into()),
                usage: "declared help".into(),
            },
            FlagSpec {
                name: "help".into(),
                shorthand: "second".into(),
                kind: FlagKind::Strings,
                default: Value::Null,
                usage: "second declared help".into(),
            },
        ]);
        check(&custom);
    }
}
