//! Seeded input for the differential tests of the ingestion encoders. Every
//! generator is deterministic, so a failure names a reproducible case.

/// xorshift64*: deterministic, dependency-free test input.
pub(super) struct Rng(u64);

impl Rng {
    pub(super) fn new(seed: u64) -> Self {
        Self(seed.wrapping_mul(0x9e37_79b9_7f4a_7c15) | 1)
    }

    pub(super) fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }

    /// A value in `0..bound`.
    pub(super) fn below(&mut self, bound: usize) -> usize {
        (self.next() >> 33) as usize % bound
    }

    pub(super) fn chance(&mut self, percent: usize) -> bool {
        self.below(100) < percent
    }

    pub(super) fn pick<'a, T: ?Sized>(&mut self, choices: &[&'a T]) -> &'a T {
        choices[self.below(choices.len())]
    }
}

/// Pieces of text which exercise every escape, the bytes either side of each
/// escaped range, and characters of every encoded length.
const TEXT: &[&str] = &[
    "a",
    "b",
    " ",
    "key",
    "hello world",
    "0123456789abcdef",
    "\"",
    "\\",
    "/",
    "\u{8}",
    "\t",
    "\n",
    "\u{c}",
    "\r",
    "\0",
    "\u{1}",
    "\u{b}",
    "\u{1f}",
    "\u{20}",
    "!",
    "#",
    "[",
    "]",
    "\u{7f}",
    "\u{80}",
    "é",
    "ключ",
    "✓",
    "日本",
    "\u{2028}",
    "\u{ffff}",
    "😀",
    "\u{10ffff}",
    "\\u0041",
    "\\n",
    "{\"k\":\"v\"}",
];

/// Text of roughly `pieces` fragments; valid UTF-8 by construction.
pub(super) fn text(rng: &mut Rng, pieces: usize) -> String {
    let mut text = String::new();
    for _ in 0..pieces {
        if rng.chance(30) {
            // Long clean runs cross several eight-byte words.
            let run = rng.below(40);
            text.extend(std::iter::repeat_n('x', run));
        } else {
            text.push_str(rng.pick(TEXT));
        }
    }
    text
}

/// Damage `line` so that it is (almost always) no longer valid UTF-8: a
/// stray or truncated sequence, an overlong form, or an encoded surrogate.
pub(super) fn corrupt(rng: &mut Rng, line: &mut Vec<u8>) {
    let damage: &[u8] = rng.pick(&[
        &[0xff][..],
        &[0x80],
        &[0xc3],
        &[0xe2, 0x9c],
        &[0xf0, 0x9f, 0x98],
        &[0xc0, 0x80],
        &[0xed, 0xa0, 0x80],
        &[0xf4, 0x90, 0x80, 0x80],
    ]);
    let at = rng.below(line.len() + 1);
    line.splice(at..at, damage.iter().copied());
}

const WHITESPACE: &[&str] = &["", "", "", " ", "  ", "\t", " \t ", "\r"];

/// Number spellings: plain, at and beyond the 64-bit limits, with fractions
/// and exponents of every form, and (from `INVALID_NUMBERS` on) not numbers.
const NUMBERS: &[&str] = &[
    "0",
    "-0",
    "1",
    "-1",
    "7",
    "-42",
    "100",
    "12345678901234567890",
    "123456789012345678901234567890",
    "9223372036854775807",
    "-9223372036854775808",
    "-9223372036854775809",
    "18446744073709551615",
    "18446744073709551616",
    "0.0",
    "-0.0",
    "-0.5",
    "1.5",
    "3.14159",
    "10.010",
    "0.123456789012345678901234567890",
    "1000000000000000000000000000000.5",
    "9999999999999999999999999999999999999999999999999999999999999999999999",
    "1e5",
    "1E5",
    "1e+20",
    "1E-7",
    "-0e1",
    "0e0",
    "1.0e0",
    "2.5E+3",
    "-1.25e-3",
    "1.",
    "01",
    "-01",
    "00",
    ".5",
    "+1",
    "1e",
    "1e+",
    "1.e5",
    "--1",
    "-",
    "0x10",
    "NaN",
    "Infinity",
    "-Infinity",
    "1_000",
];
const INVALID_NUMBERS: usize = 32;

const KEYS: &[&str] = &[
    "\"a\"",
    "\"b\"",
    "\"c\"",
    "\"id\"",
    "\"msg\"",
    "\"level\"",
    "\"nested\"",
    "\"a\"",
    "\"\"",
    "\"k\\u0061\"",
    "\"\\u0061\"",
    "\"\\u0041\"",
    "\"A\"",
    "\"a\\/b\"",
    "\"a/b\"",
    "\"a\\nb\"",
    "\"a\\u000ab\"",
    "\"ключ\"",
    "\"\\u043a\\u043b\\u044e\\u0447\"",
    "\"$serde_json::private::Number\"",
    "\"$serde_json::private::RawValue\"",
    "\"$serde_json::private::Other\"",
    "\"$\"",
];

fn whitespace(rng: &mut Rng) -> &'static str {
    rng.pick(WHITESPACE)
}

/// A JSON string in source form: plain text, every escape, surrogate pairs,
/// and (rarely) escapes and raw control characters which are errors.
fn json_string(rng: &mut Rng) -> String {
    let mut string = String::from("\"");
    for _ in 0..rng.below(7) {
        let choices: &[&str] = match rng.below(100) {
            0..55 => &[
                "a",
                "b",
                "key",
                "msg",
                "hello world",
                "café",
                "✓",
                "日本",
                "😀",
                "/",
                "'",
                "<>&",
                " ",
                "\u{7f}",
                "\u{80}",
                "\u{2028}",
                "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            ],
            55..75 => &["\\\"", "\\\\", "\\/", "\\b", "\\f", "\\n", "\\r", "\\t"],
            75..86 => &[
                "\\u0041",
                "\\u00e9",
                "\\u00E9",
                "\\u2713",
                "\\ud83d\\ude00",
                "\\uD83D\\uDE00",
                "\\udbff\\udfff",
                "\\ud800\\udc00",
                "\\u0000",
                "\\u001f",
                "\\u001F",
                "\\u0008",
                "\\u000c",
                "\\u0022",
                "\\u005C",
                "\\u005c",
                "\\u002f",
                "\\u000a",
                "\\u007f",
                "\\uffff",
                "\\ud7ff",
                "\\ue000",
            ],
            86..90 => &[
                "\\ud800",
                "\\udc00",
                "\\ud800\\u0041",
                "\\ud800\\ud800",
                "\\udc00\\ud800",
                "\\ud83d\\n",
                "\\ud83dx",
                "\\x41",
                "\\u12",
                "\\u12g4",
                "\\",
                "\\a",
                "\\U0041",
                "\\u",
                "\\ud83d\\u",
            ],
            90..93 => &["\u{1}", "\t", "\u{1f}", "\0"],
            _ => &["plain", "value", "0123456789abcdef0123456789abcdef"],
        };
        string.push_str(rng.pick(choices));
    }
    string.push('"');
    string
}

fn json_value(rng: &mut Rng, depth: usize) -> String {
    let mut choice = rng.below(100);
    if depth > 4 {
        choice = choice * 6 / 10;
    }
    match choice {
        0..25 => json_string(rng),
        25..45 if rng.chance(90) => rng.pick(&NUMBERS[..INVALID_NUMBERS]).to_owned(),
        25..45 => rng.pick(NUMBERS).to_owned(),
        45..55 if rng.chance(10) => rng
            .pick(&["True", "nul", "tru", "nulll", "falsey", "NULL"])
            .to_owned(),
        45..55 => rng.pick(&["true", "false", "null"]).to_owned(),
        55..60 => rng.pick(NUMBERS).to_owned(),
        60..80 => json_object(rng, depth + 1, false),
        _ => {
            let mut array = format!("[{}", whitespace(rng));
            for index in 0..rng.below(5) {
                if index != 0 {
                    array.push(',');
                    array.push_str(whitespace(rng));
                }
                array.push_str(&json_value(rng, depth + 1));
                array.push_str(whitespace(rng));
            }
            if rng.chance(3) {
                array.push(',');
            }
            array.push(']');
            array
        }
    }
}

/// An object in source form. `wide` objects have enough keys to be checked
/// for duplicates by sorting, and now and then really contain one.
fn json_object(rng: &mut Rng, depth: usize, wide: bool) -> String {
    let count = if wide {
        14 + rng.below(30)
    } else {
        rng.below(6)
    };
    let mut object = format!("{{{}", whitespace(rng));
    for index in 0..count {
        if index != 0 {
            object.push(',');
            object.push_str(whitespace(rng));
        }
        if !wide && rng.chance(80) {
            object.push_str(rng.pick(KEYS));
        } else if rng.chance(90) {
            let number = if rng.chance(97) {
                index
            } else {
                rng.below(count)
            };
            object.push_str(&format!("\"k{number}\""));
        } else {
            object.push_str(&json_string(rng));
        }
        object.push_str(whitespace(rng));
        object.push(':');
        object.push_str(whitespace(rng));
        object.push_str(&json_value(rng, depth));
        object.push_str(whitespace(rng));
    }
    if rng.chance(2) {
        object.push(',');
    }
    object.push('}');
    object
}

/// One JSONL source line without its newline: mostly objects of every
/// shape, with a share of other values, broken syntax, nesting around the
/// depth limits, and invalid UTF-8.
pub(super) fn jsonl_line(rng: &mut Rng) -> Vec<u8> {
    let line = match rng.below(100) {
        0..78 => {
            let wide = rng.chance(4);
            format!(
                "{}{}{}",
                whitespace(rng),
                json_object(rng, 0, wide),
                whitespace(rng)
            )
        }
        78..82 => json_value(rng, 0),
        82..84 => format!(
            "{}{}",
            json_object(rng, 0, false),
            rng.pick(&["x", "{}", ",", "]", "}", " 1", "\u{feff}"])
        ),
        84..86 => rng
            .pick(&["[", "{", "{\"a\":", "{\"a\":["])
            .repeat(1 + rng.below(70)),
        86..90 => {
            let depth = *rng.pick(&[
                &1, &10, &62, &63, &64, &65, &66, &126, &127, &128, &129, &200,
            ]);
            let (open, close) = *rng.pick(&[&("{\"a\":", "}"), &("[", "]"), &("{\"a\":[", "]}")]);
            let leaf = rng.pick(&["1", "{}", "[]", "\"s\"", ""]);
            format!(
                "{{\"top\":{}{leaf}{}}}",
                open.repeat(depth - 1),
                close.repeat(depth - 1)
            )
        }
        90..91 => String::new(),
        91..94 => {
            let object = json_object(rng, 0, false);
            let cut = rng.below(object.len().min(24) + 1);
            String::from_utf8_lossy(&object.as_bytes()[..cut]).into_owned()
        }
        _ => {
            // Ordinary log records, as a serializer writes them.
            let message = text(rng, 6);
            let record = serde_json::json!({
                "i": rng.next() >> rng.below(64),
                "s": message,
                "f": (rng.below(2_000_000) as f64 - 1e6) / 64.0,
                "n": [null, true, -(rng.below(1 << 30) as i64)],
                "o": {"level": rng.pick(&["info", "warn"]), "tags": ["a", "b"]},
            });
            if rng.chance(50) {
                serde_json::to_string(&record).unwrap()
            } else {
                serde_json::to_string_pretty(&record)
                    .unwrap()
                    .replace('\n', " ")
            }
        }
    };
    let mut line = line.into_bytes();
    if rng.chance(1) {
        corrupt(rng, &mut line);
    }
    line
}
