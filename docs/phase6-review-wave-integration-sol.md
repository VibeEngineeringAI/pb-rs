# Phase6 independent integration review — Sol6.1

**Scoped disposition: no new objection in the shared Rust integration or locked
dependency inventory. Two concrete adapter findings remain: I-1 (medium,
verified corpus bytes are reread without binding) and I-2 (low, stale H2
per-case success flags).** Neither is a production tail defect. This review is
not whole-phase, live-server, resource, performance or cross-platform acceptance,
and does not substitute for the required fresh whole-phase Opus review.

## Scope and current snapshot

Review date: 2026-10-14. Worktree/HEAD/base:
`/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`,
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, plus dirty implementation.
Pinned Go revision: `07af390ab09bda8ac9e427ffc6310077bace0125`; toolchain
contract: Go 1.25.4. I read that revision's `cmd/tail.go` through `git show`;
I did not run Go.

Read the current Phase6 document, Phase5 subprocess/FD warning, complete tracked
diff against the base, Cargo manifest/lock, shared Rust changes, command/worker/
discovery/diagnostic boundaries, the three new root adapters and their tests,
and the relevant integration, decoder, transport, values, CW and measurement
review records. There were no applicable AGENTS.md files in the worktree or
checked ancestor paths. No other worktree was read, edited or merged; no
agents or commits were made.

The root extended the dictionary adapter during this review, then explicitly
settled it. All adapter findings and the final 23-test run concern that settled
extension, including explicit `values-wave` and text mode. The earlier
21-test run preceded its two added tests and is not substituted for the final
run. Root also completed R6 central checks and produced release artifacts
during this review. These supersede the brief's initial "no R6 yet" status.

Independently computed SHA256 values:

- `Cargo.toml`: `eb55b298c3158cf32c51a9876f53d3ebe220adbd7eaa51fd1af8fe4d1dc0c83c`.
- `Cargo.lock`: `aa35f338bd6b6b803c5e970f021d59f54d2e7435823f75a5baee018d7a1ebb44`.
- `src/lib.rs`: `638a9e0edce26ce60c8dba6398c1671c1da92cb2af6d5e29cca2657e623dea05`.
- `src/diagnostics.rs`: `2117c675cdead8fb14294def2dada41139bebc7fe889f5448b3b2d4816147189`.
- Shared `src/transport.rs`: `d4b9d521eb726bb24601749b50369f1708d0027b9699874b518b5e7faa49dd7c`.
- Unchanged `src/api.rs`: `3195a29ef68b485025eb540f3dba0ada718db4de39f01c3bbe2ecc38dfaafe92`.
- Unchanged `src/main.rs`: `68f04d772c2a51d13df15fec98a0a364f45cd83745f4d6a07990603439b34ab2`.
- Tail boundary `mod.rs`: `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7`.
- `resources.rs`: `8210efe689760620dd3536e2825a738acad28a51c5f17ac078933e3829d5473b`.
- `session.rs`: `2d793026cf4e920a5d821a98855a32f7dee918fadc338e2368e0df5134d58a1e`.
- `session/tests.rs`: `cb9bb45eb5a0a403e7ba469203884f3c5e18c17128132cccecf07d4a974fd076`.
- Tail `transport.rs`: `f08a0c73afa112216365b4d39f9dc682a2c27a24b18b711a34c0111217a89845`.
- `validation.rs`: `65ebd8fab90a99937b343955affe8deca50c702aa37f72edef6f980f9425d3f6`.
- `values.rs`: `e02d7e63aec6c9cf6f53f31a927a2cda5855a648adf3e7e05735d09d10354fd5`.
- `harness/phase6_dictionary_cases.py`: `70d0e0ee1109d120b188b3a50a0d85c5cacd25e745d52389fed598978c01ca14`.
- Its tests: `a811403c78d0e71a41612486c818f32dc6aca20e1379ac7dd90bfa0b4f21feee`.
- `harness/phase6_transport_wave.py`: `7b2afebee528d353eb5d1ad1c4592b7ed47c68e3ae77d3cc63dc919a47e53a87`.
- `harness/phase6_h2_limits.py`: `3c8c5326ea1983dfbef59037f4d1488ae5ce8d1d60159a7183bc85c6560e4829`.

Cache `before.json`/`after.json` retain the wider identity inventory; reviewed
adapter and settled transport/values source copies are in `reviewed-source/`.
Only `docs/rust-phase6.md` and the dictionary adapter/tests changed among the
original 87 inventoried inputs. The final inventory includes 90 files,
including newly completed review records. Production/Cargo identities stayed
unchanged throughout the inventoried interval.

## I-1 — medium: corpus verification does not bind the bytes actually used

**Location:** `harness/phase6_dictionary_cases.py:84–89`, `:104–116`,
`:167–168`, `:209–213`, at the hash above. This affects IA1, dictionary-wave
and the new values-wave selection, in both modes.

`cases()` hashes each manifest-listed file, discards those verified bytes, then
rereads IPC and expected stdout/terminal files when constructing fixture frames
and independent expectations. `main()` binds only the adapter, `phase6.py` and
the manifest as sources, not the corpus files. Its later manifest hash is also
a separate read from the manifest bytes parsed in `cases()`. A corpus change
after verification can therefore enter both the executed scenario and its
expectations while leaving `identities_unchanged=true`.

**Measured process-free witness:** `adapter_probes.py` copied the actual 25-case
IA1 corpus into the owned cache. A `Path.read_bytes` hook read the last artifact
in the initial verification loop, then changed only that cache's
`list_names_same.arrows` dictionary values `[7,8]` to `[9,8]` and the matching
`list_names_same.go.stdout`. The original manifest remained byte-identical.
Every initial hash check still examined original bytes. Subsequent case
construction consumed the changed bytes, with no rejection. Running the actual
`main()` with mocked capture creation returned:

```text
status=0 complete=true passed=true failures=0
remaining_contract_passed=25 identities_unchanged=true
```

The recorded Go signature's stdout digest was
`de146f8f1768aa01a9851ed1857f901e9f5548bd38d1ee937608c03e321f6552`,
not the manifest's original
`12c2854788960810824c4c7c4cb5c8e615741f72c7308f4427f1362e45a1a93b`.
IPC changed from
`16df99ca576c00de94421929349f9b70bea41826d97ee5c51f618402868e9fd7`
to `7ee144afa07b4885b0909c4ccfb791f0af17e15e920cf42791215c9a712c4b78`.
Neither artifact was in the report's end-source check. A separate call against
the already changed corpus correctly rejected it before verification, proving
that the existing preverification test covers a different boundary.

This is an actual admission/provenance witness with synthetic captures, **not
a native Go/Rust outcome measurement**. It does not assert that historical
captures experienced this race. Full R6 production build provenance does not
by itself bind a later Python runner's corpus reads.

**Narrow correction:** retain and consume the exact verified corpus bytes,
including output, terminal and values `.errors`/`.panic` files. Retain the
manifest identity from its original parsed bytes. Bind all declared corpus
artifacts to their verified identities for end checks; do not establish fresh
baselines with later rereads. Add pure tests for a replacement after successful
verification and a later end-check replacement. The existing preverification
failure should remain. No new native campaign is needed to test this fix.

## I-2 — low: H2 case labels survive a failed repeat

**Location:** `harness/phase6_h2_limits.py:127–148`, at the hash above.
`classify()` sets `declared_limitation_verified` and
`control_remaining_contract_passed` before the main loop adds repeat and
executable-identity objections. The aggregate count correctly consults the
final issues, but the saved per-case boolean is never recomputed.

**Measured process-free witness:** a single actual H2-CL-1 JSON case was passed
through `main()` with otherwise conforming mocked Go/candidate captures. Only
the candidate repeat's `failure` was set to an independent timeout control.
The result correctly returned status 1, `failures=1`, aggregate verified count
0 and `candidate_repeat: collector failure: independent timeout control`, yet
the same case retained `declared_limitation_verified=true`.

The overall runner does not falsely pass. A reader consuming the per-case
verification label gets contradictory evidence. Recompute both per-case
success booleans after every repeat/identity issue is appended, and test the
main report path. Transport-wave already recomputes its case flags there.

## Shared Rust integration and preserved contracts

The tracked production diff is appropriately narrow. `lib.rs` adds a private
tail module and dispatches exactly `pb tail`, after help, argument and active
profile preflight. It preserves the public `run`/`run_with_writers` signatures,
Outcome and error types. Tail validates output after that profile preflight,
as the pinned Go command does. No API, auth, config, command declaration or
`main.rs` changes were present.

The two shared transport changes expose `no_proxy_matches` and
`load_tls_config` as `pub(crate)`; their bodies are unchanged. This creates
neither a public library API nor a shared transport-policy rewrite. Discovery
uses the existing concrete `ApiClient`, including its existing updated-profile
accessor after `/api/v1/about`. Flight snapshots metadata/ticket and reuses it
across reconnects. Existing shared HTTP refresh behavior is untouched.

The tail driver uses a current-thread runtime and a scoped output/decoder
worker, with capacity-one channels and acknowledgment before the next
application message. DoGet and schema initialization remain fatal; established
EOF/OK/Canceled/Unavailable reconnect after 500 ms with fresh decoder state.
The original raw-message/error rendering boundary remains in use. Text status
does not depend on terminal detection, and both output modes receive the same
row/batch stdout framing. Pinned Go ignores `RecordToJSON` and `fmt.Println`
errors; the worker's ignored conversion/output returns therefore do not imply
a new shared error-propagation contract. Existing main writer retry, flush,
SIGPIPE and startup FD behavior remain unchanged by source identity.

Phase5's warning about restoring the original FD limit before future child
execution remains applicable to future production subprocesses. No production
tail subprocess is introduced. The self-spawning handoff regression is under
`#[cfg(test)]` and is not evidence of a production subprocess. Scoped worker
and resolver threads do not execute a child program.

Diagnostics additions compile only with the existing diagnostics feature.
`Session::begin` resets optional tail counters; the additive
`tail_resources` field is omitted unless subscriptions were counted.
`finish` observes joined workers. The snapshot explicitly distinguishes
owner maxima from RSS, unique allocations, simultaneous totals and transport
read-ahead. There is no basis here for stronger memory or signal-sidecar claims.

An independent pure guard comparison removed exactly two Phase1 stubs:
`tail phase1` and `tail phase1 -ojson`. Guard count changed 11 → 9; all other
semantic guard tuples were unchanged, and none were added. No other deferred
feature was promoted. The separate Phase3 normalization requires the exact
redirect-deadline case, POST URL, complete error bytes and enabled timeout
normalization; its two focused tests pass and preserve duration/exit/request
gates. Fixture shared changes are conditional on Phase6 configuration; the
capability banner is additive. No new retained acceptance result is inferred
from reading these paths.

## Dependencies, package and platform claims

Independent TOML set comparison confirms **161 → 230 locked package identities:
69 additions, zero removals, zero old-version/source replacements**. It does
not mean old lock records are byte-identical: seven existing dependency lists
expand (`futures-channel`, `futures-util`, `getrandom` 0.4.3, `hyper`,
`num-traits`, `tokio`, `tracing`), plus the root package dependency list.
Existing registry checksums are unchanged. `lock-audit.json` records each
before/after record rather than equating feature expansion with replacement.

Actual direct locked versions and manifest requirements are:

- Arrow Flight/array/schema/buffer: 60.0.0 (`^60`); Arrow IPC: 60.0.0
  (`=60.0.0`, LZ4/Zstandard enabled).
- Flatbuffers: 25.12.19 (`=25.12.19`).
- tonic and transitive tonic-prost: 0.14.6; direct tonic requirement `^0.14`.
- prost/prost-types: 0.14.4 (`^0.14`).
- futures-util: 0.3.34 (`^0.3`); h2: 0.4.20 (`^0.4`). Those two packages
  already existed in the base graph, although direct use is newly declared.

Thus the exact graph is pinned by `Cargo.lock`/`--locked`; only IPC and
Flatbuffers use exact manifest constraints among these additions. Calling all
manifest requirements exact pins would be incorrect. Arrow/tonic dependency
features unify: disabling Arrow Flight defaults does not eliminate the
transport/server/router dependencies required by that crate itself. Metadata
shows those tonic features are enabled upstream, so removing only the root's
tonic defaults would not remove that graph. No dependency-minimization change
is requested without build evidence.

The locked sources identify official Arrow, Flatbuffers, tonic and prost
projects rather than local forks. A primary-source check confirms Arrow's
current Rust documentation and tonic's maintained release branch; the latter
repository now redirects from hyperium/tonic to grpc/grpc-rust and directs
released users to 0.14.x. These are maintenance indicators, not a claim that
every chosen version is latest or vulnerability-free.
[Apache Arrow Rust documentation](https://arrow.apache.org/rust/),
[tonic upstream](https://github.com/grpc/grpc-rust),
[Flatbuffers upstream releases](https://github.com/google/flatbuffers/releases).

Read-only Linux-filtered Cargo metadata resolves 193 packages, including 66
of the 69 additions. All those additions declare licenses; Arrow is mainly
Apache-2.0 (array declares Apache-2.0 AND MIT), tonic MIT, prost/Flatbuffers
Apache-2.0, and zstd BSD-3-Clause. The largest declared new dependency MSRV
observed is 1.88, below the unchanged package requirement 1.96. Root metadata
still declares edition 2024, version 0.1.0 and AGPL-3.0-or-later. This is an
inventory, not distribution-notice or legal certification.

Unfiltered offline metadata failed because the cached `wasip2
1.0.4+wasi-0.2.12` source is absent. No download was attempted. The
Linux-filtered inventory excludes new r-efi 5.3.0, wasip2 and wit-bindgen
0.57.1; their all-platform metadata/licenses were not certified. `cargo
package --list --allow-dirty --locked --offline` succeeded and lists the tail
modules, LICENSE and existing Go-source license notice. It checks inclusion,
not package compilation, installation or a release archive. Its missing
homepage/repository/documentation warning is pre-existing root metadata.

No native build was performed by this reviewer. TLS cfg paths retain the
existing Linux root loader and non-Linux native-roots path. No new
cross-platform compile/run claim follows. The Phase6 document correctly
withholds live delivery and cross-platform acceptance. During this audit the
root consolidated its "Latest" paragraphs to identify R6, corrected values,
transport limits and R5 retained phases1–3 separately from active reviews and
replays. Historical paragraphs remain history, not evidence that R6 was
replayed. Native zstd build
work and dependency/binary size costs are not comparative build measurements.

## Prior claims and present evidence

- **CW r2:** preserve its scoped acceptance of 40 independent tests and 12
  captures. Historical 291 cases retain their disclosed lack of independent
  destination-port provenance. Neither finding here reopens that accepted
  lane or retroactively certifies those ports.
- **Measurement r4:** preserve acceptance of its 84 guarded pure/mock tests
  and the two corrected input races. I did not rerun its campaign, pressure
  audit or suite. I-1 is a new witness in a separate root adapter, not a claim
  that the accepted collector reverted.
- **Transport:** prior F1/F3 closure and F2(a) unpadded scope remain the Opus
  lane's disposition. The reviewed production source is now `f08a0c73…`, not
  its earlier `09bb4c9d…` checkpoint. I inspected the constant-space open-header
  block guard and its tests, without independently executing h2. R2-1's source
  correction is present. R2-2 padded-empty-DATA remains a narrow declared limit
  with Rust unit evidence and source-only Go behavior; no Go process witness
  is credited. Tiny-frame pressure remains unknown.
- **Values:** current source is `e02d7e63…`, using the existing zmij dependency
  and the targeted empty-interval behavior. The original V1/V2 report's failing
  checkpoint is historical. The root reports source settlement; its latest
  document still identifies fresh Opus correction review as active. No second
  renderer campaign was attempted here. The separate nine-case native adapter
  exists, with no native replay acceptance supplied by this reviewer.
- **Decoder:** W1/W2 fresh Opus correction review closes source/unit scope;
  it explicitly leaves successor process replay outstanding. Its N2 limited
  source-binding note is superseded for central Rust verification by the full
  current R6 records, not retroactively repaired in its old logs. IA1's 25 R5
  process passes and W1/W2's 11 durable streams remain distinct inventories.
- **R5 and retained:** 291 historical Flight cases plus 25 IA1 passes do not
  test the later transport/values/W fixes. The brief's R5 full phases1–3 passes
  and phase4 real120 active status are owner claims, not fresh reviewer runs.
  The complete R4 retained artifact belongs to its earlier source/binaries.
  No present retained whole-phase pass is claimed here.

R6 evidence appeared during the audit. I independently rehashed all **47**
source entries in `central-review-r6-tests.json` and `candidate-review-r6.json`:
both match the current source, with zero mismatches. Both release artifact
hashes match the build manifest:

- Standard: `f1fb7a9e294a8f96bd9835de4c6f32318b0adf01bb164db648c4fb9c0ad5140f`,
  10,712,616 bytes.
- Cloud: `36096c89100fac562545a5d72f45a2cd8aae4770715e0b424327711386e31af6`,
  10,712,944 bytes.

I rehashed the four central logs and read their summaries: normal library
295 passed/1 ignored; diagnostics library 299 passed/2 ignored; all recorded
target results pass (367 normal and 373 diagnostics tests in total),
fmt/Clippy exits recorded as zero. These are the root's
actual runs, inspected independently, **not reviewer-executed cargo checks**.
The ignored exhaustive float-vector test is not an active central pass.
R6 release existence and source binding do not establish successor native
replay, retained acceptance, platform support, build cost or performance.

## Commands and reviewer evidence

All reviewer-generated artifacts are under
`/home/ajs/.cache/pb-phase6/review-wave-integration-sol`. The only worktree
write is this document. Core commands actually executed, from the named
Phase6 worktree:

```sh
export TMPDIR=/home/ajs/.cache/pb-phase6/review-wave-integration-sol/tmp
export GOTMPDIR="$TMPDIR" CLAUDE_CODE_TMPDIR="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1
cache=/home/ajs/.cache/pb-phase6/review-wave-integration-sol
git status --short
git rev-parse HEAD
git diff 4a812d5
git show 4a812d5:Cargo.lock > "$cache/base-Cargo.lock"
git show 07af390ab09bda8ac9e427ffc6310077bace0125:cmd/tail.go > "$cache/pinned-go-tail.go"
python3 "$cache/audit.py"
python3 "$cache/run_tests.py"
python3 "$cache/adapter_probes.py" > "$cache/adapter-probes.log" 2>&1
/home/ajs/.cargo/bin/cargo metadata --locked --offline --filter-platform x86_64-unknown-linux-gnu --format-version 1 > "$cache/cargo-metadata-linux.json"
/home/ajs/.cargo/bin/cargo metadata --locked --offline --no-deps --format-version 1 > "$cache/cargo-metadata-root.json"
/home/ajs/.cargo/bin/cargo package --list --allow-dirty --locked --offline > "$cache/package-list.txt"
```

The first bare `cargo` invocation failed because cargo was absent from PATH;
using its absolute path resolved that environment issue. The unfiltered
offline metadata failure is retained as the limitation above. Additional
read-only `rg`, `sed`, `nl`, `sha256sum` and short Python metadata/hash/guard
comparisons produced `lock-audit.json`, `dependency-metadata.json`,
`guard-promotion.json`, `r6-readonly-audit.json` and `r6-log-audit.json`.

**23 focused tests passed, zero failures/errors/skips.** They cover the three
new adapter suites, Phase1 CorpusTests and the two new Phase3 boundary tests.
Process creation/system execution and performance flock primitives were
guarded, with zero calls. The independent race and repeat-report probes use
the same guards, with zero calls. Their actual reports and hashes are retained
in `adapter-probes.json` and its referenced directories. All executables in
the probes are synthetic bytes and were never executed.

The probe script's default outputs are single-use and refuse existing corpus
copies; preserve them and use a fresh cache-owned output layout for another
round. No native client/fixture, cargo test/build, Go test/build, profiler,
pressure, timing, soak, quiet-window token or performance lock was run or
acquired by this reviewer. Full successor process/retained replay, remaining
lane decisions, final Opus whole-phase review, and live/platform/resource/
performance acceptance remain parent-owned work.
