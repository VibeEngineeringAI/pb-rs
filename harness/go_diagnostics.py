#!/usr/bin/env python3
"""Build an optimized, diagnostic Go CLI without changing its original sources.

Only the pinned existing main.go and cmd/query.go are accepted. An explicit Go
build overlay replaces those files for this build alone. Templates use .go.txt
so ordinary `go build ./...` never includes diagnostic code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SHA256 = {
    "main.go": "9e540da1614d0cbf4a29049b1295143530f6b2b18dc80b9dbfa52c3c3cb5f70a",
    "cmd/query.go": "8952eea540f963c0ca61d514f884eb7d5a32e03932d728e1efee5c8bd0cbf61c",
}
DIAGNOSTIC_IMPORTS = '''\n\tpbDiagPath "path/filepath"
\tpbDiagRuntime "runtime"
\tpbDiagPprof "runtime/pprof"
\tpbDiagAtomic "sync/atomic"
\tpbDiagSyscall "syscall"
'''


def replace_once(source, anchor, replacement):
    count = source.count(anchor)
    if count != 1:
        raise ValueError(f"diagnostic source anchor changed (expected once, found {count}): {anchor!r}")
    return source.replace(anchor, replacement, 1)


def instrument_renderer(source):
    """Insert coarse call timers into the real, unchanged renderer operations."""
    start = source.index("func writePrettyJSONArray(")
    end = source.index("\nfunc firstNonSpace(", start)
    renderer = source[start:end]
    renderer = replace_once(renderer, "\tdecoder := json.NewDecoder(body)\n",
        "\tdiagElement := &pbDiagElement{selected: true}\n"
        "\tout = pbDiagAttachElement(out, diagElement)\n"
        "\tdecoder := json.NewDecoder(body)\n")
    for anchor, replacement in [
        ("decoder.Token()", "pbDiagToken(decoder)"),
        ("decoder.More()", "pbDiagMore(decoder, diagElement)"),
        ("decoder.Decode(&item)", "pbDiagDecodeItem(decoder, &item, diagElement)"),
        ('json.MarshalIndent(item, "", "  ")', "pbDiagMarshalItem(item, diagElement)"),
        ('string(bytes.ReplaceAll(encoded, []byte("\\n"), []byte("\\n  ")))',
         "pbDiagIndentItem(encoded, diagElement)"),
    ]:
        # The original renderer has two delimiter-token calls; require both.
        if anchor == "decoder.Token()":
            if renderer.count(anchor) != 2:
                raise ValueError("diagnostic delimiter-token source anchor changed")
            renderer = renderer.replace(anchor, replacement)
        else:
            renderer = replace_once(renderer, anchor, replacement)
    return source[:start] + renderer + source[end:]


def generate_overlay(repo=ROOT, out=None):
    repo = Path(repo).resolve()
    out = Path(out or repo / "target/go-diagnostics-overlay").resolve()
    if out == repo or out in repo.parents:
        raise ValueError("overlay directory must be separate from the source root")
    originals = {}
    for name, expected in SOURCE_SHA256.items():
        source = (repo / name).read_bytes()
        if hashlib.sha256(source).hexdigest() != expected:
            raise ValueError(f"diagnostic source drift: {name}; review and explicitly update pinned instrumentation")
        originals[name] = source.decode()
    main = replace_once(originals["main.go"], "func main() {\n",
                        "func main() {\n\tpb.DiagnosticsStart()\n\tdefer pb.DiagnosticsFinish()\n")
    query = replace_once(originals["cmd/query.go"], "import (\n", "import (" + DIAGNOSTIC_IMPORTS)
    query = replace_once(query, "\tresp, err := client.Client.Do(req)\n\tstopSpinner()",
                         "\tdiagRequestStart := pbDiagBegin(pbDiagRequest)\n\tresp, err := client.Client.Do(req)\n\tpbDiagEnd(pbDiagRequest, diagRequestStart)\n\tstopSpinner()")
    query = replace_once(query, "\treader := bufio.NewReader(resp.Body)",
                         "\treader := bufio.NewReader(pbDiagInput(resp.Body))")
    query = replace_once(query,
        "func streamSQLJSONResponse(out io.Writer, reader *bufio.Reader, status string) error {\n",
        "func streamSQLJSONResponse(out io.Writer, reader *bufio.Reader, status string) error {\n"
        "\tdiagRendererStart := pbDiagBegin(pbDiagRenderer)\n"
        "\tdefer pbDiagEnd(pbDiagRenderer, diagRendererStart)\n"
        "\tout = pbDiagOutput(out)\n")
    query = instrument_renderer(query)
    helper = (Path(__file__).parent / "diagnostics_go/helper.go.txt").read_text()
    query += "\n" + helper
    # Reject accidental overlay destinations that would mutate source files.
    destinations = {"main.go": out / "main.go", "cmd/query.go": out / "query.go"}
    for original in SOURCE_SHA256:
        for destination in destinations.values():
            if destination.resolve() == (repo / original).resolve():
                raise ValueError("overlay would overwrite original sources")
    out.mkdir(parents=True, exist_ok=True)
    destinations["main.go"].write_text(main)
    destinations["cmd/query.go"].write_text(query)
    overlay = out / "overlay.json"
    overlay.write_text(json.dumps({"Replace": {str(repo / key): str(value)
                                               for key, value in destinations.items()}}, indent=2) + "\n")
    metadata = {"schema_version": 1, "source_sha256": SOURCE_SHA256,
                "helper_sha256": hashlib.sha256(helper.encode()).hexdigest(),
                "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "overlay_source_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                          for name, path in destinations.items()},
                "overlay": str(overlay), "normal_sources_modified": False}
    (out / "sources.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return overlay


def build_command(go, overlay, binary, version="v0.0.0-harness"):
    return [str(go), "build", "-buildvcs=false", "-trimpath", "-tags", "kqueue",
            "-overlay", str(overlay), "-ldflags",
            f"-s -w -X main.Version={version} -X main.Commit=harness",
            "-o", str(binary), "."]


def validate_binary_destination(repo, binary):
    repo, binary = Path(repo).resolve(), Path(binary).resolve()
    if not any(directory in binary.parents for directory in [repo / "bin", repo / "target"]):
        raise ValueError("diagnostic binary must live separately under bin/ or target/")
    if not binary.name.startswith("pb-go-diagnostic"):
        raise ValueError("diagnostic binary name must start with pb-go-diagnostic to protect reference binaries")
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, help="generated overlay directory")
    parser.add_argument("--binary", type=Path, help="diagnostic binary (default bin/pb-go-diagnostics)")
    parser.add_argument("--go", default="go")
    parser.add_argument("--toolchain", default="go1.25.4")
    parser.add_argument("--version", default="v0.0.0-harness")
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    overlay = generate_overlay(repo, args.out)
    binary = (args.binary or repo / "bin/pb-go-diagnostics").resolve()
    try:
        binary = validate_binary_destination(repo, binary)
    except ValueError as error:
        parser.error(str(error))
    command = build_command(args.go, overlay, binary, args.version)
    env = os.environ.copy()
    env.update({"GOTOOLCHAIN": args.toolchain, "CGO_ENABLED": "0"})
    temporary = repo / "target/tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env.update({"TMPDIR": str(temporary), "GOTMPDIR": str(temporary)})
    metadata = json.loads((overlay.parent / "sources.json").read_text())
    metadata.update({"build_command": command, "build_env": {key: env[key] for key in
                    ("GOTOOLCHAIN", "CGO_ENABLED", "TMPDIR", "GOTMPDIR")}, "binary": str(binary)})
    if not args.generate_only:
        binary.parent.mkdir(parents=True, exist_ok=True)
        compiler = subprocess.run([args.go, "version"], env=env, cwd=repo, check=True,
                                  stdout=subprocess.PIPE, text=True).stdout.strip()
        metadata["compiler_version"] = compiler
        subprocess.run(command, env=env, cwd=repo, check=True)
        metadata["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    (overlay.parent / "build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
