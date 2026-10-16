//! User and role inventories: one listing read, indexed concurrent detail
//! reads, Go-compatible decoding, late output validation, complete-success
//! JSON, and text tables that can precede an aggregate failure.
mod style;
mod wire;

pub(crate) use style::Terminal;

use crate::{
    ErrorCode,
    api::{ApiClient, ApiResponse},
    error::CliError,
    parser::{ParsedInvocation, quote},
    render,
};
use futures_util::{StreamExt, stream::FuturesUnordered};
use http::Method;
use serde::Serialize;
use std::io::Write as _;
use std::{collections::BTreeMap, future::Future};
use style::{BODY, HEADER, MUTED, RULE, Style, pad_right, truncate_cell, width};
use wire::Action;

/// Successful command output. Text printing ignores writer failures as Go's
/// fmt.Print does; JSON reports them like cmd.writeJSON.
#[derive(Debug, PartialEq)]
pub(crate) struct Output {
    pub(crate) stdout: String,
    pub(crate) json: bool,
}

impl Output {
    pub(crate) fn write(&self, stdout: &mut dyn std::io::Write) -> Result<(), CliError> {
        match stdout.write_all(self.stdout.as_bytes()) {
            Err(error) if self.json => Err(CliError::output("failed to encode JSON output", error)),
            _ => Ok(()),
        }
    }
}

/// Execute `pb user list` or `pb role list`. A text failure after a rendered
/// table returns that table in `CliError::stdout`.
pub(crate) async fn execute(
    parsed: &ParsedInvocation,
    client: &ApiClient,
    terminal: &Terminal,
) -> Result<Output, CliError> {
    match parsed.command_path.as_str() {
        "pb user list" => user_list(parsed, client, terminal).await,
        "pb role list" => role_list(parsed, client, terminal).await,
        path => Err(CliError::new(
            ErrorCode::CommandFailed,
            format!("unsupported inventory command {}", quote(path)),
        )),
    }
}

/// GET user identifiers in server order, duplicates retained (cmd.fetchUsers).
pub(crate) async fn user_ids(client: &ApiClient) -> Result<Vec<String>, CliError> {
    let response = get(client, "user").await?;
    if response.status != 200 {
        return Err(listing_status(&response));
    }
    wire::user_ids(&response.body).map_err(command_failed)
}

/// GET role names (cmd.fetchRoles). Go map order is random; this keeps the
/// response's first-insertion order with duplicate keys collapsed.
pub(crate) async fn role_names(client: &ApiClient) -> Result<Vec<String>, CliError> {
    let response = get(client, "roles").await?;
    if response.status != 200 {
        return Err(listing_status(&response));
    }
    wire::role_names(&response.body).map_err(command_failed)
}

fn command_failed(message: String) -> CliError {
    CliError::new(ErrorCode::CommandFailed, message)
}

async fn get(client: &ApiClient, path: &str) -> Result<ApiResponse, CliError> {
    client
        .standard(Method::GET, path, None)
        .await
        .map_err(CliError::from)
}

/// The listing and role-detail helpers' typed HTTP failure.
fn listing_status(response: &ApiResponse) -> CliError {
    let status = response.status_text();
    let mut message = format!("request failed\nstatus code: {status}\nresponse: ").into_bytes();
    message.extend_from_slice(&response.body);
    CliError::http(
        response.status,
        crate::error::go_string(&message),
        format!("request failed: {status}"),
    )
    .with_raw_message(message)
}

/// cmd.fetchUserRoles, flattened to role names; the body is released here.
async fn user_roles(client: &ApiClient, id: &str) -> Result<Vec<String>, Vec<u8>> {
    let response = get(client, &format!("user/{id}/role"))
        .await
        .map_err(|error| error.message_bytes().to_vec())?;
    if response.status != 200 {
        return Err(CliError::status(
            "fetch user roles",
            response.status,
            &response.status_text(),
            &response.body,
        )
        .message_bytes()
        .to_vec());
    }
    wire::user_role_names(&response.body).map_err(String::into_bytes)
}

/// cmd.fetchSpecificRole; the body is released here.
async fn role_actions(client: &ApiClient, name: &str) -> Result<Option<Vec<Action>>, Vec<u8>> {
    let response = get(client, &format!("role/{name}"))
        .await
        .map_err(|error| error.message_bytes().to_vec())?;
    if response.status != 200 {
        return Err(listing_status(&response).message_bytes().to_vec());
    }
    wire::role_actions(&response.body).map_err(String::into_bytes)
}

fn json_output(parsed: &ParsedInvocation) -> Result<bool, CliError> {
    crate::output_format(parsed)
        .map(|format| format == "json")
        .map_err(|message| CliError::new(ErrorCode::InvalidInput, message))
}

/// Per-entry failures collapse into one plain Go error, so the aggregate is
/// COMMAND_FAILED even when an individual read had a typed HTTP status.
fn aggregate<T>(
    names: &[String],
    details: &[Result<T, Vec<u8>>],
    entry: &str,
    summary: impl FnOnce(usize) -> String,
) -> Option<CliError> {
    let failures: Vec<Vec<u8>> = names
        .iter()
        .zip(details)
        .filter_map(|(name, detail)| {
            detail.as_ref().err().map(|error| {
                let mut text = format!("{entry} {name}: ").into_bytes();
                text.extend_from_slice(error);
                text
            })
        })
        .collect();
    (!failures.is_empty()).then(|| {
        let mut message = format!("{}: ", summary(failures.len())).into_bytes();
        for (index, failure) in failures.iter().enumerate() {
            if index != 0 {
                message.extend_from_slice(b"; ");
            }
            message.extend_from_slice(failure);
        }
        command_failed(crate::error::go_string(&message)).with_raw_message(message)
    })
}

/// Start every detail read like Go, retaining results in listing order. A
/// provisional 64-request cap failed the wide server-barrier oracle; there is
/// no new queue deadline or concurrency limit in the compatibility path.
async fn indexed<T, F>(count: usize, mut start: impl FnMut(usize) -> F) -> Vec<T>
where
    F: Future<Output = T>,
{
    let mut results: Vec<Option<T>> = std::iter::repeat_with(|| None).take(count).collect();
    let mut active: FuturesUnordered<_> = (0..count)
        .map(|index| {
            let future = start(index);
            async move { (index, future.await) }
        })
        .collect();
    // The ready queue polls a pending read again only after its own wake.
    // All reads remain owned here, so dropping the gather cancels them all.
    while let Some((index, value)) = active.next().await {
        results[index] = Some(value);
    }
    results
        .into_iter()
        .map(|result| result.expect("every indexed future completed"))
        .collect()
}

async fn user_list(
    parsed: &ParsedInvocation,
    client: &ApiClient,
    terminal: &Terminal,
) -> Result<Output, CliError> {
    let users = user_ids(client).await?;
    let ids: &[String] = &users;
    let details = indexed(ids.len(), move |index| user_roles(client, &ids[index])).await;
    // Go validates the output flag only after every detail read completes.
    let json = json_output(parsed)?;
    let failure = aggregate(&users, &details, "Error fetching roles for", |count| {
        format!("failed to fetch roles for {count} user(s)")
    });
    if json {
        if let Some(error) = failure {
            return Err(error);
        }
        #[derive(Serialize)]
        struct Row<'a> {
            id: &'a str,
            roles: &'a [String],
        }
        let rows: Vec<Row<'_>> = users
            .iter()
            .zip(&details)
            .map(|(id, roles)| Row {
                id,
                roles: roles.as_deref().unwrap_or_default(),
            })
            .collect();
        return Ok(Output {
            stdout: render::json(&rows, false),
            json: true,
        });
    }
    finish_text(user_table(&users, &details, terminal), failure)
}

async fn role_list(
    parsed: &ParsedInvocation,
    client: &ApiClient,
    terminal: &Terminal,
) -> Result<Output, CliError> {
    let roles = role_names(client).await?;
    // Go validates the output flag before any detail read.
    let json = json_output(parsed)?;
    let names: &[String] = &roles;
    let details = indexed(names.len(), move |index| {
        role_actions(client, &names[index])
    })
    .await;
    let failure = aggregate(&roles, &details, "Error fetching role data for", |count| {
        format!("failed to fetch details for {count} role(s)")
    });
    if json {
        if let Some(error) = failure {
            return Err(error);
        }
        // map[string][]RoleData: sorted keys; nil actions encode as null.
        let document: BTreeMap<&str, &Option<Vec<Action>>> = roles
            .iter()
            .zip(&details)
            .filter_map(|(name, actions)| actions.as_ref().ok().map(|a| (name.as_str(), a)))
            .collect();
        return Ok(Output {
            stdout: render::json(&document, false),
            json: true,
        });
    }
    finish_text(role_table(&roles, &details, terminal), failure)
}

fn finish_text(stdout: Vec<u8>, failure: Option<CliError>) -> Result<Output, CliError> {
    match failure {
        Some(mut error) => {
            error.stdout = crate::error::go_string(&stdout);
            error.raw_stdout = Some(stdout.into_boxed_slice());
            Err(error)
        }
        None => Ok(Output {
            stdout: String::from_utf8(stdout).expect("successful inventory is decoded UTF-8"),
            json: false,
        }),
    }
}

/// A dotted leader of at least two cells.
fn leader(column: usize, used: usize) -> String {
    ".".repeat(column.saturating_sub(used + 1).max(2))
}

/// cmd.printUserRoleTable.
fn user_table(users: &[String], details: &[Result<Vec<String>, Vec<u8>>], t: &Terminal) -> Vec<u8> {
    const MAX_USER_WIDTH: usize = 64;
    const USER: &str = "USER";
    const ROLES: &str = "ROLES";
    let user_width = users
        .iter()
        .map(|user| width(user).min(MAX_USER_WIDTH))
        .fold(width(USER), usize::max);
    let role_column = user_width + 4;
    let mut out = format!(
        "\n{}{}\n{}{}\n",
        HEADER.render(&pad_right(USER, role_column), t),
        HEADER.render(ROLES, t),
        RULE.render(&"─".repeat(role_column), t),
        RULE.render(&"─".repeat(width(ROLES)), t)
    )
    .into_bytes();
    for (user, roles) in users.iter().zip(details) {
        let (text, style) = match roles {
            Err(error) => {
                let mut text = b"error: ".to_vec();
                text.extend_from_slice(error);
                (text, MUTED)
            }
            Ok(roles) if !roles.is_empty() => (roles.join(", ").into_bytes(), BODY),
            Ok(_) => (b"-".to_vec(), MUTED),
        };
        let cell = truncate_cell(user, MAX_USER_WIDTH);
        write!(
            out,
            "{} {} ",
            BODY.render(&cell, t),
            RULE.render(&leader(role_column, width(&cell)), t)
        )
        .unwrap();
        out.extend(style.render_bytes(&text, t));
        out.push(b'\n');
    }
    out.push(b'\n');
    out
}

/// cmd.printRoleTable.
fn role_table(
    roles: &[String],
    details: &[Result<Option<Vec<Action>>, Vec<u8>>],
    t: &Terminal,
) -> Vec<u8> {
    const MAX_ROLE_WIDTH: usize = 42;
    const MAX_DATASET_WIDTH: usize = 48;
    let dataset_cell = |action: &Action| match &action.resource {
        Some(resource) if !resource.dataset().is_empty() => resource.dataset().to_owned(),
        _ => "-".to_owned(),
    };
    let mut role_width = width("ROLE");
    let mut privilege_width = width("PRIVILEGE");
    let mut dataset_width = width("DATASET");
    for (name, detail) in roles.iter().zip(details) {
        role_width = role_width.max(width(name).min(MAX_ROLE_WIDTH));
        for action in detail.iter().flatten().flatten() {
            privilege_width = privilege_width.max(width(&action.privilege));
            dataset_width = dataset_width.max(width(&dataset_cell(action)).min(MAX_DATASET_WIDTH));
        }
    }
    let privilege_column = role_width + 4;
    let dataset_column = privilege_column + privilege_width + 4;

    let mut out = format!(
        "\n{}{}{}\n{}{}{}\n",
        HEADER.render(&pad_right("ROLE", privilege_column + 1), t),
        HEADER.render(&pad_right("PRIVILEGE", privilege_width + 5), t),
        HEADER.render("DATASET", t),
        RULE.render(&"─".repeat(privilege_column + 1), t),
        RULE.render(&"─".repeat(privilege_width + 5), t),
        RULE.render(&"─".repeat(dataset_width), t)
    )
    .into_bytes();

    let mut row = |role: &str,
                   privilege: &str,
                   privilege_style: Style,
                   dataset: &[u8],
                   dataset_style: Style| {
        if role.is_empty() {
            out.extend(std::iter::repeat_n(b' ', privilege_column));
        } else {
            out.extend(BODY.render(role, t).bytes());
            out.push(b' ');
            out.extend(
                RULE.render(&leader(privilege_column, width(role)), t)
                    .bytes(),
            );
        }
        out.push(b' ');
        let privilege = truncate_cell(privilege, privilege_width);
        out.extend(privilege_style.render(&privilege, t).bytes());
        out.push(b' ');
        out.extend(
            RULE.render(
                &leader(dataset_column - privilege_column, width(&privilege)),
                t,
            )
            .bytes(),
        );
        out.push(b' ');
        out.extend(
            dataset_style.render_bytes(&style::truncate_bytes(dataset, MAX_DATASET_WIDTH), t),
        );
        out.push(b'\n');
    };
    for (name, detail) in roles.iter().zip(details) {
        let role = truncate_cell(name, MAX_ROLE_WIDTH);
        let actions = match detail {
            Err(error) => {
                let mut text = b"error: ".to_vec();
                text.extend_from_slice(error);
                row(&role, "-", MUTED, &text, MUTED);
                continue;
            }
            Ok(actions) => actions.as_deref().unwrap_or_default(),
        };
        if actions.is_empty() {
            row(&role, "-", MUTED, b"-", MUTED);
            continue;
        }
        for (index, action) in actions.iter().enumerate() {
            let dataset = dataset_cell(action);
            let dataset_style = if dataset == "-" { MUTED } else { BODY };
            let privilege = if action.privilege.trim().is_empty() {
                "-"
            } else {
                &action.privilege
            };
            row(
                if index == 0 { &role } else { "" },
                privilege,
                BODY,
                dataset.as_bytes(),
                dataset_style,
            );
        }
    }
    out.push(b'\n');
    out
}

#[cfg(test)]
mod tests;
