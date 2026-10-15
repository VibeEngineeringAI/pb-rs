//! Live tail owns command policy; Flight transport, subscription decoding and
//! Go-compatible value rendering stay in separate modules.
#[cfg(feature = "diagnostics")]
pub(crate) mod resources;
mod session;
mod transport;
mod validation;
mod values;

use crate::{
    ErrorCode, Outcome, config::ProfileStore, error::CliError, parser::ParsedInvocation,
    spec::CommandSpec,
};
use std::io::Write;

/// `pb tail DATASET`. A session ends only with a failure: Go's loop has no
/// successful exit, and process signals keep their default disposition.
pub(crate) fn execute(
    parsed: &ParsedInvocation,
    command: &CommandSpec,
    path: &str,
    structured: bool,
    stdout: &mut (dyn Write + Send),
    stderr: &mut (dyn Write + Send),
) -> Outcome {
    // Go validates output in RunE, after the profile preflight lib.rs ran.
    let json = match crate::output_format(parsed) {
        Ok(format) => format == "json",
        Err(message) => {
            return crate::failure(
                command,
                path,
                &message,
                ErrorCode::InvalidInput,
                structured,
                false,
            );
        }
    };
    let dataset = parsed.positionals.first().map(String::as_str).unwrap_or("");
    let store = match ProfileStore::from_env() {
        Ok(store) => store,
        Err(err) => return CliError::new(err.code, err.message).render(command, path, structured),
    };
    match session::run(&store, dataset, json, stdout, stderr) {
        session::Terminal::Failed(error) => error.render(command, path, structured),
        session::Terminal::Panicked(message) => go_panic(&message),
    }
}

/// Go's array.RecordToJSON can panic for some nested non-finite floats; the
/// uncaught panic terminates with status 2 and no command error rendering.
/// The goroutine trace is not reproduced (documented divergence).
fn go_panic(message: &str) -> Outcome {
    Outcome {
        stderr: format!("panic: {message}\n\ngoroutine 1 [running]:\n").into_bytes(),
        exit_code: 2,
        ..Outcome::default()
    }
}
