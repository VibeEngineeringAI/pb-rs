//! Opt-in diagnostics for a separately built CLI. No payloads or invocation
//! arguments enter the report. Stage totals are exclusive on each thread;
//! thread spans are inclusive and must never be added as process wall time.
use serde::Serialize;
use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::{Cell, RefCell},
    fs::{self, OpenOptions},
    io::{self, Write},
    path::PathBuf,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Instant,
};

const STAGE_COUNT: usize = 11;
const MAX_DEPTH: usize = 32;
static ACTIVE: AtomicBool = AtomicBool::new(false);
static ALLOCATIONS: AtomicBool = AtomicBool::new(false);
static SAMPLE_EVERY: AtomicU64 = AtomicU64::new(1);
static REPORTS: OnceLock<Mutex<Vec<ThreadReport>>> = OnceLock::new();

#[derive(Clone, Copy)]
#[repr(usize)]
pub enum Stage {
    ScanFraming,
    OrderedDecode,
    ValueConversion,
    Serialization,
    IndentationCopy,
    OutputWrite,
    OutputFlush,
    Drop,
    BodyWait,
    RequestAndHeaders,
    RuntimePump,
}
const STAGES: [Stage; STAGE_COUNT] = [
    Stage::ScanFraming,
    Stage::OrderedDecode,
    Stage::ValueConversion,
    Stage::Serialization,
    Stage::IndentationCopy,
    Stage::OutputWrite,
    Stage::OutputFlush,
    Stage::Drop,
    Stage::BodyWait,
    Stage::RequestAndHeaders,
    Stage::RuntimePump,
];
impl Stage {
    fn name(self) -> &'static str {
        match self {
            Self::ScanFraming => "scan_framing",
            Self::OrderedDecode => "ordered_decode",
            Self::ValueConversion => "value_conversion",
            Self::Serialization => "serialization",
            Self::IndentationCopy => "indentation_copy",
            Self::OutputWrite => "output_write",
            Self::OutputFlush => "output_flush",
            Self::Drop => "drop",
            Self::BodyWait => "body_wait",
            Self::RequestAndHeaders => "request_and_headers",
            Self::RuntimePump => "runtime_pump",
        }
    }
}

#[derive(Clone, Copy, Default, Serialize, Debug, PartialEq)]
pub struct AllocationCounts {
    alloc_calls: u64,
    alloc_requested_bytes: u64,
    realloc_calls: u64,
    realloc_requested_bytes: u64,
    dealloc_calls: u64,
    dealloc_layout_bytes: u64,
}
impl AllocationCounts {
    fn minus(self, other: Self) -> Self {
        Self {
            alloc_calls: self.alloc_calls.saturating_sub(other.alloc_calls),
            alloc_requested_bytes: self
                .alloc_requested_bytes
                .saturating_sub(other.alloc_requested_bytes),
            realloc_calls: self.realloc_calls.saturating_sub(other.realloc_calls),
            realloc_requested_bytes: self
                .realloc_requested_bytes
                .saturating_sub(other.realloc_requested_bytes),
            dealloc_calls: self.dealloc_calls.saturating_sub(other.dealloc_calls),
            dealloc_layout_bytes: self
                .dealloc_layout_bytes
                .saturating_sub(other.dealloc_layout_bytes),
        }
    }
    fn add(&mut self, other: Self) {
        self.alloc_calls += other.alloc_calls;
        self.alloc_requested_bytes += other.alloc_requested_bytes;
        self.realloc_calls += other.realloc_calls;
        self.realloc_requested_bytes += other.realloc_requested_bytes;
        self.dealloc_calls += other.dealloc_calls;
        self.dealloc_layout_bytes += other.dealloc_layout_bytes;
    }
}

// The allocator only touches const-initialized, allocation-free Cell TLS. It
// never borrows ThreadState, allocates, takes a mutex, formats, or panics. TLS
// teardown safely disables counting via try_with. Layout sizes are requests,
// not usable sizes, retained heap, peak memory, or RSS; failed calls count too.
thread_local! {
    static TRACK_ALLOCATIONS: Cell<bool> = const { Cell::new(false) };
    static ALLOCATION_COUNTS: Cell<AllocationCounts> = const { Cell::new(AllocationCounts {
        alloc_calls: 0, alloc_requested_bytes: 0, realloc_calls: 0,
        realloc_requested_bytes: 0, dealloc_calls: 0, dealloc_layout_bytes: 0,
    }) };
    static STATE: RefCell<ThreadState> = const { RefCell::new(ThreadState::new()) };
}

fn allocation_event(update: impl FnOnce(&mut AllocationCounts)) {
    if !ALLOCATIONS.load(Ordering::Relaxed) {
        return;
    }
    let _ = TRACK_ALLOCATIONS.try_with(|enabled| {
        if enabled.get() {
            let _ = ALLOCATION_COUNTS.try_with(|counts| {
                let mut value = counts.get();
                update(&mut value);
                counts.set(value);
            });
        }
    });
}

/// Install only in the diagnostics binary. Forward exactly the System allocator
/// contract; counting neither examines memory nor changes allocation results.
pub struct CountingAllocator;
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        allocation_event(|c| {
            c.alloc_calls = c.alloc_calls.saturating_add(1);
            c.alloc_requested_bytes = c.alloc_requested_bytes.saturating_add(layout.size() as u64);
        });
        unsafe { System.alloc(layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        allocation_event(|c| {
            c.alloc_calls = c.alloc_calls.saturating_add(1);
            c.alloc_requested_bytes = c.alloc_requested_bytes.saturating_add(layout.size() as u64);
        });
        unsafe { System.alloc_zeroed(layout) }
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        allocation_event(|c| {
            c.dealloc_calls = c.dealloc_calls.saturating_add(1);
            c.dealloc_layout_bytes = c.dealloc_layout_bytes.saturating_add(layout.size() as u64);
        });
        unsafe { System.dealloc(pointer, layout) }
    }
    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        allocation_event(|c| {
            c.realloc_calls = c.realloc_calls.saturating_add(1);
            c.realloc_requested_bytes = c.realloc_requested_bytes.saturating_add(size as u64);
        });
        unsafe { System.realloc(pointer, layout, size) }
    }
}

struct PauseAllocations(bool);
impl PauseAllocations {
    fn new() -> Self {
        Self(TRACK_ALLOCATIONS.with(|value| value.replace(false)))
    }
}
impl Drop for PauseAllocations {
    fn drop(&mut self) {
        TRACK_ALLOCATIONS.with(|value| value.set(self.0));
    }
}
fn allocations() -> AllocationCounts {
    ALLOCATION_COUNTS.with(Cell::get)
}

#[derive(Clone, Copy, Default, Serialize)]
struct Metric {
    calls: u64,
    timed_calls: u64,
    wall_ns: u64,
    cpu_ns: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    allocations: Option<AllocationCounts>,
}
#[derive(Clone, Copy, Default)]
struct Children {
    wall_ns: u64,
    cpu_ns: u64,
    allocations: AllocationCounts,
}
const EMPTY_METRIC: Metric = Metric {
    calls: 0,
    timed_calls: 0,
    wall_ns: 0,
    cpu_ns: 0,
    allocations: None,
};
const EMPTY_CHILDREN: Children = Children {
    wall_ns: 0,
    cpu_ns: 0,
    allocations: AllocationCounts {
        alloc_calls: 0,
        alloc_requested_bytes: 0,
        realloc_calls: 0,
        realloc_requested_bytes: 0,
        dealloc_calls: 0,
        dealloc_layout_bytes: 0,
    },
};
#[derive(Default, Serialize)]
struct Counters {
    input_consumed_bytes: u64,
    elements: u64,
    element_input_bytes: u64,
    serialized_bytes: u64,
    indented_bytes: u64,
    write_calls: u64,
    write_attempted_bytes: u64,
    write_completed_bytes: u64,
    flush_calls: u64,
}
struct ThreadState {
    registered: bool,
    sample_element: bool,
    element_index: u64,
    depth: usize,
    children: [Children; MAX_DEPTH],
    metrics: [Metric; STAGE_COUNT],
    counters: Counters,
}
impl ThreadState {
    const fn new() -> Self {
        Self {
            registered: false,
            sample_element: true,
            element_index: 0,
            depth: 0,
            children: [EMPTY_CHILDREN; MAX_DEPTH],
            metrics: [EMPTY_METRIC; STAGE_COUNT],
            counters: Counters {
                input_consumed_bytes: 0,
                elements: 0,
                element_input_bytes: 0,
                serialized_bytes: 0,
                indented_bytes: 0,
                write_calls: 0,
                write_attempted_bytes: 0,
                write_completed_bytes: 0,
                flush_calls: 0,
            },
        }
    }
}

fn ns(duration: std::time::Duration) -> u64 {
    duration.as_nanos().min(u64::MAX as u128) as u64
}
#[cfg(unix)]
fn cpu_now() -> Option<u64> {
    let mut value = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // This is the executing thread's CPU clock. Waiting for input/output does
    // not consume CPU; it still appears in wall time of its waiting stage.
    if unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut value) } == 0 {
        Some(
            (value.tv_sec as u64)
                .saturating_mul(1_000_000_000)
                .saturating_add(value.tv_nsec as u64),
        )
    } else {
        None
    }
}
#[cfg(not(unix))]
fn cpu_now() -> Option<u64> {
    None
}
fn cpu_elapsed(start: Option<u64>, end: Option<u64>) -> u64 {
    start
        .zip(end)
        .map_or(0, |(start, end)| end.saturating_sub(start))
}
/// Whole-loop clock for isolated diagnostic tests; never called per byte.
pub fn current_thread_cpu_ns() -> Option<u64> {
    cpu_now()
}

pub struct Timer {
    measured: Option<(Instant, Option<u64>, AllocationCounts, usize)>,
    stage: Stage,
    thread_bound: std::marker::PhantomData<std::rc::Rc<()>>,
}
impl Timer {
    pub fn begin(stage: Stage) -> Self {
        let measured = if ACTIVE.load(Ordering::Relaxed) {
            let pause = PauseAllocations::new();
            let depth = STATE.with(|state| {
                let mut state = state.borrow_mut();
                if !state.registered || state.depth == MAX_DEPTH {
                    return None;
                }
                state.metrics[stage as usize].calls += 1;
                if !state.sample_element {
                    return None;
                }
                let depth = state.depth;
                state.children[depth] = EMPTY_CHILDREN;
                state.depth += 1;
                Some(depth)
            });
            let allocation_start = allocations();
            drop(pause);
            depth.map(|depth| (Instant::now(), cpu_now(), allocation_start, depth))
        } else {
            None
        };
        Self {
            measured,
            stage,
            thread_bound: std::marker::PhantomData,
        }
    }
}
impl Drop for Timer {
    fn drop(&mut self) {
        let Some((start, cpu_start, allocation_start, depth)) = self.measured else {
            return;
        };
        let cpu_ns = cpu_elapsed(cpu_start, cpu_now());
        let wall_ns = ns(start.elapsed());
        let _pause = PauseAllocations::new();
        let allocation_delta = allocations().minus(allocation_start);
        STATE.with(|state| {
            let mut state = state.borrow_mut();
            debug_assert_eq!(
                state.depth,
                depth + 1,
                "diagnostic stage guards must end in stack order"
            );
            state.depth = depth;
            let children = state.children[depth];
            let metric = &mut state.metrics[self.stage as usize];
            metric.timed_calls += 1;
            metric.wall_ns += wall_ns.saturating_sub(children.wall_ns);
            metric.cpu_ns += cpu_ns.saturating_sub(children.cpu_ns);
            if ALLOCATIONS.load(Ordering::Relaxed) {
                metric
                    .allocations
                    .get_or_insert_default()
                    .add(allocation_delta.minus(children.allocations));
            }
            if depth > 0 {
                let parent = &mut state.children[depth - 1];
                parent.wall_ns += wall_ns;
                parent.cpu_ns += cpu_ns;
                parent.allocations.add(allocation_delta);
            }
        });
    }
}

pub fn measure<T>(stage: Stage, operation: impl FnOnce() -> T) -> T {
    let _timer = Timer::begin(stage);
    operation()
}
/// Sample a whole element together so nested wait/write/flush stages preserve
/// exclusive boundaries. Size/call counters still cover every element. Sampled
/// times are actual observations, never extrapolated totals. Initial framing
/// and main-thread stages are always measured. Allocation mode forces every=1
/// so requested bytes remain attributable to all stages.
pub struct ElementSample(Option<bool>, std::marker::PhantomData<std::rc::Rc<()>>);
impl ElementSample {
    pub fn begin() -> Self {
        let previous = if ACTIVE.load(Ordering::Relaxed) {
            STATE.with(|state| {
                let mut state = state.borrow_mut();
                let previous = state.sample_element;
                state.sample_element =
                    state.element_index % SAMPLE_EVERY.load(Ordering::Relaxed) == 0;
                state.element_index += 1;
                Some(previous)
            })
        } else {
            None
        };
        Self(previous, std::marker::PhantomData)
    }
}
impl Drop for ElementSample {
    fn drop(&mut self) {
        if let Some(previous) = self.0 {
            STATE.with(|state| state.borrow_mut().sample_element = previous);
        }
    }
}
fn count(update: impl FnOnce(&mut Counters)) {
    if ACTIVE.load(Ordering::Relaxed) {
        let _pause = PauseAllocations::new();
        STATE.with(|state| {
            let mut state = state.borrow_mut();
            if state.registered {
                update(&mut state.counters);
            }
        });
    }
}
pub fn input_consumed(bytes: usize) {
    count(|c| c.input_consumed_bytes += bytes as u64);
}
pub fn element(bytes: usize) {
    count(|c| {
        c.elements += 1;
        c.element_input_bytes += bytes as u64;
    });
}
pub fn serialized(bytes: usize) {
    count(|c| c.serialized_bytes += bytes as u64);
}
pub fn indented(bytes: usize) {
    count(|c| c.indented_bytes += bytes as u64);
}

#[derive(Serialize)]
struct StageReport {
    name: &'static str,
    #[serde(flatten)]
    metric: Metric,
}
#[derive(Serialize)]
struct ThreadReport {
    thread_id: String,
    role: &'static str,
    span_wall_ns: u64,
    span_cpu_ns: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    allocations: Option<AllocationCounts>,
    stages: Vec<StageReport>,
    counters: Counters,
}
/// One inclusive lifetime per registered thread. Create before its measured
/// objects so normal destructors finish before this guard collects the report.
pub struct ThreadSpan {
    measured: Option<(Instant, Option<u64>, AllocationCounts)>,
    role: &'static str,
    thread_bound: std::marker::PhantomData<std::rc::Rc<()>>,
}
impl ThreadSpan {
    pub fn begin(role: &'static str) -> Self {
        let measured = if ACTIVE.load(Ordering::Relaxed) {
            let pause = PauseAllocations::new();
            STATE.with(|state| {
                let mut state = state.borrow_mut();
                *state = ThreadState::new();
                state.registered = true;
            });
            let start = allocations();
            drop(pause);
            TRACK_ALLOCATIONS.with(|v| v.set(ALLOCATIONS.load(Ordering::Relaxed)));
            Some((Instant::now(), cpu_now(), start))
        } else {
            None
        };
        Self {
            measured,
            role,
            thread_bound: std::marker::PhantomData,
        }
    }
}
impl Drop for ThreadSpan {
    fn drop(&mut self) {
        let Some((start, cpu_start, allocation_start)) = self.measured else {
            return;
        };
        let cpu_end = cpu_now();
        let wall_ns = ns(start.elapsed());
        TRACK_ALLOCATIONS.with(|v| v.set(false));
        let total_allocations = allocations().minus(allocation_start);
        let state =
            STATE.with(|state| std::mem::replace(&mut *state.borrow_mut(), ThreadState::new()));
        let report = ThreadReport {
            thread_id: format!("{:?}", std::thread::current().id()),
            role: self.role,
            span_wall_ns: wall_ns,
            span_cpu_ns: cpu_start.zip(cpu_end).map(|(s, e)| e.saturating_sub(s)),
            allocations: ALLOCATIONS
                .load(Ordering::Relaxed)
                .then_some(total_allocations),
            stages: STAGES
                .iter()
                .map(|stage| StageReport {
                    name: stage.name(),
                    metric: state.metrics[*stage as usize],
                })
                .collect(),
            counters: state.counters,
        };
        if let Some(reports) = REPORTS.get()
            && let Ok(mut reports) = reports.lock()
        {
            reports.push(report);
        }
    }
}

/// Best effort sidecar writer. Missing/invalid destinations never alter CLI
/// stdout, stderr, exit status, or normal output-error behavior.
pub struct Session {
    directory: Option<PathBuf>,
    main: Option<ThreadSpan>,
}
impl Session {
    pub fn begin() -> Self {
        let directory = std::env::var_os("PB_DIAGNOSTICS_DIR")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from);
        if directory.is_some() {
            let _ = REPORTS.get_or_init(|| Mutex::new(Vec::new()));
            ALLOCATIONS.store(
                std::env::var_os("PB_DIAGNOSTICS_ALLOCATIONS").is_some_and(|v| v == "1"),
                Ordering::Relaxed,
            );
            let requested = std::env::var("PB_DIAGNOSTICS_SAMPLE_EVERY")
                .ok()
                .and_then(|v| v.parse::<u64>().ok())
                .filter(|v| *v > 0)
                .unwrap_or(1);
            SAMPLE_EVERY.store(
                if ALLOCATIONS.load(Ordering::Relaxed) {
                    1
                } else {
                    requested
                },
                Ordering::Relaxed,
            );
            ACTIVE.store(true, Ordering::Relaxed);
        }
        let main = directory.as_ref().map(|_| ThreadSpan::begin("main"));
        Self { directory, main }
    }
    pub fn finish(mut self, exit_code: i32) {
        drop(self.main.take());
        ACTIVE.store(false, Ordering::Relaxed);
        ALLOCATIONS.store(false, Ordering::Relaxed);
        let Some(directory) = self.directory else {
            return;
        };
        let Some(reports) = REPORTS.get() else {
            return;
        };
        let Ok(reports) = reports.lock() else {
            return;
        };
        #[derive(Serialize)]
        struct Measurement {
            elapsed_wall_ns: u64,
            thread_cpu_clock: &'static str,
            stage_times: &'static str,
            thread_spans: &'static str,
            sidecar_io: &'static str,
            allocation_semantics: &'static str,
            writer_calls: &'static str,
            input_bytes: &'static str,
            serialization: &'static str,
            sample_every: u64,
            sampling: &'static str,
            drop: &'static str,
            unregistered_threads: &'static str,
        }
        #[derive(Serialize)]
        struct Report<'a> {
            schema_version: u32,
            complete: bool,
            implementation: &'static str,
            mode: &'static str,
            process_id: u32,
            exit_code: i32,
            measurement: Measurement,
            threads: &'a [ThreadReport],
        }
        let allocations_enabled = reports.iter().any(|r| r.allocations.is_some());
        let report = Report {
            schema_version: 1,
            complete: true,
            implementation: "rust",
            process_id: std::process::id(),
            exit_code,
            mode: if allocations_enabled {
                "timing_allocations"
            } else {
                "timing"
            },
            measurement: Measurement {
                elapsed_wall_ns: reports
                    .iter()
                    .find(|r| r.role == "main")
                    .map_or(0, |r| r.span_wall_ns),
                thread_cpu_clock: if reports.iter().all(|r| r.span_cpu_ns.is_some()) {
                    "CLOCK_THREAD_CPUTIME_ID"
                } else {
                    "unavailable"
                },
                stage_times: "exclusive on each thread; nested same-thread stages subtracted; blocking is wall, not CPU",
                thread_spans: "inclusive; threads overlap; do not sum thread wall spans or stage wall across threads",
                sidecar_io: "excluded; serialization and file creation/write occur after measurement",
                allocation_semantics: "Rust GlobalAlloc requested alloc/realloc bytes and supplied dealloc layout bytes; failed requests count; foreign/libc allocations excluded; not heap/RSS/peak or net memory; registered threads only; diagnostics bookkeeping excluded",
                writer_calls: "logical Write::write and Write::flush calls at the CLI sink boundary; not OS syscalls; implicit SignalWriter flush counted separately",
                input_bytes: "response bytes consumed by renderer; unread tail excluded; no transport framing or decompression attribution",
                serialization: "render::json includes pretty serialization, UTF-8 conversion, HTML/U+2028/U+2029 escaping, and terminal newline",
                sample_every: SAMPLE_EVERY.load(Ordering::Relaxed),
                sampling: "whole SQL JSON element stages sampled together; calls and size counters cover every element; timed_calls/wall_ns/cpu_ns cover actual sampled calls, never extrapolated; initial framing/main stages always timed; allocations force sample_every=1",
                drop: "explicit framed bytes, ordered document, converted document, serialized and indented buffer destruction; other destructor work remains in inclusive thread span",
                unregistered_threads: "spinner and library-created helper threads are excluded; process CPU must be measured externally",
            },
            threads: &reports,
        };
        let _ = (|| -> io::Result<()> {
            fs::create_dir_all(&directory)?;
            let path = directory.join(format!("pb-diagnostics-{}.json", std::process::id()));
            // Avoid overwriting existing captures, and keep counters private.
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options.open(path)?;
            serde_json::to_writer_pretty(&mut file, &report)?;
            file.write_all(b"\n")
        })();
    }
}

/// Optional renderer-side wrapping for in-memory tests and attribution. The
/// actual CLI instruments SignalWriter instead to avoid counting writes twice.
pub struct MeasuredWriter<W>(pub W);
impl<W: Write> Write for MeasuredWriter<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let _timer = Timer::begin(Stage::OutputWrite);
        let result = self.0.write(bytes);
        write_count(bytes.len(), result.as_ref().ok().copied().unwrap_or(0));
        result
    }
    fn flush(&mut self) -> io::Result<()> {
        let _timer = Timer::begin(Stage::OutputFlush);
        flush_count();
        self.0.flush()
    }
}
pub fn write_count(attempted: usize, completed: usize) {
    count(|c| {
        c.write_calls += 1;
        c.write_attempted_bytes += attempted as u64;
        c.write_completed_bytes += completed as u64;
    });
}
pub fn flush_count() {
    count(|c| c.flush_calls += 1);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn local_thread() {
        STATE.with(|state| {
            *state.borrow_mut() = ThreadState::new();
            state.borrow_mut().registered = true;
        });
        ACTIVE.store(true, Ordering::Relaxed);
    }

    #[test]
    fn diagnostic_nested_stages_count_exclusively_and_writer_counts_calls() {
        // Single test owns the diagnostic global switch; the CLI tests run in
        // subprocesses, so no process environment/global allocator mutation.
        local_thread();
        let inclusive_start = Instant::now();
        let outer = Timer::begin(Stage::ScanFraming);
        let inner = Timer::begin(Stage::OrderedDecode);
        let inner_start = Instant::now();
        while inner_start.elapsed().as_millis() < 2 {
            std::hint::spin_loop();
        }
        drop(inner);
        let child_wall_ns = STATE.with(|state| state.borrow().children[0].wall_ns);
        drop(outer);
        let inclusive_wall_ns = ns(inclusive_start.elapsed());
        input_consumed(3);
        let mut writer = MeasuredWriter(Vec::new());
        writer.write_all(b"ok").unwrap();
        writer.flush().unwrap();
        STATE.with(|state| {
            let state = state.borrow();
            assert_eq!(state.metrics[Stage::ScanFraming as usize].calls, 1);
            let inner = state.metrics[Stage::OrderedDecode as usize];
            assert!(inner.wall_ns >= 2_000_000);
            assert_eq!(child_wall_ns, inner.wall_ns);
            assert!(
                state.metrics[Stage::ScanFraming as usize].wall_ns + inner.wall_ns
                    <= inclusive_wall_ns
            );
            assert_eq!(state.counters.input_consumed_bytes, 3);
            assert_eq!(state.counters.write_calls, 1);
            assert_eq!(state.counters.write_completed_bytes, 2);
            assert_eq!(state.counters.flush_calls, 1);
        });
        ACTIVE.store(false, Ordering::Relaxed);
    }

    #[test]
    fn diagnostic_allocation_subtraction_does_not_treat_frees_as_negative_heap() {
        let request = AllocationCounts {
            alloc_calls: 1,
            alloc_requested_bytes: 32,
            realloc_calls: 1,
            realloc_requested_bytes: 64,
            dealloc_calls: 2,
            dealloc_layout_bytes: 96,
        };
        assert_eq!(request.minus(request), AllocationCounts::default());
        let mut total = AllocationCounts::default();
        total.add(request);
        total.add(request);
        assert_eq!(total.alloc_requested_bytes, 64);
        assert_eq!(total.dealloc_layout_bytes, 192);
    }
}
