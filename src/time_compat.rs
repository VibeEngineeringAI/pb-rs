//! CLI time and PromQL step grammars, matching the Go helpers.
use chrono::{DateTime, Datelike, Duration, Utc};

pub fn parse_time(value: &str) -> Result<DateTime<Utc>, String> {
    parse_time_with(value, Utc::now)
}

pub fn parse_time_with(
    value: &str,
    mut clock: impl FnMut() -> DateTime<Utc>,
) -> Result<DateTime<Utc>, String> {
    if value == "now" {
        return Ok(clock());
    }
    if let Some(time) = parse_rfc3339(value) {
        return Ok(time);
    }
    if let Some(days) = value.strip_suffix('d').and_then(|v| v.parse::<i64>().ok()) {
        let nanos = days.wrapping_mul(24 * 60 * 60 * 1_000_000_000);
        return Ok(clock() + Duration::nanoseconds(nanos.wrapping_neg()));
    }
    if let Some(nanos) = duration_nanos(value) {
        return Ok(clock() + Duration::nanoseconds(nanos.wrapping_neg()));
    }
    Err(format!(
        "unrecognized time format {} (use: now, 10m, 2h, 1d, or RFC3339)",
        crate::parser::quote(value)
    ))
}

// time.Parse's fallback accepts one-digit hours, comma fractions and offsets
// with hour 24/minute 60. Keep those quirks without accepting leap seconds.
fn parse_rfc3339(value: &str) -> Option<DateTime<Utc>> {
    use chrono::{NaiveDate, NaiveTime};
    let (date, rest) = value.split_once('T')?;
    if !date.is_ascii() || !rest.is_ascii() {
        return None;
    }
    if date.len() != 10 || &date[4..5] != "-" || &date[7..8] != "-" {
        return None;
    }
    if !date[..4]
        .bytes()
        .chain(date[5..7].bytes())
        .chain(date[8..].bytes())
        .all(|b| b.is_ascii_digit())
    {
        return None;
    }
    let date = NaiveDate::from_ymd_opt(
        date[..4].parse().ok()?,
        date[5..7].parse().ok()?,
        date[8..].parse().ok()?,
    )?;
    let (time, offset) = if let Some(time) = rest.strip_suffix('Z') {
        (time, 0i64)
    } else {
        let i = rest.find(['+', '-'])?;
        let zone = &rest[i + 1..];
        if zone.len() != 5 || &zone[2..3] != ":" {
            return None;
        }
        if !zone[..2]
            .bytes()
            .chain(zone[3..].bytes())
            .all(|b| b.is_ascii_digit())
        {
            return None;
        }
        let hours = zone[..2].parse::<i64>().ok()?;
        let minutes = zone[3..].parse::<i64>().ok()?;
        if hours > 24 || minutes > 60 {
            return None;
        }
        (
            &rest[..i],
            (hours * 3600 + minutes * 60) * if rest.as_bytes()[i] == b'-' { -1 } else { 1 },
        )
    };
    let mut fields = time.split(':');
    let hour = fields.next()?;
    let minute = fields.next()?;
    let seconds = fields.next()?;
    if fields.next().is_some() || !(1..=2).contains(&hour.len()) || minute.len() != 2 {
        return None;
    }
    let (second, fraction) = seconds
        .split_once(['.', ','])
        .map_or((seconds, ""), |(s, f)| (s, f));
    if second.len() != 2
        || (seconds.len() > 2 && fraction.is_empty())
        || !fraction.bytes().all(|v| v.is_ascii_digit())
    {
        return None;
    }
    if !hour
        .bytes()
        .chain(minute.bytes())
        .chain(second.bytes())
        .all(|v| v.is_ascii_digit())
    {
        return None;
    }
    let fraction = &fraction[..fraction.len().min(9)];
    let nanos = if fraction.is_empty() {
        0
    } else {
        fraction.parse::<u32>().ok()? * 10u32.pow(9 - fraction.len() as u32)
    };
    let time = NaiveTime::from_hms_nano_opt(
        hour.parse().ok()?,
        minute.parse().ok()?,
        second.parse().ok()?,
        nanos,
    )?;
    Some(date.and_time(time).and_utc() - Duration::seconds(offset))
}

pub fn format_time(time: DateTime<Utc>) -> String {
    // UTC conversion may cross either end of the input's four-digit year.
    // Go pads the magnitude to four digits but never adds a positive sign.
    let year = time.year();
    let year = if year < 0 {
        format!("-{:04}", -year)
    } else {
        format!("{year:04}")
    };
    format!("{year}{}", time.format("-%m-%dT%H:%M:%SZ"))
}

fn duration_nanos(value: &str) -> Option<i64> {
    let (negative, mut value) = match value.as_bytes().first() {
        Some(b'-') => (true, &value[1..]),
        Some(b'+') => (false, &value[1..]),
        _ => (false, value),
    };
    if value == "0" {
        return Some(0);
    }
    let mut total: u128 = 0;
    if value.is_empty() {
        return None;
    }
    while !value.is_empty() {
        let size = value
            .bytes()
            .take_while(|b| b.is_ascii_digit() || *b == b'.')
            .count();
        if size == 0 {
            return None;
        }
        let number = &value[..size];
        value = &value[size..];
        let (unit, multiplier) = [
            ("ns", 1u128),
            ("us", 1000),
            ("µs", 1000),
            ("μs", 1000),
            ("ms", 1_000_000),
            ("s", 1_000_000_000),
            ("m", 60_000_000_000),
            ("h", 3_600_000_000_000),
        ]
        .into_iter()
        .find(|(u, _)| value.starts_with(u))?;
        value = &value[unit.len()..];
        let mut pieces = number.split('.');
        let integer = pieces.next()?;
        let fraction = pieces.next();
        if pieces.next().is_some() || (integer.is_empty() && fraction.is_none_or(str::is_empty)) {
            return None;
        }
        let whole = if integer.is_empty() {
            0
        } else {
            integer.parse::<u128>().ok()?
        };
        total = total.checked_add(whole.checked_mul(multiplier)?)?;
        if let Some(fraction) = fraction {
            // Match Go leadingFraction's bounded accumulator and floating-point
            // scaling; exact decimal arithmetic differs at nanosecond boundaries.
            let (mut digits, mut scale) = (0u64, 1.0f64);
            for digit in fraction.bytes() {
                if digits > i64::MAX as u64 / 10 {
                    break;
                }
                let next = digits * 10 + u64::from(digit - b'0');
                if next > 1u64 << 63 {
                    break;
                }
                digits = next;
                scale *= 10.0;
            }
            total = total.checked_add((digits as f64 * (multiplier as f64 / scale)) as u128)?;
        }
        if total > (i64::MAX as u128 + u128::from(negative)) {
            return None;
        }
    }
    Some(if negative {
        (total as i64).wrapping_neg()
    } else {
        total as i64
    })
}

pub fn resolve_step(step: &str, start: DateTime<Utc>, end: DateTime<Utc>) -> String {
    let trimmed = step.trim();
    if valid_step(trimmed) {
        return trimmed.into();
    }
    let delta = end - start;
    let nanos = delta
        .num_nanoseconds()
        .unwrap_or(if delta < Duration::zero() {
            i64::MIN
        } else {
            i64::MAX
        });
    if nanos <= 0 {
        return "1m".into();
    }
    for (seconds, label) in [
        (15, "15s"),
        (30, "30s"),
        (60, "1m"),
        (120, "2m"),
        (300, "5m"),
        (600, "10m"),
        (900, "15m"),
        (1800, "30m"),
        (3600, "1h"),
        (7200, "2h"),
        (14400, "4h"),
        (21600, "6h"),
        (43200, "12h"),
        (86400, "1d"),
        (172800, "2d"),
        (604800, "7d"),
    ] {
        if nanos / (seconds * 1_000_000_000i64) <= 100 {
            return label.into();
        }
    }
    "7d".into()
}
fn valid_step(mut value: &str) -> bool {
    if value.is_empty() {
        return false;
    }
    while !value.is_empty() {
        let size = value.bytes().take_while(u8::is_ascii_digit).count();
        if size == 0 || !value[..size].parse::<i64>().is_ok_and(|v| v > 0) {
            return false;
        }
        value = &value[size..];
        if let Some(unit) = ["ms", "s", "m", "h", "d", "w", "y"]
            .into_iter()
            .find(|u| value.starts_with(u))
        {
            value = &value[unit.len()..];
        } else {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    fn now() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-10-13T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc)
    }
    #[test]
    fn relative_grammar_and_clock() {
        for (text, seconds) in [
            ("1d", 86400),
            ("-2h", -7200),
            ("1h2m3.5s", 3723),
            (".5s", 0),
            ("0", 0),
        ] {
            let parsed = parse_time_with(text, now).unwrap();
            assert_eq!((now() - parsed).num_seconds(), seconds, "{text}");
        }
        for text in [" now", "1.5d", "1w", "", "1e3s", "9223372036854775808ns"] {
            assert!(parse_time_with(text, now).is_err(), "{text}");
        }
        assert_eq!(
            format_time(parse_time_with("2024-01-01T02:00:00.123+02:00", now).unwrap()),
            "2024-01-01T00:00:00Z"
        );
    }
    #[test]
    fn minimum_duration_wraps_negation_before_clock_addition() {
        let parsed = parse_time_with("-9223372036854775808ns", now).unwrap();
        assert_eq!((parsed - now()).num_nanoseconds(), Some(i64::MIN));
    }

    #[test]
    fn day_duration_at_minimum_wraps_negation_before_clock_addition() {
        let parsed = parse_time_with("140737488355328d", now).unwrap();
        assert_eq!((parsed - now()).num_nanoseconds(), Some(i64::MIN));
    }

    #[test]
    fn adjacent_negative_duration_moves_clock_forward() {
        let parsed = parse_time_with("-9223372036854775807ns", now).unwrap();
        assert_eq!((parsed - now()).num_nanoseconds(), Some(i64::MAX));
    }

    #[test]
    fn step_integer_division_boundary_and_manual_grammar() {
        assert_eq!(
            resolve_step("auto", now(), now() + Duration::seconds(1514)),
            "15s"
        );
        assert_eq!(
            resolve_step("auto", now(), now() + Duration::seconds(1515)),
            "30s"
        );
        for value in [" 1h2m ", "1ms", "01s", "1y1w"] {
            assert_eq!(resolve_step(value, now(), now()), value.trim());
        }
        for value in ["0s", "1h0m", "1.5s", "AUTO", "", "1µs"] {
            assert_eq!(resolve_step(value, now(), now()), "1m");
        }
    }

    #[test]
    fn utc_conversion_crosses_four_digit_year_boundary() {
        for (input, expected) in [
            ("9999-12-31T23:00:00-02:00", "10000-01-01T01:00:00Z"),
            ("0000-01-01T00:00:00+02:00", "-0001-12-31T22:00:00Z"),
        ] {
            assert_eq!(format_time(parse_time_with(input, now).unwrap()), expected);
        }
    }
}
