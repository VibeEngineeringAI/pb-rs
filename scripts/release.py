#!/usr/bin/env python3
"""Release helpers shared by .github/workflows/release.yaml and local dry runs.

  version  TAG                         validate a tag against Cargo.toml, print outputs
  package  --version V --os O --arch A --binary PATH --out DIR
  finalize --version V --dist DIR      stamp installers, write checksums, check the set

Assets keep the GoReleaser names: pb_<version>_<os>_<arch>.tar.gz (.zip on
Windows) and pb_<version>_checksums.txt, where <version> has no leading "v".
"""
import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
BINARY = "pb"
DOCS = ("README.md", "LICENSE", "THIRD-PARTY-NOTICES")
INSTALLERS = ("install.sh", "install.ps1")
REQUIRED_TARGETS = (
    ("linux", "amd64"), ("linux", "arm64"),
    ("darwin", "amd64"), ("darwin", "arm64"),
    ("windows", "amd64"),
)
# Built on a native runner but allowed to be absent; install.ps1 falls back to x64.
OPTIONAL_TARGETS = (("windows", "arm64"),)
TAG = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)(?P<pre>-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")
STAMPS = {
    "install.sh": ('DEFAULT_VERSION="latest"', 'DEFAULT_VERSION="{tag}"'),
    "install.ps1": ('$DefaultVersion = "latest"', '$DefaultVersion = "{tag}"'),
}


def fail(message):
    sys.exit(f"error: {message}")


def archive_name(version, os_name, arch):
    ext = "zip" if os_name == "windows" else "tar.gz"
    return f"{BINARY}_{version}_{os_name}_{arch}.{ext}"


def checksums_name(version):
    return f"{BINARY}_{version}_checksums.txt"


def parse_tag(tag, cargo_version):
    match = TAG.match(tag)
    if not match:
        fail(f"tag {tag!r} is not vMAJOR.MINOR.PATCH[-PRERELEASE]")
    if match["base"] != cargo_version:
        fail(f"tag {tag} does not match Cargo.toml version {cargo_version}; "
             "bump Cargo.toml or use a matching tag")
    return {"tag": tag, "version": tag[1:], "prerelease": "true" if match["pre"] else "false"}


def cargo_version():
    with open(ROOT / "Cargo.toml", "rb") as handle:
        return tomllib.load(handle)["package"]["version"]


def cmd_version(args):
    outputs = parse_tag(args.tag, cargo_version())
    lines = [f"{key}={value}" for key, value in outputs.items()]
    print("\n".join(lines))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def epoch():
    return int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))


def members(binary, os_name):
    name = BINARY + (".exe" if os_name == "windows" else "")
    yield name, Path(binary), 0o755
    for doc in DOCS:
        yield doc, ROOT / doc, 0o644


def cmd_package(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not Path(args.binary).is_file():
        fail(f"binary not found: {args.binary}")
    path = out / archive_name(args.version, args.os, args.arch)
    mtime = epoch()
    if args.os == "windows":
        stamp = (1980, 1, 1, 0, 0, 0)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, source, mode in members(args.binary, args.os):
                info = zipfile.ZipInfo(name, stamp)
                info.external_attr = (0o100000 | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, source.read_bytes())
    else:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, source, mode in members(args.binary, args.os):
                data = source.read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), mode, mtime
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(data))
        with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=mtime) as gz:
            gz.write(buffer.getvalue())
    print(path)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stamp_installer(source, tag):
    old, new = STAMPS[source.name]
    text = source.read_text(encoding="utf-8")
    if text.count(old) != 1:
        fail(f"{source} must contain exactly one {old!r} line to pin")
    return text.replace(old, new.format(tag=tag))


def cmd_finalize(args):
    dist = Path(args.dist)
    version = args.version
    tag = "v" + version
    for name in INSTALLERS:
        (dist / name).write_text(stamp_installer(ROOT / "scripts" / name, tag), encoding="utf-8", newline="\n")
    expected = {archive_name(version, o, a) for o, a in REQUIRED_TARGETS}
    optional = {archive_name(version, o, a) for o, a in OPTIONAL_TARGETS}
    present = {p.name for p in dist.iterdir() if p.is_file()} - set(INSTALLERS) - {checksums_name(version)}
    missing = sorted(expected - present)
    unexpected = sorted(present - expected - optional)
    if missing or unexpected:
        fail(f"release asset set is wrong; missing={missing} unexpected={unexpected}")
    for name in sorted(optional - present):
        print(f"warning: optional asset {name} was not built", file=sys.stderr)
    names = sorted(present) + list(INSTALLERS)
    lines = [f"{sha256(dist / name)}  {name}\n" for name in names]
    (dist / checksums_name(version)).write_text("".join(lines), encoding="utf-8", newline="\n")
    sys.stdout.write("".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    version = sub.add_parser("version")
    version.add_argument("tag")
    package = sub.add_parser("package")
    package.add_argument("--version", required=True)
    package.add_argument("--os", required=True, choices=["linux", "darwin", "windows"])
    package.add_argument("--arch", required=True, choices=["amd64", "arm64"])
    package.add_argument("--binary", required=True)
    package.add_argument("--out", required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--version", required=True)
    finalize.add_argument("--dist", required=True)
    args = parser.parse_args(argv)
    {"version": cmd_version, "package": cmd_package, "finalize": cmd_finalize}[args.command](args)


if __name__ == "__main__":
    main()
