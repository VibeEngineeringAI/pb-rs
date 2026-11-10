//! Bounded memoization of pure pane composition, shared by SQL and PromQL.
use super::{
    sql::reflow_truncate,
    style::{self, Env, Style},
};
use std::cell::RefCell;

struct Entry {
    body: String,
    width: i64,
    height: i64,
    border: Style,
    env: Env,
    lines: Vec<String>,
}

thread_local! {
    // Small panes only: neither result data nor entire frames are retained
    // without a bound. Thread locality also keeps independent sessions apart.
    static PANES: RefCell<Vec<Entry>> = const { RefCell::new(Vec::new()) };
}

pub(super) fn pane_body_lines(body: &str, width: i64, height: i64, border: &Style) -> Vec<String> {
    pane_body_lines_in(body, width, height, border, Env::process())
}

fn pane_body_lines_in(
    body: &str,
    width: i64,
    height: i64,
    border: &Style,
    env: Env,
) -> Vec<String> {
    if let Some(lines) = PANES.with(|cache| {
        let mut cache = cache.borrow_mut();
        let index = cache.iter().position(|entry| {
            entry.width == width
                && entry.height == height
                && entry.border == *border
                && entry.env == env
                && entry.body == body
        })?;
        let entry = cache.remove(index);
        let lines = entry.lines.clone();
        cache.push(entry);
        Some(lines)
    }) {
        return lines;
    }
    let text_w = (width - 4).max(1) as usize;
    let edge = border.render_in("│", env);
    let mut source = body.split('\n');
    let lines: Vec<String> = (0..height.max(0))
        .map(|_| {
            let line = source.next().unwrap_or("");
            let measured = style::width(line);
            let truncated;
            let (line, measured) = if measured > text_w {
                truncated = reflow_truncate(line, text_w, "");
                (truncated.as_str(), style::width(&truncated))
            } else {
                (line, measured)
            };
            let pad = text_w.saturating_sub(measured);
            let mut out = String::with_capacity(edge.len() * 2 + line.len() + pad + 2);
            out.push_str(&edge);
            out.push(' ');
            out.push_str(line);
            out.extend(std::iter::repeat_n(' ', pad));
            out.push(' ');
            out.push_str(&edge);
            out
        })
        .collect();
    let bytes = body.len() + lines.iter().map(String::len).sum::<usize>();
    if bytes <= 16 * 1024 {
        PANES.with(|cache| {
            let mut cache = cache.borrow_mut();
            if cache.len() == 16 {
                cache.remove(0);
            }
            cache.push(Entry {
                body: body.to_owned(),
                width,
                height,
                border: *border,
                env,
                lines: lines.clone(),
            });
        });
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::super::style::{Color, Profile, Role};
    use super::*;

    #[test]
    fn environment_is_part_of_pane_key() {
        for profile in [
            Profile::Ascii,
            Profile::Ansi,
            Profile::Ansi256,
            Profile::TrueColor,
        ] {
            for dark in [false, true] {
                let env = Env { profile, dark };
                let border = Style::new().fg(Color::role(Role::Border));
                let edge = border.render_in("│", env);
                let expected = vec![
                    format!("{edge} text   {edge}"),
                    format!("{edge}        {edge}"),
                ];
                assert_eq!(pane_body_lines_in("text", 10, 2, &border, env), expected);
                assert_eq!(pane_body_lines_in("text", 10, 2, &border, env), expected);
            }
        }
    }
}
