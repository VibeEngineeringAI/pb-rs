# PR #7: shell runtime validation and remaining differences

This change follows the [PR #6 compatibility checkpoint](compatibility-closure.md).
Its scope is generated completion scripts executed by real shells and a focused
[SQL/Flight difference audit](pr7-remaining-differences.md). A fresh 25-gate
replay validates the frozen executables built from this change.

## Required shell checks

The `Shell completion runtime` workflow runs Zsh, Fish and PowerShell on Linux
and PowerShell on Windows. Each job builds the Go oracle with Go 1.25.4 and
requires its selected runtimes, so a missing shell cannot produce a passing
runtime gate. Test transcripts are retained as workflow artifacts.

`PB_REQUIRE_COMPLETION_SHELLS=1` requires all three shells. A comma-separated
list, such as `PB_REQUIRE_COMPLETION_SHELLS=powershell`, requires only those
shells. Locally installed executables may be selected with `PB_COMPLETION_ZSH`,
`PB_COMPLETION_FISH` and `PB_COMPLETION_POWERSHELL`. Set `PB_GO_ORACLE` to the
pinned Go executable to compare the behavior of both generated scripts.

The Linux runtime matrix was executed with Zsh 5.9.2, Fish 4.9.2 and PowerShell
7.4.13: six scenarios per shell, two description modes, and both Rust and the
pinned Go executable (72 scenario executions). The scenarios cover flags,
commands, quoted preceding values, Unicode flag values, filename escaping and
an empty final argument with file completion disabled. The full completion test
target also checks generated-byte and protocol parity across the command tree.

The runtime matrix explicitly retains the inherited Fish quoted-value splitting
and PowerShell empty-result errors described in the
[completion adapter notes](../src/completion/README.md). Matching Go in those
cases is compatibility evidence, not a claim of correct shell behavior.
Native Windows execution is configured in CI; the local Linux runs do not
establish a Windows result.

## Validation of this change

- All **25 retained replay gates passed**, with unchanged input identities
  throughout the run. This includes all **688 SQL cases**, all deadline cases,
  Flight main/follow-up/decoder checks, device login, root help, 160 gated
  terminal cases, and all 59 interactive effects checks.
  The terminal suite also recorded 30 documentary cases outside parity counts;
  the light-theme logout replacement case retained its known Go profile-order
  screen difference. Flight main passed 219 payload/auth cases: 36 also passed
  strict wire parity, while 183 retained the exact user-agent exception. The
  two follow-up suites passed 16 and 56 payload/auth cases with that same
  exception. All three live Flight suites reported zero diagnostic differences.
- `cargo test --locked`: 741 passed, no failures, nine existing ignored tests.
  The replay separately runs the eight opt-in interactive effects tests; the
  external float corpus remains an opt-in check.
- `cargo fmt --check` and strict all-target, all-feature Clippy passed.
- Python harness: 964 tests across 33 isolated modules. The initial run passed
  936 and skipped 28 because of missing local binary paths or the resize opt-in.
  Targeted follow-ups supplied the retained Go/fixture/meter binaries and
  enabled the native resize control; all 28 then passed, with no remaining skips.
- The required Linux completion runtime tests passed against both executables.
  A deliberately missing required runtime failed, verifying CI cannot silently
  skip that shell.

The [test log inventory](evidence/pr7/test-logs.json) records retained compressed
logs and their uncompressed SHA-256 hashes. The
[build manifest](evidence/pr7/build-manifest.json) records the source identities,
toolchain, compile-time configuration, and hashes of the normal, Cloud and
device-login release executables. These are functional compatibility checks;
no performance claim is made.

The [replay manifest](evidence/pr7/replay.json) retains every gate command,
result and input hash. After it completed, only the phase-three and phase-six
contract prose was corrected to describe the current gate counts, promoted
interactive boundaries and removed diagnostic waiver. The
[documentation delta](evidence/pr7/post-replay-documentation.json) records both
hashes and points to the archived text used during replay. No executable source,
runner or pinned JSON expectation changed after the run.

## Subsequent work

- Extend interactive coverage to complete Windows console workflows and compare
  malformed UTF-8 terminal frames against Go. A PowerShell completion check does
  not establish Windows TUI coverage.
- Turn the existing self-hosted dataset, SQL, HTTP/2 and Flight probes into a
  repeatable integration suite. Track the no-table SQL server panic and Prism
  API-key authentication defect separately from client parity; their observed
  causes and working probes are recorded in the compatibility checkpoint.
- Package the full frozen replay and native platform checks into release
  verification with retained executable hashes and reports.
- Execute the opposite-endian Flight checks on native big-endian hardware.

Real Cloud browser authentication remains outside the self-hosted scope.
