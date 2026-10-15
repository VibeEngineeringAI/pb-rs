# Phase 7 review: A1-04 launcher, narrow independent review (Opus r1)

Reviewer: Opus 5.5 (`claude-opus-5-5`), fresh independent narrow review with no nested agents.
Scope: only the new cache launcher package at
`/home/ajs/.cache/pb-phase7/under30-combined-a1-retry04-preparation`.
Review cache: `/home/ajs/.cache/pb-phase7/review-a1-retry04-launcher-opus-r1`.

**Verdict: ACCEPT the narrow launcher delta, with one Low finding and some informational notes. No blocking finding.**
This review gives no runtime, functional or performance acceptance. It is not a root disposition, quiet ACK or Phase 6 agreement.

## Identities verified

| Item | SHA-256 | Result |
|---|---|---|
| `launch.py` | `b2c86da6db2069aedc8cf401f56cb9e0909bd523b78d00e6ff806e65a519bddf` | matches the request, schema const and preparation receipt |
| `plan.json` | `0cf00d23b37d5eac408dc01120b6ab8e571b206b2a256a32afe2b8b0d8cd6b12` | matches |
| A1-03 `launch.py` (base of the patch) | `2cf2cb9d3c11e2508d7d5ddc8924effcfffb6296d39710e7206b774982495782` | — |
| `launcher-03-to-04.patch` | `56ed1802…e564` | equals the actual `difflib` unified diff of 03→04, apart from the header lines |
| `PROCESS_SCAN_POLICY` digest | `15410e8c51fbf0e7e19fbb2ab5b6f8ec38c7b03697598bba722202447914080c` | equals the schema const, `plan.process_scan_policy_sha256` and `process-scan-policy.json`. Also `plan.process_scan_policy` equals the launcher constant |
| source binding | `fe779004832665a7410e8534966d1a5afb7bffa083877127fbfe71f90b302c35` | unchanged; schema const matches |
| supervisor `bounded-execution.py` | `fc814f72…c0dc` | pinned in the plan; read-only check that it sets `PR_SET_CHILD_SUBREAPER` and emits `cleanup.recorded_unreaped` (lines 344–349, 671–686) |
| `HANDOFF.txt` | `3adb61c6…133e` | read in full |
| `test_launcher.py` / `pure-tests.json` | `be79c8e5…82bd` / `07f48a56…77bcc` | read; the author's receipt reports 41/41 and zero guard trips |

Independent read-only checks (`verify-static.json`, zero guard trips):

- **Plan pins:** all 654 plan identities rehash identical now, and `inputs-end.json` has 654 entries. Non-own identities are byte-equal to A1-03's (643). The 11 new pins are the A1-03 evidence (`plan`, `launch`, `definition`, `coordination/{receipt,root-cleanup,proc-access-characterization}.json`) plus A1-04's own `launch`, `guard`, `prepare`, `definition` and `process-scan-policy`. Inventories are equal.
- **Artifact hashes:** all 370 `SHA256SUMS` lines verify.
- **A1-03 preservation:** I rehashed all 1393 files of the A1-03 tree now; they equal `a1-03-preservation-after.json`, with nothing missing or extra.
- **Definition:** `definition.json` equals A1-03's once its output path is mapped back to A1-03. There are 12 case hashes.
- **Plan delta:** only these keys changed: `campaign`, `collector_argv`, `design_changes`, `identities`, `limitations`, `output`, `status`, `supervision` and `supervisor_argv_template`. Four keys were added: `process_scan_disposition_path`, `process_scan_policy`, `process_scan_policy_sha256` and `supersedes_refused_campaign`. None were removed.
  - `collector_argv` differs only at index 29 (the `-03` → `-04` output path).
  - `supervisor_argv_template` differs only at index 6 (supervision dir) and index 58 (output path). Lengths are equal.
- **Schedule unchanged:** `--limit 1800 --launch-by 30 --graceful-at 1710 --kill-at 1735 --reap-by 1755 --finalize-by 1770 --hard-exit-at 1780 --require-lock-free`. Coordinator `FINAL_BUDGET=1794`, `ALARM=1795`, launch20, ACK ≤60s and ≥1800s remaining are all unchanged; `freshness` is AST-identical.
- **Definition audit:** an AST comparison confirms `launcher-change-audit.json` exactly.
  - Changed: `known_processes`, `main`, `supervisor_checks`, `validate_ack`.
  - Added: `ProcView`, `capture_process_scan`, `process_error`, `process_scan_issues`, `scan_record`.
  - The 16 listed functions are byte-identical in AST, including `functional_checks`, `helper_checks`, `passive_lock`, `reacquire_lock`, `identity`, `check_before` and `freshness`.
- **No signal-sending code:** no `os.kill`, `killpg`, `pidfd_send_signal`, `.terminate(` or `.kill(` tokens appear in the launcher. `ProcView` has no kill or signal seam.
- **Nothing pending:** `quiet-ack.json`, `root-process-scan-disposition.json`, `coordination/` and both A1-04 output paths are absent. After my runs, the reviewed files rehash identical (`04-post-hashes.txt`).

## Delta assessment

1. **Independent channels.** `known_processes` attempts `exe` and `cmdline` separately for every same-UID PID. It also does so for PIDs whose UID read failed, which it counts as `owner_unknown_entries`. A failure on one channel never suppresses the other. Readable other-UID PIDs are skipped before any component read. A known match from either channel is retained as `{pid, matched_paths}` and refuses.
2. **Classification.** `PermissionError` or EACCES/EPERM maps to `permission_denied`. `FileNotFoundError`, `ProcessLookupError`, ENOENT and ESRCH map to `vanished`. Everything else maps to `read_error`. `process_scan_issues` refuses in all of these cases:
   - any match
   - `enumeration_completed` not true, or status `not_attempted`
   - any `scan` or `enumeration` stage error
   - any `read_error`
   - when the run is unreviewed

   Only `permission_denied` and `vanished` survive, and only after review.
3. **Sanitization.** Error records keep `type`, `errno` and `os.strerror(errno)`, never `str(exception)` or filenames. Records keep matched known paths only. My tests used private markers in exception text, filenames, an unrelated exe path and `--token=` argv, including through `main()` and everything written to `coordination/*.json`. No private text appeared anywhere.
4. **Incomplete coverage is never reported as empty.** Records start as `not_attempted` with `coverage_complete:false`. `coverage_complete` is true only when enumeration completed and there were zero component errors, and `global_scan_empty_claimed` is always false. A failure inside `capture_process_scan` yields `incomplete`, a sanitized `scan` stage error and a refusal. The backstop marker now best-effort includes both coverage records; it still exits 71 and is never accepted.
5. **The root disposition is required before any scan.** `validate_ack` runs before the first `capture_process_scan`. It requires all of the following:
   - `root_process_scan_policy_approved is True`
   - the disposition path equals the plan path and is absolute
   - the disposition file's hash equals the ACK record
   - exact `campaign`, `approved`, `policy_sha256` (computed from the launcher constant), `plan_sha256`, `launcher_sha256`, `source_binding_sha256`, the three acknowledgement flags and `owned_cleanup_authority`
   - `plan.process_scan_policy` equal to the constant
   - an absolute independent review path whose hash equals both the disposition and `ack.launcher_review_sha256`

   The disposition and review identities join the supervisor `--hash` inputs and the end-identity comparison. My `main()` test with a bad disposition showed that neither scan adapter was constructed and `Popen` was never called.
6. **Owned cleanup authority is unchanged, and slightly strengthened.**
   - `supervisor_checks` still requires actual exit 0 and verdict `ok`, `echild_verified is True` and `own_children_after == []`. It now also requires `recorded_unreaped == []`; a missing key also fails.
   - Root ECHILD (`waitpid(-1, WNOHANG)` → `ChildProcessError`) still gates `functional_pass` and lock reacquisition.
   - The supervisor is a child subreaper, so owned descendants cannot escape its ECHILD and ledger.
   - The supplemental scan's result enters `errors` but is never consulted for cleanup, and it creates no kill targets.
   - The one removed conjunct, `known_processes_after == []`, is replaced by an after-scan whose issues go into `errors`, and `functional_pass` requires empty `errors`. Mutant m9 confirms this path matters.
7. **Failure and finalization paths.** The `finally` order is unchanged: wait after failure, root ECHILD, raw and supervisor artifacts, end identities, inventory, reports, validation, then the new after-scan (only if reviewed), lock, final receipt. The deadline, launch20 check and actual-exit authority are unchanged.

## Tests and guard trips

Every reviewer run used `/usr/bin/python3 -I -B` with `rguard.py` installed before importing the launcher. The guard blocks fork, exec, spawn, `os.kill`, `killpg`, `waitpid`, `subprocess.*`, `pty.*`, sockets, `ctypes`, `flock` and `lockf`. It also blocks `/proc` listdir/scandir and reads of `/proc/*/{cmdline,exe,locks,mountinfo}`, and any write or mkdir outside my review cache. The launcher was loaded from its file via `spec_from_file_location` and `main()` was called only with patched seams: `ProcView` fake, mocked `Popen`/`waitpid`/`passive_lock`/`reacquire_lock`/`signal`/`utc`/`bt`, synthetic ACKs and dispositions under `fixtures/`. The functional check was isolated with `functional_checks → []`.

- **Static run** (`verify-static.json`): exit 0, trips `[]`.
- **First test run** (`real-result.json`): 48 tests, 1 error, retained. The error was in my own harness: a test name containing `/` broke a fixture `mkdir`. It was not a launcher defect.
- **Corrected run** (`real2-result.json`): **48/48 pass**, trips `[]`, children `''`. The 48 tests are:
  - 14 scan tests
  - 23 disposition and ACK binding tests: 15 disposition-field refusals, 5 ACK refusals, hash mismatch, missing file and plan-policy drift, plus a positive control
  - 11 `main()` tests:
    - Control: denied coverage after review passes, coverage stays incomplete, and the lock is reacquired once.
    - Known `cmdline` with denied `exe` before launch refuses with no `Popen`; so does known `exe` with denied `cmdline` and unknown owner.
    - An after-scan known match fails, and so does an after-scan EIO.
    - Supervisor `recorded_unreaped` set or key missing, `own_children_after` non-empty, and `echild_verified` false each fail.
    - Root non-ECHILD fails with no lock reacquire.
    - A bad disposition leads to no scan and no launch.
- **Mutants** (10 copies in `mutants/`, each run bounded at ≤60s; `mutants-report.txt`), with the number of tests that failed (killed the mutant):

  | Mutant | Tests that failed |
  |---|---|
  | m1: exe error skips cmdline | 6 |
  | m2: UID error skips PID | 3 |
  | m3: `read_error` allowed | 3 |
  | m4: matches ignored | 8 |
  | m5: ledger check removed | 2 |
  | m6: `known_matches_refuse` unbound | 3 |
  | m7: unrelated exe retained | 1 |
  | m8: review hash unbound | 1 |
  | m9: after-scan issues dropped | 2 |
  | m10: enumeration/scan stage allowed | 0 (survived) |

  m10 survived because it is an equivalent mutant: the independent `enumeration_completed is not True` check still refuses.
- **Probe** (`probe-truthy.json`): see L1.

The worst-case total of the bounded commands was well under 600s. Every Python child reported zero children. I did not rerun the author's suite in place, because it writes into the preparation directory.

## Findings

**L1 (Low, not blocking): disposition flags are compared by equality, not identity.**
The checks `disposition.get(k) != value` accept JSON `1` for `approved`, `acknowledges_incomplete_same_uid_host_scan`, `unknown_entries_are_not_kill_targets` and `known_matches_refuse` (confirmed by the probe). The schema's `const: true` and the ACK gates (`is not True`) are stricter. There is no safety weakening:
- the disposition is root-authored and hash-bound;
- known-match refusal is enforced by code, not by the flag.

Root should write literal `true`. If the launcher is edited for any other reason, use `is True` for the booleans.

**I1 (Info): the after-scan in `finally` is not wrapped in `stage()`.** `capture_process_scan` catches `Exception`, and `process_scan_issues` only reads keys that `process_error` and `scan_record` always produce. So in practice it cannot suppress the final receipt. A `BaseException` would require a new signal path; INT, TERM and HUP are non-raising.

**I2 (Info): exposure to a blocking `cmdline` read is marginally wider.** `cmdline` is now also read for PIDs whose `exe` read was denied or whose owner is unknown. A `/proc/<pid>/cmdline` read can block on a stuck target's mm lock, and Python's SIGALRM backstop cannot preempt a blocking syscall. This is covered by the disclosed no-D-state / running-scheduler condition, and A1-03 already read `cmdline` for most same-UID PIDs.

**I3 (Info): matching is exact only, unchanged from A1-03.** An `exe` path with ` (deleted)`, `--opt=/path` argv forms, or a non-dumpable known binary with a rewritten `argv[0]` would not match. In that last case both channels are blind and the PID shows up only as permission-denied coverage. This is the acknowledged limitation; the subreaper ledger and ECHILD remain the cleanup authority.

**I4 (Info): the review is bound by hash, not content.** The launcher checks the review only by path and hash, so whether the review accepts this exact launcher SHA is root's attestation. Once root binds this report's hash, the file must not change: it becomes an end-identity input, and any drift fails closed.

## Judgement on the root's proposed disposition

**Supportable.** The root may let permission-denied and vanished arbitrary host components stay UNKNOWN for this descriptive functional preflight, under these conditions:
- Never claim the host is globally empty (code: `global_scan_empty_claimed` is always false and coverage is incomplete).
- Create no kill targets from unknown entries (code: no signal path exists).
- Known matches from either channel refuse, both before and after the run.
- Unexpected read, enumeration or scan errors refuse.
- Owned cleanup stays with the supervisor: exit 0/ok, ECHILD, empty children, `recorded_unreaped == []`, plus root ECHILD and the lock identity and reacquire checks.

The delta does not weaken owned cleanup. Compared with A1-03, it narrows the refusal surface only for `permission_denied` and `vanished` component reads after explicit root review, and adds the ledger gate. The A1-03 behaviour of refusing on any `PermissionError` was also the cause of the 0.254s pre-supervisor refusal; that evidence is preserved byte-identically.

## Limitations of this review

- Static and pure-mock only. No live `/proc` scan, supervisor, collector, helper, PTY, lock, compiler or native run, and no Git process.
- No broad source or case suites were rerun; the source acceptance relies on the existing root gates.
- The supervisor was only grepped and read for the cleanup fields.
- Collector SIGINT interoperability, real non-dumpable process behaviour, races and TOCTOU are untested.
- Gives no functional or runtime acceptance, and supplies no root disposition, ACK or approval.
- Finished with zero owned processes.
