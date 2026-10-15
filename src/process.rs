//! Process startup limits and the matching child-process boundary.
//!
//! Go raises its own soft open-file limit, but restores the inherited value
//! before exec. Keep that value outside workflow state so every browser
//! command can use the same boundary without changing the parent's limit.
use std::process::Command;

#[cfg(unix)]
static INHERITED_NOFILE: std::sync::OnceLock<Option<libc::rlimit>> = std::sync::OnceLock::new();

/// Called once at binary startup, before creating threads or running commands.
pub fn initialize_open_file_limit() {
    #[cfg(unix)]
    INHERITED_NOFILE.get_or_init(|| {
        let mut inherited = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut inherited) } != 0 {
            return None;
        }
        if inherited.rlim_max == 0 || inherited.rlim_cur >= inherited.rlim_max - 1 {
            return None;
        }
        #[allow(unused_mut)]
        let mut soft = inherited.rlim_max - 1;
        #[cfg(target_vendor = "apple")]
        {
            let mut per_process: u32 = 0;
            let mut size = std::mem::size_of::<u32>();
            let status = unsafe {
                libc::sysctlbyname(
                    c"kern.maxfilesperproc".as_ptr(),
                    (&raw mut per_process).cast(),
                    &mut size,
                    std::ptr::null_mut(),
                    0,
                )
            };
            if status == 0 && size == std::mem::size_of::<u32>() {
                soft = soft.min(libc::rlim_t::from(per_process));
            }
        }
        if soft > inherited.rlim_cur {
            let raised = libc::rlimit {
                rlim_cur: soft,
                rlim_max: inherited.rlim_max,
            };
            unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &raised) };
        }
        Some(inherited)
    });
}

/// Install the inherited-limit restoration immediately before a child exec.
/// Does nothing for library callers that did not change their process limits.
pub(crate) fn restore_child_limits(command: &mut Command) {
    #[cfg(unix)]
    if let Some(Some(inherited)) = INHERITED_NOFILE.get() {
        use std::os::unix::process::CommandExt;
        let inherited = *inherited;
        // Capture before fork. The child callback only calls async-signal-safe
        // setrlimit; no locks, allocation, logging or environment access.
        unsafe {
            command.pre_exec(move || {
                #[cfg(target_os = "linux")]
                {
                    // Go's exec boundary honors an external prlimit change:
                    // restore only while the parent still has the limits set
                    // by startup (or if reading them fails).
                    let mut current = libc::rlimit {
                        rlim_cur: 0,
                        rlim_max: 0,
                    };
                    if libc::getrlimit(libc::RLIMIT_NOFILE, &mut current) == 0
                        && (current.rlim_cur != inherited.rlim_max - 1
                            || current.rlim_max != inherited.rlim_max)
                    {
                        return Ok(());
                    }
                }
                // Go still execs if an external hard-limit reduction prevents
                // restoration. The opener also keeps that failure silent.
                libc::setrlimit(libc::RLIMIT_NOFILE, &inherited);
                Ok(())
            });
        }
    }
    #[cfg(not(unix))]
    let _ = command;
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::process::CommandExt;

    #[test]
    fn child_restores_inherited_limit_without_lowering_parent() {
        const MARKER: &str = "PB_TEST_INHERITED_LIMIT_CHILD";
        if std::env::var_os(MARKER).is_some() {
            initialize_open_file_limit();
            let mut before = libc::rlimit {
                rlim_cur: 0,
                rlim_max: 0,
            };
            assert_eq!(
                unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut before) },
                0
            );
            assert!(before.rlim_cur > 64);
            let mut child = Command::new("/bin/sh");
            child.args(["-c", "ulimit -n"]);
            restore_child_limits(&mut child);
            let result = child.output().unwrap();
            assert!(result.status.success());
            assert_eq!(String::from_utf8(result.stdout).unwrap().trim(), "64");
            // Linux Go honors external limit changes instead of restoring
            // the startup value. Other Unix targets ignore restore failures.
            let mut restricted = Command::new("/bin/sh");
            restricted.args(["-c", "ulimit -n"]);
            unsafe {
                restricted.pre_exec(|| {
                    let limit = libc::rlimit {
                        rlim_cur: 64,
                        rlim_max: 64,
                    };
                    if libc::setrlimit(libc::RLIMIT_NOFILE, &limit) != 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
            restore_child_limits(&mut restricted);
            let result = restricted.output().unwrap();
            assert!(result.status.success());
            assert_eq!(String::from_utf8(result.stdout).unwrap().trim(), "64");
            #[cfg(target_os = "linux")]
            {
                let mut changed = Command::new("/bin/sh");
                changed.args(["-c", "ulimit -n"]);
                unsafe {
                    changed.pre_exec(move || {
                        let limit = libc::rlimit {
                            rlim_cur: 128,
                            rlim_max: before.rlim_max,
                        };
                        if libc::setrlimit(libc::RLIMIT_NOFILE, &limit) != 0 {
                            return Err(std::io::Error::last_os_error());
                        }
                        Ok(())
                    });
                }
                restore_child_limits(&mut changed);
                let result = changed.output().unwrap();
                assert!(result.status.success());
                assert_eq!(String::from_utf8(result.stdout).unwrap().trim(), "128");
            }
            let mut after = before;
            assert_eq!(
                unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut after) },
                0
            );
            assert_eq!(before.rlim_cur, after.rlim_cur);
            return;
        }
        let mut current = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut current) },
            0
        );
        if current.rlim_max <= 65 {
            return;
        }
        let mut child = Command::new(std::env::current_exe().unwrap());
        child
            .args([
                "--exact",
                "process::tests::child_restores_inherited_limit_without_lowering_parent",
            ])
            .env(MARKER, "1");
        unsafe {
            child.pre_exec(move || {
                let limit = libc::rlimit {
                    rlim_cur: 64,
                    rlim_max: current.rlim_max,
                };
                if libc::setrlimit(libc::RLIMIT_NOFILE, &limit) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let result = child.output().unwrap();
        assert!(
            result.status.success(),
            "{}\n{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
    }
}
