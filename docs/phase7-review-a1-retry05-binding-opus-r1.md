# Phase 7 A1-05 prospective binding review (Opus, r1)

**Verdict: ACCEPT. Prospective source, binary, case and gate binding only.**

Scope: this is a narrow, independent, prospective binding review of the A1-05 preparation at
`/home/ajs/.cache/pb-phase7/under30-combined-a1-retry05-preparation`. It does not re-review the
full code and grants no functional, runtime, deadline or performance acceptance. It is not a quiet
ACK, a root disposition or a launch authorization. All checks were read-only file, AST and hash
work with Python `-I -B`. No cargo, CLI, helper, PTY, socket, probe, build, live /proc scan or
common lock was used. Scratch output is in `/home/ajs/.cache/pb-phase7/review-a1-retry05-binding-opus-r1/`
(`check.py`, `check.json`, `s1.txt`–`s3.txt`). No source or preparation file was modified. This
review started no processes that are still running.

## Pinned identities (independently recomputed)

| Item | SHA256 | Result |
|---|---|---|
| plan.json | `eab5c2d2aef687547cb89f99ff918af3064b53e82a383c5ce0ed10740428e959` | match |
| launch.py | `c4233db2074eb8bf906d38455902b1279dfd2238e41952b50a258b52ced17f44` | match |
| A1-04 launch.py (predecessor) | `b2c86da6db2069aedc8cf401f56cb9e0909bd523b78d00e6ff806e65a519bddf` | match to `plan.reviewed_launcher_predecessor` |
| A1-04 launcher review | `4dbdf0794db5128c441356287cb669cdc50bc4dd3c926045a42f7d145b08a57f` | match, in identities |
| promql shared-rows review | `b43992fe381f5b14c0ba0cfb65b8d4168e74c085bcd4fdedcc24080327abbd3b` | match, in identities |
| production disposition | `b00aaf8bb20457705e8c7f4a59a6309cb46138861596a9ec3b40aba314a0bbc9` | match, in identities |
| central report | `02077b05708dbb9fc588a9fc9ed82eb8b4d4fc4910cee42289cc000bd4a1b06b` | match, in identities |
| build receipt | `1a8537e78de1d508c1ae18fe3b1e815e453712d5907a0ab93fb234230c02ec1e` | match, in identities |
| release7 pb-normal (12512408 B) | `af3c0922a9065c909c7faf6718bda38f58224a872a8a93f724cd18fa529a366e` | match, in identities |
| release7 manifest | `16662d6d4484c9a87df8a5760e95ad08d021d240c39da27e8c380a9cafd6ff46` | match, in identities |
| live `src/tui/promql.rs` | `9e186f17c9f61d81ce1d16a9ca0847ad0d44294be0fa9386f8831b17f8a572d2` | match |
| source_binding (canonical JSON, sort_keys, compact) | `84cc179b9ba367d5fea07872e72a843874e657d6895851bfc9871cf9fb6754e3` | recomputed = claim |
| root_production_gate_record (canonical) | `d8d6c30bb191dc1a5335fa0890ca1b0a92b2ae536163444b9370a7dd88b9373f` | recomputed = disposition schema const |
| process_scan_policy (launcher `digest`) | `15410e8c51fbf0e7e19fbb2ab5b6f8ec38c7b03697598bba722202447914080c` | recomputed = plan/schema |

**687 plan identities were rehashed in full** (sha256, size, resolved path, symlink flag). All 687 pass and none are bad.

## Delta against accepted A1-04

- **Launcher:** both versions are 604 lines. The only changed lines are 19 and 20 (`OWN`, `CAMPAIGN`). All 34 function and class AST dumps are identical, and the only top-level statement differences are those two assignments. `validate_ack`, the disposition check, the scan channels and owned cleanup are byte-identical to the reviewed A1-04 launcher.
- **Collector argv:** both are 30 items. Only index 9 (candidate release6 to release7) and index 29 (output -04 to -05) differ. The supervisor template is 59 items, and only indices 6, 38 and 58 differ (supervision dir, candidate, output). These are the same substitutions.
- **definition.json** is byte-identical to A1-04 after reversing the two path substitutions. Each substituted string occurs exactly once, and no `release6` or `dark-04` remnant is left.
- **Cases, options and timing:** the 12 `case_hashes` and the `typed_options` are identical. `functional_roles` is 24. The typed options are 120x40 dark, observer primary, session 60 s, idle 2 s, budget 1620 s, max sessions 24, network 150 ms and metrics 256, with long idle off. The coordinator values are 1794 / 1795 / 1791 / 1790 / 20, unchanged.
- **Source groups:** `go` (87 files) and `harness` (65 files) are identical to A1-04. In `rust` (64 files) the only file difference is `src/tui/promql.rs`, which goes from `51387004…` to `9e186f17…`. All 216 group files match the live worktree. The plan's rust group equals `package-delta.new_production_group`, and the A1-04 group equals `old_production_group`.
- **Manifest:** `source_files` holds 64 entries and equals the new production group exactly. It names the candidate SHA. The build receipt reports exit 0, 64 verified sources, ECHILD, empty owned children, `recorded_unreaped []`, and the normal, cloud and login hashes.
- **Central report:** `fmt`, `clippy --all-targets -D warnings` and `--lib` all exited 0. Its `before` and `after` inputs are equal (77 files, including `promql.rs` = `9e186f17…`), and `source_unchanged` is true. The 562 passed / 1 ignored counts are as recorded in the plan gate record. I did not re-parse `lib.log`.
- **Identity set changes:** the identity set changes only for the release7 artifacts, the central r1 logs, the new review/disposition/build docs, the new prep scripts, the A1-04 plan/receipt (as historical inputs), and `tests/*`, `.git` HEAD/ref/commondir and `LICENSE-Go`. The only path whose content hash changed is `src/tui/promql.rs`. There is no implicit carry-over of old acceptance: the A1-04 actual failure (11/12 pairs, sustained timeout) stays a pinned historical input and is not relabeled.

## Findings

- **L1 (inherited, Low):** in `validate_ack`, the disposition fields are compared with `!=` (`launch.py:151`), so `1` or `1.0` would satisfy `approved`, `acknowledges_*`, `unknown_entries_*` and `known_matches_refuse`. The ACK root flags use `is not True` and are literal-safe. **Root must write literal JSON `true`** for every boolean in both schemas. The plan and handoff already disclose this.
- **I1 (Info, disclosed):** the new supplemental schema fields are not parsed by the launcher. These are `production_review_sha256`, `production_central_checks_sha256`, `candidate_manifest_sha256`, `production_gate_record_sha256`, `production_source_reviewed`, `production_central_checks_bound` and `release7_manifest_verified`. They are attestation-only. Executable enforcement of the new gates rests on two things: (a) all of the underlying evidence is in the 687 mandatory identities, which are rehashed before launch, under the supervisor and at the end; and (b) `root_review_approval is True` combined with the exact plan and source-binding hashes. This is stated in the plan `enforcement` field and in the schema description. That is acceptable, provided **root explicitly reads and attests the new production gates** and does not rely on the old approval semantics.
- **I2 (Info):** the retry05 `process-scan-policy.json` (`c3fbb9bd…`, a different serialization) is not in the identities, and the A1-04 copy was removed. This is harmless. The executable binding is the in-code `PROCESS_SCAN_POLICY`, which must equal `plan.process_scan_policy`, together with the disposition `policy_sha256` `15410e8c…` (verified). The file itself is informational.
- **I3 (Info):** the disposition schema accepts any absolute path for `independent_launcher_review`. Root should bind it to this document's path and final SHA256, and set `ack.launcher_review_sha256` to the same value.
- **I4 (Info):** the quiet-ACK schema keeps `fresh_review_sha256` equal to the historical step15 successor review `ea94f609…`, because the code requires equality with `successor_review`. This is unchanged and correct. It is not a slot for this review.

No blocking issues. **The binding is accepted** for root to author a fresh A1-05 disposition and obtain fresh own-lane, all-lane and Phase6 quiet. No ACK, disposition or launch is implied.
