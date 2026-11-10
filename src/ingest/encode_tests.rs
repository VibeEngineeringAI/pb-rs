use super::super::fuzz::{self, Rng};
use super::*;

/// serde_json's own string serialization, which these functions replace.
fn serde_string(text: &str) -> String {
    serde_json::to_string(text).unwrap()
}

fn string(text: &str) -> String {
    let mut out = b"kept".to_vec();
    push_string(&mut out, text.as_bytes());
    String::from_utf8(out.split_off(4)).unwrap()
}

/// `copy_plain` from the start of `bytes`: the index it stops at, having
/// appended exactly the bytes before it and nothing else.
fn plain_run(bytes: &[u8]) -> usize {
    let mut out = b"kept".to_vec();
    let stop = copy_plain(&mut out, bytes, 0);
    assert_eq!(out[..4], *b"kept");
    assert_eq!(out[4..], bytes[..stop]);
    // Starting part-way gives the same answer from there.
    for start in [1, 3, 8, 9].into_iter().filter(|&start| start <= stop) {
        let mut out = Vec::new();
        assert_eq!(copy_plain(&mut out, bytes, start), stop);
        assert_eq!(out, bytes[start..stop]);
    }
    stop
}

#[test]
fn plain_run_stops_at_the_first_byte_needing_an_escape_at_every_offset() {
    // Every byte value at every position of every length up to three words,
    // surrounded by bytes which are clean but adjacent to the escaped ranges.
    for filler in [b'a', 0x20, 0x21, 0x23, 0x5b, 0x5d, 0x7f, 0x80, 0xff] {
        for length in 0..=24 {
            let clean = vec![filler; length];
            assert_eq!(plain_run(&clean), length);
            for position in 0..length {
                for byte in 0..=255u8 {
                    let mut bytes = clean.clone();
                    bytes[position] = byte;
                    let expected = if needs_escape(byte) { position } else { length };
                    assert_eq!(
                        plain_run(&bytes),
                        expected,
                        "{byte:#x} at {position}/{length}"
                    );
                }
            }
        }
    }
    // A flagged byte must not hide an earlier one, nor a borrow invent one.
    let mut rng = Rng::new(1);
    for _ in 0..200_000 {
        let length = rng.below(40);
        let bytes: Vec<u8> = (0..length)
            .map(|_| match rng.below(8) {
                0 => rng.below(0x21) as u8,
                1 => *rng.pick(&[&b'"', &b'\\', &0x20, &0x21, &0x23, &0x5b, &0x5d]),
                2 => rng.below(256) as u8,
                _ => b'a' + rng.below(26) as u8,
            })
            .collect();
        let expected = bytes
            .iter()
            .position(|&byte| needs_escape(byte))
            .unwrap_or(length);
        assert_eq!(plain_run(&bytes), expected, "{bytes:?}");
    }
}

#[test]
fn strings_match_serde_json_for_every_character_class_and_alignment() {
    assert_eq!(string(""), "\"\"");
    assert_eq!(
        string("say \"hi\"\t\\ \u{e9}\u{1}\u{7f}/"),
        "\"say \\\"hi\\\"\\t\\\\ \u{e9}\\u0001\u{7f}/\""
    );
    // Every ASCII character and a sample of longer ones, at each offset
    // within and across eight-byte words, alone and doubled.
    let wide = ['\u{80}', 'é', '\u{7ff}', '\u{800}', '✓', '\u{ffff}', '😀'];
    for character in (0..=0x7fu8).map(char::from).chain(wide) {
        for before in 0..=17 {
            for after in [0, 1, 7, 8, 9] {
                for repeat in 1..=2 {
                    let mut text = "p".repeat(before);
                    text.extend(std::iter::repeat_n(character, repeat));
                    text.push_str(&"s".repeat(after));
                    assert_eq!(string(&text), serde_string(&text), "{text:?}");
                }
            }
        }
    }
}

#[test]
fn seeded_strings_match_serde_json() {
    let mut rng = Rng::new(2);
    for case in 0..60_000 {
        let pieces = rng.below(if case % 100 == 0 { 400 } else { 12 });
        let text = fuzz::text(&mut rng, pieces);
        assert_eq!(string(&text), serde_string(&text), "case {case}: {text:?}");
    }
    // Huge and uniform inputs: a megabyte of clean text, of one escape, and
    // of clean text with an escape at the very end.
    for text in [
        "x".repeat(1 << 20),
        "\"".repeat(1 << 20),
        "\u{1}".repeat(1 << 18),
        "é".repeat(1 << 19),
        format!("{}\n", "x".repeat((1 << 20) + 3)),
    ] {
        assert!(string(&text) == serde_string(&text));
    }
}

#[test]
fn integers_match_their_decimal_display() {
    let mut values = vec![
        0,
        1,
        9,
        10,
        11,
        99,
        100,
        (1 << 53) - 1,
        u64::MAX - 1,
        u64::MAX,
    ];
    let mut power = 1u64;
    while let Some(next) = power.checked_mul(10) {
        values.extend([next - 1, next, next + 1]);
        power = next;
    }
    let mut rng = Rng::new(3);
    values.extend((0..10_000).map(|_| rng.next() >> rng.below(64)));
    for value in values {
        let mut out = b"n=".to_vec();
        push_u64(&mut out, value);
        assert_eq!(out, format!("n={value}").into_bytes());
        assert_eq!(out[2..], serde_json::to_vec(&value).unwrap());
    }
}
