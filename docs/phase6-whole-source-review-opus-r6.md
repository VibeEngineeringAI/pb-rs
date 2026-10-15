# Phase6 whole-source review R6 — Opus 5.5

**Disposition: no blocker, high or medium production defect found in the
reviewed surfaces. Three new low findings: L-1, an undeclared diagnostic
difference, confirmed with a Go probe; L-2, a source-derived retention and
accounting caveat; L-3, a source-derived host-unescaping edge.** The
source/contract surface is ready to proceed to measurement, subject to the
conditions below. That is separate from whole-phase completion, which remains
open. This is a bounded review of the listed surfaces, not a proof of
correctness. It is also not performance, resource, soak, live-server or
platform acceptance.

## Scope and identities

Review date: 2026-10-14. This is a fresh round, not a continuation. Worktree:
`/home/ajs/.t3/worktrees/pb-rs/feat-phase6-flight-tail`. HEAD/base:
`4a812d5b9bc4c40882ab10897a73c3ce1e715ce0`, plus the dirty implementation.
Go oracle: `07af390ab09bda8ac9e427ffc6310077bace0125`. I read `cmd/tail.go`
with `git show` in `/home/ajs/Projects/pb-rs`. Arrow Go v13.0.0 and grpc-go
v1.65.0 were read from the module cache.

I computed these SHA256 values myself. They were identical at the start and end
of the review, and each is present in `candidate-review-r6.json` and
`central-review-r6-tests.json`:

| Input | SHA256 |
| --- | --- |
| `src/tail/transport.rs` | `f08a0c73afa112216365b4d39f9dc682a2c27a24b18b711a34c0111217a89845` |
| `src/tail/values.rs` | `e02d7e63aec6c9cf6f53f31a927a2cda5855a648adf3e7e05735d09d10354fd5` |
| `src/tail/session.rs` | `2d793026cf4e920a5d821a98855a32f7dee918fadc338e2368e0df5134d58a1e` |
| `src/tail/session/tests.rs` | `cb9bb45eb5a0a403e7ba469203884f3c5e18c17128132cccecf07d4a974fd076` |
| `src/tail/validation.rs` | `65ebd8fab90a99937b343955affe8deca50c702aa37f72edef6f980f9425d3f6` |
| `src/tail/mod.rs` | `0fd7102d65f863c4fd079c893c51627553e0cb15c3506dba37e42de62fa3c0c7` |
| `src/tail/resources.rs` | `8210efe689760620dd3536e2825a738acad28a51c5f17ac078933e3829d5473b` |
| `Cargo.toml` / `Cargo.lock` | `eb55b298c3158cf32c51a9876f53d3ebe220adbd7eaa51fd1af8fe4d1dc0c83c` / `aa35f338bd6b6b803c5e970f021d59f54d2e7435823f75a5baee018d7a1ebb44` |
| `src/lib.rs`, `src/transport.rs`, `src/diagnostics.rs`, `src/main.rs` | `638a9e0edce26ce60c8dba6398c1671c1da92cb2af6d5e29cca2657e623dea05`, `d4b9d521eb726bb24601749b50369f1708d0027b9699874b518b5e7faa49dd7c`, `2117c675cdead8fb14294def2dada41139bebc7fe889f5448b3b2d4816147189`, `68f04d772c2a51d13df15fec98a0a364f45cd83745f4d6a07990603439b34ab2` |

R6 binaries were not executed or rehashed by me. Their recorded identities are
standard `f1fb7a9e…5140f`, which is present in the candidate record, and Cloud
`36096c89…31af6`.

The root edited these documents during the review, as announced. They are not
frozen, and I do not rely on their wording:

| Document | SHA256 before | SHA256 after |
| --- | --- | --- |
| `docs/rust-phase6.md` | `fea171011e50e63a3ae7d736feacc05052577df63bdad6ddde6b132a51b2de90`, 367 lines at first count | `c8745eca17736204c9508d8493a9bcd325a34d80d4baaac58190b769a3839947` |
| `docs/phase6-values-notes.md` | `73945a87d1b0f138e138868ef95764597412fc62c5390c38fbf699df79f891f1` | `8a92653e63d7412b8daf29e68f763fa2adb1ec6960c110bad4e03227bb38826a` |
| `docs/phase6-transport-notes.md` | `a33cd6c57e8f6c15b3ed7fbc1a16fc753afdd0b602b0f7101926a5f3f54dc838` | `6fa4a50da8d1012cbffeb7d0c86e438e3923e6d2371e20b3908a001975f7ad49` |
| `docs/phase6-h2-limits.md` | `72a083dfff8c72d4bbebc5bb6947da45e07160ccbfe1c8c0a859da76362d0a51` | `679b6132c5b2b5321429093019ce8e5dad5d3db146e6afd6aec784af4b0a0f02` |
| `docs/phase6-integration-adapter-resolutions.md` | — | first seen at end: `894ab255d0e4b11f6feff84f3a71b4e8a4cf6540df70a8688149672a929152e4` |

Read in full: `src/tail/{mod,session,transport (non-test),validation,resources}.rs`,
`values.rs` lines 1–1240 and its function outline. Read selectively:
`session/tests.rs` index and the dictionary/depth tests. I also read the shared
diff (`lib.rs`, `transport.rs`, `diagnostics.rs`, `Cargo.toml`) and
`src/main.rs`. The plan and process documents I read were the governing plan,
`docs/rust-phase6.md` and the integration review. I used the original wave and
r1–r4 reviews only for targeted ledger checks.

Pinned Go sources read:

- Arrow Go v13: `ipc/reader.go` (`Read`, `next`, `getInitialDicts`,
  `readSchema`, `NewReaderFromMessageReader`), `ipc/file_reader.go`
  (`readDictionary`), `ipc/message.go` (`MessageType.String`) and
  `flight/record_batch_reader.go`.
- Dependency sources: tonic 0.14.6 `codec/buffer.rs` and `codec/decode.rs`,
  and arrow-ipc 60.0.0 `read_buffer`.

## Findings

### L-1 — low: a record batch before the dictionaries can reuse a dictionary ID

- **Location:** `session.rs:470–484`, in `Decoder::decode` while `pending > 0`.
- **Trigger:** a record batch arrives where an initial dictionary is expected,
  and its flatbuffer `length` equals a declared dictionary ID. The realistic
  form is a zero-row batch, whose length is omitted and reads as 0, with
  dictionary ID 0. Go reinterprets the record-batch table as a DictionaryBatch
  (`readDictionary`, `md.Data`/`md.Id`). Rust instead returns Go's overwritten
  `r.err` text.
- **Executed evidence (Go only):**
  - **Probe:** Go 1.25.4, Arrow v13. Bounded with `ulimit -v 4 GiB` and
    `timeout 20`, using a disk TMP. Source
    `$cache/goprobe/main.go` (`2db5ec26…6134`). It builds a schema of
    `d: dictionary<int32, utf8>` (ID 0) and `n: int64`, writes it with
    Arrow Go's own Flight writer, and replays it through a fake
    `DataStreamReader` using pinned tail's `Read`/`RecordToJSON`/`Println`
    loop.
  - **Go result:** both the "zero-row batch, then dictionary, then batch" and
    the "zero-row batch, then EOF" streams end with
    `fatal: arrow/ipc: unknown error while reading: arrow/ipc: field metadata out of bound`
    and no stdout row. The 3-row control
    (`no dictionary type found with id: 3`) matches the existing Rust unit test.
    Outputs: `probe.stdout` `939fe6af…eef7`, `probe.stderr` `87f98111…cf19`.
- **Rust (source-derived, not executed):** the same stream yields
  `arrow/ipc: IPC stream did not have the expected (1) dictionaries at the start of the stream`.
  It occurs at the same stage, with an identical status-line clear, no stdout
  and the same command-failure exit. Only the fatal stderr text differs.
- **Ledger:** this branch is undeclared. Grep finds only the unknown-ID test;
  the code comment calls it "characterized", but that holds only for the
  unknown-ID branch.
- **Recommendation:** declare it as a malformed-producer diagnostic residual in
  the decoder or values notes. Go's text depends on garbage flatbuffer
  reinterpretation and could vary with the schema, so do not emulate it.
  Optionally add this probe stream as a durable Go case.

### L-2 — low (source-derived): retained aligned dictionaries pin transport buffers and are under-counted

- **Mechanism:** `ipc_body` (`session.rs:968–974`) keeps the protobuf `Bytes`
  zero-copy whenever the body address is 64-byte aligned. Those bytes come from
  tonic's `DecodeBuf::copy_to_bytes`, which uses `BytesMut::split_to().freeze()`
  (`tonic-0.14.6/src/codec/buffer.rs:48–52`). They therefore share tonic's whole
  receive allocation; for a large message that is at least the message size,
  grown in 8 KiB increments.
- **Effect:** arrow-ipc `read_buffer` slices without copying, so a flat
  dictionary stored in `Decoder::dictionaries` or a nested-delta piece keeps
  that whole allocation alive until replacement or reconnect.
  `get_buffer_memory_size` in diagnostics (`session.rs:634–641`) reports only
  the slice length.
- **Bounds:** this is not accumulation. The effect is bounded by one allocation
  per retained aligned dictionary, plus each nested-delta piece, which already
  grows without bound. Whether a producer hits alignment is deterministic per
  framing layout and was not measured. Unaligned bodies are copied to their
  exact size and are unaffected.
- **Not executed.**
- **Recommendation (root's design choice):** either copy dictionary bodies
  unconditionally (the cost is negligible beside record batches), or list
  "transport receive buffers pinned by aligned dictionaries" among the retained
  owners and in resource interpretation. The current owner list and the
  sidecar under-describe this.

### L-3 — low/informational (source-derived): the Flight hostname is not percent-unescaped

- **Location:** `hostname()` (`transport.rs:1837–1846`) returns the raw
  authority host. Go's `url.Parse` unescapes the host (`parseHost` → `unescape`
  with `encodeHost`) before `Hostname()`, `ServerName` and `GrpcAddr` use it.
- **Reachability:** the shared HTTP discovery parses the profile URL with the
  `url` crate first, which rejects `%` hosts and IPv6 zones. The only reachable
  inputs are therefore those the URL crate accepts but Go unescapes, such as
  percent-encoded non-ASCII hosts. In that case Go dials and verifies `café…`,
  while Rust dials `caf%C3%A9…`. Both are expected to fail at DoGet, with
  different dial text.
- **Assessment:** this is a URL-parser divergence from an earlier phase, not a
  new tail lifecycle defect.
- **Recommendation:** note it in the transport notes, or reuse a Go-compatible
  host unescape if the HTTP layer later adopts one.

## Whole-lifecycle review against the oracle (read, not re-executed)

Each item was checked against pinned Go source. None produced an objection.

- **Stage order and status:**
  - Spinner before discovery, then stop/clear, then the error. Transport,
    credential and metadata errors come before `watching`.
  - The DoGet, reader-initialization and established-read error paths each
    clear first.
  - The `watching` line precedes the fixed 500 ms sleep.
  - These match `tail.go:75–150`.
- **Read classification:**
  - Go `Read` returns `io.EOF` only when `done && err == nil`. Established
    dictionary loops map EOF to nil.
  - `getInitialDicts` maps EOF to nil at `i==0` and to "ended without" when
    `i>0`, and passes statuses through unchanged.
  - Rust `receive`/`Decoder::end` reproduces this: Unavailable during the
    initial dictionaries reconnects, and EOF partway through them is fatal.
  - Initialization stays fatal for EOF and every status. Only the first empty
    header is skipped (`NewRecordReader`).
  - `MessageType.String` uses `EnumNamesMessageHeader` (`NONE`), which matches
    `header_name`.
- **One acknowledged application event:**
  - `try_send` on a capacity-one channel cannot fail while the previous event
    has been consumed and acknowledged; this includes the spinner path.
  - The worker is a plain scoped thread, so `blocking_send` is legal there.
  - The driver never blocks the runtime: the std mutexes are short, and
    `resolve` runs on its own thread.
  - Schema conversion, dictionary and record decoding, and rendering and writes
    all run on the worker. Batches are released before the blocking write, and
    the decoder is dropped at reconnect.
- **Output and signals:**
  - Println framing is the same in both modes.
  - Ordinary write errors are ignored. EPIPE on either stream raises SIGPIPE
    from the writing thread with default disposition.
  - An O_NONBLOCK pipe is polled on the worker. No `tokio::signal` is
    installed.
  - Go panic emulation is exit 2 with no batch output. A real Rust panic
    re-raises after join (crash, documented).
- **Transport integration:**
  - The credential/ticket snapshot is reused across reconnects.
  - The channel is replaced after GOAWAY, and the shared retry slot keeps F3.
  - The response/`Wire` binding is valid because exactly one DoGet is live:
    reconnect happens only after the previous stream ended, and fatal paths
    exit.
  - Lazy tonic `Reconnect` never fails `poll_ready`, so dial errors surface
    from `call` as Go-stage DoGet failures.
  - The receive cap, header list size and window/BDP settings match grpc-go
    1.65 defaults.
- **Ticket and metadata:** Go 1.22+ `\b`/`\f` escapes match serde_json's; the
  HTML and U+2028/2029 replacements are explicit. Metadata validation is
  Internal at DoGet. Raw cookie, lowercase `authorization` and IP-literal
  SNI/IP-SAN handling also match.
- **Shared diff:** narrow. There is one dispatch line, two `pub(crate)`
  exposures with unchanged bodies, and diagnostics fields gated by the feature.
  No production subprocess exists, so the Phase5 inherited-FD/`setrlimit`
  warning stays a future-subprocess obligation, not a tail defect.

## Prior-finding closure map (not re-proved)

| Item | Disposition here |
| --- | --- |
| IA1, W1/W2, V1/V2, transport F1–F4 | Current source implements the described corrections, and the code paths agree with the cited reviews. Their closures are by the fresh lane reviews and the R6 focused replay; I did not re-execute them. |
| Earlier GOAWAY-loss claim | The invalid-HPACK-helper retraction stands. Valid trailers are preserved (`finish`, `ended_cleanly`). |
| I1, map panic message | Rust produces one of the two possible Go messages. The scope is reserved as before; no waiver. |
| CW1–CW3 | Accepted in scope. Historical 291 captures are not retroactively port-certified. |
| Integration I-1/I-2 (adapter) | Adapter fixes are settled but their fresh Sol review is still active. **Not accepted here and not reviewed by me.** |
| Measurement r4 | Tooling is accepted in scope. No campaign. Not performance acceptance. |
| Explicit limitations (non-native endian, verifier depth 128, producer row-count/FSB/union rejections, header UTF-8/schema residuals, IA2 HTTP 103, H2-CL1/CL2, CL3, tiny frames) | These remain limitations, **not parity**. CL3 is a stated limit: padded empty DATA over h2's 100 cap gets GOAWAY(ENHANCE_YOUR_CALM) and Unavailable. That is fatal before the first schema message (reader initialization) and causes a reconnect on an established stream. Go accepts it. The Rust 100/101 boundary is unit-measured; Go's behavior is source-only. I consider the declared rejections acceptable as designed safety and capability boundaries, provided they stay outside parity counts. |

## Remaining unknowns

- Go behavior for L-1-style reinterpretation with other schemas.
- Whether producer framing commonly hits 64-byte body alignment (L-2).
- Stack use on the 2 MiB worker for schemas around 125 levels deep that reset
  nesting through dictionaries. Tests cover 64-level lists; the release path is
  untested here.
- Tiny-frame pressure.
- Live server delivery and authorization.
- macOS and other platforms.
- Every performance, RSS, soak and dictionary-growth number.

## Readiness decision

- **Ready for measurement:** yes, on source/contract grounds.
  - No new source change is required to begin the native campaign.
  - L-1 needs a ledger entry, and L-2 needs either a documentation entry or a
    copy policy before resource numbers are interpreted.
  - Measurement admission still needs the I-1/I-2 adapter fresh-review
    acceptance, the full R6 retained replay (phase 3 onward is active), and the
    campaign's own quiet window and lock.
- **Whole-phase completion:** not established. Outstanding work:
  - retained completion;
  - adapter review acceptance;
  - latency, throughput, RSS and soak campaigns;
  - dictionary-growth and slow-sink trends;
  - live, integration and platform tiers.

## Commands actually executed

All reviewer artifacts are in
`/home/ajs/.cache/pb-phase6/whole-source-review-opus-r6`, and bash output was
redirected there because the harness `/tmp` hit EDQUOT. Commands:

- read-only `sha256sum`, `wc`, `grep`, `sed` and `git diff 4a812d5`;
- `git show 07af390…:cmd/tail.go`;
- Python hash-membership checks on the R6 evidence;
- one Go build and run, using copies of the worktree's `go.mod`/`go.sum`:
  `GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=mod`,
  `TMPDIR`/`GOTMPDIR`/`GOCACHE` under the cache, with the Go 1.25.4 toolchain at
  `~/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64`. The probe
  binary is `20e8bad1…030a`.

No cargo build or test, Rust process, fixture, timing, profiler, pressure test,
lock or agent was used. I made no commits. The only worktree write is this
file.
