//! Clipboard reads for the editors' Ctrl+V, matching atotto/clipboard v0.1.4
//! `ReadAll` (the bubbles `Paste` command): one helper process per request
//! on Unix, the user32/kernel32 CF_UNICODETEXT sequence on Windows.
//! Windows intentionally fixes v0.1.4's final GlobalUnlock handling: zero
//! with NO_ERROR is success, so real clipboard text reaches the editor.
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
#[cfg(unix)]
#[derive(Clone, Debug, PartialEq, Eq)]
struct Paste {
    args: &'static [&'static str],
    trim_dos: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Backend {
    #[cfg(unix)]
    Command(Paste),
    #[cfg(all(unix, not(target_os = "macos")))]
    Unsupported(&'static str),
    /// clipboard_windows.go `readAll`.
    #[cfg(windows)]
    Native,
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

/// clipboard_windows.go has no program to select.
#[cfg(windows)]
fn select(_wayland: bool, _found: impl Fn(&str) -> bool) -> Backend {
    Backend::Native
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

/// atotto `ReadAll`.
pub(crate) async fn read() -> Result<String, String> {
    match backend() {
        #[cfg(unix)]
        Backend::Command(paste) => read_command(paste).await,
        #[cfg(all(unix, not(target_os = "macos")))]
        Backend::Unsupported(message) => Err((*message).to_string()),
        // Go locks the OS thread so OpenClipboard and CloseClipboard run on
        // the same one; a blocking-pool closure gives the same guarantee. It
        // cannot be aborted, but it ends within the one-second open wait.
        #[cfg(windows)]
        Backend::Native => {
            tokio::task::spawn_blocking(|| win32::read_all(&mut win32::User32, win32::OPEN_TIMEOUT))
                .await
                .map_err(|e| e.to_string())?
        }
    }
}

/// Run the paste program with stdin from /dev/null, capture stdout (stderr
/// captured and discarded, as `Cmd.Output` does), fail on a non-zero exit,
/// apply `trimDos` (drop the last two bytes when longer than one byte).
/// Invalid UTF-8 becomes U+FFFD, which the editors' sanitizer drops exactly
/// like bubbles drops `utf8.RuneError`.
#[cfg(unix)]
async fn read_command(paste: &Paste) -> Result<String, String> {
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

/// clipboard_windows.go `readAll`. The call sequence is generic over the
/// Win32 surface so its error and cleanup paths are tested on every host;
/// only `User32` touches the real clipboard.
#[cfg(any(windows, test))]
mod win32 {
    use std::time::{Duration, Instant};

    /// `waitOpenClipboard`'s limit.
    #[cfg(windows)]
    pub(super) const OPEN_TIMEOUT: Duration = Duration::from_secs(1);

    /// Go views the locked memory as `*[1 << 20]uint16` before
    /// `syscall.UTF16ToString`, so longer text is cut at this many units.
    pub(super) const MAX_UNITS: usize = 1 << 20;

    /// One clipboard data handle (`HANDLE` as Go's uintptr).
    pub(super) type Handle = usize;

    /// The Win32 calls `readAll` makes. Each failure carries the
    /// GetLastError text Go's `Proc.Call` returns beside the result.
    pub(super) trait Api {
        /// IsClipboardFormatAvailable(CF_UNICODETEXT) != 0.
        fn format_available(&mut self) -> Result<(), String>;
        /// OpenClipboard(0) != 0.
        fn open(&mut self) -> Result<(), String>;
        /// GetClipboardData(CF_UNICODETEXT) != 0.
        fn data(&mut self) -> Result<Handle, String>;
        /// GlobalLock != 0, then UTF16ToString over the locked units.
        fn lock_text(&mut self, handle: Handle) -> Result<String, String>;
        /// GlobalUnlock succeeds on a nonzero result OR zero with NO_ERROR.
        /// This deliberately fixes atotto v0.1.4's last-unlock error.
        fn unlock(&mut self, handle: Handle) -> Result<(), String>;
        /// CloseClipboard != 0.
        fn close(&mut self) -> Result<(), String>;
    }

    /// `readAll`'s order and cleanup: no CloseClipboard when the format check
    /// or the open fails, a discarded CloseClipboard after a later failure.
    pub(super) fn read_all(api: &mut impl Api, open_timeout: Duration) -> Result<String, String> {
        api.format_available()?;
        wait_open(api, open_timeout)?;
        let text = api
            .data()
            .and_then(|handle| Ok((handle, api.lock_text(handle)?)))
            .and_then(|(handle, text)| api.unlock(handle).map(|()| text));
        match text {
            Ok(text) => api.close().map(|()| text),
            Err(error) => {
                let _ = api.close();
                Err(error)
            }
        }
    }

    /// `waitOpenClipboard`: retry every millisecond until the limit, then
    /// return the last attempt's error.
    fn wait_open(api: &mut impl Api, timeout: Duration) -> Result<(), String> {
        let limit = Instant::now() + timeout;
        loop {
            let error = match api.open() {
                Ok(()) => return Ok(()),
                Err(error) => error,
            };
            std::thread::sleep(Duration::from_millis(1));
            if Instant::now() >= limit {
                return Err(error);
            }
        }
    }

    /// `syscall.UTF16ToString` over at most `MAX_UNITS` units: stop at the
    /// first NUL; unpaired surrogates (including a pair split by the cut)
    /// become U+FFFD.
    pub(super) fn decode(units: &[u16]) -> String {
        let units = &units[..units.len().min(MAX_UNITS)];
        let end = units.iter().position(|&u| u == 0).unwrap_or(units.len());
        String::from_utf16_lossy(&units[..end])
    }

    pub(super) fn unlock_succeeded(result: i32, error: u32) -> bool {
        result != 0 || error == 0
    }

    #[cfg(windows)]
    pub(super) struct User32;

    #[cfg(windows)]
    mod ffi {
        use std::ffi::c_void;

        pub(super) const CF_UNICODETEXT: u32 = 13;

        #[link(name = "user32")]
        unsafe extern "system" {
            pub(super) fn IsClipboardFormatAvailable(format: u32) -> i32;
            pub(super) fn OpenClipboard(owner: *mut c_void) -> i32;
            pub(super) fn CloseClipboard() -> i32;
            pub(super) fn GetClipboardData(format: u32) -> *mut c_void;
        }

        #[link(name = "kernel32")]
        unsafe extern "system" {
            pub(super) fn GlobalLock(memory: *mut c_void) -> *mut c_void;
            pub(super) fn GlobalUnlock(memory: *mut c_void) -> i32;
            pub(super) fn GlobalSize(memory: *mut c_void) -> usize;
            pub(super) fn SetLastError(error: u32);
            pub(super) fn GetLastError() -> u32;
        }
    }

    /// Go's `syscall.Errno` text for GetLastError (FormatMessage, without
    /// Rust's " (os error N)" suffix).
    #[cfg(windows)]
    fn last_error() -> String {
        let text = std::io::Error::last_os_error().to_string();
        text.split(" (os error ")
            .next()
            .unwrap_or(&text)
            .to_string()
    }

    #[cfg(windows)]
    fn check(result: i32) -> Result<(), String> {
        if result == 0 {
            Err(last_error())
        } else {
            Ok(())
        }
    }

    #[cfg(windows)]
    impl Api for User32 {
        fn format_available(&mut self) -> Result<(), String> {
            // SAFETY: plain Win32 call with a constant format.
            check(unsafe { ffi::IsClipboardFormatAvailable(ffi::CF_UNICODETEXT) })
        }

        fn open(&mut self) -> Result<(), String> {
            // SAFETY: a null owner associates the clipboard with this task.
            check(unsafe { ffi::OpenClipboard(std::ptr::null_mut()) })
        }

        fn data(&mut self) -> Result<Handle, String> {
            // SAFETY: the clipboard is open on this thread.
            let handle = unsafe { ffi::GetClipboardData(ffi::CF_UNICODETEXT) };
            if handle.is_null() {
                Err(last_error())
            } else {
                Ok(handle as Handle)
            }
        }

        fn lock_text(&mut self, handle: Handle) -> Result<String, String> {
            let memory = handle as *mut std::ffi::c_void;
            // SAFETY: handle is the open clipboard's CF_UNICODETEXT memory.
            let units = unsafe { ffi::GlobalLock(memory) };
            if units.is_null() {
                return Err(last_error());
            }
            // Go reads up to the NUL without consulting the allocation size;
            // the scan is additionally bounded by it here.
            // SAFETY: the locked block holds GlobalSize bytes of u16 units.
            let length = unsafe { ffi::GlobalSize(memory) } / 2;
            let units =
                unsafe { std::slice::from_raw_parts(units.cast::<u16>(), length.min(MAX_UNITS)) };
            Ok(decode(units))
        }

        fn unlock(&mut self, handle: Handle) -> Result<(), String> {
            // SAFETY: handle was locked by lock_text. Reset the error so a
            // successful final unlock cannot be confused with a stale failure.
            let (result, error) = unsafe {
                ffi::SetLastError(0);
                let result = ffi::GlobalUnlock(handle as *mut std::ffi::c_void);
                (result, ffi::GetLastError())
            };
            if unlock_succeeded(result, error) {
                Ok(())
            } else {
                Err(last_error())
            }
        }

        fn close(&mut self) -> Result<(), String> {
            // SAFETY: the clipboard was opened on this thread.
            check(unsafe { ffi::CloseClipboard() })
        }
    }
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

#[cfg(test)]
mod win32_tests {
    use super::win32::{Api, Handle, MAX_UNITS, decode, read_all};
    use std::time::{Duration, Instant};

    /// Scripted Win32 surface recording the calls `readAll` makes.
    #[derive(Default)]
    struct Fake {
        calls: Vec<&'static str>,
        no_format: bool,
        open_failures: usize,
        no_data: bool,
        no_lock: bool,
        /// Documented GlobalUnlock on the last lock: 0 with NO_ERROR.
        unlock_zero: bool,
        unlock_error: u32,
        close_fails: bool,
        units: Vec<u16>,
    }

    impl Api for Fake {
        fn format_available(&mut self) -> Result<(), String> {
            self.calls.push("format");
            if self.no_format {
                Err("format".into())
            } else {
                Ok(())
            }
        }

        fn open(&mut self) -> Result<(), String> {
            self.calls.push("open");
            if self.open_failures == 0 {
                return Ok(());
            }
            self.open_failures -= 1;
            Err(format!("busy {}", self.open_failures))
        }

        fn data(&mut self) -> Result<Handle, String> {
            self.calls.push("data");
            if self.no_data {
                Err("data".into())
            } else {
                Ok(7)
            }
        }

        fn lock_text(&mut self, handle: Handle) -> Result<String, String> {
            assert_eq!(handle, 7);
            self.calls.push("lock");
            if self.no_lock {
                Err("lock".into())
            } else {
                Ok(decode(&self.units))
            }
        }

        fn unlock(&mut self, handle: Handle) -> Result<(), String> {
            assert_eq!(handle, 7);
            self.calls.push("unlock");
            if super::win32::unlock_succeeded(
                if self.unlock_zero { 0 } else { 1 },
                self.unlock_error,
            ) {
                Ok(())
            } else {
                Err("unlock".into())
            }
        }

        fn close(&mut self) -> Result<(), String> {
            self.calls.push("close");
            if self.close_fails {
                Err("close".into())
            } else {
                Ok(())
            }
        }
    }

    fn utf16(text: &str) -> Vec<u16> {
        text.encode_utf16().chain([0]).collect()
    }

    fn run(fake: &mut Fake) -> Result<String, String> {
        read_all(fake, Duration::from_millis(200))
    }

    #[test]
    fn successful_read_follows_go_order_and_closes_once() {
        let mut fake = Fake {
            units: utf16("a\r\nb 𝄞"),
            ..Fake::default()
        };
        assert_eq!(run(&mut fake), Ok("a\r\nb 𝄞".into()));
        assert_eq!(
            fake.calls,
            ["format", "open", "data", "lock", "unlock", "close"]
        );
    }

    #[test]
    fn failures_before_open_never_close() {
        let mut fake = Fake {
            no_format: true,
            ..Fake::default()
        };
        assert_eq!(run(&mut fake), Err("format".into()));
        assert_eq!(fake.calls, ["format"]);
        let mut fake = Fake {
            open_failures: usize::MAX,
            ..Fake::default()
        };
        let started = Instant::now();
        let error = run(&mut fake).unwrap_err();
        assert!(started.elapsed() >= Duration::from_millis(200));
        // The last attempt's error, not the first.
        assert_ne!(error, format!("busy {}", usize::MAX - 1));
        assert!(!fake.calls.contains(&"close"));
        assert!(!fake.calls.contains(&"data"));
    }

    #[test]
    fn open_is_retried_while_another_owner_holds_the_clipboard() {
        let mut fake = Fake {
            open_failures: 3,
            units: utf16("x"),
            ..Fake::default()
        };
        assert_eq!(run(&mut fake), Ok("x".into()));
        assert_eq!(fake.calls.iter().filter(|c| **c == "open").count(), 4);
    }

    #[test]
    fn final_unlock_zero_without_error_returns_clipboard_text() {
        let mut fake = Fake {
            unlock_zero: true,
            units: utf16("native paste"),
            ..Fake::default()
        };
        assert_eq!(run(&mut fake), Ok("native paste".into()));
        assert_eq!(
            fake.calls,
            ["format", "open", "data", "lock", "unlock", "close"]
        );
        assert!(super::win32::unlock_succeeded(1, 6));
        assert!(!super::win32::unlock_succeeded(0, 6));
    }

    #[test]
    fn later_failures_close_and_return_the_failing_call_error() {
        for (fake, error, last) in [
            (
                Fake {
                    no_data: true,
                    ..Fake::default()
                },
                "data",
                "data",
            ),
            (
                Fake {
                    no_lock: true,
                    ..Fake::default()
                },
                "lock",
                "lock",
            ),
            (
                Fake {
                    unlock_zero: true,
                    unlock_error: 6,
                    units: utf16("discarded"),
                    ..Fake::default()
                },
                "unlock",
                "unlock",
            ),
        ] {
            let mut fake = Fake {
                close_fails: true,
                ..fake
            };
            // A failing cleanup close is discarded, as in Go.
            assert_eq!(run(&mut fake), Err(error.into()));
            assert_eq!(&fake.calls[fake.calls.len() - 2..], [last, "close"]);
        }
        let mut fake = Fake {
            close_fails: true,
            units: utf16("text"),
            ..Fake::default()
        };
        assert_eq!(run(&mut fake), Err("close".into()));
    }

    #[test]
    fn decode_matches_utf16_to_string_over_go_view() {
        assert_eq!(decode(&utf16("")), "");
        assert_eq!(decode(&[0x61, 0, 0x62]), "a");
        // No NUL inside the view: everything in it.
        assert_eq!(decode(&[0x61, 0x62]), "ab");
        // Unpaired surrogates each become U+FFFD; a valid pair decodes.
        assert_eq!(
            decode(&[0xd800, 0x61, 0xdc00, 0xd834, 0xdd1e, 0]),
            "\u{fffd}a\u{fffd}\u{1d11e}"
        );
        // The 1 << 20 view cuts long text, splitting a pair at the boundary.
        let mut long = vec![0x61u16; MAX_UNITS - 1];
        long.extend([0xd834, 0xdd1e, 0x62, 0]);
        let decoded = decode(&long);
        assert_eq!(decoded.chars().count(), MAX_UNITS);
        assert!(decoded.ends_with("a\u{fffd}"));
    }
}

/// Native Windows: a real clipboard round trip, opt-in because it needs an
/// interactive window station and replaces the user's clipboard. CI also
/// compares the result with atotto v0.1.4 when `PB_GO_CLIPBOARD_ORACLE`
/// names a binary printing `ok:<text>` or `err:<message>`. Only Go's known
/// final-unlock NO_ERROR failure may differ. Native execution is still required
/// to establish platform behavior; Linux mocks alone do not prove paste.
#[cfg(all(test, windows))]
mod native_tests {
    use std::ffi::c_void;

    #[link(name = "user32")]
    unsafe extern "system" {
        fn CreateWindowExW(
            ex_style: u32,
            class: *const u16,
            title: *const u16,
            style: u32,
            x: i32,
            y: i32,
            width: i32,
            height: i32,
            parent: *mut c_void,
            menu: *mut c_void,
            instance: *mut c_void,
            parameter: *mut c_void,
        ) -> *mut c_void;
        fn DestroyWindow(window: *mut c_void) -> i32;
        fn OpenClipboard(owner: *mut c_void) -> i32;
        fn EmptyClipboard() -> i32;
        fn SetClipboardData(format: u32, memory: *mut c_void) -> *mut c_void;
        fn CloseClipboard() -> i32;
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GlobalAlloc(flags: u32, bytes: usize) -> *mut c_void;
        fn GlobalLock(memory: *mut c_void) -> *mut c_void;
        fn GlobalUnlock(memory: *mut c_void) -> i32;
        fn GlobalFree(memory: *mut c_void) -> *mut c_void;
        fn SetLastError(error: u32);
        fn GetLastError() -> u32;
    }

    struct Window(*mut c_void);
    impl Drop for Window {
        fn drop(&mut self) {
            // SAFETY: this thread created and owns the hidden window.
            unsafe {
                DestroyWindow(self.0);
            }
        }
    }

    struct Open(bool);
    impl Open {
        fn close(mut self) {
            // SAFETY: this guard owns the thread's open clipboard.
            let closed = unsafe { CloseClipboard() };
            self.0 = false;
            assert_ne!(
                closed,
                0,
                "CloseClipboard: {}",
                std::io::Error::last_os_error()
            );
        }
    }
    impl Drop for Open {
        fn drop(&mut self) {
            if self.0 {
                // SAFETY: constructed only after this thread opens the clipboard.
                unsafe {
                    CloseClipboard();
                }
            }
        }
    }

    struct Memory(*mut c_void);
    impl Drop for Memory {
        fn drop(&mut self) {
            if !self.0.is_null() {
                // SAFETY: untransferred allocation owned by this helper.
                unsafe {
                    GlobalFree(self.0);
                }
            }
        }
    }

    /// atotto `writeAll`'s sequence: GMEM_MOVEABLE, NUL-terminated UTF-16.
    fn write(text: &str) -> Window {
        let units: Vec<u16> = text.encode_utf16().chain([0]).collect();
        // SAFETY: standard clipboard ownership transfer; every result is
        // checked and the allocation holds `units`.
        unsafe {
            // A hidden built-in window supplies a valid clipboard owner.
            // OpenClipboard(NULL) + EmptyClipboard has no valid owner under
            // the documented SetClipboardData contract.
            let class = [83_u16, 84, 65, 84, 73, 67, 0]; // STATIC
            let window = Window(CreateWindowExW(
                0,
                class.as_ptr(),
                class.as_ptr(),
                0,
                0,
                0,
                0,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            ));
            assert!(
                !window.0.is_null(),
                "CreateWindowExW: {}",
                std::io::Error::last_os_error()
            );
            let mut opened = false;
            for _ in 0..1000 {
                if OpenClipboard(window.0) != 0 {
                    opened = true;
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            assert!(opened, "OpenClipboard: {}", std::io::Error::last_os_error());
            let open = Open(true);
            assert_ne!(EmptyClipboard(), 0);
            let mut memory = Memory(GlobalAlloc(0x0002, units.len() * 2));
            assert!(!memory.0.is_null());
            let target = GlobalLock(memory.0).cast::<u16>();
            assert!(!target.is_null());
            std::ptr::copy_nonoverlapping(units.as_ptr(), target, units.len());
            SetLastError(0);
            let unlocked = GlobalUnlock(memory.0);
            assert!(unlocked != 0 || GetLastError() == 0);
            assert!(!SetClipboardData(13, memory.0).is_null());
            memory.0 = std::ptr::null_mut(); // ownership transferred
            open.close();
            window
        }
    }

    /// Independently establish that the real fixture contains the expected
    /// UTF-16 text, accepting Win32's successful final unlock. This fixture
    /// assertion is separate from the production reader's result and oracle.
    fn fixture_text() -> String {
        use super::win32::Api;
        let mut api = super::win32::User32;
        api.format_available().unwrap();
        api.open().unwrap();
        let open = Open(true);
        let handle = api.data().unwrap();
        let text = api.lock_text(handle).unwrap();
        // SAFETY: the handle was locked above on this thread. Unlike the
        // pinned oracle, accept a zero return when GetLastError is NO_ERROR.
        unsafe {
            SetLastError(0);
            let unlocked = GlobalUnlock(handle as *mut c_void);
            assert!(unlocked != 0 || GetLastError() == 0);
        }
        open.close();
        text
    }

    #[test]
    #[ignore = "replaces the clipboard; run on a native Windows desktop session"]
    fn native_read_matches_atotto() {
        let sample = "pb paste \u{e9}\u{1d11e}\r\nline 2";
        let _owner = write(sample);
        assert_eq!(fixture_text(), sample);
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let rust = runtime.block_on(super::read());
        println!("rust: {rust:?}");
        assert_eq!(rust, Ok(sample.to_string()));
        let Some(oracle) = std::env::var_os("PB_GO_CLIPBOARD_ORACLE") else {
            return;
        };
        let output = std::process::Command::new(oracle).output().unwrap();
        assert!(output.status.success(), "{output:?}");
        let go = String::from_utf8(output.stdout).unwrap();
        println!("go: {go:?}");
        match (go.strip_prefix("ok:"), go.strip_prefix("err:")) {
            (Some(text), _) => assert_eq!(rust, Ok(text.to_string())),
            (_, Some(message)) => {
                // Only the pinned oracle's zero/NO_ERROR unlock bug is allowed.
                // Any format/open/lock/close failure remains a test failure.
                let zero_error = std::io::Error::from_raw_os_error(0).to_string();
                let zero_error = zero_error.split(" (os error ").next().unwrap();
                assert_eq!(message, zero_error, "unexpected Go clipboard failure");
                println!("intentional difference: Go v0.1.4 rejects successful final GlobalUnlock");
            }
            _ => panic!("unexpected oracle output {go:?}"),
        }
    }
}
