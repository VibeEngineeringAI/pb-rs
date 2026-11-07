//! SQL owns command policy; the transport and renderer remain independent.
mod output;
pub(crate) mod rewrite;

use crate::{
    ErrorCode, Outcome,
    api::{ApiClient, ApiError, StreamingResponse},
    config::ProfileStore,
    error::CliError,
    parser::ParsedInvocation,
    spec::CommandSpec,
};
use bytes::Bytes;
use std::io::{self, BufRead, Read, Write};
use tokio::sync::{mpsc, oneshot};

type Chunk = Result<Bytes, ApiError>;
const TEXT_BRIDGE_CHUNK: usize = 128 * 1024;

/// The bridge holds at most a consumed, queued, and producer-held chunk:
/// at most TEXT_BRIDGE_CHUNK logical bytes each for successful text, 8KiB
/// for JSON and error previews. Slices retain their Hyper backing allocations;
/// transport buffers and flow-control windows add memory beyond this bridge.
/// Neither the bridge nor the transport buffers the whole response.
struct BodyReader {
    receiver: mpsc::Receiver<Chunk>,
    current: Bytes,
    position: usize,
    #[cfg(feature = "diagnostics")]
    diagnostic_delivered_bytes: usize,
}
impl Read for BodyReader {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        if out.is_empty() {
            return Ok(0);
        }
        let bytes = self.fill_buf()?;
        let count = out.len().min(bytes.len());
        out[..count].copy_from_slice(&bytes[..count]);
        self.consume(count);
        Ok(count)
    }
}
impl BufRead for BodyReader {
    fn fill_buf(&mut self) -> io::Result<&[u8]> {
        while self.position == self.current.len() {
            #[cfg(feature = "diagnostics")]
            let _wait = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::BodyWait);
            match self.receiver.blocking_recv() {
                Some(Ok(bytes)) => {
                    #[cfg(feature = "diagnostics")]
                    {
                        self.diagnostic_delivered_bytes += bytes.len();
                    }
                    self.current = bytes;
                    self.position = 0;
                }
                Some(Err(error)) => return Err(io::Error::other(error)),
                None => return Ok(&[]),
            }
        }
        Ok(&self.current[self.position..])
    }
    fn consume(&mut self, count: usize) {
        // Keep ownership of the chunk unchanged until refill. Scanners often
        // consume one byte at a time, so slicing Bytes here would repeatedly
        // clone and drop its shared allocation's reference count.
        self.position += count.min(self.current.len() - self.position);
    }
}
#[cfg(feature = "diagnostics")]
impl Drop for BodyReader {
    fn drop(&mut self) {
        crate::diagnostics::input_consumed(
            self.diagnostic_delivered_bytes
                .saturating_sub(self.current.len() - self.position),
        );
    }
}

enum Failure {
    Api(&'static str, ApiError),
    Output(output::OutputError),
    Status {
        status: u16,
        status_text: String,
        preview: Vec<u8>,
    },
    Preview(io::Error),
}

pub(super) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
    terminal: bool,
) -> Outcome {
    if parsed.boolean("interactive") {
        return match crate::tui::sql::execute(parsed, stdout, stderr) {
            Ok(exit_code) => Outcome {
                exit_code,
                ..Outcome::default()
            },
            Err(error) => error.render(command, path, structured),
        };
    }
    let query = parsed.positionals.first().map(String::as_str).unwrap_or("");
    if query.trim().is_empty() {
        return CliError::new(ErrorCode::InvalidInput,
            "SQL query is required; example: pb sql run \"select * from frontend\" --from=10m --to=now")
            .render(command, path, structured);
    }
    let normalized_query = rewrite::prepare_query(query);
    let query = rewrite::ensure_default_limit(&normalized_query);
    let format = match crate::output_format(parsed) {
        Ok(format) => format,
        Err(message) => {
            let mut error = CliError::new(ErrorCode::InvalidInput, &message);
            error.message = format!("failed to get 'output' flag: {message}");
            return error.render(command, path, structured);
        }
    };
    match fetch_query(
        parsed,
        None,
        QueryText {
            normalized: &normalized_query,
            wire: &query,
        },
        format,
        stdout,
        stderr,
        terminal,
    ) {
        Ok(()) => Outcome::default(),
        Err(error) => render_failure(error, command, path, structured, ""),
    }
}

/// A saved menu selection reaches the same fetch service, after its own print
/// and empty-query behavior. Its display SQL must not acquire the wire LIMIT.
pub(crate) fn execute_saved(
    application: &crate::saved_queries::SavedApplication,
    profile: (ProfileStore, crate::config::ProfileRecord),
    command: &CommandSpec,
    structured: bool,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
    terminal: bool,
) -> Outcome {
    let normalized = &application.query;
    let from = application.from.as_str();
    let to = application.to.as_str();
    let _ = writeln!(stdout, "Query: {normalized}");
    let wire_query = rewrite::ensure_default_limit(normalized);
    let mut parsed = ParsedInvocation {
        raw: Default::default(),
        command_path: "pb sql list".into(),
        positionals: Vec::new(),
        flags: Default::default(),
    };
    for (name, value) in [
        ("from", if from.is_empty() { "1h" } else { from }),
        ("to", if to.is_empty() { "now" } else { to }),
    ] {
        parsed.flags.insert(
            name.into(),
            crate::parser::ParsedFlag {
                value: crate::parser::FlagValue::Text(value.into()),
                explicit: true,
            },
        );
    }
    match fetch_query(
        &parsed,
        Some(profile),
        QueryText {
            normalized,
            wire: &wire_query,
        },
        "text",
        stdout,
        stderr,
        terminal,
    ) {
        Ok(()) => Outcome::default(),
        Err(error) => render_failure(
            error,
            command,
            "pb sql list",
            structured,
            "selected saved query failed: ",
        ),
    }
}

struct QueryText<'a> {
    normalized: &'a str,
    wire: &'a str,
}

fn fetch_query(
    parsed: &ParsedInvocation,
    supplied_profile: Option<(ProfileStore, crate::config::ProfileRecord)>,
    text: QueryText<'_>,
    format: &str,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
    terminal: bool,
) -> Result<(), Failure> {
    let QueryText {
        normalized: normalized_query,
        wire: query,
    } = text;
    let start = if parsed.text("from").is_empty() {
        "1m"
    } else {
        parsed.text("from")
    };
    let end = if parsed.text("to").is_empty() {
        "now"
    } else {
        parsed.text("to")
    };
    #[derive(serde::Serialize)]
    struct Request<'a> {
        query: &'a serde_json::value::RawValue,
        #[serde(rename = "startTime")]
        start: &'a serde_json::value::RawValue,
        #[serde(rename = "endTime")]
        end: &'a serde_json::value::RawValue,
    }
    let raw_query = if parsed.raw.positionals.is_empty() {
        query.as_bytes().to_vec()
    } else {
        rewrite::ensure_default_limit_bytes(&rewrite::prepare_query_bytes(
            parsed.positional_bytes(0),
        ))
    };
    let query = crate::error::raw_json_string(&raw_query);
    let start = crate::error::raw_json_string(if parsed.text("from").is_empty() {
        start.as_bytes()
    } else {
        parsed.text_bytes("from")
    });
    let end = crate::error::raw_json_string(if parsed.text("to").is_empty() {
        end.as_bytes()
    } else {
        parsed.text_bytes("to")
    });
    let body = serde_json::to_string(&Request {
        query: &query,
        start: &start,
        end: &end,
    })
    .expect("SQL request strings")
    .replace('&', "\\u0026")
    .replace('<', "\\u003c")
    .replace('>', "\\u003e")
    .replace('\u{2028}', "\\u2028")
    .replace('\u{2029}', "\\u2029")
    .into_bytes();
    (|| -> Result<(), Failure> {
        let (store, profile) = match supplied_profile {
            Some(profile) => profile,
            None => {
                let store = ProfileStore::from_env().map_err(|e| {
                    Failure::Api("failed to create new request", ApiError::Persistence(e))
                })?;
                let profile = store.active_profile().map_err(|e| {
                    Failure::Api(
                        "failed to create new request",
                        ApiError::InvalidResponse(e.message),
                    )
                })?;
                (store, profile)
            }
        };
        let client = ApiClient::new(profile, store)
            .map_err(|e| Failure::Api("failed to create new request", e))?;
        let request = client
            .prepare_standard(http::Method::POST, "query", Some(body))
            .map_err(|e| Failure::Api("failed to create new request", e))?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| {
                Failure::Api(
                    "request execution failed",
                    ApiError::InvalidResponse(e.to_string()),
                )
            })?;
        #[cfg(feature = "diagnostics")]
        let headers_timer =
            crate::diagnostics::Timer::begin(crate::diagnostics::Stage::RequestAndHeaders);
        let response = if terminal {
            let stderr = &mut *stderr;
            std::thread::scope(|scope| {
                let (stop, stopped) = std::sync::mpsc::channel();
                let spinner = scope.spawn(move || {
                    let mut frame = 0;
                    loop {
                        if !matches!(
                            stopped.try_recv(),
                            Err(std::sync::mpsc::TryRecvError::Empty)
                        ) {
                            let _ = stderr.write_all(b"\r\x1b[K");
                            let _ = stderr.flush();
                            break;
                        }
                        let _ =
                            write!(stderr, "\r{} fetching...", ['|', '/', '-', '\\'][frame % 4]);
                        let _ = stderr.flush();
                        frame += 1;
                        // Go's spinner clears on its next 100ms polling boundary.
                        std::thread::sleep(std::time::Duration::from_millis(100));
                        if stopped.try_recv().is_ok() {
                            let _ = stderr.write_all(b"\r\x1b[K");
                            let _ = stderr.flush();
                            break;
                        }
                    }
                });
                let result = runtime.block_on(client.send_streaming(request));
                let _ = stop.send(());
                let _ = spinner.join();
                result
            })
        } else {
            runtime.block_on(client.send_streaming(request))
        }
        .map_err(|e| Failure::Api("request execution failed", e))?;
        #[cfg(feature = "diagnostics")]
        drop(headers_timer);
        render_response(&runtime, response, format, stdout)?;
        let name = parsed.text("save-as");
        if !name.is_empty() {
            match crate::saved_queries::save_after_query(
                parsed,
                normalized_query,
                &client,
                &runtime,
            ) {
                Ok(()) => {
                    let _ = stderr.write_all(
                        &[
                            b"Query saved as '".as_slice(),
                            parsed.text_bytes("save-as"),
                            b"'\n",
                        ]
                        .concat(),
                    );
                }
                Err(error) => {
                    let _ = writeln!(stderr, "warning: could not save query: {}", error.message);
                }
            }
        }
        Ok(())
    })()
}

fn render_response(
    runtime: &tokio::runtime::Runtime,
    mut response: StreamingResponse,
    format: &str,
    mut stdout: &mut (dyn Write + Send),
) -> Result<(), Failure> {
    let status = response.status;
    let status_text = response.status_text.clone();
    // Return available data immediately rather than waiting to fill a chunk.
    // Cheap text copies need fewer cross-thread handoffs; JSON and preview
    // read-ahead retain their existing limits.
    let chunk_limit = if status == 200 && format != "json" {
        TEXT_BRIDGE_CHUNK
    } else {
        8192
    };
    std::thread::scope(|scope| {
        let (sender, receiver) = mpsc::channel(1);
        let (finished, completion) = oneshot::channel();
        let worker = scope.spawn(move || {
            #[cfg(feature = "diagnostics")]
            let _diagnostic_thread = crate::diagnostics::ThreadSpan::begin("sql_renderer");
            let mut reader = BodyReader {
                receiver,
                current: Bytes::new(),
                position: 0,
                #[cfg(feature = "diagnostics")]
                diagnostic_delivered_bytes: 0,
            };
            let result = if status != 200 {
                output::read_error_preview(&mut reader)
                    .map_err(Failure::Preview)
                    .and_then(|preview| {
                        Err(Failure::Status {
                            status,
                            status_text,
                            preview,
                        })
                    })
            } else if format == "json" {
                output::stream_json(&mut reader, &mut stdout, &status_text).map_err(Failure::Output)
            } else {
                output::stream_text(&mut reader, &mut stdout, &status_text).map_err(Failure::Output)
            };
            let _ = finished.send(());
            result
        });
        #[cfg(feature = "diagnostics")]
        let pump_timer = crate::diagnostics::Timer::begin(crate::diagnostics::Stage::RuntimePump);
        runtime.block_on(async move {
            let pump = async {
                let deadline = response.deadline();
                loop {
                    let chunk = response.chunk_limit(chunk_limit).await;
                    let (item, last) = match chunk {
                        Ok(Some(bytes)) => (Ok(bytes), false),
                        Ok(None) => break,
                        Err(error) => (Err(error), true),
                    };
                    if last {
                        let _ = sender.send(item).await;
                        break;
                    }
                    // Backpressure can outlast the network deadline while the
                    // renderer is blocked on stdout. Cancel the body now, but
                    // deliver its error only when the reader asks for more.
                    // The outer completion branch still owns early success.
                    match tokio::time::timeout_at(deadline, sender.reserve()).await {
                        Ok(Ok(permit)) => {
                            if tokio::time::Instant::now() >= deadline {
                                permit.send(Err(response.cancel_body()));
                                break;
                            }
                            permit.send(item);
                        }
                        Ok(Err(_)) => break,
                        Err(_) => {
                            drop(item);
                            let error = response.cancel_body();
                            let _ = sender.send(Err(error)).await;
                            break;
                        }
                    }
                }
            };
            tokio::pin!(pump);
            tokio::pin!(completion);
            // Completion cancels a pending read of an irrelevant trailing body.
            std::future::poll_fn(|cx| {
                use std::future::Future;
                if completion.as_mut().poll(cx).is_ready() {
                    return std::task::Poll::Ready(());
                }
                pump.as_mut().poll(cx)
            })
            .await;
        });
        #[cfg(feature = "diagnostics")]
        drop(pump_timer);
        worker.join().expect("SQL renderer panicked")
    })
}

fn render_failure(
    failure: Failure,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    prefix: &str,
) -> Outcome {
    match failure {
        Failure::Status {
            status,
            status_text,
            preview,
        } => {
            let public = format!("query failed: server returned {status_text}");
            if structured {
                return CliError::http(status, &public, &public).render(command, path, true);
            }
            let mut stderr = format!("Error: {prefix}{public}").into_bytes();
            if preview.is_empty() {
                stderr.extend_from_slice(b" with an empty response body");
            } else {
                stderr.extend_from_slice(b": ");
                stderr.extend(preview);
            }
            stderr.push(b'\n');
            Outcome {
                stderr,
                exit_code: 1,
                ..Outcome::default()
            }
        }
        Failure::Api(context, error) => {
            let mut cli = CliError::from(error);
            cli.message = format!("{prefix}{context}: {}", cli.message);
            cli.public_message = cli.message.clone();
            cli.render(command, path, structured)
        }
        Failure::Preview(error) => io_failure(
            &format!("{prefix}failed to read error response body"),
            error,
            command,
            path,
            structured,
        ),
        Failure::Output(error) => match error.kind {
            output::OutputErrorKind::Io(cause) => io_failure(
                &format!("{prefix}{}", error.context),
                cause,
                command,
                path,
                structured,
            ),
            output::OutputErrorKind::Decode(message) => CliError::new(
                ErrorCode::CommandFailed,
                format!("{prefix}{}: {message}", error.context),
            )
            .render(command, path, structured),
        },
    }
}

fn io_failure(
    context: &str,
    error: io::Error,
    command: &CommandSpec,
    path: &str,
    structured: bool,
) -> Outcome {
    let message = format!("{context}: {error}");
    let writer_error = error
        .get_ref()
        .is_some_and(|e| e.is::<crate::OutputWriteError>());
    let mut cli = match error
        .into_inner()
        .and_then(|e| e.downcast::<ApiError>().ok())
    {
        Some(api) => CliError::from(*api),
        None => CliError::new(ErrorCode::CommandFailed, &message),
    };
    if writer_error {
        cli.code = ErrorCode::ConnectionFailed;
        cli.retryable = true;
    }
    cli.message = message.clone();
    cli.public_message = message;
    cli.render(command, path, structured)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::ProfileRecord;
    use std::{
        net::TcpListener,
        sync::{
            atomic::{AtomicUsize, Ordering},
            mpsc as blocking,
        },
        thread,
        time::Duration,
    };

    static NEXT: AtomicUsize = AtomicUsize::new(0);

    fn body_reader(chunks: Vec<Chunk>) -> BodyReader {
        let (sender, receiver) = mpsc::channel(chunks.len().max(1));
        for chunk in chunks {
            sender.try_send(chunk).unwrap();
        }
        BodyReader {
            receiver,
            current: Bytes::new(),
            position: 0,
            #[cfg(feature = "diagnostics")]
            diagnostic_delivered_bytes: 0,
        }
    }

    #[test]
    fn body_reader_consumes_lazily_across_chunks_and_empty_chunks() {
        let mut reader = body_reader(vec![
            Ok(Bytes::from_static(b"abc")),
            Ok(Bytes::new()),
            Ok(Bytes::from_static(b"de")),
        ]);
        let mut empty = [];
        assert_eq!(reader.read(&mut empty).unwrap(), 0);
        assert_eq!(reader.receiver.len(), 3);
        assert_eq!(reader.fill_buf().unwrap(), b"abc");
        reader.consume(1);
        assert_eq!(reader.fill_buf().unwrap(), b"bc");
        assert_eq!(reader.receiver.len(), 2);
        reader.consume(usize::MAX);
        // Consuming the last byte does not ask the producer for another chunk.
        assert_eq!(reader.receiver.len(), 2);
        let mut bytes = [0; 4];
        assert_eq!(reader.read(&mut bytes).unwrap(), 2);
        assert_eq!(&bytes[..2], b"de");
        assert!(reader.fill_buf().unwrap().is_empty());
        assert!(reader.fill_buf().unwrap().is_empty());
        assert_eq!(reader.read(&mut bytes).unwrap(), 0);
    }

    #[test]
    fn body_reader_defers_error_until_current_chunk_is_consumed() {
        let mut reader = body_reader(vec![
            Ok(Bytes::from_static(b"ab")),
            Err(ApiError::BodyCanceled),
        ]);
        assert_eq!(reader.fill_buf().unwrap(), b"ab");
        reader.consume(1);
        assert_eq!(reader.fill_buf().unwrap(), b"b");
        reader.consume(1);
        let error = reader.fill_buf().unwrap_err();
        assert!(matches!(
            error.get_ref().unwrap().downcast_ref::<ApiError>(),
            Some(ApiError::BodyCanceled)
        ));
        assert!(reader.fill_buf().unwrap().is_empty());
    }

    struct HeldWriter {
        entered: Option<blocking::Sender<()>>,
        release: blocking::Receiver<()>,
        bytes: Vec<u8>,
    }
    impl Write for HeldWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            if let Some(entered) = self.entered.take() {
                entered.send(()).unwrap();
                self.release.recv_timeout(Duration::from_secs(2)).unwrap();
            }
            self.bytes.extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn stalled_writer_case(json: bool) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let (closed, observed_close) = blocking::channel();
        let body = if json {
            let mut bytes = b"[]".to_vec();
            bytes.extend(vec![b'x'; 256 * 1024]);
            bytes
        } else {
            vec![b'x'; 1024 * 1024]
        };
        let body_len = body.len();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                stream.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
            }
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    )
                    .as_bytes(),
                )
                .unwrap();
            let _ = stream.write_all(&body);
            let mut byte = [0];
            let canceled = match stream.read(&mut byte) {
                Ok(0) => true,
                Err(error) if error.kind() == io::ErrorKind::ConnectionReset => true,
                _ => false,
            };
            closed.send(canceled).unwrap();
        });
        let (entered, wait_entered) = blocking::channel();
        let (release, wait_release) = blocking::channel();
        let controller = thread::spawn(move || {
            wait_entered.recv_timeout(Duration::from_secs(1)).unwrap();
            // The network body must close while stdout is still held. Waiting
            // for the writer to resume before canceling would fail causally.
            let canceled = observed_close.recv_timeout(Duration::from_secs(2)).unwrap();
            release.send(()).unwrap();
            assert!(canceled, "body was not canceled while stdout was blocked");
        });
        let profile = ProfileRecord {
            url: endpoint,
            api_key: "fixture-key".into(),
            ..Default::default()
        };
        let path = std::env::temp_dir()
            .join(format!(
                "pb-sql-bridge-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ))
            .join("config.toml");
        let store = ProfileStore::new(path);
        let client = ApiClient::new(profile, store).unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let request = client
            .prepare(
                http::Method::GET,
                &["api/v1/query"],
                None,
                Duration::from_millis(100),
            )
            .unwrap();
        let response = runtime.block_on(client.send_streaming(request)).unwrap();
        let mut writer = HeldWriter {
            entered: Some(entered),
            release: wait_release,
            bytes: Vec::new(),
        };
        let result = render_response(
            &runtime,
            response,
            if json { "json" } else { "text" },
            &mut writer,
        );
        if json {
            assert!(
                result.is_ok(),
                "completed JSON must ignore canceled unread tail"
            );
            assert_eq!(writer.bytes, b"[]\n");
        } else {
            assert!(!writer.bytes.is_empty());
            assert!(writer.bytes.len() < body_len);
            assert!(writer.bytes.len() <= 2 * TEXT_BRIDGE_CHUNK + 16);
            let Err(Failure::Output(output::OutputError {
                kind: output::OutputErrorKind::Io(error),
                ..
            })) = result
            else {
                panic!("stalled text output did not observe timeout");
            };
            let cause = error.get_ref().unwrap().downcast_ref::<ApiError>().unwrap();
            assert!(cause.is_timeout());
            assert!(matches!(cause, ApiError::BodyCanceled));
        }
        controller.join().unwrap();
        server.join().unwrap();
    }

    #[test]
    fn blocked_stdout_preserves_body_deadline_and_partial_output() {
        stalled_writer_case(false);
    }
    #[test]
    fn completed_json_wins_over_deadline_after_blocked_stdout_resumes() {
        stalled_writer_case(true);
    }
}
