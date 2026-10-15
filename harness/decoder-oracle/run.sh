#!/usr/bin/env bash
# Re-run the decoder review r2 Go probe (main.go, preserved verbatim) on every
# tests/fixtures/arrow-go-v13-decoder/*.arrows stream and record what pinned Go
# Arrow v13 actually does: stdout bytes, stderr bytes and exit status. Each run
# is bounded to 4 GiB of virtual memory and 10 s. This is test-evidence
# tooling, not part of the pb product.
#
#   GO=<go1.25.4>/bin/go harness/decoder-oracle/run.sh [fixture-dir [generator]]
#
# fixture-dir defaults to tests/fixtures/arrow-go-v13-decoder; generator is the
# repository path of the preserved stream generator recorded in the manifest
# (default harness/decoder-oracle/r2_stream_dump.rs.txt). For the review r3
# corpus:
#
#   GO=... harness/decoder-oracle/run.sh tests/fixtures/arrow-go-v13-decoder-r3 \
#     harness/decoder-oracle/r3_stream_dump.rs.txt
#
# GO must be Go 1.25.4. The probe builds with the root module's pinned
# dependencies (GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly).
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
dir=${1:-$root/tests/fixtures/arrow-go-v13-decoder}
generator=${2:-harness/decoder-oracle/r2_stream_dump.rs.txt}
: "${GO:?set GO to a Go 1.25.4 binary}"
export GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly
if [[ $("$GO" env GOVERSION) != go1.25.4 ]]; then
	printf '%s\n' 'decoder oracle requires Go 1.25.4' >&2
	exit 1
fi
oracle_tmp=${TMPDIR:-${XDG_CACHE_HOME:-$HOME/.cache}/pb-phase6/tmp}
oracle_tmp=$(python3 - "$oracle_tmp" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1]).expanduser().resolve()
if path == Path('/tmp') or Path('/tmp') in path.parents:
    raise SystemExit('decoder oracle requires disk-backed temporary storage outside /tmp')
path.mkdir(parents=True, exist_ok=True)
print(path)
PY
)
export TMPDIR="$oracle_tmp" GOTMPDIR="$oracle_tmp"
scratch=$(mktemp -d "$oracle_tmp/decoder-oracle.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
(cd "$root" && "$GO" build -trimpath -o "$scratch/probe" ./harness/decoder-oracle)

for stream in "$dir"/*.arrows; do
	case_name=$(basename "$stream" .arrows)
	set +e
	(ulimit -v 4194304 && ulimit -f 16384 && exec timeout 10 "$scratch/probe" "$stream") \
		>"$dir/$case_name.stdout" 2>"$dir/$case_name.stderr"
	status=$?
	set -e
	printf '%s\n' "$status" >"$dir/$case_name.status"
	printf '%s: exit %s\n' "$case_name" "$status" >&2
done

python3 - "$dir" "$root/harness/decoder-oracle/main.go" "$GO" "$scratch/probe" "$generator" "$root" <<'PY'
import hashlib, json, os, shlex, subprocess, sys
directory, source, go, binary, generator, root = sys.argv[1:]
def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()
modules = {}
for line in subprocess.run([go, "version", "-m", binary], capture_output=True, text=True, check=True).stdout.splitlines():
    parts = line.split()
    if len(parts) >= 3 and parts[0] == "dep" and parts[1] in ("github.com/apache/arrow/go/v13", "github.com/goccy/go-json"):
        modules[parts[1]] = parts[2]
files = []
for name in sorted(os.listdir(directory)):
    if name == "manifest.json":
        continue
    path = os.path.join(directory, name)
    files.append({"name": name, "bytes": os.path.getsize(path), "sha256": digest(path)})
manifest = {
    "probe": "harness/decoder-oracle/main.go",
    "probe_sha256": digest(source),
    "stream_generator": generator + (" (Rust, from the r2 review scratch copy; not compiled)" if generator.endswith("r2_stream_dump.rs.txt") else " (Rust, from a review scratch copy; not compiled)"),
    "go_toolchain": subprocess.run([go, "env", "GOVERSION"], capture_output=True, text=True, check=True).stdout.strip(),
    "modules": modules,
    "bounds": "ulimit -v 4194304 (4 GiB), ulimit -f 16384 (at most 16 MiB per output file), timeout 10 s; disk-backed TMPDIR/GOTMPDIR outside /tmp",
    "regenerate": "GO=<go1.25.4>/bin/go harness/decoder-oracle/run.sh" + (
        "" if generator.endswith("r2_stream_dump.rs.txt") else
        " <fixture-dir> " + shlex.quote(generator)),
    "note": "stderr of a Go panic includes goroutine traces whose addresses can vary between builds; compare the first line and the exit status.",
    "cases": sorted(n[:-len(".arrows")] for n in os.listdir(directory) if n.endswith(".arrows")),
    "files": files,
}
with open(os.path.join(directory, "manifest.json"), "w") as handle:
    json.dump(manifest, handle, indent=2)
    handle.write("\n")
PY
