use pb::{
    config::{Config, ProfileRecord, ProfileStore},
    parser, profile, spec,
};
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

static COUNTER: AtomicU64 = AtomicU64::new(0);
struct TemporaryConfig {
    directory: PathBuf,
    store: ProfileStore,
}
impl TemporaryConfig {
    fn new() -> Self {
        let directory = std::env::temp_dir().join(format!(
            "pb-config-test-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&directory).unwrap();
        Self {
            store: ProfileStore::new(directory.join("pb/config.toml")),
            directory,
        }
    }
    fn raw(&self, value: &str) {
        fs::create_dir_all(self.store.path().parent().unwrap()).unwrap();
        fs::write(self.store.path(), value).unwrap();
    }
}
impl Drop for TemporaryConfig {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}
fn invoke(store: &ProfileStore, args: &[&str]) -> Result<String, profile::ProfileError> {
    let args = args.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    let (command, path, remaining) = parser::discover(spec::command_spec(), &args).unwrap();
    let parsed = parser::parse(command, &path, &remaining).unwrap();
    parser::validate_args(command, &parsed).unwrap();
    profile::execute(&parsed, store).map(|s| s.unwrap())
}
fn record(url: &str) -> ProfileRecord {
    ProfileRecord {
        url: url.into(),
        ..ProfileRecord::default()
    }
}

#[test]
fn legacy_key_precedence_and_permissive_records() {
    let temp = TemporaryConfig::new();
    temp.raw("DefaultProfile = 'canonical'\n[Profiles.canonical]\napiKey = 'canonical'\napi_key = 'legacy'\ntoken = 'oldest'\nusername = 'conflicting'\ncloud = true\nunknown = 'ignored'\n[Profiles.legacy]\napiKey = ''\napi_key = 'legacy'\ntoken = 'oldest'\n[Profiles.oldest]\napi_key = ''\ntoken = 'oldest'\n[Profiles.empty]\n");
    let config = temp.store.read().unwrap();
    assert_eq!(config.profiles["canonical"].api_key, "canonical");
    assert_eq!(config.profiles["legacy"].api_key, "legacy");
    assert_eq!(config.profiles["oldest"].api_key, "oldest");
    assert_eq!(config.profiles["empty"], ProfileRecord::default());
    assert!(temp.store.active_profile().unwrap().cloud);
    temp.store.write(&config).unwrap();
    let encoded = fs::read_to_string(temp.store.path()).unwrap();
    assert!(encoded.contains("apiKey = "));
    assert!(
        !encoded.contains("api_key = ")
            && !encoded.contains("token = ")
            && !encoded.contains("unknown = ")
    );
    assert_eq!(temp.store.read().unwrap(), config);
}

#[test]
fn empty_config_and_missing_active_profile_remain_distinct() {
    let temp = TemporaryConfig::new();
    assert!(temp.store.read().unwrap_err().is_not_found());
    assert_eq!(
        temp.store.active_profile().unwrap_err().message,
        "no profile configured. run: pb login"
    );
    temp.raw("");
    assert_eq!(temp.store.read().unwrap(), Config::default());
    assert_eq!(
        temp.store.active_profile().unwrap_err().message,
        "no profile configured. run: pb login"
    );
    temp.raw("DefaultProfile = 'missing'\n[Profiles.other]\nurl = 'relative'\n");
    assert_eq!(
        temp.store.active_profile().unwrap_err().message,
        "no profile configured. run: pb login"
    );
}

#[test]
fn case_insensitive_struct_keys_keep_profile_names_case_sensitive() {
    let temp = TemporaryConfig::new();
    temp.raw("defaultprofile='Upper'\n[profiles.Upper]\nURL='relative'\nCloud=true\nAPIKEY='canonical'\nTENANT_ID='tenant'\n[profiles.upper]\nurl='other'\n");
    let config = temp.store.read().unwrap();
    assert_eq!(config.default_profile, "Upper");
    assert_eq!(config.profiles["Upper"].api_key, "canonical");
    assert_eq!(config.profiles["Upper"].tenant_id, "tenant");
    assert_eq!(config.profiles["upper"].url, "other");
}

#[test]
fn case_insensitive_table_aliases_merge_existing_credentials() {
    let temp = TemporaryConfig::new();
    temp.raw("DefaultProfile='p'\n[Profiles.p]\nurl='http://first'\nusername='u'\npassword='example-password'\ntoken='example-token'\n[profiles.p]\nurl='http://second'\n");
    let config = temp.store.read().unwrap();
    assert_eq!(config.profiles["p"].url, "http://second");
    assert_eq!(config.profiles["p"].username, "u");
    assert_eq!(config.profiles["p"].password, "example-password");
    assert_eq!(config.profiles["p"].api_key, "example-token");
    invoke(&temp.store, &["profile", "default", "p"]).unwrap();
    assert_eq!(temp.store.read().unwrap(), config);
    if let Some(oracle) = std::env::var_os("PB_GO_ORACLE") {
        let output = std::process::Command::new(oracle)
            .args(["profile", "default", "p"])
            .env("XDG_CONFIG_HOME", &temp.directory)
            .output()
            .unwrap();
        assert!(output.status.success());
        assert_eq!(temp.store.read().unwrap(), config);
    }
    temp.raw("[Profiles.p]\ntoken='older'\n[profiles.p]\ntoken=''\n");
    assert!(temp.store.read().unwrap().profiles["p"].api_key.is_empty());
}

#[test]
fn failed_config_directory_creation_names_the_failing_component() {
    let temp = TemporaryConfig::new();
    let blocked = temp.directory.join("blocked");
    fs::write(&blocked, "regular file").unwrap();
    let store = ProfileStore::new(blocked.join("pb/config.toml"));
    let error = store.write(&Config::default()).unwrap_err();
    assert_eq!(
        error.message,
        format!("mkdir {}: not a directory", blocked.display())
    );
    assert_eq!(error.code, pb::ErrorCode::ConnectionFailed);
}

#[test]
fn seconds_less_time_diagnostics_apply_only_to_decoded_fields() {
    let temp = TemporaryConfig::new();
    for value in ["07:32", "2000-01-01T07:32"] {
        temp.raw(&format!("[Profiles.p]\nworkspace_id={value}"));
        assert_eq!(
            temp.store.read().unwrap_err().message,
            "toml: times are expected to have the format HH:MM:SS[.NNNNNN]"
        );
    }
    temp.raw("[Profiles.p]\nunknown=07:32");
    assert_eq!(
        temp.store.read().unwrap().profiles["p"],
        ProfileRecord::default()
    );
}

#[test]
fn malformed_config_diagnostics_match_pinned_go_cases() {
    let temp = TemporaryConfig::new();
    for (input, expected) in [
        (
            "[[invalid",
            "expected character ] but the document ended here",
        ),
        (
            "broken",
            "expected = after a key, but the document ends there",
        ),
        ("[Profiles.<>&]", "invalid character at start of key: <"),
        ("[Profiles.café]", "expected character ]"),
        ("café = 1", "expected character ="),
        ("DefaultProfile =", "expected value, not eof"),
        ("DefaultProfile = \n", "incomplete number"),
        ("DefaultProfile = \r\n", "incomplete number"),
        ("DefaultProfile = #comment\n", "incomplete number"),
        ("[Profiles.local\nurl='x'", "expected character ]"),
        (
            "DefaultProfile=1",
            "cannot decode TOML integer into struct field config.Config.DefaultProfile of type string",
        ),
        (
            "[Profiles.x]\ncloud='no'",
            "cannot store TOML string into a Go bool",
        ),
        (
            "[Profiles.x]\nurl=1",
            "cannot decode TOML integer into struct field config.Profile.URL of type string",
        ),
        (
            "[Profiles.x]\nurl='a'\nurl='b'",
            "key url is already defined",
        ),
        (
            "[Profiles.x]\nurl=\"\\q\"",
            "invalid escaped character U+0071 'q'",
        ),
        (
            "[Profiles.x]\nurl=\"\\x61\"",
            "invalid escaped character U+0078 'x'",
        ),
        ("x={\na=1}", "invalid character at start of key: \n"),
        ("x={a=1,}", "invalid character at start of key: }"),
        ("x=null", "expected 'nan'"),
        ("url=\"unfinished", "basic string not terminated by \""),
        ("url=\"x\" trailing", "expected newline but got U+0074 't'"),
    ] {
        temp.raw(input);
        assert_eq!(
            temp.store.read().unwrap_err().message,
            format!("toml: {expected}"),
            "{input}"
        );
    }
}

#[test]
fn command_output_and_debug_never_include_credential_fields() {
    let temp = TemporaryConfig::new();
    let profile = ProfileRecord {
        url: "relative".into(),
        username: "user".into(),
        password: "PASSWORD_SECRET".into(),
        api_key: "API_SECRET".into(),
        session_token: "SESSION_SECRET".into(),
        refresh_token: "REFRESH_SECRET".into(),
        ..ProfileRecord::default()
    };
    profile::save_login_profile(&temp.store, "local", profile.clone()).unwrap();
    let output = invoke(&temp.store, &["profile", "list", "-o", "json"]).unwrap();
    assert_eq!(
        output,
        "{\n  \"local\": {\n    \"url\": \"relative\",\n    \"username\": \"user\",\n    \"cloud\": false\n  }\n}\n"
    );
    let debug = format!("{profile:?}");
    for secret in [
        "PASSWORD_SECRET",
        "API_SECRET",
        "SESSION_SECRET",
        "REFRESH_SECRET",
    ] {
        assert!(!output.contains(secret) && !debug.contains(secret));
    }
}

#[test]
fn profile_list_keeps_html_but_add_uses_go_marshal_escaping() {
    let temp = TemporaryConfig::new();
    profile::save_login_profile(&temp.store, "<>&", record("<>&")).unwrap();
    let output = invoke(&temp.store, &["profile", "list", "-o", "json"]).unwrap();
    assert!(output.contains("\"<>&\"") && !output.contains("\\u003c"));
    let output = invoke(
        &temp.store,
        &[
            "profile", "add", "add", "relative", "<>&", "secret", "-o", "json",
        ],
    )
    .unwrap();
    assert!(output.contains("\\u003c\\u003e\\u0026"));
}

#[test]
fn styled_profile_list_normalizes_tabs_and_crlf_before_padding() {
    let temp = TemporaryConfig::new();
    profile::save_login_profile(
        &temp.store,
        "a\tb",
        ProfileRecord {
            username: "u\tv".into(),
            ..record("url")
        },
    )
    .unwrap();
    assert_eq!(
        invoke(&temp.store, &["profile", "list"]).unwrap(),
        "│ a    b      \n│ url: url    \n│ user: u    v\n\n"
    );
    let mut config = Config {
        default_profile: "a\nb".into(),
        ..Config::default()
    };
    config.profiles.insert(
        "a\nb".into(),
        ProfileRecord {
            username: "u\r\nv".into(),
            ..record("url")
        },
    );
    temp.store.write(&config).unwrap();
    assert_eq!(
        invoke(&temp.store, &["profile", "list"]).unwrap(),
        "│ a       \n│ b       \n│ url: url\n│ user: u \n│ v       \n\n"
    );
}

#[test]
fn directory_read_error_has_go_network_error_classification() {
    let temp = TemporaryConfig::new();
    fs::create_dir_all(temp.store.path()).unwrap();
    let error = temp.store.read().unwrap_err();
    assert_eq!(error.code, pb::ErrorCode::ConnectionFailed);
    assert_eq!(
        error.message,
        format!("read {}: is a directory", temp.store.path().display())
    );
    assert_eq!(
        invoke(&temp.store, &["profile", "list", "-o", "json"])
            .unwrap_err()
            .code,
        pb::ErrorCode::ConnectionFailed
    );
}

#[test]
fn add_update_default_and_remove_preserve_go_policies() {
    let temp = TemporaryConfig::new();
    assert_eq!(
        invoke(
            &temp.store,
            &[
                "profile",
                "add",
                "local",
                "relative path",
                "user",
                "pass",
                "-o",
                "json"
            ]
        )
        .unwrap(),
        "{\n  \"url\": \"relative%20path\",\n  \"username\": \"user\",\n  \"cloud\": false\n}\n"
    );
    invoke(
        &temp.store,
        &[
            "profile",
            "add",
            "key",
            "http://host",
            "--api-key",
            "  test-key  ",
        ],
    )
    .unwrap();
    assert_eq!(temp.store.read().unwrap().default_profile, "key");
    assert_eq!(
        temp.store.read().unwrap().profiles["key"].api_key,
        "test-key"
    );
    assert_eq!(
        invoke(
            &temp.store,
            &["profile", "update", "local", "new relative path"]
        )
        .unwrap(),
        "Profile 'local' URL updated to new relative path\n"
    );
    assert_eq!(
        temp.store.read().unwrap().profiles["local"].url,
        "new relative path"
    );
    invoke(&temp.store, &["profile", "default", "local"]).unwrap();
    assert_eq!(
        invoke(&temp.store, &["profile", "remove", "local", "-o", "json"]).unwrap(),
        "'key' is now set as the default profile\n\"Deleted profile local\"\n"
    );
    assert_eq!(temp.store.read().unwrap().default_profile, "key");
    invoke(&temp.store, &["profile", "remove", "key"]).unwrap();
    assert_eq!(temp.store.read().unwrap(), Config::default());
}

#[test]
fn login_replaces_corrupt_config_but_cloud_refuses_it_and_requires_force() {
    let temp = TemporaryConfig::new();
    temp.raw("[[invalid");
    let original = fs::read(temp.store.path()).unwrap();
    assert!(
        profile::save_cloud_profile(&temp.store, "cloud", record("cloud"), false, true)
            .unwrap_err()
            .message
            .starts_with("failed to read config: ")
    );
    assert_eq!(fs::read(temp.store.path()).unwrap(), original);
    profile::save_login_profile(&temp.store, "local", record("local")).unwrap();
    profile::save_cloud_profile(&temp.store, "cloud", record("cloud"), false, false).unwrap();
    assert_eq!(temp.store.read().unwrap().default_profile, "local");
    assert_eq!(
        profile::save_cloud_profile(&temp.store, "cloud", record("replacement"), false, true)
            .unwrap_err()
            .message,
        "profile \"cloud\" already exists. use --force to overwrite"
    );
    profile::save_cloud_profile(&temp.store, "cloud", record("replacement"), true, true).unwrap();
    assert_eq!(temp.store.read().unwrap().default_profile, "cloud");
}

#[test]
fn logout_yes_selects_first_sorted_remaining_profile() {
    let temp = TemporaryConfig::new();
    for name in ["zeta", "alpha", "active"] {
        profile::save_login_profile(&temp.store, name, record(name)).unwrap();
    }
    let output = invoke(&temp.store, &["logout", "--yes", "-o", "json"]).unwrap();
    assert_eq!(
        output,
        "{\n  \"status\": \"ok\",\n  \"profile\": \"active\",\n  \"url\": \"active\",\n  \"removed\": true,\n  \"default_profile\": \"alpha\"\n}\n"
    );
    let config = temp.store.read().unwrap();
    assert_eq!(config.default_profile, "alpha");
    assert!(!config.profiles.contains_key("active"));
    assert_eq!(
        invoke(&temp.store, &["logout", "-o", "json"])
            .unwrap_err()
            .message,
        "use --yes with -o json to avoid interactive prompts"
    );
    assert_eq!(temp.store.read().unwrap(), config);
}

#[test]
fn logout_uses_sole_profile_but_refuses_ambiguous_missing_default() {
    let temp = TemporaryConfig::new();
    let mut config = Config::default();
    config.profiles.insert("sole".into(), record("url"));
    temp.store.write(&config).unwrap();
    assert_eq!(
        invoke(&temp.store, &["logout", "--yes"]).unwrap(),
        "Logged out and removed profile 'sole'\n"
    );
    config.profiles.insert("other".into(), record("other"));
    temp.store.write(&config).unwrap();
    assert_eq!(
        invoke(&temp.store, &["logout", "--yes"])
            .unwrap_err()
            .message,
        "no active profile found; set one with: pb profile default <name>"
    );
    assert_eq!(temp.store.read().unwrap(), config);
}

// Interactive cancellation/persistence is exercised in interactive_effects.rs
// using isolated child processes with real PTYs.

#[test]
fn refresh_updates_only_matching_cloud_record() {
    let temp = TemporaryConfig::new();
    let mut config = Config::default();
    config.profiles.insert(
        "basic".into(),
        ProfileRecord {
            refresh_token: "old".into(),
            ..record("basic")
        },
    );
    config.profiles.insert(
        "cloud".into(),
        ProfileRecord {
            cloud: true,
            refresh_token: "old".into(),
            ..record("cloud")
        },
    );
    temp.store.write(&config).unwrap();
    temp.store
        .update_cloud_tokens("old", "rotated-session", "rotated-refresh")
        .unwrap();
    let changed = temp.store.read().unwrap();
    assert_eq!(changed.profiles["basic"].refresh_token, "old");
    assert_eq!(changed.profiles["cloud"].session_token, "rotated-session");
    assert_eq!(changed.profiles["cloud"].refresh_token, "rotated-refresh");
    assert_eq!(
        temp.store
            .update_cloud_tokens("old", "other", "other")
            .unwrap_err()
            .message,
        "cloud profile for refresh token not found"
    );
}

#[cfg(unix)]
#[test]
fn saves_create_and_restore_private_permissions() {
    use std::os::unix::fs::PermissionsExt;
    let temp = TemporaryConfig::new();
    temp.store.write(&Config::default()).unwrap();
    assert_eq!(
        fs::metadata(temp.store.path())
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    fs::set_permissions(temp.store.path(), fs::Permissions::from_mode(0o666)).unwrap();
    temp.store.write(&Config::default()).unwrap();
    assert_eq!(
        fs::metadata(temp.store.path())
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
}

#[test]
fn profile_urls_accept_relative_references_and_preserve_query_escapes() {
    for (raw, expected) in [
        ("HTTP://EXAMPLE.COM/A", "http://EXAMPLE.COM/A"),
        ("relative path", "relative%20path"),
        ("a:b", "a:b"),
        ("host:8000", "host:8000"),
        ("http://", "http:"),
        ("foo?x=%zz", "foo?x=%zz"),
        ("http://host/a%2fb", "http://host/a%2fb"),
        ("http://café/你好", "http://caf%C3%A9/%E4%BD%A0%E5%A5%BD"),
        ("http://user:p@ss@h/", "http://user:p%40ss@h/"),
        ("http://a@b@h/", "http://a%40b@h/"),
        ("http://h[::1]/", "http://[::1]/"),
        ("http://[::1%25e%20n]/", "http://[::1%25e%20n]/"),
    ] {
        assert_eq!(profile::parse_profile_url(raw).unwrap(), expected);
    }
    assert_eq!(
        profile::parse_profile_url("http://host:abc").unwrap_err(),
        "parse \"http://host:abc\": invalid port \":abc\" after host"
    );
    assert_eq!(
        profile::parse_profile_url("%zz").unwrap_err(),
        "parse \"%zz\": invalid URL escape \"%zz\""
    );
    assert!(
        profile::parse_profile_url("1:a")
            .unwrap_err()
            .contains("first path segment")
    );
    assert_eq!(
        profile::parse_profile_url("http://h[x]/").unwrap_err(),
        "parse \"http://h[x]/\": invalid host: ParseAddr(\"x\"): unable to parse IP"
    );
    for raw in ["http://h[/", "http://a[b:80/"] {
        assert!(
            profile::parse_profile_url(raw)
                .unwrap_err()
                .ends_with("missing ']' in host")
        );
    }
    assert_eq!(
        profile::parse_profile_url("http://[1.2.3.4]/").unwrap_err(),
        "parse \"http://[1.2.3.4]/\": invalid IP-literal"
    );
}

/// The optional pinned oracle verifies successful TOML exchange in both
/// directions without becoming a dependency of ordinary Rust builds/tests.
#[test]
fn go_toml_interoperability_when_pinned_oracle_is_supplied() {
    let Some(oracle) = std::env::var_os("PB_GO_ORACLE") else {
        return;
    };
    let temp = TemporaryConfig::new();
    profile::save_login_profile(
        &temp.store,
        "rust",
        ProfileRecord {
            username: "user".into(),
            password: "pass".into(),
            ..record("relative")
        },
    )
    .unwrap();
    let output = std::process::Command::new(&oracle)
        .args(["profile", "list", "-o", "json"])
        .env("XDG_CONFIG_HOME", &temp.directory)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap()["rust"]["url"],
        "relative"
    );
    let output = std::process::Command::new(oracle)
        .args([
            "profile",
            "add",
            "go",
            "http://host",
            "--api-key",
            "example-key",
        ])
        .env("XDG_CONFIG_HOME", &temp.directory)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let config = temp.store.read().unwrap();
    assert_eq!(config.default_profile, "go");
    assert_eq!(config.profiles["go"].api_key, "example-key");
    assert_eq!(config.profiles["rust"].password, "pass");
}
