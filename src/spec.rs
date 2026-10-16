//! Owned command declarations. Metadata mirrors command registrations in main.go
//! and cmd/*.go; it is input to parsing and rendering, never captured CLI output.
use serde::Deserialize;
use serde_json::Value;
use std::sync::OnceLock;

#[derive(Clone, Debug, Deserialize)]
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

#[derive(Clone, Debug, Deserialize)]
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

#[derive(Clone, Debug, Deserialize)]
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

pub fn command_spec() -> &'static CommandSpec {
    static ROOT: OnceLock<CommandSpec> = OnceLock::new();
    ROOT.get_or_init(|| {
        serde_json::from_str(include_str!("spec.json")).expect("valid owned command specification")
    })
}
