use super::super::fuzz::{self, Rng};
use super::*;
use serde_json::Value;

/// The definition `object` must reproduce: the line through a serde_json
/// tree, when that is an object.
fn serde(line: &[u8]) -> Option<String> {
    match serde_json::from_slice::<Value>(line) {
        Ok(Value::Object(object)) => Some(serde_json::to_string(&object).unwrap()),
        _ => None,
    }
}

/// Whatever is accepted must be exactly what serde_json produces.
fn check(line: &[u8]) -> Option<String> {
    let fast = object(line);
    if let Some(fast) = &fast {
        let expected = serde(line);
        assert!(
            expected.as_ref() == Some(fast),
            "accepted {:?}\n  as    {fast:?}\n  serde {expected:?}",
            String::from_utf8_lossy(line)
        );
    }
    fast
}

fn nested(open: &str, leaf: &str, close: &str, depth: usize) -> String {
    format!("{}{leaf}{}", open.repeat(depth), close.repeat(depth))
}

#[test]
fn accepted_lines_are_copied_in_serde_json_form() {
    let deep = nested("{\"a\":", "1", "}", MAX_DEPTH);
    let deep_arrays = format!("{{\"a\":{}}}", nested("[", "", "]", MAX_DEPTH - 1));
    let wide = format!(
        "{{{}}}",
        (0..40)
            .map(|n| format!("\"key{n}\":{n}"))
            .collect::<Vec<_>>()
            .join(",")
    );
    let spaced_wide = wide.replace(',', " ,\t").replace(':', " : ");
    for (line, expected) in [
        ("{}", "{}"),
        (" \t{ }\r", "{}"),
        (
            r#" { "b" : 1 , "a" : [ ] , "c" : [ 1 , "x" , null , true , false , { } ] } "#,
            r#"{"b":1,"a":[],"c":[1,"x",null,true,false,{}]}"#,
        ),
        // Number text is kept whatever its size or precision.
        (
            r#"{"big":123456789012345678901234567890,"frac":0.1000000000000000055511151231257827}"#,
            r#"{"big":123456789012345678901234567890,"frac":0.1000000000000000055511151231257827}"#,
        ),
        (
            r#"{"n":[0,-1,-0.0,-0.5,1.50,10.010,18446744073709551616,-9223372036854775809]}"#,
            r#"{"n":[0,-1,-0.0,-0.5,1.50,10.010,18446744073709551616,-9223372036854775809]}"#,
        ),
        // Two-byte escapes are kept; a solidus loses its backslash.
        (
            r#"{"s":"A\/\t\b\f\n\ré\"\\","a\/b":"\/\/"}"#,
            "{\"s\":\"A/\\t\\b\\f\\n\\r\u{e9}\\\"\\\\\",\"a/b\":\"//\"}",
        ),
        // \u escapes become their character, escaped only where required.
        (
            r#"{"\u0061":"\u00e9\u00E9\u2713\ud83d\ude00\uD83D\uDE00\udbff\udfff"}"#,
            "{\"a\":\"\u{e9}\u{e9}\u{2713}\u{1f600}\u{1f600}\u{10ffff}\"}",
        ),
        (
            r#"{"c":"\u0022\u005c\u005C\u002f\u000a\u0008\u000c\u000d\u0009\u0000\u001f\u001F\u007f\u0020"}"#,
            "{\"c\":\"\\\"\\\\\\\\/\\n\\b\\f\\r\\t\\u0000\\u001f\\u001f\u{7f} \"}",
        ),
        (
            "{\"raw\":\"\u{7f}\u{80}\u{2028}\u{ffff}😀\",\"ключ\":\"日本\"}",
            "{\"raw\":\"\u{7f}\u{80}\u{2028}\u{ffff}😀\",\"ключ\":\"日本\"}",
        ),
        // Keys which only look like duplicates or private tokens.
        (
            r#"{"a":1,"A":2,"":3,"a ":4,"$":5,"$serde_json":6,"x":{"a":7}}"#,
            r#"{"a":1,"A":2,"":3,"a ":4,"$":5,"$serde_json":6,"x":{"a":7}}"#,
        ),
        (&deep, &deep),
        (&deep_arrays, &deep_arrays),
        (&spaced_wide, &wide),
    ] {
        assert_eq!(check(line.as_bytes()).as_deref(), Some(expected), "{line}");
    }
}

#[test]
fn valid_lines_serde_json_normalizes_are_deferred() {
    let too_deep = nested("{\"a\":", "1", "}", MAX_DEPTH + 1);
    let too_deep_arrays = format!("{{\"a\":{}}}", nested("[", "", "]", MAX_DEPTH));
    let wide_duplicate = format!(
        "{{{},\"key7\":true}}",
        (0..40)
            .map(|n| format!("\"key{n}\":{n}"))
            .collect::<Vec<_>>()
            .join(",")
    );
    let wide_kept = format!(
        "{{{}}}",
        (0..40)
            .map(|n| match n {
                7 => "\"key7\":true".to_owned(),
                n => format!("\"key{n}\":{n}"),
            })
            .collect::<Vec<_>>()
            .join(",")
    );
    for (line, expected) in [
        // Exponents are rewritten.
        (r#"{"n":1e5}"#, r#"{"n":1e+5}"#),
        (r#"{"n":1E5}"#, r#"{"n":1e+5}"#),
        (r#"{"n":1.5E-7}"#, r#"{"n":1.5e-7}"#),
        (r#"{"n":[0e0]}"#, r#"{"n":[0e+0]}"#),
        (r#"{"n":-0e1}"#, r#"{"n":-0e+1}"#),
        // A duplicate key keeps its first position and its last value,
        // however either occurrence is spelled and at any depth or width.
        (r#"{ "b" : 1, "a": 2, "b": 3 }"#, r#"{"b":3,"a":2}"#),
        (r#"{"a":1,"\u0061":2}"#, r#"{"a":2}"#),
        (r#"{"a/b":1,"a\/b":2}"#, r#"{"a/b":2}"#),
        (r#"{"a\nb":1,"a\u000ab":2}"#, r#"{"a\nb":2}"#),
        (r#"{"x":[{"k":1,"k":{}}]}"#, r#"{"x":[{"k":{}}]}"#),
        (&wide_duplicate, &wide_kept),
        // Deeper than this module follows, within serde_json's limit.
        (&too_deep, &too_deep),
        (&too_deep_arrays, &too_deep_arrays),
    ] {
        assert_eq!(object(line.as_bytes()), None, "{line}");
        assert_eq!(serde(line.as_bytes()).as_deref(), Some(expected), "{line}");
    }
    // The integer -0 is deferred whatever this serde_json version does.
    assert_eq!(object(br#"{"n":-0}"#), None);
    assert_eq!(object(br#"{"n":[1,-0]}"#), None);
    assert!(serde(br#"{"n":-0}"#).is_some());
}

#[test]
fn private_serde_json_keys_are_deferred() {
    // serde_json reads these objects as a number and as raw JSON, so the
    // first is not an object at all and the second is a different one.
    let number = br#"{"$serde_json::private::Number":"12"}"#;
    assert_eq!(serde(number), None);
    assert_eq!(object(number), None);
    let raw = br#"{"$serde_json::private::RawValue":"{\"inner\":1}"}"#;
    assert_eq!(serde(raw).as_deref(), Some(r#"{"inner":1}"#));
    assert_eq!(object(raw), None);
    // Anywhere in the document, first key or not.
    for line in [
        r#"{"a":{"$serde_json::private::Number":"12"}}"#,
        r#"{"a":[{"b":1,"$serde_json::private::RawValue":"null"}]}"#,
        r#"{"\u0024serde_json::private::Number":"12"}"#,
        r#"{"$serde_json::private::Unknown":1}"#,
    ] {
        assert_eq!(check(line.as_bytes()), None, "{line}");
    }
}

#[test]
fn lines_serde_json_rejects_are_deferred() {
    let beyond_serde = nested("{\"a\":", "1", "}", 200);
    for line in [
        &b""[..],
        b" ",
        b"null",
        b"[]",
        b"[{}]",
        b"\"{}\"",
        b"12",
        b"{",
        b"}",
        b"{}x",
        b"{}{}",
        b"{} 1",
        b"{},",
        b"\xef\xbb\xbf{}",
        b"{\"a\"}",
        b"{\"a\":}",
        b"{\"a\":1,}",
        b"{,\"a\":1}",
        b"{\"a\":1 \"b\":2}",
        b"{\"a\" 1}",
        b"{a:1}",
        b"{'a':1}",
        b"{1:1}",
        b"{\"a\":[1,]}",
        b"{\"a\":[,1]}",
        b"{\"a\":[1 2]}",
        b"{\"a\":[1}",
        b"{\"a\":{]}",
        b"{\"a\":tru}",
        b"{\"a\":True}",
        b"{\"a\":nulll}",
        b"{\"a\":falsey}",
        b"{\"a\":01}",
        b"{\"a\":-01}",
        b"{\"a\":00}",
        b"{\"a\":1.}",
        b"{\"a\":.5}",
        b"{\"a\":+1}",
        b"{\"a\":-}",
        b"{\"a\":--1}",
        b"{\"a\":1.5.5}",
        b"{\"a\":1e}",
        b"{\"a\":0x10}",
        b"{\"a\":NaN}",
        b"{\"a\":\"unterminated}",
        b"{\"a\":\"bad \\x escape\"}",
        b"{\"a\":\"bad \\U0041\"}",
        b"{\"a\":\"short \\u12\"}",
        b"{\"a\":\"short \\u12g4\"}",
        b"{\"a\":\"\\",
        b"{\"a\":\"\\u",
        b"{\"a\":\"\\ud800\"}",
        b"{\"a\":\"\\udc00\"}",
        b"{\"a\":\"\\udc00\\ud800\"}",
        b"{\"a\":\"\\ud800\\u0041\"}",
        b"{\"a\":\"\\ud800\\ud800\"}",
        b"{\"a\":\"\\ud83d\\n\"}",
        b"{\"a\":\"\\ud83dx\"}",
        b"{\"a\":\"\\ud83d\\u\"}",
        b"{\"a\":\"\\ud83d\\ude0\"}",
        b"{\"a\":\"raw\ttab\"}",
        b"{\"a\":\"raw\x01control\"}",
        b"{\"a\":\"raw\x00nul\"}",
        b"{\"a\tb\":1}",
        b"{\"a\":\"\xff\"}",
        b"{\"a\":\"\xc3\"}",
        b"{\"a\":\"\xed\xa0\x80\"}",
        b"{\"a\":\"\xc0\x80\"}",
        b"{\"\xff\":1}",
        b"{\"a\":1}\xff",
        beyond_serde.as_bytes(),
    ] {
        assert_eq!(serde(line), None, "{}", String::from_utf8_lossy(line));
        assert_eq!(object(line), None, "{}", String::from_utf8_lossy(line));
    }
}

#[test]
fn nesting_is_followed_to_the_limit_and_deferred_beyond_it() {
    for (open, leaf, close) in [
        ("{\"a\":", "1", "}"),
        ("{\"a\":", "{}", "}"),
        ("{\"a\":[", "", "]}"),
    ] {
        for depth in 1..=140 {
            let line = nested(open, leaf, close, depth);
            // Each repetition opens one or two levels, and the leaf may too.
            let levels = depth * open.matches(['{', '[']).count() + usize::from(leaf == "{}");
            let fast = check(line.as_bytes());
            assert_eq!(fast.is_some(), levels <= MAX_DEPTH, "{levels} levels");
            // serde_json itself gives up further out, so deferral is safe.
            assert_eq!(serde(line.as_bytes()).is_some(), levels < 128);
        }
    }
}

#[test]
fn seeded_lines_are_accepted_only_in_serde_json_form() {
    let mut rng = Rng::new(31);
    let (mut accepted, mut deferred, mut invalid) = (0, 0, 0);
    for _ in 0..200_000 {
        let line = fuzz::jsonl_line(&mut rng);
        match (check(&line), serde(&line)) {
            (Some(_), _) => accepted += 1,
            (None, Some(_)) => deferred += 1,
            (None, None) => invalid += 1,
        }
    }
    // Every outcome is well represented, so the comparison is not vacuous.
    assert!(accepted > 50_000, "{accepted} accepted");
    assert!(deferred > 20_000, "{deferred} deferred");
    assert!(invalid > 50_000, "{invalid} invalid");
}

/// A random document of the kinds this module accepts, as a serde_json
/// tree: any text, integers of any size, fractions, and unique keys.
fn ordinary(rng: &mut Rng, depth: usize) -> Value {
    match rng.below(if depth >= 4 { 6 } else { 9 }) {
        0 => Value::Null,
        1 => Value::Bool(rng.chance(50)),
        2 => Value::String(fuzz::text(rng, 5)),
        3 => Value::from(rng.next() >> rng.below(64)),
        4 => Value::from(-((rng.next() >> (1 + rng.below(63))) as i64) - 1),
        5 => {
            let lexeme = format!(
                "{}{}.{:03}",
                if rng.chance(50) { "-" } else { "" },
                rng.next() >> rng.below(64),
                rng.below(1000)
            );
            Value::Number(lexeme.parse().unwrap())
        }
        6 | 7 => {
            let keys = if rng.chance(5) { 60 } else { 6 };
            let keys = rng.below(keys);
            Value::Object(
                (0..keys)
                    .map(|index| {
                        (
                            format!("{}{index}", fuzz::text(rng, 2)),
                            ordinary(rng, depth + 1),
                        )
                    })
                    .collect(),
            )
        }
        _ => Value::Array(
            (0..rng.below(5))
                .map(|_| ordinary(rng, depth + 1))
                .collect(),
        ),
    }
}

#[test]
fn seeded_ordinary_records_are_always_accepted() {
    let mut rng = Rng::new(32);
    for case in 0..30_000 {
        let record = Value::Object(
            (0..rng.below(8))
                .map(|index| (format!("field{index}"), ordinary(&mut rng, 1)))
                .collect(),
        );
        let compact = serde_json::to_string(&record).unwrap();
        let line = if rng.chance(50) {
            compact.clone()
        } else {
            format!(" {}\t\r", serde_json::to_string_pretty(&record).unwrap())
        };
        assert!(
            check(line.as_bytes()).as_deref() == Some(&compact),
            "case {case} was not accepted: {line}"
        );
    }
}

#[test]
fn seeded_damage_to_valid_records_never_changes_the_answer() {
    let mut rng = Rng::new(33);
    let splinters: &[&[u8]] = &[
        b"\"", b"\\", b"{", b"}", b"[", b"]", b",", b":", b" ", b"0", b"-", b".", b"e", b"E", b"+",
        b"1", b"\\u", b"\\ud83d", b"\\/", b"\x01", b"\xff", b"\xc3", b"a", b"null", b"\"a\"",
    ];
    let (mut accepted, mut rejected) = (0, 0);
    for _ in 0..40_000 {
        let record = Value::Object(
            (0..1 + rng.below(5))
                .map(|index| (format!("f{index}"), ordinary(&mut rng, 2)))
                .collect(),
        );
        let mut line = serde_json::to_vec(&record).unwrap();
        for _ in 0..1 + rng.below(3) {
            let at = rng.below(line.len() + 1);
            match rng.below(4) {
                0 if at < line.len() => {
                    line.remove(at);
                }
                1 if at < line.len() => {
                    let copy = line[at];
                    line.insert(at, copy);
                }
                2 if at < line.len() => {
                    let splinter = rng.pick(splinters);
                    line.splice(at..at + 1, splinter.iter().copied());
                }
                _ => {
                    let splinter = rng.pick(splinters);
                    line.splice(at..at, splinter.iter().copied());
                }
            }
            if line.is_empty() {
                break;
            }
        }
        match check(&line) {
            Some(_) => accepted += 1,
            None => rejected += usize::from(serde(&line).is_none()),
        }
    }
    assert!(accepted > 8_000, "{accepted} accepted");
    assert!(rejected > 20_000, "{rejected} rejected");
}

#[test]
fn huge_lines_match_serde_json() {
    let mut rng = Rng::new(34);
    // One long string, clean and full of escapes, near the 4 MiB line limit.
    let clean = format!("{{\"message\":\"{}\"}}", "x".repeat(4 * 1024 * 1024 - 16));
    assert!(check(clean.as_bytes()).as_deref() == Some(&clean));
    let message = fuzz::text(&mut rng, 300_000);
    let escaped = serde_json::to_string(&serde_json::json!({ "message": message })).unwrap();
    assert!(escaped.len() > 1024 * 1024);
    assert!(check(escaped.as_bytes()).as_deref() == Some(&escaped));
    let unicode = format!(
        "{{\"m\":\"{}\"}}",
        "\\u00e9\\ud83d\\ude00\\/".repeat(100_000)
    );
    assert!(check(unicode.as_bytes()).is_some());
    // Very many keys, without and with one repeated at the far end.
    let fields: Vec<String> = (0..100_000).map(|n| format!("\"k{n}\": {n}")).collect();
    let unique = format!("{{ {} }}", fields.join(" , "));
    assert!(check(unique.as_bytes()).is_some());
    let repeated = format!("{{ {} , \"k0\": null }}", fields.join(" , "));
    assert_eq!(object(repeated.as_bytes()), None);
    assert!(
        serde(repeated.as_bytes())
            .unwrap()
            .starts_with("{\"k0\":null,\"k1\":1,")
    );
    // A number of a hundred thousand digits keeps every one of them.
    let digits = format!("{{\"n\":-{}.{}}}", "9".repeat(50_000), "1".repeat(50_000));
    assert!(check(digits.as_bytes()).as_deref() == Some(&digits));
}
