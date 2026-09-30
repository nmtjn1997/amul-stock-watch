#!/usr/bin/env bash
# Install amul-watch on macOS or Linux.
#   curl -fsSL https://raw.githubusercontent.com/nmtjn1997/amul-stock-watch/main/scripts/install.sh | bash
# Run from a checkout to install that checkout instead of the GitHub version.
set -euo pipefail

REPO="git+https://github.com/nmtjn1997/amul-stock-watch.git"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "$HERE" && -f "$HERE/../pyproject.toml" ]]; then SRC="$(cd "$HERE/.." && pwd)"; else SRC="$REPO"; fi

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is required ($2)" >&2; exit 1; }; }
need curl "it ships with macOS; on Linux: sudo apt install curl"
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$(command -v "$c")"; break
  fi
done
[[ -n "$PY" ]] || { echo "error: Python 3.10+ not found. macOS: brew install python (or python.org). Linux: sudo apt install python3 python3-venv" >&2; exit 1; }
if [[ "$SRC" == "$REPO" ]]; then need git "macOS: xcode-select --install  |  Linux: sudo apt install git"; fi
if ! command -v pipx >/dev/null 2>&1 && ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  echo "error: $PY cannot create virtual environments. On Debian/Ubuntu: sudo apt install python3-venv" >&2
  exit 1
fi

if command -v pipx >/dev/null 2>&1; then
  pipx install --force --python "$PY" "$SRC"
else
  VENV="${XDG_DATA_HOME:-$HOME/.local/share}/amul-watch/venv"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet "$SRC"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV/bin/amul-watch" "$HOME/.local/bin/amul-watch"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to your PATH";; esac
fi

AW="$(command -v amul-watch || echo "$HOME/.local/bin/amul-watch")"
"$AW" init
echo
echo "Installed: $AW"
echo "Next:"
echo "  $AW serve              # web UI + poller, opens http://127.0.0.1:8847"
echo "  $AW service install    # keep it running in the background after login"
command -v amul-watch >/dev/null 2>&1 || echo "(open a new terminal, or add $(dirname "$AW") to PATH, to type just amul-watch)"
