//! Saved SQL creation, projection, and the interactive menu's fetch policy.
use crate::{
    ErrorCode, Outcome,
    api::ApiClient,
    config::ProfileStore,
    error::CliError,
    gojson::{self, Node},
    parser::ParsedInvocation,
    render,
    spec::CommandSpec,
    time_compat,
};
use http::Method;
use serde::Serialize;
use std::io::Write;

#[derive(Default, Debug, Serialize)]
struct SavedSql {
    id: String,
    title: String,
    stream: String,
    desc: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    from: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    to: String,
}

/// Owned menu result. `sql` is the saved description, never the dataset name.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SavedSelection {
    pub(crate) id: String,
    pub(crate) title: String,
    pub(crate) dataset: String,
    pub(crate) sql: String,
    pub(crate) from: String,
    pub(crate) to: String,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct SavedApplication {
    pub(crate) query: String,
    pub(crate) from: String,
    pub(crate) to: String,
}
impl SavedSelection {
    /// The command only attempts application when an ID or nonblank SQL is
    /// present. An all-empty menu item behaves like quit, without a message.
    pub(crate) fn should_apply(&self) -> bool {
        !self.id.is_empty() || !trim(&self.sql).is_empty()
    }
    /// An applied blank SQL selection has its own stdout message. This method
    /// returns None for that case; menu quit is a separate outer Option.
    pub(crate) fn application(&self) -> Option<SavedApplication> {
        if trim(&self.sql).is_empty() {
            return None;
        }
        Some(SavedApplication {
            query: crate::sql::rewrite::quote_fields_with_dots(
                &crate::sql::rewrite::quote_stream_names(&self.sql),
            ),
            from: default_time(&self.from, "1h").into(),
            to: default_time(&self.to, "now").into(),
        })
    }
}
impl From<SavedSql> for SavedSelection {
    fn from(saved: SavedSql) -> Self {
        Self {
            id: saved.id,
            title: saved.title,
            dataset: saved.stream,
            sql: saved.desc,
            from: saved.from,
            to: saved.to,
        }
    }
}

/// Called after command preflight. Fetch is independent of any explicitly
/// chosen application profile and finishes before acquiring the alt screen.
pub(crate) fn select_menu(stderr: &mut dyn Write) -> Result<Option<SavedSelection>, CliError> {
    let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
    // Go's ReadConfigFromFile returns an empty, non-nil Config on failure.
    // The menu reports the reread failure and continues with that empty value.
    let config = store.read().unwrap_or_else(|_| {
        let _ = writeln!(stderr, "Error reading Default Profile");
        Default::default()
    });
    let profile = config
        .profiles
        .get(&config.default_profile)
        .cloned()
        .unwrap_or_default();
    let queries = match ApiClient::new(profile, store) {
        Ok(client) => fetch_menu(&client, &runtime()?, stderr),
        Err(error) => {
            let _ = writeln!(stderr, "Error creating request: {error}");
            Vec::new()
        }
    };
    crate::tui::saved::select(queries)
}

/// The menu intentionally does NOT inspect HTTP status. It reports each
/// failure stage on stderr and still opens an empty list, unlike `list -o`.
fn fetch_menu(
    client: &ApiClient,
    runtime: &tokio::runtime::Runtime,
    stderr: &mut dyn Write,
) -> Vec<SavedSelection> {
    let request = match client.prepare_standard(Method::GET, "filters", None) {
        Ok(request) => request,
        Err(error) => {
            let _ = writeln!(stderr, "Error creating request: {error}");
            return Vec::new();
        }
    };
    let response = match runtime.block_on(client.send_streaming(request)) {
        Ok(response) => response,
        Err(error) => {
            let _ = writeln!(stderr, "Error making request: {error}");
            return Vec::new();
        }
    };
    let response = match runtime.block_on(response.collect()) {
        Ok(response) => response,
        Err(error) => {
            let _ = writeln!(stderr, "Error reading response body: {error}");
            return Vec::new();
        }
    };
    match decode(&response.body) {
        Ok(queries) => queries.into_iter().map(SavedSelection::from).collect(),
        Err(error) => {
            let _ = writeln!(stderr, "Error unmarshalling response: {error}");
            Vec::new()
        }
    }
}

fn error(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::CommandFailed, message)
}
fn context(label: &str, cause: impl Into<CliError>) -> CliError {
    let mut e = cause.into();
    e.message = format!("{label}: {}", e.message);
    e.public_message = e.message.clone();
    e
}
fn client() -> Result<ApiClient, CliError> {
    let store = ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
    let profile = store
        .active_profile()
        .map_err(|e| CliError::new(e.code, e.message))?;
    ApiClient::new(profile, store).map_err(CliError::from)
}
fn runtime() -> Result<tokio::runtime::Runtime, CliError> {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| error(e.to_string()))
}

pub(super) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    stdout: &mut (dyn Write + Send),
) -> Outcome {
    let result = if path.ends_with(" list") || path.ends_with(" ls") {
        if parsed.text("output").is_empty() {
            return crate::failure(
                command,
                path,
                &format!(
                    "command {} is not implemented in this Rust phase",
                    crate::parser::quote(path)
                ),
                ErrorCode::CommandFailed,
                structured,
                false,
            );
        }
        list(parsed)
    } else {
        save(parsed)
    };
    match result {
        Ok(outcome) => {
            let json = path.ends_with(" list") && crate::output_format(parsed) == Ok("json");
            if let Err(e) = stdout.write_all(&outcome.stdout)
                && json
            {
                return CliError::output("failed to encode JSON output", e)
                    .render(command, path, structured);
            }
            Outcome::default()
        }
        Err(e) => e.render(command, path, structured),
    }
}

fn list(parsed: &ParsedInvocation) -> Result<Outcome, CliError> {
    let format =
        crate::output_format(parsed).map_err(|e| CliError::new(ErrorCode::InvalidInput, e))?;
    let client = client().map_err(|e| context("failed to create saved-query request", e))?;
    let request = client
        .prepare_standard(Method::GET, "filters", None)
        .map_err(|e| context("failed to create saved-query request", e))?;
    let runtime = runtime()?;
    let response = runtime
        .block_on(client.send_streaming(request))
        .map_err(|e| context("failed to fetch saved queries", e))?;
    let response = runtime
        .block_on(response.collect())
        .map_err(|e| context("failed to read saved-query response", e))?;
    if !(200..300).contains(&response.status) {
        let public = format!("failed to fetch saved queries: {}", response.status_text());
        return Err(CliError::http(
            response.status,
            format!(
                "{public}: {}",
                trim(&String::from_utf8_lossy(&response.body))
            ),
            public,
        ));
    }
    let queries = decode(&response.body)
        .map_err(|e| context("failed to decode saved-query response", error(e)))?;
    let output = if format == "json" {
        render::json(&queries, false)
    } else {
        let mut out = String::new();
        for q in queries {
            let parts = [&q.title, &q.stream, &q.desc, &q.from, &q.to]
                .into_iter()
                .filter(|s| !s.is_empty())
                .map(String::as_str)
                .collect::<Vec<_>>();
            out.push_str(&parts.join(", "));
            out.push('\n');
        }
        out
    };
    Ok(Outcome::success(output))
}
fn save(parsed: &ParsedInvocation) -> Result<Outcome, CliError> {
    let query = trim(parsed.positionals.first().map(String::as_str).unwrap_or(""));
    if query.is_empty() {
        return Err(CliError::new(
            ErrorCode::InvalidInput,
            "SQL query is required; example: pb sql save \"select * from frontend\"",
        ));
    }
    let query = crate::sql::rewrite::prepare_query(query);
    let supplied = trim(parsed.text("name"));
    let name = if supplied.is_empty() {
        let stream = extract_stream(&query);
        if stream.is_empty() {
            "saved-query"
        } else {
            stream
        }
    } else {
        supplied
    };
    create(
        &query,
        name,
        default_time(parsed.text("from"), "1m"),
        default_time(parsed.text("to"), "now"),
    )?;
    Ok(Outcome::success(format!("Query saved as '{name}'\n")))
}
pub(super) fn save_after_query(
    parsed: &ParsedInvocation,
    query: &str,
    client: &ApiClient,
    runtime: &tokio::runtime::Runtime,
) -> Result<(), CliError> {
    create_with_client(
        Some((client, runtime)),
        query,
        parsed.text("save-as"),
        default_time(parsed.text("from"), "1m"),
        default_time(parsed.text("to"), "now"),
    )
}
fn default_time<'a>(value: &'a str, default: &'a str) -> &'a str {
    if value.is_empty() { default } else { value }
}
fn create(query: &str, name: &str, from: &str, to: &str) -> Result<(), CliError> {
    create_with_client(None, query, name, from, to)
}
fn create_with_client(
    supplied: Option<(&ApiClient, &tokio::runtime::Runtime)>,
    query: &str,
    name: &str,
    from: &str,
    to: &str,
) -> Result<(), CliError> {
    let from =
        time_compat::parse_time(from).map_err(|e| error(format!("invalid start time: {e}")))?;
    let to = time_compat::parse_time(to).map_err(|e| error(format!("invalid end time: {e}")))?;
    let owned_client;
    let client = match supplied {
        Some((client, _)) => client,
        None => {
            owned_client = self::client()?;
            &owned_client
        }
    };
    #[derive(Serialize)]
    struct Sql<'a> {
        filter_type: &'static str,
        filter_query: &'a str,
    }
    #[derive(Serialize)]
    struct Time {
        from: String,
        to: String,
    }
    #[derive(Serialize)]
    struct Filter<'a> {
        stream_name: &'a str,
        filter_name: &'a str,
        user_id: String,
        query: Sql<'a>,
        time_filter: Time,
    }
    let body = serde_json::to_string(&Filter {
        stream_name: extract_stream(query),
        filter_name: name,
        user_id: client.profile().username,
        query: Sql {
            filter_type: "sql",
            filter_query: query,
        },
        time_filter: Time {
            from: time_compat::format_time(from),
            to: time_compat::format_time(to),
        },
    })
    .unwrap()
    .replace('&', "\\u0026")
    .replace('<', "\\u003c")
    .replace('>', "\\u003e")
    .replace('\u{2028}', "\\u2028")
    .replace('\u{2029}', "\\u2029")
    .into_bytes();
    let request = client
        .prepare_standard(Method::POST, "filters", Some(body))
        .map_err(CliError::from)?;
    let owned_runtime;
    let runtime = match supplied {
        Some((_, runtime)) => runtime,
        None => {
            owned_runtime = self::runtime()?;
            &owned_runtime
        }
    };
    let mut response = runtime
        .block_on(client.send_streaming(request))
        .map_err(CliError::from)?;
    if response.status == 200 {
        return Ok(());
    } // Go closes success without reading it.
    let status = response.status;
    let reason = if response.reason.is_empty() {
        status.to_string()
    } else {
        format!("{status} {}", response.reason)
    };
    let mut body = Vec::new();
    runtime.block_on(async {
        while let Ok(Some(chunk)) = response.chunk().await {
            body.extend_from_slice(&chunk);
        }
    });
    let public = format!("server returned {reason}");
    Err(CliError::http(
        status,
        format!("{public}: {}", trim(&String::from_utf8_lossy(&body))),
        public,
    ))
}

// Go's regexp uses ASCII word boundaries and whitespace; it also scans literals/comments.
fn extract_stream(query: &str) -> &str {
    let bytes = query.as_bytes();
    let word = |b: u8| b.is_ascii_alphanumeric() || b == b'_';
    for i in 0..bytes.len() {
        if i > 0 && word(bytes[i - 1]) {
            continue;
        }
        if !bytes
            .get(i..i + 4)
            .is_some_and(|s| s.eq_ignore_ascii_case(b"from"))
        {
            continue;
        }
        let mut j = i + 4;
        if !bytes
            .get(j)
            .is_some_and(|b| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | b'\x0c'))
        {
            continue;
        }
        while bytes
            .get(j)
            .is_some_and(|b| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | b'\x0c'))
        {
            j += 1;
        }
        if bytes.get(j) == Some(&b'"') {
            if let Some(end) = bytes[j + 1..].iter().position(|&b| b == b'"')
                && end != 0
            {
                return &query[j + 1..j + 1 + end];
            }
        } else if bytes
            .get(j)
            .is_some_and(|b| b.is_ascii_alphabetic() || *b == b'_')
        {
            let start = j;
            j += 1;
            while bytes.get(j).is_some_and(|b| word(*b) || *b == b'-') {
                j += 1;
            }
            return &query[start..j];
        }
    }
    ""
}
fn trim(value: &str) -> &str {
    crate::sql::rewrite::trim_sql_space(value)
}

fn decode(body: &[u8]) -> Result<Vec<SavedSql>, String> {
    let doc = gojson::decode_ordered(body)?;
    if doc.is_null() {
        return Ok(Vec::new());
    }
    let array = doc.as_array().ok_or_else(|| {
        format!(
            "json: cannot unmarshal {} into Go value of type []model.Filter",
            doc.kind()
        )
    })?;
    let mut first_error = None;
    let mut result = Vec::new();
    for node in array {
        let mut fields = std::collections::HashMap::new();
        validate(node, "Filter", "", &mut fields, &mut first_error);
        if let Some(sql) = fields.get("query.filter_query") {
            result.push(SavedSql {
                id: fields.get("filter_id").cloned().unwrap_or_default(),
                title: fields.get("filter_name").cloned().unwrap_or_default(),
                stream: fields.get("stream_name").cloned().unwrap_or_default(),
                desc: sql.clone(),
                from: fields.get("time_filter.from").cloned().unwrap_or_default(),
                to: fields.get("time_filter.to").cloned().unwrap_or_default(),
            });
        }
    }
    match first_error {
        Some(e) => Err(e),
        None => Ok(result),
    }
}
fn validate(
    node: &Node,
    typename: &str,
    path: &str,
    fields: &mut std::collections::HashMap<String, String>,
    first: &mut Option<String>,
) {
    if node.is_null() {
        return;
    }
    let Some(object) = node.as_object() else {
        type_error(
            node,
            if path.is_empty() {
                "Go value of type model.Filter".to_string()
            } else {
                format!("Go struct field {typename}.{path} of type model.{typename}")
            },
            first,
        );
        return;
    };
    for (name, value) in object {
        let name: String = name
            .chars()
            .map(|ch| match ch {
                '\u{017f}' => 's',
                '\u{212a}' => 'k',
                _ => ch.to_ascii_lowercase(),
            })
            .collect();
        let expected = match (typename, name.as_str()) {
            ("Filter", "version" | "user_id" | "stream_name" | "filter_name" | "filter_id") => {
                "string"
            }
            ("Filter", "query") => "Query",
            ("Filter", "time_filter") => "TimeFilter",
            ("Query", "filter_type" | "filter_query") => "string",
            ("Query", "filter_builder") => "FilterBuilder",
            ("TimeFilter", "from" | "to") => "string",
            ("FilterBuilder" | "RuleSet", "id" | "combinator") => "string",
            ("FilterBuilder", "rules") => "[]RuleSet",
            ("RuleSet", "rules") => "[]Rule",
            ("Rule", "id" | "field" | "value" | "operator") => "string",
            _ => continue,
        };
        let field = if path.is_empty() {
            name
        } else {
            format!("{path}.{name}")
        };
        if value.is_null() {
            if field == "query.filter_query" {
                fields.remove(&field);
            }
            continue;
        }
        if expected == "string" {
            if let Some(s) = value.as_str() {
                fields.insert(field, s.to_string());
            } else {
                type_error(
                    value,
                    format!("Go struct field {typename}.{field} of type string"),
                    first,
                );
            }
        } else if let Some(element) = expected.strip_prefix("[]") {
            if let Some(array) = value.as_array() {
                for entry in array {
                    if entry.is_null() || entry.is_object() {
                        validate(entry, element, &field, fields, first);
                    } else {
                        type_error(
                            entry,
                            format!("Go struct field {typename}.{field} of type model.{element}"),
                            first,
                        );
                    }
                }
            } else {
                type_error(
                    value,
                    format!("Go struct field {typename}.{field} of type []model.{element}"),
                    first,
                );
            }
        } else if value.is_object() {
            validate(value, expected, &field, fields, first);
        } else {
            type_error(
                value,
                format!("Go struct field {typename}.{field} of type model.{expected}"),
                first,
            );
        }
    }
}
fn type_error(node: &Node, destination: String, first: &mut Option<String>) {
    if first.is_none() {
        *first = Some(format!(
            "json: cannot unmarshal {} into {destination}",
            node.kind()
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn menu_application_keeps_printed_query_separate_from_wire_limit() {
        let selected = SavedSelection {
            id: "id".into(),
            dataset: "must-not-execute".into(),
            sql: "select obj.key from app-logs".into(),
            ..Default::default()
        };
        let application = selected.application().unwrap();
        assert_eq!(application.query, "select \"obj.key\" from \"app-logs\"");
        assert_eq!(
            (application.from.as_str(), application.to.as_str()),
            ("1h", "now")
        );
        assert_eq!(
            crate::sql::rewrite::ensure_default_limit(&application.query),
            "select \"obj.key\" from \"app-logs\" LIMIT 500"
        );
        assert!(
            SavedSelection {
                sql: " \t\n".into(),
                id: "id".into(),
                ..Default::default()
            }
            .application()
            .is_none()
        );
    }
    #[test]
    fn menu_fetch_accepts_non_success_arrays_and_reports_decode_failure() {
        use std::{
            io::{Read, Write},
            net::TcpListener,
            thread,
        };
        for body in [br#"[{"filter_name":"Menu","stream_name":"wrong","query":{"filter_query":"select 1"}}]"#.as_slice(), b"oops".as_slice()] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let client = ApiClient::new(crate::config::ProfileRecord {
                url: format!("http://{}", listener.local_addr().unwrap()),
                username: "u".into(), password: "p".into(), ..Default::default()
            }, ProfileStore::new("/nonexistent/menu-test")).unwrap();
            let body = body.to_vec();
            let invalid = body == b"oops";
            let server = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                socket.set_read_timeout(Some(std::time::Duration::from_secs(2))).unwrap();
                let mut head = Vec::new();
                while !head.ends_with(b"\r\n\r\n") {
                    let mut byte = [0]; socket.read_exact(&mut byte).unwrap(); head.push(byte[0]);
                }
                let head = String::from_utf8(head).unwrap();
                assert!(head.starts_with("GET /api/v1/filters HTTP/1.1"));
                write!(socket, "HTTP/1.1 500 Internal Server Error\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
                socket.write_all(&body).unwrap();
            });
            let mut stderr = Vec::new();
            let result = fetch_menu(&client, &runtime().unwrap(), &mut stderr);
            server.join().unwrap();
            if invalid {
                assert!(result.is_empty());
                assert_eq!(String::from_utf8(stderr).unwrap(), "Error unmarshalling response: invalid character 'o' looking for beginning of value\n");
            } else {
                assert_eq!(result.len(), 1);
                assert_eq!(result[0].sql, "select 1");
                assert_eq!(result[0].dataset, "wrong");
                assert!(stderr.is_empty());
            }
        }
    }
    #[test]
    fn projection_filters_only_null_sql_and_validates_builders() {
        let q = decode(br#"[{"query":{"filter_type":"builder","filter_query":""}},{"query":{}},{"query":{"filter_query":null}},{"query":{"filter_query":"select 1"},"time_filter":{"from":"then"}}]"#).unwrap();
        assert_eq!(q.len(), 2);
        assert_eq!(q[0].desc, "");
        assert_eq!(q[1].from, "then");
        let e = decode(br#"[{"query":{"filter_builder":{"rules":[{"rules":[{"value":9}]}]}}}]"#)
            .unwrap_err();
        assert_eq!(
            e,
            "json: cannot unmarshal number into Go struct field Rule.query.filter_builder.rules.rules.value of type string"
        );
        assert_eq!(render::json(&decode(b"null").unwrap(), false), "[]\n");
    }
    #[test]
    fn duplicate_nulls_and_case_matching_follow_go_structs() {
        let q = decode(br#"[{"FILTER_NAME":"a","filter_name":null,"QUERY":{"FILTER_QUERY":"x","filter_query":null}},{"query":{"filter_query":"x"},"QUERY":null}]"#).unwrap();
        assert_eq!(q.len(), 1);
        assert_eq!(q[0].desc, "x");
    }
    #[test]
    fn stream_extraction_retains_regex_quirks() {
        for (q, expected) in [
            ("select * from logs.v1", "logs"),
            ("'from a-b'", "a-b"),
            ("éfrom 中文 from \"a b\"", "a b"),
            ("from \"\" from second", "second"),
            ("from\u{000b}logs", ""),
            ("xfrom logs", ""),
        ] {
            assert_eq!(extract_stream(q), expected);
        }
    }
    #[test]
    fn successful_create_drops_stalled_body_and_encodes_go_request() {
        use std::{
            io::{Read, Write},
            net::TcpListener,
            sync::mpsc,
            thread,
            time::Duration,
        };
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (sent, received) = mpsc::channel();
        let (release, released) = mpsc::channel();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut headers = Vec::new();
            while !headers.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                headers.push(byte[0]);
            }
            let head = String::from_utf8(headers).unwrap();
            let length: usize = head
                .lines()
                .find_map(|l| {
                    l.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .map(str::parse)
                })
                .unwrap()
                .unwrap();
            let mut body = vec![0; length];
            socket.read_exact(&mut body).unwrap();
            sent.send((head, body)).unwrap();
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n")
                .unwrap();
            let _ = released.recv_timeout(Duration::from_secs(2));
        });
        let (completed, completion) = mpsc::channel();
        let worker = thread::spawn(move || {
            let client = ApiClient::new(
                crate::config::ProfileRecord {
                    url: endpoint,
                    username: "u".into(),
                    password: "p".into(),
                    ..Default::default()
                },
                ProfileStore::new("/nonexistent/saved-query-test"),
            )
            .unwrap();
            let runtime = runtime().unwrap();
            let result = create_with_client(
                Some((&client, &runtime)),
                "select * from logs LIMIT 500",
                "a<&",
                "2024-01-01T00:00:00Z",
                "2024-01-01T01:00:00+01:00",
            );
            completed.send(result.map_err(|e| e.message)).unwrap();
        });
        let (headers, body) = received.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(headers.starts_with("POST /api/v1/filters HTTP/1.1"));
        assert_eq!(
            String::from_utf8(body).unwrap(),
            r#"{"stream_name":"logs","filter_name":"a\u003c\u0026","user_id":"u","query":{"filter_type":"sql","filter_query":"select * from logs LIMIT 500"},"time_filter":{"from":"2024-01-01T00:00:00Z","to":"2024-01-01T00:00:00Z"}}"#
        );
        let result = completion.recv_timeout(Duration::from_millis(500));
        let _ = release.send(());
        server.join().unwrap();
        worker.join().unwrap();
        assert_eq!(result.unwrap(), Ok(()));
    }
}
