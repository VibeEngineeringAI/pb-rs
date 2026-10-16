# Collect files and observe a session

The Rust CLI can collect appended files on machine A into Parseable, then return
small, acknowledged batches to an external agent on machine B. The agent decides
when to read, how to investigate, and whether to delegate to another agent. `pb`
does not run a model, start agents, or wrap the producer process.

```text
Machine A                         Parseable                 Machine B
script → JSONL/text files → pb ingest files → dataset → pb observe → external agent
                                                            ↓
                                                   separate agent/cursor
```

The [interactive design demo](pb-shoulder-demo.html) illustrates the earlier
proposal with simulated data. Its command examples and response shapes are
conceptual; this document describes the implemented CLI contract.

## Collect on machine A

Configure an active Parseable profile and a destination dataset first. Use a
session identifier unique to this run and one collector state file for the
entire session. Multiple include globs share that state and its event sequence.
Prefer keeping state outside the watched globs. The collector automatically
excludes its own state file, lock sidecar, and atomic-save temporary files.

```sh
pb ingest files './logs/**/*.jsonl' './workers/**/*.jsonl' \
  --dataset script_runs --session deploy-123 \
  --format jsonl --from beginning \
  --state-file collector.json --follow
```

Quote globs so `pb` can discover newly matching files on later scans. Add
`--exclude '**/archive/**'` to omit paths; exclusions can be repeated. The current
working directory, endpoint, cloud tenant/workspace, dataset, session, glob arguments, exclusions, and
format are bound to the state file. Resume with the same values.

`--format text` and `--from end` are the defaults. Text lines become
`{"message":"the original line"}` payloads. JSONL requires one JSON object per
line and preserves nested objects, arrays, numbers, booleans, and nulls in the
payload. Both formats require UTF-8 and emit only complete newline-terminated
lines; CRLF is accepted. An unfinished final line waits for its newline.

`--from end` skips existing bytes only when initializing the collector. Saved
offsets take precedence on restart, even if `--from` changes. Files discovered
after initialization start at the beginning so bytes written before discovery
are captured. Starting at EOF in the middle of a line discards the remainder of
that line when it is eventually terminated.

The default invalid JSONL policy is `--on-invalid error`, which stops with a
source path and byte offset. `--on-invalid skip` explicitly discards malformed
complete JSONL lines and reports the skipped count on stderr. Invalid UTF-8 text
and oversized lines still fail. `--max-line-bytes` defaults to 1 MiB and accepts
values up to 4 MiB. `--batch-size` defaults to 100 events, with a maximum of 1000;
the collector also bounds payload bytes per batch. `--poll-interval` defaults to
`1s` and accepts integer durations from `10ms` through `300s`.

Without `--follow`, the command drains available complete lines and exits, leaving
the session open. In follow mode, stop the writers, then stop the follower and
run the same command with `--complete` in place of `--follow`:

```sh
pb ingest files './logs/**/*.jsonl' './workers/**/*.jsonl' \
  --dataset script_runs --session deploy-123 \
  --format jsonl --from beginning \
  --state-file collector.json --complete
```

This drains remaining lines and uploads an explicit final marker. Completion
fails if a matching source still has an unfinished line. `--complete` and
`--follow` cannot be combined. A finalized state file cannot collect more events;
repeating `--complete` is safe. Start a new session with a new state file for the
next run. Do not reuse the same session with a fresh collector state file: a
session has one collector and one contiguous sequence.

## Delivery and source limits

Before sending HTTP, the collector durably stores the exact pending upload,
including its payload, event IDs, sequence numbers, and proposed source offsets.
It commits delivery offsets after a successful 2xx response. If a request fails
or its outcome is uncertain, rerun the same command with the same state file;
the saved pending batch is sent again before reading more source data. Once a
batch is in that outbox, later source changes cannot change the replay.

An accepted upload followed by a lost response or local crash can produce server
duplicates. Stable event IDs let observation deduplicate those retries. This is
at-least-once delivery for captured batches, not an exactly-once guarantee for
uploads or agent actions. There is one bounded pending batch rather than an
unbounded local spool. An upload failure exits the command, so the caller or
process supervisor must restart it to retry. HTTP 413 responses split a batch
into smaller requests with the same event identities. A permanently rejected
single event remains pending: correct the destination's schema, permissions, or
request-size limit before retrying. Do not delete the checkpoint to skip it.

Source files must be append-oriented. Identity, saved byte offsets, and byte
anchors detect ordinary replacement, rename rotation, and many truncations;
detected replacements start a new source generation. Keep rotated files under
matching include globs until their bytes have been captured. A file deleted or
renamed outside those globs before capture is unrecoverable. An identical
copytruncate-and-regrow can evade detection. Rewriting arbitrary source files or
configuration files is outside this contract. Observed concurrent rewrites fail
the scan without committing its offsets; stop the rewriting producer before
retrying in one-shot mode. Follow mode reports transient source changes and
rescans after the polling interval without advancing offsets. Sources rotate
through the batch's starting position so a continuously busy file does not
starve later paths. File paths must be valid UTF-8. Missing paths expire from the checkpoint
after two committed scans, so a file that disappears and later returns can be
ingested again under a new generation. Discovery races during rotation and
copying logs can also produce duplicate content with new event IDs.
There is currently no stdin input
or command/process-wrapping ingestion mode.

## Stored event envelope

The destination uses a fixed set of collection columns so heterogeneous payload
fields do not force a shared Parseable column type:

| Column | Meaning |
|---|---|
| `pb_session` | Unique run/session identifier supplied by the caller |
| `pb_collector` | UUID created with the collector state file |
| `pb_seq` | Contiguous session sequence starting at 1, including the final marker |
| `pb_event_id` | Stable event identity for upload replay and deduplication |
| `pb_kind` | `event` or `complete` |
| `pb_source` | Canonical source path; empty for the final marker |
| `pb_generation` | Source generation identity |
| `pb_source_seq` | Source line sequence within that generation |
| `pb_host` | Collector host label |
| `pb_collected_at` | Collection timestamp, preserved when retrying a batch |
| `pb_payload` | JSON string containing the original payload object |

For example, a JSONL payload `{"attempt":2,"result":{"ok":true}}` is stored as
the string `"{\"attempt\":2,\"result\":{\"ok\":true}}"` in `pb_payload`.
Observation decodes that string back into the payload object. User payload keys
remain inside that object, including any keys that start with `pb_`.

`pb_seq` orders the collector's captured events across all included files. It is
not a timestamp order across producers and is not a server resume cursor. Source
line sequences may have gaps when invalid JSONL lines are explicitly skipped;
the session sequence remains contiguous. Sequence values stay below the Float64
exact-integer boundary because Parseable may infer numeric columns as floats.
The server may normalize timestamps; observation uses sequence numbers for progress.

## Observe on machine B

Use a profile that can query the dataset. Give each independent observer its own
local cursor file; the parent directory must already exist.

```sh
pb observe script_runs --session deploy-123 \
  --cursor-file supervisor.cursor.json \
  --wait 5s --limit 100 --max-bytes 262144
```

Observation defaults to JSON. `--limit` accepts 1–1000 events. `--max-bytes`
accepts 1024–8388608 bytes and caps the entire JSON document, including its
envelope and receipt token, excluding the trailing newline. Text output is an
optional human-readable rendering; use JSON for an agent's byte budget.
A single event that cannot fit fails instead of silently dropping the event or
advancing the cursor. `--wait` bounds polling for visible events; a quiet or empty
result does not mean the producer is finished. Positive `--wait` durations bound
the first request and subsequent polling, up to five minutes. `--wait 0s` makes
one query with a ten-second request timeout. Queries use the server's `now`.
The HTTP response has a separate 32 MiB ceiling; lower `--limit` if a large
query response exceeds it. Start observation after the first upload has
established the collection columns in the destination dataset.

A read durably saves an exact pending response before printing it. Its
`next_cursor` is the receipt token to acknowledge after processing. Repeating a
read with the same cursor file before acknowledgment returns that same pending
response. New arrivals do not change it. If output is interrupted or the agent
crashes, reading again recovers the receipt. Lowering `--limit` or `--max-bytes`
below a pending receipt's size fails without changing it. Replay with sufficient
bounds, process it, and acknowledge it before using smaller bounds for the next batch.

Read responses include `status` (`ready`, `empty`, `gap`, or `complete`),
`events`, `count`, `bytes`, `from_seq`, `next_seq`, `has_more`,
`session_complete`, and `next_cursor`. Events contain `seq`, `event_id`, the
decoded `payload`, and source `collection` metadata. Only `ready` supplies a
receipt; empty and gap responses have `next_cursor: null`. A gap also identifies
`missing_seq`. Command errors use JSON by default; pass `-o json` explicitly to
also request structured preflight/parser errors.

```sh
pb observe script_runs --session deploy-123 \
  --cursor-file supervisor.cursor.json --ack 'TOKEN_FROM_NEXT_CURSOR'
```

`--ack` acknowledges the pending receipt and advances only the local observer
cursor. It does not fetch another batch or modify the dataset. Use a separate
read invocation for the next batch. Treat receipt tokens as opaque; do not edit
cursor files or construct tokens from sequence numbers. Do not combine `--ack`
with `--wait`, `--limit`, or `--max-bytes`. Repeating the latest acknowledgment is
safe until another read creates a new receipt.

The observer queries by session, deduplicates replayed rows, and returns only
contiguous sequence progress. If sequence 12 is visible before sequence 11,
progress stops at the gap until 11 becomes visible. Visibility can therefore lag
behind accepted uploads. The final marker establishes completion only when all
earlier sequence numbers have been observed; silence, timeout, or a marker beyond
a gap does not establish completion. A reported completion receipt must still
be processed and acknowledged before the observer is done. Rows removed by
retention or lost by the server may leave a permanent gap. There is no
server-wide cursor or multi-collector coordination. Conflicts below the
acknowledged sequence are not rechecked, and an acknowledged complete session
is not queried again. Keep each session exclusive to one collector.

## Drive the external agent loop

An agent runner should implement this sequence:

1. Call `pb observe` with a stable cursor path and bounded count/bytes.
2. Read and process the returned events. Keep any durable work record needed to
   recover its own actions, using event IDs as idempotency keys when appropriate.
3. If `next_cursor` is non-null, acknowledge it only after processing succeeds.
4. Stop after acknowledging a receipt that establishes session completion;
   otherwise read again. An empty open-session response is a polling result.

If processing fails, leave the receipt unacknowledged and retry the read. A crash
after an external action but before acknowledgment can repeat that action on
replay; the application must make its actions idempotent or record their results.
`pb` cannot make model decisions or external side effects exactly once.

To delegate an investigation, the external agent launches its chosen runner and
provides the dataset, session, task, and a different cursor path:

```sh
pb observe script_runs --session deploy-123 \
  --cursor-file specialist.cursor.json --wait 5s --limit 50 --max-bytes 131072
```

A fresh cursor starts that observer at the beginning of the session. The
specialist's acknowledgments do not advance the supervisor's cursor, remove
events, or claim work from other observers. Delegation is outside `pb`.

## Checkpoint handling and validation status

Collector and observer checkpoints use a persistent `.lock` sidecar to reject
concurrent use of the same path. Saves write a temporary file, sync it, and
atomically replace the checkpoint; on Unix the parent directory is also synced.
New checkpoint and lock files use mode `0600` on Unix. Symlink checkpoint/lock
targets, corrupt JSON, and state larger than 64 MiB are rejected. Keep these
files private and preserve them for restart. Removing the lock sidecar while a
process is running can defeat serialization; its continued presence after exit
is normal.

| Area | Progress | Validation |
|---|---|---|
| CLI declarations, help, and agent catalog | Complete | Four declaration tests; 2760 retained Go/Rust differential cases |
| Append collection, discovery, line parsing, and source checkpoints | Complete | 22 collector tests, including fairness, rotation, truncation, and retry classification |
| Durable upload outbox and explicit session finalization | Complete | 17 process tests cover this and the observer flow, including HTTP 413 splitting |
| Bounded reads, durable receipts, deduplication, gaps, and independent cursors | Complete | 22 observer tests, including HTTP deadlines, lowered replay budgets, and large responses |
| Checkpoint locking, permissions, and atomic replacement | Complete | Eight state tests |
| Parseable ingestion and observation | Verified on standalone 3.2.4 | Disposable-server roundtrip: seven events, three batches, replay, acknowledgments, and completion |
| Native platform coverage | Linux verified; Windows/macOS runtime remains open | Windows collector identity cross-check passed; native platform CI is still required |

The diagnostics-enabled Rust suite passes 820 tests with ten existing opt-in tests
ignored. Strict all-target/all-feature Clippy, rustfmt, and the Go verifier pass.
The Go compatibility comparison explicitly projects out only the new Rust
command entries while preserving raw transcripts and exact legacy comparisons.
The interactive HTML remains a conceptual demo; this document is the command contract.
See [the validation record](shoulder-validation.md) for environment details,
review decisions, and the limits of these checks.
