//! Shared presentation primitives for the phase-7 interactive flows.
//!
//! This reproduces the pinned Go presentation stack used by the TUIs:
//! pkg/ui/theme.go + chrome.go + logo.go, lipgloss v1.0.0 (default renderer
//! bound to os.Stdout), termenv v0.15.2 color degradation through
//! go-colorful v1.2.0 HSLuv distances, and charmbracelet/x/ansi v0.4.5
//! width/truncate/wrap. It is not a widget framework: screens build strings
//! with [`Style`] and the layout helpers, then hand the view to the terminal
//! session.
//!
//! Two palettes resolve differently in Go and both are kept:
//! * `ui.Active` ([`active`], [`Color::active`]): `PB_THEME`, then
//!   `COLORFGBG` ending in `;15`, then lipgloss `HasDarkBackground`.
//! * `ui.Adaptive`/`lipgloss.AdaptiveColor` ([`Color::role`],
//!   [`Color::adaptive`]): resolved per render by `HasDarkBackground` only,
//!   so `PB_THEME=light` on a dark terminal still picks the dark side.
//!
//! The color profile and the background answer reuse the phase-4 caches in
//! `metrics::output::style` (termenv `EnvColorProfile` of stdout, and the
//! OSC 11 probe primed at process start).
//!
//! Attribution: behavior and data tables are derived from charmbracelet
//! lipgloss and x/ansi, muesli termenv, lucasb-eyer go-colorful (incl. the
//! hsluv-go port), rivo/uniseg and mattn/go-runewidth (all MIT licensed).
//! The Unicode property ranges (GraphemeBreakProperty, emoji-data
//! Extended_Pictographic/Emoji_Presentation 15.0.0, EastAsianWidth) come from
//! the Unicode Character Database, © Unicode, Inc., used under the Unicode
//! License (https://www.unicode.org/license.html), via those libraries'
//! generated tables. Full upstream copyright and license notices, exact
//! versions and fixture provenance: docs/evidence/phase7/style-notices.txt.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

pub(crate) use crate::metrics::output::style::Profile;

// ── Render environment ──────────────────────────────────────────────────────

/// What lipgloss's default renderer knows about stdout: the termenv profile
/// and `HasDarkBackground`. Tests pass explicit values to `render_in`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Env {
    pub(crate) profile: Profile,
    pub(crate) dark: bool,
}

impl Env {
    /// The process renderer: cached profile and cached background probe.
    pub(crate) fn process() -> Env {
        Env {
            profile: profile(),
            dark: crate::metrics::has_dark_background(),
        }
    }
}

/// termenv profile of stdout (phase-4 `EnvColorProfile`, cached).
pub(crate) fn profile() -> Profile {
    crate::metrics::output::style::Terminal::from_process().profile
}

fn process_env(key: &str) -> String {
    std::env::var_os(key)
        .map(|v| v.to_string_lossy().into_owned())
        .unwrap_or_default()
}

// ── Palette (pkg/ui/theme.go) ───────────────────────────────────────────────

/// A `ui.Palette` field. The enum keeps theme.go's complete, frozen palette:
/// workflows use a subset, and the full set is pinned by the Go color-oracle
/// tests (which construct every variant).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "complete frozen pkg/ui palette; unused roles are covered by the color oracle tests"
    )
)]
pub(crate) enum Role {
    Bg,
    Panel,
    PanelAlt,
    EditorBg,
    Border,
    BorderSoft,
    BorderHi,
    Text,
    Body,
    Mute,
    Dim,
    Faint,
    Ghost,
    Accent,
    Accent2,
    AccentSoft,
    Active,
    Ok,
    OkSoft,
    OkSoftBg,
    Warn,
    Err,
    String,
    Number,
    SelRow,
    Cursor,
    EditorActive,
    InvertText,
    Overlay,
    BadgeBg,
}

const fn dark_hex(role: Role) -> &'static str {
    match role {
        Role::Bg => "#0B0B12",
        Role::Panel => "#13131F",
        Role::PanelAlt => "#1A1A2A",
        Role::EditorBg => "#1B1B1F",
        Role::Border => "#4A4A5C",
        Role::BorderSoft => "#2A2A3D",
        Role::BorderHi => "#7878E0",
        Role::Text => "#F4F4F5",
        Role::Body => "#E4E4E7",
        Role::Mute => "#B5B5BE",
        Role::Dim => "#9595A0",
        Role::Faint => "#7E7E8A",
        Role::Ghost => "#525260",
        Role::Accent => "#9E9EF0",
        Role::Accent2 => "#8484C8",
        Role::AccentSoft => "#2A2A4E",
        Role::Active => "#38BDF8",
        Role::Ok => "#34D399",
        Role::OkSoft => "#6EE7B7",
        Role::OkSoftBg => "#1A3D3A",
        Role::Warn => "#FB923C",
        Role::Err => "#F87171",
        Role::String => "#5EEAD4",
        Role::Number => "#C084FC",
        Role::SelRow => "#25253A",
        Role::Cursor => "#9E9EF0",
        Role::EditorActive => "#26262C",
        Role::InvertText => "#0B0B12",
        Role::Overlay => "#070710",
        Role::BadgeBg => "#1E1E28",
    }
}

const fn light_hex(role: Role) -> &'static str {
    match role {
        Role::Bg => "#FBFBFD",
        Role::Panel => "#FFFFFF",
        Role::PanelAlt => "#F1F1F5",
        Role::EditorBg => "#F4F4F5",
        Role::Border => "#D1D5DB",
        Role::BorderSoft => "#E5E7EB",
        Role::BorderHi => "#5A5AC8",
        Role::Text => "#09090B",
        Role::Body => "#18181B",
        Role::Mute => "#3F3F46",
        Role::Dim => "#52525B",
        Role::Faint => "#71717A",
        Role::Ghost => "#A8A8AE",
        Role::Accent => "#3A3A8C",
        Role::Accent2 => "#5151A0",
        Role::AccentSoft => "#E5E5FF",
        Role::Active => "#0284C7",
        Role::Ok => "#047857",
        Role::OkSoft => "#047857",
        Role::OkSoftBg => "#DCEEE7",
        Role::Warn => "#C2410C",
        Role::Err => "#B91C1C",
        Role::String => "#047857",
        Role::Number => "#7E22CE",
        Role::SelRow => "#EEEEF9",
        Role::Cursor => "#3A3A8C",
        Role::EditorActive => "#EAEAEC",
        Role::InvertText => "#FFFFFF",
        Role::Overlay => "#F1F1F5",
        Role::BadgeBg => "#EDEDEF",
    }
}

/// `ui.Dark` or `ui.Light`.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Palette {
    light: bool,
}

pub(crate) static DARK: Palette = Palette { light: false };
pub(crate) static LIGHT: Palette = Palette { light: true };

impl Palette {
    pub(crate) const fn hex(&self, role: Role) -> &'static str {
        if self.light {
            light_hex(role)
        } else {
            dark_hex(role)
        }
    }
}

/// `ui.Active = LoadTheme()`, computed once.
pub(crate) fn active() -> &'static Palette {
    static ACTIVE: OnceLock<&'static Palette> = OnceLock::new();
    ACTIVE.get_or_init(|| {
        load_theme(
            &process_env("PB_THEME"),
            &process_env("COLORFGBG"),
            crate::metrics::has_dark_background,
        )
    })
}

/// ui.LoadTheme. `dark_background` is lipgloss.HasDarkBackground and is only
/// consulted on the auto path (Go primes it at init regardless; the
/// metrics module reproduces that priming).
fn load_theme(
    pb_theme: &str,
    colorfgbg: &str,
    dark_background: impl FnOnce() -> bool,
) -> &'static Palette {
    match go_lower(pb_theme).as_str() {
        "light" => &LIGHT,
        "dark" => &DARK,
        _ => {
            if !colorfgbg.is_empty() {
                let parts: Vec<&str> = colorfgbg.split(';').collect();
                if parts.len() >= 2 && parts[parts.len() - 1] == "15" {
                    return &LIGHT;
                }
            }
            if dark_background() { &DARK } else { &LIGHT }
        }
    }
}

/// strings.ToLower: simple (one rune to one rune) case mapping.
fn go_lower(s: &str) -> String {
    s.chars()
        .map(|c| {
            let mut mapped: Vec<char> = c.to_lowercase().collect();
            match (mapped.len(), c) {
                (1, _) => mapped.pop().unwrap_or(c),
                (_, '\u{130}') => 'i',
                _ => c,
            }
        })
        .collect()
}

// ── Colors (lipgloss TerminalColor → termenv) ───────────────────────────────

/// A lipgloss color.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum Color {
    /// lipgloss.NoColor / unset.
    #[default]
    None,
    /// lipgloss.Color(s): "#rrggbb" (or "#rgb") or a decimal ANSI index.
    Spec(&'static str),
    /// lipgloss.Color("<n>").
    Ansi(u8),
    /// lipgloss.AdaptiveColor{Light, Dark}, chosen by HasDarkBackground.
    Adaptive {
        light: &'static str,
        dark: &'static str,
    },
    /// lipgloss.Color(s) for a runtime string (chart series colors).
    Dyn(DynSpec),
}

/// A runtime lipgloss.Color string kept inline so `Color` stays `Copy`.
/// Only the first 7 bytes matter to termenv (`#rrggbb`; Sscanf ignores the
/// rest) apart from the length-4 `#rgb` form, which is remembered.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct DynSpec {
    bytes: [u8; 7],
    len: u8,
    short: bool,
}

impl DynSpec {
    fn new(spec: &str) -> DynSpec {
        let mut end = spec.len().min(7);
        while !spec.is_char_boundary(end) {
            end -= 1;
        }
        let mut bytes = [0u8; 7];
        bytes[..end].copy_from_slice(&spec.as_bytes()[..end]);
        DynSpec {
            bytes,
            len: end as u8,
            short: spec.len() == 4,
        }
    }
    fn as_str(&self) -> &str {
        std::str::from_utf8(&self.bytes[..usize::from(self.len)]).unwrap_or("")
    }
}

/// `Role` converts to the adaptive (`ui.Adaptive`) color: resolved by the
/// terminal background, independent of PB_THEME. Use [`Color::active`] where
/// Go reads `ui.Active.<role>`.
impl From<Role> for Color {
    fn from(role: Role) -> Color {
        Color::role(role)
    }
}

impl Color {
    pub(crate) const fn hex(spec: &'static str) -> Color {
        Color::Spec(spec)
    }
    /// lipgloss.Color(spec) for a runtime string.
    pub(crate) fn dynamic(spec: &str) -> Color {
        Color::Dyn(DynSpec::new(spec))
    }
    pub(crate) const fn ansi256(index: u8) -> Color {
        Color::Ansi(index)
    }
    pub(crate) const fn adaptive(light: &'static str, dark: &'static str) -> Color {
        Color::Adaptive { light, dark }
    }
    /// `ui.Adaptive(func(p) p.<role>)`: resolved by background, not PB_THEME.
    pub(crate) const fn role(role: Role) -> Color {
        Color::Adaptive {
            light: light_hex(role),
            dark: dark_hex(role),
        }
    }
    /// `ui.Active.<role>`: a fixed lipgloss.Color from the loaded theme.
    pub(crate) fn active(role: Role) -> Color {
        Color::Spec(active().hex(role))
    }
    /// A fixed lipgloss.Color from an explicit palette.
    pub(crate) const fn of(palette: &Palette, role: Role) -> Color {
        Color::Spec(palette.hex(role))
    }

    /// termenv `Color.Sequence(bg)` after `Profile.Convert`, or None when
    /// termenv would emit nothing (Ascii profile, unset, unparsable).
    pub(crate) fn sequence(self, background: bool, env: Env) -> Option<String> {
        if env.profile == Profile::Ascii {
            return None;
        }
        match self {
            Color::None => None,
            Color::Spec(spec) => spec_sequence(spec, background, env.profile),
            Color::Ansi(index) => index_sequence(i64::from(index), background, env.profile),
            Color::Adaptive { light, dark } => {
                spec_sequence(if env.dark { dark } else { light }, background, env.profile)
            }
            Color::Dyn(spec) => {
                if spec.short {
                    // Re-expand to a length-4 spec so the #rgb rule applies.
                    let text = spec.as_str();
                    spec_sequence(&text[..text.len().min(4)], background, env.profile)
                } else if spec.len == 4 {
                    // A 4-byte prefix of a longer spec must not take the #rgb path.
                    spec_sequence(&format!("{}\u{0}", spec.as_str()), background, env.profile)
                } else {
                    spec_sequence(spec.as_str(), background, env.profile)
                }
            }
        }
    }
}

/// termenv Profile.Color(s) + Sequence, memoized for degraded profiles
/// (the HSLuv search is not free and views re-render every frame).
fn spec_sequence(spec: &str, background: bool, profile: Profile) -> Option<String> {
    if profile != Profile::Ansi256 && profile != Profile::Ansi {
        return convert_spec(spec, background, profile);
    }
    type Cache = Mutex<HashMap<(String, bool, bool), Option<String>>>;
    static CACHE: OnceLock<Cache> = OnceLock::new();
    let key = (spec.to_owned(), background, profile == Profile::Ansi);
    let cache = CACHE.get_or_init(Default::default);
    if let Some(hit) = cache.lock().ok().and_then(|map| map.get(&key).cloned()) {
        return hit;
    }
    let value = convert_spec(spec, background, profile);
    if let Ok(mut map) = cache.lock() {
        map.insert(key, value.clone());
    }
    value
}

fn convert_spec(spec: &str, background: bool, profile: Profile) -> Option<String> {
    if profile == Profile::Ascii || spec.is_empty() {
        return None;
    }
    if spec.starts_with('#') {
        let rgb = parse_hex(spec)?;
        return Some(match profile {
            Profile::TrueColor => rgb_sequence(rgb, background),
            Profile::Ansi256 => ansi256_sequence(hex_to_ansi256(rgb), background),
            _ => ansi_sequence(ansi256_to_ansi(hex_to_ansi256(rgb)), background),
        });
    }
    // strconv.Atoi.
    index_sequence(spec.parse::<i64>().ok()?, background, profile)
}

fn index_sequence(index: i64, background: bool, profile: Profile) -> Option<String> {
    match profile {
        Profile::Ascii => None,
        _ if index < 16 => Some(ansi_sequence(index, background)),
        // Go indexes ansiHex here and panics beyond 255. Unreachable in pb:
        // numeric specs are only the &'static literals "240", "62", "230"
        // (Color::Ansi is a u8), and Color::dynamic is fed chart hex colors.
        Profile::Ansi if index > 255 => None,
        Profile::Ansi => Some(ansi_sequence(ansi256_to_ansi(index), background)),
        _ => Some(ansi256_sequence(index, background)),
    }
}

/// ANSIColor.Sequence.
fn ansi_sequence(color: i64, background: bool) -> String {
    let modifier = |c: i64| if background { c + 10 } else { c };
    if color < 8 {
        (modifier(color) + 30).to_string()
    } else {
        (modifier(color - 8) + 90).to_string()
    }
}

/// ANSI256Color.Sequence.
fn ansi256_sequence(index: i64, background: bool) -> String {
    format!("{};5;{index}", if background { "48" } else { "38" })
}

/// RGBColor.Sequence: uint8(f.R*255) truncates, so 0xE4 renders as 227.
fn rgb_sequence(c: Rgb, background: bool) -> String {
    format!(
        "{};2;{};{};{}",
        if background { "48" } else { "38" },
        (c.r * 255.0) as u8,
        (c.g * 255.0) as u8,
        (c.b * 255.0) as u8
    )
}

/// colorful.Color.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Rgb {
    r: f64,
    g: f64,
    b: f64,
}

/// colorful.Hex: Sscanf "#%02x%02x%02x" (or "#%1x%1x%1x" for length 4)
/// times 1/255 (1/15). Trailing input after three components is ignored,
/// as Sscanf does.
fn parse_hex(spec: &str) -> Option<Rgb> {
    let bytes = spec.as_bytes();
    let (digits, factor) = if bytes.len() == 4 {
        (1, 1.0 / 15.0)
    } else {
        (2, 1.0 / 255.0)
    };
    let mut rest = bytes.strip_prefix(b"#")?;
    let mut out = [0.0f64; 3];
    for value in &mut out {
        let mut n = 0u32;
        let mut count = 0;
        while count < digits {
            let Some(d) = rest.first().and_then(|c| char::from(*c).to_digit(16)) else {
                break;
            };
            n = n * 16 + d;
            rest = &rest[1..];
            count += 1;
        }
        if count == 0 {
            return None;
        }
        *value = f64::from(n) * factor;
    }
    Some(Rgb {
        r: out[0],
        g: out[1],
        b: out[2],
    })
}

/// termenv hexToANSI256Color, including its gray-index computation from the
/// 0..5 cube indices (which always selects gray 232).
fn hex_to_ansi256(c: Rgb) -> i64 {
    let v2ci = |v: f64| -> i64 {
        if v < 48.0 {
            0
        } else if v < 115.0 {
            1
        } else {
            ((v - 35.0) / 40.0) as i64
        }
    };
    let (r, g, b) = (v2ci(c.r * 255.0), v2ci(c.g * 255.0), v2ci(c.b * 255.0));
    let ci = 36 * r + 6 * g + b;
    const I2CV: [i64; 6] = [0, 0x5f, 0x87, 0xaf, 0xd7, 0xff];
    let level = |i: i64| I2CV[i.clamp(0, 5) as usize];
    let (cr, cg, cb) = (level(r), level(g), level(b));
    let average = (r + g + b) / 3;
    let gray_index = if average > 238 {
        23
    } else {
        (average - 3) / 10
    };
    let gv = 8 + 10 * gray_index;
    let c2 = Rgb {
        r: cr as f64 / 255.0,
        g: cg as f64 / 255.0,
        b: cb as f64 / 255.0,
    };
    let g2 = Rgb {
        r: gv as f64 / 255.0,
        g: gv as f64 / 255.0,
        b: gv as f64 / 255.0,
    };
    if distance_hsluv(c, c2) <= distance_hsluv(c, g2) {
        16 + ci
    } else {
        232 + gray_index
    }
}

/// termenv ansi256ToANSIColor: nearest of the 16 system colors by HSLuv
/// distance, first minimum wins.
fn ansi256_to_ansi(index: i64) -> i64 {
    let h = ansi_color(index);
    let mut best = 0;
    let mut min = f64::MAX;
    for i in 0..=15 {
        let d = distance_hsluv(h, ansi_color(i));
        if d < min {
            min = d;
            best = i;
        }
    }
    best
}

/// colorful.Hex(termenv ansiHex[index]).
fn ansi_color(index: i64) -> Rgb {
    let (r, g, b) = ansi_rgb(index.clamp(0, 255) as u8);
    let unit = |v: u8| f64::from(v) * (1.0 / 255.0);
    Rgb {
        r: unit(r),
        g: unit(g),
        b: unit(b),
    }
}

/// termenv ansiHex: xterm system colors, 6x6x6 cube, gray ramp.
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

/// colorful DistanceHSLuv. Operation order follows go-colorful v1.2.0 so
/// amd64 results match bit for bit up to libm differences (see doc).
fn distance_hsluv(c1: Rgb, c2: Rgb) -> f64 {
    let (h1, s1, l1) = hsluv(c1);
    let (h2, s2, l2) = hsluv(c2);
    let sq = |v: f64| v * v;
    (sq((h1 - h2) / 100.0) + sq(s1 - s2) + sq(l1 - l2)).sqrt()
}

const HSLUV_D65: [f64; 3] = [0.95045592705167, 1.0, 1.089057750759878];

fn hsluv(c: Rgb) -> (f64, f64, f64) {
    let (x, y, z) = xyz(c);
    let (l, u, v) = xyz_to_luv(x, y, z, HSLUV_D65);
    let (l, c, h) = luv_to_lch(l, u, v);
    lch_to_hsluv(l, c, h)
}

/// math.Pow(x, 2.4) as Go computes it: Exp(0.4*Log(x)) times x² by
/// repeated squaring (power-of-two scaling is exact).
fn go_pow_2_4(x: f64) -> f64 {
    let fraction = 2.4f64 - 2.0;
    (fraction * x.ln()).exp() * (x * x)
}

fn linearize(v: f64) -> f64 {
    if v <= 0.04045 {
        v / 12.92
    } else {
        go_pow_2_4((v + 0.055) / 1.055)
    }
}

fn xyz(c: Rgb) -> (f64, f64, f64) {
    let (r, g, b) = (linearize(c.r), linearize(c.g), linearize(c.b));
    (
        // go-colorful's literals, written in their shortest round-trip form
        // (identical f64 values).
        0.4123907992659595 * r + 0.35758433938387796 * g + 0.1804807884018343 * b,
        0.21263900587151036 * r + 0.7151686787677559 * g + 0.07219231536073371 * b,
        0.01933081871559185 * r + 0.11919477979462599 * g + 0.9505321522496606 * b,
    )
}

fn xyz_to_uv(x: f64, y: f64, z: f64) -> (f64, f64) {
    let denom = x + 15.0 * y + 3.0 * z;
    if denom == 0.0 {
        (0.0, 0.0)
    } else {
        (4.0 * x / denom, 9.0 * y / denom)
    }
}

fn xyz_to_luv(x: f64, y: f64, z: f64, wref: [f64; 3]) -> (f64, f64, f64) {
    // Go folds 6/29*6/29*6/29 and 29/3*29/3*29/3 exactly before rounding.
    let l = if y / wref[1] <= 216.0 / 24389.0 {
        y / wref[1] * (24389.0 / 27.0) / 100.0
    } else {
        1.16 * (y / wref[1]).cbrt() - 0.16
    };
    let (ubis, vbis) = xyz_to_uv(x, y, z);
    let (un, vn) = xyz_to_uv(wref[0], wref[1], wref[2]);
    (l, 13.0 * l * (ubis - un), 13.0 * l * (vbis - vn))
}

fn luv_to_lch(l: f64, u: f64, v: f64) -> (f64, f64, f64) {
    let h = if (v - u).abs() > 1e-4 && u.abs() > 1e-4 {
        (57.29577951308232 * v.atan2(u) + 360.0) % 360.0
    } else {
        0.0
    };
    (l, (u * u + v * v).sqrt(), h)
}

fn clamp01(v: f64) -> f64 {
    0.0f64.max(v.min(1.0))
}

fn lch_to_hsluv(l: f64, c: f64, h: f64) -> (f64, f64, f64) {
    let c = c * 100.0;
    let l = l * 100.0;
    // l is finite here (sRGB input), so this matches Go's `l > hi || l < lo`.
    let s = if !(0.00000001..=99.9999999).contains(&l) {
        0.0
    } else {
        c / max_chroma_for_lh(l, h) * 100.0
    };
    (h, clamp01(s / 100.0), clamp01(l / 100.0))
}

const HSLUV_M: [[f64; 3]; 3] = [
    [3.2409699419045214, -1.5373831775700935, -0.4986107602930033],
    [-0.9692436362808798, 1.8759675015077207, 0.04155505740717561],
    [
        0.05563007969699361,
        -0.20397695888897657,
        1.0569715142428786,
    ],
];

fn max_chroma_for_lh(l: f64, h: f64) -> f64 {
    let h_rad = h / 360.0 * std::f64::consts::PI * 2.0;
    let mut min_length = f64::MAX;
    for (x, y) in hsluv_bounds(l) {
        let length = y / (h_rad.sin() - x * h_rad.cos());
        if length > 0.0 && length < min_length {
            min_length = length;
        }
    }
    min_length
}

fn hsluv_bounds(l: f64) -> [(f64, f64); 6] {
    const KAPPA: f64 = 903.2962962962963;
    const EPSILON: f64 = 0.008856451679035631;
    let base = l + 16.0;
    // math.Pow(base, 3) by repeated squaring: base * (base * base).
    let sub1 = base * (base * base) / 1560896.0;
    let sub2 = if sub1 > EPSILON { sub1 } else { l / KAPPA };
    let mut out = [(0.0, 0.0); 6];
    for (i, m) in HSLUV_M.iter().enumerate() {
        for k in 0..2 {
            let k = f64::from(k);
            let top1 = (284517.0 * m[0] - 94839.0 * m[2]) * sub2;
            let top2 =
                (838422.0 * m[2] + 769860.0 * m[1] + 731718.0 * m[0]) * l * sub2 - 769860.0 * k * l;
            let bottom = (632260.0 * m[2] - 126452.0 * m[1]) * sub2 + 126452.0 * k;
            out[i * 2 + k as usize] = (top1 / bottom, top2 / bottom);
        }
    }
    out
}

// ── lipgloss.Style subset ───────────────────────────────────────────────────

/// lipgloss Position constants.
pub(crate) const TOP: f64 = 0.0;
pub(crate) const BOTTOM: f64 = 1.0;
pub(crate) const LEFT: f64 = 0.0;
pub(crate) const RIGHT: f64 = 1.0;
pub(crate) const CENTER: f64 = 0.5;

/// lipgloss.Border (corner/edge glyphs; the table-only middle glyphs are
/// omitted).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Border {
    pub(crate) top: &'static str,
    pub(crate) bottom: &'static str,
    pub(crate) left: &'static str,
    pub(crate) right: &'static str,
    pub(crate) top_left: &'static str,
    pub(crate) top_right: &'static str,
    pub(crate) bottom_left: &'static str,
    pub(crate) bottom_right: &'static str,
}

impl Border {
    const fn of(edges: [&'static str; 8]) -> Border {
        Border {
            top: edges[0],
            bottom: edges[1],
            left: edges[2],
            right: edges[3],
            top_left: edges[4],
            top_right: edges[5],
            bottom_left: edges[6],
            bottom_right: edges[7],
        }
    }
    pub(crate) const NONE: Border = Border::of([""; 8]);
    pub(crate) const NORMAL: Border = Border::of(["─", "─", "│", "│", "┌", "┐", "└", "┘"]);
    pub(crate) const ROUNDED: Border = Border::of(["─", "─", "│", "│", "╭", "╮", "╰", "╯"]);

    // Contract-v1 spelling still used by callers, same value as ROUNDED.
    #[allow(non_upper_case_globals)]
    pub(crate) const Rounded: Border = Border::ROUNDED;
}

/// lipgloss.Style. Every setter marks the style as non-empty (lipgloss
/// `props`), which changes Render even when the value is false/zero.
/// Integer setters take `usize`: lipgloss clamps negative values to zero,
/// so callers saturate signed arithmetic.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct Style {
    /// lipgloss `props`: one bit per explicitly set property (see `P_*`).
    props: u64,
    bold: bool,
    italic: bool,
    underline: bool,
    strikethrough: bool,
    reverse: bool,
    blink: bool,
    faint: bool,
    fg: Color,
    bg: Color,
    width: usize,
    height: usize,
    align_h: f64,
    align_v: f64,
    /// top, right, bottom, left
    padding: [usize; 4],
    margin: [usize; 4],
    margin_bg: Color,
    border: Border,
    border_sides: [Option<bool>; 4],
    border_fg: [Color; 4],
    border_bg: [Color; 4],
    inline: bool,
    max_width: usize,
    max_height: usize,
    color_whitespace: Option<bool>,
    underline_spaces: Option<bool>,
    strikethrough_spaces: Option<bool>,
    tab_width: Option<isize>,
}

/// lipgloss whichSides*: 1 → all, 2 → vertical/horizontal, 3 → top,
/// horizontal, bottom, 4 → top/right/bottom/left; other counts are ignored.
fn which_sides<T: Copy>(values: &[T]) -> Option<[T; 4]> {
    Some(match *values {
        [a] => [a, a, a, a],
        [v, h] => [v, h, v, h],
        [t, h, b] => [t, h, b, h],
        [t, r, b, l] => [t, r, b, l],
        _ => return None,
    })
}

// lipgloss propKey bits, in lipgloss declaration order (Inherit walks them).
const P_BOLD: u64 = 1 << 0;
const P_ITALIC: u64 = 1 << 1;
const P_UNDERLINE: u64 = 1 << 2;
const P_STRIKETHROUGH: u64 = 1 << 3;
const P_REVERSE: u64 = 1 << 4;
const P_BLINK: u64 = 1 << 5;
const P_FAINT: u64 = 1 << 6;
const P_UNDERLINE_SPACES: u64 = 1 << 7;
const P_STRIKETHROUGH_SPACES: u64 = 1 << 8;
const P_COLOR_WHITESPACE: u64 = 1 << 9;
const P_FG: u64 = 1 << 10;
const P_BG: u64 = 1 << 11;
const P_WIDTH: u64 = 1 << 12;
const P_HEIGHT: u64 = 1 << 13;
const P_ALIGN_H: u64 = 1 << 14;
const P_ALIGN_V: u64 = 1 << 15;
const P_PADDING: [u64; 4] = [1 << 16, 1 << 17, 1 << 18, 1 << 19];
const P_MARGIN: [u64; 4] = [1 << 20, 1 << 21, 1 << 22, 1 << 23];
const P_MARGIN_BG: u64 = 1 << 24;
const P_BORDER_STYLE: u64 = 1 << 25;
const P_BORDER_SIDE: [u64; 4] = [1 << 26, 1 << 27, 1 << 28, 1 << 29];
const P_BORDER_FG: [u64; 4] = [1 << 30, 1 << 31, 1 << 32, 1 << 33];
const P_BORDER_BG: [u64; 4] = [1 << 34, 1 << 35, 1 << 36, 1 << 37];
const P_INLINE: u64 = 1 << 38;
const P_MAX_WIDTH: u64 = 1 << 39;
const P_MAX_HEIGHT: u64 = 1 << 40;
const P_TAB_WIDTH: u64 = 1 << 41;

const fn all_bits(sides: [u64; 4]) -> u64 {
    sides[0] | sides[1] | sides[2] | sides[3]
}

/// Alignment name accepted wherever a lipgloss Position is (the other
/// positions are the `LEFT`/`CENTER`/`RIGHT`/`TOP`/`BOTTOM` constants).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Align {
    Right,
}

impl From<Align> for f64 {
    fn from(align: Align) -> f64 {
        match align {
            Align::Right => RIGHT,
        }
    }
}

macro_rules! flag_setter {
    ($($name:ident => $bit:ident),*) => {$(
        pub(crate) fn $name(mut self, on: bool) -> Self {
            self.props |= $bit;
            self.$name = on;
            self
        }
    )*};
}

impl Style {
    pub(crate) fn new() -> Style {
        Style::default()
    }

    flag_setter!(
        bold => P_BOLD,
        italic => P_ITALIC,
        reverse => P_REVERSE,
        inline => P_INLINE
    );

    fn mark(mut self, bit: u64) -> Self {
        self.props |= bit;
        self
    }
    fn is_set(&self, bit: u64) -> bool {
        self.props & bit != 0
    }

    /// Foreground(c). A `Role` means the adaptive palette color.
    pub(crate) fn fg(mut self, color: impl Into<Color>) -> Self {
        self.fg = color.into();
        self.mark(P_FG)
    }
    /// Foreground(lipgloss.Color(spec)) for a runtime string.
    pub(crate) fn fg_hex(self, spec: &str) -> Self {
        self.fg(Color::dynamic(spec))
    }
    pub(crate) fn bg(mut self, color: impl Into<Color>) -> Self {
        self.bg = color.into();
        self.mark(P_BG)
    }
    pub(crate) fn width(mut self, width: usize) -> Self {
        self.width = width;
        self.mark(P_WIDTH)
    }
    pub(crate) fn height(mut self, height: usize) -> Self {
        self.height = height;
        self.mark(P_HEIGHT)
    }
    pub(crate) fn max_width(mut self, width: usize) -> Self {
        self.max_width = width;
        self.mark(P_MAX_WIDTH)
    }
    pub(crate) fn max_height(mut self, height: usize) -> Self {
        self.max_height = height;
        self.mark(P_MAX_HEIGHT)
    }
    /// Align(horizontal): `Align::Right` or a Position such as `CENTER`.
    pub(crate) fn align(mut self, position: impl Into<f64>) -> Self {
        self.align_h = position.into();
        self.mark(P_ALIGN_H)
    }
    /// Padding(sides...) with lipgloss's 1/2/3/4-value side rules, e.g.
    /// `.padding(&[0, 1])`.
    pub(crate) fn padding(mut self, sides: &[usize]) -> Self {
        if let Some(sides) = which_sides(sides) {
            self.padding = sides;
            self.props |= all_bits(P_PADDING);
        }
        self
    }
    pub(crate) fn padding_left(mut self, n: usize) -> Self {
        self.padding[3] = n;
        self.mark(P_PADDING[3])
    }
    pub(crate) fn margin_left(mut self, n: usize) -> Self {
        self.margin[3] = n;
        self.mark(P_MARGIN[3])
    }
    /// Border(b): the style plus all four sides explicitly on.
    pub(crate) fn border(self, border: Border) -> Self {
        self.border_sides(border, &[])
    }
    /// Border(b, sides...) with the 1/2/3/4-value side rules, e.g.
    /// `.border_sides(Border::NORMAL, &[false, false, false, true])`; any
    /// other count turns all sides on, as lipgloss does.
    pub(crate) fn border_sides(mut self, border: Border, sides: &[bool]) -> Self {
        self.border = border;
        self.props |= P_BORDER_STYLE;
        self.border_sides = which_sides(sides).unwrap_or([true; 4]).map(Some);
        self.props |= all_bits(P_BORDER_SIDE);
        self
    }
    /// BorderStyle(b): glyphs only; sides stay implicit unless set.
    pub(crate) fn border_style(mut self, border: Border) -> Self {
        self.border = border;
        self.mark(P_BORDER_STYLE)
    }
    pub(crate) fn border_top(mut self, on: bool) -> Self {
        self.border_sides[0] = Some(on);
        self.mark(P_BORDER_SIDE[0])
    }
    pub(crate) fn border_right(mut self, on: bool) -> Self {
        self.border_sides[1] = Some(on);
        self.mark(P_BORDER_SIDE[1])
    }
    pub(crate) fn border_bottom(mut self, on: bool) -> Self {
        self.border_sides[2] = Some(on);
        self.mark(P_BORDER_SIDE[2])
    }
    pub(crate) fn border_left(mut self, on: bool) -> Self {
        self.border_sides[3] = Some(on);
        self.mark(P_BORDER_SIDE[3])
    }
    /// BorderForeground(c): all sides.
    pub(crate) fn border_fg(self, color: impl Into<Color>) -> Self {
        self.border_fg_sides(&[color.into()])
    }
    pub(crate) fn border_fg_sides(mut self, colors: &[Color]) -> Self {
        if let Some(colors) = which_sides(colors) {
            self.border_fg = colors;
            self.props |= all_bits(P_BORDER_FG);
        }
        self
    }

    /// lipgloss Style.Inherit: copy each property set on `other` and unset
    /// here. Margins and padding are not inherited; a set background on
    /// `other` also becomes the margin background when neither style has one.
    pub(crate) fn inherit(mut self, other: &Style) -> Self {
        if other.is_set(P_BG) && !self.is_set(P_MARGIN_BG) && !other.is_set(P_MARGIN_BG) {
            self.margin_bg = other.bg;
            self.props |= P_MARGIN_BG;
        }
        let mine = self.props;
        let take = |bit: u64| other.props & bit != 0 && mine & bit == 0;
        macro_rules! copy {
            ($bit:expr, $($field:tt)+) => {
                if take($bit) {
                    self.$($field)+ = other.$($field)+;
                    self.props |= $bit;
                }
            };
        }
        copy!(P_BOLD, bold);
        copy!(P_ITALIC, italic);
        copy!(P_UNDERLINE, underline);
        copy!(P_STRIKETHROUGH, strikethrough);
        copy!(P_REVERSE, reverse);
        copy!(P_BLINK, blink);
        copy!(P_FAINT, faint);
        copy!(P_UNDERLINE_SPACES, underline_spaces);
        copy!(P_STRIKETHROUGH_SPACES, strikethrough_spaces);
        copy!(P_COLOR_WHITESPACE, color_whitespace);
        copy!(P_FG, fg);
        copy!(P_BG, bg);
        copy!(P_WIDTH, width);
        copy!(P_HEIGHT, height);
        copy!(P_ALIGN_H, align_h);
        copy!(P_ALIGN_V, align_v);
        copy!(P_MARGIN_BG, margin_bg);
        copy!(P_BORDER_STYLE, border);
        for i in 0..4 {
            copy!(P_BORDER_SIDE[i], border_sides[i]);
            copy!(P_BORDER_FG[i], border_fg[i]);
            copy!(P_BORDER_BG[i], border_bg[i]);
        }
        copy!(P_INLINE, inline);
        copy!(P_MAX_WIDTH, max_width);
        copy!(P_MAX_HEIGHT, max_height);
        copy!(P_TAB_WIDTH, tab_width);
        self
    }

    fn sides_on(&self) -> [bool; 4] {
        let implicit = self.border != Border::NONE && self.border_sides.iter().all(Option::is_none);
        if implicit {
            [true; 4]
        } else {
            self.border_sides.map(|side| side.unwrap_or(false))
        }
    }

    /// Style.Render on the process renderer.
    pub(crate) fn render(&self, text: &str) -> String {
        self.render_in(text, Env::process())
    }

    /// Style.Render for an explicit renderer.
    pub(crate) fn render_in(&self, text: &str, env: Env) -> String {
        String::from_utf8_lossy(&self.render_impl(text.as_bytes(), env, true)).into_owned()
    }

    /// Style.Render preserving Go string bytes through layout and emission.
    pub(crate) fn render_bytes(&self, text: &[u8]) -> Vec<u8> {
        self.render_bytes_in(text, Env::process())
    }

    /// Byte renderer with an explicit profile/background for oracle tests.
    pub(crate) fn render_bytes_in(&self, text: &[u8], env: Env) -> Vec<u8> {
        self.render_impl(text, env, false)
    }

    fn render_impl(&self, text: &[u8], env: Env, string_contract: bool) -> Vec<u8> {
        if self.props == 0 {
            return self.convert_tabs_bytes(text);
        }
        let underline_spaces = self.underline_spaces.unwrap_or(self.underline);
        let strikethrough_spaces = self.strikethrough_spaces.unwrap_or(self.strikethrough);
        let style_whitespace = self.reverse;
        // lipgloss: (u && !us) || (s && !ss) || us || ss, which reduces to:
        let use_space_styler =
            self.underline || underline_spaces || self.strikethrough || strikethrough_spaces;
        let color_whitespace = self.color_whitespace.unwrap_or(true);

        let mut te: Vec<String> = Vec::new();
        let mut te_space: Vec<String> = Vec::new();
        let mut te_whitespace: Vec<String> = Vec::new();
        let attr = |list: &mut Vec<String>, seq: &str| list.push(seq.to_owned());
        if self.bold {
            attr(&mut te, "1");
        }
        if self.italic {
            attr(&mut te, "3");
        }
        if self.underline {
            attr(&mut te, "4");
        }
        if self.reverse {
            attr(&mut te_whitespace, "7");
            attr(&mut te, "7");
        }
        if self.blink {
            attr(&mut te, "5");
        }
        if self.faint {
            attr(&mut te, "2");
        }
        if self.fg != Color::None
            && let Some(seq) = self.fg.sequence(false, env)
        {
            te.push(seq.clone());
            if style_whitespace {
                te_whitespace.push(seq.clone());
            }
            if use_space_styler {
                te_space.push(seq);
            }
        }
        if self.bg != Color::None
            && let Some(seq) = self.bg.sequence(true, env)
        {
            te.push(seq.clone());
            if color_whitespace {
                te_whitespace.push(seq.clone());
            }
            if use_space_styler {
                te_space.push(seq);
            }
        }
        // lipgloss appends underline a second time here.
        if self.underline {
            attr(&mut te, "4");
        }
        if self.strikethrough {
            attr(&mut te, "9");
        }
        if underline_spaces {
            attr(&mut te_space, "4");
        }
        if strikethrough_spaces {
            attr(&mut te_space, "9");
        }

        let mut str = replace_bytes(&self.convert_tabs_bytes(text), b"\r\n", b"\n");
        if self.inline {
            str.retain(|b| *b != b'\n');
        }
        let [pad_top, pad_right, pad_bottom, pad_left] = self.padding;
        if !self.inline && self.width > 0 {
            let wrap_at = self.width as isize - pad_left as isize - pad_right as isize;
            if wrap_at >= 1 {
                // Tab/newline replacement preserves valid UTF-8 for the
                // string facade. Keep its established lossy boundary after
                // wrapping; byte callers retain Go's raw output instead.
                str = if string_contract {
                    let text = std::str::from_utf8(&str)
                        .expect("string renderer input remains valid UTF-8 before wrapping");
                    wrap(text, wrap_at as usize, "").into_bytes()
                } else {
                    wrap_bytes(&str, wrap_at as usize, b"")
                };
            }
        }

        // Core text.
        let mut core = Vec::with_capacity(str.len());
        for (index, line) in str.split(|b| *b == b'\n').enumerate() {
            if index != 0 {
                core.push(b'\n');
            }
            if use_space_styler {
                let mut buf = [0u8; 4];
                for (_, c) in go_runes(line) {
                    let piece = c.encode_utf8(&mut buf);
                    if c.is_whitespace() {
                        core.extend(styled_bytes(&te_space, piece.as_bytes(), env));
                    } else {
                        core.extend(styled_bytes(&te, piece.as_bytes(), env));
                    }
                }
            } else {
                core.extend(styled_bytes(&te, line, env));
            }
        }
        str = core;

        let whitespace = (color_whitespace || style_whitespace).then_some(te_whitespace.as_slice());
        if !self.inline {
            if pad_left > 0 {
                str = pad_bytes(&str, pad_left, true, whitespace, env);
            }
            if pad_right > 0 {
                str = pad_bytes(&str, pad_right, false, whitespace, env);
            }
            if pad_top > 0 {
                str.splice(..0, std::iter::repeat_n(b'\n', pad_top));
            }
            if pad_bottom > 0 {
                str.extend(std::iter::repeat_n(b'\n', pad_bottom));
            }
        }

        if self.height > 0 {
            str = align_vertical_bytes(&str, self.align_v, self.height);
        }

        let lines = str.iter().filter(|b| **b == b'\n').count();
        if !(lines == 0 && self.width == 0) {
            str = align_horizontal_bytes(&str, self.align_h, self.width, whitespace, env);
        }

        if !self.inline {
            str = self.apply_border_bytes(&str, env);
            str = self.apply_margins_bytes(&str, env);
        }

        if self.max_width > 0 {
            str = str
                .split(|b| *b == b'\n')
                .map(|line| truncate_bytes(line, self.max_width, b""))
                .collect::<Vec<_>>()
                .join(&b'\n');
        }
        if self.max_height > 0 {
            let lines: Vec<&[u8]> = str.split(|b| *b == b'\n').collect();
            let height = self.max_height.min(lines.len());
            str = lines[..height].join(&b'\n');
        }
        str
    }

    fn convert_tabs_bytes(&self, text: &[u8]) -> Vec<u8> {
        match self.tab_width.unwrap_or(4) {
            -1 => text.to_vec(),
            n => replace_bytes(text, b"\t", &vec![b' '; n.max(0) as usize]),
        }
    }

    fn apply_border_bytes(&self, str: &[u8], env: Env) -> Vec<u8> {
        let mut border = self.border;
        let [has_top, has_right, has_bottom, has_left] = self.sides_on();
        if border == Border::NONE || !(has_top || has_right || has_bottom || has_left) {
            return str.to_vec();
        }
        let (lines, mut width) = get_lines_bytes(str);
        if has_left {
            if border.left.is_empty() {
                border.left = " ";
            }
            width += max_rune_width(border.left);
        }
        if has_right && border.right.is_empty() {
            border.right = " ";
        }
        if has_top && has_left && border.top_left.is_empty() {
            border.top_left = " ";
        }
        if has_top && has_right && border.top_right.is_empty() {
            border.top_right = " ";
        }
        if has_bottom && has_left && border.bottom_left.is_empty() {
            border.bottom_left = " ";
        }
        if has_bottom && has_right && border.bottom_right.is_empty() {
            border.bottom_right = " ";
        }
        if has_top {
            if !has_left {
                border.top_left = "";
            }
            if !has_right {
                border.top_right = "";
            }
        }
        if has_bottom {
            if !has_left {
                border.bottom_left = "";
            }
            if !has_right {
                border.bottom_right = "";
            }
        }
        let first = |s: &'static str| -> &'static str {
            s.char_indices().nth(1).map_or(s, |(index, _)| &s[..index])
        };
        border.top_left = first(border.top_left);
        border.top_right = first(border.top_right);
        border.bottom_right = first(border.bottom_right);
        border.bottom_left = first(border.bottom_left);

        let [top_fg, right_fg, bottom_fg, left_fg] = self.border_fg;
        let [top_bg, right_bg, bottom_bg, left_bg] = self.border_bg;
        let mut out = Vec::new();
        if has_top {
            let top = render_horizontal_edge(border.top_left, border.top, border.top_right, width);
            out.extend(style_border(&top, top_fg, top_bg, env).as_bytes());
            out.push(b'\n');
        }
        let left_runes: Vec<char> = border.left.chars().collect();
        let right_runes: Vec<char> = border.right.chars().collect();
        let (mut left_index, mut right_index) = (0, 0);
        let count = lines.len();
        for (index, line) in lines.into_iter().enumerate() {
            if has_left {
                let rune = left_runes[left_index].to_string();
                left_index = (left_index + 1) % left_runes.len();
                out.extend(style_border(&rune, left_fg, left_bg, env).as_bytes());
            }
            out.extend_from_slice(line);
            if has_right {
                let rune = right_runes[right_index].to_string();
                right_index = (right_index + 1) % right_runes.len();
                out.extend(style_border(&rune, right_fg, right_bg, env).as_bytes());
            }
            if index < count - 1 {
                out.push(b'\n');
            }
        }
        if has_bottom {
            let bottom = render_horizontal_edge(
                border.bottom_left,
                border.bottom,
                border.bottom_right,
                width,
            );
            out.push(b'\n');
            out.extend(style_border(&bottom, bottom_fg, bottom_bg, env).as_bytes());
        }
        out
    }

    fn apply_margins_bytes(&self, str: &[u8], env: Env) -> Vec<u8> {
        let [top, right, bottom, left] = self.margin;
        let styler: Vec<String> = self.margin_bg.sequence(true, env).into_iter().collect();
        let mut str = pad_bytes(str, left, true, Some(styler.as_slice()), env);
        str = pad_bytes(&str, right, false, Some(styler.as_slice()), env);
        let (_, width) = get_lines_bytes(&str);
        let spaces = " ".repeat(width);
        if top > 0 {
            str.splice(
                ..0,
                styled_bytes(&styler, format!("{spaces}\n").repeat(top).as_bytes(), env),
            );
        }
        if bottom > 0 {
            str.extend(styled_bytes(
                &styler,
                format!("\n{spaces}").repeat(bottom).as_bytes(),
                env,
            ));
        }
        str
    }
}

/// termenv Style.Styled.
fn styled(styles: &[String], s: &str, env: Env) -> String {
    if env.profile == Profile::Ascii || styles.is_empty() {
        return s.to_owned();
    }
    let seq = styles.join(";");
    if seq.is_empty() {
        return s.to_owned();
    }
    format!("\x1b[{seq}m{s}\x1b[0m")
}

/// termenv styles wrap raw Go bytes without interpreting their payload.
fn styled_bytes(styles: &[String], s: &[u8], env: Env) -> Vec<u8> {
    if env.profile == Profile::Ascii || styles.is_empty() {
        return s.to_vec();
    }
    let seq = styles.join(";");
    if seq.is_empty() {
        return s.to_vec();
    }
    let mut out = format!("\x1b[{seq}m").into_bytes();
    out.extend_from_slice(s);
    out.extend_from_slice(b"\x1b[0m");
    out
}

fn replace_bytes(text: &[u8], from: &[u8], to: &[u8]) -> Vec<u8> {
    debug_assert!(!from.is_empty());
    let mut out = Vec::with_capacity(text.len());
    let mut rest = text;
    while let Some(index) = rest.windows(from.len()).position(|window| window == from) {
        out.extend_from_slice(&rest[..index]);
        out.extend_from_slice(to);
        rest = &rest[index + from.len()..];
    }
    out.extend_from_slice(rest);
    out
}

/// lipgloss pad: `n` spaces (styled when a style is given) on each line.
fn pad_bytes(str: &[u8], n: usize, left: bool, style: Option<&[String]>, env: Env) -> Vec<u8> {
    if n == 0 {
        return str.to_vec();
    }
    let spaces = " ".repeat(n);
    let spaces = match style {
        Some(style) => styled_bytes(style, spaces.as_bytes(), env),
        None => spaces.into_bytes(),
    };
    let mut out = Vec::with_capacity(str.len() + spaces.len());
    for (index, line) in str.split(|b| *b == b'\n').enumerate() {
        if index != 0 {
            out.push(b'\n');
        }
        if left {
            out.extend_from_slice(&spaces);
            out.extend_from_slice(line);
        } else {
            out.extend_from_slice(line);
            out.extend_from_slice(&spaces);
        }
    }
    out
}

/// lipgloss getLines: lines and the widest ansi.StringWidth.
fn get_lines(s: &str) -> (Vec<&str>, usize) {
    let lines: Vec<&str> = s.split('\n').collect();
    let widest = get_lines_bytes(s.as_bytes()).1;
    (lines, widest)
}

fn get_lines_bytes(s: &[u8]) -> (Vec<&[u8]>, usize) {
    let lines: Vec<&[u8]> = s.split(|b| *b == b'\n').collect();
    let widest = lines
        .iter()
        .map(|line| width_bytes(line))
        .max()
        .unwrap_or(0);
    (lines, widest)
}

fn align_horizontal_bytes(
    str: &[u8],
    position: f64,
    width: usize,
    style: Option<&[String]>,
    env: Env,
) -> Vec<u8> {
    let (lines, widest) = get_lines_bytes(str);
    let fill = |n: usize| {
        let spaces = " ".repeat(n);
        match style {
            Some(style) => styled_bytes(style, spaces.as_bytes(), env),
            None => spaces.into_bytes(),
        }
    };
    let mut out = Vec::with_capacity(str.len());
    let count = lines.len();
    for (index, line) in lines.into_iter().enumerate() {
        let line_width = width_bytes(line);
        let mut short = widest - line_width;
        short += (width as isize - (short + line_width) as isize).max(0) as usize;
        if short > 0 {
            if position == RIGHT {
                out.extend(fill(short));
                out.extend_from_slice(line);
            } else if position == CENTER {
                let left = short / 2;
                let right = left + short % 2;
                out.extend(fill(left));
                out.extend_from_slice(line);
                out.extend(fill(right));
            } else {
                out.extend_from_slice(line);
                out.extend(fill(short));
            }
        } else {
            out.extend_from_slice(line);
        }
        if index < count - 1 {
            out.push(b'\n');
        }
    }
    out
}

fn align_vertical_bytes(str: &[u8], position: f64, height: usize) -> Vec<u8> {
    let str_height = str.iter().filter(|b| **b == b'\n').count() + 1;
    if height < str_height {
        return str.to_vec();
    }
    let gap = height - str_height;
    let (top, bottom) = if position == TOP {
        (0, gap)
    } else if position == CENTER {
        (gap / 2, gap - gap / 2)
    } else if position == BOTTOM {
        (gap, 0)
    } else {
        (0, 0)
    };
    let mut out = vec![b'\n'; top];
    out.extend_from_slice(str);
    out.extend(std::iter::repeat_n(b'\n', bottom));
    out
}

fn render_horizontal_edge(left: &str, middle: &str, right: &str, width: usize) -> String {
    let middle = if middle.is_empty() { " " } else { middle };
    let left_width = self::width(left);
    let right_width = self::width(right);
    let runes: Vec<char> = middle.chars().collect();
    let mut j = 0;
    let mut out = String::from(left);
    let mut i = left_width + right_width;
    let mut buf = [0u8; 4];
    while i < width + right_width {
        out.push(runes[j]);
        j = (j + 1) % runes.len();
        // Go loops forever on a zero-width edge glyph. Unreachable in pb:
        // every Border is a compile-time constant (lipgloss presets and the
        // bubble-table glyphs), all of width 1, and an empty middle becomes
        // " ". The guard only keeps a programming error from hanging.
        i += self::width(runes[j].encode_utf8(&mut buf)).max(1);
    }
    out.push_str(right);
    out
}

fn style_border(border: &str, fg: Color, bg: Color, env: Env) -> String {
    if fg == Color::None && bg == Color::None {
        return border.to_owned();
    }
    let mut styles = Vec::new();
    styles.extend(fg.sequence(false, env));
    styles.extend(bg.sequence(true, env));
    styled(&styles, border, env)
}

/// lipgloss maxRuneWidth: widest grapheme cluster.
fn max_rune_width(s: &str) -> usize {
    let mut widest = 0;
    let mut rest = s;
    while !rest.is_empty() {
        let (len, width) = first_cluster(rest);
        widest = widest.max(width);
        rest = &rest[len.max(1)..];
    }
    widest
}

/// lipgloss.Width: widest line.
pub(crate) fn block_width(s: &str) -> usize {
    get_lines(s).1
}

// ── Joining and placement ───────────────────────────────────────────────────

/// lipgloss.JoinHorizontal.
pub(crate) fn join_horizontal(position: impl Into<f64>, blocks: &[&str]) -> String {
    let bytes: Vec<&[u8]> = blocks.iter().map(|s| s.as_bytes()).collect();
    String::from_utf8_lossy(&join_horizontal_bytes(position, &bytes)).into_owned()
}

/// lipgloss byte join; layout uses the raw ANSI parser width of each line.
pub(crate) fn join_horizontal_bytes(position: impl Into<f64>, blocks: &[&[u8]]) -> Vec<u8> {
    let position: f64 = position.into();
    match blocks {
        [] => return Vec::new(),
        [only] => return (*only).to_owned(),
        _ => {}
    }
    let mut split: Vec<(Vec<&[u8]>, usize)> = blocks.iter().map(|s| get_lines_bytes(s)).collect();
    let max_height = split
        .iter()
        .map(|(lines, _)| lines.len())
        .max()
        .unwrap_or(0);
    for (lines, _) in &mut split {
        if lines.len() >= max_height {
            continue;
        }
        let n = max_height - lines.len();
        // Middle positions put round(n*pos) blank lines above.
        let above = if position == TOP {
            0
        } else if position == BOTTOM {
            n
        } else {
            (n as f64 * position.clamp(0.0, 1.0)).round() as usize
        };
        let mut padded = vec![&b""[..]; above];
        padded.append(lines);
        padded.extend(std::iter::repeat_n(&b""[..], n - above));
        *lines = padded;
    }
    let mut out = Vec::new();
    for row in 0..max_height {
        for (lines, widest) in &split {
            out.extend_from_slice(lines[row]);
            out.extend(std::iter::repeat_n(b' ', widest - width_bytes(lines[row])));
        }
        if row < max_height - 1 {
            out.push(b'\n');
        }
    }
    out
}

/// lipgloss.JoinVertical.
pub(crate) fn join_vertical(position: impl Into<f64>, blocks: &[&str]) -> String {
    let bytes: Vec<&[u8]> = blocks.iter().map(|s| s.as_bytes()).collect();
    String::from_utf8_lossy(&join_vertical_bytes(position, &bytes)).into_owned()
}

/// lipgloss byte join; layout uses the raw ANSI parser width of each line.
pub(crate) fn join_vertical_bytes(position: impl Into<f64>, blocks: &[&[u8]]) -> Vec<u8> {
    let position: f64 = position.into();
    match blocks {
        [] => return Vec::new(),
        [only] => return (*only).to_owned(),
        _ => {}
    }
    let split: Vec<Vec<&[u8]>> = blocks.iter().map(|s| get_lines_bytes(s).0).collect();
    let max_width = blocks
        .iter()
        .map(|s| get_lines_bytes(s).1)
        .max()
        .unwrap_or(0);
    let mut out = Vec::new();
    for (i, block) in split.iter().enumerate() {
        for (j, line) in block.iter().enumerate() {
            let w = max_width - width_bytes(line);
            if position == LEFT {
                out.extend_from_slice(line);
                out.extend(std::iter::repeat_n(b' ', w));
            } else if position == RIGHT {
                out.extend(std::iter::repeat_n(b' ', w));
                out.extend_from_slice(line);
            } else if w < 1 {
                out.extend_from_slice(line);
            } else {
                let left = (w as f64 * position.clamp(0.0, 1.0)).round() as usize;
                let right = w - left;
                out.extend(std::iter::repeat_n(b' ', left));
                out.extend_from_slice(line);
                out.extend(std::iter::repeat_n(b' ', right));
            }
            if !(i == split.len() - 1 && j == block.len() - 1) {
                out.push(b'\n');
            }
        }
    }
    out
}

/// lipgloss WhitespaceOption set for Place.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Whitespace {
    pub(crate) chars: &'static str,
    pub(crate) fg: Color,
    pub(crate) bg: Color,
}

impl Whitespace {
    fn render(&self, n: usize, env: Env) -> String {
        let chars: Vec<char> = if self.chars.is_empty() {
            vec![' ']
        } else {
            self.chars.chars().collect()
        };
        let mut out = String::new();
        let mut j = 0;
        let mut i = 0;
        let mut buf = [0u8; 4];
        while i < n {
            out.push(chars[j]);
            j = (j + 1) % chars.len();
            // As in render_horizontal_edge: Go hangs on a zero-width glyph;
            // `chars` is a &'static str chosen by pb code (default " ").
            i += width(chars[j].encode_utf8(&mut buf)).max(1);
        }
        let short = n as isize - width(&out) as isize;
        if short > 0 {
            out.push_str(&" ".repeat(short as usize));
        }
        let mut styles = Vec::new();
        if self.fg != Color::None {
            styles.extend(self.fg.sequence(false, env));
        }
        if self.bg != Color::None {
            styles.extend(self.bg.sequence(true, env));
        }
        styled(&styles, &out, env)
    }
}

/// lipgloss.Place with default (unstyled space) whitespace.
pub(crate) fn place(
    width: usize,
    height: usize,
    hpos: impl Into<f64>,
    vpos: impl Into<f64>,
    s: &str,
) -> String {
    place_with(
        width,
        height,
        hpos.into(),
        vpos.into(),
        s,
        Whitespace::default(),
        Env::process(),
    )
}

/// lipgloss.Place with whitespace options and an explicit renderer.
pub(crate) fn place_with(
    width: usize,
    height: usize,
    hpos: f64,
    vpos: f64,
    s: &str,
    whitespace: Whitespace,
    env: Env,
) -> String {
    let horizontal = place_horizontal_bytes(width, hpos, s.as_bytes(), whitespace, env);
    String::from_utf8_lossy(&place_vertical_bytes(
        height,
        vpos,
        &horizontal,
        whitespace,
        env,
    ))
    .into_owned()
}

/// lipgloss.Place on Go string bytes with default (unstyled space) whitespace.
pub(crate) fn place_bytes(
    width: usize,
    height: usize,
    hpos: impl Into<f64>,
    vpos: impl Into<f64>,
    s: &[u8],
) -> Vec<u8> {
    let env = Env::process();
    let ws = Whitespace::default();
    let horizontal = place_horizontal_bytes(width, hpos.into(), s, ws, env);
    place_vertical_bytes(height, vpos.into(), &horizontal, ws, env)
}

fn place_horizontal_bytes(
    width: usize,
    position: f64,
    s: &[u8],
    ws: Whitespace,
    env: Env,
) -> Vec<u8> {
    let (lines, content_width) = get_lines_bytes(s);
    if width <= content_width {
        return s.to_vec();
    }
    let gap = width - content_width;
    let mut out = Vec::new();
    let count = lines.len();
    for (index, line) in lines.into_iter().enumerate() {
        let short = content_width.saturating_sub(width_bytes(line));
        if position == LEFT {
            out.extend_from_slice(line);
            out.extend(ws.render(gap + short, env).as_bytes());
        } else if position == RIGHT {
            out.extend(ws.render(gap + short, env).as_bytes());
            out.extend_from_slice(line);
        } else {
            let total = gap + short;
            let split = (total as f64 * position.clamp(0.0, 1.0)).round() as usize;
            let left = total - split;
            out.extend(ws.render(left, env).as_bytes());
            out.extend_from_slice(line);
            out.extend(ws.render(total - left, env).as_bytes());
        }
        if index < count - 1 {
            out.push(b'\n');
        }
    }
    out
}

fn place_vertical_bytes(
    height: usize,
    position: f64,
    s: &[u8],
    ws: Whitespace,
    env: Env,
) -> Vec<u8> {
    let content_height = s.iter().filter(|b| **b == b'\n').count() + 1;
    if height <= content_height {
        return s.to_vec();
    }
    let gap = height - content_height;
    let (_, width) = get_lines_bytes(s);
    let empty = ws.render(width, env);
    let mut out = Vec::new();
    if position == TOP {
        out.extend_from_slice(s);
        out.push(b'\n');
        for i in 0..gap {
            out.extend(empty.as_bytes());
            if i < gap - 1 {
                out.push(b'\n');
            }
        }
    } else if position == BOTTOM {
        out.extend(format!("{empty}\n").repeat(gap).as_bytes());
        out.extend_from_slice(s);
    } else {
        let split = (gap as f64 * position.clamp(0.0, 1.0)).round() as usize;
        let top = gap - split;
        out.extend(format!("{empty}\n").repeat(top).as_bytes());
        out.extend_from_slice(s);
        for _ in 0..gap - top {
            out.push(b'\n');
            out.extend(empty.as_bytes());
        }
    }
    out
}

// ── x/ansi v0.4.5: parser table, width, truncate, wrap ──────────────────────

const GROUND: u8 = 0;
const CSI_ENTRY: u8 = 1;
const CSI_INTERMEDIATE: u8 = 2;
const CSI_PARAM: u8 = 3;
const DCS_ENTRY: u8 = 4;
const DCS_INTERMEDIATE: u8 = 5;
const DCS_PARAM: u8 = 6;
const DCS_STRING: u8 = 7;
const ESCAPE: u8 = 8;
const ESCAPE_INTERMEDIATE: u8 = 9;
const OSC_STRING: u8 = 10;
const SOS_STRING: u8 = 11;
const PM_STRING: u8 = 12;
const APC_STRING: u8 = 13;
const UTF8: u8 = 14;

const NONE: u8 = 0;
const CLEAR: u8 = 1;
const COLLECT: u8 = 2;
const MARKER: u8 = 3;
const DISPATCH: u8 = 4;
const EXECUTE: u8 = 5;
const START: u8 = 6;
const PUT: u8 = 7;
const PARAM: u8 = 8;
const PRINT: u8 = 9;

const fn add_range(t: &mut [u8; 4096], start: u8, end: u8, state: u8, action: u8, next: u8) {
    let mut code = start as usize;
    while code <= end as usize {
        t[(state as usize) << 8 | code] = action << 4 | next;
        code += 1;
    }
}
const fn add(t: &mut [u8; 4096], code: u8, state: u8, action: u8, next: u8) {
    add_range(t, code, code, state, action, next);
}

/// parser.GenerateTransitionTable.
const fn transition_table() -> [u8; 4096] {
    let mut t = [NONE << 4 | GROUND; 4096];
    let mut state = GROUND;
    while state <= UTF8 {
        add(&mut t, 0x18, state, EXECUTE, GROUND);
        add(&mut t, 0x1a, state, EXECUTE, GROUND);
        add(&mut t, 0x99, state, EXECUTE, GROUND);
        add(&mut t, 0x9a, state, EXECUTE, GROUND);
        add_range(&mut t, 0x80, 0x8F, state, EXECUTE, GROUND);
        add_range(&mut t, 0x90, 0x97, state, EXECUTE, GROUND);
        add(&mut t, 0x9C, state, EXECUTE, GROUND);
        add(&mut t, 0x1B, state, CLEAR, ESCAPE);
        add(&mut t, 0x98, state, START, SOS_STRING);
        add(&mut t, 0x9E, state, START, PM_STRING);
        add(&mut t, 0x9F, state, START, APC_STRING);
        add(&mut t, 0x9B, state, CLEAR, CSI_ENTRY);
        add(&mut t, 0x90, state, CLEAR, DCS_ENTRY);
        add(&mut t, 0x9D, state, START, OSC_STRING);
        add_range(&mut t, 0xC2, 0xDF, state, COLLECT, UTF8);
        add_range(&mut t, 0xE0, 0xEF, state, COLLECT, UTF8);
        add_range(&mut t, 0xF0, 0xF4, state, COLLECT, UTF8);
        state += 1;
    }

    add_range(&mut t, 0x00, 0x17, GROUND, EXECUTE, GROUND);
    add(&mut t, 0x19, GROUND, EXECUTE, GROUND);
    add_range(&mut t, 0x1C, 0x1F, GROUND, EXECUTE, GROUND);
    add_range(&mut t, 0x20, 0x7E, GROUND, PRINT, GROUND);
    add(&mut t, 0x7F, GROUND, EXECUTE, GROUND);

    add_range(
        &mut t,
        0x00,
        0x17,
        ESCAPE_INTERMEDIATE,
        EXECUTE,
        ESCAPE_INTERMEDIATE,
    );
    add(
        &mut t,
        0x19,
        ESCAPE_INTERMEDIATE,
        EXECUTE,
        ESCAPE_INTERMEDIATE,
    );
    add_range(
        &mut t,
        0x1C,
        0x1F,
        ESCAPE_INTERMEDIATE,
        EXECUTE,
        ESCAPE_INTERMEDIATE,
    );
    add_range(
        &mut t,
        0x20,
        0x2F,
        ESCAPE_INTERMEDIATE,
        COLLECT,
        ESCAPE_INTERMEDIATE,
    );
    add(&mut t, 0x7F, ESCAPE_INTERMEDIATE, NONE, ESCAPE_INTERMEDIATE);
    add_range(&mut t, 0x30, 0x7E, ESCAPE_INTERMEDIATE, DISPATCH, GROUND);

    add_range(&mut t, 0x00, 0x17, ESCAPE, EXECUTE, ESCAPE);
    add(&mut t, 0x19, ESCAPE, EXECUTE, ESCAPE);
    add_range(&mut t, 0x1C, 0x1F, ESCAPE, EXECUTE, ESCAPE);
    add(&mut t, 0x7F, ESCAPE, NONE, ESCAPE);
    add_range(&mut t, 0x30, 0x4F, ESCAPE, DISPATCH, GROUND);
    add_range(&mut t, 0x51, 0x57, ESCAPE, DISPATCH, GROUND);
    add(&mut t, 0x59, ESCAPE, DISPATCH, GROUND);
    add(&mut t, 0x5A, ESCAPE, DISPATCH, GROUND);
    add(&mut t, 0x5C, ESCAPE, DISPATCH, GROUND);
    add_range(&mut t, 0x60, 0x7E, ESCAPE, DISPATCH, GROUND);
    add_range(&mut t, 0x20, 0x2F, ESCAPE, COLLECT, ESCAPE_INTERMEDIATE);
    add(&mut t, b'X', ESCAPE, START, SOS_STRING);
    add(&mut t, b'^', ESCAPE, START, PM_STRING);
    add(&mut t, b'_', ESCAPE, START, APC_STRING);
    add(&mut t, b'P', ESCAPE, CLEAR, DCS_ENTRY);
    add(&mut t, b'[', ESCAPE, CLEAR, CSI_ENTRY);
    add(&mut t, b']', ESCAPE, START, OSC_STRING);

    let mut state = SOS_STRING;
    while state <= APC_STRING {
        add_range(&mut t, 0x00, 0x17, state, PUT, state);
        add(&mut t, 0x19, state, PUT, state);
        add_range(&mut t, 0x1C, 0x1F, state, PUT, state);
        add_range(&mut t, 0x20, 0x7F, state, PUT, state);
        add(&mut t, 0x1B, state, DISPATCH, ESCAPE);
        add(&mut t, 0x9C, state, DISPATCH, GROUND);
        add(&mut t, 0x18, state, NONE, GROUND);
        add(&mut t, 0x1A, state, NONE, GROUND);
        state += 1;
    }

    add_range(&mut t, 0x00, 0x07, DCS_ENTRY, NONE, DCS_ENTRY);
    add_range(&mut t, 0x0E, 0x17, DCS_ENTRY, NONE, DCS_ENTRY);
    add(&mut t, 0x19, DCS_ENTRY, NONE, DCS_ENTRY);
    add_range(&mut t, 0x1C, 0x1F, DCS_ENTRY, NONE, DCS_ENTRY);
    add(&mut t, 0x7F, DCS_ENTRY, NONE, DCS_ENTRY);
    add_range(&mut t, 0x20, 0x2F, DCS_ENTRY, COLLECT, DCS_INTERMEDIATE);
    add_range(&mut t, 0x30, 0x3B, DCS_ENTRY, PARAM, DCS_PARAM);
    add_range(&mut t, 0x3C, 0x3F, DCS_ENTRY, MARKER, DCS_PARAM);
    add_range(&mut t, 0x08, 0x0D, DCS_ENTRY, PUT, DCS_STRING);
    add(&mut t, 0x1B, DCS_ENTRY, PUT, DCS_STRING);
    add_range(&mut t, 0x40, 0x7E, DCS_ENTRY, START, DCS_STRING);

    add_range(&mut t, 0x00, 0x17, DCS_INTERMEDIATE, NONE, DCS_INTERMEDIATE);
    add(&mut t, 0x19, DCS_INTERMEDIATE, NONE, DCS_INTERMEDIATE);
    add_range(&mut t, 0x1C, 0x1F, DCS_INTERMEDIATE, NONE, DCS_INTERMEDIATE);
    add_range(
        &mut t,
        0x20,
        0x2F,
        DCS_INTERMEDIATE,
        COLLECT,
        DCS_INTERMEDIATE,
    );
    add(&mut t, 0x7F, DCS_INTERMEDIATE, NONE, DCS_INTERMEDIATE);
    add_range(&mut t, 0x30, 0x3F, DCS_INTERMEDIATE, START, DCS_STRING);
    add_range(&mut t, 0x40, 0x7E, DCS_INTERMEDIATE, START, DCS_STRING);

    add_range(&mut t, 0x00, 0x17, DCS_PARAM, NONE, DCS_PARAM);
    add(&mut t, 0x19, DCS_PARAM, NONE, DCS_PARAM);
    add_range(&mut t, 0x1C, 0x1F, DCS_PARAM, NONE, DCS_PARAM);
    add_range(&mut t, 0x30, 0x3B, DCS_PARAM, PARAM, DCS_PARAM);
    add(&mut t, 0x7F, DCS_PARAM, NONE, DCS_PARAM);
    add_range(&mut t, 0x3C, 0x3F, DCS_PARAM, NONE, DCS_PARAM);
    add_range(&mut t, 0x20, 0x2F, DCS_PARAM, COLLECT, DCS_INTERMEDIATE);
    add_range(&mut t, 0x40, 0x7E, DCS_PARAM, START, DCS_STRING);

    add_range(&mut t, 0x00, 0x17, DCS_STRING, PUT, DCS_STRING);
    add(&mut t, 0x19, DCS_STRING, PUT, DCS_STRING);
    add_range(&mut t, 0x1C, 0x1F, DCS_STRING, PUT, DCS_STRING);
    add_range(&mut t, 0x20, 0x7E, DCS_STRING, PUT, DCS_STRING);
    add(&mut t, 0x7F, DCS_STRING, PUT, DCS_STRING);
    add_range(&mut t, 0x80, 0xFF, DCS_STRING, PUT, DCS_STRING);
    add(&mut t, 0x1B, DCS_STRING, DISPATCH, ESCAPE);
    add(&mut t, 0x9C, DCS_STRING, DISPATCH, GROUND);
    add(&mut t, 0x18, DCS_STRING, NONE, GROUND);
    add(&mut t, 0x1A, DCS_STRING, NONE, GROUND);

    add_range(&mut t, 0x00, 0x17, CSI_PARAM, EXECUTE, CSI_PARAM);
    add(&mut t, 0x19, CSI_PARAM, EXECUTE, CSI_PARAM);
    add_range(&mut t, 0x1C, 0x1F, CSI_PARAM, EXECUTE, CSI_PARAM);
    add_range(&mut t, 0x30, 0x3B, CSI_PARAM, PARAM, CSI_PARAM);
    add(&mut t, 0x7F, CSI_PARAM, NONE, CSI_PARAM);
    add_range(&mut t, 0x3C, 0x3F, CSI_PARAM, NONE, CSI_PARAM);
    add_range(&mut t, 0x40, 0x7E, CSI_PARAM, DISPATCH, GROUND);
    add_range(&mut t, 0x20, 0x2F, CSI_PARAM, COLLECT, CSI_INTERMEDIATE);

    add_range(
        &mut t,
        0x00,
        0x17,
        CSI_INTERMEDIATE,
        EXECUTE,
        CSI_INTERMEDIATE,
    );
    add(&mut t, 0x19, CSI_INTERMEDIATE, EXECUTE, CSI_INTERMEDIATE);
    add_range(
        &mut t,
        0x1C,
        0x1F,
        CSI_INTERMEDIATE,
        EXECUTE,
        CSI_INTERMEDIATE,
    );
    add_range(
        &mut t,
        0x20,
        0x2F,
        CSI_INTERMEDIATE,
        COLLECT,
        CSI_INTERMEDIATE,
    );
    add(&mut t, 0x7F, CSI_INTERMEDIATE, NONE, CSI_INTERMEDIATE);
    add_range(&mut t, 0x40, 0x7E, CSI_INTERMEDIATE, DISPATCH, GROUND);
    add_range(&mut t, 0x30, 0x3F, CSI_INTERMEDIATE, NONE, GROUND);

    add_range(&mut t, 0x00, 0x17, CSI_ENTRY, EXECUTE, CSI_ENTRY);
    add(&mut t, 0x19, CSI_ENTRY, EXECUTE, CSI_ENTRY);
    add_range(&mut t, 0x1C, 0x1F, CSI_ENTRY, EXECUTE, CSI_ENTRY);
    add(&mut t, 0x7F, CSI_ENTRY, NONE, CSI_ENTRY);
    add_range(&mut t, 0x40, 0x7E, CSI_ENTRY, DISPATCH, GROUND);
    add_range(&mut t, 0x20, 0x2F, CSI_ENTRY, COLLECT, CSI_INTERMEDIATE);
    add_range(&mut t, 0x30, 0x3B, CSI_ENTRY, PARAM, CSI_PARAM);
    add_range(&mut t, 0x3C, 0x3F, CSI_ENTRY, MARKER, CSI_PARAM);

    add_range(&mut t, 0x00, 0x06, OSC_STRING, NONE, OSC_STRING);
    add_range(&mut t, 0x08, 0x17, OSC_STRING, NONE, OSC_STRING);
    add(&mut t, 0x19, OSC_STRING, NONE, OSC_STRING);
    add_range(&mut t, 0x1C, 0x1F, OSC_STRING, NONE, OSC_STRING);
    add_range(&mut t, 0x20, 0xFF, OSC_STRING, PUT, OSC_STRING);
    add(&mut t, 0x1B, OSC_STRING, DISPATCH, ESCAPE);
    add(&mut t, 0x07, OSC_STRING, DISPATCH, GROUND);
    add(&mut t, 0x9C, OSC_STRING, DISPATCH, GROUND);
    add(&mut t, 0x18, OSC_STRING, NONE, GROUND);
    add(&mut t, 0x1A, OSC_STRING, NONE, GROUND);
    t
}

static TABLE: [u8; 4096] = transition_table();

fn transition(state: u8, byte: u8) -> (u8, u8) {
    let value = TABLE[(state as usize) << 8 | byte as usize];
    (value & 15, value >> 4)
}

/// ansi.StringWidth: printable ASCII counts one cell, ANSI sequences zero,
/// non-ASCII text is measured per grapheme cluster (uniseg rules).
pub(crate) fn width(s: &str) -> usize {
    width_bytes(s.as_bytes())
}

/// ansi.StringWidth over Go string bytes, including malformed UTF-8 and C1.
pub(crate) fn width_bytes(bytes: &[u8]) -> usize {
    let mut state = GROUND;
    let mut total = 0;
    let mut i = 0;
    while i < bytes.len() {
        let (next, action) = transition(state, bytes[i]);
        if next == UTF8 {
            let (len, w) = first_cluster_bytes(&bytes[i..]);
            total += w;
            i += len.max(1);
            state = GROUND;
            continue;
        }
        if action == PRINT {
            total += 1;
        }
        state = next;
        i += 1;
    }
    total
}

/// ansi.Truncate: keep at most `length` cells, appending `tail` when cut;
/// escape sequences after the cut are retained.
pub(crate) fn truncate(s: &str, length: usize, tail: &str) -> String {
    String::from_utf8_lossy(&truncate_bytes(s.as_bytes(), length, tail.as_bytes())).into_owned()
}

/// ansi.Truncate over Go string bytes. Retained clusters and escapes are copied
/// exactly; the parser decides which bytes print, execute, or enter UTF-8.
pub(crate) fn truncate_bytes(bytes: &[u8], length: usize, tail: &[u8]) -> Vec<u8> {
    if width_bytes(bytes) <= length {
        return bytes.to_vec();
    }
    let tail_width = width_bytes(tail);
    if tail_width > length {
        return Vec::new();
    }
    let length = length - tail_width;
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut current = 0;
    let mut ignoring = false;
    let mut state = GROUND;
    let mut i = 0;
    while i < bytes.len() {
        let (next, action) = transition(state, bytes[i]);
        if next == UTF8 {
            let (len, w) = first_cluster_bytes(&bytes[i..]);
            let cluster = &bytes[i..i + len.max(1)];
            i += cluster.len();
            if ignoring {
                continue;
            }
            if current + w > length {
                ignoring = true;
                out.extend_from_slice(tail);
                continue;
            }
            current += w;
            out.extend_from_slice(cluster);
            state = GROUND;
            continue;
        }
        if action == PRINT {
            if current >= length && !ignoring {
                ignoring = true;
                out.extend_from_slice(tail);
            }
            if ignoring {
                i += 1;
                continue;
            }
            current += 1;
        }
        out.push(bytes[i]);
        i += 1;
        state = next;
        if current > length && !ignoring {
            ignoring = true;
            out.extend_from_slice(tail);
        }
    }
    out
}

/// ansi.Wrap: word wrap at `limit` cells, hard-wrapping words longer than
/// the limit; `-` and any rune in `breakpoints` are break opportunities.
pub(crate) fn wrap(s: &str, limit: usize, breakpoints: &str) -> String {
    String::from_utf8_lossy(&wrap_bytes(s.as_bytes(), limit, breakpoints.as_bytes())).into_owned()
}

/// ansi.Wrap over Go string bytes. Execute bytes are re-encoded with WriteRune
/// semantics, while ANSI payloads and grapheme clusters retain their raw bytes.
pub(crate) fn wrap_bytes(bytes: &[u8], limit: usize, breakpoints: &[u8]) -> Vec<u8> {
    if limit < 1 {
        return bytes.to_vec();
    }
    let breakpoints: Vec<char> = go_runes(breakpoints).map(|(_, c)| c).collect();
    struct Wrapper {
        buf: Vec<u8>,
        word: Vec<u8>,
        space: Vec<u8>,
        current: usize,
        word_len: usize,
    }
    impl Wrapper {
        fn add_space(&mut self) {
            self.current += self.space.len();
            self.buf.extend_from_slice(&self.space);
            self.space.clear();
        }
        fn add_word(&mut self) {
            if self.word.is_empty() {
                return;
            }
            self.add_space();
            self.current += self.word_len;
            self.buf.extend_from_slice(&self.word);
            self.word.clear();
            self.word_len = 0;
        }
        fn add_newline(&mut self) {
            self.buf.push(b'\n');
            self.current = 0;
            self.space.clear();
        }
    }
    let mut w = Wrapper {
        buf: Vec::with_capacity(bytes.len() + 8),
        word: Vec::new(),
        space: Vec::new(),
        current: 0,
        word_len: 0,
    };
    let mut state = GROUND;
    let mut i = 0;
    while i < bytes.len() {
        let (next, action) = transition(state, bytes[i]);
        if next == UTF8 {
            let (len, width) = first_cluster_bytes(&bytes[i..]);
            let cluster = &bytes[i..i + len.max(1)];
            i += cluster.len();
            let (first, _) = decode_rune(cluster);
            if first.is_whitespace() && first != '\u{A0}' {
                w.add_word();
                let mut buf = [0u8; 4];
                w.space
                    .extend_from_slice(first.encode_utf8(&mut buf).as_bytes());
            } else if go_runes(cluster).any(|(_, c)| breakpoints.contains(&c)) {
                w.add_space();
                if w.current + w.word_len + width > limit {
                    w.word.extend_from_slice(cluster);
                    w.word_len += width;
                } else {
                    w.add_word();
                    w.buf.extend_from_slice(cluster);
                    w.current += width;
                }
            } else {
                if w.word_len + width > limit {
                    w.add_word();
                }
                w.word.extend_from_slice(cluster);
                w.word_len += width;
                if w.current + w.word_len + w.space.len() > limit {
                    w.add_newline();
                }
            }
            state = GROUND;
            continue;
        }
        let byte = bytes[i];
        if action == PRINT || action == EXECUTE {
            // Go uses rune(b[i]) and WriteRune for every PRINT/EXECUTE
            // transition, including raw C1 bytes in any parser state.
            // U+0085 counts as a space; other C1 runes count as words.
            let r = char::from(byte);
            let mut encoded = [0u8; 4];
            let rune = r.encode_utf8(&mut encoded).as_bytes();
            if byte == b'\n' {
                if w.word_len == 0 {
                    if w.current + w.space.len() > limit {
                        w.current = 0;
                    } else {
                        let space = std::mem::take(&mut w.space);
                        w.buf.extend_from_slice(&space);
                    }
                    w.space.clear();
                }
                w.add_word();
                w.add_newline();
            } else if r.is_whitespace() {
                w.add_word();
                w.space.extend_from_slice(rune);
            } else if byte == b'-' || breakpoints.contains(&r) {
                w.add_space();
                if w.current + w.word_len >= limit {
                    w.word.extend_from_slice(rune);
                    w.word_len += 1;
                } else {
                    w.add_word();
                    w.buf.extend_from_slice(rune);
                    w.current += 1;
                }
            } else {
                if w.current == limit {
                    w.add_newline();
                }
                w.word.extend_from_slice(rune);
                w.word_len += 1;
                if w.word_len == limit {
                    w.add_word();
                }
                if w.current + w.word_len + w.space.len() > limit {
                    w.add_newline();
                }
            }
        } else {
            w.word.push(byte);
        }
        state = next;
        i += 1;
    }
    if !w.word.is_empty() {
        if w.current + w.space.len() > limit {
            w.buf.push(b'\n');
        }
        w.add_space();
    }
    let word = std::mem::take(&mut w.word);
    w.buf.extend_from_slice(&word);
    w.buf
}

// ── Grapheme clusters and widths (uniseg v0.4.7 rules) ──────────────────────

/// Grapheme_Cluster_Break values that affect segmentation or width.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Gcb {
    Any,
    Cr,
    Lf,
    Control,
    Extend,
    Zwj,
    Ri,
    Prepend,
    SpacingMark,
    L,
    V,
    T,
    Lv,
    Lvt,
    Pict,
}

fn in_ranges(cp: u32, ranges: &[(u32, u32)]) -> bool {
    ranges
        .binary_search_by(|&(lo, hi)| {
            if hi < cp {
                std::cmp::Ordering::Less
            } else if lo > cp {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// uniseg v0.4.7 `graphemeCodePoints` (GraphemeBreakProperty 15.0.0),
/// transcribed for Control, Extend, Prepend and SpacingMark. CR, LF, ZWJ,
/// regional indicators and the Hangul L/V/T/LV/LVT classes are matched
/// directly in `grapheme_property`; Extended_Pictographic is `PICTOGRAPHIC`.
/// Adjacent rows of the same class are merged.
const GRAPHEME: &[(u32, u32, Gcb)] = {
    use Gcb::{Control as C, Extend as E, Prepend as P, SpacingMark as S};
    &[
        (0x0000, 0x0009, C),
        (0x000B, 0x000C, C),
        (0x000E, 0x001F, C),
        (0x007F, 0x009F, C),
        (0x00AD, 0x00AD, C),
        (0x0300, 0x036F, E),
        (0x0483, 0x0489, E),
        (0x0591, 0x05BD, E),
        (0x05BF, 0x05BF, E),
        (0x05C1, 0x05C2, E),
        (0x05C4, 0x05C5, E),
        (0x05C7, 0x05C7, E),
        (0x0600, 0x0605, P),
        (0x0610, 0x061A, E),
        (0x061C, 0x061C, C),
        (0x064B, 0x065F, E),
        (0x0670, 0x0670, E),
        (0x06D6, 0x06DC, E),
        (0x06DD, 0x06DD, P),
        (0x06DF, 0x06E4, E),
        (0x06E7, 0x06E8, E),
        (0x06EA, 0x06ED, E),
        (0x070F, 0x070F, P),
        (0x0711, 0x0711, E),
        (0x0730, 0x074A, E),
        (0x07A6, 0x07B0, E),
        (0x07EB, 0x07F3, E),
        (0x07FD, 0x07FD, E),
        (0x0816, 0x0819, E),
        (0x081B, 0x0823, E),
        (0x0825, 0x0827, E),
        (0x0829, 0x082D, E),
        (0x0859, 0x085B, E),
        (0x0890, 0x0891, P),
        (0x0898, 0x089F, E),
        (0x08CA, 0x08E1, E),
        (0x08E2, 0x08E2, P),
        (0x08E3, 0x0902, E),
        (0x0903, 0x0903, S),
        (0x093A, 0x093A, E),
        (0x093B, 0x093B, S),
        (0x093C, 0x093C, E),
        (0x093E, 0x0940, S),
        (0x0941, 0x0948, E),
        (0x0949, 0x094C, S),
        (0x094D, 0x094D, E),
        (0x094E, 0x094F, S),
        (0x0951, 0x0957, E),
        (0x0962, 0x0963, E),
        (0x0981, 0x0981, E),
        (0x0982, 0x0983, S),
        (0x09BC, 0x09BC, E),
        (0x09BE, 0x09BE, E),
        (0x09BF, 0x09C0, S),
        (0x09C1, 0x09C4, E),
        (0x09C7, 0x09C8, S),
        (0x09CB, 0x09CC, S),
        (0x09CD, 0x09CD, E),
        (0x09D7, 0x09D7, E),
        (0x09E2, 0x09E3, E),
        (0x09FE, 0x09FE, E),
        (0x0A01, 0x0A02, E),
        (0x0A03, 0x0A03, S),
        (0x0A3C, 0x0A3C, E),
        (0x0A3E, 0x0A40, S),
        (0x0A41, 0x0A42, E),
        (0x0A47, 0x0A48, E),
        (0x0A4B, 0x0A4D, E),
        (0x0A51, 0x0A51, E),
        (0x0A70, 0x0A71, E),
        (0x0A75, 0x0A75, E),
        (0x0A81, 0x0A82, E),
        (0x0A83, 0x0A83, S),
        (0x0ABC, 0x0ABC, E),
        (0x0ABE, 0x0AC0, S),
        (0x0AC1, 0x0AC5, E),
        (0x0AC7, 0x0AC8, E),
        (0x0AC9, 0x0AC9, S),
        (0x0ACB, 0x0ACC, S),
        (0x0ACD, 0x0ACD, E),
        (0x0AE2, 0x0AE3, E),
        (0x0AFA, 0x0AFF, E),
        (0x0B01, 0x0B01, E),
        (0x0B02, 0x0B03, S),
        (0x0B3C, 0x0B3C, E),
        (0x0B3E, 0x0B3F, E),
        (0x0B40, 0x0B40, S),
        (0x0B41, 0x0B44, E),
        (0x0B47, 0x0B48, S),
        (0x0B4B, 0x0B4C, S),
        (0x0B4D, 0x0B4D, E),
        (0x0B55, 0x0B57, E),
        (0x0B62, 0x0B63, E),
        (0x0B82, 0x0B82, E),
        (0x0BBE, 0x0BBE, E),
        (0x0BBF, 0x0BBF, S),
        (0x0BC0, 0x0BC0, E),
        (0x0BC1, 0x0BC2, S),
        (0x0BC6, 0x0BC8, S),
        (0x0BCA, 0x0BCC, S),
        (0x0BCD, 0x0BCD, E),
        (0x0BD7, 0x0BD7, E),
        (0x0C00, 0x0C00, E),
        (0x0C01, 0x0C03, S),
        (0x0C04, 0x0C04, E),
        (0x0C3C, 0x0C3C, E),
        (0x0C3E, 0x0C40, E),
        (0x0C41, 0x0C44, S),
        (0x0C46, 0x0C48, E),
        (0x0C4A, 0x0C4D, E),
        (0x0C55, 0x0C56, E),
        (0x0C62, 0x0C63, E),
        (0x0C81, 0x0C81, E),
        (0x0C82, 0x0C83, S),
        (0x0CBC, 0x0CBC, E),
        (0x0CBE, 0x0CBE, S),
        (0x0CBF, 0x0CBF, E),
        (0x0CC0, 0x0CC1, S),
        (0x0CC2, 0x0CC2, E),
        (0x0CC3, 0x0CC4, S),
        (0x0CC6, 0x0CC6, E),
        (0x0CC7, 0x0CC8, S),
        (0x0CCA, 0x0CCB, S),
        (0x0CCC, 0x0CCD, E),
        (0x0CD5, 0x0CD6, E),
        (0x0CE2, 0x0CE3, E),
        (0x0CF3, 0x0CF3, S),
        (0x0D00, 0x0D01, E),
        (0x0D02, 0x0D03, S),
        (0x0D3B, 0x0D3C, E),
        (0x0D3E, 0x0D3E, E),
        (0x0D3F, 0x0D40, S),
        (0x0D41, 0x0D44, E),
        (0x0D46, 0x0D48, S),
        (0x0D4A, 0x0D4C, S),
        (0x0D4D, 0x0D4D, E),
        (0x0D4E, 0x0D4E, P),
        (0x0D57, 0x0D57, E),
        (0x0D62, 0x0D63, E),
        (0x0D81, 0x0D81, E),
        (0x0D82, 0x0D83, S),
        (0x0DCA, 0x0DCA, E),
        (0x0DCF, 0x0DCF, E),
        (0x0DD0, 0x0DD1, S),
        (0x0DD2, 0x0DD4, E),
        (0x0DD6, 0x0DD6, E),
        (0x0DD8, 0x0DDE, S),
        (0x0DDF, 0x0DDF, E),
        (0x0DF2, 0x0DF3, S),
        (0x0E31, 0x0E31, E),
        (0x0E33, 0x0E33, S),
        (0x0E34, 0x0E3A, E),
        (0x0E47, 0x0E4E, E),
        (0x0EB1, 0x0EB1, E),
        (0x0EB3, 0x0EB3, S),
        (0x0EB4, 0x0EBC, E),
        (0x0EC8, 0x0ECE, E),
        (0x0F18, 0x0F19, E),
        (0x0F35, 0x0F35, E),
        (0x0F37, 0x0F37, E),
        (0x0F39, 0x0F39, E),
        (0x0F3E, 0x0F3F, S),
        (0x0F71, 0x0F7E, E),
        (0x0F7F, 0x0F7F, S),
        (0x0F80, 0x0F84, E),
        (0x0F86, 0x0F87, E),
        (0x0F8D, 0x0F97, E),
        (0x0F99, 0x0FBC, E),
        (0x0FC6, 0x0FC6, E),
        (0x102D, 0x1030, E),
        (0x1031, 0x1031, S),
        (0x1032, 0x1037, E),
        (0x1039, 0x103A, E),
        (0x103B, 0x103C, S),
        (0x103D, 0x103E, E),
        (0x1056, 0x1057, S),
        (0x1058, 0x1059, E),
        (0x105E, 0x1060, E),
        (0x1071, 0x1074, E),
        (0x1082, 0x1082, E),
        (0x1084, 0x1084, S),
        (0x1085, 0x1086, E),
        (0x108D, 0x108D, E),
        (0x109D, 0x109D, E),
        (0x135D, 0x135F, E),
        (0x1712, 0x1714, E),
        (0x1715, 0x1715, S),
        (0x1732, 0x1733, E),
        (0x1734, 0x1734, S),
        (0x1752, 0x1753, E),
        (0x1772, 0x1773, E),
        (0x17B4, 0x17B5, E),
        (0x17B6, 0x17B6, S),
        (0x17B7, 0x17BD, E),
        (0x17BE, 0x17C5, S),
        (0x17C6, 0x17C6, E),
        (0x17C7, 0x17C8, S),
        (0x17C9, 0x17D3, E),
        (0x17DD, 0x17DD, E),
        (0x180B, 0x180D, E),
        (0x180E, 0x180E, C),
        (0x180F, 0x180F, E),
        (0x1885, 0x1886, E),
        (0x18A9, 0x18A9, E),
        (0x1920, 0x1922, E),
        (0x1923, 0x1926, S),
        (0x1927, 0x1928, E),
        (0x1929, 0x192B, S),
        (0x1930, 0x1931, S),
        (0x1932, 0x1932, E),
        (0x1933, 0x1938, S),
        (0x1939, 0x193B, E),
        (0x1A17, 0x1A18, E),
        (0x1A19, 0x1A1A, S),
        (0x1A1B, 0x1A1B, E),
        (0x1A55, 0x1A55, S),
        (0x1A56, 0x1A56, E),
        (0x1A57, 0x1A57, S),
        (0x1A58, 0x1A5E, E),
        (0x1A60, 0x1A60, E),
        (0x1A62, 0x1A62, E),
        (0x1A65, 0x1A6C, E),
        (0x1A6D, 0x1A72, S),
        (0x1A73, 0x1A7C, E),
        (0x1A7F, 0x1A7F, E),
        (0x1AB0, 0x1ACE, E),
        (0x1B00, 0x1B03, E),
        (0x1B04, 0x1B04, S),
        (0x1B34, 0x1B3A, E),
        (0x1B3B, 0x1B3B, S),
        (0x1B3C, 0x1B3C, E),
        (0x1B3D, 0x1B41, S),
        (0x1B42, 0x1B42, E),
        (0x1B43, 0x1B44, S),
        (0x1B6B, 0x1B73, E),
        (0x1B80, 0x1B81, E),
        (0x1B82, 0x1B82, S),
        (0x1BA1, 0x1BA1, S),
        (0x1BA2, 0x1BA5, E),
        (0x1BA6, 0x1BA7, S),
        (0x1BA8, 0x1BA9, E),
        (0x1BAA, 0x1BAA, S),
        (0x1BAB, 0x1BAD, E),
        (0x1BE6, 0x1BE6, E),
        (0x1BE7, 0x1BE7, S),
        (0x1BE8, 0x1BE9, E),
        (0x1BEA, 0x1BEC, S),
        (0x1BED, 0x1BED, E),
        (0x1BEE, 0x1BEE, S),
        (0x1BEF, 0x1BF1, E),
        (0x1BF2, 0x1BF3, S),
        (0x1C24, 0x1C2B, S),
        (0x1C2C, 0x1C33, E),
        (0x1C34, 0x1C35, S),
        (0x1C36, 0x1C37, E),
        (0x1CD0, 0x1CD2, E),
        (0x1CD4, 0x1CE0, E),
        (0x1CE1, 0x1CE1, S),
        (0x1CE2, 0x1CE8, E),
        (0x1CED, 0x1CED, E),
        (0x1CF4, 0x1CF4, E),
        (0x1CF7, 0x1CF7, S),
        (0x1CF8, 0x1CF9, E),
        (0x1DC0, 0x1DFF, E),
        (0x200B, 0x200B, C),
        (0x200C, 0x200C, E),
        (0x200E, 0x200F, C),
        (0x2028, 0x202E, C),
        (0x2060, 0x206F, C),
        (0x20D0, 0x20F0, E),
        (0x2CEF, 0x2CF1, E),
        (0x2D7F, 0x2D7F, E),
        (0x2DE0, 0x2DFF, E),
        (0x302A, 0x302F, E),
        (0x3099, 0x309A, E),
        (0xA66F, 0xA672, E),
        (0xA674, 0xA67D, E),
        (0xA69E, 0xA69F, E),
        (0xA6F0, 0xA6F1, E),
        (0xA802, 0xA802, E),
        (0xA806, 0xA806, E),
        (0xA80B, 0xA80B, E),
        (0xA823, 0xA824, S),
        (0xA825, 0xA826, E),
        (0xA827, 0xA827, S),
        (0xA82C, 0xA82C, E),
        (0xA880, 0xA881, S),
        (0xA8B4, 0xA8C3, S),
        (0xA8C4, 0xA8C5, E),
        (0xA8E0, 0xA8F1, E),
        (0xA8FF, 0xA8FF, E),
        (0xA926, 0xA92D, E),
        (0xA947, 0xA951, E),
        (0xA952, 0xA953, S),
        (0xA980, 0xA982, E),
        (0xA983, 0xA983, S),
        (0xA9B3, 0xA9B3, E),
        (0xA9B4, 0xA9B5, S),
        (0xA9B6, 0xA9B9, E),
        (0xA9BA, 0xA9BB, S),
        (0xA9BC, 0xA9BD, E),
        (0xA9BE, 0xA9C0, S),
        (0xA9E5, 0xA9E5, E),
        (0xAA29, 0xAA2E, E),
        (0xAA2F, 0xAA30, S),
        (0xAA31, 0xAA32, E),
        (0xAA33, 0xAA34, S),
        (0xAA35, 0xAA36, E),
        (0xAA43, 0xAA43, E),
        (0xAA4C, 0xAA4C, E),
        (0xAA4D, 0xAA4D, S),
        (0xAA7C, 0xAA7C, E),
        (0xAAB0, 0xAAB0, E),
        (0xAAB2, 0xAAB4, E),
        (0xAAB7, 0xAAB8, E),
        (0xAABE, 0xAABF, E),
        (0xAAC1, 0xAAC1, E),
        (0xAAEB, 0xAAEB, S),
        (0xAAEC, 0xAAED, E),
        (0xAAEE, 0xAAEF, S),
        (0xAAF5, 0xAAF5, S),
        (0xAAF6, 0xAAF6, E),
        (0xABE3, 0xABE4, S),
        (0xABE5, 0xABE5, E),
        (0xABE6, 0xABE7, S),
        (0xABE8, 0xABE8, E),
        (0xABE9, 0xABEA, S),
        (0xABEC, 0xABEC, S),
        (0xABED, 0xABED, E),
        (0xFB1E, 0xFB1E, E),
        (0xFE00, 0xFE0F, E),
        (0xFE20, 0xFE2F, E),
        (0xFEFF, 0xFEFF, C),
        (0xFF9E, 0xFF9F, E),
        (0xFFF0, 0xFFFB, C),
        (0x101FD, 0x101FD, E),
        (0x102E0, 0x102E0, E),
        (0x10376, 0x1037A, E),
        (0x10A01, 0x10A03, E),
        (0x10A05, 0x10A06, E),
        (0x10A0C, 0x10A0F, E),
        (0x10A38, 0x10A3A, E),
        (0x10A3F, 0x10A3F, E),
        (0x10AE5, 0x10AE6, E),
        (0x10D24, 0x10D27, E),
        (0x10EAB, 0x10EAC, E),
        (0x10EFD, 0x10EFF, E),
        (0x10F46, 0x10F50, E),
        (0x10F82, 0x10F85, E),
        (0x11000, 0x11000, S),
        (0x11001, 0x11001, E),
        (0x11002, 0x11002, S),
        (0x11038, 0x11046, E),
        (0x11070, 0x11070, E),
        (0x11073, 0x11074, E),
        (0x1107F, 0x11081, E),
        (0x11082, 0x11082, S),
        (0x110B0, 0x110B2, S),
        (0x110B3, 0x110B6, E),
        (0x110B7, 0x110B8, S),
        (0x110B9, 0x110BA, E),
        (0x110BD, 0x110BD, P),
        (0x110C2, 0x110C2, E),
        (0x110CD, 0x110CD, P),
        (0x11100, 0x11102, E),
        (0x11127, 0x1112B, E),
        (0x1112C, 0x1112C, S),
        (0x1112D, 0x11134, E),
        (0x11145, 0x11146, S),
        (0x11173, 0x11173, E),
        (0x11180, 0x11181, E),
        (0x11182, 0x11182, S),
        (0x111B3, 0x111B5, S),
        (0x111B6, 0x111BE, E),
        (0x111BF, 0x111C0, S),
        (0x111C2, 0x111C3, P),
        (0x111C9, 0x111CC, E),
        (0x111CE, 0x111CE, S),
        (0x111CF, 0x111CF, E),
        (0x1122C, 0x1122E, S),
        (0x1122F, 0x11231, E),
        (0x11232, 0x11233, S),
        (0x11234, 0x11234, E),
        (0x11235, 0x11235, S),
        (0x11236, 0x11237, E),
        (0x1123E, 0x1123E, E),
        (0x11241, 0x11241, E),
        (0x112DF, 0x112DF, E),
        (0x112E0, 0x112E2, S),
        (0x112E3, 0x112EA, E),
        (0x11300, 0x11301, E),
        (0x11302, 0x11303, S),
        (0x1133B, 0x1133C, E),
        (0x1133E, 0x1133E, E),
        (0x1133F, 0x1133F, S),
        (0x11340, 0x11340, E),
        (0x11341, 0x11344, S),
        (0x11347, 0x11348, S),
        (0x1134B, 0x1134D, S),
        (0x11357, 0x11357, E),
        (0x11362, 0x11363, S),
        (0x11366, 0x1136C, E),
        (0x11370, 0x11374, E),
        (0x11435, 0x11437, S),
        (0x11438, 0x1143F, E),
        (0x11440, 0x11441, S),
        (0x11442, 0x11444, E),
        (0x11445, 0x11445, S),
        (0x11446, 0x11446, E),
        (0x1145E, 0x1145E, E),
        (0x114B0, 0x114B0, E),
        (0x114B1, 0x114B2, S),
        (0x114B3, 0x114B8, E),
        (0x114B9, 0x114B9, S),
        (0x114BA, 0x114BA, E),
        (0x114BB, 0x114BC, S),
        (0x114BD, 0x114BD, E),
        (0x114BE, 0x114BE, S),
        (0x114BF, 0x114C0, E),
        (0x114C1, 0x114C1, S),
        (0x114C2, 0x114C3, E),
        (0x115AF, 0x115AF, E),
        (0x115B0, 0x115B1, S),
        (0x115B2, 0x115B5, E),
        (0x115B8, 0x115BB, S),
        (0x115BC, 0x115BD, E),
        (0x115BE, 0x115BE, S),
        (0x115BF, 0x115C0, E),
        (0x115DC, 0x115DD, E),
        (0x11630, 0x11632, S),
        (0x11633, 0x1163A, E),
        (0x1163B, 0x1163C, S),
        (0x1163D, 0x1163D, E),
        (0x1163E, 0x1163E, S),
        (0x1163F, 0x11640, E),
        (0x116AB, 0x116AB, E),
        (0x116AC, 0x116AC, S),
        (0x116AD, 0x116AD, E),
        (0x116AE, 0x116AF, S),
        (0x116B0, 0x116B5, E),
        (0x116B6, 0x116B6, S),
        (0x116B7, 0x116B7, E),
        (0x1171D, 0x1171F, E),
        (0x11722, 0x11725, E),
        (0x11726, 0x11726, S),
        (0x11727, 0x1172B, E),
        (0x1182C, 0x1182E, S),
        (0x1182F, 0x11837, E),
        (0x11838, 0x11838, S),
        (0x11839, 0x1183A, E),
        (0x11930, 0x11930, E),
        (0x11931, 0x11935, S),
        (0x11937, 0x11938, S),
        (0x1193B, 0x1193C, E),
        (0x1193D, 0x1193D, S),
        (0x1193E, 0x1193E, E),
        (0x1193F, 0x1193F, P),
        (0x11940, 0x11940, S),
        (0x11941, 0x11941, P),
        (0x11942, 0x11942, S),
        (0x11943, 0x11943, E),
        (0x119D1, 0x119D3, S),
        (0x119D4, 0x119D7, E),
        (0x119DA, 0x119DB, E),
        (0x119DC, 0x119DF, S),
        (0x119E0, 0x119E0, E),
        (0x119E4, 0x119E4, S),
        (0x11A01, 0x11A0A, E),
        (0x11A33, 0x11A38, E),
        (0x11A39, 0x11A39, S),
        (0x11A3A, 0x11A3A, P),
        (0x11A3B, 0x11A3E, E),
        (0x11A47, 0x11A47, E),
        (0x11A51, 0x11A56, E),
        (0x11A57, 0x11A58, S),
        (0x11A59, 0x11A5B, E),
        (0x11A84, 0x11A89, P),
        (0x11A8A, 0x11A96, E),
        (0x11A97, 0x11A97, S),
        (0x11A98, 0x11A99, E),
        (0x11C2F, 0x11C2F, S),
        (0x11C30, 0x11C36, E),
        (0x11C38, 0x11C3D, E),
        (0x11C3E, 0x11C3E, S),
        (0x11C3F, 0x11C3F, E),
        (0x11C92, 0x11CA7, E),
        (0x11CA9, 0x11CA9, S),
        (0x11CAA, 0x11CB0, E),
        (0x11CB1, 0x11CB1, S),
        (0x11CB2, 0x11CB3, E),
        (0x11CB4, 0x11CB4, S),
        (0x11CB5, 0x11CB6, E),
        (0x11D31, 0x11D36, E),
        (0x11D3A, 0x11D3A, E),
        (0x11D3C, 0x11D3D, E),
        (0x11D3F, 0x11D45, E),
        (0x11D46, 0x11D46, P),
        (0x11D47, 0x11D47, E),
        (0x11D8A, 0x11D8E, S),
        (0x11D90, 0x11D91, E),
        (0x11D93, 0x11D94, S),
        (0x11D95, 0x11D95, E),
        (0x11D96, 0x11D96, S),
        (0x11D97, 0x11D97, E),
        (0x11EF3, 0x11EF4, E),
        (0x11EF5, 0x11EF6, S),
        (0x11F00, 0x11F01, E),
        (0x11F02, 0x11F02, P),
        (0x11F03, 0x11F03, S),
        (0x11F34, 0x11F35, S),
        (0x11F36, 0x11F3A, E),
        (0x11F3E, 0x11F3F, S),
        (0x11F40, 0x11F40, E),
        (0x11F41, 0x11F41, S),
        (0x11F42, 0x11F42, E),
        (0x13430, 0x1343F, C),
        (0x13440, 0x13440, E),
        (0x13447, 0x13455, E),
        (0x16AF0, 0x16AF4, E),
        (0x16B30, 0x16B36, E),
        (0x16F4F, 0x16F4F, E),
        (0x16F51, 0x16F87, S),
        (0x16F8F, 0x16F92, E),
        (0x16FE4, 0x16FE4, E),
        (0x16FF0, 0x16FF1, S),
        (0x1BC9D, 0x1BC9E, E),
        (0x1BCA0, 0x1BCA3, C),
        (0x1CF00, 0x1CF2D, E),
        (0x1CF30, 0x1CF46, E),
        (0x1D165, 0x1D165, E),
        (0x1D166, 0x1D166, S),
        (0x1D167, 0x1D169, E),
        (0x1D16D, 0x1D16D, S),
        (0x1D16E, 0x1D172, E),
        (0x1D173, 0x1D17A, C),
        (0x1D17B, 0x1D182, E),
        (0x1D185, 0x1D18B, E),
        (0x1D1AA, 0x1D1AD, E),
        (0x1D242, 0x1D244, E),
        (0x1DA00, 0x1DA36, E),
        (0x1DA3B, 0x1DA6C, E),
        (0x1DA75, 0x1DA75, E),
        (0x1DA84, 0x1DA84, E),
        (0x1DA9B, 0x1DA9F, E),
        (0x1DAA1, 0x1DAAF, E),
        (0x1E000, 0x1E006, E),
        (0x1E008, 0x1E018, E),
        (0x1E01B, 0x1E021, E),
        (0x1E023, 0x1E024, E),
        (0x1E026, 0x1E02A, E),
        (0x1E08F, 0x1E08F, E),
        (0x1E130, 0x1E136, E),
        (0x1E2AE, 0x1E2AE, E),
        (0x1E2EC, 0x1E2EF, E),
        (0x1E4EC, 0x1E4EF, E),
        (0x1E8D0, 0x1E8D6, E),
        (0x1E944, 0x1E94A, E),
        (0x1F3FB, 0x1F3FF, E),
        (0xE0000, 0xE001F, C),
        (0xE0020, 0xE007F, E),
        (0xE0080, 0xE00FF, C),
        (0xE0100, 0xE01EF, E),
        (0xE01F0, 0xE0FFF, C),
    ]
};

fn grapheme_class(cp: u32) -> Option<Gcb> {
    GRAPHEME
        .binary_search_by(|&(lo, hi, _)| {
            if hi < cp {
                std::cmp::Ordering::Less
            } else if lo > cp {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .ok()
        .map(|index| GRAPHEME[index].2)
}

/// East Asian Width W or F as uniseg v0.4.7 sees it (EastAsianWidth 15.0.0):
/// go-runewidth v0.0.16's `doublewidth` (W ∪ F, Unicode 15.1) minus the
/// 15.1-only assignments U+2FFC..U+2FFF and U+31EF.
fn uniseg_wide(cp: u32) -> bool {
    in_ranges(cp, DOUBLEWIDTH) && !(0x2FFC..=0x2FFF).contains(&cp) && cp != 0x31EF
}

/// Extended_Pictographic (emoji-data, Unicode 15.0).
const PICTOGRAPHIC: &[(u32, u32)] = &[
    (0x00A9, 0x00A9),
    (0x00AE, 0x00AE),
    (0x203C, 0x203C),
    (0x2049, 0x2049),
    (0x2122, 0x2122),
    (0x2139, 0x2139),
    (0x2194, 0x2199),
    (0x21A9, 0x21AA),
    (0x231A, 0x231B),
    (0x2328, 0x2328),
    (0x2388, 0x2388),
    (0x23CF, 0x23CF),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x24C2, 0x24C2),
    (0x25AA, 0x25AB),
    (0x25B6, 0x25B6),
    (0x25C0, 0x25C0),
    (0x25FB, 0x25FE),
    (0x2600, 0x2605),
    (0x2607, 0x2612),
    (0x2614, 0x2685),
    (0x2690, 0x2705),
    (0x2708, 0x2712),
    (0x2714, 0x2714),
    (0x2716, 0x2716),
    (0x271D, 0x271D),
    (0x2721, 0x2721),
    (0x2728, 0x2728),
    (0x2733, 0x2734),
    (0x2744, 0x2744),
    (0x2747, 0x2747),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2763, 0x2767),
    (0x2795, 0x2797),
    (0x27A1, 0x27A1),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2934, 0x2935),
    (0x2B05, 0x2B07),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x1F000, 0x1F0FF),
    (0x1F10D, 0x1F10F),
    (0x1F12F, 0x1F12F),
    (0x1F16C, 0x1F171),
    (0x1F17E, 0x1F17F),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F1AD, 0x1F1E5),
    (0x1F201, 0x1F20F),
    (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F),
    (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F),
    (0x1F249, 0x1F3FA),
    (0x1F400, 0x1F53D),
    (0x1F546, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F774, 0x1F77F),
    (0x1F7D5, 0x1F7FF),
    (0x1F80C, 0x1F80F),
    (0x1F848, 0x1F84F),
    (0x1F85A, 0x1F85F),
    (0x1F888, 0x1F88F),
    (0x1F8AE, 0x1F8FF),
    (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945),
    (0x1F947, 0x1FAFF),
    (0x1FC00, 0x1FFFD),
];

/// Emoji_Presentation (emoji-data, Unicode 15.0), pictographic part only.
const EMOJI_PRESENTATION: &[(u32, u32)] = &[
    (0x231A, 0x231B),
    (0x23E9, 0x23EC),
    (0x23F0, 0x23F0),
    (0x23F3, 0x23F3),
    (0x25FD, 0x25FE),
    (0x2614, 0x2615),
    (0x2648, 0x2653),
    (0x267F, 0x267F),
    (0x2693, 0x2693),
    (0x26A1, 0x26A1),
    (0x26AA, 0x26AB),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26CE, 0x26CE),
    (0x26D4, 0x26D4),
    (0x26EA, 0x26EA),
    (0x26F2, 0x26F3),
    (0x26F5, 0x26F5),
    (0x26FA, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2705, 0x2705),
    (0x270A, 0x270B),
    (0x2728, 0x2728),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2795, 0x2797),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x1F004, 0x1F004),
    (0x1F0CF, 0x1F0CF),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F201, 0x1F201),
    (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F),
    (0x1F232, 0x1F236),
    (0x1F238, 0x1F23A),
    (0x1F250, 0x1F251),
    (0x1F300, 0x1F320),
    (0x1F32D, 0x1F335),
    (0x1F337, 0x1F37C),
    (0x1F37E, 0x1F393),
    (0x1F3A0, 0x1F3CA),
    (0x1F3CF, 0x1F3D3),
    (0x1F3E0, 0x1F3F0),
    (0x1F3F4, 0x1F3F4),
    (0x1F3F8, 0x1F43E),
    (0x1F440, 0x1F440),
    (0x1F442, 0x1F4FC),
    (0x1F4FF, 0x1F53D),
    (0x1F54B, 0x1F54E),
    (0x1F550, 0x1F567),
    (0x1F57A, 0x1F57A),
    (0x1F595, 0x1F596),
    (0x1F5A4, 0x1F5A4),
    (0x1F5FB, 0x1F64F),
    (0x1F680, 0x1F6C5),
    (0x1F6CC, 0x1F6CC),
    (0x1F6D0, 0x1F6D2),
    (0x1F6D5, 0x1F6D7),
    (0x1F6DC, 0x1F6DF),
    (0x1F6EB, 0x1F6EC),
    (0x1F6F4, 0x1F6FC),
    (0x1F7E0, 0x1F7EB),
    (0x1F7F0, 0x1F7F0),
    (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945),
    (0x1F947, 0x1F9FF),
    (0x1FA70, 0x1FA7C),
    (0x1FA80, 0x1FA88),
    (0x1FA90, 0x1FABD),
    (0x1FABF, 0x1FAC5),
    (0x1FACE, 0x1FADB),
    (0x1FAE0, 0x1FAE8),
    (0x1FAF0, 0x1FAF8),
];

// go-runewidth v0.0.16 runewidth.go / runewidth_table.go, verbatim.
const NONPRINT: &[(u32, u32)] = &[
    (0x0000, 0x001F),
    (0x007F, 0x009F),
    (0x00AD, 0x00AD),
    (0x070F, 0x070F),
    (0x180B, 0x180E),
    (0x200B, 0x200F),
    (0x2028, 0x202E),
    (0x206A, 0x206F),
    (0xD800, 0xDFFF),
    (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB),
    (0xFFFE, 0xFFFF),
];

const COMBINING: &[(u32, u32)] = &[
    (0x0300, 0x036F),
    (0x0483, 0x0489),
    (0x07EB, 0x07F3),
    (0x0C00, 0x0C00),
    (0x0C04, 0x0C04),
    (0x0CF3, 0x0CF3),
    (0x0D00, 0x0D01),
    (0x135D, 0x135F),
    (0x1A7F, 0x1A7F),
    (0x1AB0, 0x1ACE),
    (0x1B6B, 0x1B73),
    (0x1DC0, 0x1DFF),
    (0x20D0, 0x20F0),
    (0x2CEF, 0x2CF1),
    (0x2DE0, 0x2DFF),
    (0x3099, 0x309A),
    (0xA66F, 0xA672),
    (0xA674, 0xA67D),
    (0xA69E, 0xA69F),
    (0xA6F0, 0xA6F1),
    (0xA8E0, 0xA8F1),
    (0xFE20, 0xFE2F),
    (0x101FD, 0x101FD),
    (0x10376, 0x1037A),
    (0x10EAB, 0x10EAC),
    (0x10F46, 0x10F50),
    (0x10F82, 0x10F85),
    (0x11300, 0x11301),
    (0x1133B, 0x1133C),
    (0x11366, 0x1136C),
    (0x11370, 0x11374),
    (0x16AF0, 0x16AF4),
    (0x1CF00, 0x1CF2D),
    (0x1CF30, 0x1CF46),
    (0x1D165, 0x1D169),
    (0x1D16D, 0x1D172),
    (0x1D17B, 0x1D182),
    (0x1D185, 0x1D18B),
    (0x1D1AA, 0x1D1AD),
    (0x1D242, 0x1D244),
    (0x1E000, 0x1E006),
    (0x1E008, 0x1E018),
    (0x1E01B, 0x1E021),
    (0x1E023, 0x1E024),
    (0x1E026, 0x1E02A),
    (0x1E08F, 0x1E08F),
    (0x1E8D0, 0x1E8D6),
];

const DOUBLEWIDTH: &[(u32, u32)] = &[
    (0x1100, 0x115F),
    (0x231A, 0x231B),
    (0x2329, 0x232A),
    (0x23E9, 0x23EC),
    (0x23F0, 0x23F0),
    (0x23F3, 0x23F3),
    (0x25FD, 0x25FE),
    (0x2614, 0x2615),
    (0x2648, 0x2653),
    (0x267F, 0x267F),
    (0x2693, 0x2693),
    (0x26A1, 0x26A1),
    (0x26AA, 0x26AB),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26CE, 0x26CE),
    (0x26D4, 0x26D4),
    (0x26EA, 0x26EA),
    (0x26F2, 0x26F3),
    (0x26F5, 0x26F5),
    (0x26FA, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2705, 0x2705),
    (0x270A, 0x270B),
    (0x2728, 0x2728),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2795, 0x2797),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x2E80, 0x2E99),
    (0x2E9B, 0x2EF3),
    (0x2F00, 0x2FD5),
    (0x2FF0, 0x303E),
    (0x3041, 0x3096),
    (0x3099, 0x30FF),
    (0x3105, 0x312F),
    (0x3131, 0x318E),
    (0x3190, 0x31E3),
    (0x31EF, 0x321E),
    (0x3220, 0x3247),
    (0x3250, 0x4DBF),
    (0x4E00, 0xA48C),
    (0xA490, 0xA4C6),
    (0xA960, 0xA97C),
    (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF),
    (0xFE10, 0xFE19),
    (0xFE30, 0xFE52),
    (0xFE54, 0xFE66),
    (0xFE68, 0xFE6B),
    (0xFF01, 0xFF60),
    (0xFFE0, 0xFFE6),
    (0x16FE0, 0x16FE4),
    (0x16FF0, 0x16FF1),
    (0x17000, 0x187F7),
    (0x18800, 0x18CD5),
    (0x18D00, 0x18D08),
    (0x1AFF0, 0x1AFF3),
    (0x1AFF5, 0x1AFFB),
    (0x1AFFD, 0x1AFFE),
    (0x1B000, 0x1B122),
    (0x1B132, 0x1B132),
    (0x1B150, 0x1B152),
    (0x1B155, 0x1B155),
    (0x1B164, 0x1B167),
    (0x1B170, 0x1B2FB),
    (0x1F004, 0x1F004),
    (0x1F0CF, 0x1F0CF),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F200, 0x1F202),
    (0x1F210, 0x1F23B),
    (0x1F240, 0x1F248),
    (0x1F250, 0x1F251),
    (0x1F260, 0x1F265),
    (0x1F300, 0x1F320),
    (0x1F32D, 0x1F335),
    (0x1F337, 0x1F37C),
    (0x1F37E, 0x1F393),
    (0x1F3A0, 0x1F3CA),
    (0x1F3CF, 0x1F3D3),
    (0x1F3E0, 0x1F3F0),
    (0x1F3F4, 0x1F3F4),
    (0x1F3F8, 0x1F43E),
    (0x1F440, 0x1F440),
    (0x1F442, 0x1F4FC),
    (0x1F4FF, 0x1F53D),
    (0x1F54B, 0x1F54E),
    (0x1F550, 0x1F567),
    (0x1F57A, 0x1F57A),
    (0x1F595, 0x1F596),
    (0x1F5A4, 0x1F5A4),
    (0x1F5FB, 0x1F64F),
    (0x1F680, 0x1F6C5),
    (0x1F6CC, 0x1F6CC),
    (0x1F6D0, 0x1F6D2),
    (0x1F6D5, 0x1F6D7),
    (0x1F6DC, 0x1F6DF),
    (0x1F6EB, 0x1F6EC),
    (0x1F6F4, 0x1F6FC),
    (0x1F7E0, 0x1F7EB),
    (0x1F7F0, 0x1F7F0),
    (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945),
    (0x1F947, 0x1F9FF),
    (0x1FA70, 0x1FA7C),
    (0x1FA80, 0x1FA88),
    (0x1FA90, 0x1FABD),
    (0x1FABF, 0x1FAC5),
    (0x1FACE, 0x1FADB),
    (0x1FAE0, 0x1FAE8),
    (0x1FAF0, 0x1FAF8),
    (0x20000, 0x2FFFD),
    (0x30000, 0x3FFFD),
];

const AMBIGUOUS: &[(u32, u32)] = &[
    (0x00A1, 0x00A1),
    (0x00A4, 0x00A4),
    (0x00A7, 0x00A8),
    (0x00AA, 0x00AA),
    (0x00AD, 0x00AE),
    (0x00B0, 0x00B4),
    (0x00B6, 0x00BA),
    (0x00BC, 0x00BF),
    (0x00C6, 0x00C6),
    (0x00D0, 0x00D0),
    (0x00D7, 0x00D8),
    (0x00DE, 0x00E1),
    (0x00E6, 0x00E6),
    (0x00E8, 0x00EA),
    (0x00EC, 0x00ED),
    (0x00F0, 0x00F0),
    (0x00F2, 0x00F3),
    (0x00F7, 0x00FA),
    (0x00FC, 0x00FC),
    (0x00FE, 0x00FE),
    (0x0101, 0x0101),
    (0x0111, 0x0111),
    (0x0113, 0x0113),
    (0x011B, 0x011B),
    (0x0126, 0x0127),
    (0x012B, 0x012B),
    (0x0131, 0x0133),
    (0x0138, 0x0138),
    (0x013F, 0x0142),
    (0x0144, 0x0144),
    (0x0148, 0x014B),
    (0x014D, 0x014D),
    (0x0152, 0x0153),
    (0x0166, 0x0167),
    (0x016B, 0x016B),
    (0x01CE, 0x01CE),
    (0x01D0, 0x01D0),
    (0x01D2, 0x01D2),
    (0x01D4, 0x01D4),
    (0x01D6, 0x01D6),
    (0x01D8, 0x01D8),
    (0x01DA, 0x01DA),
    (0x01DC, 0x01DC),
    (0x0251, 0x0251),
    (0x0261, 0x0261),
    (0x02C4, 0x02C4),
    (0x02C7, 0x02C7),
    (0x02C9, 0x02CB),
    (0x02CD, 0x02CD),
    (0x02D0, 0x02D0),
    (0x02D8, 0x02DB),
    (0x02DD, 0x02DD),
    (0x02DF, 0x02DF),
    (0x0300, 0x036F),
    (0x0391, 0x03A1),
    (0x03A3, 0x03A9),
    (0x03B1, 0x03C1),
    (0x03C3, 0x03C9),
    (0x0401, 0x0401),
    (0x0410, 0x044F),
    (0x0451, 0x0451),
    (0x2010, 0x2010),
    (0x2013, 0x2016),
    (0x2018, 0x2019),
    (0x201C, 0x201D),
    (0x2020, 0x2022),
    (0x2024, 0x2027),
    (0x2030, 0x2030),
    (0x2032, 0x2033),
    (0x2035, 0x2035),
    (0x203B, 0x203B),
    (0x203E, 0x203E),
    (0x2074, 0x2074),
    (0x207F, 0x207F),
    (0x2081, 0x2084),
    (0x20AC, 0x20AC),
    (0x2103, 0x2103),
    (0x2105, 0x2105),
    (0x2109, 0x2109),
    (0x2113, 0x2113),
    (0x2116, 0x2116),
    (0x2121, 0x2122),
    (0x2126, 0x2126),
    (0x212B, 0x212B),
    (0x2153, 0x2154),
    (0x215B, 0x215E),
    (0x2160, 0x216B),
    (0x2170, 0x2179),
    (0x2189, 0x2189),
    (0x2190, 0x2199),
    (0x21B8, 0x21B9),
    (0x21D2, 0x21D2),
    (0x21D4, 0x21D4),
    (0x21E7, 0x21E7),
    (0x2200, 0x2200),
    (0x2202, 0x2203),
    (0x2207, 0x2208),
    (0x220B, 0x220B),
    (0x220F, 0x220F),
    (0x2211, 0x2211),
    (0x2215, 0x2215),
    (0x221A, 0x221A),
    (0x221D, 0x2220),
    (0x2223, 0x2223),
    (0x2225, 0x2225),
    (0x2227, 0x222C),
    (0x222E, 0x222E),
    (0x2234, 0x2237),
    (0x223C, 0x223D),
    (0x2248, 0x2248),
    (0x224C, 0x224C),
    (0x2252, 0x2252),
    (0x2260, 0x2261),
    (0x2264, 0x2267),
    (0x226A, 0x226B),
    (0x226E, 0x226F),
    (0x2282, 0x2283),
    (0x2286, 0x2287),
    (0x2295, 0x2295),
    (0x2299, 0x2299),
    (0x22A5, 0x22A5),
    (0x22BF, 0x22BF),
    (0x2312, 0x2312),
    (0x2460, 0x24E9),
    (0x24EB, 0x254B),
    (0x2550, 0x2573),
    (0x2580, 0x258F),
    (0x2592, 0x2595),
    (0x25A0, 0x25A1),
    (0x25A3, 0x25A9),
    (0x25B2, 0x25B3),
    (0x25B6, 0x25B7),
    (0x25BC, 0x25BD),
    (0x25C0, 0x25C1),
    (0x25C6, 0x25C8),
    (0x25CB, 0x25CB),
    (0x25CE, 0x25D1),
    (0x25E2, 0x25E5),
    (0x25EF, 0x25EF),
    (0x2605, 0x2606),
    (0x2609, 0x2609),
    (0x260E, 0x260F),
    (0x261C, 0x261C),
    (0x261E, 0x261E),
    (0x2640, 0x2640),
    (0x2642, 0x2642),
    (0x2660, 0x2661),
    (0x2663, 0x2665),
    (0x2667, 0x266A),
    (0x266C, 0x266D),
    (0x266F, 0x266F),
    (0x269E, 0x269F),
    (0x26BF, 0x26BF),
    (0x26C6, 0x26CD),
    (0x26CF, 0x26D3),
    (0x26D5, 0x26E1),
    (0x26E3, 0x26E3),
    (0x26E8, 0x26E9),
    (0x26EB, 0x26F1),
    (0x26F4, 0x26F4),
    (0x26F6, 0x26F9),
    (0x26FB, 0x26FC),
    (0x26FE, 0x26FF),
    (0x273D, 0x273D),
    (0x2776, 0x277F),
    (0x2B56, 0x2B59),
    (0x3248, 0x324F),
    (0xE000, 0xF8FF),
    (0xFE00, 0xFE0F),
    (0xFFFD, 0xFFFD),
    (0x1F100, 0x1F10A),
    (0x1F110, 0x1F12D),
    (0x1F130, 0x1F169),
    (0x1F170, 0x1F18D),
    (0x1F18F, 0x1F190),
    (0x1F19B, 0x1F1AC),
    (0xE0100, 0xE01EF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
];

const NARROW: &[(u32, u32)] = &[
    (0x0020, 0x007E),
    (0x00A2, 0x00A3),
    (0x00A5, 0x00A6),
    (0x00AC, 0x00AC),
    (0x00AF, 0x00AF),
    (0x27E6, 0x27ED),
    (0x2985, 0x2986),
];

fn grapheme_property(c: char) -> Gcb {
    let cp = u32::from(c);
    match cp {
        0x0D => Gcb::Cr,
        0x0A => Gcb::Lf,
        0x200D => Gcb::Zwj,
        0x1F1E6..=0x1F1FF => Gcb::Ri,
        0x1100..=0x115F | 0xA960..=0xA97C => Gcb::L,
        0x1160..=0x11A7 | 0xD7B0..=0xD7C6 => Gcb::V,
        0x11A8..=0x11FF | 0xD7CB..=0xD7FB => Gcb::T,
        0xAC00..=0xD7A3 => {
            if (cp - 0xAC00) % 28 == 0 {
                Gcb::Lv
            } else {
                Gcb::Lvt
            }
        }
        _ => match grapheme_class(cp) {
            Some(class) => class,
            None if in_ranges(cp, PICTOGRAPHIC) => Gcb::Pict,
            None => Gcb::Any,
        },
    }
}

/// Width of one rune on its own: uniseg `StringWidth(string(r))`, which is
/// what `width` gives for a one-rune string (ASCII controls count 0).
/// bubbles paths that call go-runewidth must use [`bubbles_rune_width`].
/// Oracle comparison only: pb code measures strings with [`width`].
#[cfg(test)]
fn rune_width(c: char) -> usize {
    if c.is_ascii() {
        return usize::from(matches!(c, ' '..='~'));
    }
    property_width(c, grapheme_property(c))
}

/// uniseg runeWidth.
fn property_width(c: char, property: Gcb) -> usize {
    match property {
        Gcb::Control | Gcb::Cr | Gcb::Lf | Gcb::Extend | Gcb::Zwj => 0,
        Gcb::Ri => 2,
        Gcb::Pict => {
            if in_ranges(u32::from(c), EMOJI_PRESENTATION) {
                2
            } else {
                1
            }
        }
        _ => match c {
            '\u{2E3A}' => 3,
            '\u{2E3B}' => 4,
            _ if uniseg_wide(u32::from(c)) => 2,
            _ => 1,
        },
    }
}

/// uniseg grapheme parser states (graphemerules.go).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum GrState {
    Any,
    Cr,
    ControlLf,
    L,
    Lvv,
    Lvtt,
    Prepend,
    Pict,
    PictZwj,
    RiOdd,
    RiEven,
}

/// uniseg grTransitions: (next state, boundary, rule number).
fn gr_transition(state: GrState, property: Gcb) -> Option<(GrState, bool, u16)> {
    use GrState as S;
    Some(match (state, property) {
        (S::Any, Gcb::Cr) => (S::Cr, true, 50),
        (S::Any, Gcb::Lf | Gcb::Control) => (S::ControlLf, true, 50),
        (S::Cr | S::ControlLf, Gcb::Any) => (S::Any, true, 40),
        (S::Cr, Gcb::Lf) => (S::ControlLf, false, 30),
        (S::Any, Gcb::L) => (S::L, true, 9990),
        (S::L, Gcb::L) => (S::L, false, 60),
        (S::L, Gcb::V | Gcb::Lv) => (S::Lvv, false, 60),
        (S::L, Gcb::Lvt) => (S::Lvtt, false, 60),
        (S::Any, Gcb::Lv | Gcb::V) => (S::Lvv, true, 9990),
        (S::Lvv, Gcb::V) => (S::Lvv, false, 70),
        (S::Lvv, Gcb::T) => (S::Lvtt, false, 70),
        (S::Any, Gcb::Lvt | Gcb::T) => (S::Lvtt, true, 9990),
        (S::Lvtt, Gcb::T) => (S::Lvtt, false, 80),
        (S::Any, Gcb::Extend | Gcb::Zwj) => (S::Any, false, 90),
        (S::Any, Gcb::SpacingMark) => (S::Any, false, 91),
        (S::Any, Gcb::Prepend) => (S::Prepend, true, 9990),
        (S::Prepend, Gcb::Any) => (S::Any, false, 92),
        (S::Any, Gcb::Pict) => (S::Pict, true, 9990),
        (S::Pict, Gcb::Extend) => (S::Pict, false, 110),
        (S::Pict, Gcb::Zwj) => (S::PictZwj, false, 110),
        (S::PictZwj, Gcb::Pict) => (S::Pict, false, 110),
        (S::Any, Gcb::Ri) => (S::RiOdd, true, 9990),
        (S::RiOdd, Gcb::Ri) => (S::RiEven, false, 120),
        (S::RiEven, Gcb::Ri) => (S::RiOdd, true, 120),
        _ => return None,
    })
}

/// uniseg transitionGraphemeState: specific transition, else the
/// state-only / property-only fallbacks with the lower rule number deciding
/// the boundary, else GB999.
fn gr_step(state: GrState, property: Gcb) -> (GrState, bool) {
    if let Some((next, boundary, _)) = gr_transition(state, property) {
        return (next, boundary);
    }
    match (
        gr_transition(state, Gcb::Any),
        gr_transition(GrState::Any, property),
    ) {
        (Some((_, any_prop_boundary, any_prop_rule)), Some((next, boundary, rule))) => (
            next,
            if any_prop_rule < rule {
                any_prop_boundary
            } else {
                boundary
            },
        ),
        (Some((next, boundary, _)), None) | (None, Some((next, boundary, _))) => (next, boundary),
        (None, None) => (GrState::Any, true),
    }
}

/// uniseg FirstGraphemeClusterInString: (byte length, width) of the first
/// grapheme cluster of a non-empty string.
fn first_cluster(s: &str) -> (usize, usize) {
    first_cluster_bytes(s.as_bytes())
}

/// utf8.DecodeRune: malformed sequences consume one byte and yield RuneError.
fn decode_rune(bytes: &[u8]) -> (char, usize) {
    let Some(&first) = bytes.first() else {
        return ('\u{FFFD}', 0);
    };
    if first < 0x80 {
        return (char::from(first), 1);
    }
    let len = match first {
        0xC2..=0xDF => 2,
        0xE0..=0xEF => 3,
        0xF0..=0xF4 => 4,
        _ => return ('\u{FFFD}', 1),
    };
    if let Some(prefix) = bytes.get(..len)
        && let Ok(text) = std::str::from_utf8(prefix)
    {
        return (text.chars().next().unwrap_or('\u{FFFD}'), len);
    }
    ('\u{FFFD}', 1)
}

fn go_runes(bytes: &[u8]) -> impl Iterator<Item = (usize, char)> {
    let mut index = 0;
    std::iter::from_fn(move || {
        if index >= bytes.len() {
            return None;
        }
        let start = index;
        let (rune, len) = decode_rune(&bytes[index..]);
        index += len;
        Some((start, rune))
    })
}

/// uniseg FirstGraphemeCluster over bytes. RuneError affects segmentation and
/// width, but the returned length always selects the original input bytes.
fn first_cluster_bytes(bytes: &[u8]) -> (usize, usize) {
    let mut chars = go_runes(bytes);
    let Some((_, first)) = chars.next() else {
        return (0, 0);
    };
    let first_property = grapheme_property(first);
    // Initial state -1: only the any-state transition can match.
    let mut state = gr_transition(GrState::Any, first_property).map_or(GrState::Any, |t| t.0);
    let mut width = property_width(first, first_property);
    let mut end = decode_rune(bytes).1;
    for (index, c) in chars {
        let property = grapheme_property(c);
        let (next, boundary) = gr_step(state, property);
        if boundary {
            break;
        }
        state = next;
        if first_property == Gcb::Pict {
            if c == '\u{FE0E}' {
                width = 1;
            } else if c == '\u{FE0F}' {
                width = 2;
            }
        } else if first_property != Gcb::Ri && first_property != Gcb::L {
            width += property_width(c, property);
        }
        end = index + decode_rune(&bytes[index..]).1;
    }
    (end, width)
}

/// Grapheme clusters of `s` (uniseg.Graphemes), for oracle comparisons.
#[cfg(test)]
fn clusters(s: &str) -> impl Iterator<Item = &str> {
    let mut rest = s;
    std::iter::from_fn(move || {
        if rest.is_empty() {
            return None;
        }
        let (len, _) = first_cluster(rest);
        let (cluster, tail) = rest.split_at(len.max(1));
        rest = tail;
        Some(cluster)
    })
}

// ── go-runewidth v0.0.16 (used by bubbles textinput/textarea paths) ──────────

/// go-runewidth `RuneWidth` with `DefaultCondition`: EastAsianWidth from
/// RUNEWIDTH_EASTASIAN, else the CJK-locale test on LC_ALL / LC_CTYPE /
/// LANG (computed once, as runewidth's init does); StrictEmojiNeutral true.
pub(crate) fn bubbles_rune_width(c: char) -> usize {
    runewidth_rune(c, default_east_asian())
}

/// runewidth.EastAsianWidth after its init (handleEnv), computed once.
fn default_east_asian() -> bool {
    static EAST_ASIAN: OnceLock<bool> = OnceLock::new();
    *EAST_ASIAN.get_or_init(|| {
        runewidth_east_asian(
            &process_env("RUNEWIDTH_EASTASIAN"),
            &process_env("LC_ALL"),
            &process_env("LC_CTYPE"),
            &process_env("LANG"),
        )
    })
}

/// Condition.StringWidth for an explicit EastAsianWidth setting (oracle
/// comparison only; no pb call site uses runewidth.StringWidth).
#[cfg(test)]
fn runewidth_string(s: &str, east_asian: bool) -> usize {
    clusters(s)
        .map(|cluster| {
            cluster
                .chars()
                .map(|c| runewidth_rune(c, east_asian))
                .find(|&w| w > 0)
                .unwrap_or(0)
        })
        .sum()
}

/// Condition.RuneWidth.
fn runewidth_rune(c: char, east_asian: bool) -> usize {
    let cp = u32::from(c);
    if !east_asian {
        if cp < 0x20 || (0x7F..=0x9F).contains(&cp) || cp == 0xAD {
            0
        } else if cp < 0x300 || in_ranges(cp, NARROW) {
            1
        } else if in_ranges(cp, NONPRINT) || in_ranges(cp, COMBINING) {
            0
        } else if in_ranges(cp, DOUBLEWIDTH) {
            2
        } else {
            1
        }
    } else if in_ranges(cp, NONPRINT) || in_ranges(cp, COMBINING) {
        0
    } else if in_ranges(cp, NARROW) {
        1
    } else if in_ranges(cp, AMBIGUOUS) || in_ranges(cp, DOUBLEWIDTH) {
        2
    } else {
        1
    }
}

/// runewidth handleEnv + IsEastAsian (runewidth_posix.go).
fn runewidth_east_asian(override_env: &str, lc_all: &str, lc_ctype: &str, lang: &str) -> bool {
    if !override_env.is_empty() {
        return override_env == "1";
    }
    let locale = [lc_all, lc_ctype, lang]
        .into_iter()
        .find(|v| !v.is_empty())
        .unwrap_or("");
    if locale == "POSIX" || locale == "C" {
        return false;
    }
    let bytes = locale.as_bytes();
    if bytes.len() > 1 && bytes[0] == b'C' && matches!(bytes[1], b'.' | b'-') {
        return false;
    }
    let mut charset = match locale_charset(locale) {
        Some(charset) => go_lower(charset),
        None => go_lower(locale),
    };
    if charset.ends_with("@cjk_narrow") {
        return false;
    }
    if let Some(at) = charset.find('@') {
        charset.truncate(at);
    }
    let max = match charset.as_str() {
        "utf-8" | "utf8" => 6,
        "jis" => 8,
        "eucjp" => 3,
        "euckr" | "euccn" | "sjis" | "cp932" | "cp51932" | "cp936" | "cp949" | "cp950" | "big5"
        | "gbk" | "gb2312" => 2,
        _ => 1,
    };
    max > 1
        && (!charset.starts_with('u')
            || locale.starts_with("ja")
            || locale.starts_with("ko")
            || locale.starts_with("zh"))
}

/// The capture of `^[a-z][a-z][a-z]?(?:_[A-Z][A-Z])?\.(.+)` (RE2, `.` stops
/// at newline).
fn locale_charset(locale: &str) -> Option<&str> {
    let b = locale.as_bytes();
    let lower = |i: usize| b.get(i).is_some_and(u8::is_ascii_lowercase);
    let upper = |i: usize| b.get(i).is_some_and(u8::is_ascii_uppercase);
    if !(lower(0) && lower(1)) {
        return None;
    }
    // Leftmost-first: prefer the optional third letter and the territory.
    let starts: &[usize] = if lower(2) { &[3, 2] } else { &[2] };
    for &start in starts {
        let territory = b.get(start) == Some(&b'_') && upper(start + 1) && upper(start + 2);
        let with_territory = territory.then_some(start + 3);
        for dot in with_territory.into_iter().chain(std::iter::once(start)) {
            if b.get(dot) == Some(&b'.') {
                let rest = &locale[dot + 1..];
                let capture = rest.split('\n').next().unwrap_or("");
                if !capture.is_empty() {
                    return Some(capture);
                }
            }
        }
    }
    None
}

// ── pkg/ui chrome used by workflows (KeyGrid, Truncate) ─────────────────────

/// A palette plus renderer: what pkg/ui reads from `ui.Active` and the
/// lipgloss default renderer.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Theme {
    pub(crate) palette: &'static Palette,
    pub(crate) env: Env,
}

/// ui.KeyHint.
#[derive(Clone, Copy, Debug)]
pub(crate) struct KeyHint<'a> {
    pub(crate) key: &'a str,
    pub(crate) label: &'a str,
}

/// A Go runtime panic reached by a ported helper. Display is the Go panic
/// value; Bubble Tea's recovery prints it as `Caught panic:\n\n{value}\n\n
/// Restoring terminal...\n\n` (see the editor owner's `EditorFault` duties).
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct GoPanic(pub(crate) String);

impl std::fmt::Display for GoPanic {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// ui.Truncate: clip to `n` cells with "…", cutting by runes as Go does.
///
/// Go cuts with `string([]rune(s)[:n-1])`. Wide runes can make `n-1`
/// exceed the rune count, and then Go's slice capacity decides what
/// happens. The non-escaping `[]rune(s)` uses the runtime's zeroed 32-rune
/// stack buffer when the string has ≤32 runes; otherwise it uses a heap
/// slice whose capacity is the malloc size class divided by 4. A cut within
/// the capacity appends NUL runes. A cut beyond it panics with
/// `slice bounds out of range`, returned here as `Err(GoPanic)`.
pub(crate) fn ui_truncate(s: &str, n: isize) -> Result<String, GoPanic> {
    ui_truncate_bytes(s.as_bytes(), n).map(|out| String::from_utf8_lossy(&out).into_owned())
}

/// ui.Truncate on Go string bytes: retain raw input unless clipping is needed;
/// then decode one RuneError per invalid byte as Go's []rune conversion does.
pub(crate) fn ui_truncate_bytes(s: &[u8], n: isize) -> Result<Vec<u8>, GoPanic> {
    if n <= 0 {
        return Ok(Vec::new());
    }
    if get_lines_bytes(s).1 as isize <= n {
        return Ok(s.to_vec());
    }
    if n <= 1 {
        return Ok("…".as_bytes().to_vec());
    }
    let high = (n - 1) as usize;
    let runes: Vec<char> = go_runes(s).map(|(_, c)| c).collect();
    let capacity = go_rune_slice_capacity(runes.len());
    if high > capacity {
        return Err(GoPanic(format!(
            "runtime error: slice bounds out of range [:{high}] with capacity {capacity}"
        )));
    }
    let mut out: String = runes.iter().take(high).collect();
    out.extend(std::iter::repeat_n('\0', high.saturating_sub(runes.len())));
    out.push('…');
    Ok(out.into_bytes())
}

/// Capacity of Go 1.25 `[]rune(s)` for a non-escaping conversion of `len`
/// runes: the 32-rune `tmpBuf`, else `roundupsize(len*4)/4`.
fn go_rune_slice_capacity(len: usize) -> usize {
    const TMP_STRING_BUF_SIZE: usize = 32;
    if len <= TMP_STRING_BUF_SIZE {
        return TMP_STRING_BUF_SIZE;
    }
    go_roundupsize(len * 4) / 4
}

/// runtime.roundupsize for noscan objects: malloc size classes up to 32 KiB,
/// then whole 8 KiB pages.
fn go_roundupsize(size: usize) -> usize {
    const CLASSES: [usize; 67] = [
        8, 16, 24, 32, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 256, 288, 320,
        352, 384, 416, 448, 480, 512, 576, 640, 704, 768, 896, 1024, 1152, 1280, 1408, 1536, 1792,
        2048, 2304, 2688, 3072, 3200, 3456, 4096, 4864, 5376, 6144, 6528, 6784, 6912, 8192, 9472,
        9728, 10240, 10880, 12288, 13568, 14336, 16384, 18432, 19072, 20480, 21760, 24576, 27264,
        28672, 32768,
    ];
    match CLASSES.iter().find(|&&class| class >= size) {
        Some(&class) => class,
        None => size.div_ceil(8192) * 8192,
    }
}

fn nonnegative(n: isize) -> usize {
    n.max(0) as usize
}

impl Theme {
    /// ui.Active on the process renderer.
    pub(crate) fn process() -> Theme {
        Theme {
            palette: active(),
            env: Env::process(),
        }
    }

    fn c(&self, role: Role) -> Color {
        Color::of(self.palette, role)
    }

    fn key_grid_rows(
        &self,
        keys: &[KeyHint<'_>],
        cols: isize,
        avail_w: isize,
    ) -> Result<String, GoPanic> {
        if keys.is_empty() {
            return Ok(String::new());
        }
        let env = self.env;
        let len = keys.len() as isize;
        let rows = (len + cols - 1) / cols;
        let col_w = (avail_w / cols).max(14);
        let mut out = Vec::new();
        for ri in 0..rows {
            let mut row = Vec::new();
            for ci in 0..cols {
                let idx = ri * cols + ci;
                if idx >= len {
                    row.push(Style::new().width(nonnegative(col_w)).render_in("", env));
                    continue;
                }
                let k = keys[idx as usize];
                let key = Style::new().fg(self.c(Role::Accent)).render_in(k.key, env);
                let label = Style::new().fg(self.c(Role::Mute)).render_in(
                    &ui_truncate(k.label, col_w - block_width(k.key) as isize - 2)?,
                    env,
                );
                let cell = join_horizontal(TOP, &[&key, " ", &label]);
                row.push(Style::new().width(nonnegative(col_w)).render_in(&cell, env));
            }
            let row: Vec<&str> = row.iter().map(String::as_str).collect();
            out.push(join_horizontal(TOP, &row));
        }
        Ok(out.join("\n"))
    }

    /// ui.KeyGrid. `Err` is the Go panic `ui.truncate` can raise.
    pub(crate) fn key_grid(
        &self,
        keys: &[KeyHint<'_>],
        cols: isize,
        avail_w: isize,
    ) -> Result<String, GoPanic> {
        if cols <= 0 || avail_w <= 0 || keys.is_empty() {
            return Ok(String::new());
        }
        self.key_grid_rows(keys, cols, avail_w)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TRUE: Env = Env {
        profile: Profile::TrueColor,
        dark: true,
    };
    const PLAIN: Env = Env {
        profile: Profile::Ascii,
        dark: true,
    };
    fn env(profile: Profile, dark: bool) -> Env {
        Env { profile, dark }
    }

    #[test]
    fn palettes_hold_theme_go_values() {
        assert_eq!(DARK.hex(Role::Accent), "#9E9EF0");
        assert_eq!(LIGHT.hex(Role::Accent), "#3A3A8C");
        assert_eq!(DARK.hex(Role::Active), "#38BDF8");
        assert_eq!(LIGHT.hex(Role::OkSoft), "#047857");
        assert_eq!(LIGHT.hex(Role::Panel), "#FFFFFF");
        assert_eq!(DARK.hex(Role::Overlay), "#070710");
        assert!(LIGHT.light && !DARK.light);
    }

    #[test]
    fn load_theme_precedence_matches_ui_load_theme() {
        let never = || -> bool { panic!("background must not be consulted") };
        assert_eq!(load_theme("LIGHT", "", never), &LIGHT);
        assert_eq!(load_theme("dark", "0;15", never), &DARK);
        assert_eq!(load_theme("", "0;15", never), &LIGHT);
        assert_eq!(load_theme("auto", "15;0;15", never), &LIGHT);
        // Single field or non-15 tail falls through to the probe.
        assert_eq!(load_theme("", "15", || false), &LIGHT);
        assert_eq!(load_theme("", "15;0", || true), &DARK);
        assert_eq!(load_theme("bogus", "", || false), &LIGHT);
        assert_eq!(load_theme("", "", || true), &DARK);
    }

    #[test]
    fn adaptive_role_follows_background_not_theme() {
        let dark = Color::from(Role::Accent).sequence(false, env(Profile::TrueColor, true));
        let light = Color::from(Role::Accent).sequence(false, env(Profile::TrueColor, false));
        assert_eq!(dark.as_deref(), Some("38;2;158;158;240"));
        assert_eq!(light.as_deref(), Some("38;2;58;58;140"));
        assert_eq!(Color::of(&LIGHT, Role::Accent).sequence(false, TRUE), light);
    }

    /// Values the phase-4 metrics module recorded from pinned Go termenv.
    #[test]
    fn degraded_colors_match_go_termenv_for_known_palette_entries() {
        let cases = [
            // (role, dark, truecolor, ansi256, ansi)
            (Role::Accent, true, "38;2;158;158;240", "38;5;147", "94"),
            (Role::Body, true, "38;2;227;227;231", "38;5;188", "37"),
            (Role::Mute, true, "38;2;181;181;190", "38;5;145", "37"),
            (Role::Border, true, "38;2;73;73;92", "38;5;59", "90"),
            (Role::Accent, false, "38;2;58;58;140", "38;5;60", "94"),
            (Role::Body, false, "38;2;24;24;27", "38;5;232", "30"),
            (Role::Mute, false, "38;2;63;63;70", "38;5;59", "90"),
            (Role::Border, false, "38;2;209;213;219", "38;5;188", "37"),
        ];
        for (role, dark, truecolor, ansi256, ansi) in cases {
            let color = Color::role(role);
            assert_eq!(
                color
                    .sequence(false, env(Profile::TrueColor, dark))
                    .as_deref(),
                Some(truecolor),
                "{role:?}"
            );
            assert_eq!(
                color
                    .sequence(false, env(Profile::Ansi256, dark))
                    .as_deref(),
                Some(ansi256),
                "{role:?}"
            );
            assert_eq!(
                color.sequence(false, env(Profile::Ansi, dark)).as_deref(),
                Some(ansi),
                "{role:?}"
            );
            assert_eq!(color.sequence(false, env(Profile::Ascii, dark)), None);
        }
    }

    #[test]
    fn exact_colors_and_literal_indices_convert_like_termenv() {
        let red = Color::hex("#ff0000");
        assert_eq!(
            red.sequence(false, env(Profile::Ansi256, true)).as_deref(),
            Some("38;5;196")
        );
        assert_eq!(
            red.sequence(false, env(Profile::Ansi, true)).as_deref(),
            Some("91")
        );
        assert_eq!(
            red.sequence(true, env(Profile::Ansi, true)).as_deref(),
            Some("101")
        );
        let black = Color::hex("#000000");
        assert_eq!(
            black.sequence(true, env(Profile::Ansi256, true)).as_deref(),
            Some("48;5;16")
        );
        assert_eq!(
            black.sequence(false, env(Profile::Ansi, true)).as_deref(),
            Some("30")
        );
        assert_eq!(
            Color::ansi256(240).sequence(false, TRUE).as_deref(),
            Some("38;5;240")
        );
        assert_eq!(
            Color::ansi256(1).sequence(false, TRUE).as_deref(),
            Some("31")
        );
        assert_eq!(
            Color::ansi256(9).sequence(true, TRUE).as_deref(),
            Some("101")
        );
        assert_eq!(
            Color::hex("62").sequence(false, TRUE).as_deref(),
            Some("38;5;62")
        );
        assert_eq!(
            Color::hex("#abc").sequence(false, TRUE).as_deref(),
            Some("38;2;170;187;204")
        );
        assert_eq!(Color::hex("nope").sequence(false, TRUE), None);
        assert_eq!(Color::hex("").sequence(false, TRUE), None);
        assert_eq!(
            Color::dynamic("#FF3B30").sequence(false, TRUE),
            Color::hex("#FF3B30").sequence(false, TRUE)
        );
        assert_eq!(
            Color::dynamic("#abc").sequence(false, TRUE).as_deref(),
            Some("38;2;170;187;204")
        );
        assert_eq!(Color::dynamic("").sequence(false, TRUE), None);
    }

    #[test]
    fn width_follows_x_ansi_string_width() {
        assert_eq!(width(""), 0);
        assert_eq!(width("hello"), 5);
        assert_eq!(width("\x1b[1;38;2;1;2;3mhi\x1b[0m"), 2);
        assert_eq!(width("\x1b]8;;http://x\x07link\x1b]8;;\x07"), 4);
        assert_eq!(width("日本語"), 6);
        assert_eq!(width("e\u{301}"), 1);
        assert_eq!(width("─│┌"), 3);
        assert_eq!(width("…"), 1);
        assert_eq!(width("🙂"), 2);
        assert_eq!(width("👨\u{200D}👩\u{200D}👧"), 2);
        assert_eq!(width("🇺🇸"), 2);
        assert_eq!(width("☺"), 1);
        assert_eq!(width("☺\u{FE0F}"), 2);
        assert_eq!(width("한"), 2);
        assert_eq!(width("\u{1100}\u{1161}"), 2);
        assert_eq!(width("\u{3164}"), 2);
        assert_eq!(bubbles_rune_width('a'), 1);
        assert_eq!(width("a\tb"), 2);
        assert_eq!(rune_width('a'), 1);
        assert_eq!(rune_width('\u{7}'), 0);
        assert_eq!(rune_width('日'), 2);
        assert_eq!(rune_width('\u{301}'), 0);
        assert_eq!(block_width("ab\ncdef"), 4);
    }

    #[test]
    fn truncate_follows_x_ansi_truncate() {
        assert_eq!(truncate("hello", 5, "…"), "hello");
        assert_eq!(truncate("hello world", 5, ""), "hello");
        assert_eq!(truncate("hello world", 5, "…"), "hell…");
        assert_eq!(truncate("foo bar", 3, "..."), "...");
        assert_eq!(truncate("foo", 1, "..."), "");
        assert_eq!(
            truncate("\x1b[31mhello\x1b[0m", 3, ""),
            "\x1b[31mhel\x1b[0m"
        );
        assert_eq!(truncate("你好世界", 5, ""), "你好");
        assert_eq!(truncate("你好世界", 5, "…"), "你好…");
    }

    #[test]
    fn wrap_follows_x_ansi_wrap() {
        assert_eq!(wrap("hello world", 5, ""), "hello\nworld");
        assert_eq!(wrap("abcdefgh", 3, ""), "abc\ndef\ngh");
        assert_eq!(wrap("a b", 0, ""), "a b");
        assert_eq!(wrap("────────", 8, ""), "────────");
        assert_eq!(wrap("short", 10, ""), "short");
        assert_eq!(wrap("one two", 10, ""), "one two");
        assert_eq!(wrap("one-two", 4, ""), "one-\ntwo");
        assert_eq!(wrap("trailing  ", 20, ""), "trailing");
    }

    #[test]
    fn render_reproduces_lipgloss_sequences() {
        assert_eq!(Style::new().render_in("a\tb", TRUE), "a    b");
        assert_eq!(
            Style::new().bold(true).render_in("hi", TRUE),
            "\x1b[1mhi\x1b[0m"
        );
        assert_eq!(Style::new().bold(true).render_in("hi", PLAIN), "hi");
        assert_eq!(
            Style::new().bold(true).render_in("", TRUE),
            "\x1b[1m\x1b[0m"
        );
        // false still marks the style, but emits nothing.
        assert_eq!(Style::new().bold(false).render_in("x", TRUE), "x");
        assert_eq!(
            Style::new()
                .bold(true)
                .fg(Role::Accent)
                .render_in("x", TRUE),
            "\x1b[1;38;2;158;158;240mx\x1b[0m"
        );
        // Multi-line text is padded to the widest line with plain spaces.
        assert_eq!(Style::new().bold(true).render_in("ab\nc", PLAIN), "ab\nc ");
        assert_eq!(
            Style::new().bold(true).render_in("ab\nc", TRUE),
            "\x1b[1mab\x1b[0m\n\x1b[1mc\x1b[0m "
        );
    }

    #[test]
    fn width_padding_alignment_and_background_whitespace() {
        assert_eq!(Style::new().width(5).render_in("ab", PLAIN), "ab   ");
        assert_eq!(
            Style::new()
                .width(5)
                .align(Align::Right)
                .render_in("ab", PLAIN),
            "   ab"
        );
        assert_eq!(
            Style::new().width(5).align(CENTER).render_in("ab", PLAIN),
            " ab  "
        );
        assert_eq!(Style::new().padding(&[0, 1]).render_in("ab", PLAIN), " ab ");
        assert_eq!(
            Style::new().padding(&[1, 0]).render_in("ab", PLAIN),
            "  \nab\n  "
        );
        assert_eq!(
            Style::new().width(4).render_in("aaa bbb", PLAIN),
            "aaa \nbbb "
        );
        assert_eq!(Style::new().height(3).render_in("a", PLAIN), "a\n \n ");
        assert_eq!(
            Style::new()
                .inline(true)
                .padding(&[1])
                .render_in("a\nb", PLAIN),
            "ab"
        );
        assert_eq!(Style::new().max_width(2).render_in("abcd", PLAIN), "ab");
        assert_eq!(Style::new().max_height(1).render_in("a\nb", PLAIN), "a");
        let bg = Color::hex("#000000");
        assert_eq!(
            Style::new().bg(bg).width(3).render_in("", TRUE),
            "\x1b[48;2;0;0;0m\x1b[0m\x1b[48;2;0;0;0m   \x1b[0m"
        );
        assert_eq!(
            Style::new().bg(bg).padding(&[0, 1]).render_in("x", TRUE),
            "\x1b[48;2;0;0;0m \x1b[0m\x1b[48;2;0;0;0mx\x1b[0m\x1b[48;2;0;0;0m \x1b[0m"
        );
        assert_eq!(Style::new().margin_left(2).render_in("x", PLAIN), "  x");
    }

    #[test]
    fn borders_follow_lipgloss_apply_border() {
        assert_eq!(
            Style::new().border(Border::NORMAL).render_in("ab", PLAIN),
            "┌──┐\n│ab│\n└──┘"
        );
        assert_eq!(
            Style::new()
                .border(Border::Rounded)
                .padding(&[0, 1])
                .render_in("ab", PLAIN),
            "╭────╮\n│ ab │\n╰────╯"
        );
        assert_eq!(
            Style::new()
                .border_style(Border::NORMAL)
                .border_bottom(true)
                .render_in("ab", PLAIN),
            "ab\n──"
        );
        assert_eq!(
            Style::new()
                .border_style(Border::NORMAL)
                .border_right(true)
                .render_in("a\nbc", PLAIN),
            "a │\nbc│"
        );
        assert_eq!(
            Style::new()
                .border_sides(Border::NORMAL, &[false, false, false, true])
                .render_in("ab", PLAIN),
            "│ab"
        );
        assert_eq!(
            Style::new()
                .border_style(Border::NONE)
                .render_in("ab", PLAIN),
            "ab"
        );
        assert_eq!(
            Style::new()
                .border(Border::NORMAL)
                .border_fg(Color::hex("#ff0000"))
                .render_in("a", TRUE),
            "\x1b[38;2;255;0;0m┌─┐\x1b[0m\n\x1b[38;2;255;0;0m│\x1b[0ma\x1b[38;2;255;0;0m│\x1b[0m\n\x1b[38;2;255;0;0m└─┘\x1b[0m"
        );
        let custom = Border {
            top_left: "",
            top: "-",
            top_right: "",
            right: "|",
            bottom_right: "",
            bottom: "-",
            bottom_left: "",
            left: "",
        };
        assert_eq!(
            Style::new()
                .border_sides(custom, &[true, true, true, false])
                .render_in("ab", PLAIN),
            "-- \nab|\n-- "
        );
    }

    #[test]
    fn inherit_copies_only_unset_properties() {
        let base = Style::new()
            .fg(Role::Accent)
            .bold(true)
            .padding(&[1])
            .width(9);
        let own = Style::new().fg(Color::hex("#ff0000")).inherit(&base);
        assert_eq!(
            own.render_in("x", TRUE),
            format!("\x1b[1;38;2;255;0;0mx\x1b[0m{}", " ".repeat(8))
        );
        // Padding is not inherited; width is.
        assert_eq!(
            Style::new().inherit(&base).render_in("x", PLAIN),
            format!("x{}", " ".repeat(8))
        );
        // A background also becomes the margin background.
        let bg = Style::new().bg(Color::hex("#000000"));
        let child = Style::new().margin_left(1).inherit(&bg);
        assert_eq!(
            child.render_in("x", TRUE),
            "\x1b[48;2;0;0;0m \x1b[0m\x1b[48;2;0;0;0mx\x1b[0m"
        );
    }

    #[test]
    fn joins_and_place_follow_lipgloss_positions() {
        assert_eq!(join_horizontal(TOP, &["a\nb", "cc"]), "acc\nb  ");
        assert_eq!(join_horizontal(BOTTOM, &["a\nb", "cc"]), "a  \nbcc");
        assert_eq!(join_horizontal(CENTER, &["a\nb\nc", "x"]), "a \nbx\nc ");
        assert_eq!(join_horizontal(TOP, &["only"]), "only");
        assert_eq!(join_vertical(LEFT, &["a", "ccc"]), "a  \nccc");
        assert_eq!(join_vertical(RIGHT, &["a", "ccc"]), "  a\nccc");
        assert_eq!(join_vertical(CENTER, &["a", "ccc"]), " a \nccc");
        let ws = Whitespace::default();
        assert_eq!(
            place_with(5, 3, CENTER, CENTER, "ab", ws, PLAIN),
            "     \n ab  \n     "
        );
        assert_eq!(place_with(4, 2, LEFT, TOP, "ab", ws, PLAIN), "ab  \n    ");
        assert_eq!(
            place_with(4, 2, RIGHT, BOTTOM, "ab", ws, PLAIN),
            "    \n  ab"
        );
        assert_eq!(place_with(1, 1, CENTER, CENTER, "ab", ws, PLAIN), "ab");
    }

    #[test]
    fn ui_truncate_cuts_runes_with_go_slice_capacity() {
        let ok = |s: &str| Ok::<String, GoPanic>(s.to_owned());
        assert_eq!(ui_truncate("hello", 0), ok(""));
        assert_eq!(ui_truncate("hello", 5), ok("hello"));
        assert_eq!(ui_truncate("hello", 1), ok("…"));
        assert_eq!(ui_truncate("hello", 3), ok("he…"));
        // Cut past the rune count but within the 32-rune stack buffer: NULs.
        assert_eq!(ui_truncate("日本語", 5), ok("日本語\0…"));
        // 40 runes (heap, 160-byte class => capacity 40), cut at 59: panic.
        let wide = "日".repeat(40);
        assert_eq!(
            ui_truncate(&wide, 60),
            Err(GoPanic(
                "runtime error: slice bounds out of range [:59] with capacity 40".to_owned()
            ))
        );
        assert_eq!(go_rune_slice_capacity(3), 32);
        assert_eq!(go_rune_slice_capacity(33), 36);
        assert_eq!(go_roundupsize(40_000), 40_960);
    }

    /// /home/ajs/.cache/pb-phase7/style-oracle.txt: pinned termenv v0.15.2
    /// (Go 1.25.4, linux/amd64) Color(spec) sequences for TrueColor, ANSI256,
    /// ANSI foreground and ANSI background.
    #[test]
    fn color_conversions_match_go_oracle_table() {
        const ORACLE: &[(&str, &str, &str, &str, &str)] = &[
            ("#0B0B12", "38;2;11;11;18", "38;5;232", "30", "40"),
            ("#13131F", "38;2;19;19;31", "38;5;232", "30", "40"),
            ("#1A1A2A", "38;2;26;26;42", "38;5;232", "30", "40"),
            ("#1B1B1F", "38;2;27;27;31", "38;5;232", "30", "40"),
            ("#4A4A5C", "38;2;73;73;92", "38;5;59", "90", "100"),
            ("#2A2A3D", "38;2;42;42;60", "38;5;17", "34", "44"),
            ("#7878E0", "38;2;120;120;224", "38;5;104", "94", "104"),
            ("#F4F4F5", "38;2;243;243;245", "38;5;231", "97", "107"),
            ("#E4E4E7", "38;2;227;227;231", "38;5;188", "37", "47"),
            ("#B5B5BE", "38;2;181;181;190", "38;5;145", "37", "47"),
            ("#9595A0", "38;2;149;149;160", "38;5;103", "94", "104"),
            ("#7E7E8A", "38;2;126;126;138", "38;5;102", "90", "100"),
            ("#525260", "38;2;81;81;96", "38;5;59", "90", "100"),
            ("#9E9EF0", "38;2;158;158;240", "38;5;147", "94", "104"),
            ("#8484C8", "38;2;131;131;200", "38;5;104", "94", "104"),
            ("#2A2A4E", "38;2;42;42;78", "38;5;17", "34", "44"),
            ("#38BDF8", "38;2;56;189;248", "38;5;75", "94", "104"),
            ("#34D399", "38;2;52;211;153", "38;5;78", "92", "102"),
            ("#6EE7B7", "38;2;110;231;183", "38;5;79", "92", "102"),
            ("#1A3D3A", "38;2;26;60;58", "38;5;23", "36", "46"),
            ("#FB923C", "38;2;251;146;60", "38;5;209", "91", "101"),
            ("#F87171", "38;2;248;113;113", "38;5;203", "91", "101"),
            ("#5EEAD4", "38;2;94;234;211", "38;5;80", "96", "106"),
            ("#C084FC", "38;2;192;131;252", "38;5;141", "95", "105"),
            ("#25253A", "38;2;36;36;58", "38;5;17", "34", "44"),
            ("#26262C", "38;2;38;38;44", "38;5;232", "30", "40"),
            ("#070710", "38;2;7;7;16", "38;5;232", "30", "40"),
            ("#1E1E28", "38;2;30;30;40", "38;5;232", "30", "40"),
            ("#FBFBFD", "38;2;251;251;253", "38;5;231", "97", "107"),
            ("#FFFFFF", "38;2;255;255;255", "38;5;231", "97", "107"),
            ("#F1F1F5", "38;2;241;241;245", "38;5;231", "97", "107"),
            ("#D1D5DB", "38;2;209;213;219", "38;5;188", "37", "47"),
            ("#E5E7EB", "38;2;229;231;235", "38;5;189", "95", "105"),
            ("#5A5AC8", "38;2;89;89;200", "38;5;62", "94", "104"),
            ("#09090B", "38;2;9;9;11", "38;5;232", "30", "40"),
            ("#18181B", "38;2;24;24;27", "38;5;232", "30", "40"),
            ("#3F3F46", "38;2;63;63;70", "38;5;59", "90", "100"),
            ("#52525B", "38;2;81;81;91", "38;5;59", "90", "100"),
            ("#71717A", "38;2;113;113;121", "38;5;60", "94", "104"),
            ("#A8A8AE", "38;2;168;168;174", "38;5;145", "37", "47"),
            ("#3A3A8C", "38;2;58;58;140", "38;5;60", "94", "104"),
            ("#5151A0", "38;2;81;81;160", "38;5;61", "94", "104"),
            ("#E5E5FF", "38;2;229;229;255", "38;5;189", "95", "105"),
            ("#0284C7", "38;2;2;131;199", "38;5;32", "94", "104"),
            ("#047857", "38;2;4;120;87", "38;5;29", "32", "42"),
            ("#DCEEE7", "38;2;220;238;231", "38;5;194", "92", "102"),
            ("#C2410C", "38;2;194;65;12", "38;5;130", "91", "101"),
            ("#B91C1C", "38;2;185;28;28", "38;5;124", "31", "41"),
            ("#7E22CE", "38;2;126;34;206", "38;5;92", "94", "104"),
            ("#EEEEF9", "38;2;238;238;249", "38;5;231", "97", "107"),
            ("#EAEAEC", "38;2;234;234;236", "38;5;189", "95", "105"),
            ("#EDEDEF", "38;2;237;237;239", "38;5;231", "97", "107"),
            ("#FF3B30", "38;2;255;59;48", "38;5;203", "91", "101"),
            ("#00E5FF", "38;2;0;229;255", "38;5;45", "96", "106"),
            ("#FF2D55", "38;2;255;44;85", "38;5;197", "91", "101"),
            ("#BF5AF2", "38;2;191;89;242", "38;5;135", "95", "105"),
            ("#FF6B00", "38;2;255;107;0", "38;5;202", "91", "101"),
            ("#64D2FF", "38;2;100;210;255", "38;5;81", "96", "106"),
            ("#5E5CE6", "38;2;94;92;230", "38;5;62", "94", "104"),
            ("#FF7AB6", "38;2;255;121;182", "38;5;211", "95", "105"),
            ("#B388FF", "38;2;179;136;255", "38;5;141", "95", "105"),
            ("#FFCC99", "38;2;255;204;153", "38;5;222", "93", "103"),
            ("#00C7BE", "38;2;0;199;190", "38;5;43", "96", "106"),
            ("#A78BFA", "38;2;167;139;250", "38;5;141", "95", "105"),
            ("#04B575", "38;2;4;181;117", "38;5;36", "32", "42"),
            ("#EE6FF8", "38;2;238;111;248", "38;5;207", "95", "105"),
            ("#626262", "38;2;97;97;97", "38;5;59", "90", "100"),
            ("#909090", "38;2;144;144;144", "38;5;102", "90", "100"),
            ("#abc", "38;2;170;187;204", "38;5;146", "94", "104"),
            ("240", "38;5;240", "38;5;240", "90", "100"),
            ("62", "38;5;62", "38;5;62", "94", "104"),
            ("230", "38;5;230", "38;5;230", "93", "103"),
            ("1", "31", "31", "31", "41"),
            ("9", "91", "91", "91", "101"),
        ];
        for &(spec, truecolor, ansi256, ansi, ansi_bg) in ORACLE {
            let color = Color::dynamic(spec);
            let seq = |profile, bg| color.sequence(bg, env(profile, true));
            assert_eq!(
                seq(Profile::TrueColor, false).as_deref(),
                Some(truecolor),
                "{spec}"
            );
            assert_eq!(
                seq(Profile::Ansi256, false).as_deref(),
                Some(ansi256),
                "{spec}"
            );
            assert_eq!(seq(Profile::Ansi, false).as_deref(), Some(ansi), "{spec}");
            assert_eq!(seq(Profile::Ansi, true).as_deref(), Some(ansi_bg), "{spec}");
            assert_eq!(
                Color::hex(spec)
                    .sequence(false, env(Profile::Ansi256, true))
                    .as_deref(),
                Some(ansi256)
            );
        }
        // Every palette entry is covered by the table.
        for palette in [&DARK, &LIGHT] {
            for role in ALL_ROLES {
                let hex = palette.hex(role);
                assert!(
                    ORACLE.iter().any(|row| row.0 == hex),
                    "{hex} missing from oracle"
                );
            }
        }
    }

    const ALL_ROLES: [Role; 30] = [
        Role::Bg,
        Role::Panel,
        Role::PanelAlt,
        Role::EditorBg,
        Role::Border,
        Role::BorderSoft,
        Role::BorderHi,
        Role::Text,
        Role::Body,
        Role::Mute,
        Role::Dim,
        Role::Faint,
        Role::Ghost,
        Role::Accent,
        Role::Accent2,
        Role::AccentSoft,
        Role::Active,
        Role::Ok,
        Role::OkSoft,
        Role::OkSoftBg,
        Role::Warn,
        Role::Err,
        Role::String,
        Role::Number,
        Role::SelRow,
        Role::Cursor,
        Role::EditorActive,
        Role::InvertText,
        Role::Overlay,
        Role::BadgeBg,
    ];

    /// /home/ajs/.cache/pb-phase7/style-width-oracle.txt: x/ansi.StringWidth,
    /// runewidth.StringWidth (non-CJK locale) and uniseg.StringWidth.
    #[test]
    fn widths_match_go_width_oracle() {
        const ORACLE: &[(&str, usize, usize, usize)] = &[
            ("e\u{301}", 1, 1, 1),
            ("\u{263A}", 1, 1, 1),
            ("\u{263A}\u{FE0F}", 2, 1, 2),
            ("\u{231A}\u{FE0E}", 1, 2, 1),
            ("\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}", 2, 2, 2),
            ("\u{1F1FA}\u{1F1F8}", 2, 1, 2),
            ("\u{1F1FA}", 2, 1, 2),
            ("\u{1F3FB}", 0, 2, 0),
            ("\u{D55C}", 2, 2, 2),
            ("\u{1100}\u{1161}", 2, 2, 2),
            ("\u{AC00}\u{11A8}", 3, 2, 3),
            ("\u{3030}", 1, 2, 1),
            ("\u{3164}", 2, 2, 2),
            ("\u{AD}", 0, 0, 0),
            ("\u{200B}", 0, 0, 0),
            ("\u{600}1", 2, 1, 2),
            ("\u{915}\u{93E}", 2, 1, 2),
            ("日本語", 6, 6, 6),
            ("…", 1, 1, 1),
            ("─", 1, 1, 1),
        ];
        for &(s, ansi, runewidth, uniseg) in ORACLE {
            assert_eq!(width(s), ansi, "ansi {s:?}");
            assert_eq!(runewidth_string(s, false), runewidth, "runewidth {s:?}");
            let summed: usize = clusters(s).map(|c| first_cluster(c).1).sum();
            assert_eq!(summed, uniseg, "uniseg {s:?}");
        }
        // CJK locales make ambiguous runes (box drawing, ellipsis) double.
        assert_eq!(runewidth_string("─…", true), 4);
        assert_eq!(runewidth_rune('\u{301}', true), 0);
    }

    /// Every code point (surrogates excluded) against the pinned Go dump
    /// `style-oracle2-widths.txt`: `first last uniseg runewidth runewidthEA`.
    #[test]
    fn every_codepoint_width_matches_go_dump() {
        let mut next = 0u32;
        for line in WIDTH_DUMP.lines().filter(|l| !l.trim().is_empty()) {
            let f: Vec<&str> = line.split_whitespace().collect();
            let hex = |s: &str| u32::from_str_radix(s, 16).expect("hex");
            let (first, last) = (hex(f[0]), hex(f[1]));
            let num = |s: &str| s.parse::<usize>().expect("num");
            let (uniseg, narrow, wide) = (num(f[2]), num(f[3]), num(f[4]));
            assert_eq!(first, next, "dump gap before {first:X}");
            for cp in first..=last {
                let Some(c) = char::from_u32(cp) else {
                    continue;
                };
                assert_eq!(rune_width(c), uniseg, "uniseg U+{cp:04X}");
                let mut buf = [0u8; 4];
                assert_eq!(width(c.encode_utf8(&mut buf)), uniseg, "ansi U+{cp:04X}");
                assert_eq!(runewidth_rune(c, false), narrow, "runewidth U+{cp:04X}");
                assert_eq!(runewidth_rune(c, true), wide, "runewidth EA U+{cp:04X}");
            }
            next = if last == 0xD7FF { 0xE000 } else { last + 1 };
        }
        assert_eq!(next, 0x110000);
    }

    /// Independent pinned-Go byte fixtures. Covers every C1 byte in all 14
    /// persistent ANSI states, every single input byte, malformed UTF-8,
    /// runes after invalid bytes, capacity faults, joins and default placement.
    #[test]
    fn byte_primitives_match_pinned_go_oracle() {
        fn unhex(s: &str) -> Vec<u8> {
            s.as_bytes()
                .chunks_exact(2)
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect()
        }
        let fixtures = include_str!("../../docs/evidence/phase7/style-byte-oracle-fixtures.tsv");
        for line in fixtures.lines() {
            let f: Vec<&str> = line.split('\t').collect();
            assert_eq!(f.len(), 13);
            let input = unhex(f[1]);
            let n: usize = f[2].parse().unwrap();
            let tail = unhex(f[3]);
            let breakpoints = unhex(f[4]);
            assert_eq!(
                width_bytes(&input),
                f[5].parse::<usize>().unwrap(),
                "{} width",
                f[0]
            );
            let outputs = [
                truncate_bytes(&input, n, &tail),
                wrap_bytes(&input, n, &breakpoints),
                ui_truncate_bytes(&input, n as isize)
                    .unwrap_or_else(|e| format!("panic:{e}").into_bytes()),
                join_horizontal_bytes(TOP, &[&input, b"AB\nC"]),
                join_vertical_bytes(RIGHT, &[&input, b"AB\nC"]),
                place_bytes(9, 4, CENTER, CENTER, &input),
                place_bytes(9, 4, 0.25, 0.75, &input),
            ];
            for (index, output) in outputs.into_iter().enumerate() {
                assert_eq!(output, unhex(f[index + 6]), "{} operation {index}", f[0]);
            }
        }
    }

    #[test]
    fn byte_render_preserves_go_payload_and_string_wrap_contract() {
        let raw = b"\xb5\x8b\xe8\xaf\x95";
        assert_eq!(width_bytes(raw), 2);
        assert_eq!(ui_truncate_bytes(raw, 2).unwrap(), raw);
        let s = Style::new().fg(Color::hex("#e3e3e7"));
        assert_eq!(
            s.render_bytes_in(raw, TRUE),
            b"\x1b[38;2;227;227;231m\xb5\x8b\xe8\xaf\x95\x1b[0m"
        );
        // The final Width render executes the raw 8B byte as a rune and
        // counts it as one word cell, while B5 remains a raw payload byte.
        assert_eq!(
            Style::new().width(3).render_bytes_in(raw, PLAIN),
            b"\xb5\xc2\x8b\xe8\xaf\x95 "
        );
        let input = "x\x1b]0;t\u{2705}y z";
        let style = Style::new().width(3);
        assert_eq!(
            wrap_bytes(input.as_bytes(), 3, b""),
            b"x\x1b]0;t\xe2\x9c\ny z"
        );
        // L-STYLE-1 is fixed only when the caller carries the byte path
        // through emission. Existing &str callers keep their lossy boundary.
        assert_eq!(
            style.render_bytes_in(input.as_bytes(), PLAIN),
            b"x\x1b]0;t\xe2\x9c  \ny z"
        );
        assert_eq!(style.render_in(input, PLAIN), "x\x1b]0;t\u{FFFD}  \ny z");
        let mut underline = Style::new().width(7).inline(true);
        underline.underline = true;
        underline.props |= P_UNDERLINE;
        // Go's space styler loops over []rune, so this branch replaces each
        // invalid byte separately rather than preserving it as a payload.
        assert_eq!(
            underline.render_bytes_in(b"\xe2\x82", PLAIN),
            "\u{FFFD}\u{FFFD}     ".as_bytes()
        );
        for env in [PLAIN, TRUE] {
            for text in ["", "ASCII", "试", "é", "a\r\nb", "a\tb", "👨‍👩‍👧"] {
                assert_eq!(
                    s.render_bytes_in(text.as_bytes(), env),
                    s.render_in(text, env).as_bytes()
                );
            }
        }
    }

    /// `style-oracle2-cases.txt` (pinned lipgloss v1.0.0, x/ansi v0.4.5,
    /// pkg/ui at 07af390; ui.* with PB_THEME=dark and the Ascii profile).
    #[test]
    fn cases_match_go_oracle2() {
        // x/ansi
        assert_eq!(width("\x1b[31\u{e9}m"), 2);
        assert_eq!(width("\u{600}1"), 2);
        assert_eq!(
            truncate("a\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}b", 2, ""),
            "a"
        );
        assert_eq!(
            truncate("\x1b[31m你好\x1b[0m世", 3, "…"),
            "\x1b[31m你…\x1b[0m"
        );
        assert_eq!(wrap("aaa bbb", 4, ""), "aaa\nbbb");
        assert_eq!(wrap("日本語の文章です", 5, ""), "日本\n語の\n文章\nです");
        assert_eq!(wrap("a  \nb", 2, ""), "a\nb");
        assert_eq!(
            wrap("\x1b[1mbold words here\x1b[0m", 6, ""),
            "\x1b[1mbold\nwords\nhere\x1b[0m"
        );
        assert_eq!(wrap("path/to/file", 5, "/"), "path/\nto/\nfile");
        // The &str facade keeps L-STYLE-1's lossy boundary; the byte
        // renderer has a separate exact-output assertion above.
        assert_eq!(
            wrap("x\x1b]0;t\u{2705}y z", 3, ""),
            "x\x1b]0;t\u{FFFD}\ny z"
        );
        // lipgloss
        let red = Color::hex("#ff0000");
        assert_eq!(
            Style::new()
                .reverse(true)
                .padding(&[0, 1])
                .fg(red)
                .render_in("x", TRUE),
            "\x1b[7;38;2;255;0;0m \x1b[0m\x1b[7;38;2;255;0;0mx\x1b[0m\x1b[7;38;2;255;0;0m \x1b[0m"
        );
        assert_eq!(
            Style::new().border(Border::NORMAL).render_in("日本", PLAIN),
            "┌────┐\n│日本│\n└────┘"
        );
        assert_eq!(
            Style::new().width(5).render_in("日本語の文章", PLAIN),
            "日本 \n語の \n文章 "
        );
        assert_eq!(join_horizontal(0.3, &["a\nb\nc\nd", "x"]), "a \nbx\nc \nd ");
        assert_eq!(join_vertical(CENTER, &["a", "cccc"]), "  a \ncccc");
        let ws = Whitespace::default();
        assert_eq!(
            place_with(7, 1, CENTER, CENTER, "日本", ws, PLAIN),
            " 日本  "
        );
        assert_eq!(
            ui_truncate(&"日".repeat(33), 40),
            Err(GoPanic(
                "runtime error: slice bounds out of range [:39] with capacity 36".to_owned()
            ))
        );
    }

    #[test]
    fn runewidth_locale_detection_follows_handle_env() {
        assert!(runewidth_east_asian("1", "", "", ""));
        assert!(!runewidth_east_asian("0", "", "", "ja_JP.UTF-8"));
        assert!(runewidth_east_asian("", "", "", "ja_JP.UTF-8"));
        assert!(!runewidth_east_asian("", "", "", "en_US.UTF-8"));
        assert!(runewidth_east_asian("", "", "", "zh_CN.GB2312"));
        assert!(runewidth_east_asian("", "", "", "en_US.eucJP"));
        assert!(!runewidth_east_asian("", "", "", "ja_JP.UTF-8@cjk_narrow"));
        assert!(!runewidth_east_asian("", "C.UTF-8", "ja_JP.UTF-8", ""));
        assert!(!runewidth_east_asian("", "POSIX", "", "ja_JP.UTF-8"));
        assert!(runewidth_east_asian("", "", "ko_KR.UTF-8", "C"));
        assert!(!runewidth_east_asian("", "", "", "ko"));
        assert!(!runewidth_east_asian("", "", "", ""));
        assert_eq!(locale_charset("ja_JP.UTF-8"), Some("UTF-8"));
        assert_eq!(locale_charset("jpn.sjis"), Some("sjis"));
        assert_eq!(locale_charset("ja_jp.UTF-8"), None);
        assert_eq!(locale_charset("ja.\nx"), None);
    }

    #[test]
    fn grapheme_segmentation_follows_uniseg_transitions() {
        fn split(s: &str) -> Vec<&str> {
            clusters(s).collect()
        }
        assert_eq!(split("a\r\nb"), ["a", "\r\n", "b"]);
        assert_eq!(split("\u{600}1x"), ["\u{600}1", "x"]);
        assert_eq!(
            split("\u{915}\u{93E}\u{915}"),
            ["\u{915}\u{93E}", "\u{915}"]
        );
        assert_eq!(
            split("\u{1F1FA}\u{1F1F8}\u{1F1FA}"),
            ["\u{1F1FA}\u{1F1F8}", "\u{1F1FA}"]
        );
        assert_eq!(
            split("\u{1100}\u{300}\u{1161}"),
            ["\u{1100}\u{300}", "\u{1161}"]
        );
        // Prepend before a control still breaks (GB5 has the lower rule).
        assert_eq!(split("\u{600}\n"), ["\u{600}", "\n"]);
        // Pict ZWJ Extend Pict breaks before the second pictograph.
        assert_eq!(
            split("\u{1F468}\u{200D}\u{301}\u{1F469}"),
            ["\u{1F468}\u{200D}\u{301}", "\u{1F469}"]
        );
    }

    /// Verbatim /home/ajs/.cache/pb-phase7/style-oracle2-widths.txt, produced
    /// by `go run . widths` in /home/ajs/.cache/pb-phase7/style-oracle2/
    /// (Go 1.25.4 linux/amd64; uniseg v0.4.7, go-runewidth v0.0.16). Columns:
    /// first last uniseg.StringWidth(string(r)) RuneWidth(EA=false)
    /// RuneWidth(EA=true); surrogates skipped.
    const WIDTH_DUMP: &str = "\
0 1F 0 0 0
20 7E 1 1 1
7F 9F 0 0 0
A0 A0 1 1 1
A1 A1 1 1 2
A2 A3 1 1 1
A4 A4 1 1 2
A5 A6 1 1 1
A7 A8 1 1 2
A9 A9 1 1 1
AA AA 1 1 2
AB AC 1 1 1
AD AD 0 0 0
AE AE 1 1 2
AF AF 1 1 1
B0 B4 1 1 2
B5 B5 1 1 1
B6 BA 1 1 2
BB BB 1 1 1
BC BF 1 1 2
C0 C5 1 1 1
C6 C6 1 1 2
C7 CF 1 1 1
D0 D0 1 1 2
D1 D6 1 1 1
D7 D8 1 1 2
D9 DD 1 1 1
DE E1 1 1 2
E2 E5 1 1 1
E6 E6 1 1 2
E7 E7 1 1 1
E8 EA 1 1 2
EB EB 1 1 1
EC ED 1 1 2
EE EF 1 1 1
F0 F0 1 1 2
F1 F1 1 1 1
F2 F3 1 1 2
F4 F6 1 1 1
F7 FA 1 1 2
FB FB 1 1 1
FC FC 1 1 2
FD FD 1 1 1
FE FE 1 1 2
FF 100 1 1 1
101 101 1 1 2
102 110 1 1 1
111 111 1 1 2
112 112 1 1 1
113 113 1 1 2
114 11A 1 1 1
11B 11B 1 1 2
11C 125 1 1 1
126 127 1 1 2
128 12A 1 1 1
12B 12B 1 1 2
12C 130 1 1 1
131 133 1 1 2
134 137 1 1 1
138 138 1 1 2
139 13E 1 1 1
13F 142 1 1 2
143 143 1 1 1
144 144 1 1 2
145 147 1 1 1
148 14B 1 1 2
14C 14C 1 1 1
14D 14D 1 1 2
14E 151 1 1 1
152 153 1 1 2
154 165 1 1 1
166 167 1 1 2
168 16A 1 1 1
16B 16B 1 1 2
16C 1CD 1 1 1
1CE 1CE 1 1 2
1CF 1CF 1 1 1
1D0 1D0 1 1 2
1D1 1D1 1 1 1
1D2 1D2 1 1 2
1D3 1D3 1 1 1
1D4 1D4 1 1 2
1D5 1D5 1 1 1
1D6 1D6 1 1 2
1D7 1D7 1 1 1
1D8 1D8 1 1 2
1D9 1D9 1 1 1
1DA 1DA 1 1 2
1DB 1DB 1 1 1
1DC 1DC 1 1 2
1DD 250 1 1 1
251 251 1 1 2
252 260 1 1 1
261 261 1 1 2
262 2C3 1 1 1
2C4 2C4 1 1 2
2C5 2C6 1 1 1
2C7 2C7 1 1 2
2C8 2C8 1 1 1
2C9 2CB 1 1 2
2CC 2CC 1 1 1
2CD 2CD 1 1 2
2CE 2CF 1 1 1
2D0 2D0 1 1 2
2D1 2D7 1 1 1
2D8 2DB 1 1 2
2DC 2DC 1 1 1
2DD 2DD 1 1 2
2DE 2DE 1 1 1
2DF 2DF 1 1 2
2E0 2FF 1 1 1
300 36F 0 0 0
370 390 1 1 1
391 3A1 1 1 2
3A2 3A2 1 1 1
3A3 3A9 1 1 2
3AA 3B0 1 1 1
3B1 3C1 1 1 2
3C2 3C2 1 1 1
3C3 3C9 1 1 2
3CA 400 1 1 1
401 401 1 1 2
402 40F 1 1 1
410 44F 1 1 2
450 450 1 1 1
451 451 1 1 2
452 482 1 1 1
483 489 0 0 0
48A 590 1 1 1
591 5BD 0 1 1
5BE 5BE 1 1 1
5BF 5BF 0 1 1
5C0 5C0 1 1 1
5C1 5C2 0 1 1
5C3 5C3 1 1 1
5C4 5C5 0 1 1
5C6 5C6 1 1 1
5C7 5C7 0 1 1
5C8 60F 1 1 1
610 61A 0 1 1
61B 61B 1 1 1
61C 61C 0 1 1
61D 64A 1 1 1
64B 65F 0 1 1
660 66F 1 1 1
670 670 0 1 1
671 6D5 1 1 1
6D6 6DC 0 1 1
6DD 6DE 1 1 1
6DF 6E4 0 1 1
6E5 6E6 1 1 1
6E7 6E8 0 1 1
6E9 6E9 1 1 1
6EA 6ED 0 1 1
6EE 70E 1 1 1
70F 70F 1 0 0
710 710 1 1 1
711 711 0 1 1
712 72F 1 1 1
730 74A 0 1 1
74B 7A5 1 1 1
7A6 7B0 0 1 1
7B1 7EA 1 1 1
7EB 7F3 0 0 0
7F4 7FC 1 1 1
7FD 7FD 0 1 1
7FE 815 1 1 1
816 819 0 1 1
81A 81A 1 1 1
81B 823 0 1 1
824 824 1 1 1
825 827 0 1 1
828 828 1 1 1
829 82D 0 1 1
82E 858 1 1 1
859 85B 0 1 1
85C 897 1 1 1
898 89F 0 1 1
8A0 8C9 1 1 1
8CA 8E1 0 1 1
8E2 8E2 1 1 1
8E3 902 0 1 1
903 939 1 1 1
93A 93A 0 1 1
93B 93B 1 1 1
93C 93C 0 1 1
93D 940 1 1 1
941 948 0 1 1
949 94C 1 1 1
94D 94D 0 1 1
94E 950 1 1 1
951 957 0 1 1
958 961 1 1 1
962 963 0 1 1
964 980 1 1 1
981 981 0 1 1
982 9BB 1 1 1
9BC 9BC 0 1 1
9BD 9BD 1 1 1
9BE 9BE 0 1 1
9BF 9C0 1 1 1
9C1 9C4 0 1 1
9C5 9CC 1 1 1
9CD 9CD 0 1 1
9CE 9D6 1 1 1
9D7 9D7 0 1 1
9D8 9E1 1 1 1
9E2 9E3 0 1 1
9E4 9FD 1 1 1
9FE 9FE 0 1 1
9FF A00 1 1 1
A01 A02 0 1 1
A03 A3B 1 1 1
A3C A3C 0 1 1
A3D A40 1 1 1
A41 A42 0 1 1
A43 A46 1 1 1
A47 A48 0 1 1
A49 A4A 1 1 1
A4B A4D 0 1 1
A4E A50 1 1 1
A51 A51 0 1 1
A52 A6F 1 1 1
A70 A71 0 1 1
A72 A74 1 1 1
A75 A75 0 1 1
A76 A80 1 1 1
A81 A82 0 1 1
A83 ABB 1 1 1
ABC ABC 0 1 1
ABD AC0 1 1 1
AC1 AC5 0 1 1
AC6 AC6 1 1 1
AC7 AC8 0 1 1
AC9 ACC 1 1 1
ACD ACD 0 1 1
ACE AE1 1 1 1
AE2 AE3 0 1 1
AE4 AF9 1 1 1
AFA AFF 0 1 1
B00 B00 1 1 1
B01 B01 0 1 1
B02 B3B 1 1 1
B3C B3C 0 1 1
B3D B3D 1 1 1
B3E B3F 0 1 1
B40 B40 1 1 1
B41 B44 0 1 1
B45 B4C 1 1 1
B4D B4D 0 1 1
B4E B54 1 1 1
B55 B57 0 1 1
B58 B61 1 1 1
B62 B63 0 1 1
B64 B81 1 1 1
B82 B82 0 1 1
B83 BBD 1 1 1
BBE BBE 0 1 1
BBF BBF 1 1 1
BC0 BC0 0 1 1
BC1 BCC 1 1 1
BCD BCD 0 1 1
BCE BD6 1 1 1
BD7 BD7 0 1 1
BD8 BFF 1 1 1
C00 C00 0 0 0
C01 C03 1 1 1
C04 C04 0 0 0
C05 C3B 1 1 1
C3C C3C 0 1 1
C3D C3D 1 1 1
C3E C40 0 1 1
C41 C45 1 1 1
C46 C48 0 1 1
C49 C49 1 1 1
C4A C4D 0 1 1
C4E C54 1 1 1
C55 C56 0 1 1
C57 C61 1 1 1
C62 C63 0 1 1
C64 C80 1 1 1
C81 C81 0 1 1
C82 CBB 1 1 1
CBC CBC 0 1 1
CBD CBE 1 1 1
CBF CBF 0 1 1
CC0 CC1 1 1 1
CC2 CC2 0 1 1
CC3 CC5 1 1 1
CC6 CC6 0 1 1
CC7 CCB 1 1 1
CCC CCD 0 1 1
CCE CD4 1 1 1
CD5 CD6 0 1 1
CD7 CE1 1 1 1
CE2 CE3 0 1 1
CE4 CF2 1 1 1
CF3 CF3 1 0 0
CF4 CFF 1 1 1
D00 D01 0 0 0
D02 D3A 1 1 1
D3B D3C 0 1 1
D3D D3D 1 1 1
D3E D3E 0 1 1
D3F D40 1 1 1
D41 D44 0 1 1
D45 D4C 1 1 1
D4D D4D 0 1 1
D4E D56 1 1 1
D57 D57 0 1 1
D58 D61 1 1 1
D62 D63 0 1 1
D64 D80 1 1 1
D81 D81 0 1 1
D82 DC9 1 1 1
DCA DCA 0 1 1
DCB DCE 1 1 1
DCF DCF 0 1 1
DD0 DD1 1 1 1
DD2 DD4 0 1 1
DD5 DD5 1 1 1
DD6 DD6 0 1 1
DD7 DDE 1 1 1
DDF DDF 0 1 1
DE0 E30 1 1 1
E31 E31 0 1 1
E32 E33 1 1 1
E34 E3A 0 1 1
E3B E46 1 1 1
E47 E4E 0 1 1
E4F EB0 1 1 1
EB1 EB1 0 1 1
EB2 EB3 1 1 1
EB4 EBC 0 1 1
EBD EC7 1 1 1
EC8 ECE 0 1 1
ECF F17 1 1 1
F18 F19 0 1 1
F1A F34 1 1 1
F35 F35 0 1 1
F36 F36 1 1 1
F37 F37 0 1 1
F38 F38 1 1 1
F39 F39 0 1 1
F3A F70 1 1 1
F71 F7E 0 1 1
F7F F7F 1 1 1
F80 F84 0 1 1
F85 F85 1 1 1
F86 F87 0 1 1
F88 F8C 1 1 1
F8D F97 0 1 1
F98 F98 1 1 1
F99 FBC 0 1 1
FBD FC5 1 1 1
FC6 FC6 0 1 1
FC7 102C 1 1 1
102D 1030 0 1 1
1031 1031 1 1 1
1032 1037 0 1 1
1038 1038 1 1 1
1039 103A 0 1 1
103B 103C 1 1 1
103D 103E 0 1 1
103F 1057 1 1 1
1058 1059 0 1 1
105A 105D 1 1 1
105E 1060 0 1 1
1061 1070 1 1 1
1071 1074 0 1 1
1075 1081 1 1 1
1082 1082 0 1 1
1083 1084 1 1 1
1085 1086 0 1 1
1087 108C 1 1 1
108D 108D 0 1 1
108E 109C 1 1 1
109D 109D 0 1 1
109E 10FF 1 1 1
1100 115F 2 2 2
1160 135C 1 1 1
135D 135F 0 0 0
1360 1711 1 1 1
1712 1714 0 1 1
1715 1731 1 1 1
1732 1733 0 1 1
1734 1751 1 1 1
1752 1753 0 1 1
1754 1771 1 1 1
1772 1773 0 1 1
1774 17B3 1 1 1
17B4 17B5 0 1 1
17B6 17B6 1 1 1
17B7 17BD 0 1 1
17BE 17C5 1 1 1
17C6 17C6 0 1 1
17C7 17C8 1 1 1
17C9 17D3 0 1 1
17D4 17DC 1 1 1
17DD 17DD 0 1 1
17DE 180A 1 1 1
180B 180E 0 0 0
180F 180F 0 1 1
1810 1884 1 1 1
1885 1886 0 1 1
1887 18A8 1 1 1
18A9 18A9 0 1 1
18AA 191F 1 1 1
1920 1922 0 1 1
1923 1926 1 1 1
1927 1928 0 1 1
1929 1931 1 1 1
1932 1932 0 1 1
1933 1938 1 1 1
1939 193B 0 1 1
193C 1A16 1 1 1
1A17 1A18 0 1 1
1A19 1A1A 1 1 1
1A1B 1A1B 0 1 1
1A1C 1A55 1 1 1
1A56 1A56 0 1 1
1A57 1A57 1 1 1
1A58 1A5E 0 1 1
1A5F 1A5F 1 1 1
1A60 1A60 0 1 1
1A61 1A61 1 1 1
1A62 1A62 0 1 1
1A63 1A64 1 1 1
1A65 1A6C 0 1 1
1A6D 1A72 1 1 1
1A73 1A7C 0 1 1
1A7D 1A7E 1 1 1
1A7F 1A7F 0 0 0
1A80 1AAF 1 1 1
1AB0 1ACE 0 0 0
1ACF 1AFF 1 1 1
1B00 1B03 0 1 1
1B04 1B33 1 1 1
1B34 1B3A 0 1 1
1B3B 1B3B 1 1 1
1B3C 1B3C 0 1 1
1B3D 1B41 1 1 1
1B42 1B42 0 1 1
1B43 1B6A 1 1 1
1B6B 1B73 0 0 0
1B74 1B7F 1 1 1
1B80 1B81 0 1 1
1B82 1BA1 1 1 1
1BA2 1BA5 0 1 1
1BA6 1BA7 1 1 1
1BA8 1BA9 0 1 1
1BAA 1BAA 1 1 1
1BAB 1BAD 0 1 1
1BAE 1BE5 1 1 1
1BE6 1BE6 0 1 1
1BE7 1BE7 1 1 1
1BE8 1BE9 0 1 1
1BEA 1BEC 1 1 1
1BED 1BED 0 1 1
1BEE 1BEE 1 1 1
1BEF 1BF1 0 1 1
1BF2 1C2B 1 1 1
1C2C 1C33 0 1 1
1C34 1C35 1 1 1
1C36 1C37 0 1 1
1C38 1CCF 1 1 1
1CD0 1CD2 0 1 1
1CD3 1CD3 1 1 1
1CD4 1CE0 0 1 1
1CE1 1CE1 1 1 1
1CE2 1CE8 0 1 1
1CE9 1CEC 1 1 1
1CED 1CED 0 1 1
1CEE 1CF3 1 1 1
1CF4 1CF4 0 1 1
1CF5 1CF7 1 1 1
1CF8 1CF9 0 1 1
1CFA 1DBF 1 1 1
1DC0 1DFF 0 0 0
1E00 200A 1 1 1
200B 200F 0 0 0
2010 2010 1 1 2
2011 2012 1 1 1
2013 2016 1 1 2
2017 2017 1 1 1
2018 2019 1 1 2
201A 201B 1 1 1
201C 201D 1 1 2
201E 201F 1 1 1
2020 2022 1 1 2
2023 2023 1 1 1
2024 2027 1 1 2
2028 202E 0 0 0
202F 202F 1 1 1
2030 2030 1 1 2
2031 2031 1 1 1
2032 2033 1 1 2
2034 2034 1 1 1
2035 2035 1 1 2
2036 203A 1 1 1
203B 203B 1 1 2
203C 203D 1 1 1
203E 203E 1 1 2
203F 205F 1 1 1
2060 2069 0 1 1
206A 206F 0 0 0
2070 2073 1 1 1
2074 2074 1 1 2
2075 207E 1 1 1
207F 207F 1 1 2
2080 2080 1 1 1
2081 2084 1 1 2
2085 20AB 1 1 1
20AC 20AC 1 1 2
20AD 20CF 1 1 1
20D0 20F0 0 0 0
20F1 2102 1 1 1
2103 2103 1 1 2
2104 2104 1 1 1
2105 2105 1 1 2
2106 2108 1 1 1
2109 2109 1 1 2
210A 2112 1 1 1
2113 2113 1 1 2
2114 2115 1 1 1
2116 2116 1 1 2
2117 2120 1 1 1
2121 2122 1 1 2
2123 2125 1 1 1
2126 2126 1 1 2
2127 212A 1 1 1
212B 212B 1 1 2
212C 2152 1 1 1
2153 2154 1 1 2
2155 215A 1 1 1
215B 215E 1 1 2
215F 215F 1 1 1
2160 216B 1 1 2
216C 216F 1 1 1
2170 2179 1 1 2
217A 2188 1 1 1
2189 2189 1 1 2
218A 218F 1 1 1
2190 2199 1 1 2
219A 21B7 1 1 1
21B8 21B9 1 1 2
21BA 21D1 1 1 1
21D2 21D2 1 1 2
21D3 21D3 1 1 1
21D4 21D4 1 1 2
21D5 21E6 1 1 1
21E7 21E7 1 1 2
21E8 21FF 1 1 1
2200 2200 1 1 2
2201 2201 1 1 1
2202 2203 1 1 2
2204 2206 1 1 1
2207 2208 1 1 2
2209 220A 1 1 1
220B 220B 1 1 2
220C 220E 1 1 1
220F 220F 1 1 2
2210 2210 1 1 1
2211 2211 1 1 2
2212 2214 1 1 1
2215 2215 1 1 2
2216 2219 1 1 1
221A 221A 1 1 2
221B 221C 1 1 1
221D 2220 1 1 2
2221 2222 1 1 1
2223 2223 1 1 2
2224 2224 1 1 1
2225 2225 1 1 2
2226 2226 1 1 1
2227 222C 1 1 2
222D 222D 1 1 1
222E 222E 1 1 2
222F 2233 1 1 1
2234 2237 1 1 2
2238 223B 1 1 1
223C 223D 1 1 2
223E 2247 1 1 1
2248 2248 1 1 2
2249 224B 1 1 1
224C 224C 1 1 2
224D 2251 1 1 1
2252 2252 1 1 2
2253 225F 1 1 1
2260 2261 1 1 2
2262 2263 1 1 1
2264 2267 1 1 2
2268 2269 1 1 1
226A 226B 1 1 2
226C 226D 1 1 1
226E 226F 1 1 2
2270 2281 1 1 1
2282 2283 1 1 2
2284 2285 1 1 1
2286 2287 1 1 2
2288 2294 1 1 1
2295 2295 1 1 2
2296 2298 1 1 1
2299 2299 1 1 2
229A 22A4 1 1 1
22A5 22A5 1 1 2
22A6 22BE 1 1 1
22BF 22BF 1 1 2
22C0 2311 1 1 1
2312 2312 1 1 2
2313 2319 1 1 1
231A 231B 2 2 2
231C 2328 1 1 1
2329 232A 2 2 2
232B 23E8 1 1 1
23E9 23EC 2 2 2
23ED 23EF 1 1 1
23F0 23F0 2 2 2
23F1 23F2 1 1 1
23F3 23F3 2 2 2
23F4 245F 1 1 1
2460 24E9 1 1 2
24EA 24EA 1 1 1
24EB 254B 1 1 2
254C 254F 1 1 1
2550 2573 1 1 2
2574 257F 1 1 1
2580 258F 1 1 2
2590 2591 1 1 1
2592 2595 1 1 2
2596 259F 1 1 1
25A0 25A1 1 1 2
25A2 25A2 1 1 1
25A3 25A9 1 1 2
25AA 25B1 1 1 1
25B2 25B3 1 1 2
25B4 25B5 1 1 1
25B6 25B7 1 1 2
25B8 25BB 1 1 1
25BC 25BD 1 1 2
25BE 25BF 1 1 1
25C0 25C1 1 1 2
25C2 25C5 1 1 1
25C6 25C8 1 1 2
25C9 25CA 1 1 1
25CB 25CB 1 1 2
25CC 25CD 1 1 1
25CE 25D1 1 1 2
25D2 25E1 1 1 1
25E2 25E5 1 1 2
25E6 25EE 1 1 1
25EF 25EF 1 1 2
25F0 25FC 1 1 1
25FD 25FE 2 2 2
25FF 2604 1 1 1
2605 2606 1 1 2
2607 2608 1 1 1
2609 2609 1 1 2
260A 260D 1 1 1
260E 260F 1 1 2
2610 2613 1 1 1
2614 2615 2 2 2
2616 261B 1 1 1
261C 261C 1 1 2
261D 261D 1 1 1
261E 261E 1 1 2
261F 263F 1 1 1
2640 2640 1 1 2
2641 2641 1 1 1
2642 2642 1 1 2
2643 2647 1 1 1
2648 2653 2 2 2
2654 265F 1 1 1
2660 2661 1 1 2
2662 2662 1 1 1
2663 2665 1 1 2
2666 2666 1 1 1
2667 266A 1 1 2
266B 266B 1 1 1
266C 266D 1 1 2
266E 266E 1 1 1
266F 266F 1 1 2
2670 267E 1 1 1
267F 267F 2 2 2
2680 2692 1 1 1
2693 2693 2 2 2
2694 269D 1 1 1
269E 269F 1 1 2
26A0 26A0 1 1 1
26A1 26A1 2 2 2
26A2 26A9 1 1 1
26AA 26AB 2 2 2
26AC 26BC 1 1 1
26BD 26BE 2 2 2
26BF 26BF 1 1 2
26C0 26C3 1 1 1
26C4 26C5 2 2 2
26C6 26CD 1 1 2
26CE 26CE 2 2 2
26CF 26D3 1 1 2
26D4 26D4 2 2 2
26D5 26E1 1 1 2
26E2 26E2 1 1 1
26E3 26E3 1 1 2
26E4 26E7 1 1 1
26E8 26E9 1 1 2
26EA 26EA 2 2 2
26EB 26F1 1 1 2
26F2 26F3 2 2 2
26F4 26F4 1 1 2
26F5 26F5 2 2 2
26F6 26F9 1 1 2
26FA 26FA 2 2 2
26FB 26FC 1 1 2
26FD 26FD 2 2 2
26FE 26FF 1 1 2
2700 2704 1 1 1
2705 2705 2 2 2
2706 2709 1 1 1
270A 270B 2 2 2
270C 2727 1 1 1
2728 2728 2 2 2
2729 273C 1 1 1
273D 273D 1 1 2
273E 274B 1 1 1
274C 274C 2 2 2
274D 274D 1 1 1
274E 274E 2 2 2
274F 2752 1 1 1
2753 2755 2 2 2
2756 2756 1 1 1
2757 2757 2 2 2
2758 2775 1 1 1
2776 277F 1 1 2
2780 2794 1 1 1
2795 2797 2 2 2
2798 27AF 1 1 1
27B0 27B0 2 2 2
27B1 27BE 1 1 1
27BF 27BF 2 2 2
27C0 2B1A 1 1 1
2B1B 2B1C 2 2 2
2B1D 2B4F 1 1 1
2B50 2B50 2 2 2
2B51 2B54 1 1 1
2B55 2B55 2 2 2
2B56 2B59 1 1 2
2B5A 2CEE 1 1 1
2CEF 2CF1 0 0 0
2CF2 2D7E 1 1 1
2D7F 2D7F 0 1 1
2D80 2DDF 1 1 1
2DE0 2DFF 0 0 0
2E00 2E39 1 1 1
2E3A 2E3A 3 1 1
2E3B 2E3B 4 1 1
2E3C 2E7F 1 1 1
2E80 2E99 2 2 2
2E9A 2E9A 1 1 1
2E9B 2EF3 2 2 2
2EF4 2EFF 1 1 1
2F00 2FD5 2 2 2
2FD6 2FEF 1 1 1
2FF0 2FFB 2 2 2
2FFC 2FFF 1 2 2
3000 3029 2 2 2
302A 302F 0 2 2
3030 3030 1 2 2
3031 303C 2 2 2
303D 303D 1 2 2
303E 303E 2 2 2
303F 3040 1 1 1
3041 3096 2 2 2
3097 3098 1 1 1
3099 309A 0 0 0
309B 30FF 2 2 2
3100 3104 1 1 1
3105 312F 2 2 2
3130 3130 1 1 1
3131 318E 2 2 2
318F 318F 1 1 1
3190 31E3 2 2 2
31E4 31EE 1 1 1
31EF 31EF 1 2 2
31F0 321E 2 2 2
321F 321F 1 1 1
3220 3247 2 2 2
3248 324F 1 1 2
3250 3296 2 2 2
3297 3297 1 2 2
3298 3298 2 2 2
3299 3299 1 2 2
329A 4DBF 2 2 2
4DC0 4DFF 1 1 1
4E00 A48C 2 2 2
A48D A48F 1 1 1
A490 A4C6 2 2 2
A4C7 A66E 1 1 1
A66F A672 0 0 0
A673 A673 1 1 1
A674 A67D 0 0 0
A67E A69D 1 1 1
A69E A69F 0 0 0
A6A0 A6EF 1 1 1
A6F0 A6F1 0 0 0
A6F2 A801 1 1 1
A802 A802 0 1 1
A803 A805 1 1 1
A806 A806 0 1 1
A807 A80A 1 1 1
A80B A80B 0 1 1
A80C A824 1 1 1
A825 A826 0 1 1
A827 A82B 1 1 1
A82C A82C 0 1 1
A82D A8C3 1 1 1
A8C4 A8C5 0 1 1
A8C6 A8DF 1 1 1
A8E0 A8F1 0 0 0
A8F2 A8FE 1 1 1
A8FF A8FF 0 1 1
A900 A925 1 1 1
A926 A92D 0 1 1
A92E A946 1 1 1
A947 A951 0 1 1
A952 A95F 1 1 1
A960 A97C 2 2 2
A97D A97F 1 1 1
A980 A982 0 1 1
A983 A9B2 1 1 1
A9B3 A9B3 0 1 1
A9B4 A9B5 1 1 1
A9B6 A9B9 0 1 1
A9BA A9BB 1 1 1
A9BC A9BD 0 1 1
A9BE A9E4 1 1 1
A9E5 A9E5 0 1 1
A9E6 AA28 1 1 1
AA29 AA2E 0 1 1
AA2F AA30 1 1 1
AA31 AA32 0 1 1
AA33 AA34 1 1 1
AA35 AA36 0 1 1
AA37 AA42 1 1 1
AA43 AA43 0 1 1
AA44 AA4B 1 1 1
AA4C AA4C 0 1 1
AA4D AA7B 1 1 1
AA7C AA7C 0 1 1
AA7D AAAF 1 1 1
AAB0 AAB0 0 1 1
AAB1 AAB1 1 1 1
AAB2 AAB4 0 1 1
AAB5 AAB6 1 1 1
AAB7 AAB8 0 1 1
AAB9 AABD 1 1 1
AABE AABF 0 1 1
AAC0 AAC0 1 1 1
AAC1 AAC1 0 1 1
AAC2 AAEB 1 1 1
AAEC AAED 0 1 1
AAEE AAF5 1 1 1
AAF6 AAF6 0 1 1
AAF7 ABE4 1 1 1
ABE5 ABE5 0 1 1
ABE6 ABE7 1 1 1
ABE8 ABE8 0 1 1
ABE9 ABEC 1 1 1
ABED ABED 0 1 1
ABEE ABFF 1 1 1
AC00 D7A3 2 2 2
D7A4 D7FF 1 1 1
E000 F8FF 1 1 2
F900 FAFF 2 2 2
FB00 FB1D 1 1 1
FB1E FB1E 0 1 1
FB1F FDFF 1 1 1
FE00 FE0F 0 1 2
FE10 FE19 2 2 2
FE1A FE1F 1 1 1
FE20 FE2F 0 0 0
FE30 FE52 2 2 2
FE53 FE53 1 1 1
FE54 FE66 2 2 2
FE67 FE67 1 1 1
FE68 FE6B 2 2 2
FE6C FEFE 1 1 1
FEFF FEFF 0 0 0
FF00 FF00 1 1 1
FF01 FF60 2 2 2
FF61 FF9D 1 1 1
FF9E FF9F 0 1 1
FFA0 FFDF 1 1 1
FFE0 FFE6 2 2 2
FFE7 FFEF 1 1 1
FFF0 FFF8 0 1 1
FFF9 FFFB 0 0 0
FFFC FFFC 1 1 1
FFFD FFFD 1 1 2
FFFE FFFF 1 0 0
10000 101FC 1 1 1
101FD 101FD 0 0 0
101FE 102DF 1 1 1
102E0 102E0 0 1 1
102E1 10375 1 1 1
10376 1037A 0 0 0
1037B 10A00 1 1 1
10A01 10A03 0 1 1
10A04 10A04 1 1 1
10A05 10A06 0 1 1
10A07 10A0B 1 1 1
10A0C 10A0F 0 1 1
10A10 10A37 1 1 1
10A38 10A3A 0 1 1
10A3B 10A3E 1 1 1
10A3F 10A3F 0 1 1
10A40 10AE4 1 1 1
10AE5 10AE6 0 1 1
10AE7 10D23 1 1 1
10D24 10D27 0 1 1
10D28 10EAA 1 1 1
10EAB 10EAC 0 0 0
10EAD 10EFC 1 1 1
10EFD 10EFF 0 1 1
10F00 10F45 1 1 1
10F46 10F50 0 0 0
10F51 10F81 1 1 1
10F82 10F85 0 0 0
10F86 11000 1 1 1
11001 11001 0 1 1
11002 11037 1 1 1
11038 11046 0 1 1
11047 1106F 1 1 1
11070 11070 0 1 1
11071 11072 1 1 1
11073 11074 0 1 1
11075 1107E 1 1 1
1107F 11081 0 1 1
11082 110B2 1 1 1
110B3 110B6 0 1 1
110B7 110B8 1 1 1
110B9 110BA 0 1 1
110BB 110C1 1 1 1
110C2 110C2 0 1 1
110C3 110FF 1 1 1
11100 11102 0 1 1
11103 11126 1 1 1
11127 1112B 0 1 1
1112C 1112C 1 1 1
1112D 11134 0 1 1
11135 11172 1 1 1
11173 11173 0 1 1
11174 1117F 1 1 1
11180 11181 0 1 1
11182 111B5 1 1 1
111B6 111BE 0 1 1
111BF 111C8 1 1 1
111C9 111CC 0 1 1
111CD 111CE 1 1 1
111CF 111CF 0 1 1
111D0 1122E 1 1 1
1122F 11231 0 1 1
11232 11233 1 1 1
11234 11234 0 1 1
11235 11235 1 1 1
11236 11237 0 1 1
11238 1123D 1 1 1
1123E 1123E 0 1 1
1123F 11240 1 1 1
11241 11241 0 1 1
11242 112DE 1 1 1
112DF 112DF 0 1 1
112E0 112E2 1 1 1
112E3 112EA 0 1 1
112EB 112FF 1 1 1
11300 11301 0 0 0
11302 1133A 1 1 1
1133B 1133C 0 0 0
1133D 1133D 1 1 1
1133E 1133E 0 1 1
1133F 1133F 1 1 1
11340 11340 0 1 1
11341 11356 1 1 1
11357 11357 0 1 1
11358 11365 1 1 1
11366 1136C 0 0 0
1136D 1136F 1 1 1
11370 11374 0 0 0
11375 11437 1 1 1
11438 1143F 0 1 1
11440 11441 1 1 1
11442 11444 0 1 1
11445 11445 1 1 1
11446 11446 0 1 1
11447 1145D 1 1 1
1145E 1145E 0 1 1
1145F 114AF 1 1 1
114B0 114B0 0 1 1
114B1 114B2 1 1 1
114B3 114B8 0 1 1
114B9 114B9 1 1 1
114BA 114BA 0 1 1
114BB 114BC 1 1 1
114BD 114BD 0 1 1
114BE 114BE 1 1 1
114BF 114C0 0 1 1
114C1 114C1 1 1 1
114C2 114C3 0 1 1
114C4 115AE 1 1 1
115AF 115AF 0 1 1
115B0 115B1 1 1 1
115B2 115B5 0 1 1
115B6 115BB 1 1 1
115BC 115BD 0 1 1
115BE 115BE 1 1 1
115BF 115C0 0 1 1
115C1 115DB 1 1 1
115DC 115DD 0 1 1
115DE 11632 1 1 1
11633 1163A 0 1 1
1163B 1163C 1 1 1
1163D 1163D 0 1 1
1163E 1163E 1 1 1
1163F 11640 0 1 1
11641 116AA 1 1 1
116AB 116AB 0 1 1
116AC 116AC 1 1 1
116AD 116AD 0 1 1
116AE 116AF 1 1 1
116B0 116B5 0 1 1
116B6 116B6 1 1 1
116B7 116B7 0 1 1
116B8 1171C 1 1 1
1171D 1171F 0 1 1
11720 11721 1 1 1
11722 11725 0 1 1
11726 11726 1 1 1
11727 1172B 0 1 1
1172C 1182E 1 1 1
1182F 11837 0 1 1
11838 11838 1 1 1
11839 1183A 0 1 1
1183B 1192F 1 1 1
11930 11930 0 1 1
11931 1193A 1 1 1
1193B 1193C 0 1 1
1193D 1193D 1 1 1
1193E 1193E 0 1 1
1193F 11942 1 1 1
11943 11943 0 1 1
11944 119D3 1 1 1
119D4 119D7 0 1 1
119D8 119D9 1 1 1
119DA 119DB 0 1 1
119DC 119DF 1 1 1
119E0 119E0 0 1 1
119E1 11A00 1 1 1
11A01 11A0A 0 1 1
11A0B 11A32 1 1 1
11A33 11A38 0 1 1
11A39 11A3A 1 1 1
11A3B 11A3E 0 1 1
11A3F 11A46 1 1 1
11A47 11A47 0 1 1
11A48 11A50 1 1 1
11A51 11A56 0 1 1
11A57 11A58 1 1 1
11A59 11A5B 0 1 1
11A5C 11A89 1 1 1
11A8A 11A96 0 1 1
11A97 11A97 1 1 1
11A98 11A99 0 1 1
11A9A 11C2F 1 1 1
11C30 11C36 0 1 1
11C37 11C37 1 1 1
11C38 11C3D 0 1 1
11C3E 11C3E 1 1 1
11C3F 11C3F 0 1 1
11C40 11C91 1 1 1
11C92 11CA7 0 1 1
11CA8 11CA9 1 1 1
11CAA 11CB0 0 1 1
11CB1 11CB1 1 1 1
11CB2 11CB3 0 1 1
11CB4 11CB4 1 1 1
11CB5 11CB6 0 1 1
11CB7 11D30 1 1 1
11D31 11D36 0 1 1
11D37 11D39 1 1 1
11D3A 11D3A 0 1 1
11D3B 11D3B 1 1 1
11D3C 11D3D 0 1 1
11D3E 11D3E 1 1 1
11D3F 11D45 0 1 1
11D46 11D46 1 1 1
11D47 11D47 0 1 1
11D48 11D8F 1 1 1
11D90 11D91 0 1 1
11D92 11D94 1 1 1
11D95 11D95 0 1 1
11D96 11D96 1 1 1
11D97 11D97 0 1 1
11D98 11EF2 1 1 1
11EF3 11EF4 0 1 1
11EF5 11EFF 1 1 1
11F00 11F01 0 1 1
11F02 11F35 1 1 1
11F36 11F3A 0 1 1
11F3B 11F3F 1 1 1
11F40 11F40 0 1 1
11F41 11F41 1 1 1
11F42 11F42 0 1 1
11F43 1342F 1 1 1
13430 13440 0 1 1
13441 13446 1 1 1
13447 13455 0 1 1
13456 16AEF 1 1 1
16AF0 16AF4 0 0 0
16AF5 16B2F 1 1 1
16B30 16B36 0 1 1
16B37 16F4E 1 1 1
16F4F 16F4F 0 1 1
16F50 16F8E 1 1 1
16F8F 16F92 0 1 1
16F93 16FDF 1 1 1
16FE0 16FE3 2 2 2
16FE4 16FE4 0 2 2
16FE5 16FEF 1 1 1
16FF0 16FF1 2 2 2
16FF2 16FFF 1 1 1
17000 187F7 2 2 2
187F8 187FF 1 1 1
18800 18CD5 2 2 2
18CD6 18CFF 1 1 1
18D00 18D08 2 2 2
18D09 1AFEF 1 1 1
1AFF0 1AFF3 2 2 2
1AFF4 1AFF4 1 1 1
1AFF5 1AFFB 2 2 2
1AFFC 1AFFC 1 1 1
1AFFD 1AFFE 2 2 2
1AFFF 1AFFF 1 1 1
1B000 1B122 2 2 2
1B123 1B131 1 1 1
1B132 1B132 2 2 2
1B133 1B14F 1 1 1
1B150 1B152 2 2 2
1B153 1B154 1 1 1
1B155 1B155 2 2 2
1B156 1B163 1 1 1
1B164 1B167 2 2 2
1B168 1B16F 1 1 1
1B170 1B2FB 2 2 2
1B2FC 1BC9C 1 1 1
1BC9D 1BC9E 0 1 1
1BC9F 1BC9F 1 1 1
1BCA0 1BCA3 0 1 1
1BCA4 1CEFF 1 1 1
1CF00 1CF2D 0 0 0
1CF2E 1CF2F 1 1 1
1CF30 1CF46 0 0 0
1CF47 1D164 1 1 1
1D165 1D165 0 0 0
1D166 1D166 1 0 0
1D167 1D169 0 0 0
1D16A 1D16C 1 1 1
1D16D 1D16D 1 0 0
1D16E 1D172 0 0 0
1D173 1D17A 0 1 1
1D17B 1D182 0 0 0
1D183 1D184 1 1 1
1D185 1D18B 0 0 0
1D18C 1D1A9 1 1 1
1D1AA 1D1AD 0 0 0
1D1AE 1D241 1 1 1
1D242 1D244 0 0 0
1D245 1D9FF 1 1 1
1DA00 1DA36 0 1 1
1DA37 1DA3A 1 1 1
1DA3B 1DA6C 0 1 1
1DA6D 1DA74 1 1 1
1DA75 1DA75 0 1 1
1DA76 1DA83 1 1 1
1DA84 1DA84 0 1 1
1DA85 1DA9A 1 1 1
1DA9B 1DA9F 0 1 1
1DAA0 1DAA0 1 1 1
1DAA1 1DAAF 0 1 1
1DAB0 1DFFF 1 1 1
1E000 1E006 0 0 0
1E007 1E007 1 1 1
1E008 1E018 0 0 0
1E019 1E01A 1 1 1
1E01B 1E021 0 0 0
1E022 1E022 1 1 1
1E023 1E024 0 0 0
1E025 1E025 1 1 1
1E026 1E02A 0 0 0
1E02B 1E08E 1 1 1
1E08F 1E08F 0 0 0
1E090 1E12F 1 1 1
1E130 1E136 0 1 1
1E137 1E2AD 1 1 1
1E2AE 1E2AE 0 1 1
1E2AF 1E2EB 1 1 1
1E2EC 1E2EF 0 1 1
1E2F0 1E4EB 1 1 1
1E4EC 1E4EF 0 1 1
1E4F0 1E8CF 1 1 1
1E8D0 1E8D6 0 0 0
1E8D7 1E943 1 1 1
1E944 1E94A 0 1 1
1E94B 1F003 1 1 1
1F004 1F004 2 2 2
1F005 1F0CE 1 1 1
1F0CF 1F0CF 2 2 2
1F0D0 1F0FF 1 1 1
1F100 1F10A 1 1 2
1F10B 1F10F 1 1 1
1F110 1F12D 1 1 2
1F12E 1F12F 1 1 1
1F130 1F169 1 1 2
1F16A 1F16F 1 1 1
1F170 1F18D 1 1 2
1F18E 1F18E 2 2 2
1F18F 1F190 1 1 2
1F191 1F19A 2 2 2
1F19B 1F1AC 1 1 2
1F1AD 1F1E5 1 1 1
1F1E6 1F1FF 2 1 1
1F200 1F201 2 2 2
1F202 1F202 1 2 2
1F203 1F20F 1 1 1
1F210 1F236 2 2 2
1F237 1F237 1 2 2
1F238 1F23B 2 2 2
1F23C 1F23F 1 1 1
1F240 1F248 2 2 2
1F249 1F24F 1 1 1
1F250 1F251 2 2 2
1F252 1F25F 1 1 1
1F260 1F265 1 2 2
1F266 1F2FF 1 1 1
1F300 1F320 2 2 2
1F321 1F32C 1 1 1
1F32D 1F335 2 2 2
1F336 1F336 1 1 1
1F337 1F37C 2 2 2
1F37D 1F37D 1 1 1
1F37E 1F393 2 2 2
1F394 1F39F 1 1 1
1F3A0 1F3CA 2 2 2
1F3CB 1F3CE 1 1 1
1F3CF 1F3D3 2 2 2
1F3D4 1F3DF 1 1 1
1F3E0 1F3F0 2 2 2
1F3F1 1F3F3 1 1 1
1F3F4 1F3F4 2 2 2
1F3F5 1F3F7 1 1 1
1F3F8 1F3FA 2 2 2
1F3FB 1F3FF 0 2 2
1F400 1F43E 2 2 2
1F43F 1F43F 1 1 1
1F440 1F440 2 2 2
1F441 1F441 1 1 1
1F442 1F4FC 2 2 2
1F4FD 1F4FE 1 1 1
1F4FF 1F53D 2 2 2
1F53E 1F54A 1 1 1
1F54B 1F54E 2 2 2
1F54F 1F54F 1 1 1
1F550 1F567 2 2 2
1F568 1F579 1 1 1
1F57A 1F57A 2 2 2
1F57B 1F594 1 1 1
1F595 1F596 2 2 2
1F597 1F5A3 1 1 1
1F5A4 1F5A4 2 2 2
1F5A5 1F5FA 1 1 1
1F5FB 1F64F 2 2 2
1F650 1F67F 1 1 1
1F680 1F6C5 2 2 2
1F6C6 1F6CB 1 1 1
1F6CC 1F6CC 2 2 2
1F6CD 1F6CF 1 1 1
1F6D0 1F6D2 2 2 2
1F6D3 1F6D4 1 1 1
1F6D5 1F6D7 2 2 2
1F6D8 1F6DB 1 1 1
1F6DC 1F6DF 2 2 2
1F6E0 1F6EA 1 1 1
1F6EB 1F6EC 2 2 2
1F6ED 1F6F3 1 1 1
1F6F4 1F6FC 2 2 2
1F6FD 1F7DF 1 1 1
1F7E0 1F7EB 2 2 2
1F7EC 1F7EF 1 1 1
1F7F0 1F7F0 2 2 2
1F7F1 1F90B 1 1 1
1F90C 1F93A 2 2 2
1F93B 1F93B 1 1 1
1F93C 1F945 2 2 2
1F946 1F946 1 1 1
1F947 1F9FF 2 2 2
1FA00 1FA6F 1 1 1
1FA70 1FA7C 2 2 2
1FA7D 1FA7F 1 1 1
1FA80 1FA88 2 2 2
1FA89 1FA8F 1 1 1
1FA90 1FABD 2 2 2
1FABE 1FABE 1 1 1
1FABF 1FAC5 2 2 2
1FAC6 1FACD 1 1 1
1FACE 1FADB 2 2 2
1FADC 1FADF 1 1 1
1FAE0 1FAE8 2 2 2
1FAE9 1FAEF 1 1 1
1FAF0 1FAF8 2 2 2
1FAF9 1FFFF 1 1 1
20000 2FFFD 2 2 2
2FFFE 2FFFF 1 1 1
30000 3FFFD 2 2 2
3FFFE DFFFF 1 1 1
E0000 E00FF 0 1 1
E0100 E01EF 0 1 2
E01F0 E0FFF 0 1 1
E1000 EFFFF 1 1 1
F0000 FFFFD 1 1 2
FFFFE FFFFF 1 1 1
100000 10FFFD 1 1 2
10FFFE 10FFFF 1 1 1
";
}
