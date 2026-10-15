# Phase 6 source notices

The [notice inventory](../licenses/phase6/NOTICE.txt) records the narrow adapted
compatibility algorithms identified in the independent [source-origin audit](phase6-source-attribution.md).
The [evidence manifest](evidence/phase6/source-notices.json) binds the copied texts
and primary sources. This is source attribution, not phase, packaging or release
acceptance. No Rust, Cargo, test, frozen binary or build input changed.

The inventory reuses the existing [Go BSD license](../src/LICENSE-Go.txt), adds
Go donor copyright years 2009/2010/2015, and maps individual date, decimal,
base64-error and dial algorithms to their exact files. It includes the full
[Apache-2.0 license](../licenses/phase6/Apache-2.0.txt), applicable
[Arrow notice](../licenses/phase6/ArrowGo-NOTICE.txt),
[Arrow source header](../licenses/phase6/ArrowGo-source-notice.txt) and actual
[gRPC notice](../licenses/phase6/grpc-go-NOTICE.txt).

The Arrow Go module archive lacks a root NOTICE. The corresponding
[Apache Arrow 13.0.0 tag](https://raw.githubusercontent.com/apache/arrow/apache-arrow-13.0.0/NOTICE.txt)
supplies it: both adapted Go source files downloaded from that tag match the
pinned local v13.0.0 module byte-for-byte. Only its applicable ASF notice is
included; this does not assert use of the unrelated components in the upstream
monorepo's notice. The full fetched notice and source hashes remain in the
provenance manifest/cache.

The original values implementer confirmed adapting goccy v0.10.0 string/rune
helpers and float layout after consulting its source. The transport implementer
confirmed adapting protobuf-go v1.35.1 prototext quoting and layout. These close
the audit's origin questions; their full [MIT](../licenses/phase6/goccy-go-json-MIT.txt)
and [BSD](../licenses/phase6/protobuf-go-BSD.txt) texts are retained separately.
The current Go standard-library license is not substituted for either one.

The values implementer wrote `civil_from_days` from memory of Howard Hinnant's
formula, with no recorded historical source version. Hinnant's
[primary publication](https://howardhinnant.github.io/date_algorithms.html#summary)
identifies the formulas as donated to the public domain. The notice records
that statement and algorithm attribution; it does not invent a consulted
version or borrow the unrelated C++ datetime license bundled with Arrow.

Future source and binary packaging must retain the applicable notices and
license texts. No packaging change or release has been made here. Phase 7's
separate notices remain independently scoped; neither inventory replaces a
complete dependency/distribution inventory. Rust runtime dependencies retain
their own licenses and are not represented as copied Go source by this list.
