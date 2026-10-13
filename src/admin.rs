//! Administration owns validation stages, request sequencing, and output policy.
use crate::{
    ErrorCode, Outcome, admin_datasets, admin_inventory, api::ApiClient, config::ProfileStore,
    error::CliError, parser::ParsedInvocation, spec::CommandSpec,
};
use http::Method;
use std::io::Write;

#[derive(Clone, Copy, Debug)]
enum Operation {
    Dataset,
    UserCreate,
    UserDelete,
    UserAddRoles,
    UserList,
    RoleDelete,
    RoleList,
}
impl Operation {
    fn parse(parsed: &ParsedInvocation) -> Option<Self> {
        Some(match parsed.command_path.as_str() {
            "pb dataset add" if !trim(parsed.text("type")).is_empty() => Self::Dataset,
            "pb dataset remove" => Self::Dataset,
            "pb user add" => Self::UserCreate,
            "pb user remove" => Self::UserDelete,
            "pb user set-role" => Self::UserAddRoles,
            "pb user list" => Self::UserList,
            "pb role remove" => Self::RoleDelete,
            "pb role list" => Self::RoleList,
            _ => return None,
        })
    }
}
pub(crate) fn is_operation(parsed: &ParsedInvocation) -> bool {
    Operation::parse(parsed).is_some()
}

enum CommandOutput {
    Mutation(Vec<u8>),
    Inventory(admin_inventory::Output),
}

pub(crate) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    structured: bool,
    stdout: &mut (dyn Write + Send),
) -> Outcome {
    let operation = Operation::parse(parsed).expect("administration predicate checked");
    let result = (|| {
        let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
        let profile = store
            .active_profile()
            .map_err(|e| CliError::new(e.code, e.message))?;
        let client = ApiClient::new(profile, store)?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| CliError::new(ErrorCode::CommandFailed, e.to_string()))?;
        crate::metrics::output::style::with_process_render(|| {
            runtime.block_on(async {
                match operation {
                    Operation::Dataset => admin_datasets::execute(parsed, &client)
                        .await
                        .map(CommandOutput::Mutation),
                    Operation::UserList | Operation::RoleList => {
                        let terminal = admin_inventory::Terminal::from_process();
                        admin_inventory::execute(parsed, &client, &terminal)
                            .await
                            .map(CommandOutput::Inventory)
                    }
                    Operation::UserCreate => create_user(parsed, &client)
                        .await
                        .map(CommandOutput::Mutation),
                    Operation::UserDelete => {
                        let name = &parsed.positionals[0];
                        delete(&client, &format!("user/{name}"), "delete user").await?;
                        Ok(CommandOutput::Mutation(
                            format!("Removed user {}\n", bold(name)).into_bytes(),
                        ))
                    }
                    Operation::UserAddRoles => add_roles(parsed, &client)
                        .await
                        .map(CommandOutput::Mutation),
                    Operation::RoleDelete => delete_role(parsed, &client)
                        .await
                        .map(CommandOutput::Mutation),
                }
            })
        })
    })();
    let path = &parsed.command_path;
    match result {
        Ok(CommandOutput::Mutation(bytes)) => {
            // Go fmt.Print ignores ordinary write failures after commit. JSON's
            // encoder propagates them. SIGPIPE remains a process-writer policy.
            let _ = stdout.write_all(&bytes);
            Outcome::default()
        }
        Ok(CommandOutput::Inventory(output)) => match output.write(stdout) {
            Ok(()) => Outcome::default(),
            Err(error) => error.render(command, path, structured),
        },
        Err(mut error) => {
            // Partial text is emitted even when a detail request failed.
            let _ = stdout.write_all(error.stdout_bytes());
            error.stdout.clear();
            error.raw_stdout = None;
            error.render(command, path, structured)
        }
    }
}

// Go strings.TrimSpace uses the Unicode White_Space property.
fn trim(value: &str) -> &str {
    value.trim_matches(|c: char| matches!(c, '\t'..='\r' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}'))
}

struct RoleInput<'a> {
    display: &'a str,
    names: Vec<&'a str>,
}
impl<'a> RoleInput<'a> {
    fn new(display: &'a str) -> Self {
        Self {
            display,
            names: display.split(',').map(trim).collect(),
        }
    }
    fn body(&self) -> Vec<u8> {
        // encoding/json.Marshal escapes HTML and line separators in requests.
        serde_json::to_string(&self.names)
            .expect("string list serializes")
            .replace('&', "\\u0026")
            .replace('<', "\\u003c")
            .replace('>', "\\u003e")
            .replace('\u{2028}', "\\u2028")
            .replace('\u{2029}', "\\u2029")
            .into_bytes()
    }
}
async fn create_user(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = &parsed.positionals[0];
    if client.profile().cloud {
        return Err(CliError::new(
            ErrorCode::InvalidInput,
            "`pb user add` is not available for Parseable Cloud. Please invite the user from the Parseable Cloud dashboard",
        ));
    }
    if admin_inventory::user_ids(client).await?.contains(name) {
        return Err(CliError::new(
            ErrorCode::Conflict,
            format!("user {name} already exists"),
        ));
    }
    let input = RoleInput::new(trim(parsed.text("role")));
    let mut available = admin_inventory::role_names(client).await?;
    if input.display.is_empty() {
        available.sort();
        let message = if let Some(first) = available.first() {
            format!(
                "a role is required to create user {name}.\nAvailable roles: {}\nAssign one, for example: `pb user add {name} --role {first}`",
                available.join(", ")
            )
        } else {
            format!(
                "a role is required to create user {name}. No roles are available. Create one first with `pb role add <role>`, then run `pb user add {name} --role <role>`"
            )
        };
        return Err(CliError::new(ErrorCode::InvalidInput, message));
    }
    for role in &input.names {
        if !available.iter().any(|r| r == role) {
            return Err(CliError::new(
                ErrorCode::NotFound,
                format!(
                    "role {role} doesn't exist; create it first with `pb role add {role}`, or use an existing role with `pb user add {name} --role <role>`"
                ),
            ));
        }
    }
    let response = client
        .standard(Method::POST, &format!("user/{name}"), Some(input.body()))
        .await?;
    if response.status != 200 {
        return Err(CliError::status(
            "create user",
            response.status,
            &response.status_text(),
            &response.body,
        ));
    }
    let mut output = format!("Added user: {name} \nPassword is: ").into_bytes();
    output.extend(response.body);
    output.extend(format!("\nRole(s) assigned: {}\n", input.display).bytes());
    Ok(output)
}
async fn add_roles(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = &parsed.positionals[0];
    if !admin_inventory::user_ids(client).await?.contains(name) {
        let message = if client.profile().cloud {
            format!(
                "user doesn't exist. Please invite the user from the Parseable Cloud dashboard first, then set the role with `pb user set-role {name} <role>`"
            )
        } else {
            format!("user doesn't exist. Please create the user with `pb user add {name}`")
        };
        return Err(CliError::new(ErrorCode::NotFound, message));
    }
    let input = RoleInput::new(&parsed.positionals[1]);
    let available = admin_inventory::role_names(client).await?;
    for role in &input.names {
        if !available.iter().any(|r| r == role) {
            return Err(CliError::new(
                ErrorCode::NotFound,
                format!("role {role} doesn't exist; create it using `pb role add {role}`"),
            ));
        }
    }
    let response = client
        .standard(
            Method::PATCH,
            &format!("user/{name}/role/add"),
            Some(input.body()),
        )
        .await?;
    if response.status != 200 {
        return Err(CliError::status(
            "set user roles",
            response.status,
            &response.status_text(),
            &response.body,
        ));
    }
    Ok(format!("Added role(s) {} to user {name}\n", input.display).into_bytes())
}
async fn delete_role(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = &parsed.positionals[0];
    let mut available = admin_inventory::role_names(client).await?;
    if !available.contains(name) {
        available.sort();
        let guidance = if available.is_empty() {
            "No roles are available".into()
        } else {
            format!("Available role names: {}", available.join(", "))
        };
        return Err(CliError::new(
            ErrorCode::NotFound,
            format!("role {name} doesn't exist. {guidance}"),
        ));
    }
    delete(client, &format!("role/{name}"), "delete role").await?;
    Ok(format!("Removed role {}\n", bold(name)).into_bytes())
}
async fn delete(client: &ApiClient, path: &str, action: &str) -> Result<(), CliError> {
    let request = client.prepare_standard(Method::DELETE, path, None)?;
    let response = client.send_streaming(request).await?;
    if response.status == 200 {
        return Ok(());
    }
    let response = response.collect().await?;
    Err(CliError::status(
        action,
        response.status,
        &response.status_text(),
        &response.body,
    ))
}
fn bold(name: &str) -> String {
    crate::metrics::output::style::BOLD.render(
        name,
        &crate::metrics::output::style::Terminal::from_process(),
        crate::metrics::output::line_width,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn role_payload_preserves_order_duplicates_empty_and_go_escaping() {
        let input = RoleInput::new(" reader,reader, ,<admin>&\u{2028}x ");
        assert_eq!(
            input.body(),
            br#"["reader","reader","","\u003cadmin\u003e\u0026\u2028x"]"#
        );
        assert_eq!(input.display, " reader,reader, ,<admin>&\u{2028}x ");
    }
}
