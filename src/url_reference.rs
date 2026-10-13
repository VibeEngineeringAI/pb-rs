//! Redirect references have different path semantics from API endpoint joins.
//! Preserve empty and percent-escaped segments while resolving literal dots.

struct Parts<'a> {
    scheme: Option<&'a str>,
    authority: Option<&'a str>,
    path: &'a str,
    query: Option<&'a str>,
    fragment: Option<&'a str>,
    opaque: bool,
}

impl<'a> Parts<'a> {
    fn parse(raw: &'a str) -> Self {
        let (head, fragment) = raw
            .split_once('#')
            .map_or((raw, None), |(a, b)| (a, Some(b)));
        let (head, query) = head
            .split_once('?')
            .map_or((head, None), |(a, b)| (a, Some(b)));
        let scheme_end = head.find(':').filter(|&end| {
            end > 0
                && head.as_bytes()[0].is_ascii_alphabetic()
                && head[..end]
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'+' | b'-' | b'.'))
        });
        let (scheme, rest) =
            scheme_end.map_or((None, head), |end| (Some(&head[..end]), &head[end + 1..]));
        let opaque = scheme.is_some() && !rest.starts_with('/');
        let (authority, path) =
            if rest.starts_with("//") && (scheme.is_some() || !rest.starts_with("///")) {
                let rest = &rest[2..];
                let end = rest.find('/').unwrap_or(rest.len());
                (Some(&rest[..end]), &rest[end..])
            } else {
                (None, rest)
            };
        Self {
            scheme,
            authority,
            path,
            query,
            fragment,
            opaque,
        }
    }
}

fn resolve_path(path: &str) -> String {
    if path.is_empty() {
        return String::new();
    }
    let mut segments = vec![""];
    let path = path.strip_prefix('/').unwrap_or(path);
    for segment in path.split('/') {
        match segment {
            "." => (),
            ".." => {
                if segments.len() > 1 {
                    segments.pop();
                }
            }
            _ => segments.push(segment),
        }
    }
    if matches!(path.rsplit('/').next(), Some("." | "..")) {
        segments.push("");
    }
    let result = segments.join("/");
    if result.is_empty() {
        "/".into()
    } else {
        result
    }
}

pub fn resolve(base: &str, reference: &str) -> Result<String, String> {
    let normalized = crate::profile::parse_profile_url(reference)?;
    let base = Parts::parse(base);
    let reference = Parts::parse(&normalized);
    let scheme = reference.scheme.or(base.scheme).unwrap_or("");
    let absolute = reference.scheme.is_some() || reference.authority.is_some_and(|a| !a.is_empty());
    let mut query = reference.query;
    let mut fragment = reference.fragment;
    let (authority, path) = if absolute {
        (
            reference.authority,
            if reference.opaque {
                reference.path.into()
            } else {
                resolve_path(reference.path)
            },
        )
    } else {
        if reference.path.is_empty() && reference.query.is_none() {
            query = base.query.filter(|q| !q.is_empty());
            if fragment.is_none_or(str::is_empty) {
                fragment = base.fragment;
            }
        }
        let merged = if reference.path.is_empty() {
            base.path.to_owned()
        } else if reference.path.starts_with('/') {
            reference.path.to_owned()
        } else {
            let end = base.path.rfind('/').map_or(0, |at| at + 1);
            format!("{}{}", &base.path[..end], reference.path)
        };
        (base.authority, resolve_path(&merged))
    };
    let mut result = format!("{scheme}:");
    if let Some(authority) = authority {
        result.push_str("//");
        result.push_str(authority);
    }
    result.push_str(&path);
    if let Some(query) = query {
        result.push('?');
        result.push_str(query);
    }
    if let Some(fragment) = fragment.filter(|s| !s.is_empty()) {
        result.push('#');
        result.push_str(fragment);
    }
    Ok(result)
}

/// Go accepts raw header octets in paths. Preserve invalid UTF-8 as escapes;
/// valid UTF-8 remains available for the URL parser's component-specific rules.
pub fn location(bytes: &[u8]) -> String {
    if let Ok(value) = std::str::from_utf8(bytes) {
        return value.into();
    }
    let mut result = String::new();
    for &byte in bytes {
        if byte.is_ascii() {
            result.push(char::from(byte));
        } else {
            use std::fmt::Write;
            write!(&mut result, "%{byte:02X}").expect("writing to a string");
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn redirect_paths_keep_empty_and_encoded_segments() {
        let base = "http://USER:pass@Example.test:80/base/old?q=old#anchor";
        for (reference, expected) in [
            ("/a//b", "http://USER:pass@Example.test:80/a//b"),
            ("/a/.", "http://USER:pass@Example.test:80/a/"),
            ("/a/..", "http://USER:pass@Example.test:80/"),
            ("../next", "http://USER:pass@Example.test:80/next"),
            ("//other.test/a/../b", "http://other.test/b"),
            ("https://other.test/a//b/../c", "https://other.test/a//c"),
            ("/a/%2e%2e/b", "http://USER:pass@Example.test:80/a/%2e%2e/b"),
            ("?new", "http://USER:pass@Example.test:80/base/old?new"),
            (
                "#new",
                "http://USER:pass@Example.test:80/base/old?q=old#new",
            ),
            ("/café", "http://USER:pass@Example.test:80/caf%C3%A9"),
            ("///a//b", "http://USER:pass@Example.test:80///a//b"),
        ] {
            assert_eq!(resolve(base, reference).unwrap(), expected, "{reference}");
        }
        assert_eq!(location(b"/caf\xc3\xa9"), "/café");
        assert_eq!(
            resolve(base, &location(b"/raw\xff")).unwrap(),
            "http://USER:pass@Example.test:80/raw%FF"
        );
    }
}
