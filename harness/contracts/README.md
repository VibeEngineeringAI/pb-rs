# Phase-one offline parser and help contracts

`phase1-surface.json` records the reachable Go CLI surface reviewed at
`07af390ab09bda8ac9e427ffc6310077bace0125`, public version `0.0.0-harness`.
The final reference uses Go `1.25.4` as pinned in `go.mod`, with Unicode `15.0.0`
tables. Building it with a newer Go compiler can change observable Unicode
quoting and case matching; use the pinned toolchain when reproducing captures.
It contains 46 visible command-tree nodes, the custom help command, and the
hidden generated completion command and its four shell children. Flags are
local: root and parent flags do not automatically become leaf flags. Arity
entries describe Cobra argument validation, which runs before operational
preflight. Handler-specific validation and configuration prerequisites are not
invented as parser validation.

The recorded reference SHA is the binary reviewed during corpus construction.
Each capture pins the exact binary SHA actually executed, its observed version
output, the corpus/surface hashes and transcript bytes. The runner checks the
public reference version and rejects visible command-tree drift. It does not
claim to infer a Git revision from an arbitrary binary: preserve the reference
binary and build provenance with the capture when rebuilding elsewhere.

Run from the repository root:

```sh
python3 -m unittest discover -s harness -p test_phase1.py -v
python3 harness/phase1.py list
python3 harness/phase1.py capture --reference bin/pb-go \
  --out harness/results/phase1-go
python3 harness/phase1.py compare --baseline harness/results/phase1-go \
  --candidate target/release/pb --out harness/results/phase1-rust

# A fresh live Go/Rust comparison also works.
python3 harness/phase1.py compare --reference bin/pb-go \
  --candidate target/release/pb --out harness/results/phase1-live

# Validate the differential harness against the oracle itself, without running
# Go's deferred operations. This checks parity machinery, not the full Rust gate.
python3 harness/phase1.py compare --baseline harness/results/phase1-go \
  --candidate bin/pb-go --parity-only --out harness/results/phase1-self-check

# Both capture and compare emit this fixed thirteen-case benchmark subset.
python3 harness/run.py run \
  --suite harness/results/phase1-rust/benchmark-suite.json \
  --binary go=bin/pb-go --binary rust=target/release/pb \
  --out harness/results/phase1-performance

# Export a captured subset, or the full parser corpus with --all.
python3 harness/phase1.py suite --baseline harness/results/phase1-go \
  --out /tmp/pb-phase1-suite.json
```

Outputs must be new paths. Exit 0 passes, exit 1 indicates compatibility or
deferred safety guard failure, and exit 2 indicates setup or artifact integrity
failure. Reports and per-case exact stdout/stderr bytes remain in the output
directory; giant oracle transcripts are deliberately not committed. Saved
baselines must use the current report schema, be complete and passing, match the current corpus and surface,
and have transcript bytes/metadata consistent with the report. A reference or
candidate executable changing during capture fails the gate. Schema 2 records
directory, symlink and special-file effects as well as regular files; older
captures are rejected. The report records the runner source SHA for provenance.
Replaying a capture does not require that source hash to match byte-for-byte
(for example, a prose-only edit can change it); semantic snapshot changes must
bump the schema and parser workload changes must update the corpus hash.

The parity corpus is finite and deterministic. It includes root help and JSON
tree, custom help paths in text and JSON, command help and aliases, root version
flags and version command, agent catalog, every reachable command's unknown
flags, every registered local flag, attached/separated/equals syntax, local flag
placement before command words, short boolean clusters, empty/missing/invalid
values, repeated string/string-array flags, integer syntax and overflow,
interspersed arguments, terminators, invalid arity, output precedence, invalid
output, control/Unicode quoting, suggestions, and raw-argv JSON error scanning.
Forty-five bounded quoting cases sweep every Unicode scalar from U+00A0 through
U+10FFFF, excluding surrogates, in chunks of at most 25,000 scalars. Each UTF-8
argument is at most 100,000 bytes, below Linux's 128 KiB per-argument limit;
outputs remain bounded by the runner's 4 MiB limit. These cases are excluded
from benchmarks. Focused cases also cover non-runnable completion parent help,
overflow-versus-syntax precedence, and simple-fold long-s JSON error selection.
Exact byte comparison includes trailing spaces, formatting, escaping, stderr
usage and exit-versus-signal status. There is no byte normalization.

Valid leaf operations are never invoked on Go by this corpus. Help short-circuits
before prerequisites and actions; parser-invalid and arity-invalid cases are
reviewed to terminate before actions. Root/version/agent/custom-help and
profile-free parent catalog rendering are the only successful actions. Direct
SQL/PromQL/dataset/user/role parent JSON discovery invokes profile initialization
in Go, so its successful metadata rendering is characterized with `help PATH -o
json`; successful direct invocations with configured profiles remain deferred.
Valid parser arguments under an empty HOME additionally characterize all
reviewed missing-profile prerequisites, including those parents and their
operational leaves, direct tail, and unconfigured status. These cases require
exit 1 and the reviewed text prefix or public JSON `NOT_FOUND` envelope before
any operational body can run; status retains its established unhealthy stdout
JSON behavior. They are excluded from the fixed benchmark subset. Defined but
unregistered schema/cluster/autocomplete/install wrappers are also listed in
the scope record. Hidden shell command help/parsing is covered; generated
scripts and `__complete`/`__completeNoDesc` protocol execution are deferred.
TTY styles, capability probes and interactive screens remain outside the pipe
output contract. The corpus uses Unicode argv. Go accepts byte-valued argv that
is not UTF-8; Rust currently rejects it explicitly at the `String` parser
boundary. That parity remains unresolved. Native execution has been verified
only on Linux; macOS and Windows coverage remains deferred. Both limitations
also appear in the generated report's deferred scope record.

Default `compare` additionally runs independently counted candidate-only
deferred guards. They invoke operational leaves and aliases with valid parser
arguments and a synthetic isolated profile pointing at `127.0.0.1:1`. They
require exit 1, empty stdout, and the explicit canonical command message
`command "pb PATH" is not implemented in this Rust phase`. Local JSON output
requires a `COMMAND_FAILED` envelope with `retryable: false`. No config/file
writes are permitted. These safety assertions establish the declared Rust
phase boundary; they are not compatible Go operation implementations and are
never exported for performance measurement. `--parity-only` skips them, and
an identical Go binary cannot be used as their candidate. Only invoke deferred
guards on a reviewed phase-one Rust binary; advancing operational phases must
update these assertions explicitly.

Every process receives empty HOME/XDG directories, fixed C locale/UTC,
`PB_ANALYTICS=disable`, `TERM=dumb`, and `PB_THEME=dark`, with stdin closed.
Timeout and output-limit failures always fail even if both binaries match.
Process groups and accidental descendants are terminated, stdout and stderr
are drained independently, and isolated entry presence, type, permissions,
regular-file content and symlink targets are checked for changes. The snapshot
uses `lstat`, includes the temporary root and directories, never follows links,
and records special-file types without opening FIFOs or devices. Timestamps are
excluded. This is environment isolation and source-reviewed offline coverage,
not an OS network or filesystem security sandbox. Requests are not silently
invented or normalized; successful network/config actions belong to later
fixture-backed acceptance gates.

The benchmark subset is chosen in corpus metadata before candidate execution,
never filtered by candidate passes. A mismatch anywhere fails the full gate;
benchmarking the subset does not compensate for a failed acceptance case.
`run.py` rechecks the subset's exit, byte contracts and absence of fixture
requests before reporting speedups. Existing `cases.py` and its original
44-case operational fixture corpus remain unchanged.

## Phase-two promotion

The 2,018-case offline corpus remains fixed. `phase1.PHASE2_OPERATIONS` explicitly
removes implemented commands from candidate-only deferred guards; 52 guards
remain. Those commands now require the independent configuration, HTTP and
interoperability gate in `phase2.py`. Passing the offline corpus alone does not
establish phase-two compatibility. See [phase-two contracts](phase2.md).
