//! Inventory-specific table helpers using the existing terminal renderer.
pub(super) use crate::metrics::output::width;
use crate::metrics::output::{line_width, style as shared};
#[cfg(test)]
pub(super) use shared::Profile;
pub(crate) use shared::Terminal;

#[derive(Clone, Copy)]
pub(super) struct Style(shared::Style);
pub(super) const HEADER: Style = Style(shared::SELECTED);
pub(super) const BODY: Style = Style(shared::STANDARD);
pub(super) const MUTED: Style = Style(shared::MUTED);
pub(super) const RULE: Style = Style(shared::RULE);
impl Style {
    pub(super) fn render_bytes(&self, text: &[u8], terminal: &Terminal) -> Vec<u8> {
        self.0.render_bytes(text, terminal)
    }
    pub(super) fn render(&self, text: &str, terminal: &Terminal) -> String {
        self.0.render(text, terminal, line_width)
    }
}

/// cmd.padRight.
pub(super) fn pad_right(text: &str, column: usize) -> String {
    format!("{text}{}", " ".repeat(column.saturating_sub(width(text))))
}

/// cmd.truncateCell measured with lipgloss.Width over the accumulated text.
pub(super) fn truncate_cell(text: &str, limit: usize) -> String {
    if width(text) <= limit {
        return text.into();
    }
    if limit <= 1 {
        return "…".into();
    }
    let mut out = String::new();
    let mut previous_lines = 0;
    let mut line_start = 0;
    for c in text.chars() {
        let joined;
        let next_line = if c == '\n' {
            previous_lines = previous_lines.max(line_width(&out[line_start..]));
            ""
        } else {
            joined = format!("{}{c}", &out[line_start..]);
            joined.as_str()
        };
        if previous_lines.max(line_width(next_line)) + 1 > limit {
            break;
        }
        out.push(c);
        if c == '\n' {
            line_start = out.len();
        }
    }
    out + "…"
}

pub(super) fn truncate_bytes(text: &[u8], limit: usize) -> Vec<u8> {
    if text
        .split(|byte| *byte == b'\n')
        .map(crate::metrics::output::line_width_bytes)
        .max()
        .unwrap_or(0)
        <= limit
    {
        return text.to_vec();
    }
    // Go's range decodes runes only on the truncation path.
    truncate_cell(&crate::error::go_string(text), limit).into_bytes()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn styles_wrap_each_line_and_pad_multiline_cells() {
        let t = Terminal {
            profile: Profile::Ansi256,
            dark: true,
        };
        assert_eq!(HEADER.render("USER", &t), "\x1b[1;38;5;147mUSER\x1b[0m");
        assert_eq!(MUTED.render("-", &t), "\x1b[38;5;145m-\x1b[0m");
        assert_eq!(
            MUTED.render("-", &Terminal { dark: false, ..t }),
            "\x1b[38;5;59m-\x1b[0m"
        );
        assert_eq!(RULE.render("", &t), "\x1b[38;5;59m\x1b[0m");
        assert_eq!(
            BODY.render("ab\ncde", &t),
            "\x1b[38;5;188mab\x1b[0m \n\x1b[38;5;188mcde\x1b[0m"
        );
        assert_eq!(BODY.render("a\tb\r\nc", &Terminal::PLAIN), "a    b\nc     ");
    }
    #[test]
    fn truncation_and_widths_follow_lipgloss() {
        assert_eq!(width("日本\nabc"), 4);
        assert_eq!(truncate_cell("abcdef", 4), "abc…");
        assert_eq!(truncate_cell("日本語", 4), "日…");
        assert_eq!(truncate_cell("abc", 1), "…");
        assert_eq!(truncate_cell("ab\ncdefg", 4), "ab\ncde…");
        assert_eq!(pad_right("日", 4), "日  ");
        assert_eq!(line_width("\x1b[31mred\x1b[0m"), 3);
    }
}
