# Compatibility closure — 2026-10-15

This checkpoint tracks work after PR #5 merged. Implementation, local validation,
native platform validation, and live service evidence are separate gates. Earlier
reports do not establish results for the binary being changed here.

## Work in progress

| Area | Completed work | Still required | Owner |
| --- | --- | --- | --- |
| Shell completion | Four generators/protocol implemented; broad Go comparisons and Bash round-trip pass; two review findings corrected and regression tests pass | Final release replay; native shell runtime coverage | Sol 6.1 |
| Windows compatibility | Native build and Clippy pass; CF_UNICODETEXT reader and owned-window fixture implemented; seven fake-API tests pass; correct Win32 unlock handling fixes the pinned Go bug intentionally | Native TUI run failed one Linux-specific errno assertion; correct it and run the real clipboard test, previously skipped after failure | Opus 5.5 + Sol 6.1 review |
| macOS compatibility | Native build and Clippy pass; select-based cancellation and high-descriptor fail-closed behavior implemented; Linux terminal tests pass | Native TUI run failed six tests: platform errno text, kernel-generated PENDIN, and terminal revoke after session-leader exit; corrections underway | Opus 5.5 + Sol 6.1 review |
| Non-UTF-8 arguments | Both Opus reviews cleared original blockers; 19 loopback network cases and two real PTY oracle tests pass; final saved-query time ordering finding fixed; raw SQL promoted to required gate with unchanged expectations | Final integrated replay; malformed-byte terminal dataset display is not established by request-only PTY checks | Sol 6.1 |
| Flight byte order | Implemented; 68 session tests pass; all three Opus findings corrected, including a real overlapping FlatBuffer regression; second Opus static review approved; unknown endian enum initialization also corrected and tested | Final release replay; native big-endian hardware remains untested | Sol 6.1 + Opus 5.5 review |
| Root help styling | Implemented; 48 exact pinned-Go cases pass across terminals, themes, disabled/forced colors and redirection | Repeat on final release binary | Primary agent |
| Final compatibility replay | Retained phase suites and pinned Go binaries located | Freeze final binary; replay CLI, Flight, interactive, and device-login suites together | Primary agent |
| Live self-hosted SQL/HTTP2 and Flight | Current debug binary successfully lists five datasets, executes SQL, and receives seven Flight records; HTTPS HTTP/2 SQL independently returns 200 | Repeat with frozen final release artifacts | Primary agent |
| Real Cloud browser authentication | User clarified self-hosted focus | Out of scope; retain Cloud device fixture replay separately | — |

The retained Python harness unit suites passed 956 tests across all 33 modules. This tests
the harness itself and does not substitute for replay against the final binaries.
The new replay coordinator additionally passes five tests proving failures,
partial runs, changed inputs and newly added source files cannot be reported as complete passes. Strict
Rust clippy checks pass for all targets and features in the integrated tree.

### Explicit implementation differences

Windows clipboard reads follow Win32's documented success convention: a final
`GlobalUnlock` returning zero with `NO_ERROR` is successful. The pinned Go
`atotto/clipboard` version incorrectly treats this as an error. The native fixture
requires the Rust read to return the actual text and only permits that precise
Go error difference. Native execution is still required.

Opposite-endian compressed buffers retain a bounded, exact positive decompressed
length check, stricter than Go's acceptance of trailing decompressed data. Empty
buffers, stored-uncompressed buffers and incomplete element tails follow Go.
No native big-endian hardware or performance result is claimed.

## Reproducible final replay

`harness/build_final.py --out <new-candidate-directory>` builds the normal,
public harness-token Cloud and loopback device-login configurations from the
same hashed sources. `build-manifest.json` binds each executable to its build
configuration. Any source change during the build invalidates that attempt.
The first attempt was invalidated while review corrections were landing and
is not acceptance evidence. Subsequent source corrections similarly supersede
any incomplete build; the final evidence names only a completed source manifest.

`harness/final_replay.py` requires that completed build manifest and explicit
paths to the retained reference binaries, HTTP fixture, Flight fixtures, meter
and follow-up reference manifests. It checks all input hashes before and after
every gate. CLI phases one through five, all twelve real deadline cases, three
Flight suites, fourteen retained decoder pairs, interactive dark/light captures,
device-login fixtures and 48 root-help cases share this input set. Independent
gates use separate output directories and can overlap their deadline waits.
Partial runs or changed inputs cannot report a complete pass.

## Evidence policy

Each completed validation will record its executable identity, command, outcome,
and retained evidence location. Unavailable native hosts, missing credentials,
failed checks, skipped cases, and known differences remain visible. Fixture
device login does not establish real Cloud browser authentication. Cross builds
do not establish native terminal or clipboard behavior.

## Self-hosted server diagnosis

The existing server runs on this workstation. Tailscale Serve forwards HTTPS
port 9443 to the HTTP listener at `127.0.0.1:8000`. `/about` reports v3.2.5,
standalone mode and Flight port 8011. No server restart or production config
change was needed for successful validation.

| Symptom | Confirmed cause | Working validation path |
| --- | --- | --- |
| `SELECT 1` returns 502 through Tailscale | Server worker panics at `src/handlers/http/query.rs:287`: indexes `table_name[0]` for a query with no tables; direct HTTP closes without a response | Query an existing dataset: `SELECT 1 AS probe FROM "codex-logs" LIMIT 1` returns `[{"probe":1}]` |
| API-key `dataset list` returns 500 | Prism `/api/prism/v1/home` reports `No authentication method supplied`; the same key succeeds on `/api/v1/logstream` | Isolated Basic-auth profile using the server's already configured credentials lists five datasets |
| HTTPS-profile Flight cannot reach this deployment | HTTP TLS terminates at Tailscale; native Flight listens on plaintext `127.0.0.1:8011` | Isolated local profile at `http://127.0.0.1:8000/`; native tail received seven valid JSON records, empty stderr, then SIGINT |

An independent authenticated HTTP/2 SQL probe through port 9443 returned 200,
TLS verification success and `[{"probe":1}]`. That probe records HTTP version
explicitly; the separate CLI SQL invocation establishes the client result.
Private captures/profiles are under `/home/ajs/.cache/pb-compat-final/`; no
credentials or log-row payloads are copied into this document or the wiki.
These are pre-final-build functional observations, not final release acceptance.
