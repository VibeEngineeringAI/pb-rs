//! Constant-space ownership observations in the separate diagnostics build.
//! These are owner-reported sizes, not unique allocations or process RSS.
use serde::Serialize;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};

static ENABLED: AtomicBool = AtomicBool::new(false);

macro_rules! counters {
    ($($field:ident),+ $(,)?) => {
        $(static $field: AtomicU64 = AtomicU64::new(0);)+
        fn clear() { $($field.store(0, Relaxed);)+ }
    };
}
counters!(
    SUBSCRIPTIONS,
    MESSAGES,
    BATCHES,
    ROWS,
    ENCODED_PEAK,
    DICTIONARY_PEAK,
    BATCH_PEAK,
    STAGING_LEN_PEAK,
    STAGING_CAPACITY_PEAK,
    STAGING_CAPACITY_LAST,
    SCRATCH_CAPACITY_PEAK,
    SCRATCH_CAPACITY_LAST,
);

#[derive(Default)]
struct Outstanding {
    current: AtomicU64,
    peak: AtomicU64,
}

static OUTSTANDING: Outstanding = Outstanding {
    current: AtomicU64::new(0),
    peak: AtomicU64::new(0),
};

impl Outstanding {
    fn begin(&self) -> Pending<'_> {
        let current = self.current.fetch_add(1, Relaxed) + 1;
        self.peak.fetch_max(current, Relaxed);
        Pending(self)
    }
}

struct Pending<'a>(&'a Outstanding);

impl Drop for Pending<'_> {
    fn drop(&mut self) {
        self.0.current.fetch_sub(1, Relaxed);
    }
}

/// Called before the CLI starts any worker. Disabled diagnostics controls do
/// not collect tail data, just as they do not collect the existing stage data.
pub(crate) fn reset(enabled: bool) {
    ENABLED.store(false, Relaxed);
    clear();
    OUTSTANDING.current.store(0, Relaxed);
    OUTSTANDING.peak.store(0, Relaxed);
    ENABLED.store(enabled, Relaxed);
}

pub(super) fn subscription() {
    if ENABLED.load(Relaxed) {
        SUBSCRIPTIONS.fetch_add(1, Relaxed);
    }
}

pub(super) fn message(header: usize, body: usize, metadata: usize) {
    if ENABLED.load(Relaxed) {
        MESSAGES.fetch_add(1, Relaxed);
        ENCODED_PEAK.fetch_max(
            header.saturating_add(body).saturating_add(metadata) as u64,
            Relaxed,
        );
    }
}

pub(super) fn dictionary_bytes(bytes: usize) {
    if ENABLED.load(Relaxed) {
        DICTIONARY_PEAK.fetch_max(bytes as u64, Relaxed);
    }
}

pub(super) fn batch(rows: usize, buffer_bytes: usize) {
    if ENABLED.load(Relaxed) {
        BATCHES.fetch_add(1, Relaxed);
        ROWS.fetch_add(rows as u64, Relaxed);
        BATCH_PEAK.fetch_max(buffer_bytes as u64, Relaxed);
    }
}

pub(super) fn staging(length: usize, capacity: usize) {
    if ENABLED.load(Relaxed) {
        STAGING_LEN_PEAK.fetch_max(length as u64, Relaxed);
        STAGING_CAPACITY_PEAK.fetch_max(capacity as u64, Relaxed);
        STAGING_CAPACITY_LAST.store(capacity as u64, Relaxed);
    }
}

/// Count an accepted schema/message handoff until its acknowledgment is
/// consumed (or the receive future is dropped). Overlapping handoffs add up.
pub(super) fn inflight() -> Option<impl Drop> {
    ENABLED.load(Relaxed).then(|| OUTSTANDING.begin())
}

/// Handoffs currently counted, for the session's guard-lifetime test.
#[cfg(test)]
pub(super) fn current_inflight() -> u64 {
    OUTSTANDING.current.load(Relaxed)
}

pub(super) fn renderer_scratch(bytes: usize) {
    if ENABLED.load(Relaxed) {
        SCRATCH_CAPACITY_PEAK.fetch_max(bytes as u64, Relaxed);
        SCRATCH_CAPACITY_LAST.store(bytes as u64, Relaxed);
    }
}

#[derive(Serialize)]
pub(crate) struct Snapshot {
    subscriptions_started: u64,
    flight_messages_received: u64,
    batches_decoded: u64,
    rows_decoded: u64,
    max_flight_payload_bytes: u64,
    max_dictionary_buffer_bytes: u64,
    max_batch_buffer_bytes: u64,
    max_staging_length_bytes: u64,
    max_staging_capacity_bytes: u64,
    last_staging_capacity_bytes: u64,
    max_application_messages_inflight: u64,
    max_renderer_scratch_capacity_bytes: u64,
    last_renderer_scratch_capacity_bytes: u64,
    semantics: &'static str,
}

/// Read after all tail workers have joined. A signal-killed process has no
/// completed sidecar, and the collector must not pretend otherwise.
pub(crate) fn finish() -> Option<Snapshot> {
    let enabled = ENABLED.swap(false, Relaxed);
    if !enabled || SUBSCRIPTIONS.load(Relaxed) == 0 {
        return None;
    }
    Some(Snapshot {
        subscriptions_started: SUBSCRIPTIONS.load(Relaxed),
        flight_messages_received: MESSAGES.load(Relaxed),
        batches_decoded: BATCHES.load(Relaxed),
        rows_decoded: ROWS.load(Relaxed),
        max_flight_payload_bytes: ENCODED_PEAK.load(Relaxed),
        max_dictionary_buffer_bytes: DICTIONARY_PEAK.load(Relaxed),
        max_batch_buffer_bytes: BATCH_PEAK.load(Relaxed),
        max_staging_length_bytes: STAGING_LEN_PEAK.load(Relaxed),
        max_staging_capacity_bytes: STAGING_CAPACITY_PEAK.load(Relaxed),
        last_staging_capacity_bytes: STAGING_CAPACITY_LAST.load(Relaxed),
        max_application_messages_inflight: OUTSTANDING.peak.load(Relaxed),
        max_renderer_scratch_capacity_bytes: SCRATCH_CAPACITY_PEAK.load(Relaxed),
        last_renderer_scratch_capacity_bytes: SCRATCH_CAPACITY_LAST.load(Relaxed),
        semantics: "owner-reported maxima, not simultaneous totals, unique heap or RSS; shared Arrow buffers may be counted by several owners; Flight payload excludes protobuf/framing and transport read-ahead; rows count decoded rows before rendering truncation; capacities are Rust Vec capacities",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outstanding_guards_are_counted_and_release_on_scope_exit() {
        let counter = Outstanding::default();
        let barrier = std::sync::Barrier::new(2);
        std::thread::scope(|scope| {
            scope.spawn(|| {
                let _pending = counter.begin();
                barrier.wait();
                barrier.wait();
            });
            let pending = counter.begin();
            barrier.wait();
            assert_eq!(counter.current.load(Relaxed), 2);
            assert_eq!(counter.peak.load(Relaxed), 2);
            drop(pending);
            barrier.wait();
        });
        assert_eq!(counter.current.load(Relaxed), 0);
        assert_eq!(counter.peak.load(Relaxed), 2);
    }
}
