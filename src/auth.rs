//! Credential resolution is deferred until a profile makes a request.
use crate::config::ProfileRecord;
use base64::{Engine, engine::general_purpose::STANDARD};
use http::header::{AUTHORIZATION, COOKIE, HeaderMap, HeaderValue};
use std::{error::Error, fmt};

#[derive(Clone, PartialEq)]
pub enum Auth {
    SelfHostedBasic {
        username: String,
        password: String,
    },
    SelfHostedApiKey {
        api_key: String,
    },
    CloudApiKey {
        api_key: String,
        tenant_id: String,
    },
    CloudOAuth {
        session_token: String,
        tenant_id: String,
    },
}
impl fmt::Debug for Auth {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::SelfHostedBasic { .. } => "SelfHostedBasic(<redacted>)",
            Self::SelfHostedApiKey { .. } => "SelfHostedApiKey(<redacted>)",
            Self::CloudApiKey { .. } => "CloudApiKey(<redacted>)",
            Self::CloudOAuth { .. } => "CloudOAuth(<redacted>)",
        })
    }
}
#[derive(Clone, Debug, PartialEq)]
pub struct AuthError(pub String);
impl fmt::Display for AuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl Error for AuthError {}

impl Auth {
    pub fn resolve(p: &ProfileRecord) -> Result<Self, AuthError> {
        let basic = !p.username.is_empty() || !p.password.is_empty();
        let key = !p.api_key.is_empty();
        let oauth = !p.session_token.is_empty();
        let fail = |message: &str| Err(AuthError(message.into()));
        if p.cloud {
            if p.tenant_id.is_empty() {
                return fail("cloud profile missing tenant_id");
            }
            if basic {
                return fail("cloud profile cannot use self-hosted credentials");
            }
            return match (key, oauth) {
                (true, false) => Ok(Self::CloudApiKey {
                    api_key: p.api_key.clone(),
                    tenant_id: p.tenant_id.clone(),
                }),
                (false, true) => Ok(Self::CloudOAuth {
                    session_token: p.session_token.clone(),
                    tenant_id: p.tenant_id.clone(),
                }),
                (true, true) => fail("cloud profile has both apiKey and session_token"),
                _ => fail("cloud profile missing apiKey or session_token"),
            };
        }
        if oauth || !p.tenant_id.is_empty() {
            return fail("self-hosted profile cannot use cloud credentials");
        }
        match (basic, key) {
            (true, false) if p.username.is_empty() || p.password.is_empty() => {
                fail("self-hosted basic profile missing username or password")
            }
            (true, false) => Ok(Self::SelfHostedBasic {
                username: p.username.clone(),
                password: p.password.clone(),
            }),
            (false, true) => Ok(Self::SelfHostedApiKey {
                api_key: p.api_key.clone(),
            }),
            (true, true) => fail("self-hosted profile has both basic credentials and api key"),
            _ => fail("self-hosted profile missing credentials"),
        }
    }

    pub fn headers(&self) -> Result<HeaderMap, AuthError> {
        let mut headers = HeaderMap::new();
        match self {
            Self::SelfHostedBasic { username, password } => {
                headers.insert(
                    AUTHORIZATION,
                    value(
                        &format!(
                            "Basic {}",
                            STANDARD.encode(format!("{username}:{password}"))
                        ),
                        true,
                    )?,
                );
            }
            Self::SelfHostedApiKey { api_key } => {
                headers.insert("x-api-key", value(api_key, true)?);
            }
            Self::CloudApiKey { api_key, tenant_id } => {
                headers.insert("x-api-key", value(api_key, true)?);
                headers.insert("x-p-tenant", value(tenant_id, false)?);
            }
            Self::CloudOAuth {
                session_token,
                tenant_id,
            } => {
                headers.insert(
                    COOKIE,
                    value(&format!("session={}", cookie_value(session_token)), true)?,
                );
                headers.insert("x-p-tenant", value(tenant_id, false)?);
            }
        }
        Ok(headers)
    }
}

fn value(raw: &str, sensitive: bool) -> Result<HeaderValue, AuthError> {
    let mut header =
        HeaderValue::from_str(raw).map_err(|_| AuthError("invalid HTTP header value".into()))?;
    header.set_sensitive(sensitive);
    Ok(header)
}

pub fn orchestrator_headers(token: &str) -> Result<HeaderMap, AuthError> {
    let token = token.trim();
    if token.is_empty() {
        return Err(AuthError(
            "cloud orchestrator auth token is not configured".into(),
        ));
    }
    let mut headers = HeaderMap::new();
    headers.insert(AUTHORIZATION, value(&format!("Bearer {token}"), true)?);
    Ok(headers)
}

/// Go's Cookie.String removes invalid octets and quotes spaces and commas.
fn cookie_value(raw: &str) -> String {
    let sanitized: String = raw
        .chars()
        .filter(|c| c.is_ascii() && (' '..='~').contains(c) && !matches!(c, '"' | ';' | '\\'))
        .collect();
    if sanitized.contains([' ', ',']) {
        format!("\"{sanitized}\"")
    } else {
        sanitized
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validation_order_and_headers() {
        let mut p = ProfileRecord {
            cloud: true,
            username: "user".into(),
            ..Default::default()
        };
        assert_eq!(
            Auth::resolve(&p).unwrap_err().to_string(),
            "cloud profile missing tenant_id"
        );
        p.tenant_id = "tenant".into();
        assert_eq!(
            Auth::resolve(&p).unwrap_err().to_string(),
            "cloud profile cannot use self-hosted credentials"
        );
        p.username.clear();
        p.api_key = "secret".into();
        let auth = Auth::resolve(&p).unwrap();
        let headers = auth.headers().unwrap();
        assert_eq!(headers["x-api-key"], "secret");
        assert_eq!(headers["x-p-tenant"], "tenant");
        assert!(headers["x-api-key"].is_sensitive());
        assert!(!format!("{auth:?}").contains("secret"));
        p.session_token = "session".into();
        assert_eq!(
            Auth::resolve(&p).unwrap_err().to_string(),
            "cloud profile has both apiKey and session_token"
        );
    }
    #[test]
    fn basic_and_cookie_encoding() {
        let auth = Auth::SelfHostedBasic {
            username: "alice".into(),
            password: "p:a:ss".into(),
        };
        assert_eq!(
            auth.headers().unwrap()[AUTHORIZATION],
            "Basic YWxpY2U6cDphOnNz"
        );
        let auth = Auth::CloudOAuth {
            session_token: "a b,c;\"\\\né".into(),
            tenant_id: "t".into(),
        };
        assert_eq!(auth.headers().unwrap()[COOKIE], "session=\"a b,c\"");
        assert_eq!(
            orchestrator_headers("  token  ").unwrap()[AUTHORIZATION],
            "Bearer token"
        );
        assert!(orchestrator_headers(" ").is_err());
    }
}
