//! Noninteractive configuration commands. No credential validation occurs here.
use crate::{
    ErrorCode,
    config::{Config, ConfigError, ProfileRecord, ProfileStore},
    parser::{ParsedInvocation, quote},
    render,
};
use serde::Serialize;
use std::fmt;

#[derive(Debug)]
pub struct ProfileError {
    pub code: ErrorCode,
    pub message: String,
    pub stdout: String,
}
impl ProfileError {
    fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            stdout: String::new(),
        }
    }
    fn deferred(action: &str) -> Self {
        Self::new(
            ErrorCode::CommandFailed,
            format!("interactive {action} is not implemented in this Rust phase"),
        )
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
fn go_json_escape(text: String) -> String {
    text.replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

pub fn execute(
    parsed: &ParsedInvocation,
    store: &ProfileStore,
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
                return Err(ProfileError::deferred("credential entry"));
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
            let Some(name) = args.first() else {
                return Err(ProfileError::deferred("default profile selection"));
            };
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
                    _ => return Err(ProfileError::deferred("new default profile selection")),
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
        "pb logout" => logout(parsed, store)?,
        "pb login" => return Err(ProfileError::deferred("login wizard")),
        _ => return Ok(None),
    };
    Ok(Some(output))
}

fn logout(parsed: &ParsedInvocation, store: &ProfileStore) -> Result<String, ProfileError> {
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
            _ => return Err(ProfileError::deferred("logout profile selection")),
        }
    }
    if !yes {
        return Err(ProfileError::deferred("logout confirmation"));
    }
    let profile = config.profiles.remove(&name).expect("selected profile");
    config.default_profile = config.profiles.keys().next().cloned().unwrap_or_default();
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
