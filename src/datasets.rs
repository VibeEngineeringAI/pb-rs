//! Dataset reads own their request order, typed response decoding, and output.
use crate::{
    ErrorCode,
    api::{ApiClient, ApiError},
    error::CliError,
    gojson::{self, Node},
    parser::{ParsedInvocation, quote},
    render,
};
use http::Method;
use serde::Serialize;
use serde_json::{Map, Number, Value};
use std::{collections::BTreeMap, str::FromStr};

pub async fn execute(parsed: &ParsedInvocation, client: &ApiClient) -> Result<String, CliError> {
    match parsed.command_path.as_str() {
        "pb dataset list" => list(parsed, client).await,
        "pb dataset info" => info(parsed, client).await,
        "pb dataset schema" => schema(parsed, client).await,
        path => Err(CliError::new(
            ErrorCode::CommandFailed,
            format!("unsupported dataset command {}", quote(path)),
        )),
    }
}

fn format(parsed: &ParsedInvocation) -> Result<bool, CliError> {
    match parsed.text("output").trim().to_ascii_lowercase().as_str() {
        "json" => Ok(true),
        "" | "text" => Ok(false),
        _ => Err(CliError::new(
            ErrorCode::InvalidInput,
            format!(
                "unsupported output format {} (expected text or json)",
                quote(parsed.text("output"))
            ),
        )),
    }
}
fn command_error(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::CommandFailed, message)
}
fn invalid_response(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::InvalidResponse, message)
}
fn decode(body: &[u8]) -> Result<gojson::OrderedDocument, CliError> {
    gojson::decode_ordered(body).map_err(command_error)
}

#[derive(Default, Debug, Serialize)]
pub(crate) struct Dataset {
    pub(crate) title: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    name: String,
    #[serde(rename = "datasetType")]
    pub(crate) dataset_type: String,
    #[serde(rename = "datasetFormat")]
    dataset_format: String,
    ingestion: bool,
}

async fn list(parsed: &ParsedInvocation, client: &ApiClient) -> Result<String, CliError> {
    let items = fetch_home(client).await?;
    // Go validates list/info output after fetching and decoding their responses.
    if format(parsed)? {
        Ok(render::json(&items, false))
    } else {
        Ok(items
            .into_iter()
            .map(|d| item_row(&d.title, &d.dataset_type))
            .collect())
    }
}

/// Shared Prism discovery decoder, including Go's title ordering and status
/// policy. Workflow-specific filtering belongs to the caller.
pub(crate) async fn fetch_home(client: &ApiClient) -> Result<Vec<Dataset>, CliError> {
    let response = client
        .prism(Method::GET, "home", None)
        .await
        .map_err(CliError::from)?;
    if response.status >= 400 {
        let body = String::from_utf8_lossy(&response.body);
        return Err(CliError::http(
            response.status,
            format!("HTTP {}: {}", response.status, body.trim()),
            format!("request failed: HTTP {}", response.status),
        ));
    }
    let value = decode(&response.body)?;
    dataset_list(&value)
}
fn dataset_list(value: &Node) -> Result<Vec<Dataset>, CliError> {
    let mut items = Vec::<Dataset>::new();
    let mut length = 0;
    if let Some(object) = object(value, "datasets.homeResponse", "", "")? {
        for (key, value) in object {
            if key_is(key, "datasets") {
                match array(value, "[]datasets.Dataset", "homeResponse", "datasets")? {
                    None => {
                        items.clear();
                        length = 0;
                    }
                    Some(values) => {
                        if values.is_empty() {
                            items.clear();
                        }
                        for (index, value) in values.iter().enumerate() {
                            if index == items.len() {
                                items.push(Dataset::default());
                            }
                            dataset(&mut items[index], value)?;
                        }
                        length = values.len();
                    }
                }
            }
        }
    }
    items.truncate(length);
    items.sort_by(|a, b| a.title.cmp(&b.title));
    Ok(items)
}
fn dataset(result: &mut Dataset, value: &Node) -> Result<(), CliError> {
    if let Some(fields) = object(value, "datasets.Dataset", "homeResponse", "datasets")? {
        for (key, value) in fields {
            match normalized(key).as_str() {
                "title" => {
                    if !value.is_null() {
                        result.title = string(value, "Dataset", "datasets.title")?;
                    }
                }
                "name" => {
                    if !value.is_null() {
                        result.name = string(value, "Dataset", "datasets.name")?;
                    }
                }
                "datasettype" => {
                    if !value.is_null() {
                        result.dataset_type = string(value, "Dataset", "datasets.datasetType")?;
                    }
                }
                "datasetformat" => {
                    if !value.is_null() {
                        result.dataset_format = string(value, "Dataset", "datasets.datasetFormat")?;
                    }
                }
                "ingestion" if !value.is_null() => {
                    result.ingestion = boolean(value, "Dataset", "datasets.ingestion")?;
                }
                _ => (),
            }
        }
    }
    Ok(())
}

#[derive(Default, Debug)]
struct Stats {
    count: i64,
    ingestion_size: u64,
    storage_size: u64,
}
#[derive(Default, Debug, Serialize)]
struct Retention {
    description: String,
    action: String,
    duration: String,
}
#[derive(Default, Debug, Serialize)]
struct Alert {
    targets: Option<Vec<Target>>,
    name: String,
    message: String,
    rule: Rule,
}
#[derive(Default, Debug, Serialize)]
struct Target {
    #[serde(rename = "type")]
    kind: String,
    endpoint: String,
    headers: Option<BTreeMap<String, String>>,
    skip_tls_check: bool,
    repeat: Repeat,
}
#[derive(Default, Debug, Serialize)]
struct Repeat {
    interval: String,
    times: i64,
}
#[derive(Default, Debug, Serialize)]
struct Rule {
    #[serde(rename = "type")]
    kind: String,
    config: RuleConfig,
}
#[derive(Default, Debug, Serialize)]
struct RuleConfig {
    column: String,
    operator: String,
    #[serde(rename = "ignoreCase")]
    ignore_case: bool,
    #[serde(serialize_with = "gojson::serialize_value")]
    value: Value,
    repeats: i64,
}

async fn info(parsed: &ParsedInvocation, client: &ApiClient) -> Result<String, CliError> {
    let name = &parsed.positionals[0];
    let response = client
        .standard(Method::GET, &format!("logstream/{name}/info"), None)
        .await
        .map_err(|error| {
            let prefix = match &error {
                ApiError::InvalidUrl(_) => "failed to create request",
                ApiError::Network(error) if error.is_body() => "failed to read response body",
                _ => "request execution failed",
            };
            let mut error = CliError::from(error);
            error.message = format!("{prefix}: {}", error.message);
            error.public_message = format!("{prefix}: {}", error.public_message);
            error
        })?;
    let dataset_type = match response.status {
        200 => decode_type(&response.body)?,
        404 => {
            let message = format!("dataset {} not found", quote(name));
            return Err(CliError::http(404, &message, &message));
        }
        status => {
            return Err(info_http_error(
                status,
                &response.status_text(),
                &response.body,
                true,
            ));
        }
    };
    let stats = match info_request(client, name, "stats").await? {
        Some(body) => decode_stats(&body)?,
        None => Stats::default(),
    };
    let retention = match info_request(client, name, "retention").await? {
        Some(body) => {
            let document = decode(&body)?;
            retention(&document)?
        }
        None => None,
    };
    let alerts = match info_request(client, name, "alert").await? {
        Some(body) => {
            let document = decode(&body)?;
            alerts(&document)?
        }
        None => Vec::new(),
    };
    let compression = if stats.ingestion_size > 0 && stats.storage_size > 0 {
        format!(
            "{:.2}%",
            100.0 - stats.storage_size as f64 / stats.ingestion_size as f64 * 100.0
        )
    } else {
        "n/a".into()
    };
    let ingestion_size = human_bytes(stats.ingestion_size);
    let storage_size = human_bytes(stats.storage_size);
    if format(parsed)? {
        #[derive(Serialize)]
        struct Info<'a> {
            compression_ratio: &'a str,
            event_count: i64,
            ingestion_size: &'a str,
            storage_size: &'a str,
        }
        #[derive(Serialize)]
        struct Output<'a> {
            alerts: &'a [Alert],
            dataset_type: &'a str,
            info: Info<'a>,
            retention: &'a Option<Vec<Retention>>,
        }
        Ok(render::json(
            &Output {
                alerts: &alerts,
                dataset_type: &dataset_type,
                info: Info {
                    compression_ratio: &compression,
                    event_count: stats.count,
                    ingestion_size: &ingestion_size,
                    storage_size: &storage_size,
                },
                retention: &retention,
            },
            false,
        ))
    } else {
        let mut output = plain_style("\nInfo:", 0) + "\n";
        for (label, value) in [
            ("Event Count:", stats.count.to_string()),
            ("Ingestion Size:", ingestion_size),
            ("Storage Size:", storage_size),
            ("Compression Ratio:", compression),
            ("Dataset Type:", plain_style(&dataset_type, 0)),
        ] {
            output.push_str(&format!("  {label:18} {value}\n"));
        }
        output.push('\n');
        if let Some(values) = retention.as_ref().filter(|v| !v.is_empty()) {
            output.push_str("Retention:\n");
            for value in values {
                output.push_str(&format!(
                    "  Action:    {}\n  Duration:  {}\n\n",
                    plain_style(&value.action, 0),
                    plain_style(&value.duration, 0)
                ));
            }
        } else {
            output.push_str(&(plain_style("No retention period set on dataset\n", 0) + "\n"));
        }
        if alerts.is_empty() {
            output.push_str(&(plain_style("No alerts set on dataset\n", 0) + "\n"));
        } else {
            output.push_str("Alerts:\n");
            for alert in alerts {
                let config = alert.rule.config;
                output.push_str(&format!(
                    "  Alert:   {}\n  Rule:    {} {} {} repeated {} times\n  Targets: ",
                    plain_style(&alert.name, 0),
                    config.column,
                    config.operator,
                    go_display(&config.value),
                    config.repeats
                ));
                for target in alert.targets.unwrap_or_default() {
                    output.push_str(&format!("{}, ", target.kind));
                }
                output.push_str("\n\n");
            }
        }
        Ok(output)
    }
}
async fn info_request(
    client: &ApiClient,
    name: &str,
    endpoint: &str,
) -> Result<Option<Vec<u8>>, CliError> {
    let response = client
        .standard(Method::GET, &format!("logstream/{name}/{endpoint}"), None)
        .await
        .map_err(CliError::from)?;
    match response.status {
        200 => Ok(Some(response.body)),
        404 => Ok(None),
        status => Err(info_http_error(
            status,
            &response.status_text(),
            &response.body,
            false,
        )),
    }
}
fn info_http_error(status: u16, wire_status: &str, body: &[u8], numeric: bool) -> CliError {
    let status_display = if numeric {
        status.to_string()
    } else {
        wire_status.to_owned()
    };
    CliError::http(
        status,
        format!(
            "Request Failed\nStatus Code: {status_display}\nResponse: {}\n",
            String::from_utf8_lossy(body)
        ),
        format!("request failed: {wire_status}"),
    )
}
const TYPE_RESPONSE: &str = "struct { StreamType string \"json:\\\"streamType\\\"\"; TelemetryType string \"json:\\\"telemetryType\\\"\" }";
pub(crate) fn decode_type(body: &[u8]) -> Result<String, CliError> {
    let result = (|| {
        let value = decode(body)?;
        let (mut stream, mut telemetry) = (String::new(), String::new());
        if let Some(fields) = object(&value, TYPE_RESPONSE, "", "")? {
            for (key, value) in fields {
                match normalized(key).as_str() {
                    "streamtype" => {
                        if !value.is_null() {
                            stream = string(value, "", "streamType")?;
                        }
                    }
                    "telemetrytype" if !value.is_null() => {
                        telemetry = string(value, "", "telemetryType")?;
                    }
                    _ => (),
                }
            }
        }
        Ok(if telemetry.is_empty() {
            stream
        } else {
            telemetry
        })
    })();
    result.map_err(|error: CliError| {
        command_error(format!("failed to unmarshal response: {}", error.message))
    })
}
const INGESTION: &str = "struct { Count int \"json:\\\"count\\\"\"; Format string \"json:\\\"format\\\"\"; Size uint64 \"json:\\\"size\\\"\" }";
const STORAGE: &str =
    "struct { Format string \"json:\\\"format\\\"\"; Size uint64 \"json:\\\"size\\\"\" }";
fn decode_stats(body: &[u8]) -> Result<Stats, CliError> {
    let document = decode(body)?;
    if document.is_object() {
        let wire = String::from_utf8_lossy(body);
        let raw = gojson::raw_members(&wire).map_err(command_error)?;
        for (key, raw) in raw {
            if key_is(&key, "time") {
                if raw == "null" {
                    continue;
                }
                if raw.starts_with('"') && raw.ends_with('"') {
                    validate_time(&Value::String(raw[1..raw.len() - 1].into()))?;
                } else {
                    return Err(command_error(
                        "Time.UnmarshalJSON: input is not a JSON string",
                    ));
                }
            }
        }
    }
    stats(&document)
}

fn stats(value: &Node) -> Result<Stats, CliError> {
    let mut result = Stats::default();
    if let Some(fields) = object(value, "cmd.DatasetStatsData", "", "")? {
        // time.Time's custom decoder aborts immediately, taking precedence over
        // ordinary UnmarshalTypeErrors that encoding/json otherwise saves.
        for (key, value) in fields {
            if key_is(key, "time") {
                validate_node_time(value)?;
            }
        }
        for (key, value) in fields {
            match normalized(key).as_str() {
                "ingestion" => {
                    if let Some(fields) = object(value, INGESTION, "DatasetStatsData", "ingestion")?
                    {
                        for (key, value) in fields {
                            match normalized(key).as_str() {
                                "count" => {
                                    if !value.is_null() {
                                        result.count = integer(value, "", "ingestion.count")?;
                                    }
                                }
                                "format" => {
                                    string(value, "", "ingestion.format")?;
                                }
                                "size" if !value.is_null() => {
                                    result.ingestion_size = unsigned(value, "", "ingestion.size")?;
                                }
                                _ => (),
                            }
                        }
                    }
                }
                "storage" => {
                    if let Some(fields) = object(value, STORAGE, "DatasetStatsData", "storage")? {
                        for (key, value) in fields {
                            match normalized(key).as_str() {
                                "format" => {
                                    string(value, "", "storage.format")?;
                                }
                                "size" if !value.is_null() => {
                                    result.storage_size = unsigned(value, "", "storage.size")?;
                                }
                                _ => (),
                            }
                        }
                    }
                }
                "stream" => {
                    string(value, "DatasetStatsData", "stream")?;
                }
                "time" => validate_node_time(value)?,
                _ => (),
            }
        }
    }
    Ok(result)
}
const RETENTION: &str = "struct { Description string \"json:\\\"description\\\"\"; Action string \"json:\\\"action\\\"\"; Duration string \"json:\\\"duration\\\"\" }";
fn retention(value: &Node) -> Result<Option<Vec<Retention>>, CliError> {
    let Some(values) = array(value, "cmd.DatasetRetentionData", "", "")? else {
        return Ok(None);
    };
    let mut result = Vec::new();
    for value in values {
        let mut item = Retention::default();
        if let Some(fields) = object(value, RETENTION, "", "")? {
            for (key, value) in fields {
                match normalized(key).as_str() {
                    "description" => {
                        if !value.is_null() {
                            item.description = string(value, "", "description")?;
                        }
                    }
                    "action" => {
                        if !value.is_null() {
                            item.action = string(value, "", "action")?;
                        }
                    }
                    "duration" if !value.is_null() => {
                        item.duration = string(value, "", "duration")?;
                    }
                    _ => (),
                }
            }
        }
        result.push(item);
    }
    Ok(Some(result))
}
fn alerts(value: &Node) -> Result<Vec<Alert>, CliError> {
    let mut result = Vec::<Alert>::new();
    let mut length = 0;
    if let Some(fields) = object(value, "cmd.AlertConfig", "", "")? {
        for (key, value) in fields {
            match normalized(key).as_str() {
                "version" => {
                    string(value, "AlertConfig", "version")?;
                }
                "alerts" => match array(value, "[]cmd.Alert", "AlertConfig", "alerts")? {
                    None => {
                        result.clear();
                        length = 0;
                    }
                    Some(values) => {
                        if values.is_empty() {
                            result.clear();
                        }
                        for (index, value) in values.iter().enumerate() {
                            if index == result.len() {
                                result.push(Alert::default());
                            }
                            alert(&mut result[index], value)?;
                        }
                        length = values.len();
                    }
                },
                _ => (),
            }
        }
    }
    result.truncate(length);
    Ok(result)
}
fn alert(result: &mut Alert, value: &Node) -> Result<(), CliError> {
    let mut targets_present = result.targets.is_some();
    let mut target_storage = result.targets.take().unwrap_or_default();
    let mut target_length = target_storage.len();
    if let Some(fields) = object(value, "cmd.Alert", "AlertConfig", "alerts")? {
        for (key, value) in fields {
            match normalized(key).as_str() {
                "name" => {
                    if !value.is_null() {
                        result.name = string(value, "Alert", "alerts.name")?;
                    }
                }
                "message" => {
                    if !value.is_null() {
                        result.message = string(value, "Alert", "alerts.message")?;
                    }
                }
                "targets" => match array(value, "[]cmd.Target", "Alert", "alerts.targets")? {
                    None => {
                        target_storage.clear();
                        target_length = 0;
                        targets_present = false;
                    }
                    Some(values) => {
                        if values.is_empty() {
                            target_storage.clear();
                        }
                        for (index, value) in values.iter().enumerate() {
                            if index == target_storage.len() {
                                target_storage.push(Target::default());
                            }
                            target(&mut target_storage[index], value)?;
                        }
                        target_length = values.len();
                        targets_present = true;
                    }
                },
                "rule" => rule(&mut result.rule, value)?,
                _ => (),
            }
        }
    }
    target_storage.truncate(target_length);
    result.targets = targets_present.then_some(target_storage);
    Ok(())
}
fn target(result: &mut Target, value: &Node) -> Result<(), CliError> {
    if let Some(fields) = object(value, "cmd.Target", "Alert", "alerts.targets")? {
        for (key, value) in fields {
            match normalized(key).as_str() {
                "type" => {
                    if !value.is_null() {
                        result.kind = string(value, "Target", "alerts.targets.type")?;
                    }
                }
                "endpoint" => {
                    if !value.is_null() {
                        result.endpoint = string(value, "Target", "alerts.targets.endpoint")?;
                    }
                }
                "headers" => {
                    if value.is_null() {
                        result.headers = None;
                        continue;
                    }
                    if let Some(values) = object(
                        value,
                        "map[string]string",
                        "Target",
                        "alerts.targets.headers",
                    )? {
                        let headers = result.headers.get_or_insert_with(BTreeMap::new);
                        for (key, value) in values {
                            headers.insert(
                                key.clone(),
                                string(value, "Target", "alerts.targets.headers")?,
                            );
                        }
                    }
                }
                "skip_tls_check" => {
                    if !value.is_null() {
                        result.skip_tls_check =
                            boolean(value, "Target", "alerts.targets.skip_tls_check")?;
                    }
                }
                "repeat" => {
                    if let Some(fields) =
                        object(value, "cmd.Repeat", "Target", "alerts.targets.repeat")?
                    {
                        for (key, value) in fields {
                            match normalized(key).as_str() {
                                "interval" => {
                                    if !value.is_null() {
                                        result.repeat.interval = string(
                                            value,
                                            "Repeat",
                                            "alerts.targets.repeat.interval",
                                        )?;
                                    }
                                }
                                "times" if !value.is_null() => {
                                    result.repeat.times =
                                        integer(value, "Repeat", "alerts.targets.repeat.times")?;
                                }
                                _ => (),
                            }
                        }
                    }
                }
                _ => (),
            }
        }
    }
    Ok(())
}
impl Drop for RuleConfig {
    fn drop(&mut self) {
        gojson::drop_value(std::mem::take(&mut self.value));
    }
}
fn rule(result: &mut Rule, value: &Node) -> Result<(), CliError> {
    if let Some(fields) = object(value, "cmd.Rule", "Alert", "alerts.rule")? {
        for (key, value) in fields {
            match normalized(key).as_str() {
                "type" => {
                    if !value.is_null() {
                        result.kind = string(value, "Rule", "alerts.rule.type")?;
                    }
                }
                "config" => {
                    if let Some(fields) =
                        object(value, "cmd.RuleConfig", "Rule", "alerts.rule.config")?
                    {
                        for (key, value) in fields {
                            match normalized(key).as_str() {
                                "column" => {
                                    if !value.is_null() {
                                        result.config.column = string(
                                            value,
                                            "RuleConfig",
                                            "alerts.rule.config.column",
                                        )?;
                                    }
                                }
                                "operator" => {
                                    if !value.is_null() {
                                        result.config.operator = string(
                                            value,
                                            "RuleConfig",
                                            "alerts.rule.config.operator",
                                        )?;
                                    }
                                }
                                "ignorecase" if !value.is_null() => {
                                    result.config.ignore_case = boolean(
                                        value,
                                        "RuleConfig",
                                        "alerts.rule.config.ignoreCase",
                                    )?
                                }
                                "value" => {
                                    let converted = interface_value(
                                        value,
                                        "RuleConfig",
                                        "alerts.rule.config.value",
                                    )?;
                                    gojson::drop_value(std::mem::replace(
                                        &mut result.config.value,
                                        converted,
                                    ));
                                }
                                "repeats" if !value.is_null() => {
                                    result.config.repeats =
                                        integer(value, "RuleConfig", "alerts.rule.config.repeats")?;
                                }
                                _ => (),
                            }
                        }
                    }
                }
                _ => (),
            }
        }
    }
    Ok(())
}

const SCHEMA_FIELD: &str = "struct { Name string \"json:\\\"name\\\"\"; DataType json.RawMessage \"json:\\\"data_type\\\"\" }";
const SCHEMA_ROOT: &str = "struct { Fields []struct { Name string \"json:\\\"name\\\"\"; DataType json.RawMessage \"json:\\\"data_type\\\"\" } \"json:\\\"fields\\\"\" }";
async fn schema(parsed: &ParsedInvocation, client: &ApiClient) -> Result<String, CliError> {
    let json = format(parsed)?;
    let name = &parsed.positionals[0];
    let response = client
        .standard(Method::GET, &format!("logstream/{name}/schema"), None)
        .await
        .map_err(CliError::from)?;
    if response.status != 200 {
        return Err(CliError::status(
            "get dataset schema",
            response.status,
            &response.status_text(),
            &response.body,
        ));
    }
    schema_output(&response.body, json)
}
fn schema_output(body: &[u8], json: bool) -> Result<String, CliError> {
    let value = gojson::decode_ordered(body)
        .map_err(|e| invalid_response(format!("invalid dataset schema: {e}")))?;
    let mut fields = None;
    let validate = (|| {
        if let Some(mapping) = object(&value, SCHEMA_ROOT, "", "")? {
            for (key, value) in mapping {
                if key_is(key, "fields") {
                    fields = array(value, &format!("[]{SCHEMA_FIELD}"), "", "fields")?;
                    if let Some(values) = fields {
                        for value in values {
                            if let Some(mapping) = object(value, SCHEMA_FIELD, "", "fields")? {
                                for (key, value) in mapping {
                                    if key_is(key, "name") {
                                        string(value, "", "fields.name")?;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(())
    })();
    validate.map_err(|e: CliError| {
        invalid_response(format!("invalid dataset schema: {}", e.message))
    })?;
    fields.ok_or_else(|| invalid_response("dataset schema has no fields array"))?;
    if json {
        let sorted = gojson::Document::new(gojson::sorted(value.into_map().into_inner()));
        return Ok(render::json(&sorted, false));
    }
    // Typed slices reuse element storage in encoding/json. Repeated arrays
    // update existing records, while null resets the slice and its backing data.
    #[derive(Default)]
    struct Field<'a> {
        name: String,
        data_type: Option<&'a str>,
    }
    let wire = String::from_utf8_lossy(body);
    let root = gojson::raw_members(&wire).map_err(invalid_response)?;
    let mut storage = Vec::<Field<'_>>::new();
    let mut length = 0;
    for (key, raw) in root {
        if !key_is(&key, "fields") {
            continue;
        }
        if raw == "null" {
            storage.clear();
            length = 0;
            continue;
        }
        let raw_fields = gojson::raw_elements(raw).map_err(invalid_response)?;
        if raw_fields.is_empty() {
            storage.clear();
        }
        for (index, raw) in raw_fields.iter().enumerate() {
            if index == storage.len() {
                storage.push(Field::default());
            }
            if *raw == "null" {
                continue;
            }
            for (key, raw) in gojson::raw_members(raw).map_err(invalid_response)? {
                if key_is(&key, "name") && raw != "null" {
                    let name = gojson::decode(raw.as_bytes()).map_err(invalid_response)?;
                    storage[index].name =
                        name.as_str().expect("validated schema field name").into();
                } else if key_is(&key, "data_type") {
                    storage[index].data_type = Some(raw);
                }
            }
        }
        length = raw_fields.len();
    }
    storage.truncate(length);
    let mut output = String::new();
    for field in storage {
        let Some(data_type) = field.data_type else {
            let mut error = invalid_response(format!(
                "invalid type for field {}: unexpected end of JSON input",
                quote(&field.name)
            ));
            error.stdout = output;
            return Err(error);
        };
        let parsed_type = gojson::decode(data_type.as_bytes()).map_err(invalid_response)?;
        let type_name = match &*parsed_type {
            Value::String(text) => text.clone(),
            Value::Null => String::new(),
            _ => gojson::compact(data_type),
        };
        output.push_str(&item_row(&field.name, &type_name));
    }
    Ok(output)
}

fn normalized(key: &str) -> String {
    key.chars()
        .map(|c| match c {
            '\u{17f}' => 's',
            '\u{212a}' => 'k',
            _ => c.to_ascii_lowercase(),
        })
        .collect()
}
fn key_is(key: &str, expected: &str) -> bool {
    normalized(key) == normalized(expected)
}
fn kind(value: &Node) -> &'static str {
    value.kind()
}

fn wrong(value: &Node, target: &str, structure: &str, path: &str) -> CliError {
    let actual = if value.is_number() && matches!(target, "int" | "uint64" | "float64") {
        format!("number {value}")
    } else {
        kind(value).into()
    };
    let destination = if path.is_empty() {
        format!("Go value of type {target}")
    } else {
        format!("Go struct field {structure}.{path} of type {target}")
    };
    command_error(format!(
        "json: cannot unmarshal {actual} into {destination}"
    ))
}
fn object<'a>(
    value: &'a Node,
    target: &str,
    structure: &str,
    path: &str,
) -> Result<Option<&'a [(String, Node)]>, CliError> {
    match value {
        Node::Scalar(Value::Null) => Ok(None),
        Node::Object(map) => Ok(Some(map)),
        _ => Err(wrong(value, target, structure, path)),
    }
}
fn array<'a>(
    value: &'a Node,
    target: &str,
    structure: &str,
    path: &str,
) -> Result<Option<&'a [Node]>, CliError> {
    match value {
        Node::Scalar(Value::Null) => Ok(None),
        Node::Array(items) => Ok(Some(items)),
        _ => Err(wrong(value, target, structure, path)),
    }
}
fn string(value: &Node, structure: &str, path: &str) -> Result<String, CliError> {
    match value {
        Node::Scalar(Value::Null) => Ok(String::new()),
        Node::Scalar(Value::String(value)) => Ok(value.clone()),
        _ => Err(wrong(value, "string", structure, path)),
    }
}
fn boolean(value: &Node, structure: &str, path: &str) -> Result<bool, CliError> {
    match value {
        Node::Scalar(Value::Null) => Ok(false),
        Node::Scalar(Value::Bool(value)) => Ok(*value),
        _ => Err(wrong(value, "bool", structure, path)),
    }
}
fn integer(value: &Node, structure: &str, path: &str) -> Result<i64, CliError> {
    if value.is_null() {
        return Ok(0);
    }
    value
        .as_i64()
        .ok_or_else(|| wrong(value, "int", structure, path))
}
fn unsigned(value: &Node, structure: &str, path: &str) -> Result<u64, CliError> {
    if value.is_null() {
        return Ok(0);
    }
    value
        .as_u64()
        .ok_or_else(|| wrong(value, "uint64", structure, path))
}
fn interface_value(value: &Node, structure: &str, path: &str) -> Result<Value, CliError> {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || {
        interface_inner(value, structure, path)
    })
}
fn interface_inner(value: &Node, structure: &str, path: &str) -> Result<Value, CliError> {
    match value {
        Node::Scalar(Value::Number(n)) => {
            let float = n
                .as_f64()
                .filter(|f| f.is_finite())
                .ok_or_else(|| wrong(value, "float64", structure, path))?;
            Ok(Value::Number(
                Number::from_str(&go_float(float, true)).expect("finite float"),
            ))
        }
        Node::Array(values) => {
            let mut result = gojson::Document::new(Value::Array(Vec::new()));
            for value in values {
                let converted = interface_value(value, structure, path)?;
                result.as_array_mut().unwrap().push(converted);
            }
            Ok(result.into_inner())
        }
        Node::Object(fields) => {
            let mut result = gojson::Document::new(Value::Object(Map::new()));
            for (key, value) in fields {
                let converted = interface_value(value, structure, path)?;
                if let Some(old) = result
                    .as_object_mut()
                    .unwrap()
                    .insert(key.clone(), converted)
                {
                    gojson::drop_value(old);
                }
            }
            // Descendants are already sorted during their own conversion.
            result.as_object_mut().unwrap().sort_keys();
            Ok(result.into_inner())
        }
        Node::Scalar(other) => Ok(other.clone()),
    }
}
fn go_float(value: f64, json: bool) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".into()
        } else {
            "0".into()
        };
    }
    let absolute = value.abs();
    let scientific = if json {
        !(1e-6..1e21).contains(&absolute)
    } else {
        !(1e-4..1e6).contains(&absolute)
    };
    if !scientific {
        return value.to_string();
    }
    let representation = format!("{value:e}");
    let (mantissa, exponent) = representation.split_once('e').expect("scientific exponent");
    let exponent: i32 = exponent.parse().expect("integer exponent");
    if json {
        format!("{mantissa}e{exponent:+}")
    } else {
        format!(
            "{mantissa}e{}{:02}",
            if exponent < 0 { '-' } else { '+' },
            exponent.abs()
        )
    }
}
fn go_display(value: &Value) -> String {
    stacker::maybe_grow(64 * 1024, 1024 * 1024, || go_display_inner(value))
}
fn go_display_inner(value: &Value) -> String {
    match value {
        Value::Null => "<nil>".into(),
        Value::String(value) => value.clone(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => go_float(value.as_f64().unwrap_or_default(), false),
        Value::Array(values) => format!(
            "[{}]",
            values.iter().map(go_display).collect::<Vec<_>>().join(" ")
        ),
        Value::Object(values) => format!(
            "map[{}]",
            values
                .iter()
                .map(|(k, v)| format!("{k}:{}", go_display(v)))
                .collect::<Vec<_>>()
                .join(" ")
        ),
    }
}
fn plain_style(text: &str, left: usize) -> String {
    use unicode_width::UnicodeWidthStr;
    let text = text.replace('\t', "    ").replace("\r\n", "\n");
    let lines: Vec<&str> = text.split('\n').collect();
    let width = lines.iter().map(|line| line.width()).max().unwrap_or(0);
    lines
        .into_iter()
        .map(|line| {
            format!(
                "{}{line}{}",
                " ".repeat(left),
                " ".repeat(width.saturating_sub(line.width()))
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}
fn item_row(name: &str, kind: &str) -> String {
    let name = plain_style(name, 0);
    let kind = plain_style(kind, 0);
    let item = if kind.is_empty() {
        format!("• {name}")
    } else {
        format!("• {name} [{kind}]")
    };
    plain_style(&item, 1) + "\n"
}
fn human_bytes(size: u64) -> String {
    if size < 10 {
        return format!("{size} B");
    }
    let exponent = ((size as f64).ln() / 1024_f64.ln()).floor() as i32;
    let suffix = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"][exponent as usize];
    let value = ((size as f64 / 1024_f64.powi(exponent)) * 10.0 + 0.5).floor() / 10.0;
    if value < 10.0 {
        format!("{value:.1} {suffix}")
    } else {
        format!("{value:.0} {suffix}")
    }
}

fn validate_node_time(value: &Node) -> Result<(), CliError> {
    match value.scalar() {
        Some(value) => validate_time(value),
        None => Err(command_error(
            "Time.UnmarshalJSON: input is not a JSON string",
        )),
    }
}
fn validate_time(value: &Value) -> Result<(), CliError> {
    if value.is_null() {
        return Ok(());
    }
    let text = value
        .as_str()
        .ok_or_else(|| command_error("Time.UnmarshalJSON: input is not a JSON string"))?;
    const LAYOUT: &str = "2006-01-02T15:04:05Z07:00";
    let mut remaining = text;
    let fail = |remaining: &str, token: &str| {
        command_error(format!(
            "parsing time {} as {}: cannot parse {} as {}",
            quote(text),
            quote(LAYOUT),
            quote(remaining),
            quote(token)
        ))
    };
    fn number(
        remaining: &mut &str,
        count: usize,
        token: &str,
        min: u32,
        max: u32,
        description: &str,
        text: &str,
    ) -> Result<u32, CliError> {
        if remaining.len() < count || !remaining.as_bytes()[..count].iter().all(u8::is_ascii_digit)
        {
            return Err(command_error(format!(
                "parsing time {} as {}: cannot parse {} as {}",
                quote(text),
                quote(LAYOUT),
                quote(remaining),
                quote(token)
            )));
        }
        let value = remaining[..count].parse::<u32>().unwrap();
        *remaining = &remaining[count..];
        if !(min..=max).contains(&value) {
            return Err(command_error(format!(
                "parsing time {}: {description} out of range",
                quote(text)
            )));
        }
        Ok(value)
    }
    let year = number(&mut remaining, 4, "2006", 0, 9999, "year", text)?;
    let (mut month, mut day) = (1, 1);
    for (separator, count, token, min, max, description) in [
        ("-", 2, "01", 1, 12, "month"),
        ("-", 2, "02", 1, 31, "day"),
        ("T", 2, "15", 0, 23, "hour"),
        (":", 2, "04", 0, 59, "minute"),
        (":", 2, "05", 0, 59, "second"),
    ] {
        if !remaining.starts_with(separator) {
            return Err(fail(remaining, separator));
        }
        remaining = &remaining[separator.len()..];
        let count = if description == "hour" && remaining.as_bytes().get(1) == Some(&b':') {
            1
        } else {
            count
        };
        let n = number(&mut remaining, count, token, min, max, description, text)?;
        match description {
            "month" => month = n,
            "day" => day = n,
            _ => (),
        }
    }
    let days = match month {
        2 if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    if day > days {
        return Err(command_error(format!(
            "parsing time {}: day out of range",
            quote(text)
        )));
    }
    if remaining.starts_with(['.', ',']) {
        let digits = remaining[1..]
            .bytes()
            .take_while(u8::is_ascii_digit)
            .count();
        if digits > 0 {
            remaining = &remaining[1 + digits..];
        }
    }
    if remaining == "Z" {
        return Ok(());
    }
    if remaining.len() >= 6 && remaining.starts_with(['+', '-']) {
        let bytes = remaining.as_bytes();
        if bytes[3] == b':'
            && bytes[1..3].iter().all(u8::is_ascii_digit)
            && bytes[4..6].iter().all(u8::is_ascii_digit)
        {
            let hour = (bytes[1] - b'0') * 10 + bytes[2] - b'0';
            let minute = (bytes[4] - b'0') * 10 + bytes[5] - b'0';
            if hour > 24 {
                return Err(command_error(format!(
                    "parsing time {}: time zone offset hour out of range",
                    quote(text)
                )));
            }
            if minute > 60 {
                return Err(command_error(format!(
                    "parsing time {}: time zone offset minute out of range",
                    quote(text)
                )));
            }
            if remaining.len() > 6 {
                return Err(command_error(format!(
                    "parsing time {}: extra text: {}",
                    quote(text),
                    quote(&remaining[6..])
                )));
            }
            return Ok(());
        }
    }
    if let Some(extra) = remaining.strip_prefix('Z') {
        return Err(command_error(format!(
            "parsing time {}: extra text: {}",
            quote(text),
            quote(extra)
        )));
    }
    Err(fail(remaining, "Z07:00"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn schema_preserves_raw_numbers_and_escape_spelling() {
        let body=br#"{"z":1.2300e+04,"fields":[{"name":"id","data_type":{"escaped":"\u0061","integer":18446744073709551615,"decimal":1.2300e+04}}],"a":-0}"#;
        let text = schema_output(body, false).unwrap();
        assert_eq!(
            text,
            " • id [{\"escaped\":\"\\u0061\",\"integer\":18446744073709551615,\"decimal\":1.2300e+04}]\n"
        );
        let json = schema_output(body, true).unwrap();
        assert!(json.starts_with("{\n  \"a\": -0,"));
        assert!(json.contains("18446744073709551615"));
        assert!(json.contains("1.2300e+04"));
        let json = schema_output(br#"{"metadata":{"f":1.50e3},"fields":[]}"#, true).unwrap();
        assert!(json.contains("\"f\": 1.50e3"));
        assert!(!json.contains("1.50e+3"));
    }
    #[test]
    fn schema_has_explicit_missing_fields_and_partial_text_errors() {
        assert_eq!(
            schema_output(b"{}", true).unwrap_err().message,
            "dataset schema has no fields array"
        );
        let error = schema_output(
            br#"{"fields":[{"name":"first","data_type":"Utf8"},{"name":"bad"}]}"#,
            false,
        )
        .unwrap_err();
        assert_eq!(error.stdout, " • first [Utf8]\n");
        assert_eq!(
            error.message,
            "invalid type for field \"bad\": unexpected end of JSON input"
        );
    }
    #[test]
    fn typed_defaults_checks_and_size_display() {
        let value = decode(
            br#"{"ingestion":{"count":null,"size":1400000},"storage":{"size":0},"time":null}"#,
        )
        .unwrap();
        let decoded_stats = stats(&value).unwrap();
        assert_eq!(decoded_stats.count, 0);
        assert_eq!(human_bytes(decoded_stats.ingestion_size), "1.3 MiB");
        assert_eq!(human_bytes(2684354560), "2.5 GiB");
        assert_eq!(human_bytes(1000), "1000 B");
        let value = decode(br#"{"ingestion":{"size":-1}}"#).unwrap();
        assert_eq!(
            stats(&value).unwrap_err().message,
            "json: cannot unmarshal number -1 into Go struct field .ingestion.size of type uint64"
        );
    }
    #[test]
    fn malformed_unused_time_field_is_safe_and_rejected() {
        for text in [
            "é",
            "2024-02-30T00:00:00Z",
            "2024-01-01T24:00:00Z",
            "2024-01-01T00:00:00+é:0",
            "2024-01-01T00:00:00+25:00",
            "2024-01-01T00:00:00+00:61",
        ] {
            assert!(
                validate_time(&Value::String(text.into())).is_err(),
                "{text}"
            );
        }
        for text in [
            "2024-02-29T00:00:00Z",
            "2024-01-01T1:00:00Z",
            "2024-01-01T00:00:00.123456789Z",
            "2024-01-01T00:00:00+01:00",
        ] {
            validate_time(&Value::String(text.into())).unwrap();
        }
        assert_eq!(
            validate_time(&Value::String("abc".into()))
                .unwrap_err()
                .message,
            "parsing time \"abc\" as \"2006-01-02T15:04:05Z07:00\": cannot parse \"abc\" as \"2006\""
        );
    }
    #[test]
    fn alert_value_matches_go_float_and_map_presentation() {
        let value =
            decode(br#"{"alerts":[{"rule":{"config":{"value":{"z":9007199254740993,"a":1e6}}}}]}"#)
                .unwrap();
        let alerts = alerts(&value).unwrap();
        assert_eq!(
            go_display(&alerts[0].rule.config.value),
            "map[a:1e+06 z:9.007199254740992e+15]"
        );
        let json = render::json(&alerts, false);
        assert!(json.contains("9007199254740992"));
        assert!(json.contains("1000000"));
        assert!(json.contains("\"targets\": null"));
    }
    #[test]
    fn multiline_styles_keep_lipgloss_padding() {
        assert_eq!(plain_style("\nInfo:", 0), "     \nInfo:");
        assert_eq!(
            plain_style("No alerts set on dataset\n", 0),
            "No alerts set on dataset\n                        "
        );
        assert_eq!(
            item_row("multi\nline", "logs"),
            " • multi     \n line  [logs]\n"
        );
        assert_eq!(item_row("a\tb", "logs"), " • a    b [logs]\n");
        assert_eq!(item_row("a\r\nb", "logs"), " • a     \n b [logs]\n");
        assert_eq!(item_row("日誌\na", "logs"), " • 日誌     \n a    [logs]\n");
    }
    #[test]
    fn stats_custom_time_uses_wire_spelling_and_precedes_saved_type_errors() {
        let error = decode_stats(br#"{"time":"2024-01-01T00:00:00+\u00e9:0"}"#).unwrap_err();
        assert!(
            error
                .message
                .contains(&quote("2024-01-01T00:00:00+\\u00e9:0"))
        );
        let error = decode_stats(br#"{"ingestion":{"count":true},"time":"abc"}"#).unwrap_err();
        assert!(error.message.starts_with("parsing time \"abc\""));
        let value = decode(br#"{"alerts":[{"targets":[{"SKIP_TLS_CHECK":2}]}]}"#).unwrap();
        assert_eq!(
            alerts(&value).unwrap_err().message,
            "json: cannot unmarshal number into Go struct field Target.alerts.targets.skip_tls_check of type bool"
        );
    }
    #[test]
    fn typed_duplicate_fields_merge_and_keep_first_errors_and_null_values() {
        let stats =
            decode_stats(br#"{"ingestion":{"count":42},"ingestion":{"size":1000,"COUNT":null}}"#)
                .unwrap();
        assert_eq!(stats.count, 42);
        assert_eq!(stats.ingestion_size, 1000);
        let error = decode_type(br#"{"telemetryType":7,"telemetryType":"logs"}"#).unwrap_err();
        assert!(error.message.contains("cannot unmarshal number"));
        assert_eq!(decode_type(br#"{"telemetryType":"first","TelemetryType":"middle","telemetryType":"last","TELEMETRYTYPE":null}"#).unwrap(),"last");
        let doc=decode(br#"{"alerts":[{"name":"old","rule":{"config":{"column":"cpu"}},"rule":{"config":{"operator":">","value":5}}}],"alerts":[{"name":null}]}"#).unwrap();
        let alerts = alerts(&doc).unwrap();
        assert_eq!(alerts[0].name, "old");
        assert_eq!(alerts[0].rule.config.column, "cpu");
        assert_eq!(alerts[0].rule.config.operator, ">");
    }
    #[test]
    fn schema_typed_validation_and_raw_map_output_both_handle_duplicates() {
        let invalid = br#"{"fields":[{"name":7,"name":"fixed","data_type":"Utf8"}]}"#;
        assert!(
            schema_output(invalid, true)
                .unwrap_err()
                .message
                .contains("cannot unmarshal number")
        );
        let reused =
            br#"{"fields":[{"name":"first","data_type":"Utf8"}],"fields":[{"data_type":"Int64"}]}"#;
        assert_eq!(schema_output(reused, false).unwrap(), " • first [Int64]\n");
        let raw = schema_output(reused, true).unwrap();
        assert!(!raw.contains("first"));
        assert!(raw.contains("Int64"));
        let null = br#"{"fields":[{"name":"first","NAME":null,"data_type":"Utf8"}]}"#;
        assert_eq!(schema_output(null, false).unwrap(), " • first [Utf8]\n");
    }
    #[test]
    fn empty_duplicate_arrays_discard_reusable_records() {
        let document = decode(
            br#"{"datasets":[{"title":"old","ingestion":true}],"datasets":[],"datasets":[{}]}"#,
        )
        .unwrap();
        let items = dataset_list(&document).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].title, "");
        assert!(!items[0].ingestion);

        let document = decode(br#"{"alerts":[{"name":"old","rule":{"config":{"value":{"x":1}}}}],"alerts":[],"alerts":[{}]}"#).unwrap();
        let values = alerts(&document).unwrap();
        assert_eq!(values[0].name, "");
        assert!(values[0].rule.config.value.is_null());

        let document = decode(br#"{"alerts":[{"targets":[{"endpoint":"old","repeat":{"times":7}}],"targets":[],"targets":[{}]}]}"#).unwrap();
        let values = alerts(&document).unwrap();
        let targets = values[0].targets.as_ref().unwrap();
        assert_eq!(targets[0].endpoint, "");
        assert_eq!(targets[0].repeat.times, 0);

        let body = br#"{"fields":[{"name":"old","data_type":"Utf8"}],"fields":[],"fields":[{}]}"#;
        let error = schema_output(body, false).unwrap_err();
        assert_eq!(error.stdout, "");
        assert_eq!(
            error.message,
            "invalid type for field \"\": unexpected end of JSON input"
        );
    }
    #[test]
    fn ignore_case_null_preserves_prior_boolean() {
        for body in [
            &br#"{"alerts":[{"rule":{"config":{"ignoreCase":true,"ignoreCase":null}}}]}"#[..],
            &br#"{"alerts":[{"rule":{"config":{"ignoreCase":true},"config":{"ignoreCase":null}}}]}"#[..],
        ] {
            let document = decode(body).unwrap();
            assert!(alerts(&document).unwrap()[0].rule.config.ignore_case);
        }
    }
    #[test]
    fn raw_schema_and_alert_maps_render_deep_objects_safely() {
        let nested = format!("{}0{}", "{\"x\":".repeat(8_000), "}".repeat(8_000));
        let body = format!("{{\"fields\":[],\"metadata\":{nested}}}");
        let output = schema_output(body.as_bytes(), true).unwrap();
        assert!(output.starts_with("{\n  \"fields\": [],\n  \"metadata\": {\n"));
        assert!(output.ends_with("\n  }\n}\n"));
        drop(output);

        let document = decode(format!("{{\"config\":{{\"value\":{nested}}}}}").as_bytes()).unwrap();
        let mut value = Rule::default();
        rule(&mut value, &document).unwrap();
        assert!(serde_json::to_string(&value).unwrap().contains(&nested));
    }
}
