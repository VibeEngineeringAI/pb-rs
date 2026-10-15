//! Network operations of the interactive PromQL session (`pb promql run -i`).
//!
//! Each operation keeps the endpoint, transport and deadline of the
//! corresponding Go command in pkg/model/promql.go:
//! results and the metric-name prefetch use the HTTP/1-only metrics
//! transport with 120 s (`promqlModelFetch`), builder label/value lookups use
//! the standard transport with 30 s (`builderHTTPGetCtx`), and the metric
//! dataset list uses Prism home discovery (`datasets.FetchHomeDatasets`).
//! Completions return owned data; the session driver applies them.
use super::json::{ReusedSlice, json_field_is};
use super::promql::{DisplayMode, QueryMode, ResultData, rows_from_result};
use crate::{
    api::{ApiClient, ApiError, ApiResponse},
    gojson::{self, Node},
};
use std::sync::Arc;
use tokio::sync::watch;

/// Owned snapshot of one result request (`NewPromqlModeFetchTask` arguments).
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ResultRequest {
    pub(crate) expr: String,
    pub(crate) dataset: String,
    pub(crate) dataset_raw: Option<Vec<u8>>,
    pub(crate) step: String,
    /// RFC 3339 UTC strings as produced by the time picker.
    pub(crate) start: String,
    pub(crate) end: String,
    pub(crate) mode: QueryMode,
    pub(crate) display: DisplayMode,
}

impl ResultRequest {
    pub(crate) fn dataset_bytes(&self) -> &[u8] {
        self.dataset_raw
            .as_deref()
            .unwrap_or(self.dataset.as_bytes())
    }
}

/// Concrete adapter over the authenticated client. Cloning shares the client.
#[derive(Clone)]
pub(crate) struct PromqlSessionService {
    client: Arc<ApiClient>,
}

impl PromqlSessionService {
    pub(crate) fn new(client: ApiClient) -> Self {
        Self {
            client: Arc::new(client),
        }
    }

    pub(crate) fn profile_url(&self) -> String {
        self.client.profile().url
    }

    /// Range then instant for `both`, otherwise one request, as Go does.
    pub(crate) async fn query(&self, request: ResultRequest) -> Result<ResultData, String> {
        let dataset_bytes = request.dataset_bytes().to_vec();
        let ResultRequest {
            expr,
            dataset: _,
            dataset_raw: _,
            step,
            start,
            end,
            mode,
            display,
        } = request;
        if mode == QueryMode::Both {
            let (range_start, range_end) = super::promql::normalize_range_window(&start, &end);
            let range = self
                .fetch_result(
                    &expr,
                    &dataset_bytes,
                    &step,
                    &range_start,
                    &range_end,
                    false,
                )
                .await?;
            let instant = self
                .fetch_result(&expr, &dataset_bytes, &step, &start, &end, true)
                .await?;
            let (rows, series_count, value_width) = rows_from_result(&instant, display);
            let (chart_rows, _, _) = rows_from_result(&range, display);
            return Ok(ResultData {
                result_type: instant.result_type,
                chart_result: range.result_type,
                rows,
                chart_rows,
                series_count,
                value_width,
            });
        }
        let result = self
            .fetch_result(
                &expr,
                &dataset_bytes,
                &step,
                &start,
                &end,
                mode == QueryMode::Instant,
            )
            .await?;
        let (rows, series_count, value_width) = rows_from_result(&result, display);
        let matrix = result.result_type == "matrix";
        Ok(ResultData {
            chart_result: if matrix {
                result.result_type.clone()
            } else {
                String::new()
            },
            chart_rows: if matrix { rows.clone() } else { Vec::new() },
            result_type: result.result_type,
            rows,
            series_count,
            value_width,
        })
    }

    /// `fetchPromqlResult`.
    async fn fetch_result(
        &self,
        expr: &str,
        dataset: &[u8],
        step: &str,
        start: &str,
        end: &str,
        instant: bool,
    ) -> Result<PromqlResult, String> {
        let mut pairs = vec![
            ("query".to_owned(), expr.as_bytes().to_vec()),
            ("stream".to_owned(), dataset.to_vec()),
        ];
        let path = if instant {
            pairs.push(("time".to_owned(), end.as_bytes().to_vec()));
            "prometheus/api/v1/query"
        } else {
            pairs.push(("start".to_owned(), start.as_bytes().to_vec()));
            pairs.push(("end".to_owned(), end.as_bytes().to_vec()));
            pairs.push((
                "step".to_owned(),
                super::promql::resolve_step_from_strings(step, start, end).into_bytes(),
            ));
            "prometheus/api/v1/query_range"
        };
        let body = self.metrics_fetch(path, &pairs).await?;
        let result =
            decode_result(&body).map_err(|error| format!("failed to parse response: {error}"))?;
        if result.status == "error" {
            return Err(format!("{}: {}", result.error_type, result.error));
        }
        Ok(result)
    }

    /// `promqlModelFetch`: HTTP/1 metrics transport, 120 s, exactly-200 policy.
    /// Go builds a new client from the session-start profile copy for every
    /// call (so range and instant halves of `both` are separate clients);
    /// `effect_scope` gives each call that isolated auth/refresh state.
    async fn metrics_fetch(
        &self,
        path: &str,
        pairs: &[(String, Vec<u8>)],
    ) -> Result<Vec<u8>, String> {
        let client = self.client.effect_scope();
        let request = client
            .prepare_metrics_bytes(path.as_bytes(), pairs)
            .map_err(|e| e.to_string())?;
        let response = client
            .send_streaming(request)
            .await
            .map_err(reset_message)?;
        let response = response.collect().await.map_err(|e| e.to_string())?;
        exactly_ok(response)
    }

    /// `fetchMetricDatasets`: Prism home datasets of type `metrics`, title order.
    pub(crate) async fn metric_datasets(&self) -> Result<Vec<String>, String> {
        // datasets.FetchHomeDatasets constructs its own client per call.
        let client = self.client.effect_scope();
        let items = crate::datasets::fetch_home(&client)
            .await
            .map_err(|e| e.message)?;
        let mut names: Vec<String> = items
            .into_iter()
            .filter(|item| item.dataset_type == "metrics")
            .map(|item| item.title)
            .collect();
        names.sort();
        Ok(names)
    }

    /// `fetchCacheMetrics`: metric names of `dataset` through the result transport.
    pub(crate) async fn cache_metrics(&self, dataset: &[u8]) -> Result<Vec<String>, String> {
        let pairs = [("stream".to_owned(), dataset.to_owned())];
        let body = self
            .metrics_fetch("prometheus/api/v1/label/__name__/values", &pairs)
            .await?;
        let list = decode_label_list(&body)?;
        if list.status == "error" {
            return Err(list.error);
        }
        Ok(list.data)
    }

    /// `fetchBuilderLabelsCtx`. `Ok(None)` means the request was canceled and,
    /// like Go's nil message, produces no completion.
    pub(crate) async fn builder_labels(
        &self,
        dataset: &[u8],
        metric: &str,
        start: &str,
        end: &str,
        cancel: watch::Receiver<bool>,
    ) -> Option<Result<Vec<String>, String>> {
        let list = self
            .builder_list(
                "prometheus/api/v1/labels",
                dataset,
                metric,
                start,
                end,
                cancel,
            )
            .await?;
        Some(list.map(|data| data.into_iter().filter(|l| l != "__name__").collect()))
    }

    /// `fetchBuilderValuesCtx` for a concrete label.
    pub(crate) async fn builder_values(
        &self,
        dataset: &[u8],
        metric: &str,
        label: &str,
        start: &str,
        end: &str,
        cancel: watch::Receiver<bool>,
    ) -> Option<Result<Vec<String>, String>> {
        let path = format!("prometheus/api/v1/label/{}/values", path_escape(label));
        self.builder_list(&path, dataset, metric, start, end, cancel)
            .await
    }

    async fn builder_list(
        &self,
        path: &str,
        dataset: &[u8],
        metric: &str,
        start: &str,
        end: &str,
        mut cancel: watch::Receiver<bool>,
    ) -> Option<Result<Vec<String>, String>> {
        // builderHTTPGetCtx: a new DefaultClient from the session-start profile.
        let client = self.client.effect_scope();
        let profile_url = client.profile().url;
        let base = match crate::api::append_url_string(&profile_url, &[path]) {
            Ok(base) => base,
            Err(error) => return Some(Err(error.to_string())),
        };
        let mut raw = format!("{base}?stream={}", crate::api::query_escape_bytes(dataset));
        if !start.is_empty() {
            raw.push_str(&format!("&start={}", query_escape(start)));
        }
        if !end.is_empty() {
            raw.push_str(&format!("&end={}", query_escape(end)));
        }
        if !metric.is_empty() {
            raw.push_str(&format!("&match[]={}", query_escape(metric)));
        }
        let request = async move {
            let prepared = client.prepare_builder_get(raw).map_err(|e| e.to_string())?;
            let response = client
                .send_streaming(prepared)
                .await
                .map_err(|e| e.to_string())?;
            let response = response.collect().await.map_err(|e| e.to_string())?;
            exactly_ok(response)
        };
        // Go suppresses a failed request when its cancellation check observes
        // a canceled context. Here simultaneous request/cancel readiness favors
        // the request; this preserves a possible race outcome, not every Go
        // interleaving or a guarantee that completion always beats cancellation.
        let body = tokio::select! {
            biased;
            body = request => body,
            _ = canceled(&mut cancel) => return None,
        };
        Some(body.and_then(|body| {
            let list = decode_label_list(&body)?;
            if list.status == "error" {
                Err(list.error)
            } else {
                Ok(list.data)
            }
        }))
    }
}

async fn canceled(cancel: &mut watch::Receiver<bool>) {
    loop {
        if *cancel.borrow_and_update() {
            return;
        }
        if cancel.changed().await.is_err() {
            // Sender dropped without canceling: never resolve.
            std::future::pending::<()>().await;
        }
    }
}

fn reset_message(error: ApiError) -> String {
    let text = error.to_string();
    if error.is_connection_reset() || text.contains("connection reset") {
        "server reset the connection — query timed out".to_owned()
    } else {
        text
    }
}

/// Go `resp.StatusCode != http.StatusOK` → `HTTP <Status>: <trimmed body or Status>`.
fn exactly_ok(response: ApiResponse) -> Result<Vec<u8>, String> {
    if response.status == 200 {
        return Ok(response.body);
    }
    let status = response.status_text();
    let text = String::from_utf8_lossy(&response.body);
    let trimmed = text.trim();
    let message = if trimmed.is_empty() {
        status.clone()
    } else {
        trimmed.to_owned()
    };
    Err(format!("HTTP {status}: {message}"))
}

/// Go url.QueryEscape.
pub(crate) fn query_escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Go url.PathEscape (encodePathSegment).
pub(crate) fn path_escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z'
            | b'a'..=b'z'
            | b'0'..=b'9'
            | b'-'
            | b'_'
            | b'.'
            | b'~'
            | b'$'
            | b'&'
            | b'+'
            | b':'
            | b'='
            | b'@' => out.push(byte as char),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

// ─── response decoding ──────────────────────────────────────────────────────
//
// encoding/json into Go's promqlRespModel / promqlLabelListResp: field names
// match by Go's fold (`json_field_is`), unknown fields ignored. Null retains
// string and struct fields but clears maps/slices. Duplicate objects merge and
// typed slices (including the label list's `data`) reuse initialized elements,
// including those hidden by a shorter earlier array.
// The first mismatch is reported
// after decoding, in encoding/json's UnmarshalTypeError format.

/// One decoded value of a `[]any` sample element.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum AnyValue {
    Null,
    Bool(bool),
    Number(f64),
    String(String),
    Array(Vec<AnyValue>),
    /// Go map[string]any, printed with sorted keys by %v.
    Object(Vec<(String, AnyValue)>),
}

#[derive(Debug, Default, PartialEq)]
pub(crate) struct PromqlSeries {
    pub(crate) metric: Vec<(String, String)>,
    pub(crate) value: Vec<AnyValue>,
    pub(crate) values: Vec<Vec<AnyValue>>,
}

#[derive(Debug, Default, PartialEq)]
pub(crate) struct PromqlResult {
    pub(crate) status: String,
    pub(crate) result_type: String,
    pub(crate) result: Vec<PromqlSeries>,
    pub(crate) error: String,
    pub(crate) error_type: String,
}

#[derive(Debug, Default)]
struct LabelList {
    status: String,
    data: Vec<String>,
    error: String,
}

struct Decoder {
    first_error: Option<String>,
}

/// The Go field (from `names`, all with distinct folds) that `key` selects.
fn json_field<'a>(key: &str, names: &[&'a str]) -> Option<&'a str> {
    names.iter().copied().find(|name| json_field_is(key, name))
}

#[derive(Default)]
struct SeriesState {
    metric: Vec<(String, String)>,
    value: ReusedSlice<AnyValue>,
    values: ReusedSlice<ReusedSlice<AnyValue>>,
}

impl SeriesState {
    fn finish(self) -> PromqlSeries {
        PromqlSeries {
            metric: self.metric,
            value: self.value.finish(),
            values: self
                .values
                .finish()
                .into_iter()
                .map(ReusedSlice::finish)
                .collect(),
        }
    }
}

impl Decoder {
    /// encoding/json reports `number` for kind mismatches and `number <lexeme>`
    /// only for float64 overflow (see `overflow`).
    fn mismatch(&mut self, node: &Node, field: &str, go_type: &str) {
        self.report(node.kind().to_owned(), field, go_type);
    }

    fn overflow(&mut self, node: &Node, field: &str) {
        let lexeme = node.scalar().map(|v| v.to_string()).unwrap_or_default();
        self.report(format!("number {lexeme}"), field, "float64");
    }

    fn report(&mut self, value: String, field: &str, go_type: &str) {
        if self.first_error.is_none() {
            self.first_error = Some(if field.is_empty() {
                format!("json: cannot unmarshal {value} into Go value of type {go_type}")
            } else {
                format!(
                    "json: cannot unmarshal {value} into Go struct field {field} of type {go_type}"
                )
            });
        }
    }

    fn string(&mut self, node: &Node, field: &str, target: &mut String) {
        if node.is_null() {
            return;
        }
        match node.as_str() {
            Some(value) => *target = value.to_owned(),
            None => self.mismatch(node, field, "string"),
        }
    }

    /// Nested interface values are fresh values, while errors retain the
    /// enclosing concrete field (map keys do not extend Go's field path).
    fn any(&mut self, node: &Node, field: &str) -> AnyValue {
        match node {
            Node::Array(items) => {
                AnyValue::Array(items.iter().map(|i| self.any(i, field)).collect())
            }
            Node::Object(fields) => {
                let mut out: Vec<(String, AnyValue)> = Vec::new();
                for (key, value) in fields {
                    let value = self.any(value, field);
                    if let Some(slot) = out.iter_mut().find(|(k, _)| k == key) {
                        slot.1 = value;
                    } else {
                        out.push((key.clone(), value));
                    }
                }
                out.sort_by(|a, b| a.0.cmp(&b.0));
                AnyValue::Object(out)
            }
            Node::Scalar(serde_json::Value::Null) => AnyValue::Null,
            Node::Scalar(serde_json::Value::Bool(b)) => AnyValue::Bool(*b),
            Node::Scalar(serde_json::Value::String(s)) => AnyValue::String(s.clone()),
            Node::Scalar(serde_json::Value::Number(n)) => match n.to_string().parse::<f64>() {
                Ok(value) if value.is_finite() => AnyValue::Number(value),
                _ => {
                    self.overflow(node, field);
                    AnyValue::Null
                }
            },
            Node::Scalar(_) => AnyValue::Null,
        }
    }

    fn any_slice(&mut self, node: &Node, field: &str, target: &mut ReusedSlice<AnyValue>) {
        if node.is_null() {
            target.clear();
            return;
        }
        let Some(items) = node.as_array() else {
            self.mismatch(node, field, "[]interface {}");
            return;
        };
        if items.is_empty() {
            target.clear();
            return;
        }
        for (index, item) in items.iter().enumerate() {
            // An interface element is replaced, even if it previously held a
            // map/slice. This differs from the enclosing typed slice reuse.
            *target.slot(index, || AnyValue::Null) = self.any(item, field);
        }
        target.set_len(items.len());
    }
}

fn fields(node: &Node) -> &[(String, Node)] {
    node.as_object().unwrap_or(&[])
}

pub(crate) fn decode_result(body: &[u8]) -> Result<PromqlResult, String> {
    let root = gojson::decode_ordered(body)?;
    let mut decoder = Decoder { first_error: None };
    let mut result = PromqlResult::default();
    let mut series = ReusedSlice::<SeriesState>::default();
    if !root.is_object() && !root.is_null() {
        decoder.mismatch(&root, "", "model.promqlRespModel");
    }
    for (key, value) in fields(&root) {
        match json_field(key, &["status", "error", "errorType", "data"]) {
            Some("status") => decoder.string(value, "promqlRespModel.status", &mut result.status),
            Some("error") => decoder.string(value, "promqlRespModel.error", &mut result.error),
            Some("errorType") => {
                decoder.string(value, "promqlRespModel.errorType", &mut result.error_type)
            }
            Some("data") => decode_data(&mut decoder, value, &mut result, &mut series),
            _ => {}
        }
    }
    match decoder.first_error {
        Some(error) => Err(error),
        None => {
            result.result = series
                .finish()
                .into_iter()
                .map(SeriesState::finish)
                .collect();
            Ok(result)
        }
    }
}

fn decode_data(
    decoder: &mut Decoder,
    node: &Node,
    result: &mut PromqlResult,
    series: &mut ReusedSlice<SeriesState>,
) {
    if node.is_null() {
        return;
    }
    if !node.is_object() {
        decoder.mismatch(node, "promqlRespModel.data", "model.promqlDataModel");
        return;
    }
    for (key, value) in fields(node) {
        match json_field(key, &["resultType", "result"]) {
            Some("resultType") => decoder.string(
                value,
                "promqlDataModel.data.resultType",
                &mut result.result_type,
            ),
            Some("result") => {
                if value.is_null() {
                    series.clear();
                    continue;
                }
                let Some(items) = value.as_array() else {
                    decoder.mismatch(
                        value,
                        "promqlDataModel.data.result",
                        "[]model.promqlSeriesModel",
                    );
                    continue;
                };
                if items.is_empty() {
                    series.clear();
                    continue;
                }
                for (index, item) in items.iter().enumerate() {
                    decode_series(decoder, item, series.slot(index, SeriesState::default));
                }
                series.set_len(items.len());
            }
            _ => {}
        }
    }
}

fn decode_series(decoder: &mut Decoder, node: &Node, series: &mut SeriesState) {
    if node.is_null() {
        // null into a concrete struct leaves all its existing fields intact.
        return;
    }
    if !node.is_object() {
        decoder.mismatch(
            node,
            "promqlDataModel.data.result",
            "model.promqlSeriesModel",
        );
        return;
    }
    for (key, value) in fields(node) {
        match json_field(key, &["metric", "value", "values"]) {
            Some("metric") => {
                if value.is_null() {
                    series.metric.clear();
                    continue;
                }
                let Some(labels) = value.as_object() else {
                    decoder.mismatch(
                        value,
                        "promqlSeriesModel.data.result.metric",
                        "map[string]string",
                    );
                    continue;
                };
                for (name, label) in labels {
                    let mut text = String::new();
                    if !label.is_null() {
                        match label.as_str() {
                            Some(s) => text = s.to_owned(),
                            None => {
                                decoder.mismatch(
                                    label,
                                    "promqlSeriesModel.data.result.metric",
                                    "string",
                                );
                                continue;
                            }
                        }
                    }
                    if let Some(slot) = series.metric.iter_mut().find(|(k, _)| k == name) {
                        slot.1 = text;
                    } else {
                        series.metric.push((name.clone(), text));
                    }
                }
            }
            Some("value") => {
                decoder.any_slice(
                    value,
                    "promqlSeriesModel.data.result.value",
                    &mut series.value,
                );
            }
            Some("values") => {
                if value.is_null() {
                    series.values.clear();
                    continue;
                }
                let Some(points) = value.as_array() else {
                    decoder.mismatch(
                        value,
                        "promqlSeriesModel.data.result.values",
                        "[][]interface {}",
                    );
                    continue;
                };
                if points.is_empty() {
                    series.values.clear();
                    continue;
                }
                for (index, point) in points.iter().enumerate() {
                    decoder.any_slice(
                        point,
                        "promqlSeriesModel.data.result.values",
                        series.values.slot(index, ReusedSlice::default),
                    );
                }
                series.values.set_len(points.len());
            }
            _ => {}
        }
    }
}

fn decode_label_list(body: &[u8]) -> Result<LabelList, String> {
    let root = gojson::decode_ordered(body)?;
    let mut decoder = Decoder { first_error: None };
    let mut list = LabelList::default();
    // A duplicate `data` reuses earlier string slots: null keeps the prior
    // text, and a longer array re-exposes hidden ones (same as `result`).
    let mut data = ReusedSlice::<String>::default();
    if !root.is_object() && !root.is_null() {
        decoder.mismatch(&root, "", "model.promqlLabelListResp");
    }
    for (key, value) in fields(&root) {
        match json_field(key, &["status", "error", "data"]) {
            Some("status") => decoder.string(value, "promqlLabelListResp.status", &mut list.status),
            Some("error") => decoder.string(value, "promqlLabelListResp.error", &mut list.error),
            Some("data") => {
                if value.is_null() {
                    data.clear();
                    continue;
                }
                let Some(items) = value.as_array() else {
                    decoder.mismatch(value, "promqlLabelListResp.data", "[]string");
                    continue;
                };
                if items.is_empty() {
                    data.clear();
                    continue;
                }
                for (index, item) in items.iter().enumerate() {
                    decoder.string(
                        item,
                        "promqlLabelListResp.data",
                        data.slot(index, String::new),
                    );
                }
                data.set_len(items.len());
            }
            _ => {}
        }
    }
    match decoder.first_error {
        Some(error) => Err(error),
        None => {
            list.data = data.finish();
            Ok(list)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_follow_go_url_package() {
        assert_eq!(query_escape("a b/c~ü"), "a+b%2Fc~%C3%BC");
        assert_eq!(path_escape("job name/x;y"), "job%20name%2Fx%3By");
        assert_eq!(path_escape("a:b=c@d$e&f+g"), "a:b=c@d$e&f+g");
    }

    #[test]
    fn decodes_vector_and_matrix_with_go_tolerance() {
        let body = br#"{"Status":"success","data":{"resultType":"matrix","result":[
            {"metric":{"__name__":"up","job":"api"},"values":[[1700000000,"1"],[1700000060.5,"2"]]},
            {"metric":null,"value":[1700000000,"3"]}]},"unknown":1}"#;
        let result = decode_result(body).unwrap();
        assert_eq!(result.status, "success");
        assert_eq!(result.result_type, "matrix");
        assert_eq!(result.result.len(), 2);
        assert_eq!(result.result[0].values.len(), 2);
        assert_eq!(
            result.result[0].values[1][0],
            AnyValue::Number(1700000060.5)
        );
        assert_eq!(result.result[1].value[1], AnyValue::String("3".into()));
    }

    #[test]
    fn type_mismatch_reports_first_go_style_error() {
        let error = decode_result(br#"{"status":1,"error":2}"#).unwrap_err();
        assert_eq!(
            error,
            "json: cannot unmarshal number into Go struct field promqlRespModel.status of type string"
        );
        assert!(
            decode_result(b"[1]")
                .unwrap_err()
                .contains("model.promqlRespModel")
        );
    }

    fn labels(series: &PromqlSeries) -> std::collections::BTreeMap<&str, &str> {
        series
            .metric
            .iter()
            .map(|(key, value)| (key.as_str(), value.as_str()))
            .collect()
    }

    #[test]
    fn duplicate_result_merges_prior_series_fields_for_vector_and_matrix() {
        // Both raw payloads were exercised through pinned Go PTYs. A normal
        // JSON Value round-trip would erase the duplicate result field.
        for (kind, sample_key) in [("vector", "value"), ("matrix", "values")] {
            let first = if kind == "vector" {
                "[1704067200,\"1\"]"
            } else {
                "[[1704067200,\"1\"]]"
            };
            let last = if kind == "vector" {
                "[1704067200,\"3\"]"
            } else {
                "[[1704067200,\"3\"]]"
            };
            let body = format!(
                r#"{{"status":"success","data":{{"resultType":"{kind}","result":[{{"metric":{{"__name__":"up","job":"a"}},"{sample_key}":{first}}}],"result":[{{"metric":{{"instance":"i"}},"{sample_key}":{last}}}]}}}}"#
            );
            let result = decode_result(body.as_bytes()).unwrap();
            assert_eq!(result.result.len(), 1);
            assert_eq!(
                labels(&result.result[0]),
                std::collections::BTreeMap::from([
                    ("__name__", "up"),
                    ("instance", "i"),
                    ("job", "a")
                ])
            );
            let sample = if kind == "vector" {
                &result.result[0].value
            } else {
                &result.result[0].values[0]
            };
            assert_eq!(
                sample,
                &[AnyValue::Number(1704067200.0), AnyValue::String("3".into())]
            );
        }
    }

    #[test]
    fn shorter_result_then_reexpansion_retains_hidden_series() {
        let body = br#"{"data":{"resultType":"vector","result":[
            {"metric":{"__name__":"first"},"value":[1,"a"]},
            {"metric":{"__name__":"second"},"value":[2,"b"]},
            {"metric":{"__name__":"third"},"value":[3,"c"]}],
            "result":[{"metric":{"job":"added"}}],"result":[null,null,null]}}"#;
        let result = decode_result(body).unwrap();
        assert_eq!(result.result.len(), 3);
        assert_eq!(
            labels(&result.result[0]),
            std::collections::BTreeMap::from([("__name__", "first"), ("job", "added")])
        );
        assert_eq!(labels(&result.result[1])["__name__"], "second");
        assert_eq!(
            result.result[2].value,
            [AnyValue::Number(3.0), AnyValue::String("c".into())]
        );
        let shorter = decode_result(br#"{"data":{"result":[{"metric":{"job":"a"}},{"metric":{"job":"b"}}],"result":[null]}}"#).unwrap();
        assert_eq!(shorter.result.len(), 1);
        assert_eq!(labels(&shorter.result[0])["job"], "a");
    }

    #[test]
    fn empty_and_null_result_discard_hidden_slots_but_null_structs_do_not() {
        for reset in ["[]", "null"] {
            let body = format!(
                r#"{{"data":{{"result":[{{"metric":{{"job":"old"}},"value":[1,"old"]}},{{"value":[2,"hidden"]}}],"result":{reset},"result":[{{}},null]}}}}"#
            );
            let result = decode_result(body.as_bytes()).unwrap();
            assert_eq!(
                result.result,
                [PromqlSeries::default(), PromqlSeries::default()]
            );
        }
        let kept = decode_result(br#"{"data":{"result":[{"metric":{"job":"old"},"value":[1,"kept"]}],"result":[null]},"data":null,"DATA":{"RESULT":[{}]}}"#).unwrap();
        assert_eq!(labels(&kept.result[0])["job"], "old");
        assert_eq!(kept.result[0].value[1], AnyValue::String("kept".into()));
    }

    #[test]
    fn metric_objects_merge_empty_objects_keep_keys_and_null_resets() {
        let kept = decode_result(br#"{"data":{"result":[{"metric":{"__name__":"up","job":"old","job":null},"metric":{},"metric":{"instance":"new"}}]}}"#).unwrap();
        assert_eq!(
            labels(&kept.result[0]),
            std::collections::BTreeMap::from([
                ("__name__", "up"),
                ("instance", "new"),
                ("job", "")
            ])
        );
        let reset = decode_result(br#"{"data":{"result":[{"metric":{"__name__":"up","job":"old"},"metric":null,"metric":{"instance":"new"}}]}}"#).unwrap();
        assert_eq!(
            labels(&reset.result[0]),
            std::collections::BTreeMap::from([("instance", "new")])
        );
    }

    #[test]
    fn sample_interfaces_replace_nested_maps_arrays_and_nulls() {
        let result = decode_result(
            br#"{"data":{"result":[{"value":[1,{"old":"gone"}],"value":[2,{"fresh":"kept"}]},
            {"value":[1,["old"]],"value":[2,["fresh"]]},
            {"value":[1,"old"],"value":[2,null]}]}}"#,
        )
        .unwrap();
        assert_eq!(
            result.result[0].value,
            [
                AnyValue::Number(2.0),
                AnyValue::Object(vec![("fresh".into(), AnyValue::String("kept".into()))])
            ]
        );
        assert_eq!(
            result.result[1].value[1],
            AnyValue::Array(vec![AnyValue::String("fresh".into())])
        );
        assert_eq!(result.result[2].value[1], AnyValue::Null);
    }

    #[test]
    fn nested_sample_slices_truncate_reexpand_and_clear_by_field() {
        let result = decode_result(
            br#"{"data":{"result":[{
            "value":[1,"old"],"values":[[1,"old0"],[2,"old1"]],
            "values":[[3,"short"]],"values":[[4,null],[5,"new"]]}],
            "result":[{"value":null}]}}"#,
        )
        .unwrap();
        assert!(result.result[0].value.is_empty());
        assert_eq!(
            result.result[0].values,
            [
                vec![AnyValue::Number(4.0), AnyValue::Null],
                vec![AnyValue::Number(5.0), AnyValue::String("new".into())]
            ]
        );
        for reset in ["[]", "null"] {
            let body = format!(
                r#"{{"data":{{"result":[{{"values":[[1,"old"]],"values":{reset},"values":[null,[2,"new"]]}}]}}}}"#
            );
            let result = decode_result(body.as_bytes()).unwrap();
            assert_eq!(
                result.result[0].values,
                [
                    vec![],
                    vec![AnyValue::Number(2.0), AnyValue::String("new".into())]
                ]
            );
        }
    }

    #[test]
    fn duplicate_repairs_do_not_hide_the_first_go_decode_error() {
        let body = br#"{"data":{"result":[{"metric":7}],"result":[{"metric":{"job":"valid"},"values":[8]}]}}"#;
        assert_eq!(
            decode_result(body).unwrap_err(),
            "json: cannot unmarshal number into Go struct field promqlSeriesModel.data.result.metric of type map[string]string"
        );
        assert_eq!(
            decode_result(br#"{"data":{"result":[{"values":[[1,"old"]],"values":[7]}]}}"#)
                .unwrap_err(),
            "json: cannot unmarshal number into Go struct field promqlSeriesModel.data.result.values of type []interface {}"
        );
        for field in ["value", "values"] {
            let sample = if field == "value" {
                "[1,{\"nested\":1e400}]"
            } else {
                "[[1,{\"nested\":1e400}]]"
            };
            let body = format!(r#"{{"data":{{"result":[{{"{field}":{sample}}}],"result":null}}}}"#);
            assert_eq!(
                decode_result(body.as_bytes()).unwrap_err(),
                format!(
                    "json: cannot unmarshal number 1e400 into Go struct field promqlSeriesModel.data.result.{field} of type float64"
                )
            );
        }
    }

    // The label-list and fold expectations come from the pinned go1.25.4
    // encoding/json (decode.go, fold.go) and model.promqlLabelListResp; the
    // actual-Go probe is decoder-review-oracle/probe.go (docs/phase7-decoder-review-fixes.md).

    #[test]
    fn duplicate_label_data_keeps_prior_strings_under_null() {
        // Review F1 trigger 4.
        let list =
            decode_label_list(br#"{"status":"success","data":["a","b"],"data":[null]}"#).unwrap();
        assert_eq!(list.status, "success");
        assert_eq!(list.data, ["a"]);
        let list = decode_label_list(
            br#"{"data":["a","b","c"],"data":["x"],"data":[null,null,null,null]}"#,
        )
        .unwrap();
        assert_eq!(list.data, ["x", "b", "c", ""]);
    }

    #[test]
    fn empty_and_null_label_data_discard_hidden_strings() {
        for reset in ["[]", "null"] {
            let body = format!(r#"{{"data":["a","b"],"data":{reset},"data":[null,null]}}"#);
            assert_eq!(decode_label_list(body.as_bytes()).unwrap().data, ["", ""]);
        }
        let list = decode_label_list(br#"{"data":["a"],"data":[]}"#).unwrap();
        assert!(list.data.is_empty());
        assert_eq!(
            decode_label_list(br#"{"data":[1],"data":["ok"]}"#).unwrap_err(),
            "json: cannot unmarshal number into Go struct field promqlLabelListResp.data of type string"
        );
    }

    #[test]
    fn field_names_fold_like_go_simple_fold() {
        // Review F3 trigger: Go reads U+017F as `s` and reports the error.
        let result = decode_result(
            "{\"\u{17F}tatus\":\"error\",\"error\":\"x\",\"errorType\":\"bad_data\"}".as_bytes(),
        )
        .unwrap();
        assert_eq!(
            (
                result.status.as_str(),
                result.error.as_str(),
                result.error_type.as_str()
            ),
            ("error", "x", "bad_data")
        );
        let result = decode_result(
            "{\"data\":{\"RESULTTYPE\":\"vector\",\"result\":[{\"value\u{17F}\":[[1,\"2\"]]}]}}"
                .as_bytes(),
        )
        .unwrap();
        assert_eq!(result.result_type, "vector");
        assert_eq!(
            result.result[0].values,
            [vec![AnyValue::Number(1.0), AnyValue::String("2".into())]]
        );
        let list = decode_label_list("{\"\u{17F}TATU\u{17F}\":\"success\"}".as_bytes()).unwrap();
        assert_eq!(list.status, "success");
        // U+212A (Kelvin) folds with k; no other non-ASCII rune reaches ASCII.
        assert!(json_field_is("\u{212A}ey", "key"));
        assert!(json_field_is("\u{17F}", "S"));
        for key in [
            "st\u{e1}tus",
            "\u{131}d",
            "\u{df}",
            "status\u{17F}",
            "statu",
        ] {
            assert!(!json_field_is(key, "status") && !json_field_is(key, "id"));
            assert!(!json_field_is(key, "ss"));
        }
    }

    #[test]
    fn non_200_uses_status_text_or_body() {
        let error = exactly_ok(ApiResponse {
            status: 503,
            reason: "Service Unavailable".into(),
            body: b"  \n".to_vec(),
        })
        .unwrap_err();
        assert_eq!(
            error,
            "HTTP 503 Service Unavailable: 503 Service Unavailable"
        );
        let error = exactly_ok(ApiResponse {
            status: 204,
            reason: "No Content".into(),
            body: b" busy ".to_vec(),
        })
        .unwrap_err();
        assert_eq!(error, "HTTP 204 No Content: busy");
    }
}
