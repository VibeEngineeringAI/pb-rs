use std::ffi::OsString;
use std::io::{self, Write};

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
        stdout: String::new(),
        stderr,
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
    pb::run(&args)
}

fn write_output(mut writer: impl Write, bytes: &[u8]) -> io::Result<()> {
    let result = writer.write_all(bytes).and_then(|()| writer.flush());
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

fn main() {
    let outcome = invocation();
    let stdout = write_output(io::stdout().lock(), outcome.stdout.as_bytes());
    let stderr = write_output(io::stderr().lock(), outcome.stderr.as_bytes());
    let code = if stdout.is_err() || stderr.is_err() {
        1
    } else {
        outcome.exit_code
    };
    std::process::exit(code);
}
