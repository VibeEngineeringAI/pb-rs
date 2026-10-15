//! Clipboard reads for the editors' Ctrl+V, matching atotto/clipboard v0.1.4
//! `ReadAll` (the bubbles `Paste` command): one helper process per request.
//!
//! The paste program is chosen once per process, like the Go package `init`.
//! Callers run `read()` as a task they own (abort on quit; `kill_on_drop`
//! stops the child) and pass `Ok` text to the focused editor's
//! `insert_paste`; errors are dropped, since bubbles stores them in an `Err`
//! field nothing displays.

use std::sync::OnceLock;

/// Go `missingCommands` text.
#[cfg(all(unix, not(target_os = "macos")))]
const MISSING: &str = "No clipboard utilities available. Please install xsel, xclip, wl-clipboard or Termux:API add-on for termux-clipboard-get/set.";

/// The selected paste program (`pasteCmdArgs`) and atotto's `trimDos` flag.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Paste {
    args: &'static [&'static str],
    trim_dos: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Backend {
    Command(Paste),
    Unsupported(&'static str),
}

fn backend() -> &'static Backend {
    static BACKEND: OnceLock<Backend> = OnceLock::new();
    BACKEND.get_or_init(|| {
        select(
            std::env::var_os("WAYLAND_DISPLAY").is_some_and(|v| !v.is_empty()),
            |name| look_path(name).is_some(),
        )
    })
}

/// clipboard_unix.go `init()` order: Wayland pair (only with WAYLAND_DISPLAY),
/// xclip, xsel, the Termux pair, then powershell.exe/clip.exe (WSL) with
/// `trimDos`; otherwise unsupported.
#[cfg(all(unix, not(target_os = "macos")))]
fn select(wayland: bool, found: impl Fn(&str) -> bool) -> Backend {
    let command = |args, trim_dos| Backend::Command(Paste { args, trim_dos });
    if wayland && found("wl-copy") && found("wl-paste") {
        return command(&["wl-paste", "--no-newline"], false);
    }
    if found("xclip") {
        return command(&["xclip", "-out", "-selection", "clipboard"], false);
    }
    if found("xsel") {
        return command(&["xsel", "--output", "--clipboard"], false);
    }
    if found("termux-clipboard-set") && found("termux-clipboard-get") {
        return command(&["termux-clipboard-get"], false);
    }
    if found("clip.exe") && found("powershell.exe") {
        return command(&["powershell.exe", "Get-Clipboard"], true);
    }
    Backend::Unsupported(MISSING)
}

/// clipboard_darwin.go: always `pbpaste`, resolved when the command runs.
#[cfg(target_os = "macos")]
fn select(_wayland: bool, _found: impl Fn(&str) -> bool) -> Backend {
    Backend::Command(Paste {
        args: &["pbpaste"],
        trim_dos: false,
    })
}

/// clipboard_windows.go reads CF_UNICODETEXT through user32/kernel32; that
/// native path is not implemented (ledger L-EDITOR-CLIP-WIN), so Ctrl+V
/// behaves like a failed read there.
#[cfg(windows)]
fn select(_wayland: bool, _found: impl Fn(&str) -> bool) -> Backend {
    Backend::Unsupported("clipboard: native Windows clipboard read is not implemented")
}

/// Go `exec.LookPath` on Unix: `$PATH` entries (empty = "."), first regular
/// file with any execute bit; a match through a relative entry is `ErrDot`
/// (an error) in Go >= 1.19, so it does not count.
#[cfg(unix)]
fn look_path(name: &str) -> Option<std::path::PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    let executable = |p: &std::path::Path| {
        std::fs::metadata(p).is_ok_and(|m| !m.is_dir() && m.permissions().mode() & 0o111 != 0)
    };
    if name.contains('/') {
        let p = std::path::PathBuf::from(name);
        return executable(p.as_path()).then_some(p);
    }
    let path = std::env::var_os("PATH").unwrap_or_default();
    for dir in std::env::split_paths(&path) {
        let dir = if dir.as_os_str().is_empty() {
            std::path::PathBuf::from(".")
        } else {
            dir
        };
        let candidate = dir.join(name);
        if executable(candidate.as_path()) {
            return candidate.is_absolute().then_some(candidate);
        }
    }
    None
}

#[cfg(not(unix))]
fn look_path(_name: &str) -> Option<std::path::PathBuf> {
    None
}

/// atotto `ReadAll`: run the paste program with stdin from /dev/null,
/// capture stdout (stderr captured and discarded, as `Cmd.Output` does),
/// fail on a non-zero exit, apply `trimDos` (drop the last two bytes when
/// longer than one byte). Invalid UTF-8 becomes U+FFFD, which the editors'
/// sanitizer drops exactly like bubbles drops `utf8.RuneError`.
pub(crate) async fn read() -> Result<String, String> {
    let paste = match backend() {
        Backend::Command(paste) => paste,
        Backend::Unsupported(message) => return Err((*message).to_string()),
    };
    let program = paste.args[0];
    let Some(resolved) = look_path(program) else {
        return Err(format!(
            "exec: \"{program}\": executable file not found in $PATH"
        ));
    };
    let mut command = tokio::process::Command::new(resolved);
    command
        .args(&paste.args[1..])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    crate::process::restore_child_limits(command.as_std_mut());
    let output = command.output().await.map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(match output.status.code() {
            Some(code) => format!("exit status {code}"),
            None => "signal: killed".to_string(),
        });
    }
    let mut bytes = output.stdout;
    if paste.trim_dos && bytes.len() > 1 {
        bytes.truncate(bytes.len() - 2);
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

#[cfg(all(test, unix, not(target_os = "macos")))]
mod tests {
    use super::*;

    fn pick(wayland: bool, present: &[&str]) -> Backend {
        select(wayland, |n| present.contains(&n))
    }

    #[test]
    fn selection_order_matches_atotto_init() {
        let all = ["wl-copy", "wl-paste", "xclip", "xsel"];
        assert_eq!(
            pick(true, &all),
            Backend::Command(Paste {
                args: &["wl-paste", "--no-newline"],
                trim_dos: false
            })
        );
        // Without WAYLAND_DISPLAY wl-clipboard is never considered.
        assert_eq!(
            pick(false, &all),
            Backend::Command(Paste {
                args: &["xclip", "-out", "-selection", "clipboard"],
                trim_dos: false
            })
        );
        // Only one of the Wayland pair: fall through to xsel.
        assert_eq!(
            pick(true, &["wl-paste", "xsel"]),
            Backend::Command(Paste {
                args: &["xsel", "--output", "--clipboard"],
                trim_dos: false
            })
        );
        assert_eq!(
            pick(false, &["termux-clipboard-get", "termux-clipboard-set"]),
            Backend::Command(Paste {
                args: &["termux-clipboard-get"],
                trim_dos: false
            })
        );
        assert_eq!(
            pick(false, &["clip.exe", "powershell.exe"]),
            Backend::Command(Paste {
                args: &["powershell.exe", "Get-Clipboard"],
                trim_dos: true
            })
        );
        assert_eq!(
            pick(false, &["powershell.exe"]),
            Backend::Unsupported(MISSING)
        );
    }
}
