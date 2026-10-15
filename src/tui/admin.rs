//! Owned inline administration state. HTTP mutations run only after the
//! terminal driver has returned and its RAII session has restored the terminal.
use super::{
    input::{Event, Key, KeyCode},
    style::{Color, Role, Style, join_horizontal},
    terminal::{Screen, TerminalSession},
};
use crate::{admin_datasets::DatasetKind, error::CliError};

fn session_error(error: impl std::fmt::Display) -> CliError {
    CliError::new(crate::ErrorCode::CommandFailed, error.to_string())
}
fn panic_report(value: &str) {
    use std::io::Write;
    let _ = std::io::stdout()
        .lock()
        .write_all(super::profile::caught_panic_stdout(value).as_bytes());
}

pub(crate) fn select_dataset_kind() -> Result<Option<DatasetKind>, CliError> {
    let mut terminal = TerminalSession::open(Screen::Inline).map_err(session_error)?;
    let mut state = DatasetState::default();
    loop {
        terminal.draw(&state.view());
        match terminal.next_event_blocking() {
            Some(Event::Key(key)) => {
                if let Some(DatasetEffect::Finish(kind)) = state.update(&key.name()) {
                    let _ = terminal.close(&state.view());
                    return Ok(kind);
                }
            }
            Some(Event::InputError(error)) => {
                let _ = terminal.close(&state.view());
                return Err(session_error(error));
            }
            Some(Event::Panicked(value)) => {
                drop(terminal);
                panic_report(&value);
                return Err(session_error("program was killed: context canceled"));
            }
            Some(Event::Quit) | None => {
                let _ = terminal.close(&state.view());
                return Ok(None);
            }
            _ => {}
        }
    }
}

pub(crate) fn select_privilege() -> Result<Option<Privilege>, CliError> {
    let mut terminal = TerminalSession::open(Screen::Inline).map_err(session_error)?;
    let mut state = RoleState::default();
    loop {
        terminal.draw(&state.view());
        match terminal.next_event_blocking() {
            Some(Event::Key(key)) => {
                if let Some(RoleEffect::Finish(privilege)) = state.update(&key) {
                    let _ = terminal.close(&state.view());
                    return Ok(privilege);
                }
            }
            Some(Event::InputError(error)) => {
                let _ = terminal.close(&state.view());
                return Err(session_error(error));
            }
            Some(Event::Panicked(value)) => {
                drop(terminal);
                panic_report(&value);
                return Err(session_error("program was killed: context canceled"));
            }
            Some(Event::Quit) | None => {
                let _ = terminal.close(&state.view());
                return Ok(None);
            }
            _ => {}
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Privilege {
    Admin,
    Editor,
    Ingestor,
    Reader,
    Writer,
}
impl Privilege {
    const ALL: [Self; 5] = [
        Self::Admin,
        Self::Editor,
        Self::Ingestor,
        Self::Reader,
        Self::Writer,
    ];
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Admin => "admin",
            Self::Editor => "editor",
            Self::Ingestor => "ingestor",
            Self::Reader => "reader",
            Self::Writer => "writer",
        }
    }
}
#[derive(Debug, Default)]
pub(crate) struct DatasetState {
    cursor: usize,
}
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum DatasetEffect {
    Finish(Option<DatasetKind>),
}
impl DatasetState {
    const KINDS: [DatasetKind; 3] = [DatasetKind::Logs, DatasetKind::Metrics, DatasetKind::Traces];
    pub(crate) fn update(&mut self, key: &str) -> Option<DatasetEffect> {
        match key {
            "esc" | "ctrl+c" => return Some(DatasetEffect::Finish(None)),
            "up" | "k" => self.cursor = self.cursor.saturating_sub(1),
            "down" | "j" => self.cursor = (self.cursor + 1).min(2),
            "enter" => return Some(DatasetEffect::Finish(Some(Self::KINDS[self.cursor]))),
            _ => {}
        }
        None
    }
    fn view(&self) -> String {
        let accent = Style::new().fg(Color::role(Role::Accent)).bold(true);
        let mut out = format!(
            "{}\n\n{}\n\n",
            accent.render("CREATE DATASET"),
            Style::new()
                .fg(Color::role(Role::Mute))
                .bold(true)
                .render("DATASET TYPE")
        );
        for (index, kind) in Self::KINDS.iter().enumerate() {
            if index == self.cursor {
                out.push_str(&format!(
                    "{} {}",
                    Style::new().bg(Color::role(Role::Active)).render(" "),
                    Style::new()
                        .fg(Color::role(Role::Active))
                        .bold(true)
                        .render(&format!("❯ {}", kind.as_str()))
                ));
            } else {
                out.push_str(&format!(
                    "    {}",
                    Style::new()
                        .fg(Color::role(Role::Body))
                        .render(kind.as_str())
                ));
            }
            out.push('\n');
        }
        let dim = Style::new().fg(Color::role(Role::Faint));
        out.push_str(&format!(
            "\n  {}{}{}{}{}{}",
            accent.render("<↑↓>"),
            dim.render(" navigate    "),
            accent.render("<enter>"),
            dim.render(" select    "),
            accent.render("<esc>"),
            dim.render(" cancel")
        ));
        out
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum RoleFocus {
    #[default]
    Privilege,
    Submit,
}
#[derive(Debug, Default)]
pub(crate) struct RoleState {
    cursor: usize,
    focus: RoleFocus,
}
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum RoleEffect {
    Finish(Option<Privilege>),
}
impl RoleState {
    pub(crate) fn update(&mut self, key: &Key) -> Option<RoleEffect> {
        // Go's role form dispatches KeyType, so Alt+Enter/arrows behave just
        // like Enter/arrows here. Dataset's picker dispatches Key.String().
        match key.code {
            KeyCode::Ctrl(3) => return Some(RoleEffect::Finish(None)),
            KeyCode::Enter if self.focus == RoleFocus::Submit => {
                return Some(RoleEffect::Finish(Some(Privilege::ALL[self.cursor])));
            }
            KeyCode::Down | KeyCode::Tab | KeyCode::Enter | KeyCode::Up | KeyCode::ShiftTab => {
                self.focus = match self.focus {
                    RoleFocus::Privilege => RoleFocus::Submit,
                    RoleFocus::Submit => RoleFocus::Privilege,
                };
            }
            KeyCode::Left if self.focus == RoleFocus::Privilege => {
                self.cursor = self.cursor.saturating_sub(1)
            }
            KeyCode::Right if self.focus == RoleFocus::Privilege => {
                self.cursor = (self.cursor + 1).min(4)
            }
            _ => {}
        }
        None
    }
    fn view(&self) -> String {
        let focused = self.focus == RoleFocus::Privilege;
        let mut blocks = Vec::new();
        for (index, privilege) in Privilege::ALL.iter().enumerate() {
            let label = privilege.as_str();
            if index == self.cursor {
                let border =
                    Style::new().fg(Color::role(if focused { Role::Accent } else { Role::Mute }));
                let (tl, tr, bl, br) = if focused {
                    ("╭", "╮", "╰", "╯")
                } else {
                    ("┌", "┐", "└", "┘")
                };
                blocks.push(format!(
                    "{}\n{}{}{}\n{}",
                    border.render(&format!("{tl}{}{tr}", "─".repeat(label.len()))),
                    border.render("│"),
                    label,
                    border.render("│"),
                    border.render(&format!("{bl}{}{br}", "─".repeat(label.len())))
                ));
            } else {
                let padding = " ".repeat(label.len() + 2);
                blocks.push(format!("{padding}\n {label} \n{padding}"));
            }
        }
        let refs: Vec<_> = blocks.iter().map(String::as_str).collect();
        let selection = join_horizontal(0.5, &refs);
        let selection = if focused {
            join_horizontal(0.5, &["◀ ", &selection, " ▶"])
        } else {
            selection
        };
        let button = Style::new()
            .fg(Color::role(if focused { Role::Body } else { Role::Accent }))
            .render("Submit");
        format!("{selection}\n\n[ {button} ]\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dataset_navigation_clamps_and_cancel_is_not_selection() {
        let mut state = DatasetState::default();
        state.update("k");
        assert_eq!(
            state.update("enter"),
            Some(DatasetEffect::Finish(Some(DatasetKind::Logs)))
        );
        for _ in 0..5 {
            state.update("j");
        }
        assert_eq!(
            state.update("enter"),
            Some(DatasetEffect::Finish(Some(DatasetKind::Traces)))
        );
        assert_eq!(state.update("esc"), Some(DatasetEffect::Finish(None)));
        assert_eq!(state.update("ctrl+c"), Some(DatasetEffect::Finish(None)));
    }
    fn press_role(state: &mut RoleState, name: &str) -> Option<RoleEffect> {
        let bytes = match name {
            "enter" => b"\r".as_slice(),
            "alt+enter" => b"\x1b\r",
            "ctrl+c" => b"\x03",
            "esc" => b"\x1b",
            "right" => b"\x1b[C",
            "down" => b"\x1b[B",
            "shift+tab" => b"\x1b[Z",
            _ => name.as_bytes(),
        };
        let Event::Key(key) = super::super::input::parse(bytes)
            .into_iter()
            .next()
            .unwrap()
        else {
            panic!("key expected");
        };
        state.update(&key)
    }
    #[test]
    fn role_requires_submit_focus_and_ignores_escape_and_blurred_arrows() {
        let mut state = RoleState::default();
        press_role(&mut state, "right");
        press_role(&mut state, "right");
        assert_eq!(press_role(&mut state, "esc"), None);
        assert_eq!(press_role(&mut state, "enter"), None);
        press_role(&mut state, "right");
        assert_eq!(
            press_role(&mut state, "alt+enter"),
            Some(RoleEffect::Finish(Some(Privilege::Ingestor)))
        );
        assert_eq!(
            press_role(&mut state, "ctrl+c"),
            Some(RoleEffect::Finish(None))
        );
        press_role(&mut state, "shift+tab");
        for _ in 0..9 {
            press_role(&mut state, "right");
        }
        press_role(&mut state, "down");
        assert_eq!(
            press_role(&mut state, "enter"),
            Some(RoleEffect::Finish(Some(Privilege::Writer)))
        );
    }
}
