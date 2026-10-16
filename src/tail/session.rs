//! One tail session: discovery, then repeated DoGet subscriptions.
//!
//! The current-thread runtime only drives HTTP/2 and classifies stream reads.
//! A scoped worker thread owns IPC schema, dictionary and record decoding,
//! rendering and every stdout/stderr write, so neither a wide schema, a large
//! (possibly compressed) batch nor a blocked sink stops the runtime from
//! servicing PING, SETTINGS or flow
//! control. Exactly one event is outstanding: the driver awaits the worker's
//! acknowledgement before it reads the next Flight message, and both channels
//! have capacity one. That bounds messages and batches in flight, not bytes.
//!
//! Retained owners, none accumulating across batches:
//! - transport: the HTTP/2 stream window and the current gRPC frame;
//! - driver: at most one received `FlightData` (header and body `Bytes`);
//! - `Decoder` (one per subscription, dropped at reconnect): schema, renderer
//!   plan and the dictionaries, whose deltas can grow without bound for the
//!   life of a subscription;
//! - worker: one decoded `RecordBatch` until rendered, then the staging buffer.
//!   Decompression and output expansion can exceed the encoded message size.

use super::{
    transport::{self, FlightStatus},
    validation,
    values::{BatchRenderer, RenderError},
};
use crate::{ErrorCode, config::ProfileStore, error::CliError};
use arrow_array::{ArrayRef, RecordBatch};
use arrow_buffer::Buffer;
use arrow_flight::FlightData;
use arrow_ipc::MessageHeader;
use arrow_schema::{DataType, Field, FieldRef, Fields, Schema, SchemaRef};
use futures_util::{Stream, StreamExt};
use std::{
    collections::{BTreeSet, HashMap},
    io::Write,
    sync::{Arc, mpsc},
    time::Duration,
};
use tokio::sync::mpsc as async_mpsc;

const CLEAR: &[u8] = b"\r\x1b[K";
const RECONNECT_DELAY: Duration = Duration::from_millis(500);
const SPINNER_FRAME: Duration = Duration::from_millis(100);
/// The staging buffer keeps its capacity between batches unless one batch
/// expanded it beyond this size; then the allocation is released so a single
/// burst is not retained for the rest of the session.
const RETAINED_STAGING: usize = 4 << 20;
const FLIGHT_READER: &str = "arrow/flight: could not create flight reader: ";
const UNKNOWN_READ: &str = "arrow/ipc: unknown error while reading: ";
const NESTING_LIMIT: &str = "arrow/ipc: nested type limit reached";
#[path = "endian.rs"]
mod endian;
const NATIVE_ENDIAN: arrow_ipc::Endianness = if cfg!(target_endian = "big") {
    arrow_ipc::Endianness::Big
} else {
    arrow_ipc::Endianness::Little
};
/// Flatbuffer verification depth for every IPC message header. Go reads
/// schemas of any depth but loads arrays only to 64 child levels; this bound
/// admits every schema Go can render (each nested field costs one or two
/// table levels) while bounding recursive schema conversion. Deeper schemas
/// fail initialization here, where Go would initialize: a declared bound.
/// Table count and apparent size keep the verifier defaults.
const VERIFIER_MAX_DEPTH: usize = 128;
const SCHEMA_READ: &str =
    "arrow/flight: could not create flight reader: arrow/ipc: could not read message schema: ";

pub(super) enum Terminal {
    Failed(CliError),
    Panicked(String),
}

/// Stream and decoder failures are command failures: Go's errorDetails has
/// no gRPC or IPC branch.
fn failed(message: Vec<u8>) -> Terminal {
    let text = crate::error::go_string(&message);
    Terminal::Failed(CliError::new(ErrorCode::CommandFailed, text).with_raw_message(message))
}

pub(super) fn run(
    store: &ProfileStore,
    dataset: &[u8],
    json: bool,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
) -> Terminal {
    let runtime = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => return failed(error.to_string().into_bytes()),
    };
    std::thread::scope(|scope| {
        let (handoff, inbox, acks) = Handoff::new();
        let worker = scope.spawn(move || {
            #[cfg(feature = "diagnostics")]
            let _diagnostic_thread = crate::diagnostics::ThreadSpan::begin("tail_worker");
            Worker::new(stdout, stderr, dataset, json).run(inbox, acks)
        });
        let terminal = runtime.block_on(drive(store, dataset, json, handoff));
        // The driver dropped the handoff, so the worker has completed its last
        // acknowledged event and observes disconnection.
        if let Err(panic) = worker.join() {
            std::panic::resume_unwind(panic);
        }
        terminal
    })
}

/// Requests from the driver to the worker, in Go's output order.
enum Event {
    Spin,
    StopSpin,
    Watching,
    Clear,
    /// Reader initialization: verify and convert the schema message and plan
    /// its rendering. A large schema costs CPU, so this runs on the worker.
    Open(FlightData),
    /// Decode one established-stream message; render it if it is a batch.
    Message {
        decoder: Box<Decoder>,
        data: FlightData,
    },
}

enum Ack {
    Written,
    Opened(Result<Box<Decoder>, Vec<u8>>),
    /// The subscription's decoder returns to its owner with the result.
    Processed(Box<Decoder>, Result<(), String>),
    Panicked(String),
}

struct Handoff {
    events: mpsc::SyncSender<Event>,
    acks: async_mpsc::Receiver<Ack>,
}

/// The worker only disappears by panicking; `run` re-raises that panic after
/// join, so this placeholder is never rendered.
fn worker_stopped() -> Terminal {
    failed(b"tail output worker stopped".to_vec())
}

impl Handoff {
    fn new() -> (Self, mpsc::Receiver<Event>, async_mpsc::Sender<Ack>) {
        let (events, inbox) = mpsc::sync_channel(1);
        let (acks, outbox) = async_mpsc::channel(1);
        (
            Self {
                events,
                acks: outbox,
            },
            inbox,
            acks,
        )
    }

    /// Every event is acknowledged before the next is sent, so the capacity-one
    /// send never blocks the runtime. Awaiting the acknowledgement yields, which
    /// keeps the connection task serviced while the worker blocks.
    async fn deliver(&mut self, event: Event) -> Result<Ack, Terminal> {
        #[cfg(feature = "diagnostics")]
        let observes_message = matches!(&event, Event::Open(_) | Event::Message { .. });
        self.events.try_send(event).map_err(|_| worker_stopped())?;
        #[cfg(feature = "diagnostics")]
        let _inflight = if observes_message {
            super::resources::inflight()
        } else {
            None
        };
        match self.acks.recv().await {
            Some(Ack::Panicked(message)) => Err(Terminal::Panicked(message)),
            Some(ack) => Ok(ack),
            None => Err(worker_stopped()),
        }
    }

    async fn status(&mut self, json: bool, event: Event) -> Result<(), Terminal> {
        if !json {
            self.deliver(event).await?;
        }
        Ok(())
    }

    /// Go clears the status line before returning a stream-stage error.
    async fn fail(&mut self, json: bool, message: Vec<u8>) -> Terminal {
        match self.status(json, Event::Clear).await {
            Ok(()) => failed(message),
            Err(terminal) => terminal,
        }
    }
}

async fn drive(store: &ProfileStore, dataset: &[u8], json: bool, mut handoff: Handoff) -> Terminal {
    if let Err(terminal) = handoff.status(json, Event::Spin).await {
        return terminal;
    }
    let discovered = transport::discover(store, dataset).await;
    // Go stops (and clears) the spinner before inspecting the discovery result.
    if let Err(terminal) = handoff.status(json, Event::StopSpin).await {
        return terminal;
    }
    let mut connection = match discovered {
        Ok(connection) => connection,
        Err(error) => return Terminal::Failed(error),
    };
    if let Err(terminal) = handoff.status(json, Event::Watching).await {
        return terminal;
    }
    loop {
        #[cfg(feature = "diagnostics")]
        super::resources::subscription();
        // DoGet and reader initialization are fatal for every status, including
        // the codes that end an established subscription.
        let stream = match connection.do_get().await {
            Ok(stream) => stream,
            Err(status) => return handoff.fail(json, status.go_error()).await,
        };
        let (stream, decoder) = match subscribe(Box::pin(stream), &mut handoff).await {
            Ok(subscribed) => subscribed,
            Err(Exit::Fatal(message)) => return handoff.fail(json, message).await,
            Err(Exit::Terminal(terminal)) => return terminal,
        };
        match pump(stream, decoder, &mut handoff).await {
            Ok(()) => {}
            Err(Exit::Fatal(message)) => return handoff.fail(json, message).await,
            Err(Exit::Terminal(terminal)) => return terminal,
        }
        // The stream and decoder state were dropped with the subscription.
        if let Err(terminal) = handoff.status(json, Event::Watching).await {
            return terminal;
        }
        tokio::time::sleep(RECONNECT_DELAY).await;
    }
}

enum Exit {
    Fatal(Vec<u8>),
    Terminal(Terminal),
}

/// Reader initialization: read the schema message here, then verify and
/// convert it on the worker as one acknowledged event.
async fn subscribe<S>(stream: S, handoff: &mut Handoff) -> Result<(S, Box<Decoder>), Exit>
where
    S: Stream<Item = Result<FlightData, FlightStatus>> + Unpin,
{
    let (stream, schema) = open(stream).await.map_err(Exit::Fatal)?;
    let ack = handoff.deliver(Event::Open(schema)).await;
    match ack {
        Ok(Ack::Opened(Ok(decoder))) => Ok((stream, decoder)),
        Ok(Ack::Opened(Err(message))) => Err(Exit::Fatal(message)),
        Ok(_) => Err(Exit::Terminal(worker_stopped())),
        Err(terminal) => Err(Exit::Terminal(terminal)),
    }
}

/// Read established messages until the subscription ends. The next message is
/// not requested before the worker has decoded (and written) this one.
async fn pump<S>(
    mut stream: S,
    mut decoder: Box<Decoder>,
    handoff: &mut Handoff,
) -> Result<(), Exit>
where
    S: Stream<Item = Result<FlightData, FlightStatus>> + Unpin,
{
    loop {
        let data = match receive(&mut stream).await {
            Ok(Some(data)) => data,
            Ok(None) => {
                return match decoder.end() {
                    Stop::Reconnect => Ok(()),
                    Stop::Fatal(message) => Err(Exit::Fatal(message)),
                };
            }
            Err(Stop::Reconnect) => return Ok(()),
            Err(Stop::Fatal(message)) => return Err(Exit::Fatal(message)),
        };
        let ack = handoff.deliver(Event::Message { decoder, data }).await;
        match ack {
            Ok(Ack::Processed(returned, result)) => {
                decoder = returned;
                result.map_err(|e| Exit::Fatal(e.into_bytes()))?;
            }
            Ok(_) => return Err(Exit::Terminal(worker_stopped())),
            Err(terminal) => return Err(Exit::Terminal(terminal)),
        }
    }
}

#[derive(Debug, PartialEq)]
enum Stop {
    /// EOF or Canceled/Unavailable/OK while reading an established stream.
    Reconnect,
    /// Go's error text, which can carry a status message's raw bytes.
    Fatal(Vec<u8>),
}

async fn next_message<S>(stream: &mut S) -> Option<Result<FlightData, FlightStatus>>
where
    S: Stream<Item = Result<FlightData, FlightStatus>> + Unpin,
{
    // Statuses arrive already classified by the transport.
    let item = stream.next().await;
    #[cfg(feature = "diagnostics")]
    if let Some(Ok(data)) = &item {
        super::resources::message(
            data.data_header.len(),
            data.data_body.len(),
            data.app_metadata.len(),
        );
    }
    item
}

/// Classify an established-stream read.
async fn receive<S>(stream: &mut S) -> Result<Option<FlightData>, Stop>
where
    S: Stream<Item = Result<FlightData, FlightStatus>> + Unpin,
{
    match next_message(stream).await {
        Some(Ok(data)) => Ok(Some(data)),
        None => Ok(None),
        Some(Err(status)) if status.is_stream_end() => Err(Stop::Reconnect),
        Some(Err(status)) => Err(Stop::Fatal(status.go_error())),
    }
}

/// Reader initialization, following Go v13's flight.NewRecordReader. Every
/// error here is fatal, even EOF. This only reads the schema message; the
/// worker decodes it (see [`initialize`]).
async fn open<S>(mut stream: S) -> Result<(S, FlightData), Vec<u8>>
where
    S: Stream<Item = Result<FlightData, FlightStatus>> + Unpin,
{
    // NewRecordReader returns the first Recv error unwrapped.
    let first = match next_message(&mut stream).await {
        Some(Ok(data)) => data,
        Some(Err(status)) => return Err(status.go_error()),
        None => return Err(b"EOF".to_vec()),
    };
    // Only this first message is skipped when its IPC header is empty. Later
    // metadata-only messages reach the IPC decoder.
    let data = if first.data_header.is_empty() {
        match next_message(&mut stream).await {
            Some(Ok(data)) => data,
            Some(Err(status)) => {
                let mut message = SCHEMA_READ.as_bytes().to_vec();
                message.extend(status.go_error());
                return Err(message);
            }
            None => return Err(format!("{SCHEMA_READ}EOF").into_bytes()),
        }
    } else {
        first
    };
    Ok((stream, data))
}

/// The schema stage of reader initialization; every error is fatal and
/// carries the Flight reader's wrapping.
fn initialize(schema: &FlightData) -> Result<Box<Decoder>, Vec<u8>> {
    Decoder::new(&schema.data_header)
        .map(Box::new)
        .map_err(|e| format!("{FLIGHT_READER}{e}").into_bytes())
}

/// Fresh IPC state for one subscription, following Go v13's ipc.Reader.
struct Decoder {
    /// Utf8/LargeUtf8 rewritten to Binary/LargeBinary; see [`wire_schema`].
    /// The renderer keeps the original schema's plan.
    wire: SchemaRef,
    swap_endian: bool,
    /// The value layout for every dictionary ID Go's memo registers. An ID
    /// reachable from the columns keeps the last compatible column-path
    /// occurrence (Go's post-order memo), which also supplies the nested IDs
    /// bound at record time; this includes IDs nested only in earlier fields.
    /// An ID declared only under a leaf field (see [`ipc_dictionary_ids`])
    /// takes its layout from those fields. Go-compatible occurrences of one
    /// ID decode identically once canonicalized (see [`wire_schema`]); they
    /// differ only in nested IDs, which Go binds through column paths alone.
    dictionary_types: HashMap<i64, DictionaryType>,
    /// Validated values with nested dictionaries still represented by their
    /// integer keys. Binding is deferred until a record, as in Go's memo.
    dictionaries: HashMap<i64, ArrayRef>,
    /// A nested delta cannot be reified by Go. A replacement clears it; EOF
    /// without a record never attempts reification and remains normal.
    nested_deltas: HashMap<i64, Vec<ArrayRef>>,
    renderer: BatchRenderer,
    /// Unique dictionary IDs declared by the IPC schema.
    ids: BTreeSet<i64>,
    /// IDs reachable from the columns, by first use in schema order: the
    /// dictionaries Go resolves for a record, in its error order.
    dictionary_order: Vec<i64>,
    /// Whether a reachable dictionary has nested dictionaries to bind.
    nested: bool,
    /// Dictionaries Go requires before the first record (`ids.len()`).
    expected: usize,
    /// Initial dictionaries not yet read.
    pending: usize,
    /// Whether a column nests deeper than Go loads a record batch.
    too_deep: bool,
}

enum Decoded {
    Dictionary,
    Batch(RecordBatch),
}

impl Decoder {
    fn new(header: &[u8]) -> Result<Self, String> {
        let message = ipc_message(header)?;
        let kind = message.header_type();
        let schema = match message.header_as_schema() {
            Some(schema) if kind == MessageHeader::Schema => schema,
            _ => {
                return Err(format!(
                    "arrow/ipc: invalid message type (got={}, want=Schema)",
                    header_name(kind)
                ));
            }
        };
        let decode_error = |e: &dyn std::fmt::Display| {
            format!("arrow/ipc: could not decode schema from message schema: {e}")
        };
        let endianness = schema.endianness();
        let swap_endian = endianness != NATIVE_ENDIAN;
        // arrow-rs rejects big-endian decimal schemas before reading buffers.
        // Conversion only needs the logical schema; body normalization below
        // handles its original byte order. Retain all original field metadata.
        let mut logical_header = header.to_vec();
        // Go treats every non-native enum value as requiring a byte swap,
        // including unknown values. Conversion always uses a little-endian
        // logical schema regardless of that original wire declaration.
        if endianness != arrow_ipc::Endianness::Little {
            let slot = schema._tab.vtable().get(arrow_ipc::Schema::VT_ENDIANNESS);
            let offset = schema._tab.loc() + usize::from(slot);
            logical_header[offset..offset + 2].copy_from_slice(&0_i16.to_le_bytes());
        }
        let logical_message = ipc_message(&logical_header)?;
        let logical_schema = logical_message.header_as_schema().unwrap();
        if logical_schema.endianness() != arrow_ipc::Endianness::Little {
            return Err(decode_error(&"logical schema is not little endian"));
        }
        // The patch can overlap another FlatBuffer field. Validate and collect
        // dictionaries from exactly the verified copy used for conversion.
        validation::ipc_schema(&logical_schema)?;
        let (ids, orphans) = ipc_dictionary_ids(&logical_schema);
        let schema = Arc::new(
            arrow_ipc::convert::try_fb_to_schema(logical_schema).map_err(|e| decode_error(&e))?,
        );
        let orphans =
            orphan_schema(&orphans, arrow_ipc::Endianness::Little).map_err(|e| decode_error(&e))?;
        validation::schema_types(schema.fields()).map_err(|e| decode_error(&e))?;
        validation::schema_types(orphans.fields()).map_err(|e| decode_error(&e))?;
        // Go rejects types it cannot represent while decoding the schema.
        let renderer = BatchRenderer::new(&schema).map_err(|e| decode_error(&e))?;
        let expected = ids.len();
        let too_deep = validation::nesting(schema.fields()) > validation::GO_MAX_NESTING;
        let wire = Arc::new(wire_schema(&schema));
        let orphan_types = dictionary_types(wire_schema(&orphans).fields());
        let mut dictionary_types = dictionary_types(wire.fields());
        let dictionary_order = dictionary_order(wire.fields());
        let nested = dictionary_types.values().any(|t| t.nested);
        for (id, layout) in orphan_types {
            dictionary_types.entry(id).or_insert(layout);
        }
        Ok(Self {
            wire,
            swap_endian,
            dictionary_types,
            ids,
            dictionary_order,
            nested,
            dictionaries: HashMap::new(),
            nested_deltas: HashMap::new(),
            renderer,
            expected,
            pending: expected,
            too_deep,
        })
    }

    /// EOF on an established stream: normal before any initial dictionary or
    /// after all of them, fatal partway through.
    fn end(&self) -> Stop {
        if self.pending == 0 || self.pending == self.expected {
            Stop::Reconnect
        } else {
            Stop::Fatal(
                format!(
                    "arrow/ipc: IPC stream ended without reading the expected ({}) dictionaries",
                    self.expected
                )
                .into_bytes(),
            )
        }
    }

    fn decode(&mut self, data: FlightData) -> Result<Decoded, String> {
        let message = ipc_message(&data.data_header)?;
        let kind = message.header_type();
        if self.pending > 0 {
            if kind != MessageHeader::DictionaryBatch {
                // Go overwrites its initial type error by interpreting the
                // first table field as a dictionary ID. For RecordBatch that
                // field is the row count. Preserve the characterized unknown-ID
                // error without performing an unchecked flatbuffer cast.
                if let Some(batch) = message.header_as_record_batch() {
                    let id = batch.length();
                    if !self.ids.contains(&id) {
                        return Err(format!("arrow/ipc: no dictionary type found with id: {id}"));
                    }
                }
                return Err(format!(
                    "arrow/ipc: IPC stream did not have the expected ({}) dictionaries at the start of the stream",
                    self.expected
                ));
            }
            self.read_dictionary(&message, data.data_body)?;
            self.pending -= 1;
            return Ok(Decoded::Dictionary);
        }
        match kind {
            MessageHeader::DictionaryBatch => {
                self.read_dictionary(&message, data.data_body)?;
                Ok(Decoded::Dictionary)
            }
            MessageHeader::RecordBatch if self.too_deep => {
                Err(format!("{UNKNOWN_READ}{NESTING_LIMIT}"))
            }
            MessageHeader::RecordBatch => {
                let batch = message
                    .header_as_record_batch()
                    .ok_or_else(|| format!("{UNKNOWN_READ}invalid record batch header"))?;
                validate_body(&batch, data.data_body.len())?;
                let layout = validation::record_batch(
                    &batch,
                    self.wire.fields(),
                    &data.data_body,
                    message.version(),
                )
                .map_err(|e| format!("{UNKNOWN_READ}{e}"))?;
                if layout == validation::Layout::NoRows {
                    return Ok(Decoded::Batch(RecordBatch::new_empty(Arc::clone(
                        &self.wire,
                    ))));
                }
                // Go resolves every dictionary reachable from the columns,
                // even for zero rows and all-null keys. Arrow's flat path
                // instead substitutes empty values for absent IDs. Check in
                // schema order so the first missing or unresolved ID also has
                // Go's diagnostic. IDs declared only under leaf fields are
                // never resolved, so they may be absent or unresolvable.
                for id in &self.dictionary_order {
                    if self.nested_deltas.contains_key(id) {
                        return Err(format!(
                            "{UNKNOWN_READ}arrow/ipc: delta dict with unresolved nested dictionary not implemented"
                        ));
                    }
                    if !self.dictionaries.contains_key(id) {
                        return Err(format!(
                            "{UNKNOWN_READ}arrow/ipc: no dictionaries found for id={id}"
                        ));
                    }
                }
                // Go binds the current memo at record time, after all initial
                // dictionaries, replacements and deltas have arrived. Flat
                // dictionaries retain the direct Arrow path.
                let resolved;
                let dictionaries = if self.nested {
                    resolved = self.resolve_dictionaries()?;
                    &resolved
                } else {
                    &self.dictionaries
                };
                // Normalize only once this message has passed pending-dictionary,
                // layout and dictionary-binding checks. NoRows never reads buffers.
                let (normalized_header, body) = if self.swap_endian {
                    let types = self
                        .wire
                        .fields()
                        .iter()
                        .map(|f| f.data_type())
                        .collect::<Vec<_>>();
                    let (header, body) = endian::normalize(message, batch, &types, &data.data_body)
                        .map_err(|e| format!("{UNKNOWN_READ}{e}"))?;
                    (Some(header), bytes::Bytes::from(body))
                } else {
                    (None, data.data_body)
                };
                let normalized_message =
                    normalized_header.as_deref().map(ipc_message).transpose()?;
                let batch = normalized_message
                    .as_ref()
                    .and_then(|m| m.header_as_record_batch())
                    .unwrap_or(batch);
                let batch = arrow_ipc::reader::read_record_batch(
                    &ipc_body(body),
                    batch,
                    Arc::clone(&self.wire),
                    dictionaries,
                    None,
                    &message.version(),
                )
                .map_err(|e| format!("{UNKNOWN_READ}{e}"))?;
                #[cfg(feature = "diagnostics")]
                super::resources::batch(
                    batch.num_rows(),
                    batch
                        .columns()
                        .iter()
                        .map(|c| c.get_buffer_memory_size())
                        .sum(),
                );
                Ok(Decoded::Batch(batch))
            }
            // Go's message omits the closing parenthesis.
            other => Err(format!(
                "arrow/ipc: invalid message type (got={}, want=RecordBatch",
                header_name(other)
            )),
        }
    }

    fn read_dictionary(
        &mut self,
        message: &arrow_ipc::Message<'_>,
        body: bytes::Bytes,
    ) -> Result<(), String> {
        let batch = message
            .header_as_dictionary_batch()
            .ok_or_else(|| format!("{UNKNOWN_READ}invalid dictionary batch header"))?;
        let record = batch
            .data()
            .ok_or_else(|| format!("{UNKNOWN_READ}invalid dictionary record header"))?;
        validate_body(&record, body.len())?;
        let layout = self.dictionary_types.get(&batch.id()).ok_or_else(|| {
            format!(
                "arrow/ipc: no dictionary type found with id: {}",
                batch.id()
            )
        })?;
        // Go loads dictionary values from depth zero, under the same limit.
        if validation::type_nesting(&layout.values) > validation::GO_MAX_NESTING {
            return Err(format!("{UNKNOWN_READ}{NESTING_LIMIT}"));
        }
        validation::dictionary(&record, &layout.values, &body, message.version())
            .map_err(|e| format!("{UNKNOWN_READ}{e}"))?;
        if batch.isDelta() && !self.dictionaries.contains_key(&batch.id()) {
            return Err(format!(
                "{UNKNOWN_READ}arrow/ipc: adding delta to non-existing id={}",
                batch.id()
            ));
        }
        // Match the native ordering: body bounds, dictionary ID, nesting,
        // structural guards and missing-delta checks precede decompression.
        let (normalized_header, body) = if self.swap_endian {
            let (header, body) = endian::normalize(*message, record, &[&layout.values], &body)
                .map_err(|e| format!("{UNKNOWN_READ}{e}"))?;
            (Some(header), bytes::Bytes::from(body))
        } else {
            (None, body)
        };
        let normalized_message = normalized_header.as_deref().map(ipc_message).transpose()?;
        let batch = normalized_message
            .as_ref()
            .and_then(|m| m.header_as_dictionary_batch())
            .unwrap_or(batch);
        let record = batch.data().unwrap();
        if batch.isDelta() && layout.nested {
            // Go retains each unresolved delta piece without concatenating it.
            // Reification fails only at a record; replacement or EOF can occur
            // first. Decode and validate the piece, keeping that ownership.
            let schema = Arc::new(Schema::new(vec![Field::new(
                "",
                dictionary_keys(&layout.values),
                true,
            )]));
            let piece = arrow_ipc::reader::read_record_batch(
                &ipc_body(body),
                record,
                schema,
                &self.dictionaries,
                None,
                &message.version(),
            )
            .map_err(|e| e.to_string())?;
            self.nested_deltas
                .entry(batch.id())
                .or_default()
                .push(Arc::clone(piece.column(0)));
        } else {
            arrow_ipc::reader::read_dictionary(
                &ipc_body(body),
                batch,
                &layout.decoding,
                &mut self.dictionaries,
                &message.version(),
            )
            .map_err(|e| e.to_string())?;
            if !batch.isDelta() {
                self.nested_deltas.remove(&batch.id());
            }
        }
        #[cfg(feature = "diagnostics")]
        super::resources::dictionary_bytes(
            self.dictionaries
                .values()
                .chain(self.nested_deltas.values().flatten())
                .map(|d| d.get_buffer_memory_size())
                .sum(),
        );
        Ok(())
    }

    fn resolve_dictionaries(&self) -> Result<HashMap<i64, ArrayRef>, String> {
        let mut resolved = HashMap::new();
        let mut visiting = BTreeSet::new();
        for id in &self.dictionary_order {
            self.resolve_dictionary(*id, &mut resolved, &mut visiting)?;
        }
        Ok(resolved)
    }

    fn resolve_dictionary(
        &self,
        id: i64,
        resolved: &mut HashMap<i64, ArrayRef>,
        visiting: &mut BTreeSet<i64>,
    ) -> Result<ArrayRef, String> {
        if let Some(values) = resolved.get(&id) {
            return Ok(Arc::clone(values));
        }
        if self.nested_deltas.contains_key(&id) {
            return Err(format!(
                "{UNKNOWN_READ}arrow/ipc: delta dict with unresolved nested dictionary not implemented"
            ));
        }
        let values = self
            .dictionaries
            .get(&id)
            .ok_or_else(|| format!("{UNKNOWN_READ}arrow/ipc: no dictionaries found for id={id}"))?;
        let layout = &self.dictionary_types[&id];
        if !visiting.insert(id) {
            return Err(format!(
                "{UNKNOWN_READ}arrow/ipc: cyclic dictionary id={id}"
            ));
        }
        let values = if layout.nested {
            self.bind_type(&layout.values, values, resolved, visiting)?
        } else {
            Arc::clone(values)
        };
        visiting.remove(&id);
        resolved.insert(id, Arc::clone(&values));
        Ok(values)
    }

    /// Reuse validated buffers, replacing integer-key placeholders with the
    /// current dictionary values. Every rebuilt array goes through build's
    /// full Arrow validation, including the newly bound dictionary indices.
    fn bind_type(
        &self,
        data_type: &DataType,
        array: &ArrayRef,
        resolved: &mut HashMap<i64, ArrayRef>,
        visiting: &mut BTreeSet<i64>,
    ) -> Result<ArrayRef, String> {
        let fields = type_fields(data_type);
        if fields.is_empty() {
            return Ok(Arc::clone(array));
        }
        let data = array.to_data();
        let mut children = Vec::with_capacity(fields.len());
        for (field, child) in fields.into_iter().zip(data.child_data()) {
            let child = if matches!(field.data_type(), DataType::Dictionary(_, _)) {
                #[allow(deprecated)]
                let id = field
                    .dict_id()
                    .ok_or_else(|| format!("{UNKNOWN_READ}dictionary field has no ID"))?;
                let values = self.resolve_dictionary(id, resolved, visiting)?;
                child
                    .clone()
                    .into_builder()
                    .data_type(field.data_type().clone())
                    .child_data(vec![values.to_data()])
                    .build()
                    .map_err(|e| format!("{UNKNOWN_READ}{e}"))?
            } else {
                self.bind_type(
                    field.data_type(),
                    &arrow_array::make_array(child.clone()),
                    resolved,
                    visiting,
                )?
                .to_data()
            };
            children.push(child);
        }
        let data = data
            .into_builder()
            .data_type(data_type.clone())
            .child_data(children)
            .build()
            .map_err(|e| format!("{UNKNOWN_READ}{e}"))?;
        Ok(arrow_array::make_array(data))
    }
}

struct DictionaryType {
    values: DataType,
    /// One explicit dictionary field for this ID; Arrow's first-match lookup
    /// cannot select another occurrence or discard other IDs from our memo.
    decoding: Schema,
    nested: bool,
}

fn type_fields(data_type: &DataType) -> Vec<&Field> {
    match data_type {
        DataType::List(field)
        | DataType::LargeList(field)
        | DataType::ListView(field)
        | DataType::LargeListView(field)
        | DataType::FixedSizeList(field, _)
        | DataType::Map(field, _) => vec![field],
        DataType::RunEndEncoded(ends, values) => vec![ends, values],
        DataType::Struct(fields) => fields.iter().map(AsRef::as_ref).collect(),
        DataType::Union(fields, _) => fields.iter().map(|(_, f)| f.as_ref()).collect(),
        DataType::Dictionary(_, values) => type_fields(values),
        _ => Vec::new(),
    }
}

/// Only nested dictionary values become their integer keys. All other layout,
/// field positions, annotations and nullability have already been made safe by
/// wire_schema and remain intact. No UTF-8 or array validation is bypassed.
fn dictionary_keys(data_type: &DataType) -> DataType {
    let child = |field: &FieldRef| {
        Arc::new(
            field
                .as_ref()
                .clone()
                .with_data_type(dictionary_keys(field.data_type())),
        )
    };
    match data_type {
        DataType::Dictionary(key, _) => key.as_ref().clone(),
        DataType::List(f) => DataType::List(child(f)),
        DataType::LargeList(f) => DataType::LargeList(child(f)),
        DataType::ListView(f) => DataType::ListView(child(f)),
        DataType::LargeListView(f) => DataType::LargeListView(child(f)),
        DataType::FixedSizeList(f, n) => DataType::FixedSizeList(child(f), *n),
        DataType::Map(f, sorted) => DataType::Map(child(f), *sorted),
        DataType::RunEndEncoded(ends, values) => {
            DataType::RunEndEncoded(child(ends), child(values))
        }
        DataType::Struct(fields) => DataType::Struct(fields.iter().map(child).collect()),
        DataType::Union(fields, mode) => {
            DataType::Union(fields.iter().map(|(id, f)| (id, child(f))).collect(), *mode)
        }
        other => other.clone(),
    }
}

fn dictionary_types(fields: &Fields) -> HashMap<i64, DictionaryType> {
    #[allow(deprecated)]
    fn walk(field: &Field, types: &mut HashMap<i64, DictionaryType>) {
        for child in type_fields(field.data_type()) {
            walk(child, types);
        }
        if let DataType::Dictionary(_, values) = field.data_type() {
            let id = field
                .dict_id()
                .expect("converted dictionary field has an ID");
            let keys = dictionary_keys(values);
            let nested = &keys != values.as_ref();
            let decoding = Schema::new(vec![Field::new_dict(
                "",
                DataType::Dictionary(Box::new(DataType::Int32), Box::new(keys)),
                true,
                id,
                false,
            )]);
            // The original Go conflict check ran before this post-order walk.
            // Go AddType replaces a compatible type with the last occurrence.
            types.insert(
                id,
                DictionaryType {
                    values: values.as_ref().clone(),
                    decoding,
                    nested,
                },
            );
        }
    }
    let mut types = HashMap::new();
    for field in fields {
        walk(field, &mut types);
    }
    types
}

/// The decoding schema. Go strings are byte strings: Go v13 IPC builds string
/// arrays without UTF-8 validation and RecordToJSON encodes invalid bytes as
/// U+FFFD escapes. arrow-rs validates UTF-8 for Utf8 arrays (a decode error),
/// and skipping validation would also skip offset checks and be unsound. Utf8
/// and Binary share one IPC layout (validity, offsets, values), so decoding
/// string fields as Binary keeps every offset/length check, drops only the
/// UTF-8 check, and leaves the bytes for the renderer's original-schema plan.
///
/// Go never enforces declared nullability, so a producer's nulls in a field
/// marked non-nullable still render. Arrow rejects them, so every wire field is
/// nullable except where Arrow's layout requires otherwise: Map entries and
/// Map keys, and run ends (always non-nullable).
///
/// Shared dictionary IDs need one exact Arrow value type, whereas Go's
/// TypeEqual ignores list element names, nested field metadata, map field
/// names/sorting and run-end field names. After the original-schema Go
/// conflict check, dictionary values use a canonical form for these
/// properties. The renderer still uses the original schema. Canonicalizing
/// each occurrence in place, rather than copying another field's type,
/// preserves nested dictionary IDs and their positional paths (including IDs
/// found only under a later occurrence of a shared parent dictionary).
fn wire_schema(schema: &Schema) -> Schema {
    Schema::new_with_metadata(
        schema
            .fields()
            .iter()
            .map(|f| wire_field(f, false))
            .collect::<Fields>(),
        schema.metadata().clone(),
    )
}

fn wire_field(field: &Field, dictionary_value: bool) -> Field {
    let field = rebuild(field, wire_type(field.data_type(), dictionary_value), true);
    if dictionary_value {
        field.with_metadata(arrow_schema::Metadata::default())
    } else {
        field
    }
}

#[allow(deprecated)]
fn rebuild(field: &Field, data_type: DataType, nullable: bool) -> Field {
    match field.dict_id() {
        Some(id) => Field::new_dict(
            field.name(),
            data_type,
            nullable,
            id,
            field.dict_is_ordered().unwrap_or(false),
        ),
        None => Field::new(field.name(), data_type, nullable),
    }
    .with_metadata(field.metadata().clone())
}

/// Map entries and keys stay non-nullable; values become nullable.
fn wire_entries(entries: &Field, dictionary_value: bool) -> Field {
    let data_type = match entries.data_type() {
        DataType::Struct(pair) => DataType::Struct(
            pair.iter()
                .enumerate()
                .map(|(index, field)| {
                    let mut field = wire_field(field, dictionary_value).with_nullable(index != 0);
                    if dictionary_value {
                        field = field.with_name(if index == 0 { "key" } else { "value" });
                    }
                    Arc::new(field)
                })
                .collect(),
        ),
        other => wire_type(other, dictionary_value),
    };
    let field = rebuild(entries, data_type, false);
    if dictionary_value {
        field
            .with_name("entries")
            .with_metadata(arrow_schema::Metadata::default())
    } else {
        field
    }
}

fn wire_type(data_type: &DataType, dictionary_value: bool) -> DataType {
    let child = |field: &FieldRef| Arc::new(wire_field(field, dictionary_value));
    let list_child = |field: &FieldRef| {
        let field = wire_field(field, dictionary_value);
        Arc::new(if dictionary_value {
            field.with_name("item")
        } else {
            field
        })
    };
    match data_type {
        DataType::Utf8 => DataType::Binary,
        DataType::LargeUtf8 => DataType::LargeBinary,
        DataType::Dictionary(key, values) => {
            DataType::Dictionary(key.clone(), Box::new(wire_type(values, true)))
        }
        DataType::List(field) => DataType::List(list_child(field)),
        DataType::LargeList(field) => DataType::LargeList(child(field)),
        DataType::ListView(field) => DataType::ListView(child(field)),
        DataType::LargeListView(field) => DataType::LargeListView(child(field)),
        DataType::FixedSizeList(field, size) => DataType::FixedSizeList(list_child(field), *size),
        DataType::Map(field, sorted) => DataType::Map(
            Arc::new(wire_entries(field, dictionary_value)),
            !dictionary_value && *sorted,
        ),
        // Arrow requires a non-nullable run-ends field (Go ignores the flag);
        // actual nulls in run ends are still rejected.
        DataType::RunEndEncoded(ends, values) => {
            let mut ends = wire_field(ends, dictionary_value).with_nullable(false);
            let mut values = wire_field(values, dictionary_value);
            if dictionary_value {
                ends = ends.with_name("run_ends");
                values = values.with_name("values");
            }
            DataType::RunEndEncoded(Arc::new(ends), Arc::new(values))
        }
        DataType::Struct(fields) => DataType::Struct(fields.iter().map(child).collect()),
        DataType::Union(fields, mode) => DataType::Union(
            fields
                .iter()
                .map(|(id, field)| (id, child(field)))
                .collect(),
            *mode,
        ),
        other => other.clone(),
    }
}

/// Protobuf `Bytes` can start at any byte address. Arrow's dense-union reader
/// constructs typed offsets directly, unlike its other array builders which
/// realign buffers. Copy an unaligned body into Arrow-owned aligned storage
/// before decoding; already aligned bodies keep their existing owner. This is
/// the same aligned-copy boundary used by arrow-flight's decoding utility.
fn ipc_body(body: bytes::Bytes) -> Buffer {
    if (body.as_ptr() as usize).is_multiple_of(64) {
        Buffer::from(body)
    } else {
        Buffer::from(body.as_ref())
    }
}

/// The low-level IPC reader slices these ranges before validating array data.
/// Check them here so a truncated Flight body produces Go's recovered EOF,
/// rather than a Rust bounds panic. This also rejects negative/overflowing
/// ranges before their signed offsets can become usize values.
fn validate_body(batch: &arrow_ipc::RecordBatch<'_>, body_len: usize) -> Result<(), String> {
    if let Some(buffers) = batch.buffers() {
        for buffer in buffers {
            let valid = usize::try_from(buffer.offset())
                .ok()
                .zip(usize::try_from(buffer.length()).ok())
                .and_then(|(offset, length)| offset.checked_add(length))
                .is_some_and(|end| end <= body_len);
            if !valid {
                return Err(format!("{UNKNOWN_READ}EOF"));
            }
        }
    }
    Ok(())
}

/// Go's unverified flatbuffer reads panic on out-of-range offsets and the
/// reader recovers the panic into this text. The first reads are the root
/// offset and then `Message.HeaderType()`, which every message path calls
/// first; their panics are reproduced here. Rust verifies a header that
/// survives them, so deeper malformations keep a different (documented)
/// diagnostic.
fn ipc_message(header: &[u8]) -> Result<arrow_ipc::Message<'_>, String> {
    go_header_type(header).map_err(|panic| format!("{UNKNOWN_READ}runtime error: {panic}"))?;
    let options = flatbuffers::VerifierOptions {
        max_depth: VERIFIER_MAX_DEPTH,
        ..Default::default()
    };
    arrow_ipc::root_as_message_with_opts(&options, header).map_err(|e| format!("{UNKNOWN_READ}{e}"))
}

/// Go flatbuffers v23.1.21 `GetRootAsMessage(buf, 0).HeaderType()` with its
/// runtime bounds checks: `t.Bytes[off:]` slices with a uint32 offset and the
/// fixed-width getters touch their last byte first. Offsets wrap as Go's
/// UOffsetT/SOffsetT conversions do.
fn go_header_type(buf: &[u8]) -> Result<u8, String> {
    let length = buf.len();
    let slice = |off: u32| -> Result<&[u8], String> {
        buf.get(off as usize..)
            .ok_or_else(|| format!("slice bounds out of range [{off}:{length}]"))
    };
    let fixed = |bytes: &[u8], width: usize| -> Result<(), String> {
        if bytes.len() < width {
            return Err(format!(
                "index out of range [{}] with length {}",
                width - 1,
                bytes.len()
            ));
        }
        Ok(())
    };
    let u32_at = |off: u32| -> Result<u32, String> {
        let bytes = slice(off)?;
        fixed(bytes, 4)?;
        Ok(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
    };
    let u16_at = |off: u32| -> Result<u16, String> {
        let bytes = slice(off)?;
        fixed(bytes, 2)?;
        Ok(u16::from_le_bytes([bytes[0], bytes[1]]))
    };
    let position = u32_at(0)?;
    // Table.Offset(6): vtable := UOffsetT(SOffsetT(t.Pos) - t.GetSOffsetT(t.Pos))
    let vtable = (position as i32).wrapping_sub(u32_at(position)? as i32) as u32;
    if 6 >= u16_at(vtable)? {
        return Ok(0);
    }
    let field = u16_at(vtable.wrapping_add(6))?;
    if field == 0 {
        return Ok(0);
    }
    let bytes = slice(u32::from(field).wrapping_add(position))?;
    fixed(bytes, 1)?;
    Ok(bytes[0])
}

/// Go's flatbuf.EnumNamesMessageHeader, falling back to MessageType(N).
fn header_name(header: MessageHeader) -> String {
    header
        .variant_name()
        .map_or_else(|| format!("MessageType({})", header.0), str::to_owned)
}

/// Unique dictionary IDs in the IPC schema, as Go's dictionary memo mapper
/// counts them: fields sharing an ID need one dictionary message. This reads
/// the flatbuffer fields directly, where every dictionary-encoded field
/// (including ones nested in dictionary values) carries its own encoding,
/// rather than the converted Arrow fields.
///
/// Also returns the orphan fields: children of a field whose type has no
/// children. Go converts them, registering their dictionary IDs, value types
/// and conflicts, and then drops them from the field's type, as arrow-rs's
/// conversion does. Their dictionaries count towards the initial messages
/// and load like any other, but no record ever reaches them. An orphan's own
/// orphans are listed separately, so each field belongs to one conversion.
fn ipc_dictionary_ids<'a>(
    schema: &arrow_ipc::Schema<'a>,
) -> (BTreeSet<i64>, Vec<arrow_ipc::Field<'a>>) {
    fn walk<'a>(
        field: arrow_ipc::Field<'a>,
        ids: &mut BTreeSet<i64>,
        orphans: &mut Vec<arrow_ipc::Field<'a>>,
    ) {
        if let Some(dictionary) = field.dictionary() {
            ids.insert(dictionary.id());
        }
        let keeps = keeps_children(field.type_type());
        for child in field.children().into_iter().flatten() {
            if !keeps {
                orphans.push(child);
            }
            walk(child, ids, orphans);
        }
    }
    let mut ids = BTreeSet::new();
    let mut orphans = Vec::new();
    for field in schema.fields().into_iter().flatten() {
        walk(field, &mut ids, &mut orphans);
    }
    (ids, orphans)
}

/// Types whose conversion uses the field's children, in Go and arrow-rs
/// alike; every other type ignores them.
fn keeps_children(kind: arrow_ipc::Type) -> bool {
    use arrow_ipc::Type;
    matches!(
        kind,
        Type::List
            | Type::LargeList
            | Type::ListView
            | Type::LargeListView
            | Type::FixedSizeList
            | Type::Struct_
            | Type::Union
            | Type::Map
            | Type::RunEndEncoded
    )
}

/// IDs reachable from the columns, by first use in schema order. Go resolves
/// each column pre-order: a dictionary, then the dictionaries nested in its
/// values (at the column's own paths), then its children.
#[allow(deprecated)]
fn dictionary_order(fields: &Fields) -> Vec<i64> {
    fn walk(field: &Field, seen: &mut BTreeSet<i64>, order: &mut Vec<i64>) {
        if let DataType::Dictionary(_, _) = field.data_type()
            && let Some(id) = field.dict_id()
            && seen.insert(id)
        {
            order.push(id);
        }
        for child in type_fields(field.data_type()) {
            walk(child, seen, order);
        }
    }
    let mut seen = BTreeSet::new();
    let mut order = Vec::new();
    for field in fields {
        walk(field, &mut seen, &mut order);
    }
    order
}

/// Convert orphan fields with arrow-rs's own schema conversion, so their
/// types, errors and canonical wire form match column fields exactly. The
/// conversion takes a schema table, so each orphan subtree is copied into a
/// new one. Only what the conversion reads is copied; metadata is not, since
/// nothing under an orphan is rendered and dictionary values drop it anyway.
/// Copying children only for [`keeps_children`] types visits each schema
/// field at most once.
fn orphan_schema(
    orphans: &[arrow_ipc::Field<'_>],
    endianness: arrow_ipc::Endianness,
) -> Result<Schema, String> {
    if orphans.is_empty() {
        return Ok(Schema::empty());
    }
    let mut fbb = flatbuffers::FlatBufferBuilder::new();
    let fields = orphans
        .iter()
        .map(|field| copy_field(&mut fbb, *field))
        .collect::<Result<Vec<_>, _>>()?;
    let fields = fbb.create_vector(&fields);
    let schema = arrow_ipc::Schema::create(
        &mut fbb,
        &arrow_ipc::SchemaArgs {
            endianness,
            fields: Some(fields),
            ..Default::default()
        },
    );
    fbb.finish(schema, None);
    let options = flatbuffers::VerifierOptions {
        max_depth: VERIFIER_MAX_DEPTH,
        ..Default::default()
    };
    let schema = arrow_ipc::root_as_schema_with_opts(&options, fbb.finished_data())
        .map_err(|e| e.to_string())?;
    arrow_ipc::convert::try_fb_to_schema(schema).map_err(|e| e.to_string())
}

fn copy_field<'b>(
    fbb: &mut flatbuffers::FlatBufferBuilder<'b>,
    field: arrow_ipc::Field<'_>,
) -> Result<flatbuffers::WIPOffset<arrow_ipc::Field<'b>>, String> {
    let children = match field.children() {
        Some(children) if keeps_children(field.type_type()) => {
            let children = children
                .iter()
                .map(|child| copy_field(fbb, child))
                .collect::<Result<Vec<_>, _>>()?;
            Some(fbb.create_vector(&children))
        }
        _ => None,
    };
    let type_ = copy_type(fbb, field)?;
    let dictionary = field.dictionary().map(|dictionary| {
        let index_type = dictionary.indexType().map(|int| {
            arrow_ipc::Int::create(
                fbb,
                &arrow_ipc::IntArgs {
                    bitWidth: int.bitWidth(),
                    is_signed: int.is_signed(),
                },
            )
        });
        arrow_ipc::DictionaryEncoding::create(
            fbb,
            &arrow_ipc::DictionaryEncodingArgs {
                id: dictionary.id(),
                indexType: index_type,
                isOrdered: dictionary.isOrdered(),
                dictionaryKind: dictionary.dictionaryKind(),
            },
        )
    });
    let name = field.name().map(|name| fbb.create_string(name));
    Ok(arrow_ipc::Field::create(
        fbb,
        &arrow_ipc::FieldArgs {
            name,
            nullable: field.nullable(),
            type_type: field.type_type(),
            type_: Some(type_),
            dictionary,
            children,
            custom_metadata: None,
        },
    ))
}

/// The type table of every type [`validation::ipc_schema`] admits.
fn copy_type<'b>(
    fbb: &mut flatbuffers::FlatBufferBuilder<'b>,
    field: arrow_ipc::Field<'_>,
) -> Result<flatbuffers::WIPOffset<flatbuffers::UnionWIPOffset>, String> {
    use arrow_ipc::Type;
    let kind = field.type_type();
    let missing = || format!("IPC schema field is missing its {kind:?} type");
    Ok(match kind {
        // Tables without fields.
        Type::Null
        | Type::Bool
        | Type::Binary
        | Type::LargeBinary
        | Type::Utf8
        | Type::LargeUtf8
        | Type::List
        | Type::LargeList
        | Type::Struct_
        | Type::RunEndEncoded => {
            let start = fbb.start_table();
            fbb.end_table(start).as_union_value()
        }
        Type::Int => {
            let int = field.type_as_int().ok_or_else(missing)?;
            let args = arrow_ipc::IntArgs {
                bitWidth: int.bitWidth(),
                is_signed: int.is_signed(),
            };
            arrow_ipc::Int::create(fbb, &args).as_union_value()
        }
        Type::FloatingPoint => {
            let float = field.type_as_floating_point().ok_or_else(missing)?;
            let args = arrow_ipc::FloatingPointArgs {
                precision: float.precision(),
            };
            arrow_ipc::FloatingPoint::create(fbb, &args).as_union_value()
        }
        Type::Decimal => {
            let decimal = field.type_as_decimal().ok_or_else(missing)?;
            let args = arrow_ipc::DecimalArgs {
                precision: decimal.precision(),
                scale: decimal.scale(),
                bitWidth: decimal.bitWidth(),
            };
            arrow_ipc::Decimal::create(fbb, &args).as_union_value()
        }
        Type::FixedSizeBinary => {
            let binary = field.type_as_fixed_size_binary().ok_or_else(missing)?;
            let args = arrow_ipc::FixedSizeBinaryArgs {
                byteWidth: binary.byteWidth(),
            };
            arrow_ipc::FixedSizeBinary::create(fbb, &args).as_union_value()
        }
        Type::FixedSizeList => {
            let list = field.type_as_fixed_size_list().ok_or_else(missing)?;
            let args = arrow_ipc::FixedSizeListArgs {
                listSize: list.listSize(),
            };
            arrow_ipc::FixedSizeList::create(fbb, &args).as_union_value()
        }
        Type::Map => {
            let map = field.type_as_map().ok_or_else(missing)?;
            let args = arrow_ipc::MapArgs {
                keysSorted: map.keysSorted(),
            };
            arrow_ipc::Map::create(fbb, &args).as_union_value()
        }
        Type::Union => {
            let union = field.type_as_union().ok_or_else(missing)?;
            let ids = union.typeIds().map(|ids| {
                let ids: Vec<i32> = ids.iter().collect();
                fbb.create_vector(&ids)
            });
            let args = arrow_ipc::UnionArgs {
                mode: union.mode(),
                typeIds: ids,
            };
            arrow_ipc::Union::create(fbb, &args).as_union_value()
        }
        Type::Date => {
            let date = field.type_as_date().ok_or_else(missing)?;
            let args = arrow_ipc::DateArgs { unit: date.unit() };
            arrow_ipc::Date::create(fbb, &args).as_union_value()
        }
        Type::Time => {
            let time = field.type_as_time().ok_or_else(missing)?;
            let args = arrow_ipc::TimeArgs {
                unit: time.unit(),
                bitWidth: time.bitWidth(),
            };
            arrow_ipc::Time::create(fbb, &args).as_union_value()
        }
        Type::Timestamp => {
            let timestamp = field.type_as_timestamp().ok_or_else(missing)?;
            let timezone = timestamp.timezone().map(|tz| fbb.create_string(tz));
            let args = arrow_ipc::TimestampArgs {
                unit: timestamp.unit(),
                timezone,
            };
            arrow_ipc::Timestamp::create(fbb, &args).as_union_value()
        }
        Type::Interval => {
            let interval = field.type_as_interval().ok_or_else(missing)?;
            let args = arrow_ipc::IntervalArgs {
                unit: interval.unit(),
            };
            arrow_ipc::Interval::create(fbb, &args).as_union_value()
        }
        Type::Duration => {
            let duration = field.type_as_duration().ok_or_else(missing)?;
            let args = arrow_ipc::DurationArgs {
                unit: duration.unit(),
            };
            arrow_ipc::Duration::create(fbb, &args).as_union_value()
        }
        other => return Err(format!("Type {other:?} not supported")),
    })
}

/// Owns decoding, rendering and both process streams. Write failures are
/// ignored as with Go's fmt printing; a broken pipe is raised by the process
/// writer before this code sees it.
struct Worker<'a> {
    stdout: &'a mut (dyn Write + Send),
    stderr: &'a mut (dyn Write + Send),
    dataset: &'a [u8],
    json: bool,
    staging: Vec<u8>,
    /// Tests hold the worker inside reader initialization: it signals the
    /// first sender, then waits on the receiver.
    #[cfg(test)]
    open_gate: Option<(mpsc::Sender<()>, mpsc::Receiver<()>)>,
}

impl<'a> Worker<'a> {
    fn new(
        stdout: &'a mut (dyn Write + Send),
        stderr: &'a mut (dyn Write + Send),
        dataset: &'a (impl AsRef<[u8]> + ?Sized),
        json: bool,
    ) -> Self {
        Self {
            stdout,
            stderr,
            dataset: dataset.as_ref(),
            json,
            staging: Vec::new(),
            #[cfg(test)]
            open_gate: None,
        }
    }

    fn run(mut self, inbox: mpsc::Receiver<Event>, acks: async_mpsc::Sender<Ack>) {
        let mut spinning = false;
        loop {
            let event = if spinning {
                match self.spin(&inbox) {
                    Some(event) => event,
                    None => return,
                }
            } else {
                match inbox.recv() {
                    Ok(event) => event,
                    Err(_) => return,
                }
            };
            spinning = false;
            let ack = match event {
                Event::Spin => {
                    spinning = true;
                    Ack::Written
                }
                Event::StopSpin | Event::Clear => {
                    let _ = self.stderr.write_all(CLEAR);
                    Ack::Written
                }
                Event::Watching => {
                    let _ = self.stderr.write_all(
                        &[
                            b"\r\x1b[K".as_slice(),
                            "● watching ".as_bytes(),
                            self.dataset,
                            b"... (ctrl+c to stop)",
                        ]
                        .concat(),
                    );
                    Ack::Written
                }
                Event::Open(schema) => {
                    #[cfg(test)]
                    if let Some((entered, release)) = self.open_gate.take() {
                        let _ = entered.send(());
                        let _ = release.recv();
                    }
                    Ack::Opened(initialize(&schema))
                }
                Event::Message { mut decoder, data } => match self.message(&mut decoder, data) {
                    Ok(result) => Ack::Processed(decoder, result),
                    Err(panic) => Ack::Panicked(panic),
                },
            };
            if acks.blocking_send(ack).is_err() {
                return;
            }
        }
    }

    /// Go's spinner checks for stop before each frame and sleeps a full frame,
    /// so stopping waits for the current frame. The stop event itself clears.
    fn spin(&mut self, inbox: &mpsc::Receiver<Event>) -> Option<Event> {
        const FRAMES: [char; 4] = ['|', '/', '-', '\\'];
        let mut frame = 0;
        loop {
            match inbox.try_recv() {
                Ok(event) => return Some(event),
                Err(mpsc::TryRecvError::Disconnected) => return None,
                Err(mpsc::TryRecvError::Empty) => {}
            }
            let _ = write!(
                self.stderr,
                "\r{} connecting...",
                FRAMES[frame % FRAMES.len()]
            );
            frame += 1;
            std::thread::sleep(SPINNER_FRAME);
        }
    }

    /// Decode one message. For a batch, Go clears the status line, converts
    /// the whole batch, then prints it with one extra LF; conversion errors
    /// keep the rows already converted. The outer error is a Go panic.
    fn message(
        &mut self,
        decoder: &mut Decoder,
        data: FlightData,
    ) -> Result<Result<(), String>, String> {
        let batch = match decoder.decode(data) {
            Ok(Decoded::Batch(batch)) => batch,
            Ok(Decoded::Dictionary) => return Ok(Ok(())),
            Err(message) => return Ok(Err(message)),
        };
        if !self.json {
            let _ = self.stderr.write_all(CLEAR);
        }
        self.staging.clear();
        let rendered = decoder.renderer.render(&batch, &mut self.staging);
        // Release Arrow buffers before a possibly long blocking write.
        drop(batch);
        if let Err(RenderError::Panic(message)) = rendered {
            return Err(message);
        }
        self.staging.push(b'\n');
        #[cfg(feature = "diagnostics")]
        super::resources::staging(self.staging.len(), self.staging.capacity());
        let _ = self.stdout.write_all(&self.staging);
        if self.staging.capacity() > RETAINED_STAGING {
            self.staging = Vec::new();
            #[cfg(feature = "diagnostics")]
            super::resources::staging(0, 0);
        }
        Ok(Ok(()))
    }
}

#[cfg(test)]
mod tests;
