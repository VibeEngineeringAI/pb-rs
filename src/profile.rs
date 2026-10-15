//! Configuration commands, including their interactive pickers, credential
//! form and logout confirmation. No credential validation occurs here.
use crate::{
    ErrorCode,
    config::{Config, ConfigError, ProfileRecord, ProfileStore},
    parser::{ParsedInvocation, quote},
    render,
    tui::profile::PickerOutcome,
};
use serde::Serialize;
use std::{collections::BTreeMap, fmt};

/// Interactive steps the profile commands need. Production uses
/// `tui::profile::TerminalPrompts`; tests script the answers.
pub(crate) trait Prompts {
    /// Text Go prints to stdout before or between interactive steps.
    fn say(&mut self, text: &str);
    /// defaultprofile picker; Err is the Bubble Tea program error text.
    fn select_profile(
        &mut self,
        profiles: &BTreeMap<String, ProfileRecord>,
    ) -> Result<PickerOutcome, String>;
    /// credential form `Values()` after the program exits.
    fn credentials(&mut self) -> Result<FormOutcome, String>;
    /// Cooked `Continue? [y/N]:` line prompt.
    fn confirm_logout(&mut self, name: &str, url: &str) -> bool;
}

/// How the credential form program ended.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum FormOutcome {
    /// Normal quit: `Values()` (both or neither).
    Values(String, String),
    /// Update/View panicked; the value is the Go panic text.
    Panicked(String),
}

/// A recovered Bubble Tea panic in a program whose caller asserts the
/// returned model without `ok`: Run returned a nil model, so the assertion
/// panics fatally. Go prints recoverFromPanic's text on stdout and the
/// runtime panic on stderr, exit status 2 (PTY oracle, L-AUTH-3). The two
/// goroutine traces are a scoped diagnostic difference (L-AUTH-10).
fn go_panic(value: &str, model: &str) -> ProfileError {
    let message = format!("interface conversion: tea.Model is nil, not {model}");
    let mut error = ProfileError::new(ErrorCode::CommandFailed, message.clone());
    error.stdout = crate::tui::profile::caught_panic_stdout(value);
    error.fatal = Some(format!("panic: {message}\n"));
    error
}
const PICKER_MODEL: &str = "defaultprofile.Model";
const CREDENTIAL_MODEL: &str = "credential.Model";

#[derive(Debug)]
pub struct ProfileError {
    pub code: ErrorCode,
    pub message: String,
    pub stdout: String,
    /// A Go runtime panic rather than a command error: stderr text written
    /// verbatim (no `Error:` rendering) and exit status 2.
    pub fatal: Option<String>,
}
impl ProfileError {
    pub(crate) fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            stdout: String::new(),
            fatal: None,
        }
    }
    fn context(mut self, prefix: &str) -> Self {
        self.message = format!("{prefix}{}", self.message);
        self
    }
}
impl From<ConfigError> for ProfileError {
    fn from(error: ConfigError) -> Self {
        Self::new(error.code, error.message)
    }
}
impl fmt::Display for ProfileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for ProfileError {}

/// Deliberately distinct from the persisted record: credentials never enter
/// serialized command output, even when a malformed record contains all modes.
#[derive(Serialize)]
pub struct ProfileOutput<'a> {
    url: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    username: &'a str,
    cloud: bool,
    #[serde(skip_serializing_if = "str::is_empty")]
    tenant_id: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    workspace_id: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    workspace_name: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    orchestrator_url: &'a str,
}
pub fn safe_profile_output(profile: &ProfileRecord) -> ProfileOutput<'_> {
    ProfileOutput {
        url: &profile.url,
        username: &profile.username,
        cloud: profile.cloud,
        tenant_id: &profile.tenant_id,
        workspace_id: &profile.workspace_id,
        workspace_name: &profile.workspace_name,
        orchestrator_url: &profile.orchestrator_url,
    }
}

/// Login and self-hosted add overwrite after *any* read error, including
/// corruption. Cloud profile creation uses a separate, stricter policy below.
pub fn save_login_profile(
    store: &ProfileStore,
    name: &str,
    profile: ProfileRecord,
) -> Result<(), ProfileError> {
    let mut config = store.read().unwrap_or_default();
    config.profiles.insert(name.to_owned(), profile);
    config.default_profile = name.to_owned();
    store.write(&config).map_err(Into::into)
}
pub fn save_cloud_profile(
    store: &ProfileStore,
    name: &str,
    profile: ProfileRecord,
    force: bool,
    make_default: bool,
) -> Result<(), ProfileError> {
    if name.is_empty() {
        return Err(ProfileError::new(
            ErrorCode::CommandFailed,
            "profile name is required",
        ));
    }
    let mut config = match store.read() {
        Ok(config) => config,
        Err(error) if error.is_not_found() => Config::default(),
        Err(error) => return Err(ProfileError::from(error).context("failed to read config: ")),
    };
    if config.profiles.contains_key(name) && !force {
        return Err(ProfileError::new(
            ErrorCode::CommandFailed,
            format!(
                "profile {} already exists. use --force to overwrite",
                quote(name)
            ),
        ));
    }
    config.profiles.insert(name.to_owned(), profile);
    if config.default_profile.is_empty() || make_default {
        config.default_profile = name.to_owned();
    }
    store.write(&config).map_err(Into::into)
}

pub fn cloud_add_input(parsed: &ParsedInvocation) -> Result<(String, String), ProfileError> {
    let key = parsed.text("api-key").trim();
    if key.is_empty() {
        return Err(ProfileError::new(
            ErrorCode::CommandFailed,
            "api key is required. pass --api-key",
        ));
    }
    let endpoint = parsed.text("orchestrator-url").trim();
    let endpoint = if endpoint.is_empty() {
        option_env!("PB_CLOUD_ORCHESTRATOR_URL")
            .unwrap_or("")
            .trim()
    } else {
        endpoint
    };
    Ok((key.to_owned(), endpoint.to_owned()))
}

/// Called only after the HTTP adapter validates the workspace/key response.
pub fn cloud_profile_add(
    parsed: &ParsedInvocation,
    store: &ProfileStore,
    profile: ProfileRecord,
) -> Result<String, ProfileError> {
    let explicit_name = parsed.text("name").trim();
    let name = if explicit_name.is_empty() {
        let workspace = if profile.workspace_name.trim().is_empty() {
            profile.workspace_id.trim()
        } else {
            profile.workspace_name.trim()
        };
        workspace
            .chars()
            .map(|c| c.to_lowercase().next().unwrap_or(c))
            .collect::<String>()
            .replace(' ', "-")
    } else {
        explicit_name.to_owned()
    };
    save_cloud_profile(store, &name, profile.clone(), parsed.boolean("force"), true)?;
    if parsed.text("output").trim().eq_ignore_ascii_case("json") {
        #[derive(Serialize)]
        struct Added<'a> {
            status: &'a str,
            name: &'a str,
            profile: ProfileOutput<'a>,
        }
        let output = serde_json::to_string(&Added {
            status: "success",
            name: &name,
            profile: safe_profile_output(&profile),
        })
        .expect("owned output");
        Ok(go_json_escape(output) + "\n")
    } else {
        let workspace = if profile.workspace_name.is_empty() {
            profile.workspace_id.clone()
        } else {
            format!("{} ({})", profile.workspace_name, profile.workspace_id)
        };
        Ok(format!(
            "Cloud profile {name} added successfully\nWorkspace: {workspace}\nURL: {}\n",
            profile.url
        ))
    }
}
pub(crate) fn go_json_escape(text: String) -> String {
    text.replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

/// `pb login` (cmd/login.go). Configuration is touched only at the wizard's
/// duplicate-name check and when saving, so a missing $HOME fails there.
pub fn login(_parsed: &ParsedInvocation) -> Result<String, crate::error::CliError> {
    crate::tui::login::run()
}

pub fn execute(
    parsed: &ParsedInvocation,
    store: &ProfileStore,
) -> Result<Option<String>, ProfileError> {
    execute_with(parsed, store, &mut crate::tui::profile::TerminalPrompts)
}

pub(crate) fn execute_with(
    parsed: &ParsedInvocation,
    store: &ProfileStore,
    prompts: &mut dyn Prompts,
) -> Result<Option<String>, ProfileError> {
    let args = &parsed.positionals;
    let json = parsed.text("output") == "json";
    let output = match parsed.command_path.as_str() {
        "pb profile add" => {
            let name = &args[0];
            let url = parse_profile_url(&args[1]).map_err(|e| {
                ProfileError::new(ErrorCode::CommandFailed, format!("error parsing URL: {e}"))
            })?;
            let mut profile = ProfileRecord {
                url,
                ..ProfileRecord::default()
            };
            let key = parsed.text("api-key").trim();
            if !key.is_empty() {
                profile.api_key = key.to_owned();
            } else if args.len() < 4 {
                // A third positional username does not seed the form.
                match prompts.credentials().map_err(|e| {
                    ProfileError::new(
                        ErrorCode::CommandFailed,
                        format!("error reading credentials: {e}"),
                    )
                })? {
                    FormOutcome::Values(username, password) => {
                        profile.username = username;
                        profile.password = password;
                    }
                    FormOutcome::Panicked(value) => return Err(go_panic(&value, CREDENTIAL_MODEL)),
                }
            } else {
                profile.username = args[2].clone();
                profile.password = args[3].clone();
            }
            save_login_profile(store, name, profile.clone())?;
            if json {
                render::json(&safe_profile_output(&profile), true)
            } else {
                format!("Profile {name} added successfully\n")
            }
        }
        "pb profile list" => {
            let config = store.read()?;
            if parsed.text("output").trim().eq_ignore_ascii_case("json") {
                let profiles: std::collections::BTreeMap<_, _> = config
                    .profiles
                    .iter()
                    .map(|(name, profile)| (name, safe_profile_output(profile)))
                    .collect();
                render::json(&profiles, false)
            } else {
                let mut text = String::new();
                for (name, profile) in &config.profiles {
                    let mut lines = vec![name.clone(), format!("url: {}", profile.url)];
                    if !profile.username.is_empty() {
                        lines.push(format!("user: {}", profile.username));
                    }
                    // lipgloss normalizes each styled string before measuring
                    // and padding the outer item, including embedded lines.
                    let lines: Vec<String> = lines
                        .into_iter()
                        .flat_map(|line| {
                            line.replace('\t', "    ")
                                .replace("\r\n", "\n")
                                .split('\n')
                                .map(str::to_owned)
                                .collect::<Vec<_>>()
                        })
                        .collect();
                    let width = lines
                        .iter()
                        .map(|s| unicode_width::UnicodeWidthStr::width(s.as_str()))
                        .max()
                        .unwrap_or(0);
                    let prefix = if config.default_profile == *name {
                        "│ "
                    } else {
                        " "
                    };
                    for line in lines {
                        let padding = width
                            .saturating_sub(unicode_width::UnicodeWidthStr::width(line.as_str()));
                        text.push_str(&format!("{prefix}{line}{}\n", " ".repeat(padding)));
                    }
                    text.push('\n');
                }
                text
            }
        }
        "pb profile update" => {
            let name = &args[0];
            let url = &args[1];
            parse_profile_url(url).map_err(|e| {
                ProfileError::new(ErrorCode::CommandFailed, format!("invalid URL: {e}"))
            })?;
            let mut config = store
                .read()
                .map_err(|e| ProfileError::from(e).context("error reading config: "))?;
            let profile = config.profiles.get_mut(name).ok_or_else(|| {
                ProfileError::new(
                    ErrorCode::CommandFailed,
                    format!("no profile found with the name: {name}"),
                )
            })?;
            profile.url = url.clone();
            store.write(&config)?;
            format!("Profile '{name}' URL updated to {url}\n")
        }
        "pb profile default" => {
            let mut config = store.read()?;
            let name = match args.first() {
                Some(name) => name.clone(),
                None => match prompts
                    .select_profile(&config.profiles)
                    .map_err(|e| ProfileError::new(ErrorCode::CommandFailed, e))?
                {
                    PickerOutcome::Chosen(name) => name,
                    PickerOutcome::Panicked(value) => return Err(go_panic(&value, PICKER_MODEL)),
                    // Cancellation writes nothing and prints nothing.
                    PickerOutcome::Canceled | PickerOutcome::Continue => {
                        return Ok(Some(String::new()));
                    }
                },
            };
            let name = &name;
            if !config.profiles.contains_key(name) {
                return Err(ProfileError::new(
                    ErrorCode::NotFound,
                    format!("profile {name} does not exist"),
                ));
            }
            config.default_profile = name.clone();
            store.write(&config)?;
            let message = format!("{name} is now set as default profile");
            if json {
                render::json(&message, true)
            } else {
                message + "\n"
            }
        }
        "pb profile remove" => {
            let name = &args[0];
            let mut config = store.read()?;
            if config.profiles.remove(name).is_none() {
                return Err(ProfileError::new(
                    ErrorCode::NotFound,
                    format!("no profile found with the name: {name}"),
                ));
            }
            let mut prefix = String::new();
            if config.default_profile == *name {
                match config.profiles.len() {
                    0 => config.default_profile.clear(),
                    1 => {
                        config.default_profile = config.profiles.keys().next().unwrap().clone();
                        prefix = format!(
                            "'{}' is now set as the default profile\n",
                            config.default_profile
                        );
                    }
                    _ => {
                        prompts.say("Select a new default profile:\n");
                        match prompts.select_profile(&config.profiles).map_err(|e| {
                            ProfileError::new(
                                ErrorCode::CommandFailed,
                                format!("error selecting new default profile: {e}"),
                            )
                        })? {
                            PickerOutcome::Chosen(choice) => {
                                prefix = format!("'{choice}' is now set as the default profile\n");
                                config.default_profile = choice;
                            }
                            PickerOutcome::Panicked(value) => {
                                return Err(go_panic(&value, PICKER_MODEL));
                            }
                            // The original is still removed; no default remains.
                            PickerOutcome::Canceled | PickerOutcome::Continue => {
                                config.default_profile.clear();
                            }
                        }
                    }
                }
            }
            store.write(&config).map_err(|e| {
                let mut error = ProfileError::from(e);
                error.stdout = prefix.clone();
                error
            })?;
            let message = format!("Deleted profile {name}");
            prefix
                + &if json {
                    render::json(&message, true)
                } else {
                    message + "\n"
                }
        }
        "pb logout" => logout(parsed, store, prompts)?,
        _ => return Ok(None),
    };
    Ok(Some(output))
}

fn logout(
    parsed: &ParsedInvocation,
    store: &ProfileStore,
    prompts: &mut dyn Prompts,
) -> Result<String, ProfileError> {
    let json = parsed.text("output") == "json";
    let yes = parsed.boolean("yes");
    if json && !yes {
        return Err(ProfileError::new(
            ErrorCode::CommandFailed,
            "use --yes with -o json to avoid interactive prompts",
        ));
    }
    let mut config = store.read().map_err(|_| {
        ProfileError::new(
            ErrorCode::CommandFailed,
            "no config found — nothing to logout from",
        )
    })?;
    let mut name = config.default_profile.clone();
    if name.is_empty() || !config.profiles.contains_key(&name) {
        match config.profiles.len() {
            0 => {
                return Err(ProfileError::new(
                    ErrorCode::CommandFailed,
                    "no active profile found",
                ));
            }
            1 => name = config.profiles.keys().next().unwrap().clone(),
            _ if yes || json => {
                return Err(ProfileError::new(
                    ErrorCode::CommandFailed,
                    "no active profile found; set one with: pb profile default <name>",
                ));
            }
            _ => {
                prompts.say("Select profile to logout:\n");
                match prompts.select_profile(&config.profiles).map_err(|e| {
                    ProfileError::new(
                        ErrorCode::CommandFailed,
                        format!("error selecting profile to logout: {e}"),
                    )
                })? {
                    PickerOutcome::Chosen(choice) => name = choice,
                    PickerOutcome::Panicked(value) => return Err(go_panic(&value, PICKER_MODEL)),
                    PickerOutcome::Canceled | PickerOutcome::Continue => {
                        return Ok("Logout canceled\n".into());
                    }
                }
            }
        }
    }
    // Go indexes the map with the chosen title; a title is always a key.
    let url = config
        .profiles
        .get(&name)
        .map(|p| p.url.clone())
        .unwrap_or_default();
    if !yes && !prompts.confirm_logout(&name, &url) {
        return Ok("Logout canceled\n".into());
    }
    let profile = config.profiles.remove(&name).unwrap_or_default();
    // One survivor becomes default; with --yes the first sorted name does.
    config.default_profile = config.profiles.keys().next().cloned().unwrap_or_default();
    if config.profiles.len() > 1 && !yes {
        prompts.say("Select a new default profile:\n");
        match prompts.select_profile(&config.profiles).map_err(|e| {
            ProfileError::new(
                ErrorCode::CommandFailed,
                format!("error selecting new default profile: {e}"),
            )
        })? {
            PickerOutcome::Chosen(choice) => config.default_profile = choice,
            PickerOutcome::Panicked(value) => return Err(go_panic(&value, PICKER_MODEL)),
            // Confirmed logout still removes the profile; no default remains.
            PickerOutcome::Canceled | PickerOutcome::Continue => config.default_profile.clear(),
        }
    }
    store
        .write(&config)
        .map_err(|e| ProfileError::from(e).context("failed to update config: "))?;
    if json {
        #[derive(Serialize)]
        struct LoggedOut<'a> {
            status: &'a str,
            profile: &'a str,
            url: &'a str,
            removed: bool,
            default_profile: &'a str,
        }
        Ok(render::json(
            &LoggedOut {
                status: "ok",
                profile: &name,
                url: &profile.url,
                removed: true,
                default_profile: &config.default_profile,
            },
            true,
        ))
    } else {
        let mut text = format!("Logged out and removed profile '{name}'\n");
        if !config.default_profile.is_empty() {
            text.push_str(&format!(
                "'{}' is now set as the default profile\n",
                config.default_profile
            ));
        }
        Ok(text)
    }
}

/// Profile URL parsing allows relative references and opaque schemes. The URL
/// crate's stricter absolute-URL policy would reject Go-readable configurations.
pub fn parse_profile_url(raw: &str) -> Result<String, String> {
    let error = |detail: String| format!("parse {}: {detail}", quote(raw));
    if raw.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err(error("net/url: invalid control character in URL".into()));
    }
    let (before_fragment, fragment) = raw
        .split_once('#')
        .map_or((raw, None), |(a, b)| (a, Some(b)));
    let (base, query) = before_fragment
        .split_once('?')
        .map_or((before_fragment, None), |(a, b)| (a, Some(b)));
    let mut scheme = String::new();
    let mut rest = base;
    if let Some(colon) = base.find(':') {
        let candidate = &base[..colon];
        if candidate.is_empty() {
            return Err(error("missing protocol scheme".into()));
        }
        if candidate.as_bytes()[0].is_ascii_alphabetic()
            && candidate
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"+-.".contains(&b))
        {
            scheme = candidate.to_ascii_lowercase();
            rest = &base[colon + 1..];
        } else if !candidate.contains('/') {
            return Err(error(
                "first path segment in URL cannot contain colon".into(),
            ));
        }
    }
    let mut out = if scheme.is_empty() {
        String::new()
    } else {
        format!("{scheme}:")
    };
    if let Some(authority_path) = rest.strip_prefix("//") {
        let split = authority_path.find('/').unwrap_or(authority_path.len());
        let authority = &authority_path[..split];
        let path = &authority_path[split..];
        let (userinfo, host) = authority
            .rsplit_once('@')
            .map_or((None, authority), |(a, b)| (Some(a), b));
        if let Some(userinfo) = userinfo {
            if userinfo
                .bytes()
                .any(|b| !(b.is_ascii_alphanumeric() || b"-._~!$&'()*+,;=:%@".contains(&b)))
            {
                return Err(error("net/url: invalid userinfo".into()));
            }
            check_escapes(userinfo).map_err(&error)?;
        }
        let normalized_host = normalize_bracket_host(host).map_err(&error)?;
        let host = normalized_host.as_str();
        let port = if host.contains('[') {
            let close = host
                .rfind(']')
                .ok_or_else(|| error("missing ']' in host".into()))?;
            &host[close + 1..]
        } else {
            host.rfind(':').map_or("", |colon| &host[colon..])
        };
        if !port.is_empty()
            && (!port.starts_with(':') || !port[1..].bytes().all(|b| b.is_ascii_digit()))
        {
            return Err(error(format!("invalid port {} after host", quote(port))));
        }
        check_escapes(host).map_err(&error)?;
        if let Some(bad) = host.bytes().find(|b| {
            b.is_ascii() && !(b.is_ascii_alphanumeric() || b"!$&'()*+,-.:;=[]_~%".contains(b))
        }) {
            return Err(error(format!(
                "invalid character {} in host name",
                quote(&(bad as char).to_string())
            )));
        }
        if !authority.is_empty() || !path.is_empty() {
            out.push_str("//");
        }
        if let Some(userinfo) = userinfo {
            if let Some((user, password)) = userinfo.split_once(':') {
                out.push_str(&escape_bytes(&decode_percent(user), b"$&+,;=_~-."));
                out.push(':');
                out.push_str(&escape_bytes(&decode_percent(password), b"$&+,;=_~-."));
            } else {
                out.push_str(&escape_bytes(&decode_percent(userinfo), b"$&+,;=_~-."));
            }
            out.push('@');
        }
        let host_bytes = host.as_bytes();
        let mut i = 0;
        while i + 2 < host_bytes.len() {
            if host_bytes[i] == b'%' {
                let decoded = u8::from_str_radix(&host[i + 1..i + 3], 16).expect("checked escape");
                let zone_start = host.find("%25");
                let in_zone = zone_start.is_some_and(|start| i >= start);
                if decoded < 0x80 && decoded != b'%' && !in_zone {
                    return Err(error(format!(
                        "invalid URL escape {}",
                        quote(&host[i..i + 3])
                    )));
                }
                i += 3;
            } else {
                i += 1;
            }
        }
        out.push_str(&escape_bytes(&decode_percent(host), b"!$&'()*+,-.:;=[]_~"));
        check_escapes(path).map_err(&error)?;
        out.push_str(&escape_path(path, b"!$&'()*+,-./:;=@_~"));
    } else if !scheme.is_empty() && !rest.starts_with('/') {
        // Opaque scheme data is preserved verbatim, including percent escapes.
        out.push_str(rest);
    } else {
        check_escapes(rest).map_err(&error)?;
        out.push_str(&escape_path(rest, b"!$&'()*+,-./:;=@_~"));
    }
    if let Some(query) = query {
        out.push('?');
        out.push_str(query);
    }
    if let Some(fragment) = fragment {
        check_escapes(fragment).map_err(error)?;
        if !fragment.is_empty() {
            out.push('#');
            out.push_str(&escape_path(fragment, b"!$&'()*+,-./:;=?@_~"));
        }
    }
    Ok(out)
}

fn normalize_bracket_host(host: &str) -> Result<String, String> {
    let Some(open) = host.rfind('[') else {
        return Ok(host.into());
    };
    let close = host
        .rfind(']')
        .filter(|close| *close > open)
        .ok_or_else(|| "missing ']' in host".to_owned())?;
    let port = &host[close + 1..];
    if !port.is_empty()
        && (!port.starts_with(':') || !port[1..].bytes().all(|b| b.is_ascii_digit()))
    {
        return Err(format!("invalid port {} after host", quote(port)));
    }
    let inner = &host[open + 1..close];
    check_escapes(inner)?;
    let bytes = inner.as_bytes();
    let zone = inner.find("%25");
    let mut i = 0;
    while i + 2 < bytes.len() {
        if bytes[i] == b'%' {
            let decoded = u8::from_str_radix(&inner[i + 1..i + 3], 16).expect("checked escape");
            let in_zone = zone.is_some_and(|zone| i >= zone);
            let permitted_zone = in_zone
                && (decoded == b' '
                    || decoded.is_ascii_alphanumeric()
                    || b"!$&'()*+,-.:;=[]_~%".contains(&decoded));
            if decoded < 0x80 && decoded != b'%' && !permitted_zone {
                return Err(format!("invalid URL escape {}", quote(&inner[i..i + 3])));
            }
            i += 3;
        } else {
            i += 1;
        }
    }
    let decoded = String::from_utf8_lossy(&decode_percent(inner)).into_owned();
    let address = decoded.split('%').next().unwrap_or_default();
    if decoded
        .split_once('%')
        .is_some_and(|(_, zone)| zone.is_empty())
    {
        return Err(format!(
            "invalid host: ParseAddr({}): zone must be a non-empty string",
            quote(&decoded)
        ));
    }
    if address.parse::<std::net::Ipv4Addr>().is_ok() {
        return Err("invalid IP-literal".into());
    }
    if address.parse::<std::net::Ipv6Addr>().is_err() {
        let detail = if !address.contains([':', '.']) {
            "unable to parse IP".to_owned()
        } else if !address.contains(':') {
            format!("unexpected character (at {})", quote(address))
        } else if let Some((offset, _)) = address
            .char_indices()
            .find(|(_, c)| !c.is_ascii_hexdigit() && *c != ':' && *c != '.')
        {
            let start = address[..offset].rfind(':').map_or(0, |i| i + 1);
            format!(
                "each colon-separated field must have at least one digit (at {})",
                quote(&address[start..])
            )
        } else {
            "address string too short".to_owned()
        };
        return Err(format!(
            "invalid host: ParseAddr({}): {detail}",
            quote(&decoded)
        ));
    }
    Ok(format!("[{}]{port}", inner))
}
fn check_escapes(value: &str) -> Result<(), String> {
    let bytes = value.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 2 >= bytes.len()
                || !bytes[i + 1].is_ascii_hexdigit()
                || !bytes[i + 2].is_ascii_hexdigit()
            {
                let invalid = String::from_utf8_lossy(&bytes[i..(i + 3).min(bytes.len())]);
                return Err(format!("invalid URL escape {}", quote(&invalid)));
            }
            i += 3;
        } else {
            i += 1;
        }
    }
    Ok(())
}
fn decode_percent(value: &str) -> Vec<u8> {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            out.push(u8::from_str_radix(&value[i + 1..i + 3], 16).expect("checked escape"));
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    out
}
fn escape_path(value: &str, safe: &[u8]) -> String {
    if value
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || safe.contains(&b) || b"%[]".contains(&b))
    {
        value.into()
    } else {
        escape_bytes(&decode_percent(value), safe)
    }
}
fn escape_bytes(value: &[u8], safe: &[u8]) -> String {
    let mut out = String::new();
    for &byte in value {
        if byte.is_ascii_alphanumeric() || safe.contains(&byte) {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

#[cfg(test)]
mod interactive_tests {
    use super::*;
    use std::collections::VecDeque;

    #[derive(Default)]
    struct Script {
        said: String,
        picks: VecDeque<Result<PickerOutcome, String>>,
        offered: Vec<Vec<String>>,
        credentials: Option<Result<FormOutcome, String>>,
        confirms: VecDeque<bool>,
        confirmed: Vec<(String, String)>,
    }
    impl Prompts for Script {
        fn say(&mut self, text: &str) {
            self.said.push_str(text);
        }
        fn select_profile(
            &mut self,
            profiles: &BTreeMap<String, ProfileRecord>,
        ) -> Result<PickerOutcome, String> {
            self.offered.push(profiles.keys().cloned().collect());
            self.picks.pop_front().expect("unexpected picker")
        }
        fn credentials(&mut self) -> Result<FormOutcome, String> {
            self.credentials.take().expect("unexpected credential form")
        }
        fn confirm_logout(&mut self, name: &str, url: &str) -> bool {
            self.confirmed.push((name.into(), url.into()));
            self.confirms.pop_front().expect("unexpected confirmation")
        }
    }

    struct Fixture {
        store: ProfileStore,
        dir: std::path::PathBuf,
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }
    fn fixture(names: &[&str], default: &str) -> Fixture {
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "pb-auth-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let store = ProfileStore::new(dir.join("pb/config.toml"));
        if !names.is_empty() {
            let config = Config {
                profiles: names
                    .iter()
                    .map(|n| {
                        (
                            n.to_string(),
                            ProfileRecord {
                                url: format!("http://{n}"),
                                ..Default::default()
                            },
                        )
                    })
                    .collect(),
                default_profile: default.into(),
            };
            store.write(&config).unwrap();
        }
        Fixture { store, dir }
    }

    fn run(
        fixture: &Fixture,
        args: &[&str],
        script: &mut Script,
    ) -> Result<Option<String>, ProfileError> {
        let args: Vec<String> = args.iter().map(|s| s.to_string()).collect();
        let (command, path, remaining) =
            crate::parser::discover(crate::spec::command_spec(), &args).expect("command");
        let parsed = crate::parser::parse(command, &path, &remaining).expect("arguments");
        execute_with(&parsed, &fixture.store, script)
    }

    #[test]
    fn profile_add_saves_form_values_even_after_cancel() {
        let f = fixture(&["old"], "old");
        let mut s = Script {
            credentials: Some(Ok(FormOutcome::Values(String::new(), String::new()))),
            ..Default::default()
        };
        let out = run(&f, &["profile", "add", "p", "http://h", "seed"], &mut s).unwrap();
        assert_eq!(out.as_deref(), Some("Profile p added successfully\n"));
        let config = f.store.read().unwrap();
        assert_eq!(config.default_profile, "p");
        let p = &config.profiles["p"];
        assert!(p.username.is_empty() && p.password.is_empty() && p.url == "http://h");

        let mut s = Script {
            credentials: Some(Err("could not open a new TTY".into())),
            ..Default::default()
        };
        let err = run(&f, &["profile", "add", "q", "http://h"], &mut s).unwrap_err();
        assert_eq!(
            err.message,
            "error reading credentials: could not open a new TTY"
        );
        assert!(!f.store.read().unwrap().profiles.contains_key("q"));
    }

    #[test]
    fn default_picker_choice_and_silent_cancel() {
        let f = fixture(&["a", "b"], "a");
        let mut s = Script::default();
        s.picks.push_back(Ok(PickerOutcome::Chosen("b".into())));
        let out = run(&f, &["profile", "default"], &mut s).unwrap();
        assert_eq!(out.as_deref(), Some("b is now set as default profile\n"));
        assert_eq!(f.store.read().unwrap().default_profile, "b");

        let before = std::fs::read(f.store.path()).unwrap();
        s.picks.push_back(Ok(PickerOutcome::Canceled));
        assert_eq!(
            run(&f, &["profile", "default"], &mut s).unwrap().as_deref(),
            Some("")
        );
        assert_eq!(std::fs::read(f.store.path()).unwrap(), before);
        assert_eq!(s.said, "");
    }

    #[test]
    fn empty_default_picker_enter_is_a_go_fatal_panic() {
        let f = fixture(&[], "");
        f.store.write(&Config::default()).unwrap();
        let before = std::fs::read(f.store.path()).unwrap();
        let mut s = Script::default();
        s.picks.push_back(Ok(PickerOutcome::Panicked(
            crate::tui::profile::NIL_DEREFERENCE.into(),
        )));
        let err = run(&f, &["profile", "default"], &mut s).unwrap_err();
        // Matches the pinned Go PTY oracle (auth-oracle/go-picker.json).
        assert_eq!(
            err.stdout,
            "Caught panic:\n\nruntime error: invalid memory address or nil pointer dereference\n\nRestoring terminal...\n\n"
        );
        assert_eq!(
            err.fatal.as_deref(),
            Some("panic: interface conversion: tea.Model is nil, not defaultprofile.Model\n")
        );
        assert_eq!(std::fs::read(f.store.path()).unwrap(), before);
    }

    #[test]
    fn credential_form_panic_is_fatal_and_writes_nothing() {
        let f = fixture(&["old"], "old");
        let before = std::fs::read(f.store.path()).unwrap();
        let mut s = Script {
            credentials: Some(Ok(FormOutcome::Panicked(
                "runtime error: index out of range [5] with length 5".into(),
            ))),
            ..Default::default()
        };
        let err = run(&f, &["profile", "add", "p", "http://h"], &mut s).unwrap_err();
        assert_eq!(
            err.stdout,
            "Caught panic:\n\nruntime error: index out of range [5] with length 5\n\nRestoring terminal...\n\n"
        );
        assert_eq!(
            err.fatal.as_deref(),
            Some("panic: interface conversion: tea.Model is nil, not credential.Model\n")
        );
        assert_eq!(std::fs::read(f.store.path()).unwrap(), before);
    }

    #[test]
    fn removing_default_asks_for_replacement_and_cancel_clears_it() {
        let f = fixture(&["a", "b", "c"], "a");
        let mut s = Script::default();
        s.picks.push_back(Ok(PickerOutcome::Chosen("c".into())));
        let out = run(&f, &["profile", "remove", "a"], &mut s).unwrap();
        assert_eq!(s.said, "Select a new default profile:\n");
        assert_eq!(s.offered, vec![vec!["b".to_string(), "c".to_string()]]);
        assert_eq!(
            out.as_deref(),
            Some("'c' is now set as the default profile\nDeleted profile a\n")
        );

        let f = fixture(&["a", "b", "c"], "a");
        let mut s = Script::default();
        s.picks.push_back(Ok(PickerOutcome::Canceled));
        let out = run(&f, &["profile", "remove", "a"], &mut s).unwrap();
        assert_eq!(out.as_deref(), Some("Deleted profile a\n"));
        let config = f.store.read().unwrap();
        assert!(config.default_profile.is_empty() && !config.profiles.contains_key("a"));

        // A picker program error aborts before any write.
        let f = fixture(&["a", "b", "c"], "a");
        let mut s = Script::default();
        s.picks.push_back(Err("boom".into()));
        let err = run(&f, &["profile", "remove", "a"], &mut s).unwrap_err();
        assert_eq!(err.message, "error selecting new default profile: boom");
        assert!(f.store.read().unwrap().profiles.contains_key("a"));
    }

    #[test]
    fn logout_confirmation_selection_and_replacement() {
        // Declining writes nothing.
        let f = fixture(&["a", "b"], "a");
        let mut s = Script {
            confirms: [false].into(),
            ..Default::default()
        };
        let before = std::fs::read(f.store.path()).unwrap();
        assert_eq!(
            run(&f, &["logout"], &mut s).unwrap().as_deref(),
            Some("Logout canceled\n")
        );
        assert_eq!(s.confirmed, vec![("a".to_string(), "http://a".to_string())]);
        assert_eq!(std::fs::read(f.store.path()).unwrap(), before);

        // Confirmed with two survivors: replacement picker; cancel clears.
        let f = fixture(&["a", "b", "c"], "a");
        let mut s = Script {
            confirms: [true].into(),
            ..Default::default()
        };
        s.picks.push_back(Ok(PickerOutcome::Canceled));
        let out = run(&f, &["logout"], &mut s).unwrap();
        assert_eq!(out.as_deref(), Some("Logged out and removed profile 'a'\n"));
        assert_eq!(s.said, "Select a new default profile:\n");
        let config = f.store.read().unwrap();
        assert!(config.default_profile.is_empty() && config.profiles.len() == 2);

        // No usable default: choose which profile to log out; cancel stops.
        let f = fixture(&["a", "b"], "gone");
        let mut s = Script::default();
        s.picks.push_back(Ok(PickerOutcome::Canceled));
        assert_eq!(
            run(&f, &["logout"], &mut s).unwrap().as_deref(),
            Some("Logout canceled\n")
        );
        assert_eq!(s.said, "Select profile to logout:\n");

        // Chosen profile, confirmed, single survivor becomes default.
        let f = fixture(&["a", "b"], "");
        let mut s = Script {
            confirms: [true].into(),
            ..Default::default()
        };
        s.picks.push_back(Ok(PickerOutcome::Chosen("b".into())));
        let out = run(&f, &["logout"], &mut s).unwrap();
        assert_eq!(
            out.as_deref(),
            Some("Logged out and removed profile 'b'\n'a' is now set as the default profile\n")
        );

        // --yes bypasses every prompt and picks the first sorted survivor.
        let f = fixture(&["a", "c", "b"], "a");
        let mut s = Script::default();
        let out = run(&f, &["logout", "--yes"], &mut s).unwrap();
        assert_eq!(
            out.as_deref(),
            Some("Logged out and removed profile 'a'\n'b' is now set as the default profile\n")
        );
    }
}
