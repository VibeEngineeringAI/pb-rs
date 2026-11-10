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
