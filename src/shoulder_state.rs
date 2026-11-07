//! Locked, bounded JSON checkpoints shared by the ingest and observe commands.
//!
//! The sidecar lock deliberately survives its owner: unlinking it would allow
//! concurrent callers to lock different inodes. Saves never unlink the previous
//! checkpoint. If the platform cannot atomically replace it, rename fails closed.
//! A sync failure after rename is reported even though the new checkpoint may
//! already be visible; callers must not treat that failure as a durable commit.

use crate::{ErrorCode, error::CliError};
use serde::{Serialize, de::DeserializeOwned};
use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions, TryLockError},
    io::{self, BufWriter, Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

const MAX_STATE_BYTES: u64 = 64 * 1024 * 1024;
static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[derive(Debug)]
pub(crate) struct StateFile {
    path: PathBuf,
    parent: PathBuf,
    // Keeping the descriptor open keeps the advisory lock held.
    _lock: File,
}

impl StateFile {
    pub(crate) fn lock(path: &Path) -> Result<Self, CliError> {
        let name = path
            .file_name()
            .ok_or_else(|| invalid(format!("state path must name a file: {}", path.display())))?;
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let parent_meta =
            fs::metadata(parent).map_err(|e| io_error("inspect state directory", parent, e))?;
        if !parent_meta.is_dir() {
            return Err(invalid(format!(
                "state parent is not a directory: {}",
                parent.display()
            )));
        }
        check_target(path)?;
        let mut lock_name = OsString::from(name);
        lock_name.push(".lock");
        let lock_path = parent.join(lock_name);
        check_target(&lock_path)?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true);
        private_no_follow(&mut options);
        let lock = options
            .open(&lock_path)
            .map_err(|e| io_error("open state lock", &lock_path, e))?;
        require_regular(&lock, &lock_path)?;
        match lock.try_lock() {
            Ok(()) => {}
            Err(TryLockError::WouldBlock) => {
                return Err(CliError::new(
                    ErrorCode::Conflict,
                    format!("state file is already locked: {}", path.display()),
                ));
            }
            Err(TryLockError::Error(error)) => {
                return Err(io_error("lock state file", &lock_path, error));
            }
        }
        // Check again after acquiring the lock, so validation describes the
        // checkpoint protected by this owner rather than the previous owner.
        check_target(path)?;
        Ok(Self {
            path: path.to_owned(),
            parent: parent.to_owned(),
            _lock: lock,
        })
    }

    pub(crate) fn load<T: DeserializeOwned>(&self) -> Result<Option<T>, CliError> {
        check_target(&self.path)?;
        let mut options = OpenOptions::new();
        options.read(true);
        private_no_follow(&mut options);
        let file = match options.open(&self.path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(io_error("open state file", &self.path, error)),
        };
        require_regular(&file, &self.path)?;
        let size = file
            .metadata()
            .map_err(|e| io_error("inspect state file", &self.path, e))?
            .len();
        if size > MAX_STATE_BYTES {
            return Err(too_large(&self.path));
        }
        // Also bound the read in case an unrelated writer grows the file after
        // metadata was checked (advisory locks require cooperating callers).
        let mut bytes = Vec::new();
        file.take(MAX_STATE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| io_error("read state file", &self.path, e))?;
        if bytes.len() as u64 > MAX_STATE_BYTES {
            return Err(too_large(&self.path));
        }
        serde_json::from_slice(&bytes).map(Some).map_err(|e| {
            invalid(format!(
                "invalid or corrupt state file {}: {e}",
                self.path.display()
            ))
        })
    }

    pub(crate) fn save<T: Serialize>(&self, value: &T) -> Result<(), CliError> {
        check_target(&self.path)?;
        let mut temporary = TemporaryFile::create(&self.parent)?;
        {
            let file = temporary.file.as_mut().expect("temporary file is open");
            let mut buffered = BufWriter::with_capacity(64 * 1024, file);
            let mut writer = BoundedWriter {
                inner: &mut buffered,
                remaining: MAX_STATE_BYTES,
            };
            serde_json::to_writer(&mut writer, value).map_err(|error| {
                if let Some(kind) = error.io_error_kind() {
                    io_error("write state file", &self.path, io::Error::new(kind, error))
                } else {
                    invalid(format!(
                        "cannot serialize state file {}: {error}",
                        self.path.display()
                    ))
                }
            })?;
            writer
                .flush()
                .map_err(|e| io_error("write state file", &self.path, e))?;
        }
        temporary
            .file
            .as_ref()
            .expect("temporary file is open")
            .sync_all()
            .map_err(|e| io_error("sync state file", &self.path, e))?;
        // Close before rename for platforms which prohibit moving open files.
        drop(temporary.file.take());
        check_target(&self.path)?;
        fs::rename(&temporary.path, &self.path)
            .map_err(|e| io_error("replace state file", &self.path, e))?;
        temporary.committed = true;
        #[cfg(unix)]
        File::open(&self.parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|e| io_error("sync state directory after replacement", &self.parent, e))?;
        Ok(())
    }
}

fn invalid(message: impl Into<String>) -> CliError {
    CliError::new(ErrorCode::InvalidInput, message)
}

fn too_large(path: &Path) -> CliError {
    invalid(format!("state file exceeds 64 MiB: {}", path.display()))
}

fn io_error(action: &str, path: &Path, error: io::Error) -> CliError {
    let code = match error.kind() {
        io::ErrorKind::PermissionDenied => ErrorCode::PermissionDenied,
        io::ErrorKind::NotFound => ErrorCode::NotFound,
        io::ErrorKind::InvalidInput => ErrorCode::InvalidInput,
        _ => ErrorCode::CommandFailed,
    };
    CliError::new(code, format!("{action} {}: {error}", path.display()))
}

fn check_target(path: &Path) -> Result<(), CliError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(invalid(format!(
            "state paths must not be symbolic links: {}",
            path.display()
        ))),
        Ok(metadata) if !metadata.is_file() => Err(invalid(format!(
            "state path is not a regular file: {}",
            path.display()
        ))),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(io_error("inspect state path", path, error)),
    }
}

fn require_regular(file: &File, path: &Path) -> Result<(), CliError> {
    let metadata = file
        .metadata()
        .map_err(|e| io_error("inspect state file", path, e))?;
    if metadata.is_file() && !metadata.file_type().is_symlink() {
        Ok(())
    } else {
        Err(invalid(format!(
            "state path is not a regular file: {}",
            path.display()
        )))
    }
}

fn private_no_follow(options: &mut OpenOptions) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Open the reparse point itself, then reject it via handle metadata.
        options.custom_flags(0x0020_0000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
}

struct BoundedWriter<'a> {
    inner: &'a mut dyn Write,
    remaining: u64,
}

impl Write for BoundedWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() as u64 > self.remaining {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "state file exceeds 64 MiB",
            ));
        }
        let written = self.inner.write(bytes)?;
        self.remaining -= written as u64;
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

struct TemporaryFile {
    path: PathBuf,
    file: Option<File>,
    committed: bool,
}

impl TemporaryFile {
    fn create(parent: &Path) -> Result<Self, CliError> {
        // create_new is the authority for uniqueness, including across processes.
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        for _ in 0..128 {
            let serial = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
            let path = parent.join(format!(
                ".pb-state-{}-{stamp:x}-{serial:x}.tmp",
                std::process::id()
            ));
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            private_no_follow(&mut options);
            match options.open(&path) {
                Ok(file) => {
                    return Ok(Self {
                        path,
                        file: Some(file),
                        committed: false,
                    });
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(io_error("create temporary state file", &path, error)),
            }
        }
        Err(CliError::new(
            ErrorCode::CommandFailed,
            "could not create a unique temporary state file",
        ))
    }
}

impl Drop for TemporaryFile {
    fn drop(&mut self) {
        drop(self.file.take());
        if !self.committed {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::ser::SerializeMap;
    use serde_json::{Value, json};

    struct TestDirectory(PathBuf);
    impl TestDirectory {
        fn new() -> Self {
            let serial = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
            let stamp = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "pb-state-test-{}-{stamp}-{serial}",
                std::process::id()
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn state(&self) -> PathBuf {
            self.0.join("checkpoint.json")
        }
    }
    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn missing_state_round_trips_and_retains_lock_sidecar() {
        let directory = TestDirectory::new();
        let state = StateFile::lock(&directory.state()).unwrap();
        assert_eq!(state.load::<Value>().unwrap(), None);
        state.save(&json!({"offset": 7})).unwrap();
        assert_eq!(state.load::<Value>().unwrap(), Some(json!({"offset": 7})));
        state.save(&json!({"offset": 12})).unwrap();
        drop(state);
        assert!(directory.0.join("checkpoint.json.lock").is_file());
        let state = StateFile::lock(&directory.state()).unwrap();
        assert_eq!(state.load::<Value>().unwrap(), Some(json!({"offset": 12})));
    }

    #[test]
    fn lock_contention_fails_without_waiting_and_drop_releases() {
        let directory = TestDirectory::new();
        let state = StateFile::lock(&directory.state()).unwrap();
        let error = StateFile::lock(&directory.state()).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        drop(state);
        assert!(StateFile::lock(&directory.state()).is_ok());
    }

    #[test]
    fn corrupt_and_oversized_state_are_rejected() {
        let directory = TestDirectory::new();
        fs::write(directory.state(), b"{broken").unwrap();
        let state = StateFile::lock(&directory.state()).unwrap();
        let error = state.load::<Value>().unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidInput);
        assert!(error.message.contains("corrupt"));
        File::create(directory.state())
            .unwrap()
            .set_len(MAX_STATE_BYTES + 1)
            .unwrap();
        let error = state.load::<Value>().unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidInput);
        assert!(error.message.contains("64 MiB"));
    }

    struct SerializationFailure;
    impl Serialize for SerializationFailure {
        fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            let mut map = serializer.serialize_map(Some(2))?;
            map.serialize_entry("partial", &1)?;
            Err(serde::ser::Error::custom("injected serialization failure"))
        }
    }

    #[test]
    fn failed_save_preserves_previous_state_and_cleans_temporary_file() {
        let directory = TestDirectory::new();
        let state = StateFile::lock(&directory.state()).unwrap();
        state.save(&json!({"offset": 7})).unwrap();
        let previous = fs::read(directory.state()).unwrap();
        assert!(state.save(&SerializationFailure).is_err());
        assert_eq!(fs::read(directory.state()).unwrap(), previous);
        assert_eq!(fs::read_dir(&directory.0).unwrap().count(), 2);
    }

    #[test]
    fn missing_parent_and_directory_targets_are_rejected() {
        let directory = TestDirectory::new();
        let missing = directory.0.join("missing");
        let error = StateFile::lock(&missing.join("state.json")).unwrap_err();
        assert_eq!(error.code, ErrorCode::NotFound);
        assert!(!missing.exists());
        let error = StateFile::lock(&directory.0).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidInput);
    }

    #[cfg(unix)]
    #[test]
    fn unwritable_parent_reports_permission_error_and_preserves_checkpoint() {
        use std::os::unix::fs::PermissionsExt;
        // A root process bypasses Unix mode checks.
        if unsafe { libc::geteuid() } == 0 {
            return;
        }
        let directory = TestDirectory::new();
        let state = StateFile::lock(&directory.state()).unwrap();
        state.save(&json!({"offset": 7})).unwrap();
        fs::set_permissions(&directory.0, fs::Permissions::from_mode(0o500)).unwrap();
        let result = state.save(&json!({"offset": 12}));
        fs::set_permissions(&directory.0, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(result.unwrap_err().code, ErrorCode::PermissionDenied);
        assert_eq!(state.load::<Value>().unwrap(), Some(json!({"offset": 7})));
        assert_eq!(fs::read_dir(&directory.0).unwrap().count(), 2);
    }

    #[cfg(unix)]
    #[test]
    fn symlink_inserted_after_lock_cannot_redirect_reads_or_writes() {
        use std::os::unix::fs::symlink;
        let directory = TestDirectory::new();
        let state = StateFile::lock(&directory.state()).unwrap();
        let target = directory.0.join("actual.json");
        fs::write(&target, b"{\"offset\":7}").unwrap();
        symlink(&target, directory.state()).unwrap();
        assert_eq!(
            state.load::<Value>().unwrap_err().code,
            ErrorCode::InvalidInput
        );
        assert_eq!(
            state.save(&json!({"offset": 12})).unwrap_err().code,
            ErrorCode::InvalidInput
        );
        assert_eq!(fs::read(&target).unwrap(), b"{\"offset\":7}");
    }

    #[cfg(unix)]
    #[test]
    fn checkpoint_and_lock_are_private_and_symlink_targets_are_rejected() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let directory = TestDirectory::new();
        let path = directory.state();
        let lock_path = directory.0.join("checkpoint.json.lock");
        let state = StateFile::lock(&path).unwrap();
        state.save(&json!({"offset": 1})).unwrap();
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(&lock_path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        drop(state);
        let target = directory.0.join("actual.json");
        fs::rename(&path, &target).unwrap();
        symlink(&target, &path).unwrap();
        assert_eq!(
            StateFile::lock(&path).unwrap_err().code,
            ErrorCode::InvalidInput
        );
        fs::remove_file(&path).unwrap();
        fs::remove_file(&lock_path).unwrap();
        symlink(&target, &lock_path).unwrap();
        assert_eq!(
            StateFile::lock(&path).unwrap_err().code,
            ErrorCode::InvalidInput
        );
    }
}
