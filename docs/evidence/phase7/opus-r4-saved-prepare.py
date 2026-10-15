#!/usr/bin/env python3
"""Prepare O4-1 saved.rs off-tree only. Never applies a production patch.

The explicit edit-window message is required before a separate apply step.
Guards the frozen release4 input and writes only the dedicated owned cache.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPO=Path(__file__).resolve().parents[3]
CACHE=Path('/home/ajs/.cache/pb-phase7/opus-r4-saved')
TMP='/home/ajs/.cache/pb-phase7/tmp'

MATCHER=r'''// A local matcher over owned titles. Progress separates the score for the
// current pattern rune from the retained byte candidate. The latter deliberately
// survives an apply: actual pinned fuzzy controls reuse it after NUL boundaries
// and after rejecting a wrapped candidate score. No service state is captured.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct MatchFault {
    index: usize,
    len: usize,
}

impl std::fmt::Display for MatchFault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "runtime error: index out of range [{}] with length {}", self.index, self.len)
    }
}

struct MatchProgress {
    consumed: usize,
    retained_byte: Option<usize>,
    best_score: i64,
    last_applied: Option<usize>,
    adjacent_bonus: i64,
    total: i64,
}

impl MatchProgress {
    fn new() -> Self {
        Self {
            consumed: 0,
            retained_byte: None,
            best_score: -1,
            last_applied: None,
            adjacent_bonus: 0,
            total: 0,
        }
    }

    fn consider(&mut self, byte: usize, rune: char, previous: Option<(usize, char)>) {
        let mut score: i64 = if byte == 0 { 10 } else { 0 };
        if let Some((prior_byte, prior)) = previous {
            if fuzzy_unicode::is_lower(prior) && fuzzy_unicode::is_upper(rune) {
                score += 20;
            }
            if "/-_ .\\".contains(prior) {
                score += 20;
            }
            if self.last_applied == Some(prior_byte) {
                let bonus = self.adjacent_bonus.wrapping_mul(2).wrapping_add(5);
                self.adjacent_bonus = self.adjacent_bonus.wrapping_add(bonus);
                score = score.wrapping_add(bonus);
            }
        }
        // This floor applies to the candidate, not to the final match total.
        if score > self.best_score {
            self.best_score = score;
            self.retained_byte = Some(byte);
        }
    }

    fn apply(&mut self) {
        if let Some(byte) = self.retained_byte {
            if self.consumed == 0 {
                self.total = self.total.wrapping_add((byte as i64).wrapping_mul(-5).max(-15));
            }
            self.total = self.total.wrapping_add(self.best_score);
            self.last_applied = Some(byte);
            self.consumed += 1;
            self.best_score = -1;
        }
    }
}

fn match_score(pattern: &str, title: &str) -> Result<Option<i64>, MatchFault> {
    let needle: Vec<_> = pattern.chars().collect();
    if needle.is_empty() {
        return Ok(None);
    }
    let mut progress = MatchProgress::new();
    let mut text = title.char_indices().peekable();
    let mut previous = None;
    while let Some((byte, rune)) = text.next() {
        let Some(&expected) = needle.get(progress.consumed) else {
            // NUL can consume the last pattern rune while title bytes remain.
            // Surface the command fault explicitly rather than panicking in
            // Update or returning a completed match before scanning the title.
            return Err(MatchFault { index: progress.consumed, len: needle.len() });
        };
        if fold_equal(rune, expected) {
            progress.consider(byte, rune, previous);
        }
        let next_rune = text.peek().map_or('\0', |&(_, rune)| rune);
        let next_pattern = needle.get(progress.consumed + 1).copied().unwrap_or('\0');
        if next_rune == '\0' || fold_equal(next_pattern, next_rune) {
            progress.apply();
        }
        previous = Some((byte, rune));
    }
    Ok((progress.consumed == needle.len()).then(|| {
        progress.total.wrapping_add(progress.consumed as i64).wrapping_sub(title.len() as i64)
    }))
}
'''

REGRESSIONS=r'''    #[test]
    fn fuzzy_nul_fault_is_a_command_fault_and_typed_a_still_applies() {
        let fault = MatchFault { index: 1, len: 1 };
        assert_eq!(match_score("a", "a\0b"), Err(fault));
        assert_eq!(match_score("a", "a\0"), Err(fault));
        assert_eq!(match_score("ab", "ab\0x"), Err(MatchFault { index: 2, len: 2 }));
        assert_eq!(match_score("", "a\0b"), Ok(None));
        assert_eq!(super::super::profile::caught_panic_stdout(&fault.to_string()),
            "Caught panic:\n\nruntime error: index out of range [1] with length 1\n\nRestoring terminal...\n\n");
        let selected = item("nul", "a\0b", "select 1");
        let mut state = State::new(vec![selected.clone()]);
        press(&mut state, "/");
        assert_eq!(press(&mut state, "a"), Some(Effect::Finish(Some(selected.clone()))));
        assert_eq!(state.filter.value(), "");
        let mut state = State::new(vec![selected]);
        press(&mut state, "/");
        assert_eq!(press(&mut state, "ctrl+v"), Some(Effect::ReadClipboard));
        assert_eq!(state.paste("a"), Some(Effect::MatchFault(fault)));
        // A matching fault is distinct from EditorFault's successful no-op.
        let mut state = State::new(vec![item("nul", "a\0b", "select 1")]);
        press(&mut state, "/");
        assert_eq!(press(&mut state, "A"), Some(Effect::MatchFault(fault)));
    }

    #[test]
    fn fuzzy_nul_reuses_the_retained_byte_and_preserves_application() {
        assert_eq!(match_score("ab", "a\0x"), Ok(Some(8)));
        assert_eq!(match_score("abc", "a\0x\0y"), Ok(Some(6)));
        assert_eq!(match_score("a", "x\0a"), Ok(Some(-12)));
        assert_eq!(match_score("ab", "ax"), Ok(None));
        let selected = item("stale", "a\0x", "SELECT event.code FROM logs");
        let mut state = State::new(vec![item("decoy", "other", "select 0"), selected.clone()]);
        press(&mut state, "/");
        assert!(state.paste("ab").is_none());
        assert_eq!(state.visible, vec![1]);
        assert_eq!(press(&mut state, "enter"), Some(Effect::Finish(Some(selected))));
    }

    #[test]
    fn fuzzy_overflow_uses_the_verified_candidate_floor_and_go_rankings() {
        // Actual Go int64 scores. At 40 runes the total wraps negative while
        // the candidate is positive; only the 43rd candidate is rejected.
        for (count, score) in [
            (40, -8_315_356_191_162_110_941), (41, -6_499_324_499_776_781_222),
            (42, -1_051_229_425_620_792_065), (43, -1_051_229_425_620_792_066),
            (44, -1_051_229_425_620_792_066), (45, -1_051_229_425_620_792_067),
            (64, 8_492_147_925_252_739_361),
        ] {
            let pattern = "a".repeat(count);
            assert_eq!(match_score(&pattern, &pattern), Ok(Some(score)), "count {count}");
        }
        for count in [43, 44, 45, 64] {
            let pattern = "a".repeat(count);
            let titles = [pattern.clone(), format!("{}_a", "a".repeat(count - 1)),
                format!("_{pattern}"), format!("{pattern}a")];
            let mut state = State::new(titles.iter().enumerate()
                .map(|(i, title)| item(&i.to_string(), title, &format!("select {i}"))).collect());
            press(&mut state, "/");
            assert!(state.paste(&pattern).is_none());
            assert_eq!(state.visible, vec![1, 2, 0, 3], "count {count}");
            press(&mut state, "down"); // accept filter, preserving selection
            press(&mut state, "down"); // navigate to the second Go rank
            assert_eq!(state.selected().unwrap().sql, "select 2");
        }
    }

'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    before=json.loads(Path(__file__).with_name('opus-r4-saved-before.json').read_text())
    source=(REPO/'src/tui/saved.rs').read_bytes()
    assert sha(source)==before['before_sha256']['src/tui/saved.rs'], 'frozen input changed'
    text=source.decode()
    def replace(old,new):
        nonlocal text
        assert text.count(old)==1, ('replacement marker changed',old[:100])
        text=text.replace(old,new)
    replace('async fn finish(\n', '''async fn command_panic(
    terminal: TerminalSession,
    mut tasks: ClipboardTasks,
    value: impl std::fmt::Display,
) -> Result<Option<SavedSelection>, CliError> {
    stop_clipboards(&mut tasks).await;
    drop(terminal); // Command-goroutine recovery: no final render.
    use std::io::Write;
    let _ = std::io::stdout().lock()
        .write_all(super::profile::caught_panic_stdout(&value.to_string()).as_bytes());
    Err(session_error("program was killed: context canceled"))
}

async fn finish(
''')
    replace('''                Some(Effect::ReadClipboard) => {''','''                Some(Effect::MatchFault(fault)) => {
                    return command_panic(terminal, tasks, fault).await;
                }
                Some(Effect::ReadClipboard) => {''')
    replace('''                if let Some(fault) = state.paste(&text) {
                    return editor_fault(terminal, tasks, fault, None).await;
                }''','''                match state.paste(&text) {
                    Some(Effect::Fault(fault)) => {
                        return editor_fault(terminal, tasks, fault, None).await;
                    }
                    Some(Effect::MatchFault(fault)) => {
                        return command_panic(terminal, tasks, fault).await;
                    }
                    _ => {}
                }''')
    replace('''                stop_clipboards(&mut tasks).await;
                drop(terminal);
                use std::io::Write;
                let _ = std::io::stdout()
                    .lock()
                    .write_all(super::profile::caught_panic_stdout(&value).as_bytes());
                return Err(session_error("program was killed: context canceled"));''',
            '''                return command_panic(terminal, tasks, value).await;''')
    replace('    Fault(EditorFault),\n','    Fault(EditorFault),\n    MatchFault(MatchFault),\n')
    replace('''            if let Some(fault) = self.edit_filter(key) {
                return Some(Effect::Fault(fault));
            }''','''            if let Some(effect) = self.edit_filter(key) {
                return Some(effect);
            }''')
    replace('fn edit_filter(&mut self, key: &Key) -> Option<EditorFault>',
            'fn edit_filter(&mut self, key: &Key) -> Option<Effect>')
    replace('''        if let Some(fault) = self.filter.take_fault() {
            return Some(fault);
        }
        if before != self.filter.value() {
            self.refilter();
        }''','''        if let Some(fault) = self.filter.take_fault() {
            return Some(Effect::Fault(fault));
        }
        if before != self.filter.value() {
            return self.refilter().err().map(Effect::MatchFault);
        }''')
    replace('fn refilter(&mut self) {','fn refilter(&mut self) -> Result<(), MatchFault> {')
    replace('''            let mut matches: Vec<_> = self
                .items
                .iter()
                .enumerate()
                .filter_map(|(i, item)| match_score(&pattern, &item.title).map(|score| (i, score)))
                .collect();''','''            let mut matches = Vec::new();
            for (index, item) in self.items.iter().enumerate() {
                if let Some(score) = match_score(&pattern, &item.title)? {
                    matches.push((index, score));
                }
            }''')
    replace('''        self.selected = 0;
    }
    fn paste(&mut self, text: &str) -> Option<EditorFault> {''','''        self.selected = 0;
        Ok(())
    }
    fn paste(&mut self, text: &str) -> Option<Effect> {''')
    replace('''            if let Some(fault) = self.filter.take_fault() {
                return Some(fault);
            }
            self.refilter();''','''            if let Some(fault) = self.filter.take_fault() {
                return Some(Effect::Fault(fault));
            }
            return self.refilter().err().map(Effect::MatchFault);''')
    start=text.index('// A local matcher over owned title data.')
    end=text.index('fn fold_equal(',start)
    text=text[:start]+MATCHER+text[end:]
    replace('''assert!(match_score("lg", "log").unwrap() > match_score("lg", "Log Server").unwrap());''',
            '''assert!(match_score("lg", "log").unwrap().unwrap() > match_score("lg", "Log Server").unwrap().unwrap());''')
    replace('assert_eq!(match_score("ss", "ß"), None);','assert_eq!(match_score("ss", "ß"), Ok(None));')
    replace('''                Some(score),
                "{pattern} / {title}"''','''                Ok(Some(score)),
                "{pattern} / {title}"''')
    replace('''    #[test]
    fn apply_and_quit_intercept_before_filtering()''',REGRESSIONS+'''    #[test]
    fn apply_and_quit_intercept_before_filtering()''')
    rustfmt='/home/ajs/.rustup/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin/rustfmt'
    env=os.environ.copy();env['TMPDIR']=TMP
    proposed=subprocess.check_output([rustfmt,'--edition','2024','--emit','stdout','--config','skip_children=true'],input=text.encode(),env=env)
    CACHE.mkdir(parents=True,exist_ok=True)
    (CACHE/'src-tui-saved.proposed.rs').write_bytes(proposed)
    patch=''.join(difflib.unified_diff(source.decode().splitlines(True),proposed.decode().splitlines(True),
                                     fromfile='a/src/tui/saved.rs',tofile='b/src/tui/saved.rs'))
    (CACHE/'opus-r4-saved-source.patch').write_text(patch)
    print(json.dumps(dict(proposed_sha256=sha(proposed),patch_sha256=sha(patch.encode()),
                         source_unchanged=sha((REPO/'src/tui/saved.rs').read_bytes())==sha(source)),indent=2))


if __name__=='__main__':
    main()
