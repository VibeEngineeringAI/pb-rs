# Negative Arrow row-count probe

This small program characterizes the established Go Arrow v13 read/render loop
used by `tail` at `07af390`. It runs in the repository Go module; no separate
module or copied dependency files are needed. Use the pinned Go 1.25.4 compiler:

```sh
GOTOOLCHAIN=local GOPROXY=off /path/to/go1.25.4/bin/go build -o /disk/cache/probe ./harness/tail-negative-oracle
(ulimit -v 4194304; ulimit -f 1024; timeout 10 /disk/cache/probe null -1)
```

Run each of `null`, `struct`, `int64` with lengths `77`, `-1`, `-5`. The program
writes a 77-row IPC stream and patches its batch/node lengths (and Null null
count). Values start at 1000 so the Int64 data does not contain the patched 77.
Stdout contains tail's row bytes and extra batch newline. Stderr reports the
patch count, NumRows and the final reader error. The probe exits normally after
reporting a read error; actual tail treats a non-EOF decode error as fatal.
This distinguishes reader behavior from CLI exit-code evidence.

The bounded pinned-toolchain results are in
`docs/evidence/phase6/negative-row-go125.json`: negative Null and empty Struct
batches produce one newline; negative Int64 batches fail decoding without
stdout. Rust preserves the characterized rowless cases and rejects other
negative layouts safely. These are malformed IPC probes, not performance runs.
