#!/usr/bin/env python3
"""Phase-three Go/Rust differential gate for noninteractive `pb sql run`.

Every case runs real executables in fresh HOME/XDG trees against a loopback,
byte-scripted HTTP/1.1 fixture (`phase3.py serve`). Each case pins what the
pinned Go reference must do (termination, ordered requests and exact request
bodies, key output bytes and config effects) before candidate parity counts.
Expected request bodies and most success outputs come from a small, separately
tested Python model of `cmd/query.go`; the Go capture must agree with it.
See contracts/phase3.md for scope, tiers and exclusions.
"""

import argparse
import base64
import collections
import copy
import decimal
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import phase1  # noqa: E402
import phase2  # noqa: E402
import run  # noqa: E402

SCHEMA_VERSION = 1
MANIFEST = HERE / "contracts" / "phase3-reference.json"
DEFAULT_METER = HERE.parent / "bin" / "pb-meter"
NO_SERVER = phase2.NO_SERVER
STUB_MARKER = phase2.STUB_MARKER
ORCHESTRATOR_TOKEN = phase2.ORCHESTRATOR_TOKEN
START, END = "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z"
STDOUT_MEMORY = 16 << 20          # normalized in memory below this; larger output compares raw digests
DEFAULT_STDOUT_CAP = 256 << 20    # spooled to disk with an incremental digest
STDERR_CAP = 4 << 20
DEFAULT_TIMEOUT = 10.0
FULL60_TIMEOUT = 80.0             # Go's 60 s client deadline plus startup/cleanup margin
PREVIEW_LIMIT = 64 * 1024
BLOCKED_BYTES_MARGIN = 16 << 20   # declared before any candidate measurement (contracts/phase3.md)
# Request headers that gate. User-Agent and header order are recorded as advisories only.
CONTRACT_HEADERS = ("authorization", "x-api-key", "x-p-tenant", "cookie", "content-type", "accept",
                    "accept-encoding", "content-length", "transfer-encoding")
TIERS = {
    "gate": "phase-three gate",
    "full60s": "real 60-second client deadline; opt-in with --include-full60s (per-case timeout 80 s)",
    "known-divergence": "documented unresolved Go/Rust diagnostics (see KNOWN_DIVERGENCES); opt-in, never counted as passes",
}
# Accepted residuals (docs/rust-phase3.md, final review): Go expectations stay pinned and the
# cases stay runnable with --include-known-divergence, but they are outside the default gate.
EMPTY_REASON = ("HTTP/1.1 status line with an empty reason phrase: Go keeps the raw delimiter ('200 '), "
                "hyper does not expose it ('200')")
KNOWN_DIVERGENCES = {
    "p3-argv-invalid-utf8-sql": "non-UTF-8 argv rejected at the Rust String parser boundary (phase-one limitation)",
    "p3-text-status-empty-reason-empty": EMPTY_REASON, "p3-text-status-empty-reason-no-rows": EMPTY_REASON,
    "p3-json-status-empty-reason-empty": EMPTY_REASON, "p3-status-empty-reason-text": EMPTY_REASON,
    "p3-status-empty-reason-json": EMPTY_REASON, "p3-status-empty-reason-empty-body": EMPTY_REASON,
    "p3-status-299-text": EMPTY_REASON + " (299 has no standard reason)",
    "p3-status-299-json": EMPTY_REASON + " (299 has no standard reason)",
    "p3-text-reset-mid-body": "OS connection-reset wording: Go 'read tcp A->B: read: connection reset by peer', "
                              "Rust 'Connection reset by peer (os error 104)'; code/retryability match",
    "p3-json-reset-mid-body": "OS connection-reset wording (see p3-text-reset-mid-body); partial stdout and code match",
    "p3-text-close-before-headers": "premature EOF before response headers: Go distinguishes diagnostics hyper cannot",
    "p3-text-malformed-status-line": "malformed status code: Go echoes the raw token ('abc'), hyper reports only "
                                     "'invalid HTTP status-code parsed'",
}
AUTH = phase2.AUTH
HEADERS = phase2.HEADERS
SPINNER_FRAME = re.compile(rb"\r([|/\\-]) fetching\.\.\.")
SPINNER_RUN = re.compile(rb"(?:\r[|/\\-] fetching\.\.\.)*\r\x1b\[K")
SPINNER_TAIL = re.compile(rb"(?:\r[|/\\-] fetching\.\.\.)+$")
CLIENT_PORT = re.compile(rb"127\.0\.0\.1:\d+->")
# Go's net/http reports the same 60 s Client.Timeout as either cause, depending on a
# race between the deadline timer and the pending read (observed across Go runs).
TIMEOUT_CAUSE = re.compile(rb"(?:context deadline exceeded|net/http: request canceled) "
                           rb"\((Client\.Timeout exceeded while awaiting headers|Client\.Timeout or context cancellation while reading body)\)")


def mono_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def B(data):
    """JSON-safe bytes literal for the corpus."""
    return {"b64": base64.b64encode(data).decode()}


def as_bytes(value):
    if isinstance(value, dict):
        return base64.b64decode(value["b64"])
    if isinstance(value, bytes):
        return value
    return value.encode()


# --------------------------------------------------------------------------
# Go semantics model (cmd/query.go at 07af390, encoding/json and unicode of Go 1.25.4).

GO_SPACE = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B),
            0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
SQL_SPACE = b" \t\n\r"
HEX = b"0123456789abcdef"
SQL_KEYWORDS = {"all", "and", "as", "asc", "between", "by", "case", "cast", "desc", "distinct", "else", "end",
                "false", "from", "full", "group", "having", "in", "inner", "is", "join", "left", "like", "limit",
                "not", "null", "on", "or", "order", "outer", "right", "select", "then", "true", "when", "where"}


def decode_rune(data, i=0, end=None):
    """utf8.DecodeRune on data[i:end]: (rune, size); invalid input is (0xFFFD, 1)."""
    end = len(data) if end is None else end
    n = end - i
    if n <= 0:
        return 0xFFFD, 0
    b0 = data[i]
    if b0 < 0x80:
        return b0, 1

    def cont(k, lo=0x80, hi=0xBF):
        return n > k and lo <= data[i + k] <= hi

    if 0xC2 <= b0 <= 0xDF:
        return (((b0 & 0x1F) << 6) | (data[i + 1] & 0x3F), 2) if cont(1) else (0xFFFD, 1)
    if 0xE0 <= b0 <= 0xEF:
        lo, hi = (0xA0 if b0 == 0xE0 else 0x80), (0x9F if b0 == 0xED else 0xBF)
        if cont(1, lo, hi) and cont(2):
            return ((b0 & 0x0F) << 12) | ((data[i + 1] & 0x3F) << 6) | (data[i + 2] & 0x3F), 3
        return 0xFFFD, 1
    if 0xF0 <= b0 <= 0xF4:
        lo, hi = (0x90 if b0 == 0xF0 else 0x80), (0x8F if b0 == 0xF4 else 0xBF)
        if cont(1, lo, hi) and cont(2) and cont(3):
            return (((b0 & 0x07) << 18) | ((data[i + 1] & 0x3F) << 12) |
                    ((data[i + 2] & 0x3F) << 6) | (data[i + 3] & 0x3F)), 4
    return 0xFFFD, 1


def decode_last_rune(data, end):
    if end <= 0:
        return 0xFFFD, 0
    start = end - 1
    if data[start] < 0x80:
        return data[start], 1
    lim = max(end - 4, 0)
    start -= 1
    while start >= lim and (data[start] & 0xC0) == 0x80:
        start -= 1
    start = max(start, 0)
    rune, size = decode_rune(data, start, end)
    return (rune, size) if start + size == end else (0xFFFD, 1)


def go_trim_space(data):
    """bytes.TrimSpace / strings.TrimSpace: unicode.IsSpace, invalid UTF-8 stops trimming."""
    start, end = 0, len(data)
    while start < end:
        rune, size = decode_rune(data, start, end)
        if rune not in GO_SPACE or (rune == 0xFFFD and size == 1):
            break
        start += size
    while end > start:
        rune, size = decode_last_rune(data, end)
        if rune not in GO_SPACE or (rune == 0xFFFD and size == 1):
            break
        end -= size
    return data[start:end]


def equal_fold_ascii(data, word):
    """strings.EqualFold against an ASCII word (only U+017F and U+212A fold onto ASCII letters)."""
    runes, i = [], 0
    while i < len(data):
        rune, size = decode_rune(data, i)
        runes.append(rune)
        i += size
    if len(runes) != len(word):
        return False
    folds = {0x17F: ord("s"), 0x212A: ord("k")}
    for rune, expected in zip(runes, word.encode()):
        if rune < 0x80:
            rune = ord(chr(rune).lower())
        else:
            rune = folds.get(rune, -1)
        if rune != expected:
            return False
    return True


def ident_start(c):
    return 0x61 <= c <= 0x7A or 0x41 <= c <= 0x5A or c == 0x5F


def ident_char(c):
    return ident_start(c) or 0x30 <= c <= 0x39


def _copy_quoted(out, query, start, quote):
    out.append(query[start])
    i = start + 1
    while i < len(query):
        c = query[i]
        out.append(c)
        i += 1
        if c == quote:
            if i < len(query) and query[i] == quote:
                out.append(query[i])
                i += 1
                continue
            break
    return i


def _keyword_at(query, idx, word):
    if idx + len(word) > len(query) or not equal_fold_ascii(query[idx:idx + len(word)], word):
        return False
    if idx > 0 and ident_char(query[idx - 1]):
        return False
    nxt = idx + len(word)
    return nxt >= len(query) or not ident_char(query[nxt])


def _should_quote_table(name):
    if not name or equal_fold_ascii(name, "dataset"):
        return False
    if not ident_start(name[0]):
        return True
    return any(not ident_char(c) for c in name[1:])


def quote_stream_names(query):
    out, i, n = bytearray(), 0, len(query)
    while i < n:
        c = query[i]
        if c in (0x27, 0x22):
            i = _copy_quoted(out, query, i, c)
        elif _keyword_at(query, i, "from") or _keyword_at(query, i, "join"):
            out += query[i:i + 4]
            i += 4
            while i < n and query[i] in SQL_SPACE:
                out.append(query[i])
                i += 1
            if i >= n or query[i] in b"\"'(":
                continue
            start = i
            while i < n and not (query[i] in SQL_SPACE or query[i] in b",;()"):
                i += 1
            name = query[start:i]
            out += (b'"' + name.replace(b'"', b'""') + b'"') if _should_quote_table(name) else name
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _next_non_space(query, start):
    while start < len(query):
        if query[start] not in SQL_SPACE:
            return query[start]
        start += 1
    return 0


def _has_mixed_case(data):
    return any(0x41 <= c <= 0x5A for c in data) and any(0x61 <= c <= 0x7A for c in data)


def quote_fields_with_dots(query):
    out, i, n = bytearray(), 0, len(query)
    while i < n:
        ch = query[i]
        if ch in (0x27, 0x22):
            out.append(ch)
            i += 1
            while i < n:
                c = query[i]
                out.append(c)
                i += 1
                if c == ch:
                    if ch == 0x27 and i < n and query[i] == 0x27:
                        out.append(query[i])
                        i += 1
                    else:
                        break
        elif ident_start(ch):
            j = i + 1
            while j < n and ident_char(query[j]):
                j += 1
            k, dotted = j, False
            while k < n and query[k] == 0x2E and k + 1 < n and ident_char(query[k + 1]):
                dotted = True
                k += 1
                while k < n and ident_char(query[k]):
                    k += 1
            identifier = query[i:k]
            quote = dotted or (identifier.decode().lower() not in SQL_KEYWORDS and
                               _next_non_space(query, k) != 0x28 and _has_mixed_case(identifier))
            if quote:
                out += b'"' + identifier + b'"'
                i = k
            else:
                out += query[i:j]
                i = j
        else:
            out.append(ch)
            i += 1
    return bytes(out)


def has_top_level_limit(query):
    depth, i, n = 0, 0, len(query)
    while i < n:
        c = query[i]
        if c == 0x27:
            i += 1
            while i < n:
                if query[i] == 0x27:
                    i += 1
                    if i < n and query[i] == 0x27:
                        i += 1
                        continue
                    break
                i += 1
        elif c == 0x22:
            i += 1
            while i < n:
                if query[i] == 0x22:
                    i += 1
                    break
                i += 1
        elif c == 0x2D:
            if i + 1 < n and query[i + 1] == 0x2D:
                i += 2
                while i < n and query[i] != 0x0A:
                    i += 1
                continue
            i += 1
        elif c == 0x2F:
            if i + 1 < n and query[i + 1] == 0x2A:
                i += 2
                while i + 1 < n:
                    if query[i] == 0x2A and query[i + 1] == 0x2F:
                        i += 2
                        break
                    i += 1
                continue
            i += 1
        elif c == 0x28:
            depth += 1
            i += 1
        elif c == 0x29:
            depth = max(depth - 1, 0)
            i += 1
        else:
            if depth == 0 and _keyword_at(query, i, "limit"):
                return True
            i += 1
    return False


def ends_in_line_comment(query):
    single = double = block = False
    comment, i, n = -1, 0, len(query)
    while i < n:
        c = query[i]
        if single or double:
            quote = 0x27 if single else 0x22
            if c == quote:
                if i + 1 < n and query[i + 1] == quote:
                    i += 2
                    continue
                single = double = False
        elif block:
            if c == 0x2A and i + 1 < n and query[i + 1] == 0x2F:
                block = False
                i += 1
        elif c == 0x27:
            single = True
        elif c == 0x22:
            double = True
        elif c == 0x2D and i + 1 < n and query[i + 1] == 0x2D:
            comment = i
            i += 1
        elif c == 0x2F and i + 1 < n and query[i + 1] == 0x2A:
            block = True
            i += 1
        elif c in (0x0A, 0x0D):
            comment = -1
        i += 1
    return comment >= 0 and go_trim_space(query[comment + 2:]) != b""


def ensure_default_limit(query):
    trimmed = go_trim_space(query)
    if not trimmed or has_top_level_limit(trimmed):
        return query
    suffix = b""
    if trimmed.endswith(b";"):
        trimmed, suffix = go_trim_space(trimmed[:-1]), b";"
    return trimmed + (b"\n" if ends_in_line_comment(trimmed) else b" ") + b"LIMIT 500" + suffix


def rewrite_sql(query):
    """RunE quoting and LIMIT insertion, then fetchData's second ensureDefaultLimit."""
    query = as_bytes(query)
    return ensure_default_limit(ensure_default_limit(quote_fields_with_dots(quote_stream_names(query))))


_ESCAPE_HTML = re.compile("[\x00-\x1f\"\\\\<>&\u2028\u2029]")
_ESCAPE_PLAIN = re.compile("[\x00-\x1f\"\\\\\u2028\u2029]")
_NAMED_ESCAPES = {"\"": "\\\"", "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape(match):
    c = match.group(0)
    return _NAMED_ESCAPES.get(c) or "\\u%04x" % ord(c)


def go_json_string(data, escape_html=True):
    data = as_bytes(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        pattern = _ESCAPE_HTML if escape_html else _ESCAPE_PLAIN
        return ('"' + pattern.sub(_escape, text) + '"').encode("utf-8")
    out, i = bytearray(b'"'), 0
    named = {0x22: b'\\"', 0x5C: b"\\\\", 0x08: b"\\b", 0x0C: b"\\f", 0x0A: b"\\n", 0x0D: b"\\r", 0x09: b"\\t"}
    while i < len(data):
        b = data[i]
        if b < 0x80:
            if b >= 0x20 and b not in (0x22, 0x5C) and not (escape_html and b in b"<>&"):
                out.append(b)
            else:
                out += named.get(b) or b"\\u00" + bytes([HEX[b >> 4], HEX[b & 15]])
            i += 1
            continue
        rune, size = decode_rune(data, i)
        if rune == 0xFFFD and size == 1:
            out += b"\\ufffd"
        elif rune in (0x2028, 0x2029):
            out += b"\\u202" + bytes([HEX[rune & 15]])
        else:
            out += data[i:i + size]
        i += size
    return bytes(out + b'"')


def request_body(sql, start=START, end=END):
    """json.Marshal of fetchData's anonymous struct: field order query, startTime, endTime."""
    return (b'{"query":' + go_json_string(rewrite_sql(sql)) + b',"startTime":' + go_json_string(start) +
            b',"endTime":' + go_json_string(end) + b"}")


def go_float(value):
    """encoding/json float64 formatting (strconv shortest, 'e' outside [1e-6, 1e21))."""
    if math.isinf(value) or math.isnan(value):
        raise ValueError("unsupported float")
    if value == 0:
        return "-0" if math.copysign(1, value) < 0 else "0"
    sign = "-" if value < 0 else ""
    parsed = decimal.Decimal(repr(abs(value))).normalize()
    _, digit_tuple, exponent = parsed.as_tuple()
    digits = "".join(map(str, digit_tuple))
    point = len(digits) + exponent
    if abs(value) < 1e-6 or abs(value) >= 1e21:
        mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        power = point - 1
        text = f"{mantissa}e{'-' if power < 0 else '+'}{abs(power):02d}"
        if text[-4:-1] == "e-0":
            text = text[:-2] + text[-1]
        return sign + text
    if point <= 0:
        return sign + "0." + "0" * -point + digits
    if point >= len(digits):
        return sign + digits + "0" * (point - len(digits))
    return sign + digits[:point] + "." + digits[point:]


def go_decode_utf8(data):
    """Replace invalid UTF-8 per byte, as encoding/json does inside strings."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    out, i = [], 0
    while i < len(data):
        rune, size = decode_rune(data, i)
        out.append(chr(rune))
        i += size
    return "".join(out)


_SURROGATE = re.compile("[\ud800-\udfff]")


def _fix_surrogates(value):
    if isinstance(value, str):
        return _SURROGATE.sub("\ufffd", value)
    if isinstance(value, list):
        return [_fix_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {_fix_surrogates(k): _fix_surrogates(v) for k, v in value.items()}
    return value


def go_decode_array(body):
    """Model of decoding a valid outer array into []interface{}; trailing content is ignored."""
    def reject(name):
        raise ValueError("non-JSON constant " + name)

    def number(text):
        result = float(text)
        if math.isinf(result):
            raise ValueError("number out of float64 range: " + text)
        return result

    def pairs(items):
        return dict(items)

    text = go_decode_utf8(as_bytes(body))
    decoder = json.JSONDecoder(parse_float=number, parse_int=number, parse_constant=reject, object_pairs_hook=pairs)
    index = len(text) - len(text.lstrip(" \t\r\n"))
    value, _ = decoder.raw_decode(text, index)
    if not isinstance(value, list):
        raise ValueError("expected JSON array response")
    return _fix_surrogates(value) if re.search(r"\\u[dD][89a-fA-F]", text) else value


def go_encode(value, depth=0):
    """json.MarshalIndent(value, "", "  ") for decoded interface{} values."""
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, float):
        return go_float(value).encode()
    if isinstance(value, str):
        return go_json_string(value.encode("utf-8"))
    inner, outer = b"  " * (depth + 1), b"  " * depth
    if isinstance(value, list):
        if not value:
            return b"[]"
        return b"[\n" + b",\n".join(inner + go_encode(v, depth + 1) for v in value) + b"\n" + outer + b"]"
    if isinstance(value, dict):
        if not value:
            return b"{}"
        keys = sorted(value, key=lambda k: k.encode("utf-8"))
        return (b"{\n" + b",\n".join(inner + go_json_string(k.encode("utf-8")) + b": " + go_encode(value[k], depth + 1)
                                     for k in keys) + b"\n" + outer + b"}")
    raise TypeError(type(value))


def pretty_array(elements):
    out = bytearray(b"[")
    for index, element in enumerate(elements):
        out += b"\n" if index == 0 else b",\n"
        out += b"  " + go_encode(element).replace(b"\n", b"\n  ")
    out += b"]\n" if not elements else b"\n]\n"
    return bytes(out)


def _body_empty(body):
    return not body.strip(SQL_SPACE)


def json_output(body, status="200 OK"):
    body = as_bytes(body)
    if _body_empty(body):
        return f"No response body returned (status: {status}).\n".encode()
    return pretty_array(go_decode_array(body))


def text_output(body, status="200 OK"):
    body = as_bytes(body)
    if _body_empty(body):
        return f"No response body returned (status: {status}).\n".encode()
    rest = body.lstrip(SQL_SPACE)
    if rest.startswith(b"[") and rest[1:].lstrip(SQL_SPACE).startswith(b"]") and \
            _body_empty(rest[1:].lstrip(SQL_SPACE)[1:]):
        return f"Query succeeded: no rows returned (status: {status}).\n".encode()
    return body if body.endswith(b"\n") else body + b"\n"


def error_preview(body):
    """readLimitedErrorPreview on the decompressed body."""
    body = as_bytes(body)
    truncated = len(body) > PREVIEW_LIMIT
    preview = go_trim_space(body[:PREVIEW_LIMIT])
    return preview + b"..." if truncated else preview


def expand_generate(spec):
    """Deterministic body generator shared with harness/fixture (`generate` route field)."""
    width = spec.get("width", 0)
    blob = "x" * spec.get("blob_bytes", 0)
    items = []
    for index in range(spec.get("count", 0)):
        number = str(index)
        items.append(spec.get("item", "").replace("{{i}}", number)
                     .replace("{{fill}}", "x" * max(width - len(number), 0)).replace("{{blob}}", blob))
    return (spec.get("prefix", "") + spec.get("separator", "").join(items) + spec.get("suffix", "")).encode()


# --------------------------------------------------------------------------
# Byte-scripted loopback HTTP/1.1 fixture.
#
# Routes match method + unescaped path (+ optional request headers and a use
# count). A response is fully scripted: status line (custom or absent reason,
# or a raw line), headers, framing (length/chunked/close), optional gzip, and
# body steps (send N bytes, delay, wait for a harness-released barrier, stall,
# abort with FIN or RST). Control endpoints live under /__harness/.

REASONS = {100: "Continue", 200: "OK", 201: "Created", 204: "No Content", 299: "", 300: "Multiple Choices",
           301: "Moved Permanently", 302: "Found", 303: "See Other", 304: "Not Modified",
           307: "Temporary Redirect", 308: "Permanent Redirect", 400: "Bad Request", 401: "Unauthorized",
           403: "Forbidden", 404: "Not Found", 408: "Request Timeout", 409: "Conflict", 418: "I'm a teapot",
           422: "Unprocessable Entity", 429: "Too Many Requests", 500: "Internal Server Error",
           502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout"}
TRACE_HEADERS = (*CONTRACT_HEADERS, "user-agent")


def route_body(route, urls):
    if "generate" in route:
        body = expand_generate(route["generate"])
    elif "body_b64" in route:
        body = base64.b64decode(route["body_b64"])
    else:
        body = route.get("body", "").encode()
    for key, value in urls.items():
        body = body.replace(("{{" + key + "}}").encode(), value.encode())
    return body


class FixtureServer:
    """In-process server object; `serve` runs it in a disposable child process."""

    def __init__(self, plan, alias=False):
        self.plan = plan
        self.routes = plan.get("routes", [])
        self.remaining = [route.get("times", 0) for route in self.routes]
        self.lock = threading.Lock()
        self.trace, self.milestones, self.events = [], [], []
        self.barriers = collections.defaultdict(threading.Event)
        self.closing = threading.Event()
        self.connections = 0
        self.listeners = [self._listen("127.0.0.1", "primary")]
        if alias or plan.get("alias"):
            self.listeners.append(self._listen("127.0.0.2", "alias"))
        self.urls = {"url": "http://127.0.0.1:%d" % self.listeners[0][0].getsockname()[1]}
        if len(self.listeners) > 1:
            self.urls["alias_url"] = "http://127.0.0.2:%d" % self.listeners[1][0].getsockname()[1]
        self.bodies = []
        for route in self.routes:
            body = route_body(route, self.urls)
            if route.get("gzip"):
                body = gzip.compress(body, mtime=0)
                corrupt = route.get("gzip_corrupt")
                if corrupt == "header":
                    body = b"\x00" + body[1:]
                elif corrupt == "crc":
                    body = body[:-8] + bytes(b ^ 0xFF for b in body[-8:-4]) + body[-4:]
                elif corrupt == "truncate":
                    body = body[:len(body) // 2]
            self.bodies.append(body)

    @staticmethod
    def _listen(host, name):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        sock.listen(64)
        return sock, name

    def event(self, name, request=None):
        stamp = mono_ns()
        with self.lock:
            self.events.append({"name": name, "monotonic_ns": stamp, "request": request})
            if request is not None:
                self.milestones[request]["events"].append({"name": name, "monotonic_ns": stamp})
        return stamp

    def start(self):
        for sock, name in self.listeners:
            threading.Thread(target=self._accept, args=(sock, name), daemon=True).start()

    def close(self):
        self.closing.set()
        for sock, _ in self.listeners:
            sock.close()

    def _accept(self, sock, name):
        while not self.closing.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with self.lock:
                self.connections += 1
                number = self.connections
            threading.Thread(target=self._connection, args=(conn, name, number), daemon=True).start()

    # ---- request parsing ---------------------------------------------------

    @staticmethod
    def _read_request(reader):
        line = reader.readline(65537)
        while line in (b"\r\n", b"\n"):
            line = reader.readline(65537)
        if not line:
            return None
        parts = line.rstrip(b"\r\n").split(b" ")
        if len(parts) != 3:
            raise ValueError("malformed request line")
        method, target = parts[0].decode(), parts[1].decode("latin-1")
        names, headers = [], {}
        while True:
            header = reader.readline(65537)
            if header in (b"\r\n", b"\n", b""):
                break
            key, _, value = header.decode("latin-1").partition(":")
            names.append(key)
            headers.setdefault(key.strip().lower(), []).append(value.strip())
        framing, body = "none", b""
        if "chunked" in ",".join(headers.get("transfer-encoding", [])).lower():
            framing, data = "chunked", bytearray()
            while True:
                size = int(reader.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while reader.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    break
                data += reader.read(size)
                reader.readline()
            body = bytes(data)
        elif "content-length" in headers:
            framing = "length"
            body = reader.read(int(headers["content-length"][-1]))
        return {"method": method, "target": target, "names": names, "headers": headers, "framing": framing, "body": body}

    # ---- connection loop ---------------------------------------------------

    def _connection(self, conn, listener, number):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        reader = conn.makefile("rb")
        try:
            while not self.closing.is_set():
                try:
                    request = self._read_request(reader)
                except (ValueError, OSError):
                    return
                if request is None:
                    return
                received = mono_ns()
                if request["target"].startswith("/__harness/"):
                    self._control(conn, request)
                    return
                if not self._handle(conn, request, listener, number, received):
                    return
                if "close" in ",".join(request["headers"].get("connection", [])).lower():
                    return
        finally:
            try:
                reader.close()
                conn.close()
            except OSError:
                pass

    def _control(self, conn, request):
        parsed = urllib.parse.urlsplit(request["target"])
        query = urllib.parse.parse_qs(parsed.query)
        with self.lock:
            if parsed.path == "/__harness/trace":
                payload = self.trace
            elif parsed.path == "/__harness/milestones":
                payload = self.milestones
            elif parsed.path == "/__harness/events":
                payload = self.events
            elif parsed.path == "/__harness/release":
                payload = {"released": query.get("name", [])}
            else:
                payload = {"error": "unknown control endpoint"}
        if parsed.path == "/__harness/release":
            for name in query.get("name", []):
                self.barriers[name].set()
                self.event("release:" + name)
        data = json.dumps(payload).encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                     b"Content-Length: %d\r\n\r\n" % len(data) + data)

    def _select(self, request):
        path = urllib.parse.unquote(urllib.parse.urlsplit(request["target"]).path)
        single = {k: v[-1] for k, v in request["headers"].items()}
        with self.lock:
            for index, route in enumerate(self.routes):
                if route["method"] != request["method"] or route["path"] != path:
                    continue
                if not all(single.get(k.lower()) == v for k, v in route.get("headers", {}).items()):
                    continue
                if route.get("times") and self.remaining[index] <= 0:
                    continue
                if route.get("times"):
                    self.remaining[index] -= 1
                return index
        return None

    def _handle(self, conn, request, listener, number, received):
        index = self._select(request)
        single = {k: ",".join(v) for k, v in request["headers"].items()}
        with self.lock:
            request_number = len(self.trace)
            self.trace.append({
                "protocol": "http", "method": request["method"], "path": request["target"],
                "headers": {k: single[k] for k in TRACE_HEADERS if k in single and k != "user-agent"},
                "user_agent": single.get("user-agent"), "header_names": request["names"],
                "body": request["body"].decode("utf-8", "replace"), "body_b64": base64.b64encode(request["body"]).decode(),
                "framing": request["framing"], "listener": listener, "connection": number,
                "time_unix_ns": time.time_ns(), "unexpected": index is None, "route": index})
            self.milestones.append({"request": request_number, "route": index, "received_monotonic_ns": received,
                                    "events": [], "body_bytes_sent": 0, "wire_bytes_sent": 0})
        self.event("received", request_number)
        required = self.plan.get("headers", {})
        if not all(single.get(k.lower()) == v for k, v in required.items()):
            return self._simple(conn, 401, b"fixture: incorrect authentication\n", request_number)
        if index is None:
            return self._simple(conn, 500, b"fixture: unexpected request\n", request_number)
        return self._respond(conn, self.routes[index], self.bodies[index], request_number)

    def _simple(self, conn, status, body, request_number):
        conn.sendall(b"HTTP/1.1 %d %s\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: %d\r\n\r\n"
                     % (status, REASONS[status].encode(), len(body)) + body)
        self.event("complete", request_number)
        return True

    def _client_gone(self, conn):
        try:
            readable = selectors.DefaultSelector()
            readable.register(conn, selectors.EVENT_READ)
            ready = readable.select(0)
            readable.close()
            return bool(ready) and conn.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _wait(self, conn, request_number, milliseconds=None, barrier=None, label="wait"):
        """Delay, wait for a barrier, or stall (both None). False if the client went away."""
        deadline = None if milliseconds is None else time.monotonic() + milliseconds / 1000
        if barrier is not None:
            self.event("barrier:" + barrier, request_number)
        elif milliseconds is None:
            self.event("stall", request_number)
        while not self.closing.is_set():
            if barrier is not None and self.barriers[barrier].is_set():
                self.event("released:" + barrier, request_number)
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return True
            if self._client_gone(conn):
                self.event("client-gone", request_number)
                return False
            wait = 0.005 if deadline is None else min(0.005, max(deadline - time.monotonic(), 0))
            if barrier is not None:
                self.barriers[barrier].wait(wait)
            else:
                time.sleep(wait)
        return False

    def _send(self, conn, data, request_number, body_bytes=0):
        try:
            conn.sendall(data)
        except OSError:
            self.event("client-gone", request_number)
            return False
        with self.lock:
            milestone = self.milestones[request_number]
            milestone["wire_bytes_sent"] += len(data)
            milestone["body_bytes_sent"] += body_bytes
        return True

    def _abort(self, conn, how, request_number):
        if how == "rst":
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        else:
            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        self.event("abort:" + how, request_number)
        return False

    def _respond(self, conn, route, body, request_number):
        if route.get("delay_ms"):
            if not self._wait(conn, request_number, route["delay_ms"]):
                return False
        if route.get("header_barrier"):
            if not self._wait(conn, request_number, barrier=route["header_barrier"]):
                return False
        if route.get("header_stall"):
            self._wait(conn, request_number)
            return False
        if route.get("close_before_headers"):
            return self._abort(conn, route["close_before_headers"], request_number)
        status = route.get("status") or 200
        if "raw_status_line" in route:
            line = route["raw_status_line"].encode()
        elif "reason" in route and route["reason"] is None:
            line = b"HTTP/1.1 %d" % status
        else:
            line = b"HTTP/1.1 %d %s" % (status, route.get("reason", REASONS.get(status, "")).encode())
        framing = route.get("framing") or ("chunked" if route.get("chunk_bytes") or route.get("script") else "length")
        headers = []
        if route.get("content_type", "application/json") is not None:
            headers.append(("Content-Type", route.get("content_type", "application/json")))
        if route.get("gzip"):
            headers.append(("Content-Encoding", "gzip"))
        for name, value in route.get("headers_out", []):
            for key, url in self.urls.items():
                value = value.replace("{{" + key + "}}", url)
            headers.append((name, value))
        if framing == "length":
            headers.append(("Content-Length", str(route.get("content_length", len(body)))))
        elif framing == "chunked":
            headers.append(("Transfer-Encoding", "chunked"))
        elif framing == "close":
            headers.append(("Connection", "close"))
        head = line + b"\r\n" + b"".join(f"{k}: {v}\r\n".encode() for k, v in headers) + b"\r\n"
        if not self._send(conn, head, request_number):
            return False
        self.event("headers", request_number)
        steps = route.get("script")
        if steps is None:
            size = route.get("chunk_bytes") or max(len(body), 1)
            steps = []
            for offset in range(0, len(body), size):
                if offset:
                    steps.append({"delay_ms": route.get("chunk_delay_ms", 0)})
                steps.append({"send": min(size, len(body) - offset)})
        offset, first = 0, True
        for step in steps:
            if "send" in step:
                count = len(body) - offset if step["send"] == "rest" else step["send"]
                piece = body[offset:offset + count]
                offset += len(piece)
                if not piece:
                    continue
                frame = (b"%x\r\n" % len(piece) + piece + b"\r\n") if framing == "chunked" else piece
                if not self._send(conn, frame, request_number, len(piece)):
                    return False
                if first:
                    self.event("first-body", request_number)
                    first = False
            elif "delay_ms" in step:
                if step["delay_ms"] and not self._wait(conn, request_number, step["delay_ms"]):
                    return False
            elif "barrier" in step:
                if not self._wait(conn, request_number, barrier=step["barrier"]):
                    return False
            elif "stall" in step:
                self._wait(conn, request_number)
                return False
            elif "abort" in step:
                return self._abort(conn, step["abort"], request_number)
        if offset < len(body):
            rest = body[offset:]
            frame = (b"%x\r\n" % len(rest) + rest + b"\r\n") if framing == "chunked" else rest
            if not self._send(conn, frame, request_number, len(rest)):
                return False
            if first:
                self.event("first-body", request_number)
        if framing == "chunked" and not route.get("omit_terminator"):
            if not self._send(conn, b"0\r\n\r\n", request_number):
                return False
        self.event("complete", request_number)
        if route.get("after") == "stall":
            self._wait(conn, request_number)
            return False
        if framing == "close" or route.get("after") in ("fin", "rst"):
            return self._abort(conn, route.get("after", "fin"), request_number)
        return True


def serve(scenario_path):
    plan = json.loads(Path(scenario_path).read_text())
    server = FixtureServer(plan)
    server.start()
    print(json.dumps({**server.urls, "grpc_port": 0}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.close()


def alias_available():
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.2", 0))
        return True
    except OSError:
        return False


class Fixture:
    """Disposable `phase3.py serve` child process with the run.Fixture trace protocol."""

    def __init__(self, scenario, root):
        self.proc = None
        path = root / "scenario.json"
        run.write_json(path, scenario)
        self.log = (root / "fixture.stderr").open("wb")
        self.proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "serve", "--scenario", str(path)],
                                     stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError("phase3 fixture did not become ready within 10s")
            self.urls = json.loads(self.proc.stdout.readline())
            self.urls.pop("grpc_port", None)
            self.url = self.urls["url"]
        except Exception as error:
            self.close()
            raise RuntimeError(f"phase3 fixture startup failed: {error}") from error
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def control(self, path):
        with self.opener.open(self.url + "/__harness/" + path, timeout=5) as response:
            return json.load(response)

    def trace(self):
        return self.control("trace")

    def milestones(self):
        return self.control("milestones")

    def events(self):
        return self.control("events")

    def release(self, name):
        return self.control("release?name=" + urllib.parse.quote(name))

    def close(self):
        if self.proc:
            run.kill_group(self.proc, signal.SIGKILL)
            self.proc.wait()
            self.proc.stdout.close()
            self.log.close()
            self.proc = None


# --------------------------------------------------------------------------
# Process runner: spooled stdout with incremental digest, independent stderr,
# PTY/devfull devices, slow reader, and causal actions on observed state.


class ElementCounter:
    """Count complete outer-array values in stdout (structure only, no validation).

    Containers and strings complete at their closing byte. Bare scalars complete
    only at a following delimiter, because the next write may extend them.
    """

    def __init__(self):
        self.elements, self.state, self.stack, self.in_string, self.escaped = 0, "start", 0, False, False
        self.kind = None

    def feed(self, chunk):
        for byte in chunk:
            if self.state == "done":
                return
            if self.state == "start":
                if byte == 0x5B:
                    self.state = "value"
                elif byte not in SQL_SPACE:
                    self.state = "done"
                continue
            if self.kind is None:
                if byte in SQL_SPACE or byte == 0x2C:
                    continue
                if byte == 0x5D:
                    self.state = "done"
                    continue
                self.kind = "container" if byte in b"[{" else "string" if byte == 0x22 else "scalar"
                self.stack = 1 if self.kind == "container" else 0
                self.in_string = byte == 0x22
                continue
            if self.kind == "scalar":
                if byte in SQL_SPACE or byte in b",]":
                    self.elements += 1
                    self.kind = None
                    if byte == 0x5D:
                        self.state = "done"
                continue
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif byte == 0x5C:
                    self.escaped = True
                elif byte == 0x22:
                    self.in_string = False
                    if self.kind == "string":
                        self.elements += 1
                        self.kind = None
                continue
            if byte == 0x22:
                self.in_string = True
            elif byte in b"[{":
                self.stack += 1
            elif byte in b"]}":
                self.stack -= 1
                if self.stack == 0:
                    self.elements += 1
                    self.kind = None


SIGNALS = {"SIGINT": signal.SIGINT, "SIGTERM": signal.SIGTERM, "SIGHUP": signal.SIGHUP}


def _condition_met(when, state, fixture):
    if "start" in when:
        return True
    if "after_ms" in when:
        return (mono_ns() - state["armed_ns"]) / 1e6 >= when["after_ms"]
    if "stdout_bytes" in when:
        return state["stdout_bytes"] >= when["stdout_bytes"]
    if "stdout_elements" in when:
        return state["counter"].elements >= when["stdout_elements"]
    if "stdout_contains" in when:
        return as_bytes(when["stdout_contains"]) in state["stdout_window"]
    if "stderr_contains" in when:
        return as_bytes(when["stderr_contains"]) in state["stderr"]
    if "fixture" in when:
        if fixture is None:
            return False
        now = mono_ns()
        if now - state.get("polled_ns", 0) < 5_000_000:
            return False
        state["polled_ns"] = now
        try:
            return any(event["name"] == when["fixture"] for event in fixture.events())
        except OSError:
            return False
    raise ValueError(f"unknown action condition {when}")


def execute(command, env, cwd, plan, fixture, spool, timeout, meter=None):
    """Run one process. `plan` holds io devices, reader pacing and ordered actions."""
    io_spec = plan.get("io", {})
    endpoints, child_fds, metrics = {}, [], None
    for name in ("stdout", "stderr"):
        mode = io_spec.get(name, "pipe")
        if mode == "pipe":
            read_fd, write_fd = os.pipe()
        elif mode == "pty":
            read_fd, write_fd = os.openpty()
            import tty
            tty.setraw(write_fd)
        elif mode == "devfull":
            read_fd, write_fd = None, os.open("/dev/full", os.O_WRONLY)
        else:
            raise ValueError(f"unknown io mode {mode}")
        endpoints[name] = read_fd
        child_fds.append(write_fd)
    argv = [os.fsencode(a) if isinstance(a, str) else a for a in command]
    if meter:
        metrics = Path(spool).parent / "meter.json"
        argv = [os.fsencode(str(meter)), os.fsencode(str(metrics)), b"--", *argv]
    started = mono_ns()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=child_fds[0],
                            stderr=child_fds[1], start_new_session=True)
    for fd in child_fds:
        os.close(fd)
    out_file = spool.open("wb")
    digest = hashlib.sha256()
    counter = ElementCounter()
    state = {"stdout_bytes": 0, "stdout_window": bytearray(), "stderr": bytearray(), "counter": counter,
             "armed_ns": started}
    memory = bytearray()
    timings = {"first_stdout_ms": None, "first_element_ms": None, "last_stdout_ms": None, "first_stderr_ms": None}
    actions = [{"index": i, **copy.deepcopy(a), "met": None, "done_ms": None} for i, a in enumerate(plan.get("actions", []))]
    pending = list(actions)
    reader = plan.get("reader", {})
    read_size = reader.get("chunk", 65536)
    read_delay = reader.get("delay_ms", 0) / 1000
    paused, resume_at = False, 0.0
    failure, status, usage, exited = None, None, None, None
    selector = selectors.DefaultSelector()
    for name, fd in endpoints.items():
        if fd is not None:
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
    open_fds = {name: fd for name, fd in endpoints.items() if fd is not None}
    snapshots = {}

    def close_stream(name):
        fd = open_fds.pop(name, None)
        if fd is not None:
            try:
                selector.unregister(fd)
            except (KeyError, ValueError):
                pass
            os.close(fd)

    def perform(action):
        nonlocal paused
        todo = action["do"]
        if "release" in todo:
            fixture.release(todo["release"])
        elif "signal" in todo:
            run.kill_group(proc, SIGNALS[todo["signal"]])
        elif "close" in todo:
            close_stream(todo["close"])
        elif "pause_stdout" in todo:
            paused = True
            if "stdout" in open_fds:
                selector.unregister(open_fds["stdout"])
        elif "resume_stdout" in todo:
            if paused and "stdout" in open_fds:
                selector.register(open_fds["stdout"], selectors.EVENT_READ, "stdout")
            paused = False
        elif "snapshot_fixture" in todo:
            snapshots[todo["snapshot_fixture"]] = fixture.milestones() if fixture else []
        else:
            raise ValueError(f"unknown action {todo}")
        action["done_ms"] = (mono_ns() - started) / 1e6

    try:
        while True:
            now = mono_ns()
            elapsed = (now - started) / 1e9
            if status is None:
                pid, raw, stats = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    status, usage, exited = raw, stats, now
                    proc.returncode = os.waitstatus_to_exitcode(raw)
            if elapsed > timeout and failure is None:
                failure = "timeout"
                run.kill_group(proc, signal.SIGKILL)
            if status is not None and not open_fds:
                break
            if status is not None and elapsed > timeout + 2:
                failure = failure or "descendant-held-pipe"
                break
            while pending:
                action = pending[0]
                limit = action.get("within_ms", 5000)
                if _condition_met(action["when"], state, fixture):
                    action["met"] = True
                elif status is not None or (mono_ns() - state["armed_ns"]) / 1e6 > limit:
                    action["met"] = False
                else:
                    break
                pending.pop(0)
                state["armed_ns"] = mono_ns()
                if action["met"] or (status is None and action.get("force", True)):
                    try:
                        perform(action)
                    except OSError as error:
                        action["error"] = str(error)
            if paused and status is not None:
                paused = False
                if "stdout" in open_fds:
                    selector.register(open_fds["stdout"], selectors.EVENT_READ, "stdout")
            if read_delay and "stdout" in open_fds and not paused and time.monotonic() < resume_at:
                try:
                    selector.unregister(open_fds["stdout"])
                except KeyError:
                    pass
                time.sleep(min(resume_at - time.monotonic(), 0.01))
                if time.monotonic() >= resume_at:
                    selector.register(open_fds["stdout"], selectors.EVENT_READ, "stdout")
                continue
            ready = selector.select(0.005) if selector.get_map() else (time.sleep(0.005) or [])
            for key, _ in ready:
                name = key.data
                fd = open_fds.get(name)
                if fd is None:
                    continue
                try:
                    chunk = os.read(fd, read_size if name == "stdout" else 65536)
                except BlockingIOError:
                    continue
                except OSError:  # EIO from a PTY master after the slave closes
                    chunk = b""
                if not chunk:
                    close_stream(name)
                    continue
                observed = (mono_ns() - started) / 1e6
                if timings[f"first_{name}_ms"] is None:
                    timings[f"first_{name}_ms"] = observed
                if name == "stdout":
                    timings["last_stdout_ms"] = observed
                    room = plan.get("stdout_cap", DEFAULT_STDOUT_CAP) - state["stdout_bytes"]
                    if len(chunk) > room:
                        chunk = chunk[:max(room, 0)]
                        failure = failure or "output-limit"
                        run.kill_group(proc, signal.SIGKILL)
                    out_file.write(chunk)
                    digest.update(chunk)
                    state["stdout_bytes"] += len(chunk)
                    if len(memory) < STDOUT_MEMORY:
                        memory += chunk
                    window = state["stdout_window"]
                    window += chunk
                    del window[:-65536]
                    before = counter.elements
                    counter.feed(chunk)
                    if counter.elements and not before:
                        timings["first_element_ms"] = observed
                    if read_delay:
                        resume_at = time.monotonic() + read_delay
                else:
                    if len(state["stderr"]) + len(chunk) > STDERR_CAP:
                        failure = failure or "output-limit"
                        run.kill_group(proc, signal.SIGKILL)
                    state["stderr"] += chunk
    finally:
        run.kill_group(proc, signal.SIGKILL)
        if status is None:
            _, status, usage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            exited = mono_ns()
        for name in list(open_fds):
            close_stream(name)
        selector.close()
        out_file.close()
    termination = {"signal": os.WTERMSIG(status)} if os.WIFSIGNALED(status) else {"exit": os.WEXITSTATUS(status)}
    resources = {"source": "supervisor_wait4_fallback", "cpu_ms": (usage.ru_utime + usage.ru_stime) * 1000,
                 "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)}
    if metrics is not None:
        try:
            measured = json.loads(metrics.read_text())
            termination = measured["termination"]
            resources = {"source": "native_child_wait4", "cpu_ms": measured["cpu_user_ms"] + measured["cpu_system_ms"],
                         "peak_rss_bytes": measured["peak_rss_bytes"]}
        except (OSError, ValueError, KeyError):
            failure = failure or "meter did not produce resource measurements"
    for action in pending:
        action["met"] = False
    complete = state["stdout_bytes"] <= len(memory)
    return {"termination": termination, "failure": failure, "wall_ms": (exited - started) / 1e6,
            "started_monotonic_ns": started, "stdout": bytes(memory) if complete else None,
            "stdout_sha256": digest.hexdigest(), "stdout_bytes": state["stdout_bytes"], "stdout_elements": counter.elements,
            "stderr": bytes(state["stderr"]), "actions": actions, "snapshots": snapshots, "resources": resources, **timings}


# --------------------------------------------------------------------------
# Samples, normalization, contracts.


def requirement_skip(case, binaries):
    if case["build"] == "cloud" and not binaries.get("cloud"):
        return "requires binaries built with the harness cloud orchestrator token"
    for need in case["requires"]:
        if need == "nonroot" and os.geteuid() == 0:
            return "requires a non-root user (permission-denied paths)"
        if need == "devfull" and not os.path.exists("/dev/full"):
            return "requires /dev/full"
        if need == "pty" and not hasattr(os, "openpty"):
            return "requires POSIX pseudo-terminals"
        if need == "loopback-alias" and not alias_available():
            return "requires a bindable 127.0.0.2 loopback alias (Linux)"
    return None


def replacements_for(home, fixture):
    replacements = {str(home): "<HOME>"}
    if fixture:
        replacements[fixture.url] = "http://<FIXTURE>"
        replacements[fixture.url.removeprefix("http://")] = "<FIXTURE-HOST>"
        if "alias_url" in fixture.urls:
            replacements[fixture.urls["alias_url"]] = "http://<ALIAS>"
            replacements[fixture.urls["alias_url"].removeprefix("http://")] = "<ALIAS-HOST>"
    return replacements


def substitute(value, urls):
    if isinstance(value, dict):
        return as_bytes(value)
    for key, url in urls.items():
        value = value.replace("{{" + key + "}}", url)
    return value


def request_contract(trace, replacements):
    contract = []
    for entry in trace:
        contract.append({"method": entry["method"],
                         "target": run.normalize(entry["path"].encode(), replacements).decode(),
                         "headers": {k: run.normalize(v.encode(), replacements).decode() for k, v in sorted(entry["headers"].items())},
                         "body_b64": entry["body_b64"], "framing": entry["framing"], "listener": entry["listener"]})
    return contract


def sample(binary, case, out_dir, timeout, meter=None):
    """One fresh HOME, one fixture lifecycle and one process; artifacts go to out_dir."""
    out_dir.mkdir(parents=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="pb-phase3-"))
    try:
        home = tmp_root / "home"
        for name in ("config", "cache", "data", "state", "tmp"):
            (home / name).mkdir(parents=True)
        fixture_root = tmp_root / "fixture"
        fixture_root.mkdir()
        fixture = Fixture(case["fixture"], fixture_root) if case.get("fixture") is not None else None
        try:
            urls = fixture.urls if fixture else {"url": NO_SERVER}
            phase2.seed(home, case, urls["url"])
            replacements = replacements_for(home, fixture)
            before = phase2.config_state(home, replacements)
            env = {k: substitute(v, urls) for k, v in phase2.isolated_env(home, {"env": case.get("env", {}),
                                                                                "unset": case.get("unset", [])}).items()}
            argv = [str(binary), *[substitute(a, urls) for a in case["argv"]]]
            result = execute(argv, env, home, case, fixture, out_dir / "stdout.bin",
                             case.get("timeout", timeout), meter)
            trace = fixture.trace() if fixture else []
            milestones = fixture.milestones() if fixture else []
            events = fixture.events() if fixture else []
            after = phase2.config_state(home, replacements)
            config_path = home / "config" / "pb" / "config.toml"
            config_raw = config_path.read_bytes() if config_path.is_file() and os.access(config_path, os.R_OK) else None
        finally:
            if fixture:
                fixture.close()
    finally:
        for path in sorted(tmp_root.rglob("*"), reverse=True):
            try:
                if path.is_dir() and not path.is_symlink():
                    path.chmod(0o700)
            except OSError:
                pass
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)
    result.update(binary=str(binary), trace=trace, milestones=milestones, events=events, config_before=before,
                  config_after=after, config_raw_after=config_raw, replacements=replacements,
                  requests=request_contract(trace, replacements))
    stdout = result["stdout"]
    result["stdout_normalized"] = run.normalize(stdout, replacements) if stdout is not None else None
    result["stdout_normalized_sha256"] = run.digest(result["stdout_normalized"]) if stdout is not None else result["stdout_sha256"]
    result["stderr_normalized"] = run.normalize(result["stderr"], replacements)
    (out_dir / "stderr.bin").write_bytes(result["stderr"])
    (out_dir / "stderr.normalized").write_bytes(result["stderr_normalized"])
    if stdout is not None:
        (out_dir / "stdout.normalized").write_bytes(result["stdout_normalized"])
    run.write_json(out_dir / "trace.json", trace)
    run.write_json(out_dir / "milestones.json", {"process_started_monotonic_ns": result["started_monotonic_ns"],
                                                 "milestones": milestones, "events": events, "snapshots": result["snapshots"]})
    if config_raw is not None:
        (out_dir / "config-after.toml").write_bytes(config_raw)
    run.write_json(out_dir / "result.json", public(result))
    return result


def normalize_timeout(case, data):
    """Only timeout races observed in repeated Go deadline fixtures."""
    if not case.get("compare", {}).get("timeout_cause"):
        return data
    bare_context = {
        "p3-deadline-preview-needs-one-more-byte": b"failed to read error response body",
        "p3-deadline-not-reset-per-chunk": b"failed to stream response body",
    }.get(case["name"])
    if bare_context is not None and data == b"Error: " + bare_context + b": context deadline exceeded\n":
        return (b"Error: " + bare_context + b": <CLIENT-TIMEOUT> "
                b"(Client.Timeout or context cancellation while reading body)\n")
    return TIMEOUT_CAUSE.sub(rb"<CLIENT-TIMEOUT> (\1)", data)


def compare_stderr(case, data):
    """Comparison form of stderr: declared nondeterminism only (spinner frames, map order, client port)."""
    options = case.get("compare", {})
    if options.get("client_port"):
        data = CLIENT_PORT.sub(b"<CLIENT>->", data)
    data = normalize_timeout(case, data)
    if options.get("spinner"):
        data = SPINNER_RUN.sub(b"<SPINNER>", data)
        data = SPINNER_TAIL.sub(b"<SPINNER-UNCLEARED>", data)
    if options.get("stderr_unordered_lines"):
        data = b"".join(sorted(data.splitlines(True)))
    return data


def spinner_issues(stderr):
    """Frames must cycle | / - \\ from the first frame; a clear must follow unless interrupted."""
    frames = [m.group(1) for m in SPINNER_FRAME.finditer(stderr)]
    cycle = [b"|", b"/", b"-", b"\\"]
    if frames != [cycle[i % 4] for i in range(len(frames))]:
        return [f"spinner frames out of order: {frames[:8]}"]
    return []


def public(result):
    keep = ("binary", "termination", "failure", "wall_ms", "stdout_sha256", "stdout_bytes", "stdout_normalized_sha256",
            "stdout_elements", "first_stdout_ms", "first_element_ms", "last_stdout_ms", "first_stderr_ms",
            "actions", "resources", "requests")
    data = {k: result.get(k) for k in keep}
    data["stderr_sha256"] = run.digest(result["stderr_normalized"])
    data["stderr_raw_sha256"] = run.digest(result["stderr"])
    data["trace_sha256"] = run.digest(json.dumps(result["trace"], sort_keys=True).encode())
    data["milestones_sha256"] = run.digest(json.dumps(result["milestones"], sort_keys=True).encode())
    data["user_agents"] = [entry.get("user_agent") for entry in result["trace"]]
    data["fixture_milestones_relative_ms"] = relative_milestones(result)
    data["config_before"], data["config_after"] = result["config_before"], result["config_after"]
    return data


def relative_milestones(result):
    started = result["started_monotonic_ns"]
    return [{"request": m["request"], "received_ms": (m["received_monotonic_ns"] - started) / 1e6,
             "events": [{"name": e["name"], "ms": (e["monotonic_ns"] - started) / 1e6} for e in m["events"]],
             "body_bytes_sent": m["body_bytes_sent"], "wire_bytes_sent": m["wire_bytes_sent"]}
            for m in result["milestones"]]


def blocked_bytes(result, label="blocked"):
    snapshot = result["snapshots"].get(label)
    return None if snapshot is None else sum(m["body_bytes_sent"] for m in snapshot)


def manifest_entry(case, result):
    return {"termination": result["termination"], "stdout_sha256": result["stdout_normalized_sha256"],
            "stdout_bytes": result["stdout_bytes"],
            "stderr_sha256": run.digest(compare_stderr(case, result["stderr_normalized"])),
            "requests_sha256": run.digest(run.canonical(result["requests"])),
            "config_after_sha256": run.digest(run.canonical(phase2.semantic(result["config_after"])))}


# --------------------------------------------------------------------------
# Reference expectations and differential comparison.


def expectation_issues(case, result):
    """Assert the Go reference exercised the behavior each case pins."""
    want, issues = case["expect"], []
    if result["failure"]:
        issues.append(result["failure"])
    if result["termination"] != want["termination"]:
        issues.append(f"termination {result['termination']}, expected {want['termination']}")
    requests = [r["method"] + " " + urllib.parse.unquote(urllib.parse.urlsplit(r["path"]).path) for r in result["trace"]]
    if requests != want["requests"]:
        issues.append(f"requests {requests!r}, expected {want['requests']!r}")
    if any(r["unexpected"] for r in result["trace"]):
        issues.append("unexpected fixture request")
    for index, body in want.get("request_bodies", {}).items():
        index = int(index)
        if index >= len(result["trace"]) or base64.b64decode(result["trace"][index]["body_b64"]) != as_bytes(body):
            actual = base64.b64decode(result["trace"][index]["body_b64"]) if index < len(result["trace"]) else None
            issues.append(f"request {index} body {actual!r}, expected {as_bytes(body)!r}")
    for index, headers in want.get("request_headers", {}).items():
        index = int(index)
        actual = result["requests"][index]["headers"] if index < len(result["requests"]) else {}
        for key, value in headers.items():
            if actual.get(key) != value:
                issues.append(f"request {index} header {key}={actual.get(key)!r}, expected {value!r}")
    stdout = result["stdout_normalized"]
    if "stdout_sha256" in want:
        if result["stdout_normalized_sha256"] != want["stdout_sha256"] or result["stdout_bytes"] != want["stdout_bytes"]:
            issues.append(f"stdout digest/size differs from model ({result['stdout_bytes']} bytes, expected {want['stdout_bytes']})")
    for stream in ("stdout", "stderr"):
        data = stdout if stream == "stdout" else result["stderr_normalized"]
        if stream == "stderr" and case.get("compare", {}).get("client_port"):
            data = CLIENT_PORT.sub(b"<CLIENT>->", data)
        if stream == "stderr":
            data = normalize_timeout(case, data)
        if data is None:
            if any(k.startswith(stream + "_") for k in want if k not in ("stdout_sha256", "stdout_bytes")):
                issues.append(f"{stream} too large for byte expectations")
            continue
        if stream + "_exact" in want and data != as_bytes(want[stream + "_exact"]):
            issues.append(f"{stream} {data[:300]!r}, expected {as_bytes(want[stream + '_exact'])[:300]!r}")
        for needle in want.get(stream + "_has", []):
            if as_bytes(needle) not in data:
                issues.append(f"{stream} lacks {as_bytes(needle)!r}")
        if stream + "_prefix" in want and not data.startswith(as_bytes(want[stream + "_prefix"])):
            issues.append(f"{stream} {data[:200]!r} lacks prefix {as_bytes(want[stream + '_prefix'])[:200]!r}")
        if stream + "_prefix_of" in want and not as_bytes(want[stream + "_prefix_of"]).startswith(data):
            issues.append(f"{stream} {data[:200]!r} is not a prefix of the model output")
        if stream + "_json" in want:
            try:
                if json.loads(data) != want[stream + "_json"]:
                    issues.append(f"{stream} JSON {data[:300]!r} differs from expectation")
            except ValueError:
                issues.append(f"{stream} is not one JSON document")
        if stream + "_regex" in want and not re.fullmatch(as_bytes(want[stream + "_regex"]), data, re.S):
            issues.append(f"{stream} {data[:200]!r} does not match {want[stream + '_regex']!r}")
    if case.get("compare", {}).get("spinner"):
        issues.extend(spinner_issues(result["stderr"]))
    if "min_spinner_frames" in want and len(SPINNER_FRAME.findall(result["stderr"])) < max(want["min_spinner_frames"], 1):
        issues.append(f"fewer than {want['min_spinner_frames']} spinner frames")
    if "config" in want:
        issues.extend(phase2.config_issues(want["config"], result))
    if "max_wall_ms" in want and result["wall_ms"] > want["max_wall_ms"]:
        issues.append(f"wall {result['wall_ms']:.0f} ms exceeds {want['max_wall_ms']} ms (early completion)")
    lengths = case.get("compare", {}).get("stdout_lengths")
    if lengths is not None and (stdout is None or len(stdout) not in lengths):
        issues.append(f"stdout length {result['stdout_bytes']} not in the paced-deadline range {lengths}")
    if "min_wall_ms" in want and result["wall_ms"] < want["min_wall_ms"]:
        issues.append(f"wall {result['wall_ms']:.0f} ms is below {want['min_wall_ms']} ms (deadline fired early)")
    for action in result["actions"]:
        if action.get("required", True) and not action["met"]:
            issues.append(f"action {action['index']} condition {action['when']} not met")
    if want.get("output_before_completion"):
        issues.extend(causal_issues(result))
    return issues


def causal_issues(result):
    """First observed stdout must precede the fixture's final body milestone."""
    finals = [e["monotonic_ns"] for m in result["milestones"] for e in m["events"] if e["name"] == "complete"]
    if result["first_stdout_ms"] is None:
        return ["no stdout observed"]
    observed = result["started_monotonic_ns"] + result["first_stdout_ms"] * 1e6
    if finals and observed >= min(finals):
        return ["stdout was not observable before the response body completed"]
    return []


def differences(case, reference, candidate):
    issues = []
    for role, result in (("reference", reference), ("candidate", candidate)):
        if result["failure"]:
            issues.append(f"{role}: {result['failure']}")
        for action in result["actions"]:
            if action.get("required", True) and not action["met"]:
                issues.append(f"{role}: action {action['index']} condition {action['when']} not met")
    if STUB_MARKER in candidate["stderr"] or (candidate["stdout"] and STUB_MARKER in candidate["stdout"]):
        issues.append("candidate returned a deferred-operation stub")
    if reference["termination"] != candidate["termination"]:
        issues.append(f"termination {candidate['termination']}, expected {reference['termination']}")
    lengths = case.get("compare", {}).get("stdout_lengths")
    if lengths is not None:
        # Deadline boundary: both must be prefixes of the declared body with a length derived from the pacing.
        full_body = as_bytes(case["expect"]["stdout_prefix_of"])
        for role, result in (("reference", reference), ("candidate", candidate)):
            out = result["stdout_normalized"] or b""
            if not full_body.startswith(out) or len(out) not in lengths:
                issues.append(f"{role} stdout {out!r} is not a {lengths}-byte prefix of the declared body")
    elif reference["stdout_normalized_sha256"] != candidate["stdout_normalized_sha256"]:
        if reference["stdout_normalized"] is not None and candidate["stdout_normalized"] is not None:
            issues.append(phase2.stream_issue("stdout", reference["stdout_normalized"], candidate["stdout_normalized"]))
        else:
            issues.append(f"stdout digest differs: candidate {candidate['stdout_bytes']} bytes, reference {reference['stdout_bytes']} bytes")
    expected, actual = compare_stderr(case, reference["stderr_normalized"]), compare_stderr(case, candidate["stderr_normalized"])
    if expected != actual:
        issues.append(phase2.stream_issue("stderr", expected, actual))
    if case.get("compare", {}).get("spinner"):
        issues.extend("candidate: " + issue for issue in spinner_issues(candidate["stderr"]))
    frames = case["expect"].get("min_spinner_frames")
    if frames is not None and len(SPINNER_FRAME.findall(candidate["stderr"])) < frames:
        issues.append(f"candidate: fewer than {frames} spinner frames (collapsed comparison requires activation)")
    if reference["requests"] != candidate["requests"]:
        issues.append(f"requests differ: candidate {request_lines(candidate)}, reference {request_lines(reference)}")
    for key in ("config_before", "config_after"):
        if phase2.semantic(reference[key]) != phase2.semantic(candidate[key]):
            issues.append(key.replace("_", " ") + " differs semantically")
    issues.extend(phase2.rewrite_issues(reference, candidate))
    limit = case["expect"].get("max_wall_ms")
    if limit is not None and candidate["wall_ms"] > limit:
        issues.append(f"candidate wall {candidate['wall_ms']:.0f} ms exceeds {limit} ms (early completion)")
    floor = case["expect"].get("min_wall_ms")
    if floor is not None and candidate["wall_ms"] < floor:
        issues.append(f"candidate wall {candidate['wall_ms']:.0f} ms is below {floor} ms (deadline fired early)")
    if case["expect"].get("output_before_completion"):
        issues.extend("candidate: " + issue for issue in causal_issues(candidate))
    margin = case.get("compare", {}).get("blocked_bytes_margin")
    if margin is not None:
        ref_bytes, cand_bytes = blocked_bytes(reference), blocked_bytes(candidate)
        if ref_bytes is None or cand_bytes is None:
            issues.append("blocked-reader fixture snapshot missing")
        elif cand_bytes > ref_bytes + margin:
            issues.append(f"candidate consumed {cand_bytes} response bytes while stdout was blocked; "
                          f"reference {ref_bytes} + declared margin {margin}")
    return issues


def request_lines(result):
    return [r["method"] + " " + r["target"] for r in result["requests"]]


def advisories(reference, candidate):
    notes = []
    if [e.get("user_agent") for e in reference["trace"]] != [e.get("user_agent") for e in candidate["trace"]]:
        notes.append("User-Agent differs")
    if [e["header_names"] for e in reference["trace"]] != [e["header_names"] for e in candidate["trace"]]:
        notes.append("request header order/case differs")
    if reference.get("config_raw_after") != candidate.get("config_raw_after") and \
            run.normalize(reference.get("config_raw_after") or b"", reference["replacements"]) != \
            run.normalize(candidate.get("config_raw_after") or b"", candidate["replacements"]):
        notes.append("final config bytes differ (semantically equal TOML is accepted)")
    return notes


# --------------------------------------------------------------------------
# Fixed corpus. Every secret and endpoint is synthetic/loopback; seeds are recorded.

SEEDS = {"rewrite": 3001, "json-valid": 3002, "json-malformed": 3003, "text": 3004, "chunking": 3005}
QUERY = "POST /api/v1/query"
REQUIRED = 'SQL query is required; example: pb sql run "select * from frontend" --from=10m --to=now'
ROWS = b'[{"b":2,"a":"x <&> \xe2\x80\xa8"},{"a":1}]'


def http_code(status):
    known = {400: ("INVALID_INPUT", False), 422: ("INVALID_INPUT", False), 401: ("AUTH_FAILED", False),
             403: ("PERMISSION_DENIED", False), 404: ("NOT_FOUND", False), 408: ("TIMEOUT", True),
             504: ("TIMEOUT", True), 409: ("CONFLICT", False), 429: ("RATE_LIMITED", True)}
    return known.get(status, ("SERVER_ERROR", True) if status >= 500 else ("COMMAND_FAILED", False))


def pin(data):
    """Readable str when the bytes are UTF-8, otherwise a base64 literal."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return B(data)


def route(body=b"[]", status=None, method="POST", path="/api/v1/query", **options):
    result = {"method": method, "path": path, **options}
    if body is not None and "generate" not in options:
        body = as_bytes(body)
        try:
            result["body"] = body.decode("utf-8")
        except UnicodeDecodeError:
            result["body_b64"] = base64.b64encode(body).decode()
    if status is not None:
        result["status"] = status
    return result


def sql(query, *flags, times=True):
    return ["sql", "run", query, *(["--from", START, "--to", END] if times else []), *flags]


def expect(termination=0, requests=(QUERY,), **pins):
    if isinstance(termination, int):
        termination = {"exit": termination}
    elif isinstance(termination, tuple):
        termination = {termination[0]: termination[1]}
    result = {"termination": termination, "requests": list(requests)}
    for key, value in pins.items():
        if value is None:
            continue
        if isinstance(value, bytes):
            value = pin(value)
        elif isinstance(value, list):
            value = [pin(v) if isinstance(v, bytes) else v for v in value]
        result[key] = value
    return result


def text_error(message):
    return (b"Error: " + as_bytes(message) + b"\n")


def status_message(status, reason, preview):
    line = f"{status} {reason}".rstrip(" ") if reason is not None else str(status)
    if reason == "":
        line = f"{status} "
    if preview == b"":
        return f"query failed: server returned {line} with an empty response body".encode(), line
    return f"query failed: server returned {line}: ".encode() + preview, line


_CORPUS = []


def corpus():
    """The fixed corpus (built once per process; callers get an independent copy)."""
    if not _CORPUS:
        _CORPUS.append(classify(_build_corpus()))
    return copy.deepcopy(_CORPUS[0])


def classify(cases):
    """Move accepted residuals into the opt-in known-divergence tier, keeping their Go pins."""
    missing = sorted(set(KNOWN_DIVERGENCES) - {c["name"] for c in cases})
    if missing:
        raise ValueError(f"KNOWN_DIVERGENCES names unknown cases {missing}")
    for case in cases:
        if case["name"] in KNOWN_DIVERGENCES:
            case["tier"] = "known-divergence"
            case["divergence"] = KNOWN_DIVERGENCES[case["name"]]
    return cases


def _build_corpus():
    cases = []

    def add(name, argv, expectation, *, group, config=None, routes=None, auth="basic", fixture=None, build="standard",
            requires=(), tier="gate", io=None, actions=None, reader=None, env=None, unset=(), compare=None,
            timeout=None, stdout_cap=None, alias=False, scenario_headers=None):
        if config is None:
            config = {"auth": auth}
        elif config == "none":
            config = None
        if fixture is None and routes is not None:
            fixture = {"headers": HEADERS[auth] if scenario_headers is None else scenario_headers, "routes": routes}
            if alias:
                fixture["alias"] = True
        case = {"name": "p3-" + name, "group": "phase3-" + group, "tier": tier, "argv": argv, "config": config,
                "location": "xdg", "fixture": fixture, "build": build,
                "requires": sorted(set(requires) | ({"loopback-alias"} if alias else set())), "expect": expectation,
                "env": env or {}, "unset": list(unset)}
        for key, value in (("io", io), ("actions", actions), ("reader", reader), ("compare", compare),
                           ("timeout", timeout), ("stdout_cap", stdout_cap)):
            if value is not None:
                case[key] = value
        if tier == "full60s":
            case["timeout"] = FULL60_TIMEOUT
            case["compare"] = {"timeout_cause": True, **case.get("compare", {})}
            expectation.setdefault("min_wall_ms", 59000)
            expectation.setdefault("max_wall_ms", 70000)
        cases.append(case)
        return case

    def query_case(name, query, *, group="rewrite", start=START, end=END, extra=(), argv=None, out=None, **options):
        body = request_body(query, start, end)
        argv = argv or ["sql", "run", query, "--from", start, "--to", end, *extra]
        return add(name, argv, expect(0, request_bodies={"0": pin(body)},
                                      stdout_exact=out or b"Query succeeded: no rows returned (status: 200 OK).\n",
                                      stderr_exact=b"", config="unchanged"),
                   group=group, routes=[route(b"[]")], **options)

    # ----- request preparation: lexical rewrite (named regressions) --------------------------
    named_rewrites = {
        "upstream-shell-stripped": "SELECT * FROM astronomy-shop-logs WHERE StatusCode = 'FailedPrecondition' AND service.name = 'cart' LIMIT 500",
        "underscore-dataset": "SELECT * FROM fly_logs LIMIT 100",
        "join-unsafe-dataset": "SELECT * FROM fly_logs JOIN claudecode-logs ON fly_logs.id = claudecode-logs.id",
        "dotted-dataset": "SELECT * FROM metrics.v1 LIMIT 100",
        "leading-digit-dataset": "SELECT * FROM 123logs LIMIT 100",
        "quoted-left-alone": "SELECT * FROM \"astronomy-shop-logs\" WHERE \"StatusCode\" = 'FailedPrecondition' AND \"service.name\" = 'cart'",
        "keywords-functions": "SELECT COUNT(StatusCode) FROM astronomy-shop-logs WHERE StatusCode IS NOT NULL GROUP BY service.name LIMIT 10",
        "explicit-limit-above-500": "SELECT * FROM logs LIMIT 100000",
        "lowercase-limit-tab": "select * from logs limit\t7",
        "limit-only-in-subquery": "SELECT * FROM (SELECT * FROM logs LIMIT 5) t",
        "limit-in-string": "SELECT 'LIMIT 3' FROM logs",
        "limit-in-line-comment": "SELECT * FROM logs -- LIMIT 3\n",
        "limit-in-block-comment": "SELECT * FROM logs /* LIMIT 3 */",
        "limit-quoted-identifier": "SELECT \"limit\" FROM logs",
        "limited-identifier": "SELECT limited FROM logs",
        "dotted-limit-hidden-by-quoting": "SELECT t.limit FROM logs t",
        "trailing-semicolon": "SELECT * FROM logs;",
        "double-semicolon": "SELECT * FROM logs;;",
        "semicolon-then-space": "SELECT * FROM logs ;  \n",
        "trailing-whitespace": "  SELECT * FROM logs \n\t ",
        "trailing-nbsp-unicode-trim": "SELECT * FROM logs\u00a0\u3000",
        "explicit-limit-keeps-whitespace": "  SELECT * FROM logs LIMIT 5  \n",
        "trailing-line-comment": "SELECT * FROM logs -- note",
        "trailing-bare-double-dash": "SELECT * FROM logs --",
        "trailing-comment-crlf": "SELECT * FROM logs -- note\r",
        "comment-then-newline-statement": "SELECT * FROM logs -- note\nWHERE a = 1",
        "comment-with-quote-char": "SELECT * FROM logs -- it's",
        "block-comment-end": "SELECT * FROM logs /* c */",
        "unclosed-block-comment-double-append": "SELECT * FROM logs /* c",
        "unclosed-string-double-append": "SELECT * FROM logs WHERE a = 'abc",
        "unclosed-paren-double-append": "SELECT * FROM (SELECT 1",
        "unbalanced-close-paren": "SELECT 1) FROM logs",
        "unclosed-quoted-identifier": "SELECT \"abc FROM logs",
        "comment-rewritten": "SELECT 1 /* FROM a-b MixedCase */ FROM x-y",
        "line-comment-rewritten": "SELECT 1 -- FROM a-b Foo\n FROM x-y",
        "cte": "WITH a AS (SELECT * FROM x-y) SELECT * FROM a",
        "from-subquery-paren": "SELECT * FROM (SELECT * FROM logs-1) q",
        "from-string-literal": "SELECT * FROM 'logs-1'",
        "from-dataset-placeholder": "SELECT * FROM dataset",
        "from-dataset-uppercase": "SELECT * FROM DATASET",
        "from-dataset-long-s-fold": "SELECT * FROM dataſet",
        "from-embedded-quote": "SELECT * FROM a\"b",
        "from-at-end": "SELECT * FROM",
        "from-then-whitespace-only": "SELECT * FROM \n",
        "comma-separated-tables": "SELECT * FROM\tlogs-1,\nlogs-2",
        "join-quoted": "SELECT * FROM a JOIN \"b-c\" ON a.x = \"b-c\".y",
        "prefix-not-keyword": "SELECT xfrom FROM y-z",
        "non-ascii-before-keyword": "SELECT 1 éFROM a-b",
        "keyword-mixed-case": "SeLeCt * FrOm Logs WhErE Level = 'x'",
        "mixed-case-function-call": "SELECT MyFunc(x), MyFunc (y), Count FROM logs",
        "dotted-chain": "SELECT a.b.c, a.b., a..b, x1.y2, _Under.score FROM logs",
        "numbers-not-identifiers": "SELECT 1.5, 2e3, 0x1F FROM logs",
        "dotted-function": "SELECT a.b(1) FROM logs",
        "escaped-quote-in-literal": "SELECT 'it''s FROM a-b' FROM logs",
        "doubled-quote-identifier": "SELECT \"a\"\"b\" FROM logs",
        "unicode-identifiers": "SELECT 日本, Ünïcode FROM logs-é",
        "html-and-line-separators": "SELECT '<&>\u2028\u2029' FROM logs",
        "control-characters": "SELECT '\x01\x7f\b\f\t' FROM logs",
        "emoji": "SELECT '😀' FROM logs",
        "zero-width-space-only": "\u200b",
        "only-limit-keyword": "LIMIT",
        "leading-line-comment": "-- just a comment",
    }
    for label, text in named_rewrites.items():
        if text.startswith("-"):
            query_case(f"rewrite-{label}", text, argv=["sql", "run", "--from", START, "--to", END, "--", text])
        else:
            query_case(f"rewrite-{label}", text)
    query_case("rewrite-long-64k", "SELECT * FROM logs WHERE m IN (" + ",".join(f"'v{i:05d}'" for i in range(7000)) + ")")

    # ----- time forwarding: raw strings, only exact empty values become defaults -------------
    for label, flags, start, end in (
            ("defaults-absent", [], "1m", "now"),
            ("explicit-empty", ["--from", "", "--to", ""], "1m", "now"),
            ("durations", ["--from", "10m", "--to", "now"], "10m", "now"),
            ("offset-rfc3339", ["--from", "2024-01-01T00:00:00+05:30", "--to", "2024-01-02T00:00:00.123456789Z"],
             "2024-01-01T00:00:00+05:30", "2024-01-02T00:00:00.123456789Z"),
            ("unparsed-strings-forwarded", ["--from", "yesterday", "--to", "tomorrow"], "yesterday", "tomorrow"),
            ("spaces-preserved", ["--from", " 10m ", "--to", " now "], " 10m ", " now "),
            ("escaped-characters", ["--from", "<&>\u2028é\t\"\\", "--to", "日本"], "<&>\u2028é\t\"\\", "日本"),
            ("repeated-flags-last-wins", ["--from", "2h", "--to", "1m", "--from", "1h", "--to", "now"], "1h", "now")):
        body = request_body("SELECT * FROM logs", start, end)
        add(f"time-{label}", ["sql", "run", "SELECT * FROM logs", *flags],
            expect(0, request_bodies={"0": pin(body)}, stderr_exact=b""), group="time", routes=[route(b"[]")])
    for label, argv, start, end in (
            ("short-flags", ["sql", "run", "SELECT 1", "-f", "5m", "-t", "now"], "5m", "now"),
            ("short-equals-is-literal-equals", ["sql", "run", "SELECT 1", "-f=", "-t="], "=", "="),
            ("long-equals", ["sql", "run", "--from=3h", "--to=2024-01-01T00:00:00Z", "SELECT 1"], "3h", "2024-01-01T00:00:00Z")):
        add(f"time-{label}", argv, expect(0, request_bodies={"0": pin(request_body("SELECT 1", start, end))}),
            group="time", routes=[route(b"[]")])

    # ----- validation precedence (ordinary run) --------------------------------------------
    none = []
    for label, query in (("empty", ""), ("spaces", "   "), ("tab-newline", "\t\n"), ("nbsp-unicode-space", "\u00a0\u2003")):
        add(f"validate-required-{label}-text", sql(query), expect(1, none, stdout_exact=b"", stderr_exact=text_error(REQUIRED)),
            group="validation", routes=[route()])
        add(f"validate-required-{label}-json", sql(query, "-o", "json"),
            expect(1, none, stdout_exact=b"", stderr_json=phase2.error_json("INVALID_INPUT", REQUIRED)),
            group="validation", routes=[route()])
    yaml_error = 'failed to get \'output\' flag: unsupported output format "yaml" (expected text or json)'
    for label, argv, pins in (
            ("no-args", ["sql", "run"], {"stderr_exact": text_error(REQUIRED)}),
            ("no-args-json", ["sql", "run", "-o", "json"], {"stderr_json": phase2.error_json("INVALID_INPUT", REQUIRED)}),
            ("required-before-output", sql("", "-o", "yaml"), {"stderr_exact": text_error(REQUIRED)}),
            ("required-before-save-guard", sql("  ", "--save-as", "named"), {"stderr_exact": text_error(REQUIRED)}),
            ("interactive-false-required", sql("", "--interactive=false"), {"stderr_exact": text_error(REQUIRED)}),
            ("bad-output", sql("SELECT 1", "-o", "yaml"), {"stderr_exact": text_error(yaml_error)}),
            ("bad-output-before-save-guard", sql("SELECT 1", "-o", "yaml", "--save-as", "named"), {"stderr_exact": text_error(yaml_error)}),
            ("bad-output-interactive-false", sql("SELECT 1", "-i=false", "-o", "yaml"), {"stderr_exact": text_error(yaml_error)}),
            ("bad-output-structured", sql("SELECT 1", "--output=json", "--output=yaml"),
             {"stderr_json": phase2.error_json("INVALID_INPUT", 'unsupported output format "yaml" (expected text or json)')}),
            ("two-args", ["sql", "run", "SELECT 1", "SELECT 2"], {"stderr_has": ["accepts at most 1 arg(s), received 2"]}),
            ("unknown-flag", sql("SELECT 1", "--nope"), {"stderr_has": ["unknown flag: --nope"]})):
        add(f"validate-{label}", argv, expect(1, none, stdout_exact=b"", **pins), group="validation", routes=[route()])
    for label, flags, out in (("output-json-mixed-case-spaces", ["-o", " JSON "], json_output(ROWS)),
                              ("output-text-upper", ["-o", "TEXT"], text_output(ROWS)),
                              ("output-empty-is-text", ["-o", ""], text_output(ROWS)),
                              ("output-attached", ["-ojson"], json_output(ROWS)),
                              ("output-last-wins-text", ["-o", "json", "-o", "text"], text_output(ROWS)),
                              ("interactive-false", ["--interactive=false"], text_output(ROWS)),
                              ("interactive-short-false", ["-i=false", "-o", "json"], json_output(ROWS)),
                              ("save-as-exactly-empty", ["--save-as", ""], text_output(ROWS)),
                              ("save-as-equals-empty", ["--save-as=", "-o", "json"], json_output(ROWS))):
        add(f"validate-{label}", sql("SELECT * FROM logs", *flags),
            expect(0, request_bodies={"0": pin(request_body("SELECT * FROM logs"))}, stdout_exact=out, stderr_exact=b""),
            group="validation", routes=[route(ROWS)])
    add("validate-structured-errors-with-text-output", sql("SELECT 1", "-o", "json", "-o", "text"),
        expect(1, stdout_exact=b"", stderr_json=phase2.error_json("SERVER_ERROR", "query failed: server returned 500 Internal Server Error", 500, True)),
        group="validation", routes=[route(b"boom", 500)])

    # ----- profiles, configuration and request construction --------------------------------
    raw = lambda text, mode=0o600: {"toml": text, "mode": mode}  # noqa: E731
    profile_error = "error initializing default profile: no profile configured. run: pb login"
    add("profile-missing-text", sql("SELECT 1"), expect(1, none, stdout_exact=b"", stderr_exact=text_error(profile_error)),
        group="profile", config="none")
    add("profile-missing-before-required", sql(""), expect(1, none, stdout_exact=b"", stderr_exact=text_error(profile_error)),
        group="profile", config="none")
    add("profile-missing-json", sql("SELECT 1", "-o", "json"),
        expect(1, none, stdout_exact=b"", stderr_json=phase2.error_json("NOT_FOUND", "no profile configured. run: pb login")),
        group="profile", config="none")
    add("profile-unknown-default", sql("SELECT 1"), expect(1, none, stderr_exact=text_error(profile_error)), group="profile",
        config=raw(phase2.config_toml({"fixture": phase2.profile()}, default="missing")))
    add("profile-malformed-toml", sql("SELECT 1"), expect(1, none, stderr_has=["error initializing default profile: toml: "]),
        group="profile", config=raw("DefaultProfile = \n"))
    for label, fields, message in (
            ("both-basic-and-key", {"username": "u", "password": "p", "apiKey": "k"}, "self-hosted profile has both basic credentials and api key"),
            ("cloud-missing-tenant", {"cloud": True, "apiKey": "k"}, "cloud profile missing tenant_id"),
            ("no-credentials", {}, "self-hosted profile missing credentials")):
        add(f"request-construction-{label}-text", sql("SELECT 1"),
            expect(1, none, stdout_exact=b"", stderr_exact=text_error("failed to create new request: " + message)),
            group="request", config=raw(phase2.config_toml({"fixture": {"url": NO_SERVER, **fields}})))
        add(f"request-construction-{label}-json", sql("SELECT 1", "-o", "json"),
            expect(1, none, stdout_exact=b"", stderr_json=phase2.error_json("COMMAND_FAILED", "failed to create new request: " + message)),
            group="request", config=raw(phase2.config_toml({"fixture": {"url": NO_SERVER, **fields}})))
    refused = 'request execution failed: Post "http://127.0.0.1:1/api/v1/query": dial tcp 127.0.0.1:1: connect: connection refused'
    add("request-connection-refused-text", sql("SELECT 1"), expect(1, none, stdout_exact=b"", stderr_exact=text_error(refused)),
        group="request", config=raw(phase2.config_toml({"fixture": phase2.profile(url=NO_SERVER)})))
    add("request-connection-refused-json", sql("SELECT 1", "-o", "json"),
        expect(1, none, stdout_exact=b"", stderr_json=phase2.error_json("CONNECTION_FAILED", refused, retryable=True)),
        group="request", config=raw(phase2.config_toml({"fixture": phase2.profile(url=NO_SERVER)})))
    for label, url, message in (
            ("invalid-url-join-fails", "http://[::1", 'request execution failed: Post "": unsupported protocol scheme ""'),
            ("unsupported-scheme", "ftp://fixture.invalid/x", 'request execution failed: Post "ftp://fixture.invalid/x/api/v1/query": unsupported protocol scheme "ftp"')):
        add(f"request-{label}", sql("SELECT 1"), expect(1, none, stdout_exact=b"", stderr_exact=text_error(message)),
            group="request", config=raw(phase2.config_toml({"fixture": phase2.profile(url=url)})))
    for label, url, path in (("path-prefix", "{{url}}/base/", "/base/api/v1/query"),
                             ("dot-segments", "{{url}}/a/../b", "/b/api/v1/query"),
                             ("profile-query-string", "{{url}}/p?tenant=a", "/p/api/v1/query")):
        add(f"request-url-{label}", sql("SELECT 1"), expect(0, ["POST " + path], stdout_exact=text_output(b"[]")),
            group="request", config=raw(phase2.config_toml({"fixture": phase2.profile(url=url)})),
            routes=[route(b"[]", path=path)])
    for mode in AUTH:
        for output in ("text", "json"):
            add(f"auth-{mode}-{output}", sql("SELECT * FROM logs", *(["-o", "json"] if output == "json" else [])),
                expect(0, request_bodies={"0": pin(request_body("SELECT * FROM logs"))},
                       request_headers={"0": {**HEADERS[mode], "content-type": "application/json", "accept-encoding": "gzip"}},
                       stdout_exact=json_output(ROWS) if output == "json" else text_output(ROWS), stderr_exact=b"", config="unchanged"),
                group="auth", auth=mode, routes=[route(ROWS)])
    add("auth-rejected-401-basic", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_exact=text_error("query failed: server returned 401 Unauthorized: fixture: incorrect authentication")),
        group="auth", routes=[route()], scenario_headers=phase2.WRONG)
    add("auth-debug-headers", sql("SELECT 1"), expect(0, stderr_has=[
        f'pb debug request: POST {"http://<FIXTURE>/api/v1/query"}\n', "pb debug header: Authorization: <redacted>\n",
        "pb debug header: Content-Type: application/json\n"]),
        group="auth", env={"PB_DEBUG_HTTP_HEADERS": "1"}, compare={"stderr_unordered_lines": True}, routes=[route()])
    add("auth-large-request-body", sql("SELECT '" + "é<" * 20000 + "' FROM logs"),
        expect(0, request_bodies={"0": pin(request_body("SELECT '" + "é<" * 20000 + "' FROM logs"))}), group="auth",
        routes=[route()])

    # ----- text output: raw bytes ------------------------------------------------------------
    text_bodies = {
        "ascii-no-newline": b"hello", "ascii-newline": b"hello\n", "carriage-return-end": b"a\r",
        "invalid-utf8": b"\xff\xfe raw \xc3\x28 \xe2\x82 end", "truncated-multibyte-end": b"caf\xc3",
        "nul-bytes": b"a\x00b\x00", "empty": b"", "sql-whitespace-only": b" \t\r\n \n", "form-feed-not-space": b"\x0c",
        "vertical-tab-not-space": b"\x0b", "nbsp-not-sql-space": b"\xc2\xa0",
        "empty-array": b"[]", "spaced-empty-array": b"  [ \n ]  \n", "empty-array-trailing-text": b"[] x",
        "empty-array-double-close": b"[]]", "open-bracket-only": b"[", "open-bracket-newline": b"[\n",
        "open-bracket-text": b"[ x", "object": b"{}", "compact-rows": ROWS, "pretty-rows": b'[\n  {"a": 1}\n]\n',
        "leading-whitespace-rows": b"\n\n  " + ROWS, "crlf-lines": b"a\r\nb\r\n", "multiline-no-final-newline": b"a\nb",
    }
    for label, body in text_bodies.items():
        add(f"text-{label}", sql("SELECT * FROM logs"), expect(0, stdout_exact=text_output(body), stderr_exact=b""),
            group="text", routes=[route(body)])
    for label, body in (("empty", b""), ("whitespace", b" \r\n\t"), ("empty-array", b"[ ]")):
        add(f"json-mode-body-{label}", sql("SELECT * FROM logs", "-o", "json"),
            expect(0, stdout_exact=b"No response body returned (status: 200 OK).\n" if label != "empty-array" else b"[]\n",
                   stderr_exact=b""), group="json", routes=[route(body)])
    for label, options, status_text in (("custom-reason", {"reason": "Fine"}, "200 Fine"), ("no-reason", {"reason": None}, "200"),
                                        ("empty-reason", {"reason": ""}, "200 ")):
        add(f"text-status-{label}-empty", sql("SELECT 1"),
            expect(0, stdout_exact=f"No response body returned (status: {status_text}).\n".encode()), group="text",
            routes=[route(b"", **options)])
        add(f"text-status-{label}-no-rows", sql("SELECT 1"),
            expect(0, stdout_exact=f"Query succeeded: no rows returned (status: {status_text}).\n".encode()), group="text",
            routes=[route(b"[]", **options)])
        add(f"json-status-{label}-empty", sql("SELECT 1", "-o", "json"),
            expect(0, stdout_exact=f"No response body returned (status: {status_text}).\n".encode()), group="json",
            routes=[route(b"", **options)])
    big_line = b"y" * (1 << 20)
    add("text-one-mebibyte-line", sql("SELECT 1"), expect(0, stdout_exact=big_line + b"\n"), group="text", routes=[route(big_line)])
    add("text-close-delimited", sql("SELECT 1"), expect(0, stdout_exact=b"abc\n"), group="text",
        routes=[route(b"abc", framing="close")])
    add("text-content-length-zero", sql("SELECT 1"), expect(0, stdout_exact=b"No response body returned (status: 200 OK).\n"),
        group="text", routes=[route(b"")])
    add("text-gzip", sql("SELECT 1"), expect(0, stdout_exact=text_output(ROWS)), group="text", routes=[route(ROWS, gzip=True)])
    add("text-gzip-bad-header", sql("SELECT 1"), expect(1, stdout_exact=b"", stderr_exact=text_error("failed to read response body: gzip: invalid header")),
        group="text", routes=[route(ROWS, gzip=True, gzip_corrupt="header")])
    add("text-gzip-bad-checksum", sql("SELECT 1"), expect(1, stdout_has=[b"[{"], stderr_exact=text_error("failed to stream response body: gzip: invalid checksum")),
        group="text", routes=[route(ROWS, gzip=True, gzip_corrupt="crc")])
    add("text-gzip-truncated", sql("SELECT 1"), expect(1, stderr_has=["unexpected EOF"]), group="text",
        routes=[route(ROWS * 50, gzip=True, gzip_corrupt="truncate")])
    trunc = [{"send": 6}, {"delay_ms": 20}, {"abort": "fin"}]
    add("text-truncated-length", sql("SELECT 1"),
        expect(1, stdout_exact=b"abcdef", stderr_exact=text_error("failed to stream response body: unexpected EOF")),
        group="text", routes=[route(b"abcdefghij", framing="length", script=trunc)])
    add("text-truncated-chunked", sql("SELECT 1"),
        expect(1, stdout_exact=b"abcdef", stderr_exact=text_error("failed to stream response body: unexpected EOF")),
        group="text", routes=[route(b"abcdefghij", framing="chunked", script=trunc)])
    add("text-truncated-in-lookahead", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_exact=text_error("failed to read response body: unexpected EOF")),
        group="text", routes=[route(b"[  \n   ", framing="length", content_length=40, script=[{"send": 4}, {"delay_ms": 20}, {"abort": "fin"}])])
    add("text-truncated-before-first-byte", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_exact=text_error("failed to read response body: unexpected EOF")),
        group="text", routes=[route(b"abc", framing="length", script=[{"abort": "fin"}])])
    add("text-empty-array-then-fault", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_exact=text_error("failed to read response body: unexpected EOF")),
        group="text", routes=[route(b"[] \n", framing="chunked", script=[{"send": 4}, {"delay_ms": 20}, {"abort": "fin"}])])
    add("text-reset-mid-body", sql("SELECT 1"),
        expect(1, stdout_exact=b"abcdef", stderr_has=["failed to stream response body: read tcp <CLIENT>-><FIXTURE-HOST>: read: connection reset by peer"]),
        group="text", routes=[route(b"abcdefghij", framing="chunked", script=[{"send": 6}, {"delay_ms": 200}, {"abort": "rst"}])],
        compare={"client_port": True})
    add("text-malformed-chunk-size", sql("SELECT 1"), expect(1, stderr_has=["failed to"]), group="text",
        routes=[route(b"zz\r\nabc\r\n0\r\n\r\n", framing="close", headers_out=[["Transfer-Encoding", "chunked"]])])
    add("text-close-before-headers", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_exact=text_error('request execution failed: Post "http://<FIXTURE>/api/v1/query": EOF')),
        group="request", routes=[route(close_before_headers="fin")])
    add("text-malformed-status-line", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_has=['request execution failed: Post "http://<FIXTURE>/api/v1/query": ', 'malformed HTTP status code "abc"']),
        group="request", routes=[route(raw_status_line="HTTP/1.1 abc OK")])

    # ----- JSON output: per-element decoding and Go encoding --------------------------------
    json_bodies = {
        "element-types": b'[{"o":{}},[],"s",1,true,false,null,{"n":[1,{"x":[]}]}]',
        "key-sorting-bytes": '[{"b":1,"a":2,"B":3,"_":4,"é":5,"Z":6,"":7,"aa":8}]'.encode(),
        "duplicate-keys-last-wins": b'[{"a":1,"a":2,"n":{"x":1,"x":{"y":2}}}]',
        "numbers": b"[0,-0,1.0,1E2,1e+2,1.5e-3,0.000001,1e-7,1e20,1e21,123456789012345678901234567890,9007199254740993,"
                   b"5e-324,1.7976931348623157e308,0.1,0.30000000000000004,-12.5e1,100000000000000000000]",
        "number-underflow": b"[1e-400,-1e-400]",
        "html-escaping": '[{"<k>":"<script>&amp;</script>"}]'.encode(),
        "line-separators": '["\u2028\u2029", "\\u2028"]'.encode(),
        "control-escapes": b'["\\u0000\\u0001\\b\\f\\n\\r\\t\\u001f\\u007f\\/\\"\\\\"]',
        "unicode-escapes": b'["\\u00e9\\u65e5\\ud83d\\ude00", "\\ud800", "\\udc00x", "\\ud800\\u0041", "\\ud800\\ud800"]',
        "raw-utf8": '["café 日志 😀 \ufffd"]'.encode(),
        "invalid-utf8-in-string": b'["a\xffb", "\xc3\x28", "\xe2\x82x", "\xed\xa0\x80", "\xc0\xaf", "\xf4\x90\x80\x80"]',
        "whitespace-everywhere": b' \r\n\t[ \n{ "a" :\t1 , "b" : [ 1 , 2 ] } \r\n, 3\n]\n',
        "trailing-garbage-accepted": b'[1] garbage',
        "trailing-second-array": b"[1]]  [2]",
        "trailing-object": b'[{"a":1}]\n{',
        "nested-200": b"[" + b"[" * 199 + b"1" + b"]" * 199 + b"]",
        "empty-containers": b"[{},[],{\"a\":{}},[[]]]",
        "big-object": json.dumps([{f"k{i:03d}": i for i in range(300)}]).encode(),
    }
    for label, body in json_bodies.items():
        add(f"json-{label}", sql("SELECT 1", "-o", "json"), expect(0, stdout_exact=json_output(body), stderr_exact=b""),
            group="json", routes=[route(body)])
    decode_error = "error decoding JSON response: "
    malformed = {  # body, Go encoding/json message, partial stdout before the failure
        "object-not-array": (b'{"a":1}', "expected JSON array response", b""),
        "string-not-array": (b'"x"', "expected JSON array response", b""),
        "number-not-array": (b"12", "expected JSON array response", b""),
        "null-not-array": (b"null", "expected JSON array response", b""),
        "garbage": (b"abc", "invalid character 'a' looking for beginning of value", b""),
        "byte-order-mark": (b"\xef\xbb\xbf[1]", "invalid character '\u00ef' looking for beginning of value", b""),
        "form-feed-leading": (b"\x0c[1]", "invalid character '\\f' looking for beginning of value", b""),
        "missing-comma": (b"[1 2]", "expected comma after array element", b"[\n  1"),
        "trailing-comma": (b"[1,]", "invalid character ']' looking for beginning of value", b"[\n  1"),
        "leading-comma": (b"[,1]", "invalid character ',' looking for beginning of value", b"["),
        "double-comma": (b"[1,,2]", "invalid character ',' looking for beginning of value", b"[\n  1"),
        "object-missing-colon": (b'[{"a" 1}]', "invalid character '1' after object key", b"["),
        "object-trailing-comma": (b'[{"a":1,}]', "invalid character '}' looking for beginning of object key string", b"["),
        "unterminated-string": (b'["abc]', "unexpected EOF", b"["),
        "raw-newline-in-string": (b'["a\nb"]', "invalid character '\\n' in string literal", b"["),
        "bad-literal-true": (b"[tru]", "invalid character ']' in literal true (expecting 'e')", b"["),
        "bad-literal-null": (b"[nul]", "invalid character ']' in literal null (expecting 'l')", b"["),
        "leading-zero": (b"[01]", "expected comma after array element", b"[\n  0"),
        "dangling-decimal": (b"[1.]", "invalid character ']' after decimal point in numeric literal", b"["),
        "lone-minus": (b"[-]", "invalid character ']' in numeric literal", b"["),
        "dangling-exponent": (b"[1e]", "invalid character ']' in exponent of numeric literal", b"["),
        "leading-decimal": (b"[.5]", "invalid character '.' looking for beginning of value", b"["),
        "plus-sign": (b"[+1]", "invalid character '+' looking for beginning of value", b"["),
        "nan": (b"[NaN]", "invalid character 'N' looking for beginning of value", b"["),
        "infinity": (b"[Infinity]", "invalid character 'I' looking for beginning of value", b"["),
        "bad-unicode-escape": (b'["\\u12"]', "invalid character '\"' in \\u hexadecimal character escape", b"["),
        "invalid-escape": (b'["\\x"]', "invalid character 'x' in string escape code", b"["),
        "invalid-byte-outside-string": (b"[1,\xff]", "invalid character '\u00ff' looking for beginning of value", b"[\n  1"),
        "eof-after-open": (b"[", "EOF", b"["),
        "eof-after-element": (b"[1", "EOF", b"[\n  1"),
        "eof-after-comma": (b"[1,", "EOF", b"[\n  1"),
        "eof-in-object": (b'[{"a":', "unexpected EOF", b"["),
        "float-overflow": (b"[1,1e400]", "json: cannot unmarshal number 1e400 into Go value of type float64", b"[\n  1"),
        "negative-float-overflow": (b"[-1e400]", "json: cannot unmarshal number -1e400 into Go value of type float64", b"["),
        "partial-after-elements": (b'[{"a":1},{"b":2},oops]', "invalid character 'o' looking for beginning of value",
                                   b'[\n  {\n    "a": 1\n  },\n  {\n    "b": 2\n  }'),
        "element-depth-limit-exceeded": (b"[" * 10002 + b"]" * 10002, "invalid character '[' exceeded max depth", b"["),
        "mismatched-close": (b"[{]", "invalid character ']' looking for beginning of object key string", b"["),
    }
    for label, (body, message, prefix) in malformed.items():
        add(f"json-malformed-{label}", sql("SELECT 1", "-o", "json"),
            expect(1, stdout_exact=prefix, stderr_json=phase2.error_json("COMMAND_FAILED", decode_error + message)),
            group="json-errors", routes=[route(body)])
        if label in ("missing-comma", "eof-after-element", "partial-after-elements", "garbage"):
            add(f"text-mode-error-{label}", sql("SELECT 1"), expect(0, stdout_exact=text_output(body)), group="text",
                routes=[route(body)])
    add("json-malformed-text-mode-error-message", ["sql", "run", "SELECT 1", "-o", "json", "--output", "json"],
        expect(1, stdout_exact=b"[\n  1", stderr_json=phase2.error_json("COMMAND_FAILED", decode_error + "expected comma after array element")),
        group="json-errors", routes=[route(b"[1 2]")])
    add("json-malformed-structured-error", sql("SELECT 1", "-o", "json"),
        expect(1, stdout_exact=b"[\n  1", stderr_has=['"code": "COMMAND_FAILED"', '"retryable": false', "error decoding JSON response: "]),
        group="json-errors", routes=[route(b"[1 2]")])
    add("json-gzip", sql("SELECT 1", "-o", "json"), expect(0, stdout_exact=json_output(ROWS)), group="json", routes=[route(ROWS, gzip=True)])
    add("json-gzip-bad-checksum-after-array", sql("SELECT 1", "-o", "json"), expect(0, stdout_exact=json_output(ROWS)),
        group="json", routes=[route(ROWS, gzip=True, gzip_corrupt="crc")])
    add("json-truncated-after-elements", sql("SELECT 1", "-o", "json"),
        expect(1, stdout_exact=b'[\n  {\n    "a": 1\n  }', stderr_json=phase2.error_json("COMMAND_FAILED", decode_error + "unexpected EOF")),
        group="json-errors", routes=[route(b'[{"a":1},{"b":', framing="chunked", script=[{"send": "rest"}, {"delay_ms": 20}, {"abort": "fin"}])])
    add("json-reset-mid-body", sql("SELECT 1", "-o", "json"),
        expect(1, stdout_exact=b'[\n  {\n    "a": 1\n  }', stderr_has=["connection reset by peer"]),
        group="json-errors", compare={"client_port": True},
        routes=[route(b'[{"a":1},{"b":', framing="chunked", script=[{"send": "rest"}, {"delay_ms": 200}, {"abort": "rst"}])])
    add("json-10000-rows", sql("SELECT * FROM logs LIMIT 10000", "-o", "json"), expect(0, **model_digest(rendered("json", rows_spec(10000)))),
        group="large", routes=[route(None, generate=rows_spec(10000))])
    add("text-10000-rows", sql("SELECT * FROM logs LIMIT 10000"), expect(0, **model_digest(rendered("text", rows_spec(10000)))),
        group="large", routes=[route(None, generate=rows_spec(10000))])
    add("json-100000-rows", sql("SELECT * FROM logs LIMIT 100000", "-o", "json"), expect(0, **model_digest(rendered("json", rows_spec(100000)))),
        group="large", routes=[route(None, generate=rows_spec(100000))], timeout=30)
    blob = {"prefix": '[{"sequence":0,"blob":"', "item": "{{blob}}", "count": 1, "blob_bytes": 8 << 20, "suffix": '"}]'}
    add("json-one-large-element", sql("SELECT 1", "-o", "json"), expect(0, **model_digest(rendered("json", blob))),
        group="large", routes=[route(None, generate=blob)], timeout=30)
    add("text-one-large-element", sql("SELECT 1"), expect(0, **model_digest(rendered("text", blob))),
        group="large", routes=[route(None, generate=blob)], timeout=30)
    spaces = {"prefix": " " * (4 << 20) + "[", "item": '{"sequence":{{i}}}', "separator": ",", "count": 3, "suffix": "]"}
    add("json-long-leading-whitespace", sql("SELECT 1", "-o", "json"), expect(0, **model_digest(rendered("json", spaces))),
        group="large", routes=[route(None, generate=spaces)], timeout=30)
    add("text-long-leading-whitespace", sql("SELECT 1"), expect(0, **model_digest(rendered("text", spaces))),
        group="large", routes=[route(None, generate=spaces)], timeout=30)

    # ----- non-200 statuses and error previews -----------------------------------------------
    for status in (400, 401, 403, 404, 408, 409, 418, 422, 429, 500, 502, 503, 504, 201, 299, 300):
        reason = REASONS[status]
        message, line = status_message(status, reason, b"server says <no> \xc3\xa9")
        code, retry = http_code(status)
        routes = [route(b"  server says <no> \xc3\xa9 \n", status)]
        add(f"status-{status}-text", sql("SELECT 1"), expect(1, stdout_exact=b"", stderr_exact=text_error(message)),
            group="status", routes=routes)
        add(f"status-{status}-json", sql("SELECT 1", "-o", "json"),
            expect(1, stdout_exact=b"", stderr_json=phase2.error_json(code, f"query failed: server returned {line}", status, retry)),
            group="status", routes=routes)
    for status in (204, 304, 503):
        message, line = status_message(status, REASONS[status], b"")
        add(f"status-{status}-empty-body", sql("SELECT 1"), expect(1, stdout_exact=b"", stderr_exact=text_error(message)),
            group="status", routes=[route(b"" if status != 503 else b" \n\t ", status)])
    for label, options, reason in (("custom-reason", {"reason": "Kaput Now"}, "Kaput Now"), ("no-reason", {"reason": None}, None),
                                   ("empty-reason", {"reason": ""}, "")):
        message, line = status_message(500, reason, b"boom")
        add(f"status-{label}-text", sql("SELECT 1"), expect(1, stderr_exact=text_error(message)), group="status",
            routes=[route(b"boom", 500, **options)])
        add(f"status-{label}-json", sql("SELECT 1", "-o", "json"),
            expect(1, stderr_json=phase2.error_json("SERVER_ERROR", f"query failed: server returned {line}", 500, True)),
            group="status", routes=[route(b"boom", 500, **options)])
        empty_message, _ = status_message(503, reason, b"")
        add(f"status-{label}-empty-body", sql("SELECT 1"), expect(1, stderr_exact=text_error(empty_message)), group="status",
            routes=[route(b"", 503, **options)])
    previews = {
        "exactly-limit": b"e" * PREVIEW_LIMIT, "limit-plus-one": b"e" * (PREVIEW_LIMIT + 1),
        "large": bytes(range(65, 91)) * 4000, "trailing-space-at-cut": b"e" * (PREVIEW_LIMIT - 3) + b"   tail",
        "multibyte-cut-at-limit": b"e" * (PREVIEW_LIMIT - 1) + "é".encode() + b"more",
        "unicode-whitespace-trimmed": "\u00a0\u3000 boom \u2028\u0085".encode(), "invalid-utf8": b"\xff boom \xfe",
        "nul-and-controls": b"a\x00b\x1b[31mred", "whitespace-only": b" \r\n\t",
    }
    for label, body in previews.items():
        message, _ = status_message(500, "Internal Server Error", error_preview(body))
        add(f"preview-{label}-text", sql("SELECT 1"), expect(1, stdout_exact=b"", stderr_exact=text_error(message)),
            group="preview", routes=[route(body, 500)])
    add("preview-large-json-excludes-body", sql("SELECT 1", "-o", "json"),
        expect(1, stderr_json=phase2.error_json("SERVER_ERROR", "query failed: server returned 500 Internal Server Error", 500, True)),
        group="preview", routes=[route(b"x" * 100000, 500)])
    gz = b"g" * 70000
    add("preview-gzip-decompressed-limit", sql("SELECT 1"),
        expect(1, stderr_exact=text_error(status_message(502, "Bad Gateway", error_preview(gz))[0])), group="preview",
        routes=[route(gz, 502, gzip=True)])
    add("preview-truncated-body-text", sql("SELECT 1"),
        expect(1, stdout_exact=b"", stderr_exact=text_error("failed to read error response body: unexpected EOF")), group="preview",
        routes=[route(b"partial error", 500, framing="length", content_length=100, script=[{"send": "rest"}, {"delay_ms": 20}, {"abort": "fin"}])])
    add("preview-truncated-body-json", sql("SELECT 1", "-o", "json"),
        expect(1, stdout_exact=b"", stderr_json=phase2.error_json("COMMAND_FAILED", "failed to read error response body: unexpected EOF")),
        group="preview", routes=[route(b"partial error", 500, framing="length", content_length=100,
                                       script=[{"send": "rest"}, {"delay_ms": 20}, {"abort": "fin"}])])
    for label, size in (("over-limit", 70000), ("limit-plus-one", PREVIEW_LIMIT + 1)):
        body = b"s" * size
        add(f"preview-early-completion-{label}", sql("SELECT 1"),
            expect(1, stdout_exact=b"", stderr_exact=text_error(status_message(500, "Internal Server Error", error_preview(body))[0]),
                   max_wall_ms=5000),
            group="early", routes=[route(body, 500, framing="chunked", script=[{"send": "rest"}, {"stall": True}])])

    # ----- redirects ---------------------------------------------------------------------------
    for status in (307, 308):
        add(f"redirect-{status}-replays-body", sql("SELECT * FROM logs"),
            expect(0, [QUERY, "POST /v2/api/v1/query"], request_bodies={"0": pin(request_body("SELECT * FROM logs")),
                                                                     "1": pin(request_body("SELECT * FROM logs"))},
                   stdout_exact=text_output(ROWS)), group="redirect",
            routes=[route(b"", status, headers_out=[["Location", "{{url}}/v2/api/v1/query"]]), route(ROWS, path="/v2/api/v1/query")])
    for status in (301, 302, 303):
        add(f"redirect-{status}-becomes-get", sql("SELECT * FROM logs"),
            expect(0, [QUERY, "GET /v2/api/v1/query"], request_bodies={"1": ""}, stdout_exact=text_output(ROWS)),
            group="redirect", routes=[route(b"", status, headers_out=[["Location", "/v2/api/v1/query"]]),
                                      route(ROWS, method="GET", path="/v2/api/v1/query")])
    add("redirect-loop-stops", sql("SELECT 1"),
        expect(1, [QUERY] * 10, stdout_exact=b"",
               stderr_exact=text_error('request execution failed: Post "/api/v1/query": stopped after 10 redirects')),
        group="redirect", routes=[route(b"", 307, headers_out=[["Location", "/api/v1/query"]])])
    add("redirect-then-error", sql("SELECT 1"),
        expect(1, [QUERY, "POST /v2/api/v1/query"], stderr_exact=text_error("query failed: server returned 503 Service Unavailable: later")),
        group="redirect", routes=[route(b"", 307, headers_out=[["Location", "/v2/api/v1/query"]]), route(b"later", 503, path="/v2/api/v1/query")])
    add("redirect-cross-host-drops-authorization", sql("SELECT 1"),
        expect(1, [QUERY, QUERY], stderr_exact=text_error("query failed: server returned 401 Unauthorized: fixture: incorrect authentication")),
        group="redirect", alias=True, routes=[route(b"", 307, headers_out=[["Location", "{{alias_url}}/api/v1/query"]])])
    add("redirect-cross-host-keeps-api-key", sql("SELECT 1"),
        expect(0, [QUERY, QUERY], request_headers={"1": {"x-api-key": "fixture-key"}}, stdout_exact=text_output(b"[]")),
        group="redirect", alias=True, auth="api-key",
        routes=[route(b"", 307, headers_out=[["Location", "{{alias_url}}/api/v1/query"]], times=1), route(b"[]")])

    # ----- OAuth refresh and replay ----------------------------------------------------------
    cookie_profile = {"url": "{{url}}", "cloud": True, "session_token": "fixture-session", "refresh_token": "fixture-refresh",
                      "tenant_id": "fixture-tenant", "orchestrator_url": "{{url}}/orch"}
    refresh_config = raw(phase2.config_toml({"fixture": cookie_profile}))
    old = {"cookie": "session=fixture-session", "x-p-tenant": "fixture-tenant"}
    new = {"cookie": "session=rotated-session", "x-p-tenant": "fixture-tenant"}
    grant = {"authorization": "Bearer " + ORCHESTRATOR_TOKEN, "content-type": "application/json", "accept": "application/json"}
    token = "/orch/api/v1/cli/oauth/token"
    rotated = json.dumps({"access_token": "rotated-session", "refresh_token": "rotated-refresh"}).encode()
    persisted = {"profiles": {"fixture": {"session_token": "rotated-session", "refresh_token": "rotated-refresh"}}}
    body = request_body("SELECT * FROM logs")

    def refreshing(final, token_route=None, first_path="/api/v1/query"):
        return [route(b"expired", 401, path=first_path, headers=old, times=1),
                token_route or route(rotated, path=token, headers=grant, times=1), *final]

    for output in ("text", "json"):
        flags = ["-o", "json"] if output == "json" else []
        add(f"refresh-replays-{output}", sql("SELECT * FROM logs", *flags),
            expect(0, [QUERY, "POST " + token, QUERY], request_bodies={"0": pin(body), "2": pin(body)},
                   request_headers={"2": new}, stdout_exact=json_output(ROWS) if output == "json" else text_output(ROWS),
                   config=persisted),
            group="refresh", build="cloud", config=refresh_config, auth="cloud-cookie", scenario_headers={},
            routes=refreshing([route(ROWS, headers=new, framing="chunked", chunk_bytes=5)]))
    add("refresh-retry-still-unauthorized", sql("SELECT 1"),
        expect(1, [QUERY, "POST " + token, QUERY], stderr_exact=text_error("query failed: server returned 401 Unauthorized: still expired"),
               config=persisted),
        group="refresh", build="cloud", config=refresh_config, scenario_headers={},
        routes=refreshing([route(b"still expired", 401, headers=new)]))
    add("refresh-grant-rejected", sql("SELECT 1"),
        expect(1, [QUERY, "POST " + token], stderr_exact=text_error(
            'request execution failed: Post "http://<FIXTURE>/api/v1/query": cloud session refresh failed: '
            'cloud session refresh failed: 400 Bad Request: {"error":"invalid_grant"}'), config="unchanged"),
        group="refresh", build="cloud", config=refresh_config, scenario_headers={},
        routes=refreshing([], route(b'{"error":"invalid_grant"}\n', 400, path=token, headers=grant)))
    add("refresh-missing-tokens-json", sql("SELECT 1", "-o", "json"),
        expect(1, [QUERY, "POST " + token], config="unchanged",
               stderr_has=['"code": "CONNECTION_FAILED"', "cloud session refresh response missing access_token or refresh_token"]),
        group="refresh", build="cloud", config=refresh_config, scenario_headers={},
        routes=refreshing([], route(b'{"access_token":"x"}', path=token, headers=grant)))
    add("refresh-persist-failure", sql("SELECT 1"),
        expect(1, [QUERY, "POST " + token], stderr_has=["failed to persist refreshed cloud tokens: "], config="unchanged"),
        group="refresh", build="cloud", config=raw(phase2.config_toml({"fixture": cookie_profile}), mode=0o400),
        requires=["nonroot"], scenario_headers={}, routes=refreshing([]))
    add("refresh-after-redirect", sql("SELECT * FROM logs"),
        expect(0, [QUERY, "POST /b/api/v1/query", "POST " + token, "POST /b/api/v1/query"],
               request_bodies={"3": pin(body)}, stdout_exact=text_output(ROWS), config=persisted),
        group="refresh", build="cloud", config=refresh_config, scenario_headers={},
        routes=[route(b"", 307, headers=old, headers_out=[["Location", "/b/api/v1/query"]]),
                *refreshing([route(ROWS, path="/b/api/v1/query", headers=new)], first_path="/b/api/v1/query")])
    add("refresh-unconfigured-standard-build", sql("SELECT 1"),
        expect(1, [QUERY], stderr_exact=text_error('request execution failed: Post "http://<FIXTURE>/api/v1/query": cloud session refresh '
                                                   "failed: cloud orchestrator auth token is not configured"), config="unchanged"),
        group="refresh", config=refresh_config, scenario_headers={}, routes=[route(b"expired", 401, headers=old)])
    add("refresh-not-for-cloud-key", sql("SELECT 1"),
        expect(1, [QUERY], stderr_exact=text_error("query failed: server returned 401 Unauthorized: denied"), config="unchanged"),
        group="refresh", build="cloud", scenario_headers={},
        config=raw(phase2.config_toml({"fixture": {**phase2.profile("cloud-key"), "refresh_token": "r", "orchestrator_url": "{{url}}/orch"}})),
        routes=[route(b"denied", 401)])

    # ----- streaming causality, early completion, chunking and backpressure ----------------
    first = b'[{"sequence":0,"message":"first"},'
    rest = b'{"sequence":1,"message":"second"}]'
    gated = [{"send": len(first)}, {"barrier": "rest"}, {"send": "rest"}]
    for output in ("json", "text"):
        flags = ["-o", "json"] if output == "json" else []
        condition = {"stdout_elements": 1} if output == "json" else {"stdout_contains": pin(first)}
        add(f"causal-first-element-{output}", sql("SELECT 1", *flags),
            expect(0, stdout_exact=json_output(first + rest) if output == "json" else text_output(first + rest), output_before_completion=True),
            group="causal", routes=[route(first + rest, framing="chunked", script=gated)],
            actions=[{"when": condition, "do": {"release": "rest"}, "within_ms": 5000}])
    add("causal-after-leading-whitespace-json", sql("SELECT 1", "-o", "json"),
        expect(0, stdout_exact=json_output(b"  \n" + first + rest), output_before_completion=True),
        group="causal", routes=[route(b"  \n" + first + rest, framing="chunked", script=[{"send": 3 + len(first)}, {"barrier": "rest"}])],
        actions=[{"when": {"stdout_elements": 1}, "do": {"release": "rest"}}])
    add("causal-headers-before-body-json", sql("SELECT 1", "-o", "json"),
        expect(0, stdout_exact=json_output(first + rest)), group="causal",
        routes=[route(first + rest, framing="chunked", script=[{"barrier": "body"}, {"send": "rest"}])],
        actions=[{"when": {"fixture": "barrier:body"}, "do": {"release": "body"}}])
    for label, body, output in (("json-array-closed", b"[1,2]", "json"), ("json-trailing-content", b'[{"a":1}] trailing', "json"),
                                ("json-empty-array", b"[]", "json")):
        add(f"early-completion-{label}", sql("SELECT 1", "-o", "json"),
            expect(0, stdout_exact=json_output(body), stderr_exact=b"", max_wall_ms=5000), group="early",
            routes=[route(body, framing="chunked", script=[{"send": "rest"}, {"stall": True}])])
    add("early-completion-text-no-rows-waits-for-eof", sql("SELECT 1"),
        expect(0, stdout_exact=b"Query succeeded: no rows returned (status: 200 OK).\n"), group="early",
        routes=[route(b"[ ]  ", framing="chunked", script=[{"send": "rest"}, {"delay_ms": 400}])])
    split_body = '[{"s":"é日😀\\u00e9\\n","n":-12.5e-3,"t":true,"f":false,"z":null},{"k":12345678901234567890}]'.encode()
    for output in ("json", "text"):
        add(f"chunks-one-byte-{output}", sql("SELECT 1", *(["-o", "json"] if output == "json" else [])),
            expect(0, stdout_exact=json_output(split_body) if output == "json" else text_output(split_body)), group="chunking",
            routes=[route(split_body, framing="chunked", chunk_bytes=1, chunk_delay_ms=1)])
    add("chunks-oversized-frame", sql("SELECT 1", "-o", "json"), expect(0, **model_digest(rendered("json", rows_spec(20000)))),
        group="chunking", routes=[route(None, generate=rows_spec(20000), framing="chunked", script=[{"send": "rest"}])])
    for output in ("json", "text"):
        add(f"slow-reader-{output}", sql("SELECT * FROM logs LIMIT 10000", *(["-o", "json"] if output == "json" else [])),
            expect(0, **model_digest(rendered(output, rows_spec(10000)))), group="backpressure",
            routes=[route(None, generate=rows_spec(10000), chunk_bytes=16384)], reader={"chunk": 4096, "delay_ms": 1}, timeout=60)
    blocked = {"prefix": "", "item": "z" * 1023 + "\n", "count": 48 * 1024}
    add("blocked-reader-bounded-text", sql("SELECT 1"), expect(0, **model_digest(rendered("text", blocked))),
        group="backpressure", routes=[route(None, generate=blocked, framing="chunked", chunk_bytes=65536)], timeout=60,
        actions=[{"when": {"start": True}, "do": {"pause_stdout": True}},
                 {"when": {"after_ms": 1500}, "do": {"snapshot_fixture": "blocked"}, "within_ms": 3000},
                 {"when": {"start": True}, "do": {"resume_stdout": True}}],
        compare={"blocked_bytes_margin": BLOCKED_BYTES_MARGIN})

    # ----- signals and broken pipes ------------------------------------------------------------
    for output in ("text", "json"):
        flags = ["-o", "json"] if output == "json" else []
        add(f"signal-sigint-awaiting-headers-{output}", sql("SELECT 1", *flags), expect(("signal", 2), stdout_exact=b"", stderr_exact=b""),
            group="signals", routes=[route(b"[]", header_barrier="never")],
            actions=[{"when": {"fixture": "barrier:never"}, "do": {"signal": "SIGINT"}}])
        add(f"signal-sigint-awaiting-body-{output}", sql("SELECT 1", *flags),
            expect(("signal", 2), stdout_exact=(b'[\n  {\n    "message": "first",\n    "sequence": 0\n  }' if output == "json" else first)),
            group="signals", routes=[route(first + rest, framing="chunked", script=gated)],
            actions=[{"when": {"stdout_elements": 1} if output == "json" else {"stdout_contains": pin(first)}, "do": {"signal": "SIGINT"}}])
        add(f"signal-sigterm-awaiting-body-{output}", sql("SELECT 1", *flags), expect(("signal", 15)),
            group="signals", routes=[route(first + rest, framing="chunked", script=gated)],
            actions=[{"when": {"stdout_elements": 1} if output == "json" else {"stdout_contains": pin(first)}, "do": {"signal": "SIGTERM"}}])
        add(f"broken-pipe-after-first-element-{output}", sql("SELECT 1", *flags), expect(("signal", 13), stderr_exact=b""),
            group="signals", routes=[route(first + rest * 400, framing="chunked", script=gated)],
            actions=[{"when": {"stdout_elements": 1} if output == "json" else {"stdout_contains": pin(first)}, "do": {"close": "stdout"}},
                     {"when": {"start": True}, "do": {"release": "rest"}}])
        add(f"broken-pipe-before-output-{output}", sql("SELECT 1", *flags), expect(("signal", 13), stderr_exact=b""),
            group="signals", routes=[route(b"[]" if output == "text" else ROWS, delay_ms=200)],
            actions=[{"when": {"start": True}, "do": {"close": "stdout"}}])
    add("broken-pipe-stderr-error", sql("SELECT 1"), expect(("signal", 13), stdout_exact=b""), group="signals",
        routes=[route(b"boom", 500, delay_ms=200)], actions=[{"when": {"start": True}, "do": {"close": "stderr"}}])

    # ----- output-device failures (/dev/full) ---------------------------------------------------
    full = {"stdout": "devfull"}
    nospace = "write /dev/stdout: no space left on device"
    add("devfull-text-raw", sql("SELECT 1"), expect(1, stderr_exact=text_error("failed to stream response body: " + nospace)),
        group="devices", io=full, requires=["devfull"], routes=[route(ROWS)])
    add("devfull-text-no-rows-ignored", sql("SELECT 1"), expect(0, stderr_exact=b""), group="devices", io=full,
        requires=["devfull"], routes=[route(b"[]")])
    add("devfull-text-empty-body-ignored", sql("SELECT 1"), expect(0, stderr_exact=b""), group="devices", io=full,
        requires=["devfull"], routes=[route(b"")])
    add("devfull-json-rows-ignored", sql("SELECT 1", "-o", "json"), expect(0, stderr_exact=b""), group="devices", io=full,
        requires=["devfull"], routes=[route(ROWS)])
    add("devfull-json-decode-error", sql("SELECT 1", "-o", "json"),
        expect(1, stderr_has=["error decoding JSON response: "]), group="devices", io=full, requires=["devfull"],
        routes=[route(b"[1 2]")])
    add("devfull-stderr-error", sql("SELECT 1"), expect(1, stdout_exact=b""), group="devices", io={"stderr": "devfull"},
        requires=["devfull"], routes=[route(b"boom", 500)])
    add("devfull-stderr-error-json", sql("SELECT 1", "-o", "json"), expect(1, stdout_exact=b""), group="devices",
        io={"stderr": "devfull"}, requires=["devfull"], routes=[route(b"boom", 500)])
    add("devfull-both-success", sql("SELECT 1"), expect(1), group="devices", io={"stdout": "devfull", "stderr": "devfull"},
        requires=["devfull"], routes=[route(ROWS)])

    # ----- stderr-TTY spinner (PTY; frame counts are scheduler-dependent) -------------------------
    spin = rb"(?:\r[|/\\-] fetching\.\.\.)+\r\x1b\[K"
    pty = {"stderr": "pty"}
    add("spinner-success-text", sql("SELECT 1"),
        expect(0, stdout_exact=text_output(ROWS), stderr_regex=pin(spin), min_spinner_frames=2), group="spinner", io=pty,
        requires=["pty"], compare={"spinner": True}, routes=[route(ROWS, delay_ms=450)])
    add("spinner-success-json", sql("SELECT 1", "-o", "json"),
        expect(0, stdout_exact=json_output(ROWS), stderr_regex=pin(spin), min_spinner_frames=2), group="spinner", io=pty,
        requires=["pty"], compare={"spinner": True}, routes=[route(ROWS, delay_ms=450)])
    add("spinner-cleared-before-error", sql("SELECT 1"),
        expect(1, stderr_regex=pin(spin + re.escape(text_error("query failed: server returned 500 Internal Server Error: boom")))),
        group="spinner", io=pty, requires=["pty"], compare={"spinner": True}, routes=[route(b"boom", 500, delay_ms=300)])
    add("spinner-connection-refused", sql("SELECT 1"),
        expect(1, none, stderr_regex=pin(rb"(?:\r[|/\\-] fetching\.\.\.)*\r\x1b\[K" + re.escape(text_error(refused)))),
        group="spinner", io=pty, requires=["pty"], compare={"spinner": True},
        config=raw(phase2.config_toml({"fixture": phase2.profile(url=NO_SERVER)})))
    add("spinner-sigint-uncleared", sql("SELECT 1"),
        expect(("signal", 2), stderr_regex=pin(rb"(?:\r[|/\\-] fetching\.\.\.)+")), group="spinner", io=pty, requires=["pty"],
        compare={"spinner": True}, routes=[route(b"[]", header_barrier="never")],
        actions=[{"when": {"fixture": "barrier:never"}, "do": {"snapshot_fixture": "awaiting-headers"}},
                 {"when": {"stderr_contains": "fetching..."}, "do": {"signal": "SIGINT"}}])
    add("spinner-not-before-validation", sql(""), expect(1, none, stderr_exact=text_error(REQUIRED)), group="spinner", io=pty,
        requires=["pty"], routes=[route()])
    add("spinner-not-before-output-validation", sql("SELECT 1", "-o", "yaml"), expect(1, none, stderr_exact=text_error(yaml_error)),
        group="spinner", io=pty, requires=["pty"], routes=[route()])
    add("spinner-not-before-request-construction", sql("SELECT 1"),
        expect(1, none, stderr_exact=text_error("failed to create new request: self-hosted profile missing credentials")),
        group="spinner", io=pty, requires=["pty"], config=raw(phase2.config_toml({"fixture": {"url": NO_SERVER}})))
    add("spinner-absent-when-only-stdout-is-tty", sql("SELECT 1"), expect(0, stdout_exact=text_output(ROWS), stderr_exact=b""),
        group="spinner", io={"stdout": "pty"}, requires=["pty"], routes=[route(ROWS, delay_ms=250)])

    # ----- seeded generation (seeds recorded in SEEDS and in every report) --------------------
    cases.extend(seeded_cases(add))

    # ----- full 60-second client deadline (opt-in tier) ---------------------------------------
    deadline_headers = 'request execution failed: Post "http://<FIXTURE>/api/v1/query": <CLIENT-TIMEOUT> (Client.Timeout exceeded while awaiting headers)'
    for output in ("text", "json"):
        flags = ["-o", "json"] if output == "json" else []
        add(f"deadline-header-stall-{output}", sql("SELECT 1", *flags),
            expect(1, stdout_exact=b"", **({"stderr_exact": text_error(deadline_headers)} if output == "text" else
                                          {"stderr_has": ['"code": "TIMEOUT"', "Client.Timeout exceeded while awaiting headers"]})),
            group="deadline", tier="full60s",
            routes=[route(b"[]", header_stall=True)])
        add(f"deadline-body-stall-{output}", sql("SELECT 1", *flags),
            expect(1, stdout_exact=json_output(first[:-1] + b"]")[:-3] if output == "json" else first,
                   **({"stderr_json": phase2.error_json("TIMEOUT", "error decoding JSON response: <CLIENT-TIMEOUT> "
                                                        "(Client.Timeout or context cancellation while reading body)", retryable=True)}
                      if output == "json" else {"stderr_exact": text_error("failed to stream response body: <CLIENT-TIMEOUT> "
                                                                           "(Client.Timeout or context cancellation while reading body)")})),
            group="deadline",
            tier="full60s", routes=[route(first + rest, framing="chunked", script=[{"send": len(first)}, {"stall": True}])])
    add("deadline-text-empty-array-lookahead", sql("SELECT 1"), expect(1, stdout_exact=b"", stderr_has=["failed to read response body: "]),
        group="deadline", tier="full60s", routes=[route(b"[]", framing="chunked", script=[{"send": "rest"}, {"stall": True}])])
    add("deadline-not-reset-per-chunk", sql("SELECT 1"),
        expect(1, stdout_prefix_of=b"abcdefghijklmnopqrstuvwxyz",
               stderr_exact=text_error("failed to stream response body: <CLIENT-TIMEOUT> (Client.Timeout or context cancellation while reading body)")),
        group="deadline", tier="full60s", compare={"stdout_lengths": paced_lengths(60000, 4000)},
        routes=[route(b"abcdefghijklmnopqrstuvwxyz", framing="chunked", chunk_bytes=1, chunk_delay_ms=4000)])
    add("deadline-spans-redirects", sql("SELECT 1"), expect(1, [QUERY, "POST /v2/api/v1/query"], stderr_has=["Client.Timeout exceeded while awaiting headers"]),
        group="deadline", tier="full60s", routes=[route(b"", 307, delay_ms=35000, headers_out=[["Location", "/v2/api/v1/query"]]),
                                                    route(b"[]", path="/v2/api/v1/query", delay_ms=35000)])
    add("deadline-spans-refresh", sql("SELECT 1"), expect(1, [QUERY, "POST " + token, QUERY], stderr_has=["Client.Timeout"]),
        group="deadline", tier="full60s", build="cloud", config=refresh_config, scenario_headers={},
        routes=[route(b"expired", 401, headers=old, times=1, delay_ms=35000), route(rotated, path=token, headers=grant, times=1),
                route(b"[]", headers=new, delay_ms=35000)])
    add("deadline-preview-needs-one-more-byte", sql("SELECT 1"), expect(1, stderr_has=["failed to read error response body: "]),
        group="deadline", tier="full60s",
        routes=[route(b"p" * PREVIEW_LIMIT, 500, framing="chunked", script=[{"send": "rest"}, {"stall": True}])])

    # ----- known divergence: non-UTF-8 argv (phase-one limitation) ------------------------------
    add("argv-invalid-utf8-sql", ["sql", "run", B(b"SELECT '\xff' FROM logs"), "--from", START, "--to", END],
        expect(0, request_bodies={"0": pin(request_body(b"SELECT '\xff' FROM logs"))}), group="argv", tier="known-divergence",
        routes=[route()])
    return cases


def paced_lengths(deadline_ms, delay_ms):
    """1-byte chunks: the first is sent with the headers, then one per delay. Bytes sent strictly
    before the deadline always arrive; the one scheduled exactly at the deadline races it."""
    before = 1 + (deadline_ms - 1) // delay_ms
    return [before, before + 1] if deadline_ms % delay_ms == 0 else [before]


def rows_spec(count):
    return {"prefix": "[", "separator": ",", "suffix": "]", "count": count, "width": 6,
            "item": '{"sequence":{{i}},"pad":"{{fill}}","p_timestamp":"2024-01-01T00:00:00.000Z","level":"info",'
                    '"message":"fixture <event> café 日志","value":0.25,"count":1234567,"ok":true,"optional":null}'}


def rows_body(count):
    return expand_generate(rows_spec(count))


def model_digest(output):
    return {"stdout_sha256": run.digest(output), "stdout_bytes": len(output)}


_RENDER_CACHE = {}


def rendered(output, spec):
    """Cached model rendering of a generated body (corpus and suite share large workloads)."""
    key = (output, json.dumps(spec, sort_keys=True))
    if key not in _RENDER_CACHE:
        body = expand_generate(spec)
        _RENDER_CACHE[key] = (json_output if output == "json" else text_output)(body)
    return _RENDER_CACHE[key]


# --------------------------------------------------------------------------
# Seeded generation. Expectations come from the model; Go must agree.

REWRITE_FRAGMENTS = [
    "SELECT", "select", "*", "FROM", "from", "JOIN", "join", "logs", "nginx-logs", "my.table", "123t", "dataset", "Dataset",
    "\"quoted-id\"", "\"a\"\"b\"", "'lit FROM x-y'", "'it''s'", "StatusCode", "service.name", "a.b.c", "Count", "count(",
    "(", ")", ",", ";", "-- comment", "-- Foo FROM a-b", "/* block */", "/* unclosed", "LIMIT 10", "limit 1000", "Limit",
    "LIMITED", "x.limit", "WHERE", "=", "1", "1.5", "é", "日本", "<&>", "\u2028", "\u00a0", "_under", "t1", "ORDER BY",
    "Desc", "--", "'unclosed", "\"unclosed", "ſ", "\t", "\n", "\r\n", "  ",
]
TIME_POOL = ["10m", "now", "", "2024-01-01T00:00:00Z", "<&>", "\u2028", " 1h ", "é", "1d"]
NUMBER_SPELLINGS = ["0", "-0", "1", "-17", "3.25", "1e3", "1E-3", "2.5e+10", "9007199254740993", "123456789012345678901234",
                    "1e21", "1e-7", "0.000001", "5e-324", "1.7976931348623157e308", "4.35", "0.1", "1.0", "-0.0", "100",
                    "12345678.9", "6.02214076e23", "1e-6", "999999999999999999999", "0.30000000000000004"]
STRING_PIECES = [b"a", b"Z", b" ", b"<", b">", b"&", b"\\n", b"\\t", b"\\\"", b"\\\\", b"\\/", b"\\u00e9", b"\\u2028",
                 b"\\ud83d\\ude00", b"\\ud800", b"\\u0000", b"\\u001f", "é".encode(), "日".encode(), "😀".encode(),
                 "\u2029".encode(), b"\x7f", b"\\b", b"\\f"]
INVALID_STRING_PIECES = [b"\xff", b"\xc3\x28", b"\xe2\x82", b"\xed\xa0\x80", b"\xc0\xaf"]
WHITESPACE = [b"", b"", b"", b" ", b"\n", b"\t", b"\r\n", b"  "]


def gen_tokens(rng, depth, invalid_bytes):
    """Valid JSON value as a token list; boundaries between tokens are structural positions."""
    kind = rng.choice(["object", "object", "array", "string", "number", "literal"] if depth < 3 else ["string", "number", "literal"])
    ws = lambda: [rng.choice(WHITESPACE)]  # noqa: E731
    if kind == "string":
        pieces = STRING_PIECES + (INVALID_STRING_PIECES if invalid_bytes else [])
        return [b'"' + b"".join(rng.choice(pieces) for _ in range(rng.randint(0, 6))) + b'"']
    if kind == "number":
        return [rng.choice(NUMBER_SPELLINGS).encode()]
    if kind == "literal":
        return [rng.choice([b"true", b"false", b"null"])]
    tokens = [b"{" if kind == "object" else b"["]
    for index in range(rng.randint(0, 4)):
        if index:
            tokens += [b","]
        tokens += ws()
        if kind == "object":
            key = rng.choice([b"a", b"b", b"A", b"_", b"sequence", "é".encode(), b"<k>", b"", b"a"])
            tokens += [b'"' + key + b'"', *ws(), b":", *ws()]
        tokens += gen_tokens(rng, depth + 1, invalid_bytes) + ws()
    tokens.append(b"}" if kind == "object" else b"]")
    return tokens


def gen_array(rng, invalid_bytes=False):
    """(tokens, index of the outer closing bracket); a trailer after it is ignored by Go."""
    tokens = [rng.choice(WHITESPACE), b"["]
    for index in range(rng.randint(0, 5)):
        if index:
            tokens.append(b",")
        tokens += [rng.choice(WHITESPACE), *gen_tokens(rng, 1, invalid_bytes), rng.choice(WHITESPACE)]
    tokens = [t for t in tokens if t != b""]
    close = len(tokens)
    tokens += [b"]", *[t for t in [rng.choice([b"", b"\n", b" trailing", b"]"])] if t]]
    return tokens, close


def mutate(rng, tokens, close):
    """Guaranteed-invalid structural mutation before the outer close; returns (kind, body)."""
    start = tokens.index(b"[")
    commas = [i for i in range(start, close) if tokens[i] == b","]
    choice = rng.choice(["truncate", "insert", "comma-space", "double-comma"] if commas else ["truncate", "insert"])
    if choice == "truncate":
        return choice, b"".join(tokens[:rng.randint(start + 1, close)])
    if choice == "insert":
        at = rng.randint(start + 1, close)
        return choice, b"".join(tokens[:at] + [rng.choice([b"#", b"\x01", b"@"])] + tokens[at:])
    at = rng.choice(commas)
    return choice, b"".join(tokens[:at] + [b" " if choice == "comma-space" else b",,"] + tokens[at + 1:])


def seeded_cases(add):
    generated = []
    rng = random.Random(SEEDS["rewrite"])
    for index in range(160):
        query = "SELECT " + "".join(rng.choice(REWRITE_FRAGMENTS) + rng.choice(["", " ", " ", "\n"]) for _ in range(rng.randint(1, 12)))
        start, end = rng.choice(TIME_POOL), rng.choice(TIME_POOL)
        body = request_body(query, start or "1m", end or "now")
        generated.append(add(f"seeded-rewrite-{index:03d}", ["sql", "run", query, "--from", start, "--to", end],
                             expect(0, request_bodies={"0": pin(body)},
                                    stdout_exact=b"Query succeeded: no rows returned (status: 200 OK).\n"),
                             group="rewrite-seeded", routes=[route(b"[]")]))
    rng = random.Random(SEEDS["json-valid"])
    for index in range(48):
        body = b"".join(gen_array(rng, invalid_bytes=index % 4 == 3)[0])
        output = "json" if index % 3 else "text"
        model = json_output(body) if output == "json" else text_output(body)
        generated.append(add(f"seeded-json-{index:03d}-{output}", sql("SELECT 1", *(["-o", "json"] if output == "json" else [])),
                             expect(0, stdout_exact=model, stderr_exact=b""), group="json-seeded", routes=[route(body)]))
    rng = random.Random(SEEDS["json-malformed"])
    for index in range(48):
        tokens, close = gen_array(rng, invalid_bytes=index % 5 == 4)
        valid = json_output(b"".join(tokens))
        mutation, body = mutate(rng, tokens, close)
        generated.append(add(f"seeded-malformed-{index:03d}-{mutation}", sql("SELECT 1", "-o", "json"),
                             expect(1, stdout_prefix_of=valid, stderr_has=['"code": "COMMAND_FAILED"', '"message": "error decoding JSON response: ']),
                             group="json-seeded", routes=[route(body)]))
    rng = random.Random(SEEDS["text"])
    alphabet = [b" ", b"\t", b"\r", b"\n", b"[", b"]", b"{", b"}", b'"', b"x", b"\xff", "é".encode(), b"\x00", b"\x0c", b"\xc3"]
    for index in range(32):
        body = b"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        if index % 4 == 0:
            body = rng.choice([b"", b" ", b"\n"]) + b"[" + rng.choice([b"", b" ", b"\n"]) + b"]" + body
        generated.append(add(f"seeded-text-{index:03d}", sql("SELECT 1"), expect(0, stdout_exact=text_output(body), stderr_exact=b""),
                             group="text-seeded", routes=[route(body)]))
    rng = random.Random(SEEDS["chunking"])
    for index in range(24):
        body = b"".join(gen_array(rng)[0])
        steps, remaining = [], len(body)
        while remaining:
            count = min(rng.randint(1, 9), remaining)
            steps += [{"send": count}, {"delay_ms": rng.choice([0, 0, 1])}]
            remaining -= count
        output = "json" if index % 2 else "text"
        model = json_output(body) if output == "json" else text_output(body)
        generated.append(add(f"seeded-chunks-{index:03d}-{output}", sql("SELECT 1", *(["-o", "json"] if output == "json" else [])),
                             expect(0, stdout_exact=model), group="chunking-seeded",
                             routes=[route(body, framing="chunked", script=steps)]))
    # add() already appended; return nothing new to avoid duplicates.
    return []


# --------------------------------------------------------------------------
# Candidate-only deferred boundaries: interactive mode and nonempty --save-as.

def deferred_boundaries():
    """No Go parity claim: an explicit unimplemented error before any request or HOME change."""
    checks = []
    for label, argv, structured in (
            ("interactive-with-sql", ["sql", "run", "SELECT 1", "-i"], False),
            ("interactive-without-sql", ["sql", "run", "-i"], False),
            ("interactive-long", ["sql", "run", "SELECT 1", "--interactive"], False),
            ("interactive-true", ["sql", "run", "SELECT 1", "--interactive=true"], False),
            ("interactive-ignores-output-validation", ["sql", "run", "SELECT 1", "-i", "-o", "yaml"], False),
            ("interactive-before-save", ["sql", "run", "SELECT 1", "-i", "--save-as", "named"], False),
            ("interactive-json", ["sql", "run", "SELECT 1", "-i", "-o", "json"], True),
            ("save-as-named", sql("SELECT * FROM logs", "--save-as", "named"), False),
            ("save-as-whitespace", sql("SELECT * FROM logs", "--save-as", " "), False),
            ("save-as-equals", sql("SELECT * FROM logs", "--save-as=named", "-o", "text"), False),
            ("save-as-json", sql("SELECT * FROM logs", "--save-as", "named", "-o", "json"), True)):
        checks.append({"name": "p3-boundary-" + label, "argv": argv, "structured": structured, "location": "xdg",
                       "config": {"auth": "basic"}, "build": "standard", "requires": [], "env": {}, "unset": [],
                       "fixture": {"headers": {}, "routes": [route(b"[]"), route(b"{}", path="/api/v1/filters")]},
                       "expect": {"termination": {"exit": 1}, "requests": []}})
    return checks


def boundary_issues(check, result):
    issues = []
    if result["failure"]:
        issues.append(result["failure"])
    if result["termination"] != {"exit": 1}:
        issues.append(f"termination {result['termination']}, expected exit 1 for an unimplemented operation")
    if result["stdout_bytes"]:
        issues.append("stdout must be empty (no query output before the deferred guard)")
    stderr = result["stderr"]
    if check["structured"]:
        try:
            detail = json.loads(stderr)["error"]
            if detail.get("code") != "COMMAND_FAILED" or STUB_MARKER.decode() not in detail.get("message", "") or detail.get("retryable"):
                issues.append("JSON stderr must be a COMMAND_FAILED, non-retryable stub envelope")
        except (ValueError, KeyError, TypeError, UnicodeError):
            issues.append("structured deferred error must be one JSON envelope on stderr")
    elif not (stderr.startswith(b"Error: ") and STUB_MARKER in stderr.split(b"\n", 1)[0]):
        issues.append(f"stderr must start with an explicit 'Error: ... {STUB_MARKER.decode()}' line")
    if b"unsupported output format" in stderr:
        issues.append("interactive mode must not acquire ordinary-run output validation")
    if result["trace"]:
        issues.append(f"network requests made: {request_lines(result)}")
    if result["config_before"] != result["config_after"]:
        issues.append("HOME changed")
    return issues


# --------------------------------------------------------------------------
# run.py benchmark export (fixture `generate`/`gzip` capabilities of harness/fixture).

FIXTURE_CAPABILITIES = ("generate", "gzip", "milestones")


def benchmark_suite():
    """Fixed run.py workloads with explicit SQL limits; outputs are model-pinned for row/order checks."""
    suite = []

    def add(name, group, argv, route_spec, output, rows, body, **extra):
        output_bytes = rendered(output, route_spec["generate"]) if "generate" in route_spec else \
            (json_output if output == "json" else text_output)(body)
        entry = {"name": "p3-bench-" + name, "group": "phase3-" + group, "argv": argv, "profile": True, "auth": "basic",
                 "fixture": {"headers": HEADERS["basic"], "routes": [route_spec]}, "expected_exit": 0,
                 "expected_requests": [QUERY], "fixture_response_body_bytes": len(body),
                 "expected_stdout_sha256": run.digest(output_bytes), "expected_stdout_bytes": len(output_bytes),
                 "fixture_capabilities": [c for c in FIXTURE_CAPABILITIES if c in json.dumps(route_spec)] or [],
                 "sql_output": output,
                 "max_output_bytes": min(max(32 << 20, len(output_bytes) + len(output_bytes) // 4 + (1 << 20)), 100 << 20)}
        if rows is None:
            entry["track_json_array"] = False
        else:
            entry["json_array_rows"] = rows
        entry.update(extra)
        suite.append(entry)

    def go_route(**options):
        return {"method": "POST", "path": "/api/v1/query", **options}

    for count in (1, 500, 10000, 100000):
        for output in ("json", "text"):
            flags = ["-o", "json"] if output == "json" else []
            add(f"rows-{count}-{output}", "sql-scale", sql(f"SELECT * FROM logs LIMIT {count}", *flags),
                go_route(generate=rows_spec(count)), output, count, rows_body(count))
    for output in ("json", "text"):
        flags = ["-o", "json"] if output == "json" else []
        add(f"rows-10000-gzip-{output}", "sql-compressed", sql("SELECT * FROM logs LIMIT 10000", *flags),
            go_route(generate=rows_spec(10000), gzip=True), output, 10000, rows_body(10000))
        add(f"rows-500-header-delay-50ms-{output}", "sql-latency", sql("SELECT * FROM logs LIMIT 500", *flags),
            go_route(generate=rows_spec(500), delay_ms=50), output, 500, rows_body(500))
        add(f"rows-10000-paced-{output}", "sql-latency", sql("SELECT * FROM logs LIMIT 10000", *flags),
            go_route(generate=rows_spec(10000), chunk_bytes=16384, chunk_delay_ms=2), output, 10000, rows_body(10000))
        blob = {"prefix": '[{"sequence":0,"blob":"', "item": "{{blob}}", "count": 1, "blob_bytes": 8 << 20, "suffix": '"}]'}
        add(f"one-large-element-8mib-{output}", "sql-memory-exceptions", sql("SELECT * FROM logs LIMIT 1", *flags),
            go_route(generate=blob), output, 1, expand_generate(blob), time_first_json_element=False)
        spaces = {"prefix": " " * (4 << 20) + "[", "item": rows_spec(1)["item"], "separator": ",", "count": 500, "width": 6, "suffix": "]"}
        add(f"leading-whitespace-4mib-{output}", "sql-memory-exceptions", sql("SELECT * FROM logs LIMIT 500", *flags),
            go_route(generate=spaces), output, 500, expand_generate(spaces), time_first_json_element=False)
        add(f"rows-10000-slow-reader-{output}", "sql-slow-reader", sql("SELECT * FROM logs LIMIT 10000", *flags),
            go_route(generate=rows_spec(10000)), output, 10000, rows_body(10000), read_delay_ms=1)
        add(f"empty-{output}", "sql-small", sql("SELECT * FROM logs LIMIT 10", *flags), go_route(body="[]"), output, None, b"[]")
    for entry in suite:
        if entry["name"].endswith("empty-json"):
            entry["json_array_rows"] = 0
            entry.pop("track_json_array")
    return suite


def check_fixture_capabilities(binary):
    result = subprocess.run([str(binary), "--capabilities"], capture_output=True, timeout=10)
    try:
        available = set(json.loads(result.stdout))
    except ValueError:
        available = set()
    missing = sorted(set(FIXTURE_CAPABILITIES) - available)
    if result.returncode or missing:
        raise ValueError(f"{binary} lacks fixture capabilities {missing}; rebuild harness/fixture (make -f harness/Makefile tools)")


# --------------------------------------------------------------------------
# Orchestration, reports and the frozen reference manifest.


def select(cases, names=None, groups=None, tiers=("gate",)):
    if names:
        unknown = set(names) - {c["name"] for c in cases}
        if unknown:
            raise ValueError(f"unknown cases: {sorted(unknown)}")
        outside = sorted(c["name"] for c in cases if c["name"] in names and c["tier"] not in tiers)
        if outside:
            raise ValueError(f"{outside} are outside the selected tiers {list(tiers)}; pass --include-full60s/--include-known-divergence")
        cases = [c for c in cases if c["name"] in names]
    cases = [c for c in cases if c["tier"] in tiers]
    if groups:
        cases = [c for c in cases if c["group"] in groups or c["group"].removeprefix("phase3-") in groups]
    if not cases:
        raise ValueError("no cases selected")
    return cases


def scope_summary(full, tiers):
    counts = collections.Counter(c["tier"] for c in full)
    return {"defined_cases": len(full), "tiers": dict(counts), "selected_tiers": list(tiers),
            "excluded_tiers": {t: TIERS[t] for t in TIERS if t not in tiers}, "seeds": SEEDS,
            "deferred_boundaries": [b["name"] for b in deferred_boundaries()],
            "known_divergences": {c["name"]: c["divergence"] for c in full if c["tier"] == "known-divergence"},
            "exclusions": SCOPE_EXCLUSIONS}


SCOPE_EXCLUSIONS = [
    "Interactive -i/--interactive and nonempty --save-as: candidate-only zero-request boundary checks, not Go parity.",
    "TLS, certificate trust and HTTP proxies: covered by native Rust transport tests; loopback proxies are bypassed by Go.",
    "HTTP/2, 1xx informational responses, Expect/100-continue and trailers are not scripted.",
    "Spinner frame counts and timing are scheduler-dependent; frames are collapsed, order and cleanup are checked.",
    "Known-divergence tier (opt-in, Go-pinned): empty HTTP reason phrases, OS connection-reset wording, premature "
    "header EOF and malformed status-code diagnostics, non-UTF-8 argv (see KNOWN_DIVERGENCES).",
    "SIGQUIT and HTTP/2 are outside the current fixtures.",
    "Real 60-second deadlines run only with --include-full60s (80 s per-case timeout).",
    "Peak RSS/CPU are recorded per sample but do not gate; run.py benchmark workloads measure them.",
    "Outputs over 16 MiB are compared by raw digest and size (no fixture URL/HOME appears in them).",
]


def check_version(path):
    phase2.check_version(path)


def platform_id():
    import platform
    return f"{sys.platform}-{platform.machine().lower()}"


def load_manifest(path):
    if not path or not Path(path).is_file():
        return None
    manifest = json.loads(Path(path).read_text())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported manifest schema")
    if manifest.get("platform") != platform_id():
        raise ValueError(f"{path} was frozen on {manifest.get('platform')}, this host is {platform_id()}; capture and freeze a "
                         "reference for this platform explicitly, or pass --manifest none for an unpinned run")
    return manifest


def manifest_issues(manifest, case, result):
    if manifest is None or case["tier"] not in manifest.get("selected_tiers", []):
        return []  # recorded in report["manifest"]["unfrozen_tiers_run"]; never claimed as verified
    frozen = manifest["cases"].get(case["name"])
    if frozen is None:
        return ["case missing from the frozen reference manifest"]
    actual = manifest_entry(case, result)
    return [f"reference {key} differs from frozen manifest" for key in frozen if frozen[key] != actual.get(key)]


def run_cases(args, mode):
    os.umask(0o022)
    tiers = ["gate", *(["full60s"] if args.include_full60s else []), *(["known-divergence"] if args.include_known_divergence else [])]
    full = corpus()
    cases = select(full, args.case, args.group, tiers)
    binaries = {}
    if args.reference:
        binaries["reference"] = phase1.binary_identity(args.reference)
    if args.cloud_reference:
        binaries["cloud_reference"] = phase1.binary_identity(args.cloud_reference)
    baseline = None
    if getattr(args, "baseline", None):
        baseline = Path(args.baseline).resolve()
        saved = json.loads((baseline / "report.json").read_text())
        baseline_issues(saved, full, tiers)
        binaries.update({k: v for k, v in saved["binaries"].items() if k in ("reference", "cloud_reference")})
    if mode == "compare":
        binaries["candidate"] = phase1.binary_identity(args.candidate)
        if args.cloud_candidate:
            binaries["cloud_candidate"] = phase1.binary_identity(args.cloud_candidate)
        if bool(binaries.get("cloud_reference")) != bool(args.cloud_candidate):
            raise ValueError("cloud reference and --cloud-candidate must be supplied together")
    if "reference" not in binaries:
        raise ValueError("--reference or --baseline is required")
    self_check = mode == "compare" and (binaries["candidate"]["sha256"] == binaries["reference"]["sha256"] or (
        "cloud_candidate" in binaries and binaries["cloud_candidate"]["sha256"] == binaries.get("cloud_reference", {}).get("sha256")))
    if self_check and not args.self_check:
        raise ValueError("candidate is the reference executable; pass --self-check to validate the harness itself")
    for role, identity in binaries.items():
        if baseline is None or role.endswith("candidate"):
            check_version(identity["path"])
    manifest = load_manifest(args.manifest) if args.manifest != "none" else None
    if manifest and manifest.get("selected_sha256") != tiers_sha256(full, manifest.get("selected_tiers", [])):
        raise ValueError(f"{args.manifest} is frozen for different case definitions; refreeze with `phase3.py freeze` after review")
    if manifest:
        for role in ("reference", "cloud_reference"):
            if role in binaries and manifest.get(role) and binaries[role]["sha256"] != manifest[role]:
                raise ValueError(f"{role} {binaries[role]['sha256']} is not the frozen manifest's {manifest[role]}")
    meter = None
    if args.meter != "none":
        meter = Path(args.meter).resolve()
        if not meter.is_file():
            raise ValueError(f"meter {meter} not found (make -f harness/Makefile tools, or --meter none)")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    run.write_json(out / "corpus.json", cases)
    report = {"schema_version": SCHEMA_VERSION, "mode": mode, "complete": False, "created_unix_ns": time.time_ns(),
              "runner_sha256": run.digest(Path(__file__).read_bytes()), "corpus_sha256": run.digest(run.canonical(full)),
              "selected_sha256": run.digest(run.canonical(cases)),
              "tier_sha256": {tier: tiers_sha256(full, [tier]) for tier in TIERS}, "binaries": binaries,
              "meter": phase1.binary_identity(meter) if meter else None, "baseline": str(baseline) if baseline else None,
              "manifest": {"path": str(args.manifest), "sha256": run.digest(Path(args.manifest).read_bytes()),
                           "frozen_tiers": manifest.get("selected_tiers", []),
                           "unfrozen_tiers_run": sorted({c["tier"] for c in cases} - set(manifest.get("selected_tiers", [])))}
              if manifest else None,
              "self_check": self_check, "partial": bool(args.case or args.group), "reference_runs": args.reference_runs,
              "scope": scope_summary(full, tiers), "cases": [], "boundaries": [], "reference_manifest": {}}
    requirement_binaries = {"cloud": binaries.get("cloud_reference")}
    for index, case in enumerate(cases):
        item = {"name": case["name"], "group": case["group"], "tier": case["tier"], "argv": case["argv"], "status": "pass",
                "issues": [], "advisories": [], "runs": {}}
        report["cases"].append(item)
        skip = requirement_skip(case, requirement_binaries)
        if skip:
            item.update(status="skip", skip=skip)
            continue
        cloud = case["build"] == "cloud"
        folder = out / "cases" / case["name"]
        if baseline is not None:
            entry = next((c for c in saved["cases"] if c["name"] == case["name"]), None)
            ref = load_stored(baseline / "cases" / case["name"] / "reference", case) if entry else None
            if ref is None or entry["status"] != "pass":
                item.update(status="fail", issues=["baseline lacks a passing reference run for this case"])
                continue
            stored = stored_issues(ref, entry, saved.get("reference_manifest", {}).get(case["name"]), case)
            if stored:
                item.update(status="fail", issues=["baseline artifact: " + issue for issue in stored])
                continue
            item["issues"] += ["baseline reference: " + issue for issue in expectation_issues(case, ref)]
            item["issues"] += ["baseline reference: " + issue for issue in manifest_issues(manifest, case, ref)]
        else:
            reference = binaries["cloud_reference" if cloud else "reference"]["path"]
            ref = sample(reference, case, folder / "reference", args.timeout, meter)
            item["issues"] += ["reference: " + issue for issue in expectation_issues(case, ref)]
            item["issues"] += ["reference: " + issue for issue in manifest_issues(manifest, case, ref)]
            for repeat in range(1, args.reference_runs):
                again = sample(reference, case, folder / f"reference-repeat-{repeat}", args.timeout, meter)
                item["issues"] += ["reference unstable: " + issue for issue in differences(case, ref, again)]
            report["reference_manifest"][case["name"]] = manifest_entry(case, ref)
        item["runs"]["reference"] = public(ref)
        if mode == "compare":
            candidate = binaries["cloud_candidate" if cloud else "candidate"]["path"]
            actual = sample(candidate, case, folder / "candidate", args.timeout, meter)
            item["runs"]["candidate"] = public(actual)
            item["issues"] += differences(case, ref, actual)
            item["advisories"] += advisories(ref, actual)
        if item["issues"]:
            item["status"] = "fail"
            print(f"FAIL {case['name']}: {'; '.join(item['issues'][:3])}", file=sys.stderr)
        if (index + 1) % 50 == 0:
            print(f"phase3: {index + 1}/{len(cases)} cases processed", file=sys.stderr)
    if mode != "compare":
        report["boundaries_status"] = "not applicable (reference check)"
    elif self_check:
        report["boundaries_status"] = "not applicable (self-check: Go is not expected to be a stub)"
    elif report["partial"]:
        report["boundaries_status"] = "not run (case/group filter)"
    else:
        report["boundaries_status"] = "run"
        for check in deferred_boundaries():
            actual = sample(binaries["candidate"]["path"], check, out / "boundaries" / check["name"], args.timeout, meter)
            issues = boundary_issues(check, actual)
            report["boundaries"].append({"name": check["name"], "argv": check["argv"], "status": "fail" if issues else "pass",
                                         "issues": issues, "run": public(actual)})
            if issues:
                print(f"BOUNDARY FAIL {check['name']}: {'; '.join(issues[:3])}", file=sys.stderr)
    for role, identity in binaries.items():
        if Path(identity["path"]).is_file() and phase1.binary_identity(identity["path"])["sha256"] != identity["sha256"]:
            report.setdefault("identity_issues", []).append(role + " executable changed during the run")
    report["complete"] = True
    counts = collections.Counter(item["status"] for item in report["cases"])
    report["counts"] = dict(counts)
    suite = benchmark_suite()
    run.write_json(out / "benchmark-suite.json", suite)
    report["benchmark_suite_sha256"] = run.digest(run.canonical(suite))
    write_report(out, report)
    boundary_failures = sum(item["status"] == "fail" for item in report["boundaries"])
    print(f"phase3 {mode}: {counts['pass']} passed, {counts['fail']} failed, {counts['skip']} skipped of {len(cases)}; "
          f"boundaries {len(report['boundaries']) - boundary_failures}/{len(report['boundaries'])} ({report['boundaries_status']}); "
          f"report: {out / 'report.md'}")
    if counts["fail"] or boundary_failures or report.get("identity_issues"):
        return 1
    if counts["skip"] and not args.allow_skips:
        print("phase3: skipped cases make this an incomplete gate (use --allow-skips to accept)", file=sys.stderr)
        return 1
    return 0


def tiers_sha256(full, tiers):
    """Identity of the selected tiers' cases: other tiers may change without invalidating a capture."""
    return run.digest(run.canonical([c for c in full if c["tier"] in tiers]))


def baseline_issues(saved, full, tiers=("gate",)):
    """A frozen baseline is usable only if its source capture passed completely and unfiltered."""
    if saved.get("schema_version") != SCHEMA_VERSION or saved.get("mode") != "reference-check" or not saved.get("complete"):
        raise ValueError("--baseline must be a complete check-reference directory of the current schema")
    captured = saved.get("scope", {}).get("selected_tiers", [])
    if not set(tiers) <= set(captured):
        raise ValueError(f"--baseline captured tiers {captured}; requested {list(tiers)}")
    if saved.get("selected_sha256") != tiers_sha256(full, captured):
        raise ValueError("--baseline was captured from different case definitions; recapture the Go reference")
    if saved.get("identity_issues"):
        raise ValueError(f"--baseline capture has identity issues: {saved['identity_issues']}")
    if saved.get("partial"):
        raise ValueError("--baseline capture was filtered; capture the full reference corpus")
    failed = [c["name"] for c in saved["cases"] if c["status"] != "pass"]
    if failed:
        raise ValueError(f"--baseline reference capture did not pass: {failed[:5]}")
    for role, identity in saved["binaries"].items():
        path = Path(identity["path"])
        if path.is_file() and phase1.binary_identity(path)["sha256"] != identity["sha256"]:
            raise ValueError(f"--baseline {role} executable at {path} changed since the capture")


def stored_issues(ref, entry, frozen, case):
    """Recompute saved artifact digests against the source report (tamper/corruption check)."""
    issues = []
    recorded = entry["runs"]["reference"]
    if ref.get("stdout_raw_digest") != recorded["stdout_sha256"]:
        issues.append("stdout.bin digest differs from the source report")
    if ref["stdout_normalized"] is not None and run.digest(ref["stdout_normalized"]) != recorded["stdout_normalized_sha256"]:
        issues.append("stdout.normalized digest differs from the source report")
    if ref["stdout_normalized"] is None and recorded["stdout_normalized_sha256"] != recorded["stdout_sha256"]:
        issues.append("stdout.normalized missing for an in-memory capture")
    if run.digest(ref["stderr_normalized"]) != recorded["stderr_sha256"]:
        issues.append("stderr.normalized digest differs from the source report")
    if run.digest(ref["stderr"]) != recorded.get("stderr_raw_sha256"):
        issues.append("stderr.bin digest differs from the source report")
    if run.digest(json.dumps(ref["trace"], sort_keys=True).encode()) != recorded.get("trace_sha256"):
        issues.append("trace.json differs from the source report")
    if run.digest(json.dumps(ref["milestones"], sort_keys=True).encode()) != recorded.get("milestones_sha256"):
        issues.append("milestones.json differs from the source report")
    saved = {k: v for k, v in ref.items() if k in recorded}
    for key in sorted(recorded):
        if saved.get(key) != recorded[key]:
            issues.append(f"result.json {key} differs from the source report")
    if frozen is None or manifest_entry(case, ref) != frozen:
        issues.append("reference digests differ from (or are missing in) the source capture manifest")
    return issues


def load_stored(folder, case):
    """Rebuild a comparable reference result from a check-reference artifact directory."""
    if not (folder / "result.json").is_file():
        return None
    saved = json.loads((folder / "result.json").read_text())
    stdout_path = folder / "stdout.normalized"
    stdout = stdout_path.read_bytes() if stdout_path.is_file() else None
    raw = hashlib.sha256()
    with (folder / "stdout.bin").open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            raw.update(block)
    trace = json.loads((folder / "trace.json").read_text())
    milestones = json.loads((folder / "milestones.json").read_text())
    config_raw = (folder / "config-after.toml").read_bytes() if (folder / "config-after.toml").is_file() else None
    return {**saved, "stdout": stdout, "stdout_normalized": stdout, "stdout_raw_digest": raw.hexdigest(),
            "stderr": (folder / "stderr.bin").read_bytes(),
            "stderr_normalized": (folder / "stderr.normalized").read_bytes(), "trace": trace,
            "milestones": milestones["milestones"], "snapshots": milestones.get("snapshots", {}),
            "started_monotonic_ns": milestones["process_started_monotonic_ns"], "config_raw_after": config_raw,
            "replacements": {}}


def write_report(out, report):
    run.write_json(out / "report.json", report)
    counts = collections.Counter(item["status"] for item in report["cases"])
    title = {"compare": "Go/Rust differential", "reference-check": "Go reference check"}[report["mode"]]
    lines = [f"# Phase-three SQL {title}", "",
             f"Passed {counts['pass']}, failed {counts['fail']}, skipped {counts['skip']} of {len(report['cases'])} selected cases.",
             f"Corpus SHA-256: `{report['corpus_sha256']}` ({report['scope']['defined_cases']} defined: {report['scope']['tiers']}).",
             f"Seeds: {report['scope']['seeds']}."]
    for role, identity in report["binaries"].items():
        lines.append(f"{role.replace('_', ' ').title()}: `{identity['path']}` `{identity['sha256']}`.")
    if report.get("baseline"):
        lines.append(f"Reference results loaded from frozen check-reference baseline `{report['baseline']}`.")
    if report.get("manifest"):
        lines.append(f"Reference captures of tiers {report['manifest']['frozen_tiers']} verified against frozen manifest "
                     f"`{report['manifest']['path']}`.")
        if report["manifest"]["unfrozen_tiers_run"]:
            lines.append(f"NOT FROZEN: tiers {report['manifest']['unfrozen_tiers_run']} are checked only against live Go "
                         "runs and source pins; no frozen-outcome claim is made for them.")
    if report.get("self_check"):
        lines.append("SELF-CHECK: candidate is the reference executable; this validates the harness, not the Rust port.")
    if report.get("partial"):
        lines.append("PARTIAL: a case/group filter was applied; this is not the full phase-three gate.")
    for tier, reason in report["scope"]["excluded_tiers"].items():
        lines.append(f"EXCLUDED tier `{tier}` ({report['scope']['tiers'].get(tier, 0)} cases): {reason}.")
    if "known-divergence" in report["scope"]["selected_tiers"]:
        lines.append("OPT-IN known-divergence cases are included; their results are documented residuals, not gate passes.")
    lines.append(f"Deferred boundary checks: {sum(b['status'] == 'pass' for b in report['boundaries'])}/"
                 f"{len(report['boundaries'])} passed ({report['boundaries_status']}); candidate-only, not Go compatibility.")
    lines += ["", "Gating: exact stdout/stderr bytes, OS termination, ordered requests (target, contract headers, raw body",
              "bytes, framing, listener), causal actions, early-completion limits and config state. Normalized: temporary HOME,",
              "fixture origins, and per-case declared spinner frames / header map order / client ephemeral ports.", "",
              "Scope exclusions:", *[f"- {item}" for item in report["scope"]["exclusions"]], ""]
    for item in report["cases"]:
        if item["status"] == "fail":
            lines += [f"FAIL `{item['name']}`:", *[f"- {issue}" for issue in item["issues"]], ""]
        elif item["status"] == "skip":
            lines.append(f"SKIP `{item['name']}`: {item['skip']}")
    for item in report["boundaries"]:
        if item["status"] == "fail":
            lines += ["", f"BOUNDARY FAIL `{item['name']}`:", *[f"- {issue}" for issue in item["issues"]]]
    advisory = [item for item in report["cases"] if item.get("advisories")]
    if advisory:
        lines += ["", "Advisories (not gating):", *[f"- `{i['name']}`: {'; '.join(i['advisories'])}" for i in advisory]]
    (out / "report.md").write_text("\n".join(lines) + "\n")


def freeze(args):
    source = Path(args.source).resolve()
    report = json.loads((source / "report.json").read_text())
    full = corpus()
    if report.get("mode") != "reference-check" or not report.get("complete") or report.get("partial"):
        raise ValueError("freeze needs a complete, unfiltered check-reference report")
    if any(item["status"] != "pass" for item in report["cases"]):
        raise ValueError("freeze needs every selected reference case to pass")
    if report.get("identity_issues"):
        raise ValueError(f"freeze rejects a capture with identity issues: {report['identity_issues']}")
    tiers = report["scope"]["selected_tiers"]
    if report.get("selected_sha256") != tiers_sha256(full, tiers):
        raise ValueError("report case definitions differ from the current corpus for its tiers")
    manifest = {"schema_version": SCHEMA_VERSION, "corpus_sha256": run.digest(run.canonical(full)),
                "selected_sha256": report["selected_sha256"],
                "reference": report["binaries"]["reference"]["sha256"],
                "cloud_reference": report["binaries"].get("cloud_reference", {}).get("sha256"),
                "go_toolchain": args.go_toolchain, "platform": platform_id(),
                "build": "go build -trimpath -buildvcs=false -tags kqueue (harness/Makefile baseline, baseline-cloud)",
                "source_report_runner_sha256": report["runner_sha256"],
                "selected_tiers": report["scope"]["selected_tiers"], "cases": report["reference_manifest"]}
    Path(args.out).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"phase3: froze {len(manifest['cases'])} reference cases into {args.out}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list the fixed phase-three corpus")
    listing.add_argument("--json", action="store_true")
    for name in ("compare", "check-reference"):
        command = sub.add_parser(name, help="Go/Rust differential gate" if name == "compare" else "validate expectations against Go only")
        command.add_argument("--reference", help="pinned Go executable (bin/pb-go)")
        command.add_argument("--cloud-reference", help="Go built with -X ...CloudOrchestratorAuthToken=" + ORCHESTRATOR_TOKEN)
        if name == "compare":
            command.add_argument("--baseline", help="frozen check-reference directory instead of a live --reference")
            command.add_argument("--candidate", required=True)
            command.add_argument("--cloud-candidate")
            command.add_argument("--self-check", action="store_true", help="allow candidate == reference (harness validation)")
        command.add_argument("--out", required=True, help="new artifact directory")
        command.add_argument("--case", action="append")
        command.add_argument("--group", action="append")
        command.add_argument("--include-full60s", action="store_true", help="add the real 60 s deadline tier (80 s per-case timeout)")
        command.add_argument("--include-known-divergence", action="store_true")
        command.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="default per-case timeout (seconds)")
        command.add_argument("--reference-runs", type=int, default=2, help="reference repetitions checked for stability")
        command.add_argument("--meter", default=str(DEFAULT_METER) if DEFAULT_METER.is_file() else "none",
                             help="pb-meter supervisor for child termination/RSS, or 'none'")
        command.add_argument("--manifest", default=str(MANIFEST), help="frozen reference digests to verify, or 'none'")
        command.add_argument("--allow-skips", action="store_true")
    freezing = sub.add_parser("freeze", help="write the reviewed reference manifest from a passing check-reference run")
    freezing.add_argument("source")
    freezing.add_argument("--out", default=str(MANIFEST))
    freezing.add_argument("--go-toolchain", default="go1.25.4")
    exporter = sub.add_parser("suite", help="export fixed run.py benchmark workloads")
    exporter.add_argument("--out", required=True)
    exporter.add_argument("--fixture", help="verify this pb-fixture supports the required capabilities")
    server = sub.add_parser("serve", help=argparse.SUPPRESS)
    server.add_argument("--scenario", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            serve(args.scenario)
            return 0
        if args.command == "list":
            cases = corpus()
            if args.json:
                print(json.dumps(cases, indent=2, ensure_ascii=False))
            else:
                for case in cases:
                    print(case["name"], case["group"], case["tier"], case["build"], json.dumps(case["argv"], ensure_ascii=False)[:160])
                print(f"{len(cases)} defined cases; tiers {dict(collections.Counter(c['tier'] for c in cases))}; "
                      f"{len(deferred_boundaries())} candidate-only boundaries; seeds {SEEDS}")
            return 0
        if args.command == "suite":
            if args.fixture:
                check_fixture_capabilities(args.fixture)
            with Path(args.out).open("x") as handle:
                json.dump(benchmark_suite(), handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            return 0
        if args.command == "freeze":
            return freeze(args)
        if args.timeout <= 0 or args.reference_runs < 1:
            raise ValueError("--timeout must be positive and --reference-runs at least 1")
        if args.command == "check-reference":
            args.candidate = args.cloud_candidate = args.baseline = None
            args.self_check = False
            return run_cases(args, "reference-check")
        return run_cases(args, "compare")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print("phase3 setup error: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
