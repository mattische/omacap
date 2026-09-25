#!/usr/bin/env bash
# Install or update omacap.
#
#   curl -fsSL https://raw.githubusercontent.com/mattische/omacap/main/install.sh | bash
#
# Puts a git checkout in ~/.local/share/omacap/src, a virtualenv beside it, and
# a launcher on your PATH. Running it again updates an existing install, which
# is also what `omacap update` does.
#
# Options (as environment variables):
#   OMACAP_HOME=<dir>    where to install        (default ~/.local/share/omacap)
#   OMACAP_BIN=<dir>     where to link omacap    (default ~/.local/bin)
#   OMACAP_REF=<ref>     branch or tag to check out            (default main)
#   OMACAP_NO_ANALYZE=1  skip numpy, install recording only

set -euo pipefail

REPO_URL="${OMACAP_REPO:-https://github.com/mattische/omacap.git}"
HOME_DIR="${OMACAP_HOME:-$HOME/.local/share/omacap}"
BIN_DIR="${OMACAP_BIN:-$HOME/.local/bin}"
REF="${OMACAP_REF:-main}"
CHECKOUT="$HOME_DIR/src"
VENV="$HOME_DIR/venv"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
warn() { printf '\033[33m  %s\033[0m\n' "$1" >&2; }
die()  { printf '\033[31momacap install: %s\033[0m\n' "$1" >&2; exit 1; }

bold "Installing omacap"

# -- prerequisites ---------------------------------------------------------
for tool in git python3; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required but not installed."
done

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 or newer is required (found $(python3 -V 2>&1))."

python3 -c 'import venv' >/dev/null 2>&1 \
  || die "the Python venv module is missing. On Debian/Ubuntu: sudo apt install python3-venv"

missing=()
command -v ffmpeg >/dev/null 2>&1 || missing+=("ffmpeg")
command -v pactl  >/dev/null 2>&1 || missing+=("pactl")
if [ ${#missing[@]} -gt 0 ]; then
  warn "missing at runtime: ${missing[*]}"
  warn "  Arch:          sudo pacman -S ffmpeg libpulse"
  warn "  Debian/Ubuntu: sudo apt install ffmpeg pulseaudio-utils"
  warn "  Fedora:        sudo dnf install ffmpeg pulseaudio-utils"
  warn "omacap will install, but recording needs these. 'omacap doctor' will confirm."
fi

# -- code ------------------------------------------------------------------
if [ -d "$CHECKOUT/.git" ]; then
  info "updating $CHECKOUT"
  if [ -n "$(git -C "$CHECKOUT" status --porcelain)" ]; then
    die "$CHECKOUT has uncommitted changes; updating would overwrite them."
  fi
  git -C "$CHECKOUT" fetch --quiet origin "$REF"
  git -C "$CHECKOUT" checkout --quiet "$REF"
  git -C "$CHECKOUT" merge --quiet --ff-only "origin/$REF"
else
  info "cloning into $CHECKOUT"
  mkdir -p "$HOME_DIR"
  rm -rf "$CHECKOUT"
  git clone --quiet --branch "$REF" "$REPO_URL" "$CHECKOUT" \
    || die "could not clone $REPO_URL (is the repository reachable?)"
fi

# -- environment -----------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  info "creating a virtualenv in $VENV"
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true

# Editable, so `omacap update` only has to pull: the code it runs is the checkout.
extras="[analyze]"
[ -n "${OMACAP_NO_ANALYZE:-}" ] && extras=""
info "installing omacap${extras}"
"$VENV/bin/python" -m pip install --quiet --editable "$CHECKOUT$extras" \
  || die "pip install failed."

# -- launcher --------------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/omacap" "$BIN_DIR/omacap"
info "linked $BIN_DIR/omacap"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. Add this to your shell profile:"
     warn "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

echo
bold "Installed: $("$BIN_DIR/omacap" --version)"
info "run 'omacap doctor' to check everything is in place"
info "run 'omacap' to start recording, 'omacap update' to update"
