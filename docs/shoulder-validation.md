# Shoulder observation validation

Validation performed on Linux on 2026-10-15. The command contract and operational
limits are in [shoulder.md](shoulder.md).

## Automated checks

| Check | Result |
|---|---|
| `cargo +1.96.0 test --all-targets --features diagnostics` | 820 passed, zero failed, ten existing opt-in tests ignored across 20 suites |
| `cargo +1.96.0 clippy --all-targets --all-features -- -D warnings` | Passed |
| `cargo +1.96.0 fmt --all -- --check` | Passed |
| Go repository verifiers (vet and pinned golangci-lint 2.11.4) | Passed, zero lint issues |
| Retained Go/Rust differential corpus | 2760/2760 passed; final compatibility-projection replay also 2760/2760 |
| Python harness tests | 33 of 34 modules passed in the full pinned-environment run; the remaining Go diagnostics module passed all 13 tests separately after selecting the real Go binary and pinned toolchain |

The full Python run exited nonzero because the environment's `go` shim had no
configured toolchain. The separate successful diagnostics run used Go 1.25.4.
The harness requires its pinned `pyte` and `wcwidth` dependencies; system Python
without those dependencies is insufficient. Local temporary storage was directed
to `target/tmp` because the user quota on `/tmp` was exhausted.

The compatibility harness explicitly removes only the new Rust command entries
from legacy help, completion, and catalog comparisons. It retains raw transcripts
and exact legacy behavior checks. Separate declaration and process tests verify
the added commands. The frozen differential binary preceded the final
feature-only fairness/observer fixes; the final Rust suite and live roundtrip
cover the integrated implementation. No startup performance claim is made.

## Live server roundtrip

A disposable Parseable 3.2.4 standalone container accepted seven JSONL events.
The final binary observed them in three acknowledged batches. Checks passed for
nested payload roundtrips, count and serialized-byte bounds, byte-for-byte
pending receipt replay, rejection of lowered replay budgets and incorrect
acknowledgments, and contiguous explicit completion. The test used isolated
datasets and fixture credentials.

This verifies the existing ingest/query APIs, including the server's Float64
sequence representation and normalized timestamps. It is not production-scale
load testing or validation of every Parseable deployment mode.

## Review decisions and remaining limits

GPT-6.1-Sol agents implemented and tested separate components; Claude Opus 5.5
agents reviewed architecture, ingestion, observation, and the final integration.
Review findings led to stable Windows identities, rejection of lossy paths,
exclusion of checkpoint files from broad globs, upload splitting, bounded state,
fair source scheduling, and retrying transient rotation races in follow mode.

The final review found no remaining silent-loss or false-completion blocker.
Two deliberate limits remain: oversized query responses require a lower
`--limit`, and invalid UTF-8 or oversized text lines stop collection rather than
being silently skipped. Append-only sources, one collector per session, and
retention long enough to preserve sequence continuity remain requirements.

Linux runtime validation is complete. The Windows collector identity code passed
an isolated cross-target compile check; full native Windows and macOS runtime
validation remains for platform CI. No exactly-once guarantee is made for
external agent actions.
