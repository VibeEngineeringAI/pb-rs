//! Pretty JSON from validated input spans. Containers borrow their values
//! instead of allocating a second complete JSON tree; objects retain Go's
//! sorted keys and last duplicate member semantics.
use crate::{gojson, render};
use serde::Serialize;
use std::collections::BTreeMap;

/// The caller validates the complete body and supplies single-value input.
/// Syntax and trailing-value diagnostics remain the caller's responsibility.
pub(super) fn render_validated(body: &[u8]) -> String {
    render::json(&RawValue(body), false)
}

struct RawValue<'a>(&'a [u8]);

impl Serialize for RawValue<'_> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        stacker::maybe_grow(64 * 1024, 1024 * 1024, || self.serialize_inner(serializer))
    }
}

impl RawValue<'_> {
    fn serialize_inner<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::{SerializeMap, SerializeSeq};
        let first = self
            .0
            .iter()
            .find(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
            .expect("validated JSON has a value");
        match first {
            b'{' => {
                let members = gojson::raw_members_bytes(self.0)
                    .expect("validated JSON object has valid members");
                let mut sorted = BTreeMap::new();
                for (key, value) in members {
                    sorted.insert(key, value);
                }
                let mut map = serializer.serialize_map(Some(sorted.len()))?;
                for (key, value) in sorted {
                    map.serialize_entry(&key, &RawValue(value))?;
                }
                map.end()
            }
            b'[' => {
                let elements = gojson::raw_elements_bytes(self.0)
                    .expect("validated JSON array has valid elements");
                let mut sequence = serializer.serialize_seq(Some(elements.len()))?;
                for value in elements {
                    sequence.serialize_element(&RawValue(value))?;
                }
                sequence.end()
            }
            _ => {
                // Decode only this scalar to retain Go's Unicode replacement
                // rules and owned, unnormalized numeric lexemes. A generic
                // serde decoder would reject malformed UTF-8 strings.
                let scalar =
                    gojson::decode_ordered(self.0).expect("validated JSON scalar can be decoded");
                scalar.serialize(serializer)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_matches_tree(body: &[u8]) {
        gojson::validate(body).unwrap();
        let document = gojson::decode_ordered(body).unwrap();
        let sorted = gojson::Document::new(gojson::sorted(document.into_map().into_inner()));
        assert_eq!(render_validated(body), render::json(&sorted, false));
    }

    #[test]
    fn sorted_objects_replace_duplicate_decoded_keys() {
        assert_matches_tree(
            br#" {"z":0,"x":{"b":true,"a":null},"\u0078":[{"c":1,"a":2,"c":3}],"a":{},"z":[]} "#,
        );
        assert_matches_tree(br#"{"\ud800":1,"\ufffd":2,"\udfff":3}"#);
    }

    #[test]
    fn preserves_scalar_lexemes_and_go_escaping() {
        for body in [
            &b"null"[..],
            b"false",
            b"-0",
            b"1e10000",
            br#"[1e3,1E+003,1.2300,-0.000,0e-10,true,null,"<&>"]"#,
            br#"["\ud800","\udfff","\ud800\u0041","\ud83d\ude00","\u2028\u2029","\n\t\\\""]"#,
            b"{\"\xff\":\"\xf0\x80\x80\x80\",\"valid\":\"\xe2\x82\"}",
        ] {
            assert_matches_tree(body);
        }
    }

    #[test]
    fn handles_nested_containers_beyond_serde_default_depth() {
        let depth = 512;
        let body = format!(
            "{}{{\"z\":1e3,\"a\":\"x\"}}{}",
            "[".repeat(depth),
            "]".repeat(depth)
        );
        assert_matches_tree(body.as_bytes());
    }

    #[test]
    fn handles_wide_sample_arrays() {
        let points = (0..1024)
            .map(|index| format!("[{index},\"{index}\"]"))
            .collect::<Vec<_>>()
            .join(",");
        let body = format!(
            "{{\"data\":{{\"result\":[{{\"values\":[{points}],\"metric\":{{\"z\":\"y\",\"a\":\"b\"}}}}],\"resultType\":\"matrix\"}},\"status\":\"success\"}}"
        );
        assert_matches_tree(body.as_bytes());
    }
}
