# Phase-two configuration, authentication and HTTP read contracts

`harness/phase2.py` is the fixed Go/Rust differential gate for phase two:
configuration interoperability, noninteractive profile operations, `logout
--yes`, `status` in all four authentication modes, `dataset list|info|schema` in
text and JSON, and cloud OAuth session refresh. The corpus
is code (`phase2.corpus()`), not a captured golden file. Each case pins what the
Go reference must do (exit, ordered requests, key output and resulting config)
before candidate parity counts. A reference regression or harness mistake
therefore fails the case instead of passing vacuously.

## Scope and case counts

The corpus defines 225 cases. **219 are the phase-two gate**, and all of them
must run: a cloud or non-root skip fails the gate unless `--allow-skips` is
passed explicitly.

Six cases are tagged `scope: future-terminal` and are **excluded** from the
default gate:

- `p2-logout-prompt-eof`, `p2-logout-prompt-no`, `p2-logout-prompt-yes` and
  `p2-logout-prompt-y-no-newline` are interactive `logout` confirmation cases.
- `p2-login-without-terminal` and `p2-login-without-terminal-no-config` are
  terminal `login` cases. Go needs `/dev/tty`.

This is a **scope correction, not a compatibility result**. The original
frozen corpus included these cases. However, `docs/rust-phase2.md` limits phase
two to noninteractive profiles and `logout --yes`. Interactive login and logout
are deferred to the terminal phase. The candidate has not been shown to match Go
for these six behaviors. Every compare report lists them under `scope` and in
`report.md` (`EXCLUDED as future terminal`).

The definitions are kept with their full Go expectations.
`--include-future-terminal` runs them on `compare` and `check-reference`. The
opt-in report is labeled `OPT-IN`, and those cases gate that run. Naming one
with `--case` without the flag is a setup error, so it is never silently dropped.

### Deferred boundary checks

Instead of Go parity, an unfiltered, non-self-check `compare` runs six
candidate-only boundary checks (`p2-boundary-*`), one per excluded case. Each uses
the same argv and stdin. Each check passes only if all of the following hold:

- The process exits 1, and stdout is empty: no prompt, no `Logout canceled`
  and no success text.
- The first stderr line starts with `Error: ` and names the command (`login` or
  `logout`). It also contains `is not implemented in this Rust phase`. Go's
  `/dev/tty` error is not required and does not satisfy the check.
- No HTTP request is made. Every profile URL and the cloud
  `orchestrator_url` point at a loopback fixture with no routes, so any probe,
  login or refresh attempt is recorded.
- The entire HOME tree is byte-identical afterwards: types, modes and raw
  digests. Even a rewrite to equal TOML fails.

A boundary failure fails the gate. Reports record boundary status as `run`,
`not run (case/group filter)` or `not applicable` (reference check or
self-check). The Go oracle test confirms that Go fails all six boundaries, so
the checks discriminate.

## Running

```sh
# Validate every expectation against Go alone (also checks repeat stability).
python3 harness/phase2.py check-reference --reference bin/pb-go \
  --cloud-reference bin/pb-go-cloud --out harness/results/phase2-go

# Go/Rust gate. Single commands compare Go vs Rust. Interop chains also run
# Go-write/Rust-read and Rust-write/Go-read and compare both with Go-only.
python3 harness/phase2.py compare --reference bin/pb-go --candidate target/release/pb \
  --cloud-reference bin/pb-go-cloud --cloud-candidate target/cloud/pb \
  --out harness/results/phase2-rust

# Harness self-validation: a byte-identical copy at a different path still
# executes the cross-order chains.
cp bin/pb-go bin/pb-go-copy
python3 harness/phase2.py compare --reference bin/pb-go --candidate bin/pb-go-copy \
  --self-check --out harness/results/phase2-self ...

# Fixed measurement subset for the native runner (statuses, dataset reads,
# profile add/list, logout --yes); every entry uses run.py's seeded profiles.
# compare also writes OUT/benchmark-suite.json from the full phase-two corpus,
# independent of --case/--group filters and of results.
python3 harness/phase2.py suite --out harness/results/phase2-suite.json
python3 harness/run.py run --suite harness/results/phase2-suite.json \
  --binary go=bin/pb-go --binary rust=target/release/pb --fixture bin/pb-fixture \
  --out harness/results/phase2-performance
```

`--case` and `--group` (for example `refresh`, `interop`, `config-read`) are
repeatable; filtered reports say `PARTIAL`. Exit 0 passes, 1 means a
compatibility, expectation, stability or identity failure, or a skipped case
without `--allow-skips`. Exit 2 is a setup error. Output directories must be new.

## Cloud token builds

Release builds inject `config.CloudOrchestratorAuthToken`. The standard harness
reference has no token, so the corpus checks the unconfigured paths there. A
401 still attempts a refresh and fails with `cloud orchestrator auth token is
not configured`. A second pair of builds covers successful refresh, refresh
failures and `cloud profile add`. These cases have `build: cloud`.

```sh
go build -trimpath -tags kqueue -ldflags '-s -w -X main.Version=v0.0.0-harness -X main.Commit=harness \
  -X github.com/parseablehq/pb/pkg/config.CloudOrchestratorAuthToken=harness-orchestrator-token' -o bin/pb-go-cloud .
```

The Rust candidate needs the equivalent build-time injection of the same
synthetic token. Without both cloud binaries, these cases are reported as
skips, and the gate is incomplete.

## Comparison rules

- Exact stdout/stderr bytes and termination per step. The only normalizations
  are the temporary HOME, the fixture origin and its `host:port`.
- Ordered requests use `run.request_contract`. They compare the raw
  escaped request path, query, selected auth/content-type/accept headers and JSON
  bodies. Go sends no `Accept` header on profile requests; an HTTP client
  default would be a difference.
- Configuration: every HOME entry's type and mode, plus parsed TOML for config
  files, before and after. Differing raw TOML bytes that parse to the same
  values are reported as **advisories** and do not gate. Go writes go-toml
  single-quoted literals; byte identity is an open decision, not a requirement
  of this gate. There are two exceptions where bytes do gate:
  - Malformed, unreadable and non-TOML files keep their raw digest in the
    comparison, because there is no parse to stand in for them.
  - If the reference leaves a file byte-identical, the candidate must too.
    Rewriting a config on a read or a failed operation drops comments and
    formatting even when the TOML is equal.

  Reference expectations of `unchanged` are byte-exact.
- One explicit exception: `stderr_unordered_lines` for the two
  `PB_DEBUG_HTTP_HEADERS` cases, because Go ranges over `http.Header`, a map.
  Line content still must match exactly.
- A candidate whose output contains the phase-one stub message fails explicitly.
- The Go reference runs twice by default (`--reference-runs`). Any instability
  fails the case rather than being normalized.

## Fixtures

Ordinary cases use the existing Go `pb-fixture` (global header check,
method/path routes). Refresh needs one path to answer 401 and then 200 depending
on the cookie. `phase2.py serve` is a loopback-only Python fixture that speaks
the same ready-line and `/__harness/trace` protocol as `run.Fixture`. It adds
per-route `headers` requirements and `times` limits, plus a `{{url}}` body
placeholder. Unmatched requests return 500 and are marked unexpected.

Configs, argv and env may contain `{{url}}`, which is replaced with the
fixture origin (or `http://127.0.0.1:1` when no server is wanted). All
credentials are synthetic and all non-loopback hosts end in `.invalid`.

## Go behaviors pinned by the corpus

These behaviors are easy to get wrong in a native port:

- `url.JoinPath` cleans dot segments and unescapes elements. `dataset info ../x`
  requests `/api/v1/x/...`, and `schema 'a b/c%2F'` requests `/a%20b/c//schema`.
  Spaces, `?`, `#` and UTF-8 are percent-escaped.
- `profile add/default/remove` JSON uses `MarshalIndent`, so HTML characters are
  escaped (`&`). Status, list and dataset JSON use `SetEscapeHTML(false)`.
  Schema JSON is re-encoded with sorted keys and `UseNumber` literals.
- Text-mode errors from `status` and `login` print Cobra usage after `Error:`.
  Dataset commands set `SilenceUsage`.
- `status` reads config itself. Malformed or unreadable config gives "no profile
  configured", an unknown default gives "no active profile", and both are JSON on
  stdout. Dataset commands wrap go-toml errors as `error initializing default
  profile: toml: ...`.
- `dataset list` validates `-o` after the request; `dataset schema` validates it
  first; `dataset info` validates it after all four requests.
- Config read is case-insensitive for keys. Legacy `api_key`/`token` fill an
  empty `apiKey` and are dropped (migrated) on the next write, together with
  unknown keys. Writes force `0600`, and a new directory is created with `0777 &^ umask`.
- `profile update` stores the raw URL; `profile add` stores `url.Parse(..).String()`
  (lowercased scheme only). `profile update` has no `-o` flag.
- Logout `--yes` picks the first sorted remaining profile. Without `--yes`, it
  reads one stdin line; EOF or no newline cancels.
- Refresh wraps errors twice (`cloud session refresh failed: cloud session
  refresh failed: 400 Bad Request: ...`). It persists before retrying and only
  updates the profile whose refresh token matches. A persist failure returns
  without retry. A retried 401 is reported normally.
- Go network/JSON error strings (`dial tcp ...: connect: connection refused`,
  `invalid character ...`, `json: cannot unmarshal ...`, go-toml messages) are
  compared exactly.

## Not covered here

These are outside this gate, apart from the deferred boundary checks above:
interactive login and logout confirmation, the profile/credential TUIs,
logout/remove default pickers, device-code login, redirects/TLS/proxies, the
User-Agent header, concurrent refresh, cross-origin refresh suppression,
Windows paths and dataset add/remove. PTY coverage belongs to the
interactive phase.
