//! Owned, permissive persisted profiles. Authentication is resolved separately.
use crate::ErrorCode;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fmt, fs,
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
};

static CONFIG_WRITE: Mutex<()> = Mutex::new(());

#[derive(Clone, Default, PartialEq, Serialize)]
pub struct ProfileRecord {
    pub url: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub username: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub password: String,
    pub cloud: bool,
    #[serde(rename = "apiKey", skip_serializing_if = "String::is_empty")]
    pub api_key: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub session_token: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub refresh_token: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub tenant_id: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub workspace_id: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub workspace_name: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub orchestrator_url: String,
}

impl fmt::Debug for ProfileRecord {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ProfileRecord")
            .field("url", &self.url)
            .field("username", &self.username)
            .field("cloud", &self.cloud)
            .field("tenant_id", &self.tenant_id)
            .field("workspace_id", &self.workspace_id)
            .field("workspace_name", &self.workspace_name)
            .field("orchestrator_url", &self.orchestrator_url)
            .field("password", &"[REDACTED]")
            .field("api_key", &"[REDACTED]")
            .field("session_token", &"[REDACTED]")
            .field("refresh_token", &"[REDACTED]")
            .finish()
    }
}

// Independent legacy fields allow coexistence; serde aliases would reject the
// duplicate fields before applying Go's nonempty precedence rule.
#[derive(Default, Deserialize)]
#[serde(default)]
struct ProfileWire {
    url: String,
    username: String,
    password: String,
    cloud: bool,
    #[serde(rename = "apiKey")]
    canonical_api_key: String,
    api_key: String,
    token: String,
    session_token: String,
    refresh_token: String,
    tenant_id: String,
    workspace_id: String,
    workspace_name: String,
    orchestrator_url: String,
}
impl<'de> Deserialize<'de> for ProfileRecord {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let wire = ProfileWire::deserialize(deserializer)?;
        let api_key = if !wire.canonical_api_key.is_empty() {
            wire.canonical_api_key
        } else if !wire.api_key.is_empty() {
            wire.api_key
        } else {
            wire.token
        };
        Ok(Self {
            url: wire.url,
            username: wire.username,
            password: wire.password,
            cloud: wire.cloud,
            api_key,
            session_token: wire.session_token,
            refresh_token: wire.refresh_token,
            tenant_id: wire.tenant_id,
            workspace_id: wire.workspace_id,
            workspace_name: wire.workspace_name,
            orchestrator_url: wire.orchestrator_url,
        })
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    #[serde(rename = "Profiles")]
    pub profiles: BTreeMap<String, ProfileRecord>,
    #[serde(rename = "DefaultProfile")]
    pub default_profile: String,
}

#[derive(Debug)]
pub struct ConfigError {
    pub code: ErrorCode,
    pub message: String,
    kind: Option<std::io::ErrorKind>,
}
impl ConfigError {
    pub fn is_not_found(&self) -> bool {
        self.kind == Some(std::io::ErrorKind::NotFound)
    }
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            kind: None,
        }
    }
    fn io(operation: &str, path: &Path, error: std::io::Error) -> Self {
        let code = match error.kind() {
            std::io::ErrorKind::NotFound => ErrorCode::NotFound,
            std::io::ErrorKind::PermissionDenied => ErrorCode::PermissionDenied,
            _ => ErrorCode::ConnectionFailed,
        };
        let detail = match error.kind() {
            std::io::ErrorKind::NotFound => "no such file or directory".to_owned(),
            std::io::ErrorKind::PermissionDenied => "permission denied".to_owned(),
            std::io::ErrorKind::IsADirectory => "is a directory".to_owned(),
            std::io::ErrorKind::NotADirectory => "not a directory".to_owned(),
            _ => error.to_string(),
        };
        Self {
            code,
            message: format!("{operation} {}: {detail}", path.display()),
            kind: Some(error.kind()),
        }
    }
}
impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for ConfigError {}

#[derive(Clone, Debug)]
pub struct ProfileStore {
    path: PathBuf,
}
impl ProfileStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
    pub fn from_env() -> Result<Self, ConfigError> {
        #[cfg(windows)]
        let root = std::env::var_os("APPDATA")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| {
                ConfigError::new(ErrorCode::CommandFailed, "%AppData% is not defined")
            })?;
        #[cfg(not(windows))]
        let root = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .filter(|v| !v.is_empty())
                    .map(|h| PathBuf::from(h).join(".config"))
            })
            .ok_or_else(|| ConfigError::new(ErrorCode::CommandFailed, "$HOME is not defined"))?;
        Ok(Self::new(root.join("pb/config.toml")))
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn read(&self) -> Result<Config, ConfigError> {
        let data = fs::read(&self.path).map_err(|e| {
            let operation = if e.kind() == std::io::ErrorKind::IsADirectory {
                "read"
            } else {
                "open"
            };
            ConfigError::io(operation, &self.path, e)
        })?;
        let text = std::str::from_utf8(&data)
            .map_err(|_| ConfigError::new(ErrorCode::CommandFailed, "toml: invalid character"))?;
        let value: toml::Value = toml::from_str(text).map_err(|e| toml_diagnostic(text, e))?;
        reject_new_toml_syntax(text)?;
        decode_config(value).map_err(|error| datetime_diagnostic(text, error))
    }
    pub fn write(&self, config: &Config) -> Result<(), ConfigError> {
        let _guard = CONFIG_WRITE.lock().unwrap_or_else(|e| e.into_inner());
        self.write_unlocked(config)
    }
    fn write_unlocked(&self, config: &Config) -> Result<(), ConfigError> {
        let data = toml::to_string(config).map_err(|e| {
            ConfigError::new(
                ErrorCode::CommandFailed,
                format!("failed to encode config: {e}"),
            )
        })?;
        self.write_encoded(data.as_bytes())
    }
    pub(crate) fn write_encoded(&self, data: &[u8]) -> Result<(), ConfigError> {
        if let Some(parent) = self.path.parent() {
            create_config_directory(parent)?;
        }
        let mut options = fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&self.path).map_err(|e| {
            let mut error = ConfigError::io("open", &self.path, e);
            error.message = format!("create config file: {}", error.message);
            error
        })?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(|e| {
                    let mut error = ConfigError::io("chmod", &self.path, e);
                    error.message = format!("set config file permissions: {}", error.message);
                    error
                })?;
        }
        file.write_all(data).map_err(|e| {
            let mut error = ConfigError::io("write", &self.path, e);
            error.message = format!("write config file: {}", error.message);
            error
        })
    }
    pub(crate) fn save_raw_profile(
        &self,
        config: &Config,
        name: &[u8],
        profile: &ProfileRecord,
        overrides: &[(&str, &[u8])],
        default: &[u8],
    ) -> Result<(), ConfigError> {
        let _guard = CONFIG_WRITE.lock().unwrap_or_else(|e| e.into_inner());
        let mut profiles: BTreeMap<Vec<u8>, serde_json::Map<String, serde_json::Value>> = config
            .profiles
            .iter()
            .map(|(key, profile)| {
                (
                    key.as_bytes().to_vec(),
                    serde_json::to_value(profile)
                        .unwrap()
                        .as_object()
                        .unwrap()
                        .clone(),
                )
            })
            .collect();
        profiles.insert(
            name.to_vec(),
            serde_json::to_value(profile)
                .unwrap()
                .as_object()
                .unwrap()
                .clone(),
        );
        let mut data = b"DefaultProfile = ".to_vec();
        data.extend(toml_quote_bytes(default));
        data.extend_from_slice(b"\n\n[Profiles]\n");
        for (key, fields) in profiles {
            data.extend_from_slice(b"[Profiles.");
            if !key.is_empty()
                && key
                    .iter()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
            {
                data.extend_from_slice(&key);
            } else {
                data.extend(toml_quote_bytes(&key));
            }
            data.extend_from_slice(b"]\n");
            for (field, value) in fields {
                data.extend_from_slice(field.as_bytes());
                data.extend_from_slice(b" = ");
                if let serde_json::Value::String(value) = value {
                    let raw = if key == name {
                        overrides
                            .iter()
                            .find(|(name, _)| *name == field)
                            .map(|(_, raw)| *raw)
                            .unwrap_or(value.as_bytes())
                    } else {
                        value.as_bytes()
                    };
                    data.extend(toml_quote_bytes(raw));
                } else {
                    data.extend_from_slice(value.to_string().as_bytes());
                }
                data.push(b'\n');
            }
        }
        self.write_encoded(&data)
    }
    pub fn active_profile(&self) -> Result<ProfileRecord, ConfigError> {
        let missing =
            || ConfigError::new(ErrorCode::NotFound, "no profile configured. run: pb login");
        let config = self
            .read()
            .map_err(|e| if e.is_not_found() { missing() } else { e })?;
        if config.default_profile.is_empty() {
            return Err(missing());
        }
        config
            .profiles
            .get(&config.default_profile)
            .cloned()
            .ok_or_else(missing)
    }
    pub fn update_cloud_tokens(
        &self,
        old_refresh_token: &str,
        session_token: &str,
        refresh_token: &str,
    ) -> Result<(), ConfigError> {
        let _guard = CONFIG_WRITE.lock().unwrap_or_else(|e| e.into_inner());
        let mut config = self.read()?;
        for profile in config.profiles.values_mut() {
            if profile.cloud && profile.refresh_token == old_refresh_token {
                profile.session_token = session_token.to_owned();
                profile.refresh_token = refresh_token.to_owned();
                return self.write_unlocked(&config);
            }
        }
        Err(ConfigError::new(
            ErrorCode::CommandFailed,
            "cloud profile for refresh token not found",
        ))
    }
}

fn toml_error(message: impl Into<String>) -> ConfigError {
    ConfigError::new(
        ErrorCode::CommandFailed,
        format!("toml: {}", message.into()),
    )
}

fn create_config_directory(path: &Path) -> Result<(), ConfigError> {
    if path.as_os_str().is_empty() {
        return Ok(());
    }
    if let Ok(metadata) = fs::metadata(path) {
        return if metadata.is_dir() {
            Ok(())
        } else {
            Err(ConfigError::io(
                "mkdir",
                path,
                std::io::Error::new(std::io::ErrorKind::NotADirectory, "not a directory"),
            ))
        };
    }
    if let Some(parent) = path.parent() {
        create_config_directory(parent)?;
    }
    match fs::create_dir(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && path.is_dir() => Ok(()),
        Err(error) => Err(ConfigError::io("mkdir", path, error)),
    }
}

fn datetime_diagnostic(text: &str, error: ConfigError) -> ConfigError {
    if !error
        .message
        .contains("cannot decode TOML datetime into struct field")
    {
        return error;
    }
    let field = error
        .message
        .split("struct field ")
        .nth(1)
        .and_then(|s| s.split(" of type").next())
        .unwrap_or_default();
    let key = match field {
        "config.Config.DefaultProfile" => "DefaultProfile",
        "config.Profile.URL" => "url",
        "config.Profile.Username" => "username",
        "config.Profile.Password" => "password",
        "config.Profile.APIKey" => "apiKey",
        "config.Profile.SessionToken" => "session_token",
        "config.Profile.RefreshToken" => "refresh_token",
        "config.Profile.TenantID" => "tenant_id",
        "config.Profile.WorkspaceID" => "workspace_id",
        "config.Profile.WorkspaceName" => "workspace_name",
        "config.Profile.OrchestratorURL" => "orchestrator_url",
        _ => return error,
    };
    let missing_seconds = text.lines().any(|line| {
        let Some((name, raw)) = line.split_once('=') else {
            return false;
        };
        if !name
            .trim()
            .trim_matches(['\'', '"'])
            .eq_ignore_ascii_case(key)
        {
            return false;
        }
        let raw = raw.trim().split('#').next().unwrap_or_default().trim();
        let clock = raw
            .split(['T', 't', ' '])
            .next_back()
            .unwrap_or_default()
            .as_bytes();
        clock.len() >= 5
            && clock[..2].iter().all(u8::is_ascii_digit)
            && clock[2] == b':'
            && clock[3..5].iter().all(u8::is_ascii_digit)
            && clock.get(5) != Some(&b':')
    });
    if missing_seconds {
        toml_error("times are expected to have the format HH:MM:SS[.NNNNNN]")
    } else {
        error
    }
}

fn toml_diagnostic(text: &str, error: toml::de::Error) -> ConfigError {
    let message = error.message();
    let span = error.span().unwrap_or(text.len()..text.len());
    let at = span.start.min(text.len());
    let suffix = text.get(at..).unwrap_or("");
    let detail =
        if message.starts_with("unclosed array table") || message.starts_with("unclosed table") {
            if at == text.len() {
                "expected character ] but the document ended here".into()
            } else {
                "expected character ]".into()
            }
        } else if message.starts_with("key with no value") {
            if at == text.len() {
                "expected = after a key, but the document ends there".into()
            } else {
                "expected = after a key".into()
            }
        } else if message.starts_with("invalid unquoted key") {
            if suffix.chars().next().is_some_and(|c| !c.is_ascii()) {
                let line = text[..at].rsplit('\n').next().unwrap_or_default();
                if line.trim_start().starts_with('[') {
                    "expected character ]".into()
                } else {
                    "expected character =".into()
                }
            } else {
                format!(
                    "invalid character at start of key: {}",
                    suffix.chars().next().unwrap_or('\n')
                )
            }
        } else if message == "duplicate key" {
            let key = text.get(span).unwrap_or("").trim_matches(['\'', '"']);
            format!("key {key} is already defined")
        } else if message.starts_with("missing escaped value") {
            let c = suffix.chars().next().unwrap_or('\\');
            format!("invalid escaped character U+{:04X} '{c}'", c as u32)
        } else if message.starts_with("invalid basic string") && at == text.len() {
            "basic string not terminated by \"".into()
        } else if message.starts_with("invalid literal string") && at == text.len() {
            "literal string not terminated by '".into()
        } else if message.starts_with("invalid basic string")
            && suffix
                .bytes()
                .next()
                .is_some_and(|b| b < 0x20 && b != b'\n' && b != b'\t' && b != b'\r')
        {
            "invalid UTF-8".into()
        } else if message.starts_with("invalid float, expected `nan`") {
            "expected 'nan'".into()
        } else if message.starts_with("string values must be quoted") && at == text.len() {
            "expected value, not eof".into()
        } else if message.starts_with("string values must be quoted")
            && suffix.starts_with(['\n', '\r', '#'])
        {
            "incomplete number".into()
        } else if message.starts_with("unexpected key or value, expected newline") {
            match suffix.chars().next() {
                Some(c) => format!("expected newline but got U+{:04X} '{c}'", c as u32),
                None => "expected newline".into(),
            }
        } else {
            message.to_owned()
        };
    toml_error(detail)
}

// Go's decoder resolves struct field names without regard to ASCII case. The
// profile-name map remains case sensitive, and legacy credential keys remain
// independent from apiKey until ProfileRecord applies nonempty precedence.
fn decode_config(value: toml::Value) -> Result<Config, ConfigError> {
    let mut config = Config::default();
    let mut legacy = BTreeMap::<String, (String, String)>::new();
    let mut legacy_valid = true;
    for (key, value) in value.as_table().expect("TOML document table") {
        if key.eq_ignore_ascii_case("DefaultProfile") {
            config.default_profile = string_value(value, "config.Config.DefaultProfile")?;
        } else if key.eq_ignore_ascii_case("Profiles") {
            let profiles = value.as_table().ok_or_else(|| {
                type_error(
                    value,
                    "config.Config.Profiles",
                    "map[string]config.Profile",
                    "map",
                )
            })?;
            for (name, value) in profiles {
                let fields = value.as_table().ok_or_else(|| {
                    type_error(
                        value,
                        "config.Config.Profiles",
                        "map[string]config.Profile",
                        "struct",
                    )
                })?;
                let mut profile = config.profiles.get(name).cloned().unwrap_or_default();
                let (mut legacy_api_key, mut legacy_token) =
                    legacy.get(name).cloned().unwrap_or_default();
                for (key, value) in fields {
                    let key = key.to_ascii_lowercase();
                    if key == "cloud" {
                        profile.cloud = value.as_bool().ok_or_else(|| {
                            type_error(value, "config.Profile.Cloud", "bool", "bool")
                        })?;
                        continue;
                    }
                    let (target, field) = match key.as_str() {
                        "url" => (&mut profile.url, "URL"),
                        "username" => (&mut profile.username, "Username"),
                        "password" => (&mut profile.password, "Password"),
                        "apikey" => (&mut profile.api_key, "APIKey"),
                        "session_token" => (&mut profile.session_token, "SessionToken"),
                        "refresh_token" => (&mut profile.refresh_token, "RefreshToken"),
                        "tenant_id" => (&mut profile.tenant_id, "TenantID"),
                        "workspace_id" => (&mut profile.workspace_id, "WorkspaceID"),
                        "workspace_name" => (&mut profile.workspace_name, "WorkspaceName"),
                        "orchestrator_url" => (&mut profile.orchestrator_url, "OrchestratorURL"),
                        // Legacy decoding failures are ignored by Go's second
                        // decoding pass, leaving canonical credentials intact.
                        "api_key" => {
                            if let Some(s) = value.as_str() {
                                legacy_api_key = s.into();
                            } else {
                                legacy_valid = false;
                            }
                            continue;
                        }
                        "token" => {
                            if let Some(s) = value.as_str() {
                                legacy_token = s.into();
                            } else {
                                legacy_valid = false;
                            }
                            continue;
                        }
                        _ => continue,
                    };
                    *target = string_value(value, &format!("config.Profile.{field}"))?;
                }
                legacy.insert(name.clone(), (legacy_api_key, legacy_token));
                config.profiles.insert(name.clone(), profile);
            }
        }
    }
    if legacy_valid {
        for (name, (api_key, token)) in legacy {
            if let Some(profile) = config.profiles.get_mut(&name) {
                if profile.api_key.is_empty() {
                    profile.api_key = api_key;
                }
                if profile.api_key.is_empty() {
                    profile.api_key = token;
                }
            }
        }
    }
    Ok(config)
}
fn string_value(value: &toml::Value, field: &str) -> Result<String, ConfigError> {
    value
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| type_error(value, field, "string", "string"))
}
fn type_error(value: &toml::Value, field: &str, target: &str, kind: &str) -> ConfigError {
    let detail = match value {
        toml::Value::Integer(_) => {
            format!("cannot decode TOML integer into struct field {field} of type {target}")
        }
        toml::Value::Array(_) => {
            format!("cannot decode TOML array into struct field {field} of type {target}")
        }
        toml::Value::String(_) => format!("cannot store TOML string into a Go {kind}"),
        toml::Value::Float(_) => format!("float cannot be assigned to {target}"),
        toml::Value::Boolean(b) => format!("cannot assign boolean to a {b}"),
        toml::Value::Table(_) => format!("cannot store inline table in Go type {target}"),
        toml::Value::Datetime(_) => {
            format!("cannot decode TOML datetime into struct field {field} of type {target}")
        }
    };
    toml_error(detail)
}

// The current Rust TOML parser accepts some TOML 1.1 syntax that the pinned Go
// parser rejects. Scan only successfully parsed input; this keeps first syntax
// error provenance in the TOML parser and excludes comments/literal strings.
fn reject_new_toml_syntax(text: &str) -> Result<(), ConfigError> {
    let bytes = text.as_bytes();
    let mut i = 0;
    let mut stack = Vec::new();
    let mut previous = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'#' => {
                while i < bytes.len() && bytes[i] != b'\n' {
                    i += 1;
                }
                continue;
            }
            quote @ (b'\'' | b'"') => {
                let triple = bytes
                    .get(i..i + 3)
                    .is_some_and(|v| v == [quote, quote, quote]);
                i += if triple { 3 } else { 1 };
                while i < bytes.len() {
                    if quote == b'"' && bytes[i] == b'\\' {
                        if bytes.get(i + 1) == Some(&b'x') {
                            return Err(toml_error("invalid escaped character U+0078 'x'"));
                        }
                        i += 2;
                        continue;
                    }
                    if bytes[i] == quote
                        && (!triple
                            || bytes
                                .get(i..i + 3)
                                .is_some_and(|v| v == [quote, quote, quote]))
                    {
                        i += if triple { 3 } else { 1 };
                        break;
                    }
                    i += 1;
                }
                previous = quote;
                continue;
            }
            b'{' | b'[' => stack.push(bytes[i]),
            b'}' => {
                if previous == b',' {
                    return Err(toml_error("invalid character at start of key: }"));
                }
                stack.pop();
            }
            b']' => {
                stack.pop();
            }
            b'\n' if stack.last() == Some(&b'{') => {
                return Err(toml_error("invalid character at start of key: \n"));
            }
            _ => {}
        }
        if !bytes[i].is_ascii_whitespace() {
            previous = bytes[i];
        }
        i += 1;
    }
    Ok(())
}

// Go's TOML encoder retains malformed bytes inside literal/basic strings.
fn toml_quote_bytes(raw: &[u8]) -> Vec<u8> {
    if !raw.contains(&b'\'') && !raw.iter().any(|b| (*b < 0x20 && *b != b'\t') || *b == 0x7f) {
        return [b"'".as_slice(), raw, b"'"].concat();
    }
    let mut output = b"\"".to_vec();
    for byte in raw {
        match byte {
            b'"' => output.extend_from_slice(b"\\\""),
            b'\\' => output.extend_from_slice(b"\\\\"),
            b'\n' => output.extend_from_slice(b"\\n"),
            b'\r' => output.extend_from_slice(b"\\r"),
            b'\t' => output.extend_from_slice(b"\\t"),
            8 => output.extend_from_slice(b"\\b"),
            12 => output.extend_from_slice(b"\\f"),
            0..=0x1f | 0x7f => output.extend_from_slice(format!("\\u{byte:04x}").as_bytes()),
            _ => output.push(*byte),
        }
    }
    output.push(b'"');
    output
}
