//! Small value-ownership rules shared by interactive JSON adapters.
//! HTTP policy and workflow state remain in each concrete service.

/// encoding/json struct field matching (decode.go `object`: exact name, else
/// `foldName`, fold.go) for an ASCII Go field name. `foldName` maps each rune
/// to the smallest of its `unicode.SimpleFold` set; the only non-ASCII runes
/// whose set contains ASCII are U+017F (ſ, with s/S) and U+212A (Kelvin, with
/// k/K). Both interactive query adapters use this value-only rule.
pub(super) fn json_field_is(key: &str, name: &str) -> bool {
    debug_assert!(name.is_ascii());
    let mut expected = name.bytes();
    for c in key.chars() {
        let c = match c {
            '\u{017F}' => 's',
            '\u{212A}' => 'k',
            c => c,
        };
        match expected.next() {
            Some(b) if c.is_ascii() && (c as u8).eq_ignore_ascii_case(&b) => {}
            _ => return false,
        }
    }
    expected.next().is_none()
}

/// Owned initialized slots are kept separately from the JSON array's active
/// length. Rust allocation capacity must not decide which prior values survive
/// a later duplicate field: only slots decoded earlier in this document matter.
/// Used by both interactive query adapters; typed slices follow the same
/// encoding/json array reuse without coupling their request policies.
pub(super) struct ReusedSlice<T> {
    slots: Vec<T>,
    len: usize,
}

impl<T> Default for ReusedSlice<T> {
    fn default() -> Self {
        Self {
            slots: Vec::new(),
            len: 0,
        }
    }
}

impl<T> ReusedSlice<T> {
    /// Element `index` of a JSON array decoded in order from 0: a slot this
    /// document decoded before (visible or hidden) is reused, else a zero value.
    pub(super) fn slot(&mut self, index: usize, initial: impl FnOnce() -> T) -> &mut T {
        if index == self.slots.len() {
            self.slots.push(initial());
        }
        &mut self.slots[index]
    }

    /// `SetLen` after a non-empty array: later slots stay as hidden capacity.
    pub(super) fn set_len(&mut self, len: usize) {
        debug_assert!(len <= self.slots.len());
        self.len = len;
    }

    pub(super) fn clear(&mut self) {
        // Both [] and null discard the old slice, including hidden elements.
        self.slots.clear();
        self.len = 0;
    }

    pub(super) fn finish(mut self) -> Vec<T> {
        self.slots.truncate(self.len);
        self.slots
    }
}
