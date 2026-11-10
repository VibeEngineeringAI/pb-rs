//! Differential tests: the single-pass writer against the former pipeline
//! (request-layer envelope check, `gojson::validate`, then the re-slicing
//! serializer), comparing output bytes and every error.
use crate::gojson;
use crate::metrics::output::{self, RawJson};

/// What JSON mode did with a 2xx body before the single-pass writer.
fn former(body: &[u8]) -> RawJson {
    output::former_raw_json_response(body)
}

/// The single pass alone, never handing a body to the former pipeline.
fn single_pass(body: &[u8]) -> Option<RawJson> {
    match super::render_unbounded(body) {
        super::Pass::Rendered(rendered) => Some(match rendered.envelope_error {
            Some(message) => RawJson::Envelope(message),
            None => RawJson::Output(rendered.output),
        }),
        super::Pass::Invalid => None,
        super::Pass::Costly => panic!("an unbounded pass gave up"),
    }
}

fn check(body: &[u8]) -> RawJson {
    let expected = former(body);
    let actual = output::raw_json_response(body);
    if expected != actual {
        let show = |result: &RawJson| match result {
            RawJson::Output(bytes) => format!("Output({:?})", String::from_utf8_lossy(bytes)),
            other => format!("{other:?}"),
        };
        panic!(
            "body {:?}\n  bytes {body:x?}\n former {}\ncurrent {}",
            String::from_utf8_lossy(body),
            show(&expected),
            show(&actual)
        );
    }
    // The writer itself, for bodies the entry point hands to the pipeline.
    match single_pass(body) {
        Some(rendered) => assert!(
            rendered == expected,
            "single pass {:?}",
            String::from_utf8_lossy(body)
        ),
        None => assert!(
            matches!(expected, RawJson::Invalid(_)),
            "single pass rejected {:?}",
            String::from_utf8_lossy(body)
        ),
    }
    // The entry point without the envelope check.
    let expected = output::former_raw_json(body).map(String::into_bytes);
    assert!(
        output::render("run", body, true, "") == expected,
        "render {:?}",
        String::from_utf8_lossy(body)
    );
    actual
}

/// An independent second oracle: the generic tree pipeline.
fn assert_matches_tree(body: &[u8]) {
    let document = gojson::decode_ordered(body).unwrap();
    let sorted = gojson::Document::new(gojson::sorted(document.into_map().into_inner()));
    let RawJson::Output(output) = output::raw_json_response(body) else {
        panic!("{:?}", String::from_utf8_lossy(body));
    };
    assert_eq!(
        String::from_utf8(output).unwrap(),
        crate::render::json(&sorted, false)
    );
    check(body);
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        // xorshift64*: deterministic, dependency-free test input.
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }
    fn below(&mut self, bound: usize) -> usize {
        (self.next() >> 33) as usize % bound
    }
    fn one_in(&mut self, odds: usize) -> bool {
        self.below(odds) == 0
    }
    fn pick<'a, T: ?Sized>(&mut self, items: &[&'a T]) -> &'a T {
        items[self.below(items.len())]
    }
}

fn space(rng: &mut Rng, out: &mut Vec<u8>) {
    if rng.one_in(4) {
        for _ in 0..rng.below(3) + 1 {
            out.push(*rng.pick(&[&b' ', &b'\t', &b'\r', &b'\n']));
        }
    }
}

const NUMBERS: &[&str] = &[
    "0",
    "-0",
    "1",
    "-1",
    "7",
    "42",
    "1700000000",
    "1704067200.123",
    "0.5",
    "-0.000",
    "1.2300",
    "1e3",
    "1E3",
    "1E+003",
    "1e-003",
    "1.50e-10",
    "-1.5E-7",
    "0e0",
    "0e-10",
    "0E+0",
    "1e10000",
    "-1e-10000",
    "123456789012345678901234567890",
    "0.000000000000000000000000000001",
    "18446744073709551615",
    "-9223372036854775808",
];
const BAD_NUMBERS: &[&str] = &[
    "01",
    "00",
    "-",
    "-a",
    "1.",
    "1.e3",
    ".5",
    "1e",
    "1e+",
    "1E-",
    "+1",
    "0x10",
    "NaN",
    "Infinity",
    "-Infinity",
    "1_000",
    "--1",
    "1.2.3",
    "1e1e1",
    "0123",
    "-01",
    "1,",
    "٣",
];

const TEXT: &[&[u8]] = &[
    b"",
    b"a",
    b"b",
    b"up",
    b"http_requests_total",
    b"api-server",
    b"10.0.0.1:9090",
    b"/api/v1/item/7",
    b"1.5",
    b"NaN",
    b"+Inf",
    b"success",
    b"error",
    b"bad_data",
    b"<&>",
    b" ",
    b"'",
    b"/",
    b"\x7f",
    br#"\""#,
    br"\\",
    br"\/",
    br"\b",
    br"\f",
    br"\n",
    br"\r",
    br"\t",
    br"\u0000",
    br"\u001f",
    br"\u001F",
    br"\u0008",
    br"\u007f",
    br"\u0022",
    br"\u005c",
    br"\u005C",
    br"\u002f",
    br"\u0061",
    br"\u00e9",
    br"\u2028",
    br"\u2029",
    br"\u202a",
    br"\ufffd",
    br"\ud83d\ude00",
    br"\uD83D\uDE00",
    br"\ud800",
    br"\udbff",
    br"\udc00",
    br"\udfff",
    br"\ud800\u0041",
    br"\ud800\ud800\udc00",
    br"\ud800\\u",
    br"\ud800\n",
    "é".as_bytes(),
    "€".as_bytes(),
    "😀".as_bytes(),
    "日本".as_bytes(),
    "\u{2028}".as_bytes(),
    "\u{2029}".as_bytes(),
    "\u{2027}".as_bytes(),
    "\u{202a}".as_bytes(),
    "\u{fffd}".as_bytes(),
    "\u{10ffff}".as_bytes(),
    "\u{17f}".as_bytes(),
    "\u{212a}".as_bytes(),
    b"\xff",
    b"\x80",
    b"\xc0\xaf",
    b"\xc2",
    b"\xe2",
    b"\xe2\x80",
    b"\xe2\x82",
    b"\xa8",
    b"\xa9",
    b"\xed\xa0\x80",
    b"\xf0\x9f\x92",
    b"\xf4\x90\x80\x80",
    b"\xf8\x88\x80\x80\x80",
];
const BAD_TEXT: &[&[u8]] = &[
    b"\n",
    b"\t",
    b"\x00",
    b"\x1f",
    br"\x",
    br"\a",
    br"\U0041",
    br"\u12",
    br"\u12G4",
    br"\u+123",
    br"\ud800\u12",
    br"\ud800\uZZZZ",
    b"\"",
    b"\\",
];

fn string(rng: &mut Rng, out: &mut Vec<u8>) {
    out.push(b'"');
    let pieces = if rng.one_in(3) { rng.below(6) } else { 1 };
    for _ in 0..pieces {
        if rng.one_in(60) {
            out.extend_from_slice(rng.pick(BAD_TEXT));
        } else {
            out.extend_from_slice(rng.pick(TEXT));
        }
    }
    out.push(b'"');
}

/// Keys that collide after decoding or case folding, and keys whose encoded
/// order differs from their decoded order.
const KEYS: &[&[u8]] = &[
    b"status",
    b"Status",
    b"STATUS",
    "\u{17f}tatus".as_bytes(),
    br"\u0073tatus",
    br"\u017ftatus",
    br"stat\u0075s",
    b"error",
    b"Error",
    b"ERROR",
    br"\u0045rror",
    b"errorType",
    b"errortype",
    b"ERRORTYPE",
    b"error_type",
    "error\u{17f}".as_bytes(),
    "\u{212a}".as_bytes(),
    b"data",
    b"Data",
    b"result",
    b"resultType",
    b"metric",
    b"value",
    b"values",
    b"histograms",
    b"warnings",
    b"infos",
    b"stats",
    b"__name__",
    b"job",
    b"instance",
    b"le",
    b"",
    b"a",
    b"b",
    b"z",
    b"A",
    b"aa",
    br"\u0061",
    br"a\u0000",
    br#"a\""#,
    br"a\\",
    br"a\/",
    b"a/",
    br"a\n",
    b"a b",
    b"a\x7f",
    "é".as_bytes(),
    br"\u00e9",
    br"\u00E9",
    "\u{2028}".as_bytes(),
    br"\u2028",
    "\u{fffd}".as_bytes(),
    br"\ufffd",
    br"\ud800",
    br"\udc00",
    b"\xff",
    b"\x80",
    b"\xe2\x82",
    "\u{fffd}\u{fffd}".as_bytes(),
    "😀".as_bytes(),
    br"\ud83d\ude00",
];

fn key(rng: &mut Rng, out: &mut Vec<u8>) {
    if rng.one_in(10) {
        string(rng, out);
    } else {
        out.push(b'"');
        out.extend_from_slice(rng.pick(KEYS));
        out.push(b'"');
    }
}

fn scalar(rng: &mut Rng, out: &mut Vec<u8>) {
    match rng.below(12) {
        0 => out.extend_from_slice(b"null"),
        1 => out.extend_from_slice(b"true"),
        2 => out.extend_from_slice(b"false"),
        3..=6 => out.extend_from_slice(rng.pick(NUMBERS).as_bytes()),
        7 if rng.one_in(8) => out.extend_from_slice(rng.pick(BAD_NUMBERS).as_bytes()),
        8 if rng.one_in(8) => out.extend_from_slice(rng.pick(&[
            &b"nul"[..],
            b"nulL",
            b"True",
            b"tru",
            b"truee",
            b"fals",
            b"falsy",
            b"nil",
            b"undefined",
            b"'a'",
        ])),
        _ => string(rng, out),
    }
}

fn members(rng: &mut Rng, depth: usize, count: usize, out: &mut Vec<u8>) {
    out.push(b'{');
    for index in 0..count {
        if index > 0 {
            out.push(b',');
        }
        space(rng, out);
        key(rng, out);
        space(rng, out);
        out.push(b':');
        space(rng, out);
        value(rng, depth + 1, out);
        space(rng, out);
    }
    space(rng, out);
    out.push(b'}');
}

fn value(rng: &mut Rng, depth: usize, out: &mut Vec<u8>) {
    let container = depth < 6 && rng.below(10) < 4;
    if !container {
        return scalar(rng, out);
    }
    let count = match rng.below(8) {
        0 => 0,
        1..=4 => rng.below(4) + 1,
        5 | 6 => rng.below(9) + 1,
        _ => rng.below(40) + 1,
    };
    if rng.one_in(2) {
        members(rng, depth, count, out);
    } else {
        out.push(b'[');
        for index in 0..count {
            if index > 0 {
                out.push(b',');
            }
            space(rng, out);
            value(rng, depth + 1, out);
            space(rng, out);
        }
        space(rng, out);
        out.push(b']');
    }
}

fn spelled(rng: &mut Rng, name: &str, out: &mut Vec<u8>) {
    out.push(b'"');
    match rng.below(12) {
        0 => out.extend_from_slice(name.to_ascii_uppercase().as_bytes()),
        1 => {
            // Mixed case, with the Unicode folds encoding/json accepts.
            for c in name.chars() {
                match (c, rng.below(4)) {
                    ('s', 0) => out.extend_from_slice("\u{17f}".as_bytes()),
                    ('s', 1) => out.extend_from_slice(br"\u017f"),
                    ('k', 0) => out.extend_from_slice("\u{212a}".as_bytes()),
                    (c, 2) => out.push(c.to_ascii_uppercase() as u8),
                    (c, 3) => out.extend_from_slice(format!("\\u{:04x}", c as u32).as_bytes()),
                    (c, _) => out.push(c as u8),
                }
            }
        }
        _ => out.extend_from_slice(name.as_bytes()),
    }
    out.push(b'"');
}

fn fields(rng: &mut Rng, out: &mut Vec<u8>, mut fields: Vec<(&str, Vec<u8>)>) {
    // Unsorted, repeated and unknown members at this level.
    for _ in 0..rng.below(3) {
        if rng.one_in(2) && !fields.is_empty() {
            let copy = rng.below(fields.len());
            let mut replacement = Vec::new();
            value(rng, 4, &mut replacement);
            let name = fields[copy].0;
            fields.push((
                name,
                if rng.one_in(2) {
                    replacement
                } else {
                    fields[copy].1.clone()
                },
            ));
        } else {
            let mut extra = Vec::new();
            value(rng, 4, &mut extra);
            fields.push((
                rng.pick(&["unknown", "", "a", "zz", "Data", "stats"]),
                extra,
            ));
        }
    }
    if !rng.one_in(4) {
        for index in (1..fields.len()).rev() {
            fields.swap(index, rng.below(index + 1));
        }
    }
    out.push(b'{');
    for (index, (name, value)) in fields.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        space(rng, out);
        spelled(rng, name, out);
        space(rng, out);
        out.push(b':');
        space(rng, out);
        out.extend_from_slice(value);
        space(rng, out);
    }
    out.push(b'}');
}

fn text_value(rng: &mut Rng, choices: &[&str]) -> Vec<u8> {
    let mut out = Vec::new();
    match rng.below(14) {
        0 => out.extend_from_slice(b"null"),
        1 => value(rng, 4, &mut out),
        2 => string(rng, &mut out),
        _ => {
            out.push(b'"');
            out.extend_from_slice(rng.pick(choices).as_bytes());
            out.push(b'"');
        }
    }
    out
}

fn sample(rng: &mut Rng, out: &mut Vec<u8>) {
    out.push(b'[');
    space(rng, out);
    out.extend_from_slice(rng.pick(NUMBERS).as_bytes());
    out.push(b',');
    space(rng, out);
    if rng.one_in(20) {
        scalar(rng, out);
    } else {
        out.push(b'"');
        out.extend_from_slice(
            rng.pick(&["1", "0.125", "NaN", "+Inf", "-Inf", "1e+06", ""])
                .as_bytes(),
        );
        out.push(b'"');
    }
    space(rng, out);
    out.push(b']');
}

fn series(rng: &mut Rng, kind: &str, out: &mut Vec<u8>) {
    let mut metric = Vec::new();
    let labels = (0..rng.below(6))
        .map(|_| {
            let mut text = Vec::new();
            string(rng, &mut text);
            (
                rng.pick(&["__name__", "job", "instance", "le", "code", "a"]),
                text,
            )
        })
        .collect();
    fields(rng, &mut metric, labels);
    let mut points = Vec::new();
    if kind == "matrix" {
        points.push(b'[');
        for index in 0..rng.below(5) {
            if index > 0 {
                points.push(b',');
            }
            sample(rng, &mut points);
        }
        points.push(b']');
    } else {
        sample(rng, &mut points);
    }
    let name = if kind == "matrix" { "values" } else { "value" };
    let mut members = vec![("metric", metric), (name, points)];
    if rng.one_in(8) {
        members.remove(rng.below(2));
    }
    fields(rng, out, members);
}

/// A Prometheus API envelope: matrix, vector, scalar and string results,
/// error envelopes, warnings and infos, with every level perturbed.
fn envelope(rng: &mut Rng, out: &mut Vec<u8>) {
    let kind = rng.pick(&["matrix", "vector", "scalar", "string", "streams", ""]);
    let mut result = Vec::new();
    match kind {
        "matrix" | "vector" => {
            result.push(b'[');
            for index in 0..rng.below(4) {
                if index > 0 {
                    result.push(b',');
                }
                space(rng, &mut result);
                series(rng, kind, &mut result);
            }
            result.push(b']');
        }
        "scalar" | "string" => sample(rng, &mut result),
        _ => value(rng, 3, &mut result),
    }
    let mut data = Vec::new();
    let mut kind_text = Vec::new();
    kind_text.push(b'"');
    kind_text.extend_from_slice(kind.as_bytes());
    kind_text.push(b'"');
    fields(
        rng,
        &mut data,
        vec![("resultType", kind_text), ("result", result)],
    );
    let mut members = Vec::new();
    let failed = rng.one_in(3);
    if !rng.one_in(10) {
        let choices: &[&str] = if failed {
            &["error", "error", "error", "Error", "errors", ""]
        } else {
            &["success", "success", "error", ""]
        };
        members.push(("status", text_value(rng, choices)));
    }
    if failed || rng.one_in(6) {
        if !rng.one_in(6) {
            members.push((
                "errorType",
                text_value(rng, &["bad_data", "timeout", "execution", ""]),
            ));
        }
        if !rng.one_in(6) {
            members.push((
                "error",
                text_value(
                    rng,
                    &["parse error", "query timed out", "a \\\"quoted\\\" <b>", ""],
                ),
            ));
        }
    }
    if !failed || rng.one_in(2) {
        members.push(("data", data));
    }
    for name in ["warnings", "infos"] {
        if rng.one_in(5) {
            let mut list = Vec::new();
            list.push(b'[');
            for index in 0..rng.below(3) {
                if index > 0 {
                    list.push(b',');
                }
                string(rng, &mut list);
            }
            list.push(b']');
            members.push((name, list));
        }
    }
    space(rng, out);
    fields(rng, out, members);
    space(rng, out);
}

const GARBAGE: &[&[u8]] = &[
    b"x", b" x", b"{}", b" {}", b"[]", b"]", b"}", b",", b":", b"null", b" 1", b"1", b"\"",
    b"\"a\"", b"\x00", b"\xff", b"\n\n", b" \t\r\n", b"{", b"[", b"{\"a\"", b"-", b"tru", b"0123",
];

/// A damaged copy of `body`: truncated, extended or edited at one place.
fn mutate(rng: &mut Rng, body: &[u8]) -> Vec<u8> {
    let mut out = body.to_vec();
    let at = rng.below(body.len() + 1);
    match rng.below(7) {
        0 => out.truncate(at),
        1 => out.extend_from_slice(rng.pick(GARBAGE)),
        2 if at < out.len() => {
            out[at] = *rng.pick(&[
                &b'{', &b'}', &b'[', &b']', &b',', &b':', &b'"', &b'\\', &b'u', &b'0', &b'1',
                &b'-', &b'+', &b'.', &b'e', &b'E', &b' ', &b'\n', &b'\x00', &b'\x1f', &b'\x7f',
                &b'\x80', &b'\xe2', &b'\xff', &b'n', &b't', &b'f', &b'a',
            ]);
        }
        3 if at < out.len() => {
            out.remove(at);
        }
        4 => {
            let piece = rng.pick(GARBAGE);
            out.splice(at..at, piece.iter().copied());
        }
        5 if at < out.len() => {
            // Repeat a slice, which often duplicates members or elements.
            let end = (at + rng.below(24) + 1).min(out.len());
            let piece = out[at..end].to_vec();
            out.splice(end..end, piece);
        }
        _ => {
            let mut prefix = rng.pick(GARBAGE).to_vec();
            prefix.extend_from_slice(&out);
            out = prefix;
        }
    }
    out
}

#[derive(Default)]
struct Tally {
    cases: usize,
    output: usize,
    envelope: usize,
    invalid: usize,
}
impl Tally {
    fn check(&mut self, body: &[u8]) {
        self.cases += 1;
        match check(body) {
            RawJson::Output(_) => self.output += 1,
            RawJson::Envelope(_) => self.envelope += 1,
            RawJson::Invalid(_) => self.invalid += 1,
        }
    }
}

#[test]
fn seeded_envelopes_match_former_pipeline() {
    let mut rng = Rng(0x9e37_79b9_7f4a_7c15);
    let mut tally = Tally::default();
    for _ in 0..40_000 {
        let mut body = Vec::new();
        envelope(&mut rng, &mut body);
        tally.check(&body);
        for _ in 0..2 {
            tally.check(&mutate(&mut rng, &body));
        }
    }
    // Every outcome must be well represented, or the comparison proves little.
    assert_eq!(tally.cases, 120_000);
    assert!(tally.output > 20_000, "{}", tally.output);
    assert!(tally.envelope > 5_000, "{}", tally.envelope);
    assert!(tally.invalid > 20_000, "{}", tally.invalid);
}

#[test]
fn seeded_documents_match_former_pipeline() {
    let mut rng = Rng(0x0123_4567_89ab_cdef);
    let mut tally = Tally::default();
    for _ in 0..40_000 {
        let mut body = Vec::new();
        space(&mut rng, &mut body);
        value(&mut rng, 0, &mut body);
        space(&mut rng, &mut body);
        tally.check(&body);
        for _ in 0..2 {
            tally.check(&mutate(&mut rng, &body));
        }
    }
    assert_eq!(tally.cases, 120_000);
    assert!(tally.output > 20_000, "{}", tally.output);
    assert!(tally.invalid > 20_000, "{}", tally.invalid);
}

#[test]
fn every_truncation_and_single_byte_edit_matches_former_pipeline() {
    let fixtures: &[&[u8]] = &[
        br#"{"status":"success","data":{"resultType":"matrix","result":[{"metric":{"__name__":"up","job":"a"},"values":[[1704067200,"1"],[1704067260.5,"NaN"]]}]}}"#,
        br#"{"status":"error","errorType":"bad_data","error":"parse \"error\" \u00e9"}"#,
        br#" { "Status" : "error" , "ERROR" : null , "error" : "x" , "errortype" : "" } "#,
        br#"{"x":[null,true,false,-0,1.50e+03],"x":{"a":"a\n\uD800\uDC00\uD800\u0041"}}"#,
        b"[\"\xff\xe2\x82\",{\"\xff\":0},[],{}]",
        "{\"\u{17f}tatus\":\"error\",\"b\":\"\u{2028}\",\"a\":[]}".as_bytes(),
        br#""\ud800\ud800\udc00\udfff""#,
        b"-0.00E-001",
        b"true false",
    ];
    let mut cases = 0;
    for body in fixtures {
        check(body);
        for end in 0..body.len() {
            check(&body[..end]);
            cases += 1;
        }
        for index in 0..body.len() {
            for byte in b"{}[],:\\\"x0-+.eEunsr \n\x00\x7f\x80\xe2\xa8\xff" {
                let mut edited = body.to_vec();
                edited[index] = *byte;
                check(&edited);
                let mut inserted = body.to_vec();
                inserted.insert(index, *byte);
                check(&inserted);
                cases += 2;
            }
        }
    }
    assert_eq!(cases, 26_290);
}

#[test]
fn error_precedence_and_text() {
    let invalid = |body: &[u8]| match check(body) {
        RawJson::Invalid(message) => message,
        other => panic!("{other:?}"),
    };
    assert_eq!(invalid(b""), "server returned invalid JSON: EOF");
    assert_eq!(invalid(b" \n"), "server returned invalid JSON: EOF");
    assert_eq!(
        invalid(b"{"),
        "server returned invalid JSON: unexpected EOF"
    );
    assert_eq!(invalid(b"{} {}"), "server returned multiple JSON values");
    assert_eq!(invalid(b"0123"), "server returned multiple JSON values");
    assert_eq!(
        invalid(b"{}x"),
        "server returned invalid JSON: invalid character 'x' looking for beginning of value"
    );
    // A second value outranks the first value's error envelope, and so does
    // any syntax error after it.
    assert_eq!(
        invalid(br#"{"status":"error","error":"x"} {}"#),
        "server returned multiple JSON values"
    );
    assert_eq!(
        invalid(br#"{"status":"error","error":"x"} ]"#),
        "server returned invalid JSON: invalid character ']' looking for beginning of value"
    );
    assert_eq!(
        invalid(br#"{"status":"error","error":"x","a":01}"#),
        "server returned invalid JSON: invalid character '1' after object key:value pair"
    );

    let envelope = |body: &[u8]| match check(body) {
        RawJson::Envelope(message) => message,
        other => panic!("{other:?}"),
    };
    assert_eq!(
        envelope(br#"{"status":"error","errorType":"bad_data","error":"boom"}"#),
        "PromQL error (bad_data): boom"
    );
    assert_eq!(envelope(br#"{"status":"error"}"#), "PromQL error: ");
    // Last value wins, null leaves the field alone, keys fold like Go's.
    assert_eq!(
        envelope(
            &[
                "{\"error\":\"first\",\"STATUS\":\"success\",\"\u{17f}tatus\":\"error\",\"status\":null,"
                    .as_bytes(),
                b"\"Error\":\"\\ud800<\\u00e9>\\n\",\"ERRORTYPE\":\"\xff\"}",
            ]
            .concat()
        ),
        "PromQL error (\u{fffd}): \u{fffd}<é>\n"
    );
    let printed = |body: &[u8]| matches!(check(body), RawJson::Output(_));
    // A mistyped envelope field anywhere fails Go's decode and skips the check.
    assert!(printed(br#"{"status":"error","error":5}"#));
    assert!(printed(br#"{"errorType":[],"status":"error","error":"x"}"#));
    assert!(printed(br#"{"status":"error","status":{}}"#));
    assert!(printed(br#"{"status":"error","status":"success"}"#));
    assert!(printed(br#"{"data":{"status":"error"}}"#));
    assert!(printed(br#"[{"status":"error"}]"#));
    assert!(printed(br#""error""#));
}

#[test]
fn sorted_objects_replace_duplicate_decoded_keys() {
    assert_matches_tree(
        br#" {"z":0,"x":{"b":true,"a":null},"\u0078":[{"c":1,"a":2,"c":3}],"a":{},"z":[]} "#,
    );
    assert_matches_tree(br#"{"\ud800":1,"\ufffd":2,"\udfff":3}"#);
    // Encoded order differs from decoded order.
    assert_matches_tree(br#"{"a\u0000":1,"a\"":2,"a!":3,"a\\":4,"a\u2028":5,"a~":6,"a":7}"#);
    assert_matches_tree(b"{\"b\":1,\"\xff\":2,\"a\":3,\"\\ufffd\":4,\"\xc3\xa9\":5,\"\\u00e9\":6}");
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
        "[\"\u{2028}\",\"a\u{2029}b\",\"\u{2027}\u{202a}\",\"\u{7f}\",\"\\u007f\\u001f\\b\\f\"]"
            .as_bytes(),
        b"[\"\xe2\x80\",\"\xe2\x80\xa8\xe2\x80\",\"\xe2\",\"\xe2\x80\xe2\x80\xa9\"]",
    ] {
        assert_matches_tree(body);
    }
}

fn nested(open: &str, close: &str, depth: usize, core: &str) -> String {
    format!("{}{core}{}", open.repeat(depth), close.repeat(depth))
}

#[test]
fn nesting_matches_former_pipeline_around_the_indent_table() {
    for depth in [1, 2, 28, 29, 30, 31, 32, 33, 34, 130, 512] {
        for (open, close) in [("[", "]"), ("{\"x\":", "}"), ("[{\"b\":0,\"a\":", "}]")] {
            for core in [
                "{\"z\":1e3,\"a\":\"x\"}",
                "[]",
                "{}",
                "1",
                "[1,[2,{}],\"\\n\"]",
            ] {
                assert_matches_tree(nested(open, close, depth, core).as_bytes());
            }
        }
    }
    // Unsorted members at every level of a deep document.
    assert_matches_tree(nested("{\"b\":[1,2],\"a\":", ",\"0\":null}", 300, "\"x\"").as_bytes());
}

/// The indented form of `depth` nested containers around `core`, written
/// out directly: at the limit the former renderer takes minutes unoptimized.
fn nested_output(open: &str, close: &str, depth: usize, core: &str) -> Vec<u8> {
    let mut out = Vec::new();
    for level in 0..depth {
        out.extend_from_slice(&open.as_bytes()[..1]);
        out.push(b'\n');
        out.resize(out.len() + 2 * (level + 1), b' ');
        if open.len() > 1 {
            out.extend_from_slice(open[1..].replace(':', ": ").as_bytes());
        }
    }
    out.extend_from_slice(core.as_bytes());
    for level in (0..depth).rev() {
        out.push(b'\n');
        out.resize(out.len() + 2 * level, b' ');
        out.extend_from_slice(close.as_bytes());
    }
    out.push(b'\n');
    out
}

#[test]
fn depth_limit_matches_former_pipeline() {
    for (open, close) in [("[", "]"), ("{\"x\":", "}")] {
        // The construction above is the former renderer's output.
        let body = nested(open, close, 700, "[]");
        assert!(check(body.as_bytes()) == RawJson::Output(nested_output(open, close, 700, "[]")));
        for (depth, core) in [(10_000, "0"), (9_999, "[]"), (9_999, "{}")] {
            let body = nested(open, close, depth, core);
            assert_eq!(gojson::validate(body.as_bytes()), Ok(()));
            assert!(
                output::raw_json_response(body.as_bytes())
                    == RawJson::Output(nested_output(open, close, depth, core))
            );
        }
        for (depth, core) in [(10_001, "0"), (10_000, "[]"), (10_000, "{}"), (10_000, "[")] {
            let body = nested(open, close, depth, core);
            assert_eq!(
                check(body.as_bytes()),
                RawJson::Invalid(format!(
                    "server returned invalid JSON: invalid character '{}' exceeded max depth",
                    if depth == 10_001 {
                        &open[..1]
                    } else {
                        &core[..1]
                    }
                ))
            );
        }
        // Unclosed at the limit, and far past it.
        check(open.repeat(10_000).as_bytes());
        check(open.repeat(200_000).as_bytes());
    }
}

/// One full comparison with the former renderer at the limit itself.
#[test]
#[ignore = "the former renderer needs minutes for 200 MB of indentation unoptimized"]
fn deepest_document_matches_former_pipeline() {
    for (open, close) in [("[", "]"), ("{\"x\":", "}")] {
        check(nested(open, close, 10_000, "0").as_bytes());
    }
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

/// Members far larger than their siblings, in every position, so the member
/// that stays in the output moves both ways and is also the one replaced.
#[test]
fn reordering_moves_large_members_in_place() {
    let large = |fill: usize| {
        let items = (0..20_000)
            .map(|index| format!("[{},\"{fill}\"]", index + fill))
            .collect::<Vec<_>>()
            .join(",");
        format!("[{items}]")
    };
    let (first, second) = (large(1), large(2));
    for body in [
        format!("{{\"status\":\"success\",\"data\":{first}}}"),
        format!("{{\"data\":{first},\"a\":1}}"),
        format!("{{\"m\":{first},\"z\":1,\"a\":2,\"y\":3,\"b\":4}}"),
        format!("{{\"z\":1,\"m\":{first},\"a\":2}}"),
        format!("{{\"m\":{first},\"m\":2,\"a\":3}}"),
        format!("{{\"m\":1,\"b\":2,\"m\":{first},\"a\":3}}"),
        format!("{{\"m\":{first},\"a\":3,\"m\":{second},\"m\":{first}}}"),
        format!("{{\"z\":{first},\"a\":{second}}}"),
        format!("{{\"a\":{second},\"z\":{first},\"a\":{first},\"k\":{second}}}"),
        format!("{{\"b\":{{\"z\":{first},\"y\":{{\"q\":{second},\"p\":1}}}},\"a\":0}}"),
    ] {
        assert_matches_tree(body.as_bytes());
    }
}

#[test]
fn reordering_handles_many_members() {
    let mut rng = Rng(7);
    for (count, names) in [(2, 2), (3, 2), (50, 10), (5_000, 400), (5_000, 1_000_000)] {
        let members = (0..count)
            .map(|index| {
                let mut text = Vec::new();
                value(&mut rng, 4, &mut text);
                let text = if gojson::validate(&text).is_ok() {
                    String::from_utf8_lossy(&text).into_owned()
                } else {
                    index.to_string()
                };
                format!("\"k{}\":{text}", rng.below(names))
            })
            .collect::<Vec<_>>()
            .join(",");
        assert_matches_tree(format!("{{{members}}}").as_bytes());
        assert_matches_tree(format!("[{{{members}}},{{\"b\":{{{members}}},\"a\":[]}}]").as_bytes());
    }
}

/// Tokens around the fixed copy block's size, including ones that end within
/// a block's length of the end of the body.
#[test]
fn token_lengths_around_the_copy_block() {
    for length in (0..40).chain([62, 63, 64, 65, 66, 127, 128, 129, 1_000]) {
        let text = "t".repeat(length);
        let digits = "7".repeat(length.max(1));
        let tail = "x".repeat(length);
        for body in [
            format!("\"{text}\""),
            digits.clone(),
            format!("-{digits}.{digits}e+{digits}"),
            format!("[\"{text}\",{digits},\"{text}\"]"),
            format!("{{\"{text}\":{digits}}}"),
            format!("{{\"{text}\":\"{text}\",\"a\":0.{digits}}}"),
            format!("[{digits},\"{text}\",{{\"{text}\":[\"{tail}\"]}}]"),
            format!("[\"{text}\\n\",\"é{text}\",\"{text}\u{2028}\",\"{text}"),
        ] {
            check(body.as_bytes());
            check(format!(" {body}\n").as_bytes());
        }
    }
}

/// Out-of-order keys whose later-sorting neighbours are set aside while the
/// value is written, at every size around the limit and nested in each other.
#[test]
fn out_of_order_members_are_held_around_the_limit() {
    for size in [
        0, 1, 500, 990, 1_000, 1_010, 1_020, 1_030, 1_040, 2_000, 5_000,
    ] {
        let pad = "p".repeat(size);
        for body in [
            format!("{{\"z\":\"{pad}\",\"a\":[1,2]}}"),
            format!("{{\"y\":1,\"z\":\"{pad}\",\"a\":[1,2],\"b\":3}}"),
            format!(
                "{{\"m\":0,\"y\":\"{pad}\",\"z\":2,\"n\":{{\"k\":[]}},\"a\":\"{pad}\",\"zz\":4}}"
            ),
            format!("{{\"a\":0,\"y\":\"{pad}\",\"z\":2,\"y\":3}}"),
            format!("{{\"a\":0,\"y\":\"{pad}\",\"z\":2,\"a\":3,\"b\":4}}"),
            // A held member waits while a nested object holds its own.
            format!(
                "{{\"z\":\"{pad}\",\"y\":{{\"q\":\"{pad}\",\"p\":{{\"d\":1,\"c\":[{{\"b\":2,\"a\":3}}]}},\"o\":5}},\"x\":6}}"
            ),
            // Held members are dropped with a body that turns out invalid.
            format!("{{\"z\":\"{pad}\",\"a\":[1,2"),
            format!("{{\"z\":\"{pad}\",\"a\":[1,2]]"),
            format!("{{\"z\":\"{pad}\",\"a\":tru}}"),
        ] {
            check(body.as_bytes());
        }
    }
    // The envelope shape this exists for, with members before and after.
    let result = (0..3_000)
        .map(|index| format!("[{index},\"1\"]"))
        .collect::<Vec<_>>()
        .join(",");
    for body in [
        format!(
            "{{\"status\":\"success\",\"data\":{{\"resultType\":\"matrix\",\"result\":[{result}]}}}}"
        ),
        format!(
            "{{\"status\":\"success\",\"warnings\":[\"w\"],\"data\":{{\"resultType\":\"matrix\",\"stats\":{{}},\"result\":[{result}]}},\"infos\":[]}}"
        ),
        format!(
            "{{\"status\":\"success\",\"data\":[{result}],\"Status\":\"ok\",\"a\":[{result}]}}"
        ),
    ] {
        assert_matches_tree(body.as_bytes());
    }
    // An envelope error discovered after the result was written.
    assert_eq!(
        check(
            format!("{{\"status\":\"success\",\"data\":[{result}],\"Status\":\"error\"}}")
                .as_bytes()
        ),
        RawJson::Envelope("PromQL error: ".into())
    );
    // Every permutation of five members, each with a container or a scalar.
    let names = ["a", "b", "c", "d", "e"];
    let mut order = [0, 1, 2, 3, 4];
    let mut permutations = 0;
    loop {
        for containers in 0..32 {
            let members = order
                .iter()
                .map(|index| {
                    if containers >> index & 1 == 1 {
                        format!("\"{}\":[{index}]", names[*index])
                    } else {
                        format!("\"{}\":{index}", names[*index])
                    }
                })
                .collect::<Vec<_>>()
                .join(",");
            assert_matches_tree(format!("{{{members}}}").as_bytes());
        }
        permutations += 1;
        // Next lexicographic permutation.
        let Some(pivot) = (0..4).rev().find(|index| order[*index] < order[index + 1]) else {
            break;
        };
        let swap = (pivot + 1..5)
            .rev()
            .find(|index| order[*index] > order[pivot])
            .unwrap();
        order.swap(pivot, swap);
        order[pivot + 1..].reverse();
    }
    assert_eq!(permutations, 120);
}

/// `{"k":0,"k":{"k":0,"k":…0…}}`: every level repeats a key, so every level
/// is put in order when it closes, moving everything inside it.
fn repeated_key_nest(depth: usize, first: &str) -> String {
    let mut body = format!("{{{first}:0,\"k\":").repeat(depth);
    body.push('0');
    body.push_str(&"}".repeat(depth));
    body
}

#[test]
fn deeply_nested_reordering_is_handed_to_the_former_pipeline() {
    for first in ["\"k\"", "\"z\""] {
        // Shallow nests stay in the single pass.
        let body = repeated_key_nest(40, first);
        assert!(matches!(
            super::render(body.as_bytes()),
            super::Pass::Rendered(_)
        ));
        check(body.as_bytes());
        // A deep one would move output cubic in its depth.
        let body = repeated_key_nest(1500, first);
        assert!(matches!(
            super::render(body.as_bytes()),
            super::Pass::Costly
        ));
        assert_eq!(
            output::raw_json_response(body.as_bytes()),
            former(body.as_bytes())
        );
        // Abandoned bodies keep the pipeline's diagnostics too.
        for damaged in [
            format!("{body} 1"),
            format!("{body}}}"),
            body[..body.len() - 1].to_owned(),
        ] {
            assert_eq!(
                output::raw_json_response(damaged.as_bytes()),
                former(damaged.as_bytes())
            );
            assert!(matches!(
                output::raw_json_response(damaged.as_bytes()),
                RawJson::Invalid(_)
            ));
        }
    }
}

#[test]
fn response_shapes_out_of_key_order_stay_in_the_single_pass() {
    // Every object out of order at once: the envelope (a long warning list
    // before the data), the data, each series and each label set.
    let series: Vec<String> = (0..2_000)
        .map(|s| {
            let values: Vec<String> = (0..64)
                .map(|i| format!("[{},\"{}\"]", 1_704_067_200 + 60 * i, s * 31 + i))
                .collect();
            format!(
                "{{\"values\":[{}],\"metric\":{{\"job\":\"api\",\"instance\":\"10.0.0.{s}:9090\",\"__name__\":\"up\"}}}}",
                values.join(",")
            )
        })
        .collect();
    let warnings = vec!["\"a long warning about the query\""; 20_000].join(",");
    let body = format!(
        "{{\"warnings\":[{warnings}],\"status\":\"success\",\"data\":{{\"resultType\":\"matrix\",\"result\":[{}],\"aaa\":1}}}}",
        series.join(",")
    );
    assert!(matches!(
        super::render(body.as_bytes()),
        super::Pass::Rendered(_)
    ));
    check(body.as_bytes());
}
