#!/usr/bin/env bash
# subtitle-translator installer for Debian 12+ and Ubuntu 22.04+.
# Installs mkvtoolnix, tesseract (eng), the pgsrip system libraries, vobsub2srt
# (apt, or a pinned source build), uv, and the project dependencies. Re-runnable.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV_VERSION="0.11.8"
VOBSUB2SRT_COMMIT="0ba6e25e078a040195d7295e860cc9064bef7c2c"

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || die "Linux only. On macOS, see the README."
command -v apt-get >/dev/null || die "apt-get not found. This installer is for Debian and Ubuntu only."

SUDO=""
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "Not root and no sudo. Run again as root."
  SUDO="sudo"
fi

log "apt update"
$SUDO apt-get update -qq

# Debian 13 and Ubuntu 24.04 renamed the GLib runtime package for the 64-bit time_t transition.
GLIB_PKG="libglib2.0-0"
apt-cache show libglib2.0-0t64 >/dev/null 2>&1 && GLIB_PKG="libglib2.0-0t64"

log "Installing apt packages"
$SUDO apt-get install -y --no-install-recommends \
  mkvtoolnix \
  tesseract-ocr \
  tesseract-ocr-eng \
  libgl1 \
  "$GLIB_PKG" \
  libtesseract-dev \
  libleptonica-dev \
  libtiff-dev \
  build-essential \
  cmake \
  pkg-config \
  git \
  curl \
  ca-certificates

if command -v vobsub2srt >/dev/null; then
  log "vobsub2srt already installed: $(command -v vobsub2srt)"
elif apt-cache show vobsub2srt >/dev/null 2>&1; then
  log "Installing vobsub2srt from apt"
  $SUDO apt-get install -y --no-install-recommends vobsub2srt
else
  log "vobsub2srt is not in apt. Building commit ${VOBSUB2SRT_COMMIT:0:7} from source"
  BUILD_DIR="$(mktemp -d)"
  trap 'rm -rf "$BUILD_DIR"' EXIT
  git clone --quiet https://github.com/ruediger/VobSub2SRT.git "$BUILD_DIR/v2s"
  git -C "$BUILD_DIR/v2s" checkout --quiet "$VOBSUB2SRT_COMMIT"
  # The 2017 source can fail against Tesseract 5. PGS OCR does not need it.
  if (cd "$BUILD_DIR/v2s" && cmake . -DCMAKE_BUILD_TYPE=Release >/dev/null && make >/dev/null); then
    $SUDO make -C "$BUILD_DIR/v2s" install >/dev/null
    log "vobsub2srt installed: $(command -v vobsub2srt)"
  else
    warn "vobsub2srt build failed. PGS OCR still works; VobSub tracks are skipped."
  fi
fi

if ! command -v uv >/dev/null; then
  log "Installing uv $UV_VERSION"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || die "uv is installed but not on PATH. Add ~/.local/bin to PATH and run again."
else
  log "uv already installed: $(uv --version)"
fi

log "uv sync"
uv sync --frozen --no-dev --directory "$REPO_DIR"

ok() { "$@" >/dev/null 2>&1 && echo OK || echo MISSING; }
cat <<EOF

Tools:
  mkvmerge    $(ok mkvmerge --version)
  tesseract   $(ok tesseract --version)
  pgsrip      $(ok "$REPO_DIR/.venv/bin/pgsrip" --help)
  vobsub2srt  $(ok command -v vobsub2srt)

Next:
  cd "$REPO_DIR"
  export OPENROUTER_API_KEY=...
  uv run subtrans /path/to/movie.mkv --list
  uv run subtrans /path/to/movie.mkv
  uv run subtrans-sweep /path/to/library --dry-run
EOF
