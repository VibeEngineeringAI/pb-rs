# Phase 6 review wave, round 3: Flight transport (Opus)

This is an independent, scoped follow-up to
[round 1](phase6-review-wave-transport-opus.md) and
[round 2](phase6-review-wave-transport-opus-r2.md). It judges only two items
against the current [transport notes](phase6-transport-notes.md):

- the owner's fix for **R2-1** (an empty DATA frame dropped inside an open
  header block);
- the parent's decision on **R2-2** (padded empty DATA, now H2-CL-3).

Round 2 already closed F1, F3, F4, I1 and the unpadded F2(a) filter, and I did
not re-derive those. I edited no production source, tests, harness, notes or
shared evidence. I wrote only this report and the lane cache
`/home/ajs/.cache/pb-phase6/review-wave-transport-opus-r3/`. This report does
not replace the final whole-phase Opus review.

## Snapshot

Every hash was recomputed in this session unless the source column says
otherwise.

| Item | Value | Source |
| --- | --- | --- |
| Base | `4a812d5b9bc4c40882ab10897a73c3ce1e715ce0` | `git rev-parse HEAD` |
| `src/tail/transport.rs` | `f08a0c73afa112216365b4d39f9dc682a2c27a24b18b711a34c0111217a89845` | Measured at 10:22, 10:26 and 10:27 (before and after my test runs). Matches the brief. |
| `docs/phase6-transport-notes.md` | `a33cd6c57e8f6c15b3ed7fbc1a16fc753afdd0b602b0f7101926a5f3f54dc838` | Measured. Matches the brief. |
| `docs/phase6-h2-limits.md` (as read) | `72a083dfff8c72d4bbebc5bb6947da45e07160ccbfe1c8c0a859da76362d0a51` | Measured. It was modified at 10:23:30, during this review. The root says it is still updating the CL-3 wording. |
| `docs/evidence/phase6/focused-review-r6.json` | `c8beb3a912c8da0348e3a6a43800b3e973b1afc59851e4de5b26dbef1037c914` | Measured hash. I read the summaries only (below). |
| Locked dependency sources read | h2 0.4.20, hyper 1.12.0, x/net v0.30.0, grpc-go v1.65.0 | Cargo registry and `~/go/pkg/mod` |
| Toolchain | cargo/rustc 1.96.0 (2026-05-25) | Measured |

The legacy file `src/transport.rs` (`d4b9d521…`, 4 changed lines against base)
is not the Flight transport, and I did not review it.

## What I ran (bounded)

Claude Code's temporary directory is still at its quota (`EDQUOT`). I left
`/tmp` alone. Each command redirected its output into the lane cache, and I
read the files back from there. The harness reported an error after each
command, but the commands themselves completed.

```sh
C=/home/ajs/.cache/pb-phase6/review-wave-transport-opus-r3
cp -a --reflink=always /home/ajs/.cache/pb-phase6/target-values-wave $C/target   # private seed
export PATH=$HOME/.cargo/bin:$PATH CARGO_TARGET_DIR=$C/target
for i in 1 2 3; do cargo test -j 6 --lib tail::transport -- --test-threads=4; done
```

**Measured result:** `39 passed; 0 failed; 0 ignored; 257 filtered out` three
times, on source `f08a0c73…`. The log is `$C/t-transport.log` and its summary
is `$C/t-transport.sum`. These runs include `empty_data_filter_is_exact_and_bounded`,
`empty_data_inside_a_header_block_still_fails_in_h2`,
`padded_empty_data_keeps_h2_lifetime_cap` and
`empty_data_frames_do_not_reach_h2_limits`.

**Not run by me.** These are owner or root reports that I did not reproduce:

- the full lib run (295 + 1 ignored by the owner; 367 + 1 ignored by the root on
  R6);
- the diagnostics run (299 + 2 ignored by the owner; 373 + 2 ignored on R6);
- fmt and clippy;
- the disabled-fix negative controls;
- any Go, native-client, harness, bench, profiler or soak run.

After my test runs, the root asked me to stop running commands, so I did not
repeat the scratch negative control.

**Reported by the root (R6), not reproduced.** I read only the summary block of
`focused-review-r6.json`:

- `transport-wave`: 15 cases, 0 failures, 15 remaining. Report
  `tmp/harness-review-r6-focused-v1/transport-wave/report.json`, sha256
  `4a982d1c…`.
- `blocked-writer`: 6 cases. The `failures` field is `null`.
- `h2-limits`: 6 cases, 0 failures, 4 limits plus 2 remaining controls.

The root also reports exact output, error, physical connection and stream-reuse
matches across pinned Go A/A and R6 A/A, with 60 actual captures. I did not
re-hash any raw capture. The successor replay of the 291 original contracts is
still running.

## R2-1, an illegal interleave hidden by the filter: closed

**Source (`transport.rs:2274-2320`).**

- `header_block_open` is one `bool`.
- Any kept HEADERS (0x1) or PUSH_PROMISE (0x5) sets it to `!END_HEADERS`.
- A CONTINUATION (0x9) with END_HEADERS clears it.
- The drop predicate requires `!self.header_block_open`.
- Only DATA is ever dropped, so the tracker sees every HEADERS,
  PUSH_PROMISE and CONTINUATION header. The state is O(1) and needs no HPACK.

**Agreement with h2.** h2 0.4.20 `decode_frame` (`framed_read.rs:160-163`)
rejects any frame other than CONTINUATION while `partial` is set, with
GOAWAY(PROTOCOL_ERROR). I checked whether the filter's state can disagree with
`partial`:

- **Filter closed while h2 is open.** This would hide an interleave. It cannot
  happen while the connection survives:
  - The filter closes only on a CONTINUATION with END_HEADERS.
  - A CONTINUATION on another stream is a connection error in h2
    (`framed_read.rs:322`).
  - On the right stream, it ends h2's partial block too.
- **Filter open while h2 is closed.** This can happen, but only with a
  malformed first fragment:
  - `InvalidDependencyId` gives a stream reset without `partial`.
  - So does a `MalformedMessage` from `load_hpack` on the first fragment.
  - The filter then keeps every frame until an END_HEADERS CONTINUATION.
    That only restores the pre-filter h2 behavior, so it is the fail-safe
    direction. It is not a new divergence.

**Agreement with Go.** x/net v0.30.0 `checkFrameOrder` (`frame.go:543-577`)
rejects any frame other than CONTINUATION after HEADERS without END_HEADERS,
with a connection error. grpc-go then closes the transport, and `tail`
reconnects on Unavailable.

**Tests (source read; measured passing 3/3).**

- **`empty_data_filter_is_exact_and_bounded`:**
  - an open block with interleaved empties, kept both whole and at every
    split position of the combined sequence;
  - an empty frame after END_HEADERS, dropped again;
  - PUSH_PROMISE kept open;
  - a HEADERS with END_HEADERS, which opens no block;
  - first-frame strictness.
- **`empty_data_inside_a_header_block_still_fails_in_h2`** runs over a raw h2
  server. With the interleave the stream ends with a single Unavailable. The
  same block without the DATA frame streams the message and status 3.

**No new protocol error hidden.** Outside a header block, I checked every Go
path that sees an unpadded, zero-length, non-END_STREAM DATA frame on a nonzero
stream. Go treats all of them as no-ops:

- `parseDataFrame` validates only stream 0 and padding.
- `checkFrameOrder` is quiet outside a block.
- `handleData` (`http2_client.go:1140-1178`) only updates flow control, then
  returns for an unknown stream or for size 0.

Some of the dropped frames are ones h2 itself would reject:

- DATA on an idle or forgotten stream (`streams.rs` "stream not found",
  PROTOCOL_ERROR);
- DATA before the response headers;
- DATA after the stream has closed.

Dropping those moves Rust toward Go, not away from it (inferred from source; not
measured). Stream 0, PADDED, END_STREAM and the first frame are still never
dropped.

**Bounds unchanged.** My reading of `poll_read` (`:2476-2535`) and `filter()`
(`:2325-2351`) agrees with round 2:

- A header is withheld until all 9 bytes arrive.
- A straddling header is completed by reads of at most 8 bytes.
- Withheld bytes are released at EOF and dropped on a read error.
- A zero-capacity buffer touches nothing.
- At most 32 discard-only reads happen per poll, followed by a self-wake and
  `Pending`, and the reader never returns an empty `Ok` unless the inner reader
  hit EOF.
- `debug_assert_eq!(filled, 0)` holds on entry to `filter()`.

The open-block bool adds no reads and no buffering.

**Flow control.** Only zero-length unpadded frames are dropped, and they have no
flow-controlled length. Every frame with flow-control weight still reaches h2.

## R2-2 / H2-CL-3, padded empty DATA: accepted as an enumerated declared limit

The decision keeps h2's flow-control enforcement. It adds no padded-byte
dropping, no HPACK parser and no fork. I agree there is no minimal native drop:
a PADDED frame has flow-controlled length, so withholding it would leave h2's
window accounting out of step with the peer.

**Source.** h2 `Streams::recv_data` (`streams.rs:652-662`) calls
`record_data_frame(frame.payload().len())` for every DATA frame without
END_STREAM, using the unpadded length. `counts.rs:120-128` fails past
`MAX_RECV_EMPTY_DATA_FRAMES = 100`.

**Rust, measured by me and by the owner.**
`padded_empty_data_keeps_h2_lifetime_cap` checks two shapes: length 1 with pad
length 0, and length 2 with pad length 1. In both, 100 frames give
`[Ok, Ok, Err(3,"done")]`, and 101 frames give `[Ok, Err(Unavailable)]`.

**Go, source only.** `handleData` only returns the padding's window. No pinned-Go
process measurement exists, and the notes and limits record say so.

The existing malformed length-0 PADDED case stays in the filter test, next to
the two genuine forms (`:5014-5018`).

## Remaining objections (none blocking)

1. **The CL-3 wording in `phase6-h2-limits.md`** (as read, `72a083df…`; the root
   is updating it).
   - Line 30-31 says "This is currently a source-supported **Go** limitation."
     The limitation is Rust's. Go's behavior is the part that is
     source-supported.
   - Line 3 still says "These two limits", while CL-3 is described below it.
2. **The comment and the notes overstate x/net parity for PUSH_PROMISE.**
   - The comment at `transport.rs:2266-2268` and the F2(a) notes row say h2 "(like
     x/net's `checkFrameOrder`)" rejects DATA inside a HEADERS **or
     PUSH_PROMISE** block. But `checkFrameOrder` tracks only HEADERS and
     CONTINUATION.
   - For PUSH_PROMISE without END_HEADERS, Go accepts the DATA and fails at the
     following CONTINUATION ("unexpected CONTINUATION for stream N"). h2 fails
     at the DATA.
   - Both are connection errors with no data between them, so the outcome class
     is the same.
   - This is a wording nit only. The behavior is correct and conservative.
3. **Weak assertion in the interleave test.** The test asserts only the code,
   Unavailable. A regression that turned the library GOAWAY into a transparent
   retry would also end Unavailable, because the raw server closes its listener
   after one connection.
   - From source, no retry happens: `Wire::wrote` records only HEADERS stream
     ids, and library h2 errors are not `is_remote`.
   - Asserting one accepted connection would pin this down. Optional.
4. **The CL-3 notes row is narrower than the trigger.** It says the stream ends
   Unavailable "after a delivered message". By inference from `classify`, the
   same overflow before the first message also gives Unavailable and a
   reconnect. The row could say "at any point".

**Informational, outside this scope (pre-existing, not caused by the filter, not
a gate):**

- hyper 1.12.0 sets `enable_push(false)` (`proto/h2/client.rs:115`), so h2
  rejects any PUSH_PROMISE with GOAWAY(PROTOCOL_ERROR)
  (`recv.rs:1021-1023`, `ensure_can_reserve`).
- grpc-go 1.65 sends no `SETTINGS_ENABLE_PUSH`. I found no match in its transport
  package. Its reader's default case only logs unhandled frame types
  (`http2_client.go:1667-1668`).
- So a peer that pushes anyway would reconnect Rust while Go might keep
  streaming. This is inferred: I did not read x/net `parsePushPromise`, and
  nothing was measured.
- A compliant server does not push to Rust, because Rust advertised push
  disabled. The root may add a ledger line if it wants.

## Disposition

**R2-1:** closed. Source review, my 3× transport unit runs on `f08a0c73…`, and
the owner's reported disabled-fix controls (not reproduced) support it.

**R2-2 / H2-CL-3:**

- accepted as an enumerated, source-supported declared limit;
- the Rust 100/101 boundary is measured at unit level for both padded shapes;
- the Go side is source-only and not measured.

**Bounds:** the 32-read budget and the 9-byte header and payload handling are
unchanged. I found no newly hidden protocol error and no flow-control problem.

**Objections:** the wording items 1, 2 and 4 and the optional test hardening 3.
None blocks the scoped transport correction.

**Not accepted here:** CL-1 and CL-2 stay with their separate 6-case native
evidence, which I did not re-run. The tiny-frame budget and I2 stay unknown or
unproven, and they are not gates.

**Still required:**

- the successor replay of the 291 original contracts, still running;
- the final whole-phase Opus review.

The root's R6 results are recorded above as reported.
