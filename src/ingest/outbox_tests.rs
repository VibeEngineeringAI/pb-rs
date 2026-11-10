use super::fuzz::{self, Rng};
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

fn batch(rows: &[EventRow<'_>]) -> OutboxRows {
    let mut builder = RowsBuilder::with_capacity(rows.len(), 0);
    for row in rows {
        builder.push(row);
    }
    assert_eq!(builder.encoded_len(), builder.json.len() + 1);
    builder.finish()
}

/// The former outbox, one owned JSON text per row, kept as the oracle for
/// request sizes, request bodies and the order in which an upload splits.
mod former {
    use super::super::{EventRow, MAX_REQUEST};
    use serde_json::value::RawValue;

    pub(super) fn row(row: &EventRow<'_>) -> Box<RawValue> {
        serde_json::value::to_raw_value(row).unwrap()
    }

    pub(super) fn encoded_rows_len(rows: &[Box<RawValue>]) -> usize {
        rows.iter()
            .fold(2 + rows.len().saturating_sub(1), |len, row| {
                len.saturating_add(row.get().len())
            })
    }

    pub(super) fn encode_rows(rows: &[Box<RawValue>]) -> Vec<u8> {
        let mut body = Vec::with_capacity(encoded_rows_len(rows));
        body.push(b'[');
        for (index, row) in rows.iter().enumerate() {
            if index != 0 {
                body.push(b',');
            }
            body.extend_from_slice(row.get().as_bytes());
        }
        body.push(b']');
        body
    }

    /// The former upload loop with the destination replaced by `status`.
    /// Returns every request body in order and whether the upload succeeded.
    pub(super) fn upload(
        rows: &[Box<RawValue>],
        mut status: impl FnMut(&[u8]) -> u16,
    ) -> (Vec<Vec<u8>>, bool) {
        let mut requests = Vec::new();
        let mut pieces = vec![rows];
        while let Some(piece) = pieces.pop() {
            if encoded_rows_len(piece) > MAX_REQUEST && piece.len() > 1 {
                let (left, right) = piece.split_at(piece.len() / 2);
                pieces.extend([right, left]);
                continue;
            }
            let body = encode_rows(piece);
            let status = status(&body);
            requests.push(body);
            if status == 413 && piece.len() > 1 {
                let (left, right) = piece.split_at(piece.len() / 2);
                pieces.extend([right, left]);
                continue;
            }
            if !(200..300).contains(&status) {
                return (requests, false);
            }
        }
        (requests, true)
    }
}

#[test]
fn cached_rows_match_legacy_wire_bytes_lengths_and_event_count() {
    let payloads = [
        "{}",
        r#"{"b":3,"a":2,"large":123456789012345678901234567890,"decimal":0.1000000000000000055511151231257827}"#,
        "{\"message\":\"\\\"\\\\\\t\\u0001é😀\"}",
    ];
    let events: Vec<EventRow<'_>> = payloads
        .into_iter()
        .zip(["event", "event", "complete"])
        .map(|(payload, kind)| event(payload, kind))
        .collect();
    let legacy: Vec<Value> = events.iter().map(legacy_row).collect();
    let former: Vec<_> = events.iter().map(former::row).collect();
    let rows = batch(&events);
    for range in [0..0, 0..1, 0..2, 0..3, 1..3, 1..2, 2..3, 1..1, 3..3] {
        let expected = serde_json::to_vec(&legacy[range.clone()]).unwrap();
        assert_eq!(former::encode_rows(&former[range.clone()]), expected);
        // A piece of the whole batch, as sent after a request is split.
        assert_eq!(rows.body(&range), expected);
        assert_eq!(rows.encoded_len(&range), expected.len());
        // The same rows as a batch of their own, as checkpointed and sent.
        let piece = batch(&events[range]);
        assert_eq!(piece.body(&piece.whole()), expected);
        assert_eq!(serde_json::to_vec(&piece).unwrap(), expected);
        assert_eq!(piece.encoded_len(&piece.whole()), expected.len());
    }
    assert_eq!(rows.events, 2);
}

#[test]
fn seeded_rows_match_the_derived_serialization() {
    let mut rng = Rng::new(21);
    let mut rows = Vec::new();
    for case in 0..20_000 {
        let mut field = |pieces| {
            let pieces = rng.below(pieces);
            fuzz::text(&mut rng, pieces)
        };
        let strings = [
            field(4),
            field(4),
            field(6),
            field(2),
            field(8),
            field(3),
            field(4),
            field(4),
            field(if case % 500 == 0 { 3_000 } else { 30 }),
        ];
        let row = EventRow {
            pb_session: &strings[0],
            pb_collector: &strings[1],
            pb_seq: rng.next() >> rng.below(64),
            pb_event_id: &strings[2],
            pb_kind: if rng.chance(90) { "event" } else { &strings[3] },
            pb_source: &strings[4],
            pb_generation: &strings[5],
            pb_source_seq: rng.next() >> rng.below(64),
            pb_host: &strings[6],
            pb_collected_at: &strings[7],
            pb_payload: &strings[8],
        };
        let expected = former::row(&row);
        let mut actual = Vec::new();
        row.encode(&mut actual);
        assert!(actual == expected.get().as_bytes(), "case {case}");
        if case % 40 == 0 {
            rows.push(expected);
        }
        if rows.len() == 7 {
            // Whole batches of the same rows: one document, former bytes.
            let document = former::encode_rows(&rows);
            let decoded: OutboxRows = serde_json::from_slice(&document).unwrap();
            assert_eq!(decoded.body(&decoded.whole()), document);
            assert_eq!(serde_json::to_vec(&decoded).unwrap(), document);
            assert_eq!(
                decoded.encoded_len(&(2..5)),
                former::encoded_rows_len(&rows[2..5])
            );
            assert_eq!(decoded.body(&(2..5)), former::encode_rows(&rows[2..5]));
            rows.clear();
        }
    }
}

#[test]
fn legacy_rows_normalize_and_preserve_unknown_fields_and_number_lexemes() {
    let saved = r#"[
        {"unknown":{"z":1,"z":2},"pb_kind":"complete","pb_kind":"event","pb_seq":1e+20,"pb_payload":"{}"},
        {"pb_payload":"{}","pb_seq":-0,"pb_kind":"complete","extra":0.123456789012345678901234567890},
        null, [], true, "event", 123456789012345678901234567890
    ]"#;
    let legacy: Vec<Value> = serde_json::from_str(saved).unwrap();
    let rows: OutboxRows = serde_json::from_str(saved).unwrap();
    let expected = serde_json::to_vec(&legacy).unwrap();
    assert_eq!(rows.len(), legacy.len());
    assert_eq!(rows.body(&rows.whole()), expected);
    assert_eq!(serde_json::to_vec(&rows).unwrap(), expected);
    assert_eq!(rows.encoded_len(&rows.whole()), expected.len());
    for index in 0..legacy.len() {
        let expected = serde_json::to_vec(&legacy[index..=index]).unwrap();
        assert_eq!(rows.body(&(index..index + 1)), expected);
        assert_eq!(rows.encoded_len(&(index..index + 1)), expected.len());
    }
    assert_eq!(rows.events, 1);
}

#[test]
fn legacy_row_decode_retains_recursion_limit_and_syntax_errors() {
    for document in [
        format!("[{}0{}]", "[".repeat(130), "]".repeat(130)),
        r#"[{"pb_payload":"\uD800"}]"#.into(),
        r#"[{"pb_seq":1e}]"#.into(),
        r#"[{"pb_kind":"event",}]"#.into(),
        r#"[{"pb_kind":"event"},"#.into(),
        r#"{"pb_kind":"event"}"#.into(),
        "null".into(),
        "\"rows\"".into(),
        "7".into(),
    ] {
        let before = serde_json::from_str::<Vec<Value>>(&document)
            .err()
            .unwrap()
            .to_string();
        let after = serde_json::from_str::<OutboxRows>(&document)
            .err()
            .unwrap()
            .to_string();
        assert_eq!(after, before);
    }
}

#[test]
fn whole_batch_request_shares_the_checkpointed_buffer() {
    let events = ["{}", "{\"n\":1}"].map(|payload| event(payload, "event"));
    let rows = batch(&events);
    assert_eq!(rows.body(&rows.whole()).as_ptr(), rows.json.as_ptr());
    assert_ne!(rows.body(&(0..1)).as_ptr(), rows.json.as_ptr());
    let empty = batch(&[]);
    assert_eq!(empty.len(), 0);
    assert_eq!(empty.body(&empty.whole()), "[]");
    assert_eq!(empty.encoded_len(&empty.whole()), 2);
    assert_eq!(serde_json::to_string(&empty).unwrap(), "[]");
}

fn checkpoint(pending: Option<Outbox>) -> Checkpoint {
    let files = serde_json::json!({
        "initialized": true, "revision": 3, "next_generation": 2,
        "files": {"/tmp/é\"x\\.log": {
            "identity": "1:2", "generation_number": 1, "generation": "file-00",
            "offset": 10, "sequence": 2, "discard_fragment": false,
            "anchor": [1, 2], "prefix": [3], "missing_scans": 0
        }}
    });
    Checkpoint {
        version: 1,
        binding: Binding {
            endpoint: "http://fixture".into(),
            tenant_id: "tenant \"é\"".into(),
            workspace_id: String::new(),
            dataset: "logs".into(),
            session: "session\n".into(),
            directory: "/tmp".into(),
            includes: vec!["*.log".into(), "\"rows\":null".into()],
            excludes: vec![],
            format: "jsonl".into(),
        },
        collector: "collector".into(),
        host: "host\tname".into(),
        next_sequence: 7,
        files: serde_json::from_value(files).unwrap(),
        complete: pending.as_ref().is_some_and(|pending| pending.complete),
        pending,
    }
}

#[test]
fn checkpoint_document_is_its_derived_serialization() {
    let directory = std::env::temp_dir().join(format!("pb-outbox-test-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir(&directory).unwrap();
    let path = directory.join("state.json");
    let state = StateFile::lock(&path).unwrap();
    let mut rng = Rng::new(22);
    let payloads: Vec<String> = (0..40).map(|_| fuzz::text(&mut rng, 12)).collect();
    let events: Vec<EventRow<'_>> = payloads
        .iter()
        .map(|payload| event(payload, "event"))
        .collect();
    let legacy = r#"[{"pb_kind":"event","pb_seq":1e+20,"z":{"a":1,"a":2}},null,[],-0,"event"]"#;
    let pending = |rows, complete, skipped_lines| Outbox {
        rows,
        next_files: serde_json::from_str(r#"{"initialized":true,"revision":9}"#).unwrap(),
        next_sequence: 47,
        complete,
        skipped_lines,
    };
    for checkpoint in [
        checkpoint(None),
        checkpoint(Some(pending(batch(&events), false, 0))),
        checkpoint(Some(pending(batch(&events[..1]), true, u64::MAX))),
        checkpoint(Some(pending(batch(&[]), false, 3))),
        checkpoint(Some(pending(
            serde_json::from_str(legacy).unwrap(),
            false,
            1,
        ))),
    ] {
        // The derived serialization parses and re-encodes pending rows; the
        // document copies them. The bytes are the same.
        let derived = serde_json::to_vec(&checkpoint).unwrap();
        let mut document = Vec::new();
        checkpoint.write_json(&mut document).unwrap();
        assert!(document == derived);
        // Saved either way it is that document, and loads back to it.
        state.save_document(&checkpoint).unwrap();
        assert!(std::fs::read(&path).unwrap() == derived);
        state.save(&checkpoint).unwrap();
        assert!(std::fs::read(&path).unwrap() == derived);
        let loaded = state.load::<Checkpoint>().unwrap().unwrap();
        assert!(serde_json::to_vec(&loaded).unwrap() == derived);
        assert_eq!(
            loaded.pending.as_ref().map(|pending| pending.rows.events),
            checkpoint
                .pending
                .as_ref()
                .map(|pending| pending.rows.events)
        );
    }
    // A write failure is reported as one, not as a serialization fault.
    struct Full;
    impl Write for Full {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::ErrorKind::StorageFull.into())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    for pending in [None, Some(pending(batch(&events), false, 0))] {
        let error = checkpoint(pending).write_json(&mut Full).unwrap_err();
        assert_eq!(error.io_error_kind(), Some(std::io::ErrorKind::StorageFull));
    }
    drop(state);
    std::fs::remove_dir_all(directory).unwrap();
}

#[test]
fn serialized_rows_are_checked_json() {
    // The general serde route parses the batch text before copying it, so
    // text which is not one JSON value cannot be serialized through it.
    for (json, starts) in [
        (&b"[{\"pb_kind\":}]"[..], vec![1]),
        (b"[{\"pb_kind\":\"event\"}", vec![1]),
        (b"[{}]]", vec![1]),
        (b"[{\"pb_payload\":\"\xff\"}]", vec![1]),
        (b"", vec![]),
    ] {
        let rows = OutboxRows {
            json: Bytes::copy_from_slice(json),
            starts,
            events: 0,
        };
        assert!(serde_json::to_vec(&rows).is_err());
    }
}

/// Serve `count` ingest requests, answering each with `status(body)`, and
/// return the request bodies in arrival order.
fn destination(
    count: usize,
    status: impl Fn(&[u8]) -> u16 + Send + 'static,
) -> (String, std::thread::JoinHandle<Vec<Vec<u8>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let worker = std::thread::spawn(move || {
        let mut requests = Vec::new();
        for _ in 0..count {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(30)))
                .unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                stream.read_exact(&mut byte).unwrap();
                head.push(byte[0]);
            }
            let head = String::from_utf8(head).unwrap();
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
            let status = status(&body);
            requests.push(body);
            write!(
                stream,
                "HTTP/1.1 {status} Status\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            .unwrap();
        }
        requests
    });
    (endpoint, worker)
}

/// Upload `events` through the real client and compare every request body,
/// in order, with what the former per-row upload loop sends for `status`.
fn check_upload_matches_former(events: &[EventRow<'_>], status: fn(&[u8]) -> u16) -> usize {
    let former: Vec<_> = events.iter().map(former::row).collect();
    let (expected, succeeds) = former::upload(&former, status);
    let rows = batch(events);
    assert_eq!(
        rows.encoded_len(&rows.whole()),
        former::encoded_rows_len(&former)
    );
    let (endpoint, worker) = destination(expected.len(), status);
    let directory = std::env::temp_dir().join(format!("pb-outbox-test-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir(&directory).unwrap();
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
    let result = runtime.block_on(upload(
        &client,
        http::HeaderValue::from_static("logs"),
        &rows,
    ));
    assert_eq!(result.is_ok(), succeeds);
    let requests = worker.join().unwrap();
    assert_eq!(requests.len(), expected.len());
    for (index, (actual, expected)) in requests.iter().zip(&expected).enumerate() {
        assert!(actual == expected, "request {index} differs");
    }
    std::fs::remove_dir_all(directory).unwrap();
    requests.len()
}

fn row_count(body: &[u8]) -> usize {
    serde_json::from_slice::<Vec<Value>>(body).unwrap().len()
}

#[test]
fn rejected_requests_split_in_the_former_order_with_the_former_bodies() {
    let payloads: Vec<String> = (0..13)
        .map(|n| format!("{{\"n\":{n},\"pad\":\"{}\"}}", "\\\"é\t".repeat(n * 7 % 5)))
        .collect();
    let events: Vec<EventRow<'_>> = payloads
        .iter()
        .enumerate()
        .map(|(n, payload)| event(payload, if n == 12 { "complete" } else { "event" }))
        .collect();
    // Every size of batch, against destinations which accept at most three
    // rows, exactly one row, and nothing but a single row they then refuse.
    for count in 0..=events.len() {
        check_upload_matches_former(&events[..count], |body| {
            if row_count(body) > 3 { 413 } else { 200 }
        });
    }
    let requests =
        check_upload_matches_former(&events, |body| if row_count(body) > 1 { 413 } else { 200 });
    assert_eq!(requests, 25);
    // A single row cannot be split: its 413 is the failure, after 13, 6, 3, 1.
    let requests = check_upload_matches_former(&events, |_| 413);
    assert_eq!(requests, 4);
    // A 503 part-way leaves later pieces unsent.
    check_upload_matches_former(&events, |body| match row_count(body) {
        13 | 6 => 413,
        3 => 200,
        _ => 503,
    });
}

#[test]
fn request_byte_budget_splits_before_sending_at_the_former_boundary() {
    // Two rows whose combined body is exactly the budget go as one request;
    // one more byte and they are split without asking the destination.
    let fixed = batch(&[event("", "event"), event("", "event")]);
    let padding = MAX_REQUEST - fixed.encoded_len(&fixed.whole());
    for (extra, expected_requests) in [(0, 1), (1, 2)] {
        let first = "a".repeat(padding / 2);
        let second = "b".repeat(padding - padding / 2 + extra);
        let events = [event(&first, "event"), event(&second, "event")];
        let rows = batch(&events);
        assert_eq!(rows.encoded_len(&rows.whole()), MAX_REQUEST + extra);
        assert_eq!(
            check_upload_matches_former(&events, |_| 200),
            expected_requests
        );
    }
    // Four rows over the budget twice: halved, then each half sent whole.
    let quarter = "c".repeat(MAX_REQUEST / 3);
    let events = [(); 4].map(|()| event(&quarter, "event"));
    assert!(batch(&events).encoded_len(&(0..4)) < MAX_UPLOAD);
    assert_eq!(check_upload_matches_former(&events, |_| 200), 2);
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
    state.save_document(&checkpoint).unwrap();
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
    state.save_document(&replay).unwrap();
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
