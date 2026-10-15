# Shared-command RSS investigation: static binary inventory

The primary capture reported a 1.010–1.746 MiB median peak-RSS increase against the accepted Phase5 binary. Fresh isolated confirmation captures are retained and under independent audit. A root descriptive check sees the increase persist in their medians (0.980–1.676 MiB); this is not the independent confidence-interval disposition. No samples or original reports were changed.

The following is a pure file-byte ELF inventory, with no executable run, profiler, loader observation or new measurement. All three complete binary hashes matched the preserved baselines and were unchanged afterward.

| Binary | File bytes | .text bytes | .rodata bytes | .data.rel.ro bytes | RELATIVE target 4 KiB pages |
| --- | ---: | ---: | ---: | ---: | ---: |
| phase5 | 7617800 | 5631238 | 864576 | 180264 | 52 |
| merged_phase6 | 10747592 | 8118582 | 955172 | 250888 | 70 |
| combined | 12513760 | 9601286 | 1016640 | 273896 | 76 |

[Complete inventory](evidence/phase7/shared-rss-static-layout-r1.json) retains section headers, load segments, relocation counts and identities. The 4 KiB grouping is an arithmetic convention, not a verified runtime page-size or RSS measurement. Mapped bytes and relocation destinations do not establish residency, dirty pages, heap size or executed code. Other relocation encodings are not counted by the RELA/type8 inventory.

Substantial code growth predates Phase7: the preserved corrected merged-Phase6 release is 10,747,592 bytes, between Phase5 and the combined release. That does **not** establish how much of the observed RSS difference is attributable to either phase. Existing root dispatch starts online runtimes only on relevant paths, but that source fact is not an execution profile.

The next useful causal separation is a separately coordinated, clearly labeled descriptive capture of the three preserved Rust baselines on the same retained suite, with exact identities, unchanged resource mapping, complete raw evidence and under-30-minute cleanup. This is a proposed diagnostic comparison, not admission, a new acceptance baseline, or permission to drop Phase5 regressions. Actual kernel-accounting correspondence and missing original native-meter payload limits remain explicit. No production change is proposed from this inventory.
