//! Decode one series at a time from borrowed response fragments. The output
//! remains private until all elements validate, preserving atomic decode errors.
//! Repeated enclosing fields use the general decoder: Go reuses slice backing
//! elements across those fields, so independently rendering them would be wrong.
use super::*;

fn fields<'a>(body: &'a [u8], names: &[&str]) -> Option<Vec<Option<&'a [u8]>>> {
    let mut selected = vec![None; names.len()];
    for (key, value) in gojson::raw_members_bytes(body).ok()? {
        if let Some(index) = names.iter().position(|name| key_is(&key, name)) {
            if selected[index].is_some() {
                return None;
            }
            selected[index] = Some(value);
        }
    }
    Some(selected)
}

fn string(value: Option<&[u8]>) -> Option<String> {
    let mut result = String::new();
    if let Some(value) = value {
        let document = gojson::decode_ordered(value).ok()?;
        decode_string(&document, &mut result, "", "").ok()?;
    }
    Some(result)
}

fn elements(value: Option<&[u8]>) -> Option<Vec<&[u8]>> {
    match value {
        None | Some(b"null") => Some(Vec::new()),
        Some(value) => gojson::raw_elements_bytes(value).ok(),
    }
}

pub(super) fn run(body: &[u8]) -> Option<Vec<u8>> {
    let envelope = fields(body, &["status", "error", "errorType", "data"])?;
    if string(envelope[0])? == "error" {
        return None;
    }
    string(envelope[1])?;
    string(envelope[2])?;
    let data = match envelope[3] {
        None | Some(b"null") => vec![None, None],
        Some(data) => fields(data, &["resultType", "result"])?,
    };
    let result_type = string(data[0])?;
    let result = elements(data[1])?;
    if result.is_empty() {
        return Some(b"No data returned.\n".to_vec());
    }
    let mut out = Vec::new();
    for item in &result {
        append_series(&mut out, item, &result_type)?;
    }
    out.extend_from_slice(
        format!("result_type={result_type}  series={}\n", result.len()).as_bytes(),
    );
    Some(out)
}

fn append_series(out: &mut Vec<u8>, body: &[u8], result_type: &str) -> Option<()> {
    let data = match body {
        b"null" => vec![None, None, None],
        _ => match fields(body, &["metric", "value", "values"]) {
            Some(data) => data,
            None => return append_owned_series(out, body, result_type),
        },
    };
    let mut metric = None;
    if let Some(raw) = data[0] {
        let document = gojson::decode_ordered(raw).ok()?;
        decode_map(&document, &mut metric, "", "").ok()?;
    }
    out.extend_from_slice(format_labels(metric.as_ref()).as_bytes());
    out.push(b'\n');
    // Validate both sample fields even when the result type does not print
    // them. Only a single point's generic and typed values coexist; a matrix
    // with one very long series must not recreate a response-sized tree.
    if let Some(raw) = data[1] {
        append_point(out, raw, result_type == "vector")?;
    }
    for raw in elements(data[2])? {
        append_point(out, raw, result_type == "matrix")?;
    }
    out.push(b'\n');
    Some(())
}

fn append_owned_series(out: &mut Vec<u8>, body: &[u8], result_type: &str) -> Option<()> {
    let document = gojson::decode_ordered(body).ok()?;
    let mut series = Series::default();
    series_fields(&document, &mut series).ok()?;
    drop(document);
    out.extend_from_slice(format_labels(series.metric.as_ref()).as_bytes());
    out.push(b'\n');
    match result_type {
        "vector" if series.value.len() == 2 => sample(out, &series.value),
        "matrix" => {
            for point in &series.values {
                if point.len() == 2 {
                    sample(out, point);
                }
            }
        }
        _ => (),
    }
    out.push(b'\n');
    Some(())
}

fn append_point(out: &mut Vec<u8>, raw: &[u8], print: bool) -> Option<()> {
    let document = gojson::decode_ordered(raw).ok()?;
    let point = decode_any_list(&document, "[]interface {}", "", "").ok()?;
    if print && point.len() == 2 {
        sample(out, &point);
    }
    Some(())
}

pub(super) fn series(body: &[u8], t: &Terminal) -> Option<Vec<u8>> {
    let envelope = fields(body, &["status", "error", "data"])?;
    if string(envelope[0])? == "error" {
        return None;
    }
    string(envelope[1])?;
    let data = elements(envelope[2])?;
    if data.is_empty() {
        return Some(b"No series found.\n".to_vec());
    }
    let mut out = Vec::new();
    for item in &data {
        let document = gojson::decode_ordered(item).ok()?;
        let mut series = None;
        decode_map(&document, &mut series, "", "data").ok()?;
        drop(document);
        print_series(&mut out, series.as_ref(), t);
    }
    out.extend_from_slice(format!("\ntotal={}\n", data.len()).as_bytes());
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fragmented_rendering_matches_general_decoder() {
        let bodies: &[&[u8]] = &[
            br#"{"data":{"resultType":"matrix","result":[{"metric":{"x":"y"},"values":[[0,"1"],[1,null],[2,{"b":2,"a":1}]]},null,{}]}}"#,
            br#"{"STATUS":null,"data":{"resultType":"vector","result":[{"metric":{"x":"y"},"metric":{"z":null},"value":[-0.1,"x"]}]}}"#,
            b"{\"data\":{\"resultType\":\"vector\",\"result\":[{\"value\":[1,\"\xff\"]}]}}",
            br#"{"data":null,"unknown":{"ignored":[1,2,3]}}"#,
        ];
        for body in bodies {
            assert_eq!(run(body).unwrap(), super::super::run(body).unwrap());
        }
        for body in [
            br#"{"data":[{"__name__":"up","x":"y","x":null},null,{}]}"#.as_slice(),
            br#"{"data":null}"#,
        ] {
            assert_eq!(
                series(body, &Terminal::PLAIN).unwrap(),
                super::super::series(body, &Terminal::PLAIN).unwrap()
            );
        }
    }

    #[test]
    fn duplicate_containers_and_late_errors_fall_back_without_output() {
        for body in [
            br#"{"data":{"result":[{},{}],"RESULT":[{}]}}"#.as_slice(),
            br#"{"data":{},"DATA":null}"#,
            br#"{"data":{"resultType":"matrix","result":[{"values":[[0,"1"]]},{"values":4}]}}"#,
            br#"{"data":{"result":[]},"status":42}"#,
            br#"{"data":{"result":[]},"unknown":[1,]}"#,
        ] {
            assert!(run(body).is_none());
        }
        assert!(series(br#"{"data":[{}],"DATA":[null]}"#, &Terminal::PLAIN).is_none());
        assert!(series(br#"{"data":[{}, {"x":4}]}"#, &Terminal::PLAIN).is_none());
    }
}
