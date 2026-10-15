# Phase6 source-origin inventory

Offline review of the frozen R6 Phase6 worktree, 2026-10-14. This inventory
identifies narrow compatibility algorithms adapted from local upstream sources;
it does not classify the native session, connection or renderer ownership design
as a Go translation. It is documentation evidence, not production, performance,
phase or legal acceptance. No Phase7 UI material was reviewed.

Only this document and
[`source-attribution-inventory-r1`](/home/ajs/.cache/pb-phase6/source-attribution-inventory-r1)
were written. No production, Cargo, harness, test or license edits, builds,
native probes, timing campaigns, network requests or subagents were used.
The four audited files and all source entries in
[`candidate-review-r6.json`](evidence/phase6/candidate-review-r6.json) match that
manifest. Both frozen R6 executable hashes match it too; neither was executed.
Before/after hashes of 763 protected worktree files and the binaries are retained
in the cache evidence.

## Evidence boundary and existing convention

The audit reads `src/tail/{transport,values,validation,session}.rs`, the Phase6
transport/value notes, the whole-source R6 review and the existing notices.
Line numbers below refer to these R6 bytes, not an earlier scratch candidate.

**Confirmed algorithm adaptation** below means that comparison establishes a
distinctive upstream algorithm or decision tree in the Rust helper, beyond a
matching output or a comment naming Go. It does not establish literal copying,
the author's editing history, or a translation of the whole donor file.
**Source-guided behavior / origin unconfirmed** records a relevant donor and
matching rules where the available evidence does not establish code adaptation.
Native implementation evidence is likewise not a claim of a clean-room history.

The existing convention is explicit and narrow:
[`src/printable.rs`](../src/printable.rs), lines 1–5, identifies Go 1.25.4
`src/strconv/isprint.go`, derived Unicode data, Copyright 2013 The Go Authors,
the changed interval representation and
[`src/LICENSE-Go.txt`](../src/LICENSE-Go.txt).
[`rust-phase1.md`](rust-phase1.md), lines 58–62, repeats that attribution and
records retention with future binary packaging. That is precedent, not a new
Phase6 discovery or permission to expand this audit into other phases.

The root [`LICENSE`](../LICENSE) is GNU AGPL version 3. It does not contain the
upstream Apache or MIT texts below. `src/LICENSE-Go.txt` is byte-identical to the
local Go 1.25.4 `LICENSE`; its opening copyright is 2009. The Phase6 files name
many behavioral references but contain no upstream copyright/license mapping
equivalent to `printable.rs`. A module cache license is available evidence, not
a notice already included in this repository.

## Local pinned sources and actual license material

The following exact roots were inspected; newer installed module versions were
not substituted. `go.mod`/`go.sum` pin the Go modules, and `Cargo.lock` pins
arrow-ipc/arrow-flight 60.0.0. The Go toolchain directory's `VERSION` is
`go1.25.4`, matching the project's toolchain directive and R6 notes.

| Source | Local root | License/notice actually present |
| --- | --- | --- |
| Go standard library 1.25.4 | `/home/ajs/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.4.linux-amd64` | `LICENSE`: Go BSD 3-clause, Copyright 2009 The Go Authors; selected files also have their own copyright years. Existing `src/LICENSE-Go.txt` retains the same full license text. |
| ArrowGo v13.0.0 | `/home/ajs/go/pkg/mod/github.com/apache/arrow/go/v13@v13.0.0` | `LICENSE.txt`: Apache-2.0 followed by bundled third-party texts. Selected Go files carry the ASF contributor/Apache header. No separate `NOTICE`/`NOTICE.txt` exists at this module root, despite the file headers referring to NOTICE. |
| grpc-go v1.65.0 | `/home/ajs/go/pkg/mod/google.golang.org/grpc@v1.65.0` | `LICENSE`: Apache-2.0; `NOTICE.txt`: Copyright 2014 gRPC authors and Apache notice. `internal/status/status.go` has Copyright 2020 gRPC authors; `internal/transport/proxy.go` has Copyright 2017 gRPC authors. |
| goccy/go-json v0.10.0 | `/home/ajs/go/pkg/mod/github.com/goccy/go-json@v0.10.0` | `LICENSE`: MIT, Copyright (c) 2020 Masaaki Goshima. Its encoder source files inspected here have no separate copyright header. |
| protobuf-go v1.35.1 | `/home/ajs/go/pkg/mod/google.golang.org/protobuf@v1.35.1` | `LICENSE`: BSD 3-clause, Copyright (c) 2018 The Go Authors, naming Google Inc.; `internal/encoding/text/encode.go` has Copyright 2018 The Go Authors. This is not the identical text/year of `src/LICENSE-Go.txt`. |
| golang.org/x/net v0.30.0 | `/home/ajs/go/pkg/mod/golang.org/x/net@v0.30.0` | `LICENSE`: Go BSD text. `http2/errors.go`, `http2/frame.go` and `http/httpproxy/proxy.go` were inspected as behavioral references. |
| Rust arrow-ipc / arrow-flight 60.0.0 | `/home/ajs/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/arrow-{ipc,flight}-60.0.0` | Each has `LICENSE.txt` and `NOTICE.txt`. The latter starts Apache Arrow, Copyright 2016–2026 The Apache Software Foundation, and includes chronoutil/compact-thrift notices. These are Rust dependency notices, not a substitute for an absent ArrowGo-v13 NOTICE. |

Exact source and license copies, SHA-256 hashes and paths are retained in
[`evidence.json`](/home/ajs/.cache/pb-phase6/source-attribution-inventory-r1/evidence.json)
and its `local-sources/` directory. Numbered side-by-side excerpts are the
`A1`–`A8` and `C1`–`C5` text files in the same cache.

## Confirmed narrow algorithm adaptations

| ID / Rust location | Exact upstream file and lines | Evidence beyond output equality | Existing notice / gap |
| --- | --- | --- | --- |
| A1 — `values.rs:1505–1542`, `go_abs_time` / `push_abs_date` | Go 1.25.4 `src/time/time.go:538–560,638–749,766–806` | Rust combines Go's absolute-year offset `292277022400`, wrapping Unix-to-absolute seconds and the same optimized date decomposition: `4*days+3`, `cd | 3`, high/low halves of multiplication by `2939745`, then `2141*ayday+197913` and the January/February adjustment. This distinctive chain corresponds to Go's split/date/locabs implementation, not merely Gregorian output. | Full Go BSD text present. The Phase6 helper does not identify adaptation or retain the donor's Copyright 2009 The Go Authors header. Go itself cites Neri/Schneider for the optimization; no direct adaptation from that paper is established here. |
| A2 — `values.rs:1724–1746`, `go_pow10` | Go 1.25.4 `src/math/pow10.go:1–49` | Same three tables of 32, 10 and 11 powers; same `/32` and `%32` indexing; multiplication for positive and division for negative powers; same 308/−323 bounds. The purpose includes reproducing the table-product rounding artifacts. | Go BSD text present; source/version/adaptation mapping and donor Copyright 2009 header absent at this helper. |
| A3 — `values.rs:1806–1903`, `Decimal::{trim,round,format_g}` | Go 1.25.4 `src/math/big/decimal.go:202–274`; `src/math/big/ftoa.go:113–160,244–316` | Rust folds `shouldRoundUp`, `roundUp`, `roundDown` and trim into methods with the same exact-half test, previous-digit parity, backward scan over 9s, single-1 carry and decimal-point increment. `format_g` preserves the unusual `eprec > len && len >= point` adjustment followed by the exponent/precision branches and e/f formatting. This is a narrowed decimal formatter, not a translation of Go's bignum engine. | Go BSD text present. Both donor files have Copyright 2015 The Go Authors, which the existing 2009 license opening and printable-table 2013 notice do not identify. Specific adaptation/copyright mapping absent. |
| A4 — `transport.rs:1420–1472`, `go_base64_error` | Go 1.25.4 `src/encoding/base64/base64.go:312–380`, `decodeQuantum` | Rust removes decoding/output but preserves the quantum's validation tree and uncommon error indices: `si-j` on incomplete input, padding at positions 0/1, skipping CR/LF before a second `=`, reporting `len(src)` for missing padding and `si-1` for the wrong second padding byte, then trailing-garbage index. Actual bytes are decoded with the Rust base64 crate at lines 1319–1334. | Go BSD text present; donor Copyright 2009/source/adaptation mapping absent. Do not attribute the Rust base64 crate's implementation to Go. |
| A5 — `transport.rs:2748–2757`, `partial_deadline`; related scheduling at 2681–2745 | Go 1.25.4 `src/net/dial.go:269–289,585–710` | The deadline helper preserves equal division over remaining addresses followed by the same two-second minimum stolen from later addresses, capped by total time left. The surrounding race preserves Go's early fallback and primary-error choice but uses pinned Rust futures, `select` and Tokio cancellation rather than Go goroutines/channels. Confirmed algorithm reuse is narrow; no wholesale dialer translation is established. | Go BSD text present; donor Copyright 2010 The Go Authors/source/adaptation mapping absent. |
| A6 — `values.rs:1360–1371`, `go_f16_to_f32` | ArrowGo v13.0.0 `arrow/float16/float16.go:55–68` | Same bit extraction, exponent bias, zero/all-ones exceptions and output shifts. In particular both keep exponent zero for half subnormals rather than normalizing them. Reproducing this donor-specific conversion defect corroborates the structural match. | ASF/Apache-2.0 donor header and license not retained in the Phase6 file or repository notices. The Go BSD notice does not cover this ArrowGo adaptation. |
| A7 — `validation.rs:135–189`, `type_equal` | ArrowGo v13.0.0 `arrow/compare.go:41–176`, `TypeEqual` without `CheckMetadata` | Same selective recursive comparisons: List/FixedSizeList ignore element names but check nullability; Map checks item type/nullability while ignoring sorting; Struct/Union check field names/nullability/types and union mode/codes; run-end compares both types. LargeList and Dictionary fall through to deep equality rather than the List rule, matching the donor's distinctive case coverage. Rust's `GoType` and derived equality are its own representation. | ASF/Apache-2.0 donor header and license absent from repository notices. This does not classify the entire Rust schema validator as translated ArrowGo. |
| A8 — `transport.rs:1212–1238`, `final_status` | grpc-go v1.65.0 `internal/status/status.go:49–81`, `NewWithProto` | Same ordered fallbacks: ignore absent/repeated details; ignore protobuf decode failure; use proto code/message only when codes agree; otherwise construct Internal with the same three-part mismatch diagnostic. The nearby “Probably not a google.rpc.Status proto” comment also follows the donor; the complete branch correspondence supplies the algorithm evidence. Rust uses prost and its own reduced prototext rendering. | Apache-2.0 license, gRPC NOTICE and donor Copyright 2020 gRPC authors absent from repository notices. |

## Source-guided behavior and unresolved origins

These entries prevent broad attribution claims. A named Go routine, identical
error text, a standard constant or a successful compatibility corpus alone
does not establish that its implementation was copied.

- **goccy string/rune helpers (`values.rs:1227–1318`; C1).** The relevant exact
  donors are `internal/encoder/string.go:38–161` and
  `internal/encoder/decode_rune.go:1–137` in v0.10.0. The Rust four-state API,
  continuation checks and separator special cases closely resemble the latter,
  with its 256-byte dispatch table unfolded into ranges. Rust's string loop
  replaces the Go word-scan/unsafe fast path with a byte match and shared
  slice appends. UTF-8 bounds and escaping also follow common format rules;
  available evidence supports a source-structured adaptation candidate but
  does not resolve extraction versus a fresh implementation of those rules.
  The local MIT text is present in the module cache and absent in the repo.
  If the implementer's source record confirms adapting these helpers, retain
  that exact MIT copyright/permission text and map the modified helpers to it;
  do not treat the Go BSD text as the goccy notice.
- **Arrow schema diagnostics (`validation.rs:195–489`; C2).** Local donors are
  ArrowGo `arrow/ipc/metadata.go:176–210,574–610,664–867`,
  `arrow/internal/dictutils/dict.go:292–310`, and relevant type constructors.
  The child-first traversal, concrete-type-before-dictionary-index order,
  AddType conflict check and check/error ordering are source-guided. Rust
  builds a separate `GoType` model and uses `Fault::{Error,Panic}` instead of
  importing unchecked Go constructors. This is a compatibility-rule adapter;
  translation of those donor functions is not confirmed. A7 independently
  establishes the narrower ArrowGo algorithm adaptation and its notice gap.
- **grpc-go response/payload/CONNECT rules (`transport.rs:909–985,
  1156–1210,1338–1417,1477–1571,1622–1722,2790–3022`).** Local references are
  grpc-go `rpc_util.go` (`parser.recvMsg`, `checkRecvPayload`),
  `internal/transport/http2_client.go` (`operateHeaders`, `handleRSTStream`),
  `internal/transport/http_util.go:51–86,132–139,265–301`, and
  `internal/transport/proxy.go:75–113`. HTTP/CONNECT parser references are Go
  `src/net/http/response.go:154` (`ReadResponse`),
  `src/net/http/request.go:817` (`ParseHTTPVersion`),
  `src/net/textproto/reader.go:515` (`readMIMEHeader`) and
  `src/net/http/transfer.go:661` (`fixLength`). Rust substitutes incremental
  byte parsing, HeaderMap iteration, its own body forwarding state and a
  limited Content-Length drain for those implementations. Behavioral matches,
  preserved diagnostics and lookup tables establish compatibility targets;
  they do not by themselves establish translation of these source files.
  The HTTP status text table and x/net error names are standard compatibility
  data; their extraction history is unconfirmed. A8 separately confirms a
  grpc-go algorithm adaptation. The `grpc-go/1.65.0` CONNECT user-agent is a
  documented wire token, not copying evidence or a source notice.
- **protobuf diagnostic strings (`transport.rs:1283–1316`; C3).** The donor
  reference is protobuf-go v1.35.1
  `internal/encoding/text/encode.go:118–178` (`appendString`) and
  `prepareNext` later in that file. Rust's `utf8_chunks`, character matches
  and formatting replace its rune decoder, scanning and append primitives.
  The two-space separator is a measured binary-dependent behavior. Neither
  matching escape rules nor that separator confirms a code translation.
  No confirmed protobuf adaptation is charged here; if a source record
  establishes one, preserve its actual 2018 BSD notice, not only the existing
  2009/Google LLC standard-library license.
- **Hinnant date algorithm (`values.rs:1595–1608`; C4).** The helper names
  Hinnant and implements the characteristic `719468` epoch shift,
  400-year eras, `doe/1460 - doe/36524 + doe/146096` corrections and
  `(5*doy+2)/153` month decomposition. It is not Go 1.25.4's optimized
  absolute-date implementation (A1). No exact donor implementation/version
  is established by the inspected pinned sources. ArrowGo's `LICENSE.txt`
  contains MIT notices for vendored Howard Hinnant **C++ datetime files** at
  lines 524–557, but those files are absent from this Go module and that text
  cannot establish this Rust helper's origin. Specific donor/license remains
  unknown; obtain the implementer's source reference before choosing a notice.
- **Rust IPC safety walk (`validation.rs:584–903`; C5).** The relevant local
  Rust source is arrow-ipc 60.0.0 `src/reader.rs:88–239`
  (`RecordBatchDecoder::create_array`) and `:901–959`
  (`get_dictionary_values`). The validator follows the same IPC node/buffer
  consumption layout but computes safe sizes/alignment and returns its own
  exhaustion/invalid states instead of constructing arrays. Layout ordering
  follows IPC as well as the decoder; the comparison does not establish
  copied Rust implementation. Similar alignment behavior in
  `session.rs:968–977` and arrow-flight 60.0.0 `src/utils.rs:55–84` likewise
  does not establish translation. Dependency source/notices are preserved in
  cache as references, without adding a new copied-source finding.

## Native ownership and independently implemented matching

`session.rs:79–293,371–738,747–1044,1047–1183` uses a current-thread Tokio
driver, scoped blocking worker, acknowledged capacity-one handoff, Rust Arrow
decoding APIs, owned dictionary maps, validated rebinding, wire-schema
normalization and bounded staging retention. Go's corresponding observable
ordering and dictionary behavior are targets; this organization is native Rust.
It does not reproduce the ArrowGo reader's unsafe casts, panic recovery or
Go object ownership. Dictionary equality's A7 attribution stays distinct from
this service design.

`transport.rs:131–404,766–1147,1576–1687,1734–1815,2011–2635` implements owned
tonic/Tower channels, prost messages, hyper body adaptation, a retry state
machine, byte/frame observation, empty-DATA filtering, rustls TLS and Tokio I/O.
These bridge Rust dependency behavior to the pinned interface. No broad
grpc-go transport, HPACK implementation or Go networking stack translation was
established. A4/A5/A8 identify individual algorithms within that native design.

The renderer's schema plans, borrowed Arrow column views, gather/encode staging
and scratch ownership (`values.rs:29–1220`) differ from ArrowGo's
`RecordToJSON` row maps and individual `MarshalJSON` implementations. Nil slices,
duplicate columns, nested error precedence and encoded-key sorting are
behavioral contracts. `push_go_float` (`:1379–1489`) obtains shortest digits
from **zmij**, then lays them out; this is not a translated `strconv` shortest
float algorithm. Base64 output (`:1341–1354`) calls the Rust base64 crate.
The small owned limb arithmetic and quotient construction (`:1610–1721,
1749–1804`) emulate decimal precision/rounding behavior; no translation of Go's
`math/big` arithmetic engine is established. A2/A3 identify its narrower table
and decimal-formatting adaptations.

## Concrete follow-up for the coordinator

No remedy was applied to frozen R6 source or artifacts. The bounded findings
support a separate attribution-only patch:

1. Follow the existing printable-table convention for A1–A5: map each helper to
   Go 1.25.4 and the named donor file, state that its algorithm was adapted,
   retain the relevant 2009/2010/2015 Go Authors copyright notices and point to
   the existing unchanged `src/LICENSE-Go.txt`. A3's 2015 and A5's 2010 donor
   years are the concrete omissions beyond the existing 2009 license text.
2. For A6/A7, retain the selected ArrowGo files' ASF/Apache source notice and
   identify the changed Rust representation. Include the full Apache-2.0 text
   available at the start of ArrowGo v13 `LICENSE.txt`. Do not import its entire
   unrelated bundled third-party license inventory. The inspected Go module
   lacks the NOTICE its source headers mention; report that evidence limit
   rather than inventing a v13 notice from the newer Rust dependency.
3. For A8, retain the donor's Copyright 2020 gRPC authors, adaptation mapping,
   full Apache-2.0 text from grpc-go `LICENSE` (a shared identical text may be
   used) and its actual `NOTICE.txt`. The root AGPL file and Go BSD file do not
   supply these texts.

The goccy, protobuf and Hinnant rows remain source-origin questions, with their
locally available license texts distinguished from confirmed donors. They do
not justify attributing the whole renderer to those libraries or adding
unrelated notices. Existing future binary-packaging notice retention should
include the confirmed additions when the coordinator applies them; this audit
does not assess packaging or modify the R6 binaries.
