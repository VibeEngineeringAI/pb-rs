# Phase 6 interim independent source audit

Reviewer: Sol 6.1, 2026-10-14. This is a new independent review of current
source; I did not implement Phase 6. **Source acceptance is blocked by IA-1.**
IA-2 also requires correction or an explicit, accurately described divergence
decision. No new high-severity issue was found in the inspected paths.

This report **does not satisfy the required final Opus review**. The task brief
reports that the newly delegated Opus review failed immediately with a provider
rate limit and inspected nothing. I did not attempt another provider or launch
agents. This Sol review is interim source evidence, not completed whole-phase
acceptance or a replacement for that requirement.

## Identity and scope

Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`, HEAD
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` plus dirty implementation. Go oracle:
`07af390ab09bda8ac9e427ffc6310077bace0125`. All production source and Cargo
hashes matched `docs/evidence/phase6/candidate-review-r4.json`, at the initial
snapshot and after the audit. No production source changed during this review.

Read the governing cached plan, `docs/rust-phase6.md`, transport/value notes,
transport reviews r1–r3, decoder reviews r1–r4, and Phase 5's FD/startup policy.
Traced all production tail modules, their test boundaries, CLI dispatch,
discovery/auth snapshot, raw-error rendering, process writer/signal behavior,
diagnostics integration and shared visibility changes. Inspected the narrow
Phase 1 guard promotion and Phase 3 timeout-normalization diff. I did not audit
the moving measurement collector or companion worktree.

Exact SHA-256 identities for the main reviewed files:

| File | SHA-256 |
| --- | --- |
| `Cargo.toml` | `eb55b298c3158cf32c51a9876f53d3ebe220adbd7eaa51fd1af8fe4d1dc0c83c` |
| `Cargo.lock` | `aa35f338bd6b6b803c5e970f021d59f54d2e7435823f75a5baee018d7a1ebb44` |
| `src/tail/mod.rs` | `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7` |
| `src/tail/session.rs` | `bc62d630339e2fd4423fc3813f8772490c5df55a60597ecad8d35874e293ca97` |
| `src/tail/session/tests.rs` | `9876ea971feeb56cf3761038dca364b1b983bd822a4904a85133796b5c9b02f4` |
| `src/tail/validation.rs` | `65ebd8fab90a99937b343955affe8deca50c702aa37f72edef6f980f9425d3f6` |
| `src/tail/values.rs` | `037183a7939fc11d3921c36d086fad023d9a6c0004839096b28ab140cd56ea6b` |
| `src/tail/resources.rs` | `8210efe689760620dd3536e2825a738acad28a51c5f17ac078933e3829d5473b` |
| `src/tail/transport.rs` | `77dd92bb0bf34c620bcb2ca259347f0d6b27d83d72593a76e912984858165240` |
| `src/lib.rs` | `638a9e0edce26ce60c8dba6398c1671c1da92cb2af6d5e29cca2657e623dea05` |
| `src/transport.rs` | `d4b9d521eb726bb24601749b50369f1708d0027b9699874b518b5e7faa49dd7c` |
| `src/diagnostics.rs` | `2117c675cdead8fb14294def2dada41139bebc7fe889f5448b3b2d4816147189` |
| `src/api.rs` | `3195a29ef68b485025eb540f3dba0ada718db4de39f01c3bbe2ecc38dfaafe92` |
| `src/auth.rs` | `fe0623db3305abefb2cf1689bc99dd9e7edad2de6eb3722b152572099a4f10ce` |
| `src/error.rs` | `e2ee65175d1764ab6e7c829b59e5a0bfac1275689b8a45cda5fe612ce59a75c0` |
| `src/main.rs` | `68f04d772c2a51d13df15fec98a0a364f45cd83745f4d6a07990603439b34ab2` |

The complete source hashes, documentation/plan hashes, artifact digests and
verification results are in
[`evidence.json`](/home/ajs/.cache/pb-phase6/interim-source-audit/evidence.json).
All scratch work stayed under `/home/ajs/.cache/pb-phase6/interim-source-audit`.
Only this report was written in the worktree. No commit, production/test edit,
timing campaign, profiler, live service access or `/tmp` modification occurred.

## Findings

### IA-1 — Medium: Go-equal shared dictionary types initialize but fail at the first record

**Independently reproduced through the production worker and immutable CLI.**

Relevant paths: `src/tail/validation.rs:154` (`type_equal`),
`src/tail/session.rs:535` (`read_dictionary`), `src/tail/session.rs:589`
(`wire_schema`), and `src/tail/session/tests.rs:2140`.

Give two fields dictionary ID 0, with value types `List<item: Int8>` and
`List<element: Int8>`, identical element nullability and identical values.
Send one dictionary containing `[7,8]`, then a one-row record selecting index
0 for both columns. This is a supported, Go-equal shared dictionary shape;
element field names do not participate in Go's `TypeEqual(List, List)`.

The recent pre-walk correctly accepts this schema, but `wire_schema` preserves
both different child names. Arrow IPC loads the single dictionary with the
first field's value type. Its record decoder then builds the second dictionary
column using the second field's expected type and the first dictionary's actual
type. Arrow Data's `get_valid_child_data` compares the entire `DataType`,
including the element field name, and rejects it.

Measured immutable CLI outcomes (`tail d -o json`, one discovery and one
physical DoGet each):

| Shape | Go stdout | Rust stdout | Terminal message |
| --- | --- | --- | --- |
| Both list element names `item` | `{"a":[7,8],"b":[7,8]}\n\n` | Same | Both reach fixture InvalidArgument `final` |
| Names `item` / `element` | Same row and separator | Empty | Rust fails at established read with `Child type mismatch`; Go reaches `final` |

Rust's exact failure is:

```text
arrow/ipc: unknown error while reading: Invalid argument error: Child type mismatch for Dictionary(Int32, List(Int8, field: 'element')). Expected List(Int8, field: 'element') but child data had List(Int8)
```

All processes exit 1 because the fixture deliberately ends with a fatal status;
that matching exit code does not make the missing Rust output compatible.
The separate pinned Go IPC probe also renders both streams and ends at EOF.

The R4 compatible-name test asserts only `initialize(...).is_ok()`. Its
schema-only Go probe therefore cannot catch this cross-module failure.
R4-2's conflicting-ID rejection is fixed, but compatible shared-ID support is
not fully verified by that correction.

**Required response:** reconcile each Go-equal shared dictionary value type
with the canonical wire type used to decode that ID, while preserving original
renderer semantics and normal Arrow validation. Add a complete dictionary plus
record regression, with the same-name control and exact output. Do not weaken
validation or label this a hostile-offset/safety rejection. This loss of a
Go-supported batch needs a fix or a separately authorized capability-limit
decision; this audit grants no waiver.

Evidence: [`targeted-probes.log`](/home/ajs/.cache/pb-phase6/interim-source-audit/logs/targeted-probes.log),
[`process-results.json`](/home/ajs/.cache/pb-phase6/interim-source-audit/process-results.json),
and the `streams/list_names_{same,differ}.*` artifacts. The scratch generator
is preserved as
[`probe-added-session.rs.txt`](/home/ajs/.cache/pb-phase6/interim-source-audit/probe-added-session.rs.txt).

### IA-2 — Low: initial 1xx divergence changes output, contrary to the existing ledger

**Independently reproduced through the native transport and immutable CLIs.**

Relevant paths: `src/tail/transport.rs:804` (`GoChannel::call`) and
`docs/phase6-transport-notes.md:90`. This corrects the disposition of transport
review r3 L2; it is not a claim that ordinary 200 responses are broken.

Send valid HTTP/2 HEADERS `:status=103`, followed by ordinary 200 gRPC response
headers, the same valid dictionary/record control, and final InvalidArgument
trailers. The probe uses valid HPACK literals, SETTINGS/ACK, and stream 1.

Go fails before schema initialization with:

```text
rpc error: code = Unknown desc = unexpected HTTP status code received from server: 103 (Early Hints)
```

Go stdout is empty. Rust prints `{"a":[7,8],"b":[7,8]}\n\n` and reaches the
fixture's final InvalidArgument. Both exit 1 only because this bounded fixture
supplies a later fatal status.

Locked h2 0.4.20 `recv.rs:350` skips informational response events when polling
the main response. Consequently `GoChannel`'s `response_headers` policy sees
only the later 200. The current ledger says 1xx is skipped, but then incorrectly
states “All are fatal in both clients; the texts differ.” This is an output and
stage/outcome difference. A later nonterminating valid stream would continue
in Rust where Go has already terminated (source inference, not a soak run).

**Required response:** correct the ledger and retain the bounded process case.
If restoring Go's rejection, use a maintained-stack informational-response
hook where feasible. Otherwise obtain an explicit decision for this full
outcome divergence; a text-only exception is insufficient. No parallel HPACK
parser is justified by this finding.

Evidence: the `info_list_names_same` entries in
[`process-results.json`](/home/ajs/.cache/pb-phase6/interim-source-audit/process-results.json),
[`process-probe.py`](/home/ajs/.cache/pb-phase6/interim-source-audit/process-probe.py),
and the scratch transport test preserved as
[`probe-added-transport.rs.txt`](/home/ajs/.cache/pb-phase6/interim-source-audit/probe-added-transport.rs.txt).

## Tests and probes actually executed

Rust 1.96.0, locked/offline dependencies, a scratch copy and separate
`CARGO_TARGET_DIR=.../interim-source-audit/target`. `TMPDIR`, `GOTMPDIR` and
`CLAUDE_CODE_TMPDIR` were all `.../interim-source-audit/tmp`.

- Unmodified snapshot: `cargo test --locked --offline --lib tail::`:
  **95 passed**, zero failed/ignored.
- Restored unmodified snapshot:
  `cargo test --locked --offline --features diagnostics --lib tail::`:
  **97 passed**, zero failed/ignored. This includes the isolated real Open
  handoff guard-lifetime test and the separate primitive overlap test.
- Scratch additions only: `cargo test --locked --offline --lib interim_ --
  --nocapture`: **2 passed**, reproducing IA-1 with two shapes and IA-2 with
  an empty Flight message at the transport boundary.
- Rebuilt the unchanged decoder oracle with
  `/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64/bin/go`,
  `GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly`, `build -trimpath`.
  Ran the two new IPC streams with 4 GiB virtual-memory, 16 MiB per-file
  output and 10-second limits: both render the expected row and end at EOF.
- **Six CLI executions:** three bounded raw-HTTP/2 shapes, each against frozen
  Go and standard Rust. All six finish within the five-second probe deadline;
  server assertions pass and no fixture thread remains. These are functional
  probes, not latency measurements. Exact binary hashes:
  Go `fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`,
  Rust `fe26926a98cba5e00774e26c5eab33c7c35ebd570ddaec7ceb38ddef663ecc43`.
- Independently verified all corpus file sizes/digests against manifests:
  values 44 cases/176 files; decoder r2 10/40; r3 30/120; r4 15/60.
  The Rust suite replays values/r2/r3 through the worker. I did not regenerate
  all existing Go corpora or rerun the parent's 15 R4 Go probes.
- Verified the four central-check log digests and three compatibility-report
  digests referenced by the review-r4 evidence. Those executions belong to
  the coordinator, not this review.

The first shell test attempt failed because Cargo was absent from `PATH`; the
retry used `/home/ajs/.cargo/bin`. The first scratch generator compile failed
on my incorrect `OffsetBuffer` constructor and was corrected in cache. Neither
was a production failure. No EDQUOT or provider-execution failure occurred in
this Sol audit.

I did **not** rerun the full all-target suites, fmt/clippy, 291-case process
captures, retained real-deadline suites, Cloud process probes, signal fixtures,
large dictionary functional workloads, performance campaigns or final review.

## Prior findings disposition and integration checks

“Verified” below means current source tracing plus the relevant passing tail
tests, unless a different evidence owner is named. It is bounded to those
cases, not a universal malformed-input or network proof.

| Prior item | Current disposition and evidence |
| --- | --- |
| Transport r1 T1/T2/T4/T5 | Loss adapter, TLS origin, 16 MiB header setting and explicit adaptive windows are present. Native loss/status tests pass; TLS process evidence remains coordinator-owned. Slow-sink BDP memory remains unmeasured. |
| Transport r1 T7–T12 and r2 CONNECT/order/EOF fixes | Incremental CONNECT validation, single-digit HTTP version, Content-Length rules, raw quoting, space-containing keys, mapped-loopback bypass and lowercased scheme are in current source; module tests pass. Chunked/close-delimited and URL-parsing limitations remain declared. |
| Transport r2 B1/M1–M3 | Connection/stream observation handles erased NO_ERROR terminals; non-gRPC HTTP responses are rejected before tonic decoding; remote message/binary status fields are parsed privately and sanitized. Tests cover raw bytes, malformed status/base64, HTTP mapping and unknown numeric codes. |
| Transport r3 B1 | Resolved for tested paths: local/library errors are not replaced by frame observations; `Terminal::Trailers` does not synthesize a missing-trailer status. Long valid HPACK trailers survive later GOAWAY/FIN; deliberately invalid HPACK yields transport loss. The former trailer-discard claim stays disproved. |
| Transport r3 M1–M3/L1/L5 | Partial prefixes/bodies at trailers/reset/DATA-end become Internal unexpected EOF; remote loss/drain remains Unavailable. Details protobuf code/message selection and mismatch are implemented. Exactly one unprocessed-refusal retry occurs before any complete message; second refusal and post-message refusal surface. Tests pass, including pre-header reset mapping and late-old-stream isolation. |
| Transport r3 L2 | **IA-2 remains open:** its 1xx disposition was incorrectly reduced to diagnostic text. Other declared malformed-header, repeated-bin ordering and TLS-text residues are not waived by this report. |
| Preface write/read race correction | Source holds the shared 20-second attempt deadline, absorbs later preface-stage write failures without queuing work, and preserves reset-on-read text. Deterministic socket-double tests pass; no new race/timing experiment was run. |
| Decoder r1/r2 panic and value corrections | Short validity/union buffers, alignment, signed lengths, negative FSB width, FSL multiplication and compressed prefixes are checked before trusting Arrow constructors. Production validation stays enabled. Union bitmap semantics, float16 subnormals, nested FSL panic wrapping and extreme time behavior pass the production corpus replay. |
| Decoder r3 FSL/union/nesting/run ends | Go-ordered pre-walk and error wrapping are present. FSL ≤ 0 and out-of-range union IDs fail initialization; array-load nesting limit is 64, verifier table depth 128. Wire run ends remain non-nullable. Relevant tests/corpora pass. |
| Decoder r3 row-count differences / r4 R4-8 | Five Go-renders/Rust-fatal cases remain explicit divergences; current tests now require the exact established-read error, addressing R4-8. Two further cases pin matching fatal stages with different text. |
| Decoder r4 R4-1 | Silent byte-swapped output is prevented by explicit non-native-endian rejection during initialization. It remains a **valid-stream capability limitation**: Go supports byte swapping. Current tests pass; parent verification records all 15 Go probes. Docs do not hide the limitation. |
| Decoder r4 R4-2 | Conflict detection compares original Go types before rewriting and registers children first; exact wrapping tests pass. **IA-1 exposes an unhandled compatible-type record path.** Do not mark the entire shared-ID contract closed based on schema-only probes. |
| Decoder r4 R4-3–R4-6 | Unknown union-mode panic text is fixed/tested. Empty/duplicate IDs, decimal metadata ranges, all flatbuffer header-string validation and absent schema fields remain explicitly ledgered. No universal producer parity is claimed. |
| Decoder r4 R4-7 | Named RAII guard spans accepted Open/Message delivery through ack consumption. The real worker gate asserts held current=1, then current=0/peak=1; it passes independently here. Primitive overlap reaches 2 separately. Docs distinguish the two and avoid an all-read-ahead claim. |

Cross-module ownership is coherent outside the findings: discovery finishes
before `ApiClient::profile()` snapshots refreshed credentials; subsequent
DoGets reuse ticket/auth without HTTP refresh. The scoped worker owns schema
initialization, decode/render/write; one acknowledged owned event precedes each
next application-message read. Blocking sink and schema-gate tests preserve
runtime progress. Decoder state is dropped on reconnect; decoded batches are
dropped before output write. Ordinary write errors retain Go's ignored-print
policy, while the existing process writer handles actual SIGPIPE. No new signal
handler, production subprocess, unsafe decode bypass or `catch_unwind` was
introduced. The diagnostics child process exists only in a test.

Raw fatal bytes survive the transport/session/CLI boundaries; Flight failures
remain COMMAND_FAILED. Default signal death is not presented as clean session
shutdown. The FD-startup policy is unchanged; Phase 6 has no production browser
or subprocess that would require restoring Go's original FD limit.

Staging above 4 MiB and aggregate list scratch above 1 MiB are released after
batches. They are retention policies, not peak caps. Channels bound items;
transport buffers, aligned-body aliases, dictionary deltas, decompression and
output expansion can exceed encoded size. Valid-looking huge compressed
prefixes can exhaust the allocator. Nothing inspected establishes constant
RSS or a whole-session byte bound.

## Compatibility evidence and acceptance boundary

The immutable coordinator evidence remains **291 remaining-contract passes:
36 strict, 255 native h2 UA differences, and one exact malformed-flatbuffer
diagnostic exception overlapping UA**. Main 219, framing/details/refusal 56
and trailer-close 16 are separate inventories. I verified report hashes,
not all those executions. The two findings above are outside that corpus;
its “zero unaccepted mismatches” describes its selected cases and cannot
establish absence of current-source mismatches.

The coordinator's 351 normal/357 diagnostics all-target tests, one ignored,
fmt and clippy with warnings denied have matching source/log identities.
These are distinct from my 95/97 tail-only executions. The R4 Go decoder
parent regeneration evidence has the expected 15-case/60-file manifest;
my checks verified committed file identities, not that regeneration again.

The review brief reports retained real-deadline phases 1–2 passed and phase 3
active. Complete retained acceptance is still pending; I did not inspect or
promote a later moving result. The shared Phase 3 normalization is narrowly
case/error-bound in source, and no Phase 7 feature guards were applied.

**Disposition:** the inspected ownership and previously corrected error
boundaries have substantial source and bounded regression evidence, but
unqualified final source acceptance is withheld for IA-1 and the unresolved
IA-2 disposition. Corrections need fresh focused evidence against the actual
new source hashes. Existing explicit capability/safety/diagnostic limitations
remain limitations; this report adds no implicit waiver.

Whole-phase acceptance additionally awaits complete retained gates,
independent review of settled measurement tooling and its H1–H5 corrections,
actual admitted performance/resource/soak campaigns and quiet-window controls,
resolution of final findings, and the required fresh final Opus source plus
integration/evidence review. Large compressed dictionary functional passes
are not resource acceptance. Successful real-server Flight authorization and
delivery, cross-platform packaging and minimum-OS/release readiness remain
separate unverified tiers. No continuity or exactly-once guarantee follows
from reconnect or these fixtures.
