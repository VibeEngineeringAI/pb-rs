//! Interactive PromQL session (`pb promql run -i`).
//!
//! Behavior follows pkg/model/promql.go at Go 07af390: the screen state is an
//! owned `PromqlState`; `update` applies one key, resize or completion and
//! returns the effects to run; `view` renders the full frame as owned ANSI
//! text through the shared `tui::style` lipgloss port. The runner owns the
//! terminal session, the concrete `PromqlSessionService` and every task.
//!
//! Deliberately preserved Go behavior (see docs/phase7-promql.md):
//! * result completions carry no generation check (a slow earlier run can
//!   replace a later one), builder label/value completions keep their
//!   metric/label display guards and the cache prefetch its dataset guard;
//! * Ctrl+C quits only when no overlay is open;
//! * `w` toggles metric unwrap before the table sees it, but only in table
//!   view; in chart view `w` reaches the table key map (row up);
//! * a resize resets the VALUE column to the width of its title.
use super::{
    editor::{AreaStyles, InputStyles, PB_AREA_KEYS, TextArea, TextInput},
    input::{Event, Key, KeyCode},
    promql_service::{AnyValue, PromqlResult, PromqlSessionService, ResultRequest},
    sql::{GoTime, Zone, parse_cli_time, reflow_truncate},
    style::{self, Border, Color, GoPanic, KeyHint, Role, Style},
    terminal::{Screen, TerminalSession},
};
use crate::{ErrorCode, error::CliError, parser::ParsedInvocation};
use chrono::{DateTime, Local, LocalResult, NaiveDate, NaiveDateTime, TimeZone, Utc};
use std::collections::HashMap;
use std::io::Write;
use std::sync::Arc;

// ─── constants ──────────────────────────────────────────────────────────────

const TIMESTAMP_WIDTH: usize = "TIME [LOCAL]".len();
const AUTO_STEP: &str = "auto";
const SPOTLIGHT_WIDTH: usize = 58;
const SPOTLIGHT_MAX_ITEMS: usize = 12;
const BUILDER_MAX_ITEMS: usize = 10;
const TOP_H: usize = 13;
const SQL_CONTROLS_MIN_WIDTH: usize = "02 Jun 2026, 13:25 | UTC+05:30".len() + 4;
const ANY: &str = "(any)";
const SPINNER_FRAMES: [&str; 4] = ["|", "/", "-", "\\"];
const SPINNER_INTERVAL: std::time::Duration = std::time::Duration::from_millis(100);
const NANOS_PER_SECOND: i64 = 1_000_000_000;
const HOUR_NANOS: i64 = 3600 * NANOS_PER_SECOND;
const CHART_X_LABEL_COUNT: usize = 8;

const PARSEABLE_ASCII_ART: &str = " ____   _    ____  ____  _____    _    ____  _     _____
|  _ \\ / \\  |  _ \\/ ___|| ____|  / \\  | __ )| |   | ____|
| |_) / _ \\ | |_) \\___ \\|  _|   / _ \\ |  _ \\| |   |  _|
|  __/ ___ \\|  _ < ___) | |___ / ___ \\| |_) | |___| |___
|_| /_/   \\_\\_| \\_\\____/|_____/_/   \\_\\____/|_____|_____|";

const CHART_SERIES_PALETTE: [&str; 12] = [
    "#FF3B30", "#00E5FF", "#FF2D55", "#BF5AF2", "#FF6B00", "#64D2FF", "#5E5CE6", "#FF7AB6",
    "#B388FF", "#FFCC99", "#00C7BE", "#A78BFA",
];

// ─── public data types shared with the service ─────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum QueryMode {
    Range,
    Instant,
    Both,
}

impl QueryMode {
    fn label(self) -> &'static str {
        match self {
            Self::Range => "range",
            Self::Instant => "instant",
            Self::Both => "both",
        }
    }
    fn next(self) -> Self {
        match self {
            Self::Range => Self::Instant,
            Self::Instant => Self::Both,
            Self::Both => Self::Range,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum DisplayMode {
    Local,
    Utc,
}

/// One result row (Go table.RowData with timestamp/_timestamp_full/metric/value).
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Row {
    pub(crate) timestamp: String,
    pub(crate) timestamp_full: String,
    pub(crate) metric: String,
    pub(crate) value: String,
}

/// Successful `PromqlFetchData`.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ResultData {
    pub(crate) result_type: String,
    pub(crate) chart_result: String,
    pub(crate) rows: Vec<Row>,
    pub(crate) chart_rows: Vec<Row>,
    pub(crate) series_count: usize,
    pub(crate) value_width: usize,
}

// ─── step grammar ───────────────────────────────────────────────────────────

/// `isValidPromqlStep`: `^(?:[0-9]+(?:ms|s|m|h|d|w|y))+$`, every count > 0.
fn is_valid_step(step: &str) -> bool {
    let bytes = step.as_bytes();
    if bytes.is_empty() {
        return false;
    }
    let mut i = 0;
    while i < bytes.len() {
        let start = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if i == start {
            return false;
        }
        match step[start..i].parse::<i64>() {
            Ok(value) if value > 0 => {}
            _ => return false,
        }
        if bytes[i..].starts_with(b"ms") {
            i += 2;
        } else if i < bytes.len() && matches!(bytes[i], b's' | b'm' | b'h' | b'd' | b'w' | b'y') {
            i += 1;
        } else {
            return false;
        }
    }
    true
}

pub(crate) fn normalize_step(step: &str) -> String {
    let trimmed = step.trim();
    if trimmed.is_empty() || trimmed.eq_ignore_ascii_case(AUTO_STEP) {
        return AUTO_STEP.to_owned();
    }
    if is_valid_step(trimmed) {
        trimmed.to_owned()
    } else {
        AUTO_STEP.to_owned()
    }
}

/// `ResolvePromqlStep` on a nanosecond duration.
fn resolve_step_duration(step: &str, duration_nanos: i64) -> String {
    let trimmed = normalize_step(step);
    if trimmed != AUTO_STEP {
        return trimmed;
    }
    if duration_nanos <= 0 {
        return "1m".to_owned();
    }
    const S: i64 = NANOS_PER_SECOND;
    const CANDIDATES: [(i64, &str); 16] = [
        (15 * S, "15s"),
        (30 * S, "30s"),
        (60 * S, "1m"),
        (120 * S, "2m"),
        (300 * S, "5m"),
        (600 * S, "10m"),
        (900 * S, "15m"),
        (1800 * S, "30m"),
        (3600 * S, "1h"),
        (7200 * S, "2h"),
        (4 * 3600 * S, "4h"),
        (6 * 3600 * S, "6h"),
        (12 * 3600 * S, "12h"),
        (24 * 3600 * S, "1d"),
        (48 * 3600 * S, "2d"),
        (7 * 24 * 3600 * S, "7d"),
    ];
    for (duration, label) in CANDIDATES {
        if duration_nanos / duration <= 100 {
            return label.to_owned();
        }
    }
    "7d".to_owned()
}

fn parse_rfc3339(value: &str) -> Option<DateTime<Utc>> {
    crate::time_compat::parse_rfc3339(value)
}

/// `resolvePromqlStepFromStrings`.
pub(crate) fn resolve_step_from_strings(step: &str, start: &str, end: &str) -> String {
    let trimmed = normalize_step(step);
    if trimmed != AUTO_STEP {
        return trimmed;
    }
    match (parse_rfc3339(start), parse_rfc3339(end)) {
        (Some(start), Some(end)) => resolve_step_duration(&trimmed, duration_nanos(start, end)),
        _ => "1m".to_owned(),
    }
}

fn duration_nanos<A: TimeZone, B: TimeZone>(start: DateTime<A>, end: DateTime<B>) -> i64 {
    let delta = end.with_timezone(&Utc) - start.with_timezone(&Utc);
    delta
        .num_nanoseconds()
        .unwrap_or(if delta > chrono::TimeDelta::zero() {
            i64::MAX
        } else {
            i64::MIN
        })
}

/// `normalizePromqlRangeWindow`: a non-increasing range becomes the hour
/// ending at `end` for the range half of `both`.
pub(crate) fn normalize_range_window(start: &str, end: &str) -> (String, String) {
    match (parse_rfc3339(start), parse_rfc3339(end)) {
        (Some(s), Some(e)) if s >= e => (
            crate::time_compat::format_time(e - chrono::TimeDelta::hours(1)),
            end.to_owned(),
        ),
        _ => (start.to_owned(), end.to_owned()),
    }
}

// ─── result conversion ──────────────────────────────────────────────────────

/// `promqlModelFormatLabels`.
fn format_labels(metric: &[(String, String)]) -> String {
    let name = metric
        .iter()
        .find(|(k, _)| k == "__name__")
        .map(|(_, v)| v.as_str())
        .unwrap_or("");
    let mut labels: Vec<String> = metric
        .iter()
        .filter(|(k, _)| k != "__name__")
        .map(|(k, v)| format!("{k}=\"{v}\""))
        .collect();
    labels.sort();
    if labels.is_empty() {
        return name.to_owned();
    }
    if name.is_empty() {
        return format!("{{{}}}", labels.join(", "));
    }
    format!("{name}{{{}}}", labels.join(", "))
}

/// Go `strconv.FormatFloat(f, 'g', -1, 64)`, which `%v` uses for float64.
pub(crate) fn go_float(value: f64) -> String {
    if value.is_nan() {
        return "NaN".to_owned();
    }
    if value.is_infinite() {
        return if value > 0.0 { "+Inf" } else { "-Inf" }.to_owned();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.to_owned();
    }
    let formatted = format!("{value:e}");
    let (mantissa, exponent) = formatted.split_once('e').expect("LowerExp has an exponent");
    let exponent: i32 = exponent.parse().expect("integer exponent");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let sign = if negative { "-" } else { "" };
    if !(-4..6).contains(&exponent) {
        let mut out = format!("{sign}{}", &digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let exp_sign = if exponent < 0 { '-' } else { '+' };
        out.push_str(&format!("e{exp_sign}{:02}", exponent.abs()));
        return out;
    }
    if exponent < 0 {
        let zeros = "0".repeat((-exponent - 1) as usize);
        return format!("{sign}0.{zeros}{digits}");
    }
    let point = exponent as usize + 1;
    if digits.len() <= point {
        format!("{sign}{digits}{}", "0".repeat(point - digits.len()))
    } else {
        format!("{sign}{}.{}", &digits[..point], &digits[point..])
    }
}

/// `fmt.Sprintf("%v", any)` for JSON-decoded values.
fn format_any(value: &AnyValue) -> String {
    match value {
        AnyValue::Null => "<nil>".to_owned(),
        AnyValue::Bool(b) => b.to_string(),
        AnyValue::Number(n) => go_float(*n),
        AnyValue::String(s) => s.clone(),
        AnyValue::Array(items) => format!(
            "[{}]",
            items.iter().map(format_any).collect::<Vec<_>>().join(" ")
        ),
        AnyValue::Object(fields) => format!(
            "map[{}]",
            fields
                .iter()
                .map(|(k, v)| format!("{k}:{}", format_any(v)))
                .collect::<Vec<_>>()
                .join(" ")
        ),
    }
}

fn local_time(t: DateTime<Utc>) -> DateTime<Local> {
    t.with_timezone(&Local)
}

/// Go layout "2006-01-02T15:04:05-07:00" in the local zone.
fn format_local_rfc3339(t: DateTime<Local>) -> String {
    t.format("%Y-%m-%dT%H:%M:%S%:z").to_string()
}

fn format_utc_rfc3339(t: DateTime<Utc>) -> String {
    t.format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Go `int64(f)` / `int(f)`. Go leaves an out-of-range conversion
/// implementation-specific. linux/amd64 (CVTTSD2SQ, the oracle-tested
/// target) yields MinInt64 for NaN, ±Inf and every out-of-range value.
/// Elsewhere this uses Rust's saturating cast (NaN → 0), which is the arm64
/// FCVTZS result; other targets are not characterized.
pub(crate) fn go_f64_to_i64(value: f64) -> i64 {
    if cfg!(target_arch = "x86_64") {
        if (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&value) {
            value as i64
        } else {
            i64::MIN
        }
    } else {
        value as i64
    }
}

// Go 1.25 time package constants (src/time/time.go).
const GO_SECONDS_PER_DAY: u64 = 86_400;
const GO_ABSOLUTE_YEARS: u64 = 292_277_022_400;
/// `internalToAbsolute = (absoluteYears*365.2425 + marchThruDecember) * secondsPerDay`.
const GO_INTERNAL_TO_ABSOLUTE: i64 =
    (GO_ABSOLUTE_YEARS as i64 * 365 + GO_ABSOLUTE_YEARS as i64 / 400 * 97 + 306) * 86_400;
const GO_UNIX_TO_INTERNAL: i64 = (1969 * 365 + 1969 / 4 - 1969 / 100 + 1969 / 400) * 86_400;
const GO_UNIX_TO_ABSOLUTE: i64 = GO_UNIX_TO_INTERNAL + GO_INTERNAL_TO_ABSOLUTE;

/// `time.Unix(sec, nsec)` normalization (wrapping int64 arithmetic), then
/// `Time.unixSec` (the internal offset added and removed again wraps back).
fn go_unix_seconds(mut sec: i64, mut nsec: i64) -> i64 {
    if !(0..NANOS_PER_SECOND).contains(&nsec) {
        let n = nsec / NANOS_PER_SECOND;
        sec = sec.wrapping_add(n);
        nsec -= n * NANOS_PER_SECOND;
        if nsec < 0 {
            sec = sec.wrapping_sub(1);
        }
    }
    sec
}

/// Go `appendInt(b, x, width)`: sign, then the magnitude zero-padded.
fn go_append_int(out: &mut String, x: i64, width: usize) {
    if x < 0 {
        out.push('-');
    }
    out.push_str(&format!("{:0width$}", x.unsigned_abs()));
}

/// Go `Time.Format` for `2006-01-02T15:04:05` plus a zone suffix, computed
/// as Go does from `absSec` (`uint64(sec + offset + unixToAbsolute)`, which
/// wraps for out-of-range seconds) and `absDays.date` / `absSeconds.clock`.
/// `utc` selects RFC3339 (`Z` for a zero offset); otherwise the
/// `-07:00` layout. Ported from Go 1.25 src/time/{time,format,format_rfc3339}.go,
/// Copyright 2009 The Go Authors. All rights reserved. Use of this source
/// code is governed by a BSD-style license that can be found in
/// src/LICENSE-Go.txt.
fn go_format_unix(sec: i64, offset: i32, utc: bool) -> String {
    let abs = sec
        .wrapping_add(i64::from(offset))
        .wrapping_add(GO_UNIX_TO_ABSOLUTE) as u64;
    let days = abs / GO_SECONDS_PER_DAY;
    // absDays.split
    let d = days.wrapping_mul(4).wrapping_add(3);
    let century = d / 146_097;
    let cd = (d % 146_097) as u32 | 3;
    let product = 2_939_745u64 * u64::from(cd);
    let cyear = (product >> 32) as u32;
    let ayday = (product as u32) / 2_939_745 / 4;
    // absYday.split / janFeb / month / year
    let m = 2141 * ayday + 197_913;
    let amonth = m >> 16;
    let day = 1 + (m & 0xFFFF) / 2141;
    let jan_feb = u32::from(ayday >= 306);
    let year = century.wrapping_mul(100).wrapping_sub(GO_ABSOLUTE_YEARS) as i64
        + i64::from(cyear)
        + i64::from(jan_feb);
    let month = amonth - jan_feb * 12;
    // absSeconds.clock
    let clock = abs % GO_SECONDS_PER_DAY;
    let mut out = String::new();
    go_append_int(&mut out, year, 4);
    out.push('-');
    go_append_int(&mut out, i64::from(month), 2);
    out.push('-');
    go_append_int(&mut out, i64::from(day), 2);
    out.push('T');
    go_append_int(&mut out, (clock / 3600) as i64, 2);
    out.push(':');
    go_append_int(&mut out, (clock % 3600 / 60) as i64, 2);
    out.push(':');
    go_append_int(&mut out, (clock % 60) as i64, 2);
    if utc && offset == 0 {
        out.push('Z');
        return out;
    }
    let mut zone = offset / 60;
    if zone < 0 {
        out.push('-');
        zone = -zone;
    } else {
        out.push('+');
    }
    go_append_int(&mut out, i64::from(zone / 60), 2);
    out.push(':');
    go_append_int(&mut out, i64::from(zone % 60), 2);
    out
}

/// time.Local's offset at `sec`. chrono's tzif lookup covers its range;
/// outside it the nearest representable instant is used (exact for TZ=UTC,
/// the oracle fixture zone; other zones there are uncharacterized).
fn local_offset_at(sec: i64) -> i32 {
    let clamped = sec.clamp(
        DateTime::<Utc>::MIN_UTC.timestamp(),
        DateTime::<Utc>::MAX_UTC.timestamp(),
    );
    DateTime::<Utc>::from_timestamp(clamped, 0)
        .map(|t| t.with_timezone(&Local).offset().local_minus_utc())
        .unwrap_or(0)
}

/// `promqlModelFormatTS`.
fn format_ts(value: &AnyValue, display: DisplayMode) -> String {
    match value {
        AnyValue::Number(f) => {
            // sec, frac := math.Modf(v); time.Unix(int64(sec), int64(frac*1e9)).
            let (sec, frac) = if f.is_infinite() {
                (*f, f64::NAN)
            } else {
                (f.trunc(), f - f.trunc())
            };
            let sec = go_unix_seconds(go_f64_to_i64(sec), go_f64_to_i64(frac * 1e9));
            match display {
                DisplayMode::Utc => go_format_unix(sec, 0, true),
                DisplayMode::Local => go_format_unix(sec, local_offset_at(sec), false),
            }
        }
        AnyValue::String(s) => match parse_chart_time(s) {
            Some(t) => match display {
                DisplayMode::Utc => format_utc_rfc3339(t.with_timezone(&Utc)),
                DisplayMode::Local => format_local_rfc3339(t),
            },
            None => s.clone(),
        },
        other => format_any(other),
    }
}

/// `trimTimestampToHMS`.
pub(crate) fn trim_timestamp_to_hms(value: &str) -> String {
    let separator = value.find('T').or_else(|| value.find(' '));
    let Some(t) = separator else {
        return value.to_owned();
    };
    if t + 1 >= value.len() {
        return value.to_owned();
    }
    let rest = &value[t + 1..];
    for (i, c) in rest.char_indices() {
        if matches!(c, '.' | 'Z' | '+' | '-') {
            return rest[..i].to_owned();
        }
    }
    if rest.len() >= 8 {
        let mut end = 8;
        while !rest.is_char_boundary(end) {
            end -= 1;
        }
        return rest[..end].to_owned();
    }
    rest.to_owned()
}

/// `promqlResultToRows`: rows, series count and the VALUE width.
pub(crate) fn rows_from_result(
    result: &PromqlResult,
    display: DisplayMode,
) -> (Vec<Row>, usize, usize) {
    let mut rows = Vec::new();
    let mut value_width = 14;
    let mut add = |metric: &str, point: &[AnyValue]| {
        let full = format_ts(&point[0], display);
        let value = format_any(&point[1]);
        value_width = value_width.max(value.len());
        rows.push(Row {
            timestamp: trim_timestamp_to_hms(&full),
            timestamp_full: full,
            metric: metric.to_owned(),
            value,
        });
    };
    for series in &result.result {
        let metric = format_labels(&series.metric);
        match result.result_type.as_str() {
            "vector" if series.value.len() == 2 => add(&metric, &series.value),
            "matrix" => {
                for point in &series.values {
                    if point.len() == 2 {
                        add(&metric, point);
                    }
                }
            }
            _ => {}
        }
    }
    (rows, result.result.len(), value_width)
}

/// `parsePromqlChartTime`: zoned layouts convert to the local zone.
fn parse_chart_time(value: &str) -> Option<DateTime<Local>> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    if let Some(t) = parse_rfc3339(value) {
        return Some(local_time(t));
    }
    let today_less = |naive: NaiveDateTime| resolve_local(naive);
    for layout in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(value, layout) {
            return today_less(naive);
        }
    }
    for layout in ["%H:%M:%S", "%H:%M"] {
        if let Ok(time) = chrono::NaiveTime::parse_from_str(value, layout) {
            let date = NaiveDate::from_ymd_opt(0, 1, 1)?;
            return today_less(date.and_time(time));
        }
    }
    None
}

/// Go `time.Date` in the local zone: ambiguous times take the earlier
/// instant; nonexistent times are shifted forward by the gap.
fn resolve_local(naive: NaiveDateTime) -> Option<DateTime<Local>> {
    match Local.from_local_datetime(&naive) {
        LocalResult::Single(t) => Some(t),
        LocalResult::Ambiguous(a, _) => Some(a),
        LocalResult::None => {
            let shifted = naive + chrono::TimeDelta::hours(1);
            Local
                .from_local_datetime(&shifted)
                .earliest()
                .map(|t| t - chrono::TimeDelta::hours(1))
        }
    }
}

// ─── Go sort compatibility (sort.Slice / sort.SliceStable) ──────────────────

/// Exact ports of Go 1.25.4 `sort.Slice` (pdqsort) and `sort.SliceStable`
/// (insertion blocks + SymMerge), keeping Go's Less/Swap sequence. Element
/// order then matches Go for equal keys (sort.Slice is not stable above 12
/// elements) and for comparators that are not a strict weak order (NaN),
/// where Rust's sorts differ or may panic.
///
/// Ported from Go src/sort/{zsortfunc.go,sort.go,slice.go}.
/// Copyright 2009, 2017, 2022 The Go Authors. All rights reserved.
/// Use of this source code is governed by a BSD-style license that can be
/// found in src/LICENSE-Go.txt.
mod go_sort {
    trait LessSwap {
        fn less(&mut self, i: usize, j: usize) -> bool;
        fn swap(&mut self, i: usize, j: usize);
    }

    struct Data<'a, T, F> {
        items: &'a mut [T],
        less: F,
    }

    impl<T, F: FnMut(&T, &T) -> bool> LessSwap for Data<'_, T, F> {
        fn less(&mut self, i: usize, j: usize) -> bool {
            (self.less)(&self.items[i], &self.items[j])
        }
        fn swap(&mut self, i: usize, j: usize) {
            self.items.swap(i, j);
        }
    }

    /// `bits.Len(uint(x))`.
    fn bits_len(x: usize) -> u32 {
        usize::BITS - x.leading_zeros()
    }

    /// `sort.Slice`.
    pub(super) fn slice<T>(items: &mut [T], less: impl FnMut(&T, &T) -> bool) {
        let n = items.len();
        let mut data = Data { items, less };
        pdqsort(&mut data, 0, n, bits_len(n));
    }

    /// `sort.SliceStable`.
    pub(super) fn slice_stable<T>(items: &mut [T], less: impl FnMut(&T, &T) -> bool) {
        let n = items.len();
        let mut data = Data { items, less };
        stable(&mut data, n);
    }

    fn insertion_sort(d: &mut dyn LessSwap, a: usize, b: usize) {
        for i in a + 1..b {
            let mut j = i;
            while j > a && d.less(j, j - 1) {
                d.swap(j, j - 1);
                j -= 1;
            }
        }
    }

    fn sift_down(d: &mut dyn LessSwap, lo: usize, hi: usize, first: usize) {
        let mut root = lo;
        loop {
            let mut child = 2 * root + 1;
            if child >= hi {
                break;
            }
            if child + 1 < hi && d.less(first + child, first + child + 1) {
                child += 1;
            }
            if !d.less(first + root, first + child) {
                return;
            }
            d.swap(first + root, first + child);
            root = child;
        }
    }

    fn heap_sort(d: &mut dyn LessSwap, a: usize, b: usize) {
        let first = a;
        let lo = 0;
        let hi = b - a;
        let mut i = (hi as isize - 1) / 2;
        while i >= 0 {
            sift_down(d, i as usize, hi, first);
            i -= 1;
        }
        for i in (0..hi).rev() {
            d.swap(first, first + i);
            sift_down(d, lo, i, first);
        }
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Hint {
        Unknown,
        Increasing,
        Decreasing,
    }

    fn pdqsort(d: &mut dyn LessSwap, mut a: usize, mut b: usize, mut limit: u32) {
        const MAX_INSERTION: usize = 12;
        let (mut was_balanced, mut was_partitioned) = (true, true);
        loop {
            let length = b - a;
            if length <= MAX_INSERTION {
                insertion_sort(d, a, b);
                return;
            }
            if limit == 0 {
                heap_sort(d, a, b);
                return;
            }
            if !was_balanced {
                break_patterns(d, a, b);
                limit -= 1;
            }
            let (mut pivot, mut hint) = choose_pivot(d, a, b);
            if hint == Hint::Decreasing {
                reverse_range(d, a, b);
                pivot = (b - 1) - (pivot - a);
                hint = Hint::Increasing;
            }
            if was_balanced
                && was_partitioned
                && hint == Hint::Increasing
                && partial_insertion_sort(d, a, b)
            {
                return;
            }
            if a > 0 && !d.less(a - 1, pivot) {
                a = partition_equal(d, a, b, pivot);
                continue;
            }
            let (mid, already_partitioned) = partition(d, a, b, pivot);
            was_partitioned = already_partitioned;
            let (left_len, right_len) = (mid - a, b - mid);
            let balance_threshold = length / 8;
            if left_len < right_len {
                was_balanced = left_len >= balance_threshold;
                pdqsort(d, a, mid, limit);
                a = mid + 1;
            } else {
                was_balanced = right_len >= balance_threshold;
                pdqsort(d, mid + 1, b, limit);
                b = mid;
            }
        }
    }

    fn partition(d: &mut dyn LessSwap, a: usize, b: usize, pivot: usize) -> (usize, bool) {
        d.swap(a, pivot);
        let (mut i, mut j) = (a as isize + 1, b as isize - 1);
        while i <= j && d.less(i as usize, a) {
            i += 1;
        }
        while i <= j && !d.less(j as usize, a) {
            j -= 1;
        }
        if i > j {
            d.swap(j as usize, a);
            return (j as usize, true);
        }
        d.swap(i as usize, j as usize);
        i += 1;
        j -= 1;
        loop {
            while i <= j && d.less(i as usize, a) {
                i += 1;
            }
            while i <= j && !d.less(j as usize, a) {
                j -= 1;
            }
            if i > j {
                break;
            }
            d.swap(i as usize, j as usize);
            i += 1;
            j -= 1;
        }
        d.swap(j as usize, a);
        (j as usize, false)
    }

    fn partition_equal(d: &mut dyn LessSwap, a: usize, b: usize, pivot: usize) -> usize {
        d.swap(a, pivot);
        let (mut i, mut j) = (a as isize + 1, b as isize - 1);
        loop {
            while i <= j && !d.less(a, i as usize) {
                i += 1;
            }
            while i <= j && d.less(a, j as usize) {
                j -= 1;
            }
            if i > j {
                break;
            }
            d.swap(i as usize, j as usize);
            i += 1;
            j -= 1;
        }
        i as usize
    }

    fn partial_insertion_sort(d: &mut dyn LessSwap, a: usize, b: usize) -> bool {
        const MAX_STEPS: usize = 5;
        const SHORTEST_SHIFTING: usize = 50;
        let mut i = a + 1;
        for _ in 0..MAX_STEPS {
            while i < b && !d.less(i, i - 1) {
                i += 1;
            }
            if i == b {
                return true;
            }
            if b - a < SHORTEST_SHIFTING {
                return false;
            }
            d.swap(i, i - 1);
            // Go shifts down to index 1 (not `a`); kept as written.
            if i - a >= 2 {
                let mut j = i - 1;
                while j >= 1 {
                    if !d.less(j, j - 1) {
                        break;
                    }
                    d.swap(j, j - 1);
                    j -= 1;
                }
            }
            if b - i >= 2 {
                let mut j = i + 1;
                while j < b {
                    if !d.less(j, j - 1) {
                        break;
                    }
                    d.swap(j, j - 1);
                    j += 1;
                }
            }
        }
        false
    }

    fn break_patterns(d: &mut dyn LessSwap, a: usize, b: usize) {
        let length = b - a;
        if length >= 8 {
            // xorshift seeded with the length; nextPowerOfTwo = 1 << bits.Len.
            let mut random = length as u64;
            let modulus = 1usize << bits_len(length);
            let mid = a + (length / 4) * 2;
            for idx in mid - 1..=mid + 1 {
                random ^= random << 13;
                random ^= random >> 7;
                random ^= random << 17;
                let mut other = (random as usize) & (modulus - 1);
                if other >= length {
                    other -= length;
                }
                d.swap(idx, a + other);
            }
        }
    }

    fn choose_pivot(d: &mut dyn LessSwap, a: usize, b: usize) -> (usize, Hint) {
        const SHORTEST_NINTHER: usize = 50;
        const MAX_SWAPS: usize = 4 * 3;
        let l = b - a;
        let mut swaps = 0;
        let mut i = a + l / 4;
        let mut j = a + l / 4 * 2;
        let mut k = a + l / 4 * 3;
        if l >= 8 {
            if l >= SHORTEST_NINTHER {
                i = median_adjacent(d, i, &mut swaps);
                j = median_adjacent(d, j, &mut swaps);
                k = median_adjacent(d, k, &mut swaps);
            }
            j = median(d, i, j, k, &mut swaps);
        }
        match swaps {
            0 => (j, Hint::Increasing),
            MAX_SWAPS => (j, Hint::Decreasing),
            _ => (j, Hint::Unknown),
        }
    }

    fn order2(d: &mut dyn LessSwap, a: usize, b: usize, swaps: &mut usize) -> (usize, usize) {
        if d.less(b, a) {
            *swaps += 1;
            return (b, a);
        }
        (a, b)
    }

    fn median(d: &mut dyn LessSwap, a: usize, b: usize, c: usize, swaps: &mut usize) -> usize {
        let (a, b) = order2(d, a, b, swaps);
        let (b, _) = order2(d, b, c, swaps);
        let (_, b) = order2(d, a, b, swaps);
        b
    }

    fn median_adjacent(d: &mut dyn LessSwap, a: usize, swaps: &mut usize) -> usize {
        median(d, a - 1, a, a + 1, swaps)
    }

    fn reverse_range(d: &mut dyn LessSwap, a: usize, b: usize) {
        let (mut i, mut j) = (a, b - 1);
        while i < j {
            d.swap(i, j);
            i += 1;
            j -= 1;
        }
    }

    fn swap_range(d: &mut dyn LessSwap, a: usize, b: usize, n: usize) {
        for i in 0..n {
            d.swap(a + i, b + i);
        }
    }

    fn stable(d: &mut dyn LessSwap, n: usize) {
        let mut block = 20;
        let (mut a, mut b) = (0, block);
        while b <= n {
            insertion_sort(d, a, b);
            a = b;
            b += block;
        }
        insertion_sort(d, a, n);
        while block < n {
            a = 0;
            b = 2 * block;
            while b <= n {
                sym_merge(d, a, a + block, b);
                a = b;
                b += 2 * block;
            }
            let m = a + block;
            if m < n {
                sym_merge(d, a, m, n);
            }
            block *= 2;
        }
    }

    fn sym_merge(d: &mut dyn LessSwap, a: usize, m: usize, b: usize) {
        if m - a == 1 {
            let (mut i, mut j) = (m, b);
            while i < j {
                let h = (i + j) >> 1;
                if d.less(h, a) {
                    i = h + 1;
                } else {
                    j = h;
                }
            }
            for k in a..i - 1 {
                d.swap(k, k + 1);
            }
            return;
        }
        if b - m == 1 {
            let (mut i, mut j) = (a, m);
            while i < j {
                let h = (i + j) >> 1;
                if !d.less(m, h) {
                    i = h + 1;
                } else {
                    j = h;
                }
            }
            let mut k = m;
            while k > i {
                d.swap(k, k - 1);
                k -= 1;
            }
            return;
        }
        let mid = (a + b) >> 1;
        let n = mid + m;
        let (mut start, mut r) = if m > mid { (n - b, mid) } else { (a, m) };
        let p = n - 1;
        while start < r {
            let c = (start + r) >> 1;
            if !d.less(p - c, c) {
                start = c + 1;
            } else {
                r = c;
            }
        }
        let end = n - start;
        if start < m && m < end {
            rotate(d, start, m, end);
        }
        if a < start && start < mid {
            sym_merge(d, a, start, mid);
        }
        if mid < end && end < b {
            sym_merge(d, mid, end, b);
        }
    }

    fn rotate(d: &mut dyn LessSwap, a: usize, m: usize, b: usize) {
        let (mut i, mut j) = (m - a, b - m);
        while i != j {
            if i > j {
                swap_range(d, m - i, m, j);
                i -= j;
            } else {
                swap_range(d, m - i, m + j - i, i);
                j -= i;
            }
        }
        swap_range(d, m - i, m, i);
    }
}

// ─── lipgloss helpers used by the PromQL views ──────────────────────────────

fn st() -> Style {
    Style::new()
}

/// PromQL views read `p := ui.Active` (fixed loaded-theme colors); only the
/// table and filter-input styles use `ui.Adaptive` (`Color::role`).
trait ActiveColors {
    fn fgr(self, role: Role) -> Self;
    fn bgr(self, role: Role) -> Self;
    fn border_fgr(self, role: Role) -> Self;
}

impl ActiveColors for Style {
    fn fgr(self, role: Role) -> Self {
        self.fg(Color::active(role))
    }
    fn bgr(self, role: Role) -> Self {
        self.bg(Color::active(role))
    }
    fn border_fgr(self, role: Role) -> Self {
        self.border_fg(Color::active(role))
    }
}
// Chart series colors (lipgloss.Color of a runtime hex) use the shared
// `Style::fg_hex`.

fn height(s: &str) -> usize {
    s.split('\n').count()
}

/// `ui.Truncate`, including Go's NUL fill and its reachable
/// `slice bounds out of range` panic for wide runes (shared style port).
/// Callers in Go clamp nothing, so a negative width is passed through.
fn ui_truncate(s: &str, n: usize) -> Result<String, GoPanic> {
    style::ui_truncate(s, n as isize)
}

/// `paneRule` from pkg/model/query.go.
fn pane_rule(
    left: &str,
    right: &str,
    title: &str,
    width: usize,
    border: &Style,
    title_style: &Style,
) -> Result<String, GoPanic> {
    let width = width.max(2);
    if title.is_empty() {
        return Ok(border.render(&format!("{left}{}{right}", "─".repeat(width - 2))));
    }
    let mut label = format!(" {title} ");
    let prefix = format!("{left}──");
    let used = style::width(&prefix) + style::width(&label) + style::width(right);
    let fill = if used > width {
        let title_w =
            (width as isize - style::width(&prefix) as isize - style::width(right) as isize - 2)
                .max(1) as usize;
        label = format!(" {} ", ui_truncate(title, title_w)?);
        0
    } else {
        width - used
    };
    Ok(format!(
        "{}{}{}",
        border.render(&prefix),
        title_style.render(&label),
        border.render(&format!("{}{right}", "─".repeat(fill)))
    ))
}

/// `promqlPaneRule` (title plus an optional right label).
fn promql_pane_rule(
    left: &str,
    right: &str,
    title: &str,
    right_text: &str,
    width: usize,
    border: &Style,
    title_style: &Style,
) -> Result<String, GoPanic> {
    let width = width.max(2);
    if title.is_empty() {
        return Ok(border.render(&format!("{left}{}{right}", "─".repeat(width - 2))));
    }
    let mut label = format!(" {title} ");
    let prefix = format!("{left}──");
    let right_label = if right_text.trim().is_empty() {
        String::new()
    } else {
        format!(" {right_text} ")
    };
    let used = style::width(&prefix)
        + style::width(&label)
        + style::width(&right_label)
        + style::width(right);
    let fill = if used > width {
        let title_w = (width as isize
            - style::width(&prefix) as isize
            - style::width(&right_label) as isize
            - style::width(right) as isize
            - 2)
        .max(1) as usize;
        label = format!(" {} ", ui_truncate(title, title_w)?);
        0
    } else {
        width - used
    };
    Ok(format!(
        "{}{}{}{}{}",
        border.render(&prefix),
        title_style.render(&label),
        border.render(&"─".repeat(fill)),
        right_label,
        border.render(right)
    ))
}

/// `paneBodyLines`.
fn pane_body_lines(body: &str, width: usize, height: usize, border: &Style) -> Vec<String> {
    let inner_w = width.saturating_sub(2).max(1);
    let text_w = inner_w.saturating_sub(2).max(1);
    let mut lines: Vec<&str> = body.split('\n').collect();
    lines.truncate(height);
    while lines.len() < height {
        lines.push("");
    }
    lines
        .into_iter()
        .map(|line| {
            let line = if style::width(line) > text_w {
                reflow_truncate(line, text_w, "")
            } else {
                line.to_owned()
            };
            let pad = text_w.saturating_sub(style::width(&line));
            format!(
                "{} {line}{} {}",
                border.render("│"),
                " ".repeat(pad),
                border.render("│")
            )
        })
        .collect()
}

fn join_vertical(parts: &[String]) -> String {
    let refs: Vec<&str> = parts.iter().map(String::as_str).collect();
    style::join_vertical(0.0, &refs)
}

fn join_horizontal_top(parts: &[&str]) -> String {
    style::join_horizontal(0.0, parts)
}

fn place_center(width: usize, height: usize, content: &str) -> String {
    style::place(width, height, 0.5, 0.5, content)
}

const fn hint(key: &'static str, label: &'static str) -> KeyHint<'static> {
    KeyHint { key, label }
}

/// `ui.KeyGrid` (shared chrome port on `ui.Active`).
fn key_grid(keys: &[KeyHint<'_>], cols: usize, avail_w: usize) -> Result<String, GoPanic> {
    style::Theme::process().key_grid(keys, cols as isize, avail_w as isize)
}

/// `wrapVisibleText` / `splitVisibleWord`.
fn wrap_visible_text(text: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return vec![text.to_owned()];
    }
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.is_empty() {
        return Vec::new();
    }
    let mut lines = Vec::new();
    let mut current = String::new();
    for word in words {
        if style::width(word) > width {
            if !current.is_empty() {
                lines.push(std::mem::take(&mut current));
            }
            let mut chunk = String::new();
            for c in word.chars() {
                let mut next = chunk.clone();
                next.push(c);
                if !chunk.is_empty() && style::width(&next) > width {
                    lines.push(std::mem::replace(&mut chunk, c.to_string()));
                    continue;
                }
                chunk = next;
            }
            if !chunk.is_empty() {
                lines.push(chunk);
            }
            continue;
        }
        if current.is_empty() {
            current = word.to_owned();
            continue;
        }
        let candidate = format!("{current} {word}");
        if style::width(&candidate) <= width {
            current = candidate;
            continue;
        }
        lines.push(std::mem::replace(&mut current, word.to_owned()));
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

fn spinner_view(frame: usize) -> String {
    st().fgr(Role::Accent)
        .render(SPINNER_FRAMES[frame % SPINNER_FRAMES.len()])
}

// ─── time picker (pkg/model/timeinput.go + datetime) ────────────────────────

const LOCAL_LAYOUT: &str = "%Y-%b-%d %H:%M:%S";
const SEGMENT_STARTS: [usize; 6] = [0, 5, 9, 12, 15, 18];
const SEGMENT_ENDS: [usize; 6] = [4, 8, 11, 14, 17, 20];
const PRESETS: [(i64, &str); 5] = [
    (-10 * 60 * NANOS_PER_SECOND, "10 Minutes"),
    (-HOUR_NANOS, "1 Hour"),
    (-5 * HOUR_NANOS, "5 Hours"),
    (-24 * HOUR_NANOS, "1 Day"),
    (-72 * HOUR_NANOS, "3 Days"),
];

fn add_nanos(t: DateTime<Local>, nanos: i64) -> DateTime<Local> {
    t + chrono::TimeDelta::nanoseconds(nanos)
}

/// time.Time.Add on a picker time; the location is kept (shared SQL `GoTime`).
fn go_add(t: GoTime, nanos: i64) -> GoTime {
    t.add(chrono::TimeDelta::nanoseconds(nanos))
}

/// `time.Now()`: the local location.
fn go_now(now: DateTime<Local>) -> GoTime {
    GoTime::local(now.with_timezone(&Utc))
}

/// Nanoseconds from `start` to `end` between Go times.
fn go_between(start: GoTime, end: GoTime) -> i64 {
    duration_nanos(start.instant, end.instant)
}

fn segment_index(pos: usize) -> usize {
    for i in 0..SEGMENT_STARTS.len() {
        if pos >= SEGMENT_STARTS[i] && pos < SEGMENT_ENDS[i] {
            return i;
        }
    }
    for (i, start) in SEGMENT_STARTS.iter().enumerate() {
        if pos < *start {
            return i;
        }
    }
    SEGMENT_STARTS.len() - 1
}

/// datetime.Model: a time plus its editable `2006-Jan-02 15:04:05` text.
/// The time keeps its Go location (`--from 2024-01-01T00:00:00Z` stays UTC
/// until a digit edit reparses it in Local), via the shared SQL `GoTime`.
#[derive(Clone, Debug)]
struct DateTimeField {
    time: GoTime,
    value: Vec<char>,
    pos: usize,
    focused: bool,
}

impl DateTimeField {
    fn new(time: GoTime) -> Self {
        let mut field = Self {
            time,
            value: Vec::new(),
            pos: 0,
            focused: false,
        };
        field.set_time(time);
        field
    }

    /// textinput.SetValue cursor rule: an empty field moves to the end.
    fn set_time(&mut self, time: GoTime) {
        self.time = time;
        let empty = self.value.is_empty();
        self.value = time.format(LOCAL_LAYOUT).chars().collect();
        if (self.pos == 0 && empty) || self.pos > self.value.len() {
            self.pos = self.value.len();
        }
    }

    fn set_cursor(&mut self, pos: usize) {
        self.pos = pos.min(self.value.len());
    }

    fn local_value(&self) -> String {
        self.value.iter().collect()
    }

    fn value_utc(&self) -> String {
        crate::time_compat::format_time(self.time.instant)
    }

    fn move_segment(&mut self, direction: isize) {
        let seg = segment_index(self.pos) as isize + direction;
        if seg < 0 || seg >= SEGMENT_STARTS.len() as isize {
            return;
        }
        self.set_cursor(SEGMENT_STARTS[seg as usize]);
    }

    fn adjust_segment(&mut self, delta: i64) {
        let seg = segment_index(self.pos);
        let t = self.time;
        // AdjustSegment: calendar segments use AddDate/addMonthsClamped in the
        // time's own location; clock segments add a Duration.
        let next = match seg {
            0 => t.add_date(delta as i32, 0, 0),
            1 => t.add_months_clamped(delta as i32),
            2 => t.add_date(0, 0, delta),
            3 => go_add(t, delta * HOUR_NANOS),
            4 => go_add(t, delta * 60 * NANOS_PER_SECOND),
            _ => go_add(t, delta * NANOS_PER_SECOND),
        };
        self.set_time(next);
        self.set_cursor(SEGMENT_STARTS[seg]);
    }

    fn word_forward(&mut self) {
        let mut i = self.pos;
        while i < self.value.len() && self.value[i].is_whitespace() {
            self.set_cursor(self.pos + 1);
            i += 1;
        }
        while i < self.value.len() && !self.value[i].is_whitespace() {
            self.set_cursor(self.pos + 1);
            i += 1;
        }
    }

    fn word_backward(&mut self) {
        if self.pos == 0 {
            return;
        }
        let mut i = self.pos as isize - 1;
        while i >= 0 && self.value[i as usize].is_whitespace() {
            self.set_cursor(self.pos - 1);
            i -= 1;
        }
        while i >= 0 && !self.value[i as usize].is_whitespace() {
            self.set_cursor(self.pos - 1);
            i -= 1;
        }
    }

    fn update(&mut self, key: &Key) {
        if !self.focused {
            return;
        }
        match key.code {
            KeyCode::Right => return self.move_segment(1),
            KeyCode::Left => return self.move_segment(-1),
            KeyCode::Up => return self.adjust_segment(1),
            KeyCode::Down => return self.adjust_segment(-1),
            _ => {}
        }
        if key.is(&["right", "ctrl+f"]) {
            return self.move_segment(1);
        }
        if key.is(&["left", "ctrl+b"]) {
            return self.move_segment(-1);
        }
        if key.is(&["alt+right", "ctrl+right", "alt+f"]) {
            return self.word_forward();
        }
        if key.is(&["alt+left", "ctrl+left", "alt+b"]) {
            return self.word_backward();
        }
        if key.is(&["home", "ctrl+a"]) {
            return self.set_cursor(0);
        }
        if key.is(&["end", "ctrl+e"]) {
            return self.set_cursor(self.value.len());
        }
        if key.runes.len() == 1 && key.runes[0].is_ascii_digit() {
            let pos = self.pos;
            if pos >= self.value.len() || !self.value[pos].is_ascii_digit() {
                return;
            }
            let mut next = self.value.clone();
            next[pos] = key.runes[0];
            let text: String = next.iter().collect();
            // time.ParseInLocation(localLayout, value, Local): the edited time
            // moves to the local location.
            if let Ok(naive) = NaiveDateTime::parse_from_str(&text, LOCAL_LAYOUT) {
                self.set_time(GoTime::from_naive(naive, Zone::Local));
                self.set_cursor(pos);
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TimeFocus {
    List,
    Start,
    End,
    Display,
}
const TIME_FOCUS: [TimeFocus; 4] = [
    TimeFocus::List,
    TimeFocus::Start,
    TimeFocus::End,
    TimeFocus::Display,
];

/// TimeInputModel.
#[derive(Clone, Debug)]
struct TimeInput {
    start: DateTimeField,
    end: DateTimeField,
    preset: usize,
    focus: usize,
    instant: bool,
    display: DisplayMode,
}

impl TimeInput {
    fn new(start: GoTime, end: GoTime) -> Self {
        let mut start = DateTimeField::new(start);
        start.focused = true;
        Self {
            start,
            end: DateTimeField::new(end),
            preset: 0,
            focus: 0,
            instant: false,
            display: DisplayMode::Local,
        }
    }

    fn current_focus(&self) -> TimeFocus {
        TIME_FOCUS[self.focus]
    }

    fn set_instant(&mut self, instant: bool) {
        self.instant = instant;
        self.focus = 0;
        self.focus_selected();
        if instant {
            self.preset = 1;
        }
    }

    fn focus_selected(&mut self) {
        self.start.focused = false;
        self.end.focused = false;
        match self.current_focus() {
            TimeFocus::Start => {
                self.start.focused = true;
                self.start.set_cursor(SEGMENT_STARTS[0]);
            }
            TimeFocus::End => {
                self.end.focused = true;
                self.end.set_cursor(SEGMENT_STARTS[0]);
            }
            _ => {}
        }
    }

    fn navigate(&mut self, forward: bool) {
        let n = TIME_FOCUS.len();
        if forward {
            self.focus = (self.focus + 1) % n;
            if self.instant && self.current_focus() == TimeFocus::Start {
                self.focus = (self.focus + 1) % n;
            }
        } else {
            self.focus = (self.focus + n - 1) % n;
            if self.instant && self.current_focus() == TimeFocus::Start {
                self.focus = (self.focus + n - 1) % n;
            }
        }
    }

    /// `SyncPreset`: nearest preset to the current span (or offset from now).
    fn sync_preset(&mut self, now: DateTime<Local>) {
        let target = if self.instant {
            round_second(go_between(go_now(now), self.end.time))
        } else {
            round_second(go_between(self.end.time, self.start.time))
        };
        let mut best = 0;
        let mut best_diff = (target as i128 - PRESETS[0].0 as i128).abs();
        for (i, (duration, _)) in PRESETS.iter().enumerate() {
            let diff = (target as i128 - *duration as i128).abs();
            if diff < best_diff {
                best = i;
                best_diff = diff;
            }
        }
        self.preset = best;
    }

    fn end_allowed(&self, now: DateTime<Local>) -> bool {
        if self.end.time.instant > now.with_timezone(&Utc) {
            return false;
        }
        self.instant || self.end.time.instant >= self.start.time.instant
    }

    fn update(&mut self, key: &Key, now: DateTime<Local>) {
        match key.code {
            KeyCode::Tab => {
                self.navigate(true);
                self.focus_selected();
            }
            KeyCode::ShiftTab => {
                self.navigate(false);
                self.focus_selected();
            }
            _ => match self.current_focus() {
                TimeFocus::List => {
                    if !matches!(key.code, KeyCode::Up | KeyCode::Down) {
                        return;
                    }
                    let previous = PRESETS[self.preset].0;
                    if key.code == KeyCode::Up {
                        self.preset = self.preset.saturating_sub(1);
                    } else {
                        self.preset = (self.preset + 1).min(PRESETS.len() - 1);
                    }
                    let duration = PRESETS[self.preset].0;
                    if self.instant {
                        let anchor = go_add(self.end.time, -previous);
                        self.end.set_time(go_add(anchor, duration));
                    } else {
                        let start = go_add(self.end.time, duration);
                        self.start.set_time(start);
                    }
                }
                TimeFocus::Start => {
                    let previous = self.start.clone();
                    self.start.update(key);
                    if self.start.time.instant > self.end.time.instant {
                        self.start = previous;
                    }
                }
                TimeFocus::End => {
                    let previous = self.end.clone();
                    self.end.update(key);
                    if !self.end_allowed(now) {
                        self.end = previous;
                    }
                }
                TimeFocus::Display => {
                    if matches!(
                        key.code,
                        KeyCode::Left
                            | KeyCode::Right
                            | KeyCode::Up
                            | KeyCode::Down
                            | KeyCode::Space
                    ) {
                        self.display = match self.display {
                            DisplayMode::Local => DisplayMode::Utc,
                            DisplayMode::Utc => DisplayMode::Local,
                        };
                    }
                }
            },
        }
    }

    /// TimeInputModel.View (72-cell modal).
    fn view(&self, now: DateTime<Local>) -> Result<String, GoPanic> {
        const WIDTH: usize = 72;
        const LEFT_W: usize = 27;
        const GAP_W: usize = 2;
        let right_w = WIDTH - 4 - LEFT_W - GAP_W;
        let border = st().fgr(Role::BorderHi);
        let title = st().fgr(Role::Accent).bold(true);
        let label = st().fgr(Role::Faint).bold(true);
        let mut lines = vec![pane_rule("┌", "┐", "QUERY TIME", WIDTH, &border, &title)?];
        let header = time_picker_join(
            &label.render("  PRESETS"),
            &label.render("CUSTOM RANGE"),
            LEFT_W,
            GAP_W,
            right_w,
        );
        let mut start_box = time_input_box(
            "start",
            &self.start,
            right_w,
            self.current_focus() == TimeFocus::Start,
            false,
        )?;
        let end_title = if self.instant {
            "evaluation time"
        } else {
            "end"
        };
        let show_now =
            (go_between(self.end.time, go_now(now)) as i128).abs() <= 2 * NANOS_PER_SECOND as i128;
        let end_box = time_input_box(
            end_title,
            &self.end,
            right_w,
            self.current_focus() == TimeFocus::End,
            show_now,
        )?;
        if self.instant {
            start_box = vec![String::new(), String::new(), String::new()];
        }
        let display_rows = time_display_mode(
            self.display,
            right_w,
            self.current_focus() == TimeFocus::Display,
            self.end.time,
        );
        let mut rows = vec![header];
        for i in 0..9 {
            let left = if i < PRESETS.len() {
                time_preset(i, self.preset, self.current_focus() == TimeFocus::List)
            } else {
                String::new()
            };
            let right = match i {
                0..=2 => start_box[i].clone(),
                3..=5 => end_box[i - 3].clone(),
                6 | 7 => display_rows[i - 6].clone(),
                _ => String::new(),
            };
            rows.push(time_picker_join(&left, &right, LEFT_W, GAP_W, right_w));
        }
        let row_count = rows.len();
        lines.extend(pane_body_lines(
            &join_vertical(&rows),
            WIDTH,
            row_count,
            &border,
        ));
        lines.push(border.render(&format!("└{}┘", "─".repeat(WIDTH - 2))));
        Ok(join_vertical(&lines))
    }
}

/// time.Duration.Round(time.Second): half away from zero.
fn round_second(nanos: i64) -> i64 {
    let s = NANOS_PER_SECOND;
    let r = nanos % s;
    let base = nanos - r;
    if r.abs() * 2 >= s {
        if nanos < 0 { base - s } else { base + s }
    } else {
        base
    }
}

fn time_preset(index: usize, selected: usize, focused: bool) -> String {
    let repr = PRESETS[index].1;
    if index != selected {
        return format!("  {}", st().fgr(Role::Body).render(repr));
    }
    if focused {
        let rail = st().bgr(Role::Active).render(" ");
        return format!("{rail} {}", st().fgr(Role::Active).bold(true).render(repr));
    }
    format!("  {}", st().fgr(Role::Accent).bold(true).render(repr))
}

fn time_display_mode(
    mode: DisplayMode,
    width: usize,
    focused: bool,
    reference: GoTime,
) -> Vec<String> {
    let label = if focused {
        st().fgr(Role::Active).bold(true)
    } else {
        st().fgr(Role::Faint).bold(true)
    };
    let active = st().fgr(Role::InvertText).bgr(Role::Active).bold(true);
    let inactive = st().fgr(Role::Body);
    let local_label = format!(" Local {} ", reference.format("UTC%:z"));
    let (local, utc) = match mode {
        DisplayMode::Utc => (inactive.render(&local_label), active.render(" UTC ")),
        DisplayMode::Local => (active.render(&local_label), inactive.render(" UTC ")),
    };
    let mut line = format!("{local}  {utc}");
    if style::width(&line) > width {
        line = reflow_truncate(&line, width, "");
    }
    vec![label.render("DISPLAY RESULTS AS"), line]
}

fn time_input_box(
    title: &str,
    input: &DateTimeField,
    width: usize,
    focused: bool,
    show_now: bool,
) -> Result<Vec<String>, GoPanic> {
    let (border_role, title_role) = if focused {
        (Role::Active, Role::Active)
    } else {
        (Role::Border, Role::Faint)
    };
    let border = st().fgr(border_role);
    let title_style = st().fgr(title_role).bold(focused);
    let top = pane_rule("┌", "┐", title, width, &border, &title_style)?;
    let value_w = width.saturating_sub(4).max(1);
    let mut value = segmented_time(&input.local_value(), input.pos, focused);
    if show_now {
        let now = st().fgr(Role::Active).bold(true).render("[NOW]");
        let plain_w = (value_w as isize - style::width(&now) as isize - 1).max(1) as usize;
        if style::width(&value) > plain_w {
            value = reflow_truncate(&value, plain_w, "");
        }
        let pad = (value_w as isize - style::width(&value) as isize - style::width(&now) as isize)
            .max(1) as usize;
        value = format!("{value}{}{now}", " ".repeat(pad));
    }
    if style::width(&value) > value_w {
        value = reflow_truncate(&value, value_w, "");
    }
    let pad = value_w.saturating_sub(style::width(&value));
    let mid = format!(
        "{} {value}{} {}",
        border.render("│"),
        " ".repeat(pad),
        border.render("│")
    );
    let bottom = border.render(&format!("└{}┘", "─".repeat(width.saturating_sub(2))));
    Ok(vec![top, mid, bottom])
}

fn segmented_time(value: &str, cursor: usize, focused: bool) -> String {
    let normal = st().fgr(Role::Body);
    if !focused || value.is_empty() {
        return normal.render(value);
    }
    let runes: Vec<char> = value.chars().collect();
    let cursor = cursor.min(runes.len() - 1);
    let (start, end) = time_segment_bounds(&runes, cursor);
    let active = st().bgr(Role::Active).fgr(Role::InvertText).bold(true);
    let part = |a: usize, b: usize| runes[a..b].iter().collect::<String>();
    format!(
        "{}{}{}",
        normal.render(&part(0, start)),
        active.render(&part(start, end)),
        normal.render(&part(end, runes.len()))
    )
}

fn time_segment_bounds(runes: &[char], cursor: usize) -> (usize, usize) {
    if runes.is_empty() {
        return (0, 0);
    }
    let segment = |c: char| c.is_ascii_digit() || c.is_ascii_alphabetic();
    let same = |a: char, b: char| {
        (a.is_ascii_digit() && b.is_ascii_digit())
            || (a.is_ascii_alphabetic() && b.is_ascii_alphabetic())
    };
    let mut cursor = cursor.min(runes.len() - 1);
    if !segment(runes[cursor]) {
        if cursor + 1 < runes.len() && segment(runes[cursor + 1]) {
            cursor += 1;
        } else if cursor > 0 && segment(runes[cursor - 1]) {
            cursor -= 1;
        }
    }
    let mut start = cursor;
    while start > 0 && same(runes[start - 1], runes[cursor]) {
        start -= 1;
    }
    let mut end = cursor + 1;
    while end < runes.len() && same(runes[end], runes[cursor]) {
        end += 1;
    }
    (start, end)
}

fn time_picker_join(
    left: &str,
    right: &str,
    left_w: usize,
    gap_w: usize,
    right_w: usize,
) -> String {
    let left = if style::width(left) > left_w {
        reflow_truncate(left, left_w, "")
    } else {
        left.to_owned()
    };
    let right = if style::width(right) > right_w {
        reflow_truncate(right, right_w, "")
    } else {
        right.to_owned()
    };
    let left_pad = left_w.saturating_sub(style::width(&left));
    let right_pad = right_w.saturating_sub(style::width(&right));
    format!(
        "{left}{}{right}{}",
        " ".repeat(left_pad + gap_w),
        " ".repeat(right_pad)
    )
}

/// `formatSQLControlTime`: the "local" form formats in the time's own Go
/// location (UTC for a `Z` CLI value), not necessarily time.Local.
fn format_control_time(t: GoTime, mode: DisplayMode) -> String {
    match mode {
        DisplayMode::Utc => t.instant.format("%d %b %Y, %H:%M | UTC").to_string(),
        DisplayMode::Local => t.format("%d %b %Y, %H:%M | UTC%:z"),
    }
}

fn result_time_label(mode: DisplayMode) -> &'static str {
    match mode {
        DisplayMode::Utc => "[UTC]",
        DisplayMode::Local => "[LOCAL]",
    }
}

// ─── results table (evertras/bubble-table v0.19.2 subset) ───────────────────

/// customBorder from pkg/model/query.go.
const TABLE_TOP: &str = " ";
const TABLE_BOTTOM: &str = "─";
const TABLE_INNER_DIVIDER: &str = "│";

#[derive(Clone, Debug)]
struct ResultsTable {
    // Row payloads are immutable; table/view snapshots own only another handle.
    rows: Arc<[Row]>,
    time_title: String,
    value_width: usize,
    /// Metric and value columns participate in filtering after the first result.
    filterable: bool,
    target_width: usize,
    page_size: usize,
    current_page: usize,
    cursor: usize,
    focused: bool,
    filter: TextInput,
}

impl ResultsTable {
    fn new(width: usize, page_size: usize, display: DisplayMode) -> Self {
        Self {
            rows: Arc::default(),
            time_title: format!("TIME {}", result_time_label(display)),
            value_width: 14,
            filterable: false,
            target_width: width,
            page_size,
            current_page: 0,
            cursor: 0,
            focused: false,
            filter: TextInput::new(),
        }
    }

    fn visible_rows(&self) -> Vec<&Row> {
        let filter = self.filter.value();
        if filter.is_empty() || !self.filterable {
            return self.rows.iter().collect();
        }
        let needle = filter.to_lowercase();
        self.rows
            .iter()
            .filter(|row| {
                row.metric.to_lowercase().contains(&needle)
                    || row.value.to_lowercase().contains(&needle)
            })
            .collect()
    }

    fn max_pages(&self) -> usize {
        let total = self.visible_rows().len();
        if self.page_size == 0 || total == 0 {
            1
        } else {
            (total - 1) / self.page_size + 1
        }
    }

    /// `expectedPageForRowIndex`: page 0 without pagination.
    fn page_for(&self, index: usize) -> usize {
        index.checked_div(self.page_size).unwrap_or(0)
    }

    fn page_last(&mut self) {
        self.current_page = self.max_pages() - 1;
        self.cursor = self.current_page * self.page_size;
    }

    fn with_rows(&mut self, rows: Arc<[Row]>) {
        self.rows = rows;
        if self.cursor >= self.rows.len() {
            self.cursor = self.rows.len().saturating_sub(1);
        }
        if self.page_size != 0 && self.max_pages() <= self.current_page {
            self.page_last();
        }
    }

    fn with_page_size(&mut self, page_size: usize) {
        self.page_size = page_size;
        let max = self.max_pages();
        if self.current_page >= max {
            self.current_page = max - 1;
        }
    }

    fn with_highlighted_row(&mut self, index: usize) {
        let visible = self.visible_rows().len();
        self.cursor = index.min(visible.saturating_sub(1));
        self.current_page = self.page_for(self.cursor);
    }

    fn highlighted_row(&self) -> Option<Row> {
        self.visible_rows().get(self.cursor).map(|r| (*r).clone())
    }

    fn update(&mut self, key: &Key) {
        if !self.focused {
            return;
        }
        if self.filter.focused() {
            self.filter.update(key);
            self.current_page = 0;
            self.cursor = 0;
            return;
        }
        let visible = self.visible_rows().len();
        if key.is(&["down", "s"]) {
            self.cursor += 1;
            if self.cursor >= visible {
                self.cursor = 0;
            }
            self.current_page = self.page_for(self.cursor);
        }
        if key.is(&["up", "w"]) {
            if self.cursor == 0 {
                self.cursor = visible.saturating_sub(1);
            } else {
                self.cursor -= 1;
            }
            self.current_page = self.page_for(self.cursor);
        }
        if key.is(&["shift+down", "S", "pgdown"]) && self.page_size != 0 && visible > self.page_size
        {
            self.current_page += 1;
            if self.current_page > self.max_pages() - 1 {
                self.current_page = 0;
            }
            self.cursor = self.current_page * self.page_size;
        }
        if key.is(&["shift+up", "W", "pgup"]) && self.page_size != 0 && visible > self.page_size {
            if self.current_page == 0 {
                self.current_page = self.max_pages() - 1;
            } else {
                self.current_page -= 1;
            }
            self.cursor = self.current_page * self.page_size;
        }
        if key.is(&["home", "ctrl+y"]) {
            self.current_page = 0;
            self.cursor = 0;
        }
        if key.is(&["end", "ctrl+v"]) {
            self.page_last();
        }
        if key.is(&["/"]) {
            self.filter.focus();
        }
        if key.is(&["esc"]) {
            self.filter.set_value("");
        }
    }

    /// Column widths after `updateColumnWidths` with one flex METRIC column.
    fn widths(&self) -> [usize; 3] {
        let fixed = TIMESTAMP_WIDTH + self.value_width;
        let flex = (self.target_width as isize - 4 - fixed as isize).max(1) as usize;
        [TIMESTAMP_WIDTH, flex, self.value_width]
    }

    /// bubble-table border styles: `BorderStyle(b)` plus the sides it turns
    /// on explicitly (`None` keeps lipgloss's implicit all-sides rule).
    fn border_style(border: Border, sides: Option<(bool, bool, bool, bool)>) -> Style {
        let style = st().border_style(border);
        match sides {
            Some((top, right, bottom, left)) => style
                .border_top(top)
                .border_right(right)
                .border_bottom(bottom)
                .border_left(left),
            None => style,
        }
    }

    /// Table.View: header plus the current page of rows. Cell styles are the
    /// result of bubble-table's Inherit chain: header (Accent, bold), row
    /// (Body, or the SelRow/Accent/bold highlight), column width, left
    /// alignment; padding is never inherited.
    fn view(&self) -> String {
        let visible = self.visible_rows();
        let widths = self.widths();
        let has_rows = !visible.is_empty();
        let titles = [self.time_title.as_str(), "METRIC", "VALUE"];
        let chars =
            |top_left, top, top_right, right, bottom_right, bottom, bottom_left, left| Border {
                top,
                bottom,
                left,
                right,
                top_left,
                top_right,
                bottom_left,
                bottom_right,
            };
        // Header border styles: multi (rows present) or single row.
        let header_borders: [Style; 3] = if has_rows {
            [
                Self::border_style(
                    chars(
                        " ",
                        TABLE_TOP,
                        " ",
                        TABLE_INNER_DIVIDER,
                        "─",
                        TABLE_BOTTOM,
                        " ",
                        "",
                    ),
                    None,
                ),
                Self::border_style(
                    chars(
                        "",
                        TABLE_TOP,
                        " ",
                        TABLE_INNER_DIVIDER,
                        "─",
                        TABLE_BOTTOM,
                        "",
                        "",
                    ),
                    Some((true, true, true, false)),
                ),
                Self::border_style(
                    chars("", TABLE_TOP, " ", "", " ", TABLE_BOTTOM, "", ""),
                    Some((true, true, true, false)),
                ),
            ]
        } else {
            [
                Self::border_style(
                    chars(
                        " ",
                        TABLE_TOP,
                        " ",
                        TABLE_INNER_DIVIDER,
                        "─",
                        TABLE_BOTTOM,
                        "─",
                        "",
                    ),
                    None,
                ),
                Self::border_style(
                    chars(
                        "",
                        TABLE_TOP,
                        " ",
                        TABLE_INNER_DIVIDER,
                        "─",
                        TABLE_BOTTOM,
                        "",
                        "",
                    ),
                    Some((true, true, true, false)),
                ),
                Self::border_style(
                    chars("", TABLE_TOP, " ", "", "─", TABLE_BOTTOM, "", ""),
                    Some((true, true, true, false)),
                ),
            ]
        };
        let mut headers = Vec::with_capacity(3);
        for (i, title) in titles.iter().enumerate() {
            let cell = header_borders[i]
                .fg(Color::role(Role::Accent))
                .bold(true)
                .width(widths[i])
                .align(style::LEFT);
            headers.push(cell.render(&limit_str(title, widths[i])));
        }
        let header_refs: Vec<&str> = headers.iter().map(String::as_str).collect();
        let mut lines = vec![style::join_horizontal(1.0, &header_refs)];
        if has_rows {
            let page_size = if self.page_size == 0 {
                visible.len()
            } else {
                self.page_size
            };
            let start = page_size * self.current_page;
            let end = (start + page_size).min(visible.len());
            let inner = [
                Self::border_style(
                    chars("", "", "", TABLE_INNER_DIVIDER, "", "", "", ""),
                    Some((false, true, false, true)),
                ),
                Self::border_style(
                    chars("", "", "", TABLE_INNER_DIVIDER, "", "", "", ""),
                    Some((false, true, false, false)),
                ),
                Self::border_style(
                    chars("", "", "", "", "", "", "", ""),
                    Some((false, true, false, false)),
                ),
            ];
            let last = [
                Self::border_style(
                    chars("", "", "", TABLE_INNER_DIVIDER, "─", TABLE_BOTTOM, "─", ""),
                    Some((false, true, true, true)),
                ),
                Self::border_style(
                    chars("", "", "", TABLE_INNER_DIVIDER, "─", TABLE_BOTTOM, "", ""),
                    Some((false, true, true, false)),
                ),
                Self::border_style(
                    chars("", "", "", "", "─", TABLE_BOTTOM, "", ""),
                    Some((false, true, true, false)),
                ),
            ];
            for (index, row) in visible.iter().enumerate().take(end).skip(start) {
                let highlighted = self.focused && index == self.cursor;
                let is_last = index + 1 == end;
                let data = [
                    row.timestamp.as_str(),
                    row.metric.as_str(),
                    row.value.as_str(),
                ];
                let mut cells = Vec::with_capacity(3);
                let borders = if is_last { &last } else { &inner };
                for ((border, width), text) in borders.iter().zip(widths).zip(data) {
                    let border = *border;
                    let cell = if highlighted {
                        border
                            .bg(Color::role(Role::SelRow))
                            .fg(Color::role(Role::Accent))
                            .bold(true)
                    } else {
                        border.fg(Color::role(Role::Body))
                    };
                    let cell = cell.height(1).width(width).align(style::LEFT);
                    cells.push(cell.render(&limit_str(text, width)));
                }
                let refs: Vec<&str> = cells.iter().map(String::as_str).collect();
                lines.push(style::join_horizontal(1.0, &refs));
            }
        }
        join_vertical(&lines)
    }
}

/// bubble-table `limitStr`.
fn limit_str(s: &str, max: usize) -> String {
    if max == 0 {
        return String::new();
    }
    let mut s = s.to_owned();
    if let Some(i) = s.find('\n') {
        s.truncate(i);
        s.push('…');
    }
    if style::width(&s) > max {
        return reflow_truncate(&s, max, "…");
    }
    s
}

// ─── chart (ntcharts v0.5.1 timeserieslinechart subset) ─────────────────────

#[derive(Clone, Debug, PartialEq)]
enum CellColor {
    Role(Role),
    Hex(String),
}

#[derive(Clone, Debug, Default)]
struct Cell {
    ch: char,
    color: Option<CellColor>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct Point {
    x: isize,
    y: isize,
}

#[derive(Clone, Debug)]
struct Canvas {
    width: usize,
    height: usize,
    cells: Vec<Cell>,
}

impl Canvas {
    fn new(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            cells: vec![Cell::default(); width * height],
        }
    }
    fn index(&self, p: Point) -> Option<usize> {
        if p.x < 0 || p.y < 0 || p.x as usize >= self.width || p.y as usize >= self.height {
            return None;
        }
        Some(p.y as usize * self.width + p.x as usize)
    }
    fn cell(&self, p: Point) -> char {
        self.index(p).map_or('\0', |i| self.cells[i].ch)
    }
    fn set(&mut self, p: Point, ch: char, color: Option<CellColor>) {
        if let Some(i) = self.index(p) {
            self.cells[i] = Cell { ch, color };
        }
    }
    fn set_string(&mut self, p: Point, s: &str, color: Option<CellColor>) {
        if p.y < 0 || p.y as usize >= self.height {
            return;
        }
        for (offset, ch) in s.chars().enumerate() {
            self.set(
                Point {
                    x: p.x + offset as isize,
                    y: p.y,
                },
                ch,
                color.clone(),
            );
        }
    }
    /// canvas.Model.View: every cell rendered with its own style.
    fn view(&self) -> String {
        let mut cache: HashMap<Option<CellColorKey>, Style> = HashMap::new();
        let mut out = String::new();
        for y in 0..self.height {
            for x in 0..self.width {
                let cell = &self.cells[y * self.width + x];
                let text = if cell.ch == '\0' { ' ' } else { cell.ch };
                let key = cell.color.as_ref().map(CellColorKey::from);
                let style = cache.entry(key).or_insert_with(|| match &cell.color {
                    None => st(),
                    Some(CellColor::Role(role)) => st().fgr(*role),
                    Some(CellColor::Hex(hex)) => st().fg_hex(hex),
                });
                out.push_str(&style.render(&text.to_string()));
            }
            if y + 1 != self.height {
                out.push('\n');
            }
        }
        out
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum CellColorKey {
    Role(String),
    Hex(String),
}
impl From<&CellColor> for CellColorKey {
    fn from(color: &CellColor) -> Self {
        match color {
            CellColor::Role(role) => Self::Role(format!("{role:?}")),
            CellColor::Hex(hex) => Self::Hex(hex.clone()),
        }
    }
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
struct Segments {
    up: bool,
    down: bool,
    left: bool,
    right: bool,
}

fn is_line(r: char) -> bool {
    let c = r as u32;
    (0x2500..=0x253C).contains(&c)
        || (0x256D..=0x2570).contains(&c)
        || (0x2574..=0x2577).contains(&c)
}

fn line_segments(r: char, s: &mut Segments) {
    match r {
        '╭' | '┌' => (s.down, s.right) = (true, true),
        '╮' | '┐' => (s.down, s.left) = (true, true),
        '╯' | '┘' => (s.up, s.left) = (true, true),
        '╰' | '└' => (s.up, s.right) = (true, true),
        '─' => (s.left, s.right) = (true, true),
        '│' => (s.up, s.down) = (true, true),
        '┴' => (s.up, s.left, s.right) = (true, true, true),
        '┬' => (s.down, s.left, s.right) = (true, true, true),
        '├' => (s.up, s.down, s.right) = (true, true, true),
        '┤' => (s.up, s.down, s.left) = (true, true, true),
        '┼' => (s.up, s.down, s.left, s.right) = (true, true, true, true),
        '╵' => s.up = true,
        '╷' => s.down = true,
        '╴' => s.left = true,
        '╶' => s.right = true,
        _ => {}
    }
}

fn thin_line(s: Segments) -> char {
    match (s.up, s.down, s.left, s.right) {
        (false, false, false, false) => '\0',
        (false, false, false, true) => '╶',
        (false, false, true, false) => '╴',
        (false, false, true, true) => '─',
        (false, true, false, false) => '╷',
        (false, true, false, true) => '┌',
        (false, true, true, false) => '┐',
        (false, true, true, true) => '┬',
        (true, false, false, false) => '╵',
        (true, false, false, true) => '└',
        (true, false, true, false) => '┘',
        (true, false, true, true) => '┴',
        (true, true, false, false) => '│',
        (true, true, false, true) => '├',
        (true, true, true, false) => '┤',
        (true, true, true, true) => '┼',
    }
}

fn arc_line(s: Segments) -> char {
    match (s.up, s.down, s.left, s.right) {
        (false, true, false, true) => '╭',
        (false, true, true, false) => '╮',
        (true, false, false, true) => '╰',
        (true, false, true, false) => '╯',
        _ => thin_line(s),
    }
}

/// graph.DrawLineRune with runes.ThinLineStyle.
fn draw_line_rune(canvas: &mut Canvas, p: Point, r: char, color: &CellColor) {
    if r == '\0' || !is_line(r) {
        return;
    }
    let existing = canvas.cell(p);
    let mut segments = Segments::default();
    if is_line(existing) {
        line_segments(existing, &mut segments);
    }
    line_segments(r, &mut segments);
    canvas.set(p, thin_line(segments), Some(color.clone()));
}

/// graph.DrawLinePoints.
fn draw_line_points(canvas: &mut Canvas, points: &[Point], color: &CellColor) {
    if points.len() < 2 {
        return;
    }
    let mut extra: Vec<(Point, char)> = Vec::new();
    let mut dir = vec![Segments::default(); points.len()];
    for i in 1..points.len() {
        let (p, prev) = (points[i], points[i - 1]);
        if p.x > prev.x {
            dir[i - 1].right = true;
            if p.y > prev.y {
                dir[i].up = true;
                extra.push((Point { x: p.x, y: p.y - 1 }, '╮'));
            } else if p.y < prev.y {
                dir[i].down = true;
                extra.push((Point { x: p.x, y: p.y + 1 }, '╯'));
            } else {
                dir[i].left = true;
            }
        } else if p.x < prev.x {
            dir[i - 1].left = true;
            if p.y > prev.y {
                dir[i].up = true;
                extra.push((Point { x: p.x, y: p.y - 1 }, '╭'));
            } else if p.y < prev.y {
                dir[i].down = true;
                extra.push((Point { x: p.x, y: p.y + 1 }, '╰'));
            } else {
                dir[i].right = true;
            }
        } else if p.y > prev.y {
            dir[i - 1].down = true;
            dir[i].up = true;
        } else if p.y < prev.y {
            dir[i - 1].up = true;
            dir[i].down = true;
        }
    }
    for (i, segments) in dir.iter().enumerate() {
        draw_line_rune(canvas, points[i], arc_line(*segments), color);
    }
    for (p, r) in extra {
        draw_line_rune(canvas, p, r, color);
    }
}

/// graph.GetLinePoints (Bresenham).
fn line_points(p1: Point, p2: Point) -> Vec<Point> {
    if (p2.y - p1.y).abs() < (p2.x - p1.x).abs() {
        if p1.x > p2.x {
            line_low(p2, p1)
        } else {
            line_low(p1, p2)
        }
    } else if p1.y > p2.y {
        line_high(p2, p1)
    } else {
        line_high(p1, p2)
    }
}

fn line_low(p1: Point, p2: Point) -> Vec<Point> {
    let dx = p2.x - p1.x;
    let mut dy = p2.y - p1.y;
    let mut yi = 1;
    if dy < 0 {
        yi = -1;
        dy = -dy;
    }
    let mut d = 2 * dy - dx;
    let mut y = p1.y;
    let (start, end) = (p1.x.min(p2.x), p1.x.max(p2.x));
    let mut out = Vec::new();
    for x in start..=end {
        out.push(Point { x, y });
        if d > 0 {
            y += yi;
            d += 2 * (dy - dx);
        } else {
            d += 2 * dy;
        }
    }
    out
}

fn line_high(p1: Point, p2: Point) -> Vec<Point> {
    let mut dx = p2.x - p1.x;
    let dy = p2.y - p1.y;
    let mut xi = 1;
    if dx < 0 {
        xi = -1;
        dx = -dx;
    }
    let mut d = 2 * dx - dy;
    let mut x = p1.x;
    let (start, end) = (p1.y.min(p2.y), p1.y.max(p2.y));
    let mut out = Vec::new();
    for y in start..=end {
        out.push(Point { x, y });
        if d > 0 {
            x += xi;
            d += 2 * (dx - dy);
        } else {
            d += 2 * dx;
        }
    }
    out
}

/// `promqlChartSeries`: label, full timestamps and parsed values.
type RawSeries = (String, Vec<String>, Vec<f64>);

#[derive(Clone, Debug, PartialEq)]
struct TimePoint {
    time: DateTime<Local>,
    value: f64,
}

#[derive(Clone, Debug, PartialEq)]
struct ChartSeries {
    label: String,
    color: String,
    points: Vec<TimePoint>,
}

/// A built chart (timeserieslinechart.Model after drawing).
#[derive(Clone, Debug)]
struct Chart {
    canvas: Canvas,
    origin: Point,
    graph_width: isize,
    view_min_x: f64,
    view_max_x: f64,
    view_min_y: f64,
    view_max_y: f64,
}

fn unix_time(seconds: f64) -> DateTime<Local> {
    local_time(DateTime::<Utc>::from_timestamp(go_f64_to_i64(seconds), 0).unwrap_or_default())
}

/// `int(math.Round(v))`: half away from zero, then Go's float→int
/// conversion (+Inf/NaN → MinInt64 on linux/amd64; see `go_f64_to_i64`).
fn round_go(value: f64) -> isize {
    go_f64_to_i64(value.round()) as isize
}

impl Chart {
    fn canvas_x(&self, t: DateTime<Local>) -> isize {
        self.origin.x
            + chart_graph_x(
                t,
                unix_time(self.view_min_x),
                unix_time(self.view_max_x),
                self.graph_width,
            )
    }
    fn canvas_y(&self, value: f64) -> isize {
        let dy = self.view_max_y - self.view_min_y;
        if dy <= 0.0 {
            return self.origin.y;
        }
        let y =
            round_go((value - self.view_min_y) * self.origin.y as f64 / dy).clamp(0, self.origin.y);
        self.origin.y - y
    }
}

fn chart_graph_x(
    current: DateTime<Local>,
    first: DateTime<Local>,
    last: DateTime<Local>,
    graph_width: isize,
) -> isize {
    if graph_width <= 0 {
        return 0;
    }
    let total = duration_nanos(first, last);
    if total <= 0 {
        return 0;
    }
    let elapsed = duration_nanos(first, current).clamp(0, total);
    round_go(elapsed as f64 / total as f64 * graph_width as f64)
}

/// `chartSeriesColor`.
fn chart_series_color(index: usize) -> String {
    if let Some(hex) = CHART_SERIES_PALETTE.get(index) {
        return (*hex).to_owned();
    }
    let hue = (23.0 + index as f64 * 137.508) % 360.0 / 360.0;
    let (r, g, b) = hsl_to_rgb(hue, 0.82, 0.56);
    format!("#{r:02X}{g:02X}{b:02X}")
}

fn hsl_to_rgb(h: f64, s: f64, l: f64) -> (u8, u8, u8) {
    let (r, g, b) = if s == 0.0 {
        (l, l, l)
    } else {
        let q = if l < 0.5 {
            l * (1.0 + s)
        } else {
            l + s - l * s
        };
        let p = 2.0 * l - q;
        (
            hue_to_rgb(p, q, h + 1.0 / 3.0),
            hue_to_rgb(p, q, h),
            hue_to_rgb(p, q, h - 1.0 / 3.0),
        )
    };
    let c = |v: f64| (v * 255.0).round() as u8;
    (c(r), c(g), c(b))
}

fn hue_to_rgb(p: f64, q: f64, mut t: f64) -> f64 {
    if t < 0.0 {
        t += 1.0;
    }
    if t > 1.0 {
        t -= 1.0;
    }
    if t < 1.0 / 6.0 {
        p + (q - p) * 6.0 * t
    } else if t < 0.5 {
        q
    } else if t < 2.0 / 3.0 {
        p + (q - p) * (2.0 / 3.0 - t) * 6.0
    } else {
        p
    }
}

/// `formatCompactChartValue`.
pub(crate) fn format_compact(value: f64) -> String {
    let abs = value.abs();
    if abs >= 1_000_000.0 {
        return format!("{}M", go_fixed1(value / 1_000_000.0)).replacen(".0M", "M", 1);
    }
    if abs >= 1000.0 {
        return format!("{}k", go_fixed1(value / 1000.0)).replacen(".0k", "k", 1);
    }
    let formatted = go_fixed1(value);
    formatted
        .strip_suffix(".0")
        .map(str::to_owned)
        .unwrap_or(formatted)
}

/// Go `fmt.Sprintf("%.1f", v)`: specials print as `+Inf`, `-Inf` and `NaN`
/// (Rust prints `inf`); finite values round exactly in both.
fn go_fixed1(value: f64) -> String {
    if value.is_nan() {
        "NaN".to_owned()
    } else if value.is_infinite() {
        if value > 0.0 { "+Inf" } else { "-Inf" }.to_owned()
    } else {
        format!("{value:.1}")
    }
}

fn nice_step(step: f64) -> f64 {
    if step <= 0.0 || step.is_nan() {
        return 1.0;
    }
    let magnitude = 10f64.powf(step.log10().floor());
    let normalized = step / magnitude;
    if normalized <= 1.0 {
        magnitude
    } else if normalized <= 2.0 {
        2.0 * magnitude
    } else if normalized <= 2.5 {
        2.5 * magnitude
    } else if normalized <= 5.0 {
        5.0 * magnitude
    } else {
        10.0 * magnitude
    }
}

fn y_row_step(graph_height: isize) -> isize {
    if graph_height >= 22 {
        2
    } else if graph_height >= 14 {
        3
    } else {
        4
    }
}

/// `chartYAxisScale` → (min, max, row step).
fn y_axis_scale(points: &[TimePoint], height: isize) -> (f64, f64, isize) {
    let mut min = points[0].value;
    let mut max = points[0].value;
    for p in &points[1..] {
        if p.value < min {
            min = p.value;
        }
        if p.value > max {
            max = p.value;
        }
    }
    let mut range = max - min;
    if range == 0.0 {
        range = max.abs() * 0.05;
        if range == 0.0 {
            range = 1.0;
        }
        min -= range / 2.0;
        max += range / 2.0;
    }
    let graph_height = (height - 2).max(1);
    let row_step = y_row_step(graph_height);
    let intervals = (graph_height as f64 / row_step as f64).max(1.0);
    let mut padding = range * 0.05;
    if padding == 0.0 {
        padding = 1.0;
    }
    let padded_min = min - padding;
    let padded_max = max + padding;
    let mut step = nice_step((padded_max - padded_min) / intervals);
    let span = |step: f64| step * graph_height as f64 / row_step as f64;
    let mut nice_min = (padded_min / step).floor() * step;
    let mut nice_max = nice_min + span(step);
    if min >= 0.0 && nice_min < 0.0 {
        nice_min = 0.0;
        nice_max = nice_min + span(step);
    }
    while nice_max < padded_max {
        step = nice_step(step * 1.01);
        nice_min = (padded_min / step).floor() * step;
        if min >= 0.0 && nice_min < 0.0 {
            nice_min = 0.0;
        }
        nice_max = nice_min + span(step);
    }
    (nice_min, nice_max, row_step)
}

/// linechart getGraphSizeAndOrigin.
fn graph_size(
    width: isize,
    height: isize,
    min_y: f64,
    max_y: f64,
    y_step: isize,
    fmt: fn(f64) -> String,
) -> (Point, isize) {
    let mut origin = Point {
        x: 0,
        y: height - 1,
    };
    let mut graph_w = width;
    let graph_h = height - 2;
    origin.y -= 1;
    let mut value_len = 0;
    let mut last = String::new();
    let increment = (max_y - min_y) / graph_h as f64;
    let mut i = 0;
    while i <= graph_h {
        let s = fmt(min_y + increment * i as f64);
        if s != last {
            value_len = value_len.max(s.len() as isize);
            last = s;
        }
        i += y_step;
    }
    origin.x += value_len;
    graph_w -= value_len + 1;
    (origin, graph_w)
}

/// `formatChartAxisLabel` and friends.
fn is_date_range(duration_nanos: i64) -> bool {
    duration_nanos >= 48 * HOUR_NANOS
}
fn display_time(t: DateTime<Local>, mode: DisplayMode) -> chrono::DateTime<chrono::FixedOffset> {
    match mode {
        DisplayMode::Utc => t.with_timezone(&Utc).fixed_offset(),
        DisplayMode::Local => t.fixed_offset(),
    }
}
fn format_range_time(t: chrono::DateTime<chrono::FixedOffset>, duration: i64) -> String {
    if is_date_range(duration) {
        t.format("%b %-d, %Y").to_string()
    } else {
        t.format("%-I:%M%P").to_string()
    }
}
fn format_axis_label(
    t: DateTime<Local>,
    duration: i64,
    graph_width: isize,
    mode: DisplayMode,
) -> String {
    let t = display_time(t, mode);
    if is_date_range(duration) && graph_width < (CHART_X_LABEL_COUNT * 12) as isize {
        return t.format("%b %-d").to_string();
    }
    format_range_time(t, duration)
}

// ─── state ──────────────────────────────────────────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Pane {
    Query,
    Time,
    Dataset,
    Step,
    Table,
}
const PANES: [Pane; 5] = [
    Pane::Query,
    Pane::Time,
    Pane::Dataset,
    Pane::Step,
    Pane::Table,
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Overlay {
    None,
    Time,
    Dataset,
    Builder,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum EditorMode {
    Code,
    Builder,
}

/// Effects returned by `update`, executed by the runner in order.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Effect {
    /// spinner.Tick: deliver `Completion::Tick { tag }` immediately.
    Tick {
        tag: u64,
    },
    /// The spinner's follow-up tick after 100 ms.
    TickAfter {
        tag: u64,
    },
    FetchResult(ResultRequest),
    FetchDatasets,
    FetchCacheMetrics {
        dataset: String,
    },
    FetchLabels {
        id: u64,
        dataset: String,
        metric: String,
        start: String,
        end: String,
    },
    FetchValues {
        id: u64,
        dataset: String,
        metric: String,
        label: String,
        start: String,
        end: String,
    },
    /// context.CancelFunc of an in-flight builder request.
    Cancel {
        id: u64,
    },
    /// bubbles Paste command started by a focused editor's Ctrl+V binding.
    ReadClipboard,
    Quit,
}

/// Typed completions of the effects above.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Completion {
    Tick {
        tag: u64,
    },
    Result(Result<ResultData, String>),
    Datasets(Result<Vec<String>, String>),
    CacheMetrics {
        dataset: String,
        result: Result<Vec<String>, String>,
    },
    Labels {
        id: u64,
        metric: String,
        result: Result<Vec<String>, String>,
    },
    Values {
        id: u64,
        metric: String,
        label: String,
        result: Result<Vec<String>, String>,
    },
    /// pasteMsg / pasteErrMsg. PromqlModel.Update has no case for either, so
    /// the text is never inserted (the read itself still runs).
    Paste(Result<String, String>),
}

#[derive(Clone, Debug)]
pub(crate) enum Message {
    Key(Key),
    Resize { width: usize, height: usize },
    Done(Completion),
}

/// Inputs to `PromqlState::new` (NewPromqlModel arguments).
pub(crate) struct Setup {
    pub(crate) profile_url: String,
    pub(crate) expr: String,
    /// CLI times keep their parsed Go location (see `parse_cli_time`).
    pub(crate) start: GoTime,
    pub(crate) end: GoTime,
    pub(crate) step: String,
    pub(crate) dataset: String,
    pub(crate) instant: bool,
    pub(crate) width: usize,
    pub(crate) height: usize,
}

#[derive(Clone)]
pub(crate) struct PromqlState {
    width: usize,
    height: usize,
    table: ResultsTable,
    query: TextArea,
    time_range: TimeInput,
    time_range_edit: TimeInput,
    profile_url: String,
    status_error: String,
    spinner_frame: usize,
    spinner_tag: u64,

    loading: bool,
    has_queried: bool,
    overlay: Overlay,
    focused: usize,
    data_rows: Arc<[Row]>,
    chart_rows: Arc<[Row]>,
    fetch_err: String,
    last_result_type: String,
    chart_mode: bool,
    unwrap_metric: bool,
    chart_graph_width: Option<isize>,
    chart_cursor: usize,
    chart_hover: bool,

    dataset: String,
    step: String,
    mode: QueryMode,

    step_input: TextInput,

    spotlight_filter: TextInput,
    all_datasets: Vec<String>,
    filtered_datasets: Vec<String>,
    dataset_idx: usize,
    datasets_loading: bool,

    cache_dataset: String,
    cache_metrics: Vec<String>,
    cache_labels: HashMap<String, Vec<String>>,
    cache_values: HashMap<String, HashMap<String, Vec<String>>>,

    builder_col: usize,
    builder_metric: String,
    builder_label: String,
    builder_metrics: Vec<String>,
    builder_labels: Vec<String>,
    builder_values: Vec<String>,
    builder_metrics_filtered: Vec<String>,
    builder_labels_filtered: Vec<String>,
    builder_values_filtered: Vec<String>,
    builder_metrics_idx: usize,
    builder_labels_idx: usize,
    builder_values_idx: usize,
    builder_metrics_loading: bool,
    builder_labels_loading: bool,
    builder_values_loading: bool,
    builder_filter: TextInput,
    cancel_labels: Option<u64>,
    cancel_values: Option<u64>,
    next_request: u64,

    editor_mode: EditorMode,
    clock: fn() -> DateTime<Local>,
    view_fault: std::cell::Cell<Option<super::editor::EditorFault>>,
    render_fault: std::cell::RefCell<Option<GoPanic>>,
}

/// A Go runtime fault on the event-loop goroutine (Update or View).
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Fault {
    /// A recovered panic value, printed by Bubble Tea as `Caught panic`.
    Panic(String),
    /// Go spins inside Update forever.
    Hang,
}

fn panel_widths(total: usize) -> (usize, usize) {
    if total >= 140 {
        (22, 30)
    } else if total < 100 {
        (18, 26)
    } else {
        (20, 28)
    }
}

/// `promqlPageSize`.
fn page_size_for(total_h: usize, bottom_h: usize) -> usize {
    let available = (total_h as isize - TOP_H as isize - bottom_h as isize).max(6);
    let inner = (available - 3).max(3);
    (inner - 5).max(1) as usize
}

fn builder_col_width(width: usize) -> usize {
    ((width as isize - 14) / 3).max(18) as usize
}

/// `filterDatasets`.
fn filter_datasets(all: &[String], query: &str) -> Vec<String> {
    if query.is_empty() {
        return all.to_vec();
    }
    let q = query.to_lowercase();
    all.iter()
        .filter(|d| d.to_lowercase().contains(&q))
        .cloned()
        .collect()
}

/// `filterBuilderList`: keeps "(any)" first when present.
fn filter_builder_list(all: &[String], query: &str) -> Vec<String> {
    if query.is_empty() {
        return all.to_vec();
    }
    let q = query.to_lowercase();
    let filtered = all
        .iter()
        .filter(|i| i.as_str() != ANY && i.to_lowercase().contains(&q))
        .cloned();
    if all.first().is_some_and(|f| f == ANY) {
        std::iter::once(ANY.to_owned()).chain(filtered).collect()
    } else {
        filtered.collect()
    }
}

fn escape_promql_value(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\t', "\\t")
}

/// `buildPromqlExpr`.
pub(crate) fn build_expr(metric: &str, label: &str, value: &str) -> String {
    if metric.is_empty() {
        return String::new();
    }
    if label.is_empty() || label == ANY {
        return metric.to_owned();
    }
    if value.is_empty() || value == ANY {
        return format!("{metric}{{{label}!=\"\"}}");
    }
    format!("{metric}{{{label}=\"{}\"}}", escape_promql_value(value))
}

fn current_of(list: &[String], idx: usize) -> String {
    if list.is_empty() {
        return String::new();
    }
    list[idx.min(list.len() - 1)].clone()
}

/// Spotlight and builder filters: `ui.Adaptive` prompt/placeholder styles.
fn filter_input_styles() -> InputStyles {
    InputStyles {
        prompt: st().fg(Color::role(Role::Accent)).bold(true),
        placeholder: st().fg(Color::role(Role::Ghost)).italic(true),
        ..InputStyles::default()
    }
}

impl PromqlState {
    pub(crate) fn new(setup: Setup) -> Self {
        Self::with_clock(setup, Local::now)
    }

    pub(crate) fn with_clock(setup: Setup, clock: fn() -> DateTime<Local>) -> Self {
        let Setup {
            profile_url,
            expr,
            start,
            end,
            step,
            dataset,
            instant,
            width,
            height,
        } = setup;
        let step = if step.trim().is_empty() || step == "1m" {
            AUTO_STEP.to_owned()
        } else {
            step
        };
        let mut time_range = TimeInput::new(start, end);
        time_range.set_instant(instant);
        let mode = if instant {
            QueryMode::Instant
        } else {
            QueryMode::Range
        };
        let page_size = page_size_for(height, 3).max(5);
        let table = ResultsTable::new(width, page_size, time_range.display);

        // NewPromqlModel's textarea setup, in Go order (default 400-cell limit kept).
        let mut query = TextArea::new();
        query.set_max_height(0);
        query.set_max_width(0);
        query.set_height(1);
        query.set_show_line_numbers(false);
        query.set_end_of_buffer(' ');
        query.set_value(&expr);
        query.set_placeholder("Write your queries here");
        query.set_keymap(&PB_AREA_KEYS);
        query.set_prompt("  ");
        let (lw, rw) = panel_widths(width);
        let editor_w = (width as isize - lw as isize - rw as isize - 2).max(30) as usize;
        query.set_width((editor_w - 6).max(24));
        query.set_value(&expr);
        query.focus();
        // PromqlModel never routes cursor blink messages, so no editor blinks.
        query.disarm_blink();

        let mut step_input = TextInput::new();
        step_input.set_prompt("");
        step_input.set_value(&step);
        step_input.set_width(4);
        step_input.blur();

        let mut spotlight_filter = TextInput::new();
        spotlight_filter.set_placeholder("filter datasets");
        spotlight_filter.set_prompt("> ");
        spotlight_filter.set_width(SPOTLIGHT_WIDTH - 8);
        spotlight_filter.blur();

        let mut builder_filter = TextInput::new();
        builder_filter.set_placeholder("filter");
        builder_filter.set_prompt("> ");
        builder_filter.set_width(30);
        builder_filter.blur();

        let has_query = !expr.trim().is_empty() && !dataset.is_empty();
        Self {
            width,
            height,
            table,
            query,
            time_range_edit: time_range.clone(),
            time_range,
            profile_url,
            status_error: String::new(),
            spinner_frame: 0,
            spinner_tag: 0,
            loading: has_query,
            has_queried: has_query,
            overlay: Overlay::None,
            focused: 0,
            data_rows: Arc::default(),
            chart_rows: Arc::default(),
            fetch_err: String::new(),
            last_result_type: String::new(),
            chart_mode: true,
            unwrap_metric: false,
            chart_graph_width: None,
            chart_cursor: 0,
            chart_hover: false,
            dataset,
            step,
            mode,
            step_input,
            spotlight_filter,
            all_datasets: Vec::new(),
            filtered_datasets: Vec::new(),
            dataset_idx: 0,
            datasets_loading: false,
            cache_dataset: String::new(),
            cache_metrics: Vec::new(),
            cache_labels: HashMap::new(),
            cache_values: HashMap::new(),
            builder_col: 0,
            builder_metric: String::new(),
            builder_label: String::new(),
            builder_metrics: Vec::new(),
            builder_labels: Vec::new(),
            builder_values: Vec::new(),
            builder_metrics_filtered: Vec::new(),
            builder_labels_filtered: Vec::new(),
            builder_values_filtered: Vec::new(),
            builder_metrics_idx: 0,
            builder_labels_idx: 0,
            builder_values_idx: 0,
            builder_metrics_loading: false,
            builder_labels_loading: false,
            builder_values_loading: false,
            builder_filter,
            cancel_labels: None,
            cancel_values: None,
            next_request: 0,
            editor_mode: EditorMode::Code,
            clock,
            view_fault: std::cell::Cell::new(None),
            render_fault: std::cell::RefCell::new(None),
        }
    }

    pub(crate) fn focus(&self) -> Pane {
        PANES[self.focused]
    }

    // ── read-only accessors for reducer tests ──

    #[cfg(test)]
    pub(crate) fn overlay(&self) -> Overlay {
        self.overlay
    }
    #[cfg(test)]
    pub(crate) fn query_text(&self) -> String {
        self.query.value()
    }
    #[cfg(test)]
    pub(crate) fn mode(&self) -> QueryMode {
        self.mode
    }
    #[cfg(test)]
    pub(crate) fn dataset(&self) -> &str {
        &self.dataset
    }
    #[cfg(test)]
    pub(crate) fn chart_mode(&self) -> bool {
        self.chart_mode
    }
    #[cfg(test)]
    pub(crate) fn unwrap_metric(&self) -> bool {
        self.unwrap_metric
    }
    #[cfg(test)]
    pub(crate) fn editor_mode(&self) -> EditorMode {
        self.editor_mode
    }

    fn now(&self) -> DateTime<Local> {
        (self.clock)()
    }

    /// A Go panic or hang point reached on the event-loop goroutine by the
    /// last Update or View: an editor fault or a `ui.truncate` panic.
    pub(crate) fn take_fault(&self) -> Option<Fault> {
        let editor = [
            self.query.take_fault(),
            self.step_input.take_fault(),
            self.spotlight_filter.take_fault(),
            self.builder_filter.take_fault(),
            self.table.filter.take_fault(),
            self.view_fault.take(),
        ]
        .into_iter()
        .flatten()
        .next()
        .map(|fault| match fault {
            super::editor::EditorFault::Hang => Fault::Hang,
            other => Fault::Panic(other.to_string()),
        });
        let render = self
            .render_fault
            .borrow_mut()
            .take()
            .map(|p| Fault::Panic(p.0));
        editor.or(render)
    }

    /// `Init`.
    pub(crate) fn init(&self) -> Vec<Effect> {
        let mut effects = vec![Effect::Tick {
            tag: self.spinner_tag,
        }];
        if !self.query.value().trim().is_empty() && !self.dataset.is_empty() {
            effects.push(Effect::FetchResult(self.result_request()));
        }
        if !self.dataset.is_empty() {
            effects.push(Effect::FetchCacheMetrics {
                dataset: self.dataset.clone(),
            });
        }
        effects.push(Effect::FetchDatasets);
        effects
    }

    fn result_request(&self) -> ResultRequest {
        ResultRequest {
            expr: self.query.value(),
            dataset: self.dataset.clone(),
            step: self.step.clone(),
            start: self.time_range.start.value_utc(),
            end: self.time_range.end.value_utc(),
            mode: self.mode,
            display: self.time_range.display,
        }
    }

    fn focus_selected(&mut self) {
        self.query.blur();
        self.table.focused = false;
        self.spotlight_filter.blur();
        self.step_input.blur();
        match self.focus() {
            Pane::Query => self.query.focus(),
            Pane::Step => self.step_input.focus(),
            Pane::Table => self.table.focused = true,
            _ => {}
        }
    }

    fn focus_pane(&mut self, pane: Pane) {
        self.focused = PANES.iter().position(|p| *p == pane).unwrap_or(0);
        self.focus_selected();
    }

    fn query_width(&self) -> usize {
        let (lw, rw) = panel_widths(self.width);
        let editor_w = (self.width as isize - lw as isize - rw as isize - 2).max(30) as usize;
        (editor_w - 6).max(24)
    }

    fn normalize_step_for_run(&mut self) {
        let normalized = normalize_step(&self.step);
        if normalized == self.step {
            return;
        }
        self.step = normalized.clone();
        self.step_input.set_value(&normalized);
        self.step_input.cursor_end();
    }

    /// The common "start loading and fetch" tail of several Go branches.
    fn run_query(&mut self, effects: &mut Vec<Effect>) {
        self.normalize_step_for_run();
        self.status_error.clear();
        self.loading = true;
        self.has_queried = true;
        effects.push(Effect::Tick {
            tag: self.spinner_tag,
        });
        effects.push(Effect::FetchResult(self.result_request()));
    }

    fn open_dataset_overlay(&mut self) -> Vec<Effect> {
        self.overlay = Overlay::Dataset;
        self.spotlight_filter.focus();
        self.datasets_loading = true;
        vec![Effect::FetchDatasets]
    }

    fn builder_current_metric(&self) -> String {
        current_of(&self.builder_metrics_filtered, self.builder_metrics_idx)
    }
    fn builder_current_label(&self) -> String {
        current_of(&self.builder_labels_filtered, self.builder_labels_idx)
    }
    fn builder_current_value(&self) -> String {
        current_of(&self.builder_values_filtered, self.builder_values_idx)
    }

    fn update_table_columns(&mut self, value_width: usize) {
        self.table.value_width = value_width.max("value".len());
        self.table.time_title = format!("TIME {}", result_time_label(self.time_range.display));
        self.table.filterable = true;
        self.table.target_width = self.width;
        self.table.with_rows(Arc::clone(&self.data_rows));
    }

    /// Apply one message; returns effects to execute.
    pub(crate) fn update(&mut self, message: Message) -> Vec<Effect> {
        match message {
            Message::Resize { width, height } => {
                self.resize(width, height);
                Vec::new()
            }
            Message::Done(completion) => self.complete(completion),
            Message::Key(key) => self.key(key),
        }
    }

    fn resize(&mut self, width: usize, height: usize) {
        self.width = width;
        self.height = height;
        let query_width = self.query_width();
        self.query.set_width(query_width);
        self.query.set_height(TOP_H - 4);
        self.step_input.set_width(4);
        self.spotlight_filter.set_width(SPOTLIGHT_WIDTH - 6);
        self.builder_filter
            .set_width(builder_col_width(width) * 3 + 8);
        self.update_table_columns(0);
        // Go builds the bottom bar here (WindowSizeMsg); a ui.truncate panic
        // aborts the rest of Update and is recovered by Bubble Tea.
        match self.bottom_bar(self.width) {
            Ok(bottom) => self
                .table
                .with_page_size(page_size_for(height, height_of(&bottom))),
            Err(panic) => {
                *self.render_fault.borrow_mut() = Some(panic);
                return;
            }
        }
        self.rebuild_chart();
    }

    fn complete(&mut self, completion: Completion) -> Vec<Effect> {
        let mut effects = Vec::new();
        match completion {
            Completion::Paste(_) => {}
            Completion::Tick { tag } => {
                if self.loading && tag == self.spinner_tag {
                    self.spinner_frame = (self.spinner_frame + 1) % SPINNER_FRAMES.len();
                    self.spinner_tag += 1;
                    effects.push(Effect::TickAfter {
                        tag: self.spinner_tag,
                    });
                }
            }
            Completion::Datasets(result) => {
                self.datasets_loading = false;
                match result {
                    Err(error) => self.status_error = format!("could not load datasets: {error}"),
                    Ok(datasets) => {
                        self.all_datasets = datasets.clone();
                        self.filtered_datasets = datasets.clone();
                        self.dataset_idx = 0;
                        if (self.dataset.is_empty() || self.dataset == "select-dataset")
                            && !datasets.is_empty()
                        {
                            self.dataset = datasets[0].clone();
                            self.cache_dataset.clear();
                            self.cache_metrics.clear();
                            if !self.query.value().trim().is_empty() {
                                self.normalize_step_for_run();
                                self.loading = true;
                                self.has_queried = true;
                                effects.push(Effect::Tick {
                                    tag: self.spinner_tag,
                                });
                                effects.push(Effect::FetchCacheMetrics {
                                    dataset: self.dataset.clone(),
                                });
                                effects.push(Effect::FetchResult(self.result_request()));
                                return effects;
                            }
                            effects.push(Effect::FetchCacheMetrics {
                                dataset: self.dataset.clone(),
                            });
                            return effects;
                        }
                        if let Some(i) = self
                            .filtered_datasets
                            .iter()
                            .position(|d| *d == self.dataset)
                        {
                            self.dataset_idx = i;
                        }
                    }
                }
            }
            Completion::CacheMetrics { dataset, result } => {
                if let Ok(items) = result
                    && !items.is_empty()
                    && dataset == self.dataset
                {
                    self.cache_dataset = dataset;
                    self.cache_metrics = items.clone();
                    if self.overlay == Overlay::Builder && self.builder_metrics_loading {
                        self.builder_metrics_loading = false;
                        self.builder_metric = items[0].clone();
                        self.builder_metrics_filtered = items.clone();
                        self.builder_metrics = items;
                        self.builder_metrics_idx = 0;
                    }
                }
            }
            Completion::Labels { id, metric, result } => {
                self.builder_labels_loading = false;
                self.cancel_labels = None;
                let _ = id;
                if !metric.is_empty()
                    && let Ok(items) = &result
                {
                    self.cache_labels.insert(metric.clone(), items.clone());
                }
                if metric != self.builder_current_metric() {
                    return effects;
                }
                match result {
                    Ok(items) if !items.is_empty() => {
                        let labels: Vec<String> =
                            std::iter::once(ANY.to_owned()).chain(items).collect();
                        self.builder_labels_filtered = labels.clone();
                        self.builder_labels = labels;
                        self.builder_labels_idx = 1;
                    }
                    _ => {
                        self.builder_labels = vec![ANY.to_owned()];
                        self.builder_labels_filtered = vec![ANY.to_owned()];
                        self.builder_labels_idx = 0;
                        self.builder_values = vec![ANY.to_owned()];
                        self.builder_values_filtered = vec![ANY.to_owned()];
                    }
                }
            }
            Completion::Values {
                id,
                metric,
                label,
                result,
            } => {
                self.builder_values_loading = false;
                self.cancel_values = None;
                let _ = id;
                if !metric.is_empty()
                    && !label.is_empty()
                    && let Ok(items) = &result
                {
                    self.cache_values
                        .entry(metric.clone())
                        .or_default()
                        .insert(label.clone(), items.clone());
                }
                if !metric.is_empty()
                    && (metric != self.builder_current_metric()
                        || label != self.builder_current_label())
                {
                    return effects;
                }
                let values = match result {
                    Ok(items) if !items.is_empty() => {
                        std::iter::once(ANY.to_owned()).chain(items).collect()
                    }
                    _ => vec![ANY.to_owned()],
                };
                self.builder_values_filtered = values.clone();
                self.builder_values = values;
                self.builder_values_idx = 0;
            }
            Completion::Result(result) => {
                self.loading = false;
                match result {
                    Ok(data) => {
                        self.fetch_err.clear();
                        self.status_error.clear();
                        // Consume service vectors once; snapshots share the immutable rows.
                        self.data_rows = data.rows.into();
                        self.chart_rows = data.chart_rows.into();
                        if self.chart_rows.is_empty() && data.result_type == "matrix" {
                            self.chart_rows = Arc::clone(&self.data_rows);
                        }
                        self.last_result_type = data.result_type.clone();
                        self.chart_cursor = 0;
                        self.chart_hover = false;
                        self.update_table_columns(data.value_width);
                        if self.mode == QueryMode::Both || data.chart_result == "matrix" {
                            self.chart_mode = true;
                        } else if self.mode == QueryMode::Instant || data.result_type == "vector" {
                            self.chart_mode = false;
                        }
                        self.rebuild_chart();
                        self.focus_pane(Pane::Table);
                    }
                    Err(error) => {
                        self.data_rows = Arc::default();
                        self.chart_rows = Arc::default();
                        self.table.with_rows(Arc::clone(&self.data_rows));
                        self.chart_graph_width = None;
                        self.chart_cursor = 0;
                        self.chart_hover = false;
                        self.fetch_err = if error.is_empty() {
                            "query failed".to_owned()
                        } else {
                            error
                        };
                        self.status_error = "query failed".to_owned();
                    }
                }
            }
        }
        effects
    }

    fn key(&mut self, key: Key) -> Vec<Effect> {
        let mut effects = self.key_inner(key);
        // A Ctrl+V binding that reached an editor returns bubbles' Paste
        // command; Go runs it even though its result is discarded.
        let requested = [
            self.query.take_paste_request(),
            self.step_input.take_paste_request(),
            self.spotlight_filter.take_paste_request(),
            self.builder_filter.take_paste_request(),
            self.table.filter.take_paste_request(),
        ];
        if requested.into_iter().any(|r| r) {
            effects.push(Effect::ReadClipboard);
        }
        effects
    }

    fn key_inner(&mut self, key: Key) -> Vec<Effect> {
        let mut effects = Vec::new();
        // ── global shortcuts (no overlay) ──
        if self.overlay == Overlay::None {
            match key.code {
                KeyCode::Ctrl(4) => return self.open_dataset_overlay(),
                KeyCode::Ctrl(2) => {
                    self.editor_mode = match self.editor_mode {
                        EditorMode::Builder => EditorMode::Code,
                        EditorMode::Code => EditorMode::Builder,
                    };
                    self.focus_pane(Pane::Query);
                    return effects;
                }
                KeyCode::Ctrl(17) => {
                    self.focus_pane(Pane::Query);
                    return effects;
                }
                _ => {}
            }
        }
        match self.overlay {
            Overlay::Builder => return self.builder_key(&key),
            Overlay::Dataset => return self.dataset_key(&key),
            Overlay::Time => {
                match key.code {
                    KeyCode::Esc => {
                        self.overlay = Overlay::None;
                        self.focus_selected();
                    }
                    KeyCode::Enter => {
                        self.time_range = self.time_range_edit.clone();
                        self.overlay = Overlay::None;
                        self.focus_selected();
                        self.run_query(&mut effects);
                    }
                    _ => {
                        let now = self.now();
                        self.time_range_edit.update(&key, now);
                    }
                }
                return effects;
            }
            Overlay::None => {}
        }
        // ── main navigation ──
        match key.code {
            KeyCode::Tab => {
                self.focused = (self.focused + 1) % PANES.len();
                self.focus_selected();
                return effects;
            }
            KeyCode::ShiftTab => {
                self.focused = (self.focused + PANES.len() - 1) % PANES.len();
                self.focus_selected();
                return effects;
            }
            _ => {}
        }
        if key.code == KeyCode::Down && self.focus() == Pane::Dataset {
            self.focus_pane(Pane::Step);
            return effects;
        }
        if key.code == KeyCode::Up && self.focus() == Pane::Step {
            self.focus_pane(Pane::Dataset);
            return effects;
        }
        if key.code == KeyCode::Enter && self.focus() == Pane::Dataset {
            return self.open_dataset_overlay();
        }
        if key.code == KeyCode::Enter && self.focus() == Pane::Time {
            self.time_range_edit = self.time_range.clone();
            let now = self.now();
            self.time_range_edit.sync_preset(now);
            self.overlay = Overlay::Time;
            return effects;
        }
        if key.code == KeyCode::Enter
            && self.focus() == Pane::Query
            && self.editor_mode == EditorMode::Builder
        {
            return self.open_builder_overlay();
        }
        if key.code == KeyCode::Ctrl(18) || (key.alt && key.code == KeyCode::Enter) {
            if self.dataset.is_empty() {
                let effects = self.open_dataset_overlay();
                self.status_error = "select a dataset first".to_owned();
                return effects;
            }
            self.overlay = Overlay::None;
            self.run_query(&mut effects);
            return effects;
        }
        if (key.code == KeyCode::Ctrl(0) || key.name() == "ctrl+space")
            && self.focus() == Pane::Step
        {
            self.toggle_mode(&mut effects);
            return effects;
        }
        if key.name() == "t" && self.focus() == Pane::Table {
            if self.mode == QueryMode::Instant
                || (self.mode != QueryMode::Both && self.last_result_type == "vector")
            {
                self.chart_mode = false;
                return effects;
            }
            self.chart_mode = !self.chart_mode;
            self.rebuild_chart();
            return effects;
        }
        if key.name() == "w" && self.focus() == Pane::Table && !self.chart_mode {
            self.unwrap_metric = !self.unwrap_metric;
            return effects;
        }
        if self.chart_mode && self.focus() == Pane::Table {
            if key.code == KeyCode::Left || key.name() == "[" {
                self.move_chart_cursor(-1);
                self.rebuild_chart();
                return effects;
            }
            if key.code == KeyCode::Right || key.name() == "]" {
                self.move_chart_cursor(1);
                self.rebuild_chart();
                return effects;
            }
        }
        if key.code == KeyCode::Ctrl(3) {
            effects.push(Effect::Quit);
            return effects;
        }
        match self.focus() {
            Pane::Query => {
                if self.editor_mode == EditorMode::Code {
                    self.query.update(&key);
                }
            }
            Pane::Step => {
                self.step_input.update(&key);
                self.step = self.step_input.value();
            }
            Pane::Table => self.table.update(&key),
            _ => {}
        }
        effects
    }

    fn toggle_mode(&mut self, effects: &mut Vec<Effect>) {
        let previous = self.mode;
        self.mode = self.mode.next();
        let instant = self.mode == QueryMode::Instant;
        self.time_range.set_instant(instant);
        let now = go_now(self.now());
        if instant {
            self.time_range.end.set_time(go_add(now, -HOUR_NANOS));
            self.chart_mode = false;
        } else {
            match previous {
                QueryMode::Both => self.time_range.end.set_time(now),
                QueryMode::Instant => {
                    let duration = PRESETS[self.time_range.preset].0;
                    let start = go_add(self.time_range.end.time, duration);
                    self.time_range.start.set_time(start);
                }
                QueryMode::Range => {}
            }
            self.chart_mode = true;
        }
        if !self.query.value().trim().is_empty() && !self.dataset.is_empty() {
            self.run_query(effects);
        }
    }

    fn open_builder_overlay(&mut self) -> Vec<Effect> {
        self.overlay = Overlay::Builder;
        self.builder_col = 0;
        self.builder_metric.clear();
        self.builder_label.clear();
        self.builder_metrics_idx = 0;
        self.builder_labels_idx = 0;
        self.builder_values_idx = 0;
        self.builder_labels.clear();
        self.builder_labels_filtered.clear();
        self.builder_values.clear();
        self.builder_values_filtered.clear();
        self.builder_labels_loading = false;
        self.builder_values_loading = false;
        self.builder_filter.set_value("");
        self.builder_filter.focus();
        if self.dataset.is_empty() {
            self.builder_metrics.clear();
            self.builder_metrics_filtered.clear();
            self.builder_metrics_loading = false;
            return Vec::new();
        }
        if self.cache_dataset == self.dataset && !self.cache_metrics.is_empty() {
            self.builder_metrics = self.cache_metrics.clone();
            self.builder_metrics_filtered = self.cache_metrics.clone();
            self.builder_metrics_loading = false;
            self.builder_metric = self.cache_metrics[0].clone();
            return Vec::new();
        }
        self.builder_metrics.clear();
        self.builder_metrics_filtered.clear();
        self.builder_metrics_loading = true;
        vec![Effect::FetchCacheMetrics {
            dataset: self.dataset.clone(),
        }]
    }

    /// `runQueryFromBuilder`.
    fn run_from_builder(&mut self, expr: String) -> Vec<Effect> {
        let mut effects = Vec::new();
        if !expr.is_empty() {
            self.query.set_value(&expr);
            self.query.cursor_end();
        }
        self.editor_mode = EditorMode::Code;
        self.overlay = Overlay::None;
        self.builder_filter.set_value("");
        self.builder_filter.blur();
        self.focus_pane(Pane::Query);
        if self.query.value().is_empty() {
            return effects;
        }
        self.run_query(&mut effects);
        effects
    }

    fn next_id(&mut self) -> u64 {
        self.next_request += 1;
        self.next_request
    }

    fn builder_key(&mut self, key: &Key) -> Vec<Effect> {
        let mut effects = Vec::new();
        match key.code {
            KeyCode::Esc => {
                self.overlay = Overlay::None;
                self.builder_filter.set_value("");
                self.builder_filter.blur();
                self.focus_selected();
            }
            KeyCode::Ctrl(18) => {
                let expr = build_expr(
                    &self.builder_current_metric(),
                    &self.builder_current_label(),
                    &self.builder_current_value(),
                );
                return self.run_from_builder(expr);
            }
            KeyCode::Enter => match self.builder_col {
                0 => {
                    let metric = self.builder_current_metric();
                    if metric.is_empty() {
                        return effects;
                    }
                    self.builder_metric = metric.clone();
                    self.builder_labels.clear();
                    self.builder_labels_filtered.clear();
                    self.builder_values.clear();
                    self.builder_values_filtered.clear();
                    self.builder_labels_idx = 0;
                    self.builder_values_idx = 0;
                    self.builder_col = 1;
                    self.builder_filter.set_value("");
                    if let Some(id) = self.cancel_labels {
                        effects.push(Effect::Cancel { id });
                    }
                    if let Some(labels) = self.cache_labels.get(&metric) {
                        let full: Vec<String> = std::iter::once(ANY.to_owned())
                            .chain(labels.iter().cloned())
                            .collect();
                        self.builder_labels_filtered = full.clone();
                        self.builder_labels = full;
                        self.builder_labels_idx = 1;
                        self.builder_labels_loading = false;
                        self.cancel_labels = None;
                        return effects;
                    }
                    self.builder_labels_loading = true;
                    let id = self.next_id();
                    self.cancel_labels = Some(id);
                    effects.push(Effect::FetchLabels {
                        id,
                        dataset: self.dataset.clone(),
                        metric,
                        start: self.time_range.start.value_utc(),
                        end: self.time_range.end.value_utc(),
                    });
                }
                1 => {
                    let label = self.builder_current_label();
                    self.builder_label = label.clone();
                    self.builder_filter.set_value("");
                    if label.is_empty() || label == ANY {
                        let expr = build_expr(&self.builder_current_metric(), "", "");
                        return self.run_from_builder(expr);
                    }
                    self.builder_values.clear();
                    self.builder_values_filtered.clear();
                    self.builder_values_idx = 0;
                    self.builder_col = 2;
                    if let Some(id) = self.cancel_values {
                        effects.push(Effect::Cancel { id });
                    }
                    let metric = self.builder_current_metric();
                    if let Some(values) = self.cache_values.get(&metric).and_then(|m| m.get(&label))
                    {
                        let full: Vec<String> = std::iter::once(ANY.to_owned())
                            .chain(values.iter().cloned())
                            .collect();
                        self.builder_values_filtered = full.clone();
                        self.builder_values = full;
                        self.builder_values_idx = 1;
                        self.builder_values_loading = false;
                        self.cancel_values = None;
                        return effects;
                    }
                    self.builder_values_loading = true;
                    let id = self.next_id();
                    self.cancel_values = Some(id);
                    effects.push(Effect::FetchValues {
                        id,
                        dataset: self.dataset.clone(),
                        metric,
                        label,
                        start: self.time_range.start.value_utc(),
                        end: self.time_range.end.value_utc(),
                    });
                }
                _ => {
                    let expr = build_expr(
                        &self.builder_current_metric(),
                        &self.builder_current_label(),
                        &self.builder_current_value(),
                    );
                    return self.run_from_builder(expr);
                }
            },
            KeyCode::Tab => {
                self.builder_col = (self.builder_col + 1) % 3;
                self.builder_filter.set_value("");
            }
            KeyCode::ShiftTab => {
                self.builder_col = (self.builder_col + 2) % 3;
                self.builder_filter.set_value("");
            }
            KeyCode::Up => match self.builder_col {
                0 => self.builder_metrics_idx = self.builder_metrics_idx.saturating_sub(1),
                1 => self.builder_labels_idx = self.builder_labels_idx.saturating_sub(1),
                _ => self.builder_values_idx = self.builder_values_idx.saturating_sub(1),
            },
            KeyCode::Down => {
                let (idx, len) = match self.builder_col {
                    0 => (
                        &mut self.builder_metrics_idx,
                        self.builder_metrics_filtered.len(),
                    ),
                    1 => (
                        &mut self.builder_labels_idx,
                        self.builder_labels_filtered.len(),
                    ),
                    _ => (
                        &mut self.builder_values_idx,
                        self.builder_values_filtered.len(),
                    ),
                };
                if (*idx as isize) < len as isize - 1 {
                    *idx += 1;
                }
            }
            _ => {
                let previous = self.builder_filter.value();
                self.builder_filter.update(key);
                let filter = self.builder_filter.value();
                if filter != previous {
                    match self.builder_col {
                        0 => {
                            self.builder_metrics_filtered =
                                filter_datasets(&self.builder_metrics, &filter);
                            self.builder_metrics_idx = 0;
                        }
                        1 => {
                            self.builder_labels_filtered =
                                filter_builder_list(&self.builder_labels, &filter);
                            self.builder_labels_idx = 0;
                        }
                        _ => {
                            self.builder_values_filtered =
                                filter_builder_list(&self.builder_values, &filter);
                            self.builder_values_idx = 0;
                        }
                    }
                }
            }
        }
        effects
    }

    fn dataset_key(&mut self, key: &Key) -> Vec<Effect> {
        let mut effects = Vec::new();
        match key.code {
            KeyCode::Esc => {
                self.overlay = Overlay::None;
                self.spotlight_filter.set_value("");
                self.spotlight_filter.blur();
                self.focus_selected();
            }
            KeyCode::Enter => {
                if let Some(selected) = self.filtered_datasets.get(self.dataset_idx).cloned()
                    && selected != self.dataset
                {
                    self.dataset = selected;
                    self.query.set_value("");
                    self.cache_dataset.clear();
                    self.cache_metrics.clear();
                    self.cache_labels.clear();
                    self.cache_values.clear();
                    self.overlay = Overlay::None;
                    self.spotlight_filter.set_value("");
                    self.spotlight_filter.blur();
                    self.focused = 0;
                    self.focus_selected();
                    effects.push(Effect::FetchCacheMetrics {
                        dataset: self.dataset.clone(),
                    });
                    return effects;
                }
                self.overlay = Overlay::None;
                self.spotlight_filter.set_value("");
                self.spotlight_filter.blur();
                self.focus_selected();
            }
            KeyCode::Up => self.dataset_idx = self.dataset_idx.saturating_sub(1),
            KeyCode::Down => {
                if (self.dataset_idx as isize) < self.filtered_datasets.len() as isize - 1 {
                    self.dataset_idx += 1;
                }
            }
            _ => {
                let previous = self.spotlight_filter.value();
                self.spotlight_filter.update(key);
                let filter = self.spotlight_filter.value();
                if filter != previous {
                    self.filtered_datasets = filter_datasets(&self.all_datasets, &filter);
                    self.dataset_idx = 0;
                }
            }
        }
        effects
    }

    // ── chart model ──

    /// `extractChartData`: series in first-seen order with ≥ 2 values.
    fn extract_chart_data(&self) -> Option<Vec<RawSeries>> {
        let rows = if self.chart_rows.is_empty() {
            &self.data_rows
        } else {
            &self.chart_rows
        };
        if rows.len() < 2 {
            return None;
        }
        let mut order: Vec<String> = Vec::new();
        let mut series: HashMap<String, (String, Vec<String>, Vec<f64>)> = HashMap::new();
        for row in rows.iter() {
            let value = go_parse_float(&row.value)?;
            let label = if row.metric.is_empty() {
                "value".to_owned()
            } else {
                row.metric.clone()
            };
            let key = chart_label_set(&label).to_owned();
            let entry = series.entry(key.clone()).or_insert_with(|| {
                order.push(key.clone());
                (label.clone(), Vec::new(), Vec::new())
            });
            entry.1.push(row.timestamp_full.clone());
            entry.2.push(value);
        }
        let out: Vec<_> = order
            .into_iter()
            .filter_map(|k| series.remove(&k))
            .filter(|s| s.2.len() >= 2)
            .collect();
        if out.is_empty() { None } else { Some(out) }
    }

    /// `chartSeriesPoints`: sorted by last value (desc) then label.
    fn chart_series(&self) -> Vec<ChartSeries> {
        let Some(raw) = self.extract_chart_data() else {
            return Vec::new();
        };
        let mut out = Vec::new();
        for (label, times, values) in raw {
            let mut points: Vec<TimePoint> = times
                .iter()
                .zip(values.iter())
                .filter_map(|(t, v)| parse_chart_time(t).map(|time| TimePoint { time, value: *v }))
                .collect();
            if points.len() < 2 {
                continue;
            }
            // sort.Slice by Time.Before: not stable above 12 points (G10).
            go_sort::slice(&mut points, |x, y| x.time < y.time);
            out.push(ChartSeries {
                label,
                color: String::new(),
                points,
            });
        }
        // sort.SliceStable on (last value desc, label). A NaN last value makes
        // Less false both ways; Go's exact merge sequence decides the order.
        go_sort::slice_stable(&mut out, |a, b| {
            let left = a.points.last().map_or(0.0, |p| p.value);
            let right = b.points.last().map_or(0.0, |p| p.value);
            if left == right {
                a.label < b.label
            } else {
                left > right
            }
        });
        for (i, s) in out.iter_mut().enumerate() {
            s.color = chart_series_color(i);
        }
        out
    }

    fn selected_duration(&self, first: DateTime<Local>, last: DateTime<Local>) -> i64 {
        let (start, end) = (self.time_range.start.time, self.time_range.end.time);
        if end.instant > start.instant {
            go_between(start, end)
        } else {
            duration_nanos(first, last)
        }
    }

    fn build_chart(&self, width: isize, height: isize, series: &[ChartSeries]) -> Option<Chart> {
        let all: Vec<TimePoint> = series
            .iter()
            .flat_map(|s| s.points.iter().cloned())
            .collect();
        let first = all.iter().map(|p| p.time).min()?;
        let last = all.iter().map(|p| p.time).max()?;
        if all.len() < 2 {
            return None;
        }
        let width = width.max(20);
        let height = height.max(8);
        let (min_y, max_y, row_step) = y_axis_scale(&all, height);
        let time_range = self.selected_duration(first, last);
        let x_step = (width / 8).max(8);
        let _ = x_step;
        // timeserieslinechart.New: X view starts at now..now+1s and only
        // moves to [first, last] when that range is non-empty.
        let now = self.now().timestamp() as f64;
        let (mut view_min_x, mut view_max_x) = (now, now + 1.0);
        let (first_s, last_s) = (first.timestamp() as f64, last.timestamp() as f64);
        if first_s < last_s {
            view_min_x = first_s;
            view_max_x = last_s;
        }
        let (mut view_min_y, mut view_max_y) = (0.0, 1.0);
        if min_y < max_y {
            view_min_y = min_y;
            view_max_y = max_y;
        }
        let (origin, graph_width) = graph_size(
            width,
            height,
            view_min_y,
            view_max_y,
            row_step,
            format_compact,
        );
        let mut chart = Chart {
            canvas: Canvas::new(width as usize, height as usize),
            origin,
            graph_width,
            view_min_x,
            view_max_x,
            view_min_y,
            view_max_y,
        };
        let axis = Some(CellColor::Role(Role::Faint));
        // DrawXYAxisAndLabel
        chart.canvas.set(origin, '└', axis.clone());
        for y in (0..origin.y).rev() {
            chart
                .canvas
                .set(Point { x: origin.x, y }, '│', axis.clone());
        }
        for x in origin.x + 1..width {
            chart
                .canvas
                .set(Point { x, y: origin.y }, '─', axis.clone());
        }
        let graph_height = height - 2;
        let increment = (view_max_y - view_min_y) / graph_height as f64;
        let mut last_label = String::new();
        let mut i = 0;
        while i <= graph_height {
            let s = format_compact(view_min_y + increment * i as f64);
            if s != last_label {
                chart.canvas.set_string(
                    Point {
                        x: origin.x - s.len() as isize,
                        y: origin.y - i,
                    },
                    &s,
                    axis.clone(),
                );
                last_label = s;
            }
            i += row_step;
        }
        // drawChartSeriesSegments
        for s in series {
            let color = CellColor::Hex(s.color.clone());
            let points = self.canvas_points(&chart, &s.points);
            if points.len() == 1 {
                chart.canvas.set(points[0], '─', Some(color));
                continue;
            }
            for j in 1..points.len() {
                if points[j - 1] == points[j] {
                    continue;
                }
                let line = line_points(points[j - 1], points[j]);
                draw_line_points(&mut chart.canvas, &line, &color);
            }
        }
        self.draw_x_labels(&mut chart, first, last, time_range);
        if series.len() == 1 {
            self.draw_cursor(&mut chart, &series[0].points);
        }
        Some(chart)
    }

    fn canvas_points(&self, chart: &Chart, points: &[TimePoint]) -> Vec<Point> {
        let min = unix_time(chart.view_min_x);
        let max = unix_time(chart.view_max_x);
        let mut order: Vec<isize> = Vec::new();
        let mut buckets: HashMap<isize, (isize, isize)> = HashMap::new();
        for p in points {
            if p.time < min || p.time > max {
                continue;
            }
            let x = chart.canvas_x(p.time);
            let y = chart.canvas_y(p.value);
            if x < chart.origin.x || x >= chart.canvas.width as isize || y < 0 || y > chart.origin.y
            {
                continue;
            }
            let bucket = buckets.entry(x).or_insert_with(|| {
                order.push(x);
                (0, 0)
            });
            bucket.0 += y;
            bucket.1 += 1;
        }
        order.sort_unstable();
        order
            .into_iter()
            .map(|x| {
                let (sum, count) = buckets[&x];
                Point {
                    x,
                    y: round_go(sum as f64 / count as f64),
                }
            })
            .collect()
    }

    fn draw_x_labels(
        &self,
        chart: &mut Chart,
        first: DateTime<Local>,
        last: DateTime<Local>,
        duration: i64,
    ) {
        if chart.graph_width <= 0 || chart.origin.y + 1 >= chart.canvas.height as isize {
            return;
        }
        let label_y = chart.origin.y + 1;
        let faint = Some(CellColor::Role(Role::Faint));
        for x in 0..chart.canvas.width as isize {
            chart
                .canvas
                .set(Point { x, y: label_y }, ' ', faint.clone());
        }
        let total = duration_nanos(first, last);
        if total <= 0 {
            return;
        }
        let width = chart.canvas.width as isize;
        for i in 0..CHART_X_LABEL_COUNT {
            let ratio = i as f64 / (CHART_X_LABEL_COUNT - 1) as f64;
            let t = add_nanos(first, go_f64_to_i64(ratio * total as f64));
            let label = format_axis_label(t, duration, chart.graph_width, self.time_range.display);
            let x = chart.origin.x + round_go(ratio * chart.graph_width as f64);
            if label.is_empty() {
                continue;
            }
            let len = label.chars().count() as isize;
            let mut start = x;
            if start + len > width {
                start = width - len;
            }
            if start < chart.origin.x {
                start = chart.origin.x;
            }
            chart.canvas.set_string(
                Point {
                    x: start,
                    y: label_y,
                },
                &label,
                faint.clone(),
            );
        }
    }

    fn selected_point(&self, points: &[TimePoint]) -> Option<usize> {
        if !self.chart_hover || points.is_empty() {
            return None;
        }
        Some(self.chart_cursor.min(points.len() - 1))
    }

    fn draw_cursor(&self, chart: &mut Chart, points: &[TimePoint]) {
        let Some(idx) = self.selected_point(points) else {
            return;
        };
        let point = &points[idx];
        let x = chart.canvas_x(point.time);
        let y = chart.canvas_y(point.value);
        for row in 0..chart.origin.y {
            chart
                .canvas
                .set(Point { x, y: row }, '│', Some(CellColor::Role(Role::Faint)));
        }
        chart
            .canvas
            .set(Point { x, y }, '●', Some(CellColor::Role(Role::Accent)));
    }

    /// `rebuildChart`: only the graph width is retained for cursor movement.
    fn rebuild_chart(&mut self) {
        let series = self.chart_series();
        self.chart_graph_width = self
            .build_chart(self.width as isize - 2, self.height as isize - 8, &series)
            .map(|c| c.graph_width);
    }

    fn cursor_graph_width(&self) -> isize {
        if let Some(width) = self.chart_graph_width
            && width > 0
        {
            return width;
        }
        let width = (self.width as isize - 4).max(20);
        let series = self.chart_series();
        match self.build_chart(width, self.height as isize - 8, &series) {
            Some(chart) if chart.graph_width > 0 => chart.graph_width,
            _ => width,
        }
    }

    /// `moveChartCursor`.
    fn move_chart_cursor(&mut self, delta: isize) {
        let series = self.chart_series();
        let Some(points) = series.first().map(|s| s.points.clone()) else {
            return;
        };
        if !self.chart_hover {
            self.chart_cursor = 0;
            if delta < 0 {
                self.chart_cursor = points.len() - 1;
            } else if delta > 0 && points.len() > 1 {
                self.chart_cursor = 1;
            }
            self.chart_hover = true;
            return;
        }
        let current = self.chart_cursor.min(points.len() - 1);
        let direction: isize = if delta < 0 { -1 } else { 1 };
        let graph_width = self.cursor_graph_width();
        let first = points[0].time;
        let last = points[points.len() - 1].time;
        let current_x = chart_graph_x(points[current].time, first, last, graph_width);
        let mut next = current as isize;
        loop {
            next += direction;
            if next < 0 {
                next = 0;
                break;
            }
            if next >= points.len() as isize {
                next = points.len() as isize - 1;
                break;
            }
            if chart_graph_x(points[next as usize].time, first, last, graph_width) != current_x {
                break;
            }
        }
        self.chart_cursor = next as usize;
    }

    // ── view ──

    fn keys_for_focus(&self) -> Vec<KeyHint<'static>> {
        let common = [
            hint("<tab>", "Next pane"),
            hint("<shift+tab>", "Prev pane"),
            hint("<ctrl-r>", "Run"),
            hint("<ctrl-c>", "Quit"),
        ];
        match self.overlay {
            Overlay::Dataset => {
                return vec![
                    hint("<↑/↓>", "Navigate"),
                    hint("<enter>", "Select"),
                    hint("<esc>", "Cancel"),
                    hint("type", "Filter"),
                ];
            }
            Overlay::Builder => {
                return vec![
                    hint("<↑/↓>", "Navigate"),
                    hint("<enter>", "Next col"),
                    hint("<tab>", "Cycle col"),
                    hint("<ctrl-r>", "Run"),
                    hint("<esc>", "Cancel"),
                ];
            }
            Overlay::Time => {
                let label = match self.time_range_edit.current_focus() {
                    TimeFocus::List => "Preset",
                    TimeFocus::Display => "Display time",
                    _ => "Adjust",
                };
                return vec![
                    hint("<↑/↓>", label),
                    hint("<tab>", "Switch pane"),
                    hint("<enter>", "Apply"),
                    hint("<esc>", "Cancel"),
                ];
            }
            Overlay::None => {}
        }
        let mut hints = Vec::new();
        match self.focus() {
            Pane::Dataset => {
                hints.push(hint("<enter>", "Open picker"));
                hints.push(hint("<ctrl-d>", "Datasets"));
                hints.extend(common);
            }
            Pane::Query => {
                hints.push(hint("<ctrl-b>", "Toggle builder"));
                if self.editor_mode == EditorMode::Builder {
                    hints.push(hint("<enter>", "Open builder"));
                }
                hints.extend(common);
            }
            Pane::Time => {
                hints.push(hint("<enter>", "Open picker"));
                hints.extend(common);
            }
            Pane::Step => {
                hints.push(hint("type", "Edit (15s, 5m, 1h)"));
                hints.push(hint("<ctrl+space>", "Range/instant/both"));
                hints.extend(common);
            }
            Pane::Table => {
                if self.chart_mode {
                    hints.push(hint("<←/→>", "Inspect"));
                    if self.mode != QueryMode::Instant {
                        hints.push(hint("<t>", "Toggle chart/table"));
                    }
                } else {
                    hints.push(hint("<↑/↓>", "Row"));
                    hints.push(hint("<w>", "Wrap metric"));
                    hints.push(hint("<tab>", "Next pane"));
                    hints.push(hint("<shift+tab>", "Prev pane"));
                    if self.mode != QueryMode::Instant {
                        hints.push(hint("<t>", "Toggle chart/table"));
                    }
                    hints.push(hint("<ctrl-r>", "Run"));
                    hints.push(hint("<ctrl-c>", "Quit"));
                    return hints;
                }
                hints.extend(common);
            }
        }
        hints
    }

    /// `buildPromqlBottomBar`.
    fn bottom_bar(&self, width: usize) -> Result<String, GoPanic> {
        const PAD: usize = 2;
        let inner_w = width.saturating_sub(PAD * 2).max(1);
        let padding = " ".repeat(PAD);
        let label = st().fgr(Role::Faint);
        let sep = st().fgr(Role::BorderSoft);
        let mut shortcuts = key_grid(&self.keys_for_focus(), 4, inner_w)?;
        if shortcuts.is_empty() {
            shortcuts = " ".to_owned();
        }
        let mut lines: Vec<String> = shortcuts
            .split('\n')
            .map(|l| format!("{padding}{l}"))
            .collect();
        lines.push(sep.render(&"─".repeat(width)));
        let parseable = st().fgr(Role::Accent).bold(true).render("Parseable");
        let url = label.render(&self.profile_url);
        let conn_left = style::join_horizontal(1.0, &[parseable.as_str(), "  ", url.as_str()]);
        let mut right_parts: Vec<String> = Vec::new();
        // View clears the error while loading (on its copy).
        if !self.status_error.is_empty() && !self.loading {
            right_parts.push(st().fgr(Role::Err).bold(true).render(&self.status_error));
            right_parts.push(sep.render(" │ "));
        }
        right_parts.push(label.render("QUERY"));
        right_parts.push(" ".to_owned());
        right_parts.push(st().fgr(Role::Accent).bold(true).render("PROMQL"));
        let refs: Vec<&str> = right_parts.iter().map(String::as_str).collect();
        let conn_right = style::join_horizontal(1.0, &refs);
        let gap = (inner_w as isize
            - style::width(&conn_left) as isize
            - style::width(&conn_right) as isize)
            .max(1) as usize;
        lines.push(format!(
            "{padding}{conn_left}{}{conn_right}{padding}",
            " ".repeat(gap)
        ));
        Ok(join_vertical(&lines))
    }

    fn editor_pane(
        &self,
        body: &str,
        width: usize,
        height: usize,
        focused: bool,
    ) -> Result<String, GoPanic> {
        let (border_role, title_role) = if focused {
            (Role::BorderHi, Role::Accent)
        } else {
            (Role::Border, Role::Faint)
        };
        let inner_w = width.saturating_sub(2).max(4);
        let inner_h = height.saturating_sub(2).max(3);
        let active = st().fgr(Role::Active).bold(true);
        let idle = st().fgr(Role::Faint);
        let sep = st().fgr(Role::Faint).render(" | ");
        let right = if self.editor_mode == EditorMode::Builder {
            format!("{}{sep}{}", idle.render("Code"), active.render("Builder"))
        } else {
            format!("{}{sep}{}", active.render("Code"), idle.render("Builder"))
        };
        let border = st().fgr(border_role);
        let title = st().fgr(title_role).bold(focused);
        let mut lines = vec![promql_pane_rule(
            "┌", "┐", "EDITOR", &right, width, &border, &title,
        )?];
        lines.extend(pane_body_lines(body, width, inner_h, &border));
        lines.push(border.render(&format!("└{}┘", "─".repeat(inner_w))));
        Ok(join_vertical(&lines))
    }

    fn controls_pane(
        &self,
        start: &str,
        end: &str,
        dataset: &str,
        step: &str,
        width: usize,
        height: usize,
    ) -> Result<String, GoPanic> {
        let focus = self.focus();
        let instant = self.mode == QueryMode::Instant;
        let inner_w = width.saturating_sub(2).max(4);
        let val_w = inner_w.saturating_sub(2).max(4);
        let rail = st().bgr(Role::Active).render(" ");
        let dim = st().fgr(Role::Faint);
        let body = st().fgr(Role::Body);
        let active = st().fgr(Role::Active).bold(true);
        let ghost = st().fgr(Role::Ghost);
        let section_focused = matches!(focus, Pane::Time | Pane::Dataset | Pane::Step);
        let border = st().fgr(if section_focused {
            Role::BorderHi
        } else {
            Role::Border
        });
        let title_style = |pane: Pane| {
            if focus == pane {
                st().fgr(Role::Accent).bold(true)
            } else {
                st().fgr(Role::Faint)
            }
        };
        let row = |label: &str, value: &str| -> Result<[String; 2], GoPanic> {
            let (prefix, label_style) = if focus == Pane::Time {
                (format!("{rail} "), active)
            } else {
                ("  ".to_owned(), dim)
            };
            Ok([
                format!("{prefix}{}", label_style.render(label)),
                format!("{prefix}{}", body.render(&ui_truncate(value, val_w)?)),
            ])
        };
        let mut time_lines: Vec<String> = Vec::new();
        if !instant {
            time_lines.extend(row("FROM", start)?);
        }
        time_lines.extend(row("TO", end)?);
        time_lines.push(String::new());
        let (dataset_prefix, dataset_style) = if focus == Pane::Dataset {
            (format!("{rail} "), active)
        } else {
            ("  ".to_owned(), body)
        };
        let dataset_lines = vec![
            format!(
                "{dataset_prefix}{}",
                dataset_style.render(&ui_truncate(dataset, val_w)?)
            ),
            String::new(),
        ];
        let (step_prefix, mut step_label) = if focus == Pane::Step {
            (format!("{rail} "), active)
        } else {
            ("  ".to_owned(), dim)
        };
        // Go evaluates both truncations before the focus/instant overrides.
        let mut step_value = body.render(&ui_truncate(step, val_w.saturating_sub(5))?);
        let mode_value = body.render(&ui_truncate(self.mode.label(), val_w.saturating_sub(5))?);
        if focus == Pane::Step {
            step_value = step.to_owned();
        }
        if instant {
            step_label = ghost;
            step_value = ghost.render("—");
        }
        let query_lines = vec![
            format!("{step_prefix}{}{step_value}", step_label.render("STEP ")),
            format!("{step_prefix}{}{mode_value}", step_label.render("MODE ")),
        ];
        let query_h =
            (height as isize - 4 - time_lines.len() as isize - dataset_lines.len() as isize)
                .max(query_lines.len() as isize) as usize;
        let display = self.time_range.display;
        let mut lines = vec![pane_rule(
            "┌",
            "┐",
            &format!("TIME RANGE {}", result_time_label(display)),
            width,
            &border,
            &title_style(Pane::Time),
        )?];
        lines.extend(pane_body_lines(
            &join_vertical(&time_lines),
            width,
            time_lines.len(),
            &border,
        ));
        lines.push(pane_rule(
            "├",
            "┤",
            "DATASET",
            width,
            &border,
            &title_style(Pane::Dataset),
        )?);
        lines.extend(pane_body_lines(
            &join_vertical(&dataset_lines),
            width,
            dataset_lines.len(),
            &border,
        ));
        lines.push(pane_rule(
            "├",
            "┤",
            "QUERY",
            width,
            &border,
            &title_style(Pane::Step),
        )?);
        lines.extend(pane_body_lines(
            &join_vertical(&query_lines),
            width,
            query_h,
            &border,
        ));
        lines.push(border.render(&format!("└{}┘", "─".repeat(inner_w))));
        Ok(join_vertical(&lines))
    }

    fn results_pane(
        &self,
        body: &str,
        width: usize,
        height: usize,
        title: &str,
        right: &str,
    ) -> Result<String, GoPanic> {
        let focused = self.focus() == Pane::Table;
        let (border_role, title_role) = if focused {
            (Role::BorderHi, Role::Accent)
        } else {
            (Role::Border, Role::Faint)
        };
        let inner_w = width.saturating_sub(2).max(4);
        let inner_h = height.saturating_sub(2).max(3);
        let border = st().fgr(border_role);
        let title_style = st().fgr(title_role).bold(focused);
        let mut lines = vec![promql_pane_rule(
            "┌",
            "┐",
            title,
            right,
            width,
            &border,
            &title_style,
        )?];
        lines.extend(pane_body_lines(body, width, inner_h, &border));
        lines.push(border.render(&format!("└{}┘", "─".repeat(inner_w))));
        Ok(join_vertical(&lines))
    }

    fn metric_detail(
        &self,
        table: &ResultsTable,
        width: usize,
        max_height: usize,
    ) -> Result<String, GoPanic> {
        if self.data_rows.is_empty() {
            return Ok(String::new());
        }
        let Some(row) = table.highlighted_row() else {
            return Ok(String::new());
        };
        let metric = row.metric.trim().to_owned();
        if metric.is_empty() {
            return Ok(String::new());
        }
        let title = st().fgr(Role::Accent).bold(true).render("METRIC");
        let prefix = format!(" {title} ");
        let mut text_w = width as isize - style::width(&prefix) as isize - 1;
        if text_w < 12 {
            text_w = width as isize - 2;
        }
        let text_w = text_w.max(1) as usize;
        let mut lines = wrap_visible_text(&metric, text_w);
        if lines.is_empty() {
            return Ok(String::new());
        }
        if max_height > 0 && lines.len() > max_height {
            lines.truncate(max_height);
            let last = lines.len() - 1;
            lines[last] = ui_truncate(&lines[last], text_w)?;
        }
        let body = st().fgr(Role::Body);
        let indent = " ".repeat(style::width(&prefix));
        Ok(lines
            .iter()
            .enumerate()
            .map(|(i, line)| {
                let lead = if i == 0 {
                    prefix.as_str()
                } else {
                    indent.as_str()
                };
                st().width(width)
                    .render(&format!("{lead}{}", body.render(line)))
            })
            .collect::<Vec<_>>()
            .join("\n"))
    }

    fn results_table(&self, width: usize, height: usize) -> Result<String, GoPanic> {
        let mut table = self.table.clone();
        table.target_width = width;
        table.with_rows(Arc::clone(&self.data_rows));
        let detail = if self.unwrap_metric && !self.data_rows.is_empty() && height > 8 {
            self.metric_detail(&table, width, height - 6)?
        } else {
            String::new()
        };
        let detail_h = height_of(&detail);
        let table_h = (height as isize - detail_h as isize).max(6);
        table.with_page_size((table_h - 5).max(1) as usize);
        let cursor = table.cursor;
        table.with_highlighted_row(cursor);
        let rendered = table.view();
        let mut lines: Vec<&str> = rendered.split('\n').collect();
        while lines.last().is_some_and(|l| l.is_empty()) {
            lines.pop();
        }
        let bottom_rule = lines.pop().unwrap_or("").to_owned();
        let body_h = lines.len() + 1;
        let padding_h = (height as isize - 1 - detail_h as isize - body_h as isize).max(0) as usize;
        let dash = st().fgr(Role::Ghost).width(width).render(" --");
        let left = if table.max_pages() > 1 {
            format!("{}/{}", table.current_page + 1, table.max_pages())
        } else {
            String::new()
        };
        let right = if self.data_rows.is_empty() {
            String::new()
        } else {
            format!("{} | {} rows", table.cursor + 1, self.data_rows.len())
        };
        let faint = st().fgr(Role::Faint);
        let (left, right) = (faint.render(&left), faint.render(&right));
        let gap =
            (width as isize - 2 - style::width(&left) as isize - style::width(&right) as isize)
                .max(1) as usize;
        let footer = st()
            .width(width)
            .padding(&[0, 1])
            .render(&format!("{left}{}{right}", " ".repeat(gap)));
        let mut parts = vec![lines.join("\n")];
        for _ in 0..padding_h {
            parts.push(dash.clone());
        }
        parts.push(bottom_rule);
        if !detail.is_empty() {
            parts.push(detail);
        }
        parts.push(footer);
        Ok(parts.join("\n"))
    }

    fn chart_inspector(&self, width: usize, series: &[ChartSeries]) -> String {
        if series.len() != 1 {
            return String::new();
        }
        let points = &series[0].points;
        let Some(idx) = self.selected_point(points) else {
            return String::new();
        };
        let point = &points[idx];
        let dot = st().fgr(Role::Accent).render("●");
        let time = display_time(point.time, self.time_range.display)
            .format("%-I:%M %p, %b %-d, %Y")
            .to_string();
        let inspector = format!(
            "{}  {}  {dot} {} {}",
            st().fgr(Role::Body).render(&time),
            st().fgr(Role::Faint).render("|"),
            st().fgr(Role::Faint).render("value"),
            st().fgr(Role::Body)
                .bold(true)
                .render(&format_compact(point.value)),
        );
        if style::width(&inspector) > width {
            return st().max_width(width).render(&inspector);
        }
        inspector
    }

    fn chart_title(&self) -> String {
        let series = self.chart_series();
        let all = series.iter().flat_map(|s| s.points.iter());
        let first = all.clone().map(|p| p.time).min();
        let last = all.map(|p| p.time).max();
        let (Some(first), Some(last)) = (first, last) else {
            return String::new();
        };
        let duration = self.selected_duration(first, last);
        let mode = self.time_range.display;
        format!(
            "📊 {} → {}",
            format_range_time(display_time(first, mode), duration),
            format_range_time(display_time(last, mode), duration)
        )
    }

    fn render_chart(&self, width: usize, height: usize) -> Result<String, GoPanic> {
        let series = self.chart_series();
        let inspector = self.chart_inspector(width, &series);
        let legend_lines = if height >= 34 {
            4
        } else if height >= 24 {
            3
        } else {
            2
        };
        let legend = chart_legend(&series, width, legend_lines)?;
        let mut chart_h = height as isize - height_of(&legend) as isize;
        if !inspector.is_empty() {
            chart_h -= 1;
        }
        let chart_h = chart_h.max(1);
        let Some(chart) = self.build_chart(width as isize, chart_h, &series) else {
            let message = if self.extract_chart_data().is_none() {
                "no numeric data to plot"
            } else {
                "not enough data points to plot"
            };
            return Ok(place_center(
                width,
                height,
                &st().fgr(Role::Faint).render(message),
            ));
        };
        let output = chart.canvas.view().trim_end_matches('\n').to_owned();
        if output.is_empty() {
            let error = st()
                .fgr(Role::Err)
                .render("chart error: failed to generate chart");
            return Ok(place_center(width, height, &error));
        }
        let mut parts = Vec::new();
        if !inspector.is_empty() {
            parts.push(inspector);
        }
        parts.push(output);
        if !legend.is_empty() {
            parts.push(legend);
        }
        let body = join_vertical(&parts).trim_end_matches('\n').to_owned();
        Ok(st()
            .fgr(Role::Body)
            .width(width)
            .render(&body)
            .trim_end_matches('\n')
            .to_owned())
    }

    fn spotlight(&self) -> String {
        let inner_w = (SPOTLIGHT_WIDTH - 6).max(20);
        let title_left = st().fgr(Role::Accent).bold(true).render("SELECT DATASET");
        let count = if self.datasets_loading {
            String::new()
        } else {
            format!("{} datasets", self.filtered_datasets.len())
        };
        let title_right = st().fgr(Role::Faint).render(&count);
        let gap = (inner_w as isize
            - style::width(&title_left) as isize
            - style::width(&title_right) as isize)
            .max(1) as usize;
        let header = format!("{title_left}{}{title_right}", " ".repeat(gap));
        let rule = st().fgr(Role::BorderSoft).render(&"─".repeat(inner_w));
        let search = st()
            .width(inner_w)
            .render(&self.spotlight_filter.view(&filter_input_styles()));
        let mut list: Vec<String> = Vec::new();
        if self.datasets_loading {
            list.push(
                st().fgr(Role::Faint)
                    .width(inner_w)
                    .padding(&[1, 0])
                    .render(&format!("  {} loading…", spinner_view(self.spinner_frame))),
            );
        } else if self.filtered_datasets.is_empty() {
            list.push(
                st().fgr(Role::Faint)
                    .width(inner_w)
                    .padding(&[1, 0])
                    .render("  no datasets found"),
            );
        } else {
            let limit = self.filtered_datasets.len().min(SPOTLIGHT_MAX_ITEMS);
            let start = if self.dataset_idx >= SPOTLIGHT_MAX_ITEMS {
                self.dataset_idx - SPOTLIGHT_MAX_ITEMS + 1
            } else {
                0
            };
            let rail = st().bgr(Role::Active).render(" ");
            for (i, name) in self
                .filtered_datasets
                .iter()
                .enumerate()
                .take(start + limit)
                .skip(start)
            {
                if i == self.dataset_idx {
                    list.push(format!(
                        "{rail} {}",
                        st().fgr(Role::Active).bold(true).render(name)
                    ));
                } else {
                    list.push(format!("  {}", st().fgr(Role::Body).render(name)));
                }
            }
            if self.filtered_datasets.len() > SPOTLIGHT_MAX_ITEMS {
                list.push(
                    st().fgr(Role::Faint)
                        .width(inner_w)
                        .align(style::RIGHT)
                        .render(&format!(
                            "+{} more",
                            self.filtered_datasets.len() - SPOTLIGHT_MAX_ITEMS
                        )),
                );
            }
        }
        let body = join_vertical(&[
            header,
            rule,
            String::new(),
            search,
            String::new(),
            list.join("\n"),
        ]);
        st().border(Border::NORMAL)
            .border_fgr(Role::Border)
            .padding(&[1, 2])
            .width(SPOTLIGHT_WIDTH)
            .render(&body)
    }

    fn builder_column(
        &self,
        title: &str,
        items: &[String],
        selected: usize,
        loading: bool,
        focused: bool,
        col_w: usize,
    ) -> Vec<u8> {
        let inner_w = col_w.saturating_sub(2);
        let (border_role, title_role) = if focused {
            (Role::BorderHi, Role::Accent)
        } else {
            (Role::Border, Role::Faint)
        };
        let title_row = st()
            .fgr(title_role)
            .bold(true)
            .width(inner_w)
            .padding(&[0, 1])
            .render(&title.to_uppercase());
        let mut rows: Vec<Vec<u8>> = Vec::new();
        if loading {
            rows.push(
                st().fgr(Role::Faint)
                    .width(inner_w)
                    .padding(&[0, 1])
                    .render("loading...")
                    .into_bytes(),
            );
        } else if items.is_empty() {
            rows.push(
                st().fgr(Role::Faint)
                    .width(inner_w)
                    .padding(&[0, 1])
                    .render("(empty)")
                    .into_bytes(),
            );
        } else {
            let start = if selected >= BUILDER_MAX_ITEMS {
                selected - BUILDER_MAX_ITEMS + 1
            } else {
                0
            };
            let end = (start + BUILDER_MAX_ITEMS).min(items.len());
            let rail = st().bgr(Role::Active).render(" ");
            for (i, item) in items.iter().enumerate().take(end).skip(start) {
                let max_len = inner_w as isize - 4;
                let text = if max_len > 3 && item.len() > max_len as usize {
                    // Preserve the byte slice until the terminal renderer: a
                    // partial rune participates in ANSI width/wrap processing.
                    [&item.as_bytes()[..max_len as usize - 3], b"..."].concat()
                } else {
                    item.as_bytes().to_vec()
                };
                let (prefix, item_style) = if i == selected {
                    (format!(" {rail} "), st().fgr(Role::Active).bold(true))
                } else {
                    ("   ".into(), st().fgr(Role::Body))
                };
                rows.push([prefix.as_bytes(), &item_style.render_bytes(&text)].concat());
            }
        }
        let rows = rows.join(&b'\n');
        let content = style::join_vertical_bytes(0.0, &[title_row.as_bytes(), &rows]);
        st().border(Border::NORMAL)
            .border_fgr(border_role)
            .width(col_w)
            .render_bytes(&content)
    }

    fn builder_view(&self) -> Vec<u8> {
        let col_w = builder_col_width(self.width);
        let metrics = if self.dataset.is_empty() {
            vec!["── select a dataset first ──".to_owned()]
        } else {
            self.builder_metrics_filtered.clone()
        };
        let c0 = self.builder_column(
            "metrics",
            &metrics,
            self.builder_metrics_idx,
            self.builder_metrics_loading,
            self.builder_col == 0,
            col_w,
        );
        let c1 = self.builder_column(
            "labels",
            &self.builder_labels_filtered,
            self.builder_labels_idx,
            self.builder_labels_loading,
            self.builder_col == 1,
            col_w,
        );
        let c2 = self.builder_column(
            "values",
            &self.builder_values_filtered,
            self.builder_values_idx,
            self.builder_values_loading,
            self.builder_col == 2,
            col_w,
        );
        let columns = style::join_horizontal_bytes(0.0, &[&c0, &c1, &c2]);
        let cols_w = columns
            .split(|b| *b == b'\n')
            .map(style::width_bytes)
            .max()
            .unwrap_or(0);
        let expr = build_expr(
            &self.builder_current_metric(),
            &self.builder_current_label(),
            &self.builder_current_value(),
        );
        let expr_line = format!(
            "{}{}",
            st().fgr(Role::Faint).render("built  "),
            st().fgr(Role::Accent).bold(true).render(&expr)
        );
        let search = st()
            .border(Border::NORMAL)
            .border_fgr(Role::Border)
            .width(cols_w.saturating_sub(4))
            .padding(&[0, 1])
            .render(&self.builder_filter.view(&filter_input_styles()));
        let title = st()
            .fgr(Role::Accent)
            .bold(true)
            .width(cols_w)
            .align(style::CENTER)
            .render("PROMQL QUERY BUILDER");
        let body = style::join_vertical_bytes(
            0.0,
            &[
                title.as_bytes(),
                b"",
                &columns,
                b"",
                expr_line.as_bytes(),
                b"",
                search.as_bytes(),
            ],
        );
        st().border(Border::NORMAL)
            .border_fgr(Role::Border)
            .padding(&[1, 2])
            .render_bytes(&body)
    }

    /// `View`. A Go panic reached while rendering is recorded for
    /// `take_fault` (Bubble Tea recovers it in Run), and no frame is produced.
    pub(crate) fn view(&self) -> Vec<u8> {
        match self.render() {
            Ok(frame) => frame,
            Err(panic) => {
                *self.render_fault.borrow_mut() = Some(panic);
                Vec::new()
            }
        }
    }

    fn render(&self) -> Result<Vec<u8>, GoPanic> {
        if self.width == 0 || self.height == 0 {
            return Ok(Vec::new());
        }
        let bottom = self.bottom_bar(self.width)?;
        let bottom_h = height(&bottom);
        let sidebar_w = if self.width >= 140 {
            SQL_CONTROLS_MIN_WIDTH + 3
        } else {
            SQL_CONTROLS_MIN_WIDTH
        };
        let editor_w = (self.width as isize - sidebar_w as isize - 1).max(30) as usize;
        // Go resizes the View copy in both modes; the textarea viewport is
        // shared with the model (a pointer in Go, an Arc in the shared editor).
        let mut query = self.query.clone();
        query.set_width(editor_w - 6);
        query.set_height(TOP_H - 4);
        let editor_body = if self.editor_mode == EditorMode::Builder {
            let expr = self.query.value();
            if expr.is_empty() {
                st().fgr(Role::Faint)
                    .italic(true)
                    .render("Press Enter to open builder…")
            } else {
                st().fgr(Role::Accent).bold(true).render(&expr)
            }
        } else {
            let body = query.view(&AreaStyles::pb_editor());
            // The rendered copy carries its own fault cell.
            if let Some(fault) = query.take_fault() {
                self.view_fault.set(Some(fault));
            }
            body
        };
        let editor =
            self.editor_pane(&editor_body, editor_w, TOP_H, self.focus() == Pane::Query)?;
        let dataset = if self.dataset.is_empty() {
            "select-dataset"
        } else {
            &self.dataset
        };
        let step_display = if self.focus() == Pane::Step {
            self.step_input.view(&InputStyles::default())
        } else {
            self.step.clone()
        };
        let display = self.time_range.display;
        let sidebar = self.controls_pane(
            &format_control_time(self.time_range.start.time, display),
            &format_control_time(self.time_range.end.time, display),
            dataset,
            &step_display,
            sidebar_w,
            TOP_H,
        )?;
        let gap = st().width(1).height(TOP_H).render("");
        let top = join_horizontal_top(&[editor.as_str(), gap.as_str(), sidebar.as_str()]);

        let avail_h = (self.height as isize - TOP_H as isize - bottom_h as isize).max(6) as usize;
        let inner_h = avail_h.saturating_sub(3).max(3);
        let inner_w = self.width.saturating_sub(4).max(10);
        let mut inner = if !self.has_queried {
            let wordmark = st()
                .fgr(Role::Accent)
                .bold(true)
                .render(PARSEABLE_ASCII_ART);
            place_center(inner_w, inner_h, &wordmark)
        } else if self.loading {
            let content = st()
                .fgr(Role::Accent)
                .render(&format!("{} fetching...", spinner_view(self.spinner_frame)));
            place_center(inner_w, inner_h, &content)
        } else if !self.fetch_err.is_empty() {
            let rendered = st()
                .padding(&[1, 2])
                .fgr(Role::Err)
                .width(inner_w)
                .render(&self.fetch_err);
            rendered
                .split('\n')
                .take(inner_h)
                .collect::<Vec<_>>()
                .join("\n")
        } else if self.data_rows.is_empty() && (!self.chart_mode || self.chart_rows.is_empty()) {
            place_center(
                inner_w,
                inner_h,
                &st().fgr(Role::Faint).render("no results for this query"),
            )
        } else if self.chart_mode {
            self.render_chart(inner_w, inner_h)?
        } else {
            self.results_table(inner_w, inner_h)?
        };
        inner = inner
            .split('\n')
            .take(inner_h)
            .collect::<Vec<_>>()
            .join("\n");
        let (title, right) = if self.chart_mode {
            (
                "RESULTS | Chart View",
                st().fgr(Role::Accent)
                    .bold(true)
                    .render(&self.chart_title()),
            )
        } else {
            ("RESULTS | Table View", String::new())
        };
        let results = self.results_pane(&inner, self.width, avail_h, title, &right)?;
        let body = join_vertical(&[top, results]);
        let main_h = self.height.saturating_sub(bottom_h);
        let main = match self.overlay {
            Overlay::None => body,
            Overlay::Time => {
                place_center(self.width, main_h, &self.time_range_edit.view(self.now())?)
            }
            Overlay::Dataset => place_center(self.width, main_h, &self.spotlight()),
            Overlay::Builder => {
                let main = style::place_bytes(self.width, main_h, 0.5, 0.5, &self.builder_view());
                return Ok(st()
                    .width(self.width)
                    .render_bytes(&style::join_vertical_bytes(
                        0.0,
                        &[&main, bottom.as_bytes()],
                    )));
            }
        };
        Ok(st()
            .width(self.width)
            .render(&join_vertical(&[main, bottom]))
            .into_bytes())
    }
}

/// lipgloss.Height counts lines; "" is one line.
fn height_of(s: &str) -> usize {
    height(s)
}

/// Go `strconv.ParseFloat(s, 64)` success cases (`chartRowValue`): the
/// specials `[+-]inf`, `[+-]infinity` and unsigned `nan` (ASCII case
/// insensitive), decimal and `0x` hexadecimal literals with Go's underscore
/// placement rule. Results that overflow to ±Inf are ErrRange failures.
pub(crate) fn go_parse_float(s: &str) -> Option<f64> {
    if s.is_empty() {
        return None;
    }
    let lower = s.to_ascii_lowercase();
    let (negative, unsigned) = match lower.as_bytes()[0] {
        b'+' => (false, &lower[1..]),
        b'-' => (true, &lower[1..]),
        _ => (false, lower.as_str()),
    };
    if unsigned == "inf" || unsigned == "infinity" {
        return Some(if negative {
            f64::NEG_INFINITY
        } else {
            f64::INFINITY
        });
    }
    if lower == "nan" {
        return Some(f64::NAN);
    }
    if lower.contains('_') && !underscore_ok(&lower) {
        return None;
    }
    let cleaned: String = unsigned.chars().filter(|c| *c != '_').collect();
    let magnitude = if unsigned.starts_with("0x") {
        parse_hex_float(unsigned)?
    } else {
        let bytes = cleaned.as_bytes();
        let mut i = 0;
        let mut digits = 0;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
            digits += 1;
        }
        if i < bytes.len() && bytes[i] == b'.' {
            i += 1;
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
                digits += 1;
            }
        }
        if digits == 0 {
            return None;
        }
        if i < bytes.len() && bytes[i] == b'e' {
            i += 1;
            if i < bytes.len() && matches!(bytes[i], b'+' | b'-') {
                i += 1;
            }
            let start = i;
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            if i == start {
                return None;
            }
        }
        if i != bytes.len() {
            return None;
        }
        cleaned.parse::<f64>().ok()?
    };
    if magnitude.is_infinite() {
        return None;
    }
    Some(if negative { -magnitude } else { magnitude })
}

/// strconv underscoreOK: underscores only between digits (or after a base prefix).
fn underscore_ok(s: &str) -> bool {
    let b = s.as_bytes();
    let mut i = 0;
    let mut saw = b'^';
    let b = if !b.is_empty() && (b[0] == b'-' || b[0] == b'+') {
        &b[1..]
    } else {
        b
    };
    let mut hex = false;
    if b.len() >= 2 && b[0] == b'0' && matches!(b[1].to_ascii_lowercase(), b'b' | b'o' | b'x') {
        i = 2;
        saw = b'0';
        hex = b[1].eq_ignore_ascii_case(&b'x');
    }
    while i < b.len() {
        let c = b[i];
        if c.is_ascii_digit() || (hex && c.to_ascii_lowercase().is_ascii_hexdigit()) {
            saw = b'0';
        } else if c == b'_' {
            if saw != b'0' {
                return false;
            }
            saw = b'_';
        } else {
            if saw == b'_' {
                return false;
            }
            saw = b'!';
        }
        i += 1;
    }
    saw != b'_'
}

/// strconv `readFloat` in base 16 followed by `atofHex` for float64. `s` is
/// the unsigned, lowercased literal including its `0x` prefix (underscores
/// kept; their placement was already checked by `underscore_ok`). Returns
/// None for a syntax error or for overflow (Go: ±Inf with ErrRange).
///
/// Ported from Go 1.25.4 src/strconv/atof.go.
/// Copyright 2009 The Go Authors. All rights reserved. Use of this source
/// code is governed by a BSD-style license that can be found in
/// src/LICENSE-Go.txt.
fn parse_hex_float(s: &str) -> Option<f64> {
    const MAX_MANT_DIGITS: i64 = 16; // 16^16 fits in uint64
    let b = s.as_bytes();
    if b.len() < 3 {
        return None; // readFloat needs i+2 < len(s) to take the base prefix
    }
    let mut i = 2;
    let (mut mantissa, mut trunc) = (0u64, false);
    let (mut sawdot, mut sawdigits) = (false, false);
    let (mut nd, mut nd_mant, mut dp) = (0i64, 0i64, 0i64);
    while i < b.len() {
        let c = b[i];
        match c {
            b'_' => {}
            b'.' => {
                if sawdot {
                    break;
                }
                sawdot = true;
                dp = nd;
            }
            b'0'..=b'9' | b'a'..=b'f' => {
                sawdigits = true;
                if c == b'0' && nd == 0 {
                    // leading zeros are ignored
                    dp -= 1;
                } else {
                    nd += 1;
                    if nd_mant < MAX_MANT_DIGITS {
                        mantissa = mantissa * 16 + u64::from(char::from(c).to_digit(16)?);
                        nd_mant += 1;
                    } else if c != b'0' {
                        trunc = true;
                    }
                }
            }
            _ => break,
        }
        i += 1;
    }
    if !sawdigits {
        return None;
    }
    if !sawdot {
        dp = nd;
    }
    dp *= 4;
    nd_mant *= 4;
    // The `p` exponent is mandatory for hexadecimal.
    if i >= b.len() || b[i] != b'p' {
        return None;
    }
    i += 1;
    if i >= b.len() {
        return None;
    }
    let mut esign = 1;
    if b[i] == b'+' {
        i += 1;
    } else if b[i] == b'-' {
        i += 1;
        esign = -1;
    }
    if i >= b.len() || !b[i].is_ascii_digit() {
        return None;
    }
    let mut e = 0i64;
    while i < b.len() && (b[i].is_ascii_digit() || b[i] == b'_') {
        if b[i] != b'_' && e < 10000 {
            e = e * 10 + i64::from(b[i] - b'0');
        }
        i += 1;
    }
    dp += e * esign;
    if i != b.len() {
        return None;
    }
    let exp = if mantissa != 0 { dp - nd_mant } else { 0 };
    atof_hex(mantissa, exp, trunc)
}

/// strconv `atofHex` for float64 (positive; the caller applies the sign).
fn atof_hex(mut mantissa: u64, exp: i64, trunc: bool) -> Option<f64> {
    const MANTBITS: u32 = 52;
    const EXPBITS: u32 = 11;
    const BIAS: i64 = -1023;
    const MAX_EXP: i64 = (1 << EXPBITS) + BIAS - 2;
    const MIN_EXP: i64 = BIAS + 1;
    let mut exp = exp + i64::from(MANTBITS);
    while mantissa != 0 && mantissa >> (MANTBITS + 2) == 0 {
        mantissa <<= 1;
        exp -= 1;
    }
    if trunc {
        mantissa |= 1;
    }
    while mantissa >> (1 + MANTBITS + 2) != 0 {
        mantissa = (mantissa >> 1) | (mantissa & 1);
        exp += 1;
    }
    // Denormalize a too-negative exponent (the -2 is for the rounding bits).
    while mantissa > 1 && exp < MIN_EXP - 2 {
        mantissa = (mantissa >> 1) | (mantissa & 1);
        exp += 1;
    }
    // Round using the two bottom bits, ties to even.
    let mut round = mantissa & 3;
    mantissa >>= 2;
    round |= mantissa & 1;
    exp += 2;
    if round == 3 {
        mantissa += 1;
        if mantissa == 1 << (1 + MANTBITS) {
            mantissa >>= 1;
            exp += 1;
        }
    }
    if mantissa >> MANTBITS == 0 {
        exp = BIAS; // denormal or zero
    }
    if exp > MAX_EXP {
        return None; // ±Inf with ErrRange; chartRowValue rejects it
    }
    let bits = (mantissa & ((1 << MANTBITS) - 1))
        | ((((exp - BIAS) as u64) & ((1 << EXPBITS) - 1)) << MANTBITS);
    Some(f64::from_bits(bits))
}

/// `promqlChartLabelSet`: the text between the first "{" and the last "}".
fn chart_label_set(metric: &str) -> &str {
    if let (Some(open), Some(close)) = (metric.find('{'), metric.rfind('}'))
        && close > open
    {
        return &metric[open + 1..close];
    }
    metric
}

/// `renderChartLegend`.
fn chart_legend(series: &[ChartSeries], width: usize, max_lines: usize) -> Result<String, GoPanic> {
    if series.is_empty() || width == 0 {
        return Ok(String::new());
    }
    let max_lines = max_lines.max(1);
    let label_style = st().fgr(Role::Faint);
    let columns = if width >= 150 { 3 } else { 2 };
    let label_width = (width as isize / columns - 5).clamp(28, 72) as usize;
    let mut lines: Vec<String> = Vec::new();
    let mut current = String::new();
    for s in series {
        let mut label = chart_label_set(&s.label).to_owned();
        if label.is_empty() {
            label = s.label.clone();
        }
        let item = format!(
            "{} {}",
            st().fg_hex(&s.color).render("●"),
            label_style.render(&ui_truncate(&label, label_width)?)
        );
        if current.is_empty() {
            current = item;
            continue;
        }
        if style::width(&current) + 2 + style::width(&item) > width {
            lines.push(std::mem::replace(&mut current, item));
            if lines.len() == max_lines {
                break;
            }
            continue;
        }
        current = format!("{current}  {item}");
    }
    if !current.is_empty() && lines.len() < max_lines {
        lines.push(current);
    }
    if lines.len() == max_lines {
        let rendered: usize = lines.iter().map(|l| l.matches('●').count()).sum();
        if rendered < series.len() {
            let more = label_style.render(&format!("+{} more", series.len() - rendered));
            let last = lines.len() - 1;
            if style::width(&lines[last]) + 2 + style::width(&more) <= width {
                lines[last] = format!("{}  {more}", lines[last]);
            }
        }
    }
    Ok(lines.join("\n"))
}

// ─── runner ─────────────────────────────────────────────────────────────────

/// `pb promql run -i`: called after output and time validation.
pub(crate) fn execute(
    parsed: &ParsedInvocation,
    stdout: &mut (dyn Write + Send),
    _stderr: &mut (dyn Write + Send),
) -> Result<(), CliError> {
    let command_error = |message: String| CliError::new(ErrorCode::CommandFailed, message);
    // parseTimeStr keeps time.Parse's location (UTC for `Z`, a fixed zone or
    // Local for an offset); relative values are Local (G4).
    let to = parse_cli_time(parsed.text("to"))
        .map_err(|e| command_error(format!("invalid --to: {e}")))?;
    let from_text = if parsed.explicit("from") {
        parsed.text("from")
    } else {
        "10m"
    };
    let from =
        parse_cli_time(from_text).map_err(|e| command_error(format!("invalid --from: {e}")))?;
    let store =
        crate::config::ProfileStore::from_env().map_err(|e| CliError::new(e.code, e.message))?;
    let profile = store
        .active_profile()
        .map_err(|e| CliError::new(e.code, e.message))?;
    let client = crate::api::ApiClient::new(profile, store)?;
    let service = PromqlSessionService::new(client);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| command_error(e.to_string()))?;
    let setup = Setup {
        profile_url: service.profile_url(),
        expr: parsed.positionals.first().cloned().unwrap_or_default(),
        start: from,
        end: to,
        step: parsed.text("step").to_owned(),
        dataset: parsed.text("dataset").to_owned(),
        instant: parsed.boolean("instant"),
        width: 0,
        height: 0,
    };
    runtime.block_on(run_session(setup, service, stdout))
}

async fn run_session(
    mut setup: Setup,
    service: PromqlSessionService,
    stdout: &mut (dyn Write + Send),
) -> Result<(), CliError> {
    let session_error =
        |e: super::terminal::SessionError| CliError::new(ErrorCode::CommandFailed, e.to_string());
    let mut session = TerminalSession::open(Screen::Fullscreen).map_err(session_error)?;
    if let Some(size) = session.size() {
        setup.width = size.width as usize;
        setup.height = size.height as usize;
    }
    let mut state = PromqlState::new(setup);
    let mut tasks = Tasks::new(service);
    tasks.spawn_all(state.init());
    let view = state.view();
    if let Some(fault) = state.take_fault() {
        return editor_fault(session, tasks, fault, stdout).await;
    }
    session.draw_bytes(&view);
    let mut inbox = Inbox::default();
    let outcome = loop {
        if let Some(panic) = tasks.reap() {
            return command_panic(session, tasks, &panic, stdout);
        }
        let message = match next_input(&mut inbox, &mut session, &mut tasks.completions).await {
            Input::Event(Some(Event::Panicked(panic))) => {
                return command_panic(session, tasks, &panic, stdout);
            }
            Input::Event(None | Some(Event::Quit)) => break Ok(()),
            Input::Event(Some(Event::InputError(error))) => break Err(error),
            Input::Event(Some(Event::Unknown)) => continue,
            Input::Event(Some(Event::Resize(size))) => Message::Resize {
                width: size.width as usize,
                height: size.height as usize,
            },
            Input::Event(Some(Event::Key(key))) => Message::Key(key),
            Input::Done(completion) => {
                tasks.finished(&completion);
                Message::Done(completion)
            }
        };
        let effects = state.update(message);
        if let Some(fault) = state.take_fault() {
            return editor_fault(session, tasks, fault, stdout).await;
        }
        let quit = effects.contains(&Effect::Quit);
        tasks.spawn_all(effects);
        if quit {
            break Ok(());
        }
        let view = state.view();
        if let Some(fault) = state.take_fault() {
            return editor_fault(session, tasks, fault, stdout).await;
        }
        session.draw_bytes(&view);
    };
    // Go renders the final view (Run, tea.go:601) and restores before
    // returning; a panic in that View is recovered like any other.
    let final_view = state.view();
    if let Some(fault) = state.take_fault() {
        return editor_fault(session, tasks, fault, stdout).await;
    }
    let _ = session.close_bytes(&final_view);
    drop(tasks);
    outcome.map_err(|error| CliError::new(ErrorCode::CommandFailed, error))
}

/// Where bubbles would panic or spin inside Update/View, reproduce the Go
/// program outcome. Bubble Tea v1.2.2 `recoverFromPanic` (tea.go:701):
/// kill-restore (no final render), print the panic to stdout, print the
/// goroutine stack to stderr (no Rust equivalent; recorded as a gap), and
/// `Run` returns its zero values `(nil, nil)`, so `pb` exits 0 silently. A
/// hang leaves the program stuck inside Update: input and signals are never
/// processed again, so the terminal stays in the session until killed.
/// `runPromqlQuery` ignores the model and returns Run's nil error.
async fn editor_fault(
    session: TerminalSession,
    tasks: Tasks,
    fault: Fault,
    stdout: &mut (dyn Write + Send),
) -> Result<(), CliError> {
    let panic = match fault {
        Fault::Hang => {
            let _held = (session, tasks);
            std::future::pending::<()>().await;
            unreachable!("a pending future never resolves");
        }
        Fault::Panic(panic) => panic,
    };
    drop(tasks);
    drop(session);
    let _ = write!(
        stdout,
        "Caught panic:\n\n{panic}\n\nRestoring terminal...\n\n"
    );
    let _ = stdout.flush();
    Ok(())
}

enum Input {
    Event(Option<Event>),
    Done(Completion),
}

/// Go `%v` of a recovered panic value (Rust payloads are &str or String).
fn panic_text(payload: Box<dyn std::any::Any + Send>) -> String {
    match payload.downcast::<String>() {
        Ok(text) => *text,
        Err(payload) => payload
            .downcast_ref::<&str>()
            .map_or_else(|| "Box<dyn Any>".to_owned(), |s| (*s).to_owned()),
    }
}

/// A panic inside a command goroutine. Bubble Tea v1.2.2 wraps every command
/// in `defer p.recoverFromPanic()` (tea.go:318-326): `shutdown(true)` kills the
/// renderer without a final frame and restores the terminal, `Caught panic:`
/// goes to stdout and a goroutine stack to stderr (not reproducible). The
/// canceled context also ends the event loop, so `Run` returns
/// `ErrProgramKilled` and `pb` reports `program was killed: context canceled`.
/// In Go the panicking goroutine's print races `Run` returning; it is printed
/// first here.
fn command_panic(
    session: TerminalSession,
    tasks: Tasks,
    panic: &str,
    stdout: &mut (dyn Write + Send),
) -> Result<(), CliError> {
    drop(tasks);
    drop(session);
    let _ = write!(
        stdout,
        "Caught panic:\n\n{panic}\n\nRestoring terminal...\n\n"
    );
    let _ = stdout.flush();
    Err(CliError::new(
        ErrorCode::CommandFailed,
        "program was killed: context canceled",
    ))
}

/// Bubble Tea delivers key/resize messages and command results through one
/// channel (`p.msgs`), so Update sees them in arrival order. Here they come
/// from two sources, each stamped when it enters its queue: a terminal event
/// when the reader pushes it onto the terminal's event queue, a completion
/// when its task reserves capacity on the bounded channel (`Outbox`). The
/// earlier head is processed first, so a slow View cannot reorder a result
/// behind later keys or vice versa.
///
/// At most one completion head is held here (`done`); the terminal head
/// stays in the terminal queue and is only peeked. Every other completion
/// stays in the bounded channel, and a task finding it full waits in its
/// send, so both sources keep their own backpressure and a continuously
/// replenished source cannot grow a local queue or starve the other.
/// Comparing heads is exact: an item that was not yet queued when a head was
/// compared is stamped later than that head.
///
/// The terminal side is only *peeked* (its head stamp) until it wins: an
/// event is accepted with `try_next_event_stamped` only when it is earlier
/// than the completion head. Accepting earlier would let the terminal treat a
/// Quit as received (restoring signal dispositions, freeing the reader's one
/// pending slot) while an earlier completion is still being processed.
#[derive(Default)]
struct Inbox {
    done: Option<(std::time::Instant, Completion)>,
}

/// State of the terminal queue head.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Peek {
    Ready(std::time::Instant),
    Empty,
    Closed,
}

/// The terminal event source (a seam for ordering/bounded tests).
trait EventSource {
    /// Stamp of the next event without accepting it.
    fn peek(&self) -> Peek;
    /// Resolves (`Ready` or `Closed`) once an event is queued; cancel-safe.
    fn ready(&self) -> impl Future<Output = Peek>;
    /// Accept the head event. Called only right after `peek` returned `Ready`.
    fn take(&mut self) -> Option<(std::time::Instant, Event)>;
}

impl EventSource for TerminalSession {
    fn peek(&self) -> Peek {
        match self.peek_event_stamp() {
            Ok(Some(at)) => Peek::Ready(at),
            Ok(None) => Peek::Empty,
            Err(_) => Peek::Closed,
        }
    }
    async fn ready(&self) -> Peek {
        match self.event_ready().await {
            Ok(at) => Peek::Ready(at),
            Err(_) => Peek::Closed,
        }
    }
    fn take(&mut self) -> Option<(std::time::Instant, Event)> {
        self.try_next_event_stamped().ok().flatten()
    }
}

/// One runner step: yield first so request and timer tasks progress on the
/// current-thread runtime even under continuous input, then merge heads.
async fn next_input(
    inbox: &mut Inbox,
    source: &mut impl EventSource,
    completions: &mut tokio::sync::mpsc::Receiver<(std::time::Instant, Completion)>,
) -> Input {
    tokio::task::yield_now().await;
    loop {
        if inbox.done.is_none()
            && let Ok(done) = completions.try_recv()
        {
            inbox.done = Some(done);
        }
        let done_at = inbox.done.as_ref().map(|(at, _)| *at);
        match (source.peek(), done_at) {
            // An event not yet queued will be stamped after the completion
            // head, which is already handed over; an earlier completion wins.
            (Peek::Ready(event_at), Some(done_at)) if done_at < event_at => {}
            (Peek::Empty | Peek::Closed, Some(_)) => {}
            (Peek::Ready(_), _) => {
                if let Some((_, event)) = source.take() {
                    return Input::Event(Some(event));
                }
                continue;
            }
            (Peek::Closed, None) => return Input::Event(None),
            (Peek::Empty, None) => {
                // Wait for either source without accepting anything.
                tokio::select! {
                    _ = source.ready() => {}
                    Some(done) = completions.recv() => inbox.done = Some(done),
                }
                continue;
            }
        }
        if let Some((_, done)) = inbox.done.take() {
            return Input::Done(done);
        }
    }
}

/// A task's end of the completion channel. Each completion is stamped when
/// the task hands it over, which is when Go's command goroutine would send
/// its message on `p.msgs`. The bounded channel applies backpressure; nothing
/// is dropped.
#[derive(Clone)]
struct Outbox(tokio::sync::mpsc::Sender<(std::time::Instant, Completion)>);

impl Outbox {
    async fn send(
        &self,
        completion: Completion,
    ) -> Result<(), tokio::sync::mpsc::error::SendError<(std::time::Instant, Completion)>> {
        match self.0.reserve().await {
            Ok(permit) => {
                // Stamp once capacity is available: the stamp is the handoff.
                permit.send((std::time::Instant::now(), completion));
                Ok(())
            }
            Err(_) => Err(tokio::sync::mpsc::error::SendError((
                std::time::Instant::now(),
                completion,
            ))),
        }
    }
}

/// Owns every in-flight effect; dropping it aborts outstanding requests.
struct Tasks {
    service: PromqlSessionService,
    set: tokio::task::JoinSet<()>,
    sender: Outbox,
    completions: tokio::sync::mpsc::Receiver<(std::time::Instant, Completion)>,
    cancels: HashMap<u64, tokio::sync::watch::Sender<bool>>,
}

impl Tasks {
    fn new(service: PromqlSessionService) -> Self {
        let (sender, completions) = tokio::sync::mpsc::channel(64);
        Self {
            service,
            set: tokio::task::JoinSet::new(),
            sender: Outbox(sender),
            completions,
            cancels: HashMap::new(),
        }
    }

    fn finished(&mut self, completion: &Completion) {
        match completion {
            Completion::Labels { id, .. } | Completion::Values { id, .. } => {
                self.cancels.remove(id);
            }
            _ => {}
        }
    }

    /// Release finished task handles every loop iteration, including tasks
    /// that end without a completion (canceled builder requests). A task that
    /// panicked is reported: it is a panic inside a Go command goroutine.
    fn reap(&mut self) -> Option<String> {
        while let Some(joined) = self.set.try_join_next() {
            if let Err(error) = joined
                && error.is_panic()
            {
                return Some(panic_text(error.into_panic()));
            }
        }
        None
    }

    fn spawn_all(&mut self, effects: Vec<Effect>) {
        for effect in effects {
            self.spawn(effect);
        }
    }

    fn spawn(&mut self, effect: Effect) {
        let sender = self.sender.clone();
        let service = self.service.clone();
        match effect {
            Effect::Quit => {}
            Effect::Cancel { id } => {
                if let Some(cancel) = self.cancels.remove(&id) {
                    let _ = cancel.send(true);
                }
            }
            Effect::ReadClipboard => {
                self.set.spawn(async move {
                    let result = super::clipboard::read().await;
                    let _ = sender.send(Completion::Paste(result)).await;
                });
            }
            Effect::Tick { tag } => {
                self.set.spawn(async move {
                    let _ = sender.send(Completion::Tick { tag }).await;
                });
            }
            Effect::TickAfter { tag } => {
                self.set.spawn(async move {
                    tokio::time::sleep(SPINNER_INTERVAL).await;
                    let _ = sender.send(Completion::Tick { tag }).await;
                });
            }
            Effect::FetchResult(request) => {
                self.set.spawn(async move {
                    // NewPromqlModeFetchTask recovers its own panics into
                    // `errMsg = "panic: %v"` (promql.go:2114-2119). The inner
                    // set is owned by this task, so aborting it aborts the query.
                    let mut inner = tokio::task::JoinSet::new();
                    // Go keeps running here, so the session's panic reaction
                    // (restore + Event::Panicked) must not fire: terminal P2.
                    inner.spawn(super::terminal::recovered(async move {
                        service.query(request).await
                    }));
                    let result = match inner.join_next().await {
                        Some(Ok(result)) => result,
                        Some(Err(error)) if error.is_panic() => {
                            Err(format!("panic: {}", panic_text(error.into_panic())))
                        }
                        _ => return,
                    };
                    let _ = sender.send(Completion::Result(result)).await;
                });
            }
            Effect::FetchDatasets => {
                self.set.spawn(async move {
                    let result = service.metric_datasets().await;
                    let _ = sender.send(Completion::Datasets(result)).await;
                });
            }
            Effect::FetchCacheMetrics { dataset } => {
                self.set.spawn(async move {
                    let result = service.cache_metrics(&dataset).await;
                    let _ = sender
                        .send(Completion::CacheMetrics { dataset, result })
                        .await;
                });
            }
            Effect::FetchLabels {
                id,
                dataset,
                metric,
                start,
                end,
            } => {
                let (cancel, canceled) = tokio::sync::watch::channel(false);
                self.cancels.insert(id, cancel);
                self.set.spawn(async move {
                    if let Some(result) = service
                        .builder_labels(&dataset, &metric, &start, &end, canceled)
                        .await
                    {
                        let _ = sender.send(Completion::Labels { id, metric, result }).await;
                    }
                });
            }
            Effect::FetchValues {
                id,
                dataset,
                metric,
                label,
                start,
                end,
            } => {
                let (cancel, canceled) = tokio::sync::watch::channel(false);
                self.cancels.insert(id, cancel);
                self.set.spawn(async move {
                    if let Some(result) = service
                        .builder_values(&dataset, &metric, &label, &start, &end, canceled)
                        .await
                    {
                        let _ = sender
                            .send(Completion::Values {
                                id,
                                metric,
                                label,
                                result,
                            })
                            .await;
                    }
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixed_now() -> DateTime<Local> {
        Local
            .timestamp_opt(1_760_000_000, 0)
            .single()
            .expect("valid instant")
    }

    fn key(code: KeyCode) -> Key {
        Key {
            code,
            runes: Vec::new(),
            alt: false,
            paste: false,
        }
    }

    fn rune(c: char) -> Key {
        Key {
            code: KeyCode::Runes,
            runes: vec![c],
            alt: false,
            paste: false,
        }
    }

    fn state(expr: &str, dataset: &str, instant: bool) -> PromqlState {
        let end = go_now(fixed_now());
        PromqlState::with_clock(
            Setup {
                profile_url: "http://pb.test".into(),
                expr: expr.into(),
                start: go_add(end, -10 * 60 * NANOS_PER_SECOND),
                end,
                step: "auto".into(),
                dataset: dataset.into(),
                instant,
                width: 120,
                height: 40,
            },
            fixed_now,
        )
    }

    fn press(s: &mut PromqlState, k: Key) -> Vec<Effect> {
        s.update(Message::Key(k))
    }

    fn row(ts: &str, metric: &str, value: &str) -> Row {
        Row {
            timestamp: trim_timestamp_to_hms(ts),
            timestamp_full: ts.into(),
            metric: metric.into(),
            value: value.into(),
        }
    }

    fn matrix(rows: Vec<Row>) -> Completion {
        Completion::Result(Ok(ResultData {
            result_type: "matrix".into(),
            chart_result: "matrix".into(),
            chart_rows: rows.clone(),
            rows,
            series_count: 1,
            value_width: 14,
        }))
    }

    fn vector(rows: Vec<Row>) -> Completion {
        Completion::Result(Ok(ResultData {
            result_type: "vector".into(),
            chart_result: String::new(),
            chart_rows: Vec::new(),
            rows,
            series_count: 1,
            value_width: 14,
        }))
    }

    /// Fixture paths also used for the retained exact pre/post byte comparison.
    #[test]
    fn row_storage_frame_fixtures() {
        fn check(name: &str, s: &PromqlState) {
            let frame = s.view();
            assert!(!frame.is_empty(), "{name}");
            assert!(s.take_fault().is_none(), "{name}");
            let table = &s.table;
            let position = (table.cursor, table.current_page, table.page_size);
            assert_eq!(s.view(), frame, "{name}: repeat render");
            assert_eq!(
                position,
                (table.cursor, table.current_page, table.page_size)
            );
            assert!(Arc::ptr_eq(&s.data_rows, &table.rows));
        }
        let mut s = state("up", "m", false);
        let rows = (0..80)
            .map(|i| {
                row(
                    "2026-01-01T00:00:00Z",
                    &format!("series_{i:02}{{job=\"API\", instance=\"a-long-label\"}}"),
                    &i.to_string(),
                )
            })
            .collect();
        s.update(Message::Done(vector(rows)));
        check("table", &s);
        press(&mut s, key(KeyCode::PgDown));
        check("page", &s);
        press(&mut s, rune('w'));
        check("detail", &s);
        press(&mut s, rune('/'));
        for c in "SERIES_2".chars() {
            press(&mut s, rune(c));
        }
        check("metric-filter", &s);
        s.table.filter.set_value("79");
        s.table.with_highlighted_row(0);
        check("value-filter", &s);
        s.table.filter.set_value("no-match");
        check("empty-filter", &s);
        s.table.filter.set_value("");
        s.table.filter.blur();
        for (width, height) in [(100, 30), (140, 45)] {
            s.update(Message::Resize { width, height });
            press(&mut s, key(KeyCode::End));
            check(&format!("resize-{width}"), &s);
        }
        s.update(Message::Done(matrix(vec![
            row("2026-01-01T00:00:00Z", "up{job=\"api\"}", "1"),
            row("2026-01-01T00:01:00Z", "up{job=\"api\"}", "2"),
        ])));
        check("chart", &s);
        s.update(Message::Done(Completion::Result(Ok(ResultData {
            result_type: "matrix".into(),
            chart_result: "matrix".into(),
            rows: vec![
                row("2026-01-01T00:00:00Z", "fallback", "3"),
                row("2026-01-01T00:01:00Z", "fallback", "4"),
            ],
            chart_rows: Vec::new(),
            series_count: 1,
            value_width: 14,
        }))));
        check("chart-fallback", &s);
        s.update(Message::Done(vector(vec![row(
            "2026-01-01T00:00:00Z",
            "replacement",
            "9",
        )])));
        check("replacement", &s);
        s.update(Message::Done(Completion::Result(Err(
            "fixture error".into()
        ))));
        check("error", &s);
    }

    #[test]
    fn row_storage_is_shared_by_state_table_and_view_snapshots() {
        let mut s = state("up", "m", false);
        let mut result = match matrix(vec![
            row("2026-01-01T00:00:00Z", "fallback", "1"),
            row("2026-01-01T00:01:00Z", "fallback", "2"),
        ]) {
            Completion::Result(Ok(data)) => data,
            _ => unreachable!(),
        };
        result.chart_rows.clear();
        s.update(Message::Done(Completion::Result(Ok(result))));
        assert!(Arc::ptr_eq(&s.data_rows, &s.table.rows));
        assert!(Arc::ptr_eq(&s.data_rows, &s.chart_rows));
        let snapshot = s.clone();
        assert!(Arc::ptr_eq(&s.data_rows, &snapshot.data_rows));
        assert!(Arc::ptr_eq(&s.chart_rows, &snapshot.chart_rows));
        assert!(Arc::ptr_eq(&s.table.rows, &snapshot.table.rows));
        // These are the table-copy operations used by results_table.
        let mut view_table = s.table.clone();
        view_table.with_rows(Arc::clone(&s.data_rows));
        view_table.with_page_size(1);
        view_table.with_highlighted_row(1);
        assert!(Arc::ptr_eq(&s.data_rows, &view_table.rows));
        assert!(std::ptr::eq(&s.data_rows[1], &view_table.rows[1]));
        assert_eq!(s.table.cursor, 0);
        assert_ne!(s.table.page_size, view_table.page_size);
        s.update_table_columns(20);
        assert!(Arc::ptr_eq(&snapshot.data_rows, &s.table.rows));
        assert!(!s.results_table(100, 20).unwrap().is_empty());
        assert!(Arc::ptr_eq(&snapshot.data_rows, &s.data_rows));

        // Both-mode chart rows are distinct from the instant table, and each
        // allocation must remain shared when the whole state is cloned.
        s.mode = QueryMode::Both;
        s.update(Message::Done(Completion::Result(Ok(ResultData {
            result_type: "vector".into(),
            chart_result: "matrix".into(),
            rows: vec![row("2026-01-01T00:02:00Z", "instant", "9")],
            chart_rows: vec![
                row("2026-01-01T00:00:00Z", "range", "3"),
                row("2026-01-01T00:01:00Z", "range", "4"),
            ],
            series_count: 1,
            value_width: 14,
        }))));
        let both = s.clone();
        assert!(!Arc::ptr_eq(&s.data_rows, &s.chart_rows));
        assert!(Arc::ptr_eq(&s.data_rows, &both.data_rows));
        assert!(Arc::ptr_eq(&s.chart_rows, &both.chart_rows));
        assert!(Arc::ptr_eq(&s.data_rows, &both.table.rows));
        let series = s.extract_chart_data().unwrap();
        assert_eq!(series[0].0, "range");
        assert_eq!(series[0].2, vec![3.0, 4.0]);
        assert_eq!(s.table.highlighted_row().unwrap().metric, "instant");
    }

    #[test]
    fn row_replacement_and_error_clear_preserve_old_snapshots() {
        let mut s = state("up", "m", false);
        s.update(Message::Done(matrix(vec![
            row("2026-01-01T00:00:00Z", "old", "1"),
            row("2026-01-01T00:01:00Z", "old", "2"),
        ])));
        let old = s.clone();
        let old_frame = old.view();
        s.update(Message::Done(vector(vec![row(
            "2026-01-01T00:00:00Z",
            "new",
            "9",
        )])));
        assert!(!Arc::ptr_eq(&s.data_rows, &old.data_rows));
        assert!(Arc::ptr_eq(&s.data_rows, &s.table.rows));
        assert!(s.chart_rows.is_empty());
        assert_eq!(s.table.highlighted_row().unwrap().metric, "new");
        assert_eq!(old.view(), old_frame);
        assert_eq!(old.chart_rows.len(), 2);
        let replacement = s.clone();
        let replacement_frame = replacement.view();
        s.update(Message::Done(Completion::Result(Err("broken".into()))));
        assert!(s.data_rows.is_empty());
        assert!(s.chart_rows.is_empty());
        assert!(s.table.rows.is_empty());
        assert!(s.table.highlighted_row().is_none());
        assert_eq!((s.table.cursor, s.table.current_page), (0, 0));
        assert_eq!(s.fetch_err, "broken");
        assert_eq!(s.status_error, "query failed");
        assert_eq!(s.chart_graph_width, None);
        assert_eq!(replacement.view(), replacement_frame);
        assert_eq!(old.view(), old_frame);
        assert_eq!(old.data_rows[0].value, "1");
        assert_eq!(replacement.data_rows[0].value, "9");
    }

    #[test]
    fn shared_rows_keep_filter_selection_and_metric_detail_local() {
        let mut s = state("up", "m", false);
        s.update(Message::Done(vector(vec![
            row("2026-01-01T00:00:00Z", "API Alpha", "10"),
            row("2026-01-01T00:01:00Z", "other", "20"),
            row("2026-01-01T00:02:00Z", "api Beta", "30"),
        ])));
        let unfiltered = s.clone();
        press(&mut s, rune('/'));
        for c in "API".chars() {
            press(&mut s, rune(c));
        }
        assert_eq!(s.table.visible_rows().len(), 2);
        assert_eq!(s.table.filter.value(), "API");
        assert_eq!(unfiltered.table.filter.value(), "");
        assert!(!unfiltered.table.filter.focused());
        assert!(Arc::ptr_eq(&unfiltered.table.rows, &s.table.rows));
        // Filter focus consumes navigation and resets selection, as before.
        press(&mut s, key(KeyCode::PgDown));
        assert_eq!((s.table.cursor, s.table.current_page), (0, 0));
        s.table.filter.blur();
        press(&mut s, key(KeyCode::Down));
        assert_eq!(s.table.highlighted_row().unwrap(), s.data_rows[2]);
        let detail = s.metric_detail(&s.table, 80, 4).unwrap();
        assert!(detail.contains("api Beta"));
        assert!(!detail.contains("API Alpha"));
        assert_eq!(unfiltered.table.cursor, 0);
        let selected = s.clone();
        s.table.filter.set_value("20");
        s.table.with_highlighted_row(0);
        assert_eq!(s.table.highlighted_row().unwrap(), s.data_rows[1]);
        assert_eq!(
            selected.table.highlighted_row().unwrap(),
            selected.data_rows[2]
        );
        s.table.filter.set_value("00:01:00");
        assert!(s.table.visible_rows().is_empty());
        assert!(s.table.highlighted_row().is_none());
        assert!(s.metric_detail(&s.table, 80, 4).unwrap().is_empty());
        assert_eq!(s.table.max_pages(), 1);
    }

    #[test]
    fn shared_dense_rows_keep_step_15_and_local_render_pagination() {
        let mut s = state("up", "m", false);
        s.update(Message::Resize {
            width: 120,
            height: 40,
        });
        s.update(Message::Done(vector(
            (0..256_000)
                .map(|i| {
                    row(
                        "2026-01-01T00:00:00Z",
                        &format!("bench-series-{:04}", i / 256),
                        &i.to_string(),
                    )
                })
                .collect(),
        )));
        assert_eq!(s.table.page_size, 15);
        s.table.with_highlighted_row(7680);
        let rows = Arc::clone(&s.data_rows);
        // The retained unfinished forward-31 burst: 18 separate keys/views.
        for step in 1..=18 {
            press(&mut s, key(KeyCode::PgDown));
            assert_eq!(s.table.cursor, 7680 + step * 15);
            assert!(
                s.results_table(118, 20)
                    .unwrap()
                    .contains(&format!("{} | 256000 rows", 7681 + step * 15))
            );
            assert!(Arc::ptr_eq(&rows, &s.table.rows));
        }
        assert_eq!(s.table.cursor, 7950);
        assert_eq!(
            s.table.highlighted_row().unwrap().metric,
            "bench-series-0031"
        );
        let original_page = s.table.current_page;
        // At height 14 the empty detail still consumes one line, leaving
        // eight render rows starting at 7944. The update page starts at 7950.
        let mut local = s.table.clone();
        local.with_page_size(8);
        local.with_highlighted_row(s.table.cursor);
        assert_eq!(local.current_page * local.page_size, 7944);
        let rendered = s.results_table(118, 14).unwrap();
        assert!(rendered.contains("7951 | 256000 rows"));
        assert!(rendered.contains("7944"));
        assert!(rendered.contains("7950"));
        assert!(!rendered.contains("7943"));
        assert!(!rendered.contains("7952"));
        assert_eq!(s.table.page_size, 15);
        assert_eq!(s.table.current_page, original_page);
        assert_eq!(s.table.cursor, 7950);
        press(&mut s, key(KeyCode::End));
        assert_eq!(s.table.cursor, 255_990);
        press(&mut s, key(KeyCode::PgDown));
        assert_eq!(s.table.cursor, 0);
        press(&mut s, key(KeyCode::PgUp));
        assert_eq!(s.table.cursor, 255_990);
        press(&mut s, key(KeyCode::Home));
        assert_eq!(s.table.cursor, 0);
    }

    #[test]
    fn step_grammar_and_resolution_follow_go() {
        assert_eq!(normalize_step(""), "auto");
        assert_eq!(normalize_step(" AUTO "), "auto");
        assert_eq!(normalize_step("1h30m"), "1h30m");
        assert_eq!(normalize_step("5ms"), "5ms");
        assert_eq!(normalize_step("0s"), "auto");
        assert_eq!(normalize_step("5"), "auto");
        assert_eq!(normalize_step("1m "), "1m");
        assert_eq!(normalize_step("99999999999999999999s"), "auto");
        assert_eq!(resolve_step_duration("auto", 0), "1m");
        assert_eq!(
            resolve_step_duration("auto", 10 * 60 * NANOS_PER_SECOND),
            "15s"
        );
        assert_eq!(resolve_step_duration("auto", HOUR_NANOS), "1m");
        assert_eq!(resolve_step_duration("auto", 3 * 24 * HOUR_NANOS), "1h");
        assert_eq!(
            resolve_step_duration("auto", 10_000 * 24 * HOUR_NANOS),
            "7d"
        );
        assert_eq!(resolve_step_duration("bogus", HOUR_NANOS), "1m");
        assert_eq!(resolve_step_from_strings("auto", "x", "y"), "1m");
        assert_eq!(
            resolve_step_from_strings("auto", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
            "1m"
        );
    }

    #[test]
    fn both_mode_range_window_is_normalized_only_when_not_increasing() {
        assert_eq!(
            normalize_range_window("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
            ("2026-01-01T00:00:00Z".into(), "2026-01-01T01:00:00Z".into())
        );
        assert_eq!(
            normalize_range_window("2026-01-01T02:00:00Z", "2026-01-01T01:00:00Z"),
            ("2026-01-01T00:00:00Z".into(), "2026-01-01T01:00:00Z".into())
        );
    }

    #[test]
    fn go_value_formatting() {
        assert_eq!(go_float(1e6), "1e+06");
        assert_eq!(go_float(123456.0), "123456");
        assert_eq!(go_float(1234567.0), "1.234567e+06");
        assert_eq!(go_float(0.0001), "0.0001");
        assert_eq!(go_float(0.00001), "1e-05");
        assert_eq!(go_float(-1.5), "-1.5");
        assert_eq!(go_float(f64::NAN), "NaN");
        assert_eq!(go_float(f64::NEG_INFINITY), "-Inf");
        assert_eq!(
            format_any(&AnyValue::Array(vec![AnyValue::Null, AnyValue::Bool(true)])),
            "[<nil> true]"
        );
        assert_eq!(format_compact(1_500_000.0), "1.5M");
        assert_eq!(format_compact(2_000_000.0), "2M");
        assert_eq!(format_compact(1000.0), "1k");
        assert_eq!(format_compact(12.0), "12");
        assert_eq!(format_compact(0.25), "0.2");
        assert_eq!(format_compact(-0.04), "-0");
        // Go %.1f prints specials as +Inf/-Inf/NaN, then the suffix applies.
        assert_eq!(format_compact(f64::INFINITY), "+InfM");
        assert_eq!(format_compact(f64::NEG_INFINITY), "-InfM");
        assert_eq!(format_compact(f64::NAN), "NaN");
    }

    #[test]
    fn float_to_int_follows_go_on_this_target() {
        assert_eq!(go_f64_to_i64(1.9), 1);
        assert_eq!(go_f64_to_i64(-1.9), -1);
        assert_eq!(go_f64_to_i64(-9_223_372_036_854_775_808.0), i64::MIN);
        if cfg!(target_arch = "x86_64") {
            // CVTTSD2SQ: the "integer indefinite" value (oracle: linux/amd64).
            assert_eq!(go_f64_to_i64(f64::INFINITY), i64::MIN);
            assert_eq!(go_f64_to_i64(f64::NAN), i64::MIN);
            assert_eq!(go_f64_to_i64(1e30), i64::MIN);
            assert_eq!(go_f64_to_i64(9_223_372_036_854_775_808.0), i64::MIN);
            assert_eq!(round_go(f64::INFINITY), isize::MIN);
        } else {
            assert_eq!(go_f64_to_i64(f64::INFINITY), i64::MAX);
            assert_eq!(go_f64_to_i64(f64::NAN), 0);
        }
    }

    #[test]
    fn numeric_timestamps_format_through_go_time_arithmetic() {
        let utc = |v: f64| format_ts(&AnyValue::Number(v), DisplayMode::Utc);
        assert_eq!(utc(1_704_067_200.0), "2024-01-01T00:00:00Z");
        assert_eq!(utc(1_704_067_200.75), "2024-01-01T00:00:00Z");
        assert_eq!(utc(-1.5), "1969-12-31T23:59:58Z");
        assert_eq!(utc(0.0), "1970-01-01T00:00:00Z");
        // Years past 9999 print without chrono's '+' sign, as Go appendInt.
        assert_eq!(utc(253_402_300_800.0), "10000-01-01T00:00:00Z");
        assert_eq!(
            go_format_unix(-62_135_596_801, 0, true),
            "0000-12-31T23:59:59Z"
        );
        assert_eq!(
            go_format_unix(-62_198_755_200, 0, true),
            "-0001-01-01T00:00:00Z"
        );
        assert_eq!(
            go_format_unix(0, 19_800, false),
            "1970-01-01T05:30:00+05:30"
        );
        assert_eq!(go_format_unix(0, 0, false), "1970-01-01T00:00:00+00:00");
        assert_eq!(
            go_format_unix(0, -18_000, false),
            "1969-12-31T19:00:00-05:00"
        );
        // math.Modf(1e30) → frac 0; int64(1e30) = MinInt64 on amd64; absSec
        // wraps. Actual Go 1.25.4 linux/amd64 screen and Go time.Unix control:
        // 292277026596-12-04T15:30:08Z (TIME column 15:30:08).
        assert_eq!(
            go_format_unix(i64::MIN, 0, true),
            "292277026596-12-04T15:30:08Z"
        );
        if cfg!(target_arch = "x86_64") {
            assert_eq!(utc(1e30), "292277026596-12-04T15:30:08Z");
            assert_eq!(trim_timestamp_to_hms(&utc(1e30)), "15:30:08");
        }
        // time.Unix normalization of an out-of-range nsec.
        assert_eq!(go_unix_seconds(10, -1), 9);
        assert_eq!(go_unix_seconds(10, 2_500_000_000), 12);
        assert_eq!(go_unix_seconds(i64::MIN, -1), i64::MAX);
    }

    #[test]
    fn chart_values_parse_like_strconv() {
        assert_eq!(go_parse_float("1.5"), Some(1.5));
        assert_eq!(go_parse_float("+Inf"), Some(f64::INFINITY));
        assert_eq!(go_parse_float("-infinity"), Some(f64::NEG_INFINITY));
        assert!(go_parse_float("NaN").is_some_and(f64::is_nan));
        assert_eq!(go_parse_float("+NaN"), None);
        assert_eq!(go_parse_float("1_000"), Some(1000.0));
        assert_eq!(go_parse_float("1__0"), None);
        assert_eq!(go_parse_float("_1"), None);
        assert_eq!(go_parse_float("0x1p-2"), Some(0.25));
        assert_eq!(go_parse_float("0x_1p4"), Some(16.0));
        assert_eq!(go_parse_float("0x1"), None);
        assert_eq!(go_parse_float("1e999"), None);
        assert_eq!(go_parse_float(".5"), Some(0.5));
        assert_eq!(go_parse_float("1."), Some(1.0));
        assert_eq!(go_parse_float("abc"), None);
        assert_eq!(go_parse_float(""), None);
    }

    #[test]
    fn hex_floats_round_exactly_like_atof_hex() {
        let bits = |s: &str| go_parse_float(s).map(f64::to_bits);
        // Subnormal rounding: 1.5 × 2^-1075 rounds up to the smallest subnormal,
        // an exact half (2^-1075) ties to even (zero).
        assert_eq!(bits("0x1.8p-1075"), Some(1));
        assert_eq!(bits("0x1p-1074"), Some(1));
        assert_eq!(bits("0x1p-1075"), Some(0));
        assert_eq!(bits("0x1p-1076"), Some(0));
        assert_eq!(bits("-0x1p-1075"), Some(1 << 63));
        assert_eq!(bits("0x0.0000000000001p-1022"), Some(1));
        assert_eq!(bits("0x1p-1022"), Some(f64::MIN_POSITIVE.to_bits()));
        // Largest finite, and overflow (Go: ±Inf with ErrRange → rejected).
        assert_eq!(go_parse_float("0x1.fffffffffffffp1023"), Some(f64::MAX));
        assert_eq!(go_parse_float("0x1.fffffffffffff8p1023"), None);
        assert_eq!(go_parse_float("0x1p1024"), None);
        assert_eq!(go_parse_float("-0x1p1024"), None);
        // Ties to even at 53 bits; a truncated 17th digit is sticky.
        assert_eq!(go_parse_float("0x1.00000000000008p0"), Some(1.0));
        assert_eq!(
            go_parse_float("0x1.00000000000018p0"),
            Some(1.0 + 2.0 * f64::EPSILON)
        );
        assert_eq!(
            go_parse_float("0x1.0000000000000801p0"),
            Some(1.0 + f64::EPSILON)
        );
        // Leading zeros, case, underscores and syntax.
        assert_eq!(go_parse_float("0x000.8p1"), Some(1.0));
        assert_eq!(go_parse_float("0X1P-1"), Some(0.5));
        assert_eq!(go_parse_float("0x1_0p0"), Some(16.0));
        assert_eq!(go_parse_float("0x1p1_0"), Some(1024.0));
        assert_eq!(go_parse_float("0x.p0"), None);
        assert_eq!(go_parse_float("0x1p"), None);
        assert_eq!(go_parse_float("0x1p+"), None);
        assert_eq!(go_parse_float("0x"), None);
        assert_eq!(go_parse_float("0x1.2.3p0"), None);
        assert_eq!(go_parse_float("0x1pz"), None);
        assert_eq!(go_parse_float("0x0p0"), Some(0.0));
        assert_eq!(go_parse_float("0x1p-99999"), Some(0.0));
    }

    #[test]
    fn labels_timestamps_and_builder_expressions() {
        let metric = vec![
            ("job".to_string(), "api".to_string()),
            ("__name__".to_string(), "up".to_string()),
            ("a".to_string(), "x\"y".to_string()),
        ];
        assert_eq!(format_labels(&metric), "up{a=\"x\"y\", job=\"api\"}");
        assert_eq!(format_labels(&metric[..1]), "{job=\"api\"}");
        assert_eq!(
            trim_timestamp_to_hms("2026-01-01T10:11:12+05:30"),
            "10:11:12"
        );
        assert_eq!(trim_timestamp_to_hms("2026-01-01T10:11:12Z"), "10:11:12");
        assert_eq!(trim_timestamp_to_hms("nodate"), "nodate");
        assert_eq!(build_expr("", "a", "b"), "");
        assert_eq!(build_expr("up", ANY, "b"), "up");
        assert_eq!(build_expr("up", "job", ANY), "up{job!=\"\"}");
        assert_eq!(
            build_expr("up", "job", "a\"b\\\n"),
            "up{job=\"a\\\"b\\\\\\n\"}"
        );
        let list: Vec<String> = [ANY, "Job", "instance"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(
            filter_builder_list(&list, "JO"),
            vec![ANY.to_string(), "Job".to_string()]
        );
        assert_eq!(
            filter_datasets(&list[1..], "INST"),
            vec!["instance".to_string()]
        );
    }

    #[test]
    fn init_requests_result_cache_and_datasets_in_go_order() {
        let s = state("up", "metrics", false);
        let effects = s.init();
        assert!(matches!(effects[0], Effect::Tick { tag: 0 }));
        assert!(
            matches!(&effects[1], Effect::FetchResult(r) if r.expr == "up" && r.mode == QueryMode::Range)
        );
        assert_eq!(
            effects[2],
            Effect::FetchCacheMetrics {
                dataset: "metrics".into()
            }
        );
        assert_eq!(effects[3], Effect::FetchDatasets);
        let empty = state("", "metrics", false);
        assert_eq!(empty.init().len(), 3);
    }

    #[test]
    fn dataset_list_selects_first_dataset_for_placeholder_and_runs() {
        let mut s = state("up", "select-dataset", false);
        let effects = s.update(Message::Done(Completion::Datasets(Ok(vec![
            "m1".into(),
            "m2".into(),
        ]))));
        assert_eq!(s.dataset(), "m1");
        assert_eq!(
            effects[1],
            Effect::FetchCacheMetrics {
                dataset: "m1".into()
            }
        );
        assert!(matches!(&effects[2], Effect::FetchResult(r) if r.dataset == "m1"));
        // A later list (e.g. from the picker) no longer replaces a real dataset.
        assert!(
            s.update(Message::Done(Completion::Datasets(Ok(vec!["zz".into()]))))
                .is_empty()
        );
        assert_eq!(s.dataset(), "m1");
    }

    #[test]
    fn results_have_no_stale_guard_and_arrive_in_completion_order() {
        let mut s = state("up", "m", false);
        let first = vec![
            row("2026-01-01T00:00:00Z", "up", "1"),
            row("2026-01-01T00:01:00Z", "up", "2"),
        ];
        let second = vec![row("2026-01-01T00:00:00Z", "down", "3")];
        s.update(Message::Done(vector(second)));
        s.update(Message::Done(matrix(first)));
        // The later-arriving (earlier) run wins, as in Go.
        assert_eq!(s.data_rows[0].metric, "up");
        assert!(s.chart_mode());
        assert_eq!(s.focus(), Pane::Table);
        s.update(Message::Done(Completion::Result(Err(String::new()))));
        assert_eq!(s.fetch_err, "query failed");
        assert_eq!(s.status_error, "query failed");
    }

    #[test]
    fn w_unwraps_only_in_table_view_and_reaches_table_in_chart_view() {
        let mut s = state("up", "m", false);
        s.update(Message::Done(vector(vec![row(
            "2026-01-01T00:00:00Z",
            "up",
            "1",
        )])));
        assert!(!s.chart_mode());
        press(&mut s, rune('w'));
        assert!(s.unwrap_metric());
        let mut c = state("up", "m", false);
        c.update(Message::Done(matrix(vec![
            row("2026-01-01T00:00:00Z", "up", "1"),
            row("2026-01-01T00:01:00Z", "up", "2"),
        ])));
        assert!(c.chart_mode());
        press(&mut c, rune('w'));
        assert!(!c.unwrap_metric());
    }

    #[test]
    fn ctrl_c_quits_only_without_an_overlay() {
        let mut s = state("up", "m", false);
        press(&mut s, key(KeyCode::Ctrl(4)));
        assert_eq!(s.overlay(), Overlay::Dataset);
        assert!(!press(&mut s, key(KeyCode::Ctrl(3))).contains(&Effect::Quit));
        press(&mut s, key(KeyCode::Esc));
        assert_eq!(s.overlay(), Overlay::None);
        assert!(press(&mut s, key(KeyCode::Ctrl(3))).contains(&Effect::Quit));
    }

    #[test]
    fn ctrl_b_toggles_builder_and_ctrl_q_focuses_query() {
        let mut s = state("up", "m", false);
        press(&mut s, key(KeyCode::Tab));
        assert_eq!(s.focus(), Pane::Time);
        press(&mut s, key(KeyCode::Ctrl(17)));
        assert_eq!(s.focus(), Pane::Query);
        press(&mut s, key(KeyCode::Ctrl(2)));
        assert_eq!(s.editor_mode(), EditorMode::Builder);
        // Enter in builder mode opens the overlay; no cache yet → prefetch.
        let effects = press(&mut s, key(KeyCode::Enter));
        assert_eq!(s.overlay(), Overlay::Builder);
        assert_eq!(
            effects,
            vec![Effect::FetchCacheMetrics {
                dataset: "m".into()
            }]
        );
    }

    #[test]
    fn mode_cycles_range_instant_both_on_step_pane_and_reruns() {
        let mut s = state("up", "m", false);
        for _ in 0..3 {
            press(&mut s, key(KeyCode::Tab));
        }
        assert_eq!(s.focus(), Pane::Step);
        let effects = press(&mut s, key(KeyCode::Ctrl(0)));
        assert_eq!(s.mode(), QueryMode::Instant);
        assert!(!s.chart_mode());
        assert!(
            effects
                .iter()
                .any(|e| matches!(e, Effect::FetchResult(r) if r.mode == QueryMode::Instant))
        );
        assert_eq!(
            s.time_range.end.time,
            go_add(go_now(fixed_now()), -HOUR_NANOS)
        );
        press(&mut s, key(KeyCode::Ctrl(0)));
        assert_eq!(s.mode(), QueryMode::Both);
        assert!(s.chart_mode());
        press(&mut s, key(KeyCode::Ctrl(0)));
        assert_eq!(s.mode(), QueryMode::Range);
        assert_eq!(s.time_range.end.time, go_now(fixed_now()));
    }

    #[test]
    fn run_without_dataset_opens_picker_with_error() {
        let mut s = state("up", "", false);
        let effects = press(&mut s, key(KeyCode::Ctrl(18)));
        assert_eq!(effects, vec![Effect::FetchDatasets]);
        assert_eq!(s.overlay(), Overlay::Dataset);
        assert_eq!(s.status_error, "select a dataset first");
    }

    #[test]
    fn alt_enter_on_dataset_pane_opens_picker_before_running() {
        let mut s = state("up", "m", false);
        press(&mut s, key(KeyCode::Tab));
        press(&mut s, key(KeyCode::Tab));
        assert_eq!(s.focus(), Pane::Dataset);
        let mut k = key(KeyCode::Enter);
        k.alt = true;
        assert_eq!(press(&mut s, k), vec![Effect::FetchDatasets]);
        assert_eq!(s.overlay(), Overlay::Dataset);
    }

    #[test]
    fn builder_label_completion_caches_but_respects_metric_guard() {
        let mut s = state("", "m", false);
        s.update(Message::Done(Completion::CacheMetrics {
            dataset: "m".into(),
            result: Ok(vec!["a".into(), "b".into()]),
        }));
        press(&mut s, key(KeyCode::Ctrl(2)));
        press(&mut s, key(KeyCode::Enter));
        assert_eq!(
            s.builder_metrics_filtered,
            vec!["a".to_string(), "b".to_string()]
        );
        let effects = press(&mut s, key(KeyCode::Enter));
        let Some(Effect::FetchLabels { id, metric, .. }) = effects.first().cloned() else {
            panic!("expected label fetch, got {effects:?}");
        };
        assert_eq!(metric, "a");
        // User moves to metric "b" before the completion arrives.
        press(&mut s, key(KeyCode::ShiftTab));
        press(&mut s, key(KeyCode::Down));
        s.update(Message::Done(Completion::Labels {
            id,
            metric: "a".into(),
            result: Ok(vec!["job".into()]),
        }));
        assert_eq!(s.cache_labels.get("a"), Some(&vec!["job".to_string()]));
        assert!(s.builder_labels.is_empty());
        assert!(!s.builder_labels_loading);
    }

    #[test]
    fn reentering_labels_cancels_the_previous_request() {
        let mut s = state("", "m", false);
        s.update(Message::Done(Completion::CacheMetrics {
            dataset: "m".into(),
            result: Ok(vec!["a".into()]),
        }));
        press(&mut s, key(KeyCode::Ctrl(2)));
        press(&mut s, key(KeyCode::Enter));
        let first = press(&mut s, key(KeyCode::Enter));
        let Some(Effect::FetchLabels { id, .. }) = first.first().cloned() else {
            panic!()
        };
        press(&mut s, key(KeyCode::ShiftTab));
        let second = press(&mut s, key(KeyCode::Enter));
        assert_eq!(second[0], Effect::Cancel { id });
        assert!(matches!(second[1], Effect::FetchLabels { .. }));
    }

    #[test]
    fn resize_resets_value_column_to_title_width() {
        let mut s = state("up", "m", false);
        s.update(Message::Done(Completion::Result(Ok(ResultData {
            result_type: "vector".into(),
            chart_result: String::new(),
            rows: vec![row("2026-01-01T00:00:00Z", "up", "1")],
            chart_rows: Vec::new(),
            series_count: 1,
            value_width: 20,
        }))));
        assert_eq!(s.table.value_width, 20);
        s.update(Message::Resize {
            width: 100,
            height: 30,
        });
        assert_eq!(s.table.value_width, 5);
    }

    #[test]
    fn ctrl_v_starts_a_clipboard_read_that_is_never_inserted() {
        let mut s = state("up", "m", false);
        let k = Key {
            code: KeyCode::Ctrl(22),
            runes: Vec::new(),
            alt: false,
            paste: false,
        };
        let effects = press(&mut s, k);
        assert!(effects.contains(&Effect::ReadClipboard));
        assert!(
            s.update(Message::Done(Completion::Paste(Ok("x".into()))))
                .is_empty()
        );
        assert_eq!(s.query_text(), "up");
    }

    #[test]
    fn spinner_ticks_only_while_loading_and_drop_stale_tags() {
        let mut s = state("up", "m", false);
        let next = s.update(Message::Done(Completion::Tick { tag: 0 }));
        assert_eq!(next, vec![Effect::TickAfter { tag: 1 }]);
        assert!(
            s.update(Message::Done(Completion::Tick { tag: 0 }))
                .is_empty()
        );
        s.loading = false;
        assert!(
            s.update(Message::Done(Completion::Tick { tag: 1 }))
                .is_empty()
        );
    }

    #[test]
    fn chart_cursor_first_press_selects_neighbor_then_moves_by_column() {
        let mut s = state("up", "m", false);
        s.update(Message::Done(matrix(vec![
            row("2026-01-01T00:00:00Z", "up", "1"),
            row("2026-01-01T00:01:00Z", "up", "2"),
            row("2026-01-01T00:02:00Z", "up", "3"),
        ])));
        press(&mut s, key(KeyCode::Right));
        assert!(s.chart_hover);
        assert_eq!(s.chart_cursor, 1);
        press(&mut s, key(KeyCode::Right));
        assert_eq!(s.chart_cursor, 2);
        press(&mut s, key(KeyCode::Right));
        assert_eq!(s.chart_cursor, 2);
        press(&mut s, rune('['));
        assert_eq!(s.chart_cursor, 1);
    }

    /// G10: two metrics with one label set merge into a 14-point series;
    /// sort.Slice (pdqsort) orders equal times as Go does, not stably.
    /// Actual Go oracle (docs/evidence/phase7/promql-sort-oracle-main.go.txt):
    /// b0 a0 a1 b1 a2 b2 a3 b3 a4 b4 a5 b5 a6 b6; 12 points stay stable.
    #[test]
    fn equal_time_points_follow_go_pdqsort_order() {
        let order = |n: usize| {
            let mut items: Vec<(usize, char, usize)> = (0..n)
                .map(|i| (i / 2, if i % 2 == 0 { 'a' } else { 'b' }, i / 2))
                .collect();
            // Input as extractChartData appends: all of a, then all of b.
            items.sort_by_key(|(_, name, _)| *name);
            go_sort::slice(&mut items, |x, y| x.0 < y.0);
            items
                .iter()
                .map(|(_, name, i)| format!("{name}{i}"))
                .collect::<Vec<_>>()
                .join(" ")
        };
        assert_eq!(order(14), "b0 a0 a1 b1 a2 b2 a3 b3 a4 b4 a5 b5 a6 b6");
        assert_eq!(order(12), "a0 b0 a1 b1 a2 b2 a3 b3 a4 b4 a5 b5");

        let mut s = state("up", "m", false);
        let mut rows = Vec::new();
        for (name, value) in [("a", "1"), ("b", "100")] {
            for i in 0..7 {
                rows.push(row(
                    &format!("2024-01-01T00:0{i}:00Z"),
                    &format!("{name}{{job=\"x\"}}"),
                    value,
                ));
            }
        }
        s.update(Message::Done(matrix(rows)));
        let series = s.chart_series();
        assert_eq!(series.len(), 1);
        press(&mut s, key(KeyCode::Right));
        assert_eq!(s.chart_cursor, 1);
        // Go inspects a0 (value 1) on the first Right; a stable sort gave b0.
        assert_eq!(series[0].points[1].value, 1.0);
        assert_eq!(series[0].points[0].value, 100.0);
    }

    #[test]
    fn go_sort_ports_sort_large_inputs_and_keep_slice_stable_stable() {
        // Pseudo-random keys exercise partition, breakPatterns and heapsort
        // fallback paths; the result must be sorted either way.
        let mut x: u64 = 0x9E37_79B9_7F4A_7C15;
        let mut keys: Vec<u32> = (0..2000)
            .map(|_| {
                x ^= x << 13;
                x ^= x >> 7;
                x ^= x << 17;
                (x % 97) as u32
            })
            .collect();
        let mut expected = keys.clone();
        expected.sort_unstable();
        go_sort::slice(&mut keys, |a, b| a < b);
        assert_eq!(keys, expected);
        let mut descending: Vec<u32> = (0..500).rev().collect();
        go_sort::slice(&mut descending, |a, b| a < b);
        assert_eq!(descending, (0..500).collect::<Vec<_>>());
        let mut pairs: Vec<(u32, usize)> = (0..300).map(|i| ((i * 7 % 5) as u32, i)).collect();
        go_sort::slice_stable(&mut pairs, |a, b| a.0 < b.0);
        assert!(
            pairs
                .windows(2)
                .all(|w| w[0].0 < w[1].0 || (w[0].0 == w[1].0 && w[0].1 < w[1].1))
        );
        // A NaN key never panics (Less is false both ways, as in Go).
        let mut floats = vec![3.0, f64::NAN, 1.0, 2.0, f64::NAN, 0.5];
        go_sort::slice_stable(&mut floats, |a, b| a > b);
        go_sort::slice(&mut floats, |a, b| a > b);
        assert_eq!(floats.len(), 6);
    }

    type Stamped = (std::time::Instant, Event);

    /// Scripted terminal queue. `produced` counts accepted (taken) events,
    /// which is what the real terminal treats as received. When `endless`, a
    /// fresh event stamped now is always queued behind the scripted ones.
    struct FakeSource {
        queue: std::collections::VecDeque<Stamped>,
        endless: bool,
        produced: usize,
    }

    impl FakeSource {
        fn new(events: Vec<Stamped>) -> Self {
            Self {
                queue: events.into(),
                endless: false,
                produced: 0,
            }
        }
        fn endless() -> Self {
            Self {
                queue: Default::default(),
                endless: true,
                produced: 0,
            }
        }
        fn refill(&mut self) {
            if self.endless && self.queue.is_empty() {
                self.queue
                    .push_back((std::time::Instant::now(), Event::Key(rune('x'))));
            }
        }
    }

    impl EventSource for FakeSource {
        fn peek(&self) -> Peek {
            match self.queue.front() {
                Some((at, _)) => Peek::Ready(*at),
                None if self.endless => Peek::Ready(std::time::Instant::now()),
                None => Peek::Empty,
            }
        }
        fn ready(&self) -> impl Future<Output = Peek> {
            let peek = self.peek();
            async move {
                match peek {
                    Peek::Empty => std::future::pending().await,
                    other => other,
                }
            }
        }
        fn take(&mut self) -> Option<Stamped> {
            self.refill();
            let item = self.queue.pop_front();
            if item.is_some() {
                self.produced += 1;
            }
            item
        }
    }

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }

    fn label(input: &Input) -> String {
        match input {
            Input::Event(Some(Event::Key(k))) if !k.runes.is_empty() => k.runes.iter().collect(),
            Input::Event(Some(Event::Key(_))) => "key".into(),
            Input::Done(Completion::Tick { tag }) => format!("tick{tag}"),
            Input::Done(_) => "done".into(),
            Input::Event(_) => "event".into(),
        }
    }

    /// Feed scripted inputs through the real runner step into the reducer.
    fn drive(
        s: &mut PromqlState,
        events: Vec<Stamped>,
        done: Vec<(std::time::Instant, Completion)>,
    ) -> Vec<Effect> {
        runtime().block_on(async {
            let mut source = FakeSource::new(events);
            let (sender, mut receiver) = tokio::sync::mpsc::channel(64);
            let count = source.queue.len() + done.len();
            for item in done {
                sender.send(item).await.unwrap();
            }
            let mut inbox = Inbox::default();
            let mut effects = Vec::new();
            for _ in 0..count {
                let message = match next_input(&mut inbox, &mut source, &mut receiver).await {
                    Input::Event(Some(Event::Key(k))) => Message::Key(k),
                    Input::Done(c) => Message::Done(c),
                    other => panic!("unexpected input {}", label(&other)),
                };
                effects.extend(s.update(message));
            }
            effects
        })
    }

    #[test]
    fn heads_are_merged_in_arrival_order() {
        let t0 = std::time::Instant::now();
        let at = |ms| t0 + std::time::Duration::from_millis(ms);
        runtime().block_on(async {
            let mut source = FakeSource::new(vec![
                (at(2), Event::Key(rune('a'))),
                (at(5), Event::Key(rune('b'))),
            ]);
            let (sender, mut receiver) = tokio::sync::mpsc::channel(4);
            sender
                .send((at(1), Completion::Tick { tag: 0 }))
                .await
                .unwrap();
            sender
                .send((at(3), Completion::Tick { tag: 1 }))
                .await
                .unwrap();
            let mut inbox = Inbox::default();
            let mut order = Vec::new();
            for _ in 0..4 {
                let input = next_input(&mut inbox, &mut source, &mut receiver).await;
                order.push(label(&input));
            }
            assert_eq!(order, ["tick0", "a", "tick1", "b"]);
        });
    }

    #[test]
    fn quit_is_not_accepted_while_an_earlier_completion_wins() {
        let t0 = std::time::Instant::now();
        let at = |ms| t0 + std::time::Duration::from_millis(ms);
        runtime().block_on(async {
            let mut source = FakeSource::new(vec![(at(2), Event::Quit)]);
            let (sender, mut receiver) = tokio::sync::mpsc::channel(4);
            sender
                .send((at(1), Completion::Tick { tag: 0 }))
                .await
                .unwrap();
            let mut inbox = Inbox::default();
            let first = next_input(&mut inbox, &mut source, &mut receiver).await;
            assert_eq!(label(&first), "tick0");
            // The terminal still owns the Quit: it was only peeked.
            assert_eq!(source.produced, 0);
            assert_eq!(source.queue.len(), 1);
            let second = next_input(&mut inbox, &mut source, &mut receiver).await;
            assert!(matches!(second, Input::Event(Some(Event::Quit))));
            assert_eq!(source.produced, 1);
        });
    }

    #[test]
    fn control_key_stays_queued_behind_earlier_completions() {
        let t0 = std::time::Instant::now();
        let at = |ms| t0 + std::time::Duration::from_millis(ms);
        runtime().block_on(async {
            let mut source = FakeSource::new(vec![(at(3), Event::Key(key(KeyCode::Ctrl(3))))]);
            let (sender, mut receiver) = tokio::sync::mpsc::channel(4);
            sender
                .send((at(1), Completion::Tick { tag: 0 }))
                .await
                .unwrap();
            sender
                .send((at(2), Completion::Tick { tag: 1 }))
                .await
                .unwrap();
            let mut inbox = Inbox::default();
            for expected in ["tick0", "tick1"] {
                let input = next_input(&mut inbox, &mut source, &mut receiver).await;
                assert_eq!(label(&input), expected);
                assert_eq!(source.produced, 0, "ctrl+c accepted before it won");
            }
            let input = next_input(&mut inbox, &mut source, &mut receiver).await;
            assert!(
                matches!(input, Input::Event(Some(Event::Key(k))) if k.code == KeyCode::Ctrl(3))
            );
        });
    }

    #[test]
    fn continuous_input_keeps_one_head_and_cannot_starve_completions() {
        runtime().block_on(async {
            let mut source = FakeSource::endless();
            let (sender, mut receiver) = tokio::sync::mpsc::channel(1);
            let mut inbox = Inbox::default();
            let mut processed_events = 0;
            for step in 0..50 {
                if step == 10 {
                    // A completion handed over while input keeps arriving.
                    Outbox(sender.clone())
                        .send(Completion::Tick { tag: 7 })
                        .await
                        .unwrap();
                }
                match next_input(&mut inbox, &mut source, &mut receiver).await {
                    Input::Done(Completion::Tick { tag: 7 }) => {
                        assert!(step <= 11, "completion starved until step {step}");
                    }
                    Input::Event(Some(_)) => processed_events += 1,
                    other => panic!("unexpected {}", label(&other)),
                }
                // Nothing is accepted from the terminal ahead of the reducer.
                assert_eq!(source.produced, processed_events, "terminal read ahead");
            }
            assert_eq!(processed_events, 49);
        });
    }

    #[test]
    fn completion_channel_keeps_backpressure_until_its_head_is_consumed() {
        runtime().block_on(async {
            // An earlier key keeps the completion head waiting.
            let mut source =
                FakeSource::new(vec![(std::time::Instant::now(), Event::Key(rune('k')))]);
            let (sender, mut receiver) = tokio::sync::mpsc::channel(1);
            let outbox = Outbox(sender);
            let mut inbox = Inbox::default();
            outbox.send(Completion::Tick { tag: 1 }).await.unwrap();
            // The key wins; tag 1 moved into the completion head meanwhile.
            assert_eq!(
                label(&next_input(&mut inbox, &mut source, &mut receiver).await),
                "k"
            );
            outbox.send(Completion::Tick { tag: 2 }).await.unwrap(); // fills capacity 1
            let try_send = |tag| {
                let outbox = outbox.clone();
                async move {
                    tokio::select! {
                        biased;
                        _ = outbox.send(Completion::Tick { tag }) => true,
                        () = std::future::ready(()) => false,
                    }
                }
            };
            // One head plus a full channel: a third completion is held back.
            assert!(inbox.done.is_some());
            assert!(
                !try_send(3).await,
                "completion buffered beyond channel capacity"
            );
            assert_eq!(
                label(&next_input(&mut inbox, &mut source, &mut receiver).await),
                "tick1"
            );
            // Consuming tag 1 lets the next step move tag 2 into the head...
            assert_eq!(
                label(&next_input(&mut inbox, &mut source, &mut receiver).await),
                "tick2"
            );
            // ...which released the channel slot.
            assert!(
                try_send(3).await,
                "capacity was not released after the head was consumed"
            );
        });
    }

    #[test]
    fn yielding_lets_request_tasks_run_under_continuous_input() {
        runtime().block_on(async {
            let ran = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let flag = std::sync::Arc::clone(&ran);
            tokio::spawn(async move { flag.store(true, std::sync::atomic::Ordering::SeqCst) });
            let mut source = FakeSource {
                queue: Default::default(),
                endless: true,
                produced: 0,
            };
            let (_sender, mut receiver) =
                tokio::sync::mpsc::channel::<(std::time::Instant, Completion)>(1);
            let mut inbox = Inbox::default();
            for _ in 0..3 {
                next_input(&mut inbox, &mut source, &mut receiver).await;
            }
            assert!(
                ran.load(std::sync::atomic::Ordering::SeqCst),
                "network task starved by input"
            );
        });
    }

    #[test]
    fn dataset_typed_before_late_list_still_selects_the_filtered_dataset() {
        let datasets = || Completion::Datasets(Ok(vec!["alpha".into(), "beta".into()]));
        let mut s = state("", "alpha", false);
        s.update(Message::Done(datasets()));
        let t0 = std::time::Instant::now();
        let at = |ms| t0 + std::time::Duration::from_millis(ms);
        let beta = Key {
            code: KeyCode::Runes,
            runes: "beta".chars().collect(),
            alt: false,
            paste: false,
        };
        let events = vec![
            (at(1), Event::Key(key(KeyCode::Ctrl(4)))),
            (at(2), Event::Key(beta)),
            (at(3), Event::Key(key(KeyCode::Enter))),
        ];
        // The picker's dataset list arrives after the keys.
        let effects = drive(&mut s, events, vec![(at(4), datasets())]);
        assert_eq!(s.dataset(), "beta");
        assert!(
            effects.contains(&Effect::FetchCacheMetrics {
                dataset: "beta".into()
            }),
            "{effects:?}"
        );
    }

    #[test]
    fn result_arriving_before_focus_keys_is_applied_before_them() {
        let mut s = state("up", "m", false);
        let t0 = std::time::Instant::now();
        let at = |ms| t0 + std::time::Duration::from_millis(ms);
        let mut events = vec![(at(2), Event::Key(key(KeyCode::Ctrl(17))))];
        events.extend((0..3).map(|i| (at(3 + i), Event::Key(key(KeyCode::Tab)))));
        let done = vec![(at(1), vector(vec![row("2026-01-01T00:00:00Z", "up", "1")]))];
        drive(&mut s, events, done);
        assert_eq!(
            s.focus(),
            Pane::Step,
            "the result's table auto-focus must not land after Tab"
        );
    }

    #[test]
    fn wide_legend_label_reaches_go_truncate_panic() {
        // 20 wide runes (width 40) at width 88: labelWidth 39, so Go slices
        // runes[:38] of a 32-rune stack buffer and panics.
        let series = ChartSeries {
            label: "测".repeat(20),
            color: "#FF3B30".into(),
            points: Vec::new(),
        };
        let panic = chart_legend(&[series], 88, 2).unwrap_err();
        assert_eq!(
            panic.0,
            "runtime error: slice bounds out of range [:38] with capacity 32"
        );
    }

    #[test]
    fn render_and_editor_faults_map_to_go_outcomes() {
        let s = state("up", "m", false);
        assert_eq!(s.take_fault(), None);
        *s.render_fault.borrow_mut() = Some(GoPanic("boom".into()));
        assert_eq!(s.take_fault(), Some(Fault::Panic("boom".into())));
        assert_eq!(s.take_fault(), None, "a fault is reported once");
        s.view_fault
            .set(Some(super::super::editor::EditorFault::Hang));
        assert_eq!(s.take_fault(), Some(Fault::Hang));
    }

    #[test]
    fn y_axis_scale_matches_go_nice_steps() {
        let points = |values: &[f64]| -> Vec<TimePoint> {
            values
                .iter()
                .map(|v| TimePoint {
                    time: fixed_now(),
                    value: *v,
                })
                .collect()
        };
        // [1,3], height 20: graph 18, row step 3, padded [0.9, 3.1],
        // step nice(2.2/6) = 0.5, min floor(0.9/0.5)*0.5 = 0.5, max 0.5+0.5*18/3.
        assert_eq!(y_axis_scale(&points(&[1.0, 3.0]), 20), (0.5, 3.5, 3));
        // [0,10], height 30: graph 28, row step 2, padded [-0.5, 10.5],
        // step nice(11/14) = 1, floor(-0.5) = -1 clamped to 0 (min ≥ 0), max 14.
        assert_eq!(y_axis_scale(&points(&[0.0, 10.0]), 30), (0.0, 14.0, 2));
        // Flat series widen by 5% of |value|: [5,5] → [4.875, 5.125], height 10:
        // graph 8, row step 4, step nice(0.275/2) = 0.2, min 24*0.2, max min+0.4.
        let (min, max, step) = y_axis_scale(&points(&[5.0, 5.0]), 10);
        assert_eq!(step, 4);
        assert_eq!(min, (4.8625f64 / 0.2).floor() * 0.2);
        assert_eq!(max, min + 0.2 * 8.0 / 4.0);
    }

    #[test]
    fn line_drawing_combines_with_axes_like_ntcharts() {
        let mut canvas = Canvas::new(4, 3);
        canvas.set(Point { x: 1, y: 2 }, '─', None);
        let color = CellColor::Hex("#FF3B30".into());
        draw_line_points(
            &mut canvas,
            &line_points(Point { x: 0, y: 2 }, Point { x: 2, y: 2 }),
            &color,
        );
        assert_eq!(canvas.cell(Point { x: 0, y: 2 }), '╶');
        assert_eq!(canvas.cell(Point { x: 1, y: 2 }), '─');
        assert_eq!(canvas.cell(Point { x: 2, y: 2 }), '╴');
        let up = line_points(Point { x: 0, y: 2 }, Point { x: 1, y: 0 });
        assert_eq!(
            up,
            vec![
                Point { x: 1, y: 0 },
                Point { x: 1, y: 1 },
                Point { x: 0, y: 2 }
            ]
        );
    }

    #[test]
    fn time_picker_presets_and_segment_edits() {
        use chrono::Datelike;
        let now = fixed_now();
        let go = go_now(now);
        let mut t = TimeInput::new(go_add(go, -HOUR_NANOS), go);
        t.sync_preset(now);
        assert_eq!(t.preset, 1);
        t.update(&key(KeyCode::Down), now);
        assert_eq!(t.start.time, go_add(go, -5 * HOUR_NANOS));
        t.update(&key(KeyCode::Tab), now);
        assert_eq!(t.current_focus(), TimeFocus::Start);
        assert_eq!(t.start.pos, 0);
        // Year +1 would move start past end: the edit is reverted.
        t.update(&key(KeyCode::Up), now);
        assert_eq!(t.start.time, go_add(go, -5 * HOUR_NANOS));
        // Year -1 is accepted.
        t.update(&key(KeyCode::Down), now);
        assert_eq!(
            t.start.time.naive_local().year(),
            go_add(go, -5 * HOUR_NANOS).naive_local().year() - 1
        );
        // End in the future is rejected.
        t.update(&key(KeyCode::Tab), now);
        assert_eq!(t.current_focus(), TimeFocus::End);
        t.update(&key(KeyCode::Up), now);
        assert_eq!(t.end.time, go);
        let go = go_now(now);
        let mut i = TimeInput::new(go, go);
        i.set_instant(true);
        assert_eq!(i.preset, 1);
        i.update(&key(KeyCode::Tab), now);
        assert_eq!(i.current_focus(), TimeFocus::End, "instant skips start");
    }

    #[test]
    fn month_arithmetic_uses_go_normalization() {
        use chrono::Datelike;
        let wall = |y, m, d| {
            NaiveDate::from_ymd_opt(y, m, d)
                .unwrap()
                .and_hms_opt(12, 0, 0)
                .unwrap()
        };
        let mut field = DateTimeField::new(GoTime::from_naive(wall(2024, 1, 31), Zone::Utc));
        // Month segment: addMonthsClamped (Jan 31 + 1 → Feb 29).
        field.set_cursor(SEGMENT_STARTS[1]);
        field.adjust_segment(1);
        assert_eq!(field.time.naive_local().date(), wall(2024, 2, 29).date());
        field.adjust_segment(-2);
        assert_eq!(field.time.naive_local().month(), 12);
        // Year segment: AddDate normalizes Feb 29 + 1y to Mar 1.
        let mut leap = DateTimeField::new(GoTime::from_naive(wall(2024, 2, 29), Zone::Utc));
        leap.set_cursor(SEGMENT_STARTS[0]);
        leap.adjust_segment(1);
        assert_eq!(leap.time.naive_local().date(), wall(2025, 3, 1).date());
        assert_eq!(leap.time.zone, Zone::Utc, "AddDate keeps the location");
    }

    /// G4: `--from …Z` stays in UTC (not converted to Local) in the sidebar,
    /// the picker and its segment edits; a digit edit reparses in Local.
    #[test]
    fn cli_time_location_is_kept_through_the_picker() {
        let start = parse_cli_time("2024-01-01T00:00:00Z").unwrap();
        let end = parse_cli_time("2024-01-01T00:10:00Z").unwrap();
        assert_eq!(start.zone, Zone::Utc);
        assert_eq!(
            format_control_time(end, DisplayMode::Local),
            "01 Jan 2024, 00:10 | UTC+00:00"
        );
        assert_eq!(
            format_control_time(end, DisplayMode::Utc),
            "01 Jan 2024, 00:10 | UTC"
        );
        let mut t = TimeInput::new(start, end);
        assert_eq!(t.start.local_value(), "2024-Jan-01 00:00:00");
        assert_eq!(t.end.local_value(), "2024-Jan-01 00:10:00");
        assert_eq!(t.start.value_utc(), "2024-01-01T00:00:00Z");
        let rows = time_display_mode(DisplayMode::Local, 60, false, t.end.time);
        assert!(rows[1].contains(" Local UTC+00:00 "), "{:?}", rows[1]);
        // Hour -1 on start keeps UTC.
        t.focus = 1;
        t.focus_selected();
        t.start.set_cursor(SEGMENT_STARTS[3]);
        t.update(&key(KeyCode::Down), fixed_now());
        assert_eq!(t.start.time.zone, Zone::Utc);
        assert_eq!(t.start.local_value(), "2023-Dec-31 23:00:00");
        // A digit edit reparses in Local (time.ParseInLocation).
        t.start.set_cursor(0);
        t.start.update(&rune('2'));
        assert_eq!(t.start.time.zone, Zone::Local);
        assert_eq!(t.start.local_value(), "2023-Dec-31 23:00:00");
        // A fixed offset other than Local's is kept as a fixed zone.
        let fixed = parse_cli_time("2024-01-01T00:00:00+05:30").unwrap();
        let field = DateTimeField::new(fixed);
        assert_eq!(field.local_value(), "2024-Jan-01 00:00:00");
        assert_eq!(field.value_utc(), "2023-12-31T18:30:00Z");
        assert_eq!(
            format_control_time(fixed, DisplayMode::Local),
            "01 Jan 2024, 00:00 | UTC+05:30"
        );
    }

    #[test]
    fn view_renders_without_panicking_in_each_overlay() {
        let mut s = state("up", "m", false);
        assert!(String::from_utf8_lossy(&s.view()).contains("RESULTS"));
        press(&mut s, key(KeyCode::Ctrl(4)));
        assert!(String::from_utf8_lossy(&s.view()).contains("SELECT DATASET"));
        press(&mut s, key(KeyCode::Esc));
        s.update(Message::Done(matrix(vec![
            row("2026-01-01T00:00:00Z", "up{job=\"a\"}", "1"),
            row("2026-01-01T00:01:00Z", "up{job=\"a\"}", "2"),
        ])));
        let chart = s.view();
        assert!(String::from_utf8_lossy(&chart).contains("RESULTS | Chart View"));
        press(&mut s, rune('t'));
        assert!(String::from_utf8_lossy(&s.view()).contains("RESULTS | Table View"));
        let empty = state("up", "m", false);
        let mut zero = empty.clone();
        zero.width = 0;
        assert!(zero.view().is_empty());
    }
}
