//! Terminal styling for PromQL text output, matching cmd/style.go rendered
//! through lipgloss v1.0.0's default renderer and termenv v0.15.2.
//!
//! Go inspects os.Stdout (not the command's writer): the color profile comes
//! from termenv's EnvColorProfile and adaptive colors from HasDarkBackground.
//! Off a terminal with no CLICOLOR_FORCE the profile is Ascii and every
//! render is byte-identical to the plain output.

/// termenv.Profile.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Profile {
    TrueColor,
    Ansi256,
    Ansi,
    Ascii,
}

/// The renderer state Go caches in lipgloss's default renderer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Terminal {
    pub(crate) profile: Profile,
    pub(crate) dark: bool,
}

impl Terminal {
    /// Non-terminal stdout without color overrides.
    #[cfg(test)]
    pub(crate) const PLAIN: Terminal = Terminal {
        profile: Profile::Ascii,
        dark: true,
    };

    /// Detect like Go's default renderer. `stdout_terminal` is
    /// isatty(stdout); `env` returns "" for unset variables. `probe` is called
    /// exactly when termenv would send its OSC 11 background query
    /// (`ESC ] 11 ; ? ESC \` then `ESC [ 6n` on stdout) and must return the
    /// reported background RGB, or None when the query fails or times out
    /// (termenv waits up to 5s per byte).
    #[cfg(test)]
    pub(crate) fn detect(
        stdout_terminal: bool,
        env: impl Fn(&str) -> String,
        probe: impl FnOnce() -> Option<(u8, u8, u8)>,
    ) -> Terminal {
        // termenv isTTY: CI disables terminal behavior.
        let tty = stdout_terminal && env("CI").is_empty();
        Terminal {
            profile: env_profile(tty, &env),
            dark: dark_background(tty, &env, probe),
        }
    }

    /// Profile resolution is lazy, as in lipgloss. Adaptive foreground colors
    /// resolve the independently cached background inside the process render scope.
    pub(crate) fn from_process() -> Terminal {
        static PROFILE: std::sync::OnceLock<Profile> = std::sync::OnceLock::new();
        Terminal {
            profile: *PROFILE.get_or_init(|| env_profile(process_tty(), &process_env)),
            dark: true,
        }
    }
}

fn process_env(key: &str) -> String {
    std::env::var_os(key)
        .map(|v| v.to_string_lossy().into_owned())
        .unwrap_or_default()
}
fn process_tty() -> bool {
    use std::io::IsTerminal;
    std::io::stdout().is_terminal() && process_env("CI").is_empty()
}
static BACKGROUND: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
fn process_dark() -> bool {
    *BACKGROUND.get_or_init(|| dark_background(process_tty(), &process_env, probe_background))
}
/// bubbletea v1.2.2's tea_init.go primes lipgloss's background cache before
/// pkg/ui.Active = LoadTheme(). Thus even JSON, NO_COLOR, and explicit themes
/// perform this initialization, subject to termenv's terminal guards.
pub(crate) fn initialize_process_theme() {
    let _ = process_dark();
}
thread_local! { static PROCESS_RENDER: std::cell::Cell<bool> = const { std::cell::Cell::new(false) }; }
pub(crate) fn with_process_render<T>(f: impl FnOnce() -> T) -> T {
    struct Reset(bool);
    impl Drop for Reset {
        fn drop(&mut self) {
            PROCESS_RENDER.set(self.0);
        }
    }
    let _reset = Reset(PROCESS_RENDER.replace(true));
    f()
}

#[cfg(unix)]
fn probe_background() -> Option<(u8, u8, u8)> {
    const FD: libc::c_int = libc::STDOUT_FILENO;
    // The pinned safe termenv implementation only controls a foreground TTY.
    unsafe {
        if libc::tcgetpgrp(FD) != libc::getpgrp() {
            return None;
        }
        let mut original = std::mem::MaybeUninit::<libc::termios>::uninit();
        if libc::tcgetattr(FD, original.as_mut_ptr()) != 0 {
            return None;
        }
        let original = original.assume_init();
        struct Restore(libc::termios);
        impl Drop for Restore {
            fn drop(&mut self) {
                unsafe {
                    libc::tcsetattr(libc::STDOUT_FILENO, libc::TCSANOW, &self.0);
                }
            }
        }
        let _restore = Restore(original);
        let mut noecho = original;
        noecho.c_lflag &= !(libc::ECHO | libc::ICANON);
        if libc::tcsetattr(FD, libc::TCSANOW, &noecho) != 0 {
            return None;
        }
        for request in [b"\x1b]11;?\x1b\\".as_slice(), b"\x1b[6n".as_slice()] {
            let mut remaining = request;
            while !remaining.is_empty() {
                let count = libc::write(FD, remaining.as_ptr().cast(), remaining.len());
                if count <= 0 {
                    break;
                } // Go ignores write errors, then attempts to read.
                remaining = &remaining[count as usize..];
            }
        }
        let (response, osc) = read_response(read_terminal_byte)?;
        if !osc {
            return None;
        }
        read_response(read_terminal_byte)?; // Consume the cursor response, including ST's trailing slash.
        parse_background(&response)
    }
}
#[cfg(not(unix))]
fn probe_background() -> Option<(u8, u8, u8)> {
    None
}
#[cfg(unix)]
fn read_terminal_byte() -> Option<u8> {
    unsafe {
        let mut fds = std::mem::zeroed::<libc::fd_set>();
        libc::FD_SET(libc::STDOUT_FILENO, &mut fds);
        let mut timeout = libc::timeval {
            tv_sec: 5,
            tv_usec: 0,
        };
        loop {
            let n = libc::select(
                libc::STDOUT_FILENO + 1,
                &mut fds,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut timeout,
            );
            if n < 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            if n <= 0 {
                return None;
            }
            break;
        }
        let mut byte = 0;
        if libc::read(libc::STDOUT_FILENO, (&mut byte as *mut u8).cast(), 1) != 1 {
            return None;
        }
        Some(byte)
    }
}
fn read_response(mut next: impl FnMut() -> Option<u8>) -> Option<(Vec<u8>, bool)> {
    while next()? != 0x1b {}
    let kind = next()?;
    let osc = match kind {
        b']' => true,
        b'[' => false,
        _ => return None,
    };
    let mut response = vec![0x1b, kind];
    loop {
        let byte = next()?;
        response.push(byte);
        if (osc && matches!(byte, 7 | 0x1b)) || (!osc && byte == b'R') {
            return Some((response, osc));
        }
        if response.len() > 25 {
            return None;
        }
    }
}
fn parse_background(response: &[u8]) -> Option<(u8, u8, u8)> {
    if !(24..=25).contains(&response.len()) {
        return None;
    }
    let response = response
        .strip_suffix(&[7])
        .or_else(|| response.strip_suffix(&[0x1b]))
        .or_else(|| response.strip_suffix(b"\x1b\\"))?;
    let rgb = response.get(4..)?.strip_prefix(b";rgb:")?;
    let mut components = rgb.split(|b| *b == b'/');
    let mut component =
        || u8::from_str_radix(std::str::from_utf8(components.next()?.get(..2)?).ok()?, 16).ok();
    Some((component()?, component()?, component()?))
}

/// Output.EnvColorProfile.
fn env_profile(tty: bool, env: &impl Fn(&str) -> String) -> Profile {
    let forced = {
        let value = env("CLICOLOR_FORCE");
        !value.is_empty() && value != "0"
    };
    if !env("NO_COLOR").is_empty() || (env("CLICOLOR") == "0" && !forced) {
        return Profile::Ascii;
    }
    let profile = color_profile(tty, env);
    if forced && profile == Profile::Ascii {
        Profile::Ansi
    } else {
        profile
    }
}

/// Output.ColorProfile (termenv_unix.go).
fn color_profile(tty: bool, env: &impl Fn(&str) -> String) -> Profile {
    if !tty {
        return Profile::Ascii;
    }
    if env("GOOGLE_CLOUD_SHELL") == "true" {
        return Profile::TrueColor;
    }
    let term = env("TERM");
    match env("COLORTERM").to_lowercase().as_str() {
        "24bit" | "truecolor" => {
            if term.starts_with("screen") && env("TERM_PROGRAM") != "tmux" {
                return Profile::Ansi256;
            }
            return Profile::TrueColor;
        }
        "yes" | "true" => return Profile::Ansi256,
        _ => (),
    }
    match term.as_str() {
        "xterm-kitty" | "wezterm" => Profile::TrueColor,
        "linux" => Profile::Ansi,
        _ if term.contains("256color") => Profile::Ansi256,
        _ if term.contains("color") || term.contains("ansi") => Profile::Ansi,
        _ => Profile::Ascii,
    }
}

/// Output.HasDarkBackground: HSL lightness below one half.
fn dark_background(
    tty: bool,
    env: &impl Fn(&str) -> String,
    probe: impl FnOnce() -> Option<(u8, u8, u8)>,
) -> bool {
    // Off a terminal the background is NoColor, which converts to black.
    if !tty {
        return true;
    }
    let term = env("TERM");
    if !(term.starts_with("screen") || term.starts_with("tmux"))
        && let Some(rgb) = probe()
    {
        return dark(rgb);
    }
    let fgbg = env("COLORFGBG");
    if fgbg.contains(';') {
        // strconv.Atoi of the last field; Go indexes ansiHex with it and
        // panics outside 0..=255, which is not reproduced (treated as black).
        if let Ok(index) = fgbg.rsplit(';').next().unwrap_or("").parse::<i64>() {
            return u8::try_from(index).map_or(true, |index| dark(ansi_rgb(index)));
        }
    }
    true
}

fn dark((r, g, b): (u8, u8, u8)) -> bool {
    let unit = |v: u8| f64::from(v) * (1.0 / 255.0);
    let (r, g, b) = (unit(r), unit(g), unit(b));
    (r.max(g).max(b) + r.min(g).min(b)) / 2.0 < 0.5
}

/// termenv ansiHex: xterm system colors, 6x6x6 cube, then the gray ramp.
fn ansi_rgb(index: u8) -> (u8, u8, u8) {
    const SYSTEM: [(u8, u8, u8); 16] = [
        (0x00, 0x00, 0x00),
        (0x80, 0x00, 0x00),
        (0x00, 0x80, 0x00),
        (0x80, 0x80, 0x00),
        (0x00, 0x00, 0x80),
        (0x80, 0x00, 0x80),
        (0x00, 0x80, 0x80),
        (0xc0, 0xc0, 0xc0),
        (0x80, 0x80, 0x80),
        (0xff, 0x00, 0x00),
        (0x00, 0xff, 0x00),
        (0xff, 0xff, 0x00),
        (0x00, 0x00, 0xff),
        (0xff, 0x00, 0xff),
        (0x00, 0xff, 0xff),
        (0xff, 0xff, 0xff),
    ];
    const LEVELS: [u8; 6] = [0x00, 0x5f, 0x87, 0xaf, 0xd7, 0xff];
    match index {
        0..=15 => SYSTEM[usize::from(index)],
        16..=231 => {
            let i = usize::from(index - 16);
            (LEVELS[i / 36], LEVELS[i / 6 % 6], LEVELS[i % 6])
        }
        _ => {
            let v = 8 + 10 * (index - 232);
            (v, v, v)
        }
    }
}

/// The ui.Palette roles cmd/style.go uses for PromQL output.
#[derive(Clone, Copy)]
enum Role {
    /// FocusPrimary: Accent #9E9EF0 dark / #3A3A8C light.
    Accent,
    /// StandardPrimary: Body #E4E4E7 dark / #18181B light.
    Body,
    /// StandardSecondary: Mute #B5B5BE dark / #3F3F46 light.
    Mute,
    /// StandardStyleRule: Border #4A4A5C dark / #D1D5DB light.
    Border,
}

/// Foreground SGR parameters for `role`. termenv degrades hex colors with
/// hexToANSI256Color/ansi256ToANSIColor (HSLuv distances); the values below
/// were produced by the pinned termenv v0.15.2 for this palette. TrueColor
/// components are uint8(float(c)/255*255), so 0xE4 and 0x4A truncate to
/// 227 and 73.
fn foreground(role: Role, terminal: &Terminal) -> Option<&'static str> {
    use Profile::*;
    use Role::*;
    Some(match (terminal.profile, terminal.dark, role) {
        (Ascii, _, _) => return None,
        (TrueColor, true, Accent) => "38;2;158;158;240",
        (TrueColor, true, Body) => "38;2;227;227;231",
        (TrueColor, true, Mute) => "38;2;181;181;190",
        (TrueColor, true, Border) => "38;2;73;73;92",
        (TrueColor, false, Accent) => "38;2;58;58;140",
        (TrueColor, false, Body) => "38;2;24;24;27",
        (TrueColor, false, Mute) => "38;2;63;63;70",
        (TrueColor, false, Border) => "38;2;209;213;219",
        (Ansi256, true, Accent) => "38;5;147",
        (Ansi256, true, Body) => "38;5;188",
        (Ansi256, true, Mute) => "38;5;145",
        (Ansi256, true, Border) => "38;5;59",
        (Ansi256, false, Accent) => "38;5;60",
        (Ansi256, false, Body) => "38;5;232",
        (Ansi256, false, Mute) => "38;5;59",
        (Ansi256, false, Border) => "38;5;188",
        (Ansi, _, Accent) => "94",
        (Ansi, true, Body) => "37",
        (Ansi, true, Mute) => "37",
        (Ansi, true, Border) => "90",
        (Ansi, false, Body) => "30",
        (Ansi, false, Mute) => "90",
        (Ansi, false, Border) => "37",
    })
}

/// A lipgloss.Style restricted to the properties cmd/style.go sets.
#[derive(Clone, Copy)]
pub(crate) struct Style {
    bold: bool,
    fg: Option<Role>,
    left: usize,
}

/// StyleBold: bold without a foreground color.
pub(crate) const BOLD: Style = Style {
    bold: true,
    fg: None,
    left: 0,
};

/// ItemOuter: PaddingLeft(1), no colors.
pub(super) const ITEM_OUTER: Style = Style {
    bold: false,
    fg: None,
    left: 1,
};
/// SelectedStyle (and SelectedStyle.Bold(true)): bold accent.
pub(crate) const SELECTED: Style = Style {
    bold: true,
    fg: Some(Role::Accent),
    left: 0,
};
/// StandardStyle: body foreground.
pub(crate) const STANDARD: Style = Style {
    bold: false,
    fg: Some(Role::Body),
    left: 0,
};
/// StandardStyleRule: border foreground (rules and dot leaders).
pub(crate) const RULE: Style = Style {
    bold: false,
    fg: Some(Role::Border),
    left: 0,
};

/// StandardStyleAlt: muted foreground.
pub(crate) const MUTED: Style = Style {
    bold: false,
    fg: Some(Role::Mute),
    left: 0,
};

impl Style {
    /// Style.Render: tabs become four spaces, CRLF becomes LF, each line is
    /// wrapped in the SGR sequence (empty lines included), left padding is
    /// unstyled, and multi-line text is padded with plain spaces to its
    /// widest line measured by `line_width` (ansi.StringWidth).
    pub(crate) fn render(
        &self,
        text: &str,
        terminal: &Terminal,
        line_width: fn(&str) -> usize,
    ) -> String {
        String::from_utf8(self.render_raw(text.as_bytes(), terminal, |bytes| {
            line_width(std::str::from_utf8(bytes).expect("valid styled text"))
        }))
        .expect("styling preserves UTF-8")
    }

    pub(crate) fn render_bytes(&self, text: &[u8], terminal: &Terminal) -> Vec<u8> {
        self.render_raw(text, terminal, super::line_width_bytes)
    }

    fn render_raw(
        &self,
        text: &[u8],
        terminal: &Terminal,
        line_width: impl Fn(&[u8]) -> usize,
    ) -> Vec<u8> {
        let mut resolved = *terminal;
        if self.fg.is_some() && PROCESS_RENDER.get() {
            resolved.dark = process_dark();
        }
        let terminal = &resolved;
        let mut params = Vec::new();
        if self.bold {
            params.push("1");
        }
        if let Some(sequence) = self.fg.and_then(|role| foreground(role, terminal)) {
            params.push(sequence);
        }
        let sgr = (terminal.profile != Profile::Ascii && !params.is_empty())
            .then(|| format!("\x1b[{}m", params.join(";")));
        let mut expanded = Vec::with_capacity(text.len());
        let mut index = 0;
        while index < text.len() {
            match text[index] {
                b'\t' => expanded.extend_from_slice(b"    "),
                b'\r' if text.get(index + 1) == Some(&b'\n') => {}
                byte => expanded.push(byte),
            }
            index += 1;
        }
        let lines: Vec<Vec<u8>> = expanded
            .split(|byte| *byte == b'\n')
            .map(|line| {
                let mut styled = vec![b' '; self.left];
                if let Some(sgr) = &sgr {
                    styled.extend_from_slice(sgr.as_bytes());
                }
                styled.extend_from_slice(line);
                if sgr.is_some() {
                    styled.extend_from_slice(b"\x1b[0m");
                }
                styled
            })
            .collect();
        if lines.len() == 1 {
            return lines.into_iter().next().unwrap_or_default();
        }
        let widest = lines.iter().map(|line| line_width(line)).max().unwrap_or(0);
        let mut output = Vec::new();
        for (index, line) in lines.iter().enumerate() {
            if index != 0 {
                output.push(b'\n');
            }
            output.extend_from_slice(line);
            output.extend(std::iter::repeat_n(b' ', widest - line_width(line)));
        }
        output
    }
}

#[cfg(test)]
#[path = "style_tests.rs"]
mod tests;

#[cfg(test)]
mod probe_tests {
    use super::*;
    #[test]
    fn responses_follow_pinned_osc_and_cursor_framing() {
        let mut bytes = b"noise\x1b]11;rgb:ffff/8080/0000\x1b\\\x1b[42;1R"
            .iter()
            .copied();
        let (response, osc) = read_response(|| bytes.next()).unwrap();
        assert!(osc);
        assert_eq!(parse_background(&response), Some((255, 128, 0)));
        let (cursor, osc) = read_response(|| bytes.next()).unwrap();
        assert!(!osc);
        assert_eq!(cursor, b"\x1b[42;1R");
        assert_eq!(
            parse_background(b"\x1b]11;rgb:0000/0000/0000\x07"),
            Some((0, 0, 0))
        );
        assert_eq!(parse_background(b"\x1b]11;rgb:ff/ff/ff\x07"), None);
        assert_eq!(read_response(|| None), None);
    }
}
