//! Expected bytes come from the pinned Go stack: lipgloss v1.0.0 renders of
//! cmd/style.go styles for every profile/background, and bin/pb-go-pinned
//! run with stdout on a PTY (scratch tools under target/phase4/style).
use super::super::{line_width, render_styled};
use super::*;
use std::collections::HashMap;

fn term(profile: Profile, dark: bool) -> Terminal {
    Terminal { profile, dark }
}

fn detect(tty: bool, vars: &[(&str, &str)], probe: Option<(u8, u8, u8)>) -> (Terminal, bool) {
    let vars: HashMap<&str, &str> = vars.iter().copied().collect();
    let mut probed = false;
    let terminal = Terminal::detect(
        tty,
        |key| vars.get(key).map_or_else(String::new, |v| (*v).to_owned()),
        || {
            probed = true;
            probe
        },
    );
    (terminal, probed)
}

fn bullet(text: &str, t: &Terminal) -> String {
    let item = format!(
        "{} {}",
        SELECTED.render("•", t, line_width),
        STANDARD.render(text, t, line_width)
    );
    ITEM_OUTER.render(&item, t, line_width)
}

#[test]
fn renders_match_lipgloss_for_every_profile_and_background() {
    let cases = [
        (
            Profile::TrueColor,
            true,
            "1;38;2;158;158;240",
            "38;2;227;227;231",
            "38;2;73;73;92",
        ),
        (
            Profile::TrueColor,
            false,
            "1;38;2;58;58;140",
            "38;2;24;24;27",
            "38;2;209;213;219",
        ),
        (Profile::Ansi256, true, "1;38;5;147", "38;5;188", "38;5;59"),
        (Profile::Ansi256, false, "1;38;5;60", "38;5;232", "38;5;188"),
        (Profile::Ansi, true, "1;94", "37", "90"),
        (Profile::Ansi, false, "1;94", "30", "37"),
    ];
    for (profile, dark, accent, body, border) in cases {
        let t = term(profile, dark);
        let a = |s: &str| format!("\x1b[{accent}m{s}\x1b[0m");
        let b = |s: &str| format!("\x1b[{body}m{s}\x1b[0m");
        assert_eq!(bullet("up", &t), format!(" {} {}", a("•"), b("up")));
        assert_eq!(
            bullet("a\n\tbb\r\nccc\n", &t),
            format!(
                " {} {}     \n {}  \n {}     \n {}        ",
                a("•"),
                b("a"),
                b("    bb"),
                b("ccc"),
                b("")
            )
        );
        assert_eq!(bullet("", &t), format!(" {} {}", a("•"), b("")));
        assert_eq!(SELECTED.render("LABEL    ", &t, line_width), a("LABEL    "));
        assert_eq!(
            SELECTED.render("Series by a\nbb value", &t, line_width),
            format!("{}\n{}   ", a("Series by a"), a("bb value"))
        );
        assert_eq!(
            RULE.render("───", &t, line_width),
            format!("\x1b[{border}m───\x1b[0m")
        );
        assert_eq!(
            STANDARD.render("x\ny\tz", &t, line_width),
            format!("{}     \n{}", b("x"), b("y    z"))
        );
        assert_eq!(STANDARD.render("a\rb", &t, line_width), b("a\rb"));
    }
}

#[test]
fn ascii_profile_is_plain_output() {
    for dark in [true, false] {
        let t = term(Profile::Ascii, dark);
        assert_eq!(
            bullet("a\n\tbb\r\nccc\n", &t),
            " • a     \n     bb  \n ccc     \n         "
        );
        assert_eq!(bullet("", &t), " • ");
        assert_eq!(STANDARD.render("x\ny\tz", &t, line_width), "x     \ny    z");
    }
}

#[test]
fn profile_detection_follows_termenv_env_color_profile() {
    let profile = |tty, vars: &[(&str, &str)]| detect(tty, vars, None).0.profile;
    use Profile::*;
    // Off a terminal: Ascii unless CLICOLOR_FORCE upgrades it to ANSI.
    assert_eq!(
        profile(
            false,
            &[("TERM", "xterm-256color"), ("COLORTERM", "truecolor")]
        ),
        Ascii
    );
    assert_eq!(
        profile(
            false,
            &[("TERM", "xterm-256color"), ("CLICOLOR_FORCE", "1")]
        ),
        Ansi
    );
    assert_eq!(profile(false, &[("CLICOLOR_FORCE", "0")]), Ascii);
    assert_eq!(profile(true, &[("TERM", "xterm-256color")]), Ansi256);
    assert_eq!(
        profile(
            true,
            &[("TERM", "xterm-256color"), ("COLORTERM", "TrueColor")]
        ),
        TrueColor
    );
    assert_eq!(
        profile(true, &[("TERM", "screen-256color"), ("COLORTERM", "24bit")]),
        Ansi256
    );
    assert_eq!(
        profile(
            true,
            &[
                ("TERM", "screen"),
                ("COLORTERM", "truecolor"),
                ("TERM_PROGRAM", "tmux")
            ]
        ),
        TrueColor
    );
    assert_eq!(
        profile(true, &[("TERM", "dumb"), ("COLORTERM", "yes")]),
        Ansi256
    );
    assert_eq!(profile(true, &[("TERM", "xterm-kitty")]), TrueColor);
    assert_eq!(profile(true, &[("TERM", "wezterm")]), TrueColor);
    assert_eq!(profile(true, &[("TERM", "linux")]), Ansi);
    assert_eq!(profile(true, &[("TERM", "xterm-color")]), Ansi);
    assert_eq!(profile(true, &[("TERM", "ansi")]), Ansi);
    assert_eq!(profile(true, &[("TERM", "dumb")]), Ascii);
    assert_eq!(
        profile(true, &[("TERM", "dumb"), ("CLICOLOR_FORCE", "1")]),
        Ansi
    );
    assert_eq!(profile(true, &[("GOOGLE_CLOUD_SHELL", "true")]), TrueColor);
    // NO_COLOR beats CLICOLOR_FORCE; CLICOLOR=0 only without force.
    assert_eq!(
        profile(
            true,
            &[("TERM", "dumb"), ("NO_COLOR", "1"), ("CLICOLOR_FORCE", "1")]
        ),
        Ascii
    );
    assert_eq!(
        profile(true, &[("TERM", "xterm-256color"), ("CLICOLOR", "0")]),
        Ascii
    );
    assert_eq!(
        profile(
            true,
            &[
                ("TERM", "xterm-256color"),
                ("CLICOLOR", "0"),
                ("CLICOLOR_FORCE", "1")
            ]
        ),
        Ansi256
    );
    // CI disables terminal detection entirely.
    assert_eq!(
        profile(true, &[("TERM", "xterm-256color"), ("CI", "1")]),
        Ascii
    );
}

#[test]
fn background_detection_probes_only_where_termenv_does() {
    const WHITE: Option<(u8, u8, u8)> = Some((255, 255, 255));
    let xterm = [("TERM", "xterm-256color")];
    // Not a terminal (or CI): no probe, NoColor background counts as dark.
    assert_eq!(
        detect(false, &xterm, WHITE),
        (term(Profile::Ascii, true), false)
    );
    assert_eq!(
        detect(true, &[("TERM", "xterm-256color"), ("CI", "1")], WHITE),
        (term(Profile::Ascii, true), false)
    );
    // Probe answered (PTY: light palette with 38;5;60 / 38;5;232).
    assert_eq!(
        detect(true, &xterm, WHITE),
        (term(Profile::Ansi256, false), true)
    );
    assert_eq!(
        detect(true, &xterm, Some((0, 0, 0))),
        (term(Profile::Ansi256, true), true)
    );
    // NO_COLOR still probes in Go; the answer just has no visible effect.
    assert!(detect(true, &[("TERM", "xterm"), ("NO_COLOR", "1")], WHITE).1);
    // Unanswered probe falls back to COLORFGBG, then dark.
    assert!(detect(true, &xterm, None).0.dark);
    assert!(
        !detect(true, &[("TERM", "xterm"), ("COLORFGBG", "0;15")], None)
            .0
            .dark
    );
    // screen/tmux never probe and use COLORFGBG's last field.
    assert_eq!(
        detect(
            true,
            &[("TERM", "screen-256color"), ("COLORFGBG", "0;15")],
            WHITE
        ),
        (term(Profile::Ansi256, false), false)
    );
    assert_eq!(
        detect(true, &[("TERM", "tmux")], WHITE),
        (term(Profile::Ascii, true), false)
    );
    let fgbg = |value: &str| {
        !detect(true, &[("TERM", "screen"), ("COLORFGBG", value)], None)
            .0
            .dark
    };
    // Light means HSL lightness >= 0.5: silver, gray 0x80 and pure primaries.
    assert!(fgbg("0;7") && fgbg("0;8") && fgbg("0;9") && fgbg("0;12") && fgbg("15;default;15"));
    assert!(!fgbg("15;0") && !fgbg("0;4") && !fgbg("0;232") && !fgbg("0;x") && !fgbg("15"));
    assert!(fgbg("0;231") && fgbg("0;255") && fgbg("0;+15"));
}

#[test]
fn text_renders_match_pinned_go_on_a_pty() {
    let labels = br#"{"status":"success","data":["up","\u2705ok"]}"#;
    let card = br#"{"status":"success","data":[{"name":"job","value":3}]}"#;
    let text = |operation, body: &[u8], t| {
        String::from_utf8(render_styled(operation, body, false, "", &t).unwrap()).unwrap()
    };
    assert_eq!(
        text("labels", labels, term(Profile::Ansi256, true)),
        " \x1b[1;38;5;147m•\x1b[0m \x1b[38;5;188mup\x1b[0m\n \x1b[1;38;5;147m•\x1b[0m \x1b[38;5;188m✅ok\x1b[0m\n"
    );
    assert_eq!(
        text("labels", labels, term(Profile::Ansi, true)),
        " \x1b[1;94m•\x1b[0m \x1b[37mup\x1b[0m\n \x1b[1;94m•\x1b[0m \x1b[37m✅ok\x1b[0m\n"
    );
    assert_eq!(
        text(
            "cardinality label-names",
            card,
            term(Profile::TrueColor, true)
        ),
        "\x1b[1;38;2;158;158;240mLABEL    \x1b[0m\x1b[1;38;2;158;158;240mDISTINCT VALUES\x1b[0m\n\
         \x1b[38;2;73;73;92m─────────\x1b[0m\x1b[38;2;73;73;92m───────────────\x1b[0m\n\
         \x1b[38;2;227;227;231mjob\x1b[0m \x1b[38;2;73;73;92m.....\x1b[0m \x1b[38;2;227;227;231m3\x1b[0m\n\n"
    );
    // JSON and plain-only commands never carry styling.
    let t = term(Profile::TrueColor, true);
    assert_eq!(
        render_styled("labels", labels, true, "", &t).unwrap(),
        render_styled("labels", labels, true, "", &Terminal::PLAIN).unwrap()
    );
    let queries = br#"{"data":[{"query":"up","stream":"s","started_at":"x","elapsed_ms":1}]}"#;
    assert!(!text("active-queries", queries, t).contains('\x1b'));
    let run = br#"{"data":{"resultType":"vector","result":[{"metric":{"__name__":"up"},"value":[1,"1"]}]}}"#;
    assert!(!text("run", run, t).contains('\x1b'));
}

#[test]
fn styled_headers_and_widths_follow_go_structure() {
    let t = term(Profile::Ansi, true);
    let active = br#"{"data":{"total_active_series":1,"series":[{"__name__":"up","job":"a"}]}}"#;
    assert_eq!(
        String::from_utf8(
            render_styled("cardinality active-series", active, false, "", &t).unwrap()
        )
        .unwrap(),
        "\x1b[1;94mACTIVE SERIES\x1b[0m\ntotal=1\n\n \x1b[1;94m•\x1b[0m \x1b[37mup\x1b[0m\n  job=\"a\"\n\n"
    );
    let tsdb = br#"{"data":{"totalSeries":2,"totalLabelValuePairs":3,"seriesCountByFocusLabelValue":[{"name":"v","value":1}]}}"#;
    let out = String::from_utf8(render_styled("tsdb", tsdb, false, "a\tb", &t).unwrap()).unwrap();
    assert!(out.starts_with(
        "\n\x1b[1;94mTSDB SUMMARY\x1b[0m\n\x1b[37mTotal Series\x1b[0m \x1b[90m............\x1b[0m \x1b[37m2\x1b[0m\n"
    ));
    // Focus-label titles are styled after tab expansion.
    assert!(out.contains("\n\n\x1b[1;94mSeries by a    b value\x1b[0m\n"));
    // Stripping escapes yields the plain rendering byte for byte.
    let plain =
        String::from_utf8(render_styled("tsdb", tsdb, false, "a\tb", &Terminal::PLAIN).unwrap())
            .unwrap();
    assert_eq!(strip(&out), plain);
}

fn strip(text: &str) -> String {
    let mut out = String::new();
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            for c in chars.by_ref() {
                if c == 'm' {
                    break;
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

#[test]
fn widths_match_lipgloss_width_including_ascii_led_clusters() {
    // lipgloss.Width from the pinned Go stack. ansi.StringWidth counts printable
    // ASCII bytes alone and clusters only from a UTF-8 lead byte, so an
    // ASCII-led keycap or combining sequence keeps the ASCII width.
    let cases = [
        ("1\u{fe0f}\u{20e3}", 1),
        ("#\u{fe0f}\u{20e3}", 1),
        ("\x1b[31m1\u{fe0f}\u{20e3}\x1b[0m", 1),
        ("ab1\u{fe0f}\u{20e3}c", 4),
        ("A\u{20e3}", 1),
        ("a\u{301}", 1),
        ("e\u{301}x", 2),
        ("a\u{308}\u{301}b", 2),
        ("x\u{fe0f}", 1),
        ("x\u{200d}y", 2),
        ("a\u{200b}b", 2),
        ("\u{301}", 0),
        ("\u{ad}", 0),
        ("\u{e9}", 1),
        ("\u{263a}", 1),
        ("\u{263a}\u{fe0f}", 2),
        ("\u{2764}\u{fe0f}", 2),
        ("\u{2705}ok", 4),
        ("\u{65e5}\u{672c}", 4),
        ("\u{1f1fa}\u{1f1f8}", 2),
        ("\u{1f44d}\u{1f3fd}", 2),
        ("\u{1f468}\u{200d}\u{1f469}\u{200d}\u{1f467}", 2),
    ];
    let mismatches: Vec<_> = cases
        .iter()
        .filter(|(text, go)| line_width(text) != *go)
        .map(|(text, go)| (text.escape_unicode().to_string(), *go, line_width(text)))
        .collect();
    assert!(mismatches.is_empty(), "(text, go, rust): {mismatches:?}");
}
