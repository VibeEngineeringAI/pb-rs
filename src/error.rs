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
    pub(crate) raw_message: Option<Box<[u8]>>,
    pub(crate) raw_stdout: Option<Box<[u8]>>,
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
            raw_message: None,
            raw_stdout: None,
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
            raw_message: None,
            raw_stdout: None,
        }
    }

    pub fn status(action: &str, status: u16, wire_status: &str, body: &[u8]) -> Self {
        let public = format!("{action} failed: {wire_status}");
        let body = trim_bytes(body);
        let mut message = public.as_bytes().to_vec();
        if !body.is_empty() {
            message.extend_from_slice(b": ");
            message.extend_from_slice(body);
        }
        Self::http(status, go_string(&message), public).with_raw_message(message)
    }

    pub(crate) fn with_raw_message(mut self, message: Vec<u8>) -> Self {
        self.raw_message = Some(message.into_boxed_slice());
        self
    }

    pub(crate) fn message_bytes(&self) -> &[u8] {
        self.raw_message
            .as_deref()
            .unwrap_or(self.message.as_bytes())
    }

    pub(crate) fn stdout_bytes(&self) -> &[u8] {
        self.raw_stdout.as_deref().unwrap_or(self.stdout.as_bytes())
    }

    pub fn render(&self, command: &CommandSpec, path: &str, structured: bool) -> Outcome {
        #[derive(Serialize)]
        struct Detail<'a> {
            code: ErrorCode,
            message: &'a serde_json::value::RawValue,
            #[serde(skip_serializing_if = "is_zero")]
            http_status: u16,
            retryable: bool,
        }
        #[derive(Serialize)]
        struct Envelope<'a> {
            error: Detail<'a>,
        }
        let stderr = if structured {
            let public_bytes = if self.public_message == self.message {
                self.message_bytes()
            } else {
                self.public_message.as_bytes()
            };
            let public_json =
                serde_json::value::RawValue::from_string(json_string_bytes(public_bytes))
                    .expect("encoded JSON string");
            render::json(
                &Envelope {
                    error: Detail {
                        code: self.code,
                        message: &public_json,
                        http_status: self.http_status,
                        retryable: self.retryable,
                    },
                },
                false,
            )
            .into_bytes()
        } else {
            let mut value = b"Error: ".to_vec();
            value.extend_from_slice(self.message_bytes());
            value.push(b'\n');
            if !command.silence_usage {
                value.extend_from_slice(render::usage(command, path).as_bytes());
            }
            value
        };
        Outcome {
            stdout: self.stdout_bytes().to_vec(),
            stderr,
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

/// Go strings preserve invalid UTF-8 until JSON encoding, which replaces each
/// invalid byte separately (Rust's lossy conversion can combine a prefix).
pub(crate) fn go_string(bytes: &[u8]) -> String {
    let mut text = String::new();
    crate::gojson::push_string_bytes(&mut text, bytes);
    text
}

fn trim_bytes(mut bytes: &[u8]) -> &[u8] {
    // Invalid bytes are not whitespace and stop trimming on that side.
    loop {
        let prefix = match std::str::from_utf8(bytes) {
            Ok(text) => text,
            Err(error) => std::str::from_utf8(&bytes[..error.valid_up_to()]).unwrap(),
        };
        match prefix.chars().next() {
            Some(c) if c.is_whitespace() => bytes = &bytes[c.len_utf8()..],
            _ => break,
        }
    }
    loop {
        let start = bytes.len().saturating_sub(4);
        let last = (start..bytes.len()).find_map(|index| {
            std::str::from_utf8(&bytes[index..]).ok().and_then(|text| {
                let mut chars = text.chars();
                let c = chars.next()?;
                (chars.next().is_none()).then_some(c)
            })
        });
        match last {
            Some(c) if c.is_whitespace() => bytes = &bytes[..bytes.len() - c.len_utf8()],
            _ => break,
        }
    }
    bytes
}

fn json_string_bytes(mut bytes: &[u8]) -> String {
    let mut output = String::from("\"");
    while !bytes.is_empty() {
        let (valid, invalid) = match std::str::from_utf8(bytes) {
            Ok(text) => (text, false),
            Err(error) => (
                std::str::from_utf8(&bytes[..error.valid_up_to()]).unwrap(),
                true,
            ),
        };
        let quoted = serde_json::to_string(valid).expect("serializable string");
        output.push_str(&quoted[1..quoted.len() - 1]);
        bytes = &bytes[valid.len()..];
        if invalid {
            output.push_str("\\ufffd");
            bytes = &bytes[1..];
        }
    }
    output.push('"');
    output
}

#[cfg(test)]
mod byte_tests {
    use super::*;

    #[test]
    fn status_trims_go_whitespace_without_replacing_response_bytes() {
        let error = CliError::status(
            "delete user",
            403,
            "403 Forbidden",
            b" \xc2\xa0\xff\xe2\x82\t\n",
        );
        assert_eq!(
            error.message_bytes(),
            b"delete user failed: 403 Forbidden: \xff\xe2\x82"
        );
        assert_eq!(error.public_message, "delete user failed: 403 Forbidden");
        assert_eq!(trim_bytes(b" \xff \xc2\xa0"), b"\xff");
    }

    #[test]
    fn json_escapes_invalid_bytes_individually_but_keeps_valid_replacement_runes() {
        assert_eq!(
            json_string_bytes(b"\xff\xe2\x82\xef\xbf\xbd\0\""),
            "\"\\ufffd\\ufffd\\ufffd\u{fffd}\\u0000\\\"\""
        );
    }
}
