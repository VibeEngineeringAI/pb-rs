use super::*;
use arrow_array::{
    DictionaryArray, Int64Array, ListArray, StringArray, types::Float64Type, types::Int32Type,
};
use arrow_ipc::writer::{DictionaryTracker, IpcDataGenerator, IpcWriteContext, IpcWriteOptions};
use arrow_schema::Schema;
use bytes::Bytes;
use futures_util::stream;
use std::sync::atomic::{AtomicUsize, Ordering};

type Item = Result<FlightData, FlightStatus>;

/// grpc-go codes used below.
const OK: u32 = 0;
const CANCELED: u32 = 1;
const INVALID_ARGUMENT: u32 = 3;
const INTERNAL: u32 = 13;
const UNAVAILABLE: u32 = 14;
const UNAUTHENTICATED: u32 = 16;

#[test]
fn dense_union_decodes_from_unaligned_flight_bodies() {
    use arrow_array::{Array, UnionArray};
    use arrow_schema::UnionFields;
    let fields = UnionFields::try_new(
        vec![0, 1],
        vec![
            Field::new("i", DataType::Int64, true),
            Field::new("s", DataType::Utf8, true),
        ],
    )
    .unwrap();
    let union = UnionArray::try_new(
        fields,
        vec![0_i8, 1].into(),
        Some(vec![0_i32, 0].into()),
        vec![
            Arc::new(Int64Array::from(vec![42])),
            Arc::new(StringArray::from(vec!["x"])),
        ],
    )
    .unwrap();
    let schema = Arc::new(Schema::new(vec![Field::new(
        "choice",
        union.data_type().clone(),
        true,
    )]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(union)]).unwrap();
    let encoded = flight_stream(&schema, [&batch]);
    for prefix in 0..64 {
        let mut messages = encoded.clone();
        let original = &messages[1].data_body;
        let mut storage = vec![0; prefix];
        storage.extend_from_slice(original);
        messages[1].data_body = Bytes::from(storage).slice(prefix..);
        let (batches, stop) = decode_all(ok(messages));
        assert_eq!(stop, Stop::Reconnect);
        assert_eq!(batches.len(), 1);
        let mut renderer = BatchRenderer::new(&schema).unwrap();
        let mut out = Vec::new();
        renderer.render(&batches[0], &mut out).unwrap();
        assert_eq!(out, b"{\"choice\":[0,42]}\n{\"choice\":[1,\"x\"]}\n");
    }
}

#[test]
fn truncated_record_and_dictionary_bodies_fail_without_unwinding() {
    for mut messages in [int_stream(&[&[1, 2]]), dictionary_stream(1)] {
        let last = messages[1].data_body.len();
        messages[1].data_body = messages[1].data_body.slice(..last / 2);
        let (batches, stop) = decode_all(ok(messages));
        assert!(batches.is_empty());
        assert_eq!(stop, Stop::Fatal(format!("{UNKNOWN_READ}EOF").into_bytes()));
    }
}

fn block_on<F: std::future::Future>(future: F) -> F::Output {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(future)
}

/// Schema, then each batch's dictionaries and record message. One tracker
/// assigns the IDs in both the schema and the dictionary messages (arrow-flight
/// 60's batches_to_flight_data omits them from the schema).
fn flight_stream<'a>(
    schema: &Schema,
    batches: impl IntoIterator<Item = &'a RecordBatch>,
) -> Vec<FlightData> {
    flight_stream_with(IpcWriteOptions::default(), schema, batches)
}

fn flight_stream_with<'a>(
    options: IpcWriteOptions,
    schema: &Schema,
    batches: impl IntoIterator<Item = &'a RecordBatch>,
) -> Vec<FlightData> {
    let generator = IpcDataGenerator::default();
    let mut tracker = DictionaryTracker::new(false);
    let mut context = IpcWriteContext::default();
    let mut data = vec![
        generator
            .schema_to_bytes_with_dictionary_tracker(schema, &mut tracker, &options)
            .into(),
    ];
    for batch in batches {
        let (dictionaries, encoded) = generator
            .encode(batch, &mut tracker, &options, &mut context)
            .unwrap();
        data.extend(dictionaries.into_iter().map(FlightData::from));
        data.push(encoded.into());
    }
    data
}

fn int_batch(values: &[i64]) -> RecordBatch {
    let schema = Arc::new(Schema::new(vec![Field::new("n", DataType::Int64, false)]));
    RecordBatch::try_new(schema, vec![Arc::new(Int64Array::from(values.to_vec()))]).unwrap()
}

/// Schema message followed by one record batch message per batch.
fn int_stream(batches: &[&[i64]]) -> Vec<FlightData> {
    let batches: Vec<_> = batches.iter().map(|values| int_batch(values)).collect();
    flight_stream(&int_batch(&[]).schema(), &batches)
}

/// Schema, one dictionary per column, then one batch.
fn dictionary_stream(columns: usize) -> Vec<FlightData> {
    let fields: Vec<_> = (0..columns)
        .map(|i| {
            Field::new(
                format!("d{i}"),
                DataType::Dictionary(Box::new(DataType::Int32), Box::new(DataType::Utf8)),
                true,
            )
        })
        .collect();
    let schema = Arc::new(Schema::new(fields));
    let arrays: Vec<ArrayRef> = (0..columns)
        .map(|_| {
            Arc::new(DictionaryArray::<Int32Type>::new(
                vec![1, 0, 1].into(),
                Arc::new(StringArray::from(vec!["a", "b"])),
            )) as ArrayRef
        })
        .collect();
    let batch = RecordBatch::try_new(Arc::clone(&schema), arrays).unwrap();
    flight_stream(&schema, [&batch])
}

fn metadata_only() -> FlightData {
    FlightData {
        app_metadata: Bytes::from_static(b"meta"),
        ..Default::default()
    }
}

fn ok(data: Vec<FlightData>) -> Vec<Item> {
    data.into_iter().map(Ok).collect()
}

fn open_error(items: Vec<Item>) -> String {
    String::from_utf8(open_bytes(items)).unwrap()
}

fn open_bytes(items: Vec<Item>) -> Vec<u8> {
    match block_on(open(stream::iter(items))).and_then(|(_, schema)| initialize(&schema).map(drop))
    {
        Ok(_) => panic!("reader initialization unexpectedly succeeded"),
        Err(message) => message,
    }
}

/// Driver classification plus decoding, without the worker handoff.
fn decode_all(items: Vec<Item>) -> (Vec<RecordBatch>, Stop) {
    block_on(async {
        let (mut stream, schema) = open(stream::iter(items)).await.unwrap();
        let mut decoder = initialize(&schema).unwrap();
        let mut batches = Vec::new();
        loop {
            match receive(&mut stream).await {
                Ok(Some(data)) => match decoder.decode(data) {
                    Ok(Decoded::Batch(batch)) => batches.push(batch),
                    Ok(Decoded::Dictionary) => {}
                    Err(message) => return (batches, Stop::Fatal(message.into_bytes())),
                },
                Ok(None) => return (batches, decoder.end()),
                Err(stop) => return (batches, stop),
            }
        }
    })
}

#[test]
fn reader_initialization_eof_and_every_status_are_fatal() {
    assert_eq!(open_error(vec![]), "EOF");
    for code in [UNAVAILABLE, CANCELED, OK, UNAUTHENTICATED] {
        let status = FlightStatus::new(code, "init");
        assert_eq!(open_bytes(vec![Err(status.clone())]), status.go_error());
    }
}

#[test]
fn only_the_first_metadata_only_message_is_skipped() {
    let mut items = vec![Ok(metadata_only())];
    items.extend(ok(int_stream(&[&[1, 2]])));
    let (batches, stop) = decode_all(items);
    assert_eq!(batches.len(), 1);
    assert_eq!(batches[0].num_rows(), 2);
    assert_eq!(stop, Stop::Reconnect);

    let mut items = vec![Ok(metadata_only()), Ok(metadata_only())];
    items.extend(ok(int_stream(&[&[1]])));
    assert_eq!(
        open_error(items),
        "arrow/flight: could not create flight reader: arrow/ipc: unknown error while reading: \
         runtime error: index out of range [3] with length 0"
    );
}

#[test]
fn schema_read_after_metadata_only_wraps_eof_and_status() {
    assert_eq!(
        open_error(vec![Ok(metadata_only())]),
        "arrow/flight: could not create flight reader: arrow/ipc: could not read message schema: EOF"
    );
    let status = FlightStatus::new(UNAVAILABLE, "gone");
    assert_eq!(
        open_bytes(vec![Ok(metadata_only()), Err(status.clone())]),
        [SCHEMA_READ.as_bytes(), &status.go_error()].concat()
    );
}

#[test]
fn missing_or_short_schema_header_is_an_initialization_failure() {
    let data = int_stream(&[&[1]]);
    assert_eq!(
        open_error(ok(data[1..].to_vec())),
        "arrow/flight: could not create flight reader: arrow/ipc: invalid message type \
         (got=RecordBatch, want=Schema)"
    );
    let short = FlightData {
        data_header: Bytes::from_static(b"ab"),
        ..Default::default()
    };
    assert_eq!(
        open_error(vec![Ok(short)]),
        "arrow/flight: could not create flight reader: arrow/ipc: unknown error while reading: \
         runtime error: index out of range [3] with length 2"
    );
}

/// Go reads the root offset, then `HeaderType()` through the vtable, without
/// verification; each out-of-range access panics with Go's runtime text.
#[test]
fn garbage_headers_reproduce_go_header_type_panics() {
    let cases: [(&[u8], &str); 6] = [
        // The retained p6-corrupt-garbage_header fixture shape.
        (
            b"\xde\xad\xbe\xef\0\0\0\0\0\0\0\0",
            "slice bounds out of range [4022250974:12]",
        ),
        (
            &[10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            "index out of range [3] with length 2",
        ),
        // SOffsetT -100: the vtable lies past the end.
        (
            &[4, 0, 0, 0, 0x9c, 0xff, 0xff, 0xff, 0, 0, 0, 0],
            "slice bounds out of range [104:12]",
        ),
        // A vtable with one byte left.
        (
            &[4, 0, 0, 0, 0xf9, 0xff, 0xff, 0xff, 0, 0, 0, 0],
            "index out of range [1] with length 1",
        ),
        // The header-type slot lies past the end.
        (
            &[4, 0, 0, 0, 0xfc, 0xff, 0xff, 0xff, 8, 0, 0, 0],
            "slice bounds out of range [14:12]",
        ),
        // The header-type byte itself lies exactly at the end.
        (
            &[4, 0, 0, 0, 0xfc, 0xff, 0xff, 0xff, 8, 0, 0, 0, 0, 0, 12, 0],
            "index out of range [0] with length 0",
        ),
    ];
    for (header, panic) in cases {
        assert_eq!(go_header_type(header), Err(panic.to_owned()), "{header:?}");
        let data = FlightData {
            data_header: Bytes::copy_from_slice(header),
            ..Default::default()
        };
        assert_eq!(
            open_error(vec![Ok(data)]),
            format!("{FLIGHT_READER}{UNKNOWN_READ}runtime error: {panic}")
        );
    }
    // A vtable too short for the slot reads as no header, like Go.
    assert_eq!(
        go_header_type(&[4, 0, 0, 0, 0xfc, 0xff, 0xff, 0xff, 4, 0, 0, 0]),
        Ok(0)
    );
    let data = int_stream(&[&[1]]);
    assert_eq!(
        go_header_type(&data[0].data_header),
        Ok(MessageHeader::Schema.0)
    );
    assert_eq!(
        go_header_type(&data[1].data_header),
        Ok(MessageHeader::RecordBatch.0)
    );
}

#[test]
fn established_reads_reconnect_only_for_eof_and_stream_end_codes() {
    for code in [CANCELED, UNAVAILABLE, OK] {
        let mut items = ok(int_stream(&[&[1]]));
        items.push(Err(FlightStatus::new(code, "end")));
        let (batches, stop) = decode_all(items);
        assert_eq!(batches.len(), 1);
        assert_eq!(stop, Stop::Reconnect, "{code:?}");
    }
    for code in [INTERNAL, UNAUTHENTICATED, INVALID_ARGUMENT] {
        let status = FlightStatus::new(code, "fatal");
        let mut items = ok(int_stream(&[&[1]]));
        items.push(Err(status.clone()));
        let (_, stop) = decode_all(items);
        assert_eq!(stop, Stop::Fatal(status.go_error()));
    }
    // A schema followed by EOF is an ordinary empty subscription.
    let (batches, stop) = decode_all(ok(int_stream(&[])));
    assert!(batches.is_empty());
    assert_eq!(stop, Stop::Reconnect);
}

#[test]
fn midstream_schema_and_metadata_only_messages_are_fatal() {
    let mut data = int_stream(&[&[1]]);
    data.push(data[0].clone());
    let (batches, stop) = decode_all(ok(data));
    assert_eq!(batches.len(), 1);
    assert_eq!(
        stop,
        Stop::Fatal("arrow/ipc: invalid message type (got=Schema, want=RecordBatch".into())
    );

    let mut data = int_stream(&[&[1]]);
    data.insert(1, metadata_only());
    let (batches, stop) = decode_all(ok(data));
    assert!(batches.is_empty());
    assert_eq!(
        stop,
        Stop::Fatal(
            "arrow/ipc: unknown error while reading: runtime error: index out of range [3] with length 0"
                .into()
        )
    );
}

#[test]
fn initial_dictionaries_are_required_per_subscription() {
    let (batches, stop) = decode_all(ok(dictionary_stream(2)));
    assert_eq!(batches.len(), 1);
    assert_eq!(batches[0].num_rows(), 3);
    assert_eq!(stop, Stop::Reconnect);

    // EOF before the first expected dictionary ends the subscription normally.
    let data = dictionary_stream(2);
    let (batches, stop) = decode_all(ok(data[..1].to_vec()));
    assert!(batches.is_empty());
    assert_eq!(stop, Stop::Reconnect);

    // A partial dictionary prefix is fatal.
    let (_, stop) = decode_all(ok(data[..2].to_vec()));
    assert_eq!(
        stop,
        Stop::Fatal(
            "arrow/ipc: IPC stream ended without reading the expected (2) dictionaries".into()
        )
    );

    // A record where an initial dictionary is expected is fatal.
    let mut skipped = data.clone();
    skipped.remove(1);
    let (_, stop) = decode_all(ok(skipped));
    assert_eq!(
        stop,
        Stop::Fatal("arrow/ipc: no dictionary type found with id: 3".into())
    );
}

#[test]
fn unknown_header_names_follow_go_enum_fallback() {
    assert_eq!(header_name(MessageHeader::RecordBatch), "RecordBatch");
    assert_eq!(header_name(MessageHeader::NONE), "NONE");
    assert_eq!(header_name(MessageHeader(42)), "MessageType(42)");
}

/// Run established reads through the real handoff and worker thread.
fn session(
    json: bool,
    items: Vec<Item>,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
) -> Result<(), String> {
    std::thread::scope(|scope| {
        let (mut handoff, inbox, acks) = Handoff::new();
        let worker =
            scope.spawn(move || Worker::new(stdout, stderr, "app<1>", json).run(inbox, acks));
        let result = block_on(async {
            let ended = match subscribe(stream::iter(items), &mut handoff).await {
                Ok((stream, decoder)) => pump(stream, decoder, &mut handoff).await,
                Err(exit) => Err(exit),
            };
            match ended {
                Ok(()) => Ok(()),
                Err(Exit::Fatal(message)) => Err(String::from_utf8_lossy(&message).into_owned()),
                Err(Exit::Terminal(Terminal::Panicked(message))) => {
                    Err(format!("panic: {message}"))
                }
                Err(Exit::Terminal(Terminal::Failed(error))) => Err(error.message),
            }
        });
        drop(handoff);
        worker.join().unwrap();
        result
    })
}

#[test]
fn text_and_json_share_stdout_framing_including_empty_batches() {
    for json in [false, true] {
        let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
        let items = ok(int_stream(&[&[1, 2], &[]]));
        assert_eq!(session(json, items, &mut stdout, &mut stderr), Ok(()));
        assert_eq!(stdout, b"{\"n\":1}\n{\"n\":2}\n\n\n");
        // Text clears the status line before each batch; JSON never writes it.
        let expected: &[u8] = if json { b"" } else { b"\r\x1b[K\r\x1b[K" };
        assert_eq!(stderr, expected);
    }
}

#[test]
fn dictionary_decoding_runs_on_the_worker_and_renders_values() {
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    assert_eq!(
        session(true, ok(dictionary_stream(1)), &mut stdout, &mut stderr),
        Ok(())
    );
    assert_eq!(
        stdout,
        b"{\"d0\":\"b\"}\n{\"d0\":\"a\"}\n{\"d0\":\"b\"}\n\n"
    );
}

#[test]
fn worker_decode_errors_are_fatal_after_earlier_output() {
    let mut data = int_stream(&[&[5]]);
    data.push(data[0].clone());
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    assert_eq!(
        session(false, ok(data), &mut stdout, &mut stderr),
        Err("arrow/ipc: invalid message type (got=Schema, want=RecordBatch".into())
    );
    assert_eq!(stdout, b"{\"n\":5}\n\n");
}

#[test]
fn renderer_panic_ends_the_session_without_that_batch() {
    let item = Arc::new(Field::new("item", DataType::Float64, true));
    let list = ListArray::from_iter_primitive::<Float64Type, _, _>([Some([Some(f64::NAN)])]);
    let schema = Arc::new(Schema::new(vec![Field::new(
        "l",
        DataType::List(item),
        true,
    )]));
    let bad = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(list)]).unwrap();
    let data = flight_stream(&schema, [&bad]);
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let result = session(false, ok(data), &mut stdout, &mut stderr);
    assert!(matches!(result, Err(m) if m.starts_with("panic: ")));
    assert!(stdout.is_empty());
    // The clear precedes conversion, as in Go.
    assert_eq!(stderr, CLEAR);
}

/// Blocks the first write until released.
struct HeldWriter {
    entered: Option<mpsc::Sender<()>>,
    release: mpsc::Receiver<()>,
    bytes: Vec<u8>,
}
impl Write for HeldWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if let Some(entered) = self.entered.take() {
            entered.send(()).unwrap();
            self.release.recv_timeout(Duration::from_secs(5)).unwrap();
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[test]
fn blocked_stdout_keeps_the_runtime_serviced_and_reads_no_further() {
    let (entered, wait_entered) = mpsc::channel();
    let (release, wait_release) = mpsc::channel();
    let mut stdout = HeldWriter {
        entered: Some(entered),
        release: wait_release,
        bytes: Vec::new(),
    };
    let mut stderr = Vec::new();
    let ticks = Arc::new(AtomicUsize::new(0));
    let pulled = Arc::new(AtomicUsize::new(0));
    let data = int_stream(&[&[1], &[2], &[3]]);
    let total = data.len();
    let (ticker_ticks, observed_pulled) = (Arc::clone(&ticks), Arc::clone(&pulled));
    let counted = {
        let pulled = Arc::clone(&pulled);
        stream::iter(ok(data)).inspect(move |_| {
            pulled.fetch_add(1, Ordering::SeqCst);
        })
    };
    let result = std::thread::scope(|scope| {
        let (mut handoff, inbox, acks) = Handoff::new();
        let stdout = &mut stdout;
        let stderr = &mut stderr;
        let worker = scope.spawn(move || Worker::new(stdout, stderr, "d", true).run(inbox, acks));
        let controller = scope.spawn(move || {
            wait_entered.recv_timeout(Duration::from_secs(5)).unwrap();
            let before = ticker_ticks.load(Ordering::SeqCst);
            std::thread::sleep(Duration::from_millis(300));
            let progressed = ticker_ticks.load(Ordering::SeqCst) - before;
            // Schema plus the first batch only: no read-ahead past the handoff.
            let read = observed_pulled.load(Ordering::SeqCst);
            release.send(()).unwrap();
            (progressed, read)
        });
        let result = block_on(async {
            let ticks = Arc::clone(&ticks);
            let ticker = tokio::spawn(async move {
                let mut interval = tokio::time::interval(Duration::from_millis(10));
                loop {
                    interval.tick().await;
                    ticks.fetch_add(1, Ordering::SeqCst);
                }
            });
            let Ok((stream, decoder)) = subscribe(Box::pin(counted), &mut handoff).await else {
                panic!("reader initialization failed");
            };
            let result = pump(stream, decoder, &mut handoff).await.is_ok();
            ticker.abort();
            result
        });
        drop(handoff);
        worker.join().unwrap();
        (result, controller.join().unwrap())
    });
    let (completed, (progressed, read)) = result;
    assert!(completed);
    assert!(
        progressed >= 5,
        "runtime stalled while stdout was blocked: {progressed}"
    );
    assert_eq!(read, 2);
    assert_eq!(pulled.load(Ordering::SeqCst), total);
    assert_eq!(stdout.bytes, b"{\"n\":1}\n\n{\"n\":2}\n\n{\"n\":3}\n\n");
}

#[test]
fn spinner_frames_until_stopped_then_clears_and_watching_follows() {
    let mut stderr = Vec::new();
    let mut stdout = Vec::new();
    std::thread::scope(|scope| {
        let (mut handoff, inbox, acks) = Handoff::new();
        let (out, err) = (&mut stdout, &mut stderr);
        let worker = scope.spawn(move || Worker::new(out, err, "app<1>", false).run(inbox, acks));
        block_on(async {
            assert!(handoff.status(false, Event::Spin).await.is_ok());
            tokio::time::sleep(Duration::from_millis(150)).await;
            assert!(handoff.status(false, Event::StopSpin).await.is_ok());
            assert!(handoff.status(false, Event::Watching).await.is_ok());
            // JSON suppresses status events entirely.
            assert!(handoff.status(true, Event::Watching).await.is_ok());
        });
        drop(handoff);
        worker.join().unwrap();
    });
    let text = String::from_utf8(stderr).unwrap();
    assert!(
        text.starts_with("\r| connecting...\r/ connecting..."),
        "{text:?}"
    );
    assert!(
        text.ends_with("\r\x1b[K\r\x1b[K● watching app<1>... (ctrl+c to stop)"),
        "{text:?}"
    );
    assert!(stdout.is_empty());
}

struct FailingWriter;
impl Write for FailingWriter {
    fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
        Err(std::io::Error::from_raw_os_error(28))
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[test]
fn ordinary_write_errors_are_ignored_like_go_fmt() {
    let (mut stdout, mut stderr) = (FailingWriter, FailingWriter);
    let mut items = ok(int_stream(&[&[1], &[2]]));
    items.push(Err(FlightStatus::new(UNAVAILABLE, "restart")));
    assert_eq!(session(false, items, &mut stdout, &mut stderr), Ok(()));
}

#[test]
fn oversized_staging_capacity_is_released() {
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let mut worker = Worker::new(&mut stdout, &mut stderr, "d", true);
    let mut large = int_stream(&[vec![1; 600_000].as_slice()]);
    let (mut decoder, batch) = {
        let schema = large.remove(0);
        (
            Decoder::new(&schema.data_header).ok().unwrap(),
            large.remove(0),
        )
    };
    assert_eq!(worker.message(&mut decoder, batch), Ok(Ok(())));
    assert_eq!(worker.staging.capacity(), 0);
    let small = int_stream(&[&[1]]).remove(1);
    assert_eq!(worker.message(&mut decoder, small), Ok(Ok(())));
    assert!(worker.staging.capacity() > 0);
    drop(worker);
    assert_eq!(stdout.len(), 600_000 * 8 + 1 + 9);
}

const INVALID: &[u8] = b"a\xffb";

/// A stream whose schema message declares `declared`, while the dictionary and
/// record messages carry `batch`'s byte-identical Binary layout. This is how a
/// Go server's invalid UTF-8 strings reach the client.
fn declared_as(declared: &Schema, batch: &RecordBatch) -> Vec<FlightData> {
    let mut data = flight_stream(&batch.schema(), [batch]);
    let mut tracker = DictionaryTracker::new(false);
    data[0] = IpcDataGenerator::default()
        .schema_to_bytes_with_dictionary_tracker(
            declared,
            &mut tracker,
            &IpcWriteOptions::default(),
        )
        .into();
    data
}

/// Declared Utf8 fields at each nesting level, and the matching Binary batch.
fn invalid_string_batch() -> (Schema, RecordBatch) {
    use arrow_array::{BinaryArray, LargeBinaryArray, StructArray};
    use arrow_buffer::OffsetBuffer;
    let shapes = |text: DataType, large: DataType| {
        Schema::new(vec![
            Field::new("s", text.clone(), true),
            Field::new("l", large, true),
            Field::new(
                "list",
                DataType::List(Arc::new(Field::new("item", text.clone(), true))),
                true,
            ),
            Field::new(
                "st",
                DataType::Struct(vec![Field::new("inner", text.clone(), true)].into()),
                true,
            ),
            Field::new(
                "d",
                DataType::Dictionary(Box::new(DataType::Int32), Box::new(text)),
                true,
            ),
        ])
    };
    let declared = shapes(DataType::Utf8, DataType::LargeUtf8);
    let actual = Arc::new(shapes(DataType::Binary, DataType::LargeBinary));
    let bytes = || Arc::new(BinaryArray::from(vec![INVALID])) as ArrayRef;
    let DataType::List(item) = actual.field(2).data_type().clone() else {
        unreachable!()
    };
    let DataType::Struct(inner) = actual.field(3).data_type().clone() else {
        unreachable!()
    };
    let columns: Vec<ArrayRef> = vec![
        bytes(),
        Arc::new(LargeBinaryArray::from(vec![INVALID])),
        Arc::new(ListArray::new(
            item,
            OffsetBuffer::from_lengths([1]),
            bytes(),
            None,
        )),
        Arc::new(StructArray::new(inner, vec![bytes()], None)),
        Arc::new(DictionaryArray::<Int32Type>::new(vec![0].into(), bytes())),
    ];
    (declared, RecordBatch::try_new(actual, columns).unwrap())
}

#[test]
fn invalid_utf8_strings_decode_as_bytes_at_every_nesting_level() {
    use arrow_array::cast::AsArray;
    let (declared, batch) = invalid_string_batch();
    let (batches, stop) = decode_all(ok(declared_as(&declared, &batch)));
    assert_eq!(stop, Stop::Reconnect);
    let batch = &batches[0];
    assert_eq!(batch.column(0).as_binary::<i32>().value(0), INVALID);
    assert_eq!(batch.column(1).as_binary::<i64>().value(0), INVALID);
    let list = batch.column(2).as_list::<i32>().value(0);
    assert_eq!(list.as_binary::<i32>().value(0), INVALID);
    let inner = batch.column(3).as_struct().column(0);
    assert_eq!(inner.as_binary::<i32>().value(0), INVALID);
    let dictionary = batch.column(4).as_dictionary::<Int32Type>();
    assert_eq!(dictionary.values().as_binary::<i32>().value(0), INVALID);
}

#[test]
fn invalid_utf8_strings_render_with_the_original_string_plan() {
    let declared = Schema::new(vec![Field::new("s", DataType::Utf8, true)]);
    let actual = Arc::new(Schema::new(vec![Field::new("s", DataType::Binary, true)]));
    let column = Arc::new(arrow_array::BinaryArray::from(vec![INVALID]));
    let batch = RecordBatch::try_new(actual, vec![column]).unwrap();
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let items = ok(declared_as(&declared, &batch));
    assert_eq!(session(true, items, &mut stdout, &mut stderr), Ok(()));
    assert_eq!(stdout, b"{\"s\":\"a\\ufffdb\"}\n\n");

    // Nested and dictionary strings use the same original-schema plan.
    let (declared, batch) = invalid_string_batch();
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let items = ok(declared_as(&declared, &batch));
    assert_eq!(session(true, items, &mut stdout, &mut stderr), Ok(()));
    let escaped = "\"a\\ufffdb\"";
    assert_eq!(
        String::from_utf8(stdout).unwrap(),
        format!(
            "{{\"d\":{escaped},\"l\":{escaped},\"list\":[{escaped}],\"s\":{escaped},\"st\":{{\"inner\":{escaped}}}}}\n\n"
        )
    );
}

#[test]
#[allow(deprecated)]
fn wire_schema_rewrites_only_string_types_and_keeps_ids_and_metadata() {
    use arrow_schema::{UnionFields, UnionMode};
    let metadata = HashMap::from([("k".to_string(), "v".to_string())]);
    let text = Field::new("t", DataType::Utf8, false).with_metadata(metadata.clone());
    let dictionary = Field::new_dict(
        "d",
        DataType::Dictionary(Box::new(DataType::Int8), Box::new(DataType::LargeUtf8)),
        true,
        7,
        true,
    );
    let entries = Field::new(
        "entries",
        DataType::Struct(
            vec![
                Field::new("key", DataType::Utf8, false),
                Field::new("value", DataType::Int64, true),
            ]
            .into(),
        ),
        false,
    );
    let union = DataType::Union(
        UnionFields::from_iter([(3, Arc::new(Field::new("u", DataType::Utf8, true)))]),
        UnionMode::Sparse,
    );
    let schema = Schema::new_with_metadata(
        vec![
            text,
            Field::new("list", DataType::List(Arc::new(dictionary)), true),
            Field::new("map", DataType::Map(Arc::new(entries), false), true),
            Field::new("union", union, true),
            Field::new("bin", DataType::Binary, true),
        ],
        metadata.clone(),
    );
    let wire = wire_schema(&schema);
    assert_eq!(wire.metadata(), &metadata);
    assert_eq!(wire.field(0).data_type(), &DataType::Binary);
    assert_eq!(wire.field(0).metadata(), &metadata);
    // Go never enforces declared nullability.
    assert!(wire.field(0).is_nullable());
    let DataType::List(item) = wire.field(1).data_type() else {
        panic!("list")
    };
    assert_eq!(
        item.data_type(),
        &DataType::Dictionary(Box::new(DataType::Int8), Box::new(DataType::LargeBinary))
    );
    assert_eq!(item.dict_id(), Some(7));
    assert_eq!(item.dict_is_ordered(), Some(true));
    let DataType::Map(entries, false) = wire.field(2).data_type() else {
        panic!("map")
    };
    let DataType::Struct(entry) = entries.data_type() else {
        panic!("entries")
    };
    assert_eq!(entry[0].data_type(), &DataType::Binary);
    assert_eq!(entry[1].data_type(), &DataType::Int64);
    // Arrow requires non-null Map entries and keys; values become nullable.
    assert!(!entries.is_nullable() && !entry[0].is_nullable());
    assert!(entry[1].is_nullable());
    let DataType::Union(members, UnionMode::Sparse) = wire.field(3).data_type() else {
        panic!("union")
    };
    let (id, member) = members.iter().next().unwrap();
    assert_eq!((id, member.data_type()), (3, &DataType::Binary));
    assert_eq!(wire.field(4).data_type(), &DataType::Binary);
}

/// Overwrite the `index`th i32 offset of IPC buffer `buffer` in `data`.
fn corrupt_offset(
    data: &mut FlightData,
    dictionary: bool,
    buffer: usize,
    index: usize,
    value: i32,
) {
    let header = data.data_header.clone();
    let message = arrow_ipc::root_as_message(&header).unwrap();
    let batch = if dictionary {
        message
            .header_as_dictionary_batch()
            .unwrap()
            .data()
            .unwrap()
    } else {
        message.header_as_record_batch().unwrap()
    };
    let start = batch.buffers().unwrap().get(buffer).offset() as usize + index * 4;
    let mut body = data.data_body.to_vec();
    body[start..start + 4].copy_from_slice(&value.to_le_bytes());
    data.data_body = Bytes::from(body);
}

#[test]
fn hostile_offsets_remain_rejected_for_rewritten_strings() {
    let declared = Schema::new(vec![Field::new("s", DataType::Utf8, false)]);
    let actual = Arc::new(Schema::new(vec![Field::new("s", DataType::Binary, false)]));
    let column = Arc::new(arrow_array::BinaryArray::from(vec![
        b"ab".as_slice(),
        b"cd",
    ]));
    let batch = RecordBatch::try_new(actual, vec![column]).unwrap();
    // Buffers: validity, offsets [0, 2, 4], values.
    for (index, value) in [(2, 1 << 20), (1, 5), (0, -1)] {
        let mut data = declared_as(&declared, &batch);
        corrupt_offset(&mut data[1], false, 1, index, value);
        let (batches, stop) = decode_all(ok(data));
        assert!(batches.is_empty());
        assert!(
            matches!(&stop, Stop::Fatal(m) if m.starts_with(UNKNOWN_READ.as_bytes())),
            "offset {index}={value}: {stop:?}"
        );
    }

    // Dictionary values are validated through the same wire type.
    let (declared, batch) = invalid_string_batch();
    let mut data = declared_as(&declared, &batch);
    corrupt_offset(&mut data[1], true, 1, 1, 1 << 20);
    let (batches, stop) = decode_all(ok(data));
    assert!(batches.is_empty());
    assert!(matches!(stop, Stop::Fatal(_)), "{stop:?}");
}

/// A field node or buffer entry, and which of its two i64 values to patch.
#[derive(Clone, Copy)]
enum Slot {
    NodeLength(usize),
    NodeNulls(usize),
    BufferOffset(usize),
    BufferLength(usize),
}

/// Rewrite one i64 of a node or buffer entry in place. FieldNode and Buffer
/// are 16-byte flatbuffer structs stored inline, so the header stays valid.
fn patch(data: &mut FlightData, slot: Slot, value: i64) {
    let mut header = data.data_header.to_vec();
    let position = {
        let message = arrow_ipc::root_as_message(&header).unwrap();
        let batch = message
            .header_as_record_batch()
            .or_else(|| message.header_as_dictionary_batch().and_then(|d| d.data()))
            .unwrap();
        let (bytes, index, half) = match slot {
            Slot::NodeLength(i) => (batch.nodes().unwrap().bytes(), i, 0),
            Slot::NodeNulls(i) => (batch.nodes().unwrap().bytes(), i, 1),
            Slot::BufferOffset(i) => (batch.buffers().unwrap().bytes(), i, 0),
            Slot::BufferLength(i) => (batch.buffers().unwrap().bytes(), i, 1),
        };
        bytes.as_ptr() as usize - header.as_ptr() as usize + index * 16 + half * 8
    };
    header[position..position + 8].copy_from_slice(&value.to_le_bytes());
    data.data_header = Bytes::from(header);
}

/// Overwrite a compressed buffer's decoded-size prefix.
fn patch_prefix(data: &mut FlightData, buffer: usize, value: i64) {
    let start = {
        let message = arrow_ipc::root_as_message(&data.data_header).unwrap();
        let batch = message
            .header_as_record_batch()
            .or_else(|| message.header_as_dictionary_batch().and_then(|d| d.data()))
            .unwrap();
        let entry = batch.buffers().unwrap().get(buffer);
        assert!(entry.length() >= 8, "buffer {buffer} has no prefix");
        entry.offset() as usize
    };
    let mut body = data.data_body.to_vec();
    body[start..start + 8].copy_from_slice(&value.to_le_bytes());
    data.data_body = Bytes::from(body);
}

fn rejected(data: Vec<FlightData>, reason: &str) {
    let (batches, stop) = decode_all(ok(data));
    assert!(batches.is_empty());
    match stop {
        Stop::Fatal(message) => {
            let message = String::from_utf8(message).unwrap();
            assert!(
                message.starts_with(UNKNOWN_READ) && message.contains(reason),
                "{message:?} lacks {reason:?}"
            );
        }
        Stop::Reconnect => panic!("malformed batch was accepted"),
    }
}

fn nullable_ints() -> (Arc<Schema>, RecordBatch) {
    let schema = Arc::new(Schema::new(vec![Field::new("n", DataType::Int64, true)]));
    let column = Arc::new(Int64Array::from(vec![Some(1), None, Some(3)]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![column]).unwrap();
    (schema, batch)
}

/// Replace every little-endian 77 in the record header (the batch length,
/// node length and, for Null, its null count), as the Go probe did.
fn negate_rows(data: &mut FlightData, value: i64) -> usize {
    let mut header = data.data_header.to_vec();
    let mut found = 0;
    for start in 0..header.len() - 7 {
        if header[start..start + 8] == 77_i64.to_le_bytes() {
            header[start..start + 8].copy_from_slice(&value.to_le_bytes());
            found += 1;
        }
    }
    data.data_header = Bytes::from(header);
    found
}

/// One 77-row column of each probed kind.
fn probed_rows(kind: &str) -> Vec<FlightData> {
    use arrow_array::{NullArray, StructArray};
    let (field, column): (Field, ArrayRef) = match kind {
        "null" => (
            Field::new("z", DataType::Null, true),
            Arc::new(NullArray::new(77)),
        ),
        "struct" => (
            Field::new("s", DataType::Struct(Fields::empty()), true),
            Arc::new(StructArray::new_empty_fields(77, None)),
        ),
        _ => (
            Field::new("n", DataType::Int64, true),
            Arc::new(Int64Array::from_iter_values(1000..1077)),
        ),
    };
    let schema = Arc::new(Schema::new(vec![field]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![column]).unwrap();
    flight_stream(&schema, [&batch])
}

/// Go arrow v13.0.0 probe of tail's Read/RecordToJSON/Println loop, durable
/// evidence docs/evidence/phase6/negative-row-go125.json (Go 1.25.4), with
/// the batch and node lengths patched to -1 and -5:
/// Null and field-less Struct print only "\n" and the stream continues;
/// Int64 fails with "arrow/ipc: unknown error while reading: runtime error:
/// slice bounds out of range [:-1]". Rust renders the first two the same way
/// and rejects the third (a fatal error with different text).
#[test]
fn negative_row_counts_follow_the_go_probe_or_are_rejected() {
    for (kind, occurrences) in [("null", 3), ("struct", 2)] {
        let (batches, stop) = decode_all(ok(probed_rows(kind)));
        assert_eq!((batches[0].num_rows(), stop), (77, Stop::Reconnect));
        for value in [-1, -5] {
            let mut data = probed_rows(kind);
            assert_eq!(negate_rows(&mut data[1], value), occurrences, "{kind}");
            let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
            assert_eq!(session(true, ok(data), &mut stdout, &mut stderr), Ok(()));
            assert_eq!(stdout, b"\n", "{kind} {value}");
        }
    }
    for value in [-1, -5] {
        let mut data = probed_rows("int64");
        assert_eq!(negate_rows(&mut data[1], value), 2);
        rejected(data, &format!("invalid record batch length {value}"));
    }

    // Not probed: a negative node under a valid batch length, and a Null
    // column whose null count differs from its negative length.
    let mut data = probed_rows("null");
    patch(&mut data[1], Slot::NodeLength(0), -1);
    rejected(data, "invalid node length -1");
    let mut data = probed_rows("null");
    negate_rows(&mut data[1], -1);
    patch(&mut data[1], Slot::NodeNulls(0), 0);
    rejected(data, "invalid record batch length -1");
}

#[test]
fn node_null_counts_must_be_within_the_node_length() {
    let (schema, batch) = nullable_ints();
    for nulls in [-1, 4] {
        let mut data = flight_stream(&schema, [&batch]);
        patch(&mut data[1], Slot::NodeNulls(0), nulls);
        rejected(data, &format!("invalid null count {nulls}"));
    }
}

#[test]
fn short_validity_bitmaps_are_rejected_for_values_structs_and_dictionaries() {
    use arrow_array::{Array, StructArray};
    use arrow_buffer::NullBuffer;
    // Primitive column with nulls and an empty validity buffer.
    let (schema, batch) = nullable_ints();
    let mut data = flight_stream(&schema, [&batch]);
    patch(&mut data[1], Slot::BufferLength(0), 0);
    rejected(data, "validity bitmap is shorter than 3 rows");

    // Struct column with nulls: buffer 0 is the struct's validity.
    let inner = Fields::from(vec![Field::new("n", DataType::Int64, true)]);
    let column = StructArray::new(
        inner.clone(),
        vec![Arc::new(Int64Array::from(vec![1, 2, 3]))],
        Some(NullBuffer::from(vec![true, false, true])),
    );
    let schema = Arc::new(Schema::new(vec![Field::new(
        "s",
        DataType::Struct(inner),
        true,
    )]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(column)]).unwrap();
    let mut data = flight_stream(&schema, [&batch]);
    patch(&mut data[1], Slot::BufferLength(0), 0);
    rejected(data, "validity bitmap is shorter than 3 rows");

    // Dictionary values with a null, decoded from the dictionary message.
    let values = Arc::new(StringArray::from(vec![Some("a"), None]));
    let column = DictionaryArray::<Int32Type>::new(vec![0, 1].into(), values);
    let schema = Arc::new(Schema::new(vec![Field::new(
        "d",
        column.data_type().clone(),
        true,
    )]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(column)]).unwrap();
    let mut data = flight_stream(&schema, [&batch]);
    patch(&mut data[1], Slot::BufferLength(0), 0);
    rejected(data, "validity bitmap is shorter than 2 rows");
}

fn union_batch(mode: arrow_schema::UnionMode) -> (Arc<Schema>, RecordBatch) {
    use arrow_array::{Array, UnionArray};
    use arrow_schema::UnionFields;
    let fields =
        UnionFields::try_new(vec![0], vec![Field::new("i", DataType::Int64, true)]).unwrap();
    let offsets = (mode == arrow_schema::UnionMode::Dense).then(|| vec![0_i32, 1].into());
    let union = UnionArray::try_new(
        fields,
        vec![0_i8, 0].into(),
        offsets,
        vec![Arc::new(Int64Array::from(vec![4, 5]))],
    )
    .unwrap();
    let schema = Arc::new(Schema::new(vec![Field::new(
        "u",
        union.data_type().clone(),
        true,
    )]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(union)]).unwrap();
    (schema, batch)
}

#[test]
fn union_type_ids_and_dense_offsets_are_checked_before_slicing() {
    use arrow_schema::UnionMode;
    for mode in [UnionMode::Sparse, UnionMode::Dense] {
        let (schema, batch) = union_batch(mode);
        let valid = flight_stream(&schema, [&batch]);
        assert_eq!(decode_all(ok(valid.clone())).0.len(), 1);

        // Buffer 0 holds the type ids (no union validity in metadata V5).
        let mut data = valid.clone();
        patch(&mut data[1], Slot::BufferLength(0), 1);
        rejected(data, "union type ids are shorter than 2 rows");

        let mut data = valid.clone();
        patch(&mut data[1], Slot::NodeLength(0), -2);
        rejected(data, "invalid node length -2");
    }
    let (schema, batch) = union_batch(UnionMode::Dense);
    let valid = flight_stream(&schema, [&batch]);
    let mut data = valid.clone();
    patch(&mut data[1], Slot::BufferLength(1), 4);
    rejected(data, "union offsets are shorter than 2 rows");

    // In range, but no longer viewable as i32 in place.
    let mut data = valid;
    let offset = {
        let message = arrow_ipc::root_as_message(&data[1].data_header).unwrap();
        let entry = message
            .header_as_record_batch()
            .unwrap()
            .buffers()
            .unwrap()
            .get(1);
        entry.offset()
    };
    patch(&mut data[1], Slot::BufferOffset(1), offset + 1);
    rejected(data, "union offsets are not 4-byte aligned");
}

fn compressed(codec: arrow_ipc::CompressionType) -> Vec<FlightData> {
    let (schema, batch) = nullable_ints();
    let options = IpcWriteOptions::default()
        .try_with_compression(Some(codec))
        .unwrap();
    flight_stream_with(options, &schema, [&batch])
}

#[test]
fn compressed_bodies_decode_and_their_declared_sizes_are_checked() {
    use arrow_ipc::CompressionType;
    for codec in [CompressionType::LZ4_FRAME, CompressionType::ZSTD] {
        let (batches, stop) = decode_all(ok(compressed(codec)));
        assert_eq!((batches[0].num_rows(), stop), (3, Stop::Reconnect));

        // A validity buffer declared empty cannot hold the declared nulls.
        let mut data = compressed(codec);
        patch_prefix(&mut data[1], 0, 0);
        rejected(data, "validity bitmap is shorter than 3 rows");

        // An impossible decoded size is rejected before arrow allocates it.
        let mut data = compressed(codec);
        patch_prefix(&mut data[1], 1, i64::MAX);
        rejected(data, "compressed buffer declares");

        // Other negative sizes remain arrow's own error.
        let mut data = compressed(codec);
        patch_prefix(&mut data[1], 1, -5);
        let (batches, stop) = decode_all(ok(data));
        assert!(batches.is_empty());
        assert!(matches!(stop, Stop::Fatal(_)), "{stop:?}");
    }
}

/// Schema header for `schema`, with dictionary IDs assigned by the writer.
fn schema_header(schema: &Schema) -> Bytes {
    let mut tracker = DictionaryTracker::new(false);
    let encoded: FlightData = IpcDataGenerator::default()
        .schema_to_bytes_with_dictionary_tracker(schema, &mut tracker, &IpcWriteOptions::default())
        .into();
    encoded.data_header
}

#[test]
fn dictionary_ids_are_counted_from_the_ipc_schema_including_nested_fields() {
    let dictionary = || DataType::Dictionary(Box::new(DataType::Int8), Box::new(DataType::Utf8));
    let schema = Schema::new(vec![
        Field::new("x", dictionary(), true),
        Field::new(
            "l",
            DataType::List(Arc::new(Field::new("item", dictionary(), true))),
            true,
        ),
        Field::new(
            "s",
            DataType::Struct(vec![Field::new("a", dictionary(), true)].into()),
            true,
        ),
        Field::new("u", DataType::Utf8, true),
    ]);
    let decoder = Decoder::new(&schema_header(&schema)).ok().unwrap();
    assert_eq!(decoder.expected, 3);
    assert_eq!(decoder.ids.len(), 3);
    let plain = Schema::new(vec![Field::new("u", DataType::Utf8, true)]);
    assert_eq!(
        Decoder::new(&schema_header(&plain)).ok().unwrap().expected,
        0
    );
}

#[test]
#[allow(deprecated)]
fn fields_sharing_a_dictionary_id_need_one_dictionary_message() {
    // The writer numbers dictionaries 0 and 1; rewrite the stored 1 to 0. ID 0
    // is the flatbuffer default and is not stored, so 1 occurs exactly once.
    let data = dictionary_stream(2);
    let one = 1_i64.to_le_bytes();
    let header = data[0].data_header.to_vec();
    let positions: Vec<_> = (0..header.len() - 7)
        .filter(|&start| header[start..start + 8] == one)
        .collect();
    assert_eq!(positions.len(), 1);
    let mut shared = header;
    shared[positions[0]..positions[0] + 8].copy_from_slice(&0_i64.to_le_bytes());
    let decoder = Decoder::new(&shared).ok().unwrap();
    assert_eq!(decoder.expected, 1);
    assert!(decoder.wire.fields().iter().all(|f| f.dict_id() == Some(0)));

    // Schema, the single shared dictionary, then the batch.
    let mut stream = vec![data[0].clone(), data[1].clone(), data[3].clone()];
    stream[0].data_header = Bytes::from(shared);
    let (batches, stop) = decode_all(ok(stream));
    assert_eq!(stop, Stop::Reconnect);
    assert_eq!(batches[0].num_rows(), 3);
}

/// Reader initialization runs on the worker: while the worker is held inside
/// schema decoding, the runtime keeps running and the driver reads nothing
/// further. A wide schema then decodes and renders through the same path.
#[test]
fn schema_initialization_runs_on_the_worker_while_the_runtime_progresses() {
    let fields: Vec<_> = (0..20_000)
        .map(|i| Field::new(format!("c{i:05}"), DataType::Int64, true))
        .collect();
    let schema = Arc::new(Schema::new(fields));
    let columns: Vec<ArrayRef> = (0..20_000)
        .map(|i| Arc::new(Int64Array::from(vec![i])) as ArrayRef)
        .collect();
    let batch = RecordBatch::try_new(Arc::clone(&schema), columns).unwrap();
    let data = flight_stream(&schema, [&batch]);
    let pulled = Arc::new(AtomicUsize::new(0));
    let counted = {
        let pulled = Arc::clone(&pulled);
        stream::iter(ok(data)).inspect(move |_| {
            pulled.fetch_add(1, Ordering::SeqCst);
        })
    };
    let ticks = Arc::new(AtomicUsize::new(0));
    let (entered, wait_entered) = mpsc::channel();
    let (release, wait_release) = mpsc::channel();
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let (completed, (progressed, read_while_held)) = std::thread::scope(|scope| {
        let (mut handoff, inbox, acks) = Handoff::new();
        let (out, err) = (&mut stdout, &mut stderr);
        let worker = scope.spawn(move || {
            let mut worker = Worker::new(out, err, "d", true);
            worker.open_gate = Some((entered, wait_release));
            worker.run(inbox, acks)
        });
        let (observed_ticks, observed_pulled) = (Arc::clone(&ticks), Arc::clone(&pulled));
        let controller = scope.spawn(move || {
            wait_entered.recv_timeout(Duration::from_secs(5)).unwrap();
            let before = observed_ticks.load(Ordering::SeqCst);
            std::thread::sleep(Duration::from_millis(300));
            let progressed = observed_ticks.load(Ordering::SeqCst) - before;
            let read = observed_pulled.load(Ordering::SeqCst);
            release.send(()).unwrap();
            (progressed, read)
        });
        let completed = block_on(async {
            let ticks = Arc::clone(&ticks);
            let ticker = tokio::spawn(async move {
                let mut interval = tokio::time::interval(Duration::from_millis(10));
                loop {
                    interval.tick().await;
                    ticks.fetch_add(1, Ordering::SeqCst);
                }
            });
            let Ok((stream, decoder)) = subscribe(Box::pin(counted), &mut handoff).await else {
                panic!("reader initialization failed");
            };
            let completed = pump(stream, decoder, &mut handoff).await.is_ok();
            ticker.abort();
            completed
        });
        drop(handoff);
        worker.join().unwrap();
        (completed, controller.join().unwrap())
    });
    assert!(completed);
    assert!(
        progressed >= 5,
        "runtime stalled during initialization: {progressed}"
    );
    // Only the schema message was read while initialization was pending.
    assert_eq!(read_while_held, 1);
    let text = String::from_utf8(stdout).unwrap();
    assert!(
        text.starts_with("{\"c00000\":0,\"c00001\":1,"),
        "{}",
        &text[..40]
    );
    assert!(text.ends_with("\"c19999\":19999}\n\n"));
}

#[test]
fn initialization_errors_keep_their_wrapping_through_the_worker() {
    let data = int_stream(&[&[1]]);
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let result = std::thread::scope(|scope| {
        let (mut handoff, inbox, acks) = Handoff::new();
        let (out, err) = (&mut stdout, &mut stderr);
        let worker = scope.spawn(move || Worker::new(out, err, "d", true).run(inbox, acks));
        let result = block_on(async {
            match subscribe(stream::iter(ok(data[1..].to_vec())), &mut handoff).await {
                Err(Exit::Fatal(message)) => String::from_utf8(message).unwrap(),
                _ => panic!("missing schema was accepted"),
            }
        });
        drop(handoff);
        worker.join().unwrap();
        result
    });
    assert_eq!(
        result,
        "arrow/flight: could not create flight reader: arrow/ipc: invalid message type \
         (got=RecordBatch, want=Schema)"
    );
    assert!(stdout.is_empty() && stderr.is_empty());
}

#[test]
fn raw_status_bytes_survive_every_fatal_stage() {
    let message: &[u8] = b"bad \xff%FF";
    // Reader initialization: the first read, and the schema read after a
    // skipped metadata-only message.
    let status = FlightStatus::new(UNAVAILABLE, message);
    assert_eq!(open_bytes(vec![Err(status.clone())]), status.go_error());
    assert_eq!(
        open_bytes(vec![Ok(metadata_only()), Err(status.clone())]),
        [SCHEMA_READ.as_bytes(), &status.go_error()].concat()
    );
    // Established reads: fatal codes keep the bytes, stream-end codes retry.
    let fatal = FlightStatus::new(INTERNAL, message);
    let mut items = ok(int_stream(&[&[1]]));
    items.push(Err(fatal.clone()));
    assert_eq!(decode_all(items).1, Stop::Fatal(fatal.go_error()));
    for code in [OK, CANCELED, UNAVAILABLE] {
        let mut items = ok(int_stream(&[&[1]]));
        items.push(Err(FlightStatus::new(code, message)));
        assert_eq!(decode_all(items).1, Stop::Reconnect, "code {code}");
    }
}

/// Status messages are Go byte strings: text prints them raw, and JSON
/// replaces each invalid byte separately (a truncated multibyte sequence
/// gives one replacement per byte, unlike Rust's lossy conversion).
#[test]
fn fatal_bytes_render_raw_in_text_and_replaced_in_json() {
    let command = crate::spec::command_spec().child("tail").unwrap();
    for (raw_message, json_message) in [
        (b"a\xffb".as_slice(), "a\\ufffdb"),
        (b"a\xe9b", "a\\ufffdb"),
        (b"a\xe9\x80b", "a\\ufffd\\ufffdb"),
    ] {
        for (code, name) in [(INTERNAL, "Internal"), (UNAUTHENTICATED, "Unauthenticated")] {
            let status = FlightStatus::new(code, raw_message);
            let raw = [SCHEMA_READ.as_bytes(), &status.go_error()].concat();
            let Terminal::Failed(error) = failed(raw.clone()) else {
                panic!("not a command failure");
            };
            let text = error.render(command, "pb tail", false);
            assert_eq!(text.exit_code, 1);
            assert!(
                text.stderr
                    .starts_with(&[b"Error: ".as_slice(), &raw, b"\n"].concat())
            );
            let json = String::from_utf8(error.render(command, "pb tail", true).stderr).unwrap();
            let expected = format!("{SCHEMA_READ}rpc error: code = {name} desc = {json_message}");
            assert!(
                json.contains(&format!("\"message\": \"{expected}\"")),
                "{json}"
            );
            assert!(json.contains("\"code\": \"COMMAND_FAILED\""), "{json}");
            assert!(json.contains("\"retryable\": false"), "{json}");
        }
    }
}

/// Split a Go-written IPC stream (continuation marker, i32 length, padded
/// header, body; ending with a zero length) into Flight messages.
fn messages(raw: &[u8]) -> Vec<FlightData> {
    let mut at = 0;
    let mut out = Vec::new();
    loop {
        assert_eq!(&raw[at..at + 4], &[0xff; 4]);
        let length = i32::from_le_bytes(raw[at + 4..at + 8].try_into().unwrap()) as usize;
        at += 8;
        if length == 0 {
            return out;
        }
        let header = &raw[at..at + length];
        at += length;
        let body_length = ipc_message(header).unwrap().bodyLength() as usize;
        let body = &raw[at..at + body_length];
        at += body_length;
        out.push(FlightData {
            data_header: Bytes::copy_from_slice(header),
            data_body: Bytes::copy_from_slice(body),
            ..Default::default()
        });
    }
}

fn fixture(directory: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(directory)
}

fn listed_cases(dir: &std::path::Path) -> Vec<String> {
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(dir.join("manifest.json")).unwrap()).unwrap();
    manifest["cases"]
        .as_array()
        .unwrap()
        .iter()
        .map(|case| case.as_str().unwrap().to_owned())
        .collect()
}

/// Every Go-written value-corpus stream through the production path: worker
/// schema initialization, metadata validation, decoding, rendering, staging
/// and the batch separator. Go panics end the session with the same text.
#[test]
fn go_value_corpus_replays_byte_exact_through_the_session() {
    let dir = fixture("arrow-go-v13");
    let cases = listed_cases(&dir);
    assert_eq!(cases.len(), 44);
    let mut differ = Vec::new();
    for case in &cases {
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        let expected = read("expected");
        let expected_panic = String::from_utf8(read("panic")).unwrap();
        let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
        let result = session(
            true,
            ok(messages(&read("arrows"))),
            &mut stdout,
            &mut stderr,
        );
        let same = stdout == expected
            && match &result {
                Ok(()) => expected_panic.is_empty(),
                Err(message) => message.strip_prefix("panic: ") == Some(expected_panic.trim_end()),
            };
        if !same {
            differ.push(format!(
                "{case}: {result:?}\n  go:   {:?}\n  rust: {:?}",
                String::from_utf8_lossy(&expected),
                String::from_utf8_lossy(&stdout)
            ));
        }
    }
    assert!(differ.is_empty(), "{}", differ.join("\n"));
}

/// Float shortest-tie and nil interval-list regressions must also survive
/// production schema initialization, wire rewriting and IPC batch decoding.
#[test]
fn value_wave_corpus_replays_byte_exact_through_the_session() {
    let dir = fixture("arrow-go-v13-values-wave");
    let cases = listed_cases(&dir);
    assert_eq!(cases.len(), 9);
    for case in cases {
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        assert!(read("panic").is_empty(), "{case}: unexpected oracle panic");
        assert!(read("errors").is_empty(), "{case}: unexpected oracle error");
        for json in [true, false] {
            let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
            let result = session(
                json,
                ok(messages(&read("arrows"))),
                &mut stdout,
                &mut stderr,
            );
            assert!(result.is_ok(), "{case}: {result:?}");
            assert_eq!(stdout, read("expected"), "{case}: json={json}");
        }
    }
}

/// Decoder-review r2 streams against Go arrow v13.0.0 (go1.25.4; see the
/// fixture manifest). Parity cases match Go's stdout and terminal error.
/// The two declared safety divergences are counted separately: Go renders an
/// FSL whose node length overflows and panics while rendering a negative
/// FixedSizeBinary width; Rust rejects both with its own fatal text.
#[test]
fn decoder_review_streams_match_go_or_are_declared_divergences() {
    let dir = fixture("arrow-go-v13-decoder");
    let cases = listed_cases(&dir);
    assert_eq!(cases.len(), 10);
    let (mut parity, mut divergent) = (0, 0);
    for case in &cases {
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        let go_stdout = read("stdout");
        let go_stderr = String::from_utf8(read("stderr")).unwrap();
        let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
        let result = session(
            true,
            ok(messages(&read("arrows"))),
            &mut stdout,
            &mut stderr,
        );
        match case.as_str() {
            "p1_fsb_negative" => {
                assert!(
                    go_stderr.contains("panic: runtime error: slice bounds out of range [:-1]")
                );
                let message = result.unwrap_err();
                assert!(message.starts_with(FLIGHT_READER), "{message}");
                assert!(
                    message.contains("invalid FixedSizeBinary width -1"),
                    "{message}"
                );
                assert!(stdout.is_empty());
                divergent += 1;
            }
            "p2_fsl_overflow" => {
                assert_eq!(go_stdout, b"{\"f\":[1,2,3,4]}\n\n");
                let message = result.unwrap_err();
                assert!(message.contains("fixed-size list length"), "{message}");
                assert!(stdout.is_empty());
                divergent += 1;
            }
            _ => {
                assert_eq!(stdout, go_stdout, "{case}");
                // The probe's terminal line: an init error, a read error, or EOF.
                let last = go_stderr.lines().last().unwrap();
                match (
                    last.strip_prefix("init error: "),
                    last.strip_prefix("read end: "),
                ) {
                    (Some(init), _) => {
                        assert_eq!(result, Err(format!("{FLIGHT_READER}{init}")), "{case}")
                    }
                    (_, Some("EOF")) => assert_eq!(result, Ok(()), "{case}"),
                    (_, Some(read)) => assert_eq!(result, Err(read.to_owned()), "{case}"),
                    _ => panic!("{case}: unexpected Go stderr {go_stderr:?}"),
                }
                parity += 1;
            }
        }
    }
    assert_eq!((parity, divergent), (8, 2));
}

#[test]
fn schema_level_hazards_fail_reader_initialization() {
    // A negative FixedSizeBinary width nested in dictionary values.
    let schema = Schema::new(vec![Field::new(
        "d",
        DataType::Dictionary(
            Box::new(DataType::Int32),
            Box::new(DataType::FixedSizeBinary(-3)),
        ),
        true,
    )]);
    let message = String::from_utf8(
        initialize(&FlightData {
            data_header: schema_header(&schema),
            ..Default::default()
        })
        .err()
        .unwrap(),
    )
    .unwrap();
    assert_eq!(
        message,
        "arrow/flight: could not create flight reader: arrow/ipc: could not decode schema \
         from message schema: invalid FixedSizeBinary width -3"
    );
}

#[test]
fn compressed_buffers_shorter_than_their_prefix_fail_like_go() {
    use arrow_ipc::CompressionType;
    for codec in [CompressionType::LZ4_FRAME, CompressionType::ZSTD] {
        for length in [1, 7] {
            let mut data = compressed(codec);
            patch(&mut data[1], Slot::BufferLength(1), length);
            let (batches, stop) = decode_all(ok(data));
            assert!(batches.is_empty());
            assert_eq!(
                stop,
                Stop::Fatal(format!("{UNKNOWN_READ}unexpected EOF").into_bytes())
            );
        }
    }
}

#[test]
fn fixed_size_list_lengths_that_overflow_are_rejected() {
    use arrow_array::{Array, FixedSizeListArray, Int8Array};
    // i64::MAX × 2 still fits in usize, so arrow reports the short child
    // itself; × 4 overflows and must be caught before arrow's assertion.
    for (size, reason) in [
        (2, "is less than the length"),
        (4, "fixed-size list length"),
    ] {
        let column = FixedSizeListArray::new(
            Arc::new(Field::new_list_field(DataType::Int8, true)),
            size,
            Arc::new(Int8Array::from_iter_values(0..size as i8)),
            None,
        );
        let schema = Arc::new(Schema::new(vec![Field::new(
            "f",
            column.data_type().clone(),
            true,
        )]));
        let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(column)]).unwrap();
        let mut data = flight_stream(&schema, [&batch]);
        assert_eq!(decode_all(ok(data.clone())).0.len(), 1);
        patch(&mut data[1], Slot::NodeLength(0), i64::MAX);
        rejected(data, reason);
    }
}

/// Decoder-review r3 streams against Go arrow v13.0.0 (go1.25.4; see the
/// fixture manifest). Go's terminal state is the probe's last stderr line:
/// `init error: X` (fatal at reader initialization), `read end: EOF`
/// (reconnect) or `read end: X` (fatal read). Declared divergences are
/// counted separately and never as parity.
#[test]
fn decoder_review_r3_streams_match_go_or_are_declared_divergences() {
    let dir = fixture("arrow-go-v13-decoder-r3");
    let cases = listed_cases(&dir);
    assert_eq!(cases.len(), 30);
    // Go's record accepts columns at least as long as its row count (the
    // batch length, or the first column's when negative); Rust requires the
    // exact batch length and fails these malformed producers.
    let row_count = [
        "g1_int_node_gt_rows",
        "g3_int_negative_batch_valid_nodes",
        "g3b_int_zero_batch_valid_nodes",
        "g4_fsl2_node_max",
        "g5_fsl2_node2_rows1",
    ];
    // Both fatal at the same read; only the diagnostic text differs.
    let text_only = ["g2_int_node_lt_rows", "g8_map_null_key"];
    let (mut parity, mut divergent, mut text) = (0, 0, 0);
    for case in &cases {
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        let go_stdout = read("stdout");
        let go_stderr = String::from_utf8(read("stderr")).unwrap();
        let last = go_stderr.lines().last().unwrap();
        let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
        let result = session(
            true,
            ok(messages(&read("arrows"))),
            &mut stdout,
            &mut stderr,
        );
        if row_count.contains(&case.as_str()) {
            assert_eq!(last, "read end: EOF", "{case}: Go renders");
            assert!(!go_stdout.is_empty());
            // A fatal established read with arrow's (or the walker's) row
            // rule, never reader initialization.
            let reason = match case.as_str() {
                "g3_int_negative_batch_valid_nodes" => "invalid record batch length -1",
                "g4_fsl2_node_max" => {
                    "Invalid argument error: Values length 2 is less than the length \
                     (9223372036854775807) multiplied by the value size (2) for \
                     FixedSizeList(2 x Int8)"
                }
                _ => {
                    "Invalid argument error: all columns in a record batch must have \
                     the specified row count"
                }
            };
            assert_eq!(result, Err(format!("{UNKNOWN_READ}{reason}")), "{case}");
            assert!(stdout.is_empty(), "{case}");
            divergent += 1;
        } else if text_only.contains(&case.as_str()) {
            let go = last.strip_prefix("read end: ").unwrap();
            assert_ne!(go, "EOF");
            let rust = result.unwrap_err();
            assert!(
                rust.starts_with(UNKNOWN_READ) && rust != go,
                "{case}: {rust}"
            );
            assert_eq!(stdout, go_stdout, "{case}");
            text += 1;
        } else {
            assert_eq!(stdout, go_stdout, "{case}");
            match (
                last.strip_prefix("init error: "),
                last.strip_prefix("read end: "),
            ) {
                (Some(init), _) => {
                    assert_eq!(result, Err(format!("{FLIGHT_READER}{init}")), "{case}")
                }
                (_, Some("EOF")) => assert_eq!(result, Ok(()), "{case}"),
                (_, Some(read)) => assert_eq!(result, Err(read.to_owned()), "{case}"),
                _ => panic!("{case}: unexpected Go stderr {go_stderr:?}"),
            }
            parity += 1;
        }
    }
    assert_eq!((parity, divergent, text), (23, 5, 2));
}

/// A column nested `depth` lists deep around one Int8 value.
fn nested_lists(depth: usize) -> (Arc<Schema>, RecordBatch) {
    use arrow_array::{Array, Int8Array};
    use arrow_buffer::OffsetBuffer;
    let mut array: ArrayRef = Arc::new(Int8Array::from(vec![1]));
    for _ in 0..depth {
        let item = Arc::new(Field::new("item", array.data_type().clone(), true));
        array = Arc::new(ListArray::new(
            item,
            OffsetBuffer::from_lengths([1]),
            array,
            None,
        ));
    }
    let schema = Arc::new(Schema::new(vec![Field::new(
        "l",
        array.data_type().clone(),
        true,
    )]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![array]).unwrap();
    (schema, batch)
}

/// Go's 64-level read limit applies to record batches, not schemas: a deeper
/// schema initializes, a schema-only stream reconnects, and the first record
/// is fatal. Schemas beyond the verifier bound fail initialization (declared).
#[test]
fn nesting_beyond_go_read_limit_fails_at_the_first_record_only() {
    let limit = format!("{UNKNOWN_READ}{NESTING_LIMIT}");
    for depth in [64, 65, 100] {
        let (schema, batch) = nested_lists(depth);
        let data = flight_stream(&schema, [&batch]);
        let (batches, stop) = decode_all(ok(data[..1].to_vec()));
        assert_eq!(
            (batches.len(), stop),
            (0, Stop::Reconnect),
            "schema-only {depth}"
        );
        let (batches, stop) = decode_all(ok(data));
        if depth <= validation::GO_MAX_NESTING {
            assert_eq!((batches.len(), stop), (1, Stop::Reconnect), "{depth}");
        } else {
            assert!(batches.is_empty());
            assert_eq!(stop, Stop::Fatal(limit.clone().into_bytes()), "{depth}");
        }
    }
    let (schema, _) = nested_lists(VERIFIER_MAX_DEPTH);
    let message = String::from_utf8(
        initialize(&FlightData {
            data_header: schema_header(&schema),
            ..Default::default()
        })
        .err()
        .unwrap(),
    )
    .unwrap();
    assert!(
        message.starts_with(&format!("{FLIGHT_READER}{UNKNOWN_READ}")),
        "{message}"
    );
}

#[test]
fn go_nesting_counts_every_loaded_child_level() {
    use validation::type_nesting;
    let int = || DataType::Int8;
    let list = |dt: DataType| DataType::List(Arc::new(Field::new("item", dt, true)));
    let entries = DataType::Struct(
        vec![
            Field::new("key", int(), false),
            Field::new("value", list(int()), true),
        ]
        .into(),
    );
    let map = DataType::Map(Arc::new(Field::new("entries", entries, false)), false);
    // Map: entries struct, then the value list, then its items.
    assert_eq!(type_nesting(&map), 3);
    let union = DataType::Union(
        arrow_schema::UnionFields::try_new(vec![0], vec![Field::new("u", list(int()), true)])
            .unwrap(),
        arrow_schema::UnionMode::Sparse,
    );
    assert_eq!(type_nesting(&union), 2);
    let ree = DataType::RunEndEncoded(
        Arc::new(Field::new("run_ends", DataType::Int32, false)),
        Arc::new(Field::new("values", list(int()), true)),
    );
    assert_eq!(type_nesting(&ree), 2);
    // A dictionary column loads only indices; its values count separately.
    let dictionary = DataType::Dictionary(Box::new(DataType::Int32), Box::new(list(list(int()))));
    assert_eq!(type_nesting(&dictionary), 0);
    assert_eq!(type_nesting(&list(dictionary)), 1);
}

/// Dictionary values load from depth zero under the same limit, so a
/// dictionary batch whose values nest too deeply is a fatal read.
#[test]
fn dictionary_values_beyond_go_read_limit_fail_at_the_dictionary() {
    use arrow_array::Array;
    for depth in [64, 65] {
        let (_, values) = nested_lists(depth);
        let values = Arc::clone(values.column(0));
        let column = DictionaryArray::<Int32Type>::new(vec![0].into(), values);
        let schema = Arc::new(Schema::new(vec![Field::new(
            "d",
            column.data_type().clone(),
            true,
        )]));
        let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(column)]).unwrap();
        let (batches, stop) = decode_all(ok(flight_stream(&schema, [&batch])));
        if depth <= validation::GO_MAX_NESTING {
            assert_eq!((batches.len(), stop), (1, Stop::Reconnect));
        } else {
            assert!(batches.is_empty());
            assert_eq!(
                stop,
                Stop::Fatal(format!("{UNKNOWN_READ}{NESTING_LIMIT}").into_bytes())
            );
        }
    }
}

/// Hand-built schema messages for shapes arrow's writer cannot produce.
mod fb {
    use super::*;
    use flatbuffers::{FlatBufferBuilder, WIPOffset};

    pub(super) type Build<'a> = FlatBufferBuilder<'a>;
    pub(super) type Node<'a> = WIPOffset<arrow_ipc::Field<'a>>;

    fn field<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        type_type: arrow_ipc::Type,
        type_: WIPOffset<flatbuffers::UnionWIPOffset>,
        children: &[Node<'a>],
    ) -> Node<'a> {
        let name = fbb.create_string(name);
        let children = fbb.create_vector(children);
        let mut field = arrow_ipc::FieldBuilder::new(fbb);
        field.add_name(name);
        field.add_nullable(true);
        field.add_type_type(type_type);
        field.add_type_(type_);
        field.add_children(children);
        field.finish()
    }

    pub(super) fn int8<'a>(fbb: &mut Build<'a>, name: &str) -> Node<'a> {
        let mut int = arrow_ipc::IntBuilder::new(fbb);
        int.add_bitWidth(8);
        int.add_is_signed(true);
        let int = int.finish().as_union_value();
        field(fbb, name, arrow_ipc::Type::Int, int, &[])
    }

    pub(super) fn structure<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        children: &[Node<'a>],
    ) -> Node<'a> {
        let table = arrow_ipc::Struct_Builder::new(fbb)
            .finish()
            .as_union_value();
        field(fbb, name, arrow_ipc::Type::Struct_, table, children)
    }

    pub(super) fn fixed_list<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        size: i32,
        child: Node<'a>,
    ) -> Node<'a> {
        let mut list = arrow_ipc::FixedSizeListBuilder::new(fbb);
        list.add_listSize(size);
        let list = list.finish().as_union_value();
        field(fbb, name, arrow_ipc::Type::FixedSizeList, list, &[child])
    }

    pub(super) fn union<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        ids: Option<&[i32]>,
        children: &[Node<'a>],
    ) -> Node<'a> {
        let ids = ids.map(|ids| fbb.create_vector(ids));
        let mut union = arrow_ipc::UnionBuilder::new(fbb);
        union.add_mode(arrow_ipc::UnionMode::Sparse);
        if let Some(ids) = ids {
            union.add_typeIds(ids);
        }
        let union = union.finish().as_union_value();
        field(fbb, name, arrow_ipc::Type::Union, union, children)
    }

    pub(super) fn message<'a>(mut fbb: Build<'a>, fields: &[Node<'a>]) -> FlightData {
        let fields = fbb.create_vector(fields);
        let mut schema = arrow_ipc::SchemaBuilder::new(&mut fbb);
        schema.add_fields(fields);
        let schema = schema.finish().as_union_value();
        let mut message = arrow_ipc::MessageBuilder::new(&mut fbb);
        message.add_version(arrow_ipc::MetadataVersion::V5);
        message.add_header_type(MessageHeader::Schema);
        message.add_header(schema);
        let message = message.finish();
        fbb.finish(message, None);
        FlightData {
            data_header: Bytes::copy_from_slice(fbb.finished_data()),
            ..Default::default()
        }
    }
}

fn init_error(schema: FlightData) -> String {
    String::from_utf8(initialize(&schema).err().expect("schema was accepted")).unwrap()
}

#[test]
fn schema_faults_use_go_order_wrapping_and_panic_text() {
    let decode = "arrow/flight: could not create flight reader: arrow/ipc: could not decode \
                  schema from message schema: arrow/ipc: could not convert field";
    let recovered = format!("{FLIGHT_READER}{UNKNOWN_READ}");

    // An explicit union ID outside int8 is a returned error, wrapped with the
    // field path, including a nested child.
    let mut fbb = fb::Build::new();
    let child = fb::int8(&mut fbb, "i");
    let union = fb::union(&mut fbb, "u", Some(&[256]), &[child]);
    let other = fb::int8(&mut fbb, "n");
    let outer = fb::structure(&mut fbb, "s", &[other, union]);
    let first = fb::int8(&mut fbb, "a");
    assert_eq!(
        init_error(fb::message(fbb, &[first, outer])),
        format!(
            "{decode} 1 from flatbuf: arrow/ipc: could not convert field child 1: \
             arrow/ipc: could not convert field type: union type id out of bounds"
        )
    );

    // In-range negative IDs and a count mismatch are recovered panics.
    for (ids, panic) in [
        (&[-1][..], "arrow: union type code out of bounds"),
        (
            &[0, 1][..],
            "arrow: union types should have the same number of fields as type codes",
        ),
    ] {
        let mut fbb = fb::Build::new();
        let child = fb::int8(&mut fbb, "i");
        let union = fb::union(&mut fbb, "u", Some(ids), &[child]);
        let outer = fb::structure(&mut fbb, "s", &[union]);
        assert_eq!(
            init_error(fb::message(fbb, &[outer])),
            format!("{recovered}{panic}")
        );
    }

    // FixedSizeList sizes of zero or less panic at any depth, unwrapped.
    for size in [0, -1] {
        let mut fbb = fb::Build::new();
        let child = fb::int8(&mut fbb, "i");
        let list = fb::fixed_list(&mut fbb, "f", size, child);
        let outer = fb::structure(&mut fbb, "s", &[list]);
        assert_eq!(
            init_error(fb::message(fbb, &[outer])),
            format!("{recovered}arrow: invalid size")
        );
    }

    // Children convert before their parent's type: a child's returned error
    // wins over the parent's panic.
    let mut fbb = fb::Build::new();
    let child = fb::int8(&mut fbb, "i");
    let union = fb::union(&mut fbb, "u", Some(&[999]), &[child]);
    let list = fb::fixed_list(&mut fbb, "f", 0, union);
    assert_eq!(
        init_error(fb::message(fbb, &[list])),
        format!(
            "{decode} 0 from flatbuf: arrow/ipc: could not convert field child 0: \
             arrow/ipc: could not convert field type: union type id out of bounds"
        )
    );

    // Valid explicit IDs still initialize.
    let mut fbb = fb::Build::new();
    let child = fb::int8(&mut fbb, "i");
    let union = fb::union(&mut fbb, "u", Some(&[5]), &[child]);
    assert!(initialize(&fb::message(fbb, &[union])).is_ok());
}

#[test]
fn wire_run_ends_are_non_nullable_whatever_the_schema_declares() {
    let ree = DataType::RunEndEncoded(
        Arc::new(Field::new("run_ends", DataType::Int32, true)),
        Arc::new(Field::new("values", DataType::Utf8, false)),
    );
    let wire = wire_schema(&Schema::new(vec![Field::new("r", ree, true)]));
    let DataType::RunEndEncoded(ends, values) = wire.field(0).data_type() else {
        panic!("run-end encoded");
    };
    assert!(!ends.is_nullable());
    assert!(values.is_nullable());
    assert_eq!(values.data_type(), &DataType::Binary);
}

/// Schema messages with dictionary encodings, string/binary leaves and an
/// explicit byte order, for shapes arrow's writer will not produce.
mod fbx {
    use super::*;
    use flatbuffers::{FlatBufferBuilder, UnionWIPOffset, WIPOffset};

    pub(super) type Build<'a> = FlatBufferBuilder<'a>;
    pub(super) type Node<'a> = WIPOffset<arrow_ipc::Field<'a>>;

    #[derive(Clone, Copy)]
    pub(super) enum Leaf {
        Int8,
        Int32,
        Int64,
        Utf8,
        Binary,
    }

    pub(super) fn leaf<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        leaf: Leaf,
        nullable: bool,
        dictionary: Option<i64>,
    ) -> Node<'a> {
        let (type_type, table) = match leaf {
            Leaf::Int8 | Leaf::Int32 | Leaf::Int64 => {
                let mut int = arrow_ipc::IntBuilder::new(fbb);
                int.add_bitWidth(match leaf {
                    Leaf::Int8 => 8,
                    Leaf::Int32 => 32,
                    _ => 64,
                });
                int.add_is_signed(true);
                (arrow_ipc::Type::Int, int.finish().as_union_value())
            }
            Leaf::Utf8 => (
                arrow_ipc::Type::Utf8,
                arrow_ipc::Utf8Builder::new(fbb).finish().as_union_value(),
            ),
            Leaf::Binary => (
                arrow_ipc::Type::Binary,
                arrow_ipc::BinaryBuilder::new(fbb).finish().as_union_value(),
            ),
        };
        node(fbb, name, nullable, type_type, table, &[], dictionary)
    }

    pub(super) fn node<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        nullable: bool,
        type_type: arrow_ipc::Type,
        table: WIPOffset<UnionWIPOffset>,
        children: &[Node<'a>],
        dictionary: Option<i64>,
    ) -> Node<'a> {
        let dictionary = dictionary.map(|id| {
            let mut index = arrow_ipc::IntBuilder::new(fbb);
            index.add_bitWidth(32);
            index.add_is_signed(true);
            let index = index.finish();
            let mut encoding = arrow_ipc::DictionaryEncodingBuilder::new(fbb);
            encoding.add_id(id);
            encoding.add_indexType(index);
            encoding.finish()
        });
        let name = fbb.create_string(name);
        let children = fbb.create_vector(children);
        let mut field = arrow_ipc::FieldBuilder::new(fbb);
        field.add_name(name);
        field.add_nullable(nullable);
        field.add_type_type(type_type);
        field.add_type_(table);
        field.add_children(children);
        if let Some(dictionary) = dictionary {
            field.add_dictionary(dictionary);
        }
        field.finish()
    }

    pub(super) fn list<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        item: Node<'a>,
        dictionary: Option<i64>,
    ) -> Node<'a> {
        let table = arrow_ipc::ListBuilder::new(fbb).finish().as_union_value();
        node(
            fbb,
            name,
            true,
            arrow_ipc::Type::List,
            table,
            &[item],
            dictionary,
        )
    }

    pub(super) fn structure<'a>(
        fbb: &mut Build<'a>,
        name: &str,
        children: &[Node<'a>],
        dictionary: Option<i64>,
    ) -> Node<'a> {
        let table = arrow_ipc::Struct_Builder::new(fbb)
            .finish()
            .as_union_value();
        node(
            fbb,
            name,
            true,
            arrow_ipc::Type::Struct_,
            table,
            children,
            dictionary,
        )
    }

    pub(super) fn union_mode<'a>(fbb: &mut Build<'a>, mode: i16, child: Node<'a>) -> Node<'a> {
        let mut union = arrow_ipc::UnionBuilder::new(fbb);
        union.add_mode(arrow_ipc::UnionMode(mode));
        let table = union.finish().as_union_value();
        node(
            fbb,
            "u",
            true,
            arrow_ipc::Type::Union,
            table,
            &[child],
            None,
        )
    }

    pub(super) fn schema<'a>(
        mut fbb: Build<'a>,
        fields: &[Node<'a>],
        endianness: arrow_ipc::Endianness,
    ) -> FlightData {
        let fields = fbb.create_vector(fields);
        let mut schema = arrow_ipc::SchemaBuilder::new(&mut fbb);
        schema.add_fields(fields);
        schema.add_endianness(endianness);
        let schema = schema.finish().as_union_value();
        let mut message = arrow_ipc::MessageBuilder::new(&mut fbb);
        message.add_version(arrow_ipc::MetadataVersion::V5);
        message.add_header_type(MessageHeader::Schema);
        message.add_header(schema);
        let message = message.finish();
        fbb.finish(message, None);
        FlightData {
            data_header: Bytes::copy_from_slice(fbb.finished_data()),
            ..Default::default()
        }
    }
}

/// Schema initialization accepts foreign byte order; buffer normalization is
/// exercised by endian module tests before any offsets reach arrow-rs.
#[test]
fn non_native_byte_order_initializes() {
    use arrow_ipc::Endianness;
    let declared = |endianness| {
        let mut fbb = fbx::Build::new();
        let field = fbx::leaf(&mut fbb, "n", fbx::Leaf::Int64, true, None);
        fbx::schema(fbb, &[field], endianness)
    };
    let foreign = if cfg!(target_endian = "big") {
        Endianness::Little
    } else {
        Endianness::Big
    };
    assert!(Decoder::new(&declared(foreign).data_header).is_ok());
    let unknown = Decoder::new(&declared(Endianness(2)).data_header).unwrap();
    assert!(unknown.swap_endian);
    // The same schema in native order initializes and decodes.
    let mut data = int_stream(&[&[1, -2, 300]]);
    data[0] = declared(NATIVE_ENDIAN);
    let (batches, stop) = decode_all(ok(data));
    assert_eq!(stop, Stop::Reconnect);
    let values: Vec<i64> = batches[0]
        .column(0)
        .as_any()
        .downcast_ref::<Int64Array>()
        .unwrap()
        .values()
        .to_vec();
    assert_eq!(values, [1, -2, 300]);
}

/// Fields sharing a dictionary ID must have Go-equal value types (go1.25.4
/// probe, schema-only streams). Go compares the original types, before the
/// wire schema erases strings and nullability: list element names do not
/// count, list element nullability and struct field names do, and a
/// field's own nullability is not part of its value type.
#[test]
fn shared_dictionary_ids_require_go_equal_value_types() {
    use fbx::{Leaf, leaf, list, structure};
    let conflict = |field: usize, path: &str| {
        format!(
            "{FLIGHT_READER}arrow/ipc: could not decode schema from message schema: \
             arrow/ipc: could not convert field {field} from flatbuf: {path}arrow/ipc: \
             could not convert field type: arrow/ipc: conflicting dictionary types for id 0"
        )
    };
    type Shape = fn(&mut fbx::Build<'static>) -> Vec<fbx::Node<'static>>;
    let cases: [(&str, Shape, Option<String>); 9] = [
        (
            "same utf8",
            |f| {
                vec![
                    leaf(f, "a", Leaf::Utf8, true, Some(0)),
                    leaf(f, "b", Leaf::Utf8, true, Some(0)),
                ]
            },
            None,
        ),
        (
            "field nullability",
            |f| {
                vec![
                    leaf(f, "a", Leaf::Utf8, true, Some(0)),
                    leaf(f, "b", Leaf::Utf8, false, Some(0)),
                ]
            },
            None,
        ),
        (
            "list element names",
            |f| {
                let item = leaf(f, "item", Leaf::Int8, true, None);
                let a = list(f, "a", item, Some(0));
                let element = leaf(f, "element", Leaf::Int8, true, None);
                vec![a, list(f, "b", element, Some(0))]
            },
            None,
        ),
        (
            "utf8 and binary",
            |f| {
                vec![
                    leaf(f, "a", Leaf::Utf8, true, Some(0)),
                    leaf(f, "b", Leaf::Binary, true, Some(0)),
                ]
            },
            Some(conflict(1, "")),
        ),
        (
            "int32 and utf8",
            |f| {
                vec![
                    leaf(f, "a", Leaf::Int32, true, Some(0)),
                    leaf(f, "b", Leaf::Utf8, true, Some(0)),
                ]
            },
            Some(conflict(1, "")),
        ),
        (
            "list element nullability",
            |f| {
                let item = leaf(f, "item", Leaf::Int8, true, None);
                let a = list(f, "a", item, Some(0));
                let item = leaf(f, "item", Leaf::Int8, false, None);
                vec![a, list(f, "b", item, Some(0))]
            },
            Some(conflict(1, "")),
        ),
        (
            "struct field names",
            |f| {
                let x = leaf(f, "x", Leaf::Int8, true, None);
                let a = structure(f, "a", &[x], Some(0));
                let y = leaf(f, "y", Leaf::Int8, true, None);
                vec![a, structure(f, "b", &[y], Some(0))]
            },
            Some(conflict(1, "")),
        ),
        (
            "nested child",
            |f| {
                let a = leaf(f, "a", Leaf::Utf8, true, Some(0));
                let n = leaf(f, "n", Leaf::Int8, true, None);
                let c = leaf(f, "c", Leaf::Int32, true, Some(0));
                vec![a, structure(f, "s", &[n, c], None)]
            },
            Some(conflict(1, "arrow/ipc: could not convert field child 1: ")),
        ),
        (
            "child registers before its parent",
            |f| {
                let item = leaf(f, "item", Leaf::Utf8, true, Some(0));
                vec![list(f, "a", item, Some(0))]
            },
            Some(conflict(0, "")),
        ),
    ];
    for (name, shape, expected) in cases {
        let mut fbb = fbx::Build::new();
        let fields = shape(&mut fbb);
        let schema = fbx::schema(fbb, &fields, NATIVE_ENDIAN);
        match expected {
            None => assert!(initialize(&schema).is_ok(), "{name}"),
            Some(expected) => assert_eq!(init_error(schema), expected, "{name}"),
        }
    }
}

/// IA-1: initialization alone missed Arrow rejecting the dictionary's actual
/// child type at the first record. Replay the audit's complete streams and
/// actual Go output through Open, dictionary decode, record decode and render.
#[test]
fn shared_dictionary_list_names_match_go_through_the_worker() {
    let dir = fixture("arrow-go-v13-decoder-ia1");
    let cases = listed_cases(&dir);
    for case in ["list_names_same", "list_names_differ"] {
        assert!(cases.iter().any(|name| name == case));
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        let data = messages(&read("arrows"));
        assert_eq!(data.len(), 3, "{case}: schema, dictionary, record");
        let expected = read("go.stdout");
        assert_eq!(expected, b"{\"a\":[7,8],\"b\":[7,8]}\n\n");
        assert_eq!(read("go.stderr"), b"NumRows=1\nread end: EOF\n");
        assert_eq!(read("go.status"), b"0\n");
        for fatal in [false, true] {
            let mut items = ok(data.clone());
            let status = FlightStatus::new(INVALID_ARGUMENT, "final");
            if fatal {
                items.push(Err(status.clone()));
            }
            let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
            let result = session(true, items, &mut stdout, &mut stderr);
            let expected_result = if fatal {
                Err(String::from_utf8(status.go_error()).unwrap())
            } else {
                Ok(())
            };
            assert_eq!(result, expected_result, "{case}, fatal={fatal}");
            assert_eq!(stdout, expected, "{case}, fatal={fatal}");
            assert!(stderr.is_empty(), "{case}, fatal={fatal}");
        }
    }
}

/// Shared outer ID 1, with distinct nested IDs 0 and 2. The outer lists have
/// different element names and the struct's payload fields have different
/// metadata, both ignored by Go TypeEqual. Copying the first outer value type
/// wholesale would lose ID 2 and fail its dictionary message.
fn shared_dictionary_nested_stream() -> Vec<FlightData> {
    use arrow_array::{Array, Int8Array, StructArray};
    use arrow_buffer::OffsetBuffer;
    let outer = |name: &str, metadata: &str| -> ArrayRef {
        let numbers = Arc::new(ListArray::new(
            Arc::new(Field::new("item", DataType::Int8, true)),
            OffsetBuffer::new(vec![0_i32, 2].into()),
            Arc::new(Int8Array::from(vec![7_i8, 8])),
            None,
        ));
        let payload = Arc::new(DictionaryArray::<Int32Type>::new(
            vec![0_i32].into(),
            numbers,
        ));
        let fields: Fields = vec![
            Field::new("payload", payload.data_type().clone(), true)
                .with_metadata(HashMap::from([("source".to_owned(), metadata.to_owned())])),
            Field::new("text", DataType::Utf8, true),
        ]
        .into();
        let values = Arc::new(StructArray::new(
            fields,
            vec![payload, Arc::new(StringArray::from(vec!["x"]))],
            None,
        ));
        let lists = Arc::new(ListArray::new(
            Arc::new(Field::new(name, values.data_type().clone(), true)),
            OffsetBuffer::new(vec![0_i32, 1].into()),
            values,
            None,
        ));
        Arc::new(DictionaryArray::<Int32Type>::new(vec![0_i32].into(), lists))
    };
    let a = outer("item", "first");
    let b = outer("element", "second");
    let schema = Arc::new(Schema::new(vec![
        Field::new("a", a.data_type().clone(), true),
        Field::new("b", b.data_type().clone(), true),
    ]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![a, b]).unwrap();
    let mut data = flight_stream(&schema, [&batch]);
    assert_eq!(data.len(), 6);
    for (index, id) in [0, 1, 2, 3].into_iter().enumerate() {
        assert_eq!(
            ipc_message(&data[index + 1].data_header)
                .unwrap()
                .header_as_dictionary_batch()
                .unwrap()
                .id(),
            id
        );
    }
    // Rewrite only the second outer ID, preserving every child descriptor.
    let mut header = data[0].data_header.to_vec();
    let dictionary = ipc_message(&header)
        .unwrap()
        .header_as_schema()
        .unwrap()
        .fields()
        .unwrap()
        .get(1)
        .dictionary()
        .unwrap();
    assert_eq!(dictionary.id(), 3);
    let offset = usize::from(
        dictionary
            ._tab
            .vtable()
            .get(arrow_ipc::DictionaryEncoding::VT_ID),
    );
    assert_ne!(offset, 0);
    let start = dictionary._tab.loc() + offset;
    header[start..start + 8].copy_from_slice(&1_i64.to_le_bytes());
    data[0].data_header = Bytes::from(header);
    data.remove(4);
    data
}

#[test]
#[allow(deprecated)]
fn shared_dictionary_nested_ids_and_metadata_match_go_through_the_worker() {
    let data = shared_dictionary_nested_stream();
    let decoder = initialize(&data[0]).unwrap();
    assert_eq!(decoder.ids, BTreeSet::from([0, 1, 2]));
    assert_eq!(decoder.expected, 3);
    let fields = decoder.wire.fields();
    assert_eq!(fields[0].data_type(), fields[1].data_type());
    for id in [0, 2] {
        let nested = decoder.wire.fields_with_dict_id(id);
        assert_eq!(nested.len(), 1, "nested ID {id} must survive");
        assert_eq!(nested[0].name(), "payload");
        assert!(nested[0].metadata().is_empty());
        assert!(nested[0].is_nullable());
    }
    // Pinned Go v13, go1.25.4: full dictionary+record stream, EOF.
    let expected = b"{\"a\":[{\"payload\":[7,8],\"text\":\"x\"}],\"b\":[{\"payload\":[7,8],\"text\":\"x\"}]}\n\n";
    for fatal in [false, true] {
        let mut items = ok(data.clone());
        let status = FlightStatus::new(INVALID_ARGUMENT, "final");
        if fatal {
            items.push(Err(status.clone()));
        }
        let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
        let result = session(true, items, &mut stdout, &mut stderr);
        assert_eq!(
            result,
            if fatal {
                Err(String::from_utf8(status.go_error()).unwrap())
            } else {
                Ok(())
            }
        );
        assert_eq!(stdout, expected);
        assert!(stderr.is_empty());
    }
}

#[test]
fn nested_dictionary_binding_corpus_matches_go_through_the_worker() {
    let dir = fixture("arrow-go-v13-decoder-ia1");
    let cases: Vec<_> = listed_cases(&dir)
        .into_iter()
        .filter(|name| name.starts_with("nested_") || name.starts_with("flat_"))
        .collect();
    assert_eq!(cases.len(), 23);
    for case in cases {
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        let data = messages(&read("arrows"));
        let expected = read("go.stdout");
        let go_stderr = String::from_utf8(read("go.stderr")).unwrap();
        let end = go_stderr
            .lines()
            .last()
            .unwrap()
            .strip_prefix("read end: ")
            .unwrap();
        assert_eq!(read("go.status"), b"0\n");
        for fatal in [false, true] {
            let mut items = ok(data.clone());
            let status = FlightStatus::new(INVALID_ARGUMENT, "final");
            if fatal {
                items.push(Err(status.clone()));
            }
            let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
            let result = session(true, items, &mut stdout, &mut stderr);
            let expected_result = if end != "EOF" {
                Err(end.to_owned())
            } else if fatal {
                Err(String::from_utf8(status.go_error()).unwrap())
            } else {
                Ok(())
            };
            assert_eq!(stdout, expected, "{case}, fatal={fatal}, result={result:?}");
            assert_eq!(result, expected_result, "{case}, fatal={fatal}");
            assert!(stderr.is_empty());
        }
    }
}

/// Fresh Opus W-1/W-2 probes: absent IDs must fail even for null keys and
/// zero rows; the first missing ID follows schema order, not numeric order.
#[test]
fn dictionary_presence_and_order_match_the_go_wave_corpus() {
    let dir = fixture("arrow-go-v13-decoder-wave");
    let cases = listed_cases(&dir);
    assert_eq!(cases.len(), 11);
    let mut differences = Vec::new();
    for case in cases {
        let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
        let data = messages(&read("arrows"));
        let expected = read("go.stdout");
        let go_stderr = String::from_utf8(read("go.stderr")).unwrap();
        let end = go_stderr
            .lines()
            .last()
            .unwrap()
            .strip_prefix("read end: ")
            .unwrap();
        assert_eq!(read("go.status"), b"0\n");
        for fatal in [false, true] {
            let mut items = ok(data.clone());
            let status = FlightStatus::new(INVALID_ARGUMENT, "final");
            if fatal {
                items.push(Err(status.clone()));
            }
            let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
            let result = session(true, items, &mut stdout, &mut stderr);
            let expected_result = if end != "EOF" {
                Err(end.to_owned())
            } else if fatal {
                Err(String::from_utf8(status.go_error()).unwrap())
            } else {
                Ok(())
            };
            if stdout != expected || result != expected_result || !stderr.is_empty() {
                differences.push(format!(
                    "{case}, fatal={fatal}: got {result:?}, want {expected_result:?}; \
                     stdout={stdout:?}, expected={expected:?}, stderr={stderr:?}"
                ));
            }
        }
    }
    assert!(differences.is_empty(), "{}", differences.join("\n"));
}

#[test]
#[allow(deprecated)]
fn dictionary_lookup_uses_last_type_without_changing_row_paths() {
    let dir = fixture("arrow-go-v13-decoder-ia1");
    for (case, names) in [
        ("nested_distinct", ["a", "b"]),
        ("nested_fields_reverse", ["b", "a"]),
    ] {
        let data = messages(&std::fs::read(dir.join(format!("{case}.arrows"))).unwrap());
        let decoder = initialize(&data[0]).unwrap();
        assert_eq!(decoder.ids, BTreeSet::from([0, 1, 2]));
        assert_eq!(decoder.dictionary_types.len(), 3);
        let payload = |data_type: &DataType| {
            let item = type_fields(data_type)[0];
            type_fields(item.data_type())[0].dict_id()
        };
        for (index, id) in [0, 2].into_iter().enumerate() {
            let field = &decoder.wire.fields()[index];
            assert_eq!(field.name(), names[index]);
            assert_eq!(field.dict_id(), Some(1));
            assert_eq!(payload(field.data_type()), Some(id));
        }
        let layout = &decoder.dictionary_types[&1];
        assert!(layout.nested);
        assert_eq!(payload(&layout.values), Some(2));
        let DataType::Dictionary(_, keys) = layout.decoding.field(0).data_type() else {
            panic!("explicit dictionary decoding schema")
        };
        let item = type_fields(keys)[0];
        let fields = type_fields(item.data_type());
        assert_eq!(fields[0].data_type(), &DataType::Int32);
        assert_eq!(fields[1].data_type(), &DataType::Binary);
    }
}

#[test]
fn deferred_dictionary_binding_validates_indices_and_replacement_shrink() {
    let dir = fixture("arrow-go-v13-decoder-ia1");
    let original = messages(&std::fs::read(dir.join("nested_distinct.arrows")).unwrap());
    for key in [-1_i32, 2, i32::MAX] {
        let mut data = original.clone();
        let offset = ipc_message(&data[2].data_header)
            .unwrap()
            .header_as_dictionary_batch()
            .unwrap()
            .data()
            .unwrap()
            .buffers()
            .unwrap()
            .get(4)
            .offset() as usize;
        let mut body = data[2].data_body.to_vec();
        body[offset..offset + 4].copy_from_slice(&key.to_le_bytes());
        data[2].data_body = Bytes::from(body);
        // Integer placeholders decode safely; the real dictionary bound is
        // checked when a record binds the current memo, never skipped.
        let mut decoder = initialize(&data[0]).unwrap();
        for message in &data[1..4] {
            assert!(matches!(
                decoder.decode(message.clone()),
                Ok(Decoded::Dictionary)
            ));
        }
        let error = match decoder.decode(data[4].clone()) {
            Err(error) => error,
            Ok(_) => panic!("invalid nested key {key} was accepted"),
        };
        assert!(error.starts_with(UNKNOWN_READ), "{error}");
        assert!(error.contains("out of bounds"), "{error}");
        let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
        assert_eq!(
            session(true, ok(data), &mut stdout, &mut stderr),
            Err(error)
        );
        assert!(stdout.is_empty());
        assert!(stderr.is_empty());
    }
    let mut data = messages(&std::fs::read(dir.join("nested_child_delta.arrows")).unwrap());
    // Replace the child with this one-entry piece instead of appending it.
    // The next parent selects key 2, which is valid only for the true delta.
    let mut header = data[5].data_header.to_vec();
    let batch = ipc_message(&header)
        .unwrap()
        .header_as_dictionary_batch()
        .unwrap();
    assert!(batch.isDelta());
    let offset = usize::from(
        batch
            ._tab
            .vtable()
            .get(arrow_ipc::DictionaryBatch::VT_ISDELTA),
    );
    assert_ne!(offset, 0);
    let position = batch._tab.loc() + offset;
    header[position] = 0;
    data[5].data_header = Bytes::from(header);
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let error = session(true, ok(data), &mut stdout, &mut stderr).unwrap_err();
    assert!(error.starts_with(UNKNOWN_READ), "{error}");
    assert!(error.contains("out of bounds"), "{error}");
    assert_eq!(
        stdout,
        std::fs::read(dir.join("nested_distinct.go.stdout")).unwrap()
    );
    assert!(stderr.is_empty());
}

fn shared_dictionary_recursive_stream(case: &str, different: bool) -> Vec<FlightData> {
    use arrow_array::{Array, FixedSizeListArray, Int8Array, MapArray, StructArray, UnionArray};
    use arrow_buffer::OffsetBuffer;
    use arrow_schema::{UnionFields, UnionMode};
    let values = |changed: bool| -> ArrayRef {
        let name = if changed { "element" } else { "item" };
        match case {
            "fixed_size_list" => Arc::new(FixedSizeListArray::new(
                Arc::new(Field::new(name, DataType::Int8, true)),
                2,
                Arc::new(Int8Array::from(vec![7_i8, 8])),
                None,
            )),
            "map" => {
                let fields: Fields = vec![
                    Field::new(if changed { "k" } else { "key" }, DataType::Utf8, false),
                    Field::new(if changed { "v" } else { "value" }, DataType::Utf8, true),
                ]
                .into();
                let entries = StructArray::new(
                    fields,
                    vec![
                        Arc::new(StringArray::from(vec!["k"])),
                        Arc::new(StringArray::from(vec!["v"])),
                    ],
                    None,
                );
                Arc::new(MapArray::new(
                    Arc::new(Field::new(
                        if changed { "pairs" } else { "entries" },
                        entries.data_type().clone(),
                        false,
                    )),
                    OffsetBuffer::new(vec![0_i32, 1].into()),
                    entries,
                    None,
                    changed,
                ))
            }
            "union_metadata" => {
                let field =
                    Field::new("text", DataType::Utf8, true).with_metadata(HashMap::from([(
                        "source".to_owned(),
                        if changed { "second" } else { "first" }.to_owned(),
                    )]));
                let fields = UnionFields::from_iter([(3, Arc::new(field))]);
                let array = UnionArray::try_new(
                    fields,
                    vec![3_i8].into(),
                    Some(vec![0_i32].into()),
                    vec![Arc::new(StringArray::from(vec!["x"]))],
                )
                .unwrap();
                assert!(matches!(
                    array.data_type(),
                    DataType::Union(_, UnionMode::Dense)
                ));
                Arc::new(array)
            }
            _ => panic!("unknown shared dictionary case {case}"),
        }
    };
    let a = Arc::new(DictionaryArray::<Int32Type>::new(
        vec![0_i32].into(),
        values(false),
    ));
    let b = Arc::new(DictionaryArray::<Int32Type>::new(
        vec![0_i32].into(),
        values(different),
    ));
    let schema = Arc::new(Schema::new(vec![
        Field::new("a", a.data_type().clone(), true),
        Field::new("b", b.data_type().clone(), true),
    ]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![a, b]).unwrap();
    // Go v13's dictionary loader uses the pre-V5 union bitmap layout.
    // V4 is the supported full-stream control for union dictionary values.
    let options = if case == "union_metadata" {
        IpcWriteOptions::try_new(8, false, arrow_ipc::MetadataVersion::V4).unwrap()
    } else {
        IpcWriteOptions::default()
    };
    let mut data = flight_stream_with(options, &schema, [&batch]);
    assert_eq!(data.len(), 4);
    let mut header = data[0].data_header.to_vec();
    let dictionary = ipc_message(&header)
        .unwrap()
        .header_as_schema()
        .unwrap()
        .fields()
        .unwrap()
        .get(1)
        .dictionary()
        .unwrap();
    assert_eq!(dictionary.id(), 1);
    let offset = usize::from(
        dictionary
            ._tab
            .vtable()
            .get(arrow_ipc::DictionaryEncoding::VT_ID),
    );
    assert_ne!(offset, 0);
    let start = dictionary._tab.loc() + offset;
    header[start..start + 8].copy_from_slice(&0_i64.to_le_bytes());
    data[0].data_header = Bytes::from(header);
    data.remove(2);
    data
}

/// Go-equal differences are recursive and extend beyond ordinary list names.
/// Every case sends an actual dictionary and record, with an unchanged control,
/// EOF and a fatal established-stream status. Expected rows are pinned Go
/// decoder results, including its map key/value naming and union rendering.
#[test]
fn shared_dictionary_recursive_types_match_go_through_the_worker() {
    let cases: [(&str, &[u8]); 3] = [
        ("fixed_size_list", b"{\"a\":[7,8],\"b\":[7,8]}\n\n"),
        ("map", b"{\"a\":[{\"key\":\"k\",\"value\":\"v\"}],\"b\":[{\"key\":\"k\",\"value\":\"v\"}]}\n\n"),
        ("union_metadata", b"{\"a\":[3,\"x\"],\"b\":[3,\"x\"]}\n\n"),
    ];
    for (case, expected) in cases {
        for different in [false, true] {
            let data = shared_dictionary_recursive_stream(case, different);
            for fatal in [false, true] {
                let mut items = ok(data.clone());
                let status = FlightStatus::new(INVALID_ARGUMENT, "final");
                if fatal {
                    items.push(Err(status.clone()));
                }
                let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
                let result = session(true, items, &mut stdout, &mut stderr);
                assert_eq!(
                    result,
                    if fatal {
                        Err(String::from_utf8(status.go_error()).unwrap())
                    } else {
                        Ok(())
                    },
                    "{case}, different={different}, fatal={fatal}"
                );
                assert_eq!(
                    stdout, expected,
                    "{case}, different={different}, fatal={fatal}"
                );
                assert!(stderr.is_empty());
            }
        }
    }
}

/// Go leaves an unknown union mode at its zero value, which is neither of its
/// modes, and panics building the type after the type-ID range check.
#[test]
fn unknown_union_modes_use_go_panic_text() {
    let mut fbb = fbx::Build::new();
    let child = fbx::leaf(&mut fbb, "i", fbx::Leaf::Int8, true, None);
    let union = fbx::union_mode(&mut fbb, 7, child);
    assert_eq!(
        init_error(fbx::schema(fbb, &[union], NATIVE_ENDIAN)),
        format!("{FLIGHT_READER}{UNKNOWN_READ}arrow: invalid union mode")
    );
}

/// An Int32 field whose flatbuffer carries `children`, which Go converts
/// (registering their dictionaries) and then discards with the leaf type.
fn int32_with<'a>(
    fbb: &mut fbx::Build<'a>,
    name: &str,
    children: &[fbx::Node<'a>],
    dictionary: Option<i64>,
) -> fbx::Node<'a> {
    let mut int = arrow_ipc::IntBuilder::new(fbb);
    int.add_bitWidth(32);
    int.add_is_signed(true);
    let table = int.finish().as_union_value();
    fbx::node(
        fbb,
        name,
        true,
        arrow_ipc::Type::Int,
        table,
        children,
        dictionary,
    )
}

/// The same dictionary record under another ID and delta flag.
fn dictionary_as(source: &FlightData, id: i64, delta: bool) -> FlightData {
    let message = ipc_message(&source.data_header).unwrap();
    let record = message
        .header_as_dictionary_batch()
        .unwrap()
        .data()
        .unwrap();
    assert!(record.compression().is_none());
    let nodes: Vec<_> = record.nodes().unwrap().iter().copied().collect();
    let buffers: Vec<_> = record.buffers().unwrap().iter().copied().collect();
    let mut fbb = flatbuffers::FlatBufferBuilder::new();
    let nodes = fbb.create_vector(&nodes);
    let buffers = fbb.create_vector(&buffers);
    let data = arrow_ipc::RecordBatch::create(
        &mut fbb,
        &arrow_ipc::RecordBatchArgs {
            length: record.length(),
            nodes: Some(nodes),
            buffers: Some(buffers),
            ..Default::default()
        },
    );
    let batch = arrow_ipc::DictionaryBatch::create(
        &mut fbb,
        &arrow_ipc::DictionaryBatchArgs {
            id,
            data: Some(data),
            isDelta: delta,
        },
    );
    let header = arrow_ipc::Message::create(
        &mut fbb,
        &arrow_ipc::MessageArgs {
            version: message.version(),
            header_type: MessageHeader::DictionaryBatch,
            header: Some(batch.as_union_value()),
            bodyLength: message.bodyLength(),
            custom_metadata: None,
        },
    );
    fbb.finish(header, None);
    FlightData {
        data_header: Bytes::copy_from_slice(fbb.finished_data()),
        data_body: source.data_body.clone(),
        ..Default::default()
    }
}

fn dictionary_id(data: &FlightData) -> i64 {
    ipc_message(&data.data_header)
        .unwrap()
        .header_as_dictionary_batch()
        .unwrap()
        .id()
}

/// A Utf8 dictionary message (ID 0) holding `values`.
fn utf8_dictionary(values: Vec<Option<&str>>) -> FlightData {
    use arrow_array::Array;
    let column =
        DictionaryArray::<Int32Type>::new(vec![0].into(), Arc::new(StringArray::from(values)));
    let schema = Arc::new(Schema::new(vec![Field::new(
        "c",
        column.data_type().clone(),
        true,
    )]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![Arc::new(column)]).unwrap();
    let data = flight_stream(&schema, [&batch]);
    assert_eq!(dictionary_id(&data[1]), 0);
    data[1].clone()
}

/// The record message of one Int32 column `a` = [1, 2].
fn int32_record() -> FlightData {
    use arrow_array::Int32Array;
    let schema = Arc::new(Schema::new(vec![Field::new("a", DataType::Int32, true)]));
    let column = Arc::new(Int32Array::from(vec![1, 2]));
    let batch = RecordBatch::try_new(Arc::clone(&schema), vec![column]).unwrap();
    flight_stream(&schema, [&batch])[1].clone()
}

/// Arrow-written p: Dictionary<Struct<x: Dictionary<Utf8>>> (child Q is ID 0
/// = ["a", "b"], parent P is ID 1 with x keys [0, 1], p keys [0, 0]) and
/// a: Int32 = [1, 2]: schema, Q, P, record.
fn nested_parent_stream() -> Vec<FlightData> {
    use arrow_array::{Array, Int32Array, StructArray};
    let q = DictionaryArray::<Int32Type>::new(
        vec![0, 1].into(),
        Arc::new(StringArray::from(vec!["a", "b"])),
    );
    let x = Arc::new(Field::new("x", q.data_type().clone(), true));
    let p_values = StructArray::from(vec![(x, Arc::new(q) as ArrayRef)]);
    let p = DictionaryArray::<Int32Type>::new(vec![0, 0].into(), Arc::new(p_values));
    let schema = Arc::new(Schema::new(vec![
        Field::new("p", p.data_type().clone(), true),
        Field::new("a", DataType::Int32, true),
    ]));
    let batch = RecordBatch::try_new(
        Arc::clone(&schema),
        vec![Arc::new(p), Arc::new(Int32Array::from(vec![1, 2]))],
    )
    .unwrap();
    let data = flight_stream(&schema, [&batch]);
    assert_eq!(data.len(), 4);
    assert_eq!((dictionary_id(&data[1]), dictionary_id(&data[2])), (0, 1));
    data
}

/// Go's flatbuffer field for p: Struct<x: Utf8 dictionary `child`>, itself
/// dictionary 1.
fn parent_field<'a>(fbb: &mut fbx::Build<'a>, name: &str, child: i64) -> fbx::Node<'a> {
    let x = fbx::leaf(fbb, "x", fbx::Leaf::Utf8, true, Some(child));
    fbx::structure(fbb, name, &[x], Some(1))
}

fn rendered(data: Vec<FlightData>) -> (Result<(), String>, String) {
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let result = session(true, ok(data), &mut stdout, &mut stderr);
    assert!(stderr.is_empty());
    (result, String::from_utf8(stdout).unwrap())
}

/// PR #4 decoder review o1/o2 and the Go-contract follow-up, all probed with
/// the Go v13 decoder oracle (harness/decoder-oracle, Go 1.25.4): Go converts
/// a leaf field's flatbuffer children, so their dictionary IDs count towards
/// the initial dictionaries and their messages load, but no record resolves
/// them. Each case ends in EOF with exit 0 in Go.
#[test]
fn leaf_child_dictionaries_load_but_records_never_resolve_them() {
    const ROWS: &str = "{\"a\":1}\n{\"a\":2}\n\n";
    // o1 control: no child.
    let mut fbb = fbx::Build::new();
    let a = int32_with(&mut fbb, "a", &[], None);
    let control = fbx::schema(fbb, &[a], NATIVE_ENDIAN);
    assert_eq!(
        rendered(vec![control, int32_record()]),
        (Ok(()), ROWS.into())
    );
    // o1: a's child c is dictionary 0, which only a dictionary message uses.
    let mut fbb = fbx::Build::new();
    let c = fbx::leaf(&mut fbb, "c", fbx::Leaf::Utf8, true, Some(0));
    let a = int32_with(&mut fbb, "a", &[c], None);
    let schema = fbx::schema(fbb, &[a], NATIVE_ENDIAN);
    let decoder = initialize(&schema).unwrap();
    assert_eq!((decoder.expected, decoder.dictionary_order.len()), (1, 0));
    let x = utf8_dictionary(vec![Some("x")]);
    assert_eq!(
        rendered(vec![schema, x.clone(), int32_record()]),
        (Ok(()), ROWS.into())
    );

    // o2 and its control: a column d with dictionary 0 = ["x"].
    let two = {
        use arrow_array::Array;
        let column = DictionaryArray::<Int32Type>::new(
            vec![0, 0].into(),
            Arc::new(StringArray::from(vec!["x"])),
        );
        let schema = Arc::new(Schema::new(vec![
            Field::new("a", DataType::Int32, true),
            Field::new("d", column.data_type().clone(), true),
        ]));
        let a = Arc::new(arrow_array::Int32Array::from(vec![1, 2]));
        let batch = RecordBatch::try_new(Arc::clone(&schema), vec![a, Arc::new(column)]).unwrap();
        flight_stream(&schema, [&batch])
    };
    const TWO: &str = "{\"a\":1,\"d\":\"x\"}\n{\"a\":2,\"d\":\"x\"}\n\n";
    let mut fbb = fbx::Build::new();
    let a = int32_with(&mut fbb, "a", &[], None);
    let d = fbx::leaf(&mut fbb, "d", fbx::Leaf::Utf8, true, Some(0));
    let control = fbx::schema(fbb, &[a, d], NATIVE_ENDIAN);
    assert_eq!(
        rendered(vec![control, two[1].clone(), two[2].clone()]),
        (Ok(()), TWO.into())
    );
    // o2: a's child c is dictionary 1 and is never sent. Two messages for
    // dictionary 0 meet the initial count of two.
    let mut fbb = fbx::Build::new();
    let c = fbx::leaf(&mut fbb, "c", fbx::Leaf::Utf8, true, Some(1));
    let a = int32_with(&mut fbb, "a", &[c], None);
    let d = fbx::leaf(&mut fbb, "d", fbx::Leaf::Utf8, true, Some(0));
    let schema = fbx::schema(fbb, &[a, d], NATIVE_ENDIAN);
    let decoder = initialize(&schema).unwrap();
    assert_eq!(decoder.expected, 2);
    assert_eq!(decoder.dictionary_order, [0]);
    let data = vec![schema, two[1].clone(), two[1].clone(), two[2].clone()];
    assert_eq!(rendered(data), (Ok(()), TWO.into()));

    // orphan_nested_unsent_child: a's child is P = Struct<x: Q0>, dictionary
    // 1. P arrives twice, Q never; a nested layout elsewhere must not make
    // the record resolve P or Q.
    let nested = nested_parent_stream();
    let mut fbb = fbx::Build::new();
    let p = parent_field(&mut fbb, "p", 0);
    let a = int32_with(&mut fbb, "a", &[p], None);
    let schema = fbx::schema(fbb, &[a], NATIVE_ENDIAN);
    let decoder = initialize(&schema).unwrap();
    assert_eq!(decoder.expected, 2);
    assert!(decoder.dictionary_types[&1].nested && !decoder.nested);
    let data = vec![schema, nested[2].clone(), nested[2].clone(), int32_record()];
    assert_eq!(rendered(data), (Ok(()), ROWS.into()));

    // orphan_type_conflict: an orphan still takes part in Go's conflict check.
    let mut fbb = fbx::Build::new();
    let x = fbx::leaf(&mut fbb, "x", fbx::Leaf::Utf8, true, Some(0));
    let a = int32_with(&mut fbb, "a", &[x], None);
    let b = int32_with(&mut fbb, "b", &[], Some(0));
    assert_eq!(
        init_error(fbx::schema(fbb, &[a, b], NATIVE_ENDIAN)),
        format!(
            "{SCHEMA_DECODE}arrow/ipc: could not convert field 1 from flatbuf: arrow/ipc: \
             could not convert field type: arrow/ipc: conflicting dictionary types for id 0"
        )
    );
}

const SCHEMA_DECODE: &str = "arrow/flight: could not create flight reader: arrow/ipc: could not decode schema from message schema: ";

/// orphan_parent_last_live_binding (Go v13 oracle): column p is P (ID 1) =
/// Struct<x: Q0>; a later leaf child is a compatible P whose x is Q2. Go's
/// memo loads P with the last raw type, but binds P's child through column
/// p's own path, so x is Q0 = ["a", "b"], never Q2 = ["x", "y"]. Both cases
/// end in EOF with exit 0 in Go.
#[test]
fn orphan_occurrences_never_change_nested_binding() {
    let nested = nested_parent_stream();
    let mut fbb = fbx::Build::new();
    let p = parent_field(&mut fbb, "p", 0);
    let orphan = parent_field(&mut fbb, "o", 2);
    let a = int32_with(&mut fbb, "a", &[orphan], None);
    let schema = fbx::schema(fbb, &[p, a], NATIVE_ENDIAN);
    let decoder = initialize(&schema).unwrap();
    assert_eq!(decoder.expected, 3);
    assert_eq!(decoder.dictionary_order, [1, 0]);
    let xy = dictionary_as(&utf8_dictionary(vec![Some("x"), Some("y")]), 2, false);
    let data = vec![
        schema,
        nested[1].clone(),
        xy,
        nested[2].clone(),
        nested[3].clone(),
    ];
    const ROWS: &str = "{\"a\":1,\"p\":{\"x\":\"a\"}}\n{\"a\":2,\"p\":{\"x\":\"a\"}}\n\n";
    assert_eq!(rendered(data), (Ok(()), ROWS.into()));

    // Binding the nested column resolves only what it reaches: orphan ID 2
    // is never sent, and a repeated Q meets the initial count of three.
    let mut fbb = fbx::Build::new();
    let p = parent_field(&mut fbb, "p", 0);
    let c = fbx::leaf(&mut fbb, "c", fbx::Leaf::Utf8, true, Some(2));
    let a = int32_with(&mut fbb, "a", &[c], None);
    let schema = fbx::schema(fbb, &[p, a], NATIVE_ENDIAN);
    let data = vec![
        schema,
        nested[1].clone(),
        nested[2].clone(),
        nested[1].clone(),
        nested[3].clone(),
    ];
    assert_eq!(rendered(data), (Ok(()), ROWS.into()));
}

/// Go accepts deltas and replacements for orphan dictionaries and, since no
/// record reifies them, never concatenates them; a nested delta (which Go
/// cannot reify) is harmless too. Orphans of orphans count as well. A delta
/// before any dictionary for its ID still fails, as Go's AddDelta panics.
/// Go v13 oracle: rows, EOF, exit 0; and the recovered delta panic.
#[test]
fn orphan_dictionary_deltas_and_nested_orphans_follow_go() {
    const ROWS: &str = "{\"a\":1}\n{\"a\":2}\n\n";
    let nested = nested_parent_stream();
    let schema = || {
        let mut fbb = fbx::Build::new();
        let p = parent_field(&mut fbb, "p", 0);
        let c = fbx::leaf(&mut fbb, "c", fbx::Leaf::Utf8, true, Some(2));
        let b = int32_with(&mut fbb, "b", &[c], None);
        let a = int32_with(&mut fbb, "a", &[p, b], None);
        fbx::schema(fbb, &[a], NATIVE_ENDIAN)
    };
    let decoder = initialize(&schema()).unwrap();
    assert_eq!(decoder.ids, BTreeSet::from([0, 1, 2]));
    assert!(decoder.dictionary_order.is_empty());
    let q = nested[1].clone();
    let p = nested[2].clone();
    let c = dictionary_as(&utf8_dictionary(vec![Some("x")]), 2, false);
    let c_delta = dictionary_as(&c, 2, true);
    let p_delta = dictionary_as(&p, 1, true);
    let data = vec![
        schema(),
        q,
        p.clone(),
        c,
        p_delta.clone(),
        c_delta.clone(),
        int32_record(),
        p_delta,
        c_delta.clone(),
        p,
        int32_record(),
    ];
    assert_eq!(rendered(data), (Ok(()), format!("{ROWS}{ROWS}")));

    let data = vec![schema(), c_delta, int32_record()];
    assert_eq!(
        rendered(data),
        (
            Err(format!(
                "{UNKNOWN_READ}arrow/ipc: adding delta to non-existing id=2"
            )),
            String::new()
        )
    );
}

/// Orphan dictionary messages get the same safety checks as any other: an
/// empty validity bitmap under a null would otherwise trip an arrow assert.
/// The Go v13 oracle renders this stream, since no record reads the orphan;
/// rejecting it is the declared malformed-data divergence, as for a column
/// dictionary's unreferenced entries.
#[test]
fn orphan_dictionary_messages_are_validated() {
    let mut fbb = fbx::Build::new();
    let c = fbx::leaf(&mut fbb, "c", fbx::Leaf::Utf8, true, Some(0));
    let a = int32_with(&mut fbb, "a", &[c], None);
    let schema = fbx::schema(fbb, &[a], NATIVE_ENDIAN);
    let mut values = utf8_dictionary(vec![Some("a"), None]);
    patch(&mut values, Slot::BufferLength(0), 0);
    rejected(
        vec![schema, values, int32_record()],
        "validity bitmap is shorter than 2 rows",
    );
}

/// Orphans go through arrow-rs's own field conversion via a copied schema
/// table, so every type Go admits converts to exactly the column type, and
/// conversion errors stay initialization errors.
#[test]
#[allow(deprecated)]
fn orphan_conversion_matches_column_conversion_for_every_type() {
    use arrow_schema::{IntervalUnit, TimeUnit, UnionFields, UnionMode};
    let dictionary = |values| DataType::Dictionary(Box::new(DataType::Int16), Box::new(values));
    let item = |data_type| Arc::new(Field::new("item", data_type, true));
    let entries = Field::new(
        "entries",
        DataType::Struct(
            vec![
                Field::new("key", DataType::Utf8, false),
                Field::new("value", dictionary(DataType::LargeUtf8), true),
            ]
            .into(),
        ),
        false,
    );
    let types = vec![
        DataType::Null,
        DataType::Boolean,
        DataType::Int8,
        DataType::UInt64,
        DataType::Float16,
        DataType::Float64,
        DataType::Utf8,
        DataType::LargeUtf8,
        DataType::Binary,
        DataType::LargeBinary,
        DataType::FixedSizeBinary(3),
        DataType::Decimal128(38, -2),
        DataType::Decimal256(76, 10),
        DataType::Date32,
        DataType::Date64,
        DataType::Time32(TimeUnit::Millisecond),
        DataType::Time64(TimeUnit::Nanosecond),
        DataType::Timestamp(TimeUnit::Microsecond, None),
        DataType::Timestamp(TimeUnit::Second, Some("+01:00".into())),
        DataType::Interval(IntervalUnit::YearMonth),
        DataType::Interval(IntervalUnit::DayTime),
        DataType::Interval(IntervalUnit::MonthDayNano),
        DataType::Duration(TimeUnit::Second),
        DataType::List(item(dictionary(DataType::Utf8))),
        DataType::LargeList(item(DataType::Int32)),
        DataType::FixedSizeList(item(DataType::Int64), 2),
        DataType::Map(Arc::new(entries), true),
        DataType::Struct(
            vec![
                Field::new("s", DataType::Utf8, false),
                Field::new("t", dictionary(DataType::Int64), true),
            ]
            .into(),
        ),
        DataType::Union(
            UnionFields::try_new(
                vec![3, 7],
                vec![
                    Field::new("u", DataType::Int32, true),
                    Field::new("v", DataType::Utf8, true),
                ],
            )
            .unwrap(),
            UnionMode::Dense,
        ),
        DataType::Union(
            UnionFields::try_new(vec![0], vec![Field::new("w", DataType::Int8, true)]).unwrap(),
            UnionMode::Sparse,
        ),
        DataType::RunEndEncoded(
            Arc::new(Field::new("run_ends", DataType::Int32, false)),
            Arc::new(Field::new("values", DataType::Utf8, true)),
        ),
        dictionary(DataType::Struct(
            vec![Field::new("x", dictionary(DataType::Utf8), true)].into(),
        )),
    ];
    let fields: Vec<_> = types
        .into_iter()
        .enumerate()
        .map(|(index, data_type)| Field::new(format!("f{index}"), data_type, index % 2 == 0))
        .collect();
    let header = schema_header(&Schema::new(fields));
    let message = ipc_message(&header).unwrap();
    let schema = message.header_as_schema().unwrap();
    let columns = arrow_ipc::convert::try_fb_to_schema(schema).unwrap();
    let raw: Vec<_> = schema.fields().unwrap().iter().collect();
    let orphans = orphan_schema(&raw, schema.endianness()).unwrap();
    assert_eq!(orphans.fields().len(), columns.fields().len());
    for (orphan, column) in orphans.fields().iter().zip(columns.fields()) {
        assert_eq!(orphan, column);
        assert_eq!(orphan.dict_id(), column.dict_id());
    }

    // An orphan's time unit is checked when the schema is read, as Go does
    // (oracle: "Duration type with 9 unit not implemented"); the text is
    // arrow's, as for column fields.
    let mut fbb = fbx::Build::new();
    let mut duration = arrow_ipc::DurationBuilder::new(&mut fbb);
    duration.add_unit(arrow_ipc::TimeUnit(9));
    let table = duration.finish().as_union_value();
    let t = fbx::node(
        &mut fbb,
        "t",
        true,
        arrow_ipc::Type::Duration,
        table,
        &[],
        None,
    );
    let a = int32_with(&mut fbb, "a", &[t], None);
    let error = init_error(fbx::schema(fbb, &[a], NATIVE_ENDIAN));
    assert!(error.starts_with(SCHEMA_DECODE), "{error}");
    assert!(error.contains("Duration type with unit"), "{error}");
}

/// The diagnostics in-flight guard spans the real Open handoff: it is held
/// while the worker is inside schema initialization and released by the
/// acknowledgement. The counter is process-global, so this test reruns
/// itself alone in a child process.
#[cfg(feature = "diagnostics")]
#[test]
fn inflight_guard_spans_the_real_open_handoff() {
    use crate::tail::resources;
    const CHILD: &str = "PB_TAIL_INFLIGHT_GUARD_CHILD";
    const NAME: &str = "tail::session::tests::inflight_guard_spans_the_real_open_handoff";
    if std::env::var_os(CHILD).is_none() {
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", NAME, "--test-threads=1", "--nocapture"])
            .env(CHILD, "1")
            .output()
            .unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(
            output.status.success() && stdout.contains("1 passed"),
            "{stdout}\n{}",
            String::from_utf8_lossy(&output.stderr)
        );
        return;
    }
    resources::reset(true);
    resources::subscription();
    let (entered, wait_entered) = mpsc::channel();
    let (release, wait_release) = mpsc::channel();
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let held = std::thread::scope(|scope| {
        let (mut handoff, inbox, acks) = Handoff::new();
        let (out, err) = (&mut stdout, &mut stderr);
        let worker = scope.spawn(move || {
            let mut worker = Worker::new(out, err, "d", true);
            worker.open_gate = Some((entered, wait_release));
            worker.run(inbox, acks)
        });
        let controller = scope.spawn(move || {
            wait_entered.recv_timeout(Duration::from_secs(5)).unwrap();
            // The driver takes the guard right after sending; allow for its
            // scheduling, but the worker is still held inside Open.
            let deadline = std::time::Instant::now() + Duration::from_secs(2);
            let mut held = resources::current_inflight();
            while held != 1 && std::time::Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(5));
                held = resources::current_inflight();
            }
            release.send(()).unwrap();
            held
        });
        let completed = block_on(async {
            let Ok((stream, decoder)) =
                subscribe(stream::iter(ok(int_stream(&[&[1]]))), &mut handoff).await
            else {
                panic!("reader initialization failed");
            };
            pump(stream, decoder, &mut handoff).await.is_ok()
        });
        assert!(completed);
        drop(handoff);
        worker.join().unwrap();
        controller.join().unwrap()
    });
    assert_eq!(held, 1, "guard not held during the Open handoff");
    assert_eq!(resources::current_inflight(), 0, "guard outlived its ack");
    let snapshot = serde_json::to_value(resources::finish().unwrap()).unwrap();
    assert_eq!(snapshot["max_application_messages_inflight"], 1);
    assert_eq!(stdout, b"{\"n\":1}\n\n");
}

#[test]
fn logical_endian_schema_dictionary_ids_use_the_patched_copy() {
    let mut b = fbx::Build::new();
    let field = fbx::leaf(&mut b, "d", fbx::Leaf::Utf8, true, Some(1));
    let data = fbx::schema(b, &[field], arrow_ipc::Endianness::Big);
    let mut header = data.data_header.to_vec();
    let schema = ipc_message(&header).unwrap().header_as_schema().unwrap();
    let dictionary = schema.fields().unwrap().get(0).dictionary().unwrap();
    let dictionary_id_at = dictionary._tab.loc()
        + usize::from(
            dictionary
                ._tab
                .vtable()
                .get(arrow_ipc::DictionaryEncoding::VT_ID),
        );
    let schema_at = schema._tab.loc();
    // FlatBuffers permits field storage to overlap other verified tables.
    // Make endianness alias the dictionary ID's low two bytes (currently 1).
    let vtable_distance = i32::from_le_bytes(header[schema_at..schema_at + 4].try_into().unwrap());
    let vtable_at = (schema_at as isize - vtable_distance as isize) as usize;
    let entry = vtable_at + usize::from(arrow_ipc::Schema::VT_ENDIANNESS);
    header[entry..entry + 2].copy_from_slice(
        &u16::try_from(dictionary_id_at - schema_at)
            .unwrap()
            .to_le_bytes(),
    );
    let original = ipc_message(&header).unwrap().header_as_schema().unwrap();
    assert_eq!(original.endianness(), arrow_ipc::Endianness::Big);
    assert_eq!(ipc_dictionary_ids(&original).0, BTreeSet::from([1]));
    let decoder = Decoder::new(&header).unwrap();
    assert_eq!(decoder.ids, BTreeSet::from([0]));
    assert!(decoder.dictionary_types.contains_key(&0));
    assert!(!decoder.dictionary_types.contains_key(&1));
}
