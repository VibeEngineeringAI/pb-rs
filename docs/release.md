# Releasing pb

`.github/workflows/release.yaml` builds native Rust binaries on each platform's
own GitHub-hosted runner, verifies them, and publishes a GitHub release on
`VibeEngineeringAI/pb-rs`. It replaces the GoReleaser pipeline, keeping the
same asset names.

## Cutting a release

1. Set `version` in `Cargo.toml` to the release's base version (for example
   `0.1.0`) and merge it.
2. Tag and push: `git tag v0.1.0-rc.1 && git push origin v0.1.0-rc.1`.

Tags must be `vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-PRERELEASE`, and the
base version must equal the one in `Cargo.toml`. If not, the `plan` job fails
before anything is built. A tag with a suffix (`-rc.1`, `-beta.2`) is
published as a **prerelease** that is never marked latest. GitHub's
`/releases/latest` URLs ignore prereleases, so until the first stable release
exists, installation docs must pin a tag (see below).

Pull requests that touch release files, and manual `workflow_dispatch` runs,
go through every step with a synthetic `v<cargo>-ci.<run>` version except
publishing. The assembled release is uploaded as the `pb-release-<tag>`
workflow artifact.

## What the workflow does

| Job | Purpose |
| --- | --- |
| `plan` | Validates the tag against `Cargo.toml` (`scripts/release.py version`) and decides prerelease and publish. |
| `installer-tests` | Runs `tests/release/test_installers.py` offline on Linux (dash and bash, plus shellcheck), macOS, and Windows (PowerShell 7 and Windows PowerShell 5.1, including a real user-PATH update). |
| `build` | Builds `cargo build --locked --release` natively for each target, with `PB_VERSION=<tag>` and `PB_COMMIT=<short sha>`. Runs `pb version`, packages the binary, installs that archive with the pinned installer from a local mirror, and runs it again. |
| `release` | Merges the archives, stamps the installers, writes `pb_<version>_checksums.txt`, checks the exact asset set, verifies every checksum, and installs the final Linux asset. On tag pushes it then uploads to a draft release, publishes it, and refuses to overwrite an existing release. |

## Targets and assets

`<version>` is the tag without its `v`, for example `0.1.0-rc.1`.

| Asset | Rust target | Runner |
| --- | --- | --- |
| `pb_<version>_linux_amd64.tar.gz` | `x86_64-unknown-linux-gnu` (glibc ≥ 2.35) | `ubuntu-22.04` |
| `pb_<version>_linux_arm64.tar.gz` | `aarch64-unknown-linux-gnu` (glibc ≥ 2.35) | `ubuntu-22.04-arm` |
| `pb_<version>_darwin_amd64.tar.gz` | `x86_64-apple-darwin` (macOS ≥ 11) | `macos-15-intel` |
| `pb_<version>_darwin_arm64.tar.gz` | `aarch64-apple-darwin` (macOS ≥ 11) | `macos-15` |
| `pb_<version>_windows_amd64.zip` | `x86_64-pc-windows-msvc`, static CRT | `windows-latest` |
| `pb_<version>_windows_arm64.zip` | `aarch64-pc-windows-msvc`, static CRT, **optional** | `windows-11-arm` |
| `pb_<version>_checksums.txt` | SHA-256 of every other asset (`<sha256>  <name>`) | |
| `install.sh`, `install.ps1` | Installers pinned to this tag | |

Each archive holds `pb` (or `pb.exe`), `README.md`, `LICENSE` and
`THIRD-PARTY-NOTICES` at its root. The Windows arm64 build is best-effort: if
it fails, the release goes out without it. In that case `install.ps1` on
Windows on Arm installs the x64 build, which runs under emulation. Don't
promise a native arm64 Windows binary unless the release lists one.

## Installing

Pinned (required while only prereleases exist):

```sh
curl -fsSL https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1/install.sh | sh
```

```powershell
irm https://github.com/VibeEngineeringAI/pb-rs/releases/download/v0.1.0-rc.1/install.ps1 | iex
```

A release's own installer defaults to that release's tag. Once a stable
release exists, `.../releases/latest/download/install.sh` (or `install.ps1`)
installs the newest stable version. `scripts/install.*` from the default
branch resolve the newest stable version through the `/releases/latest`
redirect, without the rate-limited API. If no stable release exists, they
exit with an error asking for a pinned version.

| | `install.sh` (Linux, macOS) | `install.ps1` (Windows) |
| --- | --- | --- |
| Version | `--version v0.1.0-rc.1` or `PB_VERSION` | `PB_VERSION` |
| Directory | `--install-dir DIR` or `PB_INSTALL_DIR`; default `~/.local/bin` | `PB_INSTALL_DIR`; default `%LOCALAPPDATA%\Programs\pb\bin` |
| PATH | Never edits shell profiles; prints the line to add | Adds the directory to the user PATH (registry, `REG_EXPAND_SZ` preserved) unless `PB_NO_MODIFY_PATH=1` |
| Privileges | User-level; uses `sudo` only if the chosen directory is not writable | User-level only |

Both installers accept the legacy `VERSION` and `INSTALL_DIR` variables. Both
reject malformed versions before any network request. Both download the
checksum file, verify the archive's SHA-256, and only then install, replacing
any existing binary. `PB_INSTALL_GITHUB_URL` points either installer at a
mirror; the offline tests use it.

## Cloud defaults (optional)

If they are set, the build embeds the `PB_CLOUD_ORCHESTRATOR_URL` repository
variable and the `PB_CLOUD_ORCHESTRATOR_AUTH_TOKEN` secret. Both are optional.
Without them the binaries have no built-in Cloud device-login configuration.
Self-hosted Parseable needs neither. Configure the orchestrator before publishing
a build intended to support Cloud device login.

## Checking locally

```sh
python3 tests/release/test_installers.py                    # offline installer and helper tests
PB_INSTALLER_TEST_SH=dash python3 tests/release/test_installers.py
PB_VERSION=v0.1.0-rc.1 cargo build --locked --release
python3 scripts/release.py package --version 0.1.0-rc.1 --os linux --arch amd64 \
  --binary target/release/pb --out dist
python3 tests/release/test_installers.py --e2e-dist dist --e2e-version 0.1.0-rc.1
```

If `/tmp` is full or quota-limited, set `TMPDIR` to a directory in the
workspace (for example `target/tmp`).
