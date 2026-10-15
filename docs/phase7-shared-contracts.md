# Phase 7 shared integration contracts

This is a local review handoff, not a merged revision. Phase 6 and phase 7 must
integrate sequentially and rerun retained gates on the combined revision before
any replacement release.

## Raw SQL dataset path

`src/api.rs` adds the crate-private function:

```rust
append_url_bytes(base: &str, paths: &[&[u8]]) -> Result<String, ApiError>
```

It shares `append_url_parts` with the existing `append_url_string`. Path elements
stay bytes until URL escaping. This is necessary because Go's SQL parser can use
lowercased byte offsets against the original SQL and produce an invalid-UTF8
dataset name. A Unicode placeholder is ambiguous with a literal input character.
The byte representation avoids that ambiguity. Existing string callers retain
the same validation, joining, escaped-path and error policies.

The join preserves base prefixes, queries and fragments; cleans literal slashes
and dot segments; retains valid raw escaped paths; percent-encodes invalid bytes;
and returns the original base when a newly introduced escape is malformed,
matching the existing Go JoinPath contract. Invalid escapes in the original
base still fail. No transport/auth policy changes are bundled with this accessor.

Pinned Go examples for base `http://h/prefix?x=1#f` and path
`api/v1/logstream/{dataset}/schema`:

| Dataset bytes (hex) | Result path |
| --- | --- |
| `b5 8b e8 af 95` | `/prefix/api/v1/logstream/%B5%8B%E8%AF%95/schema` |
| `b5 f4 80 82 b5 21` | `/prefix/api/v1/logstream/%B5%F4%80%82%B5%21/schema` |
| `ff 2f 2e 2e 2f 61 25 32 46 62 3f` | `/prefix/api/v1/logstream/a/b%3F/schema` |
| `ff 25 7a 7a` | original base, unchanged |

Query/fragment suffixes remain `?x=1#f`. Evidence:
[Go outputs](evidence/phase7/sql-byte-url-oracle-results.txt),
[probe source](evidence/phase7/sql-byte-url-oracle-main.go.txt), and
`schema_endpoint_escapes_raw_invalid_bytes_like_go` in `src/tui/sql_service.rs`.
These tests pass in the 408-test source checkpoint. Final candidate process and
retained replay remain required. Phase 6 acknowledged no `api.rs` overlap.

### Existing generic URL edge cases

Final review U1/U2 identified existing shared-joiner edges outside the new raw
schema element contract: an empty final element can preserve an extra slash,
and opaque/bad-element returns preserve the input base rather than Go URL
canonicalization. The coordinator compared the merged base implementation and
confirmed these branches predate phase7 (`combined.ends_with('/')` and verbatim
base returns). Phase7 schema callers always append `schema`; no new empty-final
element is introduced. They are retained as explicit generic URL limitations,
not silently repaired or covered by the byte-path parity claim. See the
[source review](phase7-review-final-r1.md) for exact examples and scope.

## Retained redirect deadline

The separate [47-line shared harness patch](evidence/phase7/p3-redirect-deadline/shared-harness.patch)
contains only six normalization lines and 41 focused test lines. Phase 6 verified
its SHA-256 and applied it independently. It does not include phase 7 guard
promotion. [The contract and review](phase7-retained-deadline-proposal.md) explain
its exact-case scope and preserved gates. Original failed reports are retained.

## Other integration boundaries

Phase 7 owns the new `src/tui/` workflow modules and `src/process.rs` inherited-FD
subprocess boundary. Shared dispatch/service adaptations appear in the final
branch diff. Phase 6 owns `src/tail/`, Flight dependencies, transport visibility
helpers and optional tail diagnostics. Preserve both phases' Cargo additions;
phase 6's direct prost/prost-types and flatbuffers declarations are intentional.
Do not merge another worktree's unfinished state. A final exact revision/diff and
combined validation manifest remain to be supplied after phase completion.
