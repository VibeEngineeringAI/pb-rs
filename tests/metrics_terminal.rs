//! Process probes characterized against termenv v0.15.2 / bubbletea v1.2.2.
#![cfg(unix)]
use std::{
    fs::File,
    io::{Read, Write},
    net::TcpListener,
    os::{
        fd::{FromRawFd, RawFd},
        unix::process::CommandExt,
    },
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
const QUERY: &[u8] = b"\x1b]11;?\x1b\\\x1b[6n";
const WHITE: &[u8] = b"\x1b]11;rgb:ffff/ffff/ffff\x1b\\\x1b[1;1R";
const BLACK: &[u8] = b"\x1b]11;rgb:0000/0000/0000\x07\x1b[1;1R";

fn terminal_attributes(fd: RawFd) -> libc::termios {
    unsafe {
        let mut attributes = std::mem::MaybeUninit::uninit();
        assert_eq!(libc::tcgetattr(fd, attributes.as_mut_ptr()), 0);
        let attributes = attributes.assume_init();
        #[cfg(target_os = "macos")]
        let attributes = {
            let mut attributes = attributes;
            // XNU tty.c restores ICANON with kernel-generated PENDIN. Compare
            // the configured flags, not this pending-input processing marker.
            attributes.c_lflag &= !libc::PENDIN;
            attributes
        };
        attributes
    }
}

#[test]
fn process_probe_colors_bypasses_timeout_and_termios_restore() {
    type ProbeCase = (
        &'static str,
        &'static [(&'static str, &'static str)],
        Option<&'static [u8]>,
        bool,
        &'static str,
    );
    let cases: &[ProbeCase] = &[
        ("dark", &[], Some(BLACK), true, "38;5;147"),
        ("light", &[], Some(WHITE), true, "38;5;60"),
        // NO_COLOR suppresses SGR, but bubbletea's eager background probe remains.
        ("no-color", &[("NO_COLOR", "1")], Some(WHITE), true, ""),
        (
            "screen",
            &[("TERM", "screen-256color")],
            None,
            false,
            "38;5;147",
        ),
        ("ci", &[("CI", "1")], None, false, ""),
        (
            "explicit-theme-json",
            &[("PB_THEME", "dark")],
            Some(WHITE),
            true,
            "",
        ),
        ("silent", &[], None, true, "38;5;147"),
    ];
    for (name, overrides, reply, expects_probe, color) in cases {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let root = std::env::temp_dir().join(format!("pb-terminal-{}-{name}", std::process::id()));
        std::fs::create_dir_all(root.join("config/pb")).unwrap();
        std::fs::write(root.join("config/pb/config.toml"), format!(
            "DefaultProfile = \"fixture\"\n[Profiles.fixture]\nurl = \"http://127.0.0.1:{port}\"\nusername = \"u\"\npassword = \"p\"\ncloud = false\n"
        )).unwrap();
        let (request_ready, request_seen) = std::sync::mpsc::channel();
        let (restore_checked, wait_for_check) = std::sync::mpsc::channel();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut headers = Vec::new();
            while !headers.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                headers.push(byte[0]);
            }
            assert!(headers.starts_with(b"GET /prometheus/api/v1/labels"));
            // Theme probing and restoration finish before the HTTP request.
            // Hold the process alive here: Darwin revokes its controlling tty
            // when the session leader exits, invalidating the parent's slave.
            request_ready.send(()).unwrap();
            wait_for_check
                .recv_timeout(Duration::from_secs(10))
                .unwrap();
            let body = br#"{"status":"success","data":["up"]}"#;
            socket
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    )
                    .as_bytes(),
                )
                .unwrap();
            socket.write_all(body).unwrap();
        });
        let (mut master_fd, mut slave_fd) = (-1, -1);
        unsafe {
            assert_eq!(
                libc::openpty(
                    &mut master_fd,
                    &mut slave_fd,
                    std::ptr::null_mut(),
                    // `*mut` on macOS, `*const` on Linux.
                    std::ptr::null_mut(),
                    std::ptr::null_mut()
                ),
                0
            );
        }
        let mut master = unsafe { File::from_raw_fd(master_fd) };
        let slave = unsafe { File::from_raw_fd(slave_fd) };
        let before = terminal_attributes(slave_fd);
        let mut command = Command::new(env!("CARGO_BIN_EXE_pb"));
        command.args(["promql", "labels"]);
        if *name == "explicit-theme-json" {
            command.args(["-o", "json"]);
        }
        command
            .env_clear()
            .env("HOME", &root)
            .env("XDG_CONFIG_HOME", root.join("config"))
            .env("TERM", "xterm-256color")
            .env("PATH", "/usr/bin:/bin")
            .stdin(Stdio::from(slave.try_clone().unwrap()))
            .stdout(Stdio::from(slave.try_clone().unwrap()))
            .stderr(Stdio::piped());
        for (key, value) in *overrides {
            command.env(key, value);
        }
        unsafe {
            command.pre_exec(move || {
                if libc::setsid() < 0 || libc::ioctl(slave_fd, libc::TIOCSCTTY as _, 0) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                libc::close(master_fd);
                Ok(())
            });
        }
        let started = Instant::now();
        let mut child = command.spawn().unwrap();
        let mut output = Vec::new();
        let mut replied = false;
        let mut restored = None;
        loop {
            if restored.is_none() && request_seen.try_recv().is_ok() {
                restored = Some(terminal_attributes(slave_fd));
                restore_checked.send(()).unwrap();
            }
            assert!(
                started.elapsed() < Duration::from_secs(9),
                "{name}: process stalled"
            );
            let mut descriptor = libc::pollfd {
                fd: master_fd,
                events: libc::POLLIN,
                revents: 0,
            };
            let ready = unsafe { libc::poll(&mut descriptor, 1, 25) };
            if ready > 0 && descriptor.revents & libc::POLLIN != 0 {
                let mut bytes = [0; 4096];
                let count = match master.read(&mut bytes) {
                    // PTY hangup is EOF or EIO depending on the kernel. The
                    // child's exit status and complete output are checked below.
                    Ok(0) => break,
                    Ok(count) => count,
                    Err(error) if error.raw_os_error() == Some(libc::EIO) => break,
                    Err(error) => panic!("{name}: PTY read failed: {error}"),
                };
                output.extend_from_slice(&bytes[..count]);
                if !replied
                    && output.windows(QUERY.len()).any(|w| w == QUERY)
                    && let Some(reply) = reply
                {
                    master.write_all(reply).unwrap();
                    replied = true;
                }
            } else if child.try_wait().unwrap().is_some() {
                break;
            }
        }
        let result = child.wait_with_output().unwrap();
        server.join().unwrap();
        assert!(result.status.success(), "{name}: {:?}", result.stderr);
        assert_eq!(
            output.starts_with(QUERY),
            *expects_probe,
            "{name}: {output:?}"
        );
        let body = if *expects_probe {
            &output[QUERY.len()..]
        } else {
            &output
        };
        if color.is_empty() {
            assert!(!body.contains(&0x1b), "{name}: unexpected SGR");
        } else {
            assert!(
                String::from_utf8_lossy(body).contains(color),
                "{name}: {body:?}"
            );
        }
        if *name == "silent" {
            assert!(started.elapsed() >= Duration::from_millis(4900));
        }
        let after = restored.expect("terminal restoration was checked while the process was alive");
        assert_eq!(
            before.c_lflag, after.c_lflag,
            "{name}: terminal flags not restored"
        );
        assert_eq!(before.c_iflag, after.c_iflag);
        assert_eq!(before.c_oflag, after.c_oflag);
        assert_eq!(before.c_cflag, after.c_cflag);
        assert_eq!(before.c_cc, after.c_cc);
        std::fs::remove_dir_all(root).unwrap();
    }
}
