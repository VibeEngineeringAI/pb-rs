use super::*;
use serde_json::Value;
use std::{io::Read, net::TcpListener};

fn event<'a>(payload: &'a str, kind: &'a str) -> EventRow<'a> {
    EventRow {
        pb_session: "session\"\n\u{1}é",
        pb_collector: "collector",
        pb_seq: MAX_SEQUENCE,
        pb_event_id: "collector:file-1:2",
        pb_kind: kind,
        pb_source: "/tmp/é\\\"file.log",
        pb_generation: "file-1",
        pb_source_seq: u64::MAX,
        pb_host: "host\tname",
        pb_collected_at: "2026-10-17T12:13:14+00:00",
        pb_payload: payload,
    }
}

// Keep the former Value-map construction as a byte-order/escaping oracle.
fn legacy_row(row: &EventRow<'_>) -> Value {
    let mut value = serde_json::json!({
        "pb_session": row.pb_session,
        "pb_collector": row.pb_collector,
        "pb_seq": row.pb_seq,
        "pb_event_id": row.pb_event_id,
        "pb_kind": row.pb_kind,
        "pb_source": row.pb_source,
        "pb_generation": row.pb_generation,
        "pb_source_seq": row.pb_source_seq,
        "pb_host": row.pb_host,
        "pb_collected_at": row.pb_collected_at,
    });
    value["pb_payload"] = Value::String(row.pb_payload.into());
    value
}

#[test]
fn cached_rows_match_legacy_wire_bytes_lengths_and_event_count() {
    let payloads = [
        "{}",
        r#"{"b":3,"a":2,"large":123456789012345678901234567890,"decimal":0.1000000000000000055511151231257827}"#,
        "{\"message\":\"\\\"\\\\\\t\\u0001é😀\"}",
    ];
    let mut rows = Vec::new();
    let mut legacy = Vec::new();
    for (payload, kind) in payloads.into_iter().zip(["event", "event", "complete"]) {
        let row = event(payload, kind);
        legacy.push(legacy_row(&row));
        rows.push(OutboxRow::new(&row).unwrap());
    }
    for range in [0..0, 0..1, 0..2, 0..3, 1..3] {
        let expected = serde_json::to_vec(&legacy[range.clone()]).unwrap();
        let piece = &rows[range];
        assert_eq!(encode_rows(piece), expected);
        assert_eq!(serde_json::to_vec(piece).unwrap(), expected);
        assert_eq!(encoded_rows_len(piece), expected.len());
    }
    assert_eq!(rows.iter().filter(|row| row.is_event).count(), 2);
}

#[test]
fn legacy_rows_normalize_and_preserve_unknown_fields_and_number_lexemes() {
    let saved = r#"[
        {"unknown":{"z":1,"z":2},"pb_kind":"complete","pb_kind":"event","pb_seq":1e+20,"pb_payload":"{}"},
        {"pb_payload":"{}","pb_seq":-0,"pb_kind":"complete","extra":0.123456789012345678901234567890},
        null, [], true, "event", 123456789012345678901234567890
    ]"#;
    let legacy: Vec<Value> = serde_json::from_str(saved).unwrap();
    let rows: Vec<OutboxRow> = serde_json::from_str(saved).unwrap();
    let expected = serde_json::to_vec(&legacy).unwrap();
    assert_eq!(encode_rows(&rows), expected);
    assert_eq!(serde_json::to_vec(&rows).unwrap(), expected);
    assert_eq!(encoded_rows_len(&rows), expected.len());
    assert_eq!(rows.iter().filter(|row| row.is_event).count(), 1);
}

#[test]
fn legacy_row_decode_retains_recursion_limit_and_syntax_errors() {
    for document in [
        format!("[{}0{}]", "[".repeat(130), "]".repeat(130)),
        r#"[{"pb_payload":"\uD800"}]"#.into(),
        r#"[{"pb_seq":1e}]"#.into(),
        r#"[{"pb_kind":"event",}]"#.into(),
    ] {
        let before = serde_json::from_str::<Vec<Value>>(&document)
            .err()
            .unwrap()
            .to_string();
        let after = serde_json::from_str::<Vec<OutboxRow>>(&document)
            .err()
            .unwrap()
            .to_string();
        assert_eq!(after, before);
    }
}

#[test]
fn version_one_pending_checkpoint_replays_full_rows_after_partial_failure() {
    let directory = std::env::temp_dir().join(format!("pb-outbox-test-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir(&directory).unwrap();
    let path = directory.join("state.json");
    let rows = ["{\"n\":1e+20}", "{\"n\":-0}", "{\"n\":3}"]
        .map(|payload| legacy_row(&event(payload, "event")));
    let saved = serde_json::json!({
        "version": 1,
        "binding": {
            "endpoint": "http://fixture", "dataset": "logs", "session": "session",
            "directory": "/tmp", "includes": ["*.log"], "excludes": [], "format": "jsonl"
        },
        "collector": "collector", "host": "host", "next_sequence": 1,
        "files": {}, "complete": false,
        "pending": {
            "rows": rows, "next_files": {}, "next_sequence": 4,
            "complete": false, "skipped_lines": 2
        }
    });
    std::fs::write(&path, serde_json::to_vec(&saved).unwrap()).unwrap();
    let state = StateFile::lock(&path).unwrap();
    let checkpoint = state.load::<Checkpoint>().unwrap().unwrap();
    // Saving a version-1 legacy checkpoint keeps rows as ordinary JSON objects.
    state.save(&checkpoint).unwrap();
    let durable_before = std::fs::read(&path).unwrap();
    let disk: Value = serde_json::from_slice(&durable_before).unwrap();
    assert_eq!(disk["pending"]["rows"], saved["pending"]["rows"]);
    let expected = serde_json::to_vec(&rows).unwrap();

    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let worker = std::thread::spawn(move || {
        let mut requests = Vec::new();
        for status in ["413 Too Large", "200 OK", "503 Unavailable", "200 OK"] {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                stream.read_exact(&mut byte).unwrap();
                head.push(byte[0]);
            }
            let head = String::from_utf8(head).unwrap();
            assert!(head.to_ascii_lowercase().contains("x-p-stream: logs\r\n"));
            let length = head
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
                .unwrap();
            let mut body = vec![0; length];
            stream.read_exact(&mut body).unwrap();
            requests.push(body);
            write!(
                stream,
                "HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            .unwrap();
        }
        requests
    });
    let client = ApiClient::new(
        crate::config::ProfileRecord {
            url: endpoint,
            api_key: "key".into(),
            ..Default::default()
        },
        ProfileStore::new(directory.join("config.toml")),
    )
    .unwrap();
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let dataset = http::HeaderValue::from_static("logs");
    let failure = runtime
        .block_on(upload(
            &client,
            dataset.clone(),
            &checkpoint.pending.as_ref().unwrap().rows,
        ))
        .unwrap_err();
    assert!(failure.message.contains("pending batch retained"));
    assert_eq!(std::fs::read(&path).unwrap(), durable_before);
    let mut replay = state.load::<Checkpoint>().unwrap().unwrap();
    runtime
        .block_on(upload(
            &client,
            dataset,
            &replay.pending.as_ref().unwrap().rows,
        ))
        .unwrap();
    // Upload itself never acknowledges the outbox; the caller durably commits it.
    assert_eq!(std::fs::read(&path).unwrap(), durable_before);
    let pending = replay.pending.take().unwrap();
    assert_eq!(pending.skipped_lines, 2);
    replay.files = pending.next_files;
    replay.next_sequence = pending.next_sequence;
    state.save(&replay).unwrap();
    let acknowledged = state.load::<Checkpoint>().unwrap().unwrap();
    assert_eq!(acknowledged.next_sequence, 4);
    assert!(acknowledged.pending.is_none());
    let requests = worker.join().unwrap();
    assert_eq!(
        requests,
        [
            expected.clone(),
            serde_json::to_vec(&rows[..1]).unwrap(),
            serde_json::to_vec(&rows[1..]).unwrap(),
            expected
        ]
    );
    drop(state);
    std::fs::remove_dir_all(directory).unwrap();
}
