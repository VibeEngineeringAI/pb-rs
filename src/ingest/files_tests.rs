use super::*;
use std::io::Write;
use std::sync::atomic::{AtomicU64, Ordering};

struct Directory(PathBuf);

impl Directory {
    fn new() -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "pb-file-collector-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }

    fn file(&self, name: &str, contents: &str) -> PathBuf {
        let path = self.0.join(name);
        fs::write(&path, contents).unwrap();
        path
    }

    fn options(&self) -> FileOptions {
        FileOptions {
            includes: vec![format!("{}/*.log", self.0.display())],
            excludes: vec![],
            format: FileFormat::Jsonl,
            from: StartPosition::Beginning,
            invalid_json: InvalidJsonPolicy::Error,
            max_line_bytes: 4096,
            max_batch_bytes: 8192,
            max_events: 100,
        }
    }
}

impl Drop for Directory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn append(path: &Path, contents: &str) {
    let mut file = fs::OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(contents.as_bytes()).unwrap();
}

fn restart(collector: &FileCollector) -> FileCollector {
    let saved = serde_json::to_vec(collector.state()).unwrap();
    FileCollector::new(
        collector.options.clone(),
        serde_json::from_slice(&saved).unwrap(),
    )
    .unwrap()
}

fn check_alias_switch(truncate: bool, legacy_stale_state: bool) {
    let dir = Directory::new();
    let path = dir.file("b.log", "first\n");
    let alias = dir.0.join("a.log");
    let mut options = dir.options();
    options.format = FileFormat::Text;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let first = collector.scan().unwrap();
    collector.commit(&first).unwrap();
    let stale = collector.state().files[&path_key(&path).unwrap()].clone();
    collector = restart(&collector);
    fs::hard_link(&path, &alias).unwrap();
    append(&path, "second\n");
    let second = collector.scan().unwrap();
    assert_eq!(second.records.len(), 1);
    assert_eq!(second.records[0].payload["message"], "second");
    collector.commit(&second).unwrap();
    if legacy_stale_state {
        // Persist exactly the duplicate checkpoint shape written by the old
        // collector, even after the fix starts removing superseded aliases.
        collector
            .state
            .files
            .insert(path_key(&path).unwrap(), stale);
    }
    collector = restart(&collector);
    fs::remove_file(&alias).unwrap();
    if truncate {
        // Same length as the old contents: only the latest byte probes expose
        // the rewrite. The stale checkpoint's "first\n" probe still matches.
        fs::write(&path, "first\nCHANGED\n").unwrap();
    } else {
        append(&path, "third\n");
    }
    let third = collector.scan().unwrap();
    let prior_ids = [&first.records[0].event_id, &second.records[0].event_id];
    assert!(
        third
            .records
            .iter()
            .all(|r| !prior_ids.contains(&&r.event_id)),
        "alias switching reused a previously committed event ID: {:?}",
        third.records
    );
    if truncate {
        assert_eq!(third.records.len(), 2);
        assert_eq!(third.records[1].payload["message"], "CHANGED");
        assert_ne!(
            third.records[1].source_generation,
            second.records[0].source_generation
        );
        assert_eq!(third.records[1].source_sequence, 2);
    } else {
        assert_eq!(third.records.len(), 1);
        assert_eq!(third.records[0].payload["message"], "third");
        assert_eq!(
            third.records[0].source_generation,
            second.records[0].source_generation
        );
        assert_eq!(third.records[0].source_sequence, 3);
    }
    collector.commit(&third).unwrap();
    assert!(restart(&collector).scan().unwrap().records.is_empty());
}

#[test]
fn hardlink_alias_switch_append_survives_serialized_restart() {
    check_alias_switch(false, false);
}

#[test]
fn hardlink_alias_switch_truncation_preserves_event_id_uniqueness() {
    check_alias_switch(true, false);
}

#[test]
fn legacy_stale_alias_append_uses_latest_committed_progress() {
    check_alias_switch(false, true);
}

#[test]
fn legacy_stale_alias_truncation_uses_latest_generation_probes() {
    check_alias_switch(true, true);
}

#[test]
fn skipped_input_is_bounded_and_next_scan_serves_another_file() {
    let dir = Directory::new();
    let busy = dir.file("a.log", "[]\nnot json\n[]\n");
    dir.file("b.log", "{\"source\":\"b\"}\n");
    let mut options = dir.options();
    options.invalid_json = InvalidJsonPolicy::Skip;
    options.max_events = 1;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let first = collector.scan().unwrap();
    assert_eq!(
        first.skipped_lines, 1,
        "skipped lines must consume scan budget"
    );
    assert!(first.records.is_empty());
    assert!(first.has_state_changes());
    let saved = &first.next_state().files[&path_key(&busy).unwrap()];
    assert_eq!((saved.offset, saved.sequence), (3, 1));
    assert_eq!(collector.scan().unwrap().next_state(), first.next_state());
    assert_eq!(collector.state(), &FileState::default());
    collector.commit(&first).unwrap();
    append(&busy, "[]\n");
    collector = restart(&collector);
    let second = collector.scan().unwrap();
    assert_eq!(second.skipped_lines, 0);
    assert_eq!(second.records.len(), 1);
    assert_eq!(second.records[0].payload["source"], "b");
    collector.commit(&second).unwrap();
    // Bounded skipped-only progress must remain durable across every restart.
    for expected_sequence in 2..=4 {
        collector = restart(&collector);
        let batch = collector.scan().unwrap();
        assert!(batch.records.is_empty());
        assert_eq!(batch.skipped_lines, 1);
        assert_eq!(
            batch.next_state().files[&path_key(&busy).unwrap()].sequence,
            expected_sequence
        );
        collector.commit(&batch).unwrap();
    }
    assert!(!restart(&collector).scan().unwrap().has_state_changes());
}

#[test]
fn missing_source_io_errors_are_retryable() {
    let error = ScanError::io(
        Path::new("disappeared-directory"),
        std::io::Error::from(std::io::ErrorKind::NotFound),
    );
    assert!(
        error.retryable(),
        "discovery must preserve transient I/O errors: {error}"
    );
}

#[cfg(unix)]
fn rerun_without_root_permissions(test_name: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;

    if unsafe { libc::geteuid() } != 0 {
        return false;
    }
    // Root bypasses both read and search permissions. Require a real
    // unprivileged run, including the permission assertions in the child.
    let scratch = Directory::new();
    fs::set_permissions(&scratch.0, fs::Permissions::from_mode(0o777)).unwrap();
    let output = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            &format!("ingest::files::tests::{test_name}"),
            "--nocapture",
        ])
        .env("TMPDIR", &scratch.0)
        .gid(65534)
        .uid(65534)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "unprivileged test failed: {}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    true
}

#[cfg(unix)]
fn check_recursive_exclusion(test_name: &str, exclude: &str, whole_subtree: bool) {
    use std::os::unix::fs::PermissionsExt;

    if rerun_without_root_permissions(test_name) {
        return;
    }
    let dir = Directory::new();
    dir.file("good.log", "{}\n");
    // A filename glob can match an ancestor without excluding its children.
    let directory_name = if exclude == "*.tmp" {
        "locked.tmp"
    } else {
        "locked"
    };
    let locked = dir.0.join(directory_name);
    fs::create_dir(&locked).unwrap();
    let hidden = locked.join("hidden.log");
    fs::write(&hidden, "{}\n").unwrap();
    if exclude == "*.tmp" {
        fs::write(locked.join("discarded.tmp"), "{}\n").unwrap();
    }
    struct Unlock(PathBuf);
    impl Drop for Unlock {
        fn drop(&mut self) {
            fs::set_permissions(&self.0, fs::Permissions::from_mode(0o700)).unwrap();
        }
    }
    let _unlock = Unlock(locked.clone());
    let mut options = dir.options();
    options.includes = vec![format!("{}/**/*", dir.0.display())];
    options.excludes = vec![exclude.into()];
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o700)).unwrap();
    let control = FileCollector::new(options.clone(), FileState::default())
        .unwrap()
        .scan()
        .unwrap();
    assert_eq!(control.records.len(), if whole_subtree { 1 } else { 2 });
    assert!(
        control
            .records
            .iter()
            .any(|r| r.source_path.ends_with("good.log"))
    );
    assert_eq!(
        control
            .records
            .iter()
            .any(|r| r.source_path.ends_with("hidden.log")),
        !whole_subtree,
        "readable control for {exclude}"
    );

    let mut unexpected = Vec::new();
    for mode in [0o000, 0o444] {
        fs::set_permissions(&locked, fs::Permissions::from_mode(mode)).unwrap();
        if mode == 0o000 {
            assert_eq!(
                fs::read_dir(&locked).unwrap_err().kind(),
                std::io::ErrorKind::PermissionDenied
            );
        } else {
            // Listing succeeds and glob yields the child, but canonicalizing
            // that child fails because the directory lacks search permission.
            assert!(
                fs::read_dir(&locked)
                    .unwrap()
                    .any(|entry| entry.unwrap().file_name() == "hidden.log")
            );
            assert!(
                glob::glob(&options.includes[0])
                    .unwrap()
                    .any(|entry| entry.is_ok_and(|p| p == hidden))
            );
        }
        assert_eq!(
            fs::canonicalize(&hidden).unwrap_err().kind(),
            std::io::ErrorKind::PermissionDenied
        );
        let mut unexcluded_options = options.clone();
        unexcluded_options.excludes.clear();
        let error = FileCollector::new(unexcluded_options, FileState::default())
            .unwrap()
            .scan()
            .unwrap_err();
        assert!(!error.retryable());
        assert!(error.to_string().contains(directory_name));

        let result = FileCollector::new(options.clone(), FileState::default())
            .unwrap()
            .scan();
        match result {
            Ok(batch) if whole_subtree => {
                assert_eq!(batch.records.len(), 1);
                assert!(batch.records[0].source_path.ends_with("good.log"));
            }
            Err(error) if !whole_subtree => {
                assert!(!error.retryable());
                assert!(error.to_string().contains(directory_name));
            }
            other => unexpected.push(format!("mode {mode:o}, exclude {exclude:?}: {other:?}")),
        }
    }
    assert!(
        unexpected.is_empty(),
        "incorrect exclusion behavior: {unexpected:#?}"
    );
}

#[cfg(unix)]
#[test]
fn recursive_glob_honors_excluded_unreadable_subtrees() {
    check_recursive_exclusion(
        "recursive_glob_honors_excluded_unreadable_subtrees",
        "**/locked/**",
        true,
    );
}

#[cfg(unix)]
#[test]
fn recursive_glob_directory_only_exclude_preserves_traversal_errors() {
    check_recursive_exclusion(
        "recursive_glob_directory_only_exclude_preserves_traversal_errors",
        "**/locked",
        false,
    );
}

#[cfg(unix)]
#[test]
fn recursive_glob_directory_slash_only_exclude_preserves_traversal_errors() {
    check_recursive_exclusion(
        "recursive_glob_directory_slash_only_exclude_preserves_traversal_errors",
        "**/locked/",
        false,
    );
}

#[cfg(unix)]
#[test]
fn recursive_glob_filename_exclude_preserves_traversal_errors() {
    check_recursive_exclusion(
        "recursive_glob_filename_exclude_preserves_traversal_errors",
        "*.tmp",
        false,
    );
}

#[test]
fn retry_restart_and_acknowledgement_preserve_identity_and_offsets() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{\"n\":1}\n{\"n\":2}\n");
    let options = dir.options();
    let mut collector = FileCollector::new(options.clone(), FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    assert_eq!(batch.records.len(), 2);
    let retry = collector.scan().unwrap();
    assert_eq!(retry.records[0].event_id, batch.records[0].event_id);
    assert_eq!(collector.state(), &FileState::default());
    let restarted = FileCollector::new(options.clone(), collector.state().clone()).unwrap();
    assert_eq!(
        restarted.scan().unwrap().records[1].event_id,
        batch.records[1].event_id
    );
    collector.commit(&batch).unwrap();
    assert_eq!(collector.state(), batch.next_state());
    assert!(collector.commit(&retry).is_err());
    let saved = serde_json::to_vec(collector.state()).unwrap();
    let mut restarted =
        FileCollector::new(options, serde_json::from_slice(&saved).unwrap()).unwrap();
    assert!(restarted.scan().unwrap().records.is_empty());
    append(&path, "{\"n\":3}\n");
    let batch = restarted.scan().unwrap();
    assert_eq!(batch.records[0].source_sequence, 3);
    restarted.commit(&batch).unwrap();
    assert!(!restarted.scan().unwrap().has_state_changes());
}

#[test]
fn incomplete_lines_wait_for_newline_and_crlf_is_supported() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{\"n\":1}\r\n{\"n\":");
    let mut collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let first = collector.scan().unwrap();
    assert_eq!(first.records.len(), 1);
    assert!(first.has_incomplete_lines);
    collector.commit(&first).unwrap();
    assert!(collector.scan().unwrap().records.is_empty());
    append(&path, "2}");
    assert!(collector.scan().unwrap().records.is_empty());
    append(&path, "\n");
    let second = collector.scan().unwrap();
    assert_eq!(second.records[0].payload["n"], 2);
    assert_eq!(second.records[0].source_sequence, 2);
    assert!(!second.has_incomplete_lines);
}

#[test]
fn end_skips_initial_files_but_discovers_new_files_from_beginning() {
    let dir = Directory::new();
    let path = dir.file("a.log", "{\"old\":true}\n");
    dir.file("b.log", "{\"old\":true}\n");
    let mut options = dir.options();
    options.from = StartPosition::End;
    options.max_events = 1;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let initial = collector.scan().unwrap();
    assert!(!initial.has_incomplete_lines);
    assert!(initial.records.is_empty());
    collector.commit(&initial).unwrap();
    append(&path, "{\"new\":1}\n");
    let batch = collector.scan().unwrap();
    assert_eq!(batch.records.len(), 1);
    assert_eq!(batch.records[0].payload["new"], 1);
    collector.commit(&batch).unwrap();
    dir.file("c.log", "{\"new\":2}\n");
    let batch = collector.scan().unwrap();
    assert_eq!(batch.records.len(), 1);
    assert_eq!(batch.records[0].payload["new"], 2);
}

#[test]
fn end_discards_fragment_continuation_across_restarts() {
    let dir = Directory::new();
    let path = dir.file("run.log", "old partial");
    let mut options = dir.options();
    options.from = StartPosition::End;
    options.format = FileFormat::Text;
    let mut collector = FileCollector::new(options.clone(), FileState::default()).unwrap();
    let initial = collector.scan().unwrap();
    assert!(initial.has_incomplete_lines);
    collector.commit(&initial).unwrap();
    append(&path, " continued");
    let partial = collector.scan().unwrap();
    assert!(partial.records.is_empty());
    assert!(partial.has_incomplete_lines);
    collector.commit(&partial).unwrap();
    let mut restarted = FileCollector::new(options, collector.state().clone()).unwrap();
    append(&path, " end\nnext\n");
    let batch = restarted.scan().unwrap();
    assert_eq!(batch.records.len(), 1);
    assert_eq!(batch.records[0].payload["message"], "next");
    assert!(!batch.has_incomplete_lines);
    restarted.commit(&batch).unwrap();
}

#[test]
fn saved_offsets_override_end_and_new_files_after_empty_scan_start_at_zero() {
    let dir = Directory::new();
    let options = dir.options();
    let mut collector = FileCollector::new(options.clone(), FileState::default()).unwrap();
    let initial = collector.scan().unwrap();
    collector.commit(&initial).unwrap();
    let mut options = options;
    options.from = StartPosition::End;
    let collector = FileCollector::new(options, collector.state().clone()).unwrap();
    dir.file("new.log", "{}\n");
    assert_eq!(collector.scan().unwrap().records.len(), 1);
}

#[test]
fn changing_start_policy_preserves_existing_saved_offsets() {
    let dir = Directory::new();
    dir.file("run.log", "{\"n\":1}\n{\"n\":2}\n");
    let mut options = dir.options();
    options.max_events = 1;
    let mut collector = FileCollector::new(options.clone(), FileState::default()).unwrap();
    let first = collector.scan().unwrap();
    collector.commit(&first).unwrap();
    options.from = StartPosition::End;
    let restarted = FileCollector::new(options, collector.state().clone()).unwrap();
    let second = restarted.scan().unwrap();
    assert_eq!(second.records[0].payload["n"], 2);
    assert_eq!(second.records[0].source_sequence, 2);
}

#[test]
fn overlapping_includes_and_excludes_apply_on_each_discovery() {
    let dir = Directory::new();
    dir.file("a.log", "{}\n");
    dir.file("skip.log", "{}\n");
    let mut options = dir.options();
    options.includes.push(format!("{}/a.*", dir.0.display()));
    options.excludes.push(format!("{}/skip.*", dir.0.display()));
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    assert_eq!(batch.records.len(), 1);
    collector.commit(&batch).unwrap();
    dir.file("b.log", "{}\n");
    assert_eq!(collector.scan().unwrap().records.len(), 1);
}

#[test]
fn truncation_and_regrowth_start_new_generations() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{\"n\":1000}\n");
    let mut collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let original = collector.scan().unwrap();
    collector.commit(&original).unwrap();
    fs::write(&path, "{}\n").unwrap();
    let truncated = collector.scan().unwrap();
    assert_eq!(truncated.records.len(), 1);
    assert_ne!(truncated.records[0].event_id, original.records[0].event_id);
    assert_eq!(truncated.records[0].source_sequence, 1);
    collector.commit(&truncated).unwrap();
    fs::write(&path, "{\"regrown\":true}\n").unwrap();
    let regrown = collector.scan().unwrap();
    assert_eq!(regrown.records.len(), 1);
    assert_ne!(regrown.records[0].event_id, truncated.records[0].event_id);
}

#[test]
fn rename_rotation_reads_old_remainder_and_new_file() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{\"n\":1}\n");
    let mut collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let original = collector.scan().unwrap();
    collector.commit(&original).unwrap();
    append(&path, "{\"n\":2}\n");
    fs::rename(&path, dir.0.join("rotated.log")).unwrap();
    dir.file("run.log", "{\"n\":3}\n");
    let batch = collector.scan().unwrap();
    assert_eq!(batch.records.len(), 2);
    let old = batch
        .records
        .iter()
        .find(|record| record.payload["n"] == 2)
        .unwrap();
    assert_eq!(old.source_generation, original.records[0].source_generation);
    assert_eq!(old.source_sequence, 2);
    let new = batch
        .records
        .iter()
        .find(|record| record.payload["n"] == 3)
        .unwrap();
    assert_ne!(new.source_generation, original.records[0].source_generation);
    assert_eq!(new.source_sequence, 1);
}

#[test]
fn malformed_json_and_nonobject_records_require_explicit_skip() {
    let dir = Directory::new();
    dir.file("run.log", "not json\n[]\n{\"ok\":true}\n");
    let options = dir.options();
    let strict = FileCollector::new(options.clone(), FileState::default()).unwrap();
    assert!(
        strict
            .scan()
            .err()
            .unwrap()
            .to_string()
            .contains("byte 0: invalid JSONL")
    );
    let mut options = options;
    options.invalid_json = InvalidJsonPolicy::Skip;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    assert_eq!(batch.skipped_lines, 2);
    assert_eq!(batch.records.len(), 1);
    assert_eq!(batch.records[0].source_sequence, 3);
    collector.commit(&batch).unwrap();
    assert!(collector.scan().unwrap().records.is_empty());
}

#[test]
fn batch_count_and_payload_bytes_bound_each_scan() {
    let dir = Directory::new();
    dir.file("run.log", "{}\n{}\n{}\n");
    let mut options = dir.options();
    options.max_batch_bytes = 4;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let first = collector.scan().unwrap();
    assert_eq!(first.records.len(), 2);
    assert_eq!(first.payload_bytes, 4);
    collector.commit(&first).unwrap();
    assert_eq!(collector.scan().unwrap().records.len(), 1);
    let mut options = dir.options();
    options.max_events = 1;
    let collector = FileCollector::new(options, FileState::default()).unwrap();
    assert_eq!(collector.scan().unwrap().records.len(), 1);
}

#[test]
fn oversized_lines_and_records_fail_without_advancing_state() {
    let dir = Directory::new();
    dir.file("run.log", "{\"value\":123456}\n");
    let mut options = dir.options();
    options.max_line_bytes = 5;
    let collector = FileCollector::new(options, FileState::default()).unwrap();
    assert!(
        collector
            .scan()
            .err()
            .unwrap()
            .to_string()
            .contains("line limit")
    );
    assert_eq!(collector.state(), &FileState::default());
    let mut options = dir.options();
    options.max_batch_bytes = 5;
    let collector = FileCollector::new(options, FileState::default()).unwrap();
    assert!(
        collector
            .scan()
            .err()
            .unwrap()
            .to_string()
            .contains("batch limit")
    );
}

#[test]
fn text_preserves_empty_lines_and_rejects_invalid_utf8() {
    let dir = Directory::new();
    let path = dir.file("run.log", "first\n\nthird\r\n");
    let mut options = dir.options();
    options.format = FileFormat::Text;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    assert_eq!(batch.records.len(), 3);
    assert_eq!(batch.records[1].payload["message"], "");
    assert_eq!(batch.records[2].payload["message"], "third");
    collector.commit(&batch).unwrap();
    let mut file = fs::OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(&[0xff, b'\n']).unwrap();
    assert!(
        collector
            .scan()
            .err()
            .unwrap()
            .to_string()
            .contains("invalid UTF-8")
    );
}

#[cfg(unix)]
#[test]
fn canonical_non_utf8_paths_are_rejected_without_checkpoint_changes() {
    use std::os::unix::ffi::OsStringExt;
    use std::os::unix::fs::symlink;

    let dir = Directory::new();
    let invalid_path = dir
        .0
        .join(std::ffi::OsString::from_vec(b"invalid-\xff.log".to_vec()));
    fs::write(&invalid_path, "{}\n").unwrap();
    symlink(&invalid_path, dir.0.join("alias.log")).unwrap();
    let collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let error = collector.scan().err().unwrap();
    assert!(error.to_string().contains("requires UTF-8 source paths"));
    assert_eq!(collector.state(), &FileState::default());
}

#[test]
fn missing_checkpoints_expire_after_two_committed_scans() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{}\n");
    // Keep this exact inode alive so the regression exercises same-path and
    // same-inode reuse after pruning, without depending on OS allocation.
    let held = dir.0.join("held");
    fs::hard_link(&path, &held).unwrap();
    let mut collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let original = collector.scan().unwrap();
    collector.commit(&original).unwrap();
    fs::remove_file(&path).unwrap();
    let first_absence = collector.scan().unwrap();
    assert_eq!(first_absence.next_state().files.len(), 1);
    assert_eq!(
        first_absence
            .next_state()
            .files
            .values()
            .next()
            .unwrap()
            .missing_scans,
        1
    );
    collector.commit(&first_absence).unwrap();
    let second_absence = collector.scan().unwrap();
    assert!(second_absence.next_state().files.is_empty());
    collector.commit(&second_absence).unwrap();
    fs::hard_link(&held, &path).unwrap();
    let reappeared = collector.scan().unwrap();
    assert_eq!(reappeared.records.len(), 1);
    assert_eq!(reappeared.records[0].source_sequence, 1);
    assert_ne!(reappeared.records[0].event_id, original.records[0].event_id);
}

#[test]
fn batch_limits_do_not_age_present_but_unprocessed_checkpoints() {
    let dir = Directory::new();
    for name in ["a.log", "b.log", "c.log"] {
        dir.file(name, "{}\n{}\n{}\n{}\n");
    }
    let mut options = dir.options();
    options.max_events = 1;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    for _ in 0..3 {
        let batch = collector.scan().unwrap();
        assert_eq!(batch.records.len(), 1);
        assert_eq!(batch.next_state().files.len(), 3);
        assert!(
            batch
                .next_state()
                .files
                .values()
                .all(|checkpoint| checkpoint.missing_scans == 0)
        );
        collector.commit(&batch).unwrap();
    }
}

#[test]
fn truncation_during_checkpoint_comparison_is_a_mismatch() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{}\n");
    let mut collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    collector.commit(&batch).unwrap();
    let saved = collector.state().files.values().next().unwrap();
    let mut file = File::open(&path).unwrap();
    fs::write(&path, "").unwrap();
    // A previously read metadata length may already be stale by read_exact.
    assert!(!checkpoint_matches(&mut file, saved, saved.offset).unwrap());
}

#[test]
fn busy_first_source_cannot_starve_a_second_source() {
    let dir = Directory::new();
    let busy = dir.file("a.log", "{\"source\":\"a\"}\n{\"source\":\"a\"}\n");
    dir.file("b.log", "{\"source\":\"b\"}\n");
    let mut options = dir.options();
    options.max_events = 1;
    let mut collector = FileCollector::new(options.clone(), FileState::default()).unwrap();
    let first = collector.scan().unwrap();
    assert_eq!(first.records[0].payload["source"], "a");
    append(&busy, "{\"source\":\"a\"}\n");
    assert_eq!(
        collector.scan().unwrap().records[0].event_id,
        first.records[0].event_id
    );
    collector.commit(&first).unwrap();
    let state = serde_json::from_slice(&serde_json::to_vec(collector.state()).unwrap()).unwrap();
    let restarted = FileCollector::new(options, state).unwrap();
    let second = restarted.scan().unwrap();
    assert_eq!(second.records.len(), 1);
    assert_eq!(second.records[0].payload["source"], "b");
    assert!(second.records[0].source_path.ends_with("b.log"));
}

#[test]
fn scan_errors_distinguish_source_races_from_permanent_failures() {
    let dir = Directory::new();
    let path = dir.file("run.log", "not json\n");
    let collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let malformed = collector.scan().unwrap_err();
    assert!(!malformed.retryable());
    assert!(malformed.to_string().contains("invalid JSONL"));
    assert!(
        !ScanError::io(
            &path,
            std::io::Error::from(std::io::ErrorKind::PermissionDenied)
        )
        .retryable()
    );
    assert!(
        ScanError::io(
            &path,
            std::io::Error::from(std::io::ErrorKind::UnexpectedEof)
        )
        .retryable()
    );

    fs::write(&path, "{}\n").unwrap();
    let batch = collector.scan().unwrap();
    let before = batch.next_state().files.values().next().unwrap().clone();
    let proof = ReadProof::new(&before);
    let mut after = before.clone();
    let mut file = File::open(&path).unwrap();
    fs::write(&path, "{ }\n").unwrap();
    let changed = validate_read(&mut file, &before, &mut after, &proof).unwrap_err();
    let changed = ScanError::io(&path, changed);
    assert!(changed.retryable());
    assert!(
        changed
            .to_string()
            .contains("source changed during collection")
    );
    assert_eq!(collector.state(), &FileState::default());
}

#[test]
fn rewritten_consumed_bytes_fail_validation_even_when_old_anchor_matches() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{\"n\":1}\n{\"n\":2}\n");
    let mut options = dir.options();
    options.max_events = 1;
    let mut collector = FileCollector::new(options, FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    collector.commit(&batch).unwrap();
    let before = collector.state().files.values().next().unwrap().clone();
    let mut after = before.clone();
    let mut proof = ReadProof::new(&before);
    let consumed = b"{\"n\":2}\n";
    proof.consume(consumed);
    after.offset += consumed.len() as u64;
    after.sequence += 1;
    let mut file = File::open(&path).unwrap();
    fs::write(&path, "{\"n\":1}\n{\"n\":9}\n").unwrap();
    assert!(checkpoint_matches(&mut file, &before, after.offset).unwrap());
    let error = validate_read(&mut file, &before, &mut after, &proof).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("source changed during collection")
    );
    assert_eq!(collector.state().files.values().next().unwrap(), &before);
}

#[test]
fn prior_anchor_change_fails_validation_before_refresh() {
    let dir = Directory::new();
    let path = dir.file("run.log", "{\"n\":1}\n");
    let mut collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    collector.commit(&batch).unwrap();
    let before = collector.state().files.values().next().unwrap().clone();
    let mut after = before.clone();
    let proof = ReadProof::new(&before);
    let mut file = File::open(&path).unwrap();
    fs::write(&path, "{\"n\":9}\n").unwrap();
    assert!(validate_read(&mut file, &before, &mut after, &proof).is_err());
    assert_eq!(after.anchor, before.anchor);
}

#[test]
fn read_proof_tracks_bounded_prefix_and_tail_across_chunk_sizes() {
    let dir = Directory::new();
    dir.file("run.log", "");
    let collector = FileCollector::new(dir.options(), FileState::default()).unwrap();
    let batch = collector.scan().unwrap();
    let checkpoint = batch.next_state().files.values().next().unwrap();
    let mut proof = ReadProof::new(checkpoint);
    let bytes = (0..=255).collect::<Vec<u8>>();
    for chunk in bytes.chunks(37) {
        proof.consume(chunk);
        assert!(proof.prefix.len() <= 64);
        assert!(proof.anchor.len() <= 64);
    }
    assert_eq!(proof.prefix, bytes[..64]);
    assert_eq!(proof.anchor, bytes[192..]);
    proof.consume(&bytes);
    assert_eq!(proof.prefix, bytes[..64]);
    assert_eq!(proof.anchor, bytes[192..]);
}

#[cfg(windows)]
#[test]
fn windows_handle_identity_distinguishes_files_and_survives_rename() {
    let dir = Directory::new();
    let first = dir.file("first.log", "{}\n");
    let second = dir.file("second.log", "{}\n");
    let first_file = File::open(&first).unwrap();
    let second_file = File::open(&second).unwrap();
    let first_id = file_identity(&first_file, &first_file.metadata().unwrap()).unwrap();
    let second_id = file_identity(&second_file, &second_file.metadata().unwrap()).unwrap();
    assert_ne!(first_id, second_id);
    let renamed = dir.0.join("renamed.log");
    fs::rename(first, &renamed).unwrap();
    let renamed_file = File::open(renamed).unwrap();
    assert_eq!(
        first_id,
        file_identity(&renamed_file, &renamed_file.metadata().unwrap()).unwrap()
    );
}
