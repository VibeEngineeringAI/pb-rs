//! Append-oriented file collection. A scan only proposes checkpoint changes;
//! the caller commits and persists them after acknowledging the upload.
//!
//! Sources must append between rotations. Bounded byte probes detect observed
//! truncation and rewriting, but cannot provide snapshot isolation against an
//! arbitrary concurrent rewrite. Unread rotated data remains discoverable only
//! while its filename matches the include and exclude rules.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, Metadata};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum FileFormat {
    Jsonl,
    Text,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StartPosition {
    Beginning,
    End,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum InvalidJsonPolicy {
    Error,
    Skip,
}

#[derive(Clone, Debug)]
pub(crate) struct FileOptions {
    pub includes: Vec<String>,
    pub excludes: Vec<String>,
    pub format: FileFormat,
    pub from: StartPosition,
    pub invalid_json: InvalidJsonPolicy,
    pub max_line_bytes: usize,
    /// Maximum serialized payload bytes in one proposed batch.
    pub max_batch_bytes: usize,
    /// Complete source lines per scan, including explicitly skipped JSONL.
    pub max_events: usize,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub(crate) struct FileState {
    #[serde(default)]
    initialized: bool,
    #[serde(default)]
    revision: u64,
    #[serde(default)]
    next_generation: u64,
    #[serde(default)]
    files: BTreeMap<String, Checkpoint>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Checkpoint {
    identity: String,
    generation_number: u64,
    generation: String,
    offset: u64,
    sequence: u64,
    /// Starting at EOF inside a line requires discarding its future remainder.
    discard_fragment: bool,
    /// Exact bytes immediately preceding offset detect most copytruncate cases,
    /// including truncation followed by growth beyond the old offset.
    anchor: Vec<u8>,
    /// A prefix also catches rewrites whose tail happens to match the anchor.
    prefix: Vec<u8>,
    #[serde(default)]
    missing_scans: u8,
}

#[derive(Clone, Debug)]
pub(crate) struct FileRecord {
    /// Canonical JSON object text, serialized once for both budgeting and upload.
    pub payload: String,
    pub source_path: String,
    pub source_generation: String,
    pub source_sequence: u64,
    pub event_id: String,
}

/// Serializes exactly like a one-entry `message` map without allocating one.
#[derive(Serialize)]
struct TextPayload<'a> {
    message: &'a str,
}

#[derive(Clone, Debug)]
pub(crate) struct PendingBatch {
    pub records: Vec<FileRecord>,
    pub skipped_lines: u64,
    #[cfg(test)]
    pub payload_bytes: usize,
    pub has_incomplete_lines: bool,
    /// A scan budget boundary is not evidence that all sources are drained.
    pub scan_limit_reached: bool,
    base_state: FileState,
    next_state: FileState,
}

impl PendingBatch {
    pub(crate) fn has_state_changes(&self) -> bool {
        self.base_state != self.next_state
    }

    pub(crate) fn next_state(&self) -> &FileState {
        &self.next_state
    }
}

pub(crate) struct FileCollector {
    options: FileOptions,
    excludes: Vec<glob::Pattern>,
    state: FileState,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ScanError {
    message: String,
    retryable: bool,
}

impl ScanError {
    pub(crate) fn retryable(&self) -> bool {
        self.retryable
    }

    fn retry(message: String) -> Self {
        Self {
            message,
            retryable: true,
        }
    }

    fn io(path: &Path, error: std::io::Error) -> Self {
        Self::read(error).context(path.display().to_string())
    }

    fn read(error: std::io::Error) -> Self {
        let retryable = matches!(
            error.kind(),
            std::io::ErrorKind::Interrupted
                | std::io::ErrorKind::UnexpectedEof
                | std::io::ErrorKind::NotFound
        );
        Self {
            message: error.to_string(),
            retryable,
        }
    }

    fn context(mut self, prefix: String) -> Self {
        self.message = format!("{prefix}: {}", self.message);
        self
    }
}

impl From<String> for ScanError {
    fn from(message: String) -> Self {
        Self {
            message,
            retryable: false,
        }
    }
}

impl std::fmt::Display for ScanError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for ScanError {}

impl FileCollector {
    pub(crate) fn new(options: FileOptions, state: FileState) -> Result<Self, String> {
        if options.includes.is_empty() {
            return Err("file collection requires at least one include path".into());
        }
        if options.max_line_bytes == 0 || options.max_batch_bytes == 0 || options.max_events == 0 {
            return Err("file collection byte and event limits must be greater than zero".into());
        }
        for pattern in &options.includes {
            glob::Pattern::new(pattern)
                .map_err(|error| format!("invalid include glob {pattern:?}: {error}"))?;
        }
        let excludes = options
            .excludes
            .iter()
            .map(|pattern| {
                glob::Pattern::new(pattern)
                    .map_err(|error| format!("invalid exclude glob {pattern:?}: {error}"))
            })
            .collect::<Result<_, _>>()?;
        Ok(Self {
            options,
            excludes,
            state,
        })
    }

    pub(crate) fn state(&self) -> &FileState {
        &self.state
    }

    /// Repeated scans against unchanged source bytes produce the same IDs and
    /// records. Keep and retry the returned batch to preserve its exact contents.
    pub(crate) fn scan(&self) -> Result<PendingBatch, ScanError> {
        let paths = self.discover()?;
        let mut next_state = self.state.clone();
        // Retain a short rename-discovery grace period, without accumulating
        // every filename ever seen. Discovery, rather than the processing
        // budget, determines presence.
        let discovered_keys = paths
            .iter()
            .map(|path| path_key(path))
            .collect::<Result<BTreeSet<_>, _>>()?;
        next_state.files.retain(|key, checkpoint| {
            if discovered_keys.contains(key) {
                checkpoint.missing_scans = 0;
            } else {
                checkpoint.missing_scans = checkpoint.missing_scans.saturating_add(1);
            }
            checkpoint.missing_scans < 2
        });
        // Older checkpoints did not persist a global generation allocator.
        for saved in self.state.files.values() {
            next_state.next_generation = next_state
                .next_generation
                .max(saved.generation_number.saturating_add(1));
        }
        let mut sources = Vec::new();
        let mut seen_identities = BTreeSet::new();
        // Index current identity progress once rather than searching all saved
        // paths every time a discovered file is opened.
        let mut latest: BTreeMap<&str, &Checkpoint> = BTreeMap::new();
        for checkpoint in self.state.files.values().filter(|c| c.missing_scans == 0) {
            let saved = latest.entry(&checkpoint.identity).or_insert(checkpoint);
            if (
                checkpoint.generation_number,
                checkpoint.sequence,
                checkpoint.offset,
            ) > (saved.generation_number, saved.sequence, saved.offset)
            {
                *saved = checkpoint;
            }
        }

        // Initialize every discovered file before enforcing the batch limit, so
        // --from=end applies to all files present on the initial scan.
        for path in paths {
            let mut file = match open_source(&path) {
                Ok(file) => file,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => return Err(ScanError::io(&path, error)),
            };
            let metadata = file.metadata().map_err(|e| ScanError::io(&path, e))?;
            if !metadata.is_file() {
                continue;
            }
            let identity = file_identity(&file, &metadata).map_err(|e| ScanError::io(&path, e))?;
            if !seen_identities.insert(identity.clone()) {
                continue;
            }
            let key = path_key(&path)?;
            let at_path = self.state.files.get(&key);
            // Older states may contain stale hardlink aliases. The latest
            // generation/progress is authoritative even if an older alias's
            // byte probes still match after a rewrite.
            let previous = latest
                .get(identity.as_str())
                .copied()
                .into_iter()
                .chain(at_path.filter(|c| c.identity == identity))
                .max_by_key(|c| (c.generation_number, c.sequence, c.offset));
            let checkpoint = match previous {
                Some(saved)
                    if checkpoint_matches(&mut file, saved, metadata.len())
                        .map_err(|e| ScanError::io(&path, e))? =>
                {
                    saved.clone()
                }
                _ => {
                    let generation_number = next_state.next_generation;
                    next_state.next_generation = next_state
                        .next_generation
                        .checked_add(1)
                        .ok_or_else(|| "file generation counter exhausted".to_owned())?;
                    let start_at_end = !self.state.initialized
                        && self.options.from == StartPosition::End
                        && previous.is_none()
                        && at_path.is_none();
                    let offset = if start_at_end { metadata.len() } else { 0 };
                    let discard_fragment = if offset > 0 {
                        file.seek(SeekFrom::Start(offset - 1))
                            .map_err(|e| ScanError::io(&path, e))?;
                        let mut last = [0];
                        file.read_exact(&mut last)
                            .map_err(|e| ScanError::io(&path, e))?;
                        last[0] != b'\n'
                    } else {
                        false
                    };
                    let mut checkpoint = Checkpoint {
                        generation: generation_id(&key, &identity, generation_number),
                        identity,
                        generation_number,
                        offset,
                        sequence: 0,
                        discard_fragment,
                        anchor: Vec::new(),
                        prefix: Vec::new(),
                        missing_scans: 0,
                    };
                    refresh_anchor(&mut file, &mut checkpoint)
                        .map_err(|e| ScanError::io(&path, e))?;
                    checkpoint
                }
            };
            let mut checkpoint = checkpoint;
            checkpoint.missing_scans = 0;
            next_state.files.insert(key.clone(), checkpoint);
            sources.push((path, key));
        }
        // Keep one checkpoint per discovered identity so future alias switches
        // cannot select superseded progress. This also repairs legacy states.
        let source_keys = sources.iter().map(|(_, key)| key).collect::<BTreeSet<_>>();
        next_state.files.retain(|key, checkpoint| {
            !seen_identities.contains(&checkpoint.identity) || source_keys.contains(key)
        });
        next_state.initialized = true;

        // Only acknowledged checkpoints advance revision. Retries therefore
        // preserve order, while a continuously busy source cannot starve peers.
        if !sources.is_empty() {
            let start = (self.state.revision % sources.len() as u64) as usize;
            sources.rotate_left(start);
        }

        let mut records = Vec::new();
        let mut payload_bytes = 0;
        let mut skipped_lines = 0;
        let mut has_incomplete_lines = false;
        for (path, key) in sources {
            if records.len() as u64 + skipped_lines >= self.options.max_events as u64
                || payload_bytes >= self.options.max_batch_bytes
            {
                break;
            }
            let checkpoint = next_state
                .files
                .get_mut(&key)
                .expect("initialized checkpoint");
            let mut file = match open_source(&path) {
                Ok(file) => file,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    return Err(ScanError::retry(format!(
                        "{} disappeared during collection; retry the scan",
                        path.display()
                    )));
                }
                Err(error) => return Err(ScanError::io(&path, error)),
            };
            let metadata = file.metadata().map_err(|e| ScanError::io(&path, e))?;
            if !metadata.is_file()
                || checkpoint.identity
                    != file_identity(&file, &metadata).map_err(|e| ScanError::io(&path, e))?
                || !checkpoint_matches(&mut file, checkpoint, metadata.len())
                    .map_err(|e| ScanError::io(&path, e))?
            {
                return Err(ScanError::retry(format!(
                    "{} changed during collection; retry the scan",
                    path.display()
                )));
            }
            file.seek(SeekFrom::Start(checkpoint.offset))
                .map_err(|e| ScanError::io(&path, e))?;
            let mut reader = BufReader::new(file);
            let starting_checkpoint = checkpoint.clone();
            let mut proof = ReadProof::new(checkpoint);
            loop {
                if records.len() as u64 + skipped_lines >= self.options.max_events as u64
                    || payload_bytes >= self.options.max_batch_bytes
                {
                    break;
                }
                if checkpoint.discard_fragment {
                    let (consumed, complete) = discard_line(&mut reader, &mut proof)
                        .map_err(|e| ScanError::io(&path, e))?;
                    checkpoint.offset += consumed;
                    if !complete {
                        has_incomplete_lines = true;
                        break;
                    }
                    checkpoint.discard_fragment = false;
                    continue;
                }
                let line_start = checkpoint.offset;
                let (line, incomplete) = read_line(&mut reader, self.options.max_line_bytes)
                    .map_err(|error| {
                        error.context(format!("{} at byte {line_start}", path.display()))
                    })?;
                has_incomplete_lines |= incomplete;
                let Some(CompleteLine {
                    bytes: line,
                    consumed,
                }) = line
                else {
                    break;
                };
                let contents = line.strip_suffix(b"\r").unwrap_or(&line);
                let parsed = match self.options.format {
                    FileFormat::Jsonl => match serde_json::from_slice::<Value>(contents) {
                        Ok(Value::Object(object)) => Ok(serde_json::to_string(&object)),
                        Ok(_) => Err("JSONL records must be JSON objects".to_owned()),
                        Err(error) => Err(format!("invalid JSONL: {error}")),
                    },
                    FileFormat::Text => std::str::from_utf8(contents)
                        .map(|message| serde_json::to_string(&TextPayload { message }))
                        .map_err(|error| format!("invalid UTF-8 text: {error}")),
                };
                let payload = match parsed {
                    Ok(payload) => payload,
                    Err(_error)
                        if self.options.format == FileFormat::Jsonl
                            && self.options.invalid_json == InvalidJsonPolicy::Skip =>
                    {
                        skipped_lines += 1;
                        proof.consume(&line);
                        proof.consume(b"\n");
                        checkpoint.offset += consumed;
                        checkpoint.sequence += 1;
                        continue;
                    }
                    Err(error) => {
                        return Err(
                            format!("{} at byte {line_start}: {error}", path.display()).into()
                        );
                    }
                };
                // Serialization failures are not invalid input, so never skip them.
                let payload = payload.map_err(|error| error.to_string())?;
                let bytes = payload.len();
                if bytes > self.options.max_batch_bytes {
                    return Err(format!(
                        "{} at byte {line_start}: record is {bytes} bytes, exceeding the {} byte batch limit",
                        path.display(),
                        self.options.max_batch_bytes
                    ).into());
                }
                if payload_bytes + bytes > self.options.max_batch_bytes {
                    break;
                }
                checkpoint.offset += consumed;
                proof.consume(&line);
                proof.consume(b"\n");
                checkpoint.sequence += 1;
                payload_bytes += bytes;
                records.push(FileRecord {
                    payload,
                    source_path: key.clone(),
                    source_generation: checkpoint.generation.clone(),
                    source_sequence: checkpoint.sequence,
                    event_id: format!("{}-{}", checkpoint.generation, checkpoint.sequence),
                });
            }
            let mut file = reader.into_inner();
            validate_read(&mut file, &starting_checkpoint, checkpoint, &proof)
                .map_err(|e| ScanError::io(&path, e))?;
        }
        if next_state != self.state {
            next_state.revision = self.state.revision.saturating_add(1);
        }
        Ok(PendingBatch {
            scan_limit_reached: records.len() as u64 + skipped_lines
                >= self.options.max_events as u64
                || payload_bytes >= self.options.max_batch_bytes,
            records,
            skipped_lines,
            #[cfg(test)]
            payload_bytes,
            has_incomplete_lines,
            base_state: self.state.clone(),
            next_state,
        })
    }

    /// Call only after upload acknowledgement (or for a batch without records).
    /// The caller must durably save state() before accepting another batch.
    pub(crate) fn commit(&mut self, batch: &PendingBatch) -> Result<(), String> {
        if self.state != batch.base_state {
            return Err("cannot commit a stale file collection batch".into());
        }
        self.state = batch.next_state.clone();
        Ok(())
    }

    fn discover(&self) -> Result<BTreeSet<PathBuf>, ScanError> {
        let mut paths = BTreeSet::new();
        for include in &self.options.includes {
            let matches = glob::glob(include)
                .map_err(|e| format!("invalid include glob {include:?}: {e}"))?;
            for path in matches {
                let path = match path {
                    Ok(path) => path,
                    Err(error) => {
                        let path = error.path().to_owned();
                        let canonical = fs::canonicalize(&path).ok();
                        if self.excluded_directory(&path)
                            || canonical
                                .as_deref()
                                .is_some_and(|p| self.excluded_directory(p))
                        {
                            continue;
                        }
                        return Err(
                            ScanError::io(&path, error.into()).context("discover files".into())
                        );
                    }
                };
                // A listable directory can still deny search permission on
                // its children. Honor lexical exclusions before resolving them.
                if self
                    .excludes
                    .iter()
                    .any(|pattern| pattern.matches_path(&path))
                {
                    continue;
                }
                let canonical = match fs::canonicalize(&path) {
                    Ok(path) => path,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                    Err(error) => return Err(ScanError::io(&path, error)),
                };
                if self
                    .excludes
                    .iter()
                    .any(|pattern| pattern.matches_path(&canonical))
                {
                    continue;
                }
                // Opening a directory as a file fails on Windows. Filter
                // discovery first; open_source still protects the stat/open
                // race against FIFOs on Unix.
                match fs::metadata(&canonical) {
                    Ok(metadata) if !metadata.is_file() => continue,
                    Ok(_) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                    Err(error) => return Err(ScanError::io(&path, error)),
                }
                paths.insert(canonical);
            }
        }
        Ok(paths)
    }

    fn excluded_directory(&self, path: &Path) -> bool {
        path.ancestors().any(|directory| {
            self.excludes.iter().any(|pattern| {
                // Suppress traversal errors only when a terminal recursive
                // wildcard excludes every descendant. Matching a directory
                // itself (with or without a trailing slash) is insufficient.
                pattern.as_str().rsplit(std::path::is_separator).next() == Some("**")
                    && directory.to_str().is_some_and(|name| {
                        pattern.matches(&format!("{name}{}", std::path::MAIN_SEPARATOR))
                    })
            })
        })
    }
}

fn path_key(path: &Path) -> Result<String, String> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        format!("file collection requires UTF-8 source paths; unsupported path {path:?}")
    })
}

fn open_source(path: &Path) -> std::io::Result<File> {
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    // Discovery may encounter a FIFO, or a regular path may be replaced by
    // one between stat and open. Never block waiting for a pipe writer.
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    options.open(path)
}

#[cfg(unix)]
fn file_identity(_file: &File, metadata: &Metadata) -> std::io::Result<String> {
    use std::os::unix::fs::MetadataExt;
    Ok(format!("{}:{}", metadata.dev(), metadata.ino()))
}

#[cfg(windows)]
fn file_identity(file: &File, _metadata: &Metadata) -> std::io::Result<String> {
    use std::ffi::c_void;
    use std::mem::MaybeUninit;
    use std::os::windows::io::AsRawHandle;

    #[repr(C)]
    struct FileTime {
        low: u32,
        high: u32,
    }
    #[repr(C)]
    struct FileInformation {
        attributes: u32,
        creation: FileTime,
        access: FileTime,
        write: FileTime,
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetFileInformationByHandle(
            handle: *mut c_void,
            information: *mut FileInformation,
        ) -> i32;
    }
    let mut information = MaybeUninit::<FileInformation>::uninit();
    // SAFETY: the live File owns a valid handle and the output pointer has the
    // exact BY_HANDLE_FILE_INFORMATION layout and sufficient writable storage.
    let result =
        unsafe { GetFileInformationByHandle(file.as_raw_handle(), information.as_mut_ptr()) };
    if result == 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: a successful call initializes the entire output structure.
    let information = unsafe { information.assume_init() };
    Ok(format!(
        "{}:{:08x}{:08x}",
        information.volume, information.index_high, information.index_low
    ))
}

#[cfg(not(any(unix, windows)))]
fn file_identity(_file: &File, _metadata: &Metadata) -> std::io::Result<String> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "stable file identities are unsupported on this platform",
    ))
}

fn generation_id(path: &str, identity: &str, generation: u64) -> String {
    // An explicitly specified hash stays stable across Rust versions/processes.
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in path
        .bytes()
        .chain([0])
        .chain(identity.bytes())
        .chain([0])
        .chain(generation.to_le_bytes())
    {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("file-{hash:016x}")
}

fn checkpoint_matches(file: &mut File, saved: &Checkpoint, len: u64) -> std::io::Result<bool> {
    if len < saved.offset || saved.anchor.len() as u64 > saved.offset {
        return Ok(false);
    }
    let mut bytes = vec![0; saved.anchor.len()];
    file.seek(SeekFrom::Start(saved.offset - bytes.len() as u64))?;
    if !read_exact_or_changed(file, &mut bytes)? {
        return Ok(false);
    }
    if bytes != saved.anchor {
        return Ok(false);
    }
    bytes.resize(saved.prefix.len(), 0);
    file.seek(SeekFrom::Start(0))?;
    if !read_exact_or_changed(file, &mut bytes)? {
        return Ok(false);
    }
    Ok(bytes == saved.prefix)
}

fn read_exact_or_changed(file: &mut File, bytes: &mut [u8]) -> std::io::Result<bool> {
    match file.read_exact(bytes) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => Ok(false),
        Err(error) => Err(error),
    }
}

/// Bounded evidence of the actual bytes accepted during this scan. Reading
/// anchors afresh after a truncate/regrow would bless different bytes under an
/// old generation, so compare against the consumed bytes instead.
struct ReadProof {
    prefix: Vec<u8>,
    anchor: Vec<u8>,
}

impl ReadProof {
    fn new(checkpoint: &Checkpoint) -> Self {
        Self {
            prefix: checkpoint.prefix.clone(),
            anchor: checkpoint.anchor.clone(),
        }
    }

    fn consume(&mut self, bytes: &[u8]) {
        let prefix_needed = 64_usize.saturating_sub(self.prefix.len());
        self.prefix
            .extend_from_slice(&bytes[..prefix_needed.min(bytes.len())]);
        if bytes.len() >= 64 {
            self.anchor.clear();
            self.anchor.extend_from_slice(&bytes[bytes.len() - 64..]);
        } else {
            let excess = (self.anchor.len() + bytes.len()).saturating_sub(64);
            self.anchor.drain(..excess);
            self.anchor.extend_from_slice(bytes);
        }
    }
}

fn validate_read(
    file: &mut File,
    before: &Checkpoint,
    after: &mut Checkpoint,
    proof: &ReadProof,
) -> std::io::Result<()> {
    let len = file.metadata()?.len();
    let mut observed = after.clone();
    observed.anchor.clone_from(&proof.anchor);
    observed.prefix.clone_from(&proof.prefix);
    if !checkpoint_matches(file, before, len)? || !checkpoint_matches(file, &observed, len)? {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Interrupted,
            "source changed during collection; retry the scan",
        ));
    }
    after.anchor = observed.anchor;
    after.prefix = observed.prefix;
    Ok(())
}

fn refresh_anchor(file: &mut File, checkpoint: &mut Checkpoint) -> std::io::Result<()> {
    let size = checkpoint.offset.min(64) as usize;
    checkpoint.anchor.resize(size, 0);
    file.seek(SeekFrom::Start(checkpoint.offset - size as u64))?;
    file.read_exact(&mut checkpoint.anchor)?;
    checkpoint.prefix.resize(size, 0);
    file.seek(SeekFrom::Start(0))?;
    file.read_exact(&mut checkpoint.prefix)
}

/// A partial final line is deliberately left uncheckpointed and reread later.
struct CompleteLine {
    bytes: Vec<u8>,
    consumed: u64,
}

fn read_line(
    reader: &mut impl BufRead,
    limit: usize,
) -> Result<(Option<CompleteLine>, bool), ScanError> {
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().map_err(ScanError::read)?;
        if available.is_empty() {
            return Ok((None, !line.is_empty()));
        }
        let end = available.iter().position(|&byte| byte == b'\n');
        let length = end.unwrap_or(available.len());
        if line.len().saturating_add(length) > limit {
            return Err(format!("line exceeds the {limit} byte line limit").into());
        }
        line.extend_from_slice(&available[..length]);
        reader.consume(length + usize::from(end.is_some()));
        if end.is_some() {
            let consumed = line.len() as u64 + 1;
            return Ok((
                Some(CompleteLine {
                    bytes: line,
                    consumed,
                }),
                false,
            ));
        }
    }
}

fn discard_line(reader: &mut impl BufRead, proof: &mut ReadProof) -> std::io::Result<(u64, bool)> {
    let mut consumed = 0;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return Ok((consumed, false));
        }
        let end = available.iter().position(|&byte| byte == b'\n');
        let length = end.map_or(available.len(), |index| index + 1);
        proof.consume(&available[..length]);
        consumed += length as u64;
        reader.consume(length);
        if end.is_some() {
            return Ok((consumed, true));
        }
    }
}

#[cfg(test)]
#[path = "files_tests.rs"]
mod tests;
