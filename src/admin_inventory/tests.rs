use super::*;
use crate::{
    config::{ProfileRecord, ProfileStore},
    parser::{FlagValue, ParsedFlag},
};
use std::{
    cell::Cell,
    collections::BTreeMap,
    io::{Read, Write},
    net::TcpListener,
    rc::Rc,
    sync::{Arc, Mutex},
    thread,
    time::Duration,
};

// Expected errors were produced by go1.25.4 json.Unmarshal with the cmd types.
#[test]
fn users_follow_go_slice_and_field_semantics() {
    let ok = |body: &str| wire::user_ids(body.as_bytes()).unwrap();
    let err = |body: &str| wire::user_ids(body.as_bytes()).unwrap_err();
    assert!(ok("null").is_empty());
    assert_eq!(ok(r#"[null,{"id":"a"}]"#), ["", "a"]);
    assert_eq!(ok(r#"[{"id":"a","ID":null}]"#), ["a"]);
    assert_eq!(ok(r#"[{"id":"a","Id":"b"}]"#), ["b"]);
    assert_eq!(ok(r#"[{"ıd":"a"}]"#), [""]);
    assert_eq!(ok(r#"[{"id":"a"},{"id":"a","x":[1]}]"#), ["a", "a"]);
    for (body, expected) in [
        (
            "{}",
            "json: cannot unmarshal object into Go value of type []cmd.UserData",
        ),
        (
            r#""x""#,
            "json: cannot unmarshal string into Go value of type []cmd.UserData",
        ),
        (
            "[1]",
            "json: cannot unmarshal number into Go value of type cmd.UserData",
        ),
        (
            "[[1]]",
            "json: cannot unmarshal array into Go value of type cmd.UserData",
        ),
        (
            r#"[{"id":1}]"#,
            "json: cannot unmarshal number into Go struct field UserData.id of type string",
        ),
        (
            r#"[{"id":{"x":1}}]"#,
            "json: cannot unmarshal object into Go struct field UserData.id of type string",
        ),
        (
            r#"[{"method":true,"id":"a"}]"#,
            "json: cannot unmarshal bool into Go struct field UserData.method of type string",
        ),
        (
            r#"[{"id":18446744073709551616}]"#,
            "json: cannot unmarshal number into Go struct field UserData.id of type string",
        ),
        ("[", "unexpected end of JSON input"),
    ] {
        assert_eq!(err(body), expected, "{body}");
    }
}

#[test]
fn role_names_are_raw_map_keys() {
    assert!(wire::role_names(b"null").unwrap().is_empty());
    assert_eq!(
        wire::role_names(br#"{"a":1,"b":null,"a":{}}"#).unwrap(),
        ["a", "b"]
    );
    assert_eq!(
        wire::role_names(b"[]").unwrap_err(),
        "json: cannot unmarshal array into Go value of type map[string]json.RawMessage"
    );
    assert_eq!(
        wire::role_names(b"7").unwrap_err(),
        "json: cannot unmarshal number into Go value of type map[string]json.RawMessage"
    );
}

#[test]
fn role_actions_preserve_nil_reuse_and_resource_merging() {
    let encode = |body: &str| {
        let actions = wire::role_actions(body.as_bytes()).unwrap();
        serde_json::to_string(&actions).unwrap()
    };
    for body in ["null", "{}", r#"{"actions":null}"#] {
        assert_eq!(encode(body), "null", "{body}");
    }
    assert_eq!(encode(r#"{"actions":[]}"#), "[]");
    assert_eq!(
        encode(
            r#"{"actions":[{"privilege":"a","resource":{"stream":"s","dataset":"d","tag":"t","x":1}}]}"#
        ),
        r#"[{"privilege":"a","resource":{"dataset":"d","tag":"t","stream":"s"}}]"#
    );
    assert_eq!(
        encode(
            r#"{"actions":[{"privilege":"a","resource":{"dataset":"d"},"resource":{"tag":"t"}}]}"#
        ),
        r#"[{"privilege":"a","resource":{"dataset":"d","tag":"t"}}]"#
    );
    assert_eq!(
        encode(r#"{"actions":[{"privilege":"a","resource":{"dataset":"d"},"resource":null}]}"#),
        r#"[{"privilege":"a"}]"#
    );
    assert_eq!(
        encode(r#"{"actions":[{"privilege":"a","resource":{}}]}"#),
        r#"[{"privilege":"a","resource":{}}]"#
    );
    // A shorter duplicate keeps storage that a longer duplicate re-exposes.
    assert_eq!(
        encode(
            r#"{"actions":[{"privilege":"a"},{"privilege":"b","resource":{"dataset":"x"}}],"actions":[{"privilege":null}],"actions":[{},{}]}"#
        ),
        r#"[{"privilege":"a"},{"privilege":"b","resource":{"dataset":"x"}}]"#
    );
    for body in [
        r#"{"actions":[{"privilege":"a"}],"actions":[],"actions":[{}]}"#,
        r#"{"actions":[{"privilege":"a"}],"actions":null,"actions":[{}]}"#,
    ] {
        assert_eq!(encode(body), r#"[{"privilege":""}]"#, "{body}");
    }
    assert_eq!(
        encode(r#"{"actions":[null,{"privilege":"<&>"}]}"#),
        r#"[{"privilege":""},{"privilege":"<&>"}]"#
    );
    assert_eq!(
        encode(
            r#"{"actions":[{"privilege":"a","resource":{"dataset":"d"}}],"actions":[{"resource":{"tag":"t"}}]}"#
        ),
        r#"[{"privilege":"a","resource":{"dataset":"d","tag":"t"}}]"#
    );
    for (body, expected) in [
        (
            "[]",
            "json: cannot unmarshal array into Go value of type struct { Actions []cmd.RoleData \"json:\\\"actions\\\"\" }",
        ),
        (
            r#"{"actions":"x"}"#,
            "json: cannot unmarshal string into Go struct field .actions of type []cmd.RoleData",
        ),
        (
            r#"{"actions":["x"]}"#,
            "json: cannot unmarshal string into Go struct field .actions of type cmd.RoleData",
        ),
        (
            r#"{"actions":[1]}"#,
            "json: cannot unmarshal number into Go struct field .actions of type cmd.RoleData",
        ),
        (
            r#"{"actions":[{"privilege":1},{"privilege":true}]}"#,
            "json: cannot unmarshal number into Go struct field RoleData.actions.privilege of type string",
        ),
        (
            r#"{"actions":[{"privilege":"a","resource":"x"}]}"#,
            "json: cannot unmarshal string into Go struct field RoleData.actions.resource of type cmd.RoleResource",
        ),
        (
            r#"{"actions":[{"privilege":"a","resource":{"dataset":1}}]}"#,
            "json: cannot unmarshal number into Go struct field RoleResource.actions.resource.dataset of type string",
        ),
    ] {
        assert_eq!(
            wire::role_actions(body.as_bytes()).unwrap_err(),
            expected,
            "{body}"
        );
    }
}

#[test]
fn user_roles_flatten_maps_with_go_reset_and_replacement() {
    let ok = |body: &str| wire::user_role_names(body.as_bytes()).unwrap();
    for body in ["null", r#"{"roles":null}"#, r#"{"group_roles":{"g":null}}"#] {
        assert!(ok(body).is_empty(), "{body}");
    }
    assert_eq!(ok(r#"{"roles":{"a":null}}"#), ["a"]);
    assert_eq!(
        ok(r#"{"roles":{"a":{}},"roles":{"b":{}},"ROLES":{"c":{}}}"#),
        ["a", "b", "c"]
    );
    assert_eq!(
        ok(r#"{"roles":{"a":{}},"roles":null,"roles":{"c":{}}}"#),
        ["c"]
    );
    assert_eq!(
        ok(r#"{"roles":{"a":{"actions":[{"privilege":"x"}]},"a":{}}}"#),
        ["a"]
    );
    assert_eq!(
        ok(r#"{"group_roles":{"g":{"r":{}}},"group_roles":{"g":{"s":{}}}}"#),
        ["s"]
    );
    // Direct and group assignments keep their multiplicity.
    assert_eq!(
        ok(
            r#"{"group_roles":{"g":{"admin":{}},"h":{"admin":{},"reader":{}}},"roles":{"admin":{}}}"#
        ),
        ["admin", "admin", "admin", "reader"]
    );
    for (body, expected) in [
        (
            "[]",
            "json: cannot unmarshal array into Go value of type cmd.UserRolesResponse",
        ),
        (
            r#"{"roles":[]}"#,
            "json: cannot unmarshal array into Go struct field UserRolesResponse.roles of type map[string]cmd.UserServerRole",
        ),
        (
            r#"{"roles":{"a":"x"}}"#,
            "json: cannot unmarshal string into Go struct field UserRolesResponse.roles of type cmd.UserServerRole",
        ),
        (
            r#"{"roles":{"a":1,"b":true}}"#,
            "json: cannot unmarshal number into Go struct field UserRolesResponse.roles of type cmd.UserServerRole",
        ),
        (
            r#"{"roles":{"a":{"actions":"x"}}}"#,
            "json: cannot unmarshal string into Go struct field UserServerRole.roles.actions of type []cmd.UserRoleAction",
        ),
        (
            r#"{"roles":{"a":{"actions":[{"privilege":1}]}}}"#,
            "json: cannot unmarshal number into Go struct field UserRoleAction.roles.actions.privilege of type string",
        ),
        (
            r#"{"roles":{"a":{"actions":[{"resource":7}]}}}"#,
            "json: cannot unmarshal number into Go struct field UserRoleAction.roles.actions.resource of type cmd.RoleResource",
        ),
        (
            r#"{"group_roles":{"g":"x"}}"#,
            "json: cannot unmarshal string into Go struct field UserRolesResponse.group_roles of type map[string]cmd.UserServerRole",
        ),
        (
            r#"{"group_roles":{"g":{"r":"x"}}}"#,
            "json: cannot unmarshal string into Go struct field UserRolesResponse.group_roles of type cmd.UserServerRole",
        ),
        (
            r#"{"group_roles":{"g":{"r":{"actions":[{"privilege":1}]}}}}"#,
            "json: cannot unmarshal number into Go struct field UserRoleAction.group_roles.actions.privilege of type string",
        ),
    ] {
        assert_eq!(
            wire::user_role_names(body.as_bytes()).unwrap_err(),
            expected,
            "{body}"
        );
    }
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}

#[test]
fn indexed_results_ignore_completion_order_and_start_every_read() {
    let in_flight = Rc::new(Cell::new(0));
    let peak = Rc::new(Cell::new(0));
    let results = runtime().block_on(indexed(40, |index| {
        let (in_flight, peak) = (in_flight.clone(), peak.clone());
        async move {
            in_flight.set(in_flight.get() + 1);
            peak.set(peak.get().max(in_flight.get()));
            tokio::time::sleep(Duration::from_millis((40 - index as u64) % 7)).await;
            in_flight.set(in_flight.get() - 1);
            index * 10
        }
    }));
    assert_eq!(results, (0..40).map(|i| i * 10).collect::<Vec<_>>());
    assert_eq!(peak.get(), 40);
    assert!(runtime().block_on(indexed(0, |_| async { 0 })).is_empty());
}

/// A loopback fixture answering by request target; it records each target.
struct Fixture {
    url: String,
    requests: Arc<Mutex<Vec<String>>>,
}
type Route = (&'static str, &'static str, &'static str, u64);
fn fixture(routes: &'static [Route]) -> Fixture {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let requests = Arc::new(Mutex::new(Vec::new()));
    let ledger = requests.clone();
    thread::spawn(move || {
        for socket in listener.incoming() {
            let Ok(mut socket) = socket else { break };
            let ledger = ledger.clone();
            thread::spawn(move || {
                let mut head = Vec::new();
                let mut byte = [0];
                while !head.ends_with(b"\r\n\r\n") {
                    if socket.read_exact(&mut byte).is_err() {
                        return;
                    }
                    head.push(byte[0]);
                }
                let head = String::from_utf8_lossy(&head).into_owned();
                let target = head.split(' ').nth(1).unwrap_or_default().to_owned();
                ledger.lock().unwrap().push(target.clone());
                let (status, body, delay) = routes
                    .iter()
                    .find(|(path, ..)| *path == target)
                    .map(|(_, status, body, delay)| (*status, *body, *delay))
                    .unwrap_or(("404 Not Found", "missing", 0));
                thread::sleep(Duration::from_millis(delay));
                let _ = write!(
                    socket,
                    "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            });
        }
    });
    Fixture { url, requests }
}
impl Fixture {
    fn run(&self, path: &str, output: &str) -> Result<Output, CliError> {
        let client = ApiClient::new(
            ProfileRecord {
                url: self.url.clone(),
                username: "u".into(),
                password: "p".into(),
                ..Default::default()
            },
            ProfileStore::new("/nonexistent/admin-inventory-test"),
        )
        .unwrap();
        let mut flags = BTreeMap::new();
        flags.insert(
            "output".to_owned(),
            ParsedFlag {
                value: FlagValue::Text(output.into()),
                explicit: !output.is_empty(),
            },
        );
        let parsed = ParsedInvocation {
            command_path: path.into(),
            positionals: Vec::new(),
            flags,
        };
        runtime().block_on(execute(&parsed, &client, &Terminal::PLAIN))
    }
    fn requests(&self) -> Vec<String> {
        let mut requests = self.requests.lock().unwrap().clone();
        requests.sort();
        requests
    }
}

static USERS: &[Route] = &[
    (
        "/api/v1/user",
        "200 OK",
        r#"[{"id":"alice","method":"native"},{"id":"bob"}]"#,
        0,
    ),
    (
        "/api/v1/user/alice/role",
        "200 OK",
        r#"{"roles":{"admin":{}},"group_roles":{"g":{"reader":{"actions":[]}}}}"#,
        150,
    ),
    ("/api/v1/user/bob/role", "403 Forbidden", " denied\n", 0),
];

#[test]
fn user_text_renders_partial_rows_then_fails() {
    let server = fixture(USERS);
    let error = server.run("pb user list", "").unwrap_err();
    assert_eq!(
        error.stdout,
        format!(
            "\nUSER     ROLES\n{}\n\
             alice ... admin, reader\n\
             bob ..... error: fetch user roles failed: 403 Forbidden: denied\n\n",
            "─".repeat(14)
        )
    );
    assert_eq!(error.code, ErrorCode::CommandFailed);
    assert_eq!(error.http_status, 0);
    assert_eq!(
        error.message,
        "failed to fetch roles for 1 user(s): Error fetching roles for bob: fetch user roles failed: 403 Forbidden: denied"
    );
    assert_eq!(error.public_message, error.message);
    assert_eq!(
        server.requests(),
        [
            "/api/v1/user",
            "/api/v1/user/alice/role",
            "/api/v1/user/bob/role"
        ]
    );
}

#[test]
fn user_json_is_atomic_and_output_is_validated_after_details() {
    let server = fixture(USERS);
    let error = server.run("pb user list", "JSON").unwrap_err();
    assert!(error.stdout.is_empty());
    assert!(
        error
            .message
            .starts_with("failed to fetch roles for 1 user(s): ")
    );

    let server = fixture(USERS);
    let error = server.run("pb user list", "yaml").unwrap_err();
    assert_eq!(error.code, ErrorCode::InvalidInput);
    assert_eq!(
        error.message,
        "unsupported output format \"yaml\" (expected text or json)"
    );
    assert_eq!(server.requests().len(), 3);

    static OK: &[Route] = &[
        ("/api/v1/user", "200 OK", r#"[{"id":"b<&>"},{"id":"a"}]"#, 0),
        (
            "/api/v1/user/b%3C&%3E/role",
            "200 OK",
            r#"{"roles":{"x":{}}}"#,
            100,
        ),
        ("/api/v1/user/a/role", "200 OK", "{}", 0),
    ];
    let output = fixture(OK).run("pb user list", " json ").unwrap();
    assert!(output.json);
    assert_eq!(
        output.stdout,
        "[\n  {\n    \"id\": \"b<&>\",\n    \"roles\": [\n      \"x\"\n    ]\n  },\n  {\n    \"id\": \"a\",\n    \"roles\": []\n  }\n]\n"
    );
    static EMPTY: &[Route] = &[("/api/v1/user", "200 OK", "null", 0)];
    let server = fixture(EMPTY);
    assert_eq!(server.run("pb user list", "json").unwrap().stdout, "[]\n");
    assert_eq!(
        server.run("pb user list", "").unwrap().stdout,
        format!("\nUSER    ROLES\n{}\n\n", "─".repeat(13))
    );
}

#[test]
fn listing_failures_keep_typed_http_and_decode_errors() {
    static DENIED: &[Route] = &[
        ("/api/v1/user", "403 Forbidden", "no\n", 0),
        ("/api/v1/roles", "200 OK", "[]", 0),
    ];
    let server = fixture(DENIED);
    let error = server.run("pb user list", "").unwrap_err();
    assert_eq!(error.code, ErrorCode::PermissionDenied);
    assert_eq!(error.http_status, 403);
    assert_eq!(
        error.message,
        "request failed\nstatus code: 403 Forbidden\nresponse: no\n"
    );
    assert_eq!(error.public_message, "request failed: 403 Forbidden");
    let error = server.run("pb role list", "").unwrap_err();
    assert_eq!(error.code, ErrorCode::CommandFailed);
    assert_eq!(
        error.message,
        "json: cannot unmarshal array into Go value of type map[string]json.RawMessage"
    );
}

static ROLES: &[Route] = &[
    (
        "/api/v1/roles",
        "200 OK",
        r#"{"admin":{},"reader":null,"broken":1}"#,
        0,
    ),
    (
        "/api/v1/role/admin",
        "200 OK",
        r#"{"actions":[{"privilege":"admin"}]}"#,
        120,
    ),
    (
        "/api/v1/role/reader",
        "200 OK",
        r#"{"actions":[{"privilege":"reader","resource":{"stream":"legacy","dataset":"logs"}},{"privilege":" ","resource":{"tag":"t"}}]}"#,
        0,
    ),
    (
        "/api/v1/role/broken",
        "500 Internal Server Error",
        "boom",
        0,
    ),
];

#[test]
fn role_text_renders_partial_rows_then_fails() {
    let server = fixture(ROLES);
    let error = server.run("pb role list", "text").unwrap_err();
    let expected = [
        String::new(),
        "ROLE       PRIVILEGE     DATASET".into(),
        "─".repeat(32),
        "admin .... admin ....... -".into(),
        "reader ... reader ...... logs".into(),
        format!("{}- ........... -", " ".repeat(11)),
        format!(
            "broken ... - ........... error: request failed{}",
            " ".repeat(17)
        ),
        "status code: 500 Internal Server Error".into(),
        format!("response: boom{}", " ".repeat(24)),
        String::new(),
        String::new(),
    ]
    .join("\n");
    assert_eq!(error.stdout, expected);
    assert_eq!(error.code, ErrorCode::CommandFailed);
    assert_eq!(
        error.message,
        "failed to fetch details for 1 role(s): Error fetching role data for broken: request failed\nstatus code: 500 Internal Server Error\nresponse: boom"
    );
}

#[test]
fn role_output_is_validated_before_details_and_json_sorts_keys() {
    let server = fixture(ROLES);
    let error = server.run("pb role list", "xml").unwrap_err();
    assert_eq!(error.code, ErrorCode::InvalidInput);
    assert_eq!(server.requests(), ["/api/v1/roles"]);
    let error = server.run("pb role list", "json").unwrap_err();
    assert!(error.stdout.is_empty());

    static OK: &[Route] = &[
        (
            "/api/v1/roles",
            "200 OK",
            r#"{"reader":{},"admin":{},"empty":{}}"#,
            0,
        ),
        ("/api/v1/role/reader", "200 OK", r#"{"actions":null}"#, 0),
        (
            "/api/v1/role/admin",
            "200 OK",
            r#"{"actions":[{"privilege":"admin","resource":{"stream":"s"}}]}"#,
            0,
        ),
        ("/api/v1/role/empty", "200 OK", r#"{"actions":[]}"#, 0),
    ];
    let output = fixture(OK).run("pb role list", "json").unwrap();
    assert_eq!(
        output.stdout,
        "{\n  \"admin\": [\n    {\n      \"privilege\": \"admin\",\n      \"resource\": {\n        \"stream\": \"s\"\n      }\n    }\n  ],\n  \"empty\": [],\n  \"reader\": null\n}\n"
    );
    static NONE: &[Route] = &[("/api/v1/roles", "200 OK", "{}", 0)];
    assert_eq!(
        fixture(NONE).run("pb role list", "json").unwrap().stdout,
        "{}\n"
    );
}

#[test]
fn colored_tables_style_each_cell() {
    let t = Terminal {
        profile: style::Profile::Ansi,
        dark: true,
    };
    let text = user_table(
        &["a".into(), "b".into()],
        &[Ok(Vec::new()), Ok(vec!["x".into()])],
        &t,
    );
    assert_eq!(
        String::from_utf8(text).unwrap(),
        "\n\x1b[1;94mUSER    \x1b[0m\x1b[1;94mROLES\x1b[0m\n\
         \x1b[90m────────\x1b[0m\x1b[90m─────\x1b[0m\n\
         \x1b[37ma\x1b[0m \x1b[90m......\x1b[0m \x1b[37m-\x1b[0m\n\
         \x1b[37mb\x1b[0m \x1b[90m......\x1b[0m \x1b[37mx\x1b[0m\n\n"
    );
}

#[test]
fn long_cells_truncate_at_go_limits() {
    let user = "u".repeat(70);
    let text = user_table(&[user], &[Ok(vec!["r".into()])], &Terminal::PLAIN);
    let text = String::from_utf8(text).unwrap();
    let row = text.lines().nth(3).unwrap();
    assert_eq!(row, format!("{}… ... r", "u".repeat(63)));
    let role = "r".repeat(50);
    let dataset = "d".repeat(60);
    let actions = Some(vec![Action {
        privilege: "p".into(),
        resource: Some(wire::Resource {
            dataset: dataset.clone(),
            ..Default::default()
        }),
    }]);
    let text = role_table(&[role], &[Ok(actions)], &Terminal::PLAIN);
    let text = String::from_utf8(text).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines[2].chars().count(), 47 + 14 + 48);
    assert_eq!(
        lines[3],
        format!("{}… ... p ........... {}…", "r".repeat(41), "d".repeat(47))
    );
}

#[test]
fn json_write_failures_are_reported_but_text_failures_are_ignored() {
    struct Broken;
    impl Write for Broken {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("closed"))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let text = Output {
        stdout: "x".into(),
        json: false,
    };
    assert!(text.write(&mut Broken).is_ok());
    let json = Output {
        stdout: "{}\n".into(),
        json: true,
    };
    assert_eq!(
        json.write(&mut Broken).unwrap_err().message,
        "failed to encode JSON output: closed"
    );
}
