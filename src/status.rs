//! Connection checks own their failure output, including unhealthy JSON on stdout.
use crate::{Outcome, api::ApiClient, config::ProfileStore, gojson, render};
use http::Method;
use serde::Serialize;

#[derive(Serialize)]
struct Status<'a> {
    status: &'static str,
    healthy: bool,
    #[serde(skip_serializing_if = "str::is_empty")]
    profile: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    url: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    version: &'a str,
    #[serde(skip_serializing_if = "str::is_empty")]
    error: &'a str,
}

pub async fn execute(store: &ProfileStore, json: bool) -> Result<Outcome, (String, String)> {
    let conf = match store.read() {
        Ok(conf) => conf,
        Err(_) => return failed(json, "", "", "no profile configured. run: pb login"),
    };
    let name = &conf.default_profile;
    let Some(profile) = conf.profiles.get(name).filter(|_| !name.is_empty()) else {
        return failed(json, "", "", "no active profile. run: pb login");
    };
    let result = async {
        let client = ApiClient::new(profile.clone(), store.clone()).map_err(|e| e.to_string())?;
        let response = client
            .standard(Method::GET, "about", None)
            .await
            .map_err(|e| e.to_string())?;
        match response.status {
            200 => about_version(&response.body),
            401 | 403 => Err("Authentication failed: invalid username/password or API key".into()),
            _ => Err(format!(
                "Request Failed\nStatus Code: {}\nResponse: {}\n",
                response.status_text(),
                String::from_utf8_lossy(&response.body)
            )),
        }
    }
    .await;
    match result {
        Ok(version) => Ok(Outcome::success(if json {
            render::json(
                &Status {
                    status: "ok",
                    healthy: true,
                    profile: name,
                    url: &profile.url,
                    version: &version,
                    error: "",
                },
                false,
            )
        } else {
            format!(
                "Profile : {name}\nURL     : {}\nStatus  : ✓ Connected\nVersion : {version}\n",
                profile.url
            )
        })),
        Err(message) => failed(json, name, &profile.url, &message),
    }
}

fn failed(
    json: bool,
    profile: &str,
    url: &str,
    message: &str,
) -> Result<Outcome, (String, String)> {
    if json {
        return Ok(Outcome {
            stdout: render::json(
                &Status {
                    status: "error",
                    healthy: false,
                    profile,
                    url,
                    version: "",
                    error: message,
                },
                false,
            ),
            exit_code: 1,
            ..Outcome::default()
        });
    }
    if profile.is_empty() {
        return Err((message.into(), String::new()));
    }
    Err((
        format!("status check failed: {message}"),
        format!(
            "Profile : {profile}\nURL     : {url}\nStatus  : ✗ Not connected\nError   : {message}\n"
        ),
    ))
}

// The Go about decoder checks all known fields, even those status doesn't show.
fn about_version(body: &[u8]) -> Result<String, String> {
    let value = gojson::decode_ordered(body)?;
    if value.is_null() {
        return Ok(String::new());
    }
    let object = value.as_object().ok_or_else(|| {
        format!(
            "json: cannot unmarshal {} into Go value of type analytics.About",
            value.kind()
        )
    })?;
    let mut version = String::new();
    for (name, value) in object {
        let lower = name.to_ascii_lowercase();
        let (canonical, expected) = match lower.as_str() {
            "version" => ("version", "string"),
            "uiversion" => ("uiVersion", "string"),
            "commit" => ("commit", "string"),
            "deploymentid" => ("deploymentId", "string"),
            "updateavailable" => ("updateAvailable", "bool"),
            "latestversion" => ("latestVersion", "string"),
            "llmactive" => ("llmActive", "bool"),
            "llmprovider" => ("llmProvider", "string"),
            "oidcactive" => ("oidcActive", "bool"),
            "mode" => ("mode", "string"),
            "staging" => ("staging", "string"),
            "hottier" => ("hotTier", "string"),
            "grpcport" => ("grpcPort", "int"),
            "queryengine" => ("queryEngine", "string"),
            "store" => ("store", "analytics.Store"),
            "analytics" => ("analytics", "analytics.Analytics"),
            _ => continue,
        };
        if value.is_null() {
            continue;
        }
        let valid = match expected {
            "string" => value.is_string(),
            "bool" => value.is_boolean(),
            "int" => value.as_i64().is_some(),
            _ => value.is_object(),
        };
        if !valid {
            let mut actual = value.kind().to_owned();
            if expected == "int" && value.is_number() {
                actual.push(' ');
                actual.push_str(&value.to_string());
            }
            return Err(format!(
                "json: cannot unmarshal {actual} into Go struct field About.{canonical} of type {expected}"
            ));
        }
        if lower == "version" {
            version = value.as_str().unwrap().into();
        }
        if let Some(nested) = value.as_object() {
            for (field, value) in nested {
                let canonical_field = match (lower.as_str(), field.to_ascii_lowercase().as_str()) {
                    ("store", "type") => "type",
                    ("store", "path") => "path",
                    ("analytics", "claritytag") => "clarityTag",
                    _ => continue,
                };
                if !value.is_string() && !value.is_null() {
                    return Err(format!(
                        "json: cannot unmarshal {} into Go struct field {}.{canonical}.{canonical_field} of type string",
                        value.kind(),
                        if lower == "store" {
                            "Store"
                        } else {
                            "Analytics"
                        }
                    ));
                }
            }
        }
    }
    Ok(version)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    #[test]
    fn about_processes_every_duplicate_field_in_input_order() {
        assert_eq!(
            about_version(br#"{"version":7,"version":"ok"}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field About.version of type string"
        );
        assert_eq!(
            about_version(br#"{"version":"first","Version":"middle","version":"last"}"#).unwrap(),
            "last"
        );
        assert_eq!(
            about_version(br#"{"version":"first","version":null}"#).unwrap(),
            "first"
        );
        assert_eq!(
            about_version(br#"{"store":{"type":7},"store":{"type":"ok"}}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field Store.store.type of type string"
        );
    }

    #[test]
    fn about_checks_unshown_fields_and_keeps_go_null_defaults() {
        assert_eq!(
            about_version(br#"{"Version":"v1","license":{"opaque":true},"grpcPort":null}"#)
                .unwrap(),
            "v1"
        );
        assert_eq!(about_version(b"null").unwrap(), "");
        assert_eq!(
            about_version(br#"{"version":"v1","grpcPort":1.5}"#).unwrap_err(),
            "json: cannot unmarshal number 1.5 into Go struct field About.grpcPort of type int"
        );
        assert_eq!(
            about_version(br#"{"version":"v1","store":{"type":7}}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field Store.store.type of type string"
        );
    }

    #[test]
    fn unhealthy_json_owns_stdout_and_exit() {
        let result = failed(true, "fixture", "http://localhost", "denied").unwrap();
        assert_eq!(result.exit_code, 1);
        assert!(result.stderr.is_empty());
        let value: Value = serde_json::from_str(&result.stdout).unwrap();
        assert_eq!(value["healthy"], false);
        assert_eq!(value["error"], "denied");
        assert!(value.get("version").is_none());
    }
}
