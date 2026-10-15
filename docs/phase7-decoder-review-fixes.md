# Phase 7 decoder fixes for final review F1/F3

- Source: `docs/phase7-review-final-r1.md` F1 (medium) and F3 (low).
- Files changed: `src/tui/sql_service.rs`, `src/tui/promql_service.rs`, and the shared value-only `src/tui/json.rs` module.
- Go reference: the pinned go1.25.4 `encoding/json` (`decode.go` `array` :541-597,
  `object` :604-834, `fold.go` :16-50), plus oracle `07af390`:
  - `pkg/model/query.go:2720`, the window `Decoder.Decode`;
  - `:3061-3066`, the schema struct;
  - `pkg/model/promql.go:87-103`.
- **Current status:** central round10 passes all 424 library tests, including the ten
  F1/F3 regressions; all-target Clippy passes. The 31 actual Go helper controls below
  ran separately. One new process fixture exited before its asserted schema request;
  its result row was correct and causal fixture synchronization is being corrected.
  Rebuilt release-candidate acceptance remains pending. The initial implementation
  agent could not run its shell due to EDQUOT; central execution is coordinator evidence.

## F1: duplicate-key reuse

All three decoders now use `ReusedSlice` (plus `set_len`), which the parent moved, along with
`json_field_is`, from `promql_service.rs` to `src/tui/json.rs`. Both adapters import it from
there, so SQL does not depend on the PromQL service. Its slots are values this document decoded earlier, kept apart from the active
length, so Rust capacity never decides what survives.

| Decoder | Go target | Behavior now |
|---|---|---|
| SQL window `fields` | `[]string` | `null` element keeps the prior or hidden string |
| SQL window `records` | `[]map[string]interface{}` | an object merges into the slot's map (each value is a fresh `interface{}`, last key wins); a `null` element resets the map |
| SQL schema `fields` | `[]struct{Name string}` | `null` or `{}` keeps the prior name, so the schema succeeds without the fallback POST |
| PromQL label list `data` | `[]string` | `null` element keeps the prior or hidden string |

These are unchanged:

- An array value of `[]` or `null` discards every slot, including hidden ones. A trailing `[]`
  is still empty.
- An absent field is still empty.
- First-error semantics: any type error fails the whole decode (schema: "absent", so the
  fallback is taken), even when a later duplicate is valid.
- The number and type error texts.

## F3: SimpleFold field names

`json::json_field_is(key, ascii_name)` matches Go's `foldName` for ASCII field names.
It maps U+017F to `s` and U+212A to `k`, then compares ASCII case-insensitively.

- No other rune's `SimpleFold` orbit contains ASCII. In particular, `ı`, `İ` and `ß` do not
  match, and there is no multi-rune folding.
- The fold is used for every struct field of these decoders:
  - SQL: `fields`, `records`, schema `fields` and `name`;
  - PromQL: `status`, `error`, `errorType`, `data`, `resultType`, `result`, `metric`, `value`
    and `values`.
- The auth decoders are unchanged (L-AUTH-7).

## Library regressions (central round10 passed)

In `sql_service.rs`:

- `duplicate_records_merge_into_existing_maps`
- `duplicate_fields_keep_prior_strings_under_null`
- `shorter_window_arrays_hide_slots_that_a_longer_one_reexposes`
- `empty_and_null_window_arrays_discard_hidden_slots`
- `duplicate_window_keys_do_not_hide_the_first_go_error`
- `window_field_names_fold_like_go_simple_fold`
- `arrow_schema_duplicate_fields_reuse_struct_slots`

In `promql_service.rs`:

- `duplicate_label_data_keeps_prior_strings_under_null`
- `empty_and_null_label_data_discard_hidden_strings`
- `field_names_fold_like_go_simple_fold`

All 10 new tests pass in the 424-test central library run. The existing PromQL `result` tests now use `set_len` and
the fold; their expectations are unchanged.

## Actual-Go control

`/home/ajs/.cache/pb-phase7/decoder-review-oracle/probe.go` is a standalone `go run` program.
It covers every unit case above, including the four F1 triggers and the F3 trigger, and the
expected Go output is commented beside each case.

The `QueryData` and `promqlLabelListResp` declarations in the probe were copied from the
contracts Rust records. The parent checked both against the oracle:

- `QueryData` matches exactly.
- `promqlLabelListResp.Error` carries `omitempty` in Go but not in the probe. That is
  decode-equivalent, because `omitempty` only affects encoding.

### Actual-Go result (pinned go1.25.4, run by the parent)

- Output: `/home/ajs/.cache/pb-phase7/decoder-review-oracle/results.txt`, 31 lines.
- **This is Go-only evidence.** It shows what Go does and that the expectations written in the
  Rust tests agree with it. It is independent of the subsequent central Rust library run; the Go output alone
  does not establish Rust behavior.

| Go line(s) | Case | Go result | Rust test expectation |
|---|---|---|---|
| 1 | window t1-merge (F1 trigger 1) | `records=[map[a:1 b:2]]` | agrees |
| 2 | window merge-replace | `map[a:[2] n:s]` | agrees (`Value("[2]")`, `Str("s")`) |
| 3 | window t2-null-string (F1 trigger 2) | `fields=["a" "x"]`, `records=[]` | agrees |
| 4 | window hidden | `["x" "b" "c" ""]`, `[map[a:1 d:4] map[b:2 e:5] map[]]` | agrees |
| 5–6 | window reset-[] / reset-null | `["" ""]`, `[map[] map[]]` | agrees |
| 7 | window trailing-empty | both empty | agrees (`QueryData::default()`) |
| 8 | window null-record | `[map[b:2]]` | agrees |
| 9–11 | window err-fields / err-record / err-overflow | the three error texts | the texts agree byte for byte |
| 12 | window fold-long-s (F3) | `["x"]`, `[map[a:1]]` | agrees |
| 13–15 | window fold-ignored (`ı`, `ß`, extra `ſ`) | empty, no error | agrees |
| 16 | schema t3-empty-struct (F1 trigger 3) | `["a"]`, no fallback | agrees (`Some([a])`) |
| 17 | schema hidden | `["c" "b"]` | agrees |
| 18–19 | schema reset-[] / reset-null | fallback | agrees (`None`) |
| 20 | schema err-first | error, fallback | agrees (`None`) |
| 21 | schema fold | `["a"]` | agrees |
| 22 | labels t4-null-string (F1 trigger 4) | `status="success"`, `data=["a"]` | agrees |
| 23 | labels hidden | `["x" "b" "c" ""]` | agrees |
| 24–25 | labels reset-[] / reset-null | `["" ""]` | agrees |
| 26 | labels trailing-empty | `[]` | agrees |
| 27 | labels err-first | the `promqlLabelListResp.data` error text | agrees byte for byte |
| 28 | labels fold | `status="success"` | agrees |
| 29 | result fold-status (F3 trigger) | `Status:error Error:x ErrorType:bad_data` | agrees |
| 30 | result fold-values | `ResultType:vector`, `Values:[[1 2]]` | agrees, with the limit below |
| 31 | fold kelvin | `"v"` | agrees (`json_field_is("\u{212A}ey","key")`) |

Notes on the comparison:

- **Line 30:** `%+v` does not show element types, so Go's output cannot confirm the test's
  `Number(1.0)` and `String("2")`. They follow from the body bytes `[1,"2"]`, because `[]any`
  decodes JSON numbers as float64 and strings as string.
- **Line 20:** Go's error text has an empty struct name (`Go struct field .fields.name`)
  because the struct is anonymous. Rust only produces "absent", which leads to the fallback,
  so this text is never observable.
- **Values printed but not asserted:** the `fields`/`records` printed beside an error on
  lines 9–11, line 1's `fields`, and the `status`/`data` on lines 23–28. They are
  informational. Go's partial values on error lines are never observable, because the decoder
  returns the error.

## Process fixtures

The effects owner (`phase7-sol-effects-r1`) received the four F1 trigger bodies and the F3
trigger, each with the Go-observable outcome. It owns the actual-process additions.

## Not changed

- U3, the `sql_service.rs` JoinPath comment: outside F1/F3, left for its owner.
