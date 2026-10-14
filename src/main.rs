use std::ffi::OsString;
use std::io::{self, IsTerminal, Write};

#[cfg(feature = "diagnostics")]
#[global_allocator]
static DIAGNOSTIC_ALLOCATOR: pb::diagnostics::CountingAllocator =
    pb::diagnostics::CountingAllocator;

fn invalid_utf8(args: &[OsString], index: usize) -> pb::Outcome {
    // The library's phase-one boundary is UTF-8. Keep byte-valued argv an
    // explicit unsupported case until the parser and Outcome can own bytes.
    // Lossy strings are used only for the raw JSON-output request scan.
    let scan: Vec<String> = args
        .iter()
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect();
    let message = format!("argument {} is not valid UTF-8", index + 1);
    let stderr = if pb::parser::requests_json(&scan) {
        let detail = serde_json::json!({
            "error": {
                "code": "INVALID_INPUT",
                "message": message,
                "retryable": false,
            }
        });
        format!(
            "{}\n",
            serde_json::to_string_pretty(&detail).expect("serializable error")
        )
    } else {
        format!("Error: {message}\n")
    };
    pb::Outcome {
        stdout: Vec::new(),
        stderr: stderr.into_bytes(),
        exit_code: 1,
    }
}

fn invocation() -> pb::Outcome {
    let raw: Vec<OsString> = std::env::args_os().skip(1).collect();
    let mut args = Vec::with_capacity(raw.len());
    for (index, arg) in raw.iter().enumerate() {
        match arg.to_str() {
            Some(arg) => args.push(arg.to_owned()),
            None => return invalid_utf8(&raw, index),
        }
    }
    pb::run_with_writers(
        &args,
        &mut SignalWriter(StreamingStdout::new(), "stdout"),
        &mut SignalWriter(io::stderr(), "stderr"),
        io::stderr().is_terminal(),
    )
}

// SQL already chooses its streaming write/flush boundaries. Stdout's internal
// LineWriter otherwise splits each pretty-printed row again at its last LF.
// Borrow fd 1 for each syscall without taking ownership or changing its flags.
// SignalWriter supplies poll/retry, error context and SIGPIPE handling. No
// standard-stdout data is buffered before invocation; Outcome is written later.
struct StreamingStdout {
    #[cfg(not(unix))]
    inner: io::Stdout,
}
impl StreamingStdout {
    fn new() -> Self {
        Self {
            #[cfg(not(unix))]
            inner: io::stdout(),
        }
    }
}
impl Write for StreamingStdout {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        #[cfg(unix)]
        {
            // The byte slice stays valid for this synchronous call. Cap the
            // count to the syscall's signed result range; write_all retries
            // any remainder. A closed/read-only fd is an ordinary I/O error.
            let count = unsafe {
                libc::write(
                    libc::STDOUT_FILENO,
                    bytes.as_ptr().cast(),
                    bytes.len().min(isize::MAX as usize),
                )
            };
            if count < 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(count as usize)
            }
        }
        #[cfg(not(unix))]
        self.inner.write(bytes)
    }
    fn flush(&mut self) -> io::Result<()> {
        #[cfg(unix)]
        return Ok(());
        #[cfg(not(unix))]
        self.inner.flush()
    }
}

fn pipe_result<T>(result: io::Result<T>) -> io::Result<T> {
    #[cfg(unix)]
    if result
        .as_ref()
        .is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe)
    {
        // Rust starts with SIGPIPE ignored. Restore it only after an actual
        // broken output write, preserving the normal Unix pipeline exit status.
        unsafe {
            libc::signal(libc::SIGPIPE, libc::SIG_DFL);
            libc::raise(libc::SIGPIPE);
        }
        // Retain a failure exit if restoring or delivering the signal failed.
        std::process::exit(1);
    }
    result
}

struct SignalWriter<W>(W, &'static str);

fn output_retry<T>(
    stream: &'static str,
    mut operation: impl FnMut() -> io::Result<T>,
) -> io::Result<T> {
    loop {
        match operation() {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            #[cfg(unix)]
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                // A launcher can hand us an O_NONBLOCK pipe. Wait without
                // changing its shared flags or discarding JSON output on EAGAIN.
                let mut descriptor = libc::pollfd {
                    fd: if stream == "stdout" {
                        libc::STDOUT_FILENO
                    } else {
                        libc::STDERR_FILENO
                    },
                    events: libc::POLLOUT,
                    revents: 0,
                };
                loop {
                    let ready = unsafe { libc::poll(&mut descriptor, 1, -1) };
                    if ready >= 0 {
                        break;
                    }
                    let error = io::Error::last_os_error();
                    if error.kind() != io::ErrorKind::Interrupted {
                        return Err(error);
                    }
                }
            }
            result => return pipe_result(result),
        }
    }
}

impl<W: Write> Write for SignalWriter<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        #[cfg(feature = "diagnostics")]
        let _timer = pb::diagnostics::Timer::begin(pb::diagnostics::Stage::OutputWrite);
        let result = output_retry(self.1, || self.0.write(bytes)).map_err(|source| {
            std::io::Error::new(
                source.kind(),
                pb::OutputWriteError {
                    stream: self.1,
                    source,
                },
            )
        });
        #[cfg(feature = "diagnostics")]
        pb::diagnostics::write_count(bytes.len(), result.as_ref().ok().copied().unwrap_or(0));
        let count = result?;
        self.flush()?;
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        #[cfg(feature = "diagnostics")]
        let _timer = pb::diagnostics::Timer::begin(pb::diagnostics::Stage::OutputFlush);
        #[cfg(feature = "diagnostics")]
        pb::diagnostics::flush_count();
        output_retry(self.1, || self.0.flush()).map_err(|source| {
            std::io::Error::new(
                source.kind(),
                pb::OutputWriteError {
                    stream: self.1,
                    source,
                },
            )
        })
    }
}
fn write_output(mut writer: impl Write, bytes: &[u8]) -> io::Result<()> {
    if bytes.is_empty() {
        return Ok(());
    }
    pipe_result(writer.write_all(bytes).and_then(|()| writer.flush()))
}
// Go's syscall package init raises the soft open-file limit for every program
// (go1.25 syscall/rlimit.go:30-47): when hard > 0 and soft < hard-1, soft
// becomes hard-1, clamped on Apple to kern.maxfilesperproc
// (rlimit_darwin.go:10-20).
// Inventory detail reads fan out one connection per entry, so the inherited
// soft limit (often 1024, or 256 on macOS) would fail with EMFILE where Go
// succeeds. Go restores the original limit only in children it execs; pb
// starts no child processes, so nothing is retained. Best effort, and never
// lowers the limit: one getrlimit, at most one setrlimit.
#[cfg(unix)]
fn raise_open_file_limit() {
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) } != 0
        || limit.rlim_max == 0
        || limit.rlim_cur >= limit.rlim_max - 1
    {
        return;
    }
    #[allow(unused_mut)]
    let mut soft = limit.rlim_max - 1;
    #[cfg(target_vendor = "apple")]
    {
        let mut per_process: u32 = 0;
        let mut size = std::mem::size_of::<u32>();
        let status = unsafe {
            libc::sysctlbyname(
                c"kern.maxfilesperproc".as_ptr(),
                (&raw mut per_process).cast(),
                &mut size,
                std::ptr::null_mut(),
                0,
            )
        };
        if status == 0 && size == std::mem::size_of::<u32>() {
            soft = soft.min(libc::rlim_t::from(per_process));
        }
    }
    if soft > limit.rlim_cur {
        limit.rlim_cur = soft;
        unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &limit) };
    }
}

fn main() {
    #[cfg(unix)]
    raise_open_file_limit();
    pb::initialize_process_theme();
    #[cfg(feature = "diagnostics")]
    let diagnostics = pb::diagnostics::Session::begin();
    let outcome = invocation();
    let stdout = write_output(SignalWriter(io::stdout().lock(), "stdout"), &outcome.stdout);
    let stderr = write_output(SignalWriter(io::stderr().lock(), "stderr"), &outcome.stderr);
    let code = if stdout.is_err() || stderr.is_err() {
        1
    } else {
        outcome.exit_code
    };
    #[cfg(feature = "diagnostics")]
    {
        pb::diagnostics::measure(pb::diagnostics::Stage::Drop, || drop(outcome));
        diagnostics.finish(code);
    }
    std::process::exit(code);
}
