#!/usr/bin/env python3
"""Archive declared binary identities and a safe source delta against Git HEAD."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess


HERE = Path(__file__).resolve().parent
ROOT_FILES = {"Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "Makefile", ".gitignore",
              ".golangci.yml", ".goreleaser.yml", "README.md", "LICENSE",
              "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"}
EXCLUDED_PARTS = {".git", "target", "bin", "results", "artifacts", "config", "configs",
                  "secrets", "credentials", ".env", "__pycache__", "vendor", "dist"}
KNOWN_CONFIG_SOURCE = {"pkg/config/config.go", "pkg/config/defaults.go", "pkg/model/credential/credential.go"}


def source_allowed(name):
    """Explicit source/build whitelist; arbitrary repository files are excluded."""
    path = PurePosixPath(name)
    parts = path.parts
    if path.is_absolute() or not parts or any(p in {".", ".."} for p in parts):
        return False
    if name in KNOWN_CONFIG_SOURCE:
        return True
    if any(p.lower() in EXCLUDED_PARTS for p in parts):
        return False
    if path.name.lower().startswith(("secret", "credential", ".env")):
        return False
    if name in ROOT_FILES:
        return True
    if len(parts) == 1:
        return path.suffix == ".go"
    if parts[0] in {"src", "tests"}:
        return path.suffix == ".rs" or name in {"src/catalog.json", "src/spec.json", "src/LICENSE-Go.txt"}
    if parts[0] in {"cmd", "pkg"}:
        return path.suffix == ".go"
    if parts[0] == "buildscripts":
        return path.suffix in {".go", ".sh"} or name == "buildscripts/build.env"
    if parts[0] == "scripts":
        return path.suffix in {".sh", ".ps1"}
    if parts[0] == "harness":
        return (path.suffix in {".py", ".c", ".go"} or
                name == "harness/diagnostics_go/helper.go.txt" or
                name in {"harness/Makefile", "harness/README.md", "harness/contracts/README.md",
                         "harness/contracts/phase1-surface.json", "harness/contracts/phase2.md",
                         "harness/contracts/phase3.md", "harness/contracts/phase3-reference.json"})
    if parts[:2] == (".github", "workflows"):
        return path.suffix in {".yaml", ".yml"}
    return parts[0] == "docs" and len(parts) == 2 and path.name.startswith("rust-phase") and path.suffix == ".md"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def git_paths(root, *args):
    return {os.fsdecode(p) for p in git(root, "ls-files", "-z", *args).split(b"\0") if p}


def source_kind(root, name):
    """Reject symlinks at every level, including links to files outside the repo."""
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return "missing"
        if stat.S_ISLNK(mode):
            return "other"
    return "regular" if stat.S_ISREG(mode) else "other"


def regular_source(root, name):
    return source_kind(root, name) == "regular"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def source_snapshot(root):
    head = git(root, "rev-parse", "HEAD").decode().strip()
    tracked = git_paths(root)
    untracked = git_paths(root, "--others", "--exclude-standard")
    eligible = {p for p in tracked | untracked if source_allowed(p)}
    regular = {p for p in eligible if regular_source(root, p)}
    # Deleted tracked files belong in the patch; existing nonregular files never do.
    deleted = {p for p in eligible & tracked if source_kind(root, p) == "missing"}
    included = regular | deleted
    contents = {p: (root / p).read_bytes() for p in sorted(regular)}
    modes = {p: stat.S_IMODE((root / p).lstat().st_mode) for p in regular}
    patch = git(root, "diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames",
                "--", *sorted(included & tracked)) if included & tracked else b""
    raw_status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    statuses = []
    index = 0
    while index < len(raw_status) and raw_status[index]:
        entry = raw_status[index]
        code, name = entry[:2].decode("ascii"), os.fsdecode(entry[3:])
        index += 1
        if "R" in code or "C" in code:
            index += 1  # Original rename/copy path is not needed; patch disables renames.
        if name in included:
            statuses.append({"path": name, "status": code})
    # Reject capture races instead of associating a patch with different hashes.
    if (head != git(root, "rev-parse", "HEAD").decode().strip() or
            tracked != git_paths(root) or untracked != git_paths(root, "--others", "--exclude-standard") or
            any(not regular_source(root, p) or (root / p).read_bytes() != data or
                stat.S_IMODE((root / p).lstat().st_mode) != modes[p] for p, data in contents.items()) or
            any(source_kind(root, p) != "missing" for p in deleted)):
        raise ValueError("repository changed during source capture; retry after edits finish")
    files = {p: {"sha256": sha256(data), "mode": oct(modes[p])}
             for p, data in contents.items()}
    return {"head": head, "status": statuses, "files": files, "deleted_files": sorted(deleted),
            "patch_base64": base64.b64encode(patch).decode(), "patch_sha256": sha256(patch),
            "untracked_files": [{"path": p, **files[p], "contents_base64": base64.b64encode(contents[p]).decode()}
                                for p in sorted(regular & untracked)],
            "policy": "explicit repository source/build whitelist; no symlinks, config, credentials, artifacts or external directory traversal",
            "restore": "checkout head; decode patch_base64 and git apply --binary; restore untracked_files contents and modes"}


def file_identity(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"not a regular file: {path}")
    with path.open("rb") as handle:
        hashed = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"path": str(path), "sha256": hashed, "size_bytes": path.stat().st_size}


def assignments(values, option):
    result = {}
    for value in values:
        name, sep, detail = value.partition("=")
        if not sep or not name or not detail or name in result or not all(c.isalnum() or c in "_-" for c in name):
            raise ValueError(f"{option} requires unique LABEL=VALUE entries")
        result[name] = detail
    return result


def create_manifest(args):
    root = Path(os.fsdecode(git(Path(args.repo).resolve(), "rev-parse", "--show-toplevel")).strip()).resolve()
    binaries = assignments(args.binary, "--binary")
    notes = assignments(args.build_note, "--build-note")
    compilers = assignments(args.compiler_version, "--compiler-version")
    if (notes.keys() | compilers.keys()) - binaries.keys():
        raise ValueError("build notes and compiler versions must name a declared binary")
    return {"schema_version": 1, "repository": str(root), "source": source_snapshot(root),
            "binaries": {label: {**file_identity(path), "build_note": notes.get(label, "unknown"),
                                  "compiler_version": compilers.get(label, "unknown")}
                         for label, path in binaries.items()},
            "tools": {label: file_identity(path) for label, path in
                      [("manifest", Path(__file__)), ("runner", args.runner), ("meter", args.meter), ("fixture", args.fixture)]
                      if path is not None},
            "build_metadata_policy": "caller-declared per binary; never inferred from currently installed compilers",
            "source_association_policy": "source records the repository at capture; each binary's build provenance must be declared in its build_note"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="new manifest JSON file; never overwritten")
    parser.add_argument("--repo", default=str(HERE.parent))
    parser.add_argument("--binary", action="append", required=True, metavar="LABEL=PATH")
    parser.add_argument("--build-note", action="append", default=[], metavar="LABEL=TEXT")
    parser.add_argument("--compiler-version", action="append", default=[], metavar="LABEL=TEXT")
    parser.add_argument("--runner", default=str(HERE / "run.py"))
    parser.add_argument("--meter", help="exact meter used by the run; omitted tools remain unrecorded")
    parser.add_argument("--fixture", help="exact fixture used by the run")
    args = parser.parse_args()
    try:
        manifest = create_manifest(args)
        with Path(args.out).open("x") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"manifest: {error}\n")


if __name__ == "__main__":
    main()
