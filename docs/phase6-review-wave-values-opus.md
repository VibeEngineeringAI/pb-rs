# Phase 6 review wave: Arrow value and byte-encoding review (Opus)

A focused, independent review of the tail value renderer. It is **not** final
acceptance and does not cover the session, transport or decoder.

## Snapshot reviewed

| Item | Value |
| --- | --- |
| Worktree | `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail` (dirty tree on base `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`) |
| `src/tail/values.rs` | sha256 `037183a7939fc11d3921c36d086fad023d9a6c0004839096b28ab140cd56ea6b` (2560 lines), re-hashed unchanged at the end |
| `docs/phase6-values-notes.md` | sha256 `31cef3ab69afd4504d70f60ee6f668c6ac3558406b70b54c5465766a1ff0deed` |
| `harness/values-oracle/main.go` | sha256 `e5de695b062d975bdaceeef8f77ad3d8c5f0f6b06e0d9fe5f247b149d1a3eab2` |
| `tests/fixtures/arrow-go-v13/manifest.json` | sha256 `e0c8ba1f8d563deddfa530cd75a8744d95130767b523d1f7160e70d19a473856` |
| `Cargo.toml` / `Cargo.lock` | `eb55b298…0c83c` / `aa35f338…1ebb44` |
| Go oracle | go1.25.4 (`/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/bin/go`), Arrow `go/v13 v13.0.0`, goccy `go-json v0.10.0` (from the root `go.mod`/`go.sum`) |
| Rust | rustc 1.96.0 (`~/.cargo/bin`), standalone `rustc` builds only; no cargo build, no shared target |

Sources read: `values.rs` in full; the notes; the value sections of decoder
reviews r1–r4; the 44-case generator; Go Arrow v13 `array/*.go` (every
`GetOneForMarshal`/`MarshalJSON`, `setData` for interval, struct, union and
null, `util.go RecordToJSON`) and `datatype_fixedwidth.go` (`ToTime`,
interval types); goccy v0.10.0 `internal/encoder/{string.go,decode_rune.go,encoder.go}`
and `vm/vm.go` (OpMap…OpMapEnd); Go 1.25 `time/time.go` (absolute-date
split); and `src/sql/output.rs:372-381` (precedent, see V1).

## Findings

### V1. Shortest-float ties render differently from Go (MEASURED, medium: silent wrong bytes on valid finite values)

**Where.** `values.rs:1452-1454` (`push_f64`) and `1474-1476` (`push_f32`, which
Float16 also uses via `go_f16_to_f32`) take digits from core's `{value:e}`. The
comment at `values.rs:1369-1371` says Rust "emits the same shortest round-trip
digits; only the layout differs". That is false for ties. Sometimes the exact
binary value lies exactly halfway between the two shortest round-tripping
decimals. Go `strconv.AppendFloat(v, 'f'|'e', -1, bits)` (goccy
`encoder.go:332-356`) then picks the even last digit, while core picks the
upper one. All other digits agree.

**Measured (Go, end to end).** `probe-e2e` builds a record, writes and reads
it with Go IPC, and replays `RecordToJSON` plus `Println`
(`e2e-out/float_shortest_ties.stdout`, sha256 `e3f31c8a…6ec7178`):

```
{"d":2079541830868280.2,"f":43479.062,"h":0.00024414062,"lf":[43479.062,1]}
{"d":1700000000123456.2,"f":453943.62,"h":0.016601562,"lf":[]}
{"d":0.1,"f":0.1,"h":1.5,"lf":[]}
```

Inputs: f64 `2079541830868280.25` and `1700000000123456.25`; f32 `43479.0625`
and `453943.625`; f16 `0.000244140625` (bits `0x0c00`) and `0.0166015625`.

**Measured (Rust, verbatim renderer code).** The differential checker
`probe-rs/probe` runs a verbatim copy of `values.rs` lines 1203-1320 and
1336-1891 (`write_str` through `Decimal::format_g`, minus `push_base64`) on Go-generated
vectors. It reports the following:

| Kind | Vectors | Mismatches | Example (Go / Rust) |
| --- | --- | --- | --- |
| f64 | 120,494 | 23 | `431d8d54407f74e1`: `2079541830868280.2` / `2079541830868280.3`; `c24804fc47d09200`: `-206325714849.14062` / `-206325714849.14063` |
| f32 | 120,486 | 125 | `4729d710`: `43479.062` / `43479.063` |
| f16 (all 65,536 bit patterns) | 65,536 | **1,024** | `0x0c00`: `0.00024414062` / `0.00024414063` |

Every mismatch is a last-digit tie of this kind. Rust therefore renders the
e2e rows as `2079541830868280.3`, `43479.063`, `0.00024414063`, and so on. The
same functions serve top-level values, list elements, struct fields, union and
dictionary values. The 44-case corpus passes because none of its floats is a
tie.

**Exposure.** Ties need an exactly representable value whose decimal
expansion ends in a 5 one digit past the shortest form. Values parsed from
short decimals (`0.1`, `3.14`) essentially never tie. Large f64 values with
dyadic fractions do (about 1e11–1e16 with `.25`, `.0625`, …), as do many f32
values and about 1.6% of all f16 values.

**Precedent and minimal fix.** The SQL path already solved this.
`src/sql/output.rs:372-381` says "Rust's Display rounds shortest-decimal ties
differently from Go. zmij uses the same tie choice as Go" and formats through
`zmij::Buffer`. `zmij = "1.0.23"` is already a direct dependency, so the fix
is dependency-neutral: take the shortest digits from
`zmij::Buffer::format_finite` for f64 and f32 instead of `{:e}`, and keep the
existing goccy 'e'/'f' threshold and layout code.

I checked this candidate. The same `push_go_float` layout, fed by zmij
(`probe-rs/probe_zmij`), gives **0 mismatches** on all 306,516 float vectors,
including every f16 pattern. Feeding it from ryu 1.0.23 (`probe_ryu`) also
gives 0. Non-finite handling is unchanged: the float64 error stays, and f32
keeps its raw token plus `cx.raw`.

Regression tests to add:
- the `float_shortest_ties` stream with Go's bytes above;
- unit asserts f64 `0x431d8d54407f74e1` → `2079541830868280.2`, f32
  `0x4729d710` → `43479.062` and f16 `0x0c00` → `0.00024414062`.

Also correct the comment at `values.rs:1369-1371`.

**Stage impact.** Values renderer only. stdout bytes change for tied values.
There is no change to exit status, error or panic classification, or session
ownership. This is not part of W1/W2 or CW1-3.

### V2. List/LargeList of interval with an empty child array: Go prints `null`, Rust `[]` (MEASURED Go / SOURCE Rust, low)

**Go mechanism.** `MonthInterval`, `DayTimeInterval` and `MonthDayNanoInterval`
`MarshalJSON` (`array/interval.go:107-110`, `408-410`, `709-711`) return
`json.Marshal(a.values)` when `NullN() == 0`. This differs from every other
element type, which builds `make([]interface{}, Len())`.

`setData` (`interval.go:86-95`) slices `CastFromBytes(vals.Bytes())`. Go's IPC
reader turns a zero-length buffer into nil bytes, the same rule as the
Binary nil-buffer case already in the notes. So when the list's whole child
array is empty, every list slice has a nil `values`, and goccy marshals a nil
slice as `null`. `List.GetOneForMarshal` returns that as `json.RawMessage("null")`.
A grep of `arrow/array` shows these three are the only `MarshalJSON`s that
marshal a stored slice directly.

**Measured (Go, IPC round trip, `probe-e2e`).**

| Case | Go stdout |
| --- | --- |
| `List<MonthInterval>` rows `[]`,`[]`,`null` | `{"l":null}\n{"l":null}\n{"l":null}\n\n` |
| `List<DayTimeInterval>` `[]` | `{"l":null}\n\n` |
| `List<MonthDayNanoInterval>` `[]` | `{"l":null}\n\n` |
| `LargeList<MonthInterval>` `[]` | `{"l":null}\n\n` |
| `Struct{l: List<MonthInterval>}` `{l:[]}` | `{"s":{"l":null}}\n\n` |
| Control: `List<MonthInterval>` `[]`,`[{"months":5}]` (non-empty child) | `{"l":[]}\n{"l":[{"months":5}]}\n\n` |
| Control: `List<Int64>` `[]` | `{"l":[]}\n\n` |

**Rust (SOURCE, deterministic).** `marshal_run` always writes `[` and `]`
(`values.rs:1167`, `1189`), and `Col::new` for `Plan::List`
(`values.rs:727-763`) has no nil-child branch. Rust therefore prints
`{"l":[]}` in the first five rows. I did not execute the Rust renderer on
these streams (see Limits). The streams and Go stdout are in the cache.

**Reach.** A batch in which every list of an interval-element List or LargeList
column is empty or null. FixedSizeList cannot reach this, because Go rejects
size ≤ 0 at init, so a row always has a non-empty child. Map cannot either,
because its child is a Struct whose `MarshalJSON` uses `GetOneForMarshal`.
Dictionary, REE and union elements go through `GetOneForMarshal` and are
unaffected.

**Minimal fix.** In `Col::new` for `ListKind::List`/`LargeList` whose element
plan is one of the three interval plans, record `nil_child = values.is_empty()`
(the child length). In encode, a non-null list with `nil_child` writes `null`.
Gather can skip it. FSL and Map stay unchanged.

Caveat to declare: Go keys on the declared IPC buffer length being 0. arrow-rs
slices primitive buffers to the array length, so a writer that declares a
padded non-zero buffer for an empty child would give Go `[]`, against Rust
`null` after the fix. Mainstream writers declare 0.

Regression: the five streams above plus both controls.

**Stage impact.** Values renderer only, stdout bytes. Rare in practice,
because Parseable ingestion does not create interval columns.

## Informational (no change requested)

- **I1 (SOURCE): Go's choice among multiple float64 errors in one map is
  nondeterministic.** goccy encodes map values in Go's randomized
  iteration order and sorts afterwards (`vm/vm.go:394-469`). If one
  row or struct map holds several non-finite float64 values, the first error
  hit varies from run to run. At top level the message is ignored, so there is
  no effect. Inside a list (for example `List<Struct{a: NaN, b: +Inf}>`), Go's
  **panic text** varies between `…: NaN` and `…: +Inf`. Rust's sorted-order
  choice (`encode_object`, `values.rs:1127-1145`) is always one of Go's
  possible outputs. Stdout is empty and exit 2 either way. A ledger that claims
  exact panic-message parity should say "one of Go's possible messages" for
  this case. Not measured in Go.

## Checked and consistent

**Differential probe.** The pure functions, run as verbatim code, matched Go
byte for byte, with 0 mismatches in every group:
- Timestamp in all four units: 1,960,244 vectors. These cover the leap day,
  Mar 1, Dec 31 and Jan 1 boundaries for every year from −5000 to 5000 (±1
  and +86399 s), uniform random int64 (exercising Go's wrapping `v*multiplier`
  and absolute-epoch wrap), and `MinInt64`/`MaxInt64`/`-9223372028741760000±1`.
- Date32: 170,024 vectors, including the int32 extremes.
- Date64: 490,052 vectors (truncation toward zero, `MinInt64`/`MaxInt64`).
- Time32 s/ms and Time64 us/ns: 40,000 random vectors (wrapping and negative
  values).
- Strings: 60,000 random byte strings built from controls, `"\<>&`, DEL,
  U+2028/2029, overlong, surrogate, truncated and out-of-range sequences, at
  lengths 0–~50. This exercises goccy's 8-byte SWAR fast path, the per-byte
  `�` replacement and the separators.
- Decimal128: 549,926 values from real Go `Decimal128` arrays'
  `GetOneForMarshal`. Precision runs 1–38 and scale −128…127; values include
  10^k±{0,1,4,5,6,49,50,51}, 5·10^k ties, random i128 and ±(2^127−1).
- Decimal256: 55,952 values, precision 1–76.
- f16 non-tie values: 64,512 of 65,536.

**Source.** I compared the following against Go and found them consistent:
- **List elements.** For each element type, `MarshalJSON` versus
  `GetOneForMarshal` agrees with `encode` in every case except V2: Int8/UInt8
  as float64, timestamps, dates and durations via `GetOneForMarshal`, Binary nil,
  FixedSizeBinary, String, Null, Boolean, Decimal, Struct, Union and REE via
  `Encoder.Encode`, and Dictionary's gather-all-first.
- **FSL error handling.** FixedSizeList returns its error where the other
  lists panic, and the wrapper names come from the element plan.
- **Panic and error precedence.** A panic beats an encode error, and the raw
  float32 token check runs only after the run's own errors.
- **Nulls.** Union nil is decided by the selected child's bitmap; the bitmap is
  absent for Null, REE and union children; a dictionary key bitmap counts.
- **Sliced unions and structs.** Rust's absolute indexing matches.
- **Map.** Pair fields are renamed to key/value.
- **Row keys.** Encoded-key sort with the trailing `,`, and the last duplicate
  wins after every column has been gathered.
- **Scratch.** The per-depth slot discipline holds: unique slots per scope,
  dictionary pre-gather is re-run, and the scratch buffers are restored
  before release.
- **Truncation and LF ownership.** A panic restores the buffer to its length on
  entry; an encode error truncates to the row start; the batch LF is the
  caller's.

## Commands (all outputs in `/home/ajs/.cache/pb-phase6/review-wave-values-opus`)

Disk-backed `TMPDIR`/`GOTMPDIR`/`GOCACHE` were inside the cache, with `nice -n 10`, `GOMAXPROCS` ≤ 4
and `GOPROXY=off`. The probe modules use copies of the root `go.mod`/`go.sum`
with the module line renamed.

```
# extract verbatim renderer code (sha256 7c6911340644e91e9270dea6a1f902fe40f879ff76e361128ef539856d1679aa)
sed -n '1203,1320p;1336,1891p' src/tail/values.rs > probe-rs/extract.rs
# Go vector generator (probe-go/main.go 7c33cb00…1864) -> vectors.tsv (3,632,714 lines, c4dec043…9472)
cd probe-go && GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=mod $GO build -trimpath -o gen . && ./gen > ../vectors.tsv
# Rust checker (probe.rs eb41fddd…e0e3)
rustc --edition 2024 -O probe.rs -o probe && ./probe < ../vectors.tsv      # probe-result.txt
# candidate digit sources (rlibs built from ~/.cargo/registry sources)
rustc --edition 2021 --crate-type rlib --crate-name zmij -O <zmij-1.0.23>/src/lib.rs -o libzmij.rlib
rustc --edition 2024 -O probe_zmij.rs --extern zmij=libzmij.rlib && ./probe_zmij < ../vectors.tsv   # probe-zmij-result.txt
(same for ryu-1.0.23 -> probe_ryu, probe-ryu-result.txt)
# Go end-to-end IPC round trip (probe-e2e/main.go 7e2953f5…d9e5)
cd probe-e2e && $GO build -trimpath -o e2e . && ./e2e ../e2e-out
```

Results:
- `probe`: d32/d64/ts/t32/t64/str/d128/d256 0 mismatches; f64 23, f32 125, f16 1024.
- `probe_zmij` and `probe_ryu`: f64/f32/f16 0 mismatches.
- `e2e-out`: 8 streams plus stdout. For example, `float_shortest_ties.arrows`
  is `0e498bd5…d9e5` and `list_month_interval_all_empty.stdout` is
  `96027b5d…c5ea`.

Hashes are recorded in `o14.txt`, `o16.txt`, `o17.txt`, `o18.txt` and
`o22.txt`.

## Limits (not run)

- The Rust renderer and crate were not built or executed. No cargo, no shared
  target, and no new heavy native probe without parent coordination.
  - **V1:** the Rust side is the renderer's own float code, compiled verbatim
    and standalone. `Col::encode` calls it directly (`values.rs:995-997`).
  - **V2:** the Rust side comes from source reading. An end-to-end Rust replay
    of the e2e streams is the owner's regression test.
- The 44-case corpus and the session/central suites were not rerun. Their
  reported state comes from the brief.
- Not probed:
  - deeper nesting beyond the cases listed;
  - compressed zero-length buffers;
  - extension types;
  - non-mainstream writers that pad empty-child buffer lengths (V2 caveat);
  - Go's map-order nondeterminism (I1).
- No new session issue was found. W1/W2 and CW1–CW3 were not re-reviewed.
