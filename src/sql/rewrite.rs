//! SQL request preparation at the Go CLI's lexical compatibility boundary.
//!
//! These passes intentionally inspect ASCII bytes rather than parse SQL. In
//! particular, the quoting passes also rewrite comments, and the LIMIT pass
//! recognizes a token without checking for a following number.

/// Apply the command's table, field, and default-limit passes in that order.
pub fn prepare_query(query: &str) -> String {
    ensure_default_limit(&quote_fields_with_dots(&quote_stream_names(query)))
}

pub fn quote_stream_names(query: &str) -> String {
    let bytes = query.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'\'' | b'"' => {
                let end = quoted_end(bytes, i, true);
                result.extend_from_slice(&bytes[i..end]);
                i = end;
            }
            _ if keyword_at(bytes, i, b"from") || keyword_at(bytes, i, b"join") => {
                // Both supported table clauses are four bytes long.
                result.extend_from_slice(&bytes[i..i + 4]);
                i += 4;
                while i < bytes.len() && sql_space(bytes[i]) {
                    result.push(bytes[i]);
                    i += 1;
                }
                if i >= bytes.len() || matches!(bytes[i], b'"' | b'\'' | b'(') {
                    continue;
                }
                let start = i;
                while i < bytes.len() && !table_token_end(bytes[i]) {
                    i += 1;
                }
                let table = &bytes[start..i];
                if should_quote_table(table) {
                    result.push(b'"');
                    for &byte in table {
                        result.push(byte);
                        if byte == b'"' {
                            result.push(byte);
                        }
                    }
                    result.push(b'"');
                } else {
                    result.extend_from_slice(table);
                }
            }
            byte => {
                result.push(byte);
                i += 1;
            }
        }
    }
    // Every original byte survives in order; all insertions are ASCII.
    String::from_utf8(result).expect("SQL quoting preserves UTF-8")
}

pub fn quote_fields_with_dots(query: &str) -> String {
    let bytes = query.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'\'' | b'"' => {
                // The field pass only treats doubled single quotes as escapes.
                let end = quoted_end(bytes, i, bytes[i] == b'\'');
                result.extend_from_slice(&bytes[i..end]);
                i = end;
            }
            byte if ident_start(byte) => {
                let mut j = i + 1;
                while j < bytes.len() && ident_char(bytes[j]) {
                    j += 1;
                }
                let mut k = j;
                let mut has_dot = false;
                while k + 1 < bytes.len() && bytes[k] == b'.' && ident_char(bytes[k + 1]) {
                    has_dot = true;
                    k += 1;
                    while k < bytes.len() && ident_char(bytes[k]) {
                        k += 1;
                    }
                }
                let identifier = &bytes[i..k];
                if has_dot || should_quote_identifier(identifier, bytes, k) {
                    result.push(b'"');
                    result.extend_from_slice(identifier);
                    result.push(b'"');
                    i = k;
                } else {
                    result.extend_from_slice(&bytes[i..j]);
                    i = j;
                }
            }
            byte => {
                result.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8(result).expect("SQL quoting preserves UTF-8")
}

/// The request boundary applies this again after `prepare_query`, as Go does.
/// A bare trailing `--` is one deliberate exception to idempotence.
pub fn ensure_default_limit(query: &str) -> String {
    let mut trimmed = trim_sql_space(query);
    if trimmed.is_empty() || has_top_level_limit(trimmed.as_bytes()) {
        return query.to_owned();
    }
    let suffix = if let Some(without_semicolon) = trimmed.strip_suffix(';') {
        trimmed = trim_sql_space(without_semicolon);
        ";"
    } else {
        ""
    };
    let separator = if ends_in_line_comment(trimmed) {
        "\n"
    } else {
        " "
    };
    format!("{trimmed}{separator}LIMIT 500{suffix}")
}

fn ident_start(byte: u8) -> bool {
    byte.is_ascii_alphabetic() || byte == b'_'
}

fn ident_char(byte: u8) -> bool {
    ident_start(byte) || byte.is_ascii_digit()
}

fn sql_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r')
}

fn table_token_end(byte: u8) -> bool {
    sql_space(byte) || matches!(byte, b',' | b';' | b'(' | b')')
}

fn keyword_at(bytes: &[u8], i: usize, keyword: &[u8]) -> bool {
    let Some(end) = i.checked_add(keyword.len()) else {
        return false;
    };
    bytes
        .get(i..end)
        .is_some_and(|token| token.eq_ignore_ascii_case(keyword))
        && (i == 0 || !ident_char(bytes[i - 1]))
        && (end >= bytes.len() || !ident_char(bytes[end]))
}

fn should_quote_table(table: &[u8]) -> bool {
    // Go's strings.EqualFold recognizes U+017F (long s) as an ASCII S.
    // This is the only non-ASCII simple-fold spelling of the DATASET alias.
    let dataset_alias = table.eq_ignore_ascii_case(b"dataset")
        || (table.len() == 8
            && table[..4].eq_ignore_ascii_case(b"data")
            && table[4..6] == [0xc5, 0xbf]
            && table[6..].eq_ignore_ascii_case(b"et"));
    if table.is_empty() || dataset_alias {
        return false;
    }
    !ident_start(table[0]) || table[1..].iter().any(|&byte| !ident_char(byte))
}

fn should_quote_identifier(identifier: &[u8], query: &[u8], end: usize) -> bool {
    const KEYWORDS: &[&[u8]] = &[
        b"all",
        b"and",
        b"as",
        b"asc",
        b"between",
        b"by",
        b"case",
        b"cast",
        b"desc",
        b"distinct",
        b"else",
        b"end",
        b"false",
        b"from",
        b"full",
        b"group",
        b"having",
        b"in",
        b"inner",
        b"is",
        b"join",
        b"left",
        b"like",
        b"limit",
        b"not",
        b"null",
        b"on",
        b"or",
        b"order",
        b"outer",
        b"right",
        b"select",
        b"then",
        b"true",
        b"when",
        b"where",
    ];
    if KEYWORDS
        .iter()
        .any(|keyword| identifier.eq_ignore_ascii_case(keyword))
    {
        return false;
    }
    if query[end..].iter().copied().find(|&byte| !sql_space(byte)) == Some(b'(') {
        return false;
    }
    identifier.iter().any(u8::is_ascii_uppercase) && identifier.iter().any(u8::is_ascii_lowercase)
}

fn quoted_end(bytes: &[u8], start: usize, doubled_escape: bool) -> usize {
    let quote = bytes[start];
    let mut i = start + 1;
    while i < bytes.len() {
        let byte = bytes[i];
        i += 1;
        if byte == quote {
            if doubled_escape && i < bytes.len() && bytes[i] == quote {
                i += 1;
            } else {
                break;
            }
        }
    }
    i
}

fn has_top_level_limit(bytes: &[u8]) -> bool {
    let mut depth = 0usize;
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'\'' | b'"' => i = quoted_end(bytes, i, bytes[i] == b'\''),
            b'-' if bytes.get(i + 1) == Some(&b'-') => {
                i += 2;
                while i < bytes.len() && bytes[i] != b'\n' {
                    i += 1;
                }
            }
            b'/' if bytes.get(i + 1) == Some(&b'*') => {
                i += 2;
                while i + 1 < bytes.len() {
                    if bytes[i] == b'*' && bytes[i + 1] == b'/' {
                        i += 2;
                        break;
                    }
                    i += 1;
                }
            }
            b'(' => {
                depth += 1;
                i += 1;
            }
            b')' => {
                depth = depth.saturating_sub(1);
                i += 1;
            }
            _ => {
                if depth == 0 && keyword_at(bytes, i, b"limit") {
                    return true;
                }
                i += 1;
            }
        }
    }
    false
}

fn ends_in_line_comment(query: &str) -> bool {
    let bytes = query.as_bytes();
    let mut in_single = false;
    let mut in_double = false;
    let mut in_block = false;
    let mut line_start = None;
    let mut i = 0;
    while i < bytes.len() {
        if in_single || in_double {
            let quote = if in_single { b'\'' } else { b'"' };
            if bytes[i] == quote {
                if bytes.get(i + 1) == Some(&quote) {
                    i += 2;
                    continue;
                }
                in_single = false;
                in_double = false;
            }
        } else if in_block {
            if bytes[i] == b'*' && bytes.get(i + 1) == Some(&b'/') {
                in_block = false;
                i += 1;
            }
        } else {
            match bytes[i] {
                b'\'' => in_single = true,
                b'"' => in_double = true,
                b'-' if bytes.get(i + 1) == Some(&b'-') => {
                    line_start = Some(i);
                    i += 1;
                }
                b'/' if bytes.get(i + 1) == Some(&b'*') => {
                    in_block = true;
                    i += 1;
                }
                b'\n' | b'\r' => line_start = None,
                _ => {}
            }
        }
        i += 1;
    }
    line_start.is_some_and(|start| !trim_sql_space(&query[start + 2..]).is_empty())
}

pub(crate) fn trim_sql_space(query: &str) -> &str {
    // Go unicode.IsSpace / strings.TrimSpace. Keep the set explicit rather than
    // tying request bytes to the Rust toolchain's Unicode property tables.
    query.trim_matches(|ch| {
        matches!(ch,
        '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{0085}' | '\u{00a0}' |
        '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' |
        '\u{202f}' | '\u{205f}' | '\u{3000}')
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn go_original_quoting_vectors() {
        for (query, expected) in [
            (
                "SELECT * FROM astronomy-shop-logs WHERE StatusCode = 'FailedPrecondition' AND service.name = 'cart' LIMIT 500",
                "SELECT * FROM \"astronomy-shop-logs\" WHERE \"StatusCode\" = 'FailedPrecondition' AND \"service.name\" = 'cart' LIMIT 500",
            ),
            (
                "SELECT * FROM fly_logs LIMIT 100",
                "SELECT * FROM fly_logs LIMIT 100",
            ),
            (
                "SELECT COUNT(StatusCode) FROM astronomy-shop-logs WHERE StatusCode IS NOT NULL GROUP BY service.name LIMIT 10",
                "SELECT COUNT(\"StatusCode\") FROM \"astronomy-shop-logs\" WHERE \"StatusCode\" IS NOT NULL GROUP BY \"service.name\" LIMIT 10",
            ),
            (
                "SELECT * FROM \"astronomy-shop-logs\" WHERE \"StatusCode\" = 'FailedPrecondition' AND \"service.name\" = 'cart'",
                "SELECT * FROM \"astronomy-shop-logs\" WHERE \"StatusCode\" = 'FailedPrecondition' AND \"service.name\" = 'cart'",
            ),
        ] {
            assert_eq!(
                quote_fields_with_dots(&quote_stream_names(query)),
                expected,
                "{query:?}"
            );
        }
        for (query, expected) in [
            (
                "SELECT * FROM fly_logs JOIN claudecode-logs ON fly_logs.id = claudecode-logs.id",
                "SELECT * FROM fly_logs JOIN \"claudecode-logs\" ON fly_logs.id = claudecode-logs.id",
            ),
            (
                "SELECT * FROM metrics.v1 LIMIT 100",
                "SELECT * FROM \"metrics.v1\" LIMIT 100",
            ),
            (
                "SELECT * FROM 123logs LIMIT 100",
                "SELECT * FROM \"123logs\" LIMIT 100",
            ),
        ] {
            assert_eq!(quote_stream_names(query), expected, "{query:?}");
        }
    }

    #[test]
    fn go_original_limit_vectors() {
        for (query, expected) in [
            (
                "SELECT * FROM \"astronomy-shop-logs\"",
                "SELECT * FROM \"astronomy-shop-logs\" LIMIT 500",
            ),
            (
                "SELECT * FROM \"astronomy-shop-logs\" LIMIT 10",
                "SELECT * FROM \"astronomy-shop-logs\" LIMIT 10",
            ),
            (
                "SELECT * FROM (SELECT * FROM \"astronomy-shop-logs\" LIMIT 10) logs",
                "SELECT * FROM (SELECT * FROM \"astronomy-shop-logs\" LIMIT 10) logs LIMIT 500",
            ),
            (
                "SELECT * FROM \"astronomy-shop-logs\";",
                "SELECT * FROM \"astronomy-shop-logs\" LIMIT 500;",
            ),
            (
                "SELECT * FROM logs -- limit later",
                "SELECT * FROM logs -- limit later\nLIMIT 500",
            ),
            (
                "SELECT * FROM logs -- limit later\n",
                "SELECT * FROM logs -- limit later\nLIMIT 500",
            ),
            (
                "SELECT * FROM logs /* limit later */",
                "SELECT * FROM logs /* limit later */ LIMIT 500",
            ),
            (
                "SELECT * FROM logs WHERE msg = 'limit 1'",
                "SELECT * FROM logs WHERE msg = 'limit 1' LIMIT 500",
            ),
            (
                "SELECT * FROM logs WHERE \"limit\" = 'true'",
                "SELECT * FROM logs WHERE \"limit\" = 'true' LIMIT 500",
            ),
            (
                "SELECT * FROM logs LIMIT 10 -- keep",
                "SELECT * FROM logs LIMIT 10 -- keep",
            ),
            (
                "SELECT * FROM logs /* comment */ LIMIT 10",
                "SELECT * FROM logs /* comment */ LIMIT 10",
            ),
        ] {
            assert_eq!(ensure_default_limit(query), expected, "{query:?}");
        }
    }

    #[test]
    fn table_quoting_preserves_lexical_quirks() {
        for (query, expected) in [
            ("from a\"b", "from \"a\"\"b\""),
            ("from DaTaSeT join DATASET", "from DaTaSeT join DATASET"),
            ("from Logs,other-logs", "from Logs,other-logs"),
            ("from(a-b) join 'c-d'", "from(a-b) join 'c-d'"),
            ("from\u{000b}logs", "from\"\u{000b}logs\""),
            ("from\u{00a0}logs", "from\"\u{00a0}logs\""),
            ("from 中文 join café", "from \"中文\" join \"café\""),
            ("from foo/bar", "from \"foo/bar\""),
            (
                "-- from x-y\n/* join a.b */",
                "-- from \"x-y\"\n/* join \"a.b\" */",
            ),
            ("éfrom a-b FROMé a-b", "éfrom \"a-b\" FROM\"é\" a-b"),
            (
                "xfrom a-b _join c-d from1 e-f",
                "xfrom a-b _join c-d from1 e-f",
            ),
            (
                "'from a-b''join c-d' \"from e-f\"\"join g-h\"",
                "'from a-b''join c-d' \"from e-f\"\"join g-h\"",
            ),
            ("from 'unterminated join a-b", "from 'unterminated join a-b"),
        ] {
            assert_eq!(quote_stream_names(query), expected, "{query:?}");
        }
    }

    #[test]
    fn field_quoting_preserves_ascii_boundaries_and_comments() {
        for (query, expected) in [
            (
                "a.b.c a.1 _x.2 a..b a.",
                "\"a.b.c\" \"a.1\" \"_x.2\" a..b a.",
            ),
            (
                "StatusCode STATUS status _Status 1Status",
                "\"StatusCode\" STATUS status \"_Status\" 1\"Status\"",
            ),
            (
                "SeLeCt CoUnT \t\n(x) Count\u{000b}(x)",
                "SeLeCt CoUnT \t\n(x) \"Count\"\u{000b}(x)",
            ),
            (
                "Count /* comment */(x) a.b(x)",
                "\"Count\" /* comment */(x) \"a.b\"(x)",
            ),
            (
                "-- StatusCode\n/* service.name */",
                "-- \"StatusCode\"\n/* \"service.name\" */",
            ),
            (
                "中文StatusCode éFooBar café.name",
                "中文\"StatusCode\" é\"FooBar\" café.name",
            ),
            (
                "'StatusCode''service.name' \"a\"\"StatusCode\"",
                "'StatusCode''service.name' \"a\"\"StatusCode\"",
            ),
            ("'a\\' StatusCode", "'a\\' \"StatusCode\""),
            ("\"unterminated StatusCode", "\"unterminated StatusCode"),
        ] {
            assert_eq!(quote_fields_with_dots(query), expected, "{query:?}");
        }
    }

    #[test]
    fn limit_token_and_malformed_sql_quirks() {
        for (query, expected) in [
            ("", ""),
            (" \t\u{0085}\u{3000}", " \t\u{0085}\u{3000}"),
            ("\u{0085} SELECT 1 \u{3000}", "SELECT 1 LIMIT 500"),
            (
                "\u{200b}SELECT 1\u{200b}",
                "\u{200b}SELECT 1\u{200b} LIMIT 500",
            ),
            (" \tSELECT 1 LiMiT 99999; \n", " \tSELECT 1 LiMiT 99999; \n"),
            ("SELECT limit", "SELECT limit"),
            ("SELECT a.limit", "SELECT a.limit"),
            ("SELECT élimit中文", "SELECT élimit中文"),
            (
                "SELECT unlimited, limit1, _limit",
                "SELECT unlimited, limit1, _limit LIMIT 500",
            ),
            ("SELECT (limit) )", "SELECT (limit) ) LIMIT 500"),
            ("SELECT ) limit", "SELECT ) limit"),
            ("SELECT ((1) limit", "SELECT ((1) limit LIMIT 500"),
            ("SELECT 'x''limit'", "SELECT 'x''limit' LIMIT 500"),
            ("SELECT \"x\"\"limit\"", "SELECT \"x\"\"limit\" LIMIT 500"),
            ("SELECT 'limit", "SELECT 'limit LIMIT 500"),
            ("SELECT 1 /* limit", "SELECT 1 /* limit LIMIT 500"),
            (
                "SELECT 1 /* outer /* */ limit",
                "SELECT 1 /* outer /* */ limit",
            ),
            (
                "SELECT 1 -- limit\rSELECT 2",
                "SELECT 1 -- limit\rSELECT 2 LIMIT 500",
            ),
            ("SELECT 1;;", "SELECT 1; LIMIT 500;"),
            (";", " LIMIT 500;"),
            ("SELECT 1 -- note;", "SELECT 1 -- note\nLIMIT 500;"),
            ("SELECT 1 --", "SELECT 1 -- LIMIT 500"),
            (
                "SELECT 1 -- /* x\nSELECT 2",
                "SELECT 1 -- /* x\nSELECT 2\nLIMIT 500",
            ),
            (
                "SELECT 1 -- 'x\nSELECT 2",
                "SELECT 1 -- 'x\nSELECT 2\nLIMIT 500",
            ),
        ] {
            assert_eq!(ensure_default_limit(query), expected, "{query:?}");
        }
    }

    #[test]
    fn request_pass_order_and_second_limit_application() {
        assert_eq!(
            prepare_query("SELECT a.limit FROM metrics.v1"),
            "SELECT \"a.limit\" FROM \"metrics.v1\" LIMIT 500"
        );
        assert_eq!(
            prepare_query("SELECT * FROM MixedLogs"),
            "SELECT * FROM \"MixedLogs\" LIMIT 500"
        );
        let first = prepare_query("SELECT 1 --");
        assert_eq!(first, "SELECT 1 -- LIMIT 500");
        assert_eq!(
            ensure_default_limit(&first),
            "SELECT 1 -- LIMIT 500\nLIMIT 500"
        );
        for query in [
            "SELECT * FROM logs",
            "SELECT 1;",
            "SELECT 1 -- note",
            "SELECT 1 LIMIT 2000",
        ] {
            let first = prepare_query(query);
            assert_eq!(ensure_default_limit(&first), first);
        }
    }

    #[test]
    fn dataset_alias_uses_go_simple_case_folding() {
        for alias in ["dataset", "DATASET", "dataſet", "DATAſET"] {
            let query = format!("SELECT * FROM {alias}");
            assert_eq!(quote_stream_names(&query), query);
        }
        assert_eq!(
            prepare_query("SELECT * FROM dataſet"),
            "SELECT * FROM dataſet LIMIT 500"
        );
        for table in ["dataſets", "dataKet", "dátaſet"] {
            let query = format!("SELECT * FROM {table}");
            assert_eq!(
                quote_stream_names(&query),
                format!("SELECT * FROM \"{table}\"")
            );
        }
    }

    #[test]
    fn short_unicode_inputs_never_require_char_boundary_indexes() {
        for query in [
            "é", "中", "💫", "élimit", "from💫", "join中", "a.é", "éA.b", "💫--é", "💫/*é", "\"💫",
            "'é",
        ] {
            let _ = prepare_query(query);
        }
        for index in 0..=8 {
            assert!(!keyword_at("é".as_bytes(), index, b"limit"));
        }
        assert!(!keyword_at(b"", usize::MAX, b"limit"));
    }
}
