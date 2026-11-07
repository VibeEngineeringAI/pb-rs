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
    RoleCreate,
}
impl Operation {
    fn parse(parsed: &ParsedInvocation) -> Option<Self> {
        Some(match parsed.command_path.as_str() {
            "pb dataset add" => Self::Dataset,
            "pb dataset remove" => Self::Dataset,
            "pb user add" => Self::UserCreate,
            "pb user remove" => Self::UserDelete,
            "pb user set-role" => Self::UserAddRoles,
            "pb user list" => Self::UserList,
            "pb role remove" => Self::RoleDelete,
            "pb role list" => Self::RoleList,
            "pb role add" => Self::RoleCreate,
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
            let service = AdminService::new(&client);
            if matches!(operation, Operation::RoleCreate) {
                let name = parsed.positional_bytes(0);
                runtime.block_on(service.check_role_name(name))?;
                let privilege = crate::tui::admin::select_privilege()?;
                let Some(privilege) = privilege else {
                    return Ok(CommandOutput::Mutation(b"aborted by user\n".to_vec()));
                };
                return runtime
                    .block_on(service.create_role(name, privilege))
                    .map(CommandOutput::Mutation);
            }
            if matches!(operation, Operation::Dataset) && admin_datasets::deferred(parsed) {
                let kind = crate::tui::admin::select_dataset_kind()?;
                let Some(kind) = kind else {
                    return Ok(CommandOutput::Mutation(
                        b"Dataset creation canceled\n".to_vec(),
                    ));
                };
                return runtime
                    .block_on(service.create_dataset(parsed.positional_bytes(0), kind))
                    .map(CommandOutput::Mutation);
            }
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
                        let name = parsed.positional_bytes(0);
                        delete(
                            &client,
                            &[b"user/".as_slice(), name].concat(),
                            "delete user",
                        )
                        .await?;
                        Ok(CommandOutput::Mutation(
                            [b"Removed user ".as_slice(), &bold_bytes(name), b"\n"].concat(),
                        ))
                    }
                    Operation::UserAddRoles => add_roles(parsed, &client)
                        .await
                        .map(CommandOutput::Mutation),
                    Operation::RoleDelete => delete_role(parsed, &client)
                        .await
                        .map(CommandOutput::Mutation),
                    Operation::RoleCreate => unreachable!("interactive role handled above"),
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

/// Concrete service used by administration sessions. It preserves each
/// operation's preflight and response-body policy rather than unifying them.
pub(crate) struct AdminService<'a> {
    client: &'a ApiClient,
}
impl<'a> AdminService<'a> {
    pub(crate) fn new(client: &'a ApiClient) -> Self {
        Self { client }
    }
    pub(crate) async fn check_role_name(&self, name: &[u8]) -> Result<(), CliError> {
        if admin_inventory::role_names(self.client)
            .await?
            .iter()
            .any(|r| r.as_bytes() == name)
        {
            return Err(CliError::new(
                ErrorCode::Conflict,
                crate::error::go_string(
                    &[
                        b"role ".as_slice(),
                        name,
                        b" already exists; use a different name",
                    ]
                    .concat(),
                ),
            ));
        }
        Ok(())
    }
    pub(crate) async fn create_role(
        &self,
        name: &[u8],
        privilege: crate::tui::admin::Privilege,
    ) -> Result<Vec<u8>, CliError> {
        let body = serde_json::to_vec(&[serde_json::json!({"privilege": privilege.as_str()})])
            .expect("static privilege serializes");
        // Role creation reads even a successful body, unlike dataset creation.
        let response = self
            .client
            .standard_bytes(
                Method::PUT,
                &[b"role/".as_slice(), name].concat(),
                Some(body),
            )
            .await?;
        if response.status != 200 {
            return Err(CliError::status(
                "create role",
                response.status,
                &response.status_text(),
                &response.body,
            ));
        }
        Ok([b"Added role ".as_slice(), name].concat())
    }
    pub(crate) async fn create_dataset(
        &self,
        name: &[u8],
        kind: admin_datasets::DatasetKind,
    ) -> Result<Vec<u8>, CliError> {
        admin_datasets::create_selected(self.client, name, kind).await
    }
}

struct RoleInput<'a> {
    display: &'a [u8],
    names: Vec<&'a [u8]>,
}
impl<'a> RoleInput<'a> {
    fn new(display: &'a [u8]) -> Self {
        Self {
            display,
            names: display
                .split(|&b| b == b',')
                .map(crate::error::trim_bytes)
                .collect(),
        }
    }
    fn body(&self) -> Vec<u8> {
        let names: Vec<_> = self
            .names
            .iter()
            .map(|name| crate::error::go_string(name))
            .collect();
        serde_json::to_string(&names)
            .expect("string list serializes")
            .replace('&', "\\u0026")
            .replace('<', "\\u003c")
            .replace('>', "\\u003e")
            .replace('\u{2028}', "\\u2028")
            .replace('\u{2029}', "\\u2029")
            .into_bytes()
    }
}
fn bytes_error(code: ErrorCode, parts: &[&[u8]]) -> CliError {
    let bytes = parts.concat();
    CliError::new(code, crate::error::go_string(&bytes)).with_raw_message(bytes)
}
async fn create_user(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = parsed.positional_bytes(0);
    if client.profile().cloud {
        return Err(CliError::new(
            ErrorCode::InvalidInput,
            "`pb user add` is not available for Parseable Cloud. Please invite the user from the Parseable Cloud dashboard",
        ));
    }
    if admin_inventory::user_ids(client)
        .await?
        .iter()
        .any(|id| id.as_bytes() == name)
    {
        return Err(bytes_error(
            ErrorCode::Conflict,
            &[b"user ", name, b" already exists"],
        ));
    }
    let input = RoleInput::new(crate::error::trim_bytes(parsed.text_bytes("role")));
    let mut available = admin_inventory::role_names(client).await?;
    if input.display.is_empty() {
        available.sort();
        return Err(if let Some(first) = available.first() {
            bytes_error(
                ErrorCode::InvalidInput,
                &[
                    b"a role is required to create user ",
                    name,
                    b".\nAvailable roles: ",
                    available.join(", ").as_bytes(),
                    b"\nAssign one, for example: `pb user add ",
                    name,
                    b" --role ",
                    first.as_bytes(),
                    b"`",
                ],
            )
        } else {
            bytes_error(ErrorCode::InvalidInput,&[b"a role is required to create user ",name,b". No roles are available. Create one first with `pb role add <role>`, then run `pb user add ",name,b" --role <role>`"])
        });
    }
    for role in &input.names {
        if !available.iter().any(|r| r.as_bytes() == *role) {
            return Err(bytes_error(
                ErrorCode::NotFound,
                &[
                    b"role ",
                    role,
                    b" doesn't exist; create it first with `pb role add ",
                    role,
                    b"`, or use an existing role with `pb user add ",
                    name,
                    b" --role <role>`",
                ],
            ));
        }
    }
    let response = client
        .standard_bytes(
            Method::POST,
            &[b"user/".as_slice(), name].concat(),
            Some(input.body()),
        )
        .await?;
    if response.status != 200 {
        return Err(CliError::status(
            "create user",
            response.status,
            &response.status_text(),
            &response.body,
        ));
    }
    Ok([
        b"Added user: ".as_slice(),
        name,
        b" \nPassword is: ",
        &response.body,
        b"\nRole(s) assigned: ",
        input.display,
        b"\n",
    ]
    .concat())
}
async fn add_roles(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = parsed.positional_bytes(0);
    if !admin_inventory::user_ids(client)
        .await?
        .iter()
        .any(|id| id.as_bytes() == name)
    {
        return Err(if client.profile().cloud {
            bytes_error(ErrorCode::NotFound,&[b"user doesn't exist. Please invite the user from the Parseable Cloud dashboard first, then set the role with `pb user set-role ",name,b" <role>`"])
        } else {
            bytes_error(
                ErrorCode::NotFound,
                &[
                    b"user doesn't exist. Please create the user with `pb user add ",
                    name,
                    b"`",
                ],
            )
        });
    }
    let input = RoleInput::new(parsed.positional_bytes(1));
    let available = admin_inventory::role_names(client).await?;
    for role in &input.names {
        if !available.iter().any(|r| r.as_bytes() == *role) {
            return Err(bytes_error(
                ErrorCode::NotFound,
                &[
                    b"role ",
                    role,
                    b" doesn't exist; create it using `pb role add ",
                    role,
                    b"`",
                ],
            ));
        }
    }
    let response = client
        .standard_bytes(
            Method::PATCH,
            &[b"user/".as_slice(), name, b"/role/add"].concat(),
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
    Ok([
        b"Added role(s) ".as_slice(),
        input.display,
        b" to user ",
        name,
        b"\n",
    ]
    .concat())
}
async fn delete_role(parsed: &ParsedInvocation, client: &ApiClient) -> Result<Vec<u8>, CliError> {
    let name = parsed.positional_bytes(0);
    let mut available = admin_inventory::role_names(client).await?;
    if !available.iter().any(|role| role.as_bytes() == name) {
        available.sort();
        let guidance = if available.is_empty() {
            "No roles are available".into()
        } else {
            format!("Available role names: {}", available.join(", "))
        };
        return Err(bytes_error(
            ErrorCode::NotFound,
            &[b"role ", name, b" doesn't exist. ", guidance.as_bytes()],
        ));
    }
    delete(client, &[b"role/".as_slice(), name].concat(), "delete role").await?;
    Ok([b"Removed role ".as_slice(), &bold_bytes(name), b"\n"].concat())
}
async fn delete(client: &ApiClient, path: &[u8], action: &str) -> Result<(), CliError> {
    let request = client.prepare_standard_bytes(Method::DELETE, path, None)?;
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
fn bold_bytes(name: &[u8]) -> Vec<u8> {
    crate::metrics::output::style::BOLD.render_bytes(
        name,
        &crate::metrics::output::style::Terminal::from_process(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn role_preflight_and_single_privilege_mutation_use_existing_service() {
        use std::{
            io::{Read, Write},
            net::TcpListener,
            thread,
        };
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let client = ApiClient::new(
            crate::config::ProfileRecord {
                url: format!("http://{}", listener.local_addr().unwrap()),
                username: "u".into(),
                password: "p".into(),
                ..Default::default()
            },
            ProfileStore::new("/nonexistent/role-test"),
        )
        .unwrap();
        let server = thread::spawn(move || {
            let mut requests = Vec::new();
            for body in ["{}", "done"] {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(std::time::Duration::from_secs(2)))
                    .unwrap();
                let mut head = Vec::new();
                while !head.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    socket.read_exact(&mut byte).unwrap();
                    head.push(byte[0]);
                }
                let head = String::from_utf8(head).unwrap();
                let length: usize = head
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length: ")
                            .map(str::parse)
                    })
                    .transpose()
                    .unwrap()
                    .unwrap_or(0);
                let mut payload = vec![0; length];
                socket.read_exact(&mut payload).unwrap();
                requests.push((head, payload));
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
            requests
        });
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let service = AdminService::new(&client);
        runtime.block_on(service.check_role_name(b"new")).unwrap();
        let output = runtime
            .block_on(service.create_role(b"new", crate::tui::admin::Privilege::Ingestor))
            .unwrap();
        assert_eq!(output, b"Added role new");
        let requests = server.join().unwrap();
        assert!(requests[0].0.starts_with("GET /api/v1/roles HTTP/1.1"));
        assert!(requests[1].0.starts_with("PUT /api/v1/role/new HTTP/1.1"));
        assert_eq!(requests[1].1, br#"[{"privilege":"ingestor"}]"#);
    }
    #[test]
    fn role_payload_preserves_order_duplicates_empty_and_go_escaping() {
        let input = RoleInput::new(" reader,reader, ,<admin>&\u{2028}x ".as_bytes());
        assert_eq!(
            input.body(),
            br#"["reader","reader","","\u003cadmin\u003e\u0026\u2028x"]"#
        );
        assert_eq!(
            input.display,
            " reader,reader, ,<admin>&\u{2028}x ".as_bytes()
        );
    }
}
