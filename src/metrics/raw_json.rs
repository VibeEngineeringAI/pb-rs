//! Pretty JSON from validated input spans. Containers borrow their values
//! instead of allocating a second complete JSON tree; objects retain Go's
//! sorted keys and last duplicate member semantics. Output is streamed
//! through a bounded buffer rather than rendered into one complete string.
use crate::gojson;
use serde::Serialize;
use std::{
    collections::BTreeMap,
    io::{self, BufWriter, Write},
};

/// Largest write handed to the destination, except a single serializer
/// fragment (one long string or number lexeme) larger than the buffer.
const OUTPUT_BUFFER: usize = 64 * 1024;

/// Writes exactly `render::json(value, false)` for the validated body. The
/// caller validates the complete body and supplies single-value input; syntax
/// and trailing-value diagnostics remain its responsibility. Rendering a
/// validated body has no error path, so every error here is the destination's
/// own write error, returned unchanged. Buffered bytes are discarded after a
/// failure instead of being retried when the buffer is dropped.
pub(super) fn write_validated<W: Write>(body: &[u8], out: W) -> io::Result<()> {
    let mut buffered = BufWriter::with_capacity(OUTPUT_BUFFER, out);
    let result = write_buffered(body, &mut buffered);
    if let Err(error) = result {
        let _ = buffered.into_parts();
        return Err(error);
    }
    // Drain our own buffer without calling the destination's flush method:
    // the former single write_all did not flush the caller's writer either.
    match buffered.into_inner() {
        Ok(_) => Ok(()),
        Err(error) => {
            let (error, buffered) = error.into_parts();
            let _ = buffered.into_parts();
            Err(error)
        }
    }
}

fn write_buffered<W: Write>(body: &[u8], buffered: &mut BufWriter<W>) -> io::Result<()> {
    let mut escaped = LineSeparators {
        inner: &mut *buffered,
        held: 0,
    };
    let mut serializer = serde_json::Serializer::pretty(&mut escaped);
    RawValue(body)
        .serialize(serde_stacker::Serializer::new(&mut serializer))
        .map_err(|error| {
            // As render::json's expect: only the destination can fail.
            assert!(error.is_io(), "serializable output: {error}");
            io::Error::from(error)
        })?;
    escaped.finish()?;
    buffered.write_all(b"\n")
}

/// encoding/json escapes U+2028 and U+2029 even without HTML escaping
/// (render::json's final replacement). A partial E2 80 prefix is held so the
/// rewrite does not depend on how the serializer splits its writes.
struct LineSeparators<W> {
    inner: W,
    held: usize,
}

const SEPARATOR_PREFIX: [u8; 2] = [0xe2, 0x80];

impl<W: Write> LineSeparators<W> {
    fn finish(&mut self) -> io::Result<()> {
        let held = std::mem::take(&mut self.held);
        self.inner.write_all(&SEPARATOR_PREFIX[..held])
    }
}

impl<W: Write> Write for LineSeparators<W> {
    #[inline]
    fn write_all(&mut self, bytes: &[u8]) -> io::Result<()> {
        if self.held == 0 && !bytes.contains(&0xe2) {
            self.inner.write_all(bytes)
        } else {
            // write forwards every fragment with inner.write_all, so success
            // always means the complete input was accepted (including held bytes).
            self.write(bytes).map(|_| ())
        }
    }

    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        // Most serializer fragments contain no possible separator prefix.
        // Slice search can skip them without running the prefix state machine.
        if self.held == 0 && !bytes.contains(&0xe2) {
            self.inner.write_all(bytes)?;
            return Ok(bytes.len());
        }
        // bytes[run..index] pass through unchanged; while a prefix is held,
        // run == index.
        let mut run = 0;
        for (index, &byte) in bytes.iter().enumerate() {
            if self.held > 0 {
                match (self.held, byte) {
                    (1, 0x80) => {
                        self.held = 2;
                        run = index + 1;
                        continue;
                    }
                    (2, 0xa8 | 0xa9) => {
                        self.held = 0;
                        run = index + 1;
                        self.inner
                            .write_all(if byte == 0xa8 { b"\\u2028" } else { b"\\u2029" })?;
                        continue;
                    }
                    _ => self.finish()?,
                }
            }
            if byte == 0xe2 {
                self.inner.write_all(&bytes[run..index])?;
                self.held = 1;
                run = index + 1;
            }
        }
        self.inner.write_all(&bytes[run..])?;
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
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
    use crate::render;

    /// The former complete-string rendering, kept as the byte oracle.
    fn rendered(body: &[u8]) -> Vec<u8> {
        render::json(&RawValue(body), false).into_bytes()
    }

    fn streamed(body: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        write_validated(body, &mut out).unwrap();
        out
    }

    /// Accepts at most `limit` bytes per write and records each write size.
    struct Chunks {
        out: Vec<u8>,
        writes: Vec<usize>,
        limit: usize,
    }

    impl Write for Chunks {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            let count = bytes.len().min(self.limit);
            self.out.extend_from_slice(&bytes[..count]);
            self.writes.push(count);
            Ok(count)
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn assert_matches_tree(body: &[u8]) {
        gojson::validate(body).unwrap();
        let document = gojson::decode_ordered(body).unwrap();
        let sorted = gojson::Document::new(gojson::sorted(document.into_map().into_inner()));
        let expected = render::json(&sorted, false).into_bytes();
        assert_eq!(rendered(body), expected);
        assert_eq!(streamed(body), expected);
        let mut short = Chunks {
            out: Vec::new(),
            writes: Vec::new(),
            limit: 1,
        };
        write_validated(body, &mut short).unwrap();
        assert_eq!(short.out, expected);
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
    fn escapes_line_separators_in_raw_and_escaped_keys_and_values() {
        for body in [
            "{\"\u{2028}k\u{2029}\":\"a\u{2028}\u{2028}b\u{2029}\",\"\\u2028\":\"\u{2029}\"}",
            "[\"\u{2028}\",\"\u{2027}\u{202a}\u{20ac}\",\"\u{2028}\u{2029}\u{2028}\"]",
            "\"\u{2029}\"",
            "{\"\u{2028}\":{\"\u{2029}\":[\"\\u2029\u{2028}\"]}}",
        ] {
            assert_matches_tree(body.as_bytes());
            let output = streamed(body.as_bytes());
            assert!(
                !output
                    .windows(3)
                    .any(|w| w == "\u{2028}".as_bytes() || w == "\u{2029}".as_bytes())
            );
        }
        assert_eq!(
            streamed("{\"\u{2028}\":\"\u{2029}\"}".as_bytes()),
            b"{\n  \"\\u2028\": \"\\u2029\"\n}\n"
        );
    }

    #[test]
    fn line_separator_rewrite_survives_every_write_split() {
        // Prefix bytes that do not complete a separator pass through unchanged,
        // including a prefix that restarts a separator and one left at the end.
        let input: &[u8] =
            b"a\xe2\x80\xa8b\xe2\xe2\x80\xa9c\xe2\x80\x41\xe2\x82\xac\xe2\x80\xa8\xe2\x80";
        let expected: &[u8] = b"a\\u2028b\xe2\\u2029c\xe2\x80\x41\xe2\x82\xac\\u2028\xe2\x80";
        for split in 0..=input.len() {
            for second in split..=input.len() {
                let mut out = Vec::new();
                let mut escaped = LineSeparators {
                    inner: &mut out,
                    held: 0,
                };
                for part in [&input[..split], &input[split..second], &input[second..]] {
                    escaped.write_all(part).unwrap();
                }
                escaped.finish().unwrap();
                assert_eq!(out, expected, "split at {split} and {second}");
            }
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

    fn matrix(points: usize) -> String {
        let points = (0..points)
            .map(|index| format!("[{index}.{index},\"{index}e-3\"]"))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"data\":{{\"result\":[{{\"values\":[{points}],\"metric\":{{\"z\":\"y\",\"a\":\"b\",\"z\":\"\u{2028}\"}}}}],\"resultType\":\"matrix\"}},\"status\":\"success\"}}"
        )
    }

    #[test]
    fn handles_wide_sample_arrays() {
        assert_matches_tree(matrix(1024).as_bytes());
    }

    #[test]
    fn large_output_reaches_the_destination_in_bounded_writes() {
        let body = matrix(50_000);
        let expected = rendered(body.as_bytes());
        assert!(expected.len() > 8 * OUTPUT_BUFFER);
        let mut destination = Chunks {
            out: Vec::new(),
            writes: Vec::new(),
            limit: usize::MAX,
        };
        write_validated(body.as_bytes(), &mut destination).unwrap();
        assert_eq!(destination.out, expected);
        assert!(destination.writes.len() > 8);
        assert!(destination.writes.iter().all(|&size| size <= OUTPUT_BUFFER));
    }

    /// Fails every write once `capacity` bytes are accepted.
    struct Failing {
        accepted: usize,
        capacity: usize,
        calls_after_failure: usize,
        failed: bool,
    }

    #[derive(Debug)]
    struct Marker;
    impl std::fmt::Display for Marker {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.write_str("destination closed")
        }
    }
    impl std::error::Error for Marker {}

    impl Write for Failing {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            if self.failed {
                self.calls_after_failure += 1;
            }
            let count = bytes.len().min(self.capacity - self.accepted);
            if count == 0 && !bytes.is_empty() {
                self.failed = true;
                return Err(io::Error::new(io::ErrorKind::BrokenPipe, Marker));
            }
            self.accepted += count;
            Ok(count)
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn destination_flush_is_not_called() {
        struct FlushFails(Vec<u8>);
        impl Write for FlushFails {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                self.0.write(bytes)
            }
            fn flush(&mut self) -> io::Result<()> {
                Err(io::Error::other("destination flush must not be called"))
            }
        }
        for body in [b"[1]".to_vec(), matrix(20_000).into_bytes()] {
            let mut destination = FlushFails(Vec::new());
            write_validated(&body, &mut destination).unwrap();
            assert_eq!(destination.0, rendered(&body));
        }
    }

    #[test]
    fn write_errors_are_returned_unchanged_and_stop_output() {
        let body = matrix(20_000);
        let total = rendered(body.as_bytes()).len();
        for capacity in [
            0,
            1,
            OUTPUT_BUFFER - 1,
            OUTPUT_BUFFER,
            3 * OUTPUT_BUFFER + 7,
            total - 1,
        ] {
            let mut destination = Failing {
                accepted: 0,
                capacity,
                calls_after_failure: 0,
                failed: false,
            };
            let error = write_validated(body.as_bytes(), &mut destination).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
            assert!(
                error.get_ref().is_some_and(|e| e.is::<Marker>()),
                "{error:?}"
            );
            assert_eq!(destination.accepted, capacity);
            assert_eq!(destination.calls_after_failure, 0, "capacity {capacity}");
        }
    }
}
