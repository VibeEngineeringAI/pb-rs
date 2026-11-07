#!/bin/sh
# Install the native pb CLI from GitHub releases.
#
#   curl -fsSL https://github.com/VibeEngineeringAI/pb-rs/releases/latest/download/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --version v0.1.0-rc.1 --install-dir "$HOME/bin"
#
# Environment: PB_VERSION, PB_INSTALL_DIR (legacy VERSION and INSTALL_DIR are
# honoured). The archive checksum is always verified before installation.
set -eu

REPO="VibeEngineeringAI/pb-rs"
BINARY_NAME="pb"
# Release builds rewrite this line so a pinned installer installs its own tag.
DEFAULT_VERSION="latest"
GITHUB_URL="${PB_INSTALL_GITHUB_URL:-https://github.com}"
VERSION="${PB_VERSION:-${VERSION:-$DEFAULT_VERSION}}"
INSTALL_DIR="${PB_INSTALL_DIR:-${INSTALL_DIR:-"$HOME/.local/bin"}}"
# Only HTTPS, including redirects; plain HTTP only for an explicit http:// mirror.
case "$GITHUB_URL" in
http://*) CURL_PROTO="=http,https" ;;
*) CURL_PROTO="=https" ;;
esac

usage() {
	cat <<EOF
Install $BINARY_NAME from https://github.com/$REPO/releases

Usage: install.sh [--version VERSION] [--install-dir DIR]

  --version VERSION    Release tag to install, e.g. v0.1.0-rc.1 (default: $DEFAULT_VERSION)
  --install-dir DIR    Destination directory (default: \$HOME/.local/bin)
EOF
}

err() {
	echo "error: $*" >&2
	exit 1
}

while [ $# -gt 0 ]; do
	case "$1" in
	--version)
		[ $# -ge 2 ] || err "--version requires a value"
		VERSION="$2"
		shift 2
		;;
	--version=*)
		VERSION="${1#*=}"
		shift
		;;
	--install-dir)
		[ $# -ge 2 ] || err "--install-dir requires a value"
		INSTALL_DIR="$2"
		shift 2
		;;
	--install-dir=*)
		INSTALL_DIR="${1#*=}"
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*) err "unknown argument: $1 (see --help)" ;;
	esac
done

[ -n "$VERSION" ] || err "version must not be empty"
[ -n "$INSTALL_DIR" ] || err "install directory must not be empty"

need_cmd() {
	command -v "$1" >/dev/null 2>&1 || err "required command not found: $1"
}

download() {
	if command -v curl >/dev/null 2>&1; then
		curl --proto "$CURL_PROTO" --proto-redir "$CURL_PROTO" -fsSL "$1" -o "$2" || err "download failed: $1"
	elif command -v wget >/dev/null 2>&1; then
		wget -q "$1" -O "$2" || err "download failed: $1"
	else
		err "curl or wget is required"
	fi
}

# Resolve the newest stable release from the /releases/latest redirect, which
# avoids the unauthenticated API rate limit. Prereleases are never "latest".
resolve_latest() {
	url="$GITHUB_URL/$REPO/releases/latest"
	if command -v curl >/dev/null 2>&1; then
		effective="$(curl --proto "$CURL_PROTO" --proto-redir "$CURL_PROTO" -fsSLI -o /dev/null -w '%{url_effective}' "$url")" || err "could not resolve the latest release from $url"
	elif command -v wget >/dev/null 2>&1; then
		effective="$(wget -q -O /dev/null --server-response "$url" 2>&1 | awk '/^ *[Ll]ocation: / { loc=$2 } END { print loc }' | tr -d '\r')"
	else
		err "curl or wget is required"
	fi
	tag="${effective##*/}"
	case "$tag" in
	v[0-9]*) printf '%s\n' "$tag" ;;
	*) err "no stable release found at $url; pin one with --version (for example --version v0.1.0-rc.1)" ;;
	esac
}

sha256() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | awk '{print $1}'
	elif command -v openssl >/dev/null 2>&1; then
		openssl dgst -sha256 "$1" | awk '{print $NF}'
	else
		err "sha256sum, shasum or openssl is required to verify the download"
	fi
}

print_path_instructions() {
	echo ""
	echo "$INSTALL_DIR is not in your PATH. To use $BINARY_NAME from any directory:"
	shell_name="${SHELL:-sh}"
	shell_name="${shell_name##*/}"
	echo ""
	case "$shell_name" in
	bash)
		echo "  echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.bashrc && . ~/.bashrc"
		;;
	zsh)
		echo "  echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.zshrc && . ~/.zshrc"
		;;
	fish)
		echo "  fish_add_path '$INSTALL_DIR'"
		;;
	*)
		echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
		;;
	esac
}

case "$(uname -s)" in
Darwin) os="darwin" ;;
Linux) os="linux" ;;
MINGW* | MSYS* | CYGWIN*) err "use install.ps1 on Windows: irm https://github.com/$REPO/releases/latest/download/install.ps1 | iex" ;;
*) err "unsupported operating system: $(uname -s)" ;;
esac

case "$(uname -m)" in
x86_64 | amd64) arch="amd64" ;;
arm64 | aarch64) arch="arm64" ;;
*) err "unsupported architecture: $(uname -m)" ;;
esac

# An x86_64 shell under Rosetta still runs on Apple silicon; prefer native.
if [ "$os" = "darwin" ] && [ "$arch" = "amd64" ] &&
	[ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
	arch="arm64"
fi

need_cmd tar
need_cmd awk
need_cmd mktemp
need_cmd grep

if [ "$VERSION" = "latest" ]; then
	VERSION="$(resolve_latest)"
fi
VERSION="${VERSION#v}"
# Strict semver shape: the version becomes part of URLs and file names.
if [ "$(printf '%s\n' "$VERSION" | wc -l | tr -d ' ')" != "1" ] ||
	! printf '%s\n' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$'; then
	err "invalid version: $VERSION (expected vMAJOR.MINOR.PATCH[-PRERELEASE])"
fi

asset="${BINARY_NAME}_${VERSION}_${os}_${arch}.tar.gz"
checksums="${BINARY_NAME}_${VERSION}_checksums.txt"
base_url="$GITHUB_URL/$REPO/releases/download/v$VERSION"

tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t pb-install)"
trap 'rm -rf "$tmpdir"' EXIT
trap 'exit 130' INT TERM

echo "Downloading $BINARY_NAME v$VERSION for $os/$arch..."
download "$base_url/$checksums" "$tmpdir/$checksums"
download "$base_url/$asset" "$tmpdir/$asset"

expected="$(awk -v file="$asset" '{ name=$2; sub(/^\*/, "", name) } name == file { print tolower($1); exit }' "$tmpdir/$checksums")"
[ -n "$expected" ] || err "checksum for $asset not found in $checksums"
actual="$(sha256 "$tmpdir/$asset")"
[ "$actual" = "$expected" ] || err "checksum verification failed for $asset (expected $expected, got $actual)"
echo "Verified SHA-256 checksum."

mkdir "$tmpdir/extract"
tar -xzf "$tmpdir/$asset" -C "$tmpdir/extract"
[ -f "$tmpdir/extract/$BINARY_NAME" ] || err "$asset does not contain $BINARY_NAME"
chmod 755 "$tmpdir/extract/$BINARY_NAME"

# Copy beside the destination, then rename, so a running pb is replaced atomically.
dst="$INSTALL_DIR/$BINARY_NAME"
if mkdir -p "$INSTALL_DIR" 2>/dev/null && [ -w "$INSTALL_DIR" ]; then
	cp "$tmpdir/extract/$BINARY_NAME" "$dst.tmp.$$"
	mv -f "$dst.tmp.$$" "$dst"
elif command -v sudo >/dev/null 2>&1; then
	echo "$INSTALL_DIR is not writable; using sudo."
	sudo mkdir -p "$INSTALL_DIR"
	sudo install -m 755 "$tmpdir/extract/$BINARY_NAME" "$dst"
else
	err "cannot write to $INSTALL_DIR; choose another directory with --install-dir"
fi

echo "$BINARY_NAME v$VERSION installed to $dst"
case ":$PATH:" in
*":$INSTALL_DIR:"*) ;;
*) print_path_instructions ;;
esac
