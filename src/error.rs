//! Public failures carry command policy; transport and storage keep their own errors.
use crate::{ErrorCode, Outcome, render, spec::CommandSpec};
use serde::Serialize;

#[derive(Debug)]
pub struct CliError {
    pub code: ErrorCode,
    pub message: String,
    pub public_message: String,
    pub http_status: u16,
    pub retryable: bool,
    pub stdout: String,
}

impl CliError {
    pub(crate) fn output(context: &str, error: std::io::Error) -> Self {
        // Go classifies the stdout PathError through net.Error. Preserve that
        // public category only for the process writer's wrapped OS errors.
        let code = if error
            .get_ref()
            .is_some_and(|e| e.is::<crate::OutputWriteError>())
        {
            ErrorCode::ConnectionFailed
        } else {
            ErrorCode::CommandFailed
        };
        Self::new(code, format!("{context}: {error}"))
    }

    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        let message = message.into();
        Self {
            code,
            public_message: message.clone(),
            message,
            http_status: 0,
            retryable: code == ErrorCode::ConnectionFailed,
            stdout: String::new(),
        }
    }

    pub fn http(
        status: u16,
        message: impl Into<String>,
        public_message: impl Into<String>,
    ) -> Self {
        let (code, retryable) = match status {
            400 | 422 => (ErrorCode::InvalidInput, false),
            401 => (ErrorCode::AuthFailed, false),
            403 => (ErrorCode::PermissionDenied, false),
            404 => (ErrorCode::NotFound, false),
            408 | 504 => (ErrorCode::Timeout, true),
            409 => (ErrorCode::Conflict, false),
            429 => (ErrorCode::RateLimited, true),
            500.. => (ErrorCode::ServerError, true),
            _ => (ErrorCode::CommandFailed, false),
        };
        Self {
            code,
            retryable,
            http_status: status,
            message: message.into(),
            public_message: public_message.into(),
            stdout: String::new(),
        }
    }

    pub fn status(action: &str, status: u16, wire_status: &str, body: &[u8]) -> Self {
        let public = format!("{action} failed: {wire_status}");
        let detail = String::from_utf8_lossy(body);
        let detail = detail.trim();
        let message = if detail.is_empty() {
            public.clone()
        } else {
            format!("{public}: {detail}")
        };
        Self::http(status, message, public)
    }

    pub fn render(&self, command: &CommandSpec, path: &str, structured: bool) -> Outcome {
        #[derive(Serialize)]
        struct Detail<'a> {
            code: ErrorCode,
            message: &'a str,
            #[serde(skip_serializing_if = "is_zero")]
            http_status: u16,
            retryable: bool,
        }
        #[derive(Serialize)]
        struct Envelope<'a> {
            error: Detail<'a>,
        }
        let stderr = if structured {
            render::json(
                &Envelope {
                    error: Detail {
                        code: self.code,
                        message: &self.public_message,
                        http_status: self.http_status,
                        retryable: self.retryable,
                    },
                },
                false,
            )
        } else {
            let mut value = format!("Error: {}\n", self.message);
            if !command.silence_usage {
                value.push_str(&render::usage(command, path));
            }
            value
        };
        Outcome {
            stdout: self.stdout.as_bytes().to_vec(),
            stderr: stderr.into_bytes(),
            exit_code: 1,
        }
    }
}

fn is_zero(value: &u16) -> bool {
    *value == 0
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for CliError {}

impl From<crate::profile::ProfileError> for CliError {
    fn from(error: crate::profile::ProfileError) -> Self {
        let mut result = Self::new(error.code, error.message);
        result.stdout = error.stdout;
        result
    }
}

impl From<crate::api::ApiError> for CliError {
    fn from(error: crate::api::ApiError) -> Self {
        use crate::api::ApiError;
        let message = error.to_string();
        if error.is_timeout() {
            let mut result = Self::new(ErrorCode::Timeout, message);
            result.retryable = true;
            return result;
        }
        if let ApiError::Persistence(cause) = error.root_cause()
            && matches!(
                cause.code,
                ErrorCode::NotFound | ErrorCode::PermissionDenied
            )
        {
            return Self::new(cause.code, message);
        }
        if error.is_request_error()
            || matches!(error.root_cause(), ApiError::Network(source) if source.is_connection_error())
        {
            let mut result = Self::new(ErrorCode::ConnectionFailed, message);
            result.retryable = true;
            return result;
        }
        match error {
            ApiError::Persistence(cause) => Self::new(cause.code, message),
            _ => Self::new(ErrorCode::CommandFailed, message),
        }
    }
}
