//! Parseable CLI with owned command declarations and explicit command services.
pub mod api;
pub mod auth;
pub mod config;
pub mod datasets;
pub mod error;
pub mod gojson;
pub mod parser;
mod printable;
pub mod profile;
mod render;
pub mod spec;
mod status;
mod transport;
mod url_reference;

use parser::{ParsedInvocation, quote};
use serde::{Deserialize, Serialize};
use spec::{CommandSpec, Operation, command_spec};

#[derive(Debug, Default, PartialEq)]
pub struct Outcome {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
}
impl Outcome {
    fn success(stdout: String) -> Self {
        Self {
            stdout,
            ..Self::default()
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    InvalidInput,
    AuthFailed,
    PermissionDenied,
    NotFound,
    Conflict,
    RateLimited,
    ConnectionFailed,
    Timeout,
    InvalidResponse,
    ServerError,
    CommandFailed,
}
#[derive(Serialize)]
struct ErrorEnvelope<'a> {
    error: ErrorDetail<'a>,
}
#[derive(Serialize)]
struct ErrorDetail<'a> {
    code: ErrorCode,
    message: &'a str,
    retryable: bool,
}

fn input_code(message: &str) -> ErrorCode {
    if [
        "unknown command ",
        "unknown flag: ",
        "flag needs an argument: ",
        "invalid argument ",
        "requires at least ",
        "accepts at most ",
        "accepts between ",
        "accepts ",
        "unsupported output format ",
    ]
    .iter()
    .any(|p| message.starts_with(p))
    {
        ErrorCode::InvalidInput
    } else {
        ErrorCode::CommandFailed
    }
}
fn failure(
    command: &CommandSpec,
    path: &str,
    message: &str,
    code: ErrorCode,
    structured: bool,
    root_find: bool,
) -> Outcome {
    let stderr = if structured {
        render::json(
            &ErrorEnvelope {
                error: ErrorDetail {
                    code,
                    message,
                    retryable: false,
                },
            },
            false,
        )
    } else {
        let mut text = format!("Error: {message}\n");
        if root_find {
            text.push_str("Run 'pb --help' for usage.\n");
        } else if !command.silence_usage {
            text.push_str(&render::usage(command, path));
        }
        text
    };
    Outcome {
        stderr,
        exit_code: 1,
        ..Outcome::default()
    }
}

fn output_format(parsed: &ParsedInvocation) -> Result<&'static str, String> {
    let raw = parsed.text("output");
    match raw.trim().to_ascii_lowercase().as_str() {
        "" | "text" => Ok("text"),
        "json" => Ok("json"),
        _ => Err(format!(
            "unsupported output format {} (expected text or json)",
            quote(raw)
        )),
    }
}
pub fn version() -> &'static str {
    let build_version = option_env!("PB_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"));
    build_version.strip_prefix('v').unwrap_or(build_version)
}
pub fn commit() -> &'static str {
    option_env!("PB_COMMIT").unwrap_or("")
}

/// Execute a UTF-8 argument vector. Offline commands never initialize networking.
pub fn run(args: &[String]) -> Outcome {
    let root = command_spec();
    let structured = parser::requests_json(args);
    let (command, path, remaining) = match parser::discover(root, args) {
        Ok(found) => found,
        Err(err) => {
            return failure(
                root,
                "pb",
                &err.message,
                input_code(&err.message),
                structured,
                err.root_find,
            );
        }
    };
    let parsed = match parser::parse(command, &path, &remaining) {
        Ok(parsed) => parsed,
        Err(err) => {
            return failure(
                command,
                &path,
                &err.message,
                input_code(&err.message),
                structured,
                false,
            );
        }
    };
    if parsed.boolean("help") {
        return Outcome::success(render::help(command, &path, true));
    }
    if let Err(message) = parser::validate_args(command, &parsed) {
        let code = if command.args.kind == spec::ArgsKind::ProfileAdd {
            ErrorCode::InvalidInput
        } else {
            input_code(&message)
        };
        return failure(command, &path, &message, code, structured, false);
    }
    if command.requires_profile
        && let Err(err) = config::ProfileStore::from_env().and_then(|store| store.active_profile())
    {
        let mut err = error::CliError::new(err.code, err.message);
        if command.wrapped_preflight {
            err.message = format!("error initializing default profile: {}", err.message);
        }
        return err.render(command, &path, structured);
    }
    if path == "pb user set-role" && parsed.positionals.len() < 2 {
        return failure(
            command,
            &path,
            "requires at least 2 arguments",
            ErrorCode::InvalidInput,
            structured,
            false,
        );
    }
    // Operations with no local output flag must not inherit root/parent output.
    if path == "pb cloud profile add" && parsed.text("api-key").trim().is_empty() {
        return failure(
            command,
            &path,
            "api key is required. pass --api-key",
            ErrorCode::CommandFailed,
            structured,
            false,
        );
    }
    // Dataset reads validate late; legacy profile mutations and logout only
    // recognize the exact literal "json" and otherwise emit text.
    let delayed_output = matches!(
        path.as_str(),
        "pb dataset list"
            | "pb dataset info"
            | "pb logout"
            | "pb profile add"
            | "pb profile default"
            | "pb profile remove"
    );
    let format = match if delayed_output {
        Ok("text")
    } else {
        output_format(&parsed)
    } {
        Ok(format) => format,
        Err(message) => {
            return failure(
                command,
                &path,
                &message,
                ErrorCode::InvalidInput,
                structured,
                false,
            );
        }
    };
    if command.operation == Operation::Version
        || (command.operation == Operation::Root && parsed.boolean("version"))
    {
        #[derive(Serialize)]
        struct Version<'a> {
            version: &'a str,
        }
        return Outcome::success(if format == "json" {
            render::json(&Version { version: version() }, false)
        } else {
            format!("pb version {}\n", version())
        });
    }
    if command.operation == Operation::Help {
        let (target, target_path, _) = match parser::discover(root, &parsed.positionals) {
            Ok(found) => found,
            Err(err) => {
                return failure(
                    command,
                    &path,
                    &err.message,
                    input_code(&err.message),
                    structured,
                    false,
                );
            }
        };
        return Outcome::success(if format == "json" {
            render::command_json(target, &target_path, target_path == "pb help")
        } else {
            render::help(target, &target_path, target_path == "pb help")
        });
    }
    if command.operation == Operation::Agent {
        let catalog = catalog();
        return Outcome::success(if format == "json" {
            render::json(&catalog, false)
        } else {
            let mut text = format!(
                "Read-only command catalog for agents\n{}\n",
                catalog.authorization
            );
            for c in catalog.commands {
                text.push_str(&format!("- {}: {}\n", c.command, c.description));
            }
            text
        });
    }
    if matches!(
        command.operation,
        Operation::Root | Operation::Metadata | Operation::CompletionGroup
    ) && !command.requires_profile
    {
        return Outcome::success(if format == "json" {
            render::command_json(command, &path, true)
        } else {
            render::help(command, &path, true)
        });
    }
    if let Some(result) = execute_operational(&parsed, format, structured, command, &path) {
        return result;
    }
    let message = format!(
        "command {} is not implemented in this Rust phase",
        quote(&path)
    );
    failure(
        command,
        &path,
        &message,
        ErrorCode::CommandFailed,
        structured,
        false,
    )
}

/// Configuration stays synchronous; a runtime exists only for online services.
fn execute_operational(
    parsed: &ParsedInvocation,
    format: &str,
    structured: bool,
    command: &CommandSpec,
    path: &str,
) -> Option<Outcome> {
    let online = matches!(
        path,
        "pb status"
            | "pb dataset list"
            | "pb dataset info"
            | "pb dataset schema"
            | "pb cloud profile add"
    );
    let local = path.starts_with("pb profile ") || path == "pb logout";
    if !online && !local {
        return None;
    }
    let store = match config::ProfileStore::from_env() {
        Ok(store) => store,
        Err(_) if path == "pb status" => {
            return Some(status_preflight_failure(format, structured, command, path));
        }
        Err(_) if path == "pb logout" => {
            let message = if parsed.text("output") == "json" && !parsed.boolean("yes") {
                "use --yes with -o json to avoid interactive prompts"
            } else {
                "no config found — nothing to logout from"
            };
            return Some(
                error::CliError::new(ErrorCode::CommandFailed, message)
                    .render(command, path, structured),
            );
        }
        Err(err) => {
            return Some(
                error::CliError::new(err.code, err.message).render(command, path, structured),
            );
        }
    };
    if local {
        return match profile::execute(parsed, &store) {
            Ok(Some(stdout)) => Some(Outcome::success(stdout)),
            Ok(None) => None,
            Err(err) => Some(error::CliError::from(err).render(command, path, structured)),
        };
    }
    // Status can fail on local preflight; no network runtime is needed then.
    if path == "pb status" && store.read().is_err() {
        return Some(status_preflight_failure(format, structured, command, path));
    }
    let runtime = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(err) => {
            return Some(
                error::CliError::new(ErrorCode::CommandFailed, err.to_string())
                    .render(command, path, structured),
            );
        }
    };
    if path == "pb status" {
        return Some(
            match runtime.block_on(status::execute(&store, format == "json")) {
                Ok(outcome) => outcome,
                Err((message, diagnostics)) => {
                    let mut outcome = failure(
                        command,
                        path,
                        &message,
                        ErrorCode::CommandFailed,
                        structured,
                        false,
                    );
                    outcome.stderr = diagnostics + &outcome.stderr;
                    outcome
                }
            },
        );
    }
    let result: Result<String, error::CliError> = runtime.block_on(async {
        if path == "pb cloud profile add" {
            let (api_key, orchestrator_url) = profile::cloud_add_input(parsed)?;
            let workspace = api::validate_cloud_api_key(&orchestrator_url, &api_key).await?;
            return profile::cloud_profile_add(
                parsed,
                &store,
                config::ProfileRecord {
                    cloud: true,
                    url: workspace.url,
                    tenant_id: workspace.tenant_id,
                    workspace_id: workspace.workspace_id,
                    workspace_name: workspace.workspace_name,
                    api_key,
                    orchestrator_url,
                    ..Default::default()
                },
            )
            .map_err(Into::into);
        }
        let selected = store
            .active_profile()
            .map_err(|err| error::CliError::new(err.code, err.message))?;
        let client = api::ApiClient::new(selected, store)?;
        datasets::execute(parsed, &client).await
    });
    Some(match result {
        Ok(stdout) => Outcome::success(stdout),
        Err(err) => err.render(command, path, structured),
    })
}

fn status_preflight_failure(
    format: &str,
    structured: bool,
    command: &CommandSpec,
    path: &str,
) -> Outcome {
    let message = "no profile configured. run: pb login";
    if format == "json" {
        Outcome {
            stdout: render::json(
                &serde_json::json!({"status":"error","healthy":false,"error":message}),
                false,
            ),
            exit_code: 1,
            ..Outcome::default()
        }
    } else {
        failure(
            command,
            path,
            message,
            ErrorCode::CommandFailed,
            structured,
            false,
        )
    }
}

#[derive(Serialize, Deserialize)]
struct AgentCatalog {
    schema_version: String,
    catalog: String,
    authorization: String,
    contract: AgentContract,
    error_codes: Vec<AgentErrorCode>,
    commands: Vec<AgentCommand>,
    excluded: String,
}
#[derive(Serialize, Deserialize)]
struct AgentContract {
    success_output: String,
    error_output: String,
    diagnostics: String,
    failure_exit: String,
    empty_lists: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    special_cases: Vec<String>,
}
#[derive(Serialize, Deserialize)]
struct AgentErrorCode {
    code: ErrorCode,
    retryable: bool,
}
#[derive(Serialize, Deserialize)]
struct AgentCommand {
    command: String,
    description: String,
    scope: String,
    mutates: bool,
    requires_profile: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    streaming: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    constraints: Vec<String>,
}
fn is_false(value: &bool) -> bool {
    !value
}
fn catalog() -> AgentCatalog {
    serde_json::from_str(include_str!("catalog.json")).expect("valid owned agent catalog")
}
