# Phase 7 SQL byte identity — independent interim Sol review, round 2

Date: 2026-10-14. Worktree: `/home/ajs/.t3/worktrees/pb-rs/feat-phase7-interactive`.
Scope: the specific F1 resolution from `docs/phase7-review-byte-interim-sol-r1.md`,
its two reducer regressions, and the supplied SQL identity process evidence.

## Verdict

**F1 (medium) is resolved in the exact reviewed source. No new production finding
was identified in this bounded review.** All three dataset identity comparisons
use the raw sidecar when present, and schema effects still own their byte snapshots.
The centrally executed release3 process regression now supports the late-list
correction against the characterized Go behavior. Picker correction is supported
by source inspection and the reducer regression; this process case does not exercise
picker Enter.

This is an **interim Sol review**, not the required final Opus review. That review
remains required and is reported unavailable because of rate limits. Full successor
gates remain pending; this report makes no timing, whole-corpus or release-acceptance
claim. The original byte-emission assessment is outside this narrow re-review.

Reviewed `src/tui/sql.rs` SHA-256:
`a0f2adbe60c95af41b9b04892310c4dc625485c3c958224006d603c7a4a337c2`.
It matched both at inspection and after the release3 evidence arrived.

## Method and evidence labels

- **[source]** Independent inspection of current source, the supplied implementation
  patch, regression assertions and runner/embedded fixture. Behavior is inferred
  unless separately identified as recorded execution.
- **[static audit]** This reviewer checked hashes, reconstructed the prior SQL
  source in memory from the patch, and inspected stored JSON/raw captures. No
  fixture module was imported or executed.
- **[recorded execution]** Results supplied by the owners and read here. This
  reviewer did not execute those programs or tests.
- **[central report]** The user's validation/build attestation, distinct from the
  process evidence files and independent source inspection.

I ran no Rust builds/tests, candidate sessions, Go reproductions, timing campaigns,
or network requests. Only this report and my own cache audit were written:
`/home/ajs/.cache/pb-phase7/review-sql-identity-sol-r2/audit.json`.
I read the requested round-one report, `docs/phase7-sql.md`, all supplied
`sql-byte-identity*` evidence, `completion.json`, `implementation.patch`, and the
relevant SQL and embedded-fixture source. No other worktree was inspected.

## F1 — medium, resolved: replacement text no longer aliases raw dataset identity

**Original trigger [source/recorded execution]:** editing
`select 'Ⱥ' from 测试` and opening the columns drawer establishes raw identity
`B5 8B E8 AF 95` with replacement-text companion U+FFFD U+FFFD `试`. A late
dataset list containing `["logs", replacement-title]` must reject that raw name
and select `logs`. With the drawer open, columns empty and schema loading finished,
it must request `logs/schema`. Picker Enter on the valid replacement title must
instead treat it as a changed dataset even when raw-schema columns are populated.
Round one's String comparisons could retain the raw identity and wrong schema/state.

**Correction [source], concrete sites:**

- `src/tui/sql.rs:2608`: `dataset_identity_bytes(&self) -> &[u8]` borrows
  `dataset_raw.as_deref()` when present, otherwise the valid String's bytes.
  `:2615` retains the existing owned `dataset_bytes()` interface through `.to_vec()`.
  No borrowed identity escapes into an asynchronous effect.
- `:2726`: invalid-selection reset compares each title's bytes to that identity.
  On mismatch, the existing branch selects the first title, clears `dataset_raw`
  and sets index 0 (`:2731`). Its window-error caller at `:2788` receives the same
  correction without a new state policy.
- `:3194`: completion's filtered-list matching uses the same byte comparison,
  followed by the existing invalid-selection reset (`:3198`). The existing
  drawer/empty-columns/not-loading conditions remain at `:3199`; its effect at
  `:3205` owns the corrected selection's bytes.
- `:3245`: picker Enter computes `changed` using byte identity and uses it for
  both schema necessity and the existing changed-selection branch. That branch
  clears the sidecar, selected column, column filter and both column lists
  (`:3248`). The effect still owns `selected.into_bytes()` (`:3257`). Overlay
  dismissal, focus restoration and empty-editor placeholder behavior are unchanged.

**Nearby boundaries [source]:** I inspected all dataset/sidecar references and
assignments in this file; no additional title-versus-current-dataset equality was
missed. `toggle_columns_drawer` already starts with owned identity bytes (`:2657`)
and assigns the String/sidecar together after extraction (`:2662`). Initial
construction preserves the sidecar (`:2496`); valid-title assignment sites clear it.
Remaining `is_empty()` checks preserve the raw-name invariant: a malformed name's
replacement companion is nonempty. Initial sentinel checks against `—` and
`dataset` cannot alias malformed bytes through U+FFFD. Placeholder resolution at
`:2682` concerns SQL substitution and the existing JSON replacement contract,
not dataset-list identity. Picker filtering compares filter text; view selection
compares indices. Neither is another current-dataset identity comparison.

**Scope control [static audit/source]:** reversing the five SQL patch hunks in
memory exactly reconstructs round one's source SHA-256
`59b97435e2a54ab79a5bb7f8cd1c63203cbc9bffb98a362b2a1523735e5dedc5`.
The only production differences are the helper/owned wrapper and these three
comparisons; the remaining SQL hunk adds the two tests. Schema/dataset completions
remain unguarded (`:3156`), the existing window run-ID guard remains, and the picker
branch body is unchanged. No driver, timing, cancellation, stale-result suppression,
rendering or broader state-policy change appears in this resolution.

## Regression quality

**[source]** `late_dataset_list_does_not_alias_a_raw_name_to_its_replacement_title`
at `src/tui/sql.rs:5017` reaches raw extraction through Ctrl+L, verifies the first
raw schema effect, then delivers an empty schema completion before the two-title
dataset list. It checks index 0, selected `logs`, cleared sidecar, both title lists,
owned `logs` schema bytes and loading flags. The original implementation would
retain the replacement-text match and fail these consequential assertions.

`dataset_picker_replacement_title_changes_raw_identity_and_resets_columns` at
`:5060` supplies populated raw-schema columns, a selection and an active column
filter. It selects the valid replacement title through Ctrl+D/Down/Enter and
checks sidecar/filter/selection/list clearing, valid-title effect bytes, loading,
overlay and focus. At `:5102`, selecting the now-valid same title with populated
columns preserves those columns and emits no redundant schema effect. This control
guards against an unconditional changed-selection/fetch implementation.

These are meaningful reducer regressions, not helper-only byte-equality tests.
The completion case exercises both matching and reset in sequence; reset also
recomputes the index, so it does not independently isolate completion's earlier
matching expression. Direct inspection confirms that expression is corrected.
**[central report]** The user reports format/Clippy passing, 426 library tests
passing and 428 diagnostic tests passing on the same SQL source hash. I did not
rerun these checks or independently inspect their complete logs.

## Actual-process evidence and causal assessment

**[source]** `docs/evidence/phase7/sql-byte-identity-process.py:29` loads only the
repository's embedded `PROCESS_FIXTURE` from `tests/interactive_effects.rs`.
Its binary/role/output/hash parameters permit Go and candidate runs without builds
or private-cache Python imports. The home response is held (`:52`), the edited
query is acknowledged, and the drawer is closed/reopened. It waits for the raw
schema GET and empty fallback POST, then for a `no columns` emission after its
pre-reopen capture mark before releasing the list (`:76-86`). The inspected view
distinguishes `Show columns`, `loading columns...` and idle `no columns`
(`src/tui/sql.rs:3780`). Thus the checkpoint checks reducer completion rather than
merely request arrival. Existing fixture input pumping separates keys; completion
is not established by a guessed sleep.

The runner requires `logs/schema`, the unique `LOGS_SELECTED_SCHEMA` output marker,
the exact four-request sequence, exit 0, unchanged config, restored termios,
empty stderr and unchanged binary hash (`:87-99`). Raw captures and failure records
are retained. Its text observer strips CSI and replacement-decodes for marker
search; this is suitable for these ASCII state markers, not styled-frame parity.
The fixture's held response has a six-second timeout (`tests/interactive_effects.rs:211`);
the recorded follow-up GETs occur well inside that bound, so timeout release does
not explain these recorded outcomes. This observation is a causality check, not
a performance result.

**[recorded execution]** Both supplied Go records pass with binary hash
`fae8411febf8603e7f73803ac81ab6e7303dfe9305247f0be9f9767bea262314`
(recorded pin `07af390`). Their sequence is home GET, raw schema GET, empty fallback
POST, then `logs/schema` GET. The original record attributes Go identity comparisons
to `pkg/model/query.go:495-507`, `:873-895`, and `:940-958`; those references are
recorded/source evidence from the prior work, not newly executed Go checks here.

`sql-byte-identity-release2-pre-fix.json` and its failure wrapper preserve the
pre-fix binary hash
`46fe76b23feb5bdf97f997d115c0c1fdf70f73ca3de98be854ed080691ce2776`.
Its sequence repeats the raw schema GET and fallback POST after the list completes;
no `logs/schema` request appears. This positive wrong-identity trace supports F1,
beyond a missing-request timeout alone. The failed process was closed after that
assertion; graceful completion/restoration is not claimed for release2.

**[recorded execution, newly supplied]**
`docs/evidence/phase7/sql-byte-identity-release3-pass.json` records the durable
runner passing for immutable release3 normal binary SHA-256
`aceb58bd412e8fbf34f603d247c68e87e69897356cefd5aad19ab6a92484db28`.
It requests raw schema once plus fallback, then `logs/schema`, and emits
`LOGS_SELECTED_SCHEMA`. Exit 0, config unchanged, terminal restored, empty stderr
and unchanged binary are recorded. This supplies successor process support for
the specific late-list request-identity correction. **[central report]** The user
maps this build to the reviewed SQL hash; the process record itself does not contain
that SQL source hash, and this reviewer did not rebuild or execute the binary.

**[static audit]** All hashes listed in `completion.json` match. The durable Go
and release3 fixture hashes match the current embedded fixture/file. Their raw
stdout/stderr hashes match their records, and the stored output places idle
`no columns` before the unique selected-schema marker. Release3's wrapper matches
the cached record and its recorded SHA-256
`afc179550f448c2e1ec0fb58a083c6e2add9fa93a9f3f86f4bdf9b7d340f0032`.
All six release2 cache-evidence hashes listed in its failure wrapper also match.
Decoded fixture titles are actual U+FFFD U+FFFD `试`, and decoded fallback bodies
use that replacement string; Go/Rust JSON lexical differences were not treated
as raw dataset identity. Detailed fingerprints are retained in the cache audit.

## Remaining bounds

No further F1 production fix is requested by this review. Source correction,
meaningful reducer coverage and the supplied bounded successor process pass support
its resolution. Complete successor styled/retained/other gates and the required
final Opus review remain outstanding. The process regression covers late-list
selection; it does not establish process-level picker parity, arbitrary completion
ordering, full terminal-cell parity, timing behavior or whole-TUI acceptance.
