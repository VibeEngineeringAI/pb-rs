# Phase6 resource interpretation at R6

This is an interpretation addendum to the measurement plan, prompted by L-2
in the fresh whole-source Opus review. It changes no production or collector
code. R6 keeps the existing owned, reference-counted Arrow buffers; it does
not introduce an unconditional dictionary-body copy. The purpose is to measure
that implementation while accounting for its actual retained owners.

The worker owns the subscription decoder, schema, dictionaries, nested delta
pieces, renderer scratch and current staging buffer. One acknowledged event
bounds application items, not bytes. Records are released before the blocking
stdout write. Dictionary replacement or reconnect drops superseded decoder
references; nested delta pieces can grow without bound within a subscription.
These facts do not establish an RSS bound or allocator release timing.

When the protobuf body address is already 64-byte aligned, `ipc_body` preserves
its `Bytes` owner. Arrow's buffer slices can therefore keep the underlying
Tonic receive allocation alive, including allocation capacity outside the
visible body slice. A retained flat dictionary or nested-delta piece can pin
that backing allocation until its last reference is dropped. An unaligned body
is instead copied to Arrow-owned aligned storage. Decompression introduces
separate decoded allocations. Neither alignment frequency nor retained backing
allocation capacity has been measured here.

Consequences for the diagnostic sidecar and measurement reports:

- Dictionary/batch counters describe the buffer sizes exposed by Arrow, not
  complete backing allocations. They can omit transport capacity pinned by a
  slice, and can also count the same shared buffer under multiple owners.
- Do not sum owner maxima into unique heap, subtract them from RSS to assign
  the remainder, or infer a leak/release from their difference. Maxima are not
  simultaneous totals. There is no last-dictionary-owner/release counter.
- The 4 MiB receive-message cap bounds a message, not decompressed output,
  total dictionaries or the capacity of every backing allocation.
- RSS/FD/thread trends and allocation profiles must retain their own scopes.
  A stable visible dictionary length does not prove stable pinned capacity;
  an RSS plateau does not prove immediate buffer release. Profile findings
  must distinguish shared owners and allocator retention where evidence allows.

A comparison that attributes retained bytes to a particular owner requires
additional evidence; absent such evidence, report that attribution as unknown.
The planned dictionary grow/reset, compression and slow-sink campaigns are
still needed. The existing eight small R6 diagnostic checks prove output and
counter operation only, not sustained retention or release. Historical R4
large-pressure observations remain tied to their older binary.

Source basis: `src/tail/session.rs` (`ipc_body`, dictionary storage and resource
observations), tonic 0.14.6 `DecodeBuf::copy_to_bytes`, bytes' shared buffer
ownership, and arrow-ipc 60.0.0 buffer slicing. This is a source-derived
retention mechanism, not a new measured allocation result. See
[the independent review](phase6-whole-source-review-opus-r6.md) and
[the measurement plan](phase6-measurement-plan.md).
